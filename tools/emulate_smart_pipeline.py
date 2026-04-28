#!/usr/bin/env python3
"""emulate_smart_pipeline.py — #733 acceptance gate for cal-pipeline PRs.

Mirrors the SMART cal pipeline at HEAD (coverage_math.coverage_polygon,
working_area, sample_grid, solve_dmx_per_degree, angles_to_dmx) and
runs it against the synthetic corpus at ``tests/fixtures/cal/corpus.json``.
Asserts the non-degeneracy invariants the issue calls out — so any
future cal-pipeline PR that breaks one of #730 / #731 / #732 (or any
adjacent failure mode) is caught offline before the rig run.

Exit codes:
  0 — every case in the corpus passes its expectations.
  1 — at least one case violates a non-degeneracy gate.

Run: ``python tools/emulate_smart_pipeline.py``.
Add ``--verbose`` for per-case stats.

CI hookup: ``tests/regression/run_all.py`` invokes this; it must
pass before the weekly regression goes green.
"""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "desktop", "shared"))

from coverage_math import (  # noqa: E402
    coverage_polygon, working_area, sample_grid,
    solve_dmx_per_degree, angles_to_dmx, dmx_to_angles,
    world_to_fixture_pt,
    _polygon_signed_area,
)
from camera_math import camera_floor_polygon  # noqa: E402
from surface_analyzer import union_camera_floor_polygons  # noqa: E402


CORPUS_PATH = os.path.join(os.path.dirname(__file__), "..",
                           "tests", "fixtures", "cal", "corpus.json")


# ── Helpers ────────────────────────────────────────────────────────────

def _polygon_extent(poly):
    if len(poly) < 3:
        return 0.0, 0.0
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return max(xs) - min(xs), max(ys) - min(ys)


def _polygon_centroid(poly):
    n = len(poly)
    if n == 0:
        return (0.0, 0.0)
    return (sum(p[0] for p in poly) / n, sum(p[1] for p in poly) / n)


def _spread_metric(probe_points):
    """Max-NN / min-NN distance ratio — small ratio means probes are
    evenly spread; big ratio means they cluster in a sliver. We don't
    fail on any specific bound here (tight envelopes legitimately cluster);
    stash the value for inspection."""
    if len(probe_points) < 2:
        return None
    nn = []
    for i, a in enumerate(probe_points):
        best = float("inf")
        for j, b in enumerate(probe_points):
            if i == j:
                continue
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d < best:
                best = d
        nn.append(best)
    return max(nn) / max(1e-6, min(nn))


def _camera_visible_polys(cameras, stage_bounds=None):
    polys = []
    for cam in (cameras or []):
        try:
            poly = camera_floor_polygon(
                (cam["x"], cam["y"], cam["z"]),
                cam.get("rotation", [0, 0, 0]),
                cam.get("fovDeg", 90),
                stage_bounds=stage_bounds,
                floor_z=0)
            if poly:
                polys.append(poly)
        except Exception as e:
            print(f"  warn: camera {cam.get('id')} polygon raised {e}",
                  file=sys.stderr)
    return polys


# ── Per-case gates ─────────────────────────────────────────────────────

