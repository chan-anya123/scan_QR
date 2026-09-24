# QR Socket/HTTP Server — Single Camera Manual

> **Server Script**: [`single_camera/qr_http_server.py`](file:///home/cookies/qr-socket-server/single_camera/qr_http_server.py)  
> **Target Device**: Single active camera (with live on-demand switching across connected cameras)  
> **Default Port**: `5050`  
> **Default Bind Address**: `0.0.0.0` (accessible across robot LAN / local network)

---

## Table of Contents

1. [Overview & Core Architecture](#1-overview--core-architecture)
2. [Quick Start & Running the Server](#2-quick-start--running-the-server)
3. [Key Features & Hardware Controls](#3-key-features--hardware-controls)
   - [Intelligent Video Device Detection](#intelligent-video-device-detection)
   - [On-Demand & Live Camera Switching](#on-demand--live-camera-switching)
   - [Automated Hardware Autofocus Suppression](#automated-hardware-autofocus-suppression)
   - [Multi-Stage Robust QR Decoding](#multi-stage-robust-qr-decoding)
   - [Asynchronous Non-Blocking Disk I/O](#asynchronous-non-blocking-disk-io)
   - [Per-Camera Isolated Persistent Configuration](#per-camera-isolated-persistent-configuration)
   - [Smart Auto-Adjust & Anti-Overexposure](#smart-auto-adjust--anti-overexposure)
   - [Dynamic Hardware Factory Reset](#dynamic-hardware-factory-reset)
   - [Multi-Station Profile Management](#multi-station-profile-management)
   - [Per-QR-Type Overwrite Capture Storage](#per-qr-type-overwrite-capture-storage)
   - [Graceful Shutdown & Resource Release](#graceful-shutdown--resource-release)
4. [Web User Interface Guide (Pastel Theme)](#4-web-user-interface-guide-pastel-theme)
   - [Scanner Page (`/`)](#scanner-page-)
   - [Adjust Camera Page (`/adjust`)](#adjust-camera-page-adjust)
   - [Captured Screen Page (`/cap_screen`)](#captured-screen-page-cap_screen)
   - [Robot Code Updater Page (`/upload`)](#robot-code-updater-page-upload)
5. [Complete HTTP REST API Reference](#5-complete-http-rest-api-reference)
   - [API Endpoints Summary Table](#api-endpoints-summary-table)
   - [Scanning & Diagnostics (`/read`, `/health`, `/ping`)](#scanning--diagnostics)
   - [Camera Control & Tuning (`/cameras`, `/cameras/switch`, `/settings`)](#camera-control--tuning)
   - [Live Video Feed (`/video_feed`)](#live-video-feed)
   - [Profiles & Storage (`/profiles`, `/cap_screen`, `/captures`)](#profiles--storage)
   - [OTA Code Updater (`/upload`)](#ota-code-updater)
6. [Node-RED & Industrial Integration Guide](#6-node-red--industrial-integration-guide)
7. [Docker Deployment Setup](#7-docker-deployment-setup)

---

## 1. Overview & Core Architecture

The **Single-Camera QR HTTP Server** is a standalone, industrial-grade vision service built with **Python 3**, **Flask**, **Waitress**, **OpenCV (V4L2)**, and **PyZbar**. It is engineered for industrial robots, automated guided vehicles (AGVs/AMRs), and single-stream vision inspection stations:

- **Decoupled Lock Architecture**: Dedicated hardware lock (`self.cap_lock`) ensures that USB frame capture operations never block web requests. HTTP API calls (`/read`, `/settings`, `/health`) remain fast and responsive even if camera hardware stalls.
- **On-Demand Camera Switching via API**: While running as a single-camera engine, the server can switch active cameras live on command via `POST /cameras/switch` or directly on-the-fly when calling `POST /read` with a `camera_index` parameter.
- **Multi-Stage Robust QR Decoding**: Uses four-stage decoding (Processed Image -> Grayscale -> Raw Unmanipulated Frame -> Gaussian Adaptive Thresholding) to handle harsh industrial reflections and glare.
- **Asynchronous Non-Blocking Disk Writes**: Snapshot images are written to storage via a background worker thread (`ThreadPoolExecutor`), so robots receive the `/read` response immediately.
- **Throttled MJPEG Streaming**: The live stream is capped at ~30 FPS with JPEG quality 80, keeping CPU consumption around 2–5%.
- **Autofocus Suppression**: Automatically detects and turns off autofocus controls (`focus_automatic_continuous=0`, `focus_auto=0`) via V4L2 kernel controls upon startup and camera switching.
- **Per-Camera Isolated Setup**: Retains separate configuration files per camera index (`camera_setup_cam0.json`, `camera_setup_cam2.json`).
- **Disk-Optimized Capture Storage**: Overwrites older images for the same QR code data (`captures/qr_<DATA>.jpg`) with visual green bounding boxes and metadata badges.
- **OTA Code Updater**: Browser-based code updater with Python syntax pre-compilation, automatic backup (`.bak`), and background service restart.
- **Graceful Shutdown**: Listens for `SIGTERM` and `SIGINT` to safely release `/dev/video*` devices before process termination.

---

## 2. Quick Start & Running the Server

### Prerequisites & Dependencies

```bash
# 1. Install Linux system utilities (provides v4l2-ctl and ZBar C library)
sudo apt-get update
sudo apt-get install -y v4l-utils libzbar0

# 2. Activate Python environment and install dependencies
cd /home/cookies/qr-socket-server
source env/bin/activate
pip install -r single_camera/requirements.txt
```

### Starting the Server

```bash
# 1. Standard Single-Camera Server (port 5050, Flask dev server)
./env/bin/python3 single_camera/qr_http_server.py

# 2. Production WSGI Mode (Waitress with 16 worker threads)
./env/bin/python3 single_camera/qr_http_server.py --use-waitress

# 3. Custom port, initial camera index, and resolution
./env/bin/python3 single_camera/qr_http_server.py --port 5050 --camera-index 0 --width 640 --height 480

# 4. Disable OTA code updater endpoint
./env/bin/python3 single_camera/qr_http_server.py --disable-upload
```

### Command-Line Arguments Reference

| Argument | Type | Default | Description |
|---|---|---|---|
| `--host` | `str` | `0.0.0.0` | Bind IP address (accessible across robot network) |
| `--port` | `int` | `5050` | HTTP listening port |
| `--camera-index` | `int` | `None` | Initial camera index (auto-detects first valid device) |
| `--width` | `int` | `640` | Camera frame width |
| `--height` | `int` | `480` | Camera frame height |
| `--retries` | `int` | `3` | Default frame retry attempts per `/read` call |
| `--retry-interval`| `float` | `0.2` | Delay in seconds between retry attempts |
| `--disable-upload`| `flag` | `False` | Disable the `/upload` OTA endpoint |
| `--use-waitress` | `flag` | `False` | Run using Waitress production WSGI server |

---

## 3. Key Features & Hardware Controls

### Intelligent Video Device Detection
Linux UVC drivers often register multiple device paths per physical webcam (e.g. `/dev/video0` for Video Capture and `/dev/video1` for Metadata). The server executes `v4l2-ctl --info` to verify `Video Capture` capabilities and filters out metadata nodes, ensuring only real video streams are used.

### On-Demand & Live Camera Switching
Supports switching cameras live without restarting the server:
- **Explicit Switching**: Send `POST /cameras/switch` with `{"index": 2}`.
- **On-Demand via Scan**: Pass `{"camera_index": 2}` directly into `POST /read`, or call `GET /read?camera=2`. The server automatically switches, waits briefly for the first frame to stabilize, and decodes.

### Automated Hardware Autofocus Suppression
Autofocus causes focus hunting and motion blur when the robot moves. The server automatically disables `focus_automatic_continuous=0` and `focus_auto=0` via V4L2 controls upon initialization and after every camera switch.

### Multi-Stage Robust QR Decoding
To guarantee decoding under factory conditions (glare, stainless steel reflections, extreme lighting):
1. **Processed Image**: Decodes the frame with user-configured contrast/brightness/exposure.
2. **Grayscale Conversion**: Decodes grayscale version of the processed frame.
3. **Raw Fallback**: If user settings or threshold filters degraded the image, it automatically falls back to the **untouched raw camera frame**.
4. **Adaptive Thresholding**: Applies local Gaussian adaptive binarization to read through uneven reflections.

### Asynchronous Non-Blocking Disk I/O
When a QR code is detected, image bounding boxes and metadata badges are rendered in-memory. Writing the image to disk (`cv2.imwrite`) is offloaded to a background `ThreadPoolExecutor`. The `/read` API returns instantly to the robot controller without waiting for storage I/O.

### Per-Camera Isolated Persistent Configuration
Each camera maintains its own independent configuration:
- `camera_setup_cam0.json` for Camera 0
- `camera_setup_cam2.json` for Camera 2

When switching cameras, the previous camera's settings are saved, and the new camera's configuration is loaded immediately.

### Smart Auto-Adjust & Anti-Overexposure
1. Restores clean hardware baseline defaults.
2. Disables `backlight_compensation=0` on webcams (preventing sensor whitewash under bright overhead lamps).
3. Analyzes frame luminance histogram:
   - Target optimal mean luminance: **120 – 130**.
   - If mean > 145: decreases brightness shift to protect dark QR modules.
   - If mean < 105: gently boosts brightness.
4. Applies pure software pixel scaling (`cv2.convertScaleAbs`), preventing hardware register corruption.

### Dynamic Hardware Factory Reset
Executes `v4l2-ctl -d /dev/videoX --list-ctrls`, parses manufacturer default registers dynamically, restores all hardware controls to factory values, re-suppresses autofocus, and resets software adjustments to `(Brightness: 0, Contrast: 1.0, Exposure: 0, Threshold: 0)`.

### Multi-Station Profile Management
Save and load named station tuning profiles (`Point_1.json`, `Station_B.json`) inside the `camera_setups/` directory:
- Path-traversal protected filename sanitization.
- UI dropdown lists all available profiles with live summary details.

### Per-QR-Type Overwrite Capture Storage
When a QR code is scanned:
1. Draws a green bounding polygon or rectangle around the detected QR code.
2. Draws a compact dark badge with decoded text and timestamp directly below the QR box.
3. Saves the snapshot to `captures/qr_<SAFE_TEXT>.jpg`. If the same QR code is scanned again, it **overwrites** the existing file, preventing disk exhaustion.

### Graceful Shutdown & Resource Release
Handles `SIGTERM` and `SIGINT` signals by calling `camera.release()`, shutting down worker threads, and releasing `/dev/video*` devices cleanly to prevent `Device or resource busy` errors when restarting under systemd.

---

## 4. Web User Interface Guide (Pastel Theme)

The web dashboard uses a soft pastel design system (lavender `#e0e7ff`, mint `#dcfce7`, cream `#f6f8fd`) with centered navigation tabs:

```
[ Scanner Dashboard ]    [ Adjust Camera ]    [ Captured Screen ]    [ Code Updater ]
```

### Scanner Page (`/`)
- **Live Camera Stream**: Real-time throttled MJPEG feed (`/video_feed`).
- **Select Active Camera**: Dropdown listing all verified physical camera devices with live switching.
- **Scan QR Now**: Decodes QR code and displays decoded text, source camera, timestamp, and snapshot link.
- **Reset Camera Defaults**: Instantly resets the active camera to factory baseline with inline green toast notification.

### Adjust Camera Page (`/adjust`)
- **Live Feed Preview**: Real-time feedback showing slider adjustments instantly.
- **Image Controls**:
  - **Brightness**: `-100` to `100` (Default: `0`)
  - **Exposure**: `-5` to `5` EV Shift (Default: `0`)
  - **Contrast**: `0.1` to `3.0` (Default: `1.0`)
  - **Threshold**: `0` (Off) to `255` (Binarization filter)
- **Smart Auto-Adjust**: Automatically computes optimal image parameters.
- **Save Setup Profile**: Name and save station-specific configurations.
- **Load Saved Profile**: Dropdown selector to apply saved profiles live.

### Captured Screen Page (`/cap_screen`)
- Displays the latest captured snapshot with bounding box and timestamp.
- Shows decoded text, source camera, file path, and relative capture time.
- **Download Snapshot**: Direct download button.

### Robot Code Updater Page (`/upload`)
- Upload an updated `.py` script via web browser.
- Automatically compiles Python syntax before writing to disk.
- Creates rollback backup (`.bak`).
- Restarts background systemd service automatically via polkit.

---

## 5. Complete HTTP REST API Reference

### API Endpoints Summary Table

| Method | Endpoint | Description | Request Parameters / Body | Response Status & Key Fields |
|---|---|---|---|---|
| `GET` / `POST` | `/read` | Scan QR code (with optional camera selection) | Query or JSON: `camera`, `index`, `camera_index`, `retries` (JSON only), `retry_interval` (JSON only) | `200 OK`<br>`{status, data, camera_index, timestamp}` |
| `GET` | `/cameras` | List connected cameras & current active index | None | `200 OK`<br>`{status, active_index, available_cameras}` |
| `POST` | `/cameras/switch` | Switch the active camera live | JSON: `{"index": <int>}` | `200 OK`<br>`{status, message, active_index}` |
| `GET` | `/video_feed` | Live MJPEG stream (~30 FPS, JPEG q=80) | None | `200 OK`<br>`multipart/x-mixed-replace; boundary=frame` |
| `GET` | `/settings` | Get current image settings | None | `200 OK`<br>`{brightness, contrast, exposure, threshold}` |
| `POST` | `/settings` | Update image settings | JSON: `{"brightness", "contrast", "exposure", "threshold"}` | `200 OK`<br>`{status, settings}` |
| `POST` | `/settings/auto_adjust` | Run smart luminance auto-adjustment | None | `200 OK`<br>`{status, message, settings}` |
| `POST` | `/settings/reset` | Reset hardware V4L2 & software to factory defaults | None | `200 OK`<br>`{status, message, settings}` |
| `GET` | `/profiles` | List all saved station profile files | None | `200 OK`<br>`{status, profiles: [...]}` |
| `POST` | `/profiles/save` | Save active settings as a named profile | JSON: `{"name": "<profile_name>"}` | `200 OK`<br>`{status, message, data}` |
| `POST` | `/profiles/load` | Load and apply a saved station profile | JSON: `{"name": "<profile_name>"}` | `200 OK`<br>`{status, message, settings}` |
| `GET` / `POST` | `/cap_screen` | View latest captured QR image & metadata | Query: `?raw=1` or `?image=1` (binary), `?json=1` or JSON body (metadata) | `200 OK`<br>HTML page / JPEG binary / JSON |
| `GET` | `/captures/<file>` | Serve a specific saved capture JPEG | URL Path: `<filename>` (e.g. `qr_PALLET.jpg`) | `200 OK`<br>`image/jpeg` binary |
| `GET` | `/health` | Diagnostic metrics (uptime, camera state, frame age) | None | `200 OK`<br>`{status, camera_connected, uptime_seconds}` |
| `GET` | `/ping` | Lightweight heartbeat ping | None | `200 OK`<br>`{status: "pong", timestamp}` |
| `GET` | `/` | Scanner web dashboard | None | `200 OK`<br>HTML |
| `GET` | `/adjust` | Camera tuning & profiles web page | None | `200 OK`<br>HTML |
| `GET` | `/upload` | OTA code updater web page | None | `200 OK` (HTML) or `403` (if disabled) |
| `POST` | `/upload` | Upload `.py` code, verify syntax, restart service | Multipart: `file` | `200 OK` (JSON) or `400` / `403` |

---

### Scanning & Diagnostics

#### `POST /read` / `GET /read`
Triggers QR code decoding with optional camera selection and retry configuration.

- **Request Headers**: `Content-Type: application/json`
- **Request Body (Optional)**:
  ```json
  {
    "camera_index": 2,
    "retries": 3,
    "retry_interval": 0.2
  }
  ```
- **Query Parameter Alternative**: `GET /read?camera=2` (Note: `retries` and `retry_interval` are only supported via JSON body)
- **Response (Success - 200 OK)**:
  ```json
  {
    "status": "ok",
    "data": "PALLET_10482",
    "camera_index": 2,
    "timestamp": 1788401843.32
  }
  ```
- **Response (No QR Found - 200 OK)**:
  ```json
  {
    "status": "no_qr",
    "message": " ",
    "data": " ",
    "camera_index": 2,
    "timestamp": 1788401844.50
  }
  ```
- **Response (Camera Error - 503 Service Unavailable)**:
  ```json
  {
    "status": "error",
    "error_code": "CAMERA_NOT_READY",
    "message": "Camera is disconnected or frame grabber is failing",
    "camera_index": 2,
    "timestamp": 1788401845.10
  }
  ```

#### `GET /health`
Returns camera engine diagnostic metrics.
- **Response**:
  ```json
  {
    "status": "ok",
    "camera_connected": true,
    "camera_index": 0,
    "has_latest_frame": true,
    "seconds_since_last_frame": 0.03,
    "uptime_seconds": 124.5,
    "timestamp": 1788401138.73
  }
  ```

#### `GET /ping`
- **Response**: `{"status": "pong", "timestamp": 1788401138.71}`

---

### Camera Control & Tuning

#### `GET /cameras`
Returns connected capture devices and the currently active camera index.
- **Response**:
  ```json
  {
    "status": "ok",
    "active_index": 0,
    "available_cameras": [
      { "index": 0, "name": "USB2.0 HD UVC WebCam (/dev/video0)" },
      { "index": 2, "name": "WebCamera (/dev/video2)" }
    ]
  }
  ```

#### `POST /cameras/switch`
Switch the active camera live.
- **Request Body**: `{"index": 2}`
- **Response**:
  ```json
  {
    "status": "ok",
    "message": "Switched to camera index 2",
    "active_index": 2
  }
  ```

#### `GET /settings`
Returns active camera settings.
- **Response**:
  ```json
  {
    "brightness": 0,
    "contrast": 1.0,
    "exposure": 0,
    "threshold": 0
  }
  ```

#### `POST /settings`
Updates image adjustment parameters.
- **Request Body**:
  ```json
  {
    "brightness": 10,
    "contrast": 1.2,
    "exposure": 0,
    "threshold": 0
  }
  ```

#### `POST /settings/auto_adjust`
Calculates and applies clean baseline auto-adjust.
- **Response**:
  ```json
  {
    "status": "ok",
    "message": "V4L2 Hardware Auto-Adjust baseline calculated and applied",
    "settings": {
      "brightness": -15,
      "contrast": 1.2,
      "exposure": 0,
      "threshold": 0
    }
  }
  ```

#### `POST /settings/reset`
Restores physical camera factory defaults and resets software settings.
- **Response**:
  ```json
  {
    "status": "ok",
    "message": "Camera hardware & software restored to dynamic camera defaults",
    "settings": {
      "brightness": 0,
      "contrast": 1.0,
      "exposure": 0,
      "threshold": 0
    }
  }
  ```

---

### Live Video Feed

#### `GET /video_feed`
Streams multipart MJPEG video feed (`Content-Type: multipart/x-mixed-replace; boundary=frame`) throttled to ~30 FPS with JPEG quality 80.

---

### Profiles & Storage

#### `GET /profiles`
Lists all saved station profile JSON files in `camera_setups/`.
- **Response**:
  ```json
  {
    "status": "ok",
    "profiles": [
      {
        "name": "default_cam0",
        "profile_name": "default_cam0",
        "filename": "default_cam0.json",
        "brightness": 0,
        "contrast": 1.0,
        "exposure": 0,
        "threshold": 0,
        "settings": { "brightness": 0, "contrast": 1.0, "exposure": 0, "threshold": 0 }
      }
    ]
  }
  ```

#### `POST /profiles/save`
Saves current settings to a named profile file.
- **Request Body**: `{"name": "Station_A"}`
- **Response**: `{"status": "ok", "message": "Camera setup saved to profile file Station_A.json"}`

#### `POST /profiles/load`
Loads and applies a named profile file.
- **Request Body**: `{"name": "Station_A"}` (or `{"filename": "Station_A.json"}`)
- **Response**:
  ```json
  {
    "status": "ok",
    "message": "Profile 'Station_A' loaded and applied successfully",
    "settings": { "brightness": 0, "contrast": 1.0, "exposure": 0, "threshold": 0 }
  }
  ```

#### `GET /cap_screen` / `POST /cap_screen`
- `GET /cap_screen`: Renders HTML preview page.
- `GET /cap_screen?raw=1` or `?image=1`: Returns binary JPEG of the latest captured QR code image.
- `GET /cap_screen?json=1` or `POST` with JSON body: Returns metadata JSON of the latest capture.

#### `GET /captures/<filename>`
Serves saved capture image directly from disk (e.g. `/captures/qr_PALLET_10482.jpg`).

---

### OTA Code Updater

#### `GET /upload`
Renders the code upload page. (Returns HTTP 403 if `--disable-upload` is set).

#### `POST /upload`
Uploads a new `.py` script, validates Python syntax, creates `.bak` backup, saves to disk, and triggers service restart.
- **Form Data**: `file` (multipart file, must end in `.py`)

---

## 6. Node-RED & Industrial Integration Guide

Automate point-by-point station inspection flows in Node-RED:

```
[Arrive at Point 1] --> [POST /read {"camera_index": 2}] --> [Process QR Data]
```

### Example Node-RED Payloads

1. **Scan QR with specific camera**:
   - **Node**: `HTTP Request` (POST `http://<server-ip>:5050/read`)
   - **Payload**: `{"camera_index": 2, "retries": 3, "retry_interval": 0.2}`

2. **Load Point Profile**:
   - **Node**: `HTTP Request` (POST `http://<server-ip>:5050/profiles/load`)
   - **Payload**: `{"name": "Point_1"}`

3. **Fetch Image for UI Dashboard**:
   - **Node**: `HTTP Request` (GET `http://<server-ip>:5050/cap_screen?raw=1`)
   - **Return**: Binary Buffer -> Dashboard Template `<img src="data:image/jpeg;base64,{{msg.payload}}">`

4. **Live Video Feed in Dashboard**:
   - **Node**: `ui_template`
   - **Code**: `<img src="http://<server-ip>:5050/video_feed" width="100%">`

---

## 7. Docker Deployment Setup

The QR HTTP Server runs seamlessly inside a Docker container, providing hardware isolation and simple deployment across edge devices.

### Prerequisites
Make sure `docker` and `docker-compose` are installed on your host machine.

### Core Configuration Files
The deployment relies on:
1. `Dockerfile`: Uses `python:3.10-slim` and installs `v4l-utils` and `libgl1`.
2. `docker-compose.yml`: Handles camera hardware pass-through and persistent file volumes.

### Deployment Commands

**1. Build and Start the Container:**
```bash
docker-compose up -d --build
```

**2. View Live Logs (Check Camera Detection):**
```bash
docker-compose logs -f
```

**3. Stop and Teardown:**
```bash
docker-compose down
```

### Hot-Plugging USB Cameras (Privileged Mode)
The `docker-compose.yml` is configured with `privileged: true` and maps `/dev:/dev`. This allows you to plug or unplug USB webcams on the fly. The background python script will dynamically detect the new `/dev/video*` devices without needing a container restart.

### Packaging for Air-Gapped / New Machines
To transfer the pre-built Docker image to a new machine without rebuilding:
```bash
# Save image to a tarball
docker save -o qr_server_image.tar single_camera-qr-server

# On the new machine, load the tarball
docker load -i qr_server_image.tar

# Run using the loaded image (ensure 'build: .' is removed from docker-compose.yml)
docker-compose up -d
```
