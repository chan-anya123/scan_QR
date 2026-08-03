# qr-socket-server

A standalone Ubuntu application (no ROS required) that keeps a webcam
open and exposes an on-demand QR-code reader. Two interchangeable
server implementations are included:

| File | Transport | Best for |
|---|---|---|
| `qr_http_server.py` | HTTP (Flask) | Postman, curl, browsers, most integrations |
| `qr_socket_server_tcp.py` | Raw TCP + JSON lines | Lightweight embedded clients, no HTTP stack needed |

Both share the same camera-handling and retry logic — pick whichever
transport fits your client. **The HTTP version is recommended** since
it works directly with Postman and virtually any HTTP tooling.

## Features

- Camera is opened once at startup and reused for every request (fast, no re-init per call)
- Configurable retry count/interval per read request (grabs a fresh frame each attempt)
- Thread-safe: multiple clients can connect; camera access is serialized internally
- Ships with systemd units so either server can run as a background service on boot
- Includes a ready-to-import Postman collection

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

---

## HTTP server (Postman-friendly) — recommended

### Run it

```bash
python3 qr_http_server.py --port 8080 --camera-index 0
```

| Flag | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8080` | HTTP port |
| `--camera-index` | `0` | OpenCV camera index (`/dev/videoN`) |
| `--width` / `--height` | none | Force capture resolution |
| `--retries` | `3` | Default frame attempts per `/read` call before giving up |
| `--retry-interval` | `0.2` | Default seconds between retry attempts |

### Endpoints

**`GET /ping`**

```json
200 {"status": "pong"}
```

**`POST /read`**

Optional JSON body to override the server's defaults for this call:

```json
{"retries": 5, "retry_interval": 0.3}
```

Responses:

```json
200 {"status": "ok", "data": "<decoded_text>"}
200 {"status": "no_qr"}
500 {"status": "error", "message": "<details>"}
```

### Using it from Postman

1. Open Postman → **Import** → select `postman_collection.json` from this repo.
2. The collection has a `base_url` variable (defaults to `http://localhost:8080`) — edit it if your server runs elsewhere.
3. Run **Ping** to confirm connectivity, then **Read QR Code** to trigger a read. The retry settings in the request body are optional — remove them to use the server's `--retries` / `--retry-interval` defaults.

### Using it from curl

```bash
curl http://localhost:8080/ping

curl -X POST http://localhost:8080/read \
  -H "Content-Type: application/json" \
  -d '{"retries": 3, "retry_interval": 0.2}'
```

### Using it from Python

```bash
python3 qr_client_example.py --host 127.0.0.1 --port 8080 --command read
```

```python
import requests

resp = requests.post("http://127.0.0.1:8080/read", json={"retries": 3})
print(resp.json())
```

---

## Raw TCP server (alternative, no HTTP stack needed)

For lightweight clients that shouldn't need an HTTP library, `qr_socket_server_tcp.py` speaks newline-delimited JSON directly over a TCP socket.

```bash
python3 qr_socket_server_tcp.py --port 6789 --camera-index 0
```

Same `--retries` / `--retry-interval` flags apply. Protocol:

```json
Client -> Server:  {"command": "read"}
Server -> Client:  {"status": "ok", "data": "<text>"} | {"status": "no_qr"}

Client -> Server:  {"command": "ping"}
Server -> Client:  {"status": "pong"}
```

Quick test:

```bash
nc localhost 6789
{"command": "read"}
```

Postman does not support raw TCP sockets (it covers HTTP, WebSocket,
Socket.IO, gRPC, and MQTT, but plain TCP remains an unresolved [open
feature
request](https://github.com/postmanlabs/postman-app-support/issues/12253)).
Use the HTTP server above if Postman testing matters to you.

---

## Running as a systemd service

**HTTP version:**

```bash
sudo mkdir -p /opt/qr-http-server
sudo cp qr_http_server.py /opt/qr-http-server/
sudo cp systemd/qr-http-server.service /etc/systemd/system/

# edit the service file: set User=<your_linux_username>
sudo nano /etc/systemd/system/qr-http-server.service

sudo systemctl daemon-reload
sudo systemctl enable --now qr-http-server
sudo systemctl status qr-http-server
```

View logs:

```bash
journalctl -u qr-http-server -f
```

(The raw TCP version follows the same pattern — see `qr_socket_server_tcp.py` and adapt the service file accordingly.)

## How retries work

Each read grabs a fresh frame per attempt (not just re-decoding the
same image), checking up to `retries` times (default 3, ~0.2s apart)
before reporting no QR code found. With defaults, a request takes at
most roughly 0.4–0.6 seconds if nothing is in view. Tune `retries` /
`retry_interval` (via CLI flags for server-wide defaults, or per-request
in the `/read` body) to trade off responsiveness against how much time
you want to give someone to hold a code up to the camera.

## Troubleshooting

- **`Could not open camera index 0`** — check the device exists with
  `ls /dev/video*` and that your user is in the `video` group:
  `sudo usermod -aG video $USER` (then log out/in).
- **Permission denied on `/dev/video0`** — same fix as above.
- **`ModuleNotFoundError: pyzbar`** — make sure `libzbar0` is
  installed via apt, not just the `pyzbar` pip package.
- **Postman can't connect** — make sure you're hitting the HTTP
  server (`qr_http_server.py`, default port `8080`), not the raw TCP
  one (default port `6789`).

## License

MIT — see [LICENSE](LICENSE).
