#!/usr/bin/env python3
"""Bump the camera firmware patch version in camera_server.py and registry.json."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SERVER = HERE / "camera_server.py"
REGISTRY = HERE.parent / "registry.json"

# Read current version from camera_server.py
text = SERVER.read_text()
m = re.search(r'VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text)
if not m:
    print("Could not find VERSION in camera_server.py")
    sys.exit(1)

major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
new_ver = f"{major}.{minor}.{patch + 1}"

# Update camera_server.py
text = re.sub(r'VERSION\s*=\s*"[\d.]+"', f'VERSION = "{new_ver}"', text)
SERVER.write_text(text)

# Update registry.json
reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
for fw in reg.get("firmware", []):
    if fw.get("id") == "camera-orangepi":
        fw["version"] = new_ver
REGISTRY.write_text(json.dumps(reg, indent=4, ensure_ascii=False), encoding="utf-8")

print(f"Camera firmware: {major}.{minor}.{patch} → {new_ver}")
