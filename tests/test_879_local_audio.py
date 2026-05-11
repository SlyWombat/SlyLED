#!/usr/bin/env python3
"""
test_879_local_audio.py — Test coverage for the #879 local-audio
brightness producer at desktop/shared/local_audio_brightness.py.

Closes the gap surfaced in #882: no audio hardware needed — the
producer talks to ``sounddevice`` only through the ``_sd.InputStream``
class and ``_sd.query_devices()`` / ``_sd.query_hostapis()`` functions,
both of which are mockable.

Usage:
    python tests/test_879_local_audio.py
"""

import math
import os
import struct
import sys
import time
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import local_audio_brightness as lab

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


# Try to load numpy; if it's missing on this runner skip the envelope
# tests rather than crashing — the producer's actual audio path also
# requires numpy via sounddevice, but the module imports without it.
try:
    import numpy as np
    _HAVE_NUMPY = True
except ImportError:
    np = None
    _HAVE_NUMPY = False


def _make_producer(push_log=None):
    """Build a fresh producer with a no-op push callback (the default
    test path doesn't exercise the push loop directly). The caller can
    pass a list and we append each push as a 3-tuple."""
    if push_log is None:
        push_log = []

    def _push(master, flags, seq):
        push_log.append((int(master), int(flags), int(seq)))

    return lab.LocalAudioBrightness(_push), push_log


def _silence_block(frames=512, channels=1):
    """Mono float32 silence block. Audio callback expects an ndarray
    of shape (frames, channels) per sounddevice's contract."""
    if not _HAVE_NUMPY:
        return None
    return np.zeros((frames, channels), dtype=np.float32)


def _tone_block(amplitude=1.0, frames=512, channels=1, freq=440.0,
                sample_rate=22050):
    """Mono float32 sine tone at the given amplitude. Used to drive the
    envelope follower toward a known steady-state value."""
    if not _HAVE_NUMPY:
        return None
    t = np.arange(frames, dtype=np.float32) / float(sample_rate)
    wave = amplitude * np.sin(2 * np.pi * freq * t)
    if channels == 1:
        return wave.reshape(-1, 1)
    return np.tile(wave.reshape(-1, 1), (1, channels))


