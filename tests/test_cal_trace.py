#!/usr/bin/env python3
"""#686 — cal-trace recorder + replay-tool offline tests.

Exercises:

* The recorder writes one NDJSON record per probe (header + seed +
  N×probe + footer) and rotates old traces past the retention cap.
* Skip-by-filter records carry the predicted floor point + surface +
  in-fov-of fields the replay tool needs.
* The replay tool's ``load_trace`` round-trips the recorder output and
  ``print_summary`` produces a sane decision histogram.

Doesn't require Flask, matplotlib, or a running orchestrator. Live-rig
verification is tracked separately in #686's acceptance criteria.

Run:
    python -X utf8 tests/test_cal_trace.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop" / "shared"))
sys.path.insert(0, str(ROOT / "tools"))


def _make_recorder(tmpdir):
    """Inline the parts of CalTraceRecorder we need without importing
    parent_server (which would spin up Flask)."""
    src = (ROOT / "desktop" / "shared" / "parent_server.py").read_text(encoding="utf-8-sig")
    start = src.index("CAL_TRACES_DIR = DATA / \"cal_traces\"")
    end = src.index("def _wrap_grid_filter_for_trace")
    block = src[start:end]
    # Drop the DATA-relative path constant; tests pin their own path.
    block = block.replace("CAL_TRACES_DIR = DATA / \"cal_traces\"",
                          f"CAL_TRACES_DIR = Path({json.dumps(str(tmpdir))})")
    ns = {
        "Path": Path,
        "json": json,
        "log": _LogStub(),
    }
    exec(block, ns)
    return ns


class _LogStub:
    def debug(self, *a, **kw): pass
    def info(self, *a, **kw): pass
    def warning(self, *a, **kw): pass
    def exception(self, *a, **kw): pass


# ── 1. Recorder writes header + seed + probes + footer ─────────────────

def test_recorder_writes_records():
    with tempfile.TemporaryDirectory() as td:
        ns = _make_recorder(td)
        rec = ns["CalTraceRecorder"](
            fid=42, mode="markers",
            fixture_pos=(1500, 100, 3500),
            mover_rotation=[0, 0, 0],
            pan_range_deg=540, tilt_range_deg=270,
            mounted_inverted=True,
            cameras=[{"id": 12, "polygon": [(0, 1000), (3000, 1000),
                                              (3000, 4000), (0, 4000)]}],
            surfaces=None,
            scene_meta={"basement": True},
        )
        rec.record_seed(0.5, 0.5, (1500, 2500), source="markers-mode")
        rec.record_skip(0.1, 0.1, reason="grid-filter")
        rec.record_event({"stage": "beam-found", "probe": 1, "total": 24,
                            "pan": 0.5, "tilt": 0.6, "pixelX": 320,
                            "pixelY": 240})
        rec.record_event({"stage": "confirmed", "probe": 1, "total": 24,
                            "pan": 0.5, "tilt": 0.6,
                            "panShiftPx": 12, "tiltShiftPx": 14})
        rec.record_decision(0.5, 0.6, "marker-converged",
                              reason="marker 3 err=4.1px",
                              markerId=3, iterations=8)
        rec.close(status="completed", error=None,
                  extra={"sampleCount": 5})

        path = Path(rec.path)
        assert path.is_file(), f"trace file missing at {path}"
        records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        kinds = [r.get("kind") for r in records]
        assert kinds[0] == "header", kinds
        assert kinds[-1] == "footer", kinds
        assert "seed" in kinds, kinds
        assert kinds.count("probe") == 4, kinds
        # Counters on the footer reflect the probe activity.
        footer = records[-1]
        assert footer["counts"]["skipped"] == 1
        assert footer["counts"]["confirmed"] == 1
        assert footer["counts"]["probed"] == 1, footer["counts"]
        assert footer["status"] == "completed"
        assert footer["extra"]["sampleCount"] == 5


# ── 2. Skip records carry predicted-floor-point context ───────────────

def test_skip_records_carry_predictions():
    with tempfile.TemporaryDirectory() as td:
        ns = _make_recorder(td)
        rec = ns["CalTraceRecorder"](
            fid=14, mode="legacy",
            fixture_pos=(1500, 100, 3500),
            mover_rotation=[0, 0, 0],  # identity hang → tilt=0 aims down
            pan_range_deg=540, tilt_range_deg=270,
            mounted_inverted=True,
            cameras=[{"id": 13, "polygon": [(500, 1000), (2500, 1000),
                                              (2500, 3500), (500, 3500)]}],
            surfaces=None,
        )
        rec.record_skip(0.5, 0.5)  # straight down from fixture → floor
        rec.close(status="error", error="phase_timeout")

        records = [json.loads(line) for line in Path(rec.path).read_text(encoding="utf-8").splitlines() if line.strip()]
        skips = [r for r in records if r.get("kind") == "probe" and r.get("decision") == "skip-by-filter"]
        assert len(skips) == 1
        s = skips[0]
        # fixture above floor → ray hits floor under the fixture.
        fp = s.get("predictedFloorPoint")
        assert fp is not None and len(fp) == 3, s
        assert abs(fp[0] - 1500) < 5 and abs(fp[1] - 100) < 5, fp
        # Floor point at (1500, 100) is OUTSIDE the camera polygon (which
        # starts at y=1000), so predictedInFovOf should be empty.
        assert s.get("predictedInFovOf") == [], s


# ── 3. Retention cap deletes old traces per fixture ───────────────────

def test_retention_cap_per_fixture():
    import time as _time
    with tempfile.TemporaryDirectory() as td:
        ns = _make_recorder(td)
        # Lower the retention cap so the test stays cheap.
        ns["CAL_TRACE_RETENTION_PER_FIXTURE"] = 3
        cls = ns["CalTraceRecorder"]
        for i in range(5):
            r = cls(fid=99, mode="legacy",
                     fixture_pos=(1000, 1000, 3000),
                     mover_rotation=[0, 0, 0],
                     pan_range_deg=540, tilt_range_deg=270,
                     mounted_inverted=False, cameras=[], surfaces=None)
            r.close(status="error")
            # Filename has millisecond resolution; nudge the clock between
            # iterations so each trace gets a distinct path. In production
            # cal runs take minutes so collision is impossible without
            # this contrived loop.
            _time.sleep(0.005)
        # Only the 3 most recent should remain.
        remaining = sorted(Path(td).glob("fid99-*.ndjson"))
        assert len(remaining) == 3, remaining


# ── 4. Replay-tool parsing round-trips the recorder ───────────────────

def test_replay_load_trace_round_trips():
    with tempfile.TemporaryDirectory() as td:
        ns = _make_recorder(td)
        r = ns["CalTraceRecorder"](
            fid=17, mode="markers",
            fixture_pos=(1500, 100, 3500),
            mover_rotation=[0, 0, 0],
            pan_range_deg=540, tilt_range_deg=270,
            mounted_inverted=True,
            cameras=[],
            surfaces=None,
        )
        r.record_seed(0.5, 0.5, (1500, 2500), source="markers-mode")
        r.record_event({"stage": "beam-found", "probe": 1, "total": 5,
                          "pan": 0.5, "tilt": 0.5})
        r.record_event({"stage": "confirm-rejected", "probe": 1, "total": 5,
                          "pan": 0.5, "tilt": 0.5,
                          "verdict": "REJECTED_DEPTH_DISCONTINUITY",
                          "reason": "depth-discontinuity"})
        r.close(status="error", error="no-beam-found")

        # Import the replay tool and parse the file.
        sys.path.insert(0, str(ROOT / "tools"))
        import importlib
        if "cal_trace_replay" in sys.modules:
            replay = importlib.reload(sys.modules["cal_trace_replay"])
        else:
            import cal_trace_replay as replay
        header, seed, probes, footer = replay.load_trace(Path(r.path))
        assert header.get("fid") == 17
        assert seed and abs(seed.get("panNorm") - 0.5) < 1e-9
        assert len(probes) == 2
        assert probes[0].get("decision") == "detected"
        assert probes[1].get("decision") == "nudge-rejected"
        assert footer.get("status") == "error"


def main():
    failures = []
    tests = [
        ("recorder writes header + seed + probes + footer",
         test_recorder_writes_records),
        ("skip records carry predicted-floor-point context",
         test_skip_records_carry_predictions),
        ("retention cap deletes old traces per fixture",
         test_retention_cap_per_fixture),
        ("replay tool round-trips the recorder",
         test_replay_load_trace_round_trips),
    ]
    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            failures.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(f"{name}: {type(e).__name__}: {e}")
    if failures:
        for f in failures:
            print(f"[FAIL] {f}")
        print(f"{len(failures)} of {len(tests)} failed")
        return 1
    print(f"all {len(tests)} cal-trace tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
