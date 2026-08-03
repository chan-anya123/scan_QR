#!/usr/bin/env python3
"""
qr_client_example.py

Minimal example client for qr_socket_server.py (JSON protocol).

Usage:
    python3 qr_client_example.py                # sends "read", prints result
    python3 qr_client_example.py --host 192.168.1.50 --port 6789
    python3 qr_client_example.py --command ping
"""

import argparse
import json
import socket


def send_command(host: str, port: int, command: str, recv_timeout: float = 5.0) -> dict:
    with socket.create_connection((host, port), timeout=5.0) as sock:
        sock.settimeout(recv_timeout)
        request = json.dumps({"command": command}) + "\n"
        sock.sendall(request.encode("utf-8"))
        line = sock.makefile("r").readline().strip()
        return json.loads(line)


def main():
    parser = argparse.ArgumentParser(description="Example client for the QR socket server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=6789)
    parser.add_argument("--command", default="read", help="read | ping | quit")
    args = parser.parse_args()

    response = send_command(args.host, args.port, args.command)
    print(f"Server response: {response}")

    status = response.get("status")
    if status == "ok":
        print(f"Decoded QR data: {response['data']}")
    elif status == "no_qr":
        print("No QR code found in the current frame.")
    elif status == "error":
        print(f"Server error: {response.get('message')}")


if __name__ == "__main__":
    main()
