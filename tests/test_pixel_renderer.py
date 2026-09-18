#!/usr/bin/env python3
"""Server-side pixel renderer tests (#938).

Covers the Python half of the SlyLED Effect Spec v1:
  * every corpus case matches the committed golden digests
  * the numpy fast path is byte-identical to the scalar oracle
  * rendering is deterministic and seekable (same inputs -> same bytes)
  * `active_segment` matches the playback loop's selection rule
  * EFFECT_SPEC_VERSION agrees between pixel_renderer.py and pixel_renderer.js

The cross-runtime gate (JS under Node vs this module) lives in
tests/test_pixel_renderer_parity.py.

Run: `python3 tests/test_pixel_renderer.py`
Regenerate goldens after a deliberate spec change:
     `python3 tests/test_pixel_renderer.py --regen`
"""

import hashlib
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "desktop" / "shared"))

import pixel_renderer as pr  # noqa: E402

CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "pixel_corpus"
CORPUS_PATH = CORPUS_DIR / "corpus.json"
EXPECTED_PATH = CORPUS_DIR / "expected.json"
JS_RENDERER = REPO_ROOT / "desktop" / "shared" / "spa" / "js" / "pixel_renderer.js"

_passed = 0
_failed = 0


def check(cond, label):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {label}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}")


def digest(rows):
    b = bytearray()
    for px in rows:
        b.extend(px)
    return hashlib.sha256(bytes(b)).hexdigest()[:16]


def render_case(c):
    return [pr.pixel(c["t"], c["p"], i, c["n"], c["e"]) for i in range(c["n"])]


def regen():
    cases = json.loads(CORPUS_PATH.read_text())
    out = {
        "specVersion": pr.EFFECT_SPEC_VERSION,
        "note": "sha256[:16] of concatenated RGB bytes per corpus case. "
                "Regenerate with tests/test_pixel_renderer.py --regen when the spec "
                "changes, and bump EFFECT_SPEC_VERSION in both renderers.",
        "digests": [digest(render_case(c)) for c in cases],
    }
    EXPECTED_PATH.write_text(json.dumps(out, indent=0))
    print(f"regenerated {len(out['digests'])} digests at spec v{pr.EFFECT_SPEC_VERSION}")


