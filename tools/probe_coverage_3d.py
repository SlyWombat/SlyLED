#!/usr/bin/env python3
"""3D probe coverage renderer — emits a self-contained HTML using Three.js
that mirrors the SPA dashboard viewport: stage box, fixture, cameras with
FOV cones, ArUco markers on the floor, and one ray per probe with the
floor hit highlighted. Numbered, colour-coded by status.

Usage:
    python3 tools/probe_coverage_3d.py \
        --status docs/live-test-sessions/2026-04-26/cal-status-141633.ndjson \
        --orch http://localhost:8080 --fid 17 \
        --out docs/live-test-sessions/2026-04-26/probe-coverage-141633.html
"""
import argparse, json, math, os, sys, urllib.request
from pathlib import Path


def http_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def floor_hit(fx_pos, pan_norm, tilt_norm, pan_range, tilt_range,
              floor_z=0, inverted=True, home_pan_norm=None):
    """Profile-aware IK matching operator-confirmed convention:
    tilt=0 and tilt=1 are both horizontal; tilt=0.5 is straight down
    (inverted) or up (non-inverted). pan=home_pan_norm is +Y forward.
    """
    if home_pan_norm is None:
        return None
    mech_tilt_deg = tilt_norm * tilt_range
    delta_pan_deg = (pan_norm - home_pan_norm) * pan_range
    tilt_rad = math.radians(mech_tilt_deg)
    pan_rad = math.radians(delta_pan_deg)
    dy_local = math.cos(tilt_rad)
    dz_local = -math.sin(tilt_rad) if inverted else math.sin(tilt_rad)
    # Pan handedness: CCW from above (operator-confirmed 2026-04-26 by
    # driving fixture #17 to pan_off +57°/+67° and observing the beam
    # move toward stage-right, i.e. lower X in this rig's convention).
    dx = -dy_local * math.sin(pan_rad)
    dy = dy_local * math.cos(pan_rad)
    dz = dz_local
    if abs(dz) < 1e-6:
        return None
    t = (floor_z - fx_pos[2]) / dz
    if t <= 0:
        return None
    return (fx_pos[0] + dx * t, fx_pos[1] + dy * t, floor_z)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", required=True)
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--fid", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    layout = http_get(f"{args.orch}/api/layout")
    fixtures = layout.get("fixtures", [])
    children = {p["id"]: p for p in layout.get("children", [])}
    f = next((x for x in fixtures if x.get("id") == args.fid), None)
    if not f:
        print(f"fixture {args.fid} not found", file=sys.stderr); return 2
    pos = children.get(args.fid, {})
    fx_pos = (float(pos.get("x", 0)), float(pos.get("y", 0)), float(pos.get("z", 0)))
    pan_range = 540.0
    tilt_range = 180.0
    home_pan = f.get("homePanDmx16")
    home_tilt = f.get("homeTiltDmx16")
    home_pan_norm = home_pan / 65535.0 if home_pan is not None else None
    inverted = bool(f.get("mountedInverted"))

    cameras = []
    camera_floor_polygon = None
    for shared_dir in (
        "/home/sly/slyled2/desktop/shared",
        os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "desktop", "shared"),
    ):
        try:
            if shared_dir not in sys.path:
                sys.path.insert(0, shared_dir)
            from camera_math import camera_floor_polygon as _cfp
            camera_floor_polygon = _cfp
            break
        except Exception:
            continue
    for c in fixtures:
        if c.get("fixtureType") != "camera":
            continue
        cp = children.get(c["id"], {})
        cam_pos = [float(cp.get("x", 0)), float(cp.get("y", 0)),
                    float(cp.get("z", 0))]
        cam_rot = c.get("rotation", [0, 0, 0])
        cam_fov = float(c.get("fovDeg", 90))
        floor_poly = []
        if camera_floor_polygon is not None:
            try:
                floor_poly = camera_floor_polygon(
                    cam_pos, cam_rot, cam_fov,
                    stage_bounds={"w": 1e9, "d": 1e9, "h": 1e9},
                    floor_z=0.0)
                floor_poly = [[float(x), float(y)] for x, y in floor_poly]
            except Exception:
                floor_poly = []
        cameras.append({
            "id": c["id"], "name": c.get("name", ""),
            "x": cam_pos[0], "y": cam_pos[1], "z": cam_pos[2],
            "fov": cam_fov, "rot": cam_rot,
            "floorPolygon": floor_poly,
        })

    markers = http_get(f"{args.orch}/api/aruco/markers").get("markers", [])

    space_meta = http_get(f"{args.orch}/api/space?meta=1")
    stage_w = float(space_meta.get("stageW", 4000))
    stage_d = float(space_meta.get("stageD", 4000))
    stage_h = float(space_meta.get("stageH", 2000))

    # Read probes — also capture phase / "Beam found" candidates.
    probes = []
    seen = set()
    candidate_attempts = set()
    with open(args.status) as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln: continue
            try: d = json.loads(ln)
            except: continue
            phase = d.get("phase") or ""
            msg = d.get("message") or ""
            attempt = d.get("probeAttempt")
            pan = d.get("probePan")
            tilt = d.get("probeTilt")
            if attempt is None or pan is None or tilt is None:
                continue
            if "Beam found at probe" in msg:
                # Extract probe number from message like "Beam found at probe 4/24"
                import re
                m = re.search(r"probe (\d+)/", msg)
                if m:
                    candidate_attempts.add(int(m.group(1)))
            key = (round(pan, 4), round(tilt, 4))
            if key in seen:
                continue
            seen.add(key)
            hit = floor_hit(fx_pos, pan, tilt, pan_range, tilt_range,
                             floor_z=0, inverted=inverted,
                             home_pan_norm=home_pan_norm)
            probes.append({
                "n": len(probes) + 1,
                "attempt": attempt,
                "pan": pan, "tilt": tilt,
                "panDeg": pan * pan_range,
                "tiltDeg": tilt * tilt_range,
                "panOffHomeDeg": (pan - (home_pan_norm or 0.5)) * pan_range,
                "hit": hit,
                "phase": phase,
            })

    home_hit = None
    home_dir = None
    if home_pan_norm is not None:
        home_tilt_norm = (home_tilt or 0) / 65535.0
        home_hit = floor_hit(fx_pos, home_pan_norm, home_tilt_norm,
                              pan_range, tilt_range, floor_z=0,
                              inverted=inverted, home_pan_norm=home_pan_norm)
        # Always compute home direction unit vector — even when ray is
        # horizontal and never intersects the floor, the operator wants to
        # see where the head is aiming.
        mech_tilt_deg = home_tilt_norm * tilt_range
        tilt_rad = math.radians(mech_tilt_deg)
        # delta_pan is 0 by definition at home
        dy_local = math.cos(tilt_rad)
        dz_local = -math.sin(tilt_rad) if inverted else math.sin(tilt_rad)
        home_dir = [0.0, dy_local, dz_local]  # (dx=0, dy, dz) in world frame

    # Per-probe status: candidate (camera saw beam) vs probed (no detect).
    # "Last" probe gets its own marker.
    last_probe_n = max((p["n"] for p in probes), default=None)
    for p in probes:
        # Map by grid probe number from the message — but our enumeration is
        # based on unique (pan, tilt) order which usually matches the cal's
        # grid probe number. Cross-reference candidate_attempts against p["n"].
        p["candidate"] = p["n"] in candidate_attempts
        p["last"] = p["n"] == last_probe_n
        x, y = fx_pos[0], fx_pos[1]
        if p["hit"]:
            x, y = p["hit"][0], p["hit"][1]
        p["onStage"] = (0 <= x <= stage_w and 0 <= y <= stage_d)

    payload = {
        "stage": {"w": stage_w, "d": stage_d, "h": stage_h},
        "fixture": {
            "id": args.fid, "name": f.get("name", ""),
            "pos": list(fx_pos),
            "homePanDmx16": home_pan, "homeTiltDmx16": home_tilt,
            "homeHit": list(home_hit) if home_hit else None,
            "homeDir": home_dir,
            "panRange": pan_range, "tiltRange": tilt_range,
            "inverted": inverted,
        },
        "cameras": cameras,
        "markers": [{"id": m["id"], "label": m.get("label", ""),
                     "x": float(m["x"]), "y": float(m["y"]),
                     "z": float(m.get("z", 0) or 0)} for m in markers],
        "probes": probes,
    }

    # Optional: pull point cloud from /api/space and include surfaces.
    # Truncate to ~3000 points to keep the HTML size reasonable.
    try:
        space = http_get(f"{args.orch}/api/space")
        pts = space.get("points", []) or []
        if len(pts) > 3000:
            step = max(1, len(pts) // 3000)
            pts = pts[::step]
        payload["pointCloud"] = pts
        payload["surfaces"] = space.get("surfaces") or {}
    except Exception:
        payload["pointCloud"] = []
        payload["surfaces"] = {}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_html_template(payload), encoding="utf-8")
    candidates_count = sum(1 for p in probes if p["candidate"])
    print(f"wrote {out_path}  ({len(probes)} probes, {candidates_count} candidates)")
    return 0


