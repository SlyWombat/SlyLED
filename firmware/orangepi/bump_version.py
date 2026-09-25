#!/usr/bin/env python3
"""Bump the camera firmware patch version in camera_server.py and registry.json."""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SERVER = HERE / "camera_server.py"
REGISTRY = HERE.parent / "registry.json"
# The registry entry the camera node's OTA path reads (#926 — this used to be
# "camera-orangepi", which matches nothing, so the registry was never bumped).
REGISTRY_ID = "camera-node"

# Read current version from camera_server.py
text = SERVER.read_text()
m = re.search(r'VERSION\s*=\s*"(\d+)\.(\d+)\.(\d+)"', text)
if not m:
    print("Could not find VERSION in camera_server.py")
    sys.exit(1)

major, minor, patch = int(m.group(1)), int(m.group(2)), int(m.group(3))
new_ver = f"{major}.{minor}.{patch + 1}"

# Find the registry entry BEFORE writing anything, so a missing entry can't
# leave camera_server.py bumped and the registry not.
reg = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
entry = next((fw for fw in reg.get("firmware", []) if fw.get("id") == REGISTRY_ID), None)
if entry is None:
    sys.exit(f"registry.json has no '{REGISTRY_ID}' entry — nothing bumped")

# Update camera_server.py
text = re.sub(r'VERSION\s*=\s*"[\d.]+"', f'VERSION = "{new_ver}"', text)
SERVER.write_text(text)

# Update registry.json (same layout it is stored in: 4-space indent, final newline)
entry["version"] = new_ver
REGISTRY.write_text(json.dumps(reg, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")

print(f"Camera firmware: {major}.{minor}.{patch} → {new_ver} (camera_server.py + registry.json '{REGISTRY_ID}')")
