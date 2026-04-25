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

# ── Auto-increment app patch version and sync to installer.iss ────────────────
# Skipped when SLYLED_SKIP_VERSION_BUMP=1 (set by build_release.ps1 so the two
# scripts don't fight — build_release.ps1 owns the version when it's driving).
import os
if os.environ.get("SLYLED_SKIP_VERSION_BUMP") == "1":
    server_path = SHARED / "parent_server.py"
    server_py = server_path.read_text(encoding="utf-8")
    m = re.search(r'VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', server_py)
    if m:
        version = f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
        iss_path = HERE / "installer.iss"
        iss = iss_path.read_text(encoding="utf-8")
        iss_new = re.sub(r'#define AppVersion\s+"[^"]+"', f'#define AppVersion   "{version}"', iss)
        if iss_new != iss:
            iss_path.write_text(iss_new, encoding="utf-8")
            print(f"[build.py] Synced installer.iss AppVersion = {version} (bump skipped)")
        else:
            print(f"[build.py] App version = {version} (bump skipped)")
else:
    try:
        server_path = SHARED / "parent_server.py"
        server_py = server_path.read_text(encoding="utf-8")
        m = re.search(r'VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', server_py)
        if m:
            major, minor, patch = m.group(1), m.group(2), int(m.group(3)) + 1
            version = f"{major}.{minor}.{patch}"
            server_py = re.sub(r'VERSION = "[^"]+"', f'VERSION = "{version}"', server_py)
            server_path.write_text(server_py, encoding="utf-8")
            print(f"[build.py] App version = {version}")

            iss_path = HERE / "installer.iss"
            iss = iss_path.read_text(encoding="utf-8")
            iss_new = re.sub(r'#define AppVersion\s+"[^"]+"', f'#define AppVersion   "{version}"', iss)
            if iss_new != iss:
                iss_path.write_text(iss_new, encoding="utf-8")
                print(f"[build.py] Updated installer.iss AppVersion = {version}")
            # NOTE: All firmware versions (Arduino + camera) are independent — only
            # incremented when their respective firmware is compiled/deployed
    except Exception as e:
        print(f"[build.py] Warning: could not sync app version: {e}")
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
    "--add-data", f"{SHARED / 'spatial_engine.py'};.",
    "--add-data", f"{SHARED / 'bake_engine.py'};.",
    "--add-data", f"{SHARED / 'wled_bridge.py'};.",
    "--add-data", f"{SHARED / 'dmx_profiles.py'};.",
    "--add-data", f"{SHARED / 'dmx_artnet.py'};.",
    "--add-data", f"{SHARED / 'dmx_sacn.py'};.",
    "--add-data", f"{SHARED / 'show_generator.py'};.",
    "--add-data", f"{SHARED / 'community_client.py'};.",
    "--add-data", f"{SHARED / 'mover_calibrator.py'};.",
    "--add-data", f"{SHARED / 'mover_control.py'};.",
    "--add-data", f"{SHARED / 'space_mapper.py'};.",
    "--add-data", f"{SHARED / 'surface_analyzer.py'};.",
    "--add-data", f"{SHARED / 'parametric_mover.py'};.",
    "--add-data", f"{SHARED / 'remote_orientation.py'};.",
    "--add-data", f"{SHARED / 'dmx_universe.py'};.",
    "--add-data", f"{SHARED / 'depth_runtime.py'};.",
    "--add-data", f"{SHARED / 'depth_runner.py'};.",
    "--add-data", f"{SHARED / 'camera_settings.py'};.",   # #623
    "--add-data", f"{SHARED / 'ollama_runtime.py'};.",    # #623
    "--hidden-import=pystray",
    "--hidden-import=paramiko",
    "--hidden-import=numpy",
    "--hidden-import=cv2",
    "--hidden-import=PIL._tkinter_finder",
    "--collect-submodules=flask",
    "--collect-submodules=werkzeug",
    "--collect-submodules=esptool",
    "--collect-submodules=numpy",
    "--collect-submodules=cv2",
    "--collect-data=esptool",
    "--paths", str(SHARED),
]

# #568 — bundle ONLY firmware/registry.json (manifest + download URLs)
# into the installer. The binaries themselves (esp32/*.bin, giga/*.bin,
# orangepi/*.zip …) are downloaded on demand from the matching GitHub
# release via firmware_manager.download_firmware() and cached under
# %APPDATA%/SlyLED/firmware. This keeps the installer small and stops
# it from going stale when a new firmware drops.
reg_path = FWDIR / "registry.json"
if reg_path.exists():
    args.append("--add-data")
    args.append(f"{reg_path};firmware")

# #637 — bundle the user manual (HTML + images + markdown source) so the
# /help route resolves in frozen/installed builds. Without these, the
# PyInstaller bundle has no docs/ tree and /help returns 404.
HELP_DIR  = (HERE / ".." / ".." / "docs" / "help").resolve()
MANUAL_EN = (HERE / ".." / ".." / "docs" / "USER_MANUAL.md").resolve()
MANUAL_FR = (HERE / ".." / ".." / "docs" / "USER_MANUAL_fr.md").resolve()
BUILD_DIR = (HERE / ".." / ".." / "docs" / "build").resolve()
if HELP_DIR.exists():
    args += ["--add-data", f"{HELP_DIR};docs/help"]
if MANUAL_EN.exists():
    args += ["--add-data", f"{MANUAL_EN};docs"]
if MANUAL_FR.exists():
    args += ["--add-data", f"{MANUAL_FR};docs"]
if BUILD_DIR.exists():
    args += ["--add-data", f"{BUILD_DIR};docs/build"]

args.append(str(SHARED / "main.py"))

PyInstaller.__main__.run(args)
