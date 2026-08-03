#!/usr/bin/env python3
"""
qr_socket_server.py

Standalone Ubuntu application (no ROS required).

Keeps a camera open and listens on a TCP socket for JSON commands.
On receiving a "read" command, it grabs the current frame from the
camera and tries to decode a QR code from it. If none is found, it
retries a few times (grabbing a fresh frame each attempt) before
giving up, then sends the result back to the client over the same
connection.

Protocol: JSON, one object per line (newline-delimited JSON).

Client -> Server:
    {"command": "read"}
    {"command": "ping"}
    {"command": "quit"}

Server -> Client:
    {"status": "ok", "data": "<decoded_text>"}      (QR code found)
    {"status": "no_qr"}                              (no QR in this frame)
    {"status": "error", "message": "<details>"}      (camera / decode error)
    {"status": "pong"}                                (reply to "ping")

Multiple clients can connect; camera access is serialized with a lock
so frame reads from different clients don't race each other.
"""

import argparse
import json
import logging
import socketserver
import threading
import time

import cv2
from pyzbar.pyzbar import decode as decode_qr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("qr_socket_server")


class Camera:
    """Thin wrapper around cv2.VideoCapture, opened once and reused."""

    def __init__(self, index=0, width=None, height=None):
        self.index = index
        self.lock = threading.Lock()
        self.cap = cv2.VideoCapture(index)
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open camera index {index}")
        log.info("Camera %s opened", index)

    def read_frame(self):
        with self.lock:
            ok, frame = self.cap.read()
        if not ok or frame is None:
            return None
        return frame

    def release(self):
        with self.lock:
            if self.cap.isOpened():
                self.cap.release()
                log.info("Camera %s released", self.index)


def read_current_qr(camera: Camera, retries: int = 1, retry_interval: float = 0.2):
    """Grab a frame and try to decode a QR code from it, retrying a few
    times if nothing is found (each retry grabs a fresh frame).

    `retries` is the total number of attempts (1 = no retrying, just a
    single check). Returns the decoded text (str) or None if no QR
    code was found after all attempts.
    """
    last_frame_ok = False
    for attempt in range(1, retries + 1):
        frame = camera.read_frame()
        if frame is None:
            continue
        last_frame_ok = True

        results = decode_qr(frame)
        if results:
            return results[0].data.decode("utf-8", errors="replace")

        if attempt < retries:
            time.sleep(retry_interval)

    if not last_frame_ok:
        raise RuntimeError("Failed to grab a frame from the camera")
    return None


class QRRequestHandler(socketserver.StreamRequestHandler):
    def _send(self, obj: dict):
        self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))

    def handle(self):
        client = self.client_address
        log.info("Client connected: %s", client)
        try:
            while True:
                line = self.rfile.readline()
                if not line:
                    break  # client closed connection

                line = line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                    command = str(msg.get("command", "")).lower()
                except (json.JSONDecodeError, AttributeError):
                    self._send({"status": "error", "message": "invalid_json"})
                    continue

                if command == "ping":
                    self._send({"status": "pong"})

                elif command == "read":
                    log.info("read requested by %s", client)
                    try:
                        text = read_current_qr(
                            self.server.camera,
                            retries=self.server.retries,
                            retry_interval=self.server.retry_interval,
                        )
                    except Exception as e:
                        log.exception("Error while reading QR")
                        self._send({"status": "error", "message": str(e)})
                        continue

                    if text is not None:
                        log.info("QR decoded: %s", text)
                        self._send({"status": "ok", "data": text})
                    else:
                        log.info("No QR code in current frame")
                        self._send({"status": "no_qr"})

                elif command == "quit":
                    break

                else:
                    self._send({"status": "error", "message": "unknown_command"})
        finally:
            log.info("Client disconnected: %s", client)


class QRSocketServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address, handler_cls, camera, retries, retry_interval):
        super().__init__(server_address, handler_cls)
        self.camera = camera
        self.retries = retries
        self.retry_interval = retry_interval


def main():
    parser = argparse.ArgumentParser(description="Standalone QR-code socket server (JSON protocol)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=6789, help="TCP port (default: 6789)")
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index (default: 0)")
    parser.add_argument("--width", type=int, default=None, help="Camera capture width")
    parser.add_argument("--height", type=int, default=None, help="Camera capture height")
    parser.add_argument("--retries", type=int, default=3,
                         help="Number of frame attempts per 'read' command before giving up (default: 3)")
    parser.add_argument("--retry-interval", type=float, default=0.2,
                         help="Seconds to wait between retry attempts (default: 0.2)")
    args = parser.parse_args()

    camera = Camera(index=args.camera_index, width=args.width, height=args.height)

    server = QRSocketServer(
        (args.host, args.port),
        QRRequestHandler,
        camera,
        args.retries,
        args.retry_interval,
    )

    log.info("QR socket server listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down (Ctrl+C)")
    finally:
        server.shutdown()
        camera.release()


if __name__ == "__main__":
    main()
