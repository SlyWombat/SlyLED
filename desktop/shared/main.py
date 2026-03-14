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
import logging
import threading
import time
import webbrowser

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


def _make_icon():
    """32×32 programmatic icon — dark background, blue 'SL' letterform."""
    img = Image.new("RGB", (32, 32), (20, 20, 40))
    d   = ImageDraw.Draw(img)
    # S
    d.rectangle([3,  3,  13,  6],  fill=(80, 110, 240))
    d.rectangle([3,  3,  6,  16],  fill=(80, 110, 240))
    d.rectangle([3,  13, 13, 16],  fill=(80, 110, 240))
    d.rectangle([10, 16, 13, 29],  fill=(80, 110, 240))
    d.rectangle([3,  26, 13, 29],  fill=(80, 110, 240))
    # L
    d.rectangle([17, 3,  20, 29],  fill=(80, 110, 240))
    d.rectangle([17, 26, 29, 29],  fill=(80, 110, 240))
    return img


def main():
    args = _parse()
    url  = f"http://localhost:{args.port}"

    # Suppress Werkzeug request logging (keeps the --windowed exe quiet)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    # Import after arg-parse so Werkzeug sees the right environment
    from parent_server import app, VERSION

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
            pystray.MenuItem(f"SlyLED Parent  v{VERSION}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", _on_quit),
        )
        icon = pystray.Icon("SlyLED", _make_icon(), "SlyLED Parent", menu)
        icon.run()   # blocks main thread; daemon Flask thread exits when tray quits

    else:
        # No tray available — just block until Ctrl+C
        print(f"SlyLED Parent v{VERSION}  →  {url}")
        print("No system tray. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
