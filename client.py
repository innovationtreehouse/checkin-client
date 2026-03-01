#!/usr/bin/env python3
"""
CheckMeIn Kiosk Client

A thin client for Raspberry Pi that:
  1. Serves a local attendance page on localhost (auto-refreshes every 60s)
  2. Listens for USB barcode/QR scanner input
  3. Signs all requests to the backend with Ed25519
"""

import json
import os
import sys
import time
import threading
import logging
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
import requests

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("kiosk")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path="config.json"):
    if not os.path.exists(path):
        log.error(f"Config file not found: {path}")
        log.error("Copy config.example.json → config.json and edit it.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)

# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------
def load_signing_key(path):
    with open(path, "rb") as f:
        seed = f.read()
    return SigningKey(seed)

def sign_request(signing_key, method, path, body=""):
    """Create signature headers for a request.

    Signs: timestamp + method + path + body
    Returns dict of headers to merge into the request.
    """
    timestamp = str(int(time.time()))
    message = f"{timestamp}:{method}:{path}:{body}".encode()
    signature = signing_key.sign(message).signature.hex()
    return {
        "X-Kiosk-Timestamp": timestamp,
        "X-Kiosk-Signature": signature,
    }

# ---------------------------------------------------------------------------
# Backend communication
# ---------------------------------------------------------------------------
class BackendClient:
    def __init__(self, base_url, signing_key):
        self.base_url = base_url.rstrip("/")
        self.signing_key = signing_key
        self.session = requests.Session()

    def _headers(self, method, path, body=""):
        h = sign_request(self.signing_key, method, path, body)
        h["Content-Type"] = "application/json"
        return h

    def post_scan(self, participant_id):
        path = "/api/scan"
        body = json.dumps({"participantId": int(participant_id)})
        headers = self._headers("POST", path, body)
        try:
            r = self.session.post(
                self.base_url + path, headers=headers, data=body, timeout=10
            )
            return r.json(), r.status_code
        except Exception as e:
            log.error(f"Failed to post scan: {e}")
            return {"error": str(e)}, 0

# ---------------------------------------------------------------------------
# Attendance state (for flash messages)
# ---------------------------------------------------------------------------
class AttendanceState:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_scan = None     # dict with scan result for flash message

    def set_scan_result(self, result):
        with self.lock:
            self.last_scan = result

    def pop_scan_result(self):
        with self.lock:
            r = self.last_scan
            self.last_scan = None
            return r

# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def render_html(state, backend_url):
    scan_result = state.pop_scan_result()

    # Build scan flash banner
    scan_banner = ""
    if scan_result:
        sr = scan_result
        if sr.get("status", 0) >= 400 or "error" in sr.get("body", {}):
            err = sr.get("body", {}).get("error", "Unknown error")
            scan_banner = f"""
            <div class="banner banner-error">
                ✗ Scan failed: {err}
            </div>"""
        else:
            body = sr.get("body", {})
            stype = body.get("type", "")
            email = body.get("participant", {}).get("email", "?")
            label = "CHECKED IN" if stype == "checkin" else "CHECKED OUT"
            scan_banner = f"""
            <div class="banner banner-ok">
                ✓ {email} — {label}
            </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CheckMeIn — Attendance</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body, html {{ width: 100%; height: 100%; overflow: hidden; background: #0f172a; font-family: sans-serif; }}
  iframe {{ width: 100%; height: 100%; border: none; }}
  
  #flash-container {{
    position: absolute;
    top: 20px;
    left: 50%;
    transform: translateX(-50%);
    z-index: 9999;
    width: 80%;
    max-width: 600px;
    pointer-events: none;
  }}
  .banner {{
    padding: 1.5rem;
    border-radius: 12px;
    margin-bottom: 1rem;
    font-weight: bold;
    font-size: 1.5rem;
    text-align: center;
    box-shadow: 0 10px 25px rgba(0,0,0,0.5);
    animation: fadeout 5s forwards;
  }}
  .banner-ok {{
    background: rgba(16,185,129,0.95);
    border: 2px solid #34d399;
    color: #fff;
  }}
  .banner-error {{
    background: rgba(239,68,68,0.95);
    border: 2px solid #f87171;
    color: #fff;
  }}
  @keyframes fadeout {{
    0% {{ opacity: 1; }}
    80% {{ opacity: 1; }}
    100% {{ opacity: 0; display: none; }}
  }}
</style>
<script>
  let lastFlash = "";
  async function pollFlashes() {{
    try {{
      const res = await fetch("/poll");
      if (res.ok) {{
        const text = await res.text();
        if (text && text !== lastFlash) {{
          const container = document.getElementById("flash-container");
          container.innerHTML = text;
          // Trigger reflow to restart animation
          const banner = container.querySelector(".banner");
          if (banner) {{
            banner.style.animation = 'none';
            banner.offsetHeight; // trigger reflow
            banner.style.animation = null; 
          }}
          lastFlash = text;
          
          // Set a timeout to clear the HTML so old flashes don't block new identical ones
          setTimeout(() => {{ if (lastFlash === text) container.innerHTML = ''; }}, 6000);
        }}
      }}
    }} catch (e) {{}}
    setTimeout(pollFlashes, 1000);
  }}
  setTimeout(pollFlashes, 1000);
</script>
</head>
<body>
  <div id="flash-container">
    {scan_banner}
  </div>
  <iframe src="{backend_url}/attendance"></iframe>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Local HTTP server
# ---------------------------------------------------------------------------
class KioskHandler(BaseHTTPRequestHandler):
    state = None        # set from main
    backend_url = None  # set from main

    def do_GET(self):
        if self.path == "/poll":
            # Return flash message HTML if any
            scan_result = self.state.pop_scan_result()
            html = ""
            if scan_result:
                sr = scan_result
                if sr.get("status", 0) >= 400 or "error" in sr.get("body", {}):
                    err = sr.get("body", {}).get("error", "Unknown error")
                    html = f'<div class="banner banner-error">✗ Scan failed: {err}</div>'
                else:
                    body = sr.get("body", {})
                    stype = body.get("type", "")
                    email = body.get("participant", {}).get("email", "?")
                    label = "CHECKED IN" if stype == "checkin" else "CHECKED OUT"
                    html = f'<div class="banner banner-ok">✓ {email} — {label}</div>'
            
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(html.encode())
            return

        # Main page view
        html = render_html(self.state, self.backend_url)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # suppress request logs

# ---------------------------------------------------------------------------
# USB scanner listener
# ---------------------------------------------------------------------------
def usb_scanner_listener(backend, state, device_path):
    """Read scan events from a USB HID barcode scanner via evdev."""
    try:
        import evdev
    except ImportError:
        log.warning("evdev not installed — USB scanner disabled")
        log.warning("Install with: pip install evdev")
        return

    # Key code to character mapping (US keyboard layout, numbers only for IDs)
    KEY_MAP = {
        2: "1", 3: "2", 4: "3", 5: "4", 6: "5",
        7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    }
    ENTER_KEY = 28

    log.info(f"Attempting to open USB device: {device_path}")
    try:
        dev = evdev.InputDevice(device_path)
        dev.grab()  # exclusive access so keystrokes don't leak
        log.info(f"Listening on: {dev.name}")
    except Exception as e:
        log.error(f"Cannot open USB device {device_path}: {e}")
        log.error("Check the device path in config.json and permissions.")
        return

    buffer = ""
    for event in dev.read_loop():
        if event.type != evdev.ecodes.EV_KEY:
            continue
        key_event = evdev.categorize(event)
        if key_event.keystate != 1:  # key-down only
            continue

        if key_event.scancode == ENTER_KEY:
            if buffer.strip():
                participant_id = buffer.strip()
                log.info(f"Scanned ID: {participant_id}")
                handle_scan(backend, state, participant_id)
            buffer = ""
        elif key_event.scancode in KEY_MAP:
            buffer += KEY_MAP[key_event.scancode]


def stdin_scanner_listener(backend, state):
    """Fallback: read scanned IDs from stdin (for testing without a USB device)."""
    log.info("USB device not configured — reading scans from stdin")
    log.info("Type a participant ID and press Enter to simulate a scan.")
    while True:
        try:
            line = input()
            participant_id = line.strip()
            if participant_id:
                log.info(f"Stdin scan: {participant_id}")
                handle_scan(backend, state, participant_id)
        except EOFError:
            break


def handle_scan(backend, state, participant_id):
    """Process a scanned participant ID."""
    body, status = backend.post_scan(participant_id)
    state.set_scan_result({"body": body, "status": status})
    if status < 400 and "error" not in body:
        ptype = body.get("type", "?")
        email = body.get("participant", {}).get("email", "?")
        log.info(f"Scan result: {ptype.upper()} — {email}")
    else:
        log.warning(f"Scan error: {body.get('error', body)}")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    config = load_config()
    backend_url = config["backend_url"]
    key_path = config.get("private_key_path", "./client.key")
    usb_device = config.get("usb_device", "")
    port = config.get("listen_port", 8080)

    log.info(f"Backend: {backend_url}")
    log.info(f"Key:     {key_path}")
    log.info(f"USB:     {usb_device or '(stdin fallback)'}")
    log.info(f"Port:    {port}")

    # Load signing key
    if not os.path.exists(key_path):
        log.error(f"Private key not found: {key_path}")
        log.error("Run: python generate_keys.py")
        sys.exit(1)
    signing_key = load_signing_key(key_path)
    log.info("Signing key loaded")

    backend = BackendClient(backend_url, signing_key)
    state = AttendanceState()

    # Start USB scanner (or stdin fallback)
    if usb_device:
        scanner = threading.Thread(
            target=usb_scanner_listener,
            args=(backend, state, usb_device),
            daemon=True,
        )
    else:
        scanner = threading.Thread(
            target=stdin_scanner_listener, args=(backend, state), daemon=True
        )
    scanner.start()

    # Start HTTP server
    KioskHandler.state = state
    KioskHandler.backend_url = backend_url
    server = HTTPServer(("0.0.0.0", port), KioskHandler)
    log.info(f"Kiosk server running on http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
        server.shutdown()

if __name__ == "__main__":
    main()
