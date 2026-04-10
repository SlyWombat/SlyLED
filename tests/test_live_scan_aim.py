#!/usr/bin/env python3
"""
test_live_scan_aim.py — End-to-end live test:
  1. Load layout from slyled-config (1).json
  2. Calibrate camera using positioned fixtures as reference points
  3. Scan for objects (detect the chair)
  4. Aim moving heads at the chair with red lighting

Usage:
    python tests/test_live_scan_aim.py
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

results = []

def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    mark = '\u2705' if cond else '\u274c'
    line = f"  {mark} {name}"
    if detail:
        line += f"  ({detail})"
    print(line)

def run():
    from parent_server import app

    config_path = os.path.join(os.path.dirname(__file__), 'user', 'slyled-config (1).json')
    with open(config_path) as f:
        config = json.load(f)

    print("\n=== Live Scan + Aim Test ===\n")

    with app.test_client() as c:
        # ── Step 0: Factory reset ──────────────────────────────────
        c.post('/api/reset', headers={"X-SlyLED-Confirm": "true"})

        # ── Step 1: Import the config ──────────────────────────────
        print("Step 1: Loading config...")
        r = c.post('/api/config/import', json=config,
                    headers={"Content-Type": "application/json"})
        ok('Config import', r.status_code == 200, r.get_json().get('err', ''))

        # Verify fixtures loaded
        r = c.get('/api/fixtures')
        fixtures = r.get_json()
        ok('Fixtures loaded', len(fixtures) >= 3, f'{len(fixtures)} fixtures')

        cameras = [f for f in fixtures if f.get('fixtureType') == 'camera']
        movers = [f for f in fixtures if f.get('fixtureType') == 'dmx']
        leds = [f for f in fixtures if f.get('fixtureType') == 'led']
        ok('Has camera', len(cameras) >= 1, f'{len(cameras)} cameras')
        ok('Has moving heads', len(movers) >= 1, f'{len(movers)} movers')

        cam = cameras[0] if cameras else None
        cam_id = cam['id'] if cam else None
        cam_ip = cam.get('cameraIp') if cam else None
        print(f"  Camera: id={cam_id} ip={cam_ip}")
        for m in movers:
            print(f"  Mover: id={m['id']} name={m['name']} addr={m.get('dmxStartAddr')}")

        # Verify positioned fixtures
        r = c.get('/api/layout')
        layout = r.get_json()
        positioned = [f for f in layout.get('fixtures', []) if f.get('positioned')]
        ref_count = len([f for f in positioned if f.get('fixtureType') in ('led', 'dmx')])
        ok('Fixtures positioned', len(positioned) >= 2,
           f'{len(positioned)} positioned, {ref_count} DMX refs for calibration')

        # ── Step 2: Camera calibration ─────────────────────────────
        print("\nStep 2: Camera calibration...")

        if cam_id is not None:
            # Start calibration
            r = c.post(f'/api/cameras/{cam_id}/calibrate/start')
            cal_data = r.get_json()
            if r.status_code == 200 and cal_data.get('ok'):
                refs = cal_data.get('fixtures', [])
                ok('Calibrate start', True, f'{len(refs)} reference fixtures')

                # Simulate detection of reference fixtures at pixel positions
                # In a real test, the camera would flash each fixture and detect it
                # Here we use synthetic pixel positions based on fixture stage positions
                for i, ref in enumerate(refs[:4]):
                    # Map stage coords to approximate pixel coords
                    # (simple linear mapping for simulation)
                    px = 50 + (ref['x'] / 3000) * 540
                    py = 400 - (ref.get('z', 0) / 1500) * 350
                    r = c.post(f'/api/cameras/{cam_id}/calibrate/detect',
                                json={'fixtureId': ref['id'], 'pixelX': px, 'pixelY': py})
                    ok(f'Calibrate detect {ref["name"]}', r.get_json().get('ok'),
                       f'pixel=({px:.0f},{py:.0f})')

                # Compute homography
                r = c.post(f'/api/cameras/{cam_id}/calibrate/compute')
                comp = r.get_json()
                ok('Calibration computed', comp.get('ok'),
                   f'error={comp.get("error", "?")}mm')
                ok('Camera calibrated', comp.get('calibrated') is True)
            else:
                ok('Calibrate start', False,
                   cal_data.get('err', 'insufficient refs'))
                # Even without calibration, scan will use ground-plane fallback
        else:
            ok('Calibrate skipped', True, 'no camera')

        # ── Step 3: Scan for objects (find the chair) ──────────────
        print("\nStep 3: Scanning for objects...")

        if cam_id is not None and cam_ip:
            # Try live scan first
            r = c.post(f'/api/cameras/{cam_id}/scan',
                        json={'threshold': 0.3, 'resolution': 320})
            scan = r.get_json()
            if scan.get('ok'):
                dets = scan.get('detections', [])
                ok('Scan succeeded', True,
                   f'{len(dets)} detections, capture={scan.get("captureMs")}ms')
                chairs = [d for d in dets if d.get('label') == 'chair']
                people = [d for d in dets if d.get('label') == 'person']
                ok('Found objects', len(dets) > 0,
                   ', '.join(f'{d["label"]} {d["confidence"]:.0%}' for d in dets[:5]))

                if chairs:
                    chair = chairs[0]
                    print(f"  Chair at stage: x={chair['x']}mm z={chair['z']}mm "
                          f"(size {chair['w']}x{chair['h']}mm)")
                elif dets:
                    chair = dets[0]  # Use first detection as target
                    print(f"  Using {chair['label']} at stage: x={chair['x']}mm z={chair['z']}mm")
                else:
                    chair = None
            else:
                ok('Scan succeeded', False, scan.get('err', 'unknown'))
                chair = None
                dets = []
        else:
            # Simulate a chair detection at center stage
            chair = {'label': 'chair', 'confidence': 0.85,
                     'x': 1500, 'y': 0, 'z': 750, 'w': 400, 'h': 400}
            dets = [chair]
            ok('Simulated chair detection', True, 'no live camera')

        # ── Step 4: Aim moving heads at chair with RED ─────────────
        print("\nStep 4: Aiming moving heads at chair with red...")

        if chair and movers:
            target = [chair['x'], 0, chair['z']]
            print(f"  Target: {target}")

            for m in movers:
                mid = m['id']
                # Set aim point via legacy aimPoint (converted to rotation server-side)
                r = c.put(f'/api/fixtures/{mid}/aim',
                           json={'aimPoint': target})
                ok(f'Aim mover {m["name"]} at chair', r.get_json().get('ok'))

                # Verify rotation persisted (converted from aimPoint)
                r = c.get(f'/api/fixtures/{mid}')
                fx = r.get_json()
                rot = fx.get('rotation', [])
                ok(f'Mover {mid} rotation set', len(rot) == 3,
                   f'rotation={rot}')

                # Compute what pan/tilt values would be needed
                from parent_server import compute_pan_tilt_calibrated
                pt_cal = compute_pan_tilt_calibrated(mid, target)
                if pt_cal:
                    ok(f'Mover {mid} calibrated pan/tilt', True,
                       f'pan={pt_cal[0]:.3f} tilt={pt_cal[1]:.3f}')
                else:
                    # Use geometric fallback
                    pos_map = {p['id']: p for p in layout.get('fixtures', [])}
                    fx_pos = pos_map.get(mid, {})
                    fx_xyz = [fx_pos.get('x', 0), fx_pos.get('y', 0), fx_pos.get('z', 0)]
                    from spatial_engine import compute_pan_tilt
                    prof = None
                    pid = m.get('dmxProfileId')
                    if pid:
                        from parent_server import _profile_lib
                        prof = _profile_lib.get_profile(pid)
                    pan_range = prof.get('panRange', 540) if prof else 540
                    tilt_range = prof.get('tiltRange', 270) if prof else 270
                    pt = compute_pan_tilt(fx_xyz, target, pan_range, tilt_range)
                    if pt:
                        ok(f'Mover {mid} geometric pan/tilt', True,
                           f'pan={pt[0]:.3f} tilt={pt[1]:.3f} (DMX pan={int(pt[0]*255)} tilt={int(pt[1]*255)})')
                    else:
                        ok(f'Mover {mid} pan/tilt', False, 'compute returned None')

            # Create an action to set movers to RED
            r = c.post('/api/actions', json={
                'name': 'Red at Chair',
                'type': 11,  # DMX Scene
                'color': [255, 0, 0],
                'params': {'dimmer': 255},
            })
            ok('Created red action', r.status_code == 200)

            print(f"\n  Moving heads aimed at {chair['label']} at "
                  f"({chair['x']}, {chair['z']})mm with RED color")

        else:
            ok('Aim skipped', True, 'no chair or no movers')

    # ── Summary ────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    passed = sum(1 for _, c, _ in results if c)
    failed = sum(1 for _, c, _ in results if not c)
    print(f"\n{passed} passed, {failed} failed, {len(results)} total")
    if failed:
        print("\nFailed:")
        for name, cond, detail in results:
            if not cond:
                print(f"  \u274c {name}  ({detail})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run())
