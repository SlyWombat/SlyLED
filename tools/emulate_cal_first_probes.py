"""Re-emulate using the OPERATOR-VALIDATED IK from tools/probe_coverage_3d.py
to predict where the beam will ACTUALLY land for each probe (vs what the
cal's broken `_ray_floor_hit` thinks)."""
import json, math, os, sys, urllib.request
sys.path.insert(0, "/home/sly/slyled2/desktop/shared")
sys.path.insert(0, "/home/sly/slyled2/tools")
from camera_math import camera_floor_polygon
from mover_calibrator import (_adaptive_coarse_steps, _ray_floor_hit,
                                _camera_visible_tilt_band, _point_in_polygon)
from probe_coverage_3d import floor_hit as good_floor_hit

ORCH = "http://localhost:8080"
def http_get(p): return json.loads(urllib.request.urlopen(f"{ORCH}{p}", timeout=5).read())

layout = http_get("/api/layout")
fixtures = layout.get("fixtures") or []
cams_api = http_get("/api/cameras") or []
with open("/home/sly/slyled2/desktop/shared/data/stage.json") as f: stage_m = json.load(f)
sb = {"w": int((stage_m.get("w") or 3.0) * 1000),
      "d": int((stage_m.get("d") or 4.0) * 1000),
      "h": int((stage_m.get("h") or 2.0) * 1000)}

f17 = next(f for f in fixtures if f["id"] == 17)
fx_pos = (f17["x"], f17["y"], f17["z"])
fx_rot = f17.get("rotation") or [0,0,0]
home_pan = f17["homePanDmx16"] / 65535.0
home_tilt = f17["homeTiltDmx16"] / 65535.0
inv = bool(f17.get("mountedInverted"))
PR, TR = 540.0, 180.0

# camera polygons
cams = []
polys = []
for c in cams_api:
    if c.get("id") not in (12,13,16): continue
    le = next((f for f in fixtures if f["id"] == c["id"]), None)
    if not le: continue
    pos = [le["x"], le["y"], le["z"]]
    rot = le.get("rotation")
    poly = camera_floor_polygon(pos, rot, c.get("fovDeg", 90),
                                stage_bounds=sb, floor_z=0.0)
    if poly:
        cams.append({"id": c["id"], "pos": pos, "rotation": rot})
        polys.append(poly)

# build the same grid the cal will build
ps, ts = _adaptive_coarse_steps(PR, TR, 12.0)
band = _camera_visible_tilt_band(fx_pos, fx_rot, home_pan, PR, TR, inv, polys)
tlo, thi = band; tspan = (thi - tlo) / max(1, ts)
pan_frac = min(360.0, PR) / PR
half_pan = pan_frac / 2
pan_lo = max(0.0, min(1.0 - pan_frac, home_pan - half_pan))
pspan = pan_frac / max(1, ps)

grid = []
for i in range(ps):
    p = pan_lo + (i + 0.5) * pspan
    for j in range(ts):
        t = tlo + (j + 0.5) * tspan
        grid.append((p, t))

# Sort by partition + lex distance from seed (matches code at lines 1148-1194)
def grid_filter_broken(p, t):
    """Mirror of cal's filter — uses _ray_floor_hit which IGNORES pan."""
    h = _ray_floor_hit(fx_pos, fx_rot, p, t, PR, TR, mounted_inverted=inv)
    if not h: return False
    return any(_point_in_polygon(h, poly) for poly in polys)

def grid_filter_correct(p, t):
    """What the filter SHOULD be — uses operator-validated IK."""
    h = good_floor_hit(fx_pos, p, t, PR, TR, inverted=inv, home_pan_norm=home_pan)
    if not h: return False
    return any(_point_in_polygon((h[0], h[1]), poly) for poly in polys)

key = lambda xy: (abs(xy[0] - home_pan), abs(xy[1] - home_tilt))
inside, outside = [], []
for pt in grid:
    (inside if grid_filter_broken(*pt) else outside).append(pt)
inside.sort(key=key); outside.sort(key=key)
sorted_grid = inside + outside

# Same partition with the CORRECT IK
inside_c, outside_c = [], []
for pt in grid:
    (inside_c if grid_filter_correct(*pt) else outside_c).append(pt)
inside_c.sort(key=key); outside_c.sort(key=key)
sorted_grid_correct = inside_c + outside_c

print(f"=== first 5 probes (cal's order, using _ray_floor_hit AS SHIPPED) ===")
print(f"{'#':>2} {'pan':>7} {'tilt':>7}  {'cal-IK floor':>20}  {'TRUE floor':>20}  {'on-stage(true)':>14}  {'cam-FOV(true)':>14}")
print("-" * 110)
for n, (p, t) in enumerate(sorted_grid[:5], 1):
    cal_h = _ray_floor_hit(fx_pos, fx_rot, p, t, PR, TR, mounted_inverted=inv)
    true_h = good_floor_hit(fx_pos, p, t, PR, TR, inverted=inv, home_pan_norm=home_pan)
    cal_str = f"({cal_h[0]:6.0f}, {cal_h[1]:6.0f})" if cal_h else "no-hit"
    true_str = f"({true_h[0]:6.0f}, {true_h[1]:6.0f})" if true_h else "no-hit"
    on_stage = (true_h and 0 <= true_h[0] <= sb['w'] and 0 <= true_h[1] <= sb['d'])
    in_cam = []
    if true_h:
        for cam, poly in zip(cams, polys):
            if _point_in_polygon((true_h[0], true_h[1]), poly):
                in_cam.append(cam["id"])
    print(f"{n:>2} {p:>7.4f} {t:>7.4f}  {cal_str:>20}  {true_str:>20}  "
          f"{'YES' if on_stage else 'NO':>14}  {str(in_cam) if in_cam else '[]':>14}")

print()
print(f"=== partition counts ===")
print(f"  cal's IK (broken):  inside={len(inside)}/{len(grid)}, outside={len(outside)}")
print(f"  true IK:            inside={len(inside_c)}/{len(grid)}, outside={len(outside_c)}")
print()
print(f"=== first 5 probes the cal would visit IF it used the correct IK ===")
print(f"{'#':>2} {'pan':>7} {'tilt':>7}  {'true floor':>20}  {'on-stage':>10}  {'cam-FOV':>10}")
print("-" * 75)
for n, (p, t) in enumerate(sorted_grid_correct[:5], 1):
    h = good_floor_hit(fx_pos, p, t, PR, TR, inverted=inv, home_pan_norm=home_pan)
    h_str = f"({h[0]:6.0f}, {h[1]:6.0f})" if h else "no-hit"
    on_stage = (h and 0 <= h[0] <= sb['w'] and 0 <= h[1] <= sb['d'])
    in_cam = []
    if h:
        for cam, poly in zip(cams, polys):
            if _point_in_polygon((h[0], h[1]), poly):
                in_cam.append(cam["id"])
    print(f"{n:>2} {p:>7.4f} {t:>7.4f}  {h_str:>20}  {'YES' if on_stage else 'NO':>10}  {str(in_cam) if in_cam else '[]':>10}")
