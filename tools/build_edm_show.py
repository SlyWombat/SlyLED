#!/usr/bin/env python3
"""Build a 5-minute EDM sky-sweep light show and install it as the auto-show.

Creates a beat-phrased 300s timeline:
  * 350W BeamLight  — pan locked stage-forward, tilt nods 62°→90° (sky)
  * 150W movers     — contrasting pan + tilt sweeps
  * ESP LED strings — built-in motion effects (comet/fire/chase/rainbow/…)

Run against a live orchestrator:  python tools/build_edm_show.py [base_url]
The show is baked, synced, started, and flagged as the auto-start show.
"""
import json
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


def cleanup_old_show():
    """Delete any previous 'EDM Sky Sweep' timeline and the actions it
    referenced, so this builder is idempotent — re-run it freely without
    piling up orphan timelines/actions."""
    for tl in api("GET", "/api/timelines"):
        if "EDM Sky Sweep" not in tl.get("name", ""):
            continue
        tid = tl["id"]
        try:
            api("POST", f"/api/timelines/{tid}/stop")
        except Exception:
            pass
        full = api("GET", f"/api/timelines/{tid}")
        aids = {cl["actionId"] for tr in full.get("tracks", [])
                for cl in tr.get("clips", [])
                if cl.get("actionId") is not None}
        api("DELETE", f"/api/timelines/{tid}")
        for aid in aids:
            try:
                api("DELETE", f"/api/actions/{aid}")
            except Exception:
                pass
        print(f"  removed old timeline #{tid} + {len(aids)} actions")


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


# ── Discover the rig ──────────────────────────────────────────────────
print(f"Orchestrator: {BASE}")
fixtures = api("GET", "/api/fixtures")


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

print(f"  350W   : fid={f350['id']}")
print(f"  150W-L : fid={f150l['id'] if f150l else '-'}")
print(f"  150W-R : fid={f150r['id'] if f150r else '-'}")
print(f"  LED    : fid={led_fx['id']}  ({led_fx.get('name')})")

print("Cleaning up any previous EDM Sky Sweep show...")
cleanup_old_show()


# ── EDM section map (128 BPM, 15s phrases) ────────────────────────────
# (start, end, label)
SECTIONS = [
    (0,   30,  "intro"),
    (30,  75,  "build1"),
    (75,  135, "drop1"),
    (135, 180, "breakdown"),
    (180, 210, "build2"),
    (210, 270, "drop2"),
    (270, 300, "outro"),
]
DURATION = 300

# ══════════════════════════════════════════════════════════════════════
# LED programme — built-in motion effects, colourful, beat-phrased.
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
# Moving-head programmes — Pan/Tilt Move actions emitted as normalised
# 0-1 DMX values directly (the bake's panStart/panEnd/tiltStart/tiltEnd
# path). Deterministic — no dependence on bake-time profile geometry.
# Convention (compute_pan_tilt): tilt_norm = 0.5 - degAboveHorizon/range;
# pan_norm = 0.5 is stage-forward (+Y, toward the audience).
# ══════════════════════════════════════════════════════════════════════
MOVER_COLOURS = [CYAN, MAGENTA, PURPLE, LIME, ORANGE, PINK, BLUE, WHITE,
                 CYAN, AMBER, MAGENTA, GREEN, PURPLE, PINK]
# 350W aim — calibrated DMX values, read back from the AimSphere
# (POST /api/mover/<fid>/aim {azDeg,elDeg}) which honours the fixture's
# home anchors. Pure geometry (compute_pan_tilt) does NOT match this
# fixture: both DMX tilt extremes point down, "up" is mid-range.
#   azDeg 0 (stage-forward) -> pan DMX 168
#   elevation 60° -> tilt DMX 94 ; 90° (straight up) -> tilt DMX 131
PAN_350_FORWARD = round(168 / 255, 4)   # 0.6588 — stage-forward
TILT_350_60 = round(94 / 255, 4)        # 0.3686 — 60° above horizon
TILT_350_90 = round(131 / 255, 4)       # 0.5137 — straight up


def build_350w_nod():
    """350W — pan locked stage-forward; tilt nods between 60° and 90°
    above horizon (the beam reaches straight up). Faster in the drops."""
    clips = []
    t = 0.0
    i = 0
    up = True
    while t < DURATION - 0.5:
        sec = next(s for s in SECTIONS if s[0] <= t < s[1])
        base = 9 if sec[2] in ("drop1", "drop2") else 16
        dur = min(base, sec[1] - t)
        if dur < 5:
            dur = sec[1] - t
        ts = TILT_350_60 if up else TILT_350_90
        te = TILT_350_90 if up else TILT_350_60
        col = MOVER_COLOURS[i % len(MOVER_COLOURS)]
        ai = act(f"350W nod {i + 1}", 15,
                 panStart=PAN_350_FORWARD, panEnd=PAN_350_FORWARD,
                 tiltStart=ts, tiltEnd=te,
                 dimmer=255, r=col[0], g=col[1], b=col[2])
        clips.append((ai, round(t, 2), round(dur, 2)))
        t += dur
        i += 1
        up = not up
    return clips


def build_150w(name, pan_a, pan_b, tilt_a, tilt_b, clip_len=18):
    """150W mover — alternating pan + tilt sweeps (no tilt constraint)."""
    clips = []
    t = 0.0
    i = 0
    fwd = True
    while t < DURATION - 0.5:
        sec = next(s for s in SECTIONS if s[0] <= t < s[1])
        base = clip_len - 7 if sec[2] in ("drop1", "drop2") else clip_len
        dur = min(base, sec[1] - t)
        if dur < 5:
            dur = sec[1] - t
        ps, pe = (pan_a, pan_b) if fwd else (pan_b, pan_a)
        ts, te = (tilt_a, tilt_b) if fwd else (tilt_b, tilt_a)
        col = MOVER_COLOURS[i % len(MOVER_COLOURS)]
        ai = act(f"{name} {i + 1}", 15,
                 panStart=ps, panEnd=pe, tiltStart=ts, tiltEnd=te,
                 dimmer=255, r=col[0], g=col[1], b=col[2])
        clips.append((ai, round(t, 2), round(dur, 2)))
        t += dur
        i += 1
        fwd = not fwd
    return clips


# 350W — stage-forward tilt nod, 60° up to vertical (90°), calibrated.
m350_clips = build_350w_nod()
print(f"  350W nod: {len(m350_clips)} clips, tilt 60-90 deg above horizon "
      f"(DMX 94-131), pan locked stage-forward (DMX 168)")

# 150W movers — contrasting pan + tilt sweeps, opposite phase.
m150l_clips = build_150w("150W-L", 0.15, 0.85, 0.22, 0.52, clip_len=18) \
    if f150l else []
m150r_clips = build_150w("150W-R", 0.85, 0.15, 0.58, 0.30, clip_len=20) \
    if f150r else []

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

# ── DMX engine + start + flag as the auto-show ────────────────────────
# The moving heads need the Art-Net engine running; api_timeline_start
# deliberately does not auto-start it.
print("Starting DMX engine...")
api("POST", "/api/dmx/start", {"protocol": "artnet"})
time.sleep(1)

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
