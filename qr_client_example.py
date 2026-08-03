#!/usr/bin/env python3
"""
qr_client_example.py

Minimal example client for qr_http_server.py.

Usage:
    python3 qr_client_example.py
    python3 qr_client_example.py --host 192.168.1.50 --port 8080
    python3 qr_client_example.py --command ping
"""

import argparse
import json

import requests


def main():
    parser = argparse.ArgumentParser(description="Example client for the QR HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--command", default="read", choices=["read", "ping"])
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--retry-interval", type=float, default=None)
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"

    if args.command == "ping":
        resp = requests.get(f"{base_url}/ping", timeout=5)
    else:
        body = {}
        if args.retries is not None:
            body["retries"] = args.retries
        if args.retry_interval is not None:
            body["retry_interval"] = args.retry_interval
        resp = requests.post(f"{base_url}/read", json=body, timeout=15)

    data = resp.json()
    print(f"HTTP {resp.status_code}: {json.dumps(data)}")

    if data.get("status") == "ok":
        print(f"Decoded QR data: {data['data']}")
    elif data.get("status") == "no_qr":
        print("No QR code found after retries.")
    elif data.get("status") == "error":
        print(f"Server error: {data.get('message')}")


if __name__ == "__main__":
    main()
