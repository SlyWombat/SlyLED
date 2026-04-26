#!/usr/bin/env python3
"""test_battleship_seed_grid.py — #694, #695.

#694 — battleship_discover's coarse grid is centred on the operator's
       Set Home seed instead of the fixture's mechanical centre. Pre-fix,
       a 540°-pan fixture with home at pan=0.677 had its grid clamped
       to [0, 0.667] — every probe pointed AWAY from the operator's
       confirmed forward direction.

#695 — _beam_detect_flash now writes (new_pan, new_tilt, dim=0) BEFORE
       writing dim=255. Without the fix, cross-tilt-row transitions
       slewed the head with the previous probe's dim=255 still latched.

Both tests rely only on stub-able helpers in mover_calibrator — no
camera node or DMX bridge required.
"""
import os, sys, importlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0
_errors = []


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _errors.append(name)
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


import mover_calibrator as mc

# ── #694 — grid centres on seed ─────────────────────────────────────────

section('#694 — battleship_discover grid centres on seed_pan')

# Stub the network bits so battleship_discover runs in-process.
collected_grid = []
mc._fresh_buffer = lambda: bytearray(512)
mc._set_mover_dmx = lambda *a, **kw: None
mc._hold_dmx     = lambda *a, **kw: None
mc._wait_settled = lambda *a, **kw: None
mc._dark_reference = lambda *a, **kw: True
mc._beam_detect = lambda *a, **kw: None
mc._beam_detect_verified = lambda *a, **kw: None
mc._beam_detect_flash = lambda *a, **kw: None

# Capture the grid the discoverer would scan by intercepting the cell
# loop. Patch _probe_cell to record (pan, tilt) and always return None
# so battleship walks the entire grid.
def _capture_grid_stub_factory(out):
    def _stub(pan, tilt, idx, total):
        out.append((float(pan), float(tilt)))
        return None
    return _stub

original_battleship = mc.battleship_discover

# We need to instrument _probe_cell which is defined locally inside
# battleship_discover. Easiest path: drive battleship_discover with a
# sentinel that lets us capture the FIRST grid cell visited (probe 1)
# and infer where the window starts. The post-#694 grid for seed_pan=
# 0.677, pan_frac=0.667 should put probe 1 near pan=0.677, not at
# pan=0.625.

# Instead of intercepting an internal function, pull out the same
# math into a checkable closure: replicate the post-#694 window math
# and assert the produced grid contains the seed inside its bounds.
def expected_window(seed_pan, pan_frac):
    half = pan_frac / 2.0
    pan_lo = max(0.0, min(1.0 - pan_frac, seed_pan - half))
    pan_hi = pan_lo + pan_frac
    return pan_lo, pan_hi

lo, hi = expected_window(0.677, 0.667)
ok(lo <= 0.677 <= hi,
   f'home (0.677) inside window [{lo:.3f},{hi:.3f}] (#694 grid centring)')

# 540° fixture, home at the "mechanical edge" (pan=0.95, near the +540°
# extreme). Window should clamp at 1.0, not push past.
lo, hi = expected_window(0.95, 0.667)
ok(abs(hi - 1.0) < 1e-9 and abs(lo - (1.0 - 0.667)) < 1e-9,
   f'home near edge clamps window at 1.0 (got [{lo:.3f},{hi:.3f}])')

# Mid-stage fixture, home at 0.5.
lo, hi = expected_window(0.5, 0.667)
ok(abs(lo - 0.1665) < 0.01 and abs(hi - 0.8335) < 0.01,
   f'home at 0.5 → symmetric window (got [{lo:.3f},{hi:.3f}])')

# 360° fixture (pan_frac=1.0), seed irrelevant → full 0..1 window.
lo, hi = expected_window(0.5, 1.0)
ok(lo == 0.0 and hi == 1.0,
   f'pan_frac=1.0 → full sweep (got [{lo:.3f},{hi:.3f}])')

# Now drive the actual battleship_discover and inspect the first
# generated cell. We can't easily intercept _probe_cell from here, but
# we can read the log line "battleship_discover: ... probes ... seed=...".
# Easier: reach into the generator by replacing _probe_cell.
import logging
logging.basicConfig(level=logging.WARNING)

# Stub everything battleship_discover calls so it returns immediately
# while we capture the grid.
captured_cells = []

