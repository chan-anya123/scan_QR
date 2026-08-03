#!/usr/bin/env python3
"""
qr_mqtt_client_example.py

Minimal example client for qr_mqtt_server.py. Publishes a read request
and waits for the matching result on the response topic.

Usage:
    python3 qr_mqtt_client_example.py
    python3 qr_mqtt_client_example.py --broker-host 192.168.1.50
    python3 qr_mqtt_client_example.py --command ping
"""

import argparse
import json
import time
import uuid

import paho.mqtt.client as mqtt


def main():
    parser = argparse.ArgumentParser(description="Example MQTT client for the QR server")
    parser.add_argument("--broker-host", default="localhost")
    parser.add_argument("--broker-port", type=int, default=1883)
    parser.add_argument("--command", default="read", choices=["read", "ping"])
    parser.add_argument("--command-topic", default="qr/read")
    parser.add_argument("--result-topic", default="qr/result")
    parser.add_argument("--ping-topic", default="qr/ping")
    parser.add_argument("--pong-topic", default="qr/pong")
    parser.add_argument("--timeout", type=float, default=10.0, help="Seconds to wait for a response")
    args = parser.parse_args()

    request_id = str(uuid.uuid4())
    response_holder = {}

    def on_connect(client, userdata, flags, rc):
        topic = args.pong_topic if args.command == "ping" else args.result_topic
        client.subscribe(topic)

    def on_message(client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode("utf-8"))
        except json.JSONDecodeError:
            return
        if args.command == "ping" or data.get("request_id") == request_id:
            response_holder["data"] = data

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(args.broker_host, args.broker_port, keepalive=30)
    client.loop_start()

    time.sleep(0.5)  # give the subscribe a moment to land before publishing

    if args.command == "ping":
        client.publish(args.ping_topic, "{}")
    else:
        client.publish(args.command_topic, json.dumps({"request_id": request_id}))

    deadline = time.monotonic() + args.timeout
    while "data" not in response_holder and time.monotonic() < deadline:
        time.sleep(0.1)

    client.loop_stop()
    client.disconnect()

    if "data" in response_holder:
        print(f"Response: {response_holder['data']}")
    else:
        print("Timed out waiting for a response. Is qr_mqtt_server.py running and connected to the broker?")


if __name__ == "__main__":
    main()
