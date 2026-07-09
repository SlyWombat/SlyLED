#!/usr/bin/env python3
"""#906 — executable parity gate: SPA rotation-convention twins vs Python.

Hand-synced twins gated here (numeric equality within 1e-9):

  1. spa/js/app.js  rotationFromLayout / rotationToLayout
       <-> desktop/shared/camera_math.py rotation_from_layout /
           rotation_to_layout          (#586/#600 rotation convention)

  2. spa/js/scene-3d.js  _s3dStringDirFromRot
       <-> desktop/shared/camera_math.py build_camera_to_stage applied
           to the cam-local +Z forward vector    (#866 JS mirror)

The JS is executed for real via Node (classic scripts — the functions
are extracted by brace-counting and evaluated in a bare `node -e`
context, no DOM stubs needed for these pure helpers).

Run: `python3 tests/test_parity_rotation_math.py`
Requires: node (any LTS) on PATH.
"""

import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "desktop" / "shared"))

from camera_math import (  # noqa: E402
    build_camera_to_stage,
    rotation_from_layout,
    rotation_to_layout,
)

APP_JS = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "app.js"
SCENE3D_JS = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "scene-3d.js"

TOL = 1e-9


def require_node():
    if shutil.which("node") is None:
        print("[SKIP] node not on PATH — rotation parity gate skipped.")
        sys.exit(0)


