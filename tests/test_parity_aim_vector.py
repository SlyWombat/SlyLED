#!/usr/bin/env python3
"""#906 — executable parity gate: spa/js/app.js `_aimUnitVector` vs its
server-side reference.

The JS helper's own comment (#715) says it matches the orchestrator's
live-API IK "byte-for-byte". The actual Python reference is the
mount-relative branch of `/api/fixtures/live` in
desktop/shared/parent_server.py (~14554-14570), which is the same math
as `_canonical_aim_from_pan_tilt`'s mount-relative branch (~7176-7189):

    pr = (pan_norm - 0.5) * panRange ; tr = (tilt_norm - 0.5) * tiltRange
    d  = (sin(pr)·cos(tr), cos(pr)·cos(tr), -sin(tr))
    aim = euler_xyz_deg_to_matrix(rotation) @ d    (remote_math.py)

KNOWN DRIFT (found while building this gate, #906): the JS applies the
pan deviation CCW-about-Z (`dx1 = dx·cp - dy·sp`, app.js ~269, citing
the pre-#784 mover_calibrator convention), while the server, the #783
angular-aim convention, camera_math.pan_tilt_to_ray and
aim/stage_frame.py all agree pan>0 sweeps toward **+X** (CW about Z as
written). Net effect: for panNorm != homePanNorm the JS X/Y components
come out pan-mirrored. `_aimUnitVector` currently has no SPA callers,
so nothing renders wrong today, but the comment's byte-for-byte claim
is false. The fix is one line in app.js:

    var dx1 = dx*cp + dy*sp;  var dy1 = -dx*sp + dy*cp;

This gate PASSES while the mismatch is *exactly* that documented
pan-mirror (and prints a loud KNOWN DRIFT warning), and also passes
once the fix lands (strict equality). Any OTHER divergence fails.

The JS `inverted` / `homePanNorm` parameters have no server twin (the
live-API branch models the non-inverted, home=0.5 case only) — they
are exercised at home pan only, where the shared math is defined.

Run: `python3 tests/test_parity_aim_vector.py`
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

from remote_math import euler_xyz_deg_to_matrix, matrix_vec_mul  # noqa: E402

APP_JS = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "app.js"
TOL = 1e-9


def require_node():
    if shutil.which("node") is None:
        print("[SKIP] node not on PATH — aim-vector parity gate skipped.")
        sys.exit(0)


def extract_js_function(source: str, name: str) -> str:
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


def server_aim(rotation, pan_norm, tilt_norm, pan_range=540, tilt_range=270):
    """Python replica of parent_server.py /api/fixtures/live lines
    ~14554-14570 (mount-relative branch). Kept literal — this is the
    reference, not a reimplementation of the JS."""
    pr = math.radians((pan_norm - 0.5) * (pan_range or 540))
    tr = math.radians((tilt_norm - 0.5) * (tilt_range or 270))
    cos_t = math.cos(tr)
    d = (math.sin(pr) * cos_t, math.cos(pr) * cos_t, -math.sin(tr))
    rot = rotation or [0, 0, 0]
    if rot[0] == 0 and rot[1] == 0 and rot[2] == 0:
        return list(d)
    R = euler_xyz_deg_to_matrix(rot)
    return list(matrix_vec_mul(R, d))


ROTATIONS = [
    [0, 0, 0], [30, 0, 0], [-30, 0, 0], [75, 0, 0], [0, 45, 0],
    [0, 0, 60], [0, 0, -90], [30, 45, 60], [-75, 10, -120],
    [180, 0, 90], [12.5, -3.25, 270],
]
TILT_NORMS = [0.0, 0.25, 0.5, 0.75, 1.0]
PAN_NORMS = [0.0, 0.25, 0.5, 0.6, 1.0]
RANGES = [(540, 270), (360, 180)]


def vec_close(a, b, tol=TOL):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def main():
    require_node()
    src = APP_JS.read_text(encoding="utf-8")
    fn = extract_js_function(src, "_aimUnitVector")

    cases = []
    for rot in ROTATIONS:
        for pan_range, tilt_range in RANGES:
            for tn in TILT_NORMS:
                for pn in PAN_NORMS:
                    cases.append({"rot": rot, "pan": pn, "tilt": tn,
                                  "panRange": pan_range, "tiltRange": tilt_range})

    driver = f"""
const cases = {json.dumps(cases)};
const out = cases.map(c =>
  _aimUnitVector(c.rot, c.pan, c.tilt, c.panRange, c.tiltRange, false, 0.5));
console.log(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", fn + "\n" + driver],
                          capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        print(f"node failed:\n{proc.stderr}")
        sys.exit(1)
    js_results = json.loads(proc.stdout)

    failures = []
    strict = 0
    drift = 0
    for case, js in zip(cases, js_results):
        py = server_aim(case["rot"], case["pan"], case["tilt"],
                        case["panRange"], case["tiltRange"])
        if vec_close(js, py):
            strict += 1
            continue
        # #918 fixed the pan-mirror drift this gate once tolerated — the gate
        # now runs strict: any divergence at all is a failure.
        failures.append(
            f"rot={case['rot']} pan={case['pan']} tilt={case['tilt']} "
            f"ranges=({case['panRange']},{case['tiltRange']}): "
            f"js={js} py={py}")

    total = len(cases)
    print(f"_aimUnitVector parity: {total} cases — {strict} strict matches "
          f"(strict mode since #918; drift counter retired: {drift}).")
    if failures:
        print(f"FAILED ({len(failures)}):")
        for f in failures[:25]:
            print(f"  - {f}")
        sys.exit(1)
    # Sanity: at home pan every case must match strictly — if none did,
    # the extraction or reference went sideways.
    if strict == 0:
        print("FAILED: no strict matches at all — gate is miswired.")
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
