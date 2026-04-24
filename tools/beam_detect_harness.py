#!/usr/bin/env python3
"""Beam-detect harness — standalone test of the detection pipeline.

For a list of (pan, tilt) pairs, drives fixture #17 (or any DMX mover) to
each, captures a dark-reference (beam off), turns on the beam, runs beam-
detect on each requested camera, then does a pan+δ / pan-δ / tilt+δ / tilt-δ
nudge-confirm sequence. Isolates the "does the camera + detector find the
beam at this known aim" question from the cal-pipeline orchestration bugs.

Camera V4L2 settings are expected to be locked beforehand (e.g. via the
`Sly` tune preset activation). The harness does NOT change camera settings
itself — call `/api/cameras/<fid>/settings/slots/<name>/activate` first if
you want a specific preset applied.

Usage:
    /usr/bin/python3 tools/beam_detect_harness.py \\
        --fid 17 \\
        --positions '0.5,0.5;0.3,0.4;0.7,0.25' \\
        --cameras 12,13 \\
        --colour green \\
        --out docs/live-test-sessions/2026-04-24/beam-harness-run-1.ndjson

    # Or from file (CSV `pan,tilt` per line):
    --positions-file scan.csv

Output: one NDJSON line per (position × camera) test with timing, detection
result, nudge pixel-shifts, and a "confirmed" verdict (both nudge axes
moved > threshold).
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ── colour-wheel slot midpoints (movinghead-150w-12ch profile) ────────
# Name → DMX midpoint (profile ranges at offset 7)
COLOUR_SLOTS = {
    "white": 7,         # Open / white: 0-15
    "red": 23,          # 16-31
    "yellow": 39,       # 32-47
    "green": 55,        # 48-63
    "magenta": 71,      # 64-79
    "blue": 87,         # 80-95
    "amber": 103,       # 96-111
    "lightblue": 119,   # 112-127
}

# ── camera-fid → (base_url, cam_idx) map for beam-detect routing ──────
# Basement rig layout: two Pi nodes, three camera fixtures.
# Detected at runtime from orchestrator / hardcoded fallbacks.
CAMERA_ROUTES = {
    12: ("http://192.168.10.235:5000", 0),   # Stage Right
    13: ("http://192.168.10.235:5000", 1),   # Stage Left
    16: ("http://192.168.10.109:5000", 0),   # Out Left
}


def http(method, url, body=None, timeout=8):
    req = urllib.request.Request(url, method=method,
                                  headers={"Content-Type": "application/json"})
    data = json.dumps(body).encode() if body is not None else None
    try:
        resp = urllib.request.urlopen(req, data=data, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}", "_body": e.read().decode("utf-8", errors="replace")[:400]}
    except Exception as e:
        return {"_error": str(e)}


def norm_to_dmx16(v):
    """Normalised [0, 1] → (coarse, fine) 16-bit DMX pair."""
    v = max(0.0, min(1.0, float(v)))
    total = int(round(v * 65535))
    return (total >> 8) & 0xFF, total & 0xFF


def write_fixture(orch, fid, *, pan_norm, tilt_norm, dimmer, colour_dmx,
                   strobe=0, ptspeed=0, gobo=0, prism=0):
    """Raw per-channel write to fixture (offsets for movinghead-150w-12ch)."""
    pan_c, pan_f = norm_to_dmx16(pan_norm)
    tilt_c, tilt_f = norm_to_dmx16(tilt_norm)
    channels = [
        {"offset": 0, "value": pan_c},
        {"offset": 1, "value": pan_f},
        {"offset": 2, "value": tilt_c},
        {"offset": 3, "value": tilt_f},
        {"offset": 4, "value": ptspeed},
        {"offset": 5, "value": dimmer},
        {"offset": 6, "value": strobe},
        {"offset": 7, "value": colour_dmx},
        {"offset": 8, "value": gobo},
        {"offset": 9, "value": prism},
    ]
    return http("POST", f"{orch}/api/dmx/fixture/{fid}/test",
                 {"channels": channels})


def set_dimmer(orch, fid, dimmer):
    """Touch only the dimmer channel."""
    return http("POST", f"{orch}/api/dmx/fixture/{fid}/test",
                 {"channels": [{"offset": 5, "value": int(dimmer)}]})


def capture_dark_reference(cam_fid):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    return http("POST", f"{base}/dark-reference", {"cam": cam_idx}, timeout=5)


def beam_detect(cam_fid, *, threshold=30, center=False, use_dark=True):
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    body = {"cam": cam_idx, "threshold": threshold,
            "center": bool(center), "useDarkReference": bool(use_dark)}
    return http("POST", f"{base}/beam-detect", body, timeout=6)


def save_snapshot(cam_fid, out_path):
    """Grab and write a JPEG from this camera's current live view."""
    base, cam_idx = CAMERA_ROUTES[cam_fid]
    try:
        data = urllib.request.urlopen(f"{base}/snapshot?cam={cam_idx}", timeout=8).read()
        Path(out_path).write_bytes(data)
        return {"ok": True, "path": str(out_path), "bytes": len(data)}
    except Exception as e:
        return {"_error": str(e)}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_positions(pos_arg, pos_file):
    pts = []
    if pos_file:
        pts.extend(load_positions(pos_file))
    if pos_arg:
        for chunk in pos_arg.split(";"):
            chunk = chunk.strip()
            if not chunk:
                continue
            parts = [p.strip() for p in chunk.split(",")]
            p, t = float(parts[0]), float(parts[1])
            label = parts[2] if len(parts) > 2 else None
            pts.append((p, t, label))
    return pts


