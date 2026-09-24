# Multi-Camera QR Vision Server (`muti_cam.py`)

A high-performance, standalone Linux/Ubuntu multi-camera streaming and vision inspection engine. Designed for industrial robotics, AGVs/AMRs, quality inspection stations, and multi-angle conveyor lines that require simultaneous streaming, independent camera parameter tuning, and concurrent or targeted QR code decoding across multiple USB/UVC cameras.

---

## Table of Contents

1. [Key Features](#key-features)
2. [Architecture Overview](#architecture-overview)
3. [Requirements & Installation](#requirements--installation)
4. [Quick Start](#quick-start)
5. [Command-Line Arguments](#command-line-arguments)
6. [Web Dashboard Interface](#web-dashboard-interface)
7. [REST API Documentation](#rest-api-documentation)
   - [Diagnostics & Status](#1-diagnostics--status)
   - [Camera Management & Toggling](#2-camera-management--toggling)
   - [Live Video Feeds](#3-live-video-feeds)
   - [QR Code Reading (Concurrent & Targeted)](#4-qr-code-reading)
   - [Camera Tuning & Auto-Adjustment](#5-camera-tuning--adjustment)
   - [Captures & Snapshots](#6-captures--snapshots)
   - [OTA Code Updates](#7-ota-code-updates)
8. [Code Integration Examples](#code-integration-examples)
   - [cURL](#curl)
   - [Python (`requests`)](#python-requests)
   - [Node-RED Integration](#node-red-flow)
9. [Systemd Service Setup](#systemd-service-setup)
10. [Performance & USB Bandwidth Tips](#performance--usb-bandwidth-tips)

---

## Key Features

- **Concurrent Multi-Camera Capture**: Runs independent background capture threads for every connected USB camera with zero-latency frame buffers (`cv2.CAP_PROP_BUFFERSIZE = 1`).
- **Hardware MJPG Compression**: Forces hardware MJPEG encoding (`cv2.VideoWriter_fourcc(*'MJPG')`) at the V4L2 device level, preventing USB 2.0/3.0 bus saturation when streaming multiple 30fps webcams simultaneously.
- **Simultaneous & Targeted QR Scanning**:
  - **All-Camera Scan (`POST /read`)**: Rapidly scans across all enabled camera feeds in parallel and returns the first decoded QR code with source camera details.
  - **Targeted Cam Scan (`POST /read {"camera": 2}`)**: Decodes frames specifically from the specified camera index.
- **Hardware Autofocus Suppression**: Queries and disables continuous autofocus across all connected devices via `v4l2-ctl` kernel controls (`focus_automatic_continuous=0`, `focus_auto=0`) to eliminate focus hunting during robot motion.
- **Per-Camera Isolated Persistent Profiles**: Saves and restores software and hardware parameters per camera index (`camera_setup_cam0.json`, `camera_setup_cam2.json`, etc.).
- **Live Camera Enable/Disable Toggling**: Temporarily disable inactive cameras live via Web UI or API to free USB bus bandwidth and CPU cycles without stopping the server.
- **Smart Histogram Auto-Adjust**: Analyzes real-time image luminance histograms (~120–130 target mean) and calculates balanced brightness/contrast offsets while keeping hardware registers safe.
- **Hardware Factory Reset**: Reads physical default registers from hardware via `v4l2-ctl` and resets camera parameters with one click.
- **Modern Responsive Pastel Web UI**: Centered navigation tabs (`/`, `/adjust`, `/cap_screen`, `/upload`) with a multi-camera grid layout, individual camera action controls, and real-time status indicators.
- **Over-The-Air (OTA) Code Updater**: Browser-based code upload with AST syntax validation (`compile()`), automatic rollback backup (`.bak`), and hot saving.

---

## Architecture Overview

```
                            ┌─────────────────────────────────────────────────────────┐
                            │                    muti_cam.py                          │
                            │                                                         │
                            │   ┌─────────────────────────────────────────────────┐   │
                            │   │             MultiCameraEngine                   │   │
                            │   │                                                 │   │
  /dev/video0 (UVC) ───────>│   │  [CameraDevice 0] ──> Thread 0 (MJPG / V4L2)    │   │
  /dev/video2 (UVC) ───────>│   │  [CameraDevice 2] ──> Thread 1 (MJPG / V4L2)    │   │
  /dev/video4 (UVC) ───────>│   │  [CameraDevice 4] ──> Thread 2 (MJPG / V4L2)    │   │
                            │   └─────────────────────────────────────────────────┘   │
                            │                           │                             │
                            │   ┌─────────────────────────────────────────────────┐   │
                            │   │            Flask Web & REST API                 │   │
                            │   │                                                 │   │
                            │   │   • Web Dashboard (`muti_templates/`)           │   │
                            │   │   • REST Endpoints (/read, /cameras, /settings) │   │
                            │   │   • MJPEG Streams (/video_feed/<index>)         │   │
                            │   │   • Capture Storage (captures/qr_camX_*.jpg)    │   │
                            │   └─────────────────────────────────────────────────┘   │
                            └───────────────────────────┬─────────────────────────────┘
                                                        │
                                    ┌───────────────────┴───────────────────┐
                                    ▼                                       ▼
                         Robot / AMR / PLC / Node-RED              Browser Dashboard
```

---

## Requirements & Installation

### Hardware & OS Requirements
- Linux (Ubuntu 20.04, 22.04, 24.04, Debian 11/12, Raspberry Pi OS 64-bit)
- Python 3.8+
- 1 or more USB Webcams / UVC Cameras (e.g., Logitech C270, C920, C930, industrial USB3 cameras)

### Setup Instructions

```bash
# 1. Clone or navigate to the repository
cd /home/cookies/qr-socket-server

# 2. Install Linux system packages (V4L2 utilities & ZBar library)
sudo apt update
sudo apt install -y v4l-utils libzbar0

# 3. Create virtual environment and install Python packages
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

---

## Quick Start

### 1. Launch the Multi-Camera Server

```bash
# Start server on default port 5050
./env/bin/python3 muti_cam.py
```

### 2. Access the Dashboard
Open your browser and navigate to:
```
http://<server-ip>:5050/
```
*(e.g., `http://localhost:5050/` or `http://192.168.1.100:5050/`)*

---

## Command-Line Arguments

You can customize bind parameters, resolution, and scanning retry behaviors:

```bash
./env/bin/python3 muti_cam.py [OPTIONS]
```

| Argument | Type | Default | Description |
|---|---|---|---|
| `--host` | `str` | `0.0.0.0` | IP address to bind the HTTP server to |
| `--port` | `int` | `5050` | Port to listen on |
| `--width` | `int` | `640` | Requested frame capture width per camera |
| `--height` | `int` | `480` | Requested frame capture height per camera |
| `--retries` | `int` | `3` | Default frame capture attempts per `/read` call |
| `--retry-interval`| `float` | `0.2` | Seconds between scan retry attempts |

#### Example: Running with 1280x720 Resolution on Port 8080
```bash
./env/bin/python3 muti_cam.py --port 8080 --width 1280 --height 720 --retries 5 --retry-interval 0.15
```

---

## Web Dashboard Interface

The multi-camera web interface is located in [`muti_templates/`](file:///home/cookies/qr-socket-server/muti_templates) and features 4 dedicated navigation views:

### 1. Scanner Dashboard (`/`)
- **Live Multi-Camera Grid**: Displays simultaneous real-time MJPEG streams for every connected camera (`/video_feed/<index>`).
- **Individual Enable/Disable Controls**: Toggle individual camera hardware captures on and off live. Disabled cameras show a clean standby badge and release hardware resources.
- **Global & Targeted QR Scan**:
  - `Scan All Cameras`: Decodes across all enabled cameras simultaneously.
  - `Scan`: Triggers a fast scan on a single camera.
- **Reset Cam Defaults**: Restores camera hardware and software settings to factory defaults.
- **Live Scan Banner**: Shows the latest scanned QR code text, camera source badge, and timestamp.

### 2. Adjust Camera (`/adjust`)
- **Camera Selector**: Dropdown to switch between detected cameras (`Camera 0`, `Camera 2`, etc.).
- **Live Tuning Controls**:
  - **Brightness**: `-100` to `100` (software linear shift).
  - **Exposure**: `-5` to `5` (software EV scale factor).
  - **Contrast**: `0.1` to `3.0` (software scaling multiplier).
  - **Threshold**: `0` (disabled) to `255` (binary thresholding for high-glare surfaces).
- **Smart Auto-Adjust**: Computes optimal contrast and brightness shifts based on real-time histogram analysis.
- **Factory Reset**: Resets V4L2 registers and software sliders to defaults.

### 3. Captured Screen (`/cap_screen`)
- **Latest QR Snapshot**: Displays the last captured frame containing a detected QR code.
- **Visual Annotations**: Highlights the QR code with a green polygon outline and adds a dark metadata badge with QR text, camera index, and timestamp.
- **Download Snapshot**: Direct download button to save the annotated JPG image.
- **API Formats**: Access raw image via `?raw=1` or metadata via `?json=1`.

### 4. OTA Code Updater (`/upload`)
- **Browser-Based Python Upload**: Upload updated server scripts directly from the browser.
- **Pre-Save Syntax Validation**: Validates syntax using Python's `compile()` AST parser to prevent breaking the running application.
- **Automatic Rollback Backup**: Automatically backs up the previous file version to `muti_cam.py.bak`.

---

## REST API Documentation

All API responses are formatted in JSON (except for MJPEG video streams and raw snapshot downloads).

### API Summary Table

| Category | Endpoint | Method | Request Payload / Params | Response Summary |
|---|---|---|---|---|
| **Core QR Scan** | `/read` | `POST` / `GET` | `{"retries": 3, "retry_interval": 0.2}` *(All cams)*<br>`{"camera": 2, "retries": 4}` *(Targeted cam)* | Returns decoded QR data + source camera info (`{"status": "ok", "data": "...", "camera_index": 2}`) |
| **Diagnostics** | `/health` | `GET` | None | Health status, connection state & settings of all cameras |
| **Diagnostics** | `/ping` | `GET` | None | Server connectivity check (`{"status": "pong"}`) |
| **Camera Mgmt** | `/cameras` | `GET` | None | List of verified connected camera devices and diagnostics |
| **Camera Mgmt** | `/cameras/toggle` | `POST` | `{"camera": 0}` or `{"camera": 0, "enabled": false}` | Toggle or set enable/disable state for a camera |
| **Camera Mgmt** | `/cameras/enable` | `POST` | `{"camera": 0}` | Enable video capture on specified camera |
| **Camera Mgmt** | `/cameras/disable` | `POST` | `{"camera": 0}` | Disable capture & release hardware for specified camera |
| **Video Feeds** | `/video_feed/<index>` | `GET` | None | Multipart MJPEG live stream for camera `index` (e.g. `/video_feed/0`) |
| **Video Feeds** | `/video_feed` | `GET` | `?camera=0` *(optional)* | Multipart MJPEG live stream for default or query camera |
| **Camera Tuning**| `/settings` | `GET` | `?camera=0` *(optional)* | Get software image settings (`brightness`, `contrast`, `exposure`, `threshold`) |
| **Camera Tuning**| `/settings` | `POST` | `{"camera": 0, "brightness": 10, "contrast": 1.2}` | Update image adjustments and save to `camera_setup_cam0.json` |
| **Camera Tuning**| `/settings/auto_adjust`| `POST` | `{"camera": 0}` | Calculate auto brightness & contrast from real-time histogram |
| **Camera Tuning**| `/settings/reset` | `POST` | `{"camera": 0}` | Reset hardware V4L2 registers and software settings to defaults |
| **Captures** | `/cap_screen` | `GET` | `?raw=1` (Raw JPEG)<br>`?json=1` (JSON data) | Latest detected QR snapshot with bounding box & camera badge |
| **Captures** | `/captures/<file>` | `GET` | None | Direct image download for saved capture file |
| **OTA Updates** | `/upload` | `POST` | `multipart/form-data` (`file`: `.py`) | Upload Python code with AST syntax check & `.bak` rollback |
| **Web UI** | `/` | `GET` | None | Multi-camera live grid scanner dashboard |
| **Web UI** | `/adjust` | `GET` | None | Multi-camera parameter adjustment & tuning page |
| **Web UI** | `/upload` | `GET` | None | OTA code updater page |

---

### 1. Diagnostics & Status

#### `GET /ping`
Lightweight connectivity ping.
- **Response `200 OK`**:
```json
{
  "status": "pong",
  "timestamp": 1787123456.789
}
```

#### `GET /health`
System diagnostic health and status summary for all cameras.
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "camera_count": 2,
  "cameras": [
    {
      "camera_index": 0,
      "camera_name": "HD Pro Webcam C920 (/dev/video0)",
      "connected": true,
      "enabled": true,
      "has_frame": true,
      "seconds_since_last_frame": 0.03,
      "status": "ok",
      "uptime_seconds": 142.5,
      "settings": {
        "brightness": 0,
        "contrast": 1.0,
        "exposure": 0,
        "threshold": 0
      }
    },
    {
      "camera_index": 2,
      "camera_name": "USB 2.0 Camera (/dev/video2)",
      "connected": true,
      "enabled": true,
      "has_frame": true,
      "seconds_since_last_frame": 0.02,
      "status": "ok",
      "uptime_seconds": 142.5,
      "settings": {
        "brightness": 10,
        "contrast": 1.2,
        "exposure": 0,
        "threshold": 0
      }
    }
  ],
  "timestamp": 1787123456.789
}
```

---

### 2. Camera Management & Toggling

#### `GET /cameras`
Returns a list of all detected video capture devices and their current configuration.
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "camera_count": 2,
  "cameras": [ ... ],
  "timestamp": 1787123456.789
}
```

#### `POST /cameras/toggle`
Toggle the enabled/disabled state of a specific camera or set an explicit boolean state.
- **Request Body**:
```json
{
  "camera": 2,
  "enabled": false
}
```
*(If `enabled` is omitted, the state is inverted)*
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "camera_index": 2,
  "enabled": false,
  "message": "Camera 2 disabled"
}
```

#### `POST /cameras/enable` & `POST /cameras/disable`
Dedicated endpoints to enable or disable a camera.
- **Request Body**:
```json
{
  "camera": 0
}
```
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "camera_index": 0,
  "enabled": true
}
```

---

### 3. Live Video Feeds

#### `GET /video_feed/<int:index>`
Streams multipart MJPEG video for the specified camera index.
- **Example Usage in HTML**:
```html
<img src="http://localhost:5050/video_feed/0" alt="Camera 0 Stream" />
<img src="http://localhost:5050/video_feed/2" alt="Camera 2 Stream" />
```

#### `GET /video_feed`
Streams video from the default (first available) camera or specified camera index via query parameter (`/video_feed?camera=2`).

---

### 4. QR Code Reading

#### `POST /read` (or `GET /read`)
Triggers an immediate QR code scan.

#### Scenario A: Simultaneous Scan Across All Enabled Cameras
Send an empty body or omit the `camera` parameter. The engine scans all enabled cameras concurrently and returns the first detected code.
- **Request Body**:
```json
{
  "retries": 3,
  "retry_interval": 0.2
}
```
- **Response `200 OK` (QR Found)**:
```json
{
  "status": "ok",
  "data": "PALLET_STATION_B_042",
  "camera_index": 2,
  "camera_name": "USB 2.0 Camera (/dev/video2)",
  "timestamp": 1787123460.12
}
```
- **Response `200 OK` (No QR Found)**:
```json
{
  "status": "no_qr",
  "message": "No QR code detected across cameras",
  "timestamp": 1787123460.85
}
```

#### Scenario B: Targeted Camera Scan
Specify the target camera index via body or query parameter (`POST /read {"camera": 0}` or `GET /read?camera=0`).
- **Request Body**:
```json
{
  "camera": 0,
  "retries": 4,
  "retry_interval": 0.15
}
```
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "data": "BIN_A12984",
  "camera_index": 0,
  "camera_name": "HD Pro Webcam C920 (/dev/video0)",
  "timestamp": 1787123462.45
}
```

---

### 5. Camera Tuning & Adjustment

#### `GET /settings`
Retrieve current software image parameters for a camera.
- **Query Parameter**: `?camera=0` (defaults to first camera if omitted).
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "camera_index": 0,
  "settings": {
    "brightness": 0,
    "contrast": 1.0,
    "exposure": 0,
    "threshold": 0
  }
}
```

#### `POST /settings`
Update software image tuning parameters for a camera. Saves immediately to `camera_setup_cam<ID>.json`.
- **Request Body**:
```json
{
  "camera": 0,
  "brightness": 15,
  "contrast": 1.3,
  "exposure": -1,
  "threshold": 0
}
```
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "camera_index": 0,
  "settings": {
    "brightness": 15,
    "contrast": 1.3,
    "exposure": -1,
    "threshold": 0
  }
}
```

#### `POST /settings/auto_adjust`
Runs histogram luminance analysis on the live camera stream to calculate balanced brightness and contrast settings.
- **Request Body**:
```json
{
  "camera": 0
}
```
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "message": "Auto-Adjust calculated for Camera 0",
  "camera_index": 0,
  "settings": {
    "brightness": -18,
    "contrast": 1.2,
    "exposure": 0,
    "threshold": 0
  }
}
```

#### `POST /settings/reset`
Restores physical V4L2 registers and software image adjustments back to defaults.
- **Request Body**:
```json
{
  "camera": 0
}
```
- **Response `200 OK`**:
```json
{
  "status": "ok",
  "message": "Camera 0 restored to factory defaults",
  "camera_index": 0,
  "settings": {
    "brightness": 0,
    "contrast": 1.0,
    "exposure": 0,
    "threshold": 0
  }
}
```

---

### 6. Captures & Snapshots

#### `GET /cap_screen`
- **HTML View**: `GET /cap_screen` returns an interactive dashboard with the latest QR capture.
- **Direct Image Download**: `GET /cap_screen?raw=1` returns the raw annotated JPEG image (`image/jpeg`).
- **JSON Metadata**: `GET /cap_screen?json=1` returns:
```json
{
  "status": "ok",
  "qr_data": "PALLET_STATION_B_042",
  "camera_index": 2,
  "camera_name": "USB 2.0 Camera (/dev/video2)",
  "filename": "qr_cam2_PALLET_STATION_B_042.jpg",
  "image_url": "/captures/qr_cam2_PALLET_STATION_B_042.jpg",
  "timestamp": 1787123460.12
}
```

#### `GET /captures/<filename>`
Serves saved capture snapshots from the `captures/` folder (e.g., `/captures/qr_cam0_ITEM_9910.jpg`).

---

### 7. OTA Code Updates

#### `POST /upload`
Uploads updated Python code, compiles it in memory to verify syntax, creates a backup copy (`muti_cam.py.bak`), and overwrites the active script.
- **Form Data**: `multipart/form-data` with key `file` containing the `.py` file.

---

## Code Integration Examples

### cURL

```bash
# 1. Health Check
curl http://localhost:5050/health

# 2. Scan across ALL cameras simultaneously
curl -X POST http://localhost:5050/read \
  -H "Content-Type: application/json" \
  -d '{"retries": 3, "retry_interval": 0.2}'

# 3. Scan specifically on Camera 2
curl -X POST http://localhost:5050/read \
  -H "Content-Type: application/json" \
  -d '{"camera": 2, "retries": 4, "retry_interval": 0.15}'

# 4. Disable Camera 0 to conserve USB bandwidth
curl -X POST http://localhost:5050/cameras/disable \
  -H "Content-Type: application/json" \
  -d '{"camera": 0}'

# 5. Run Smart Auto-Adjust on Camera 2
curl -X POST http://localhost:5050/settings/auto_adjust \
  -H "Content-Type: application/json" \
  -d '{"camera": 2}'

# 6. Download the latest captured QR snapshot
curl -O http://localhost:5050/cap_screen?raw=1
```

---

### Python (`requests`)

```python
import requests
import time

SERVER_URL = "http://192.168.1.50:5050"

def scan_all_cameras():
    """Trigger parallel scan across all enabled cameras."""
    try:
        response = requests.post(
            f"{SERVER_URL}/read",
            json={"retries": 3, "retry_interval": 0.2},
            timeout=5.0
        )
        data = response.json()
        
        if data.get("status") == "ok":
            qr_content = data["data"]
            cam_index = data["camera_index"]
            cam_name = data["camera_name"]
            print(f"✅ Decoded QR '{qr_content}' from Camera {cam_index} ({cam_name})")
            return qr_content
        else:
            print("⚠️ No QR code detected on any camera.")
            return None
    except Exception as e:
        print(f"❌ Communication error: {e}")
        return None

def scan_specific_camera(cam_idx: int):
    """Trigger scan on a specific camera index."""
    try:
        response = requests.post(
            f"{SERVER_URL}/read",
            json={"camera": cam_idx, "retries": 4, "retry_interval": 0.15},
            timeout=5.0
        )
        data = response.json()
        if data.get("status") == "ok":
            print(f"✅ Camera {cam_idx} detected: {data['data']}")
            return data["data"]
        return None
    except Exception as e:
        print(f"❌ Error scanning camera {cam_idx}: {e}")
        return None

if __name__ == "__main__":
    # Test scan across all cameras
    result = scan_all_cameras()
    
    # Test targeted scan on Camera 0
    cam0_result = scan_specific_camera(0)
```

---

### Node-RED Flow

In Node-RED, use an **HTTP Request** node configured for `POST http://<server-ip>:5050/read`:

```
[PLC / Robot Sensor]
         │
         ▼
[Function: Set Scan Payload]
   msg.payload = { "retries": 3, "retry_interval": 0.2 };
         │
         ▼
[HTTP Request: POST http://localhost:5050/read]
         │
         ▼
[Switch: msg.payload.status == "ok"]
   ├── (True)  ──> [Process msg.payload.data & msg.payload.camera_index]
   └── (False) ──> [Station Alarm / Retry]
```

---

## Systemd Service Setup

To run `muti_cam.py` automatically on Linux boot as a background systemd service:

### 1. Create the Service Unit File

Create `/etc/systemd/system/qr-muti-server.service`:

```ini
[Unit]
Description=Multi-Camera Simultaneous QR Vision Server
After=network.target

[Service]
Type=simple
User=cookies
WorkingDirectory=/home/cookies/qr-socket-server
ExecStart=/home/cookies/qr-socket-server/env/bin/python3 /home/cookies/qr-socket-server/muti_cam.py --port 5050
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### 2. Enable and Start the Service

```bash
# Reload systemd configuration
sudo systemctl daemon-reload

# Enable service to run on boot
sudo systemctl enable qr-muti-server

# Start service immediately
sudo systemctl start qr-muti-server

# Verify running status
sudo systemctl status qr-muti-server
```

### 3. View Live Service Logs

```bash
sudo journalctl -u qr-muti-server -f
```

---

## Performance & USB Bandwidth Tips

When streaming 2 or more USB webcams simultaneously on Linux:

1. **Hardware MJPG Compression (Active by Default)**:
   `muti_cam.py` automatically initializes all cameras using `cv2.VideoWriter_fourcc(*'MJPG')`. Uncompressed YUYV streams consume ~25 MB/s per camera, which quickly saturates the USB 2.0 bus (maximum theoretical ~40 MB/s shared per controller). Hardware MJPG compresses frames down to ~1.5–3 MB/s per camera.
2. **Distribute Cameras Across Physical USB Root Hubs**:
   If connecting 3 or more webcams, connect them to separate USB physical controllers (e.g., one on a front USB 3.0 port and another on a rear USB port) to avoid exceeding USB endpoint bandwidth limits (`No space left on device` error from `uvcvideo`).
3. **Disable Idle Cameras**:
   If a particular inspection camera is only needed at specific stations, call `POST /cameras/disable {"camera": X}` to release hardware bandwidth and enable it via `POST /cameras/enable {"camera": X}` only when the workpiece arrives.
4. **Resolution Tuning**:
   For QR code reading, `640x480` provides the best balance of detection speed, frame rate (30fps), and low CPU usage. If reading dense High-Density QR codes from a distance, increase resolution using `--width 1280 --height 720`.

---

## License
MIT License. Free for industrial robotics, factory automation, and commercial use.