def run():
    # ── _coef pure-math contract ─────────────────────────────────
    # The one-pole IIR coefficient maps a time-constant in ms to a
    # per-block alpha. Verify the edge cases that drive attack/release
    # selection in _audio_callback.
    c0 = lab._coef(0, 22050, 512)
    ok('_coef(0 ms) returns 1.0 (instant tracking)', c0 == 1.0,
       f'got={c0}')

    c_neg = lab._coef(-5, 22050, 512)
    ok('_coef(negative ms) returns 1.0 (no NaN)', c_neg == 1.0,
       f'got={c_neg}')

    # 5 ms attack at the producer's native block size should give a
    # coefficient > 0.99 (one block is ~23 ms, which is much longer
    # than the 5 ms time-constant, so we reach steady state almost
    # immediately).
    c_attack = lab._coef(5.0, 22050, 512)
    ok('5 ms attack coef ≈ 1.0 (fast tracking)',
       0.99 < c_attack <= 1.0, f'got={c_attack}')

    # 200 ms release at the same block size: each block is ~10 % of
    # the time-constant, so the coefficient should be small (~0.1).
    c_release = lab._coef(200.0, 22050, 512)
    ok('200 ms release coef ≈ 0.1 (slow decay)',
       0.05 < c_release < 0.15, f'got={c_release}')

    # Release MUST be slower than attack so envelope-follower
    # asymmetry holds (rising-edge fast, falling-edge slow).
    ok('attack coef > release coef (asymmetric follower)',
       c_attack > c_release,
       f'attack={c_attack}, release={c_release}')

    # ── Construction works with or without sounddevice ─────────────
    prod, _ = _make_producer()
    ok('producer constructs even when sounddevice may be missing',
       prod is not None)
    ok('producer.is_available matches _SD_OK',
       prod.is_available() == lab._SD_OK)
    status = prod.get_status()
    ok('get_status returns dict with config + envelope keys',
       'config' in status and 'envelope' in status and 'enabled' in status,
       f'keys={list(status.keys())}')
    ok('get_status starts disabled', status['enabled'] is False)
    ok('get_status starts envelope=0.0', status['envelope'] == 0.0)

    # ── Envelope math (numpy required) ────────────────────────────
    if _HAVE_NUMPY:
        # Silence in → envelope stays at 0 (decays toward 0).
        prod, _ = _make_producer()
        for _ in range(100):
            prod._audio_callback(_silence_block(), 512, None, None)
        ok('silence → envelope stays near 0',
           prod._env_value < 1e-6,
           f'env={prod._env_value}')

        # Full-amplitude tone in → envelope climbs toward the RMS of
        # the input × gain. RMS of a unit sine is ~0.707; with the
        # default gain=1.5 the steady state should approach
        # 0.707 * 1.5 ≈ 1.06 but clipped by the 0..1 internal scale
        # (the follower can exceed 1.0 since it's pre-clip — the
        # clipping happens in the pusher).
        prod, _ = _make_producer()
        for _ in range(200):  # plenty of blocks for steady state
            prod._audio_callback(_tone_block(amplitude=1.0), 512, None, None)
        ok('+1.0 sine → envelope ≥ 0.5 (climbs toward gained RMS)',
           prod._env_value >= 0.5,
           f'env={prod._env_value}')

        # Step up then down: attack to a high value, release back to
        # near zero. Capture both transitions to confirm asymmetry.
        prod, _ = _make_producer()
        for _ in range(50):
            prod._audio_callback(_tone_block(amplitude=1.0), 512, None, None)
        env_peak = prod._env_value
        for _ in range(50):
            prod._audio_callback(_silence_block(), 512, None, None)
        env_after_release = prod._env_value
        ok('peak > after-release (release brings envelope down)',
           env_peak > env_after_release,
           f'peak={env_peak} after={env_after_release}')

        # Asymmetry: with release_ms ≫ attack_ms, after 5 release
        # blocks (≈ 115 ms) the envelope should still be well above
        # zero (release tau = 200 ms → ~57 % remaining).
        prod, _ = _make_producer()
        for _ in range(50):
            prod._audio_callback(_tone_block(amplitude=1.0), 512, None, None)
        peak = prod._env_value
        for _ in range(5):  # 5 blocks × ~23 ms = ~115 ms
            prod._audio_callback(_silence_block(), 512, None, None)
        decayed = prod._env_value
        ok('release decay slower than attack: 5 blocks of silence '
           'leaves > 30 % of peak',
           decayed > 0.3 * peak,
           f'peak={peak} after_5_blocks={decayed}')

    # ── Sensitivity remap (push-loop math, extracted for unit test) ─
    # The sensitivity → noise-gate mapping lives in _pusher_loop; we
    # reproduce its arithmetic here against a constant envelope to
    # verify the gate threshold + renormalisation contract.
    def _apply_sensitivity(env, sensitivity, floor=0, ceiling=255):
        threshold = (1.0 - sensitivity / 100.0) * 0.5
        if env <= threshold:
            env_scaled = 0.0
        else:
            span = max(0.01, 1.0 - threshold)
            env_scaled = (env - threshold) / span
        env_clipped = max(0.0, min(1.0, env_scaled))
        return int(round(floor + env_clipped * (ceiling - floor)))

    # sensitivity=100 (no gate) → env passes through linearly to
    # 0..255.
    ok('sensitivity=100, env=0.5 → master ≈ 127',
       abs(_apply_sensitivity(0.5, 100) - 127) <= 1,
       f'got={_apply_sensitivity(0.5, 100)}')
    ok('sensitivity=100, env=1.0 → master == 255',
       _apply_sensitivity(1.0, 100) == 255)
    ok('sensitivity=100, env=0.0 → master == 0',
       _apply_sensitivity(0.0, 100) == 0)

    # sensitivity=0 (max gate) → threshold = 0.5; anything below
    # collapses to floor; above renormalises through the
    # 0.5..1.0 range to 0..ceiling.
    ok('sensitivity=0, env=0.3 (below threshold) → master = floor',
       _apply_sensitivity(0.3, 0, floor=10) == 10,
       f'got={_apply_sensitivity(0.3, 0, floor=10)}')
    ok('sensitivity=0, env=0.5 (at threshold) → master = floor',
       _apply_sensitivity(0.5, 0, floor=0) == 0,
       f'got={_apply_sensitivity(0.5, 0)}')
    ok('sensitivity=0, env=1.0 (max input) → master = ceiling (255)',
       _apply_sensitivity(1.0, 0) == 255)
    # Halfway through the gated range maps to halfway through the
    # output: env=0.75, sens=0 → threshold=0.5, env_scaled=0.5,
    # master≈127.
    ok('sensitivity=0, env=0.75 → master ≈ 127 (gate renormalises)',
       abs(_apply_sensitivity(0.75, 0) - 127) <= 1,
       f'got={_apply_sensitivity(0.75, 0)}')

    # Floor + ceiling clamps: env=1.0 with floor=20 ceiling=200 should
    # produce 200 (the engine never exceeds ceiling, never drops
    # below floor unless env=0).
    ok('floor/ceiling clamp respected at full env',
       _apply_sensitivity(1.0, 100, floor=20, ceiling=200) == 200)
    ok('floor/ceiling clamp respected at zero env',
       _apply_sensitivity(0.0, 100, floor=20, ceiling=200) == 20)

    # ── update_config clamps + persistence contract ────────────────
    prod, _ = _make_producer()
    res = prod.update_config({"gain": 99.0, "sensitivity": 200,
                              "floor": -10, "ceiling": 999,
                              "attackMs": -5, "releaseMs": 5000})
    cfg = res["config"]
    ok('gain clamped to [0, 10]', 0 <= cfg["gain"] <= 10,
       f'gain={cfg["gain"]}')
    ok('sensitivity clamped to [0, 100]', 0 <= cfg["sensitivity"] <= 100,
       f'sensitivity={cfg["sensitivity"]}')
    ok('floor clamped to [0, 255]', 0 <= cfg["floor"] <= 255,
       f'floor={cfg["floor"]}')
    ok('ceiling clamped to [0, 255]', 0 <= cfg["ceiling"] <= 255,
       f'ceiling={cfg["ceiling"]}')
    ok('attackMs clamped non-negative', cfg["attackMs"] >= 0)
    ok('releaseMs clamped to ≤ 2000', cfg["releaseMs"] <= 2000)
    ok('floor never exceeds ceiling',
       cfg["floor"] <= cfg["ceiling"],
       f'floor={cfg["floor"]} ceiling={cfg["ceiling"]}')

    # ── Device enumeration (mock _sd) ─────────────────────────────
    fake_devices = [
        {"name": "Built-in Mic",       "hostapi": 0, "max_input_channels": 2,
         "max_output_channels": 0, "default_samplerate": 44100},
        {"name": "Built-in Speakers",  "hostapi": 1, "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 44100},
        {"name": "WASAPI Speakers",    "hostapi": 2, "max_input_channels": 0,
         "max_output_channels": 2, "default_samplerate": 48000},
        {"name": "USB Headset",        "hostapi": 0, "max_input_channels": 1,
         "max_output_channels": 2, "default_samplerate": 48000},
    ]
    fake_hostapis = [
        {"name": "MME"},
        {"name": "DirectSound"},
        {"name": "Windows WASAPI"},
    ]

    fake_sd = types.SimpleNamespace(
        query_devices=lambda *a, **kw: (
            fake_devices if not a else fake_devices[a[0]]),
        query_hostapis=lambda: fake_hostapis,
        default=types.SimpleNamespace(device=(0, -1)),
    )

    prod, _ = _make_producer()
    saved_sd, saved_ok = lab._sd, lab._SD_OK
    lab._sd, lab._SD_OK = fake_sd, True
    try:
        devs = prod.list_devices()
    finally:
        lab._sd, lab._SD_OK = saved_sd, saved_ok

    # Built-in Speakers (hostapi=DirectSound, no input, output but no
    # WASAPI) is skipped — not an input + not loopback-eligible.
    names = [d["name"] for d in devs]
    ok('list_devices includes input-capable devices',
       'Built-in Mic' in names and 'USB Headset' in names,
       f'names={names}')
    ok('list_devices includes WASAPI output as loopback',
       any('loopback' in d["name"] for d in devs),
       f'names={names}')
    ok('list_devices skips non-WASAPI output device',
       not any(d["name"].startswith("Built-in Speakers") for d in devs),
       f'names={names}')
    loopback = [d for d in devs if d.get('loopback')]
    ok('loopback entry flags loopback=True',
       len(loopback) == 1, f'len={len(loopback)}')
    ok('loopback entry effective channels = max_output_channels',
       loopback and loopback[0]['channels'] == 2,
       f'channels={loopback[0]["channels"] if loopback else None}')
    # The Built-in Mic at index 0 is the default; sort key places it first.
    ok('default device sorted first',
       devs[0]['name'] == 'Built-in Mic' and devs[0]['isDefault'] is True,
       f'first={devs[0]["name"]}')
    ok('label includes channel count + host API',
       all('ch)' in d['label'] for d in devs),
       f'labels={[d["label"] for d in devs]}')

    # ── list_devices when sounddevice unavailable ─────────────────
    prod, _ = _make_producer()
    saved_sd, saved_ok = lab._sd, lab._SD_OK
    lab._sd, lab._SD_OK = None, False
    try:
        ok('list_devices empty when sounddevice missing',
           prod.list_devices() == [],
           f'got={prod.list_devices()}')
    finally:
        lab._sd, lab._SD_OK = saved_sd, saved_ok

    # ── Push pipeline: drive _pusher_loop with a fake stream ──────
    if _HAVE_NUMPY:
        prod, log = _make_producer()
        # Force enabled + envelope to a known value + fake stream
        # presence so the loop doesn't continue on `_stream is None`.
        with prod._lock:
            prod._cfg["enabled"] = True
            prod._cfg["floor"] = 0
            prod._cfg["ceiling"] = 255
            prod._cfg["sensitivity"] = 100
            prod._env_value = 0.5
            prod._stream = object()  # truthy sentinel — loop checks `is None`
        # Run the loop in a thread for 0.55s → expect ~11 pushes
        import threading
        prod._pusher_stop.clear()
        th = threading.Thread(target=prod._pusher_loop, daemon=True)
        th.start()
        time.sleep(0.55)
        prod._pusher_stop.set()
        th.join(timeout=1.0)
        # 20 Hz × 0.55 s ≈ 11 ticks. Allow ±3 for scheduling jitter.
        n = len(log)
        ok('pusher loop fires at ~20 Hz (8 ≤ n ≤ 14 over 0.55 s)',
           8 <= n <= 14, f'n={n}')
        # Master values should all be ~127 (env=0.5, sens=100).
        if log:
            avg = sum(m for m, _, _ in log) / float(len(log))
            ok('pusher master matches env=0.5 → ~127',
               abs(avg - 127) <= 2, f'avg={avg}')
            # seq increments mod 256.
            seqs = [s for _, _, s in log]
            mono = all((seqs[i + 1] - seqs[i]) % 256 == 1
                       for i in range(len(seqs) - 1))
            ok('pusher seq increments monotonically (mod 256)', mono,
               f'seqs={seqs}')

        # Disabled → no pushes regardless of envelope.
        prod, log = _make_producer()
        with prod._lock:
            prod._cfg["enabled"] = False
            prod._env_value = 0.8
            prod._stream = object()
        prod._pusher_stop.clear()
        th = threading.Thread(target=prod._pusher_loop, daemon=True)
        th.start()
        time.sleep(0.3)
        prod._pusher_stop.set()
        th.join(timeout=1.0)
        ok('pusher silent when enabled=False (zero pushes)',
           len(log) == 0, f'n={len(log)}')

        # _stream=None → no pushes (capture not running).
        prod, log = _make_producer()
        with prod._lock:
            prod._cfg["enabled"] = True
            prod._env_value = 0.8
            prod._stream = None
        prod._pusher_stop.clear()
        th = threading.Thread(target=prod._pusher_loop, daemon=True)
        th.start()
        time.sleep(0.3)
        prod._pusher_stop.set()
        th.join(timeout=1.0)
        ok('pusher silent when _stream is None (zero pushes)',
           len(log) == 0, f'n={len(log)}')

    # ── Dashboard surfacing (parent_server /api/remotes/live) ────
    import parent_server
    # Feed a synthetic local-audio push directly into the dispatcher
    # the producer would normally invoke. The /api/remotes/live route
    # reads the brightness-observation registry the dispatcher writes.
    pkt = struct.pack("<HBBI", parent_server.UDP_MAGIC,
                      parent_server.UDP_VERSION,
                      parent_server.CMD_AUTOBRI_PUSH, 0) \
          + struct.pack("<BBB", 200, 0, 1)
    parent_server._handle_autobri_push(
        parent_server._LOCAL_AUDIO_BRI_SOURCE, pkt)
    with parent_server.app.test_client() as c:
        r = c.get('/api/remotes/live')
        d = r.get_json() or {}
        snap = d.get('remotes', [])
        ab = [x for x in snap if x.get('kind') == 'auto-brightness']
        ok('/api/remotes/live returns 200', r.status_code == 200)
        ok('/api/remotes/live surfaces an auto-brightness entry',
           len(ab) >= 1, f'count={len(ab)}')
        local = [x for x in ab
                 if x.get('deviceId') == parent_server._LOCAL_AUDIO_BRI_SOURCE]
        ok('local-audio source distinguished via deviceId',
           len(local) == 1, f'got={[x.get("deviceId") for x in ab]}')
        if local:
            ok('local-audio displayName = "Local Audio Brightness"',
               local[0].get('name') == 'Local Audio Brightness',
               f'name={local[0].get("name")}')
            ab_extras = local[0].get('autoBrightness') or {}
            ok('local-audio autoBrightness.currentValue = 200',
               ab_extras.get('currentValue') == 200,
               f'currentValue={ab_extras.get("currentValue")}')
            ok('local-audio autoBrightness.globalBrightness present',
               'globalBrightness' in ab_extras)

        # Feed an Android push from a phone IP; both should now
        # appear with distinct deviceIds (last-write-wins on the
        # master scalar, but the dashboard surfaces both producers).
        parent_server._handle_autobri_push("10.0.0.42", pkt)
        r2 = c.get('/api/remotes/live')
        ab2 = [x for x in (r2.get_json() or {}).get('remotes', [])
               if x.get('kind') == 'auto-brightness']
        ids2 = {x.get('deviceId') for x in ab2}
        ok('local + Android producers both surface in /api/remotes/live',
           parent_server._LOCAL_AUDIO_BRI_SOURCE in ids2 and
           '10.0.0.42' in ids2,
           f'ids={ids2}')
        android = [x for x in ab2 if x.get('deviceId') == '10.0.0.42']
        ok('Android entry name is "Android Auto Brightness (<ip>)"',
           android and 'Android Auto Brightness' in android[0]['name'],
           f'name={android[0]["name"] if android else None}')

    # ── Print results ────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)
    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)
    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    if not _HAVE_NUMPY:
        print('  (numpy unavailable — envelope + pusher tests skipped)')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run())
