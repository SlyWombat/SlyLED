#!/usr/bin/env python3
"""DMX channel-change monitor — polls /api/dmx/monitor/<uni> at high rate and
emits one NDJSON line per detected change. Fixture-aware: decodes pan/tilt
DMX pairs into normalised + degree values for the configured mover so the
trace is readable at a glance without cross-referencing the profile.

Usage:
    python3 tools/dmx_monitor.py \
        --orch http://localhost:8080 \
        --universe 1 \
        --fixture 17 --fixture-base 1 --fixture-profile movinghead-150w-12ch \
        --rate-hz 50 \
        --out docs/live-test-sessions/2026-04-26/dmx-trace.ndjson

Stops on Ctrl+C; pipes stdout to a tee for live viewing.
"""

import argparse
import json
import signal
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


PROFILE_OFFSETS = {
    "movinghead-150w-12ch": {
        0: ("pan",        "coarse"),
        1: ("pan",        "fine"),
        2: ("tilt",       "coarse"),
        3: ("tilt",       "fine"),
        4: ("ptspeed",    None),
        5: ("dimmer",     None),
        6: ("strobe",     None),
        7: ("colour",     None),
        8: ("gobo",       None),
        9: ("prism",      None),
        10: ("focus",     None),
        11: ("function",  None),
    },
    "beamlight-350w-16ch": {
        0: ("pan",        "coarse"),
        1: ("pan",        "fine"),
        2: ("tilt",       "coarse"),
        3: ("tilt",       "fine"),
        4: ("ptspeed",    None),
        5: ("dimmer",     None),
        6: ("strobe",     None),
        7: ("colour",     None),
        8: ("gobo",       None),
        9: ("prism",      None),
    },
}

PAN_RANGE_DEG = {
    "movinghead-150w-12ch": 540.0,
    "beamlight-350w-16ch": 540.0,
}
TILT_RANGE_DEG = {
    "movinghead-150w-12ch": 180.0,
    "beamlight-350w-16ch": 270.0,
}

COLOUR_SLOTS = {
    "movinghead-150w-12ch": [
        (0, 15, "white"), (16, 31, "red"), (32, 47, "yellow"),
        (48, 63, "green"), (64, 79, "magenta"), (80, 95, "blue"),
        (96, 111, "amber"), (112, 127, "lightblue"),
    ],
}


def iso_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def fetch_buffer(orch, uni, timeout=2.0):
    url = f"{orch}/api/dmx/monitor/{uni}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8"))
    return d.get("channels") or [0] * 512


def decode(profile, base_addr, channels):
    """Return ({offset: (name, role, raw)}, derived). Derived has pan_norm/
    tilt_norm/pan_deg/tilt_deg/dimmer/colour_slot/colour_dmx if computable.
    base_addr is 1-indexed DMX address."""
    spec = PROFILE_OFFSETS.get(profile, {})
    pan_c = pan_f = tilt_c = tilt_f = 0
    dimmer = colour_dmx = None
    per_off = {}
    for off, (name, role) in spec.items():
        idx = (base_addr - 1) + off
        if 0 <= idx < len(channels):
            v = channels[idx]
            per_off[off] = (name, role, v)
            if name == "pan" and role == "coarse": pan_c = v
            elif name == "pan" and role == "fine": pan_f = v
            elif name == "tilt" and role == "coarse": tilt_c = v
            elif name == "tilt" and role == "fine": tilt_f = v
            elif name == "dimmer": dimmer = v
            elif name == "colour": colour_dmx = v
    pan_total = (pan_c << 8) | pan_f
    tilt_total = (tilt_c << 8) | tilt_f
    derived = {
        "pan_norm": round(pan_total / 65535.0, 5),
        "tilt_norm": round(tilt_total / 65535.0, 5),
        "pan_deg":  round((pan_total / 65535.0) * PAN_RANGE_DEG.get(profile, 540.0), 2),
        "tilt_deg": round((tilt_total / 65535.0) * TILT_RANGE_DEG.get(profile, 180.0), 2),
        "dimmer":   dimmer,
        "colour_dmx": colour_dmx,
        "colour_slot": colour_slot_name(profile, colour_dmx),
    }
    return per_off, derived


def colour_slot_name(profile, dmx):
    if dmx is None:
        return None
    slots = COLOUR_SLOTS.get(profile, [])
    for lo, hi, name in slots:
        if lo <= dmx <= hi:
            return name
    return f"raw:{dmx}"