def load_positions(path):
    """#682-EE — importable CSV parser for the harness-positions.csv
    ground-truth file. Returns ``[(pan_norm, tilt_norm, label), ...]``.
    Used by the canary test to feed positions + expected verdicts into
    ``run_one`` without shelling out.

    Lines starting with ``#`` are treated as comments. Inline trailing
    comments after the ``label`` column (``0.5,0.5,foo # notes``) are
    stripped from the label.
    """
    pts = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                p = float(parts[0])
                t = float(parts[1])
            except ValueError:
                continue
            label = None
            if len(parts) > 2:
                raw = parts[2]
                if "#" in raw:
                    raw = raw.split("#", 1)[0]
                label = raw.strip() or None
            pts.append((p, t, label))
    return pts


def probe_at_aim(orch, fid, pan, tilt, *, dimmer, colour_dmx,
                 settle_s, cameras, threshold, center, use_dark):
    """Drive fixture, wait, beam-detect on each camera, return per-cam results."""
    write_fixture(orch, fid, pan_norm=pan, tilt_norm=tilt,
                   dimmer=dimmer, colour_dmx=colour_dmx)
    time.sleep(settle_s)
    out = {}
    for cam_fid in cameras:
        r = beam_detect(cam_fid, threshold=threshold, center=center,
                         use_dark=use_dark)
        out[cam_fid] = r
    return out


