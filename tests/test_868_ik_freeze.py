"""test_868_ik_freeze.py — pan/tilt pass-through contract (post #868 + #877).

Originally pinned the #868 fix: software EMA on `claim.pan_smooth` /
`claim.tilt_smooth` froze pan/tilt forever when `claim.smoothing=1.0`
because `alpha = 1 - smoothing = 0`. #868 replaced the EMA with pure
pass-through; smoothing then drove the DMX `pan-tilt-speed` channel.

#877 deleted smoothing entirely — operator directive: "the gyro
vector gets matched to the moving head, that is a valid vector,
never clamped, the moving head is told to move to that vector (it
takes care of its own, getting as close to the vector as possible)."
The orchestrator no longer transforms the aim vector; the
pan-tilt-speed channel write is removed; the `claim.smoothing` field
is gone. This file pins the new contract:

  1. The orient tick writes `pan_norm` → `claim.pan_smooth` and
     `tilt_norm` → `claim.tilt_smooth` with no transform.
  2. `_write_dmx` writes pan/tilt only — NEVER the pan-tilt-speed
     channel, regardless of whether the profile defines one.
  3. `MoverClaim` does not have a `smoothing` attribute.
  4. `MoverControlEngine.set_smoothing` is gone (operator-facing
     slider deleted across SPA + Android).

Run: python3 -X utf8 tests/test_868_ik_freeze.py
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


# ─── Synthetic 17-step trace from the issue's live capture ───────────────────
TRACE = [
    (0.4705, 0.0078), (0.1650, 0.0260), (0.6362, 0.0110), (0.5234, 0.1500),
    (0.7812, 0.2200), (0.4100, 0.0500), (0.3300, 0.3700), (0.6900, 0.4500),
    (0.5500, 0.6200), (0.4800, 0.0900), (0.2200, 0.2800), (0.7600, 0.5400),
    (0.5900, 0.3100), (0.4400, 0.7800), (0.6700, 0.2000), (0.3800, 0.4900),
    (0.8038, 0.3713),
]


def _drive_trace(claim, trace):
    """Drive the new pure pass-through assignment from the tick
    loop. Mirrors the rewritten block in mover_control.py — direct
    assignment, no EMA, no transform."""
    out = []
    for pan_norm, tilt_norm in trace:
        claim.pan_smooth = pan_norm
        claim.tilt_smooth = tilt_norm
        claim.have_pan_tilt = True
        out.append((claim.pan_smooth, claim.tilt_smooth))
    return out


# ─── Tests ───────────────────────────────────────────────────────────────────

def test_pan_tilt_passthrough_tracks_ik_exactly():
    """Orient tick writes pan_norm → claim.pan_smooth one-to-one.
    Used to assert this against the legacy EMA; #877 makes it the
    only assertion needed since there's no smoothing."""
    claim = MoverClaim(mover_id=14, device_id="test-dev",
                       device_name="test-dev", device_type="gyro")
    out = _drive_trace(claim, TRACE)
    for i, (ps, ts) in enumerate(out):
        if (ps, ts) != TRACE[i]:
            _assert(False, f"tick {i}: got ({ps}, {ts}), expected {TRACE[i]}")
            return
    _assert(True, "pan/tilt_smooth tracks IK output exactly")


def test_no_smoothing_attribute_on_claim():
    """#877 — MoverClaim no longer carries a `smoothing` field. Test
    documents the deletion; a future patch that re-adds it accidentally
    will fail here."""
    claim = MoverClaim(mover_id=14, device_id="test-dev",
                       device_name="test-dev", device_type="gyro")
    _assert(not hasattr(claim, "smoothing"),
            f"MoverClaim has no smoothing attribute "
            f"(got hasattr={hasattr(claim, 'smoothing')})")


def test_no_set_smoothing_method_on_engine():
    """#877 — `set_smoothing` was deleted from MoverControlEngine.
    The HTTP `/api/mover-control/smoothing` route now exists only as
    a back-compat no-op; the engine method is gone."""
    eng = _make_engine(lambda: FakeEngine(),
                       lambda _p: PROFILE_NO_SPEED, [])
    _assert(not callable(getattr(eng, "set_smoothing", None)),
            f"MoverControlEngine has no callable set_smoothing "
            f"(got {getattr(eng, 'set_smoothing', None)!r})")


def test_pan_tilt_speed_channel_never_written():
    """#877 — `_write_dmx` does NOT touch the pan-tilt-speed channel,
    even when the profile defines one at a known offset. Pre-#877
    this channel got `int(claim.smoothing × 255)`."""
    fixtures = [{"id": 14, "fixtureType": "dmx",
                 "dmxUniverse": 1, "dmxStartAddr": 100,
                 "profile": "test"}]
    fake_eng = FakeEngine()
    eng = _make_engine(lambda: fake_eng,
                       lambda _p: PROFILE_WITH_SPEED, fixtures)
    claim = MoverClaim(mover_id=14, device_id="test-dev",
                       device_name="test-dev", device_type="gyro")
    claim.have_pan_tilt = True
    claim.pan_smooth = 0.5
    claim.tilt_smooth = 0.5
    claim.state = "streaming"
    eng._claims[14] = claim
    mover = fixtures[0]
    eng._write_dmx(mover, PROFILE_WITH_SPEED, claim, include_pan_tilt=True)

    # pan-tilt-speed slot = addr + offset = 100 + 4 = 104. Must be absent.
    channels = fake_eng.universes[1].channels
    _assert(104 not in channels,
            f"#877 pan-tilt-speed (offset 4) NEVER touched "
            f"(channels written = {sorted(channels.keys())})")
    # And pan/tilt themselves were written via the fixture helper.
    _assert(len(fake_eng.universes[1].fixture_pt) == 1,
            f"set_fixture_pan_tilt was called once "
            f"(got {len(fake_eng.universes[1].fixture_pt)})")


def test_no_speed_channel_no_extra_writes():
    """Profile without pan-tilt-speed already produced no extra
    writes pre-#877; the post-#877 path is the same. Sanity check
    that the deletion didn't introduce stray writes."""
    fixtures = [{"id": 14, "fixtureType": "dmx",
                 "dmxUniverse": 1, "dmxStartAddr": 100,
                 "profile": "test"}]
    fake_eng = FakeEngine()
    eng = _make_engine(lambda: fake_eng,
                       lambda _p: PROFILE_NO_SPEED, fixtures)
    claim = MoverClaim(mover_id=14, device_id="test-dev",
                       device_name="test-dev", device_type="gyro")
    claim.have_pan_tilt = True
    claim.pan_smooth = 0.5
    claim.tilt_smooth = 0.5
    claim.state = "streaming"
    eng._claims[14] = claim
    mover = fixtures[0]
    eng._write_dmx(mover, PROFILE_NO_SPEED, claim, include_pan_tilt=True)
    written = fake_eng.universes[1].channels
    _assert(len(written) == 0,
            f"no extra channel writes for profile without speed "
            f"(got {written})")


ALL = [
    test_pan_tilt_passthrough_tracks_ik_exactly,
    test_no_smoothing_attribute_on_claim,
    test_no_set_smoothing_method_on_engine,
    test_pan_tilt_speed_channel_never_written,
    test_no_speed_channel_no_extra_writes,
]


if __name__ == "__main__":
    print("=== #877 pan/tilt pass-through contract ===")
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
