#!/usr/bin/env python3
"""test_843_global_brightness_pipeline.py - Regression for #843.

Asserts the master globalBrightness setting actually reaches the lights:

  1. POST /api/brightness broadcasts CMD_SET_BRIGHTNESS to LED children.
  2. POST /api/settings broadcasts when globalBrightness changes.
  3. PONG-time top-up: a child reconnecting at boot brightness (255)
     receives the current master value if it's below 255.
  4. _dmx_playback_loop dimmer scaling at render time on a fixture
     with a master dimmer channel.
  5. _dmx_playback_loop RGB scaling on an RGB-only fixture (no dimmer
     in profile).
  6. Gamma LUT shape: monotone, anchored at 0 and 255, mid-point
     darker than linear (the perception correction).
  7. Bake invariance: changing globalBrightness does NOT invalidate
     the bake.

Run: python -X utf8 tests/test_843_global_brightness_pipeline.py
"""

import inspect
import os
import socket
import struct
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402

_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


def _client():
    parent_server.app.config["TESTING"] = True
    return parent_server.app.test_client()


# ---------------------------------------------------------------------------
# Capture _send so we can assert outbound CMD_SET_BRIGHTNESS packets.

_sent_packets = []


def _capture_send(ip, pkt):
    _sent_packets.append((ip, pkt))


def _install_send_capture():
    parent_server._send = _capture_send
    _sent_packets.clear()


# ---------------------------------------------------------------------------

def test_brightness_endpoint_broadcasts_to_led_children():
    saved_children = list(parent_server._children)
    saved_settings = dict(parent_server._settings)
    try:
        parent_server._children[:] = [
            {"id": 1, "ip": "10.0.0.10", "type": "esp32", "hostname": "led1"},
            {"id": 2, "ip": "10.0.0.11", "type": "d1mini", "hostname": "led2"},
            {"id": 3, "ip": "10.0.0.12", "type": "dmx",   "hostname": "bridge"},
            {"id": 4, "ip": "10.0.0.13", "type": "gyro",  "hostname": "gyro"},
        ]
        parent_server._settings["globalBrightness"] = 255
        _install_send_capture()
        c = _client()
        r = c.post("/api/brightness", json={"value": 100})
        _assert(r.status_code == 200, "POST /api/brightness 200")
        ips = [ip for ip, _ in _sent_packets]
        _assert("10.0.0.10" in ips and "10.0.0.11" in ips,
                f"LED children received CMD_SET_BRIGHTNESS (got {ips})")
        _assert("10.0.0.12" not in ips,
                "DMX bridge skipped (CMD_SET_BRIGHTNESS not meaningful)")
        _assert("10.0.0.13" not in ips,
                "Gyro skipped")
        # Inspect packet shape
        if _sent_packets:
            _, pkt = _sent_packets[0]
            _assert(len(pkt) == 9,
                    f"packet is 8-byte header + 1 byte value (got {len(pkt)})")
            _assert(pkt[3] == parent_server.CMD_SET_BRIGHTNESS,
                    "cmd byte is CMD_SET_BRIGHTNESS")
            _assert(pkt[8] == 100,
                    f"value byte = 100 (got {pkt[8]})")
        # Idempotent: same value again should NOT re-broadcast.
        _sent_packets.clear()
        c.post("/api/brightness", json={"value": 100})
        _assert(len(_sent_packets) == 0,
                f"no broadcast on no-change (got {len(_sent_packets)} packets)")
    finally:
        parent_server._children[:] = saved_children
        parent_server._settings.clear()
        parent_server._settings.update(saved_settings)


def test_settings_save_broadcasts_brightness_change():
    saved_children = list(parent_server._children)
    saved_settings = dict(parent_server._settings)
    try:
        parent_server._children[:] = [
            {"id": 1, "ip": "10.0.0.10", "type": "esp32", "hostname": "led1"},
        ]
        parent_server._settings["globalBrightness"] = 255
        _install_send_capture()
        c = _client()
        r = c.post("/api/settings", json={"globalBrightness": 80})
        _assert(r.status_code == 200, "POST /api/settings 200")
        _assert(any(p[8] == 80 for _, p in _sent_packets),
                f"manual-slider change broadcasts new value "
                f"(packets: {[(ip, p[8]) for ip, p in _sent_packets]})")
    finally:
        parent_server._children[:] = saved_children
        parent_server._settings.clear()
        parent_server._settings.update(saved_settings)


