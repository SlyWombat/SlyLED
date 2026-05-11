#!/usr/bin/env python3
"""test_load_simulation.py — operator-realistic high-load scenario.

Simulates a worst-case venue:
  * 10 fixtures (5 DMX movers + 5 LED par/strips)
  * 100 actions in the global library (mix of solid, fade, breathe,
    chase, twinkle, DMX_SCENE)
  * 1 baked timeline with many tracks (each fixture × multiple clips)
  * 10 tracked people (YOLO-equivalent moving objects updated at 10 Hz)
  * 2 gyro gyros streaming orient at 20 Hz (simulated via direct
    update_from_euler_deg — same code path as the UDP listener)
  * 1 Android Auto Brightness feed posting `/api/brightness` at 20 Hz
  * Show running, all background threads live (DMX engine,
    mover_engine, claim arbiter, Track-action evaluator)

Captures per-second samples of:
  - CPU% (psutil, process-scoped)
  - Memory RSS
  - Latency for SPA-equivalent reads (/api/fixtures/live,
    /api/show/status, /api/dmx/monitor/1, /api/remotes/live)
  - DMX universe write rate (frames/sec at the playback loop's tick)

Two modes:
  * default — single 30 s run of the headline scenario
  * --sweep — vary N people / N actions / N gyros to find the
    breaking point (latency p95 > 100 ms or RSS > 1 GB)

Caveats:
  * In-process measurement via Flask test_client. Real-network
    latency (UDP receive cost, ArtNet send cost, TCP buffering on
    `_spa()` writes) is not exercised. Production CPU/RSS will be
    similar; latency will be dominated by network overhead, not the
    in-process numbers reported here.
  * Camera detection (YOLO inference) runs ON THE CAMERA NODE in
    production, not on the orchestrator. The orchestrator only
    receives the resulting object positions. This harness simulates
    that downstream half.

Run:
    python -X utf8 tests/test_load_simulation.py
    python -X utf8 tests/test_load_simulation.py --sweep
"""

import argparse
import math
import os
import random
import statistics
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402
import psutil  # noqa: E402

random.seed(2026)


# ── Setup helpers ───────────────────────────────────────────────────────

def reset(c):
    c.post("/api/reset", headers={"X-SlyLED-Confirm": "true"})