def run_one(orch, fid, pan, tilt, label, *,
            colour_dmx, cameras, nudge, settle_s,
            dark_wait_s, threshold, dark_ref, nudge_confirm,
            snapshot_dir=None, run_tag="run"):
    """Full test sequence at a single (pan, tilt): dark-ref → detect → nudge."""
    rec = {
        "ts": iso_now(),
        "pan": pan, "tilt": tilt,
        "label": label,
        "fid": fid,
        "colour_dmx": colour_dmx,
        "cameras": list(cameras),
        "threshold": threshold,
    }

    if dark_ref:
        # Dark-reference capture: move to aim with dimmer=0 so no beam,
        # wait for head to settle to the final position, then capture.
        write_fixture(orch, fid, pan_norm=pan, tilt_norm=tilt,
                       dimmer=0, colour_dmx=colour_dmx)
        time.sleep(max(dark_wait_s, settle_s))
        rec["darkRef"] = {c: capture_dark_reference(c) for c in cameras}

    # Beam-on primary probe.
    primary = probe_at_aim(orch, fid, pan, tilt, dimmer=255,
                            colour_dmx=colour_dmx, settle_s=settle_s,
                            cameras=cameras, threshold=threshold, center=False,
                            use_dark=dark_ref)
    rec["primary"] = primary

    # Snapshot capture — beam-on frame per camera, for visual verification of
    # what the detector is seeing. Filenames carry position + camera so they
    # can be matched to the NDJSON record later.
    if snapshot_dir:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        snaps = {}
        pos_tag = (label or f"p{pan:.3f}_t{tilt:.3f}").replace(" ", "_").replace("/", "-")
        for cam_fid in cameras:
            fp = snapshot_dir / f"{run_tag}-{pos_tag}-cam{cam_fid}.jpg"
            snaps[cam_fid] = save_snapshot(cam_fid, fp)
        rec["snapshots"] = snaps

    if nudge_confirm and nudge > 0.0:
        # Four nudges; each tests pixel-shift vs the primary detection.
        # After each nudge we return to the primary position.
        # pan+
        pan_plus = probe_at_aim(orch, fid, min(1.0, pan + nudge), tilt,
                                 dimmer=255, colour_dmx=colour_dmx,
                                 settle_s=settle_s, cameras=cameras,
                                 threshold=threshold, center=False,
                                 use_dark=dark_ref)
        # pan-
        pan_minus = probe_at_aim(orch, fid, max(0.0, pan - nudge), tilt,
                                  dimmer=255, colour_dmx=colour_dmx,
                                  settle_s=settle_s, cameras=cameras,
                                  threshold=threshold, center=False,
                                  use_dark=dark_ref)
        # tilt+
        tilt_plus = probe_at_aim(orch, fid, pan, min(1.0, tilt + nudge),
                                  dimmer=255, colour_dmx=colour_dmx,
                                  settle_s=settle_s, cameras=cameras,
                                  threshold=threshold, center=False,
                                  use_dark=dark_ref)
        # tilt-
        tilt_minus = probe_at_aim(orch, fid, pan, max(0.0, tilt - nudge),
                                   dimmer=255, colour_dmx=colour_dmx,
                                   settle_s=settle_s, cameras=cameras,
                                   threshold=threshold, center=False,
                                   use_dark=dark_ref)

        rec["nudge"] = {
            "delta": nudge,
            "panPlus": pan_plus, "panMinus": pan_minus,
            "tiltPlus": tilt_plus, "tiltMinus": tilt_minus,
        }

        # Compute per-camera confirmation verdict.
        verdict = {}
        for c in cameras:
            pri = primary.get(c) or {}
            px0 = pri.get("pixelX")
            py0 = pri.get("pixelY")
            if not pri.get("found") or px0 is None or py0 is None:
                verdict[c] = {"confirmed": False,
                               "reason": "primary-not-found"}
                continue
            def shift(resp):
                if not resp or not resp.get("found"): return None
                return (resp.get("pixelX") - px0, resp.get("pixelY") - py0)
            s_pp = shift(pan_plus.get(c))
            s_pm = shift(pan_minus.get(c))
            s_tp = shift(tilt_plus.get(c))
            s_tm = shift(tilt_minus.get(c))

            # Nudge-confirm: require that pan± causes pixel shift on at least
            # one X axis AND tilt± causes pixel shift on at least one Y axis,
            # both magnitudes >8 px (battleship_discover rule).
            def good_pan(s): return s is not None and abs(s[0]) > 8
            def good_tilt(s): return s is not None and abs(s[1]) > 8
            pan_ok = good_pan(s_pp) or good_pan(s_pm)
            tilt_ok = good_tilt(s_tp) or good_tilt(s_tm)

            verdict[c] = {
                "confirmed": bool(pan_ok and tilt_ok),
                "panShift": {"plus": s_pp, "minus": s_pm, "anyOk": pan_ok},
                "tiltShift": {"plus": s_tp, "minus": s_tm, "anyOk": tilt_ok},
                "notes": (
                    "passed" if pan_ok and tilt_ok else
                    "pan-shift-missing" if not pan_ok else
                    "tilt-shift-missing"
                ),
            }
        rec["confirmVerdict"] = verdict

    return rec