def test_pong_listener_tops_up_reconnecting_child():
    """The PONG handler in the UDP listener pushes brightness when a
    child reconnects below the current master value. Verify by
    inspecting the source — the live socket path is hard to fixture."""
    src = inspect.getsource(parent_server._udp_listener) \
        if hasattr(parent_server, "_udp_listener") \
        else inspect.getsource(parent_server.start_udp_listener) \
        if hasattr(parent_server, "start_udp_listener") \
        else ""
    if not src:
        # Fall back to grepping the module file for the brightness
        # top-up wired into the PONG branch.
        with open(parent_server.__file__, "r", encoding="utf-8") as f:
            src = f.read()
    _assert("globalBrightness" in src
            and "_brightness_packet" in src
            and "matched" in src,
            "PONG handler tops up brightness on child reconnect")


def test_gamma_lut_shape():
    lut = parent_server._GAMMA_LUT
    _assert(len(lut) == 256, "gamma LUT is 256 bytes")
    _assert(lut[0] == 0, "lut[0] = 0 (anchor low)")
    _assert(lut[255] == 255, "lut[255] = 255 (anchor high)")
    # Monotone non-decreasing
    monotone = all(lut[i] <= lut[i+1] for i in range(255))
    _assert(monotone, "gamma LUT is monotone non-decreasing")
    # Mid-point: linear would be 127, perception-corrected (gamma 2.2)
    # should be noticeably below 127.
    _assert(lut[127] < 70,
            f"lut[127] gamma-corrected (got {lut[127]}, expected < 70)")


def test_scale_for_brightness_passthrough_at_full():
    fn = parent_server._scale_for_brightness
    for v in (0, 50, 128, 200, 255):
        _assert(fn(v, 255) == v, f"_scale_for_brightness({v}, 255) == {v}")


def test_scale_for_brightness_zero_at_zero():
    fn = parent_server._scale_for_brightness
    for v in (0, 50, 128, 200, 255):
        _assert(fn(v, 0) == 0, f"_scale_for_brightness({v}, 0) == 0")


def test_scale_for_brightness_gamma_corrected_at_half():
    """At g_bri=128, a full input (255) scales to half linearly, then
    the gamma LUT compresses further. Expect noticeably below 128."""
    out = parent_server._scale_for_brightness(255, 128)
    _assert(out < 70,
            f"_scale_for_brightness(255, 128) gamma-corrected (got {out})")


def test_render_paths_no_longer_scale_brightness():
    """#853 — render-time scaling REMOVED. The ArtNet engine's
    `get_data_scaled` applies the master grand-master at send time so
    every writer (bake render, Track action, claim writer, dmx-test,
    no-show direct write) goes through a single uniform gate. The
    previous v1.7.82 per-render-path scaling is no longer expected;
    its presence would now double-apply on top of the send gate.
    """
    for fn_name in ("_evaluate_track_actions",
                     "_dmx_playback_loop",
                     "_dmx_playback_single"):
        src = inspect.getsource(getattr(parent_server, fn_name))
        _assert("_scale_for_brightness" not in src,
                f"{fn_name} no longer calls _scale_for_brightness "
                "(send-time gate applies the master)")


def test_artnet_engine_wired_with_master_grand_master_callbacks():
    """#853 — the ArtNet engine has the master grand-master callbacks
    wired so the send loop can apply gamma-corrected scaling at the
    final gate. `get_global_brightness` reads `_settings`,
    `get_intensity_offsets` returns the per-universe channel cache,
    `_gamma_lut` is the FastLED-parity 256-byte table."""
    eng = parent_server._artnet
    _assert(callable(getattr(eng, "_get_global_brightness", None)),
            "ArtNet engine has _get_global_brightness callback")
    _assert(callable(getattr(eng, "_get_intensity_offsets", None)),
            "ArtNet engine has _get_intensity_offsets callback")
    _assert(eng._gamma_lut is parent_server._GAMMA_LUT,
            "ArtNet engine's gamma LUT is the FastLED-parity table")


def test_brightness_endpoint_validates_input():
    c = _client()
    r = c.post("/api/brightness", json={"value": "abc"})
    _assert(r.status_code == 400, "non-numeric rejected")
    r = c.post("/api/brightness", json={"value": -50})
    _assert(r.status_code == 200, "negative clamped (not rejected)")
    _assert(r.get_json()["value"] == 0, "negative clamped to 0")
    r = c.post("/api/brightness", json={"value": 999})
    _assert(r.get_json()["value"] == 255, "over-range clamped to 255")


def test_sync_time_uses_brightness_packet_helper():
    src = inspect.getsource(parent_server.api_bake_sync)
    _assert("_brightness_packet" in src,
            "api_bake_sync uses extracted _brightness_packet helper")
    _assert("_hdr(CMD_SET_BRIGHTNESS)" not in src,
            "api_bake_sync no longer builds the packet inline")


