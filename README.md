# qr-socket-server

A standalone Ubuntu application (no ROS required) that keeps a webcam
open and listens on a TCP socket for JSON commands. Send it a `read`
command and it checks the current camera frame for a QR code — retrying
a few times if nothing is found on the first try — then sends the raw
decoded QR data straight back over the socket.

## Features

- Camera is opened once at startup and reused for every request (fast, no re-init per call)
- Simple JSON-over-TCP protocol, easy to call from any language
- Configurable retry count/interval per `read` request
- Thread-safe: multiple clients can connect; camera access is serialized internally
- Ships with a systemd unit so it can run as a background service on boot

## Requirements

- Ubuntu (or any Linux with a video device and Python 3)
- Python 3.8+
- A camera accessible via OpenCV (e.g. `/dev/video0`)

## Installation

```bash
git clone https://github.com/your_username/qr-socket-server.git
cd qr-socket-server

sudo apt update
sudo apt install python3-opencv python3-pip libzbar0
pip3 install -r requirements.txt
```

## Usage

Run the server:

```bash
python3 qr_socket_server.py --port 6789 --camera-index 0
```

### Command-line options

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `6789` | TCP port |
| `--camera-index` | `0` | OpenCV camera index (`/dev/videoN`) |
| `--width` | none | Force capture width |
| `--height` | none | Force capture height |
| `--retries` | `3` | Frame attempts per `read` command before giving up |
| `--retry-interval` | `0.2` | Seconds between retry attempts |

## Protocol

Newline-delimited JSON over TCP — one JSON object per line, in both directions.

**Commands (client → server):**

```json
{"command": "read"}
{"command": "ping"}
{"command": "quit"}
```

**Responses (server → client):**

```json
{"status": "ok", "data": "<decoded_text>"}
{"status": "no_qr"}
{"status": "error", "message": "<details>"}
{"status": "pong"}
```

### Example: netcat

```bash
nc localhost 6789
{"command": "read"}
```

### Example: Python client

```bash
python3 qr_client_example.py --host 127.0.0.1 --port 6789 --command read
```

```python
import json, socket

with socket.create_connection(("127.0.0.1", 6789), timeout=5) as sock:
    sock.sendall((json.dumps({"command": "read"}) + "\n").encode())
    response = json.loads(sock.makefile("r").readline())
    print(response)
```

### Note on Postman

Postman doesn't support raw TCP sockets — it supports HTTP, WebSocket,
Socket.IO, gRPC, and MQTT, but plain TCP is still an [open feature
request](https://github.com/postmanlabs/postman-app-support/issues/12253)
with no ETA. Since this server speaks raw JSON-over-TCP (not HTTP or
WebSocket), you can't point Postman directly at it.

If you want to test/drive this from Postman anyway, you have two options:

1. **Use the provided client / netcat instead** (see above) — simplest, no extra moving parts.
2. **Add a thin HTTP-to-TCP bridge** — a tiny Flask/FastAPI endpoint that
   opens a socket to `qr_socket_server.py`, forwards the request, and
   returns the JSON response over HTTP. Then Postman just calls that
   HTTP endpoint normally. Open an issue/PR if you'd like a ready-made
   bridge script added to this repo.

## Running as a systemd service

```bash
sudo mkdir -p /opt/qr-socket-server
sudo cp qr_socket_server.py /opt/qr-socket-server/
sudo cp systemd/qr-socket-server.service /etc/systemd/system/

# edit the service file: set User=<your_linux_username>
sudo nano /etc/systemd/system/qr-socket-server.service

sudo systemctl daemon-reload
sudo systemctl enable --now qr-socket-server
sudo systemctl status qr-socket-server
```

View logs:

```bash
journalctl -u qr-socket-server -f
```

## How retries work

Each `read` request grabs a fresh frame per attempt (not just
re-decoding the same image), checking up to `--retries` times
(default 3, ~0.2s apart) before replying `{"status": "no_qr"}`. With
defaults, a `read` call takes at most roughly 0.4–0.6 seconds if
nothing is in view. Tune `--retries` / `--retry-interval` to trade off
responsiveness against how much time you want to give someone to hold
a code up to the camera.

## Troubleshooting

- **`Could not open camera index 0`** — check the device exists with
  `ls /dev/video*` and that your user is in the `video` group:
  `sudo usermod -aG video $USER` (then log out/in).
- **Permission denied on `/dev/video0`** — same fix as above.
- **`ModuleNotFoundError: pyzbar`** — make sure `libzbar0` is
  installed via apt, not just the `pyzbar` pip package.

## License

MIT — see [LICENSE](LICENSE).