def seed_rig(c, n_movers=5, n_leds=5):
    """Create the fixture rig. Movers get Home + Secondary so AimSphere
    can resolve. LEDs get a child id."""
    fixture_ids = []
    for i in range(n_movers):
        r = c.post("/api/fixtures", json={
            "name": f"Mover {i}", "type": "point", "fixtureType": "dmx",
            "dmxUniverse": 1, "dmxStartAddr": 1 + i * 13,
            "dmxChannelCount": 12,
            "dmxProfileId": "movinghead-150w-12ch",
            "rotation": [0, 0, 0],
        })
        fid = r.get_json().get("id")
        c.post(f"/api/fixtures/{fid}/home", json={
            "panDmx16": 32768, "tiltDmx16": 16384,
            "secondary": {
                "panOffsetDmx16": 16384, "tiltOffsetDmx16": 16384,
                "panMovedDirection": "right", "tiltMovedDirection": "up",
            },
        })
        fixture_ids.append(fid)
    led_ids = []
    for i in range(n_leds):
        r = c.post("/api/children", json={
            "name": f"LED {i}",
            "strings": [{"leds": 60, "mm": 3000, "sdir": 0, "ledType": 0,
                          "cdir": 0, "folded": False, "stripDir": 0,
                          "lengthMm": 3000, "cableMm": 0}],
        })
        cid = r.get_json().get("id")
        r = c.post("/api/fixtures", json={
            "name": f"LED Fixture {i}", "type": "linear",
            "fixtureType": "led", "childId": cid,
        })
        fid = r.get_json().get("id")
        led_ids.append(fid)
    # Layout positions
    children = []
    for j, fid in enumerate(fixture_ids + led_ids):
        children.append({"id": fid,
                          "x": 1000 + (j % 5) * 1500,
                          "y": 1000 + (j // 5) * 1500,
                          "z": 2500})
    c.post("/api/layout", json={"children": children})
    return fixture_ids, led_ids


def seed_actions(c, n=100):
    """Create N varied actions in the library."""
    ids = []
    types = [(1, "Solid"), (3, "Breathe"), (4, "Chase"), (8, "Twinkle"),
              (12, "Sparkle"), (14, "DMX Scene")]
    for i in range(n):
        atype, label = types[i % len(types)]
        body = {"name": f"{label} {i}", "type": atype,
                "r": (i * 7) % 256, "g": (i * 13) % 256, "b": (i * 19) % 256}
        if atype == 3:
            body["periodMs"] = 2000 + (i % 10) * 500
            body["minBri"] = 30
        elif atype == 4:
            body["speedMs"] = 50 + (i % 10) * 10
            body["spacing"] = 2 + (i % 5)
        elif atype == 8 or atype == 12:
            body["spawnMs"] = 80 + (i % 5) * 40
            body["density"] = 3
            body["fadeSpeed"] = 12
        elif atype == 14:
            body["dimmer"] = 200
            body["pan"] = 0.5
            body["tilt"] = 0.5
        r = c.post("/api/actions", json=body)
        ids.append(r.get_json().get("id"))
    return ids


def seed_timeline(c, fixture_ids, action_ids, n_clips_per_fixture=5,
                   duration=30):
    """Create a baked timeline with N clips per fixture."""
    tracks = []
    for fid in fixture_ids:
        clips = []
        slot = duration / n_clips_per_fixture
        for k in range(n_clips_per_fixture):
            clips.append({
                "actionId": random.choice(action_ids),
                "startS": k * slot,
                "durationS": slot,
            })
        tracks.append({"fixtureId": fid, "clips": clips})
    r = c.post("/api/timelines", json={
        "name": "LoadShow", "durationS": duration, "tracks": tracks,
    })
    tid = r.get_json().get("id")

    # Inject synthetic bake — bypasses bake engine's per-fixture
    # spatial-engine.compute step (which is not under test here; we
    # want the playback render path to drive the universe at 40 Hz).
    bake_fixtures = {}
    for fid in fixture_ids:
        slot = duration / n_clips_per_fixture
        segs = []
        for k in range(n_clips_per_fixture):
            segs.append({
                "startS": k * slot, "durationS": slot, "_pri": 0,
                "params": {
                    "r": random.randint(50, 255),
                    "g": random.randint(50, 255),
                    "b": random.randint(50, 255),
                    "dimmer": random.randint(150, 255),
                    "pan": random.uniform(0.2, 0.8),
                    "tilt": random.uniform(0.2, 0.8),
                },
            })
        bake_fixtures[fid] = {"segments": segs}
    parent_server._bake_result[tid] = {
        "timelineId": tid, "bakedAt": int(time.time()),
        "fixtures": bake_fixtures, "totalFrames": 0, "fps": 40,
    }
    return tid


def seed_tracked_people(c, n=10):
    """Create N moving 'person' objects (YOLO-equivalent). Each gets a
    patrol so the position updates over time."""
    obj_ids = []
    for i in range(n):
        r = c.post("/api/objects", json={
            "name": f"Person {i}", "objectType": "person",
            "mobility": "moving",
            "color": "#00DCFF", "opacity": 50,
            "transform": {
                "pos": [random.randint(0, 8000),
                         random.randint(0, 4000),
                         1700],
                "rot": [0, 0, 0], "scale": [400, 400, 1700],
            },
            "patrol": {
                "enabled": True, "pattern": "rect",
                "speedPreset": "medium",
                "startPct": 10 + (i * 8) % 80,
                "endPct": 20 + (i * 8) % 80,
                "patrolMode": "continuous",
            },
        })
        obj_ids.append(r.get_json().get("id"))
    return obj_ids


def seed_gyros(c, n=2):
    """Add N gyro Remotes pre-calibrated against fixture 0 (so the
    claim writer trusts cross-session cal per #847). Returns the
    device-id list and the Remote handles."""
    from remote_orientation import KIND_GYRO
    remotes = []
    for i in range(n):
        did = f"gyro-load-{i:02d}"
        rem = parent_server._remotes.add(
            name=f"LoadPuck{i}", kind=KIND_GYRO, device_id=did)
        rem.R_world_to_stage = (1.0, 0.0, 0.0, 0.0)
        rem.calibrated = True
        rem.calibrated_at = time.time()
        rem.calibrated_against = {"kind": "mover", "objectId": 0}
        rem.stale_reason = None
        remotes.append((did, rem))
    return remotes


# ── Background workers ──────────────────────────────────────────────────

class GyroFeeder(threading.Thread):
    """Simulates a gyro streaming orient at 20 Hz (50 ms interval).
    Calls `update_from_euler_deg` directly — same code path as the UDP
    listener at parent_server.py:1385."""
    def __init__(self, remote, name="GyroFeed"):
        super().__init__(daemon=True, name=name)
        self.remote = remote
        self.running = True
        self.tick_count = 0

    def run(self):
        t0 = time.time()
        while self.running:
            t = time.time() - t0
            roll = math.sin(t * 0.7) * 30
            pitch = math.cos(t * 0.5) * 20
            yaw = math.sin(t * 0.3) * 45
            self.remote.update_from_euler_deg(roll, pitch, yaw)
            self.tick_count += 1
            time.sleep(0.05)


class BrightnessFeeder(threading.Thread):
    """Simulates Android Auto Brightness at 20 Hz with a beat-shaped
    envelope (BPM 120 = 0.5 s per beat). Calls the orchestrator's
    in-process functions directly (skips Flask test_client because
    its request-context stack isn't multi-thread safe — concurrent
    .post / .get on a shared client tangles the context-local
    push/pop and raises 'Token was created in a different Context')."""
    def __init__(self, name="BriFeed"):
        super().__init__(daemon=True, name=name)
        self.running = True
        self.tick_count = 0
        self.error_count = 0

    def run(self):
        t0 = time.time()
        while self.running:
            t = time.time() - t0
            beat = abs(math.sin(t * math.pi * 2 * 2.0))  # 2 Hz = 120 BPM
            value = int(160 + (255 - 160) * beat)
            try:
                with parent_server._lock:
                    parent_server._settings["globalBrightness"] = value
                # Same downstream as api_brightness_fast: log + broadcast.
                parent_server._log_brightness_hop("127.0.0.1", value, value)
                parent_server._broadcast_brightness(value)
                self.tick_count += 1
            except Exception:
                self.error_count += 1
            time.sleep(0.05)


class ObjectMover(threading.Thread):
    """Simulates camera-driven YOLO position updates at 10 Hz. Each
    object's position is jittered from its current value by a small
    random walk. Direct mutation of `_objects` (not via test_client)
    matches what the camera-detection receive endpoint routes through
    once a detection lands; it also avoids the shared-test_client
    multi-thread context issue."""
    def __init__(self, obj_ids, name="ObjMove"):
        super().__init__(daemon=True, name=name)
        self.obj_ids = obj_ids
        self.running = True
        self.tick_count = 0

    def run(self):
        while self.running:
            for oid in self.obj_ids:
                obj = next((o for o in parent_server._objects
                             if o.get("id") == oid), None)
                if obj is not None:
                    pos = obj["transform"]["pos"]
                    pos[0] = max(0, min(10000, pos[0] + random.randint(-150, 150)))
                    pos[1] = max(0, min(5000, pos[1] + random.randint(-150, 150)))
            self.tick_count += 1
            time.sleep(0.1)


# ── Measurement ─────────────────────────────────────────────────────────

def measure_latency(c, endpoint, n=10):
    """Median + p95 latency of a GET endpoint, in milliseconds."""
    samples = []
    for _ in range(n):
        t0 = time.perf_counter()
        c.get(endpoint)
        samples.append((time.perf_counter() - t0) * 1000)
    samples.sort()
    return {
        "median": statistics.median(samples),
        "p95": samples[int(0.95 * (n - 1))],
        "max": max(samples),
    }


def run_scenario(label, n_movers, n_leds, n_actions, n_clips_per_fixture,
                  n_gyros, n_people, duration_s):
    """Run the load scenario for `duration_s` seconds and return metrics."""
    print(f"\n=== {label} "
          f"(movers={n_movers}, leds={n_leds}, actions={n_actions}, "
          f"clips/fix={n_clips_per_fixture}, gyros={n_gyros}, "
          f"people={n_people}, duration={duration_s}s) ===")
    proc = psutil.Process(os.getpid())
    proc.cpu_percent(interval=None)  # prime

    with parent_server.app.test_client() as c:
        reset(c)
        c.post("/api/dmx/start", json={"protocol": "artnet"})
        fixture_ids, led_ids = seed_rig(c, n_movers=n_movers, n_leds=n_leds)
        action_ids = seed_actions(c, n=n_actions)
        tid = seed_timeline(c, fixture_ids, action_ids,
                              n_clips_per_fixture=n_clips_per_fixture,
                              duration=duration_s + 5)
        obj_ids = seed_tracked_people(c, n=n_people)
        gyros = seed_gyros(c, n=n_gyros)

        rss_pre_mb = proc.memory_info().rss / (1024 * 1024)

        # Start show via the playback loop directly (skips the 5 s
        # NTP-alignment wait that /api/show/start imposes).
        parent_server._dmx_playback_stop.set()
        time.sleep(0.05)
        parent_server._dmx_playback_stop.clear()
        threading.Thread(
            target=parent_server._dmx_playback_loop,
            args=(tid, time.time() - 0.05, duration_s + 5, False),
            daemon=True,
        ).start()

        # Spawn background feeders.
        gyro_threads = [GyroFeeder(rem) for (_did, rem) in gyros]
        for t in gyro_threads:
            t.start()
        bri_thread = BrightnessFeeder()
        bri_thread.start()
        obj_thread = ObjectMover(obj_ids)
        obj_thread.start()

        time.sleep(0.5)  # warmup

        cpu_samples = []
        rss_samples = []
        latencies = {ep: [] for ep in [
            "/api/fixtures/live",
            "/api/show/status",
            "/api/dmx/monitor/1",
            "/api/remotes/live",
            "/api/objects",
        ]}

        t_start = time.time()
        while time.time() - t_start < duration_s:
            cpu_samples.append(proc.cpu_percent(interval=None))
            rss_samples.append(proc.memory_info().rss / (1024 * 1024))
            for ep in latencies:
                t0 = time.perf_counter()
                c.get(ep)
                latencies[ep].append((time.perf_counter() - t0) * 1000)
            time.sleep(1.0)

        # Stop feeders + playback.
        for t in gyro_threads:
            t.running = False
        bri_thread.running = False
        obj_thread.running = False
        parent_server._dmx_playback_stop.set()

        rss_post_mb = proc.memory_info().rss / (1024 * 1024)

        print(f"\n  Throughput:")
        print(f"    Gyro orient:      "
              f"{sum(t.tick_count for t in gyro_threads)} ticks "
              f"({sum(t.tick_count for t in gyro_threads) / duration_s:.0f}/s "
              f"target {n_gyros * 20}/s)")
        print(f"    Brightness POST:  {bri_thread.tick_count} ticks "
              f"({bri_thread.tick_count / duration_s:.0f}/s target 20/s)")
        if bri_thread.error_count:
            print(f"      errors:         {bri_thread.error_count}")
        print(f"    Object moves:     {obj_thread.tick_count} ticks "
              f"({obj_thread.tick_count / duration_s:.0f}/s target 10/s)")

        print(f"\n  CPU:")
        print(f"    median:   {statistics.median(cpu_samples):.1f}%")
        print(f"    mean:     {statistics.mean(cpu_samples):.1f}%")
        print(f"    p95:      "
              f"{sorted(cpu_samples)[int(0.95 * (len(cpu_samples) - 1))]:.1f}%")
        print(f"    max:      {max(cpu_samples):.1f}%")

        print(f"\n  Memory (RSS):")
        print(f"    pre-load: {rss_pre_mb:.1f} MB")
        print(f"    median:   {statistics.median(rss_samples):.1f} MB")
        print(f"    max:      {max(rss_samples):.1f} MB")
        print(f"    growth:   {rss_post_mb - rss_pre_mb:+.1f} MB "
              f"(post {rss_post_mb:.1f} - pre {rss_pre_mb:.1f})")

        print(f"\n  SPA-equivalent latency (ms; {len(latencies['/api/fixtures/live'])} samples each):")
        print(f"    {'endpoint':30s}  {'median':>8s}  {'p95':>8s}  {'max':>8s}")
        for ep, samples in latencies.items():
            samples.sort()
            med = statistics.median(samples)
            p95 = samples[int(0.95 * (len(samples) - 1))]
            mx = max(samples)
            print(f"    {ep:30s}  {med:>8.2f}  {p95:>8.2f}  {mx:>8.2f}")

        # Cleanup gyro Remotes
        for (_did, rem) in gyros:
            parent_server._remotes.remove(rem.id)

        return {
            "label": label,
            "cpu_median": statistics.median(cpu_samples),
            "cpu_p95": sorted(cpu_samples)[int(0.95 * (len(cpu_samples) - 1))],
            "rss_pre_mb": rss_pre_mb,
            "rss_max_mb": max(rss_samples),
            "rss_growth_mb": rss_post_mb - rss_pre_mb,
            "latency": {ep: {
                "median": statistics.median(samples),
                "p95": sorted(samples)[int(0.95 * (len(samples) - 1))],
                "max": max(samples),
            } for ep, samples in latencies.items()},
            "n_movers": n_movers,
            "n_leds": n_leds,
            "n_actions": n_actions,
            "n_gyros": n_gyros,
            "n_people": n_people,
        }


def headline_scenario(duration_s=30):
    """The user's exact ask: 5+5 fixtures, 100 actions, 50 timeline
    clips, 10 tracked people, 2 gyros, 1 Android brightness feed."""
    return run_scenario(
        "Headline scenario (operator's spec)",
        n_movers=5, n_leds=5, n_actions=100, n_clips_per_fixture=10,
        n_gyros=2, n_people=10, duration_s=duration_s,
    )


# ── Venue-sized tiers ───────────────────────────────────────────────────
# Each tier corresponds to a representative real-world venue size, with
# fixture counts and operator-driven feeds calibrated to what an
# operator would realistically run. All tiers run the same duration so
# CPU / RSS / latency comparisons are apples-to-apples.

VENUE_TIERS = [
    # Club / small bar / restaurant (~50-150 capacity)
    {
        "label": "Club (≤150 cap, single operator)",
        "n_movers": 2, "n_leds": 2, "n_actions": 20,
        "n_clips_per_fixture": 4, "n_gyros": 1, "n_people": 0,
    },
    # Mid-size theatre / event hall (~300-800 capacity)
    {
        "label": "Theatre (300-800 cap, 1 LD + 1 gyro)",
        "n_movers": 5, "n_leds": 5, "n_actions": 50,
        "n_clips_per_fixture": 6, "n_gyros": 1, "n_people": 4,
    },
    # Concert / corporate / convention (~1k-5k capacity)
    {
        "label": "Concert (1k-5k cap, 2 LDs, headline scenario)",
        "n_movers": 10, "n_leds": 20, "n_actions": 100,
        "n_clips_per_fixture": 8, "n_gyros": 2, "n_people": 10,
    },
    # Arena / stadium tour (~10k-50k capacity)
    {
        "label": "Arena (10k-50k cap, full crew)",
        "n_movers": 30, "n_leds": 60, "n_actions": 300,
        "n_clips_per_fixture": 10, "n_gyros": 4, "n_people": 20,
    },
    # Stadium tour / festival (50k+ capacity)
    {
        "label": "Stadium / festival (50k+ cap, max rig)",
        "n_movers": 50, "n_leds": 120, "n_actions": 500,
        "n_clips_per_fixture": 12, "n_gyros": 4, "n_people": 30,
    },
]


def venue_tier_scenarios(duration_s=15):
    """Run each venue tier in turn so an operator can pick the closest
    match to their rig and read the expected resource envelope."""
    print("\n\n=== VENUE TIER SCENARIOS ===")
    results = []
    for tier in VENUE_TIERS:
        results.append(run_scenario(
            tier["label"],
            n_movers=tier["n_movers"], n_leds=tier["n_leds"],
            n_actions=tier["n_actions"],
            n_clips_per_fixture=tier["n_clips_per_fixture"],
            n_gyros=tier["n_gyros"], n_people=tier["n_people"],
            duration_s=duration_s,
        ))
    print("\n\n=== VENUE TIER SUMMARY ===")
    print(f"{'venue':45s} {'cpu med':>8s} {'cpu p95':>8s} "
          f"{'rss max':>8s} {'live p95':>9s} {'mon p95':>8s}")
    for r in results:
        print(f"{r['label']:45s} "
              f"{r['cpu_median']:>7.1f}% "
              f"{r['cpu_p95']:>7.1f}% "
              f"{r['rss_max_mb']:>6.1f}MB "
              f"{r['latency']['/api/fixtures/live']['p95']:>8.2f}ms "
              f"{r['latency']['/api/dmx/monitor/1']['p95']:>7.2f}ms")
    return results


def sweep_to_find_limits():
    """Sweep N people, gyros, and actions to find degradation
    inflection. Each step runs 10 s (shorter than the headline so
    the sweep finishes in reasonable time)."""
    print("\n\n=== LIMIT SWEEP ===")
    results = []
    for n_people in (1, 10, 50, 100):
        results.append(run_scenario(
            f"Sweep people={n_people}", n_movers=5, n_leds=5,
            n_actions=100, n_clips_per_fixture=5,
            n_gyros=2, n_people=n_people, duration_s=10,
        ))
    for n_gyros in (1, 2, 8, 16):
        results.append(run_scenario(
            f"Sweep gyros={n_gyros}", n_movers=5, n_leds=5,
            n_actions=100, n_clips_per_fixture=5,
            n_gyros=n_gyros, n_people=10, duration_s=10,
        ))
    for n_actions in (10, 100, 500, 1000):
        results.append(run_scenario(
            f"Sweep actions={n_actions}", n_movers=5, n_leds=5,
            n_actions=n_actions, n_clips_per_fixture=5,
            n_gyros=2, n_people=10, duration_s=10,
        ))

    print("\n\n=== SWEEP SUMMARY ===")
    print(f"{'scenario':45s} {'cpu med':>8s} {'cpu p95':>8s} "
          f"{'rss max':>8s} {'live p95':>9s} {'mon p95':>8s}")
    for r in results:
        print(f"{r['label']:45s} "
              f"{r['cpu_median']:>7.1f}% "
              f"{r['cpu_p95']:>7.1f}% "
              f"{r['rss_max_mb']:>6.1f}MB "
              f"{r['latency']['/api/fixtures/live']['p95']:>8.2f}ms "
              f"{r['latency']['/api/dmx/monitor/1']['p95']:>7.2f}ms")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sweep", action="store_true",
                   help="Run the limit-finding sweep")
    p.add_argument("--tiers", action="store_true",
                   help="Run the 5 venue-tier scenarios (club → stadium)")
    p.add_argument("--all", action="store_true",
                   help="Headline + tiers + sweep")
    p.add_argument("--duration", type=int, default=30,
                   help="Headline scenario duration in seconds (default 30)")
    p.add_argument("--tier-duration", type=int, default=15,
                   help="Per-tier duration in seconds (default 15)")
    args = p.parse_args()
    if args.all:
        headline_scenario(duration_s=args.duration)
        venue_tier_scenarios(duration_s=args.tier_duration)
        sweep_to_find_limits()
    else:
        headline_scenario(duration_s=args.duration)
        if args.tiers:
            venue_tier_scenarios(duration_s=args.tier_duration)
        if args.sweep:
            sweep_to_find_limits()
    return 0


if __name__ == "__main__":
    sys.exit(main())
