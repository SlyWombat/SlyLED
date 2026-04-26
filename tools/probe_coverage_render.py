#!/usr/bin/env python3
"""Probe coverage renderer — top-down SVG of where a mover-cal run aimed.

Reads a cal-status NDJSON (produced by tools/cal_status_poller.py), pulls
fixture / camera / marker / home-anchor metadata from the orchestrator, then
projects each probe's pan/tilt through `pan_tilt_to_ray` to a floor-plane
hit. Renders the result as a self-contained SVG.

Usage:
    python3 tools/probe_coverage_render.py \
        --status docs/live-test-sessions/2026-04-26/cal-status-123917.ndjson \
        --orch http://localhost:8080 \
        --fid 17 \
        --out docs/live-test-sessions/2026-04-26/probe-coverage-123917.svg
"""

import argparse, json, math, sys, urllib.request
from pathlib import Path

# Reuse the orchestrator's IK helper.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "desktop" / "shared"))
from mover_calibrator import pan_tilt_to_ray


def http_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def floor_hit(fx_pos, pan_norm, tilt_norm, pan_range, tilt_range, rot,
              floor_z=0, inverted=False, home_pan_norm=None, home_tilt_norm=None):
    """Project beam ray to floor plane z=floor_z, using a HOME-RELATIVE
    IK where (home_pan, home_tilt) means "pointing along +Y horizontal."

    The default `pan_tilt_to_ray` assumes tilt=0.5 is horizontal — that's
    not the convention for this fixture's profile (operator-confirmed
    that home pan=0.6770 / tilt=0 corresponds to the layout-page rotation
    arrow direction, i.e. horizontal forward). Reframe relative to home.

    Returns (x, y, dz_dir) on hit, None when the ray parallels or aims
    away from the floor plane.
    """
    if home_pan_norm is None:
        return None
    # Profile-aware IK matching the 150W MH (and operator's) convention:
    #   - tilt_norm = 0 and tilt_norm = 1 are BOTH horizontal (the two ends
    #     of a 180°-tilt fixture's arc through down OR up).
    #   - tilt_norm * tilt_range = mechanical angle off horizontal-forward,
    #     swept down for inverted-mount, up for non-inverted.
    #   - pan_norm = home_pan_norm corresponds to +Y horizontal forward.
    mech_tilt_deg = tilt_norm * tilt_range
    delta_pan_deg = (pan_norm - home_pan_norm) * pan_range
    tilt_rad = math.radians(mech_tilt_deg)
    pan_rad = math.radians(delta_pan_deg)
    # Local (mount) frame after tilt: +Y forward → (0, cos(t), -sin(t))
    # for inverted (beam dips toward floor), or (0, cos(t), +sin(t)) for
    # non-inverted (beam rises toward ceiling).
    dy_local = math.cos(tilt_rad)
    dz_local = -math.sin(tilt_rad) if inverted else math.sin(tilt_rad)
    # Yaw about Z by delta_pan (positive = CW from above = beam to +X).
    dx = dy_local * math.sin(pan_rad)
    dy = dy_local * math.cos(pan_rad)
    dz = dz_local
    fx_x, fx_y, fx_z = fx_pos
    if abs(dz) < 1e-6:
        return None
    t = (floor_z - fx_z) / dz
    if t <= 0:
        return None
    return (fx_x + dx * t, fx_y + dy * t, dz)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True, help="cal-status NDJSON path")
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--fid", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--floor-z", type=float, default=0.0)
    args = ap.parse_args()

    layout = http_get(f"{args.orch}/api/layout")
    fixtures = layout.get("fixtures", [])
    children = {p["id"]: p for p in layout.get("children", [])}
    f = next((x for x in fixtures if x.get("id") == args.fid), None)
    if not f:
        print(f"fixture {args.fid} not found", file=sys.stderr); return 2
    pos = children.get(args.fid, {})
    fx_pos = (float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0)))
    rot = f.get("rotation", [0, 0, 0])

    # DMX profile lookup for pan/tilt range — 150W MH default 540/180.
    pan_range = 540.0
    # 150W MH-12ch — corrected to 180° per #682 (was 270° in the on-disk
    # profile JSON). User-confirmed: "tilt range of 180 means 0 and 180
    # are both on the horizon whether mount-inverted or not."
    tilt_range = 180.0
    home_pan_dmx16 = f.get("homePanDmx16")
    home_tilt_dmx16 = f.get("homeTiltDmx16")
    home_pan_norm = home_pan_dmx16 / 65535.0 if home_pan_dmx16 is not None else None
    home_tilt_norm = home_tilt_dmx16 / 65535.0 if home_tilt_dmx16 is not None else None

    cameras = []
    for c in fixtures:
        if c.get("fixtureType") != "camera":
            continue
        cp = children.get(c["id"], {})
        cameras.append({
            "id": c["id"], "name": c.get("name", ""),
            "x": float(cp.get("x", 0)), "y": float(cp.get("y", 0)),
            "z": float(cp.get("z", 0)),
            "fov": float(c.get("fovDeg", 90)),
            "rot": c.get("rotation", [0, 0, 0]),
        })

    markers = http_get(f"{args.orch}/api/aruco/markers").get("markers", [])

    space_meta = http_get(f"{args.orch}/api/space?meta=1")
    stage_w = float(space_meta.get("stageW", 4000))
    stage_d = float(space_meta.get("stageD", 4000))

    # Read probe records.
    probes = []
    seen_attempts = set()
    with open(args.status) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln: continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            attempt = d.get("probeAttempt")
            pan = d.get("probePan")
            tilt = d.get("probeTilt")
            dim = d.get("probeDimmer")
            if attempt is None or pan is None or tilt is None:
                continue
            # Dedupe on (pan, tilt) — multiple blink-on/off cycles per probe
            # generate several status records at the same aim. We want one
            # visual marker per distinct grid cell.
            key = (round(pan, 4), round(tilt, 4))
            if key in seen_attempts:
                continue
            seen_attempts.add(key)
            hit = floor_hit(fx_pos, pan, tilt, pan_range, tilt_range, rot,
                              args.floor_z, inverted=bool(f.get("mountedInverted")))
            probes.append({
                "attempt": attempt, "pan": pan, "tilt": tilt,
                "dim": dim, "hit": hit,
                "phase": d.get("phase"), "ts": d.get("ts"),
            })
    probes.sort(key=lambda p: p["attempt"])

    home_hit = None
    if home_pan_norm is not None and home_tilt_norm is not None:
        home_hit = floor_hit(fx_pos, home_pan_norm, home_tilt_norm,
                              pan_range, tilt_range, rot, args.floor_z,
                              inverted=bool(f.get("mountedInverted")))

    # ── SVG render ────────────────────────────────────────────────────
    # World coords (mm) → SVG pixels. X-right, Y-up (so Y_world maps to -Y_svg).
    margin = 60
    pad = 1500  # extra mm around stage so probes outside stage are visible
    x_min = min(0, *(c["x"] for c in cameras), fx_pos[0]) - pad
    x_max = max(stage_w, *(c["x"] for c in cameras), fx_pos[0]) + pad
    y_min = min(0, *(c["y"] for c in cameras), fx_pos[1]) - pad
    y_max = max(stage_d, *(c["y"] for c in cameras), fx_pos[1]) + pad
    if probes:
        for p in probes:
            if p["hit"]:
                x_min = min(x_min, p["hit"][0])
                x_max = max(x_max, p["hit"][0])
                y_min = min(y_min, p["hit"][1])
                y_max = max(y_max, p["hit"][1])
    if home_hit is not None:
        x_min = min(x_min, home_hit[0]); x_max = max(x_max, home_hit[0])
        y_min = min(y_min, home_hit[1]); y_max = max(y_max, home_hit[1])
    width_mm = x_max - x_min
    height_mm = y_max - y_min
    px_per_mm = 0.18
    svg_w = int(width_mm * px_per_mm) + 2 * margin
    svg_h = int(height_mm * px_per_mm) + 2 * margin

    def to_px(x, y):
        return (margin + (x - x_min) * px_per_mm,
                margin + (y_max - y) * px_per_mm)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" '
        f'viewBox="0 0 {svg_w} {svg_h}" font-family="ui-monospace,Menlo,monospace" font-size="11">',
        '<style>.lbl{fill:#cbd5e1}.dim{fill:#64748b}'
        '.stage{fill:none;stroke:#475569;stroke-width:1.5;stroke-dasharray:6 3}'
        '.fxt{fill:#7c3aed}.cam{fill:#0ea5e9;fill-opacity:.6;stroke:#38bdf8;stroke-width:1.2}'
        '.fov{fill:#0ea5e9;fill-opacity:.07;stroke:#0ea5e9;stroke-opacity:.35;stroke-width:1}'
        '.mk{fill:#fbbf24;stroke:#a16207;stroke-width:0.8}'
        '.home{fill:#fde047;stroke:#854d0e;stroke-width:1.5}'
        '.probe-line{stroke-width:1.4;fill:none;opacity:.85}'
        '.probe-line-conf{stroke:#22c55e}.probe-line-rej{stroke:#ef4444}.probe-line-pend{stroke:#94a3b8}'
        '.probe-hit{fill:#22c55e;stroke:#14532d;stroke-width:.6}.probe-hit-rej{fill:#ef4444}'
        '.probe-hit-pend{fill:#94a3b8}'
        '.probe-num{fill:#0f172a;font-weight:bold;font-size:9px}</style>',
        f'<rect x="0" y="0" width="{svg_w}" height="{svg_h}" fill="#0f172a"/>',
    ]

    # Stage rectangle (0,0)→(stage_w, stage_d)
    sx, sy = to_px(0, 0)
    ex, ey = to_px(stage_w, stage_d)
    parts.append(f'<rect class="stage" x="{min(sx,ex)}" y="{min(sy,ey)}" '
                  f'width="{abs(ex-sx)}" height="{abs(ey-sy)}"/>')
    parts.append(f'<text class="dim" x="{sx+4}" y="{ey+14}">stage 0,0</text>')
    parts.append(f'<text class="dim" x="{ex-110}" y="{sy-4}">stage {int(stage_w)},{int(stage_d)}</text>')

    # Camera FOV cones — approx as 2D wedge using fov + rotation[2] (yaw)
    for c in cameras:
        cx_px, cy_px = to_px(c["x"], c["y"])
        # rotation_from_layout convention: rz is yaw, rz>0 aims +X
        # but live rendering convention is approximate — use rz directly
        pan_yaw_deg = (c["rot"][2] if len(c["rot"]) > 2 else 0)
        # Camera's optical axis at yaw=0 points +Y (forward)
        # FOV span in degrees, half on each side
        half = c["fov"] / 2.0
        # Wedge length 4000mm
        L = 4000
        for sign in (-1, +1):
            theta = math.radians(pan_yaw_deg + sign * half + 90)  # +Y is 90° in std math frame
            tx = c["x"] + L * math.cos(theta)
            ty = c["y"] + L * math.sin(theta)
            tx_px, ty_px = to_px(tx, ty)
            # collect endpoints for polygon
        # Draw a triangle wedge using two endpoints + camera position
        a_theta = math.radians(pan_yaw_deg - half + 90)
        b_theta = math.radians(pan_yaw_deg + half + 90)
        ax, ay = c["x"] + L * math.cos(a_theta), c["y"] + L * math.sin(a_theta)
        bx, by = c["x"] + L * math.cos(b_theta), c["y"] + L * math.sin(b_theta)
        ax_px, ay_px = to_px(ax, ay)
        bx_px, by_px = to_px(bx, by)
        parts.append(f'<polygon class="fov" points="{cx_px},{cy_px} {ax_px},{ay_px} {bx_px},{by_px}"/>')
        parts.append(f'<circle class="cam" cx="{cx_px}" cy="{cy_px}" r="6"/>')
        parts.append(f'<text class="lbl" x="{cx_px+9}" y="{cy_px-6}">cam #{c["id"]} {c["name"]}</text>')

    # Surveyed markers
    for m in markers:
        mx_px, my_px = to_px(float(m["x"]), float(m["y"]))
        is_floor = abs(float(m.get("z", 0) or 0)) < 50
        color = "mk" if is_floor else "dim"
        parts.append(f'<circle class="{color}" cx="{mx_px}" cy="{my_px}" r="5"/>')
        parts.append(f'<text class="lbl" x="{mx_px+8}" y="{my_px+4}">id {m["id"]} {m.get("label","")}</text>')

    # Fixture
    fxx, fxy = to_px(fx_pos[0], fx_pos[1])
    parts.append(f'<polygon class="fxt" points="{fxx},{fxy-9} {fxx-7},{fxy+6} {fxx+7},{fxy+6}"/>')
    parts.append(f'<text class="lbl" x="{fxx+11}" y="{fxy+4}">fixture #{args.fid} (z={int(fx_pos[2])}mm)</text>')

    # Home anchor projected hit
    if home_hit is not None:
        hx_px, hy_px = to_px(home_hit[0], home_hit[1])
        # Star
        pts = []
        for k in range(10):
            r = 12 if k % 2 == 0 else 5
            theta = math.radians(-90 + k * 36)
            pts.append(f"{hx_px + r * math.cos(theta)},{hy_px + r * math.sin(theta)}")
        parts.append(f'<polygon class="home" points="{" ".join(pts)}"/>')
        parts.append(f'<text class="lbl" x="{hx_px+15}" y="{hy_px+4}">'
                      f'home aim ({int(home_hit[0])},{int(home_hit[1])})</text>')
        # Line from fixture to home aim
        parts.append(f'<line x1="{fxx}" y1="{fxy}" x2="{hx_px}" y2="{hy_px}" '
                      f'stroke="#fde047" stroke-width="1.5" stroke-opacity=".5" stroke-dasharray="4 3"/>')

    # Probes — line from fixture + dot at floor hit
    for p in probes:
        if not p["hit"]:
            continue
        hx_px, hy_px = to_px(p["hit"][0], p["hit"][1])
        # All probes from this trace were captured but not classified yet
        cls = "probe-line-pend"; dotcls = "probe-hit-pend"
        parts.append(f'<line class="probe-line {cls}" x1="{fxx}" y1="{fxy}" x2="{hx_px}" y2="{hy_px}"/>')
        parts.append(f'<circle class="{dotcls}" cx="{hx_px}" cy="{hy_px}" r="6"/>')
        parts.append(f'<text class="probe-num" x="{hx_px-3}" y="{hy_px+3}">{p["attempt"]}</text>')

    # Legend
    legend_y = svg_h - 70
    parts.append(f'<g transform="translate({margin},{legend_y})">')
    parts.append('<rect width="540" height="58" fill="#1e293b" fill-opacity=".85" rx="4"/>')
    parts.append('<text class="lbl" x="10" y="18">'
                  f'fixture #{args.fid}  '
                  f'home pan_dmx16={home_pan_dmx16} tilt_dmx16={home_tilt_dmx16}  '
                  f'pan_range={int(pan_range)}° tilt_range={int(tilt_range)}°</text>')
    parts.append(f'<text class="dim" x="10" y="36">{len(probes)} probes captured · floor z={int(args.floor_z)}mm · '
                  f'rotation={rot} · mountedInverted={f.get("mountedInverted")}</text>')
    parts.append('<text class="dim" x="10" y="51">'
                  '<tspan fill="#fde047">★</tspan> home aim   '
                  '<tspan fill="#fbbf24">●</tspan> floor markers   '
                  '<tspan fill="#0ea5e9">●</tspan> cameras   '
                  '<tspan fill="#94a3b8">●</tspan> probe hit (numbered)</text>')
    parts.append('</g>')

    parts.append('</svg>')
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {out_path}  ({svg_w}x{svg_h}, {len(probes)} probes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
