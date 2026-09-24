#!/usr/bin/env python3
"""
QR Code HTTP Server — 100% Self-Contained Single-File Class Architecture

Zero external file/folder dependencies:
- All Web UI templates (Scanner, Adjust, Cap Screen, Upload) are embedded directly in Python.
- No 'templates/' folder required!
- Complete V4L2 camera control, real-time background capture, multi-stage QR decoding,
  REST API endpoints, Web UI dashboards, and CLI runner all in this single file.

Usage Examples:
----------------
1. In any Python Script / ROS 1 / ROS 2 Node:
    from qr_http_server import QRHttpServer

    server = QRHttpServer(camera_index=0)
    data = server.read_qr()
    print("Scanned QR:", data)

2. Standalone Server or roslaunch:
    python3 qr_http_server.py --port 5050 --camera-index 0
    roslaunch next_seervisual seer_cam.launch
"""

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
from typing import Optional, Tuple, List, Dict, Any

import cv2
import numpy as np
from flask import Flask, jsonify, request, Response, render_template_string, send_from_directory
from pyzbar.pyzbar import decode as decode_qr

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("qr_http_server")


class QRHttpServer:
    """
    Fully Self-Contained Class-based QR HTTP Server & Vision Engine.
    Requires NO external templates or files. Everything is embedded.
    """

    # -------------------------------------------------------------------------
    # Embedded HTML UI Templates (Single-File Solution, No templates/ folder needed)
    # -------------------------------------------------------------------------

    HTML_INDEX = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>QR Scanner Camera</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-page: #f6f8fd;
            --bg-card: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --pastel-purple: #8b5cf6;
            --pastel-indigo: #6366f1;
            --border-subtle: #e2e8f0;
            --shadow-card: 0 10px 25px -5px rgba(148, 163, 184, 0.12), 0 8px 10px -6px rgba(148, 163, 184, 0.08);
        }
        * { box-sizing: border-box; font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif; }
        body { 
            background: var(--bg-page); 
            background-image: radial-gradient(at 0% 0%, rgba(224, 231, 255, 0.45) 0px, transparent 50%),
                              radial-gradient(at 100% 0%, rgba(220, 252, 231, 0.4) 0px, transparent 50%),
                              radial-gradient(at 50% 100%, rgba(254, 226, 226, 0.3) 0px, transparent 50%);
            color: var(--text-main); margin: 0; padding: 0; min-height: 100vh; 
        }
        .nav-bar { 
            background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(226, 232, 240, 0.8); padding: 10px 24px; 
            position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 10px rgba(0,0,0,0.02);
        }
        .nav-container { max-width: 900px; margin: 0 auto; display: flex; gap: 8px; align-items: center; justify-content: center; }
        .nav-tab { 
            padding: 10px 18px; text-decoration: none; color: var(--text-muted); 
            font-weight: 600; font-size: 14px; border-radius: 10px; transition: all 0.2s; 
        }
        .nav-tab:hover { color: #4338ca; background: #eef2ff; }
        .nav-tab.active { color: #4f46e5; background: #ede9fe; font-weight: 700; }
        .container { max-width: 900px; margin: 28px auto 40px auto; padding: 0 20px; }
        .card { 
            background: var(--bg-card); border-radius: 20px; padding: 30px; 
            box-shadow: var(--shadow-card); border: 1px solid #edf2f7; 
        }
        .scanner-box { text-align: center; max-width: 680px; margin: 0 auto; }
        h2 { font-size: 22px; font-weight: 800; color: #0f172a; margin-top: 0; margin-bottom: 18px; letter-spacing: -0.4px; }
        #video { border-radius: 14px; max-width: 100%; height: auto; border: 1px solid var(--border-subtle); background: #0f172a; box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
        .btn { 
            padding: 13px 26px; font-size: 15px; font-weight: 700; cursor: pointer; 
            background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; border: none; 
            border-radius: 12px; transition: all 0.2s; display: inline-flex; align-items: center; 
            justify-content: center; gap: 8px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.28);
        }
        .btn:hover { 
            background: linear-gradient(135deg, #4f46e5, #7c3aed); transform: translateY(-1px);
            box-shadow: 0 8px 20px rgba(99, 102, 241, 0.35);
        }
        .btn:disabled { opacity: 0.6; cursor: not-allowed; transform: none; box-shadow: none; }
        .btn-reset { background: #f8fafc; color: #64748b; border: 1px solid #e2e8f0; box-shadow: none; }
        .btn-reset:hover { background: #e2e8f0; color: #1e293b; box-shadow: none; }
        .reset-toast { font-size: 13px; color: #059669; font-weight: 700; margin-top: 10px; opacity: 0; transition: opacity 0.3s; height: 20px; }
        #resultBox { 
            margin-top: 18px; font-size: 18px; font-weight: 800; color: #065f46; background: #f0fdf4; 
            padding: 16px 20px; border-radius: 14px; border: 1px solid #a7f3d0; word-break: break-all; text-align: left;
        }
        .cap-link { font-size: 13px; margin-top: 8px; display: inline-block; color: #4f46e5; font-weight: 700; text-decoration: none; }
        .cap-link:hover { text-decoration: underline; }
        .camera-select-row { margin-bottom: 20px; display: flex; align-items: center; justify-content: center; gap: 10px; flex-wrap: wrap; }
        .select-input { 
            padding: 10px 16px; border-radius: 10px; border: 1px solid #cbd5e1; font-weight: 700; 
            background: #f8fafc; color: #1e293b; font-size: 14px; outline: none; cursor: pointer; min-width: 240px; transition: all 0.2s;
        }
        .select-input:focus { border-color: #6366f1; background: white; box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15); }
    </style>
</head>
<body>
    <nav class="nav-bar">
        <div class="nav-container">
            <a href="/" class="nav-tab active">Scanner</a>
            <a href="/adjust" class="nav-tab">Adjust Camera</a>
            <a href="/cap_screen" class="nav-tab">Captured Screen</a>
            <a href="/upload" class="nav-tab">Code Updater</a>
        </div>
    </nav>
    <div class="container">
        <div class="card scanner-box">
            <h2>Streaming QR Code Scanner</h2>
            <div class="camera-select-row">
                <label for="cameraSelect" style="font-weight: 700; color: #475569; font-size: 14px;">Select Active Camera:</label>
                <select id="cameraSelect" onchange="onCameraSelected(this.value)" class="select-input">
                    <option value="0">Loading camera devices...</option>
                </select>
            </div>
            <img id="video" src="/video_feed" width="640" height="480" alt="Live Camera Feed" />
            <br>
            <div style="display: flex; justify-content: center; gap: 12px; flex-wrap: wrap; margin-top: 20px;">
                <button type="button" class="btn" onclick="scanQR()">Scan QR Now</button>
                <button type="button" id="btnReset" class="btn btn-reset" onclick="resetActiveCamera()">Reset Camera Defaults</button>
            </div>
            <div id="resetToast" class="reset-toast"></div>
            <div id="resultBox">Result: <span id="result">-</span><br><a id="capLink" href="/cap_screen" class="cap-link" style="display:none;">View Captured Screen &rarr;</a></div>
        </div>
    </div>
    <script>
        function loadCameras() {
            fetch('/cameras')
                .then(r => r.json())
                .then(data => {
                    const sel = document.getElementById('cameraSelect');
                    sel.innerHTML = '';
                    if (data.available_cameras && data.available_cameras.length > 0) {
                        data.available_cameras.forEach(cam => {
                            const opt = document.createElement('option');
                            opt.value = cam.index;
                            opt.innerText = cam.name;
                            if (cam.index === data.active_index) opt.selected = true;
                            sel.appendChild(opt);
                        });
                    } else {
                        sel.innerHTML = '<option value="0">Camera 0 (/dev/video0)</option>';
                    }
                })
                .catch(err => console.error('Failed to load camera list:', err));
        }
        function onCameraSelected(newIndex) {
            fetch('/cameras/switch', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ index: parseInt(newIndex) })
            })
            .then(r => r.json())
            .then(data => {
                if (data.status === 'ok') {
                    document.getElementById('video').src = '/video_feed?t=' + Date.now();
                } else {
                    alert('Failed to switch camera: ' + (data.message || 'Error'));
                }
            })
            .catch(err => alert('Failed to connect to server to switch camera'));
        }
        function scanQR() {
            document.getElementById('result').innerText = "Scanning...";
            document.getElementById('result').style.color = "#d97706";
            document.getElementById('capLink').style.display = "none";
            fetch('/read', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({retries: 5, retry_interval: 0.3})
            })
            .then(r => r.json())
            .then(data => {
                if(data.status === 'ok') {
                    document.getElementById('result').innerText = data.data;
                    document.getElementById('result').style.color = "#059669";
                    document.getElementById('capLink').style.display = "inline-block";
                } else {
                    document.getElementById('result').innerText = data.message || "No QR code detected";
                    document.getElementById('result').style.color = "#dc2626";
                }
            })
            .catch(err => {
                document.getElementById('result').innerText = "Connection Error!";
                document.getElementById('result').style.color = "#dc2626";
            });
        }
        function resetActiveCamera() {
            const btn = document.getElementById('btnReset');
            const toast = document.getElementById('resetToast');
            if (btn) { btn.disabled = true; btn.innerText = "Resetting..."; }
            fetch('/settings/reset', { method: 'POST' })
                .then(r => r.json())
                .then(data => {
                    document.getElementById('video').src = '/video_feed?t=' + Date.now();
                    if (toast) {
                        toast.innerText = "Active camera restored to defaults!";
                        toast.style.opacity = '1';
                        setTimeout(() => { toast.style.opacity = '0'; }, 3000);
                    }
                })
                .catch(err => alert("Failed to reset camera defaults"))
                .finally(() => {
                    if (btn) { btn.disabled = false; btn.innerText = "Reset Camera Defaults"; }
                });
        }
        window.onload = loadCameras;
    </script>
</body>
</html>"""

    HTML_ADJUST = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Camera Adjust - QR Scanner</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-page: #f6f8fd;
            --bg-card: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border-subtle: #e2e8f0;
            --shadow-card: 0 10px 25px -5px rgba(148, 163, 184, 0.12), 0 8px 10px -6px rgba(148, 163, 184, 0.08);
        }
        * { box-sizing: border-box; font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif; }
        body { 
            background: var(--bg-page); 
            background-image: radial-gradient(at 0% 0%, rgba(224, 231, 255, 0.45) 0px, transparent 50%),
                              radial-gradient(at 100% 0%, rgba(220, 252, 231, 0.4) 0px, transparent 50%),
                              radial-gradient(at 50% 100%, rgba(254, 226, 226, 0.3) 0px, transparent 50%);
            color: var(--text-main); margin: 0; padding: 0; min-height: 100vh; 
        }
        .nav-bar { 
            background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(226, 232, 240, 0.8); padding: 10px 24px; 
            position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 10px rgba(0,0,0,0.02);
        }
        .nav-container { max-width: 900px; margin: 0 auto; display: flex; gap: 8px; align-items: center; justify-content: center; }
        .nav-tab { 
            padding: 10px 18px; text-decoration: none; color: var(--text-muted); 
            font-weight: 600; font-size: 14px; border-radius: 10px; transition: all 0.2s; 
        }
        .nav-tab:hover { color: #4338ca; background: #eef2ff; }
        .nav-tab.active { color: #4f46e5; background: #ede9fe; font-weight: 700; }
        .container { max-width: 900px; margin: 28px auto 40px auto; padding: 0 20px; }
        .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .card { 
            background: var(--bg-card); border-radius: 20px; padding: 26px; 
            box-shadow: var(--shadow-card); border: 1px solid #edf2f7; 
        }
        h2 { margin-top: 0; font-size: 18px; font-weight: 800; color: #0f172a; margin-bottom: 18px; display: flex; align-items: center; justify-content: space-between; }
        .video-box { border-radius: 14px; overflow: hidden; background: #0f172a; display: flex; justify-content: center; align-items: center; min-height: 240px; border: 1px solid var(--border-subtle); box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
        .video-box img { width: 100%; height: auto; display: block; }
        .control-group { margin-bottom: 18px; }
        .control-label { display: flex; justify-content: space-between; font-weight: 700; font-size: 14px; margin-bottom: 8px; color: #334155; }
        .control-value { color: #6366f1; font-weight: 800; background: #eef2ff; padding: 2px 8px; border-radius: 6px; font-size: 13px; }
        input[type="range"] { width: 100%; height: 8px; background: #e2e8f0; border-radius: 4px; outline: none; -webkit-appearance: none; cursor: pointer; }
        input[type="range"]::-webkit-slider-thumb { -webkit-appearance: none; width: 20px; height: 20px; border-radius: 50%; background: #6366f1; cursor: pointer; border: 3px solid #fff; box-shadow: 0 2px 6px rgba(99, 102, 241, 0.4); }
        .btn { padding: 12px 18px; border: none; border-radius: 12px; font-weight: 700; cursor: pointer; transition: all 0.2s; font-size: 14px; display: inline-flex; align-items: center; justify-content: center; gap: 6px; }
        .btn-auto { background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; width: 100%; margin-bottom: 14px; box-shadow: 0 4px 14px rgba(99, 102, 241, 0.25); }
        .btn-auto:hover { background: linear-gradient(135deg, #4f46e5, #7c3aed); transform: translateY(-1px); }
        .btn-save { background: #10b981; color: white; width: 100%; margin-top: 8px; }
        .btn-save:hover { background: #059669; }
        .btn-load { background: #6366f1; color: white; width: 100%; margin-top: 8px; }
        .btn-load:hover { background: #4f46e5; }
        .btn-outline { background: #f8fafc; border: 1px solid #e2e8f0; color: #64748b; width: 100%; margin-top: 14px; }
        .btn-outline:hover { background: #e2e8f0; color: #1e293b; }
        .text-input, .select-input { width: 100%; padding: 10px 14px; border: 1px solid #cbd5e1; border-radius: 10px; font-size: 14px; margin-bottom: 8px; outline: none; background: #f8fafc; font-weight: 600; }
        .text-input:focus, .select-input:focus { border-color: #6366f1; background: white; }
        .save-toast { font-size: 13px; color: #059669; font-weight: 700; opacity: 0; transition: opacity 0.3s; background: #ecfdf5; padding: 4px 10px; border-radius: 8px; border: 1px solid #a7f3d0; }
        .profile-card { margin-top: 20px; padding-top: 20px; border-top: 1px dashed #cbd5e1; }
    </style>
</head>
<body>
    <nav class="nav-bar">
        <div class="nav-container">
            <a href="/" class="nav-tab">Scanner</a>
            <a href="/adjust" class="nav-tab active">Adjust Camera</a>
            <a href="/cap_screen" class="nav-tab">Captured Screen</a>
            <a href="/upload" class="nav-tab">Code Updater</a>
        </div>
    </nav>
    <div class="container">
        <div class="grid">
            <div class="card">
                <h2><span>Live Feed Preview</span><span id="toast" class="save-toast">Applied</span></h2>
                <div class="video-box"><img id="video" src="/video_feed" alt="Camera Stream" /></div>
                <button class="btn btn-outline" onclick="restoreFactorySetup()">Reset Active Camera to Defaults</button>
            </div>
            <div class="card">
                <h2>Image Adjustments</h2>
                <div class="control-group">
                    <div class="control-label"><span>Brightness</span><span class="control-value" id="brightnessVal">0</span></div>
                    <input type="range" id="brightness" min="-100" max="100" value="0" oninput="onControlChange()">
                </div>
                <div class="control-group">
                    <div class="control-label"><span>Exposure</span><span class="control-value" id="exposureVal">0</span></div>
                    <input type="range" id="exposure" min="-5" max="5" value="0" oninput="onControlChange()">
                </div>
                <div class="control-group">
                    <div class="control-label"><span>Contrast</span><span class="control-value" id="contrastVal">1.0</span></div>
                    <input type="range" id="contrast" min="0.1" max="3.0" step="0.1" value="1.0" oninput="onControlChange()">
                </div>
                <div class="control-group">
                    <div class="control-label"><span>Threshold (0 = Off)</span><span class="control-value" id="thresholdVal">Off</span></div>
                    <input type="range" id="threshold" min="0" max="255" value="0" oninput="onControlChange()">
                </div>
                <button class="btn btn-auto" onclick="triggerAutoAdjust()">Smart Auto-Adjust</button>
                <div class="profile-card">
                    <h3 style="font-size: 15px; margin-top: 0; color: #1e293b;">Save Setup Profile</h3>
                    <input type="text" id="profileName" class="text-input" placeholder="e.g. low_light_station">
                    <button class="btn btn-save" onclick="saveProfile()">Save Named Profile</button>
                    <h3 style="font-size: 15px; margin-top: 16px; margin-bottom: 6px; color: #1e293b;">Load Saved Profile</h3>
                    <select id="profileSelect" class="select-input"><option value="">Loading profiles...</option></select>
                    <button class="btn btn-load" onclick="loadProfile()">Load & Apply</button>
                </div>
            </div>
        </div>
    </div>
    <script>
        let changeDebounce = null;
        function loadSettings() {
            fetch('/settings').then(r => r.json()).then(data => {
                document.getElementById('brightness').value = data.brightness;
                document.getElementById('brightnessVal').innerText = data.brightness;
                document.getElementById('contrast').value = data.contrast;
                document.getElementById('contrastVal').innerText = data.contrast.toFixed(1);
                document.getElementById('exposure').value = data.exposure;
                document.getElementById('exposureVal').innerText = data.exposure;
                document.getElementById('threshold').value = data.threshold;
                document.getElementById('thresholdVal').innerText = data.threshold === 0 ? "Off" : data.threshold;
            }).catch(err => console.error("Failed to load settings:", err));
            loadProfiles();
        }
        function loadProfiles() {
            fetch('/profiles').then(r => r.json()).then(data => {
                const sel = document.getElementById('profileSelect');
                sel.innerHTML = '';
                if (data.profiles && data.profiles.length > 0) {
                    data.profiles.forEach(p => {
                        const opt = document.createElement('option');
                        opt.value = p.filename || p.name || p.profile_name;
                        const profName = p.profile_name || p.name || (p.filename ? p.filename.replace('.json', '') : 'Profile');
                        const b = (p.settings && p.settings.brightness !== undefined) ? p.settings.brightness : (p.brightness !== undefined ? p.brightness : 0);
                        const c = (p.settings && p.settings.contrast !== undefined) ? p.settings.contrast : (p.contrast !== undefined ? p.contrast : 1.0);
                        opt.innerText = `${profName} (Brightness: ${b}, Contrast: ${Number(c).toFixed(1)}x)`;
                        sel.appendChild(opt);
                    });
                } else {
                    sel.innerHTML = '<option value="">No saved profiles yet</option>';
                }
            }).catch(err => console.error("Failed to load profiles:", err));
        }
        function onControlChange() {
            const b = parseInt(document.getElementById('brightness').value);
            const c = parseFloat(document.getElementById('contrast').value);
            const exp = parseInt(document.getElementById('exposure').value);
            const t = parseInt(document.getElementById('threshold').value);
            document.getElementById('brightnessVal').innerText = b;
            document.getElementById('contrastVal').innerText = c.toFixed(1);
            document.getElementById('exposureVal').innerText = exp;
            document.getElementById('thresholdVal').innerText = t === 0 ? "Off" : t;
            clearTimeout(changeDebounce);
            changeDebounce = setTimeout(() => {
                fetch('/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ brightness: b, contrast: c, exposure: exp, threshold: t })
                }).then(r => r.json()).then(() => showToast("Saved"))
                .catch(err => console.error("Failed to update settings:", err));
            }, 100);
        }
        function triggerAutoAdjust() {
            fetch('/settings/auto_adjust', { method: 'POST' }).then(r => r.json()).then(data => {
                loadSettings();
                document.getElementById('video').src = '/video_feed?t=' + Date.now();
                showToast("Auto-Adjust Applied");
            }).catch(err => alert("Auto-adjust failed: " + err));
        }
        function restoreFactorySetup() {
            fetch('/settings/reset', { method: 'POST' }).then(r => r.json()).then(data => {
                loadSettings();
                document.getElementById('video').src = '/video_feed?t=' + Date.now();
                showToast("Reset to Camera Defaults");
            }).catch(err => alert("Failed to reset camera defaults"));
        }
        function saveProfile() {
            const nameInput = document.getElementById('profileName');
            const name = nameInput.value.trim();
            if (!name) { alert("Please enter a profile name"); return; }
            fetch('/profiles/save', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name, profile_name: name })
            }).then(r => r.json()).then(data => {
                nameInput.value = '';
                loadProfiles();
                showToast("Profile Saved");
            }).catch(err => alert("Failed to save profile: " + err));
        }
        function loadProfile() {
            const sel = document.getElementById('profileSelect');
            const target = sel.value;
            if (!target) { alert("Please select a profile first"); return; }
            fetch('/profiles/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ filename: target, name: target })
            }).then(r => r.json()).then(data => {
                if (data.status === 'ok') {
                    loadSettings();
                    const img = document.getElementById('video');
                    if (img) img.src = '/video_feed?t=' + Date.now();
                    showToast("Profile Loaded & Applied");
                } else {
                    alert("Failed to load profile: " + (data.message || 'Error'));
                }
            }).catch(err => alert("Failed to load profile: " + err));
        }
        function showToast(msg) {
            const toast = document.getElementById('toast');
            toast.innerText = msg; toast.style.opacity = '1';
            setTimeout(() => { toast.style.opacity = '0'; }, 2000);
        }
        window.onload = loadSettings;
    </script>
</body>
</html>"""

    HTML_CAP_SCREEN = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Captured QR Screen</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-page: #f6f8fd;
            --bg-card: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border-subtle: #e2e8f0;
            --shadow-card: 0 10px 25px -5px rgba(148, 163, 184, 0.12), 0 8px 10px -6px rgba(148, 163, 184, 0.08);
        }
        * { box-sizing: border-box; font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif; }
        body { 
            background: var(--bg-page); 
            background-image: radial-gradient(at 0% 0%, rgba(224, 231, 255, 0.45) 0px, transparent 50%),
                              radial-gradient(at 100% 0%, rgba(220, 252, 231, 0.4) 0px, transparent 50%),
                              radial-gradient(at 50% 100%, rgba(254, 226, 226, 0.3) 0px, transparent 50%);
            color: var(--text-main); margin: 0; padding: 0; min-height: 100vh; 
        }
        .nav-bar { 
            background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(226, 232, 240, 0.8); padding: 10px 24px; 
            position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 10px rgba(0,0,0,0.02);
        }
        .nav-container { max-width: 900px; margin: 0 auto; display: flex; gap: 8px; align-items: center; justify-content: center; }
        .nav-tab { 
            padding: 10px 18px; text-decoration: none; color: var(--text-muted); 
            font-weight: 600; font-size: 14px; border-radius: 10px; transition: all 0.2s; 
        }
        .nav-tab:hover { color: #4338ca; background: #eef2ff; }
        .nav-tab.active { color: #4f46e5; background: #ede9fe; font-weight: 700; }
        .container { max-width: 780px; margin: 30px auto 50px auto; padding: 0 20px; }
        .card { 
            background: var(--bg-card); border-radius: 20px; padding: 30px; 
            box-shadow: var(--shadow-card); border: 1px solid #edf2f7; text-align: center; 
        }
        h2 { margin-top: 0; color: #0f172a; font-size: 20px; font-weight: 800; letter-spacing: -0.4px; }
        .img-box { border-radius: 14px; overflow: hidden; background: #0f172a; margin: 18px 0; border: 1px solid var(--border-subtle); display: inline-block; width: 100%; box-shadow: 0 4px 12px rgba(0,0,0,0.06); }
        .img-box img { width: 100%; height: auto; display: block; }
        .info-group { margin-top: 14px; text-align: left; background: #f8fafc; padding: 14px 18px; border-radius: 12px; border: 1px solid var(--border-subtle); }
        .info-label { font-size: 11px; font-weight: 800; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px; letter-spacing: 0.6px; }
        .qr-text { font-size: 18px; font-weight: 800; color: #059669; word-break: break-all; font-family: monospace; }
        .time-text { font-size: 14px; color: #334155; font-weight: 600; }
        .empty-state { padding: 40px 20px; color: var(--text-muted); font-size: 15px; }
        .btn { 
            padding: 13px 28px; font-size: 15px; font-weight: 700; cursor: pointer; 
            background: linear-gradient(135deg, #6366f1, #8b5cf6); color: white; border: none; 
            border-radius: 12px; text-decoration: none; display: inline-block; transition: all 0.2s; 
            box-shadow: 0 4px 14px rgba(99, 102, 241, 0.28);
        }
        .btn:hover { 
            background: linear-gradient(135deg, #4f46e5, #7c3aed); transform: translateY(-1px);
            box-shadow: 0 8px 20px rgba(99, 102, 241, 0.35);
        }
    </style>
</head>
<body>
    <nav class="nav-bar">
        <div class="nav-container">
            <a href="/" class="nav-tab">Scanner</a>
            <a href="/adjust" class="nav-tab">Adjust Camera</a>
            <a href="/cap_screen" class="nav-tab active">Captured Screen</a>
            <a href="/upload" class="nav-tab">Code Updater</a>
        </div>
    </nav>
    <div class="container">
        {% if not has_frame %}
        <div class="card">
            <h2>Captured QR Screen</h2>
            <div class="empty-state">
                <p>No QR code frame captured yet.</p>
                <p style="font-size: 14px; color: var(--text-muted);">Go to the <a href="/" style="color: #6366f1; font-weight: 700;">Scanner tab</a> and scan a QR code to automatically capture & download the screen!</p>
            </div>
        </div>
        {% else %}
        <div class="card">
            <h2>Last Captured QR Screen</h2>
            <div class="img-box">
                <img src="/cap_screen?raw=1&t={{ timestamp }}" alt="Captured QR Frame">
            </div>
            <div class="info-group">
                <div class="info-label">Decoded QR Content / Type:</div>
                <div class="qr-text">{{ data }}</div>
            </div>
            <div class="info-group">
                <div class="info-label">Saved Image File:</div>
                <div class="time-text"><code><a href="/captures/{{ filename }}" target="_blank" style="color: #4f46e5; font-weight: 700;">captures/{{ filename }}</a></code></div>
            </div>
            <div class="info-group">
                <div class="info-label">Captured At:</div>
                <div class="time-text">{{ formatted_time }} ({{ ago_seconds }}s ago)</div>
            </div>
            <div style="margin-top: 24px;">
                <a href="/captures/{{ filename }}" download="{{ filename }}" class="btn">Download Snapshot</a>
            </div>
        </div>
        {% endif %}
    </div>
</body>
</html>"""

    HTML_UPLOAD = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Robot Code Updater</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-page: #f6f8fd;
            --bg-card: #ffffff;
            --text-main: #1e293b;
            --text-muted: #64748b;
            --border-subtle: #e2e8f0;
            --shadow-card: 0 10px 25px -5px rgba(148, 163, 184, 0.12), 0 8px 10px -6px rgba(148, 163, 184, 0.08);
        }
        * { box-sizing: border-box; font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif; }
        body { 
            background: var(--bg-page); 
            background-image: radial-gradient(at 0% 0%, rgba(224, 231, 255, 0.45) 0px, transparent 50%),
                              radial-gradient(at 100% 0%, rgba(220, 252, 231, 0.4) 0px, transparent 50%),
                              radial-gradient(at 50% 100%, rgba(254, 226, 226, 0.3) 0px, transparent 50%);
            color: var(--text-main); margin: 0; padding: 0; min-height: 100vh; 
        }
        .nav-bar { 
            background: rgba(255, 255, 255, 0.85); backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(226, 232, 240, 0.8); padding: 10px 24px; 
            position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 10px rgba(0,0,0,0.02);
        }
        .nav-container { max-width: 900px; margin: 0 auto; display: flex; gap: 8px; align-items: center; justify-content: center; }
        .nav-tab { 
            padding: 10px 18px; text-decoration: none; color: var(--text-muted); 
            font-weight: 600; font-size: 14px; border-radius: 10px; transition: all 0.2s; 
        }
        .nav-tab:hover { color: #4338ca; background: #eef2ff; }
        .nav-tab.active { color: #4f46e5; background: #ede9fe; font-weight: 700; }
        .container { max-width: 580px; margin: 40px auto; padding: 0 20px; }
        .card { 
            background: var(--bg-card); border-radius: 20px; padding: 32px; 
            box-shadow: var(--shadow-card); border: 1px solid #edf2f7; text-align: center; 
        }
        h2 { color: #4338ca; margin-bottom: 8px; margin-top: 0; font-size: 22px; font-weight: 800; }
        p { color: var(--text-muted); font-size: 14px; margin-bottom: 24px; }
        .file-drop { 
            border: 2px dashed #c7d2fe; border-radius: 16px; padding: 38px 20px; 
            background: #f5f3ff; cursor: pointer; transition: all 0.2s; 
        }
        .file-drop:hover { background: #ede9fe; border-color: #818cf8; }
        input[type="file"] { display: none; }
        .file-name { margin-top: 12px; font-weight: 700; color: #059669; font-size: 14px; word-break: break-all; }
        .btn { 
            margin-top: 22px; width: 100%; padding: 14px; 
            background: linear-gradient(135deg, #6366f1, #8b5cf6); color: #fff; border: none; 
            border-radius: 12px; font-size: 15px; font-weight: 700; cursor: pointer; transition: all 0.2s; 
            box-shadow: 0 4px 14px rgba(99, 102, 241, 0.28);
        }
        .btn:hover { background: linear-gradient(135deg, #4f46e5, #7c3aed); transform: translateY(-1px); }
        .btn:disabled { background: #e2e8f0; color: #94a3b8; cursor: not-allowed; transform: none; box-shadow: none; }
        #statusBox { margin-top: 20px; padding: 14px 18px; border-radius: 12px; font-size: 14px; display: none; text-align: left; }
        .success { background: #ecfdf5; color: #065f46; border: 1px solid #a7f3d0; }
        .error { background: #fff1f2; color: #9f1239; border: 1px solid #fecdd3; }
    </style>
</head>
<body>
    <nav class="nav-bar">
        <div class="nav-container">
            <a href="/" class="nav-tab">Scanner</a>
            <a href="/adjust" class="nav-tab">Adjust Camera</a>
            <a href="/cap_screen" class="nav-tab">Captured Screen</a>
            <a href="/upload" class="nav-tab active">Code Updater</a>
        </div>
    </nav>
    <div class="container">
        <div class="card">
            <h2>Robot Code Updater</h2>
            <p>Upload new <code>qr_http_server.py</code> file to update the server live.</p>
            <div class="file-drop" onclick="document.getElementById('fileInput').click()">
                <span style="font-weight: 600; color: #4338ca;">Click or Drag <b>.py</b> file here</span>
                <div id="fileName" class="file-name"></div>
            </div>
            <input type="file" id="fileInput" accept=".py" onchange="fileSelected(this)">
            <button id="uploadBtn" class="btn" onclick="uploadCode()" disabled>Upload & Restart Service</button>
            <div id="statusBox"></div>
        </div>
    </div>
    <script>
        function fileSelected(input) {
            if (input.files && input.files[0]) {
                document.getElementById('fileName').innerText = input.files[0].name;
                document.getElementById('uploadBtn').disabled = false;
            }
        }
        function uploadCode() {
            const fileInput = document.getElementById('fileInput');
            if (!fileInput.files[0]) return;
            const formData = new FormData();
            formData.append('file', fileInput.files[0]);
            const statusBox = document.getElementById('statusBox');
            const uploadBtn = document.getElementById('uploadBtn');
            statusBox.style.display = 'block';
            statusBox.className = '';
            statusBox.innerText = 'Verifying syntax & uploading code...';
            uploadBtn.disabled = true;
            fetch('/upload', { method: 'POST', body: formData })
            .then(res => res.json().then(data => ({ status: res.status, body: data })))
            .then(res => {
                if (res.status === 200 && res.body.status === 'ok') {
                    statusBox.className = 'success';
                    statusBox.innerText = res.body.message;
                    setTimeout(() => { location.reload(); }, 4000);
                } else {
                    statusBox.className = 'error';
                    statusBox.innerText = 'Error: ' + (res.body.message || 'Upload failed');
                    uploadBtn.disabled = false;
                }
            })
            .catch(err => {
                statusBox.className = 'error';
                statusBox.innerText = 'Network error during upload';
                uploadBtn.disabled = false;
            });
        }
    </script>
</body>
</html>"""

    # -------------------------------------------------------------------------
    # Static Hardware & File Utility Methods
    # -------------------------------------------------------------------------

    @staticmethod
    def sanitize_filename(name: str) -> str:
        """Sanitize string to be safe for filenames."""
        clean = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', str(name))
        return clean[:50] if clean else "unknown"

    @staticmethod
    def is_video_capture_device(dev_path: str) -> bool:
        """Check if the /dev/videoX device actually supports Video Capture (filters out metadata nodes)."""
        # Method 1: Ask v4l2-ctl for video capture format (fails on metadata-only nodes like /dev/video1)
        try:
            res = subprocess.run(
                ["v4l2-ctl", "-d", dev_path, "--get-fmt-video"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1
            )
            if res.returncode == 0:
                return True
            return False
        except FileNotFoundError:
            pass
        except Exception:
            pass

        # Method 2: If v4l-utils is not installed, probe via OpenCV
        try:
            match = re.search(r'/dev/video(\d+)', dev_path)
            if match:
                test_idx = int(match.group(1))
                test_cap = cv2.VideoCapture(test_idx, cv2.CAP_V4L2)
                opened = test_cap.isOpened()
                test_cap.release()
                return opened
        except Exception:
            pass

        return False

    @staticmethod
    def list_available_cameras() -> List[Dict[str, Any]]:
        """Enumerate physical video capture devices using v4l2-ctl with OpenCV fallback."""
        available = []
        seen_indices = set()

        try:
            out = subprocess.check_output(["v4l2-ctl", "--list-devices"], stderr=subprocess.DEVNULL, text=True)
            lines = out.strip().split("\n")
            current_name = "Camera"
            for line in lines:
                if not line:
                    continue
                if not line.startswith("\t") and not line.startswith(" "):
                    current_name = line.split("(")[0].strip()
                else:
                    match = re.search(r'/dev/video(\d+)', line)
                    if match:
                        idx = int(match.group(1))
                        dev_path = f"/dev/video{idx}"
                        if idx not in seen_indices and QRHttpServer.is_video_capture_device(dev_path):
                            seen_indices.add(idx)
                            available.append({
                                "index": idx,
                                "name": f"{current_name} ({dev_path})"
                            })
        except Exception as e:
            log.debug("v4l2-ctl device enumeration failed: %s", e)

        if not available:
            for i in range(4):
                dev_path = f"/dev/video{i}"
                if os.path.exists(dev_path) and QRHttpServer.is_video_capture_device(dev_path):
                    available.append({
                        "index": i,
                        "name": f"Camera {i} ({dev_path})"
                    })

        available.sort(key=lambda c: c["index"])
        return available

    # -------------------------------------------------------------------------
    # Lifecycle & Initialization
    # -------------------------------------------------------------------------

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 5050,
        camera_index: Optional[int] = None,
        width: int = 640,
        height: int = 480,
        retries: int = 3,
        retry_interval: float = 0.2,
        base_dir: Optional[str] = None,
        enable_upload: bool = True,
    ):
        self.host = host
        self.port = port
        self.width = width
        self.height = height
        self.default_retries = retries
        self.default_retry_interval = retry_interval
        self.enable_upload = enable_upload

        # Path Resolution for storage
        self.base_dir = base_dir or os.path.dirname(os.path.abspath(__file__))
        self.setups_dir = os.path.join(self.base_dir, "camera_setups")
        self.captures_dir = os.path.join(self.base_dir, "captures")
        os.makedirs(self.setups_dir, exist_ok=True)
        os.makedirs(self.captures_dir, exist_ok=True)

        # Synchronization & Workers
        self.lock = threading.Lock()        # State lock
        self.cap_lock = threading.Lock()    # Hardware lock (prevents I/O blocking API requests)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="qr_saver")
        self.running = True
        self.start_time = time.time()

        # Frame State
        self.latest_frame: Optional[np.ndarray] = None
        self.latest_raw_frame: Optional[np.ndarray] = None
        self.last_frame_time = 0.0

        # Last QR Capture Cache
        self.last_qr_frame: Optional[np.ndarray] = None
        self.last_qr_data: Optional[str] = None
        self.last_qr_timestamp: Optional[float] = None
        self.last_qr_filename: Optional[str] = None
        self.last_qr_camera_index: Optional[int] = None

        # Camera Initialization
        if camera_index is None:
            avail = self.list_available_cameras()
            camera_index = avail[0]["index"] if avail else 0

        self.index = camera_index
        self.settings = self.load_saved_setup(self.index)
        self.disable_autofocus(self.index)

        with self.cap_lock:
            self.cap = cv2.VideoCapture(self.index, cv2.CAP_V4L2)
            if self.width:
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            if self.height:
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        # Background Frame Grabber Thread
        self.thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.thread.start()

        # Wait briefly for camera to produce first frame
        t_wait = time.time()
        while time.time() - t_wait < 1.0:
            with self.lock:
                if self.latest_frame is not None:
                    break
            time.sleep(0.05)

        # Embedded Flask App (Self-contained, no external template folder)
        self.app = Flask(__name__)
        self._register_routes()

    # -------------------------------------------------------------------------
    # Hardware & Configuration Management
    # -------------------------------------------------------------------------

    def disable_autofocus(self, index: Optional[int] = None):
        """Turn off autofocus on camera via v4l2-ctl."""
        idx = self.index if index is None else index
        dev = f"/dev/video{idx}"
        try:
            subprocess.run(
                ["v4l2-ctl", "-d", dev, "-c", "focus_automatic_continuous=0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1
            )
            subprocess.run(
                ["v4l2-ctl", "-d", dev, "-c", "focus_auto=0"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1
            )
        except Exception as e:
            log.debug("Autofocus disable skipped for %s: %s", dev, e)

    def get_config_path(self, index: Optional[int] = None) -> str:
        """Return the setup JSON filename for given camera index."""
        idx = self.index if index is None else index
        return os.path.join(self.base_dir, f"camera_setup_cam{idx}.json")

    def _save_setup_unlocked(self, index: Optional[int] = None):
        """Save settings to disk. Assumes self.lock is already held."""
        path = self.get_config_path(index)
        try:
            with open(path, "w") as f:
                json.dump(self.settings, f, indent=4)
        except Exception as e:
            log.warning("Failed to save camera setup: %s", e)

    def save_current_setup(self, index: Optional[int] = None):
        """Thread-safe save of current camera setup."""
        with self.lock:
            self._save_setup_unlocked(index)

    def switch_camera(self, new_index: int) -> int:
        """Switch the active OpenCV camera index live."""
        with self.lock:
            if self.index == new_index and self.cap.isOpened():
                return self.index
            self._save_setup_unlocked(self.index)
            self.index = new_index

        with self.cap_lock:
            if self.cap.isOpened():
                self.cap.release()
            time.sleep(0.15)
            with self.lock:
                self.latest_frame = None
                self.latest_raw_frame = None
                self.cap = cv2.VideoCapture(new_index, cv2.CAP_V4L2)
                if self.width:
                    self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
                if self.height:
                    self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)

        new_cam_settings = self.load_saved_setup(new_index)
        self.update_settings(new_cam_settings)
        self.disable_autofocus(new_index)

        # Wait briefly for first frame to arrive from new device
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
        """Sanitize and update software image adjustment settings."""
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
        """Perform smart auto-adjustment tailored for QR code scanning."""
        dev = f"/dev/video{self.index}"
        try:
            subprocess.run(["v4l2-ctl", "-d", dev, "-c", "brightness=128"], stderr=subprocess.DEVNULL, timeout=1)
            subprocess.run(["v4l2-ctl", "-d", dev, "-c", "contrast=32"], stderr=subprocess.DEVNULL, timeout=1)
            subprocess.run(["v4l2-ctl", "-d", dev, "-c", "exposure_auto=3"], stderr=subprocess.DEVNULL, timeout=1)
            subprocess.run(["v4l2-ctl", "-d", dev, "-c", "backlight_compensation=0"], stderr=subprocess.DEVNULL, timeout=1)
            self.disable_autofocus(self.index)
        except Exception:
            pass

        time.sleep(0.35)
        raw = self.read_raw_frame()
        if raw is None:
            return self.get_settings()

        gray = cv2.cvtColor(raw, cv2.COLOR_BGR2GRAY)
        mean_lum = float(np.mean(gray))
        std_lum = float(np.std(gray))

        target_mean = 125.0
        diff = target_mean - mean_lum

        if mean_lum > 145.0:
            calc_b = int(np.clip(diff * 0.7, -45, -5))
        elif mean_lum < 105.0:
            calc_b = int(np.clip(diff * 0.7, 5, 45))
        else:
            calc_b = 0

        if std_lum < 40.0:
            calc_c = float(np.clip(1.0 + ((40.0 - std_lum) / 40.0) * 0.5, 1.1, 1.5))
        else:
            calc_c = 1.0

        new_settings = {
            "brightness": calc_b,
            "contrast": round(calc_c, 2),
            "exposure": 0,
            "threshold": 0
        }
        return self.update_settings(new_settings)

    def save_profile(self, name: Optional[str] = None, new_settings: Optional[dict] = None) -> dict:
        """Save tuning configuration to a named JSON profile in camera_setups/."""
        clean_name = self.sanitize_filename(name) if name else f"cam{self.index}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
        filename = f"{clean_name}.json" if not clean_name.endswith(".json") else clean_name
        profile_path = os.path.join(self.setups_dir, filename)

        with self.lock:
            if new_settings:
                if "brightness" in new_settings:
                    self.settings["brightness"] = max(-100, min(100, int(new_settings["brightness"])))
                if "contrast" in new_settings:
                    self.settings["contrast"] = max(0.1, min(3.0, float(new_settings["contrast"])))
                if "exposure" in new_settings:
                    self.settings["exposure"] = max(-5, min(5, int(new_settings["exposure"])))
                if "threshold" in new_settings:
                    self.settings["threshold"] = max(0, min(255, int(new_settings["threshold"])))
                self._save_setup_unlocked(self.index)

            payload = {
                "name": clean_name.replace(".json", ""),
                "profile_name": clean_name.replace(".json", ""),
                "filename": filename,
                "camera_index": self.index,
                "saved_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "brightness": self.settings["brightness"],
                "contrast": self.settings["contrast"],
                "exposure": self.settings["exposure"],
                "threshold": self.settings["threshold"],
                "settings": dict(self.settings)
            }

        with open(profile_path, "w") as f:
            json.dump(payload, f, indent=4)
        return payload

    def list_profiles(self) -> List[dict]:
        """List all saved setup profile JSON files."""
        profiles = []
        if not os.path.exists(self.setups_dir):
            return profiles

        for fname in sorted(os.listdir(self.setups_dir)):
            if fname.endswith(".json"):
                fpath = os.path.join(self.setups_dir, fname)
                try:
                    with open(fpath, "r") as f:
                        data = json.load(f)
                    prof_name = data.get("name") or data.get("profile_name") or fname.replace(".json", "")
                    s = data.get("settings", data)
                    profiles.append({
                        "name": prof_name,
                        "profile_name": prof_name,
                        "filename": fname,
                        "saved_at": data.get("saved_at", ""),
                        "brightness": s.get("brightness", 0),
                        "contrast": s.get("contrast", 1.0),
                        "exposure": s.get("exposure", 0),
                        "threshold": s.get("threshold", 0),
                        "settings": s
                    })
                except Exception as e:
                    log.warning("Could not read profile %s: %s", fname, e)
        return profiles

    def load_profile(self, target: str) -> dict:
        """Load and apply a profile JSON file by name or filename."""
        safe_target = self.sanitize_filename(target)
        if not safe_target.endswith(".json"):
            safe_target += ".json"

        target_path = os.path.abspath(os.path.join(self.setups_dir, safe_target))
        if not target_path.startswith(os.path.abspath(self.setups_dir)):
            raise ValueError(f"Invalid profile path: {target}")

        if not os.path.isfile(target_path):
            raise FileNotFoundError(f"Profile {safe_target} does not exist in camera_setups/")

        with open(target_path, "r") as f:
            data = json.load(f)

        s = data.get("settings", data)
        return self.update_settings(s)

    def load_saved_setup(self, index: Optional[int] = None) -> dict:
        """Load settings from camera-specific file or fallback defaults."""
        path = self.get_config_path(index)
        defaults = {"brightness": 0, "contrast": 1.0, "exposure": 0, "threshold": 0}

        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                return {
                    "brightness": int(data.get("brightness", 0)),
                    "contrast": float(data.get("contrast", 1.0)),
                    "exposure": int(data.get("exposure", 0)),
                    "threshold": int(data.get("threshold", 0))
                }
            except Exception as e:
                log.warning("Error reading %s: %s. Using defaults.", path, e)
                return defaults

        legacy = os.path.join(self.base_dir, "camera_setup.json")
        if os.path.exists(legacy):
            try:
                with open(legacy, "r") as f:
                    data = json.load(f)
                return {
                    "brightness": int(data.get("brightness", 0)),
                    "contrast": float(data.get("contrast", 1.0)),
                    "exposure": int(data.get("exposure", 0)),
                    "threshold": int(data.get("threshold", 0))
                }
            except Exception:
                pass

        return defaults

    def reset_to_factory(self) -> dict:
        """Reset hardware controls dynamically via v4l2-ctl and software adjustments."""
        dev = f"/dev/video{self.index}"
        try:
            out = subprocess.check_output(
                ["v4l2-ctl", "-d", dev, "--list-ctrls"],
                stderr=subprocess.DEVNULL, text=True, timeout=2
            )
            for line in out.splitlines():
                line = line.strip()
                match = re.match(r'([a-zA-Z0-9_]+)\s+0x[0-9a-fA-F]+.*default=(-?\d+)', line)
                if match:
                    ctrl_name = match.group(1)
                    ctrl_def = match.group(2)
                    try:
                        subprocess.run(
                            ["v4l2-ctl", "-d", dev, "-c", f"{ctrl_name}={ctrl_def}"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1
                        )
                    except Exception:
                        pass
        except Exception as e:
            log.warning("Dynamic factory reset through v4l2-ctl failed: %s", e)

        self.disable_autofocus(self.index)
        defaults = {"brightness": 0, "contrast": 1.0, "exposure": 0, "threshold": 0}
        return self.update_settings(defaults)

    # -------------------------------------------------------------------------
    # Frame Capture & Image Retrieval
    # -------------------------------------------------------------------------

    def _async_save_capture(self, save_path: str, frame: np.ndarray, data: str):
        """Asynchronously write captured snapshot image to disk."""
        try:
            cv2.imwrite(save_path, frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
            log.info("Saved QR capture to: %s for data: %s", save_path, data)
        except Exception as e:
            log.error("Failed to asynchronously save QR snapshot: %s", e)

    def set_last_qr_capture(self, frame: np.ndarray, data: str):
        """Cache latest successful QR capture and trigger async disk save."""
        now = time.time()
        safe_data = self.sanitize_filename(data)
        filename = f"qr_{safe_data}.jpg"
        save_path = os.path.join(self.captures_dir, filename)

        with self.lock:
            self.last_qr_frame = frame.copy()
            self.last_qr_data = data
            self.last_qr_timestamp = now
            self.last_qr_filename = filename
            self.last_qr_camera_index = self.index

        # Non-blocking background disk write
        self.executor.submit(self._async_save_capture, save_path, frame.copy(), data)

    def get_last_qr_capture(self) -> Tuple[Optional[np.ndarray], Optional[str], Optional[float], Optional[str]]:
        """Retrieve cached snapshot frame, decoded data, timestamp, and filename."""
        with self.lock:
            frame = self.last_qr_frame.copy() if self.last_qr_frame is not None else None
            return frame, self.last_qr_data, self.last_qr_timestamp, self.last_qr_filename

    def _capture_loop(self):
        """Background thread grabbing frames continuously without blocking API requests."""
        while self.running:
            with self.cap_lock:
                if not self.cap.isOpened():
                    time.sleep(0.1)
                    continue
                ret, frame = self.cap.read()

            if not ret or frame is None:
                time.sleep(0.01)
                continue

            with self.lock:
                self.latest_raw_frame = frame
                brightness = self.settings["brightness"]
                contrast = self.settings["contrast"]
                exposure = self.settings["exposure"]
                thresh_val = self.settings["threshold"]

            total_b = brightness + (exposure * 15)
            if contrast != 1.0 or total_b != 0:
                adjusted = cv2.convertScaleAbs(frame, alpha=contrast, beta=total_b)
            else:
                adjusted = frame

            if thresh_val > 0:
                gray = cv2.cvtColor(adjusted, cv2.COLOR_BGR2GRAY)
                _, thresh = cv2.threshold(gray, thresh_val, 255, cv2.THRESH_BINARY)
                final_frame = cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)
            else:
                final_frame = adjusted

            with self.lock:
                self.latest_frame = final_frame
                self.last_frame_time = time.time()

            time.sleep(0.005)

    def read_frame(self) -> Optional[np.ndarray]:
        """Return the latest processed frame."""
        with self.lock:
            return self.latest_frame.copy() if self.latest_frame is not None else None

    def read_raw_frame(self) -> Optional[np.ndarray]:
        """Return the untouched raw frame."""
        with self.lock:
            return self.latest_raw_frame.copy() if self.latest_raw_frame is not None else None

    def read_frame_with_time(self) -> Tuple[Optional[np.ndarray], float]:
        """Return latest frame copy and its capture timestamp."""
        with self.lock:
            if self.latest_frame is None:
                return None, 0.0
            return self.latest_frame.copy(), self.last_frame_time

    def is_ready(self) -> bool:
        """Check if camera hardware and frame grabber are alive."""
        with self.lock:
            return (
                self.cap.isOpened()
                and (self.latest_frame is not None)
                and (time.time() - self.last_frame_time < 3.0)
            )

    def get_health(self) -> dict:
        """Return health diagnostics dictionary."""
        with self.lock:
            has_frame = self.latest_frame is not None
            sec_ago = round(time.time() - self.last_frame_time, 2) if self.last_frame_time > 0 else None
            return {
                "status": "ok" if (self.cap.isOpened() and has_frame and (sec_ago is not None and sec_ago < 3.0)) else "degraded",
                "camera_connected": self.cap.isOpened(),
                "camera_index": self.index,
                "has_latest_frame": has_frame,
                "seconds_since_last_frame": sec_ago,
                "uptime_seconds": round(time.time() - self.start_time, 1),
                "timestamp": time.time()
            }

    # -------------------------------------------------------------------------
    # Multi-Stage QR Decoding
    # -------------------------------------------------------------------------

    def read_qr(
        self,
        camera_index: Optional[int] = None,
        retries: Optional[int] = None,
        retry_interval: Optional[float] = None
    ) -> Optional[str]:
        """
        Scan and decode QR code with multi-stage fallback.
        Can be called directly from Python or through REST API.
        """
        if camera_index is not None and self.index != camera_index:
            self.switch_camera(camera_index)

        num_retries = retries if retries is not None else self.default_retries
        interval = retry_interval if retry_interval is not None else self.default_retry_interval

        last_frame_ok = False
        for attempt in range(1, num_retries + 1):
            frame = self.read_frame()
            raw_frame = self.read_raw_frame()
            if frame is None and raw_frame is None:
                if attempt < num_retries:
                    time.sleep(0.05)
                continue
            last_frame_ok = True

            target_frame = frame if frame is not None else raw_frame
            results = decode_qr(target_frame)

            # Stage 2: Grayscale fallback
            if not results:
                gray = cv2.cvtColor(target_frame, cv2.COLOR_BGR2GRAY)
                results = decode_qr(gray)

            # Stage 3 & 4: Raw frame & Gaussian Adaptive Threshold fallback
            if not results and raw_frame is not None and target_frame is not raw_frame:
                results = decode_qr(raw_frame)
                if not results:
                    raw_gray = cv2.cvtColor(raw_frame, cv2.COLOR_BGR2GRAY)
                    results = decode_qr(raw_gray)
                    if not results:
                        try:
                            adaptive = cv2.adaptiveThreshold(
                                raw_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 51, 10
                            )
                            results = decode_qr(adaptive)
                        except Exception:
                            pass

            if results:
                qr_text = results[0].data.decode("utf-8", errors="replace")
                annotated = target_frame.copy()
                h_img, w_img = annotated.shape[:2]

                try:
                    points = getattr(results[0], "polygon", None)
                    rect = getattr(results[0], "rect", None)
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

                    if y_max + 48 > h_img:
                        text_y1 = max(22, y_min - 24)
                        text_y2 = text_y1 + 18
                    else:
                        text_y1 = y_max + 22
                        text_y2 = y_max + 40

                    ts_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    line1 = f"{qr_text}"
                    line2 = f"{ts_str}"

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
                    log.debug("Failed to overlay text on capture frame: %s", e)

                self.set_last_qr_capture(annotated, qr_text)
                return qr_text

            if attempt < num_retries:
                time.sleep(interval)

        if not last_frame_ok:
            raise RuntimeError("Camera is disconnected or not producing valid frames")
        return None

    # -------------------------------------------------------------------------
    # Web UI & REST API Handlers (Class Methods)
    # -------------------------------------------------------------------------

    def web_index(self):
        """Main web dashboard page."""
        return render_template_string(self.HTML_INDEX)

    def web_adjust(self):
        """Camera adjustment and tuning web page."""
        return render_template_string(self.HTML_ADJUST)

    def web_upload(self):
        """OTA script updater web page."""
        if not self.enable_upload:
            return "OTA Upload has been disabled on this server.", 403
        return render_template_string(self.HTML_UPLOAD)

    def api_get_capture_file(self, filename: str):
        """Serve captured snapshot file from captures directory."""
        return send_from_directory(self.captures_dir, filename)

    def api_get_cameras(self):
        """List connected camera devices and active camera index."""
        avail = self.list_available_cameras()
        return jsonify({
            "status": "ok",
            "active_index": self.index,
            "available_cameras": avail
        }), 200

    def api_switch_camera(self):
        """Switch active camera index live."""
        body = request.get_json(silent=True) or {}
        new_idx = body.get("index")
        if new_idx is None:
            return jsonify({"status": "error", "message": "Missing 'index' parameter"}), 400
        try:
            active_idx = self.switch_camera(int(new_idx))
            return jsonify({
                "status": "ok",
                "message": f"Switched to camera index {active_idx}",
                "active_index": active_idx
            }), 200
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def api_list_profiles(self):
        """List saved camera setup profiles."""
        profiles = self.list_profiles()
        return jsonify({"status": "ok", "profiles": profiles}), 200

    def api_save_profile(self):
        """Save active camera parameters as a named JSON profile."""
        body = request.get_json(silent=True) or {}
        profile_name = body.get("name") or body.get("profile_name")
        saved_data = self.save_profile(name=profile_name, new_settings=body)
        return jsonify({
            "status": "ok",
            "message": f"Camera setup saved to profile file {saved_data['filename']}",
            "data": saved_data
        }), 200

    def api_load_profile(self):
        """Load and apply a named station profile."""
        body = request.get_json(silent=True) or {}
        target = body.get("name") or body.get("filename") or body.get("profile_name")
        if not target:
            return jsonify({"status": "error", "message": "Missing 'name' or 'filename' parameter"}), 400
        try:
            active_settings = self.load_profile(target)
            return jsonify({
                "status": "ok",
                "message": f"Profile '{target}' loaded and applied successfully",
                "settings": active_settings
            }), 200
        except FileNotFoundError as e:
            return jsonify({"status": "error", "message": str(e)}), 404
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500

    def api_cap_screen(self):
        """
        Snapshot preview endpoint:
        - Default: Render HTML preview page
        - ?raw=1 or ?image=1: Raw JPEG binary image
        - ?json=1 or request.is_json: JSON metadata
        """
        frame, data, timestamp, filename = self.get_last_qr_capture()
        raw_requested = request.args.get("raw") == "1" or request.args.get("image") == "1"
        json_requested = request.args.get("json") == "1" or request.is_json

        if raw_requested:
            if frame is None:
                return "No QR capture available yet", 404
            ret, buffer = cv2.imencode('.jpg', frame)
            return Response(buffer.tobytes(), mimetype='image/jpeg')

        if json_requested:
            if frame is None:
                return jsonify({
                    "status": "no_capture",
                    "message": "No QR code frame has been captured yet"
                }), 200
            return jsonify({
                "status": "ok",
                "qr_data": data,
                "camera_index": self.last_qr_camera_index,
                "timestamp": timestamp,
                "filename": filename,
                "image_url": f"/captures/{filename}" if filename else "/cap_screen?raw=1"
            }), 200

        formatted_time = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S') if timestamp else ""
        ago_seconds = round(time.time() - timestamp, 1) if timestamp else 0
        return render_template_string(
            self.HTML_CAP_SCREEN,
            has_frame=(frame is not None),
            data=data,
            timestamp=timestamp,
            filename=filename,
            formatted_time=formatted_time,
            ago_seconds=ago_seconds
        )

    def api_settings(self):
        """Read or update active camera adjustment parameters."""
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            updated = self.update_settings(body)
            return jsonify({"status": "ok", "settings": updated}), 200
        else:
            return jsonify(self.get_settings()), 200

    def api_auto_adjust(self):
        """Perform histogram-based automatic luminance adjustments."""
        new_settings = self.auto_adjust_settings()
        return jsonify({
            "status": "ok",
            "message": "V4L2 Hardware Auto-Adjust baseline calculated and applied",
            "settings": new_settings
        }), 200

    def api_reset_factory(self):
        """Restore camera hardware V4L2 registers and software settings to default."""
        defaults = self.reset_to_factory()
        return jsonify({
            "status": "ok",
            "message": "Camera hardware & software restored to dynamic camera defaults",
            "settings": defaults
        }), 200

    def generate_frames(self):
        """Generator yielding MJPEG frame stream (~30 FPS, JPEG q=80)."""
        last_sent_time = 0.0
        while self.running:
            frame, frame_time = self.read_frame_with_time()
            if frame is None:
                time.sleep(0.05)
                continue
            if frame_time == last_sent_time:
                time.sleep(0.01)
                continue
            last_sent_time = frame_time
            ret, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ret:
                continue
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')
            time.sleep(0.03)

    def api_video_feed(self):
        """Live MJPEG video stream."""
        return Response(self.generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

    def api_ping(self):
        """Lightweight heartbeat check."""
        return jsonify({"status": "pong", "timestamp": time.time()})

    def api_health(self):
        """Detailed diagnostics (uptime, camera connection, frame rate)."""
        health_info = self.get_health()
        return jsonify(health_info), 200

    def api_read(self):
        """
        Scan QR code on current (or specified) camera.
        Accepts optional params: camera_index, retries, retry_interval
        """
        body = request.get_json(silent=True) or {}
        retries = body.get("retries", self.default_retries)
        retry_interval = body.get("retry_interval", self.default_retry_interval)

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
                if self.index != target_idx:
                    self.switch_camera(target_idx)
            except Exception as e:
                return jsonify({
                    "status": "error",
                    "error_code": "CAMERA_SWITCH_ERROR",
                    "message": f"Failed to switch to camera {target_cam}: {str(e)}",
                    "timestamp": time.time()
                }), 400

        if not self.is_ready():
            return jsonify({
                "status": "error",
                "error_code": "CAMERA_NOT_READY",
                "message": "Camera is disconnected or frame grabber is failing",
                "camera_index": self.index,
                "timestamp": time.time()
            }), 503

        try:
            text = self.read_qr(retries=retries, retry_interval=retry_interval)
        except Exception as e:
            log.exception("Error during QR code scan")
            return jsonify({
                "status": "error",
                "error_code": "SCAN_ERROR",
                "message": str(e),
                "camera_index": self.index,
                "timestamp": time.time()
            }), 500

        if text is not None:
            return jsonify({
                "status": "ok",
                "data": text,
                "camera_index": self.index,
                "timestamp": time.time()
            }), 200

        return jsonify({
            "status": "no_qr",
            "message": "No QR code detected after retries",
            "camera_index": self.index,
            "timestamp": time.time()
        }), 200

    def api_upload_code(self):
        """Upload updated Python script, verify syntax, backup, and restart service."""
        if not self.enable_upload:
            return jsonify({"status": "error", "message": "OTA upload is disabled on this server"}), 403

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
            log.info("Updated %s with new uploaded code", target_path)
        except Exception as e:
            return jsonify({"status": "error", "message": f"Failed to save file: {str(e)}"}), 500

        def restart_service():
            time.sleep(1.0)
            try:
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

    def _register_routes(self):
        """Map URL endpoints to class methods using add_url_rule."""
        # Web UI Routes
        self.app.add_url_rule("/", "index", self.web_index, methods=["GET"])
        self.app.add_url_rule("/adjust", "adjust", self.web_adjust, methods=["GET"])
        self.app.add_url_rule("/upload", "upload_page", self.web_upload, methods=["GET"])

        # REST API & Stream Routes
        self.app.add_url_rule("/captures/<path:filename>", "get_capture_file", self.api_get_capture_file, methods=["GET"])
        self.app.add_url_rule("/cameras", "get_cameras", self.api_get_cameras, methods=["GET"])
        self.app.add_url_rule("/cameras/switch", "switch_camera", self.api_switch_camera, methods=["POST"])
        self.app.add_url_rule("/profiles", "list_profiles", self.api_list_profiles, methods=["GET"])
        self.app.add_url_rule("/profiles/save", "save_profile", self.api_save_profile, methods=["POST"])
        self.app.add_url_rule("/profiles/load", "load_profile", self.api_load_profile, methods=["POST"])
        self.app.add_url_rule("/cap_screen", "cap_screen", self.api_cap_screen, methods=["GET"])
        self.app.add_url_rule("/settings", "settings", self.api_settings, methods=["GET", "POST"])
        self.app.add_url_rule("/settings/auto_adjust", "auto_adjust", self.api_auto_adjust, methods=["POST"])
        self.app.add_url_rule("/settings/reset", "reset_factory", self.api_reset_factory, methods=["POST"])
        self.app.add_url_rule("/video_feed", "video_feed", self.api_video_feed, methods=["GET"])
        self.app.add_url_rule("/ping", "ping", self.api_ping, methods=["GET"])
        self.app.add_url_rule("/health", "health", self.api_health, methods=["GET"])
        self.app.add_url_rule("/read", "read", self.api_read, methods=["GET", "POST"])
        self.app.add_url_rule("/upload", "upload", self.api_upload_code, methods=["POST"])

    # -------------------------------------------------------------------------
    # Server Execution & Context Management
    # -------------------------------------------------------------------------

    def run(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        use_waitress: bool = False,
        threaded: bool = True
    ):
        """Start the HTTP server (Flask dev server or production Waitress WSGI)."""
        h = host or self.host
        p = port or self.port
        log.info("QR HTTP Server listening on %s:%s", h, p)
        try:
            if use_waitress:
                try:
                    from waitress import serve
                    log.info("Starting production Waitress WSGI server on %s:%s (threads=16)...", h, p)
                    serve(self.app, host=h, port=p, threads=16)
                except ImportError:
                    log.warning("Waitress package not found. Falling back to Flask dev server.")
                    self.app.run(host=h, port=p, threaded=threaded)
            else:
                self.app.run(host=h, port=p, threaded=threaded)
        finally:
            self.release()

    def stop(self):
        """Stop server background tasks and release camera device."""
        self.release()

    def release(self):
        """Thread-safe release of camera hardware and worker threads."""
        self.running = False
        with self.cap_lock:
            if hasattr(self, "cap") and self.cap.isOpened():
                self.cap.release()
                log.info("Camera device %s released cleanly", self.index)
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=False)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()

    # -------------------------------------------------------------------------
    # CLI Main Entry Point
    # -------------------------------------------------------------------------

    @classmethod
    def main(cls):
        """CLI main function: parse arguments and launch server."""
        parser = argparse.ArgumentParser(description="Standalone QR-code HTTP server (Self-contained Class)")
        parser.add_argument("--host", default="0.0.0.0", help="Bind address (default: 0.0.0.0)")
        parser.add_argument("--port", type=int, default=5050, help="HTTP port (default: 5050)")
        parser.add_argument("--camera-index", type=int, default=None, help="OpenCV camera index (default: auto-detect first valid video stream)")
        parser.add_argument("--width", type=int, default=640, help="Camera width (default: 640)")
        parser.add_argument("--height", type=int, default=480, help="Camera height (default: 480)")
        parser.add_argument("--retries", type=int, default=3, help="Default frame attempts per /read call")
        parser.add_argument("--retry-interval", type=float, default=0.2, help="Default seconds between retry attempts")
        parser.add_argument("--disable-upload", action="store_true", help="Disable OTA code upload endpoint (/upload)")
        parser.add_argument("--use-waitress", action="store_true", help="Use Waitress production WSGI server instead of Flask dev server")

        # Safely parse arguments (ignores unknown flags from roslaunch or external runners)
        args, _ = parser.parse_known_args()

        server = cls(
            host=args.host,
            port=args.port,
            camera_index=args.camera_index,
            width=args.width,
            height=args.height,
            retries=args.retries,
            retry_interval=args.retry_interval,
            enable_upload=not args.disable_upload
        )

        def handle_signal(sig, frame):
            log.info("Received signal %s. Shutting down gracefully...", sig)
            server.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

        server.run(use_waitress=args.use_waitress)


if __name__ == "__main__":
    QRHttpServer.main()