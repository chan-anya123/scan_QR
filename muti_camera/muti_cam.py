#!/usr/bin/env python3
import argparse
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np
from flask import Flask, jsonify, request, Response, render_template, send_from_directory
from pyzbar.pyzbar import decode as decode_qr

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("muti_cam_server")

def sanitize_filename(name: str) -> str:
    """Sanitize string for safe filesystem usage."""
    if not name:
        return f"setup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    cleaned = re.sub(r'[^a-zA-Z0-9_-]', '_', name.strip())
    if not cleaned:
        return f"setup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    return cleaned

def is_video_capture_device(dev_path: str) -> bool:
    """Check if device path supports standard Video Capture (and is not purely metadata)."""
    try:
        res = subprocess.run(["v4l2-ctl", "-d", dev_path, "--info"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            in_dev_caps = False
            for line in res.stdout.splitlines():
                if "Device Caps" in line:
                    in_dev_caps = True
                elif in_dev_caps:
                    if line.startswith("\t\t") or line.startswith("        "):
                        if "Video Capture" in line and "Metadata" not in line:
                            return True
                    elif line and not line.startswith("\t") and not line.startswith(" "):
                        in_dev_caps = False
    except Exception:
        pass
    return False

def list_available_cameras() -> list:
    """Scan and return a list of available connected camera devices (only real Video Capture streams)."""
    cameras = []
    try:
        res = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            curr_name = "Camera"
            for line in res.stdout.splitlines():
                if line and not line.startswith("\t"):
                    curr_name = line.strip().split("(")[0].strip()
                elif line.startswith("\t/dev/video"):
                    dev_path = line.strip()
                    if is_video_capture_device(dev_path):
                        try:
                            idx = int(re.sub(r'\D', '', dev_path))
                            if not any(c["index"] == idx for c in cameras):
                                cameras.append({"index": idx, "name": f"{curr_name} ({dev_path})"})
                        except ValueError:
                            pass
    except Exception as e:
        log.debug("v4l2-ctl camera list warning: %s", e)

    if not cameras:
        for i in range(8):
            dev_path = f"/dev/video{i}"
            if os.path.exists(dev_path) and is_video_capture_device(dev_path):
                cameras.append({"index": i, "name": f"Camera {i} ({dev_path})"})

    return sorted(cameras, key=lambda x: x["index"]) if cameras else [{"index": 0, "name": "Camera 0 (/dev/video0)"}]


class CameraDevice:
    """Individual Camera Device instance with independent background capture thread,
    per-camera isolated profile configuration, and hardware autofocus suppression.
    """
    def __init__(self, index: int, name: str = None, width: int = None, height: int = None, base_dir: str = None):
        self.index = index
        self.name = name or f"Camera {index} (/dev/video{index})"
        self.width = width
        self.height = height
        self.start_time = time.time()
        self.lock = threading.Lock()

        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.setups_dir = os.path.join(self.base_dir, "camera_setups")
        self.config_path = os.path.join(self.base_dir, f"camera_setup_cam{index}.json")
        os.makedirs(self.setups_dir, exist_ok=True)

        self.settings = {
            "brightness": 0,         # Range: -100 to 100
            "contrast": 1.0,         # Range: 0.1 to 3.0
            "exposure": 0,          # Range: -5 to 5 (EV Shift)
            "threshold": 0,          # Range: 0 (Off) to 255
        }

        # Load isolated persistent setup for this camera
        self.load_saved_setup()

        # Initialize hardware capture with V4L2 backend + hardware MJPG compression
        self.cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            log.warning("Could not open camera index %s initially. Background loop will retry...", index)

        self.disable_autofocus()

        self.enabled = True
        self.running = True
        self.latest_frame = None
        self.latest_jpeg = None
        self.frame_id = 0
        self._standby_jpeg = None
        self.last_frame_time = 0

        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        log.info("CameraDevice %s (%s) initialized with hardware MJPG and capture loop started", self.index, self.name)

    def _create_standby_frame(self) -> np.ndarray:
        """Create a placeholder dark frame indicating camera is disabled / standby."""
        w = self.width or 640
        h = self.height or 480
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        frame[:] = (15, 23, 42)  # Dark slate blue
        cv2.putText(frame, f"CAMERA {self.index} DISABLED", (max(20, w // 2 - 170), h // 2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (148, 163, 184), 2, cv2.LINE_AA)
        cv2.putText(frame, "Click 'Enable' to activate stream", (max(20, w // 2 - 150), h // 2 + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (100, 116, 139), 1, cv2.LINE_AA)
        return frame

    def set_enabled(self, enabled: bool) -> bool:
        """Enable or disable camera capture live to save CPU & USB bandwidth."""
        with self.lock:
            if self.enabled == enabled:
                return self.enabled
            self.enabled = enabled
            if not enabled:
                log.info("Camera %s disabled. Releasing hardware capture...", self.index)
                if self.cap.isOpened():
                    self.cap.release()
                self.latest_frame = self._create_standby_frame()
                self.last_frame_time = time.time()
            else:
                log.info("Camera %s enabled. Re-opening hardware capture...", self.index)
                if not self.cap.isOpened():
                    self.cap.open(self.index, cv2.CAP_V4L2)
                    if self.width:
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    if self.height:
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                self.disable_autofocus()
                self.latest_frame = None
            return self.enabled

    def disable_autofocus(self):
        """Ensure hardware autofocus is disabled on this camera for stable QR reading."""
        dev_path = f"/dev/video{self.index}"
        if self.cap.isOpened():
            try:
                self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            except Exception:
                pass
        try:
            res = subprocess.run(["v4l2-ctl", "-d", dev_path, "--list-ctrls"], capture_output=True, text=True, timeout=2)
            if res.returncode == 0:
                ctrls = []
                for line in res.stdout.splitlines():
                    if "flags=inactive" in line or "flags=read-only" in line:
                        continue
                    if "focus_automatic_continuous" in line:
                        ctrls.append("focus_automatic_continuous=0")
                    elif "focus_auto" in line:
                        ctrls.append("focus_auto=0")
                if ctrls:
                    subprocess.run(["v4l2-ctl", "-d", dev_path, f"--set-ctrl={','.join(ctrls)}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                    log.info("Autofocus disabled for %s: %s", dev_path, ",".join(ctrls))
        except Exception:
            pass

    def get_settings(self) -> dict:
        """Return a copy of the current camera settings."""
        with self.lock:
            return dict(self.settings)

    def update_settings(self, new_settings: dict) -> dict:
        """Sanitize and update software image settings, saving to per-camera setup file."""
        with self.lock:
            if "brightness" in new_settings:
                self.settings["brightness"] = max(-100, min(100, int(new_settings["brightness"])))
            if "contrast" in new_settings:
                self.settings["contrast"] = max(0.1, min(3.0, float(new_settings["contrast"])))
            if "exposure" in new_settings:
                self.settings["exposure"] = max(-5, min(5, int(new_settings["exposure"])))
            if "threshold" in new_settings:
                self.settings["threshold"] = max(0, min(255, int(new_settings["threshold"])))

            self._save_setup_unlocked()
            return dict(self.settings)

    def _save_setup_unlocked(self):
        """Save settings to camera_setup_cam{index}.json."""
        data = {
            "camera_index": self.index,
            "camera_name": self.name,
            "brightness": self.settings["brightness"],
            "contrast": self.settings["contrast"],
            "exposure": self.settings["exposure"],
            "threshold": self.settings["threshold"],
            "saved_at": time.time(),
            "saved_at_iso": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning("Could not save camera %s setup (%s): %s", self.index, self.config_path, e)

    def load_saved_setup(self) -> dict:
        """Load persistent setup from file if available."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                with self.lock:
                    for key in ["brightness", "contrast", "exposure", "threshold"]:
                        if key in data:
                            self.settings[key] = data[key]
                log.info("Loaded camera %s setup from %s", self.index, self.config_path)
                return dict(self.settings)
            except Exception as e:
                log.warning("Failed to read camera %s setup: %s", self.index, e)
        return dict(self.settings)

    def reset_to_factory(self) -> dict:
        """Reset hardware controls to factory defaults and restore software defaults."""
        with self.lock:
            self.settings = {
                "brightness": 0,
                "contrast": 1.0,
                "exposure": 0,
                "threshold": 0,
            }

        dev_path = f"/dev/video{self.index}"
        try:
            res = subprocess.run(["v4l2-ctl", "-d", dev_path, "--list-ctrls"], capture_output=True, text=True, timeout=3)
            if res.returncode == 0:
                ctrl_sets = []
                for line in res.stdout.splitlines():
                    if "flags=inactive" in line or "flags=read-only" in line:
                        continue
                    match = re.search(r'^\s*([a-z0-9_]+)\s+0x[0-9a-fA-F]+\s+.*default=(-?\d+)', line)
                    if match:
                        ctrl_sets.append(f"{match.group(1)}={match.group(2)}")
                if ctrl_sets:
                    subprocess.run(["v4l2-ctl", "-d", dev_path, f"--set-ctrl={','.join(ctrl_sets)}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    log.info("Reset V4L2 controls on %s to defaults: %s", dev_path, ",".join(ctrl_sets))
        except Exception as e:
            log.debug("V4L2 reset error on %s: %s", dev_path, e)

        self.disable_autofocus()
        with self.lock:
            self._save_setup_unlocked()
        return self.get_settings()

    def auto_adjust_settings(self) -> dict:
        """Smart Auto-Adjust for this specific camera based on real-time frame histogram."""
        self.reset_to_factory()
        time.sleep(0.2)

        dev_path = f"/dev/video{self.index}"
        try:
            subprocess.run(["v4l2-ctl", "-d", dev_path, "--set-ctrl=backlight_compensation=0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
        except Exception:
            pass

        frame = self.read_frame()
        brightness_shift = 0
        contrast_factor = 1.0

        if frame is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_val = float(np.mean(gray))
            std_val = float(np.std(gray))

            if mean_val > 145.0:
                brightness_shift = int(max(-60, 128.0 - mean_val))
                contrast_factor = 1.2
            elif mean_val < 105.0:
                brightness_shift = int(min(60, 128.0 - mean_val))
                contrast_factor = 1.2
            else:
                brightness_shift = 0
                contrast_factor = 1.1

            if std_val < 30.0:
                contrast_factor = min(1.8, round(45.0 / max(10.0, std_val), 1))

        updated = {
            "brightness": brightness_shift,
            "contrast": round(contrast_factor, 1),
            "exposure": 0,
            "threshold": 0
        }
        log.info("Auto-Adjust computed for camera %s: %s", self.index, updated)
        return self.update_settings(updated)

    def _capture_loop(self):
        """Continuous background thread loop to grab and process camera frames."""
        consecutive_failures = 0
        encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), 65]

        while self.running:
            with self.lock:
                is_enabled = self.enabled
                is_opened = self.cap.isOpened()

            if not is_enabled:
                time.sleep(0.2)
                continue

            if not is_opened:
                time.sleep(1.0)
                with self.lock:
                    if self.enabled and not self.cap.isOpened():
                        self.cap.open(self.index, cv2.CAP_V4L2)
                        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*'MJPG'))
                        if self.width:
                            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                        if self.height:
                            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                        self.cap.set(cv2.CAP_PROP_FPS, 30)
                        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        self.disable_autofocus()
                continue

            ok, frame = self.cap.read()
            if not ok or frame is None:
                consecutive_failures += 1
                if consecutive_failures >= 15:
                    log.warning("Camera %s frame read failed repeatedly. Reopening...", self.index)
                    with self.lock:
                        self.cap.release()
                    consecutive_failures = 0
                time.sleep(0.02)
                continue

            consecutive_failures = 0
            with self.lock:
                b = self.settings.get("brightness", 0)
                c = self.settings.get("contrast", 1.0)
                exp = self.settings.get("exposure", 0)
                t_val = self.settings.get("threshold", 0)

            # Fast software image processing (only if modified)
            if b != 0 or c != 1.0 or exp != 0 or t_val > 0:
                alpha = c * (2.0 ** (exp * 0.3)) if exp != 0 else c
                beta = float(b)
                if alpha != 1.0 or beta != 0:
                    processed = cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)
                else:
                    processed = frame

                if t_val > 0:
                    gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
                    _, binarized = cv2.threshold(gray, t_val, 255, cv2.THRESH_BINARY)
                    processed = cv2.cvtColor(binarized, cv2.COLOR_GRAY2BGR)
            else:
                processed = frame

            # Single in-memory JPEG compression per frame (massively reduces CPU)
            ok_enc, buf = cv2.imencode('.jpg', processed, encode_params)

            now = time.time()
            with self.lock:
                self.latest_frame = processed
                if ok_enc:
                    self.latest_jpeg = buf.tobytes()
                    self.frame_id += 1
                self.last_frame_time = now

            time.sleep(0.002)

    def read_frame(self) -> Optional[np.ndarray]:
        """Return copy of latest processed frame."""
        with self.lock:
            if self.latest_frame is None:
                if not self.enabled:
                    return self._create_standby_frame()
                return None
            return self.latest_frame.copy()

    def get_latest_jpeg(self) -> Tuple[Optional[bytes], int]:
        """Return pre-encoded JPEG bytes of latest frame (zero-latency single encoding)."""
        with self.lock:
            if not self.enabled:
                if self._standby_jpeg is None:
                    sb = self._create_standby_frame()
                    _, buf = cv2.imencode('.jpg', sb, [int(cv2.IMWRITE_JPEG_QUALITY), 65])
                    self._standby_jpeg = buf.tobytes()
                return self._standby_jpeg, 0
            return self.latest_jpeg, self.frame_id

    def is_ready(self) -> bool:
        """Check if camera is active and producing fresh frames."""
        with self.lock:
            return self.enabled and self.cap.isOpened() and (self.latest_frame is not None) and (time.time() - self.last_frame_time < 3.0)

    def get_health(self) -> dict:
        """Return diagnostic metrics."""
        with self.lock:
            is_enabled = self.enabled
            opened = self.cap.isOpened()
            has_frame = self.latest_frame is not None
            last_time = self.last_frame_time
        uptime = round(time.time() - self.start_time, 1)
        healthy = is_enabled and opened and has_frame and (time.time() - last_time < 3.0)
        return {
            "camera_index": self.index,
            "camera_name": self.name,
            "enabled": is_enabled,
            "status": "disabled" if not is_enabled else ("ok" if healthy else "degraded"),
            "connected": opened,
            "has_frame": has_frame,
            "seconds_since_last_frame": round(time.time() - last_time, 2) if last_time > 0 else None,
            "uptime_seconds": uptime,
            "settings": self.get_settings()
        }

    def release(self):
        """Release capture thread and camera device."""
        self.running = False
        with self.lock:
            if self.cap.isOpened():
                self.cap.release()
                log.info("Camera %s released", self.index)


class MultiCameraEngine:
    """Multi-camera coordinator managing simultaneous camera devices, multi-feed streaming,
    and parallel/targeted QR decoding.
    """
    def __init__(self, width: int = 640, height: int = 480, base_dir: str = None):
        self.width = width
        self.height = height
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.captures_dir = os.path.join(self.base_dir, "captures")
        self.setups_dir = os.path.join(self.base_dir, "camera_setups")
        os.makedirs(self.captures_dir, exist_ok=True)
        os.makedirs(self.setups_dir, exist_ok=True)

        self.cameras: Dict[int, CameraDevice] = {}
        self.lock = threading.Lock()

        # Last captured QR record
        self.last_qr_frame = None
        self.last_qr_data = None
        self.last_qr_time = None
        self.last_qr_filename = None
        self.last_qr_camera_index = None
        self.last_qr_camera_name = None

        self.init_all_cameras()

    def init_all_cameras(self):
        """Discover all connected hardware cameras and initialize CameraDevice for each."""
        avail = list_available_cameras()
        log.info("Discovered %d video capture devices: %s", len(avail), avail)
        for cam_info in avail:
            idx = cam_info["index"]
            name = cam_info["name"]
            if idx not in self.cameras:
                try:
                    dev = CameraDevice(index=idx, name=name, width=self.width, height=self.height, base_dir=self.base_dir)
                    self.cameras[idx] = dev
                except Exception as e:
                    log.error("Failed to initialize camera %s: %s", idx, e)

    def get_camera(self, index: int) -> Optional[CameraDevice]:
        """Get CameraDevice by index."""
        with self.lock:
            return self.cameras.get(index)

    def get_first_camera(self) -> Optional[CameraDevice]:
        """Get first available CameraDevice."""
        with self.lock:
            if self.cameras:
                first_idx = sorted(self.cameras.keys())[0]
                return self.cameras[first_idx]
            return None

    def list_cameras_info(self) -> List[dict]:
        """Return list of all camera devices with their statuses and settings."""
        with self.lock:
            result = []
            for idx in sorted(self.cameras.keys()):
                cam = self.cameras[idx]
                result.append(cam.get_health())
            return result

    def set_last_qr_capture(self, frame: np.ndarray, data: str, camera_index: int, camera_name: str):
        """Save frame snapshot with camera source annotation."""
        now = time.time()
        safe_data = re.sub(r'[^a-zA-Z0-9_-]', '_', data.strip())[:50]
        if not safe_data:
            safe_data = "unknown"

        filename = f"qr_cam{camera_index}_{safe_data}.jpg"
        save_path = os.path.join(self.captures_dir, filename)

        with self.lock:
            self.last_qr_frame = frame.copy() if frame is not None else None
            self.last_qr_data = data
            self.last_qr_time = now
            self.last_qr_filename = filename
            self.last_qr_camera_index = camera_index
            self.last_qr_camera_name = camera_name

        if frame is not None:
            try:
                cv2.imwrite(save_path, frame)
                log.info("Saved QR capture from Camera %s ('%s') to %s", camera_index, data, save_path)
            except Exception as e:
                log.error("Failed to write QR image to disk: %s", e)

    def get_last_qr_capture(self):
        """Get the latest QR code capture data."""
        with self.lock:
            if self.last_qr_frame is None:
                return None, None, None, None, None, None
            return (
                self.last_qr_frame.copy(),
                self.last_qr_data,
                self.last_qr_time,
                self.last_qr_filename,
                self.last_qr_camera_index,
                self.last_qr_camera_name
            )

    def scan_qr_single(self, camera: CameraDevice, retries: int = 3, retry_interval: float = 0.2) -> Optional[str]:
        """Scan QR code from a single CameraDevice with visual badge overlay."""
        if not camera.enabled:
            return None

        for attempt in range(1, retries + 1):
            frame = camera.read_frame()
            if frame is None:
                if attempt < retries:
                    time.sleep(0.05)
                continue

            results = decode_qr(frame)
            if not results:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                results = decode_qr(gray)

            if results:
                qr_text = results[0].data.decode("utf-8", errors="replace")
                annotated = frame.copy()
                h_img, w_img = annotated.shape[:2]

                try:
                    points = results[0].polygon
                    if points and len(points) >= 4:
                        pts = np.array([(p.x, p.y) for p in points], dtype=np.int32).reshape((-1, 1, 2))
                        cv2.polylines(annotated, [pts], True, (0, 255, 0), 3)
                        xs = [p.x for p in points]
                        ys = [p.y for p in points]
                        x_min, x_max, y_min, y_max = min(xs), max(xs), min(ys), max(ys)
                    elif results[0].rect:
                        rect = results[0].rect
                        cv2.rectangle(annotated, (rect.left, rect.top), (rect.left + rect.width, rect.top + rect.height), (0, 255, 0), 3)
                        x_min, x_max = rect.left, rect.left + rect.width
                        y_min, y_max = rect.top, rect.top + rect.height
                    else:
                        x_min, x_max, y_min, y_max = 20, 200, 20, 200

                    # Draw stylish badge below QR box
                    if y_max + 48 > h_img:
                        text_y1 = max(22, y_min - 24)
                        text_y2 = text_y1 + 18
                    else:
                        text_y1 = y_max + 22
                        text_y2 = y_max + 40

                    ts_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    line1 = f"{qr_text}"
                    line2 = f"Cam {camera.index} | {ts_str}"

                    font = cv2.FONT_HERSHEY_SIMPLEX
                    scale1, scale2 = 0.6, 0.45
                    thick1, thick2 = 2, 1

                    (t1_w, t1_h), _ = cv2.getTextSize(line1, font, scale1, thick1)
                    (t2_w, t2_h), _ = cv2.getTextSize(line2, font, scale2, thick2)
                    max_w = max(t1_w, t2_w) + 16

                    bg_x1 = max(0, x_min)
                    bg_x2 = min(w_img, bg_x1 + max_w)
                    bg_y1 = text_y1 - t1_h - 6
                    bg_y2 = text_y2 + 6

                    cv2.rectangle(annotated, (bg_x1, bg_y1), (bg_x2, bg_y2), (20, 24, 33), -1)
                    cv2.rectangle(annotated, (bg_x1, bg_y1), (bg_x2, bg_y2), (0, 255, 0), 1)

                    cv2.putText(annotated, line1, (bg_x1 + 8, text_y1), font, scale1, (0, 255, 0), thick1, cv2.LINE_AA)
                    cv2.putText(annotated, line2, (bg_x1 + 8, text_y2), font, scale2, (220, 225, 235), thick2, cv2.LINE_AA)
                except Exception as e:
                    log.debug("Failed to annotate QR frame: %s", e)

                self.set_last_qr_capture(annotated, qr_text, camera.index, camera.name)
                return qr_text

            if attempt < retries:
                time.sleep(retry_interval)
        return None

    def scan_qr_all(self, retries: int = 3, retry_interval: float = 0.2) -> Tuple[Optional[str], Optional[int], Optional[str]]:
        """Simultaneously scan across all connected and enabled camera feeds."""
        with self.lock:
            cams = [c for c in self.cameras.values() if c.enabled]

        if not cams:
            return None, None, None

        for attempt in range(1, retries + 1):
            for cam in cams:
                if not cam.is_ready():
                    continue
                text = self.scan_qr_single(cam, retries=1, retry_interval=0)
                if text is not None:
                    return text, cam.index, cam.name

            if attempt < retries:
                time.sleep(retry_interval)

        return None, None, None

    def release_all(self):
        """Release all active camera instances."""
        with self.lock:
            for cam in self.cameras.values():
                cam.release()
            self.cameras.clear()


def create_app(engine: MultiCameraEngine, default_retries: int, default_retry_interval: float) -> Flask:
    app = Flask(__name__, template_folder="muti_templates")

    @app.route("/captures/<path:filename>", methods=["GET"])
    def get_capture_file(filename):
        return send_from_directory(engine.captures_dir, filename)

    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    @app.route("/adjust", methods=["GET"])
    def adjust_page():
        return render_template("adjust.html")

    @app.route("/cameras", methods=["GET"])
    def get_cameras_api():
        """Get all connected cameras and diagnostic status."""
        cams_info = engine.list_cameras_info()
        return jsonify({
            "status": "ok",
            "camera_count": len(cams_info),
            "cameras": cams_info,
            "timestamp": time.time()
        }), 200

    def extract_camera_index(req) -> Optional[int]:
        """Safely extract camera index from JSON body or query parameters (properly handling index 0)."""
        body = req.get_json(silent=True) or {}
        for key in ["camera", "camera_index"]:
            if key in body and body[key] is not None:
                try:
                    return int(body[key])
                except (ValueError, TypeError):
                    pass
        for key in ["camera", "index"]:
            if key in req.args and req.args[key] is not None:
                try:
                    return int(req.args[key])
                except (ValueError, TypeError):
                    pass
        return None

    @app.route("/cameras/toggle", methods=["POST"])
    def toggle_camera_api():
        """Toggle or set enable/disable state for a specific camera."""
        cam_idx = extract_camera_index(request)
        if cam_idx is None:
            return jsonify({"status": "error", "message": "Missing camera index"}), 400

        cam = engine.get_camera(cam_idx)
        if not cam:
            return jsonify({"status": "error", "message": f"Camera {cam_idx} not found"}), 404

        body = request.get_json(silent=True) or {}
        target_state = body.get("enabled")
        if target_state is None:
            target_state = not cam.enabled
        else:
            target_state = bool(target_state)

        new_state = cam.set_enabled(target_state)
        return jsonify({
            "status": "ok",
            "camera_index": cam.index,
            "enabled": new_state,
            "message": f"Camera {cam.index} {'enabled' if new_state else 'disabled'}"
        }), 200

    @app.route("/cameras/enable", methods=["POST"])
    def enable_camera_api():
        cam_idx = extract_camera_index(request)
        if cam_idx is None:
            return jsonify({"status": "error", "message": "Missing camera index"}), 400
        cam = engine.get_camera(cam_idx)
        if not cam:
            return jsonify({"status": "error", "message": "Camera not found"}), 404
        cam.set_enabled(True)
        return jsonify({"status": "ok", "camera_index": cam.index, "enabled": True}), 200

    @app.route("/cameras/disable", methods=["POST"])
    def disable_camera_api():
        cam_idx = extract_camera_index(request)
        if cam_idx is None:
            return jsonify({"status": "error", "message": "Missing camera index"}), 400
        cam = engine.get_camera(cam_idx)
        if not cam:
            return jsonify({"status": "error", "message": "Camera not found"}), 404
        cam.set_enabled(False)
        return jsonify({"status": "ok", "camera_index": cam.index, "enabled": False}), 200

    def generate_frames_for_camera(cam: CameraDevice, target_fps: int = 20):
        interval = 1.0 / target_fps
        while True:
            t0 = time.time()
            jpeg_bytes, _ = cam.get_latest_jpeg()
            if jpeg_bytes is None:
                time.sleep(0.04)
                continue

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + jpeg_bytes + b'\r\n')

            elapsed = time.time() - t0
            sleep_time = max(0.015, interval - elapsed)
            time.sleep(sleep_time)

    @app.route("/video_feed/<int:index>", methods=["GET"])
    def video_feed_cam(index):
        cam = engine.get_camera(index)
        if not cam:
            return f"Camera {index} not found", 404
        return Response(generate_frames_for_camera(cam), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route("/video_feed", methods=["GET"])
    def video_feed_default():
        cam_idx = extract_camera_index(request)
        if cam_idx is not None:
            cam = engine.get_camera(cam_idx)
        else:
            cam = engine.get_first_camera()

        if not cam:
            return "No active camera available", 404
        return Response(generate_frames_for_camera(cam), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route("/read", methods=["GET", "POST"])
    def read_api():
        body = request.get_json(silent=True) or {}
        retries = body.get("retries", default_retries)
        retry_interval = body.get("retry_interval", default_retry_interval)
        target_cam_idx = extract_camera_index(request)

        try:
            if target_cam_idx is not None:
                cam = engine.get_camera(target_cam_idx)
                if not cam:
                    return jsonify({"status": "error", "message": f"Camera index {target_cam_idx} not found"}), 404
                text = engine.scan_qr_single(cam, retries=retries, retry_interval=retry_interval)
                cam_idx = cam.index
                cam_name = cam.name
            else:
                text, cam_idx, cam_name = engine.scan_qr_all(retries=retries, retry_interval=retry_interval)
        except Exception as e:
            log.exception("Error during QR scan")
            return jsonify({"status": "error", "message": str(e)}), 500

        if text is not None:
            return jsonify({
                "status": "ok",
                "data": text,
                "camera_index": cam_idx,
                "camera_name": cam_name,
                "timestamp": time.time()
            }), 200

        return jsonify({
            "status": "no_qr",
            "message": "No QR code detected across cameras",
            "timestamp": time.time()
        }), 200

    @app.route("/settings", methods=["GET", "POST"])
    def settings_api():
        cam_idx = extract_camera_index(request)
        if cam_idx is not None:
            cam = engine.get_camera(cam_idx)
        else:
            cam = engine.get_first_camera()

        if not cam:
            return jsonify({"status": "error", "message": "Camera not found"}), 404

        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            updated = cam.update_settings(body)
            return jsonify({"status": "ok", "camera_index": cam.index, "settings": updated}), 200
        else:
            return jsonify({"status": "ok", "camera_index": cam.index, "settings": cam.get_settings()}), 200

    @app.route("/settings/auto_adjust", methods=["POST"])
    def auto_adjust_api():
        cam_idx = extract_camera_index(request)
        if cam_idx is not None:
            cam = engine.get_camera(cam_idx)
        else:
            cam = engine.get_first_camera()

        if not cam:
            return jsonify({"status": "error", "message": "Camera not found"}), 404

        new_settings = cam.auto_adjust_settings()
        return jsonify({
            "status": "ok",
            "message": f"Auto-Adjust calculated for Camera {cam.index}",
            "camera_index": cam.index,
            "settings": new_settings
        }), 200

    @app.route("/settings/reset", methods=["POST"])
    def reset_api():
        cam_idx = extract_camera_index(request)
        if cam_idx is not None:
            cam = engine.get_camera(cam_idx)
        else:
            cam = engine.get_first_camera()

        if not cam:
            return jsonify({"status": "error", "message": "Camera not found"}), 404

        defaults = cam.reset_to_factory()
        return jsonify({
            "status": "ok",
            "message": f"Camera {cam.index} restored to factory defaults",
            "camera_index": cam.index,
            "settings": defaults
        }), 200

    @app.route("/cap_screen", methods=["GET", "POST"])
    def cap_screen():
        frame, data, timestamp, filename, cam_idx, cam_name = engine.get_last_qr_capture()

        raw_requested = request.args.get("raw") == "1" or request.args.get("image") == "1" or request.path.endswith(".jpg")
        json_requested = request.args.get("json") == "1" or request.is_json

        if raw_requested:
            if frame is None:
                return "No QR capture available yet", 404
            ret, buffer = cv2.imencode('.jpg', frame)
            return Response(buffer.tobytes(), mimetype='image/jpeg')

        if json_requested:
            if frame is None:
                return jsonify({"status": "no_capture", "message": "No QR code frame captured yet"}), 200
            return jsonify({
                "status": "ok",
                "qr_data": data,
                "camera_index": cam_idx,
                "camera_name": cam_name,
                "timestamp": timestamp,
                "filename": filename,
                "image_url": f"/captures/{filename}" if filename else "/cap_screen?raw=1"
            }), 200

        formatted_time = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else ""
        ago_seconds = round(time.time() - timestamp, 1) if timestamp else 0
        return render_template(
            "cap_screen.html",
            has_frame=(frame is not None),
            data=data,
            camera_index=cam_idx,
            camera_name=cam_name,
            timestamp=timestamp,
            filename=filename,
            formatted_time=formatted_time,
            ago_seconds=ago_seconds
        )

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"status": "pong", "timestamp": time.time()})

    @app.route("/health", methods=["GET"])
    def health():
        cams_info = engine.list_cameras_info()
        all_ok = all(c.get("status") == "ok" for c in cams_info) if cams_info else False
        return jsonify({
            "status": "ok" if all_ok else "degraded",
            "camera_count": len(cams_info),
            "cameras": cams_info,
            "timestamp": time.time()
        }), 200

    @app.route("/upload", methods=["GET"])
    def upload_page():
        return render_template("upload.html")

    @app.route("/upload", methods=["POST"])
    def upload_code():
        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file uploaded"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"status": "error", "message": "Empty filename"}), 400

        if not file.filename.endswith(".py"):
            return jsonify({"status": "error", "message": "Only Python (.py) files are accepted"}), 400

        content = file.read().decode("utf-8", errors="ignore")

        try:
            compile(content, file.filename, "exec")
        except SyntaxError as se:
            return jsonify({
                "status": "error",
                "message": f"Syntax error on line {se.lineno}: {se.msg}"
            }), 400

        target_path = os.path.abspath(__file__)
        backup_path = f"{target_path}.bak"

        try:
            if os.path.exists(target_path):
                shutil.copy2(target_path, backup_path)
                log.info("Created rollback backup file at %s", backup_path)
        except Exception as be:
            log.warning("Could not create backup file: %s", be)

        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("Updated %s with uploaded code", target_path)
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to save file: {str(e)}"}), 500

        return jsonify({
            "status": "ok",
            "message": "Code verified, backup created, and saved successfully!"
        }), 200

    return app


def main():
    parser = argparse.ArgumentParser(description="Multi-Camera Simultaneous QR HTTP Server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5050, help="HTTP port (default: 5050)")
    parser.add_argument("--width", type=int, default=640, help="Camera width (default: 640)")
    parser.add_argument("--height", type=int, default=480, help="Camera height (default: 480)")
    parser.add_argument("--retries", type=int, default=3, help="Default frame attempts per /read call")
    parser.add_argument("--retry-interval", type=float, default=0.2, help="Default seconds between retry attempts")
    args = parser.parse_args()

    engine = MultiCameraEngine(width=args.width, height=args.height)
    app = create_app(engine, args.retries, args.retry_interval)

    log.info("Multi-Camera QR HTTP server listening on %s:%s", args.host, args.port)
    try:
        app.run(host=args.host, port=args.port, threaded=True)
    finally:
        engine.release_all()


if __name__ == "__main__":
    main()