def _run_case(case, verbose=False):
    """Returns (ok: bool, failures: list[str], stats: dict)."""
    failures = []
    stats = {}
    expect = case.get("expect", {})
    fix = case["fixture"]
    fix_xyz = (fix["x"], fix["y"], fix["z"])
    rot = fix.get("rotation", [0, 0, 0])
    profile = case["profile"]
    floor_z = case.get("floorZ", 0)

    # 1. coverage_polygon non-degeneracy.
    poly = coverage_polygon(fix_xyz, rot, profile, floor_z)
    stats["coveragePolygonPoints"] = len(poly)
    x_ext, y_ext = _polygon_extent(poly)
    area = abs(_polygon_signed_area(poly)) if len(poly) >= 3 else 0.0
    cx, cy = _polygon_centroid(poly)
    centroid_d = math.hypot(cx - fix["x"], cy - fix["y"])
    stats["coveragePolygonXExtent"] = round(x_ext, 1)
    stats["coveragePolygonYExtent"] = round(y_ext, 1)
    stats["coveragePolygonAreaMm2"] = round(area, 1)
    stats["coveragePolygonCentroidDistance"] = round(centroid_d, 1)

    if "coveragePolygonMinXExtentMm" in expect:
        if x_ext < expect["coveragePolygonMinXExtentMm"]:
            failures.append(f"coverage x_extent {x_ext:.1f} < "
                            f"{expect['coveragePolygonMinXExtentMm']}")
    if "coveragePolygonMinYExtentMm" in expect:
        if y_ext < expect["coveragePolygonMinYExtentMm"]:
            failures.append(f"coverage y_extent {y_ext:.1f} < "
                            f"{expect['coveragePolygonMinYExtentMm']}")
    if "coveragePolygonMinAreaMm2" in expect:
        if area < expect["coveragePolygonMinAreaMm2"]:
            failures.append(f"coverage area {area:.1f} mm² < "
                            f"{expect['coveragePolygonMinAreaMm2']}")
    if "coveragePolygonCentroidMaxDistanceFromFixtureMm" in expect:
        if centroid_d > expect["coveragePolygonCentroidMaxDistanceFromFixtureMm"]:
            failures.append(f"coverage centroid {centroid_d:.0f} mm > "
                            f"{expect['coveragePolygonCentroidMaxDistanceFromFixtureMm']}")

    # 2. working_area / sample_grid (only when cameras provided).
    cam_polys = _camera_visible_polys(case.get("cameras") or [])
    cam_union = union_camera_floor_polygons(cam_polys)
    if cam_union and poly:
        wp = working_area(poly, cam_union, margin_mm=150)
        stats["workingPolyArea"] = round(
            abs(_polygon_signed_area(wp)), 1) if len(wp) >= 3 else 0.0
        if expect.get("workingPolyNonEmpty") and not wp:
            failures.append("working_poly empty but expected non-empty")
        probe_points = sample_grid(wp, n=16, min_edge_margin_mm=150)
        stats["probePoints"] = len(probe_points)
        stats["probeSpreadMaxOverMin"] = (round(_spread_metric(probe_points), 2)
                                          if len(probe_points) >= 2 else None)
        if "probePointsMin" in expect:
            if len(probe_points) < expect["probePointsMin"]:
                failures.append(f"probe points {len(probe_points)} < "
                                f"{expect['probePointsMin']}")
    else:
        stats["workingPolyArea"] = 0.0
        stats["probePoints"] = 0
        if expect.get("workingPolyNonEmpty"):
            failures.append("working_poly empty (no camera coverage) "
                            "but case expected non-empty")
        if expect.get("abortReason") == "no_camera_floor" and cam_polys:
            failures.append("expected no_camera_floor but camera polygons "
                            "are non-empty")

    # 3. solve_dmx_per_degree finiteness.
    home = case.get("home")
    sec = case.get("homeSecondary")
    solve_err = None
    solve_finite = None
    if home and sec is not None:
        try:
            est = solve_dmx_per_degree(
                home, sec, rot,
                profile.get("panRange", 540),
                profile.get("tiltRange", 270))
            solve_finite = (math.isfinite(est["panDmxPerDeg"])
                            and math.isfinite(est["tiltDmxPerDeg"]))
            stats["solvePanDmxPerDeg"] = round(est["panDmxPerDeg"], 3)
            stats["solveTiltDmxPerDeg"] = round(est["tiltDmxPerDeg"], 3)
            if expect.get("solveFinite") is False:
                failures.append("solve returned model but case expected error")
            if "solveSlopeBoundDmxPerDeg" in expect:
                bound = expect["solveSlopeBoundDmxPerDeg"]
                if abs(est["panDmxPerDeg"]) > bound:
                    failures.append(
                        f"|panDmxPerDeg|={abs(est['panDmxPerDeg']):.1f} > {bound}")
                if abs(est["tiltDmxPerDeg"]) > bound:
                    failures.append(
                        f"|tiltDmxPerDeg|={abs(est['tiltDmxPerDeg']):.1f} > {bound}")
            # angles_to_dmx round-trip exact at home.
            pdx, tdx = angles_to_dmx(0, 0, est)
            if pdx != home["panDmx16"] or tdx != home["tiltDmx16"]:
                failures.append(
                    f"angles_to_dmx(0,0) {(pdx, tdx)} != home "
                    f"{(home['panDmx16'], home['tiltDmx16'])}")
        except Exception as e:
            solve_err = str(e)
            solve_finite = False
            stats["solveError"] = solve_err
            if expect.get("solveFinite", True):
                failures.append(f"solve raised: {solve_err}")
            must = expect.get("solveErrorMustContain")
            if must and must not in solve_err:
                failures.append(f"solve error '{solve_err}' missing "
                                f"required token '{must}'")

    if expect.get("solveFinite") is False and solve_finite:
        failures.append("solve returned finite model but case expected error")

    return (not failures, failures, stats)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verbose", action="store_true",
                    help="print per-case stats")
    ap.add_argument("--corpus", default=CORPUS_PATH,
                    help="path to corpus.json")
    args = ap.parse_args()

    try:
        with open(args.corpus, "r", encoding="utf-8") as fh:
            corpus = json.load(fh)
    except Exception as e:
        print(f"FAIL — could not load corpus at {args.corpus}: {e}",
              file=sys.stderr)
        return 1

    cases = corpus.get("cases", [])
    if not cases:
        print("FAIL — corpus has no cases", file=sys.stderr)
        return 1

    print(f"#733 SMART pipeline emulator — {len(cases)} cases")
    print(f"corpus: {args.corpus}")
    print()
    n_pass = 0
    n_fail = 0
    for case in cases:
        cid = case.get("id", "<unnamed>")
        ok, failures, stats = _run_case(case, verbose=args.verbose)
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {cid}")
        if args.verbose:
            for k, v in stats.items():
                print(f"          {k}: {v}")
        if not ok:
            for line in failures:
                print(f"          - {line}", file=sys.stderr)
            n_fail += 1
        else:
            n_pass += 1

    print()
    print(f"{n_pass} passed, {n_fail} failed out of {len(cases)} cases")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
