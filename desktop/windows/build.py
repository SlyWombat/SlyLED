"""build.py — PyInstaller helper for SlyLED Parent.

Called by build.bat to avoid Windows cmd ^ line-continuation quoting issues
with paths that contain spaces.
"""
import pathlib
import sys

import PyInstaller.__main__

HERE   = pathlib.Path(__file__).resolve().parent
SHARED = (HERE / ".." / "shared").resolve()
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
    "--paths", str(SHARED),
]

# Bundle firmware registry if it exists
if FWDIR.exists():
    args.append("--add-data")
    args.append(f"{FWDIR};firmware")

args.append(str(SHARED / "main.py"))

PyInstaller.__main__.run(args)