def extract_js_function(source: str, name: str) -> str:
    """Pull `function <name>(...){...}` out of a classic-script JS file
    by brace counting. The SPA files are plain scripts (no modules), so
    this is the only way to execute a single helper without stubbing
    the whole DOM."""
    marker = f"function {name}("
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        c = source[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise ValueError(f"unbalanced braces extracting {name}")


def run_node(js_functions: str, driver: str) -> dict:
    script = js_functions + "\n" + driver
    out = subprocess.run(["node", "-e", script],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f"node failed:\n{out.stderr}")
    return json.loads(out.stdout)


# ── Test grid ─────────────────────────────────────────────────────────────
# All-zero, single-axis, combined, negative, >180°, >360°, fractional,
# and the #783 tilt-sign edge (rx > 0 must aim DOWN — checked explicitly
# below, not just cross-checked between the twins).
ROT_GRID = [
    [0, 0, 0],
    [30, 0, 0], [-30, 0, 0], [90, 0, 0], [-90, 0, 0],
    [0, 45, 0], [0, -45, 0], [0, 90, 0],
    [0, 0, 60], [0, 0, -60], [0, 0, 90], [0, 0, -90],
    [180, 0, 0], [0, 180, 0], [0, 0, 180],
    [270, 0, 0], [0, -270, 0], [0, 0, 361.5],
    [30, 45, 60], [-75, 120, -200], [12.5, -3.25, 270],
    [90, 90, 90], [180, 180, 180], [359, -359, 720],
    [75, 0, 0],       # the operator-confirmed #715 fixture pose
    [45, 0, -135], [-15.75, 33.333, 99.9],
]

# Degenerate layout arrays rotationFromLayout must normalise identically.
SHORT_GRID = [None, [], [5], [5, 10]]


def close(a, b, tol=TOL):
    return abs(a - b) <= tol


def main():
    require_node()
    app_src = APP_JS.read_text(encoding="utf-8")
    scene_src = SCENE3D_JS.read_text(encoding="utf-8")

    fns = "\n".join([
        extract_js_function(app_src, "rotationFromLayout"),
        extract_js_function(app_src, "rotationToLayout"),
        extract_js_function(scene_src, "_s3dStringDirFromRot"),
    ])
    driver = f"""
const rots = {json.dumps(ROT_GRID)};
const shorts = {json.dumps(SHORT_GRID)};
const out = {{fromLayout: [], toLayout: [], stringDir: [], shortFrom: [], shortDir: []}};
for (const r of rots) {{
  out.fromLayout.push(rotationFromLayout(r));
  const a = rotationFromLayout(r);
  out.toLayout.push(rotationToLayout(a.tilt, a.pan, a.roll));
  out.stringDir.push(_s3dStringDirFromRot(r));
}}
for (const r of shorts) {{
  out.shortFrom.push(rotationFromLayout(r));
  out.shortDir.push(_s3dStringDirFromRot(r));
}}
console.log(JSON.stringify(out));
"""
    res = run_node(fns, driver)
    failures = []
    checks = 0

    # 1) rotationFromLayout / rotationToLayout ---------------------------------
    for rot, js in zip(ROT_GRID, res["fromLayout"]):
        tilt, pan, roll = rotation_from_layout(rot)
        for key, py in (("tilt", tilt), ("pan", pan), ("roll", roll)):
            checks += 1
            if not close(js[key], py):
                failures.append(
                    f"rotationFromLayout({rot}).{key}: js={js[key]} py={py}")

    for rot, js in zip(ROT_GRID, res["toLayout"]):
        tilt, pan, roll = rotation_from_layout(rot)
        py = rotation_to_layout(tilt, pan, roll)
        checks += 1
        if not all(close(a, b) for a, b in zip(js, py)):
            failures.append(f"rotationToLayout round-trip({rot}): js={js} py={py}")
        # Round-trip must reproduce the input layout exactly.
        checks += 1
        if not all(close(a, float(b)) for a, b in zip(js, rot)):
            failures.append(f"rotationToLayout({rot}) != input: js={js}")

    for rot, js in zip(SHORT_GRID, res["shortFrom"]):
        tilt, pan, roll = rotation_from_layout(rot)
        for key, py in (("tilt", tilt), ("pan", pan), ("roll", roll)):
            checks += 1
            if not close(js[key], py):
                failures.append(
                    f"rotationFromLayout(short {rot}).{key}: js={js[key]} py={py}")

    # 2) _s3dStringDirFromRot vs build_camera_to_stage @ cam-local +Z ----------
    def py_string_dir(rot):
        tilt, pan, roll = rotation_from_layout(rot)
        R = build_camera_to_stage(tilt, pan, roll)
        # R includes the pinhole->stage frame swap; cam-local forward is +Z.
        if hasattr(R, "dot"):
            v = R.dot([0.0, 0.0, 1.0])
            return [float(v[0]), float(v[1]), float(v[2])]
        return [R[0][2], R[1][2], R[2][2]]

    for rot, js in zip(ROT_GRID, res["stringDir"]):
        py = py_string_dir(rot)
        checks += 1
        if not all(close(a, b) for a, b in zip(js, py)):
            failures.append(f"_s3dStringDirFromRot({rot}): js={js} py={py}")

    # JS contract: rot must be exactly length 3, else default +Y forward.
    for rot, js in zip(SHORT_GRID, res["shortDir"]):
        checks += 1
        if js != [0, 1, 0]:
            failures.append(
                f"_s3dStringDirFromRot(short {rot}) should be [0,1,0], got {js}")

    # 3) #783 / #586 tilt-sign edge: rx > 0 pitches DOWN (forward toward -Z),
    #    rz > 0 pans toward stage-left (+X) — asserted against convention,
    #    not just twin-vs-twin (a matching sign error in both would slip a
    #    pure parity check).
    idx_down = ROT_GRID.index([30, 0, 0])
    idx_up = ROT_GRID.index([-30, 0, 0])
    idx_left = ROT_GRID.index([0, 0, 60])
    checks += 3
    if not res["stringDir"][idx_down][2] < -1e-6:
        failures.append("#783 edge: rx=+30 must aim DOWN (z<0), got "
                        f"{res['stringDir'][idx_down]}")
    if not res["stringDir"][idx_up][2] > 1e-6:
        failures.append("#783 edge: rx=-30 must aim UP (z>0), got "
                        f"{res['stringDir'][idx_up]}")
    if not res["stringDir"][idx_left][0] > 1e-6:
        failures.append("#600 edge: rz=+60 must pan toward +X, got "
                        f"{res['stringDir'][idx_left]}")

    print(f"Rotation parity: {checks} checks across {len(ROT_GRID)} rotations "
          f"+ {len(SHORT_GRID)} degenerate layouts.")
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
