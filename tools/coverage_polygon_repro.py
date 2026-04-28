#!/usr/bin/env python3
"""coverage_polygon_repro.py — #731 regression repro.

Reproduces the basement fid #17 case where ``coverage_polygon``
returned a 1D-line polygon (degenerate y_extent ≈ 0.08 mm) for a
sideways-mounted fixture with panRange ≥ 360°. Pre-#731 the perimeter-
only sampler missed the cone interior; post-#731 the interior grid
fills the body and the convex hull captures the real footprint.

Exit codes:
  0 — polygon is healthy (extent + area + centroid pass).
  1 — degenerate polygon (the bug is present).

Run: ``python tools/coverage_polygon_repro.py``.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "desktop", "shared"))

from coverage_math import coverage_polygon, _polygon_signed_area  # noqa: E402


# Fid #17 (basement 150W MH Stage Right) inputs captured 2026-04-28
# when /smart/preview returned abortReason: "no_overlap" because the
# coverage polygon had collapsed.
FIXTURE_XYZ = (600, 0, 1760)
ROTATION = [0, 0, 0]
PROFILE = {
    "panRange": 540,
    "tiltRange": 180,
    "tiltOffsetDmx16": 32768,
    "tiltUp": False,
}
FLOOR_Z = -4.0


def main():
    poly = coverage_polygon(FIXTURE_XYZ, ROTATION, PROFILE, FLOOR_Z)
    if not poly or len(poly) < 3:
        print("FAIL — polygon empty / fewer than 3 points", file=sys.stderr)
        print(f"  poly len={len(poly) if poly else 0}", file=sys.stderr)
        return 1

    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x_extent = max(xs) - min(xs)
    y_extent = max(ys) - min(ys)
    area = abs(_polygon_signed_area(poly))
    cx = sum(xs) / len(poly)
    cy = sum(ys) / len(poly)
    centroid_d = math.hypot(cx - FIXTURE_XYZ[0], cy - FIXTURE_XYZ[1])

    print(f"points     : {len(poly)}")
    print(f"x range    : {min(xs):.1f} .. {max(xs):.1f}  (extent {x_extent:.1f} mm)")
    print(f"y range    : {min(ys):.3f} .. {max(ys):.3f}  (extent {y_extent:.3f} mm)")
    print(f"area       : {area:.1f} mm²")
    print(f"centroid   : ({cx:.1f}, {cy:.1f}) — {centroid_d:.0f} mm from fixture")

    # Non-degeneracy gates (#731): 2D extent + finite area + centroid
    # near fixture XY. Pre-fix x_extent ≈ 24,500 mm, y_extent ≈ 0.08
    # mm, area ≈ 1,000 mm² — the y/area gates would have caught this.
    failed = []
    if x_extent < 100:
        failed.append(f"x_extent {x_extent:.1f} < 100 mm")
    if y_extent < 100:
        failed.append(f"y_extent {y_extent:.3f} < 100 mm")
    if area < 10_000:
        failed.append(f"area {area:.1f} < 10,000 mm²")
    if centroid_d > 5_000:
        failed.append(f"centroid_distance {centroid_d:.0f} > 5,000 mm")

    if failed:
        print("\nFAIL — degenerate polygon (#731 bug present):", file=sys.stderr)
        for line in failed:
            print(f"  {line}", file=sys.stderr)
        return 1

    print("\nPASS — polygon healthy (#731 fixed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
