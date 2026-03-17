#!/usr/bin/env python3
"""
SlyLED Parent — Windows system-tray launcher.

Starts the Flask HTTP server in a background thread, opens a browser tab,
then runs a system-tray icon on the main thread.  Double-clicking the tray
icon re-opens the browser; right-clicking shows Open / Quit.

Usage:
    python main.py [--port 8080] [--no-browser]
"""

import argparse
import base64
import io
import logging
import os
import sys
import threading
import time
import webbrowser

# When frozen by PyInstaller, local modules (parent_server, firmware_manager)
# are bundled as data files into sys._MEIPASS.  Add that to sys.path so
# they can be imported normally.
if getattr(sys, "frozen", False):
    sys.path.insert(0, sys._MEIPASS)

from parent_server import app, VERSION

# ── System-tray (optional — graceful fallback if pystray/Pillow unavailable) ──

try:
    import pystray
    from PIL import Image, ImageDraw
    _TRAY = True
except ImportError:
    _TRAY = False


def _parse():
    p = argparse.ArgumentParser(description="SlyLED Parent")
    p.add_argument("--port",       type=int, default=8080)
    p.add_argument("--no-browser", action="store_true")
    return p.parse_args()


# 64×64 SlyLED logo (PNG, base64-encoded) — used as tray icon
_ICON_B64 = (
    "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"
    "gm8AAAAASUVORK5CYII="
)

def _make_icon():
    """Load the SlyLED logo as a PIL Image for the system tray."""
    data = base64.b64decode(_ICON_B64)
    return Image.open(io.BytesIO(data)).convert("RGBA")


def _already_running(port):
    """Check if another SlyLED instance is on this port."""
    try:
        import urllib.request
        resp = urllib.request.urlopen(f"http://localhost:{port}/status", timeout=2)
        data = resp.read().decode()
        return "parent" in data or "SlyLED" in data
    except Exception:
        return False

def main():
    args = _parse()
    url  = f"http://localhost:{args.port}"

    if _already_running(args.port):
        print(f"SlyLED Orchestrator already running on port {args.port} — opening browser.")
        webbrowser.open(url)
        sys.exit(0)

    # Suppress Werkzeug request logging (keeps the --windowed exe quiet)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # ── Flask thread ──────────────────────────────────────────────────────────
    def _run_flask():
        app.run(host="0.0.0.0", port=args.port, threaded=True, use_reloader=False)

    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()

    # ── Open browser (after brief delay so Flask is ready) ───────────────────
    if not args.no_browser:
        def _open():
            time.sleep(1.2)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    # ── System tray ───────────────────────────────────────────────────────────
    if _TRAY:
        def _on_open(icon, item):
            webbrowser.open(url)

        def _on_quit(icon, item):
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("Open SlyLED", _on_open, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(f"SlyLED Orchestrator  v{VERSION}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _on_quit),
        )
        icon = pystray.Icon("SlyLED", _make_icon(), "SlyLED Orchestrator", menu)
        icon.run()   # blocks main thread; daemon Flask thread exits when tray quits

    else:
        # No tray available — just block until Ctrl+C
        print(f"SlyLED Orchestrator v{VERSION}  →  {url}")
        print("No system tray. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
