"""Compare floor detection across all 4 scan methods.

For each method (lite, mono, zoedepth, stereo):
  1. Clear existing point cloud
  2. Run the scan via its dedicated endpoint
  3. Pull the point cloud + run surface_analyzer's floor detector
  4. Report:
       - total points
       - Z-distribution (min/p10/median/p90/max)
       - points near the floor plane (|z| < 100mm)
       - detected floor z-height + RANSAC quality
       - % of points below z=-50 (below-floor anomalies)

Compared against stage dimensions from /api/stage.
"""
import os, sys, time, json, urllib.request, urllib.error

BASE = "http://localhost:8080"
OUT = "/mnt/d/temp/live-test-session"

def req(method, path, body=None, timeout=180):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method,
                                headers={"Content-Type": "application/json"} if body else {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors='ignore')
        try:    return json.loads(body)
        except: return {"httpErr": e.code, "body": body[:200]}

stage = req("GET", "/api/stage")
print(f"Stage: W={stage['w']}m  H(Z)={stage['h']}m  D(Y)={stage['d']}m")
print(f"  expected floor plane Z=0, ceiling Z={stage['h']*1000:.0f}mm")

def summarize_z(zs):
    zs = sorted(zs); n = len(zs)
    if n == 0: return {}
    return {
        "n": n,
        "min": zs[0], "p10": zs[n//10], "med": zs[n//2],
        "p90": zs[9*n//10], "max": zs[-1],
    }

def analyze(label):
    """Pull the current cloud + run floor detector."""
    cloud = req("GET", "/api/space")
    pts = cloud.get("points", [])
    if not pts:
        return {"label": label, "err": "no points returned"}
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]; zs = [p[2] for p in pts]
    ground_ish = [p for p in pts if abs(p[2]) < 100]    # ± 100mm of floor
    below = [p for p in pts if p[2] < -50]
    in_stage = [p for p in pts
                if -100 <= p[0] <= stage['w']*1000+100
                and -100 <= p[1] <= stage['d']*1000+100
                and -100 <= p[2] <= stage['h']*1000+100]

    # Ask the surface analyzer for a floor plane
    surf = req("POST", "/api/space/analyze", {})
    floor = (surf or {}).get("surfaces", {}).get("floor") if isinstance(surf, dict) else None
    if floor is None and isinstance(surf, dict):
        # Sometimes /api/space/analyze returns surfaces under top-level key
        floor = surf.get("floor")

    return {
        "label": label,
        "total": len(pts),
        "xSummary": summarize_z(xs),
        "ySummary": summarize_z(ys),
        "zSummary": summarize_z(zs),
        "withinFloorBand_|z|<100": len(ground_ish),
        "belowFloor_z<-50": len(below),
        "inStageBox": len(in_stage),
        "pctInStage": round(100*len(in_stage)/len(pts), 1),
        "pctBelowFloor": round(100*len(below)/len(pts), 1),
        "pctInFloorBand": round(100*len(ground_ish)/len(pts), 1),
        "floorDetection": floor,
    }

METHODS = [
    ("lite",     "Lite (layout synthesis)",  "POST", "/api/space/scan/lite",      {}),
    ("mono",     "Mono DA-V2 Metric (Pi)",   "POST", "/api/space/scan",           {"maxPointsPerCamera": 5000, "lighting": "blackout"}),
    ("zoedepth", "ZoeDepth (host)",          "POST", "/api/space/scan/zoedepth",  {"maxPoints": 5000, "lighting": "blackout"}),
    ("stereo",   "Stereo ORB",               "POST", "/api/space/scan/stereo",    {"cameras": [12, 13], "resolution": [1920,1080], "lighting": "blackout"}),
]

results = {}
for key, label, meth, path, body in METHODS:
    print(f"\n=== {label} ===")
    # Clear prior cloud
    req("DELETE", "/api/space")
    t0 = time.monotonic()
    r = req(meth, path, body)
    if r is None or not r.get("ok"):
        print(f"  scan failed: {r}")
        continue
    # Mono needs a poll loop
    if key == "mono" and r.get("pending"):
        for _ in range(180):
            s = req("GET", "/api/space/scan/status")
            if s and not s.get("running"):
                break
            time.sleep(1)
    elapsed = time.monotonic() - t0

    res = analyze(label)
    res["elapsed_s"] = round(elapsed, 1)
    results[key] = res

    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  total: {res.get('total')} points")
    z = res.get("zSummary", {})
    print(f"  Z(mm): min={z.get('min',0):.0f} p10={z.get('p10',0):.0f} med={z.get('med',0):.0f} p90={z.get('p90',0):.0f} max={z.get('max',0):.0f}")
    print(f"  in-stage-box: {res['inStageBox']} ({res['pctInStage']}%)")
    print(f"  below-floor z<-50: {res['belowFloor_z<-50']} ({res['pctBelowFloor']}%)")
    print(f"  near-floor |z|<100: {res['withinFloorBand_|z|<100']} ({res['pctInFloorBand']}%)")
    floor = res.get("floorDetection")
    if isinstance(floor, dict):
        print(f"  FLOOR DETECTED: z={floor.get('z')}mm  normal={floor.get('normal')}  inliers={floor.get('inliers')}")
    else:
        print(f"  FLOOR DETECTED: NO (surfaces={floor})")

print("\n" + "="*70)
print("COMPARISON TABLE")
print("="*70)
print(f"{'Method':<25} {'Points':>7} {'InStage%':>9} {'BelowFloor%':>13} {'NearFloor%':>12} {'FloorZ':>8}")
for key, r in results.items():
    floor = r.get("floorDetection")
    fz = floor.get("z") if isinstance(floor, dict) else "-"
    fz_str = f"{fz:.0f}" if isinstance(fz, (int, float)) else str(fz)
    print(f"{r['label']:<25} {r.get('total',0):>7} {r.get('pctInStage',0):>8.1f}% {r.get('pctBelowFloor',0):>12.1f}% {r.get('pctInFloorBand',0):>11.1f}% {fz_str:>8}")

with open(os.path.join(OUT, "floor-detection-results.json"), "w") as f:
    json.dump(results, f, indent=2, default=str)
print(f"\nJSON saved: {OUT}/floor-detection-results.json")