def _html_template(payload):
    data_json = json.dumps(payload, indent=2)
    return """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Probe coverage 3D — fixture %(fid)s</title>
<style>
body{margin:0;background:#0f172a;color:#e2e8f0;font-family:ui-monospace,Menlo,monospace;font-size:12px}
#hud{position:fixed;top:8px;left:8px;background:#1e293bcc;padding:10px 12px;border-radius:6px;
     max-width:420px;line-height:1.5}
#hud h2{margin:0 0 6px;font-size:13px;color:#fde047}
#hud .row{display:flex;justify-content:space-between}
#legend{position:fixed;bottom:8px;left:8px;background:#1e293bcc;padding:8px 10px;border-radius:6px}
#legend .sw{display:inline-block;width:10px;height:10px;border-radius:50%%;margin-right:6px;vertical-align:middle}
#tbl{position:fixed;top:8px;right:8px;background:#0f172af0;border:1px solid #334155;border-radius:6px;
     max-height:88vh;overflow-y:auto;font-size:11px;padding:6px 10px}
#tbl table{border-collapse:collapse}
#tbl th{position:sticky;top:0;background:#1e293b;text-align:left;padding:3px 6px}
#tbl td{padding:2px 6px;border-bottom:1px solid #1e293b40}
#tbl tr.cand td{color:#fbbf24}
#tbl tr.last td{color:#22c55e;font-weight:bold}
#tbl tr.off td{color:#94a3b8}
canvas{display:block}
</style>
</head><body>
<div id="hud">
  <h2>Probe coverage — fixture <span id="fxname"></span></h2>
  <div class="row"><span>fixture pos</span><span id="fxpos"></span></div>
  <div class="row"><span>home anchor</span><span id="homepos"></span></div>
  <div class="row"><span>probes</span><span id="np"></span></div>
  <div class="row"><span>candidates (Beam found)</span><span id="ncand"></span></div>
  <div class="row"><span>on-stage hits</span><span id="nstage"></span></div>
  <div style="margin-top:6px;color:#94a3b8;font-size:11px">drag to rotate · scroll to zoom · right-click to pan</div>
</div>
<div id="legend">
  <div><span class="sw" style="background:#fde047"></span>home aim</div>
  <div><span class="sw" style="background:#fbbf24"></span>candidate (beam found)</div>
  <div><span class="sw" style="background:#22c55e"></span>last probe</div>
  <div><span class="sw" style="background:#94a3b8"></span>probe (no detection)</div>
  <div><span class="sw" style="background:#0ea5e9"></span>camera</div>
  <div><span class="sw" style="background:#fbbf24;border:1px solid #854d0e"></span>floor marker</div>
</div>
<div id="tbl"><table id="probetbl"><thead><tr>
  <th>#</th><th>pan_n</th><th>off_home°</th><th>tilt_n</th><th>tilt°</th>
  <th>X (mm)</th><th>Y (mm)</th><th>status</th>
</tr></thead><tbody id="probetbody"></tbody></table></div>

<script type="importmap">
{ "imports": {
    "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
    "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
} }
</script>
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA = %(data_json)s;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f172a);
const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 10, 50000);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

// World-coords convention here: X right, Y forward (depth into stage), Z up.
// Three.js default has Y up; remap so our Y-forward becomes screen Z, Z-up becomes screen Y.
function v(x, y, z) { return new THREE.Vector3(x, z, -y); }  // (X, Y_world, Z_world) -> (X, Z_three, -Y_three)

const stageW = DATA.stage.w, stageD = DATA.stage.d, stageH = DATA.stage.h;

// Floor grid
const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(stageW, stageD),
    new THREE.MeshBasicMaterial({ color: 0x1e293b, side: THREE.DoubleSide })
);
floor.rotation.x = -Math.PI / 2;
floor.position.set(stageW / 2, 0, -stageD / 2);
scene.add(floor);

const grid = new THREE.GridHelper(Math.max(stageW, stageD) * 1.2, 30, 0x334155, 0x1e293b);
grid.position.set(stageW / 2, 0, -stageD / 2);
scene.add(grid);

// Stage outline (rectangle on floor)
{
    const pts = [v(0, 0, 0), v(stageW, 0, 0), v(stageW, stageD, 0), v(0, stageD, 0), v(0, 0, 0)];
    const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: 0x60a5fa, linewidth: 2 })
    );
    scene.add(line);
}

// Fixture
const fx = DATA.fixture.pos;
{
    const geo = new THREE.ConeGeometry(80, 160, 6);
    const mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color: 0x7c3aed }));
    mesh.position.copy(v(fx[0], fx[1], fx[2]));
    mesh.rotation.x = Math.PI;
    scene.add(mesh);
    // Vertical drop line from ceiling to floor below the fixture
    const drop = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([v(fx[0], fx[1], fx[2]), v(fx[0], fx[1], 0)]),
        new THREE.LineBasicMaterial({ color: 0x7c3aed, opacity: 0.4, transparent: true })
    );
    scene.add(drop);
}

// Home aim — always render the direction vector. If it intersects the
// floor, mark the hit; otherwise extend to a fixed length so the operator
// can see which way the head is aimed.
if (DATA.fixture.homeDir) {
    const d = DATA.fixture.homeDir;
    const start = v(fx[0], fx[1], fx[2]);
    let endX, endY, endZ;
    if (DATA.fixture.homeHit) {
        endX = DATA.fixture.homeHit[0];
        endY = DATA.fixture.homeHit[1];
        endZ = 0;
    } else {
        // No floor hit (horizontal aim) — extend a fixed 4 m ray so the
        // direction is visible.
        const L = 4000;
        endX = fx[0] + d[0] * L;
        endY = fx[1] + d[1] * L;
        endZ = fx[2] + d[2] * L;
    }
    const end = v(endX, endY, endZ);
    const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([start, end]),
        new THREE.LineBasicMaterial({ color: 0xfde047, linewidth: 3 })
    );
    scene.add(line);
    const dot = new THREE.Mesh(
        new THREE.SphereGeometry(50, 14, 14),
        new THREE.MeshBasicMaterial({ color: 0xfde047 })
    );
    dot.position.copy(end);
    scene.add(dot);
    // Drop indicator from terminus to floor so the operator sees the
    // home aim's projected XY footprint even when it's "off in the air."
    const groundFoot = v(endX, endY, 0);
    const drop = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([end, groundFoot]),
        new THREE.LineDashedMaterial({ color: 0xfde047, opacity: 0.4, transparent: true, dashSize: 30, gapSize: 30 })
    );
    drop.computeLineDistances();
    scene.add(drop);
    const groundDot = new THREE.Mesh(
        new THREE.RingGeometry(40, 70, 16),
        new THREE.MeshBasicMaterial({ color: 0xfde047, side: THREE.DoubleSide, transparent: true, opacity: 0.7 })
    );
    groundDot.rotation.x = -Math.PI / 2;
    groundDot.position.copy(groundFoot);
    scene.add(groundDot);
}

// Cameras + FOV cones + floor polygons (translucent coloured quads
// per camera, clipped to the stage rectangle so the visible-floor
// region each camera covers is obvious at a glance).
const CAM_PALETTE = [0x0ea5e9, 0xa855f7, 0xf97316, 0x10b981, 0xef4444];
DATA.cameras.forEach((c, idx) => {
    const colour = CAM_PALETTE[idx %% CAM_PALETTE.length];
    const p = v(c.x, c.y, c.z);
    const dot = new THREE.Mesh(
        new THREE.SphereGeometry(50, 16, 16),
        new THREE.MeshBasicMaterial({ color: colour })
    );
    dot.position.copy(p);
    scene.add(dot);
    // Drop line
    const drop = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([p, v(c.x, c.y, 0)]),
        new THREE.LineBasicMaterial({ color: colour, opacity: 0.4, transparent: true })
    );
    scene.add(drop);
    // Floor polygon (clipped to stage rect by intersecting in 2D).
    const poly = c.floorPolygon || [];
    const clipped = clipPolyToStage(poly, stageW, stageD);
    if (clipped.length >= 3) {
        const shape = new THREE.Shape();
        shape.moveTo(clipped[0][0], clipped[0][1]);
        for (let i = 1; i < clipped.length; i++) shape.lineTo(clipped[i][0], clipped[i][1]);
        shape.lineTo(clipped[0][0], clipped[0][1]);
        const polyMesh = new THREE.Mesh(
            new THREE.ShapeGeometry(shape),
            new THREE.MeshBasicMaterial({
                color: colour, transparent: true, opacity: 0.18,
                side: THREE.DoubleSide, depthWrite: false,
            })
        );
        polyMesh.position.z = 2;  // sit just above the floor mesh
        scene.add(polyMesh);
        // Outline for readability
        const pts = clipped.map(pt => v(pt[0], pt[1], 3));
        pts.push(pts[0]);
        const outline = new THREE.Line(
            new THREE.BufferGeometry().setFromPoints(pts),
            new THREE.LineBasicMaterial({ color: colour, opacity: 0.7, transparent: true })
        );
        scene.add(outline);
    }
});

// Sutherland–Hodgman polygon clip against the stage rectangle.
function clipPolyToStage(poly, w, d) {
    const edges = [
        { axis: 'x', cmp: pt => pt[0] >= 0,        // left
          intersect: (a, b) => [0, a[1] + (b[1]-a[1]) * ((0-a[0])/(b[0]-a[0])) ] },
        { axis: 'x', cmp: pt => pt[0] <= w,        // right
          intersect: (a, b) => [w, a[1] + (b[1]-a[1]) * ((w-a[0])/(b[0]-a[0])) ] },
        { axis: 'y', cmp: pt => pt[1] >= 0,        // back
          intersect: (a, b) => [a[0] + (b[0]-a[0]) * ((0-a[1])/(b[1]-a[1])), 0] },
        { axis: 'y', cmp: pt => pt[1] <= d,        // front
          intersect: (a, b) => [a[0] + (b[0]-a[0]) * ((d-a[1])/(b[1]-a[1])), d] },
    ];
    let out = poly.slice();
    for (const e of edges) {
        if (!out.length) break;
        const next = [];
        for (let i = 0; i < out.length; i++) {
            const cur = out[i];
            const prev = out[(i - 1 + out.length) %% out.length];
            const cIn = e.cmp(cur), pIn = e.cmp(prev);
            if (cIn) {
                if (!pIn) next.push(e.intersect(prev, cur));
                next.push(cur);
            } else if (pIn) {
                next.push(e.intersect(prev, cur));
            }
        }
        out = next;
    }
    return out;
}

// Point cloud (translucent dots, coloured by their RGB).
if (DATA.pointCloud && DATA.pointCloud.length) {
    const positions = new Float32Array(DATA.pointCloud.length * 3);
    const colors = new Float32Array(DATA.pointCloud.length * 3);
    DATA.pointCloud.forEach((p, i) => {
        positions[i*3]   = p[0];
        positions[i*3+1] = p[1];
        positions[i*3+2] = p[2];
        colors[i*3]   = (p[3] || 128) / 255;
        colors[i*3+1] = (p[4] || 128) / 255;
        colors[i*3+2] = (p[5] || 128) / 255;
    });
    const geom = new THREE.BufferGeometry();
    geom.setAttribute('position', new THREE.BufferAttribute(positions, 3));
    geom.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    const mat = new THREE.PointsMaterial({
        size: 18, vertexColors: true, transparent: true, opacity: 0.65,
        sizeAttenuation: true,
    });
    scene.add(new THREE.Points(geom, mat));
}

// Markers
DATA.markers.forEach(m => {
    const isFloor = Math.abs(m.z) < 50;
    const dot = new THREE.Mesh(
        new THREE.SphereGeometry(35, 12, 12),
        new THREE.MeshBasicMaterial({ color: isFloor ? 0xfbbf24 : 0x64748b })
    );
    dot.position.copy(v(m.x, m.y, m.z));
    scene.add(dot);
});

// Probes
let onStageCount = 0, candCount = 0;
DATA.probes.forEach(p => {
    if (!p.hit) return;
    if (p.onStage) onStageCount++;
    if (p.candidate) candCount++;
    let color = 0x94a3b8;
    if (p.last) color = 0x22c55e;
    else if (p.candidate) color = 0xfbbf24;
    const start = v(fx[0], fx[1], fx[2]);
    const end = v(p.hit[0], p.hit[1], 0);
    const line = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([start, end]),
        new THREE.LineBasicMaterial({ color: color, opacity: 0.7, transparent: true })
    );
    scene.add(line);
    const dot = new THREE.Mesh(
        new THREE.SphereGeometry(30, 10, 10),
        new THREE.MeshBasicMaterial({ color: color })
    );
    dot.position.copy(end);
    scene.add(dot);
});

// Camera position
const cx = stageW / 2, cy = stageD * 1.4, cz = Math.max(stageH * 1.6, 3000);
camera.position.copy(v(cx, -cy + stageD / 2, cz));
camera.lookAt(v(stageW / 2, stageD / 2, 0));

const controls = new OrbitControls(camera, renderer.domElement);
controls.target.copy(v(stageW / 2, stageD / 2, 0));
controls.update();

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

function animate() {
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
}
animate();

// HUD
document.getElementById('fxname').textContent = '#' + DATA.fixture.id + ' ' + DATA.fixture.name;
document.getElementById('fxpos').textContent = '(' + fx.map(n => Math.round(n)).join(', ') + ')';
document.getElementById('homepos').textContent = DATA.fixture.homeHit
    ? '(' + DATA.fixture.homeHit.map(n => Math.round(n)).join(', ') + ')'
    : 'no floor hit (tilt=horizon)';
document.getElementById('np').textContent = DATA.probes.length;
document.getElementById('ncand').textContent = candCount;
document.getElementById('nstage').textContent = onStageCount + ' / ' + DATA.probes.length;

// Probe table
const tbody = document.getElementById('probetbody');
DATA.probes.forEach(p => {
    const tr = document.createElement('tr');
    let cls = '';
    if (p.last) cls = 'last';
    else if (p.candidate) cls = 'cand';
    if (p.hit && !p.onStage) cls = (cls ? cls + ' ' : '') + 'off';
    tr.className = cls;
    const fmt = (n) => n === undefined || n === null ? '—' : Math.round(n);
    let status = '';
    if (p.last) status = 'LAST';
    else if (p.candidate) status = 'candidate';
    else status = '·';
    if (p.hit && !p.onStage) status += ' off-stage';
    tr.innerHTML = '<td>' + p.n + '</td>' +
        '<td>' + p.pan.toFixed(4) + '</td>' +
        '<td>' + (p.panOffHomeDeg >= 0 ? '+' : '') + p.panOffHomeDeg.toFixed(0) + '</td>' +
        '<td>' + p.tilt.toFixed(4) + '</td>' +
        '<td>' + p.tiltDeg.toFixed(0) + '</td>' +
        '<td>' + (p.hit ? fmt(p.hit[0]) : '—') + '</td>' +
        '<td>' + (p.hit ? fmt(p.hit[1]) : '—') + '</td>' +
        '<td>' + status + '</td>';
    tbody.appendChild(tr);
});
</script>
</body></html>
""" % {"fid": payload["fixture"]["id"], "data_json": data_json}


if __name__ == "__main__":
    sys.exit(main())
