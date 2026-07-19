#!/usr/bin/env python3
"""
mmwave_bench.py — capture + characterize 0x70 MMW_TARGETS traffic on the bench.

Covers the remaining #908 checklist items (design doc §10):
  item 3 — jitter vs range: run one session per distance with --label 2m/5m/8m;
           the summary prints X/Y/range/angle mean±std for Kalman tuning.
  item 4 — static-person fade: stand still in view; the summary's presence
           episodes report time-from-last-motion-to-fade (feeds COAST_MS).
  item 5 — two-node ghosting: stats are keyed per source IP; the CSV holds
           both nodes' raw slots for offline ghost analysis.
  item 6 — sustained UDP soak: run with no --duration for hours; seq-gap
           loss %, longest silence, and parse-health % are in the summary.

Usage:
    python tools/mmwave_bench.py [--label 2m] [--duration 300]
                                 [--csv PATH | --no-csv] [--port 4210]
                                 [--motion-thresh 10]

Stop with Ctrl+C (or --duration seconds); the summary prints on exit.

NOTE: binds UDP 4210 exclusively — stop the installed SlyLED.exe tray app
AND any dev orchestrator first, or this tool sees nothing / fails to bind
(the tray app holds 4210; see the #908 field gotcha). Raw capture does not
need the orchestrator running.

Wire format: mmwave/MmwProtocol.h MmwTargetsPayload —
  header <HBBI> (magic 0x534C, version 5, cmd 0x70, epoch)
  + seq(u16) count(u8) flags(u8, bit0 = parse healthy)
  + 3 x {xMm i16, yMm i16, speedCms i16, resMm u16}, unused slots zeroed.
"""

import argparse
import csv
import math
import socket
import statistics
import struct
import sys
import time
from datetime import datetime

UDP_MAGIC = 0x534C
UDP_VERSION = 5
CMD_MMW_TARGETS = 0x70
PACKET_LEN = 36          # 8-byte header + 28-byte payload
SLOTS = 3                # Rd-03D hard limit
NO_TRAFFIC_WARN_S = 10.0
EPISODE_GAP_S = 1.5      # count==0 (or silence) this long ends a presence episode
LIVE_PERIOD_S = 2.0


def parse_args():
    ap = argparse.ArgumentParser(description="Capture and characterize MMW_TARGETS (0x70) bench traffic")
    ap.add_argument("--label", default="", help="session label folded into the CSV name, e.g. 2m / 5m / static / soak")
    ap.add_argument("--duration", type=float, default=None, help="stop after N seconds (default: run until Ctrl+C)")
    ap.add_argument("--csv", default=None, help="CSV output path (default: mmwave_bench_<label>_<ts>.csv in cwd)")
    ap.add_argument("--no-csv", action="store_true", help="disable CSV logging")
    ap.add_argument("--port", type=int, default=4210)
    ap.add_argument("--motion-thresh", type=float, default=10.0,
                    help="|speed| cm/s below which a target counts as static (fade-time metric); default 10")
    return ap.parse_args()


def open_socket(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # Deliberately no SO_REUSEADDR: on Windows a shared bind with the SlyLED
    # tray app succeeds but receives nothing (#908 field gotcha). A loud
    # bind failure is the better outcome.
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"ERROR: cannot bind UDP {port}: {e}", file=sys.stderr)
        print("       Something else holds the port — stop the installed SlyLED.exe", file=sys.stderr)
        print("       tray app and any running dev orchestrator, then retry.", file=sys.stderr)
        sys.exit(1)
    sock.settimeout(0.5)
    return sock


def parse_packet(data):
    """Return (seq, count, flags, slots[3] of (x, y, speed, res)) or None."""
    if len(data) < PACKET_LEN:
        return None
    magic, version, cmd, epoch = struct.unpack_from("<HBBI", data, 0)
    if magic != UDP_MAGIC or cmd != CMD_MMW_TARGETS:
        return None
    if version != UDP_VERSION:
        return "badver"
    seq, count, flags = struct.unpack_from("<HBB", data, 8)
    slots = [struct.unpack_from("<hhhH", data, 12 + i * 8) for i in range(SLOTS)]
    return (seq, count, flags, slots)


