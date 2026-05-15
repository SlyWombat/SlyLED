#!/usr/bin/env python3
"""test_show_pipeline_regressions.py — wire-level regression for the
v1.7.x show-pipeline cluster (#835, #840, #845).

Pre-#858 the existing standalone tests for these issues asserted
*structure* (function still uses modulo-wrap, accepts the
`tl_action_ids` kwarg, etc.) which any cosmetic refactor preserves.
This harness asserts *output*: a baked show actually drives the
universe buffer, the orphan-Track-action blackout doesn't fire when
the timeline doesn't reference the action, and a single-item
loop_all playlist never punches a zero frame across wrap boundaries.

Bypasses the action/bake API by injecting `_bake_result[tid]` directly
with synthetic segments. The issue's note about ACT_DMX_SCENE auto-
promote producing rainbow output is sidestepped: we don't go through
bake at all, just feed the playback loop the segment params it
expects ({r, g, b, dimmer, pan, tilt}).

Run: python -X utf8 tests/test_show_pipeline_regressions.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402

_passed = 0
_failed = 0


def _ok(cond, msg, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}  ({detail})")


def _setup_mover_fixture(c, name, addr, profile_id="movinghead-150w-12ch"):
    """Create a fixture with Home + Secondary so AimSphere resolves and
    the playback loop can write pan/tilt without raising."""
    r = c.post("/api/fixtures", json={
        "name": name, "type": "point", "fixtureType": "dmx",
        "dmxUniverse": 1, "dmxStartAddr": addr,
        "dmxChannelCount": 12, "dmxProfileId": profile_id,
        "rotation": [0, 0, 0],
    })
    fid = r.get_json()["id"]
    c.post(f"/api/fixtures/{fid}/home", json={
        "panDmx16": 32768, "tiltDmx16": 16384,
        "secondary": {
            "panOffsetDmx16": 16384, "tiltOffsetDmx16": 16384,
            "panMovedDirection": "right", "tiltMovedDirection": "up",
        },
    })
    parent_server._layout.setdefault("children", []).append(
        {"id": fid, "x": 0, "y": 0, "z": 2000})
    return fid


def _inject_steady_red_bake(tid, fid, duration=30):
    """Synthetic bake — steady r=255 g=0 b=0 dimmer=200 across the whole
    duration. Mirrors the segment shape `_dmx_playback_loop` consumes
    (skips the bake/action pipeline entirely)."""
    parent_server._bake_result[tid] = {
        "timelineId": tid,
        "bakedAt": int(time.time()),
        "fixtures": {fid: {"segments": [{
            "startS": 0.0, "durationS": float(duration), "_pri": 0,
            "params": {"r": 255, "g": 0, "b": 0,
                       "dimmer": 200, "pan": 0.5, "tilt": 0.5},
        }]}},
        "totalFrames": 0, "fps": 40,
    }


def _drain_existing_playback():
    """Stop any in-flight playback thread from a prior test cell."""
    parent_server._dmx_playback_stop.set()
    time.sleep(0.05)
    parent_server._dmx_playback_stop.clear()


def test_835_orphan_track_action_does_not_blackout_dimmer():
    """#835 — orphan Track action in `_actions` (no clip references it)
    must NOT zero the master Dimmer on movers the timeline drives.
    Pre-fix `_evaluate_track_actions` iterated every type-18 action and
    the unassigned-heads sweep stomped dimmer to 0 every frame. The
    fix's `tl_action_ids` filter skips the orphan."""
    print("\n-- test_835_orphan_track_action_does_not_blackout_dimmer --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        fid = _setup_mover_fixture(c, "#858 mover-A", addr=1)

        # Orphan Track action: type 18 in _actions, NOT referenced by
        # any clip in the timeline. trackObjectIds=[] so the fallback
        # is the timeline-track scope.
        r = c.post("/api/actions", json={
            "name": "Orphan Track", "type": 18,
            "trackObjectType": "person",
            "trackObjectIds": [], "trackFixtureIds": [],
        })
        orphan_aid = r.get_json()["id"]

        # Timeline references the mover but has NO Track-action clips.
        r = c.post("/api/timelines", json={"name": "NoTrack", "durationS": 30})
        tid = r.get_json()["id"]
        c.put(f"/api/timelines/{tid}", json={
            "name": "NoTrack", "durationS": 30,
            "tracks": [{"fixtureId": fid, "clips": []}],
        })

        _inject_steady_red_bake(tid, fid, duration=30)

        # Run the playback loop directly; go_epoch in the past skips
        # the 5 s NTP wait the show-start API path imposes.
        threading.Thread(
            target=parent_server._dmx_playback_loop,
            args=(tid, time.time() - 0.05, 30, False),
            daemon=True,
        ).start()
        time.sleep(0.30)

        # movinghead-150w-12ch: dimmer offset 5 → ch6 at start=1.
        prof = parent_server._profile_lib.channel_info("movinghead-150w-12ch") or {}
        cmap = prof.get("channel_map") or {}
        dim_off = cmap.get("dimmer", 5)
        chans = c.get("/api/dmx/monitor/1").get_json()["channels"]
        dim_val = chans[1 - 1 + dim_off]

        _ok(dim_val > 0,
            "#835 dimmer NOT zeroed by orphan Track action",
            f"got dim={dim_val} (expected ~200)")

        # Cleanup
        parent_server._dmx_playback_stop.set()
        time.sleep(0.05)
        c.delete(f"/api/timelines/{tid}")
        c.delete(f"/api/fixtures/{fid}")
        c.delete(f"/api/actions/{orphan_aid}")
        parent_server._bake_result.pop(tid, None)
        parent_server._layout["children"] = [
            ch for ch in parent_server._layout.get("children", [])
            if ch.get("id") != fid]


def test_840_loop_wrap_no_zero_frame():
    """#840 — single-item playlist with loopAll=True must not punch a
    zero frame at wrap boundaries. Pre-fix `_dmx_playback_single` ran
    a #364 zero-sweep at `elapsed > duration` every iteration; the fix
    routes the single-item-loop_all case through `_dmx_playback_loop
    (loop=True)` which uses modulo wrap and never blacks out mid-show."""
    print("\n-- test_840_loop_wrap_no_zero_frame --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        fid = _setup_mover_fixture(c, "#858 mover-B", addr=20)

        r = c.post("/api/timelines", json={"name": "LoopRed", "durationS": 0.4})
        tid = r.get_json()["id"]
        c.put(f"/api/timelines/{tid}", json={
            "name": "LoopRed", "durationS": 0.4,
            "tracks": [{"fixtureId": fid, "clips": []}],
        })
        # Bake duration MUST match timeline so the playback's modulo
        # wrap and segment query stay in sync.
        _inject_steady_red_bake(tid, fid, duration=0.4)

        # Drive `_show_playback_loop` directly with the single-item
        # loop_all path — exercises the v1.7.82 routing fix (#840).
        threading.Thread(
            target=parent_server._show_playback_loop,
            args=([tid], True, time.time(), 0),
            daemon=True,
        ).start()

        prof = parent_server._profile_lib.channel_info("movinghead-150w-12ch") or {}
        cmap = prof.get("channel_map") or {}
        dim_off = cmap.get("dimmer", 5)
        addr_idx = 20 - 1 + dim_off

        # Sample every 25 ms for 1.0 s — covers ~2.5 wrap boundaries on
        # the 0.4 s duration. Every sample must read dim > 0.
        zero_samples = 0
        sample_count = 0
        for _ in range(40):
            time.sleep(0.025)
            chans = c.get("/api/dmx/monitor/1").get_json()["channels"]
            sample_count += 1
            if chans[addr_idx] == 0:
                zero_samples += 1

        _ok(sample_count >= 30,
            "#840 sampled ≥ 30 frames", f"sample_count={sample_count}")
        _ok(zero_samples == 0,
            "#840 single-item loop_all has NO wrap blackout",
            f"{zero_samples}/{sample_count} samples read dim=0")

        # Cleanup
        parent_server._dmx_playback_stop.set()
        time.sleep(0.05)
        c.delete(f"/api/timelines/{tid}")
        c.delete(f"/api/fixtures/{fid}")
        parent_server._bake_result.pop(tid, None)
        parent_server._layout["children"] = [
            ch for ch in parent_server._layout.get("children", [])
            if ch.get("id") != fid]


def test_860_slymovehead_geometry_orient_to_pan_smooth():
    """#860 — live test 2026-05-08 reported claim.pan_smooth frozen at
    the calibrate-end value across many orient updates on a slymovehead
    fixture (homePan 52015, homeTilt 0, panRange 540, tiltRange 180,
    inverted tilt sign). Pre-fix v1.7.80's `_aim_to_pan_tilt`
    `except Exception` swallowed AimSphere errors at DEBUG with no
    operator-visible signal. v1.7.86+'s named early-return guards
    (#855) + WARNING-level exception logging (#851) + claim.aim_error
    surface (#855) make this failure mode diagnosable.

    This test pins the steady-state contract: with valid Home +
    Secondary on a slymovehead-shape fixture, the seven orient samples
    from #860's live log (calibrate-end + 6 gyro-moves) MUST produce
    distinct pan_smooth values. If a future regression brings back the
    silent-swallow pattern, this test surfaces it before reaching the
    rig.
    """
    print("\n-- test_860_slymovehead_geometry_orient_to_pan_smooth --")
    from remote_orientation import KIND_GYRO as _KP_860
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        # Use movinghead-150w-12ch (panRange 540, tiltRange 270 vs
        # slymovehead's 180 — close enough that the IK exercises the
        # slope-from-home math identically). The fixture's home values
        # below match the live fid=19's extreme anchors.
        r = c.post("/api/fixtures", json={
            "name": "#860 slymovehead-shape", "type": "point",
            "fixtureType": "dmx",
            "dmxUniverse": 1, "dmxStartAddr": 1,
            "dmxChannelCount": 12,
            "dmxProfileId": "movinghead-150w-12ch",
            "rotation": [0, 0, 0],
        })
        fid = r.get_json()["id"]
        # Extreme home (homeTilt = 0) + inverted tilt sign — same
        # cell the issue's harness gap section flags as different
        # from prior test fixtures.
        c.post(f"/api/fixtures/{fid}/home", json={
            "panDmx16": 52015, "tiltDmx16": 0,
            "secondary": {
                "panOffsetDmx16": 16384, "tiltOffsetDmx16": 16384,
                "panMovedDirection": "right",
                "tiltMovedDirection": "down",
            },
        })
        parent_server._layout.setdefault("children", []).append(
            {"id": fid, "x": 0, "y": 0, "z": 2000})

        did = "#860-slymovehead-gyro"
        remote = parent_server._remotes.add(
            name="LiveTestGyro", kind=_KP_860, device_id=did)
        remote.R_world_to_stage = (1.0, 0.0, 0.0, 0.0)
        remote.calibrated = True
        remote.calibrated_at = time.time()
        remote.calibrated_against = {"kind": "mover", "objectId": fid}
        remote.stale_reason = None

        ok_c, _ = parent_server._mover_engine.claim(
            fid, did, "LiveTestGyro", "gyro",
            smoothing=0.15, convention="flat_pitch_yaw")
        _ok(ok_c, "#860 claim acquires (gyro pre-cal'd via #847 path)")
        parent_server._mover_engine.start_stream(fid, did)

        cl = parent_server._mover_engine._claims.get(fid)
        # Mimic calibrate-end's "force jump on next valid IK" reset.
        cl.calibrated_here = True
        cl.have_pan_tilt = False

        # Operator's 7 orient samples from #860's live log.
        orient_aim_seq = [
            (0.348, 0.312, 0.884),    # calibrate-end
            (-0.146, 0.915, 0.375),   # gyro moves 1
            (0.065, 0.985, 0.157),    # 2
            (0.082, 0.989, 0.119),    # 3
            (0.115, 0.984, 0.133),    # 4
            (0.187, 0.974, 0.126),    # 5
            (0.086, 0.994, 0.066),    # 6
        ]
        pan_history = []
        for aim in orient_aim_seq:
            # Bypass the quat layer — set aim_stage directly so the
            # test isolates the tick-loop IK math from the quat
            # convention plumbing.
            remote.aim_stage = aim
            remote.last_data = time.time()
            parent_server._mover_engine._tick()
            pan_history.append(cl.pan_smooth)

        # Contract: at least 5 distinct pan_smooth values across the 7
        # samples (some clustering is fine; freeze is the failure mode
        # we're guarding against).
        unique_count = len(set(round(p, 4) for p in pan_history))
        _ok(unique_count >= 5,
            "#860 orient sweep produces ≥5 distinct pan_smooth values "
            "(no freeze)",
            f"unique={unique_count} history={[round(p,4) for p in pan_history]}")
        # Also assert the spread covers the IK-responsive region (not
        # all clustered at one value within rounding).
        spread = max(pan_history) - min(pan_history)
        _ok(spread > 0.05,
            "#860 pan_smooth spans > 5% across the orient sweep",
            f"spread={spread:.4f}")
        # Assert no aim_error transition occurred (the per-tick guards
        # all pass for valid Home + Secondary).
        _ok(getattr(cl, "aim_error", None) is None,
            "#860 no aim_error transition during orient sweep",
            f"aim_error={getattr(cl, 'aim_error', None)}")

        # Cleanup
        try:
            parent_server._mover_engine.release(fid, did)
        except Exception:
            pass
        c.delete(f"/api/fixtures/{fid}")
        parent_server._remotes.remove(remote.id)
        parent_server._layout["children"] = [
            ch for ch in parent_server._layout.get("children", [])
            if ch.get("id") != fid]


def test_853_master_grand_master_scales_universe_no_show():
    """#853 — global brightness must apply at universe-buffer-send
    time as a final gate, regardless of whether a show is running.
    Pre-fix, /api/brightness only affected the bake-segment render
    path; Track actions, mover-control claims, direct dmx-test, and
    no-show ambient state all bypassed the scaling. Post-fix, the
    ArtNet engine's `get_data_scaled` applies the gamma-corrected
    master to intensity-class channels (red/green/blue/dimmer/etc;
    NOT pan/tilt/strobe/gobo/wheel-slot) on every send tick."""
    print("\n-- test_853_master_grand_master_scales_universe_no_show --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        # generic-dimmer-rgb (4ch: dim, R, G, B) gives us direct
        # intensity-channel addressing without going through a show.
        r = c.post("/api/fixtures", json={
            "name": "#853 par", "type": "point", "fixtureType": "dmx",
            "dmxUniverse": 1, "dmxStartAddr": 1,
            "dmxChannelCount": 4,
            "dmxProfileId": "generic-dimmer-rgb",
            "rotation": [0, 0, 0],
        })
        fid = r.get_json()["id"]

        # Reset master to 255 + write a known-state frame.
        c.post("/api/brightness", json={"value": 255})
        c.post(f"/api/fixtures/{fid}/dmx-test", json={
            "pan": -1, "tilt": -1,
            "dimmer": 200/255.0, "red": 1.0, "green": 0.5, "blue": 0.0,
        })
        chans = c.get("/api/dmx/monitor/1").get_json()["channels"]
        # Profile: dimmer=ch1 (offset 0), red=ch2, green=ch3, blue=ch4.
        _ok(chans[0] == 200,
            "#853 baseline dimmer at master=255",
            f"got {chans[0]} (expected 200)")
        _ok(chans[1] == 255,
            "#853 baseline red at master=255",
            f"got {chans[1]}")

        # Drop master to 128 — the monitor should reflect the gamma-
        # corrected scaled view (matching what the wire would carry).
        c.post("/api/brightness", json={"value": 128})
        chans = c.get("/api/dmx/monitor/1").get_json()["channels"]
        # Linear scale: 200 × 128/255 = 100. Gamma 2.2: 100 → 13.
        # Anything substantially less than 200 confirms scaling fired.
        _ok(chans[0] < 100,
            "#853 master=128 scales dimmer down",
            f"got {chans[0]} (expected < 100; gamma 200→13)")
        _ok(chans[1] < 200,
            "#853 master=128 scales red down (RGB-only profile)",
            f"got {chans[1]} (expected < 200)")

        # Restore master — output returns to baseline (no double-apply,
        # no stuck scaled values).
        c.post("/api/brightness", json={"value": 255})
        chans = c.get("/api/dmx/monitor/1").get_json()["channels"]
        _ok(chans[0] == 200,
            "#853 restore to master=255 returns dimmer to 200",
            f"got {chans[0]} (expected 200; round-trip clean)")

        c.delete(f"/api/fixtures/{fid}")
        c.post("/api/brightness", json={"value": 255})


def test_853_master_does_not_scale_pan_tilt():
    """#853 — pan/tilt/strobe/gobo/wheel-slot bytes are positional
    indices, not intensities. The master grand-master gate must NOT
    scale them. A 12-ch movinghead at master=64 should still report
    its pan/tilt bytes verbatim."""
    print("\n-- test_853_master_does_not_scale_pan_tilt --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        r = c.post("/api/fixtures", json={
            "name": "#853 mover", "type": "point", "fixtureType": "dmx",
            "dmxUniverse": 1, "dmxStartAddr": 1,
            "dmxChannelCount": 12,
            "dmxProfileId": "movinghead-150w-12ch",
            "rotation": [0, 0, 0],
        })
        fid = r.get_json()["id"]

        c.post("/api/brightness", json={"value": 255})
        c.post(f"/api/fixtures/{fid}/dmx-test", json={
            "pan": 0.5, "tilt": 0.5, "dimmer": 200/255.0,
        })
        chans_full = c.get("/api/dmx/monitor/1").get_json()["channels"]
        pan_full = chans_full[0]
        tilt_full = chans_full[2]
        dim_full = chans_full[5]

        c.post("/api/brightness", json={"value": 64})
        chans_dim = c.get("/api/dmx/monitor/1").get_json()["channels"]
        pan_dim = chans_dim[0]
        tilt_dim = chans_dim[2]
        dim_dim = chans_dim[5]

        _ok(pan_dim == pan_full,
            "#853 pan unchanged by master (positional, not intensity)",
            f"pan_full={pan_full} pan_dim={pan_dim}")
        _ok(tilt_dim == tilt_full,
            "#853 tilt unchanged by master",
            f"tilt_full={tilt_full} tilt_dim={tilt_dim}")
        _ok(dim_dim < dim_full,
            "#853 dimmer scaled by master",
            f"dim_full={dim_full} dim_dim={dim_dim}")

        c.delete(f"/api/fixtures/{fid}")
        c.post("/api/brightness", json={"value": 255})


def test_848_invariant_1_default_rgb_on_press_start():
    """#848 invariant 1 — `_gyro_lights_on` (called pre-CLAIM_ACK on
    every press-Start) must write a default RGB when the wire is
    currently dark, so the operator sees the head light up
    immediately. Pre-fix only the dimmer was written; the lamp gate
    opened but the LEDs stayed black-commanded ('no lights came on'
    operator report)."""
    print("\n-- test_848_invariant_1_default_rgb_on_press_start --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        # `generic-dimmer-rgb` (4-ch: dimmer + R/G/B) is in the static
        # profile catalog and gives us direct RGB bytes to assert.
        # The 12-ch wheel-only mover would exercise the wheel-slot
        # path via #842's set_fixture_rgb but verifying component
        # bytes is the cleaner assertion.
        r = c.post("/api/fixtures", json={
            "name": "#848 inv1 mover", "type": "point",
            "fixtureType": "dmx",
            "dmxUniverse": 1, "dmxStartAddr": 60,
            "dmxChannelCount": 4,
            "dmxProfileId": "generic-dimmer-rgb",
            "rotation": [0, 0, 0],
        })
        fid = r.get_json()["id"]

        # Wire starts dark — confirm the precondition.
        chans_before = c.get("/api/dmx/monitor/1").get_json()["channels"]
        prof = parent_server._profile_lib.channel_info("generic-dimmer-rgb") or {}
        cmap = prof.get("channel_map") or {}
        r_idx = 60 - 1 + cmap.get("red", 1)
        g_idx = 60 - 1 + cmap.get("green", 2)
        b_idx = 60 - 1 + cmap.get("blue", 3)

        _ok(chans_before[r_idx] + chans_before[g_idx] + chans_before[b_idx] < 30,
            "#848 inv1 precondition: wire is dark before lights_on",
            f"got R={chans_before[r_idx]} G={chans_before[g_idx]} B={chans_before[b_idx]}")

        # Fire the lamp-on path directly (same call the gyro press-Start
        # handler makes at line 1553 of parent_server.py).
        parent_server._gyro_lights_on(fid)

        chans_after = c.get("/api/dmx/monitor/1").get_json()["channels"]
        rgb_sum = chans_after[r_idx] + chans_after[g_idx] + chans_after[b_idx]
        _ok(rgb_sum > 30,
            "#848 inv1 lights_on writes default RGB when wire is dark",
            f"got R={chans_after[r_idx]} G={chans_after[g_idx]} B={chans_after[b_idx]} "
            f"(sum={rgb_sum}; default white expected)")

        # Inverse case: pre-set RGB on the wire, run lights_on, assert
        # the existing colour SURVIVES (#814 inheritance semantic).
        c.post("/api/dmx/monitor/1/set", json={"channels": [
            {"addr": r_idx + 1, "value": 200},
            {"addr": g_idx + 1, "value": 0},
            {"addr": b_idx + 1, "value": 0},
        ]})
        parent_server._gyro_lights_on(fid)
        chans_after2 = c.get("/api/dmx/monitor/1").get_json()["channels"]
        _ok(chans_after2[r_idx] >= 180,
            "#848 inv1 lights_on preserves existing red wash (inherits #814)",
            f"got R={chans_after2[r_idx]} (expected ~200; pre-existing red kept)")

        c.delete(f"/api/fixtures/{fid}")


def test_845_playback_writes_first_frame_under_300ms():
    """#845 sanity: the playback loop produces non-zero output within
    300 ms of go_epoch on a baked DMX fixture. Pre-fix the loop died
    on the first iteration via TypeError in `_apply_handover_slew`,
    leaving every channel at zero forever."""
    print("\n-- test_845_playback_writes_first_frame_under_300ms --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        fid = _setup_mover_fixture(c, "#858 mover-C", addr=40)
        r = c.post("/api/timelines", json={"name": "FirstFrame",
                                            "durationS": 30})
        tid = r.get_json()["id"]
        _inject_steady_red_bake(tid, fid, duration=30)

        threading.Thread(
            target=parent_server._dmx_playback_loop,
            args=(tid, time.time() - 0.05, 30, False),
            daemon=True,
        ).start()
        time.sleep(0.30)

        prof = parent_server._profile_lib.channel_info("movinghead-150w-12ch") or {}
        cmap = prof.get("channel_map") or {}
        dim_off = cmap.get("dimmer", 5)
        chans = c.get("/api/dmx/monitor/1").get_json()["channels"]
        dim_val = chans[40 - 1 + dim_off]

        _ok(dim_val > 0,
            "#845 first frame reaches universe within 300 ms",
            f"got dim={dim_val} (expected ~200; 0 means daemon thread died)")

        parent_server._dmx_playback_stop.set()
        time.sleep(0.05)
        c.delete(f"/api/timelines/{tid}")
        c.delete(f"/api/fixtures/{fid}")
        parent_server._bake_result.pop(tid, None)
        parent_server._layout["children"] = [
            ch for ch in parent_server._layout.get("children", [])
            if ch.get("id") != fid]


# ── Canonical synthetic-bake segment shape (#858) ────────────────────
# `_dmx_playback_loop` reads segment `params` directly. Per
# parent_server.py:14550-14570 the keys it understands are:
#     r, g, b           (uint8 0-255 — RGB component bytes)
#     dimmer            (uint8 0-255 — master dimmer)
#     pan, tilt         (float 0-1 — 16-bit pan/tilt fraction)
#     strobe            (uint8 0-255)
#     gobo              (uint8 0-255)
#     colorWheel        (uint8 0-255 — explicit slot override; #841)
#     prism, focus, zoom (uint8 0-255)
# These are post-bake values; the bake engine maps action types
# (Solid, Fade, DMX_SCENE, etc.) to per-frame dict[str, value]
# segments. By constructing the segment dict directly we sidestep
# the `type:1 → ACT_DMX_SCENE auto-promote produces rainbow` issue
# the harness is documenting around: any deterministic colour test
# should inject this dict shape, not invoke the bake engine.


def test_862_symptom1_press_start_seeds_default_dimmer():
    """#862 symptom #1 — Android claim → press-Start (start_stream) must
    seed `claim.dimmer` to a visible default so the lamp lights up. Pre-
    fix MoverClaim.__init__ left dimmer=None (the #814 tristate "inherit
    whatever was on the wire") AND start_stream did not seed it either,
    so a fresh claim of a parked fixture wrote a blank dimmer channel
    and the operator saw nothing happen."""
    print("\n-- test_862_symptom1_press_start_seeds_default_dimmer --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        _drain_existing_playback()

        fid = _setup_mover_fixture(c, "#862 sym1 mover", addr=20)

        ok, _ = parent_server._mover_engine.claim(
            fid, "phone-862", "Pixel", device_type="phone",
            smoothing=0.15, convention="flat_pitch_yaw")
        _ok(ok, "#862 sym1 claim accepted")

        # Pre-start the dimmer is None (inherit-mode).
        cl = parent_server._mover_engine._claims.get(fid)
        _ok(cl is not None and cl.dimmer is None,
            "#862 sym1 pre-start dimmer is None (#814 inherit semantic)",
            f"got dimmer={cl.dimmer if cl else 'no-claim'}")

        # press-Start equivalent — start_stream is the path Android calls
        # via POST /api/mover-control/start.
        parent_server._mover_engine.start_stream(fid, "phone-862")
        cl = parent_server._mover_engine._claims.get(fid)
        _ok(cl is not None and cl.dimmer == 255,
            "#862 sym1 start_stream seeds default dimmer=255",
            f"got dimmer={cl.dimmer if cl else 'no-claim'}")

        c.delete(f"/api/fixtures/{fid}")


def test_862_symptom2_calibrate_end_does_not_swing_head():
    """#862 symptom #2 — phone aimed at head's pose → press Calibrate →
    release must NOT swing the head. Pre-fix `Remote._apply_quat` ran a
    qz-negate hack on the live-orient quat that did NOT mirror into
    `Remote.calibrate()`, so calibrate-frame and orient-frame R_world_to
    _stage diverged and identity-from-calibrate was off-axis. Cell N of
    `test_orient_contract.py` is the unit-level pin; this cell is the
    integration-level pin that the head physically holds steady when
    the operator calibrates against a non-trivial phone pose."""
    print("\n-- test_862_symptom2_calibrate_end_does_not_swing_head --")
    from remote_orientation import Remote, KIND_PHONE, OrientConvention
    from remote_math import quat_from_euler_zyx_deg

    r = Remote(id=862, kind=KIND_PHONE,
               forward_local=(0.0, 1.0, 0.0),
               up_local=(0.0, 0.0, 1.0))
    r.convention = OrientConvention.FLAT_PITCH_YAW

    # Realistic non-identity portrait quat (operator holds phone slightly
    # tipped/twisted — what `getQuaternionFromVector` produces live).
    cal_quat = quat_from_euler_zyx_deg(roll=3.0, pitch=10.0, yaw=5.0)
    r.calibrate(target_aim_stage=(0.0, 1.0, 0.0), quat=cal_quat)

    # Operator is holding phone steady — orient sends back the same quat.
    r.update_from_quat(cal_quat)

    _ok(r.aim_stage is not None,
        "#862 sym2 calibrate-pose orient produces aim_stage")
    if r.aim_stage is not None:
        # Identity-from-calibrate-pose must aim at the calibrate target
        # (0, 1, 0). Pre-fix x was off by ~0.66 (the live-test swing
        # vector); post-fix x ≈ 0, y ≈ 1, z ≈ 0.
        _ok(abs(r.aim_stage[0]) < 1e-3,
            "#862 sym2 aim_stage.x ≈ 0 (no calibrate-end swing)",
            f"got x={r.aim_stage[0]:.4f}")
        _ok(r.aim_stage[1] > 0.99,
            "#862 sym2 aim_stage.y ≈ 1 (calibrate target on +Y)",
            f"got y={r.aim_stage[1]:.4f}")


def test_862_symptom3_8bit_profile_tilt_tracks_claim():
    """#862 symptom #3 — for an 8-bit moving-head profile (live fid=17
    shape), wire tilt MUST track `claim.tilt_smooth` across the full
    0..1 range. The operator-reported failure was wire.tilt frozen at
    128 (channel default) while pan tracked correctly. This cell pins
    the dmx_universe contract — `compute_pan_tilt_writes` is the
    canonical writer and must emit a tilt write for every tilt value."""
    print("\n-- test_862_symptom3_8bit_profile_tilt_tracks_claim --")
    from dmx_universe import compute_pan_tilt_writes

    profile_8bit = {
        "channel_map": {"pan": 0, "tilt": 1, "dimmer": 2,
                        "red": 3, "green": 4, "blue": 5},
        "channels": [
            {"type": "pan",    "offset": 0, "bits": 8, "default": 128},
            {"type": "tilt",   "offset": 1, "bits": 8, "default": 128},
            {"type": "dimmer", "offset": 2, "bits": 8, "default": 0},
        ],
    }
    # Boundary + interior values. Pre-#862 the issue suspected tilt was
    # being skipped; we assert it lands at every value including 0 / 1.
    cases = [
        (0.0,  0),
        (0.25, 63),
        (0.5,  127),
        (0.75, 191),
        (1.0,  255),
    ]
    for tilt, expected_byte in cases:
        writes = compute_pan_tilt_writes(0.4202, tilt, profile_8bit)
        offsets = {off: val for off, val in writes}
        _ok(1 in offsets,
            f"#862 sym3 8-bit profile emits tilt write at offset 1 "
            f"for tilt_smooth={tilt:.2f}",
            f"writes={writes}")
        if 1 in offsets:
            _ok(offsets[1] == expected_byte,
                f"#862 sym3 tilt_smooth={tilt:.2f} → wire={expected_byte}",
                f"got {offsets[1]}")


def _setup_vertical_bar(c, name, x_mm, leds=100, length_mm=2000):
    """#865 — register a single-string LED bar at stage X=x_mm with the
    string oriented along stage +Z (rotation [+90, 0, 0] tilts an sdir=1
    +Y strip up to +Z). Returns the new fixture id."""
    r = c.post("/api/fixtures", json={
        "name": name, "type": "linear", "fixtureType": "led",
        "strings": [{"leds": leds, "mm": length_mm, "sdir": 1}],
        "rotation": [90, 0, 0],
    })
    fid = r.get_json()["id"]
    parent_server._layout.setdefault("children", []).append(
        {"id": fid, "x": x_mm, "y": 3000, "z": 0})
    return fid


def _gen_bar_array_show(c, bar_xs):
    """Build a layout with one bar per supplied X, then ask the
    show-generator directly (skips the bake/playback cost). Returns the
    raw generator output dict — None if generate_show refused."""
    fids = []
    for i, x in enumerate(bar_xs):
        fids.append(_setup_vertical_bar(c, f"bar{i}", x))
    from show_generator import generate_show
    show = generate_show("vertical-bar-array",
                          parent_server._fixtures,
                          parent_server._layout,
                          parent_server._stage,
                          parent_server._profile_lib)
    return show, fids


def test_865_bar_array_4_bars_emits_seven_clips():
    """#865 — four-bar layout produces exactly 7 named clips on the
    effects track. The detector must accept all four bars (they meet the
    leds≥75 + Z-dominant heuristic)."""
    print("\n-- test_865_bar_array_4_bars_emits_seven_clips --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        show, fids = _gen_bar_array_show(c, [1000, 2500, 4000, 5500])

        _ok(isinstance(show, dict) and "tracks" in show,
            "#865 generator returns a show dict for 4-bar layout",
            f"got {type(show).__name__}: {show!r}"[:200])
        _ok(show.get("_865_bar_ids") == fids,
            "#865 detector picked up all 4 bars",
            f"detected={show.get('_865_bar_ids')!r}")
        # Effects-layer track is the one with allPerformers + 7 clips.
        eff_track = next((t for t in show.get("tracks", [])
                          if t.get("allPerformers")
                          and t.get("_layer") == "effects"), None)
        _ok(eff_track is not None and len(eff_track.get("clips", [])) == 7,
            "#865 timeline emits 7 sequenced effects-track clips",
            f"clips={(eff_track or {}).get('clips')}")
        names = [c.get("name") for c in (eff_track or {}).get("clips", [])]
        expected = ["Cross-Stage Sweep", "Vertical Climb", "Mexican Wave",
                    "Strobe Shimmer", "Lightning Strikes", "Color Cascade",
                    "Stack-Builder"]
        _ok(names == expected,
            "#865 catalog names match the spec",
            f"got {names!r}")


def test_865_bar_array_2_bars_lower_bound():
    """#865 — two-bar layout still produces the full 7-clip catalog
    (the lower bound the spec pins). Cross-sweep clip's effect motion
    starts on stage-left side, ends on stage-right side; per-bar
    sphere-field intensity peaks correlate with stage-X."""
    print("\n-- test_865_bar_array_2_bars_lower_bound --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        show, fids = _gen_bar_array_show(c, [1000, 5000])

        _ok(isinstance(show, dict) and len(show.get("_865_bar_ids", [])) == 2,
            "#865 2-bar layout still detected",
            f"detected={(show or {}).get('_865_bar_ids')!r}")
        eff = next((t for t in show.get("tracks", [])
                     if t.get("_layer") == "effects"), None)
        _ok(eff and len(eff.get("clips", [])) == 7,
            "#865 2-bar lower bound: still 7 clips",
            f"clips={(eff or {}).get('clips')}")

        # Cross-sweep timing assertion. Bar A is at x=1000, bar B at
        # x=5000. The cross-sweep effect's sphere centre travels from
        # the start X to the end X linearly across the clip duration.
        # Sample the sphere field against each bar's pixels at evenly
        # spaced t and identify the bar whose mean intensity peaks
        # earliest. That bar must be the one at lower stage-X.
        from spatial_engine import (resolve_fixture as _rf,
                                    sphere_field_evaluate as _sphere)
        cross = next((c2 for c2 in eff["clips"]
                       if c2.get("name") == "Cross-Stage Sweep"), None)
        _ok(cross is not None, "#865 Cross-Stage Sweep clip present")
        if cross:
            fx_id = id(cross["_effect_ref"])
            fx = next((f for f in show["effects"] if id(f) == fx_id), None)
            _ok(fx is not None, "#865 cross-sweep effect resolvable")
            if fx:
                start = fx["motion"]["startPos"]
                end = fx["motion"]["endPos"]
                rad = fx["size"]["radius"]
                color = [fx["r"], fx["g"], fx["b"]]
                pos_map = {p["id"]: p for p in
                           parent_server._layout.get("children", [])}
                bar_pixels = []
                for fid in fids:
                    fixture = next(f for f in parent_server._fixtures
                                    if f["id"] == fid)
                    p = pos_map[fid]
                    px = _rf({
                        "type": "linear",
                        "childPos": [p["x"], p["y"], p["z"]],
                        "strings": fixture["strings"],
                        "rotation": fixture["rotation"],
                    })["pixelPositions"]
                    bar_pixels.append((p["x"], px))
                bar_pixels.sort(key=lambda r: r[0])  # left-to-right

                samples = 41
                peak_t = []
                for _bx, px in bar_pixels:
                    best_v = -1
                    best_t = 0.0
                    for s in range(samples):
                        t = s / (samples - 1)
                        cx = start[0] + (end[0] - start[0]) * t
                        out = _sphere([cx, start[1], start[2]],
                                       rad, px, color, falloff=True)
                        mean = sum(p[0] for p in out) / max(1, len(out))
                        if mean > best_v:
                            best_v = mean
                            best_t = t
                    peak_t.append(best_t)
                # Left bar peaks earlier than right bar.
                _ok(peak_t[0] < peak_t[1] - 0.02,
                    "#865 cross-sweep per-bar peak time correlates with X",
                    f"peak_t={peak_t}")


def test_865_bar_array_install_post_returns_timeline():
    """#865 — POST /api/show/preset must materialise the timeline,
    not just return success silently. Asserts: 200 status, ok=True,
    timelineId returned, ≥1 actions and ≥1 effects (the operator's
    Runtime tab needs records to play). The operator-reported symptom
    was "select preset → nothing happens" — a 200 response with zero
    records produced exactly that symptom; the assertion below pins it."""
    print("\n-- test_865_bar_array_install_post_returns_timeline --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        for i, x in enumerate([1000, 3000, 5000, 7000]):
            _setup_vertical_bar(c, f"bar{i}", x)
        r = c.post("/api/show/preset", json={"id": "vertical-bar-array"})
        body = r.get_json()
        _ok(r.status_code == 200,
            "#865 install POST returns 200",
            f"status={r.status_code} body={body}")
        _ok(bool(body and body.get("ok")) and body.get("timelineId") is not None,
            "#865 install creates a timeline (timelineId returned)",
            f"body={body}")
        _ok((body or {}).get("actions", 0) >= 1,
            "#865 install creates ≥1 action records",
            f"actions={(body or {}).get('actions')}")
        _ok((body or {}).get("effects", 0) >= 1,
            "#865 install creates ≥1 spatial-effect records",
            f"effects={(body or {}).get('effects')}")


def test_865_bar_array_install_under_two_bars_returns_400():
    """#865 — operator-visible failure path. The SPA's loadPreset
    only shows an error when the response says ok:false. A silent 200
    with an empty timeline would look like "nothing happened." This
    test pins the 400 + ok:false + needs_bars contract end-to-end so
    the SPA's error path is reachable."""
    print("\n-- test_865_bar_array_install_under_two_bars_returns_400 --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        _setup_vertical_bar(c, "lonely", 2000)   # 1 bar only
        r = c.post("/api/show/preset", json={"id": "vertical-bar-array"})
        body = r.get_json()
        _ok(r.status_code == 400,
            "#865 1-bar install returns HTTP 400",
            f"status={r.status_code} body={body}")
        _ok(body and body.get("ok") is False
              and body.get("code") == "needs_bars",
            "#865 1-bar install body has ok:false + code:needs_bars",
            f"body={body}")
        _ok(body and "vertical LED bars" in (body.get("err") or ""),
            "#865 needs_bars message names the requirement",
            f"err={(body or {}).get('err')!r}")


def test_865_bar_array_multi_string_fixture_detected_per_string():
    """#865 — operator-reported failure mode. A single LED fixture
    driving multiple physical bars (one ESP32, several strips) must
    surface each string as a separate bar so the catalog generates.
    This cell pins the #864 ↔ #865 integration: each string's per-string
    (x, y, z) anchor is the bar position; the cross-sweep effect's
    per-anchor peak time correlates with the per-string X exactly the
    same way the multi-fixture cell asserts."""
    print("\n-- test_865_bar_array_multi_string_fixture_detected_per_string --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        # ONE fixture, FOUR strings, each at a distinct stage-X.
        r = c.post("/api/fixtures", json={
            "name": "QuadBar", "type": "linear", "fixtureType": "led",
            "strings": [
                {"leds": 100, "mm": 2000, "sdir": 1,
                 "x": 1000, "y": 3000, "z": 0},
                {"leds": 100, "mm": 2000, "sdir": 1,
                 "x": 3000, "y": 3000, "z": 0},
                {"leds": 100, "mm": 2000, "sdir": 1,
                 "x": 5000, "y": 3000, "z": 0},
                {"leds": 100, "mm": 2000, "sdir": 1,
                 "x": 7000, "y": 3000, "z": 0},
            ],
            "rotation": [90, 0, 0],
        })
        fid = r.get_json()["id"]
        parent_server._layout.setdefault("children", []).append(
            {"id": fid, "x": 0, "y": 3000, "z": 0})

        from show_generator import generate_show
        show = generate_show("vertical-bar-array",
                              parent_server._fixtures,
                              parent_server._layout,
                              parent_server._stage,
                              parent_server._profile_lib)
        _ok(isinstance(show, dict) and not show.get("error"),
            "#865 4-string single-fixture detected (no error)",
            f"got {show!r}"[:300])
        entries = (show or {}).get("_865_bar_entries", [])
        _ok(len(entries) == 4,
            "#865 detector enumerates 4 per-string bar entries",
            f"got {len(entries)} entries")
        anchors = sorted(e["anchor"][0] for e in entries)
        _ok(anchors == [1000, 3000, 5000, 7000],
            "#865 per-string anchors carry the per-string X positions",
            f"got {anchors}")

        # Install path runs end-to-end too.
        r = c.post("/api/show/preset", json={"id": "vertical-bar-array"})
        body = r.get_json()
        _ok(r.status_code == 200 and body.get("ok"),
            "#865 single-fixture-multi-string install path returns ok",
            f"status={r.status_code} body={body}")


def test_865_bar_array_bake_every_clip_drives_every_bar():
    """#865 — confirm LEDs PARTICIPATE in all 7 catalog clips after the
    install-and-bake pipeline runs. Pre-fix the Lightning Strikes and
    Stack-Builder slots only referenced sub-effect[0], so 3 of 4
    lightning sub-effects and 3 of 4 stack sub-effects never reached
    the bake — the slots ran one strike + one ground-floor box and
    silently dropped the rest. This test bakes the installed timeline
    and asserts every LED bar fixture has ≥1 segment overlapping every
    catalog slot's time window."""
    print("\n-- test_865_bar_array_bake_every_clip_drives_every_bar --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        fids = []
        for i, x in enumerate([1000, 3000, 5000, 7000]):
            fids.append(_setup_vertical_bar(c, f"bar{i}", x))

        r = c.post("/api/show/preset", json={"id": "vertical-bar-array"})
        body = r.get_json()
        _ok(r.status_code == 200 and body.get("ok"),
            "#865 install path returns ok",
            f"status={r.status_code} body={body}")
        tid = body["timelineId"]

        # Drive the bake straight through bake_timeline so we assert
        # against compiled segments rather than the cached LSQ blob.
        from bake_engine import bake_timeline
        tl = next(t for t in parent_server._timelines if t["id"] == tid)
        baked = bake_timeline(
            tl,
            parent_server._fixtures,
            parent_server._spatial_fx,
            parent_server._layout,
            actions=parent_server._actions,
            profile_lib=parent_server._profile_lib,
        )

        # Catalog slots — must mirror the generator's clip layout.
        slots = [
            (0,  8,  "Cross-Stage Sweep"),
            (8,  16, "Vertical Climb"),
            (16, 24, "Mexican Wave"),
            (24, 32, "Strobe Shimmer"),
            (32, 40, "Lightning Strikes"),
            (40, 48, "Color Cascade"),
            (48, 56, "Stack-Builder"),
        ]

        for fid in fids:
            fx_data = baked["fixtures"].get(fid) or {}
            segs = fx_data.get("segments") or []
            for slot_start, slot_end, slot_name in slots:
                hit = any(
                    s.get("startS", 0) < slot_end
                    and s.get("startS", 0) + s.get("durationS", 0) > slot_start
                    for s in segs
                )
                _ok(hit,
                    f"#865 fixture {fid} has a segment in '{slot_name}' "
                    f"slot [{slot_start},{slot_end})",
                    f"segs={[(round(s.get('startS',0),1), round(s.get('durationS',0),1)) for s in segs]}")


def test_template_sweep_every_theme_drives_leds():
    """Sweep every preset in show_generator.THEMES on a mixed rig
    (movers + LED bars + LED par strip) and assert each LED fixture
    produces ≥1 baked segment. Catches themes whose generator branch
    forgets the LED layer — operator-visible symptom is "preset loads
    but the LED strips stay dark." Fails loud per-theme so a regression
    in any branch (live_track / ribbon / bar_array / normal) is named.
    """
    print("\n-- test_template_sweep_every_theme_drives_leds --")
    from show_generator import THEMES, generate_show
    from bake_engine import bake_timeline

    skip = set()  # populated below if we discover themes that need
                  # other-than-mixed rigs and document them explicitly.

    for theme_id in list(THEMES.keys()):
        if theme_id in skip:
            continue
        with parent_server.app.test_client() as c:
            c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
            # Mixed rig: 2 vertical LED bars (covers bar_array's bar
            # heuristic), 1 LED par strip (covers normal LED path), 2
            # DMX movers (lets ribbon / live_track branches activate).
            led_bar_a = _setup_vertical_bar(c, "swp-bar-A", x_mm=1500)
            led_bar_b = _setup_vertical_bar(c, "swp-bar-B", x_mm=4500)
            r = c.post("/api/fixtures", json={
                "name": "swp-par-strip", "type": "linear",
                "fixtureType": "led",
                "strings": [{"leds": 32, "mm": 800, "sdir": 0}],
                "rotation": [0, 0, 0],
            })
            led_par = r.get_json()["id"]
            parent_server._layout.setdefault("children", []).append(
                {"id": led_par, "x": 3000, "y": 1500, "z": 0})

            mover_a = _setup_mover_fixture(c, "swp-mover-A", addr=1)
            mover_b = _setup_mover_fixture(c, "swp-mover-B", addr=20)

            led_ids = [led_bar_a, led_bar_b, led_par]

            # Install path — surfaces install errors directly.
            r = c.post("/api/show/preset", json={"id": theme_id})
            body = r.get_json()
            ok_install = (r.status_code == 200
                          and body and body.get("ok"))
            _ok(ok_install,
                f"theme '{theme_id}' install path returns ok",
                f"status={r.status_code} body={body}")
            if not ok_install:
                continue

            # Bake the freshly-installed timeline and inspect per-LED
            # segments. The bake's own "fixtures" map keys by fixture id.
            tid = body["timelineId"]
            tl = next(t for t in parent_server._timelines if t["id"] == tid)
            try:
                baked = bake_timeline(
                    tl,
                    parent_server._fixtures,
                    parent_server._spatial_fx,
                    parent_server._layout,
                    actions=parent_server._actions,
                    profile_lib=parent_server._profile_lib,
                )
            except Exception as e:
                _ok(False,
                    f"theme '{theme_id}' bake raised {type(e).__name__}: {e}",
                    "")
                continue

            for fid in led_ids:
                segs = (baked["fixtures"].get(fid) or {}).get("segments") or []
                _ok(bool(segs),
                    f"theme '{theme_id}' produces ≥1 segment for LED {fid}",
                    f"fixture {fid} got 0 segments — LEDs would be dark "
                    f"under this preset")
                # Tighter check: at least one segment must be a non-track
                # (type 18) action. Type 18 is the live-tracking primitive
                # the runtime DMX loop evaluates for movers only — on an
                # LED fixture it carries no r/g/b drive, so an LED that
                # has ONLY a type-18 segment would still sit dark even
                # though `len(segs) > 0`. The original live_track branch
                # in show_generator emitted exactly this: a single
                # allPerformers Track action and nothing else.
                non_track = [s for s in segs if s.get("type") != 18]
                _ok(bool(non_track),
                    f"theme '{theme_id}' LED {fid} has a non-Track-action "
                    f"segment (something that actually drives r/g/b)",
                    f"all {len(segs)} segments are type 18 (ACT_TRACK) — "
                    f"LEDs would not light under this preset")


def test_865_bar_array_rejects_when_under_two_bars():
    """#865 — generator returns the structured 'needs_bars' error for
    rigs with 0 or 1 bar so the SPA can show a clear message instead of
    materialising a degenerate timeline."""
    print("\n-- test_865_bar_array_rejects_when_under_two_bars --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        # 0 bars
        from show_generator import generate_show
        show = generate_show("vertical-bar-array",
                              parent_server._fixtures,
                              parent_server._layout,
                              parent_server._stage,
                              parent_server._profile_lib)
        _ok(show is None
              or (isinstance(show, dict) and not show.get("error")),
            "#865 0-fixture rig falls back to base wash (no error)",
            f"got {show!r}"[:200])

        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        _setup_vertical_bar(c, "lonely", 2000)
        show1 = generate_show("vertical-bar-array",
                               parent_server._fixtures,
                               parent_server._layout,
                               parent_server._stage,
                               parent_server._profile_lib)
        _ok(isinstance(show1, dict)
              and show1.get("error") == "needs_bars",
            "#865 1-bar rig refused with needs_bars error",
            f"got {show1!r}"[:200])


def test_v205_baked_sync_empty_rig_contract():
    """v2.0.5 regression — the SPA's _rtBakeAll chains a sync between
    bake and /api/show/start. When no children are registered (or no
    fixture has a real LED childId), the sync API takes an early-return
    that bypasses the _sync_progress dict entirely. The SPA must be able
    to detect that path from the POST response alone, because polling
    /sync/status would return {done:False} forever and hang the start
    chain (the v2.0.5 → v2.0.6 fix).

    Contract this test pins:
      - empty-rig POST /api/timelines/<tid>/baked/sync returns 200 with
        `ok=True` AND `synced` as a number — that combination is the
        unambiguous signal "no async progress, do not poll".
      - non-empty (has a child with IP) returns `ok=True` with a
        `performers` count instead (the async path).
    """
    print("\n-- test_v205_baked_sync_empty_rig_contract --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        # Minimal timeline so a bake result exists when sync runs.
        r = c.post("/api/timelines", json={"name": "empty-rig", "durationS": 5})
        tid = r.get_json()["id"]
        # Inject a synthetic bake result so sync can't 404.
        parent_server._bake_result[tid] = {
            "timelineId": tid, "bakedAt": int(time.time()),
            "fixtures": {}, "totalFrames": 0, "fps": 40,
        }

        # Case A: no children registered → empty-rig early return.
        before = list(parent_server._children)
        parent_server._children[:] = []
        try:
            r = c.post(f"/api/timelines/{tid}/baked/sync")
            _ok(r.status_code == 200,
                "v2.0.6 empty-rig sync returns 200",
                f"got {r.status_code}")
            body = r.get_json()
            _ok(body.get("ok") is True,
                "v2.0.6 empty-rig sync body has ok=True",
                f"got {body!r}")
            _ok(isinstance(body.get("synced"), int),
                "v2.0.6 empty-rig sync body has integer `synced` "
                "(SPA signal: skip /sync/status polling)",
                f"got {body!r}")
        finally:
            parent_server._children[:] = before

        c.delete(f"/api/timelines/{tid}")
        parent_server._bake_result.pop(tid, None)


def test_v205_children_duplicate_response_has_type():
    """v2.0.5 fix — duplicate POST /api/children must return `type` so
    setup-ui.js's `_submitAddFixture` can tell a re-added DMX bridge
    from a re-added LED child. Pre-fix the duplicate branch returned
    only {ok,id,duplicate} and the SPA fell through to auto-creating an
    LED fixture row for the bridge (the symptom that triggered the
    v2.0.5 patch series in the first place)."""
    print("\n-- test_v205_children_duplicate_response_has_type --")
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        # Seed a fake DMX-typed child so the duplicate path fires.
        parent_server._children.append({
            "id": 99, "ip": "10.99.99.99", "hostname": "SLYC-FAKE",
            "name": "fake-dmx-bridge", "type": "dmx",
            "boardType": "giga-dmx", "status": 0, "sc": 0, "strings": [],
        })
        try:
            r = c.post("/api/children", json={"ip": "10.99.99.99"})
            _ok(r.status_code == 200,
                "duplicate-child POST returns 200",
                f"got {r.status_code}")
            body = r.get_json()
            _ok(body.get("duplicate") is True,
                "duplicate flag is set",
                f"got {body!r}")
            _ok(body.get("type") == "dmx",
                "duplicate response carries type='dmx' so SPA can skip "
                "auto-fixture create",
                f"got type={body.get('type')!r}")
        finally:
            parent_server._children[:] = [
                x for x in parent_server._children if x.get("id") != 99]


def main():
    print("=== Show pipeline regressions (#858) ===")
    test_v205_baked_sync_empty_rig_contract()
    test_v205_children_duplicate_response_has_type()
    test_835_orphan_track_action_does_not_blackout_dimmer()
    test_840_loop_wrap_no_zero_frame()
    test_848_invariant_1_default_rgb_on_press_start()
    test_853_master_grand_master_scales_universe_no_show()
    test_853_master_does_not_scale_pan_tilt()
    test_860_slymovehead_geometry_orient_to_pan_smooth()
    test_845_playback_writes_first_frame_under_300ms()
    test_862_symptom1_press_start_seeds_default_dimmer()
    test_862_symptom2_calibrate_end_does_not_swing_head()
    test_862_symptom3_8bit_profile_tilt_tracks_claim()
    test_865_bar_array_4_bars_emits_seven_clips()
    test_865_bar_array_2_bars_lower_bound()
    test_865_bar_array_install_post_returns_timeline()
    test_865_bar_array_install_under_two_bars_returns_400()
    test_865_bar_array_multi_string_fixture_detected_per_string()
    test_865_bar_array_bake_every_clip_drives_every_bar()
    test_865_bar_array_rejects_when_under_two_bars()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
