#!/usr/bin/env python3
"""#686 — top-down replay of a mover-cal trace.

Reads an NDJSON trace produced by ``CalTraceRecorder`` (in
``desktop/shared/parent_server.py``) plus an optional project context
(``.slyshow`` export or live ``--orch`` API snapshot) and renders a PNG
that names a cal failure mode at a glance:

  - stage box (auto-bounds from the project / trace header)
  - camera floor-view polygons (one per camera, colour-coded by id)
  - surveyed ArUco markers as labelled dots
  - stage objects (pillar, walls per surface_analyzer)
  - probe trail — one dot per probe, coloured by ``decision``:
      grey   skip-by-filter
      blue   probed (no detection)
      amber  detected, mid-confirm
      green  CONFIRMED
      red    nudge-rejected (frame-edge / depth-discontinuity / disproportionate)
      purple refined-from
  - probe-trail edges connecting successive probes
  - seed star annotation
  - legend + a per-decision summary table printed to stdout

Usage:
    python tools/cal_trace_replay.py \\
        --trace /path/to/cal_traces/fid17-...ndjson \\
        --project tests/user/basement/basement.slyshow \\
        --out /tmp/cal-trace-fid17.png

    # Or pull stage/cameras/markers from a running orchestrator:
    python tools/cal_trace_replay.py \\
        --trace /path/to/cal_traces/fid17-...ndjson \\
        --orch http://localhost:8080 \\
        --out /tmp/cal-trace-fid17.png

Acceptance — produces a single PNG that visualises the geometric walk,
prints a summary table the operator can paste into a bug report.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from urllib.request import urlopen


# ── Decision colour map ────────────────────────────────────────────────

DECISION_COLOR = {
    "skip-by-filter":     "#94a3b8",  # grey
    "probed":             "#3b82f6",  # blue
    "detected":           "#f59e0b",  # amber
    "confirmed":          "#22c55e",  # green
    "nudge-rejected":     "#ef4444",  # red
    "refined-from":       "#a855f7",  # purple
    "marker-converged":   "#22c55e",  # green (counts as confirmed)
}

DECISION_LABEL = {
    "skip-by-filter":   "Skipped (FOV filter)",
    "probed":           "Probed (no detection)",
    "detected":         "Detected, in-confirm",
    "confirmed":        "Confirmed",
    "nudge-rejected":   "Nudge rejected",
    "refined-from":     "Refined from",
    "marker-converged": "Marker converged",
}


# ── Parsing ───────────────────────────────────────────────────────────

def load_trace(path: Path):
    header = None
    footer = None
    seed = None
    probes = []
    with path.open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            kind = rec.get("kind")
            if kind == "header":
                header = rec
            elif kind == "footer":
                footer = rec
            elif kind == "seed":
                seed = rec
            elif kind == "probe":
                probes.append(rec)
    return header or {}, seed, probes, footer or {}


def load_project(path: Path):
    """Read a .slyshow / project-export JSON for stage + cameras +
    markers + objects context."""
    blob = json.loads(path.read_text(encoding="utf-8"))
    stage = blob.get("stage") or {}
    fixtures = blob.get("fixtures") or []
    layout_children = (blob.get("layout") or {}).get("children") or []
    pos_map = {p.get("id"): p for p in layout_children}
    markers = (blob.get("aruco") or {}).get("markers") or []
    objects = blob.get("objects") or []
    return _shape_context(stage, fixtures, pos_map, markers, objects)


def load_live(orch: str):
    """Pull the same context from a running orchestrator."""
    def _get(path):
        with urlopen(f"{orch.rstrip('/')}{path}", timeout=5) as r:
            return json.loads(r.read().decode())
    fixtures = _get("/api/fixtures")
    layout = _get("/api/layout") or {}
    pos_map = {p.get("id"): p for p in (layout.get("children") or [])}
    stage = _get("/api/settings").get("stage", {}) if False else {}
    # Some orchestrator versions ship stage on /api/settings; if missing,
    # fall back to layout dimensions.
    try:
        st = _get("/api/settings")
        stage = st.get("stage") or stage
    except Exception:
        pass
    markers = (_get("/api/aruco/markers") or {}).get("markers", [])
    objects = _get("/api/objects") or []
    return _shape_context(stage, fixtures, pos_map, markers, objects)


def _shape_context(stage, fixtures, pos_map, markers, objects):
    sw = float((stage.get("w") or 6.0)) * 1000
    sd = float((stage.get("d") or 4.0)) * 1000
    sh = float((stage.get("h") or 3.0)) * 1000
    cams = []
    for f in fixtures:
        if f.get("fixtureType") != "camera":
            continue
        p = pos_map.get(f.get("id"))
        if not p:
            continue
        cams.append({
            "id": f.get("id"),
            "name": f.get("name") or f.get("altName") or f.get("id"),
            "pos": [float(p.get("x", 0)), float(p.get("y", 0)),
                    float(p.get("z", 0))],
            "rotation": f.get("rotation") or [0, 0, 0],
            "fov": float(f.get("fovDeg") or 90),
        })
    return {
        "stage": {"w": sw, "d": sd, "h": sh},
        "cameras": cams,
        "markers": [{"id": m.get("id"),
                       "x": float(m.get("x", 0)),
                       "y": float(m.get("y", 0)),
                       "z": float(m.get("z", 0))}
                      for m in markers if m.get("id") is not None],
        "objects": objects,
    }


# ── Rendering ─────────────────────────────────────────────────────────

def render(header, seed, probes, footer, ctx, out_path: Path,
            title: str = "Mover-cal trace replay"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle, Polygon
    except ImportError:
        print("ERROR: matplotlib not installed. Install with `pip install "
               "matplotlib` to use the renderer.", file=sys.stderr)
        sys.exit(2)

    stage = (ctx or {}).get("stage") or {}
    sw = float(stage.get("w") or 6000)
    sd = float(stage.get("d") or 4000)

    fig, ax = plt.subplots(figsize=(11, 8))
    ax.set_aspect("equal")

    # Stage box.
    ax.add_patch(Rectangle((0, 0), sw, sd, fill=False,
                             edgecolor="#475569", linewidth=2))
    ax.set_xlim(-200, sw + 200)
    ax.set_ylim(-200, sd + 200)

    # Camera FOV polygons (from header — the cal-time view).
    cam_id_color = {}
    palette = ["#0ea5e9", "#22d3ee", "#84cc16", "#f97316", "#ec4899"]
    for i, cam in enumerate(header.get("cameras") or []):
        col = palette[i % len(palette)]
        cam_id_color[cam.get("id")] = col
        poly = cam.get("polygon") or []
        if len(poly) >= 3:
            ax.add_patch(Polygon(poly, closed=True,
                                   facecolor=col, edgecolor=col,
                                   alpha=0.12, linewidth=1.2,
                                   label=f"cam{cam.get('id')} FOV"))
    # Camera positions (from project / live context).
    for cam in (ctx or {}).get("cameras") or []:
        col = cam_id_color.get(cam.get("id"), "#1e40af")
        ax.plot(cam["pos"][0], cam["pos"][1], "s", color=col,
                markersize=10, markeredgecolor="white", markeredgewidth=1.5)
        ax.annotate(f"  cam{cam.get('id')}",
                     (cam["pos"][0], cam["pos"][1]),
                     fontsize=8, color=col)

    # Surveyed markers.
    for m in (ctx or {}).get("markers") or []:
        ax.plot(m["x"], m["y"], "o", color="#ca8a04",
                markersize=6, markeredgecolor="black", markeredgewidth=0.5)
        ax.annotate(f" #{m['id']}", (m["x"], m["y"]),
                     fontsize=7, color="#854d0e")

    # Probe trail. Edges first so dots render on top.
    pts = []
    for p in probes:
        fp = p.get("predictedFloorPoint")
        if fp and fp[0] is not None and fp[1] is not None:
            pts.append((float(fp[0]), float(fp[1])))
        else:
            pts.append(None)
    drawn_pts = [p for p in pts if p is not None]
    for a, b in zip(drawn_pts, drawn_pts[1:]):
        ax.plot([a[0], b[0]], [a[1], b[1]],
                color="#1e293b", linewidth=0.4, alpha=0.5, zorder=1)

    # Coloured dots per decision.
    drawn_decisions = {}
    for p, pt in zip(probes, pts):
        if pt is None:
            continue
        decision = p.get("decision") or "probed"
        col = DECISION_COLOR.get(decision, "#475569")
        ax.plot(pt[0], pt[1], "o", color=col, markersize=4,
                markeredgecolor="black", markeredgewidth=0.3, zorder=2)
        drawn_decisions[decision] = drawn_decisions.get(decision, 0) + 1

    # Seed star.
    if seed and seed.get("predictedFloorPoint"):
        sp = seed["predictedFloorPoint"]
        ax.plot(sp[0], sp[1], "*", color="#fde047", markersize=18,
                markeredgecolor="black", markeredgewidth=0.8,
                label="Seed", zorder=3)

    # Mover position from header.
    fx_pos = header.get("fxPos") or []
    if len(fx_pos) >= 2:
        ax.plot(fx_pos[0], fx_pos[1], "^", color="#7c3aed",
                markersize=12, markeredgecolor="white", markeredgewidth=1.5,
                label=f"Mover #{header.get('fid')}", zorder=3)

    # Legend (decision colours + above markers).
    legend_handles = []
    for k, v in DECISION_LABEL.items():
        if k in drawn_decisions:
            legend_handles.append(
                plt.Line2D([], [], marker="o", color="white",
                            markerfacecolor=DECISION_COLOR[k],
                            markeredgecolor="black", markersize=8,
                            label=f"{v} ({drawn_decisions[k]})"))
    if seed and seed.get("predictedFloorPoint"):
        legend_handles.append(plt.Line2D([], [], marker="*",
                                            color="white",
                                            markerfacecolor="#fde047",
                                            markeredgecolor="black",
                                            markersize=12, label="Seed"))
    if len(fx_pos) >= 2:
        legend_handles.append(plt.Line2D([], [], marker="^",
                                            color="white",
                                            markerfacecolor="#7c3aed",
                                            markeredgecolor="white",
                                            markersize=10,
                                            label=f"Mover #{header.get('fid')}"))
    ax.legend(handles=legend_handles, loc="upper right", fontsize=8,
              frameon=True, framealpha=0.85)

    counts = footer.get("counts") or {}
    sub = []
    for k in ("probed", "skipped", "confirmed", "rejected", "refined"):
        sub.append(f"{k}={counts.get(k, 0)}")
    if footer.get("status"):
        sub.append(f"status={footer['status']}")
    ax.set_title(f"{title} — fid {header.get('fid')} mode={header.get('mode')}\n"
                  f"{' · '.join(sub)}", fontsize=11)
    ax.set_xlabel("Stage X (mm)")
    ax.set_ylabel("Stage Y (mm)")
    ax.grid(True, alpha=0.2)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)


# ── Summary table ─────────────────────────────────────────────────────

def print_summary(header, seed, probes, footer):
    print(f"\n=== Cal-trace summary — fid {header.get('fid')} "
          f"mode={header.get('mode')} ===")
    print(f"Mover at {header.get('fxPos')}, "
          f"pan_range={header.get('panRangeDeg')}°, "
          f"tilt_range={header.get('tiltRangeDeg')}°, "
          f"mountedInverted={header.get('mountedInverted')}")
    cams = header.get("cameras") or []
    print(f"Cameras: {[c.get('id') for c in cams]}")
    if seed:
        print(f"Seed: pan={seed.get('panNorm'):.3f} tilt={seed.get('tiltNorm'):.3f} "
              f"target={seed.get('targetXY')} "
              f"predictedSurface={seed.get('predictedSurface')} "
              f"inFovOf={seed.get('predictedInFovOf')}")
    decision_counts = {}
    in_fov_count = 0
    for p in probes:
        d = p.get("decision") or "?"
        decision_counts[d] = decision_counts.get(d, 0) + 1
        if (p.get("predictedInFovOf") or []):
            in_fov_count += 1
    print(f"\nProbe count: {len(probes)}")
    if probes:
        print(f"  in any camera FOV: {in_fov_count} "
              f"({100.0 * in_fov_count / max(1, len(probes)):.0f}%)")
    print(f"\nBy decision:")
    for d, c in sorted(decision_counts.items(), key=lambda x: -x[1]):
        print(f"  {d:30s} {c:4d}")
    if footer:
        print(f"\nFinal status: {footer.get('status')} "
              f"error={footer.get('error')}")
        print(f"Counters: {footer.get('counts')}")


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--trace", required=True,
                   help="cal-trace NDJSON path")
    p.add_argument("--project",
                   help=".slyshow project export for stage / camera / "
                        "marker context")
    p.add_argument("--orch",
                   help="orchestrator base URL (e.g. http://localhost:8080) "
                        "for live context — alternative to --project")
    p.add_argument("--out", default="cal_trace.png",
                   help="output PNG path (default: cal_trace.png)")
    p.add_argument("--title", default="Mover-cal trace replay")
    args = p.parse_args()

    trace_path = Path(args.trace)
    if not trace_path.is_file():
        print(f"ERROR: trace not found: {trace_path}", file=sys.stderr)
        return 2
    header, seed, probes, footer = load_trace(trace_path)

    ctx = None
    if args.project:
        ctx = load_project(Path(args.project))
    elif args.orch:
        try:
            ctx = load_live(args.orch)
        except Exception as e:
            print(f"WARN: live context fetch failed ({e}) — rendering "
                  f"from header only", file=sys.stderr)
    if ctx is None:
        # Fall back to a minimal context built from the trace header.
        ctx = {"stage": {"w": 6000, "d": 4000, "h": 3000},
               "cameras": [], "markers": [], "objects": []}

    out_path = Path(args.out)
    render(header, seed, probes, footer, ctx, out_path, title=args.title)
    print_summary(header, seed, probes, footer)
    print(f"\nWrote {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