class NodeStats:
    """Per-source-IP accumulator."""

    def __init__(self, ip):
        self.ip = ip
        self.first_t = None
        self.last_t = None
        self.packets = 0
        self.target_frames = 0        # count >= 1
        self.keepalives = 0           # count == 0
        self.unhealthy = 0            # flags bit0 clear
        self.count_hist = [0] * (SLOTS + 1)
        self.last_seq = None
        self.seq_lost = 0
        self.seq_dup = 0
        self.seq_reorder = 0
        self.longest_gap_s = 0.0
        # slot-0 samples from target frames (item 3 jitter characterization)
        self.xs, self.ys, self.ranges, self.angles, self.speeds = [], [], [], [], []
        # presence episodes (item 4): list of [start_t, end_t, last_motion_t]
        self.episodes = []
        self._open_episode = None

    def add(self, t, seq, count, flags, slots, motion_thresh):
        if self.first_t is None:
            self.first_t = t
        if self.last_t is not None:
            self.longest_gap_s = max(self.longest_gap_s, t - self.last_t)
        self.last_t = t
        self.packets += 1
        self.count_hist[min(count, SLOTS)] += 1
        if not (flags & 0x01):
            self.unhealthy += 1

        if self.last_seq is not None:
            delta = (seq - self.last_seq) & 0xFFFF
            if delta == 0:
                self.seq_dup += 1
            elif delta < 0x8000:
                self.seq_lost += delta - 1
            else:
                self.seq_reorder += 1
        self.last_seq = seq

        if count >= 1:
            self.target_frames += 1
            x, y, speed, _res = slots[0]
            self.xs.append(x)
            self.ys.append(y)
            self.ranges.append(math.hypot(x, y))
            self.angles.append(math.degrees(math.atan2(x, y)))
            self.speeds.append(speed)
            if self._open_episode is None:
                self._open_episode = [t, t, None]
            self._open_episode[1] = t
            if abs(speed) >= motion_thresh:
                self._open_episode[2] = t
        else:
            self.keepalives += 1
            self._maybe_close_episode(t)

    def _maybe_close_episode(self, now):
        ep = self._open_episode
        if ep is not None and now - ep[1] >= EPISODE_GAP_S:
            self.episodes.append(ep)
            self._open_episode = None

    def finish(self):
        if self._open_episode is not None:
            self.episodes.append(self._open_episode)
            self._open_episode = None


def fmt_stats(samples, unit):
    if not samples:
        return "n/a"
    mean = statistics.fmean(samples)
    sd = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return f"{mean:8.1f} ± {sd:6.1f} {unit}  (n={len(samples)})"


def print_summary(nodes, t0, motion_thresh):
    wall = time.monotonic() - t0
    print(f"\n{'=' * 72}\nSession summary — {wall:.1f} s wall clock, {len(nodes)} node(s)")
    for ip, n in sorted(nodes.items()):
        n.finish()
        dur = (n.last_t - n.first_t) if n.packets > 1 else 0.0
        rate = (n.packets - 1) / dur if dur > 0 else 0.0
        expected = n.packets + n.seq_lost
        loss_pct = 100.0 * n.seq_lost / expected if expected else 0.0
        healthy_pct = 100.0 * (n.packets - n.unhealthy) / n.packets if n.packets else 0.0
        print(f"\n--- {ip} ---")
        print(f"  packets        : {n.packets} over {dur:.1f} s  ({rate:.1f} Hz mean)")
        print(f"  frames         : {n.target_frames} with targets, {n.keepalives} empty keepalives")
        print(f"  count histogram: " + "  ".join(f"{c}:{n.count_hist[c]}" for c in range(SLOTS + 1)))
        print(f"  seq            : {n.seq_lost} lost ({loss_pct:.2f}%), {n.seq_dup} dup, {n.seq_reorder} reorder")
        print(f"  longest silence: {n.longest_gap_s:.2f} s")
        print(f"  parse healthy  : {healthy_pct:.1f}%")
        print(f"  slot0 X        : {fmt_stats(n.xs, 'mm')}")
        print(f"  slot0 Y        : {fmt_stats(n.ys, 'mm')}")
        print(f"  slot0 range    : {fmt_stats(n.ranges, 'mm')}")
        print(f"  slot0 angle    : {fmt_stats(n.angles, 'deg')}")
        print(f"  slot0 speed    : {fmt_stats(n.speeds, 'cm/s')}")
        if n.episodes:
            print(f"  presence episodes ({len(n.episodes)}; fade = last-motion -> lost, "
                  f"motion >= {motion_thresh:g} cm/s):")
            for ep in n.episodes[:20]:
                start, end, last_motion = ep
                fade = (end - last_motion) if last_motion is not None else None
                fade_s = f"{fade:6.2f} s fade" if fade is not None else "  no motion seen"
                print(f"    t+{start - t0:7.1f}s  held {end - start:6.1f} s   {fade_s}")
            if len(n.episodes) > 20:
                print(f"    ... {len(n.episodes) - 20} more (see CSV)")