def watched_offsets(profile, base_addr):
    spec = PROFILE_OFFSETS.get(profile, {})
    return sorted([(base_addr - 1) + o for o in spec.keys()])


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--orch", default="http://localhost:8080")
    ap.add_argument("--universe", type=int, default=1)
    ap.add_argument("--rate-hz", type=float, default=50.0)
    ap.add_argument("--fixture", type=int, default=17, help="fixture id (for label only)")
    ap.add_argument("--fixture-base", type=int, default=1, help="DMX base address (1-indexed)")
    ap.add_argument("--fixture-profile", default="movinghead-150w-12ch",
                    choices=list(PROFILE_OFFSETS.keys()))
    ap.add_argument("--watch-all", action="store_true",
                    help="emit changes on every channel in the universe (default: only the fixture's channels)")
    ap.add_argument("--out", help="NDJSON output path")
    ap.add_argument("--stdout-changes", action="store_true", default=True,
                    help="also print one-line summary per change to stdout")
    ap.add_argument("--quiet-stdout", action="store_true",
                    help="suppress stdout (file-only)")
    args = ap.parse_args()

    period = 1.0 / max(1.0, args.rate_hz)
    out_path = Path(args.out) if args.out else None
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)

    fh = out_path.open("a", encoding="utf-8") if out_path else None

    spec = PROFILE_OFFSETS[args.fixture_profile]
    fixture_offsets = {(args.fixture_base - 1) + o: (n, r) for o, (n, r) in spec.items()}
    interesting = set(fixture_offsets.keys())

    # Prime previous buffer.
    try:
        prev = fetch_buffer(args.orch, args.universe)
    except Exception as e:
        print(f"error: cannot reach {args.orch} ({e})", file=sys.stderr)
        return 2

    print(f"== monitoring universe {args.universe} @ {args.rate_hz:.0f} Hz  "
          f"fixture #{args.fixture} addr={args.fixture_base} profile={args.fixture_profile}  "
          f"watch={'all' if args.watch_all else 'fixture-only'}", flush=True)

    # Initial-state line so the trace has a baseline.
    per_off, derived = decode(args.fixture_profile, args.fixture_base, prev)
    init_rec = {
        "ts": iso_now(),
        "kind": "init",
        "universe": args.universe,
        "fixture": args.fixture,
        "derived": derived,
        "raw": {f"+{o}": v for o, (_, _, v) in per_off.items()},
    }
    if fh:
        fh.write(json.dumps(init_rec) + "\n"); fh.flush()
    if not args.quiet_stdout:
        d = derived
        print(f"[{init_rec['ts']}] INIT pan={d['pan_norm']:.4f} ({d['pan_deg']:.1f}°) "
              f"tilt={d['tilt_norm']:.4f} ({d['tilt_deg']:.1f}°) "
              f"dim={d['dimmer']} col={d['colour_slot']}", flush=True)

    stop = {"flag": False}
    def on_sig(sig, _frm):
        stop["flag"] = True
    signal.signal(signal.SIGINT, on_sig)
    signal.signal(signal.SIGTERM, on_sig)

    last_emit_ts = 0.0
    while not stop["flag"]:
        t0 = time.perf_counter()
        try:
            cur = fetch_buffer(args.orch, args.universe)
        except Exception as e:
            time.sleep(period)
            continue
        # Find changes.
        changes = []
        if args.watch_all:
            for i, (a, b) in enumerate(zip(prev, cur)):
                if a != b:
                    changes.append((i, a, b))
        else:
            for i in interesting:
                if 0 <= i < len(cur) and prev[i] != cur[i]:
                    changes.append((i, prev[i], cur[i]))
        if changes:
            per_off, derived = decode(args.fixture_profile, args.fixture_base, cur)
            ts = iso_now()
            rec = {
                "ts": ts,
                "kind": "change",
                "universe": args.universe,
                "fixture": args.fixture,
                "n": len(changes),
                "changes": [
                    {"addr": i + 1, "fix_off": i - (args.fixture_base - 1),
                     "name": fixture_offsets.get(i, (None, None))[0],
                     "from": a, "to": b}
                    for (i, a, b) in changes
                ],
                "derived": derived,
            }
            if fh:
                fh.write(json.dumps(rec) + "\n"); fh.flush()
            if not args.quiet_stdout:
                d = derived
                # Compact one-liner: changed-channel names + new derived state.
                names = ",".join(c["name"] or f"ch{c['addr']}" for c in rec["changes"][:6])
                print(f"[{ts}] {len(changes):2d}× {names:30s}  "
                      f"pan={d['pan_norm']:.4f}({d['pan_deg']:6.1f}°)  "
                      f"tilt={d['tilt_norm']:.4f}({d['tilt_deg']:6.1f}°)  "
                      f"dim={d['dimmer']!s:>3}  col={d['colour_slot']}", flush=True)
            prev = cur
        else:
            # Still update prev to drop stale references; cheap.
            prev = cur
        # Pace.
        elapsed = time.perf_counter() - t0
        sleep_for = period - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)
    if fh:
        fh.close()
    print("== monitor stopped", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
