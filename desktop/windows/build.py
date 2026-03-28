"""build.py — PyInstaller helper for SlyLED Parent.

Called by build.bat to avoid Windows cmd ^ line-continuation quoting issues
with paths that contain spaces.
"""
import pathlib
import re
import sys

import PyInstaller.__main__

HERE   = pathlib.Path(__file__).resolve().parent
SHARED = (HERE / ".." / "shared").resolve()

# ── Auto-sync installer.iss AppVersion from parent_server.py VERSION ─────────
try:
    server_py = (SHARED / "parent_server.py").read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', server_py)
    if m:
        version = m.group(1)
        iss_path = HERE / "installer.iss"
        iss = iss_path.read_text(encoding="utf-8")
        iss_new = re.sub(r'#define AppVersion\s+"[^"]+"', f'#define AppVersion   "{version}"', iss)
        if iss_new != iss:
            iss_path.write_text(iss_new, encoding="utf-8")
            print(f"[build.py] Updated installer.iss AppVersion → {version}")
except Exception as e:
    print(f"[build.py] Warning: could not sync installer version: {e}")
SPA    = SHARED / "spa"
ICO    = (HERE / ".." / ".." / "images" / "slyled.ico").resolve()
FWDIR  = (HERE / ".." / ".." / "firmware").resolve()

args = [
    "--onefile",
    "--windowed",
    "--name", "SlyLED",
    "--distpath", str(HERE / "dist"),
    "--workpath", str(HERE / "build"),
    "--specpath", str(HERE),
    "--icon", str(ICO),
    "--add-data", f"{SPA};spa",
    # Bundle local modules alongside the exe so they're importable
    "--add-data", f"{SHARED / 'parent_server.py'};.",
    "--add-data", f"{SHARED / 'firmware_manager.py'};.",
    "--hidden-import=pystray",
    "--hidden-import=PIL._tkinter_finder",
    "--collect-submodules=flask",
    "--collect-submodules=werkzeug",
    "--collect-submodules=esptool",
    "--collect-data=esptool",
    "--paths", str(SHARED),
]

# Bundle firmware registry if it exists
if FWDIR.exists():
    args.append("--add-data")
    args.append(f"{FWDIR};firmware")

args.append(str(SHARED / "main.py"))

PyInstaller.__main__.run(args)
