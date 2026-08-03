#!/usr/bin/env python3
"""
qr_mqtt_server.py

Standalone Ubuntu application (no ROS required).

Keeps a camera open and talks over MQTT instead of HTTP or raw TCP.
Unlike the request/response HTTP and TCP versions, MQTT is
publish/subscribe: a client publishes a "read" request to a command
topic, and this server publishes the result to a separate response
topic. Any number of clients can subscribe to the response topic.

Reuses the same camera-grab-and-decode logic as qr_http_server.py /
qr_socket_server_tcp.py.

Topics (defaults, all configurable via CLI flags):
    qr/read     <- client publishes here to trigger a read.
                   Payload is optional JSON: {"retries": 5, "retry_interval": 0.3,
                                               "request_id": "any-string-you-choose"}
                   An empty/non-JSON payload just uses server defaults.

    qr/result   -> server publishes the outcome here:
                   {"status": "ok", "data": "<text>", "request_id": "..."}
                   {"status": "no_qr", "request_id": "..."}
                   {"status": "error", "message": "...", "request_id": "..."}

    qr/ping     <- client publishes here (any payload) to check liveness
    qr/pong     -> server publishes {"status": "pong"} in response

"request_id" is optional and simply echoed back unchanged, so a
client can correlate a specific /read publish with the matching
/result message if multiple clients are sharing the same topics.

Requires an MQTT broker (e.g. Mosquitto). See README.md for how to
install and run one locally for testing.

Run:
    python3 qr_mqtt_server.py --broker-host localhost --broker-port 1883
"""

import argparse
import json
import logging
import threading
import time

import cv2
import paho.mqtt.client as mqtt
from pyzbar.pyzbar import decode as decode_qr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("qr_mqtt_server")


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


class QRMqttServer:
    def __init__(self, args, camera: Camera):
        self.args = args
        self.camera = camera

        self.client = mqtt.Client(client_id=args.client_id, clean_session=True)
        if args.username:
            self.client.username_pw_set(args.username, args.password)

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            log.info("Connected to broker %s:%s", self.args.broker_host, self.args.broker_port)
            client.subscribe(self.args.command_topic, qos=self.args.qos)
            client.subscribe(self.args.ping_topic, qos=self.args.qos)
            log.info("Subscribed to %s and %s", self.args.command_topic, self.args.ping_topic)
        else:
            log.error("Failed to connect to broker, rc=%s", rc)

    def _on_message(self, client, userdata, msg):
        if msg.topic == self.args.ping_topic:
            self._handle_ping()
        elif msg.topic == self.args.command_topic:
            self._handle_read(msg.payload)

    def _handle_ping(self):
        log.info("ping received")
        self.client.publish(self.args.pong_topic, json.dumps({"status": "pong"}), qos=self.args.qos)

    def _handle_read(self, payload: bytes):
        request_id = None
        retries = self.args.retries
        retry_interval = self.args.retry_interval

        raw = payload.decode("utf-8", errors="ignore").strip()
        if raw:
            try:
                body = json.loads(raw)
                request_id = body.get("request_id")
                retries = body.get("retries", retries)
                retry_interval = body.get("retry_interval", retry_interval)
            except json.JSONDecodeError:
                log.warning("Non-JSON payload on %s, using server defaults", self.args.command_topic)

        log.info("read requested (retries=%s, retry_interval=%s, request_id=%s)",
                  retries, retry_interval, request_id)

        try:
            text = read_current_qr(self.camera, retries=retries, retry_interval=retry_interval)
        except Exception as e:
            log.exception("Error while reading QR")
            result = {"status": "error", "message": str(e)}
        else:
            if text is not None:
                log.info("QR decoded: %s", text)
                result = {"status": "ok", "data": text}
            else:
                log.info("No QR code found after retries")
                result = {"status": "no_qr"}

        if request_id is not None:
            result["request_id"] = request_id

        self.client.publish(self.args.result_topic, json.dumps(result), qos=self.args.qos)

    def run(self):
        self.client.connect(self.args.broker_host, self.args.broker_port, keepalive=60)
        self.client.loop_forever()


def main():
    parser = argparse.ArgumentParser(description="Standalone QR-code MQTT server")
    parser.add_argument("--broker-host", default="localhost", help="MQTT broker host (default: localhost)")
    parser.add_argument("--broker-port", type=int, default=1883, help="MQTT broker port (default: 1883)")
    parser.add_argument("--client-id", default="qr_mqtt_server", help="MQTT client ID")
    parser.add_argument("--username", default=None, help="MQTT username (if broker requires auth)")
    parser.add_argument("--password", default=None, help="MQTT password (if broker requires auth)")
    parser.add_argument("--qos", type=int, default=1, choices=[0, 1, 2], help="MQTT QoS level (default: 1)")

    parser.add_argument("--command-topic", default="qr/read", help="Topic to listen for read requests on")
    parser.add_argument("--result-topic", default="qr/result", help="Topic to publish results to")
    parser.add_argument("--ping-topic", default="qr/ping", help="Topic to listen for liveness checks on")
    parser.add_argument("--pong-topic", default="qr/pong", help="Topic to publish liveness replies to")

    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index (default: 0)")
    parser.add_argument("--width", type=int, default=None, help="Camera capture width")
    parser.add_argument("--height", type=int, default=None, help="Camera capture height")
    parser.add_argument("--retries", type=int, default=3,
                         help="Default frame attempts per read before giving up (default: 3)")
    parser.add_argument("--retry-interval", type=float, default=0.2,
                         help="Default seconds between retry attempts (default: 0.2)")
    args = parser.parse_args()

    camera = Camera(index=args.camera_index, width=args.width, height=args.height)
    server = QRMqttServer(args, camera)

    log.info("QR MQTT server starting, connecting to %s:%s", args.broker_host, args.broker_port)
    try:
        server.run()
    except KeyboardInterrupt:
        log.info("Shutting down (Ctrl+C)")
    finally:
        camera.release()


if __name__ == "__main__":
    main()