def main():
    args = parse_args()
    sock = open_socket(args.port)

    writer = csv_file = None
    if not args.no_csv:
        path = args.csv
        if path is None:
            tag = f"_{args.label}" if args.label else ""
            path = f"mmwave_bench{tag}_{datetime.now():%Y%m%d_%H%M%S}.csv"
        csv_file = open(path, "w", newline="")
        writer = csv.writer(csv_file)
        writer.writerow(["iso_ts", "t_rel_s", "src_ip", "seq", "count", "flags"]
                        + [f"{f}{i}" for i in range(SLOTS) for f in ("x", "y", "speed", "res")])
        print(f"Logging to {path}")

    label = f" [{args.label}]" if args.label else ""
    stop_txt = f"for {args.duration:g} s" if args.duration else "until Ctrl+C"
    print(f"Listening on UDP {args.port} for 0x70 MMW_TARGETS{label}, {stop_txt}...")

    nodes = {}
    t0 = time.monotonic()
    other_cmds = badver = 0
    last_live = t0
    warned_quiet = False

    try:
        while True:
            now = time.monotonic()
            if args.duration is not None and now - t0 >= args.duration:
                break
            if not nodes and not warned_quiet and now - t0 >= NO_TRAFFIC_WARN_S:
                warned_quiet = True
                print(f"  ... no 0x70 traffic after {NO_TRAFFIC_WARN_S:g} s. Check: node powered + on WiFi\n"
                      f"      (its status page shows the target table), and NOTHING else bound to\n"
                      f"      {args.port} when this tool started (installed SlyLED.exe tray app!).")
            try:
                data, (ip, _port) = sock.recvfrom(2048)
            except socket.timeout:
                for n in nodes.values():
                    n._maybe_close_episode(time.monotonic())
                continue

            parsed = parse_packet(data)
            if parsed is None:
                other_cmds += 1
                continue
            if parsed == "badver":
                badver += 1
                continue
            seq, count, flags, slots = parsed
            t = time.monotonic()
            node = nodes.get(ip)
            if node is None:
                node = nodes[ip] = NodeStats(ip)
                print(f"  node detected: {ip}")
            node.add(t, seq, count, flags, slots, args.motion_thresh)

            if writer:
                writer.writerow([datetime.now().isoformat(timespec="milliseconds"),
                                 f"{t - t0:.3f}", ip, seq, count, flags]
                                + [v for slot in slots for v in slot])

            if t - last_live >= LIVE_PERIOD_S:
                last_live = t
                parts = []
                for nip, n in sorted(nodes.items()):
                    if n.target_frames and n.xs:
                        parts.append(f"{nip} n={n.count_hist[1] + n.count_hist[2] + n.count_hist[3]} "
                                     f"x={n.xs[-1]} y={n.ys[-1]} r={n.ranges[-1]:.0f}mm "
                                     f"a={n.angles[-1]:+.1f}deg v={n.speeds[-1]}cm/s")
                    else:
                        parts.append(f"{nip} (no targets yet)")
                print("  " + "   |   ".join(parts))
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        sock.close()
        if csv_file:
            csv_file.close()

    if other_cmds or badver:
        print(f"(ignored: {other_cmds} non-0x70 packets, {badver} wrong-version 0x70)")
    if not nodes:
        print("No MMW_TARGETS packets captured.")
        return
    print_summary(nodes, t0, args.motion_thresh)


if __name__ == "__main__":
    main()
