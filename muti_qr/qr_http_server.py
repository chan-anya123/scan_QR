#!/usr/bin/env python3
import argparse
import concurrent.futures
import datetime
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import cv2
import numpy as np
from flask import Flask, jsonify, request, Response, render_template, send_from_directory
from pyzbar.pyzbar import decode as decode_qr

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("qr_http_server")

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
        for i in range(6):
            dev_path = f"/dev/video{i}"
            if os.path.exists(dev_path) and is_video_capture_device(dev_path):
                cameras.append({"index": i, "name": f"Camera {i} ({dev_path})"})

    return cameras if cameras else [{"index": 0, "name": "Camera 0 (/dev/video0)"}]

class Camera:
    """Thread-safe camera manager with background frame capture, auto-reconnect,
    v4l2-ctl baseline hardware auto-adjust mode, per-QR-type overwrite image saving, and persistent profiles.
    """
    def __init__(self, index: int = None, width: int = None, height: int = None, base_dir: str = None):
        if index is None or index < 0:
            avail = list_available_cameras()
            index = avail[0]["index"] if avail else 0
        self.index = index
        self.width = width
        self.height = height
        self.start_time = time.time()
        self.lock = threading.Lock()
        self.cap_lock = threading.Lock()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="qr_io")

        # Directories and configuration file paths
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.setups_dir = os.path.join(self.base_dir, "camera_setups")
        self.captures_dir = os.path.join(self.base_dir, "captures")
        self.config_path = os.path.join(self.base_dir, "camera_setup.json")
        os.makedirs(self.setups_dir, exist_ok=True)
        os.makedirs(self.captures_dir, exist_ok=True)

        # Camera Image Settings
        self.settings = {
            "brightness": 0,         # Range: -100 to 100
            "contrast": 1.0,         # Range: 0.1 to 3.0
            "exposure": 0,          # Range: -5 to 5 (EV Shift)
            "threshold": 0,          # Range: 0 (Off) to 255
        }

        # Captured QR Frame Storage
        self.last_qr_frame = None
        self.last_qr_data = None
        self.last_qr_time = None
        self.last_qr_filename = None

        # Load persistent active configuration for this camera if available
        self.load_saved_setup(index)

        # Initialize Camera Hardware with explicit V4L2 backend
        self.cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        if not self.cap.isOpened():
            log.warning("Could not open camera index %s initially. Background loop will retry...", index)

        self.running = True
        self.latest_frame = None
        self.latest_raw_frame = None
        self.last_frame_time = 0.0

        # Apply initial settings to camera hardware
        self.update_settings(self.settings)

        # Ensure hardware autofocus is disabled on init if present
        self.disable_autofocus(index)

        # Background Thread for Continuous Frame Grabbing
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()
        log.info("Camera engine initialized on index %s (captures dir: %s)", index, self.captures_dir)

    def disable_autofocus(self, index: int = None):
        """Disable autofocus on the camera if supported by hardware controls to ensure stable QR scanning."""
        idx = index if index is not None else self.index
        dev_path = f"/dev/video{idx}"

        # 1. Best-effort via OpenCV
        if self.cap.isOpened():
            try:
                self.cap.set(cv2.CAP_PROP_AUTOFOCUS, 0)
            except Exception as e:
                log.debug("OpenCV disable autofocus warning on index %s: %s", idx, e)

        # 2. Hardware V4L2 control check and disable
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", dev_path, "--list-ctrls"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0:
                ctrls_to_disable = []
                for line in res.stdout.splitlines():
                    if "flags=inactive" in line or "flags=read-only" in line:
                        continue
                    if "focus_automatic_continuous" in line:
                        ctrls_to_disable.append("focus_automatic_continuous=0")
                    elif "focus_auto" in line:
                        ctrls_to_disable.append("focus_auto=0")

                if ctrls_to_disable:
                    subprocess.run(
                        ["v4l2-ctl", "-d", dev_path, f"--set-ctrl={','.join(ctrls_to_disable)}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2,
                    )
                    log.info("Hardware autofocus disabled for camera %s: %s", dev_path, ",".join(ctrls_to_disable))
        except Exception as e:
            log.debug("v4l2-ctl disable autofocus error on %s: %s", dev_path, e)

    def get_config_path(self, index: int = None) -> str:
        """Get path to camera setup file for a specific camera index."""
        idx = index if index is not None else self.index
        return os.path.join(self.base_dir, f"camera_setup_cam{idx}.json")

    def _save_setup_unlocked(self, index: int = None):
        """Save settings for a specific camera index (called when lock is already held or not needed)."""
        idx = index if index is not None else self.index
        cam_file = self.get_config_path(idx)
        data = {
            "camera_index": idx,
            "brightness": self.settings["brightness"],
            "contrast": self.settings["contrast"],
            "exposure": self.settings["exposure"],
            "threshold": self.settings["threshold"],
            "saved_at": time.time(),
            "saved_at_iso": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open(cam_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning("Could not save camera setup (%s): %s", cam_file, e)

    def save_current_setup(self, index: int = None):
        """Thread-safe save of current camera setup to per-camera JSON file."""
        with self.lock:
            self._save_setup_unlocked(index)

    def switch_camera(self, new_index: int) -> int:
        """Dynamically switch the active camera index live and load its per-camera settings."""
        with self.cap_lock:
            with self.lock:
                if self.index == new_index and self.cap.isOpened():
                    return self.index
                log.info("Switching camera index from %s to %s...", self.index, new_index)

                # 1. Save current camera settings to its own file before releasing
                self._save_setup_unlocked(self.index)

                if self.cap.isOpened():
                    self.cap.release()

                time.sleep(0.15)  # Clean hardware release delay
                self.index = new_index
                self.latest_frame = None
                self.latest_raw_frame = None
                self.cap = cv2.VideoCapture(new_index, cv2.CAP_V4L2)
                if self.width:
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                if self.height:
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        # 2. Load and apply settings specific to new camera index
        new_cam_settings = self.load_saved_setup(new_index)
        self.update_settings(new_cam_settings)
        self.disable_autofocus(new_index)

        # 3. Wait briefly (up to 1s) for the first valid frame from the new camera
        t_wait = time.time()
        while time.time() - t_wait < 1.0:
            with self.lock:
                if self.latest_frame is not None:
                    break
            time.sleep(0.05)

        return self.index

    def get_settings(self) -> dict:
        """Return a copy of the current camera settings."""
        with self.lock:
            return dict(self.settings)

    def update_settings(self, new_settings: dict) -> dict:
        """Sanitize and update software image adjustment settings, saving to per-camera setup file."""
        with self.lock:
            if "brightness" in new_settings:
                self.settings["brightness"] = max(-100, min(100, int(new_settings["brightness"])))
            if "contrast" in new_settings:
                self.settings["contrast"] = max(0.1, min(3.0, float(new_settings["contrast"])))
            if "exposure" in new_settings:
                self.settings["exposure"] = max(-5, min(5, int(new_settings["exposure"])))
            if "threshold" in new_settings:
                self.settings["threshold"] = max(0, min(255, int(new_settings["threshold"])))

            self._save_setup_unlocked(self.index)
            return dict(self.settings)

    def auto_adjust_settings(self) -> dict:
        """Perform smart auto-adjustment tailored for QR code scanning without hardware overexposure."""
        # 1. Reset camera hardware controls to clean factory defaults and disable autofocus
        self.reset_to_factory()
        time.sleep(0.2)  # Allow camera hardware ISP auto-exposure to stabilize

        dev_path = f"/dev/video{self.index}"

        # 2. Ensure backlight compensation is OFF to prevent whiteout/overexposure on webcams like Logitech C270
        try:
            subprocess.run(
                ["v4l2-ctl", "-d", dev_path, "--set-ctrl=backlight_compensation=0"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
        except Exception:
            pass

        # 3. Analyze clean unskewed baseline frame
        frame = self.read_frame()
        brightness_shift = 0
        contrast_factor = 1.0

        if frame is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_val = float(np.mean(gray))
            std_val = float(np.std(gray))

            # Target optimal mean luminance for QR code module detection (~120 - 130)
            if mean_val > 145.0:
                # Image is bright / overexposed: reduce brightness to preserve dark QR patterns
                brightness_shift = int(max(-60, 128.0 - mean_val))
                contrast_factor = 1.2
            elif mean_val < 105.0:
                # Image is dark: gently increase brightness
                brightness_shift = int(min(60, 128.0 - mean_val))
                contrast_factor = 1.2
            else:
                # Camera ISP already balanced exposure: keep brightness neutral with crisp contrast
                brightness_shift = 0
                contrast_factor = 1.1

            # Check if dynamic contrast is too flat
            if std_val < 30.0:
                contrast_factor = min(1.8, round(45.0 / max(10.0, std_val), 1))

        # 4. Apply clean software settings
        updated = {
            "brightness": brightness_shift,
            "contrast": round(contrast_factor, 1),
            "exposure": 0,
            "threshold": 0
        }
        log.info("Auto-Adjust calculated for camera %s: %s (baseline mean=%.1f, std=%.1f)", self.index, updated, mean_val if frame is not None else 0, std_val if frame is not None else 0)
        return self.update_settings(updated)

    def save_profile(self, name: str = None, new_settings: dict = None) -> dict:
        """Save a new named profile setup file into camera_setups directory."""
        if new_settings:
            self.update_settings(new_settings)

        prof_id = sanitize_filename(name)
        filename = f"{prof_id}.json" if not prof_id.endswith(".json") else prof_id
        file_path = os.path.join(self.setups_dir, filename)

        with self.lock:
            data = {
                "name": prof_id.replace(".json", ""),
                "profile_name": prof_id.replace(".json", ""),
                "filename": filename,
                "brightness": self.settings["brightness"],
                "contrast": self.settings["contrast"],
                "exposure": self.settings["exposure"],
                "threshold": self.settings["threshold"],
                "settings": {
                    "brightness": self.settings["brightness"],
                    "contrast": self.settings["contrast"],
                    "exposure": self.settings["exposure"],
                    "threshold": self.settings["threshold"],
                },
                "saved_at": time.time(),
                "saved_at_iso": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            log.info("Saved camera setup profile to file: %s", file_path)

            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.error("Failed to save profile JSON (%s): %s", file_path, e)

        return data

    def list_profiles(self) -> list:
        """List all saved camera setup profile files."""
        profiles = []
        if os.path.exists(self.setups_dir):
            for fname in sorted(os.listdir(self.setups_dir)):
                if fname.endswith(".json"):
                    fpath = os.path.join(self.setups_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            pdata = json.load(f)
                            pdata["filename"] = fname
                            pname = pdata.get("profile_name") or pdata.get("name") or fname.replace(".json", "")
                            pdata["name"] = pname
                            pdata["profile_name"] = pname
                            pdata["settings"] = {
                                "brightness": pdata.get("brightness", 0),
                                "contrast": pdata.get("contrast", 1.0),
                                "exposure": pdata.get("exposure", 0),
                                "threshold": pdata.get("threshold", 0),
                            }
                            profiles.append(pdata)
                    except Exception:
                        pass
        return profiles

    def load_profile(self, target: str) -> dict:
        """Load and apply a specific camera setup profile file."""
        if not target:
            raise ValueError("Profile name or filename is required")

        prof_id = sanitize_filename(target)
        filename = f"{prof_id}.json" if not prof_id.endswith(".json") else prof_id
        file_path = os.path.realpath(os.path.join(self.setups_dir, filename))

        # Prevent path traversal outside setups directory
        if not file_path.startswith(os.path.realpath(self.setups_dir)):
            raise ValueError("Invalid profile path")

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Setup profile file '{filename}' not found in {self.setups_dir}")

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.update_settings(data)
        log.info("Loaded and applied camera setup profile: %s", filename)

        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

        return self.get_settings()

    def load_saved_setup(self, index: int = None) -> dict:
        """Load active setup for a specific camera index if present, falling back to defaults."""
        idx = index if index is not None else self.index
        cam_file = self.get_config_path(idx)
        loaded = {
            "brightness": 0,
            "contrast": 1.0,
            "exposure": 0,
            "threshold": 0
        }
        target_path = None
        if os.path.exists(cam_file):
            target_path = cam_file
        elif os.path.exists(self.config_path):
            target_path = self.config_path

        if target_path:
            try:
                with open(target_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                log.info("Loaded camera %s setup from %s", idx, target_path)
                with self.lock:
                    for key in ["brightness", "contrast", "exposure", "threshold"]:
                        if key in data:
                            self.settings[key] = data[key]
                return dict(self.settings)
            except Exception as e:
                log.warning("Failed to read active camera setup from %s: %s", target_path, e)

        with self.lock:
            self.settings = dict(loaded)
        return dict(self.settings)

    def reset_to_factory(self) -> dict:
        """Dynamically query and reset hardware camera V4L2 controls and software settings back to factory default values for active camera."""
        with self.lock:
            self.settings = {
                "brightness": 0,
                "contrast": 1.0,
                "exposure": 0,
                "threshold": 0,
            }

        if self.cap.isOpened():
            try:
                self.cap.set(cv2.CAP_PROP_BRIGHTNESS, 0.5)
                self.cap.set(cv2.CAP_PROP_CONTRAST, 0.5)
            except Exception:
                pass

        dev_path = f"/dev/video{self.index}"
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", dev_path, "--list-ctrls"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0:
                ctrl_sets = []
                for line in res.stdout.splitlines():
                    if "flags=inactive" in line or "flags=read-only" in line:
                        continue
                    match = re.search(r'^\s*([a-z0-9_]+)\s+0x[0-9a-fA-F]+\s+.*default=(-?\d+)', line)
                    if match:
                        ctrl_sets.append(f"{match.group(1)}={match.group(2)}")

                if ctrl_sets:
                    subprocess.run(
                        ["v4l2-ctl", "-d", dev_path, f"--set-ctrl={','.join(ctrl_sets)}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=3,
                    )
                    log.info("Dynamically reset V4L2 controls on %s to defaults: %s", dev_path, ",".join(ctrl_sets))
        except Exception as e:
            log.debug("v4l2-ctl dynamic reset error on %s: %s", dev_path, e)

        # Always ensure autofocus remains disabled
        self.disable_autofocus(self.index)

        self.save_profile(f"default_cam{self.index}")
        self.save_current_setup(self.index)
        return self.get_settings()

    def _async_save_capture(self, save_path: str, frame: np.ndarray, data: str):
        """Asynchronous disk write worker."""
        try:
            cv2.imwrite(save_path, frame)
            log.info("Saved/Overwrote latest QR image for type '%s' at: %s", data, save_path)
            
            # Keep only 10 most recent captures
            try:
                files = [os.path.join(self.captures_dir, f) for f in os.listdir(self.captures_dir) if f.endswith(".jpg")]
                files.sort(key=os.path.getmtime, reverse=True)
                if len(files) > 10:
                    for f_to_delete in files[10:]:
                        os.remove(f_to_delete)
                        log.info("Deleted old capture to save space: %s", f_to_delete)
            except Exception as clean_e:
                log.warning("Failed to clean up old captures: %s", clean_e)
                
        except Exception as e:
            log.error("Failed to save QR capture image to disk: %s", e)

    def set_last_qr_capture(self, frame: np.ndarray, data: str):
        """Save frame snapshot. Overwrites previous image of the SAME QR code type/data to conserve disk space."""
        now = time.time()
        safe_data = re.sub(r'[^a-zA-Z0-9_-]', '_', data.strip())[:50]
        if not safe_data:
            safe_data = "unknown"

        filename = f"qr_{safe_data}.jpg"
        save_path = os.path.join(self.captures_dir, filename)

        with self.lock:
            self.last_qr_frame = frame.copy() if frame is not None else None
            self.last_qr_data = data
            self.last_qr_time = now
            self.last_qr_filename = filename

        if frame is not None:
            # Dispatch disk write to thread pool to prevent blocking HTTP /read response
            frame_to_save = frame.copy()
            self.executor.submit(self._async_save_capture, save_path, frame_to_save, data)

    def get_last_qr_capture(self):
        """Get the latest saved QR capture frame, decoded text, timestamp, and saved filename."""
        with self.lock:
            if self.last_qr_frame is None:
                return None, None, None, None
            return self.last_qr_frame.copy(), self.last_qr_data, self.last_qr_time, self.last_qr_filename

    def _capture_loop(self):
        """Continuous background thread loop to grab and process camera frames."""
        consecutive_failures = 0
        while self.running:
            with self.cap_lock:
                is_opened = self.cap.isOpened()

            if not is_opened:
                log.warning("Camera %s disconnected. Retrying reconnection...", self.index)
                time.sleep(1.0)
                with self.cap_lock:
                    if self.cap.isOpened():
                        self.cap.release()
                    self.cap.open(self.index, cv2.CAP_V4L2)
                    if self.width:
                        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                    if self.height:
                        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
                continue

            # 1. Grab hardware frame without holding state lock
            with self.cap_lock:
                ok, frame = self.cap.read()

            # 2. Read current settings snapshot under lock
            with self.lock:
                b = self.settings.get("brightness", 0)
                c = self.settings.get("contrast", 1.0)
                exp = self.settings.get("exposure", 0)
                t_val = self.settings.get("threshold", 0)

            if ok and frame is not None:
                consecutive_failures = 0
                raw_frame = frame.copy()
                processed = frame

                alpha = c * (2.0 ** (exp * 0.3)) if exp != 0 else c
                beta = float(b)
                if alpha != 1.0 or beta != 0:
                    processed = cv2.convertScaleAbs(processed, alpha=alpha, beta=beta)

                if t_val > 0:
                    gray = cv2.cvtColor(processed, cv2.COLOR_BGR2GRAY)
                    _, binarized = cv2.threshold(gray, t_val, 255, cv2.THRESH_BINARY)
                    processed = cv2.cvtColor(binarized, cv2.COLOR_GRAY2BGR)

                now = time.time()
                with self.lock:
                    self.latest_raw_frame = raw_frame
                    self.latest_frame = processed
                    self.last_frame_time = now
            else:
                consecutive_failures += 1
                if consecutive_failures >= 10:
                    log.warning("Failed 10 consecutive frame reads from camera %s. Re-opening...", self.index)
                    with self.cap_lock:
                        self.cap.release()
                    consecutive_failures = 0
                time.sleep(0.05)

            time.sleep(0.005)

    def read_frame(self) -> np.ndarray:
        """Return a copy of the latest processed frame."""
        with self.lock:
            if self.latest_frame is None:
                return None
            return self.latest_frame.copy()

    def read_raw_frame(self) -> np.ndarray:
        """Return a copy of the latest raw unmanipulated frame."""
        with self.lock:
            if self.latest_raw_frame is None:
                return None
            return self.latest_raw_frame.copy()

    def read_frame_with_time(self):
        """Return a copy of the latest processed frame along with its capture timestamp."""
        with self.lock:
            if self.latest_frame is None:
                return None, 0.0
            return self.latest_frame.copy(), self.last_frame_time

    def is_ready(self) -> bool:
        """Check if camera is active and producing fresh frames."""
        with self.lock:
            return self.cap.isOpened() and (self.latest_frame is not None) and (time.time() - self.last_frame_time < 3.0)

    def get_health(self) -> dict:
        """Return camera diagnostic metrics."""
        with self.lock:
            opened = self.cap.isOpened()
            has_frame = self.latest_frame is not None
            last_time = self.last_frame_time
        uptime = round(time.time() - self.start_time, 1)
        healthy = opened and has_frame and (time.time() - last_time < 3.0)
        return {
            "status": "ok" if healthy else "degraded",
            "camera_connected": opened,
            "camera_index": self.index,
            "has_latest_frame": has_frame,
            "seconds_since_last_frame": round(time.time() - last_time, 2) if last_time > 0 else None,
            "uptime_seconds": uptime,
            "timestamp": time.time(),
        }

    def release(self):
        """Safely stop capture loop, thread executor, and release camera resource."""
        self.running = False
        with self.cap_lock:
            with self.lock:
                if self.cap.isOpened():
                    self.cap.release()
                    log.info("Camera %s released", self.index)
        try:
            self.executor.shutdown(wait=False)
        except Exception:
            pass

def read_current_qr(camera: Camera, retries: int = 3, retry_interval: float = 0.2):
    """Attempt to decode a QR code from the camera with retries and robust multi-stage decoding."""
    last_frame_ok = False
    for attempt in range(1, retries + 1):
        frame = camera.read_frame()
        raw_frame = camera.read_raw_frame()
        if frame is None and raw_frame is None:
            if attempt < retries:
                time.sleep(0.05)
            continue
        last_frame_ok = True

        target_frame = frame if frame is not None else raw_frame
        results = decode_qr(target_frame)

        # Fallback 1: Grayscale on target frame
        if not results:
            gray = cv2.cvtColor(target_frame, cv2.COLOR_BGR2GRAY)
            results = decode_qr(gray)

        # Fallback 2: Raw unmanipulated frame if software threshold/filters degraded the image
        if not results and raw_frame is not None and target_frame is not raw_frame:
            results = decode_qr(raw_frame)
            if not results:
                raw_gray = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2GRAY)
                results = decode_qr(raw_gray)
                # Fallback 3: Adaptive threshold on raw grayscale for high glare / uneven lighting
                if not results:
                    try:
                        adaptive = cv2.adaptiveThreshold(
                            raw_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 10
                        )
                        results = decode_qr(adaptive)
                    except Exception:
                        pass

        if results:
            qr_texts = []
            annotated = target_frame.copy()
            h_img, w_img = annotated.shape[:2]
            ts_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            for res in results:
                qr_text = res.data.decode("utf-8", errors="replace")
                qr_texts.append(qr_text)

                try:
                    points = getattr(res, "polygon", None)
                    rect = getattr(res, "rect", None)
                    if points and len(points) >= 4:
                        pts = np.array([(p.x, p.y) for p in points], dtype=np.int32).reshape((-1, 1, 2))
                        cv2.polylines(annotated, [pts], True, (0, 255, 0), 3)
                        xs = [p.x for p in points]
                        ys = [p.y for p in points]
                        x_min, x_max = min(xs), max(xs)
                        y_min, y_max = min(ys), max(ys)
                    elif rect:
                        x_min, x_max = rect.left, rect.left + rect.width
                        y_min, y_max = rect.top, rect.top + rect.height
                        cv2.rectangle(annotated, (x_min, y_min), (x_max, y_max), (0, 255, 0), 3)
                    else:
                        x_min, x_max, y_min, y_max = 20, 200, 20, 200

                    # Determine label vertical position right below the QR box
                    if y_max + 48 > h_img:
                        # Place above box if near bottom edge of camera image
                        text_y1 = max(22, y_min - 24)
                        text_y2 = text_y1 + 18
                    else:
                        text_y1 = y_max + 22
                        text_y2 = y_max + 40

                    # Text strings
                    line1 = f"{qr_text}"
                    line2 = f"{ts_str}"

                    font = cv2.FONT_HERSHEY_SIMPLEX
                    scale1, scale2 = 0.6, 0.45
                    thick1, thick2 = 2, 1

                    # Calculate badge background dimensions
                    (t1_w, t1_h), _ = cv2.getTextSize(line1, font, scale1, thick1)
                    (t2_w, t2_h), _ = cv2.getTextSize(line2, font, scale2, thick2)
                    max_w = max(t1_w, t2_w) + 16

                    bg_x1 = max(0, x_min)
                    bg_x2 = min(w_img, bg_x1 + max_w)
                    bg_y1 = text_y1 - t1_h - 6
                    bg_y2 = text_y2 + 6

                    # Draw compact dark badge box with thin green border right below QR box
                    cv2.rectangle(annotated, (bg_x1, bg_y1), (bg_x2, bg_y2), (20, 24, 33), -1)
                    cv2.rectangle(annotated, (bg_x1, bg_y1), (bg_x2, bg_y2), (0, 255, 0), 1)

                    # Draw text inside badge
                    cv2.putText(annotated, line1, (bg_x1 + 8, text_y1), font, scale1, (0, 255, 0), thick1, cv2.LINE_AA)
                    cv2.putText(annotated, line2, (bg_x1 + 8, text_y2), font, scale2, (220, 225, 235), thick2, cv2.LINE_AA)
                except Exception as e:
                    log.debug("Failed to overlay text on capture frame: %s", e)

            joined_texts = ", ".join(qr_texts)
            camera.set_last_qr_capture(annotated, joined_texts)
            return joined_texts

        if attempt < retries:
            time.sleep(retry_interval)

    if not last_frame_ok:
        raise RuntimeError("Camera is disconnected or not producing valid frames")
    return None

def create_app(camera: Camera, default_retries: int, default_retry_interval: float, enable_upload: bool = True) -> Flask:
    app = Flask(__name__, template_folder="templates")

    @app.route("/captures/<path:filename>", methods=["GET"])
    def get_capture_file(filename):
        """Serve captured QR images directly by filename."""
        return send_from_directory(camera.captures_dir, filename)

    @app.route("/", methods=["GET"])
    def index():
        return render_template("index.html")

    @app.route("/adjust", methods=["GET"])
    def adjust_page():
        return render_template("adjust.html")

    @app.route("/cameras", methods=["GET"])
    def get_cameras_api():
        """Get connected camera devices and active camera index."""
        avail = list_available_cameras()
        return jsonify({
            "status": "ok",
            "active_index": camera.index,
            "available_cameras": avail
        }), 200

    @app.route("/cameras/switch", methods=["POST"])
    def switch_camera_api():
        """Switch active camera index live."""
        body = request.get_json(silent=True) or {}
        new_idx = body.get("index")
        if new_idx is None:
            return jsonify({"status": "error", "message": "Missing 'index' parameter"}), 400
        try:
            active_idx = camera.switch_camera(int(new_idx))
            return jsonify({
                "status": "ok",
                "message": f"Switched to camera index {active_idx}",
                "active_index": active_idx
            }), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/profiles", methods=["GET"])
    def list_profiles_api():
        """List all saved camera profile JSON files."""
        profiles = camera.list_profiles()
        return jsonify({"status": "ok", "profiles": profiles}), 200

    @app.route("/profiles/save", methods=["POST"])
    def save_profile_api():
        """Save active camera setup as a named profile file."""
        body = request.get_json(silent=True) or {}
        profile_name = body.get("name") or body.get("profile_name")
        saved_data = camera.save_profile(name=profile_name, new_settings=body)
        return jsonify({
            "status": "ok",
            "message": f"Camera setup saved to profile file {saved_data['filename']}",
            "data": saved_data
        }), 200

    @app.route("/profiles/load", methods=["POST"])
    def load_profile_api():
        """Load and apply a saved camera profile."""
        body = request.get_json(silent=True) or {}
        target = body.get("name") or body.get("filename") or body.get("profile_name")
        if not target:
            return jsonify({"status": "error", "message": "Missing 'name' or 'filename' parameter"}), 400

        try:
            active_settings = camera.load_profile(target)
            return jsonify({
                "status": "ok",
                "message": f"Profile '{target}' loaded and applied successfully",
                "settings": active_settings
            }), 200
        except FileNotFoundError as e:
            return jsonify({"status": "error", "message": str(e)}), 404
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    @app.route("/cap_screen", methods=["GET", "POST"])
    def cap_screen():
        frame, data, timestamp, filename = camera.get_last_qr_capture()
        
        raw_requested = request.args.get("raw") == "1" or request.args.get("image") == "1"
        json_requested = request.args.get("json") == "1" or request.is_json

        if raw_requested:
            if frame is None:
                return "No QR capture available yet", 404
            ret, buffer = cv2.imencode('.jpg', frame)
            return Response(buffer.tobytes(), mimetype='image/jpeg')

        if json_requested:
            if frame is None:
                return jsonify({"status": "no_capture", "message": "No QR code frame has been captured yet"}), 200
            return jsonify({
                "status": "ok",
                "qr_data": data,
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
            timestamp=timestamp,
            filename=filename,
            formatted_time=formatted_time,
            ago_seconds=ago_seconds
        )

    @app.route("/settings", methods=["GET", "POST"])
    def settings_api():
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            updated = camera.update_settings(body)
            return jsonify({"status": "ok", "settings": updated}), 200
        else:
            return jsonify(camera.get_settings()), 200

    @app.route("/settings/auto_adjust", methods=["POST"])
    def auto_adjust_api():
        new_settings = camera.auto_adjust_settings()
        return jsonify({"status": "ok", "message": "V4L2 Hardware Auto-Adjust baseline calculated and applied", "settings": new_settings}), 200

    @app.route("/settings/reset", methods=["POST"])
    def reset_factory_api():
        defaults = camera.reset_to_factory()
        return jsonify({"status": "ok", "message": "Camera hardware & software restored to dynamic camera defaults", "settings": defaults}), 200

    def generate_frames():
        last_sent_time = 0.0
        while True:
            frame, frame_time = camera.read_frame_with_time()
            if frame is None:
                time.sleep(0.05)
                continue

            # Skip re-encoding if this frame has already been streamed
            if frame_time == last_sent_time:
                time.sleep(0.01)
                continue

            last_sent_time = frame_time
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ret:
                continue

            frame_bytes = buffer.tobytes()
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.03)  # Cap at ~30 FPS to prevent 100% CPU usage

    @app.route("/video_feed", methods=["GET"])
    def video_feed():
        return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    @app.route("/ping", methods=["GET"])
    def ping():
        return jsonify({"status": "pong", "timestamp": time.time()})

    @app.route("/health", methods=["GET"])
    def health():
        health_info = camera.get_health()
        return jsonify(health_info), 200

    @app.route("/read", methods=["GET", "POST"])
    def read():
        body = request.get_json(silent=True) or {}
        retries = body.get("retries", default_retries)
        retry_interval = body.get("retry_interval", default_retry_interval)

        # Allow selecting camera directly via JSON body or query parameter (?camera=X or ?index=X)
        target_cam = None
        for key in ["camera_index", "index", "camera"]:
            if key in body and body[key] is not None:
                target_cam = body[key]
                break
        if target_cam is None:
            for key in ["camera", "index", "camera_index"]:
                if key in request.args:
                    target_cam = request.args[key]
                    break

        if target_cam is not None:
            try:
                target_idx = int(target_cam)
                if camera.index != target_idx:
                    camera.switch_camera(target_idx)
            except Exception as e:
                return jsonify({
                    "status": "error",
                    "error_code": "CAMERA_SWITCH_ERROR",
                    "message": f"Failed to switch to camera {target_cam}: {str(e)}",
                    "timestamp": time.time()
                }), 400

        if not camera.is_ready():
            return jsonify({
                "status": "error",
                "error_code": "CAMERA_NOT_READY",
                "message": "Camera is disconnected or frame grabber is failing",
                "camera_index": camera.index,
                "timestamp": time.time()
            }), 503

        try:
            text = read_current_qr(camera, retries=retries, retry_interval=retry_interval)
        except Exception as e:
            log.exception("Error during QR code scan")
            return jsonify({
                "status": "error",
                "error_code": "SCAN_ERROR",
                "message": str(e),
                "camera_index": camera.index,
                "timestamp": time.time()
            }), 500

        if text is not None:
            return jsonify({
                "status": "ok",
                "data": text,
                "camera_index": camera.index,
                "timestamp": time.time()
            }), 200

        return jsonify({
            "status": "no_qr",
            "message": " ",
            "data": " ",
            "camera_index": camera.index,
            "timestamp": time.time()
        }), 200

    @app.route("/upload", methods=["GET"])
    def upload_page():
        if not enable_upload:
            return "OTA Upload has been disabled on this server.", 403
        return render_template("upload.html")

    @app.route("/upload", methods=["POST"])
    def upload_code():
        if not enable_upload:
            return jsonify({"status": "error", "message": "OTA upload is disabled on this server"}), 403

        if "file" not in request.files:
            return jsonify({"status": "error", "message": "No file uploaded"}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"status": "error", "message": "Empty filename"}), 400

        if not file.filename.endswith(".py"):
            return jsonify({"status": "error", "message": "Only Python (.py) files are accepted"}), 400

        content = file.read().decode("utf-8", errors="ignore")

        # 1. Verify syntax before touching disk
        try:
            compile(content, file.filename, "exec")
        except SyntaxError as se:
            return jsonify({
                "status": "error",
                "message": f"Syntax error on line {se.lineno}: {se.msg}"
            }), 400

        target_path = os.path.abspath(__file__)
        backup_path = f"{target_path}.bak"

        # 2. Create automatic backup of current working script
        try:
            if os.path.exists(target_path):
                shutil.copy2(target_path, backup_path)
                log.info("Created rollback backup file at %s", backup_path)
        except Exception as be:
            log.warning("Could not create backup file: %s", be)

        # 3. Write new code to disk
        try:
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)
            log.info("Updated %s with new uploaded code", target_path)
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to save file: {str(e)}"}), 500

        # 4. Trigger safe background service restart
        def restart_service():
            time.sleep(1.0)
            try:
                # Systemd allows local user to restart service via polkit without sudo
                res = subprocess.run(["systemctl", "restart", "qr-http-server"], timeout=5)
                if res.returncode != 0:
                    subprocess.run(["sudo", "-n", "systemctl", "restart", "qr-http-server"], timeout=5)
            except Exception as ex:
                log.error("Failed to restart service: %s", ex)
            os._exit(0)

        threading.Thread(target=restart_service, daemon=True).start()

        return jsonify({
            "status": "ok",
            "message": "Code verified, backup created, and saved! Service restarting in 1s..."
        }), 200

    return app

def main():
    parser = argparse.ArgumentParser(description="Standalone QR-code HTTP server")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5050, help="HTTP port (default: 5050)")
    parser.add_argument("--camera-index", type=int, default=None, help="OpenCV camera index (default: auto-detect first valid video stream)")
    parser.add_argument("--width", type=int, default=640, help="Camera width (default: 640)")
    parser.add_argument("--height", type=int, default=480, help="Camera height (default: 480)")
    parser.add_argument("--retries", type=int, default=3, help="Default frame attempts per /read call")
    parser.add_argument("--retry-interval", type=float, default=0.2, help="Default seconds between retry attempts")
    parser.add_argument("--disable-upload", action="store_true", help="Disable OTA code upload endpoint (/upload)")
    parser.add_argument("--use-waitress", action="store_true", help="Use Waitress production WSGI server instead of Flask dev server")
    args = parser.parse_args()

    camera = Camera(index=args.camera_index, width=args.width, height=args.height)
    app = create_app(
        camera,
        args.retries,
        args.retry_interval,
        enable_upload=not args.disable_upload,
    )

    def handle_signal(sig, frame):
        log.info("Received signal %s. Shutting down gracefully...", sig)
        camera.release()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    log.info("QR HTTP server listening on %s:%s", args.host, args.port)
    try:
        if args.use_waitress:
            try:
                from waitress import serve
                log.info("Starting production Waitress WSGI server on %s:%s (threads=16)...", args.host, args.port)
                serve(app, host=args.host, port=args.port, threads=16)
            except ImportError:
                log.warning("Waitress package not found. Falling back to Flask dev server.")
                app.run(host=args.host, port=args.port, threaded=True)
        else:
            app.run(host=args.host, port=args.port, threaded=True)
    finally:
        camera.release()

if __name__ == "__main__":
    main()