#!/usr/bin/env python3
"""Build a 5-minute EDM sky-sweep light show and install it as the auto-show.

Creates a beat-phrased 300s timeline:
  * 350W BeamLight  — continuous sky sweep, tilt held 60-86° above horizon
  * 150W movers     — mid-air and crowd sweeps for contrast
  * ESP LED strings — built-in motion effects (comet/fire/chase/rainbow/…)

Run against a live orchestrator:  python tools/build_edm_show.py [base_url]
The show is baked, synced, started, and flagged as the auto-start show.
"""
import json
import math
import sys
import time
import urllib.request

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://localhost:9000"


def api(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        txt = r.read().decode()
    return json.loads(txt) if txt.strip() else {}


# ── EDM colour palette ────────────────────────────────────────────────
CYAN    = (0, 229, 255)
MAGENTA = (255, 0, 208)
PURPLE  = (150, 0, 255)
LIME    = (160, 255, 0)
ORANGE  = (255, 85, 0)
PINK    = (255, 0, 102)
BLUE    = (0, 102, 255)
WHITE   = (255, 255, 255)
RED     = (255, 40, 0)
GREEN   = (0, 255, 80)
AMBER   = (255, 150, 0)

_actions = []   # accumulates {name,type,...} dicts; POSTed in one pass


def act(name, type_, **params):
    """Register an action; returns a placeholder index resolved after POST."""
    a = {"name": name, "type": type_, "scope": "performer"}
    a.update(params)
    _actions.append(a)
    return len(_actions) - 1


# ── Moving-head aim geometry ──────────────────────────────────────────
# A Pan/Tilt Move bakes ptStartPos→ptEndPos through spatial_engine.
# compute_pan_tilt: tilt = atan2(|dz|, horizontal_dist). Keeping the aim
# point near the ceiling and within a tight horizontal radius of the
# fixture holds the beam steeply skyward.

def orbit(phi_deg, cx, cy, z):
    """A point on a horizontal circle — orbit centre (cx,cy), height z."""
    r = math.radians(phi_deg)
    return [round(cx + ORBIT_R * math.cos(r)),
            round(cy + ORBIT_R * math.sin(r)), z]


def _tilt_deg(fx, pt):
    dx, dy, dz = pt[0] - fx[0], pt[1] - fx[1], pt[2] - fx[2]
    return math.degrees(math.atan2(abs(dz), math.hypot(dx, dy)))


# ── Discover the rig ──────────────────────────────────────────────────
print(f"Orchestrator: {BASE}")
fixtures = api("GET", "/api/fixtures")
by_name = {f.get("name", ""): f for f in fixtures}


def find(substr, ftype):
    for f in fixtures:
        if substr.lower() in f.get("name", "").lower() and \
           (f.get("fixtureType") or f.get("type")) == ftype:
            return f
    return None


f350 = find("350w", "dmx")
f150l = find("stage left", "dmx")
f150r = find("stage right", "dmx")
led_fx = next((f for f in fixtures
               if (f.get("fixtureType") or f.get("type")) == "led"
               and "esp dual" in f.get("name", "").lower()), None)
if not (f350 and led_fx):
    sys.exit("Could not find the 350W mover and/or the ESP LED fixture")

print(f"  350W   : fid={f350['id']}  pos={f350.get('x'),f350.get('y'),f350.get('z')}")
print(f"  150W-L : fid={f150l['id'] if f150l else '-'}")
print(f"  150W-R : fid={f150r['id'] if f150r else '-'}")
print(f"  LED    : fid={led_fx['id']}  ({led_fx.get('name')})")

P350 = [f350.get("x", 0), f350.get("y", 0), f350.get("z", 0)]
P150L = [f150l.get("x", 0), f150l.get("y", 0), f150l.get("z", 0)] if f150l else None
P150R = [f150r.get("x", 0), f150r.get("y", 0), f150r.get("z", 0)] if f150r else None


# ── EDM section map (128 BPM, 15s phrases) ────────────────────────────
# (start, end, label, led_speed_mult, mover_pan_speed deg/s)
SECTIONS = [
    (0,   30,  "intro",     1.0, 3.0),
    (30,  75,  "build1",    0.8, 4.5),
    (75,  135, "drop1",     0.45, 9.0),
    (135, 180, "breakdown", 1.1, 3.0),
    (180, 210, "build2",    0.7, 5.5),
    (210, 270, "drop2",     0.40, 10.5),
    (270, 300, "outro",     1.2, 3.0),
]
DURATION = 300

# ══════════════════════════════════════════════════════════════════════
# LED programme — built-in motion effects, colourful, beat-phrased.
# Each tuple: (start, dur, action). speedMs lower = faster.
# ══════════════════════════════════════════════════════════════════════
led_clips = []  # (action_index, startS, durationS)


def led_clip(start, dur, ai):
    led_clips.append((ai, start, dur))


# intro — wake the rig up
led_clip(0,   20, act("LED Rainbow Rise",  5, speedMs=70, periodMs=9000, spacing=2))
led_clip(20,  20, act("LED Comet Cyan",    7, r=CYAN[0], g=CYAN[1], b=CYAN[2],
                      speedMs=46, tailLen=20, decay=18, direction=0))
led_clip(40,  20, act("LED Chase Magenta", 4, r=MAGENTA[0], g=MAGENTA[1], b=MAGENTA[2],
                      speedMs=34, spacing=4, tailLen=8, direction=1))
led_clip(60,  15, act("LED Comet Purple",  7, r=PURPLE[0], g=PURPLE[1], b=PURPLE[2],
                      speedMs=30, tailLen=16, decay=22, direction=1))
# DROP 1 — fast, hot
led_clip(75,  15, act("LED Chase Cyan FX", 4, r=CYAN[0], g=CYAN[1], b=CYAN[2],
                      speedMs=15, spacing=3, tailLen=6, direction=0))
led_clip(90,  15, act("LED Fire Drop1",    6, r=255, g=70, b=0,
                      speedMs=17, cooling=52, sparking=150))
led_clip(105, 15, act("LED Comet Pink FX", 7, r=PINK[0], g=PINK[1], b=PINK[2],
                      speedMs=13, tailLen=11, decay=14, direction=0))
led_clip(120, 15, act("LED Scanner Lime",  11, r=LIME[0], g=LIME[1], b=LIME[2],
                      speedMs=15, tailLen=9, direction=1))
# breakdown — airy
led_clip(135, 22, act("LED Twinkle Blue",  8, r=BLUE[0], g=BLUE[1], b=BLUE[2],
                      speedMs=58, density=70, decay=24, fadeSpeed=18, direction=0))
led_clip(157, 23, act("LED Sparkle Magenta", 12, r=MAGENTA[0], g=MAGENTA[1], b=MAGENTA[2],
                      speedMs=48))
# build 2
led_clip(180, 15, act("LED Chase Orange",  4, r=ORANGE[0], g=ORANGE[1], b=ORANGE[2],
                      speedMs=26, spacing=4, tailLen=7, direction=1))
led_clip(195, 15, act("LED Rainbow Build", 5, speedMs=24, periodMs=4000, spacing=3))
# DROP 2 — peak
led_clip(210, 15, act("LED Comet Cyan FX2", 7, r=CYAN[0], g=CYAN[1], b=CYAN[2],
                      speedMs=12, tailLen=12, decay=12, direction=1))
led_clip(225, 15, act("LED Fire Drop2",    6, r=255, g=90, b=10,
                      speedMs=15, cooling=48, sparking=160))
led_clip(240, 15, act("LED Scanner Mag FX", 11, r=MAGENTA[0], g=MAGENTA[1], b=MAGENTA[2],
                      speedMs=13, tailLen=10, direction=0))
led_clip(255, 15, act("LED Chase Pink FX", 4, r=PINK[0], g=PINK[1], b=PINK[2],
                      speedMs=14, spacing=3, tailLen=6, direction=1))
# outro — wind down
led_clip(270, 18, act("LED Rainbow Outro", 5, speedMs=66, periodMs=9000, spacing=2))
led_clip(288, 12, act("LED Breathe Purple", 3, r=PURPLE[0], g=PURPLE[1], b=PURPLE[2],
                      speedMs=3200, periodMs=3200, minBri=12))

# ══════════════════════════════════════════════════════════════════════
# Moving-head programmes — continuous Pan/Tilt sweeps.
# ══════════════════════════════════════════════════════════════════════
MOVER_COLOURS = [CYAN, MAGENTA, PURPLE, LIME, ORANGE, PINK, BLUE, WHITE,
                 CYAN, AMBER, MAGENTA, GREEN, PURPLE, PINK]


def build_mover(fx_pos, name, cx, cy, z, radius, clip_len=22):
    """Continuous sweep clips for one mover. Returns [(ai,start,dur)]."""
    global ORBIT_R
    ORBIT_R = radius
    clips = []
    phi = 0.0
    t = 0.0
    i = 0
    worst_tilt = 999.0
    while t < DURATION - 0.5:
        sec = next(s for s in SECTIONS if s[0] <= t < s[1])
        pan_speed = sec[4]
        dur = min(clip_len, sec[1] - t)
        if dur < 6:                       # absorb stub into this clip
            dur = sec[1] - t
        phi_end = phi + pan_speed * dur
        p0 = orbit(phi, cx, cy, z)
        p1 = orbit(phi_end, cx, cy, z)
        worst_tilt = min(worst_tilt, _tilt_deg(fx_pos, p0), _tilt_deg(fx_pos, p1))
        col = MOVER_COLOURS[i % len(MOVER_COLOURS)]
        ai = act(f"{name} sweep {i + 1}", 15,
                 ptStartPos=p0, ptEndPos=p1, dimmer=255,
                 r=col[0], g=col[1], b=col[2])
        clips.append((ai, round(t, 2), round(dur, 2)))
        phi, t, i = phi_end, t + dur, i + 1
    return clips, worst_tilt


# 350W — tight ceiling orbit, tilt stays well above 60°.
m350_clips, m350_tilt = build_mover(
    P350, "350W", cx=P350[0], cy=P350[1] + 500, z=2800, radius=700, clip_len=20)
print(f"  350W sweep: {len(m350_clips)} clips, min tilt {m350_tilt:.1f}° "
      f"(requirement: >60°)")
if m350_tilt < 60.0:
    sys.exit(f"ABORT: 350W tilt floor {m350_tilt:.1f}° violates the >60° spec")

# 150W movers — wider, lower orbits for contrast (no tilt constraint).
m150r_clips = []
m150l_clips = []
if P150R:
    m150r_clips, _ = build_mover(
        P150R, "150W-R", cx=P150R[0], cy=P150R[1] + 900, z=2300,
        radius=1100, clip_len=24)
if P150L:
    # Stage-Left mover is ceiling-mounted — sweep down across the crowd.
    m150l_clips, _ = build_mover(
        P150L, "150W-L", cx=P150L[0] - 2800, cy=P150L[1] + 4200, z=1600,
        radius=2600, clip_len=26)

# ── POST every action, capture real IDs ───────────────────────────────
print(f"Creating {len(_actions)} actions...")
ids = []
for a in _actions:
    r = api("POST", "/api/actions", a)
    if not r.get("ok"):
        sys.exit(f"Action create failed for {a['name']}: {r}")
    ids.append(r["id"])


def clips_json(clip_list):
    return [{"actionId": ids[ai], "startS": s, "durationS": d}
            for (ai, s, d) in clip_list]


# ── Assemble the timeline ─────────────────────────────────────────────
tracks = [{"fixtureId": led_fx["id"], "clips": clips_json(led_clips)},
          {"fixtureId": f350["id"],   "clips": clips_json(m350_clips)}]
if m150r_clips:
    tracks.append({"fixtureId": f150r["id"], "clips": clips_json(m150r_clips)})
if m150l_clips:
    tracks.append({"fixtureId": f150l["id"], "clips": clips_json(m150l_clips)})

tl = api("POST", "/api/timelines", {
    "name": "EDM Sky Sweep — Auto Show",
    "durationS": DURATION,
    "loop": True,
    "tracks": tracks,
})
tid = tl["id"]
total_clips = sum(len(t["clips"]) for t in tracks)
print(f"Timeline #{tid} created: {len(tracks)} tracks, {total_clips} clips, "
      f"{DURATION}s, looped")

# ── Bake ──────────────────────────────────────────────────────────────
print("Baking...")
api("POST", f"/api/timelines/{tid}/bake")
for _ in range(180):
    st = api("GET", f"/api/timelines/{tid}/baked/status")
    if st.get("done"):
        if st.get("error"):
            sys.exit(f"Bake error: {st['error']}")
        print(f"  baked ({st.get('progress', 100)}%)")
        break
    time.sleep(1)
else:
    sys.exit("Bake timed out")

# ── Sync to LED children ──────────────────────────────────────────────
print("Syncing to performers...")
api("POST", f"/api/timelines/{tid}/baked/sync")
for _ in range(120):
    st = api("GET", f"/api/timelines/{tid}/sync/status")
    if st.get("done"):
        print(f"  synced: {st.get('performers', {})}")
        break
    time.sleep(1)
else:
    print("  sync still pending — continuing (LED-only sync is best-effort)")

# ── Start + flag as the auto-show ─────────────────────────────────────
print("Starting playback...")
api("POST", f"/api/timelines/{tid}/start")
settings = api("GET", "/api/settings")
settings["autoStartShow"] = True
settings["activeTimeline"] = tid
api("POST", "/api/settings", settings)

time.sleep(2)
status = api("GET", f"/api/timelines/{tid}/status")
print(f"Playback running={status.get('running')}  elapsed={status.get('elapsed')}s")
print(f"\nDONE — 'EDM Sky Sweep' (timeline #{tid}) is live and set as the "
      f"auto-start show.")
