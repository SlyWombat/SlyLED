"""#631 — fusion-pass scaling benchmark.

Synthetic stress test for _fuse_temporal_objects at 2 / 4 / 8 / 16 camera
counts. Generates N cameras × K synthetic people and times the fusion pass.

The fusion pass is O(N×K²) in the worst case (pairwise distance check
inside the cluster builder); we want to confirm that remains usable at
realistic camera counts (≤16 cameras × ~5 people = 80 placements fused
in well under one tracker-push cycle, i.e. 500 ms).

Run:
    python -X utf8 tests/test_fusion_scaling.py

Exits 0 if every scale keeps fusion under 50 ms (10× headroom vs the
2 Hz tracker push), 1 otherwise.
"""

import os
import random
import sys
import time

_SHARED = os.path.join(os.path.dirname(__file__), "..", "desktop", "shared")
sys.path.insert(0, os.path.abspath(_SHARED))


def _run_fusion_ntimes(ps, cam_count, people_count, trials=10):
    """Seed ps._temporal_objects with cam_count × people_count entries
    (cameras all see the same set of people at slightly jittered stage
    positions) and measure the fusion-pass wall time."""
    rng = random.Random(0x631)
    stage_w = 6000.0
    stage_d = 8000.0
    elapsed = []
    for trial in range(trials):
        ps._temporal_objects.clear()
        # Synthesise person ground-truth positions.
        people = [(rng.uniform(500, stage_w - 500),
                   rng.uniform(500, stage_d - 500))
                  for _ in range(people_count)]
        now = time.time()
        nxt_id = 20000
        for ci in range(cam_count):
            for pi, (gt_x, gt_y) in enumerate(people):
                # Each camera places the person at the GT position plus a
                # small jitter (simulating imperfect per-camera placement).
                jx = rng.uniform(-100, 100)
                jy = rng.uniform(-100, 100)
                ps._temporal_objects.append({
                    "id": nxt_id,
                    "name": f"p{pi}-c{ci}",
                    "objectType": "person",
                    "_temporal": True,
                    "_method": "homography",
                    "_cameraId": 10000 + ci,
                    "_yoloConfidence": 0.8,
                    "ttl": 2.0,
                    "_expiresAt": now + 2.0,
                    "transform": {"pos": [gt_x + jx, gt_y + jy, 850.0],
                                  "rot": [0, 0, 0],
                                  "scale": [500, 1700, 500]},
                })
                nxt_id += 1
        # Time just the fusion pass (not the reap, which is fast anyway).
        t0 = time.perf_counter()
        ps._fuse_temporal_objects()
        t1 = time.perf_counter()
        elapsed.append((t1 - t0) * 1000.0)  # ms
    return elapsed


def main():
    import parent_server as ps  # noqa
    print("=== #631 fusion-pass scaling benchmark ===")
    # Camera-count sweep. People count fixed at 5 (realistic stage scene).
    scales = [(2, 5), (4, 5), (8, 5), (16, 5), (16, 10)]
    limit_ms = 50.0
    fails = 0
    for cam_count, people_count in scales:
        elapsed = _run_fusion_ntimes(ps, cam_count, people_count, trials=10)
        median = sorted(elapsed)[len(elapsed) // 2]
        worst = max(elapsed)
        placements = cam_count * people_count
        print(f"  {cam_count:2d} cams × {people_count:2d} people "
              f"({placements:3d} placements): "
              f"median={median:6.2f} ms  worst={worst:6.2f} ms  "
              f"input→fused={placements}→{len(ps._temporal_objects)}")
        if worst > limit_ms:
            fails += 1
            print(f"    FAIL: worst {worst:.2f} ms exceeds {limit_ms:.0f} ms budget")
    ps._temporal_objects.clear()
    if fails:
        print(f"\n=== #631 benchmark: {fails} scale(s) over budget ===")
        return 1
    print(f"\n=== #631 benchmark: all scales within {limit_ms:.0f} ms ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