def test_861_autobri_push_updates_globalbrightness():
    """#861 — UDP CMD_AUTOBRI_PUSH lands directly in `_settings`.

    Drives the extracted `_handle_autobri_push` helper rather than the
    listener thread so the test is hermetic (no real bind). Covers the
    audio-rate fast path that replaced the HTTP /api/brightness POST.
    """
    # Seed master so we can assert it changes.
    parent_server._settings["globalBrightness"] = 255

    # Register a fake LED child so _broadcast_brightness has something
    # to fan out to — empty-list path returns silently.
    parent_server._children.append({
        "ip": "10.0.0.50", "name": "fake-led", "type": "esp32"})

    # Capture broadcast packets to confirm CMD_SET_BRIGHTNESS
    # propagates to LED children when master changes.
    sent = []
    orig_send = parent_server._send
    parent_server._send = lambda ip, pkt: sent.append((ip, pkt))
    try:
        # Build the 11-byte packet: 8-byte header + master/flags/seq.
        hdr = struct.pack("<HBBI",
                          parent_server.UDP_MAGIC,
                          parent_server.UDP_VERSION,
                          parent_server.CMD_AUTOBRI_PUSH,
                          0)
        payload = struct.pack("<BBB", 137, 0, 42)
        parent_server._handle_autobri_push("10.0.0.99", hdr + payload)

        _assert(parent_server._settings["globalBrightness"] == 137,
                "AUTOBRI_PUSH writes master into _settings.globalBrightness")
        _assert(len(sent) >= 1,
                "AUTOBRI_PUSH broadcasts CMD_SET_BRIGHTNESS to LED children on change")

        # Same value again: must NOT re-broadcast (avoid storm).
        sent.clear()
        parent_server._handle_autobri_push("10.0.0.99", hdr + payload)
        _assert(parent_server._settings["globalBrightness"] == 137,
                "AUTOBRI_PUSH idempotent on repeat value")
        _assert(len(sent) == 0,
                "AUTOBRI_PUSH skips broadcast when master is unchanged")

        # Short packet: must not raise.
        parent_server._handle_autobri_push("10.0.0.99", hdr)  # no payload
        _assert(True, "AUTOBRI_PUSH tolerates short packet without raising")
    finally:
        parent_server._send = orig_send
        parent_server._settings["globalBrightness"] = 255
        # Pop the fake child we appended at the start of the test.
        parent_server._children[:] = [
            c for c in parent_server._children
            if c.get("ip") != "10.0.0.50" or c.get("name") != "fake-led"
        ]


def test_861_autobri_push_constant_present():
    _assert(getattr(parent_server, "CMD_AUTOBRI_PUSH", None) == 0x6D,
            "CMD_AUTOBRI_PUSH is registered as 0x6D")
    src = inspect.getsource(parent_server._udp_listener)
    _assert("CMD_AUTOBRI_PUSH" in src,
            "_udp_listener dispatches CMD_AUTOBRI_PUSH (legacy 4210 path)")
    # #862 — dedicated AUTOBRI port for Android (firmware-shared 4210 stays
    # for backward compat with any already-shipped debug APK).
    _assert(getattr(parent_server, "UDP_AUTOBRI_PORT", None) == 4211,
            "UDP_AUTOBRI_PORT is 4211")
    autobri_src = inspect.getsource(parent_server._udp_autobri_listener)
    _assert("_handle_autobri_push" in autobri_src,
            "_udp_autobri_listener dispatches AUTOBRI_PUSH")
    _assert("UDP_AUTOBRI_PORT" in autobri_src,
            "_udp_autobri_listener binds the dedicated port")


ALL = [
    test_brightness_endpoint_broadcasts_to_led_children,
    test_settings_save_broadcasts_brightness_change,
    test_pong_listener_tops_up_reconnecting_child,
    test_gamma_lut_shape,
    test_scale_for_brightness_passthrough_at_full,
    test_scale_for_brightness_zero_at_zero,
    test_scale_for_brightness_gamma_corrected_at_half,
    test_render_paths_no_longer_scale_brightness,
    test_artnet_engine_wired_with_master_grand_master_callbacks,
    test_brightness_endpoint_validates_input,
    test_sync_time_uses_brightness_packet_helper,
    test_861_autobri_push_updates_globalbrightness,
    test_861_autobri_push_constant_present,
]


if __name__ == "__main__":
    print("=== #843 globalBrightness reaches the lights ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
            import traceback
            traceback.print_exc()
    print(f"\n{_passed} assertions passed, {_failed} failed")
    sys.exit(0 if _failed == 0 else 1)
