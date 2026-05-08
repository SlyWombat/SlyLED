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


def main():
    print("=== Show pipeline regressions (#858) ===")
    test_835_orphan_track_action_does_not_blackout_dimmer()
    test_840_loop_wrap_no_zero_frame()
    test_848_invariant_1_default_rgb_on_press_start()
    test_845_playback_writes_first_frame_under_300ms()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
