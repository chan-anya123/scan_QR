#!/usr/bin/env python3
"""
qr_http_server.py

Standalone Ubuntu application (no ROS required).

Keeps a camera open and exposes an HTTP API so tools like Postman,
curl, or any HTTP client can request a QR read on demand. Internally
this reuses the same camera-grab-and-decode logic as the raw-TCP
version (qr_socket_server_tcp.py) but speaks plain HTTP/JSON instead
of a custom socket protocol.

Endpoints:
    GET  /ping
        -> 200 {"status": "pong"}

    POST /read
        Optional JSON body: {"retries": <int>, "retry_interval": <float>}
        (both optional; server-side defaults are used if omitted)

        -> 200 {"status": "ok", "data": "<decoded_text>"}   QR code found
        -> 200 {"status": "no_qr"}                           nothing found after retries
        -> 500 {"status": "error", "message": "<details>"}   camera/decode failure

Run:
    python3 qr_http_server.py --port 8080 --camera-index 0
"""

import argparse
import logging
import threading
import time

import cv2
from flask import Flask, jsonify, request
from pyzbar.pyzbar import decode as decode_qr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("qr_http_server")


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


def read_current_qr(camera: Camera, retries: int = 3, retry_interval: float = 0.2):
    """Grab a frame and try to decode a QR code from it, retrying a few
    times if nothing is found (each retry grabs a fresh frame).

    Returns the decoded text (str) or None if no QR code was found
    after all attempts.
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


def create_app(camera: Camera, default_retries: int, default_retry_interval: float) -> Flask:
    app = Flask(__name__)

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"status": "pong"})

    @app.route("/read", methods=["POST"])
    def read():
        body = request.get_json(silent=True) or {}
        retries = body.get("retries", default_retries)
        retry_interval = body.get("retry_interval", default_retry_interval)

        log.info("read requested (retries=%s, retry_interval=%s)", retries, retry_interval)
        try:
            text = read_current_qr(camera, retries=retries, retry_interval=retry_interval)
        except Exception as e:
            log.exception("Error while reading QR")
            return jsonify({"status": "error", "message": str(e)}), 500

        if text is not None:
            log.info("QR decoded: %s", text)
            return jsonify({"status": "ok", "data": text}), 200

        log.info("No QR code found after retries")
        return jsonify({"status": "no_qr"}), 200

    return app


def main():
    parser = argparse.ArgumentParser(description="Standalone QR-code HTTP server (Postman-friendly)")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8080, help="HTTP port (default: 8080)")
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index (default: 0)")
    parser.add_argument("--width", type=int, default=None, help="Camera capture width")
    parser.add_argument("--height", type=int, default=None, help="Camera capture height")
    parser.add_argument("--retries", type=int, default=3,
                         help="Default frame attempts per /read call before giving up (default: 3)")
    parser.add_argument("--retry-interval", type=float, default=0.2,
                         help="Default seconds between retry attempts (default: 0.2)")
    args = parser.parse_args()

    camera = Camera(index=args.camera_index, width=args.width, height=args.height)
    app = create_app(camera, args.retries, args.retry_interval)

    log.info("QR HTTP server listening on %s:%s", args.host, args.port)
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        camera.release()


if __name__ == "__main__":
    main()