def format_summary(rec):
    """Compact one-line-per-camera readout for the console."""
    out = []
    label = f"{rec['label'] or ''}".strip()
    head = (f"[{rec['pan']:.2f}, {rec['tilt']:.2f}]"
            + (f" {label}" if label else ""))
    for c in rec['cameras']:
        pri = rec.get('primary', {}).get(c) or {}
        conf = rec.get('confirmVerdict', {}).get(c, {})
        if pri.get('_error'):
            out.append(f"  cam #{c}: ERR {pri['_error']}")
            continue
        found = pri.get('found')
        if not found:
            out.append(f"  cam #{c}: no-beam")
            continue
        loc = f"({pri.get('pixelX')}, {pri.get('pixelY')})"
        area = pri.get('area')
        bright = pri.get('brightness')
        if 'confirmVerdict' in rec:
            conf_tag = "CONFIRMED" if conf.get('confirmed') else f"no-confirm:{conf.get('notes','?')}"
        else:
            conf_tag = "(no-nudge)"
        out.append(f"  cam #{c}: found px={loc} bright={bright} area={area}  {conf_tag}")
    return head + "\n" + "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--fid", type=int, default=17, help="DMX mover fixture id")
    ap.add_argument("--positions", help="semicolon-separated 'pan,tilt[,label]' entries, normalised 0..1")
    ap.add_argument("--positions-file", help="CSV file with pan,tilt[,label] per line")
    ap.add_argument("--cameras", default="12,13",
                    help="comma-separated camera fixture ids to detect from")
    ap.add_argument("--colour", default="green",
                    choices=list(COLOUR_SLOTS.keys()),
                    help="beam colour (colour-wheel slot)")
    ap.add_argument("--threshold", type=int, default=30,
                    help="beam-detect pixel threshold (same semantic as cam node)")
    ap.add_argument("--nudge", type=float, default=0.02,
                    help="pan/tilt nudge delta (normalised); 0 to skip confirm")
    ap.add_argument("--nudge-coarse-steps", type=int, default=0,
                    help="if >0, override --nudge with N coarse DMX steps "
                         "(1 step = 1/256 = 0.0039 norm = 2.1° pan on 540° fixture)")
    ap.add_argument("--snapshot-dir",
                    help="save beam-on snapshot from each camera per position")
    ap.add_argument("--run-tag", default="run",
                    help="prefix for snapshot filenames")
    ap.add_argument("--settle", type=float, default=0.8,
                    help="seconds to wait after DMX write before detect")
    ap.add_argument("--dark-wait", type=float, default=1.0,
                    help="seconds to wait before capturing dark-reference")
    ap.add_argument("--no-dark-ref", action="store_true",
                    help="skip per-position dark-reference capture")
    ap.add_argument("--no-confirm", action="store_true",
                    help="skip nudge-confirm (beam-detect only)")
    ap.add_argument("--out", help="NDJSON output path (appends)")
    ap.add_argument("--blackout-after", action="store_true", default=True,
                    help="set dimmer=0 after the full run")
    args = ap.parse_args()

    # Resolve final nudge delta: coarse-steps override wins if > 0.
    nudge_final = args.nudge
    if args.nudge_coarse_steps > 0:
        nudge_final = args.nudge_coarse_steps / 256.0

    positions = parse_positions(args.positions, args.positions_file)
    if not positions:
        print("error: no positions given", file=sys.stderr)
        return 2

    cameras = [int(c.strip()) for c in args.cameras.split(",") if c.strip()]
    for c in cameras:
        if c not in CAMERA_ROUTES:
            print(f"error: camera fid {c} unknown — add to CAMERA_ROUTES", file=sys.stderr)
            return 2

    colour_dmx = COLOUR_SLOTS[args.colour]
    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    # Pre-run sanity: orchestrator reachable + engine running.
    dmx = http("GET", f"{args.orch}/api/dmx/status")
    if isinstance(dmx, dict) and dmx.get("artnet", {}).get("running") is not True:
        print(f"warning: Art-Net engine is not running — writes will be queued but not emitted",
              file=sys.stderr)

    print(f"== harness run: {len(positions)} position(s) × {len(cameras)} camera(s)  "
          f"colour={args.colour} dmx={colour_dmx}  nudge={nudge_final:.5f}"
          f"{' (' + str(args.nudge_coarse_steps) + ' coarse step(s))' if args.nudge_coarse_steps > 0 else ''}  "
          f"dark-ref={'off' if args.no_dark_ref else 'on'}  "
          f"confirm={'off' if args.no_confirm else 'on'}  "
          f"snapshots={'on' if args.snapshot_dir else 'off'}")

    for pan, tilt, label in positions:
        rec = run_one(args.orch, args.fid, pan, tilt, label,
                       colour_dmx=colour_dmx,
                       cameras=cameras,
                       nudge=0.0 if args.no_confirm else nudge_final,
                       settle_s=args.settle,
                       dark_wait_s=args.dark_wait,
                       threshold=args.threshold,
                       dark_ref=not args.no_dark_ref,
                       nudge_confirm=not args.no_confirm,
                       snapshot_dir=args.snapshot_dir,
                       run_tag=args.run_tag)
        print(format_summary(rec))
        if out_path:
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec) + "\n")

    if args.blackout_after:
        set_dimmer(args.orch, args.fid, 0)
        print("== dimmer zeroed on fixture", args.fid)

    if out_path:
        print(f"== wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
