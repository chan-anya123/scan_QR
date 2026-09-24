# QR HTTP Server & Vision Inspection Engine

A high-performance, standalone Linux/Ubuntu vision application (no ROS required) designed for industrial robots, automated guided vehicles (AGVs/AMRs), and vision inspection stations. Runs hardware camera capture in a dedicated zero-latency background thread via explicit V4L2 capture and exposes instant on-demand QR code decoding via HTTP REST API and a modern pastel web dashboard.

> **Main Application Script**: [`qr_http_server.py`](file:///home/cookies/qr-socket-server/qr_http_server.py)  
> **Default Port**: `5050` | **Default Host**: `0.0.0.0` (accessible across network/robot network)

---

## Key Features

- **Ultra-Fast On-Demand QR Reading**: Continuous background capture thread drains frames via `cv2.CAP_V4L2` without queue lag, feeding fresh frames to PyZbar with grayscale fallback decoding.
- **Automatic Camera Reconnection**: Capture loop automatically handles camera disconnection and reconnects seamlessly. Recovers cleanly after consecutive frame grab failures.
- **Intelligent Video Device Detection**: Scans and parses `v4l2-ctl` to verify `Video Capture` capabilities, automatically filtering out non-capture metadata nodes (`/dev/video1`, `/dev/video3`) and exposing only true video streams.
- **Automated Hardware Autofocus Suppression**: Automatically disables continuous autofocus on initialisation and live camera switching via V4L2 kernel controls (`focus_automatic_continuous=0`, `focus_auto=0`) to prevent focus hunting during robot movement.
- **Per-Camera Isolated Setup Persistence**: Dedicated configuration files per camera index (`camera_setup_cam0.json`, `camera_setup_cam2.json`, `camera_setup_cam4.json`) automatically saved and loaded during live camera switching.
- **Smart Auto-Adjust & Anti-Overexposure**: Baseline histogram luminance analysis (~120–130 target) with pure software scaling (`cv2.convertScaleAbs`), protecting hardware contrast registers and turning off `backlight_compensation=0` on webcams like Logitech C270.
- **Dynamic Hardware Factory Reset**: Queries physical manufacturer default registers via `v4l2-ctl` and dynamically restores hardware controls and software sliders to defaults.
- **Multi-Station Setup Profiles**: Save and load named station tuning profiles (`camera_setups/Point_1.json`, `camera_setups/Station_B.json`).
- **Per-QR-Type Overwrite Capture Storage**: Draws green bounding polygon and sleek metadata badge (QR text + timestamp), saving snapshots to `captures/qr_<CONTENT>.jpg` (overwriting older images of the same QR code to prevent disk exhaustion).
- **Pastel Modern Web UI**: Responsive pastel-themed dashboard (`Plus Jakarta Sans` typography, centered navigation tabs) with live video preview, real-time sliders, and capture downloads.
- **Browser-based OTA Code Updater**: Upload updated Python code live via web UI or API with pre-validation syntax checking (`compile()`), automatic rollback backup (`qr_http_server.py.bak`), and background service restart.

---

## Project Structure

This repository contains several variations of the QR server tailored for different hardware setups and use cases. Each folder contains its own isolated server script and `docker-compose.yml` for easy deployment.

- **`single_camera/`**: The core version. Reads a single QR code from a single connected USB camera. Best for simple inspection stations.
- **`muti_qr/`**: Advanced single-camera version. Capable of scanning and returning multiple QR codes simultaneously from the same camera frame.
- **`muti_camera/`**: Multi-camera support. Designed to handle multiple physical USB cameras plugged into the same machine, allowing switching between them.
- **`ros_camera/`**: ROS (Robot Operating System) compatible version. Subscribes to ROS image topics instead of reading directly from hardware USB devices (`/dev/video*`).

---

## Installation & Requirements

### System Requirements
- Ubuntu 20.04 / 22.04 / 24.04 (or any Linux distribution with V4L2 support)
- Python 3.8+
- Connected USB Webcams or UVC Video Devices (e.g. `/dev/video0`, `/dev/video2`, `/dev/video4`)

### Setup Instructions

```bash
# 1. Clone repository
git clone git@github.com:nextroboticslab/qr-http-server.git
cd qr-http-server
# หรือ HTTPS: git clone https://github.com/nextroboticslab/qr-http-server.git

# 2. Install Linux system packages (v4l-utils and ZBar C library)
sudo apt update
sudo apt install -y v4l-utils libzbar0

# 3. Create virtual environment and install dependencies
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

---

## Quick Start & CLI Options

### Running the Server

```bash
# Run on default port 5050 (auto-detects first valid video device)
./env/bin/python3 qr_http_server.py

# Or specify custom port, camera device, and resolution
./env/bin/python3 qr_http_server.py --port 5050 --camera-index 0 --width 640 --height 480
```

Once started, open **`http://<server-ip>:5050/`** (or `http://localhost:5050/`) in your browser.

### Command Line Arguments

All arguments supported by [`qr_http_server.py`](file:///home/cookies/qr-socket-server/qr_http_server.py):

| Argument | Type | Default | Description |
|---|---|---|---|
| `--host` | `str` | `0.0.0.0` | Bind address for the HTTP server |
| `--port` | `int` | `5050` | HTTP listening port |
| `--camera-index` | `int` | `None` | OpenCV camera index (default: auto-detects first valid video capture stream) |
| `--width` | `int` | `640` | Camera capture frame width |
| `--height` | `int` | `480` | Camera capture frame height |
| `--retries` | `int` | `3` | Default frame capture attempts per `/read` call before giving up |
| `--retry-interval` | `float` | `0.2` | Default seconds between retry attempts during `/read` |

---

## Web User Interface

The web dashboard is styled in a modern pastel theme with 4 centered navigation tabs:

1. **Scanner ([`/`](http://localhost:5050/))**: Real-time MJPEG live stream, active camera dropdown selector, `Scan QR Now` action button, live decoded QR readout, and `Reset Camera Defaults` button.
2. **Adjust Camera ([`/adjust`](http://localhost:5050/adjust))**: Live slider controls (Brightness: -100..100, Contrast: 0.1..3.0, Exposure EV: -5..5, Threshold: 0..255), `Smart Auto-Adjust`, `Reset to Factory Defaults`, and Multi-Station Profile saving/loading.
3. **Captured Screen ([`/cap_screen`](http://localhost:5050/cap_screen))**: Displays the latest detected QR snapshot with green bounding polygon and timestamp badge, elapsed time counter, and an instant `Download Snapshot` button.
4. **Code Updater ([`/upload`](http://localhost:5050/upload))**: Browser-based OTA code updater with pre-save syntax validation (`compile()`), automatic rollback backup (`qr_http_server.py.bak`), and background systemd service restart.

---

## Complete REST API Reference

### Core Scanning & Diagnostics

| Endpoint | Method(s) | Description | Parameters | Success Response | Error Response |
|---|---|---|---|---|---|
| `/read` | `GET`, `POST` | Trigger QR code scan | Optional JSON: `{"retries": 3, "retry_interval": 0.2}` | **200 OK**<br>`{"status": "ok", "data": "CONTENT", "timestamp": 1786431511.0}`<br>or **200 OK (no QR)**<br>`{"status": "no_qr", "message": " ", "data": " ", "timestamp": 1786431511.0}` | **503 Service Unavailable**<br>`{"status": "error", "error_code": "CAMERA_NOT_READY", "message": "Camera is disconnected or frame grabber is failing", "timestamp": ...}`<br>**500 Internal Error**<br>`{"status": "error", "error_code": "SCAN_ERROR", "message": "...", "timestamp": ...}` |
| `/health` | `GET` | Health metrics & diagnostics | None | **200 OK**<br>`{"status": "ok", "camera_connected": true, "camera_index": 0, "has_latest_frame": true, "seconds_since_last_frame": 0.02, "uptime_seconds": 320.5, "timestamp": 1786431511.0}` | Status `"degraded"` if frame grabber inactive > 3s |
| `/ping` | `GET` | Connectivity ping | None | **200 OK**<br>`{"status": "pong", "timestamp": 1786431511.0}` | — |

### Camera Control & Tuning

| Endpoint | Method(s) | Description | Parameters | Example Response |
|---|---|---|---|---|
| `/video_feed` | `GET` | Multipart MJPEG video stream | None | `<img src="/video_feed">` (`multipart/x-mixed-replace`) |
| `/cameras` | `GET` | List verified video devices & active index | None | `{"status": "ok", "active_index": 0, "available_cameras": [{"index": 0, "name": "Logitech HD Webcam (/dev/video0)"}]}` |
| `/cameras/switch` | `POST` | Switch active camera index live | JSON: `{"index": 2}` | `{"status": "ok", "message": "Switched to camera index 2", "active_index": 2}` |
| `/settings` | `GET` | Get active software image parameters | None | `{"brightness": 0, "contrast": 1.0, "exposure": 0, "threshold": 0}` |
| `/settings` | `POST` | Update software image parameters | JSON: `{"brightness": 10, "contrast": 1.2, "exposure": 0, "threshold": 0}` | `{"status": "ok", "settings": {"brightness": 10, "contrast": 1.2, "exposure": 0, "threshold": 0}}` |
| `/settings/auto_adjust` | `POST` | Execute Smart Auto-Adjust baseline | None | `{"status": "ok", "message": "V4L2 Hardware Auto-Adjust baseline calculated and applied", "settings": {...}}` |
| `/settings/reset` | `POST` | Restore physical V4L2 hardware defaults | None | `{"status": "ok", "message": "Camera hardware & software restored to dynamic camera defaults", "settings": {...}}` |

### Profiles & Storage

| Endpoint | Method(s) | Description | Parameters / Query | Output Format |
|---|---|---|---|---|
| `/profiles` | `GET` | List all saved station profile files | None | `{"status": "ok", "profiles": [{"filename": "Point_1.json", "name": "Point_1", "settings": {...}, "saved_at": 1786431511.0, "saved_at_iso": "2026-09-01 16:00:00"}]}` |
| `/profiles/save` | `POST` | Save active settings as a named profile | JSON: `{"name": "Point_1"}` | `{"status": "ok", "message": "Camera setup saved to profile file Point_1.json", "data": {...}}` |
| `/profiles/load` | `POST` | Load and apply a named station profile | JSON: `{"name": "Point_1"}` or `{"filename": "Point_1.json"}` | `{"status": "ok", "message": "Profile 'Point_1' loaded and applied successfully", "settings": {...}}` |
| `/cap_screen` | `GET`, `POST` | Last captured QR snapshot | • Default: HTML page<br>• `?raw=1` or `?image=1`: Raw JPEG<br>• `?json=1` or `is_json`: JSON info | • HTML preview with instant download button<br>• Binary JPEG image (`image/jpeg`)<br>• JSON: `{"status": "ok", "qr_data": "...", "timestamp": ..., "filename": "qr_xxx.jpg", "image_url": "..."}` |
| `/captures/<filename>` | `GET` | Direct download of saved capture file | Path: `<filename>` (e.g. `qr_PALLET_01.jpg`) | Binary JPEG image |

### OTA Code Updater

| Endpoint | Method(s) | Description | Parameters | Output |
|---|---|---|---|---|
| `/upload` | `GET` | OTA Code Updater Web Dashboard | None | HTML page for uploading updated `.py` script |
| `/upload` | `POST` | Validate, backup, write, and restart service | Multipart form: `file` (.py) | `{"status": "ok", "message": "Code verified, backup created, and saved! Service restarting in 1s..."}` |

---

## Code Examples

### cURL

```bash
# 1. Check connectivity
curl http://localhost:5050/ping

# 2. Check health and frame grabber status
curl http://localhost:5050/health

# 3. Trigger QR Scan (GET - uses server defaults)
curl http://localhost:5050/read

# 4. Trigger QR Scan (POST - custom retries and interval)
curl -X POST http://localhost:5050/read \
  -H "Content-Type: application/json" \
  -d '{"retries": 5, "retry_interval": 0.15}'

# 5. List available verified cameras
curl http://localhost:5050/cameras

# 6. Switch active camera to index 2 live
curl -X POST http://localhost:5050/cameras/switch \
  -H "Content-Type: application/json" \
  -d '{"index": 2}'

# 7. Run Smart Auto-Adjust
curl -X POST http://localhost:5050/settings/auto_adjust

# 8. Load named Station Profile
curl -X POST http://localhost:5050/profiles/load \
  -H "Content-Type: application/json" \
  -d '{"name": "Point_1"}'

# 9. Download latest annotated QR image
curl -O http://localhost:5050/cap_screen?raw=1

# 10. OTA upload updated Python script
curl -F "file=@qr_http_server.py" http://localhost:5050/upload
```

### Python (`requests`)

```python
import requests

SERVER_URL = "http://192.168.10.19:5050"

# 1. Verify service health before operation
health = requests.get(f"{SERVER_URL}/health").json()
if health.get("status") != "ok":
    raise SystemError(f"Camera service degraded or disconnected: {health}")

# 2. Switch camera or load station-specific lighting profile
requests.post(f"{SERVER_URL}/profiles/load", json={"name": "Station_B"})

# 3. Trigger QR read (supports GET or POST with retry tuning)
resp = requests.post(f"{SERVER_URL}/read", json={"retries": 4, "retry_interval": 0.2})
result = resp.json()

if result.get("status") == "ok":
    print(f"Decoded QR Content: {result['data']} (timestamp: {result.get('timestamp')})")
elif result.get("status") == "no_qr":
    print("No QR detected in view.")
else:
    print(f"Error ({result.get('error_code')}): {result.get('message')}")
```

### Node-RED Industrial Flow

```
[AGV Arrives at Station]
       │
       ▼
[HTTP Request: POST /profiles/load {"name": "Station_Pallet"}]
       │
       ▼
[HTTP Request: POST /read {"retries": 4, "retry_interval": 0.2}]
       │
       ▼
[Switch Node: msg.payload.status]
       ├── "ok"    ──> [Extract msg.payload.data -> Send to PLC / WMS]
       ├── "no_qr" ──> [Trigger AGV Alignment Adjustment / Retry]
       └── "error" ──> [Raise Inspection Station Alarm]
```

---

## Systemd Background Service Setup

To enable [`qr_http_server.py`](file:///home/cookies/qr-socket-server/qr_http_server.py) to run continuously in the background and start automatically on Linux boot:

```bash
# 1. Copy the provided service file to systemd directory
sudo cp /home/cookies/qr-socket-server/systemd/qr-http-server.service /etc/systemd/system/

# 2. Reload systemd daemon and enable service
sudo systemctl daemon-reload
sudo systemctl enable qr-http-server
sudo systemctl start qr-http-server

# 3. Check service status and live logs
sudo systemctl status qr-http-server
sudo journalctl -u qr-http-server -f
```

The service unit file ([`systemd/qr-http-server.service`](file:///home/cookies/qr-socket-server/systemd/qr-http-server.service)) is configured with `Restart=always` and `RestartSec=5` for high industrial availability.

---

## Troubleshooting

- **Camera device busy or disconnected (`CAMERA_NOT_READY` / 503)**:
  - Check connected cameras with `v4l2-ctl --list-devices` or query `GET /cameras`.
  - Verify your Linux user is in the `video` group: `sudo usermod -aG video $USER` (log out and back in).
- **ZBar library missing (`ImportError: Unable to find zbar shared library`)**:
  - Install the system library: `sudo apt-get install -y libzbar0`.
- **Image overexposed or whitewashed under bright industrial lights**:
  - Hit `Smart Auto-Adjust` via the UI or `POST /settings/auto_adjust`. This disables webcam hardware backlight compensation (`backlight_compensation=0`) and calculates optimal software brightness and contrast.
- **Autofocus hunting when robot moves**:
  - The server automatically suppresses autofocus. To verify hardware controls manually: `v4l2-ctl -d /dev/video0 --list-ctrls | grep -i focus`.

---

## License

MIT License. Free for commercial, robotic, and industrial automation use.