def main():
    cases = json.loads(CORPUS_PATH.read_text())
    expected = json.loads(EXPECTED_PATH.read_text())

    print("Pixel renderer — corpus goldens")
    check(expected["specVersion"] == pr.EFFECT_SPEC_VERSION,
          f"corpus spec version matches renderer (v{pr.EFFECT_SPEC_VERSION})")
    check(len(expected["digests"]) == len(cases),
          f"digest count matches corpus ({len(cases)} cases)")

    mismatched = []
    total_px = 0
    for ci, c in enumerate(cases):
        rows = render_case(c)
        total_px += len(rows)
        if digest(rows) != expected["digests"][ci]:
            mismatched.append((ci, c))
    if mismatched:
        ci, c = mismatched[0]
        print(f"         first mismatch: case {ci} t={c['t']} n={c['n']} e={c['e']} p={c['p']}")
    check(not mismatched,
          f"all {len(cases)} cases match goldens ({total_px} pixels)")

    print("Pixel renderer — fast path equals scalar oracle")
    bad = []
    for c in cases:
        scalar = pr.pixel(c["t"], c["p"], 0, c["n"], c["e"])
        fast = pr.render_string(c["t"], c["p"], c["n"], c["e"])
        if scalar is None:
            if fast is not None:
                bad.append((c, "expected None"))
            continue
        want = bytearray()
        for i in range(c["n"]):
            want.extend(pr.pixel(c["t"], c["p"], i, c["n"], c["e"]))
        if bytes(want) != fast:
            bad.append((c, "bytes differ"))
    if bad:
        print(f"         first: {bad[0][1]} for t={bad[0][0]['t']} n={bad[0][0]['n']} e={bad[0][0]['e']}")
    check(not bad, f"render_string == scalar pixel() for all {len(cases)} cases")

    print("Pixel renderer — determinism / seekability")
    c = {"t": 6, "p": {}, "n": 120, "e": 987654}
    a = pr.render_string(c["t"], c["p"], c["n"], c["e"])
    b = pr.render_string(c["t"], c["p"], c["n"], c["e"])
    check(a == b, "repeat render of the same (type, params, n, elapsed) is identical")
    later = pr.render_string(c["t"], c["p"], c["n"], c["e"] + 1000)
    check(a != later, "a different elapsed produces different output (not frozen)")

    print("Pixel renderer — edge cases")
    check(pr.render_string(5, {}, 0, 0) == b"", "n=0 renders empty")
    one = pr.render_string(13, {"r": 255, "b2": 255}, 1, 0)
    check(len(one) == 3, "n=1 renders a single pixel (no divide-by-zero in GRADIENT)")
    check(pr.pixel(1, {}, 0, 10, 0) is None, "non-procedural type returns None")
    check(pr.render_string(1, {}, 10, 0) is None, "render_string mirrors the None contract")

    print("Pixel renderer — active_segment selection rule")
    # These assertions mirror bake_engine.py's LED preview loop exactly. They
    # are NOT how a priority system "should" behave — they are what the show
    # engine does, and streamed pixels must agree with the preview.
    segs = [
        {"startS": 0, "durationS": 4, "type": 5, "_pri": 0},
        {"startS": 8, "durationS": 2, "type": 7, "stringIndex": 1},
        {"startS": 8, "durationS": 2, "type": 11, "stringIndex": 0},
    ]
    check(pr.active_segment(segs, 0, 1.0)["type"] == 5, "picks the only covering segment")
    check(pr.active_segment(segs, 0, 8.5)["type"] == 11,
          "stringIndex filter applies before the first-match pick")
    check(pr.active_segment(segs, 1, 8.5)["type"] == 7,
          "a segment bound to stringIndex=1 drives string 1")
    check(pr.active_segment(segs, 0, 4.0) is None, "end is exclusive")
    check(pr.active_segment(segs, 0, -1) is None, "before start returns None")
    check(pr.active_segment([], 0, 0) is None, "empty segment list returns None")

    # durationS defaults to 1 (bake_engine's default), not 0 — a segment with
    # no explicit duration covers exactly one second.
    check(pr.active_segment([{"startS": 5, "type": 5}], 0, 5.5)["type"] == 5,
          "missing durationS defaults to 1s (covers t=5.5)")
    check(pr.active_segment([{"startS": 5, "type": 5}], 0, 6.0) is None,
          "missing durationS defaults to 1s (excludes t=6.0)")

    # _pri only breaks ties between segments sharing a startS, because the bake
    # pre-sorts by (startS, -_pri) and this takes the first match.
    tied = [{"startS": 2, "durationS": 4, "type": 4, "_pri": 5},
            {"startS": 2, "durationS": 4, "type": 9, "_pri": 1}]
    check(pr.active_segment(tied, 0, 3.0)["type"] == 4,
          "at equal startS the higher-priority segment (sorted first) wins")
    # Documented limitation: an earlier-starting segment shadows a later,
    # higher-priority overlapping one. Pinned so a future change is deliberate.
    shadowed = [{"startS": 0, "durationS": 10, "type": 5, "_pri": 0},
                {"startS": 2, "durationS": 4, "type": 4, "_pri": 5}]
    check(pr.active_segment(shadowed, 0, 3.0)["type"] == 5,
          "earlier-starting segment shadows a later higher-priority one (matches show engine)")

    print("Pixel renderer — render_fixture")
    bf = {"segments": [{"startS": 0, "durationS": 10, "type": 13,
                        "params": {"r": 255, "b2": 255}}]}
    out = pr.render_fixture(bf, [{"leds": 3}, {"leds": 2}], 1.0)
    check(len(out) == (3 + 2) * 3, "renders every string in fixture-string order")
    dark = pr.render_fixture({"segments": []}, [{"leds": 4}], 1.0)
    check(dark == b"\x00" * 12, "uncovered strings render black")

    print("Pixel renderer — JS twin agreement on spec version")
    js = JS_RENDERER.read_text(encoding="utf-8")
    m = re.search(r"EFFECT_SPEC_VERSION\s*=\s*(\d+)", js)
    check(m is not None, "EFFECT_SPEC_VERSION found in pixel_renderer.js")
    if m:
        check(int(m.group(1)) == pr.EFFECT_SPEC_VERSION,
              f"JS spec version {m.group(1)} == Python {pr.EFFECT_SPEC_VERSION}")

    print(f"\n{_passed} passed, {_failed} failed out of {_passed + _failed} tests")
    return 1 if _failed else 0


if __name__ == "__main__":
    if "--regen" in sys.argv:
        regen()
        sys.exit(0)
    sys.exit(main())
