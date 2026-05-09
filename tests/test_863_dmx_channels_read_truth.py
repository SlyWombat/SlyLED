"""#863 — /api/dmx/fixture/<fid>/channels must report the real wire value
when an engine is running, including 0.

Pre-fix the endpoint substituted the profile's `default` whenever the
buffer held 0 — even with the Art-Net engine actively driving the
universe. The intent was UX safety (show *something* when nothing's
running), but the gate was on `val == 0` instead of "engine not
running", so every legitimate 0 came back as `default`. QA pollers
built on this endpoint silently masked claim-freeze and wire-stuck-at-0
diagnostics (this is the read-side artifact one of #862's symptoms
reflected; #862 was filed against poller readings, not the wire).

Coverage:
* Engine running, channel set to 0 on a profile with default > 0 →
  endpoint reports 0.
* Engine running, channel set to a non-zero value → endpoint reports
  that value (regression check that the fix didn't break the truth
  path).
* Engine NOT running → endpoint still reports the profile default
  (legacy idle-state UX preserved).
"""

import os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server


def _setup_fixture(c, profile_id="movinghead-150w-12ch", addr=1, uni=1):
    """Create a DMX mover fixture so we have a real profile-backed
    channel list (movinghead-150w-12ch declares non-zero defaults)."""
    r = c.post("/api/fixtures", json={
        "name": "863-mover", "type": "point", "fixtureType": "dmx",
        "dmxUniverse": uni, "dmxStartAddr": addr,
        "dmxChannelCount": 12, "dmxProfileId": profile_id,
        "rotation": [0, 0, 0],
    })
    return r.get_json()["id"]


def _channel_value(c, fid, ch_type):
    body = c.get(f"/api/dmx/fixture/{fid}/channels").get_json()
    return next(ch["value"] for ch in body["channels"]
                  if ch["type"] == ch_type)


def _channel_default(c, fid, ch_type):
    body = c.get(f"/api/dmx/fixture/{fid}/channels").get_json()
    return next(ch.get("default", 0) for ch in body["channels"]
                  if ch["type"] == ch_type)


def test_863_engine_running_zero_reads_back_as_zero():
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        try:
            fid = _setup_fixture(c)
            # Pick a channel that has a non-zero profile default. dimmer
            # commonly has default 0; pan / colour-wheel often have
            # defaults like 128 or 0. Walk channels for one with
            # default > 0.
            channels = c.get(f"/api/dmx/fixture/{fid}/channels").get_json()["channels"]
            target_ch = next((ch for ch in channels
                                if ch.get("default", 0) > 0), None)
            assert target_ch, "test fixture has no channel with default > 0"
            offset = target_ch["offset"]
            default = target_ch["default"]
            ch_type = target_ch["type"]

            uni = 1
            addr = 1 + offset

            # Write 7 first to confirm the truth path works.
            c.post("/api/dmx/channel",
                   json={"universe": uni, "channel": addr, "value": 7})
            time.sleep(0.05)
            v = _channel_value(c, fid, ch_type)
            assert v == 7, f"truth path broken: got {v} (expected 7)"

            # Now write 0 and confirm it round-trips as 0, NOT as the
            # profile default. This is the #863 regression gate.
            c.post("/api/dmx/channel",
                   json={"universe": uni, "channel": addr, "value": 0})
            time.sleep(0.05)
            v = _channel_value(c, fid, ch_type)
            assert v == 0, (f"#863 regression: channel value 0 reported as "
                              f"{v} (profile default is {default}). The "
                              f"endpoint must report 0 when engine is running "
                              f"and wire is 0.")
        finally:
            c.post("/api/dmx/stop")


def test_863_engine_off_falls_back_to_profile_default():
    """Idle-state UX preserved: with no engine running, the endpoint
    still reports the profile default for a 0 channel so the SPA's
    fixture pane has something to render."""
    with parent_server.app.test_client() as c:
        c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})
        # Make sure no engine is running.
        c.post("/api/dmx/stop")
        fid = _setup_fixture(c)
        channels = c.get(f"/api/dmx/fixture/{fid}/channels").get_json()["channels"]
        # Every channel with default > 0 should report `default` since
        # no engine is driving it.
        for ch in channels:
            d = ch.get("default", 0)
            if d > 0:
                assert ch["value"] == d, (f"engine-off fallback broken: "
                                              f"channel {ch['name']} default={d} "
                                              f"value={ch['value']}")


if __name__ == "__main__":
    test_863_engine_running_zero_reads_back_as_zero()
    test_863_engine_off_falls_back_to_profile_default()
    print("OK — #863 read-truth tests passed")