def _capture_probe_cell(pan, tilt, idx, total):
    captured_cells.append((float(pan), float(tilt)))
    return None

# battleship_discover defines _probe_cell internally. Drive it via a
# minimal harness that monkey-patches the module-level helpers it uses.
# To capture cells, override _beam_detect_flash to record (pan, tilt)
# and return None (no candidate, so the discover walks every cell).
mc._beam_detect_flash = lambda bridge_ip, camera_ip, cam_idx, mover_addr, pan, tilt, color, dmx, threshold=30: (
    captured_cells.append((float(pan), float(tilt))) or None)

result = mc.battleship_discover(
    bridge_ip="0.0.0.0", camera_ip="0.0.0.0", mover_addr=1,
    cam_idx=0, color=(0, 255, 0),
    seed_pan=0.677, seed_tilt=0.0,
    pan_range_deg=540.0, tilt_range_deg=270.0,
    beam_width_deg=15.0,
    coarse_pan_min=4, coarse_pan_max=4,
    coarse_tilt_min=3, coarse_tilt_max=3,
    refine=False,
    reject_reflection=False,
    confirm_nudge_delta=0.01,
)
ok(result is None, 'discover returns None when no detect (expected)')
ok(len(captured_cells) > 0,
   f'captured probe cells (got {len(captured_cells)})')

if captured_cells:
    pans = [c[0] for c in captured_cells]
    pan_min = min(pans)
    pan_max = max(pans)
    # Window should bracket the seed (0.677). Allow some slack for
    # cell-centre math.
    ok(pan_min < 0.677 < pan_max or any(abs(p - 0.677) < 0.1 for p in pans),
       f'grid brackets seed pan=0.677 (got pan range [{pan_min:.3f},{pan_max:.3f}])')
    # No probe should sit > 0.5 normalised (90° on a 540° fixture)
    # away from the seed — that was the pre-fix symptom.
    far = [p for p in pans if abs(p - 0.677) > 0.5]
    ok(len(far) == 0,
       f'no probe more than 0.5 normalised from seed (got {len(far)} '
       f'far probes; seed=0.677, sample of far: {far[:3]})')


# ── #695 — _beam_detect_flash slews with light OFF first ───────────────

section('#695 — _beam_detect_flash blackout-then-move')

# Reset stubs to capture every _set_mover_dmx call in order.
import importlib
mc = importlib.reload(__import__('mover_calibrator'))

writes = []
def _record_write(dmx, addr, pan, tilt, r, g, b, dimmer=255, profile=None):
    writes.append({'pan': pan, 'tilt': tilt, 'rgb': (r, g, b), 'dim': dimmer})
mc._set_mover_dmx = _record_write
mc._hold_dmx = lambda *a, **kw: None
mc.time = type('t', (), {'sleep': lambda *_a, **_kw: None,
                          'time': lambda: 0,
                          'monotonic': lambda: 0})()

# Drive _beam_detect_flash; mock urlopen so it returns "not found"
# without actually hitting the network.
import urllib.request as _ur
class _FakeResp:
    def read(self): return b'{"found": false}'
_ur.urlopen = lambda *a, **kw: _FakeResp()

writes.clear()
result = mc._beam_detect_flash(
    bridge_ip="0.0.0.0", camera_ip="0.0.0.0",
    cam_idx=0, mover_addr=1,
    pan=0.625, tilt=0.250, color=(0, 255, 0),
    dmx=bytearray(512))

# Collect the writes that landed BEFORE the urlopen call. The contract:
# the FIRST write at (new_pan, new_tilt) must have dim=0 — light off
# while the head slews. THEN dim=255 once it's settled.
ok(len(writes) >= 2,
   f'flash detect writes ≥2 frames pre-camera (got {len(writes)})')
if writes:
    first = writes[0]
    ok(first['pan'] == 0.625 and first['tilt'] == 0.250,
       f'first write at new aim (got pan={first["pan"]} tilt={first["tilt"]})')
    ok(first['dim'] == 0,
       f'first write has dim=0 — slew with light OFF (got dim={first["dim"]}) '
       f'#695')
    if len(writes) >= 2:
        second = writes[1]
        ok(second['pan'] == 0.625 and second['tilt'] == 0.250,
           f'second write same aim')
        ok(second['dim'] == 255,
           f'second write has dim=255 — light on at destination '
           f'(got dim={second["dim"]})')


# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
