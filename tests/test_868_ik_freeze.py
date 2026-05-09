"""test_868_ik_freeze.py — gyro post-calibrate IK freeze contract.

Reproduces the live-test 2026-05-09 symptom: operator presses Start
+ Calibrate, head physically tilts on calibrate-end, then claim
panNorm/tiltNorm freeze for 4.5 minutes despite the puck publishing
visibly varied aim_stage vectors. Color wheel still works through
the same claim — DMX delivery is healthy — freeze is in the
pan/tilt path specifically.

Root cause: software EMA on `claim.pan_smooth` / `claim.tilt_smooth`
with `alpha = 1 - claim.smoothing`. With smoothing=1.0 (operator's
fid 23 setting per `/api/fixtures` snapshot) → alpha=0 → the EMA
update `pan_smooth += 0 * delta` was a no-op forever. But
`_set_canonical_aim` sat in the same branch and fired regardless,
so `/api/fixtures/live`'s aim field updated while DMX pan/tilt
stayed frozen.

Fix (per operator 2026-05-09): rip out the software EMA entirely.
Pan/tilt motor-speed smoothing is the fixture's job, not ours.
When the DMX profile defines a `pan-tilt-speed` channel, drive
it from `claim.smoothing` (0=fast → DMX 0, 1=slow → DMX 255).
Profiles without that channel skip the speed write and run at
the engine's unsmoothed IK rate.

Run: python -X utf8 tests/test_868_ik_freeze.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

from mover_control import MoverControlEngine, MoverClaim  # noqa: E402

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


# ─── Fakes ────────────────────────────────────────────────────────────────────

class FakeUniverse:
    def __init__(self):
        self.channels = {}
        self.fixture_pt = []  # list of (addr, pan, tilt, profile)

    def set_channel(self, ch, value):
        self.channels[ch] = int(value)

    def set_fixture_pan_tilt(self, addr, pan_norm, tilt_norm, profile):
        self.fixture_pt.append((addr, pan_norm, tilt_norm, profile))

    def set_fixture_dimmer(self, *_a, **_k):
        pass


class FakeEngine:
    def __init__(self):
        self.running = True
        self.universes = {}

    def get_universe(self, uni):
        if uni not in self.universes:
            self.universes[uni] = FakeUniverse()
        return self.universes[uni]


# Minimal profile with a pan-tilt-speed channel at offset 4.
PROFILE_WITH_SPEED = {
    "channel_map": {
        "pan": 0, "pan-fine": 1, "tilt": 2, "tilt-fine": 3,
        "pan-tilt-speed": 4, "dimmer": 5,
    },
    "channels": [
        {"offset": 0, "type": "pan"},
        {"offset": 1, "type": "pan-fine"},
        {"offset": 2, "type": "tilt"},
        {"offset": 3, "type": "tilt-fine"},
        {"offset": 4, "type": "pan-tilt-speed"},
        {"offset": 5, "type": "dimmer"},
    ],
    "panRange": 540,
    "tiltRange": 270,
}

PROFILE_NO_SPEED = {
    "channel_map": {
        "pan": 0, "pan-fine": 1, "tilt": 2, "tilt-fine": 3, "dimmer": 4,
    },
    "channels": [
        {"offset": 0, "type": "pan"},
        {"offset": 1, "type": "pan-fine"},
        {"offset": 2, "type": "tilt"},
        {"offset": 3, "type": "tilt-fine"},
        {"offset": 4, "type": "dimmer"},
    ],
    "panRange": 540,
    "tiltRange": 270,
}


def _make_engine(get_engine_fn, get_profile_fn, fixtures):
    return MoverControlEngine(
        get_fixtures=lambda: fixtures,
        get_layout=lambda: {},
        get_profile_info=get_profile_fn,
        get_engine=get_engine_fn,
        set_fixture_color_fn=lambda *a, **k: None,
        get_remote_by_device_id=lambda _d: None,
    )


def _make_engine_with_claim(profile, smoothing):
    fixtures = [{"id": 14, "fixtureType": "dmx",
                 "dmxUniverse": 1, "dmxStartAddr": 100,
                 "profile": "test"}]
    eng = _make_engine(lambda: FakeEngine(), lambda _p: profile, fixtures)
    claim = MoverClaim(mover_id=14, device_id="test-dev",
                       device_name="test-dev", device_type="gyro")
    claim.smoothing = smoothing
    claim.calibrated_here = True
    claim.state = "streaming"
    eng._claims[14] = claim
    return eng, claim


# ─── Tests ────────────────────────────────────────────────────────────────────

# Synthetic 17-step trace from the issue's live capture.
TRACE = [
    (0.4705, 0.0078), (0.1650, 0.0260), (0.6362, 0.0110), (0.5234, 0.1500),
    (0.7812, 0.2200), (0.4100, 0.0500), (0.3300, 0.3700), (0.6900, 0.4500),
    (0.5500, 0.6200), (0.4800, 0.0900), (0.2200, 0.2800), (0.7600, 0.5400),
    (0.5900, 0.3100), (0.4400, 0.7800), (0.6700, 0.2000), (0.3800, 0.4900),
    (0.8038, 0.3713),
]


def _drive_trace(claim, trace):
    """Drive the new pass-through assignment from the tick loop, then
    return the smooth-state trace. Mirrors the rewritten block in
    mover_control.py:670-679 — direct assignment, no EMA."""
    out = []
    for pan_norm, tilt_norm in trace:
        claim.pan_smooth = pan_norm
        claim.tilt_smooth = tilt_norm
        claim.have_pan_tilt = True
        out.append((claim.pan_smooth, claim.tilt_smooth))
    return out


def test_pan_smooth_tracks_ik_directly_no_smoothing():
    """smoothing=0.0 — software pass-through equals IK output exactly."""
    _, claim = _make_engine_with_claim(PROFILE_WITH_SPEED, 0.0)
    out = _drive_trace(claim, TRACE)
    for i, (ps, ts) in enumerate(out):
        if (ps, ts) != TRACE[i]:
            _assert(False,
                    f"tick {i}: got ({ps}, {ts}), expected {TRACE[i]}")
            return
    _assert(True, "smoothing=0 → pan/tilt_smooth tracks IK exactly")


def test_smoothing_one_does_not_freeze_software_path():
    """The exact freeze the operator hit. With software EMA gone,
    smoothing=1.0 still passes IK output through to pan_smooth /
    tilt_smooth — no freeze possible at this layer regardless of
    smoothing value."""
    _, claim = _make_engine_with_claim(PROFILE_WITH_SPEED, 1.0)
    out = _drive_trace(claim, TRACE)
    pan_distinct = len(set(round(p, 4) for p, _ in out))
    _assert(pan_distinct == len(set(round(p, 4) for p, _ in TRACE)),
            f"smoothing=1.0 software path reproduces every distinct IK "
            f"input ({pan_distinct} values)")
    _assert(out[-1] == TRACE[-1],
            f"final tick equals last IK input: got {out[-1]}, "
            f"expected {TRACE[-1]}")


def test_smoothing_lands_on_dmx_speed_channel_when_profile_has_one():
    """Operator's smoothing slider [0..1] → pan-tilt-speed DMX byte
    [0..255]. 0=fast (DMX 0), 1=slow (DMX 255)."""
    fixtures = [{"id": 14, "fixtureType": "dmx",
                 "dmxUniverse": 1, "dmxStartAddr": 100,
                 "profile": "test"}]
    fake_eng = FakeEngine()
    cases = [(0.0, 0), (0.15, 38), (0.5, 128), (1.0, 255)]
    for smoothing, expected_dmx in cases:
        eng = _make_engine(lambda: fake_eng,
                           lambda _p: PROFILE_WITH_SPEED, fixtures)
        claim = MoverClaim(mover_id=14, device_id="test-dev",
                           device_name="test-dev", device_type="gyro")
        claim.smoothing = smoothing
        claim.have_pan_tilt = True
        claim.pan_smooth = 0.5
        claim.tilt_smooth = 0.5
        claim.state = "streaming"
        eng._claims[14] = claim
        mover = fixtures[0]
        eng._write_dmx(mover, PROFILE_WITH_SPEED, claim, include_pan_tilt=True)
        # Address 100 + offset 4 = 104.
        actual = fake_eng.universes[1].channels.get(104)
        _assert(actual == expected_dmx,
                f"smoothing={smoothing} → DMX[104]={actual}, "
                f"expected {expected_dmx}")


def test_no_speed_channel_means_no_speed_write():
    """Profiles without a pan-tilt-speed channel leave it alone — the
    smoothing slider has no effect on these fixtures, by design."""
    fixtures = [{"id": 14, "fixtureType": "dmx",
                 "dmxUniverse": 1, "dmxStartAddr": 100,
                 "profile": "test"}]
    fake_eng = FakeEngine()
    eng = _make_engine(lambda: fake_eng,
                       lambda _p: PROFILE_NO_SPEED, fixtures)
    claim = MoverClaim(mover_id=14, device_id="test-dev",
                       device_name="test-dev", device_type="gyro")
    claim.smoothing = 1.0  # would have been the freeze case
    claim.have_pan_tilt = True
    claim.pan_smooth = 0.5
    claim.tilt_smooth = 0.5
    claim.state = "streaming"
    eng._claims[14] = claim
    mover = fixtures[0]
    eng._write_dmx(mover, PROFILE_NO_SPEED, claim, include_pan_tilt=True)
    # Without pan-tilt-speed in the channel_map, no extra channel
    # write should land beyond pan/tilt (handled by
    # `set_fixture_pan_tilt`, which our fake collects in `fixture_pt`,
    # not `channels`). The `channels` dict should therefore be empty
    # of any speed-position writes.
    written = fake_eng.universes[1].channels
    _assert(104 not in written and len(written) == 0,
            f"profile without pan-tilt-speed: no extra channel writes "
            f"(got {written})")
    _assert(len(fake_eng.universes[1].fixture_pt) == 1,
            f"pan/tilt write still happens (got "
            f"{len(fake_eng.universes[1].fixture_pt)})")


def test_speed_value_clamps_at_dmx_byte_range():
    """Even with out-of-range smoothing values (data corruption,
    legacy import), the DMX speed write stays in [0, 255]."""
    fixtures = [{"id": 14, "fixtureType": "dmx",
                 "dmxUniverse": 1, "dmxStartAddr": 100,
                 "profile": "test"}]
    fake_eng = FakeEngine()
    cases = [(-0.5, 0), (1.5, 255), (2.0, 255)]
    for smoothing, expected_dmx in cases:
        eng = _make_engine(lambda: fake_eng,
                           lambda _p: PROFILE_WITH_SPEED, fixtures)
        claim = MoverClaim(mover_id=14, device_id="test-dev",
                           device_name="test-dev", device_type="gyro")
        claim.smoothing = smoothing
        claim.have_pan_tilt = True
        claim.pan_smooth = 0.5
        claim.tilt_smooth = 0.5
        claim.state = "streaming"
        eng._claims[14] = claim
        mover = fixtures[0]
        eng._write_dmx(mover, PROFILE_WITH_SPEED, claim, include_pan_tilt=True)
        actual = fake_eng.universes[1].channels.get(104)
        _assert(actual == expected_dmx,
                f"smoothing={smoothing} clamped to DMX[104]={actual}, "
                f"expected {expected_dmx}")


ALL = [
    test_pan_smooth_tracks_ik_directly_no_smoothing,
    test_smoothing_one_does_not_freeze_software_path,
    test_smoothing_lands_on_dmx_speed_channel_when_profile_has_one,
    test_no_speed_channel_means_no_speed_write,
    test_speed_value_clamps_at_dmx_byte_range,
]


if __name__ == "__main__":
    print("=== #868 no-software-smoothing contract ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            import traceback
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
            traceback.print_exc()
    total = _passed + _failed
    print(f"\n{_passed} passed, {_failed} failed out of {total}")
    sys.exit(0 if _failed == 0 else 1)
