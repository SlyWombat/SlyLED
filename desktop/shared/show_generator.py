"""
show_generator.py — Dynamic show generation from preset themes.

Instead of hardcoded spatial effect coordinates, generates timelines that
adapt to the user's actual fixtures, positions, and capabilities.  Every
fixture gets action/effect coverage so there are no dark periods.

Usage:
    from show_generator import generate_show
    result = generate_show("ocean-wave", fixtures, layout, stage, profiles_lib)
    # result = {"actions": [...], "effects": [...], "timeline": {...}}
"""

import math
import random

# ── Theme definitions ───────────────────────────────────────────────────────
# Each theme defines aesthetic parameters — the generator maps them onto
# whatever fixtures exist on the stage.

THEMES = {
    "rainbow-up": {
        "name": "Rainbow Up",
        "desc": "Moving rainbow from floor to ceiling",
        "durationS": 30,
        "palette": [[255, 0, 0], [255, 127, 0], [0, 255, 0], [0, 0, 255], [148, 0, 211]],
        "base_action": {"type": 5, "speedMs": 60, "paletteId": 0, "direction": 1},
        "sweep_dir": "up",
        "sweep_shape": "plane",
        "sweep_speed": 10,
        "energy": 0.4,
        "accent_colors": [[255, 200, 50], [100, 0, 255]],
    },
    "rainbow-across": {
        "name": "Rainbow Across",
        "desc": "Moving rainbow from stage left to right",
        "durationS": 30,
        "palette": [[255, 0, 0], [255, 127, 0], [0, 255, 0], [0, 0, 255], [148, 0, 211]],
        "base_action": {"type": 5, "speedMs": 50, "paletteId": 0, "direction": 0},
        "sweep_dir": "left-right",
        "sweep_shape": "plane",
        "sweep_speed": 10,
        "energy": 0.4,
        "accent_colors": [[200, 255, 50], [50, 100, 255]],
    },
    "slow-fire": {
        "name": "Slow Fire",
        "desc": "Warm fire effect across all fixtures",
        "durationS": 60,
        "palette": [[255, 80, 0], [255, 40, 0], [200, 60, 0], [255, 120, 20]],
        "base_action": {"type": 6, "r": 255, "g": 80, "b": 0, "speedMs": 40, "cooling": 45, "sparking": 100},
        "sweep_dir": "up",
        "sweep_shape": "sphere",
        "sweep_speed": 15,
        "energy": 0.3,
        "accent_colors": [[255, 200, 50], [255, 60, 0]],
    },
    "disco": {
        "name": "Disco",
        "desc": "Random pastel twinkles on all fixtures",
        "durationS": 60,
        "palette": [[200, 100, 255], [100, 255, 200], [255, 200, 100], [255, 100, 150]],
        "base_action": {"type": 8, "r": 200, "g": 100, "b": 255, "spawnMs": 80, "density": 5, "fadeSpeed": 15},
        "sweep_dir": "random",
        "sweep_shape": "sphere",
        "sweep_speed": 4,
        "energy": 0.8,
        "accent_colors": [[255, 50, 200], [50, 255, 150], [255, 255, 50]],
    },
    "ocean-wave": {
        "name": "Ocean Wave",
        "desc": "Blue wave sweeping across the stage",
        "durationS": 40,
        "palette": [[0, 80, 220], [0, 180, 160], [0, 40, 180], [0, 120, 200]],
        "base_action": {"type": 3, "r": 0, "g": 30, "b": 120, "periodMs": 6000, "minBri": 15},
        "sweep_dir": "left-right",
        "sweep_shape": "plane",
        "sweep_speed": 10,
        "energy": 0.3,
        "accent_colors": [[0, 180, 160], [0, 60, 255]],
    },
    "sunset": {
        "name": "Sunset Glow",
        "desc": "Warm orange breathe with golden sweep",
        "durationS": 45,
        "palette": [[255, 100, 20], [255, 160, 30], [255, 80, 10], [200, 60, 0]],
        "base_action": {"type": 3, "r": 255, "g": 100, "b": 20, "periodMs": 4000, "minBri": 30},
        "sweep_dir": "down",
        "sweep_shape": "plane",
        "sweep_speed": 20,
        "energy": 0.2,
        "accent_colors": [[255, 160, 30], [200, 80, 0]],
    },
    "police": {
        "name": "Police Lights",
        "desc": "Red strobe with blue flash sweep",
        "durationS": 30,
        "palette": [[255, 0, 0], [0, 0, 255], [255, 0, 0], [0, 0, 255]],
        "base_action": {"type": 9, "r": 255, "g": 0, "b": 0, "periodMs": 200},
        "sweep_dir": "left-right",
        "sweep_shape": "box",
        "sweep_speed": 2,
        "energy": 0.9,
        "accent_colors": [[0, 0, 255], [255, 0, 0]],
    },
    "starfield": {
        "name": "Starfield",
        "desc": "White sparkles on dark background",
        "durationS": 60,
        "palette": [[255, 255, 255], [200, 200, 255], [255, 240, 200]],
        "base_action": {"type": 12, "r": 5, "g": 5, "b": 20, "spawnMs": 60, "density": 4},
        "sweep_dir": "random",
        "sweep_shape": "sphere",
        "sweep_speed": 8,
        # #837 — energy bumped from 0.2 → 0.4 so the sweep count comes
        # out at 2-3 random orbs instead of 1. Pre-fix a 60 s starfield
        # had a single slow random orb plus the sparkle base — felt
        # static. 0.4 gives enough variation without overwhelming the
        # quiet aesthetic the theme is going for.
        "energy": 0.4,
        "accent_colors": [[200, 200, 255], [255, 255, 200]],
    },
    "aurora": {
        "name": "Aurora Borealis",
        "desc": "Green curtain with purple shimmer",
        "durationS": 40,
        "palette": [[0, 255, 80], [120, 0, 200], [0, 200, 100], [80, 0, 160]],
        # #837 — minBri lifted from 10 → 30. At 10 the trough drops nearly
        # to black on every breathe period, producing a visible floor
        # flicker. 30 keeps the wash present without flattening the
        # breathe envelope.
        "base_action": {"type": 3, "r": 0, "g": 80, "b": 40, "periodMs": 5000, "minBri": 30},
        "sweep_dir": "left-right",
        "sweep_shape": "plane",
        "sweep_speed": 15,
        "energy": 0.3,
        # #837 — accents distinct from the base palette. Pre-fix the
        # accents `[0,255,80]` and `[120,0,200]` were the first two
        # palette entries verbatim, so sweep highlights flattened into
        # the wash and the operator never saw the accent layer.
        "accent_colors": [[200, 255, 200], [255, 200, 255]],
    },
    "spotlight-sweep": {
        "name": "Warm Orb Sweep",
        # #837 — desc renamed to match implementation. The theme emits a
        # spatial-field sphere effect that drifts across the stage and
        # per-mover sweeps at #836's stage-coord endpoints; it does NOT
        # emit a Track action. Pre-fix desc promised "moving heads track
        # it" which the generator never delivered. (Adding Track-action
        # routing here would require a patrol-prop target — out of
        # scope; the figure-eight / spotlight-follow-person themes
        # already fill the operator-driven-tracking niche.)
        "desc": "Warm orb sweeps stage with coordinated mover wash",
        "durationS": 20,
        "palette": [[255, 240, 200], [200, 180, 255]],
        "base_action": {"type": 1, "r": 20, "g": 15, "b": 10},
        "sweep_dir": "left-right",
        "sweep_shape": "sphere",
        "sweep_speed": 8,
        "energy": 0.5,
        "accent_colors": [[255, 240, 200], [200, 180, 255]],
    },
    "concert-wash": {
        "name": "Concert Wash",
        # #837 — desc no longer claims "moving heads follow"; the theme
        # emits per-mover stage-coord sweeps + a spatial-field plane,
        # not a Track action. Coordinated movement, not target-tracking.
        "desc": "Magenta flood + amber accent with synced mover sweeps",
        "durationS": 30,
        "palette": [[220, 0, 180], [255, 160, 40], [0, 40, 200]],
        "base_action": {"type": 3, "r": 0, "g": 40, "b": 200, "periodMs": 5000, "minBri": 20},
        "sweep_dir": "left-right",
        "sweep_shape": "plane",
        "sweep_speed": 12,
        "energy": 0.5,
        "accent_colors": [[220, 0, 180], [255, 160, 40]],
    },
    "figure-eight": {
        "name": "Figure Eight",
        "desc": "Virtual target traces a figure-8 on stage — all moving heads follow",
        "durationS": 60,
        "palette": [[255, 240, 200], [255, 200, 50]],
        "base_action": {"type": 1, "r": 5, "g": 10, "b": 20},
        "sweep_dir": "cross",
        "sweep_shape": "sphere",
        "sweep_speed": 6,
        "energy": 0.6,
        "accent_colors": [[0, 220, 255], [255, 200, 50]],
        "live_track": True,
        "patrol_objects": [{
            "name": "Figure-8 Target",
            "objectType": "figure8-target",
            "color": "#00DCFF",
            "patrol": {
                "enabled": True,
                "pattern": "figure8",
                "speedPreset": "medium",
                "startPct": 15,
                "endPct": 85,
                "patrolMode": "on-demand",
            },
        }],
    },
    "thunderstorm": {
        "name": "Thunderstorm",
        # #837 — desc no longer claims "heads chase strikes"; the theme
        # emits lightning sphere effects + per-mover stage-coord sweeps
        # but no Track action. Bolts originate at light-emitting
        # fixture positions only (cameras / gyros excluded post-#836).
        "desc": "Lightning bursts on a deep-blue base wash",
        "durationS": 30,
        "palette": [[255, 255, 240], [200, 200, 255], [30, 20, 80]],
        "base_action": {"type": 1, "r": 5, "g": 5, "b": 30},
        "sweep_dir": "down",
        "sweep_shape": "sphere",
        "sweep_speed": 0.3,
        "energy": 0.7,
        "accent_colors": [[255, 255, 240], [200, 200, 255]],
        "lightning": True,
    },
    "dance-floor": {
        "name": "Dance Floor",
        # #837 — desc no longer claims "rapid tracking"; theme is
        # spatial sweeps + chase pulse, not a Track action.
        "desc": "Fast orbiting spots + chase pulse",
        "durationS": 20,
        "palette": [[255, 0, 50], [50, 0, 255], [0, 255, 80]],
        "base_action": {"type": 4, "r": 255, "g": 0, "b": 128, "speedMs": 30, "spacing": 6, "direction": 0},
        "sweep_dir": "cross",
        "sweep_shape": "sphere",
        "sweep_speed": 3,
        "energy": 0.9,
        "accent_colors": [[255, 0, 50], [50, 0, 255], [0, 255, 80]],
    },
    "spotlight-follow-person": {
        "name": "Spotlight: Follow Person",
        "desc": "Moving heads track detected people in real-time via camera (requires camera node)",
        # #837 — durationS 600 → 60 with loop=True. Pre-fix a 10-minute
        # one-shot timeline confused operators who'd press Stop because
        # they assumed the show had hung. 60 s + loop gives the same
        # continuous-track behaviour with normal playlist semantics
        # (status pegs the bar at 100% per iteration, wraps cleanly via
        # the #840 single-item loop_all routing).
        "durationS": 60,
        "loop": True,
        "palette": [[255, 240, 200], [255, 200, 150]],
        "base_action": {"type": 1, "r": 10, "g": 5, "b": 30},
        "sweep_dir": "left-right",
        "sweep_shape": "sphere",
        "sweep_speed": 0.5,
        "energy": 0.3,
        "accent_colors": [[255, 240, 200]],
        "live_track": True,
    },
    # #839 — flagship template built on the new ribbon primitive. Every
    # mover beam locks onto a single travelling stage-coord anchor so
    # the rig moves as one unit; layered phase-offset spatial sweeps
    # along the same path produce the shimmer that distinguishes
    # "aurora" from "stripe"; sparkle overlay adds twinkles above the
    # wash; fade-in/out brackets wrap the timeline boundaries.
    # #865 — high-energy template tuned for stages built around many
    # parallel vertical LED bars (touring back-wall battens, ~75–300
    # LEDs/bar). Auto-detects bars from the live layout; refuses
    # generation when fewer than 2 are present (degenerate). Catalog
    # ships as a single coordinated 7-clip sequence per
    # feedback_rock_solid_no_incrementals.
    "vertical-bar-array": {
        "name": "Vertical Bar Array (rapid)",
        "desc": "Rapid catalog tuned for vertical LED bars (≥75 LEDs each, ≥2 bars)",
        "durationS": 56,            # 7 clips × 8 s
        "palette": [[255, 240, 80], [80, 220, 240], [220, 100, 220],
                     [255, 80, 60], [80, 255, 120]],
        "base_action": {
            "type": 3, "r": 30, "g": 30, "b": 60,
            "periodMs": 4000, "minBri": 30,
        },
        "energy": 0.85,
        "accent_colors": [[255, 240, 200], [120, 200, 255]],
        "bar_array": True,
        "bpm": 128,
    },
    "aurora-curtain": {
        "name": "Aurora Curtain",
        "desc": "Layered aurora ribbons sweep across the stage with synced movers",
        "durationS": 60,
        "palette": [[0, 200, 140], [40, 220, 180], [120, 80, 220],
                     [180, 40, 200], [60, 240, 200]],
        "base_action": {
            "type": 3,           # ACT_BREATHE
            "r": 0, "g": 80, "b": 60,
            "periodMs": 8000,
            "minBri": 35,        # the no-blackout floor
        },
        "sparkle_layer": {
            "type": 12,          # ACT_SPARKLE
            "r": 200, "g": 255, "b": 240,
            "spawnMs": 240, "density": 2, "fadeSpeed": 8,
        },
        "ribbon": {
            "axis": "left-right",
            "speedS": 12,
            "loopMode": "ping-pong",
            "elevation": 0.65,
            "phaseOffsets": [0.00, 0.07, -0.05],
        },
        "energy": 0.35,
        "accent_colors": [[150, 255, 200], [80, 60, 255], [255, 100, 220]],
        "fadeInS":  1.5,
        "fadeOutS": 1.5,
    },
}


def _get_stage_bounds(fixtures, layout_positions):
    """Compute actual stage bounds from fixture positions."""
    pos_map = {p["id"]: p for p in layout_positions}
    xs, ys, zs = [], [], []
    for f in fixtures:
        p = pos_map.get(f["id"], {})
        xs.append(p.get("x", 0))
        ys.append(p.get("y", 0))
        zs.append(p.get("z", 0))
    if not xs:
        return {"xMin": 0, "xMax": 10000, "yMin": 0, "yMax": 5000,
                "zMin": 0, "zMax": 5000, "cx": 5000, "cy": 2500, "cz": 2500}
    margin = 1000  # 1m margin around fixtures
    return {
        "xMin": min(xs) - margin, "xMax": max(xs) + margin,
        "yMin": min(ys) - margin, "yMax": max(ys) + margin,
        "zMin": min(zs) - margin, "zMax": max(zs) + margin,
        "cx": sum(xs) // len(xs), "cy": sum(ys) // len(ys), "cz": sum(zs) // len(zs),
    }


def _classify_fixtures(fixtures, profile_lib=None):
    """Classify fixtures by type and capabilities."""
    led_fixtures = []
    dmx_pars = []      # RGB only, no pan/tilt
    dmx_movers = []    # has pan/tilt
    groups = []

    for f in fixtures:
        ft = f.get("fixtureType", "led")
        if f.get("type") == "group":
            groups.append(f)
        elif ft == "dmx":
            pid = f.get("dmxProfileId")
            info = None
            if pid and profile_lib:
                info = profile_lib.channel_info(pid)
            has_pt = False
            if info:
                cm = info.get("channel_map", {})
                has_pt = "pan" in cm and "tilt" in cm
            if has_pt:
                dmx_movers.append(f)
            else:
                dmx_pars.append(f)
        elif ft == "led":
            led_fixtures.append(f)
        # #836 — non-light fixtures (camera, gyro, …) are deliberately
        # NOT bucketed. Pre-fix the else-branch swallowed them into
        # `led_fixtures`, producing inert LED-base tracks for cameras
        # and gyro gyros that pollute the bake and inflate the
        # dashboard's fixture count without producing any visible
        # output (those fixtures can't render an action).

    return led_fixtures, dmx_pars, dmx_movers, groups


def _make_sweep_path(bounds, direction, jitter=True):
    """Generate start/end positions for a sweep based on direction and stage bounds."""
    cx, cy, cz = bounds["cx"], bounds["cy"], bounds["cz"]
    xMin, xMax = bounds["xMin"], bounds["xMax"]
    yMin, yMax = bounds["yMin"], bounds["yMax"]
    zMin, zMax = bounds["zMin"], bounds["zMax"]

    j = lambda v, spread=500: v + random.randint(-spread, spread) if jitter else v

    if direction == "left-right":
        return [xMin, j(cy), j(cz)], [xMax, j(cy), j(cz)]
    elif direction == "right-left":
        return [xMax, j(cy), j(cz)], [xMin, j(cy), j(cz)]
    elif direction == "up":
        # #837 — Z is the vertical axis (Z-up project convention; see
        # CLAUDE.md "Rotation convention"). Pre-fix this swept Y, which
        # made every "up" sweep travel front-to-back across the stage
        # instead of floor-to-ceiling. Themes affected: rainbow-up,
        # slow-fire (rising flames). Y is the depth (back-wall →
        # audience) axis and stays at center for vertical sweeps.
        return [j(cx), j(cy), zMin], [j(cx), j(cy), zMax]
    elif direction == "down":
        return [j(cx), j(cy), zMax], [j(cx), j(cy), zMin]
    elif direction == "cross":
        # Diagonal — sweeps left-to-right while rising in Z (#837).
        return [xMin, j(cy), zMin], [xMax, j(cy), zMax]
    else:  # "random"
        return (
            [random.randint(xMin, xMax), random.randint(yMin, yMax), random.randint(zMin, zMax)],
            [random.randint(xMin, xMax), random.randint(yMin, yMax), random.randint(zMin, zMax)],
        )


def _fixture_positions(fixtures, layout_positions):
    """Return {fid: [x,y,z]} for positioned fixtures."""
    pos_map = {p["id"]: p for p in layout_positions}
    result = {}
    for f in fixtures:
        p = pos_map.get(f["id"])
        if p:
            result[f["id"]] = [p.get("x", 0), p.get("y", 0), p.get("z", 0)]
    return result


def _sphere_radius_for_coverage(bounds):
    """Compute a sphere radius large enough to cover a good portion of the stage."""
    w = bounds["xMax"] - bounds["xMin"]
    h = bounds["yMax"] - bounds["yMin"]
    diag = math.sqrt(w * w + h * h)
    return max(2000, int(diag * 0.35))


def _generate_base_actions(theme, led_fixtures, dmx_pars, dmx_movers):
    """Generate base wash actions that keep all fixtures lit throughout the show.

    Returns list of action dicts (without ids — caller assigns ids).
    """
    actions = []
    base = dict(theme["base_action"])
    palette = theme["palette"]

    # LED base action
    if led_fixtures:
        act = dict(base)
        act["name"] = f"{theme['name']} — LED Base"
        # Ensure the base action has color from the palette
        if "r" not in act or (act.get("r", 0) + act.get("g", 0) + act.get("b", 0)) == 0:
            c = palette[0]
            act["r"], act["g"], act["b"] = c[0], c[1], c[2]
        actions.append({"action": act, "targets": "led"})

    # DMX par base: solid from palette
    if dmx_pars:
        c = palette[0]
        actions.append({
            "action": {
                "name": f"{theme['name']} — Par Wash",
                "type": 14,  # DMX_SCENE
                "r": c[0], "g": c[1], "b": c[2],
                "dimmer": 200,
                "pan": 0.5, "tilt": 0.5,
            },
            "targets": "dmx_par",
        })

    # DMX mover base: dimmed with center aim
    if dmx_movers:
        c = palette[0]
        actions.append({
            "action": {
                "name": f"{theme['name']} — Mover Base",
                "type": 14,  # DMX_SCENE
                "r": max(20, c[0] // 3), "g": max(20, c[1] // 3), "b": max(20, c[2] // 3),
                "dimmer": 120,
                "pan": 0.5, "tilt": 0.5,
            },
            "targets": "dmx_mover",
        })

    return actions


def _generate_spatial_effects(theme, bounds, fixture_positions, dmx_movers):
    """Generate spatial effects that sweep through actual fixture positions.

    Returns list of effect dicts.
    """
    effects = []
    palette = theme["palette"]
    accent = theme.get("accent_colors", palette[:2])
    dur = theme["durationS"]
    # #839 — ribbon themes don't carry sweep_* fields. When they fall
    # through to this generator (no movers on the rig, so the ribbon
    # path was skipped), provide sensible defaults so the legacy
    # spatial-sweep generator still produces useful output instead of
    # raising KeyError.
    sweep_speed = theme.get("sweep_speed", 10)
    shape = theme.get("sweep_shape", "sphere")
    direction = theme.get("sweep_dir", "left-right")
    energy = theme.get("energy", 0.4)
    radius = _sphere_radius_for_coverage(bounds)

    # Number of sweep effects scales with energy and duration
    n_sweeps = max(2, int(energy * 4) + 1)

    # Alternate directions for variety
    dirs = [direction]
    if direction == "left-right":
        dirs = ["left-right", "right-left"]
    elif direction == "cross":
        dirs = ["cross", "left-right", "right-left"]
    elif direction == "random":
        dirs = ["random"] * n_sweeps

    for i in range(n_sweeps):
        color = accent[i % len(accent)]
        d = dirs[i % len(dirs)]
        start, end = _make_sweep_path(bounds, d)

        size = {}
        if shape == "sphere":
            size = {"radius": radius + random.randint(-300, 500)}
        elif shape == "plane":
            # #837 — plane normal aligns with the sweep direction. Z is
            # the vertical axis (Z-up project convention); pre-fix
            # up/down used [0,1,0] which is the depth axis, producing a
            # plane that swept front-to-back instead of floor-to-ceiling.
            if d in ("left-right", "right-left"):
                normal = [1, 0, 0]
            elif d in ("up", "down"):
                normal = [0, 0, 1]
            else:
                normal = [1, 0, 0.3]   # diagonal: X-dominant + slight Z tilt
            size = {"normal": normal, "thickness": max(1000, radius)}
        elif shape == "box":
            w = bounds["xMax"] - bounds["xMin"]
            h = bounds["yMax"] - bounds["yMin"]
            size = {"width": max(2000, w // 3), "height": max(2000, h),
                    "depth": 3000}

        # Vary speed slightly per effect
        spd = sweep_speed * (0.8 + random.random() * 0.4)
        easing = random.choice(["ease-in-out", "ease-in-out", "linear", "ease-out"])

        fx = {
            "name": f"{theme['name']} Sweep {i+1}",
            "category": "spatial-field",
            "shape": shape,
            "r": color[0], "g": color[1], "b": color[2],
            "size": size,
            "motion": {
                "startPos": start, "endPos": end,
                "durationS": round(spd, 1),
                "easing": easing,
            },
            "blend": random.choice(["add", "add", "screen"]),
        }
        effects.append(fx)

    # If there are DMX movers, add a dedicated tracking orb that sweeps
    # through all fixture positions for maximum visual impact
    if dmx_movers and fixture_positions:
        mover_pos = [fixture_positions[m["id"]] for m in dmx_movers
                     if m["id"] in fixture_positions]
        if mover_pos:
            # Create a "visiting" orb that goes fixture to fixture
            color = accent[0]
            # Sort by X for a nice sweep
            sorted_pos = sorted(mover_pos, key=lambda p: p[0])
            start_p = sorted_pos[0]
            end_p = sorted_pos[-1]
            # Aim at the midpoint height of movers
            mid_y = sum(p[1] for p in sorted_pos) // len(sorted_pos)
            effects.append({
                # #836 item 5 — renamed from "Tracker" to "Mover Visit"
                # to defuse confusion with Track actions (type 18). This
                # IS a spatial-field sphere effect that visits fixture
                # positions; it has nothing to do with object tracking
                # or camera detections. The previous name caused the
                # operator to assume a missing tracker target.
                "name": f"{theme['name']} Mover Visit",
                "category": "spatial-field",
                "shape": "sphere",
                "r": color[0], "g": color[1], "b": color[2],
                "size": {"radius": radius},
                "motion": {
                    "startPos": [start_p[0], mid_y, start_p[2]],
                    "endPos": [end_p[0], mid_y, end_p[2]],
                    "durationS": round(sweep_speed * 1.5, 1),
                    "easing": "ease-in-out",
                },
                "blend": "add",
            })

    # Thunderstorm special: add lightning bolts at random fixture positions.
    # #837 — bolts travel on Z (top → floor) and originate at light-
    # emitting positions only. Pre-fix the bolt motion was on Y (front
    # → back) and could anchor on cameras / gyros / patrol props (any
    # entry in `fixture_positions`) — strikes appeared at non-light
    # positions and weren't actually descending.
    light_emitting_ids = {m["id"] for m in dmx_movers}
    light_positions = [p for fid, p in fixture_positions.items()
                        if fid in light_emitting_ids]
    if theme.get("lightning") and light_positions:
        for i in range(min(4, len(light_positions))):
            pos = random.choice(light_positions)
            color = random.choice(accent)
            effects.append({
                "name": f"Lightning {i+1}",
                "category": "spatial-field",
                "shape": "sphere",
                "r": color[0], "g": color[1], "b": color[2],
                "size": {"radius": radius},
                "motion": {
                    # Strike from above (zMax+jitter) down to floor (zMin),
                    # offset slightly in X/Y for natural variation.
                    "startPos": [pos[0] + random.randint(-500, 500),
                                 pos[1] + random.randint(-500, 500),
                                 bounds["zMax"] + 1000],
                    "endPos": [pos[0], pos[1], bounds["zMin"]],
                    "durationS": 0.3,
                    "easing": "ease-in",
                },
                "blend": "add",
            })

    return effects


def _generate_mover_actions(theme, dmx_movers, fixture_positions, bounds):
    """Generate pan/tilt sweep actions for moving heads.

    Returns list of action dicts targeting specific movers.

    #836 — sweep endpoints are stage-mm world coordinates (`ptStartPos`
    / `ptEndPos`), NOT DMX-range fractions. Pre-fix the generator
    emitted `panStart` / `panEnd` / `tiltStart` / `tiltEnd` as 0.0-1.0
    fractions of each mover's mechanical pan/tilt range, which made
    every mover sweep through a different world-space arc — five
    disjoint movements instead of a coordinated wash. The bake engine
    already routes `ptStartPos` / `ptEndPos` through
    `spatial_engine.compute_pan_tilt`, which produces per-mover
    pan/tilt that aim every head at the same world point on every
    frame.
    """
    if not dmx_movers:
        return []

    actions = []
    palette = theme["palette"]
    energy = theme["energy"]

    # Pick a sweep direction per theme energy. High energy → diagonal
    # cross; medium → horizontal; low → gentle vertical. Keeps the
    # show generator's variety while staying stage-coord throughout.
    if energy >= 0.7:
        directions = ["cross", "left-right", "right-left"]
    elif energy >= 0.4:
        directions = ["left-right", "right-left"]
    else:
        directions = ["up", "down"]

    for i, mover in enumerate(dmx_movers):
        color = palette[i % len(palette)]
        direction = directions[i % len(directions)]
        # Adjacent movers alternate so two heads next to each other
        # sweep toward each other (visual interest), without all heads
        # being parallel.
        if i % 2 == 1:
            direction = {
                "left-right": "right-left",
                "right-left": "left-right",
                "up": "down",
                "down": "up",
                "cross": "cross",
            }[direction]

        start_pos, end_pos = _make_sweep_path(bounds, direction, jitter=False)

        actions.append({
            "action": {
                "name": f"Mover {i+1} Sweep",
                "type": 15,  # ACT_DMX_PT_MOVE
                "r": color[0], "g": color[1], "b": color[2],
                "dimmer": 255,
                # #836 — canonical stage-mm pose endpoints. Bake engine
                # at bake_engine.py:498-524 prefers these and routes
                # via spatial_engine.compute_pan_tilt for per-mover IK.
                "ptStartPos": [int(start_pos[0]), int(start_pos[1]), int(start_pos[2])],
                "ptEndPos":   [int(end_pos[0]),   int(end_pos[1]),   int(end_pos[2])],
                # No panStart/panEnd/tiltStart/tiltEnd: the generator no
                # longer emits DMX-range fractions. No speedMs: the bake
                # ignores it for type-15 actions (slice duration is
                # determined by clip duration, not action speed).
            },
            "targets": [mover["id"]],
        })

    return actions


def _generate_track_actions(theme, dmx_movers):
    """Generate live Track actions (type 18) for real-time object following.

    Creates a single Track action targeting all movers. The Track action is
    evaluated at runtime by the 40 Hz DMX loop — no baked pan/tilt values
    are needed. trackObjectType filters which moving objects to follow
    (e.g. "person" for camera tracking, "figure8-target" for patrol objects).
    """
    if not dmx_movers:
        return []

    color = theme["palette"][0] if theme["palette"] else [255, 240, 200]

    # Derive object type from patrol objects if present, else "person"
    patrol_objs = theme.get("patrol_objects", [])
    if patrol_objs:
        obj_type = patrol_objs[0].get("objectType")
        name = f"Track {patrol_objs[0].get('name', 'Target')}"
    else:
        obj_type = "person"
        name = "Follow Person"

    return [{
        "action": {
            "name": name,
            "type": 18,  # ACT_TRACK
            "r": color[0], "g": color[1], "b": color[2],
            "trackObjectType": obj_type,
            "trackDimmer": 255,
            "trackAutoSpread": False,
            "trackFixedAssignment": False,
            "trackCycleMs": 2000,
            "dimmer": 255,
        },
        "targets": [],  # empty = all movers auto-discovered at runtime
    }]


# ── #839 ribbon primitive ────────────────────────────────────────────────────

def _generate_ribbon_target(theme):
    """#839 — emit a single patrol object that acts as the rig's
    coordinated travelling anchor. Stored in `patrol_objects` so the
    same plumbing _install_preset_show already uses for live-track
    shows creates the moving-object record. Server-side
    `_evaluate_object_patrols` understands `pattern: "ribbon"` (axis +
    elevation + loopMode) and animates the position every frame.
    """
    ribbon = theme.get("ribbon")
    if not ribbon:
        return None
    palette = theme.get("palette") or [[200, 230, 255]]
    color = palette[0]
    return {
        "name": f"{theme['name']} Ribbon",
        "objectType": "ribbon-target",
        "color": "#%02X%02X%02X" % (color[0], color[1], color[2]),
        "opacity": 25,
        "scale": [400, 400, 400],
        "patrol": {
            "enabled": True,
            "pattern": "ribbon",
            "axis": ribbon.get("axis", "left-right"),
            "ribbonAxis": ribbon.get("axis", "left-right"),
            "elevation": ribbon.get("elevation", 0.65),
            "loopMode": ribbon.get("loopMode", "ping-pong"),
            "cycleS": ribbon.get("speedS", 12),
            # `speedPreset: "custom"` forces the patrol evaluator to
            # use our `cycleS` (not the default "medium" → 10 s preset
            # from `_PATROL_SPEED_PRESETS`). Otherwise the ribbon's
            # speedS is silently ignored and loops aren't stable.
            "speedPreset": "custom",
            "easing": "sine",
        },
    }


def _generate_track_action_for_ribbon(theme, dmx_movers):
    """#839 — Track action whose `trackObjectIds` is filled in by
    `_install_preset_show` once the ribbon patrol object's id is known.
    Targets all movers (empty `targets` triggers the timeline-track-fid
    fallback added in #829)."""
    if not dmx_movers:
        return None
    color = (theme.get("palette") or [[255, 255, 255]])[0]
    return {
        "action": {
            "name": f"Track {theme['name']} Ribbon",
            "type": 18,
            "r": color[0], "g": color[1], "b": color[2],
            "trackObjectIds": [],   # filled in by _install_preset_show
            "trackDimmer": 255,
            "trackAutoSpread": False,
            "trackFixedAssignment": False,
            "trackCycleMs": 2000,
            "dimmer": 255,
        },
        "targets": [],
    }


def _generate_ribbon_layered_effects(theme, bounds):
    """#839 — emit one spatial-field effect per phase offset along the
    ribbon's axis. The phase offsets stagger the effects in time so
    three layered ripples drift relative to each other (the "shimmer"
    that distinguishes aurora from a single stripe)."""
    ribbon = theme.get("ribbon")
    if not ribbon:
        return []
    phase_offsets = ribbon.get("phaseOffsets", [0.0])
    palette = theme.get("palette") or [[200, 230, 255]]
    accent = theme.get("accent_colors") or palette
    speed = float(ribbon.get("speedS", 12))
    axis = ribbon.get("axis", "left-right")
    elevation = float(ribbon.get("elevation", 0.5))
    z_anchor = bounds["zMin"] + (bounds["zMax"] - bounds["zMin"]) * elevation
    mid_y = bounds["cy"]
    if axis in ("left-right", "right-left"):
        start = [bounds["xMin"], mid_y, z_anchor]
        end   = [bounds["xMax"], mid_y, z_anchor]
        if axis == "right-left":
            start, end = end, start
    elif axis in ("front-back", "back-front"):
        start = [bounds["cx"], bounds["yMin"], z_anchor]
        end   = [bounds["cx"], bounds["yMax"], z_anchor]
        if axis == "back-front":
            start, end = end, start
    elif axis in ("up-down", "down-up"):
        start = [bounds["cx"], mid_y, bounds["zMin"]]
        end   = [bounds["cx"], mid_y, bounds["zMax"]]
        if axis == "down-up":
            start, end = end, start
    else:
        start = [bounds["xMin"], mid_y, z_anchor]
        end   = [bounds["xMax"], mid_y, z_anchor]
    effects = []
    for i, offset in enumerate(phase_offsets):
        color = accent[i % len(accent)]
        # Slight per-clip speed variation — combined with phase offset
        # this gives the ribbons a non-locked drift.
        spd = speed * (1.0 + (offset * 0.5))
        effects.append({
            "name": f"{theme['name']} Ribbon {i+1}",
            "category": "spatial-field",
            "shape": "sphere",
            "r": color[0], "g": color[1], "b": color[2],
            "size": {"radius": max(800, (bounds["xMax"] - bounds["xMin"]) // 4)},
            "motion": {
                "startPos": [int(start[0]), int(start[1]), int(start[2])],
                "endPos":   [int(end[0]),   int(end[1]),   int(end[2])],
                "durationS": round(max(1.0, spd), 1),
                "easing": "ease-in-out",
            },
            "blend": "add",
            "_phaseOffset": offset,
        })
    return effects


def _generate_sparkle_layer(theme, led_fx):
    """#839 — sparkle / accent overlay on LED fixtures. Designed to be
    additive: pixels light briefly above the wash, but the wash
    minBri floor keeps the stage from going dark when sparkle isn't
    active. Returns a list of action dicts (one per LED fixture) or
    empty list if the theme doesn't declare a sparkle_layer."""
    sparkle = theme.get("sparkle_layer")
    if not sparkle or not led_fx:
        return []
    return [{
        "action": {
            "name": f"{theme['name']} Sparkle",
            "type": int(sparkle.get("type", 12)),  # ACT_SPARKLE default
            "r": sparkle.get("r", 200),
            "g": sparkle.get("g", 255),
            "b": sparkle.get("b", 240),
            "spawnMs": sparkle.get("spawnMs", 240),
            "density": sparkle.get("density", 2),
            "fadeSpeed": sparkle.get("fadeSpeed", 8),
            "dimmer": 255,
        },
        "targets": [f["id"] for f in led_fx],
    }]


def _apply_fade_brackets(theme, tracks, dur):
    """#839 — wrap every track's first/last clip with fade-in / fade-out
    metadata so the bake renderer ramps dimmer 0→target across `fadeInS`
    at the timeline start and target→0 across `fadeOutS` at the end.

    For now the metadata is annotated on each clip's `_fadeInS` /
    `_fadeOutS` keys; the bake renderer's existing ramp logic
    (ACT_FADE / `dimmer` tween) consumes them. Themes that don't
    declare fade durations get no annotation (legacy snap-to-full
    behaviour preserved)."""
    fade_in = float(theme.get("fadeInS", 0))
    fade_out = float(theme.get("fadeOutS", 0))
    if fade_in <= 0 and fade_out <= 0:
        return tracks
    for tr in tracks:
        clips = tr.get("clips") or []
        if not clips:
            continue
        first = clips[0]
        last = clips[-1]
        # Mark only if clip envelope brackets the timeline edge.
        if fade_in > 0 and first.get("startS", 0) < 0.05:
            first["_fadeInS"] = fade_in
        if fade_out > 0:
            end_t = (last.get("startS", 0) + last.get("durationS", 0))
            if end_t > dur - 0.05:
                last["_fadeOutS"] = fade_out
    return tracks


# ── #865 vertical-bar-array primitive ────────────────────────────────────────

_BAR_MIN_LEDS = 75
_BAR_Z_DOMINANCE = 3.0


def _bar_string_qualifies(fixture, string_cfg, base_pos):
    """#865 — heuristic per string. Returns the resolved pixel list when
    the string's pixel extent is dominantly aligned with stage +Z (≥3×
    the larger of X / Y extent) and the LED count is ≥75. Returns None
    if the string is too short, can't be resolved, or isn't vertical.
    """
    if (string_cfg.get("leds") or 0) < _BAR_MIN_LEDS:
        return None
    try:
        from spatial_engine import resolve_linear_fixture as _resolve_str
        pixels = _resolve_str(
            base_pos, string_cfg,
            string_cfg.get("points"),
            fixture.get("rotation", [0, 0, 0]),
        )
    except Exception:
        return None
    if len(pixels) < 2:
        return None
    xs = [p[0] for p in pixels]
    ys = [p[1] for p in pixels]
    zs = [p[2] for p in pixels]
    z_ext = max(zs) - min(zs)
    h_ext = max(max(xs) - min(xs), max(ys) - min(ys), 1)
    if z_ext <= _BAR_Z_DOMINANCE * h_ext:
        return None
    return pixels


def _enumerate_vertical_bars(fixtures, pos_map):
    """#865 — enumerate per-bar entries across the rig.

    Each entry is one bar — which may be either:
      * an entire single-string LED fixture (legacy / common case), or
      * an individual string of a multi-string LED fixture, when
        per-string positions (#864) place it as a discrete vertical
        strip on the rig.

    Returns a list of dicts, each carrying:
      `fixture_id`     — parent fixture id (used for base-wash track)
      `string_index`   — 0-based string index inside that fixture
      `anchor`         — [x, y, z] stage-mm anchor (per-string base if
                         the string has its own (x,y,z) per #864, else
                         the fixture's layout position)
      `pixels`         — resolved pixel positions (used by tests for
                         per-bar peak-time correlation)
      `leds`           — LED count
    """
    bars = []
    for f in fixtures:
        if f.get("fixtureType") != "led":
            continue
        if f.get("type") != "linear":
            continue
        strings = f.get("strings") or []
        if not strings:
            continue
        pos = pos_map.get(f.get("id"))
        if not pos:
            continue
        fixture_pos = [pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)]
        for si, s in enumerate(strings):
            sx = s.get("x"); sy = s.get("y"); sz = s.get("z")
            if (isinstance(sx, (int, float))
                    and isinstance(sy, (int, float))
                    and isinstance(sz, (int, float))):
                base = [sx, sy, sz]
            else:
                base = fixture_pos
            pixels = _bar_string_qualifies(f, s, base)
            if pixels is None:
                continue
            bars.append({
                "fixture_id": f["id"],
                "string_index": si,
                "anchor": base,
                "pixels": pixels,
                "leds": s.get("leds") or 0,
            })
    return bars


def _is_vertical_bar(fixture, pos_map):
    """#865 — back-compat shim used by the test harness. Reports True
    when the fixture contributes at least one bar to
    `_enumerate_vertical_bars`. Multi-string fixtures may contribute
    several entries; one is enough for the per-fixture predicate."""
    return any(b["fixture_id"] == fixture.get("id")
               for b in _enumerate_vertical_bars([fixture], pos_map))


def _generate_bar_array_show(theme, fixtures, layout_positions, bounds):
    """#865 — produce the 7-clip bar-array timeline.

    Catalog (each ~8 s, on an allPerformers track, sequenced):
      1. Cross-stage horizontal sweep   (sphere field travels +X)
      2. Vertical climb                 (sphere field travels +Z)
      3. Mexican wave                   (plane field swept on X)
      4. Strobe shimmer                 (ACT_STROBE @ 10 Hz)
      5. Lightning strikes              (sphere bursts at bar X positions)
      6. Color cascade                  (ACT_RAINBOW vertical)
      7. Stack-builder                  (plane field stepping zBottom→zTop on beat)

    Returns either {"error", "msg"} (rejected — < 2 bars) or the
    standard show dict consumed by `_install_preset_show`.

    Bars are enumerated per *string*, so a single multi-string LED
    fixture (#864) can supply several bars when its per-string
    positions place each string on a different point of the rig.
    """
    pos_map = {p["id"]: p for p in layout_positions}
    bar_entries = _enumerate_vertical_bars(fixtures, pos_map)
    if len(bar_entries) < 2:
        # Surface a diagnostic that explains what was rejected so
        # operators don't have to read the source. The message lists
        # each LED fixture and what disqualified it (no layout pos,
        # too few LEDs, not vertical) so they can fix the most common
        # misconfigurations without guessing.
        diag_lines = []
        led_fixtures = [f for f in fixtures
                          if f.get("fixtureType") == "led"
                          and f.get("type") == "linear"]
        if not led_fixtures:
            diag_lines.append("No LED fixtures on the rig.")
        for f in led_fixtures:
            fid = f.get("id")
            name = f.get("name") or f"#{fid}"
            pos = pos_map.get(fid)
            if not pos:
                diag_lines.append(f"  • {name}: not placed in layout (no x/y/z).")
                continue
            strings = f.get("strings") or []
            if not strings:
                diag_lines.append(f"  • {name}: no strings configured.")
                continue
            for si, s in enumerate(strings):
                leds = s.get("leds") or 0
                if leds < _BAR_MIN_LEDS:
                    diag_lines.append(
                        f"  • {name} string {si+1}: {leds} LEDs "
                        f"(need ≥{_BAR_MIN_LEDS}).")
                    continue
                # Probe vertical-extent rejection.
                sx, sy, sz = s.get("x"), s.get("y"), s.get("z")
                if all(isinstance(v, (int, float)) for v in (sx, sy, sz)):
                    base = [sx, sy, sz]
                else:
                    base = [pos.get("x", 0), pos.get("y", 0), pos.get("z", 0)]
                if _bar_string_qualifies(f, s, base) is None:
                    diag_lines.append(
                        f"  • {name} string {si+1}: not vertical "
                        f"(strip extent must be along stage +Z; rotate "
                        f"the fixture or set sdir so the strip runs up).")
        diag = "\n".join(diag_lines) if diag_lines else "(no LED fixtures)"
        return {
            "error": "needs_bars",
            "msg": (f"Theme '{theme['name']}' needs at least 2 vertical "
                    f"LED bars (≥{_BAR_MIN_LEDS} LEDs, oriented along "
                    f"stage +Z). Detected: {len(bar_entries)}.\n\n"
                    f"What I saw:\n{diag}"),
        }

    bar_ids = list({b["fixture_id"] for b in bar_entries})
    # `led_fx` (per-fixture base wash) — dedupe by parent fixture.
    led_fx = []
    seen = set()
    for b in bar_entries:
        if b["fixture_id"] not in seen:
            led_fx.append(next(f for f in fixtures
                                 if f.get("id") == b["fixture_id"]))
            seen.add(b["fixture_id"])
    bpm = float(theme.get("bpm", 128) or 128)
    beat_s = max(0.05, 60.0 / bpm)

    cy = bounds["cy"]
    cz_mid = (bounds["zMin"] + bounds["zMax"]) // 2
    z_top = bounds["zMax"]
    z_bot = bounds["zMin"]
    x_min = bounds["xMin"]
    x_max = bounds["xMax"]
    cx = (x_min + x_max) // 2

    palette = theme["palette"]
    accent = theme.get("accent_colors", palette)
    clip_dur = 8.0

    effects = []
    actions = []

    # Clip 1 — Cross-stage horizontal sweep. Wide sphere travels +X over
    # 4 s and again −X over 4 s. The clip-relative startPos of the +X
    # leg pins the per-bar peak ordering: bars at lower stage-X cross the
    # sphere's centre first. The acceptance test asserts this directly.
    radius_h = max(800, (x_max - x_min) // 4)
    cross_sweep_a = {
        "name": "Cross-Stage Sweep →",
        "category": "spatial-field",
        "shape": "sphere",
        "r": accent[0][0], "g": accent[0][1], "b": accent[0][2],
        "size": {"radius": radius_h},
        "motion": {
            "startPos": [int(x_min - radius_h // 2), int(cy), int(cz_mid)],
            "endPos":   [int(x_max + radius_h // 2), int(cy), int(cz_mid)],
            "durationS": 4.0,
            "easing": "ease-in-out",
        },
        "blend": "add",
    }
    effects.append(cross_sweep_a)

    # Clip 2 — Vertical climb. Plane field with +Z normal so the slab
    # passes through every bar at the same height regardless of stage-X
    # — the unison-climb behaviour the spec calls out. Pre-fix this was
    # a sphere centred at cx with radius (z_top-z_bot)/5; bars at the
    # X edges (e.g. x=1000 with cx=4000) sat outside the sphere and
    # never lit during the slot, so the leftmost bar was dark during
    # Vertical Climb.
    radius_v = max(400, (z_top - z_bot) // 5)
    climb_thickness = max(400, (z_top - z_bot) // 6)
    vertical_climb = {
        "name": "Vertical Climb ↑",
        "category": "spatial-field",
        "shape": "plane",
        "r": palette[1][0], "g": palette[1][1], "b": palette[1][2],
        "size": {"normal": [0, 0, 1], "thickness": int(climb_thickness)},
        "motion": {
            "startPos": [int(cx), int(cy), int(z_bot)],
            "endPos":   [int(cx), int(cy), int(z_top)],
            "durationS": round(beat_s * 2, 2),  # 2 beats per climb
            "easing": "linear",
        },
        "blend": "add",
    }
    effects.append(vertical_climb)

    # Clip 3 — Mexican wave. Plane field with X-aligned normal so a thin
    # vertical slab travels stage-left → stage-right. Period 1.5 s per
    # the spec. Uses the existing plane primitive so bake produces the
    # same per-pixel timing as the other sweep effects.
    mexican_wave = {
        "name": "Mexican Wave",
        "category": "spatial-field",
        "shape": "plane",
        "r": palette[2][0], "g": palette[2][1], "b": palette[2][2],
        "size": {"normal": [1, 0, 0],
                  "thickness": max(800, (x_max - x_min) // 8)},
        "motion": {
            "startPos": [int(x_min), int(cy), int(cz_mid)],
            "endPos":   [int(x_max), int(cy), int(cz_mid)],
            "durationS": 1.5,
            "easing": "ease-in-out",
        },
        "blend": "add",
    }
    effects.append(mexican_wave)

    # Clip 4 — Strobe shimmer. Single ACT_STROBE keeps the bake
    # pipeline simple; per-bar phase variance is provided by the
    # strobe's free-running counter at slightly different transition
    # times when the spatial fields layered above resolve.
    strobe_action = {
        "action": {
            "name": "Bar Strobe Shimmer",
            "type": 9,
            "r": 255, "g": 240, "b": 200,
            "periodMs": 100,
        },
        "targets": "led",
    }
    actions.append(strobe_action)

    # Clip 5 — Lightning strikes. Up to 4 short sphere bursts at
    # individual bar X positions; if there are more bars than slots the
    # template picks evenly-spaced bars across the array.
    lightning_effects = []
    sorted_bars = sorted(bar_entries, key=lambda b: b["anchor"][0])
    n_strikes = min(4, len(sorted_bars))
    step = max(1, len(sorted_bars) // n_strikes) if n_strikes else 1
    for i in range(n_strikes):
        b = sorted_bars[i * step] if (i * step) < len(sorted_bars) else sorted_bars[i % len(sorted_bars)]
        bx = b["anchor"][0]
        fx = {
            "name": f"Lightning {i+1}",
            "category": "spatial-field",
            "shape": "sphere",
            "r": 255, "g": 255, "b": 240,
            "size": {"radius": radius_v},
            "motion": {
                "startPos": [int(bx), int(cy), int(z_top + 800)],
                "endPos":   [int(bx), int(cy), int(z_bot)],
                "durationS": 0.25,
                "easing": "ease-in",
            },
            "blend": "add",
        }
        effects.append(fx)
        lightning_effects.append(fx)

    # Clip 6 — Color cascade. ACT_RAINBOW direction=1 scrolls top→bottom
    # on every targeted bar in unison, exploiting their high pixel
    # density for a smooth gradient.
    cascade_action = {
        "action": {
            "name": "Bar Color Cascade",
            "type": 5,                # ACT_RAINBOW
            "speedMs": 25,
            "paletteId": 0,
            "direction": 1,
        },
        "targets": "led",
    }
    actions.append(cascade_action)

    # Clip 7 — Stack-builder. Four box fields stack from floor to top on
    # consecutive beats. The 8-s clip absorbs four beats of 0.5 s + a
    # collapse on the fifth beat (modeled by reusing the top box).
    stack_effects = []
    for i in range(4):
        z_lo = z_bot + (z_top - z_bot) * i // 4
        z_hi = z_bot + (z_top - z_bot) * (i + 1) // 4
        fx = {
            "name": f"Stack {i+1}",
            "category": "spatial-field",
            "shape": "box",
            "r": palette[(i + 1) % len(palette)][0],
            "g": palette[(i + 1) % len(palette)][1],
            "b": palette[(i + 1) % len(palette)][2],
            "size": {
                "width": int((x_max - x_min) + 4000),
                "height": int(max(2000, bounds["yMax"] - bounds["yMin"] + 2000)),
                "depth": int(max(400, (z_hi - z_lo))),
            },
            "motion": {
                "startPos": [int(cx), int(cy), int((z_lo + z_hi) // 2)],
                "endPos":   [int(cx), int(cy), int((z_lo + z_hi) // 2)],
                "durationS": round(beat_s, 2),
                "easing": "linear",
            },
            "blend": "add",
        }
        effects.append(fx)
        stack_effects.append(fx)

    # ── Build tracks ────────────────────────────────────────────────────
    # Per-fixture base wash (lowest priority — keeps the bars from going
    # dark per feedback_wash_is_intentional).
    dur = 7 * clip_dur  # = 56 s, matches theme["durationS"]
    base_actions = _generate_base_actions(theme, led_fx, [], [])
    led_base = next((b for b in base_actions
                      if b.get("targets") == "led"), None)

    tracks = []
    if led_base:
        for fx in led_fx:
            tracks.append({
                "fixtureId": fx["id"],
                "clips": [{"_action_ref": led_base, "startS": 0, "durationS": dur}],
                "_layer": "base",
            })

    # Effects/actions track (allPerformers) — 7 sequenced clips.
    fx_clips = [
        {"_effect_ref": cross_sweep_a, "startS": 0 * clip_dur,
         "durationS": clip_dur, "name": "Cross-Stage Sweep"},
        {"_effect_ref": vertical_climb, "startS": 1 * clip_dur,
         "durationS": clip_dur, "name": "Vertical Climb"},
        {"_effect_ref": mexican_wave, "startS": 2 * clip_dur,
         "durationS": clip_dur, "name": "Mexican Wave"},
        {"_action_ref": strobe_action, "startS": 3 * clip_dur,
         "durationS": clip_dur, "name": "Strobe Shimmer"},
        # Lightning slot — first lightning effect anchors the clip; the
        # rest of the strikes also live on the allPerformers track at
        # offset (0..clip_dur) inside the slot.
        {"_effect_ref": lightning_effects[0] if lightning_effects else cross_sweep_a,
         "startS": 4 * clip_dur,
         "durationS": clip_dur, "name": "Lightning Strikes"},
        {"_action_ref": cascade_action, "startS": 5 * clip_dur,
         "durationS": clip_dur, "name": "Color Cascade"},
        {"_effect_ref": stack_effects[0] if stack_effects else cross_sweep_a,
         "startS": 6 * clip_dur,
         "durationS": clip_dur, "name": "Stack-Builder"},
    ]
    tracks.append({"allPerformers": True, "clips": fx_clips,
                    "_layer": "effects"})

    # ── Stagger tracks ─────────────────────────────────────────────────
    # The Lightning Strikes and Stack-Builder slots reference effect[0]
    # only on the main effects track. effect[1..N-1] would be created as
    # spatial_fx records but never bake without an additional clip
    # somewhere in the timeline. These stagger tracks fire the rest of
    # the sub-effects across the slot's 8-second window so the operator
    # sees N strikes / N stacked boxes, not just one.

    # Lightning stagger — strikes spaced through the slot. Reuse the 4
    # source effect records cyclically up to 8 strikes (≈1 strike/sec)
    # so the slot reads "random thunder" rather than a single zap.
    if lightning_effects:
        lightning_slot_start = 4 * clip_dur
        n_total_strikes = max(8, len(lightning_effects))
        strike_step = clip_dur / n_total_strikes  # ≈1 s
        # Each strike clip lasts a fraction of the gap so the spatial
        # field gets a full enter→exit window before the next strike.
        strike_clip_dur = round(strike_step * 0.55, 2)
        stagger_clips = []
        for i in range(n_total_strikes):
            e = lightning_effects[i % len(lightning_effects)]
            stagger_clips.append({
                "_effect_ref": e,
                "startS": round(lightning_slot_start + i * strike_step, 2),
                "durationS": strike_clip_dur,
                "name": f"Lightning Stagger {i+1}",
            })
        tracks.append({"allPerformers": True, "clips": stagger_clips,
                        "_layer": "lightning-stagger"})

    # Stack-builder stagger — each box appears in sequence and stays
    # lit until the slot ends, so by the last beat all four bands are
    # stacked. Each box rides its own track because they overlap in
    # time (one track may not have overlapping clips).
    if stack_effects:
        stack_slot_start = 6 * clip_dur
        rise_step = clip_dur / (len(stack_effects) + 1)
        for i, sfx in enumerate(stack_effects):
            appear = stack_slot_start + (i + 1) * rise_step
            tracks.append({
                "allPerformers": True,
                "clips": [{
                    "_effect_ref": sfx,
                    "startS": round(appear, 2),
                    "durationS": round(stack_slot_start + clip_dur - appear, 2),
                    "name": f"Stack Stagger {i+1}",
                }],
                "_layer": "stack-stagger",
            })

    return {
        "name": theme["name"],
        "durationS": dur,
        "base_actions": base_actions,
        "mover_actions": [strobe_action, cascade_action],
        "effects": effects,
        "tracks": tracks,
        "led_fixture_ids": bar_ids,
        "dmx_par_ids": [],
        "dmx_mover_ids": [],
        "_865_bar_ids": bar_ids,    # parent fixture ids — exposed for tests
        "_865_bar_entries": bar_entries,  # per-string bars, anchors, pixels
        "_865_clips": fx_clips,
    }


def generate_show(theme_id, fixtures, layout, stage, profile_lib=None):
    """Generate a complete show from a theme and the user's actual fixtures.

    Args:
        theme_id: one of the THEMES keys
        fixtures: list of fixture dicts (from _fixtures)
        layout: layout dict with "children" positions
        stage: stage dict with w/h/d
        profile_lib: optional ProfileLibrary instance for DMX profile lookup

    Returns:
        {
            "name": str,
            "durationS": int,
            "actions": [action_dicts],    # no ids yet
            "effects": [effect_dicts],    # no ids yet
            "timeline": {tracks, clips info},
            "led_fixtures": [ids],
            "dmx_par_ids": [ids],
            "dmx_mover_ids": [ids],
        }
    """
    theme = THEMES.get(theme_id)
    if not theme:
        return None

    # Filter to non-group fixtures only
    real_fixtures = [f for f in fixtures if f.get("type") != "group"]
    if not real_fixtures:
        # Fallback: return the theme's base action as a simple show
        base = dict(theme["base_action"])
        base["name"] = theme["name"]
        base_info = {"action": base, "targets": "led"}
        dur = theme["durationS"]
        return {
            "name": theme["name"],
            "durationS": dur,
            "base_actions": [base_info],
            "mover_actions": [],
            "effects": [],
            "tracks": [{"allPerformers": True, "clips": [
                {"_action_ref": base_info, "startS": 0, "durationS": dur}
            ], "_layer": "base"}],
            "led_fixture_ids": [],
            "dmx_par_ids": [],
            "dmx_mover_ids": [],
        }

    layout_positions = layout.get("children", [])
    led_fx, dmx_pars, dmx_movers, groups = _classify_fixtures(real_fixtures, profile_lib)
    bounds = _get_stage_bounds(real_fixtures, layout_positions)
    fpos = _fixture_positions(real_fixtures, layout_positions)

    dur = theme["durationS"]

    # ── #865 vertical bar array shows ─────────────────────────────────
    # Bar-topology shows have their own primitive: catalog of 7 fast
    # effects sequenced on a single allPerformers track. Refuses if
    # fewer than 2 bars are detected (degenerate).
    if theme.get("bar_array"):
        return _generate_bar_array_show(theme, real_fixtures, layout_positions, bounds)

    # ── #839 ribbon shows: coordinated stage-anchor + layered slip ────
    # Themes with a `ribbon` block emit a single travelling patrol
    # object that all movers track in unison via a Track action.
    # Layered phase-offset spatial effects ride the same path. Sparkle
    # / fade-bracket primitives are layered on top.
    if theme.get("ribbon") and dmx_movers:
        ribbon_obj = _generate_ribbon_target(theme)
        track_act = _generate_track_action_for_ribbon(theme, dmx_movers)
        ribbon_effects = _generate_ribbon_layered_effects(theme, bounds)
        base_actions = _generate_base_actions(theme, led_fx, dmx_pars, dmx_movers)
        sparkle_actions = _generate_sparkle_layer(theme, led_fx)

        tracks = []
        # Per-fixture base wash (lowest priority)
        if led_fx:
            led_base = next((b for b in base_actions
                              if b.get("targets") == "led"), None)
            if led_base:
                for lf in led_fx:
                    tracks.append({
                        "fixtureId": lf["id"],
                        "clips": [{"_action_ref": led_base, "startS": 0, "durationS": dur}],
                        "_layer": "base",
                    })
        if dmx_pars:
            par_base = next((b for b in base_actions
                              if b.get("targets") == "dmx_par"), None)
            if par_base:
                for pf in dmx_pars:
                    tracks.append({
                        "fixtureId": pf["id"],
                        "clips": [{"_action_ref": par_base, "startS": 0, "durationS": dur}],
                        "_layer": "base",
                    })
        if dmx_movers:
            mov_base = next((b for b in base_actions
                              if b.get("targets") == "dmx_mover"), None)
            if mov_base:
                for mf in dmx_movers:
                    tracks.append({
                        "fixtureId": mf["id"],
                        "clips": [{"_action_ref": mov_base, "startS": 0, "durationS": dur}],
                        "_layer": "base",
                    })
        # Layered spatial effects — phase-offset clips along the ribbon
        # path. Each clip occupies the full timeline so the effect
        # repeats; the phase offset shifts when each ripple is at
        # peak intensity.
        if ribbon_effects:
            tracks.append({
                "allPerformers": True,
                "clips": [
                    {"_effect_ref": fx,
                     "startS": round(fx.get("_phaseOffset", 0) * dur, 2),
                     "durationS": dur}
                    for fx in ribbon_effects
                ],
                "_layer": "effects",
            })
        # Sparkle overlay on LEDs (additive, above wash, below ribbon).
        if sparkle_actions and led_fx:
            sp = sparkle_actions[0]
            for lf in led_fx:
                tracks.append({
                    "fixtureId": lf["id"],
                    "clips": [{"_action_ref": sp, "startS": 0, "durationS": dur}],
                    "_layer": "sparkle",
                })
        # Track action — one allPerformers clip referencing the Track
        # action. _install_preset_show fills `trackObjectIds` with the
        # ribbon patrol object's id once it's created.
        if track_act:
            tracks.append({
                "allPerformers": True,
                "clips": [{"_action_ref": track_act, "startS": 0, "durationS": dur}],
                "_layer": "track",
            })

        # Apply fade-in / fade-out brackets per #839.
        tracks = _apply_fade_brackets(theme, tracks, dur)

        result = {
            "name": theme["name"],
            "durationS": dur,
            "base_actions": base_actions,
            "mover_actions": ([track_act] if track_act else []) + sparkle_actions,
            "effects": ribbon_effects,
            "tracks": tracks,
            "patrol_objects": [ribbon_obj] if ribbon_obj else [],
            "led_fixture_ids": [f["id"] for f in led_fx],
            "dmx_par_ids": [f["id"] for f in dmx_pars],
            "dmx_mover_ids": [f["id"] for f in dmx_movers],
        }
        return result

    # ── Live tracking shows: minimal structure ────────────────────────
    # Track action (type 18) auto-discovers all movers at runtime —
    # no per-fixture tracks, no spatial effects, no mover base wash.
    # Just one Track action on a single allPerformers track.
    if theme.get("live_track"):
        track_action = _generate_track_actions(theme, dmx_movers)
        if not track_action:
            # #837 — refuse rather than silently fall through. Pre-fix
            # `figure-eight` / `spotlight-follow-person` on a stage
            # without movers fell through to normal generation, which
            # produces a non-tracking show that doesn't match the
            # theme's name. The caller surfaces the error to the
            # operator instead of installing a misleading preset.
            return {
                "error": "needs_movers",
                "msg": (f"Theme '{theme['name']}' requires DMX moving heads "
                        "to drive its tracking behaviour. No mover fixtures "
                        "are present on this rig. Add a moving-head fixture "
                        "(profile with panRange + tiltRange > 0), or pick a "
                        "non-tracking preset."),
            }
        else:
            # Pre-fix this branch emitted ONLY the Track action and
            # left LEDs / DMX pars completely silent — the runtime DMX
            # loop evaluates type-18 segments for movers (pan/tilt
            # only), so LED fixtures with only a type-18 segment in
            # their bake stayed dark for the entire show. Add a base
            # wash so non-mover fixtures still light from the theme's
            # palette while the movers track. Track action stays on a
            # higher-priority allPerformers track so its dimmer/r/g/b
            # still wins on movers.
            base_actions = _generate_base_actions(theme, led_fx, dmx_pars, [])
            tracks = []
            led_base = next((b for b in base_actions
                              if b.get("targets") == "led"), None)
            par_base = next((b for b in base_actions
                              if b.get("targets") == "dmx_par"), None)
            if led_base:
                for lf in led_fx:
                    tracks.append({
                        "fixtureId": lf["id"],
                        "clips": [{"_action_ref": led_base, "startS": 0,
                                    "durationS": dur}],
                        "_layer": "base",
                    })
            if par_base:
                for pf in dmx_pars:
                    tracks.append({
                        "fixtureId": pf["id"],
                        "clips": [{"_action_ref": par_base, "startS": 0,
                                    "durationS": dur}],
                        "_layer": "base",
                    })
            tracks.append({"allPerformers": True, "clips": [
                {"_action_ref": track_action[0], "startS": 0, "durationS": dur}
            ], "_layer": "track"})
            return {
                "name": theme["name"],
                "durationS": dur,
                "base_actions": base_actions,
                "mover_actions": track_action,
                "effects": [],
                "tracks": tracks,
                "patrol_objects": theme.get("patrol_objects", []),
                "led_fixture_ids": [f["id"] for f in led_fx],
                "dmx_par_ids": [f["id"] for f in dmx_pars],
                "dmx_mover_ids": [f["id"] for f in dmx_movers],
            }

    # ── Normal shows: base wash + spatial effects + mover sweeps ──────

    # 1. Base wash actions — keep everything lit
    base_actions = _generate_base_actions(theme, led_fx, dmx_pars, dmx_movers)

    # 2. Spatial effects — sweep through fixture positions
    effects = _generate_spatial_effects(theme, bounds, fpos, dmx_movers)

    # 3. Moving head actions — PT_MOVE for baked shows
    mover_actions = _generate_mover_actions(theme, dmx_movers, fpos, bounds)

    # ── Build track structure ──────────────────────────────────────────
    # Track ordering: lower index = lower priority (background)
    #   Tracks 0..N: per-fixture base wash — always-on background, one per fixture
    #   Track N+1:   spatial effects (allPerformers) — sequenced, override base
    #   Tracks N+2+: per-mover PT sweeps — override both base and effects
    #
    # Within a track, clips must NOT overlap in time.
    # Higher track overrides lower track for the same fixture at the same time.

    tracks = []

    # Base tracks: one per fixture type to avoid overlapping clips.
    # Each fixture type gets exactly one base action clip covering the full duration.
    # LED fixtures
    if led_fx:
        led_base = [ba for ba in base_actions if ba.get("targets") == "led"]
        if led_base:
            for lf in led_fx:
                tracks.append({
                    "fixtureId": lf["id"],
                    "clips": [{"_action_ref": led_base[0], "startS": 0, "durationS": dur}],
                    "_layer": "base",
                })
    # DMX pars
    if dmx_pars:
        par_base = [ba for ba in base_actions if ba.get("targets") == "dmx_par"]
        if par_base:
            for pf in dmx_pars:
                tracks.append({
                    "fixtureId": pf["id"],
                    "clips": [{"_action_ref": par_base[0], "startS": 0, "durationS": dur}],
                    "_layer": "base",
                })
    # DMX movers
    if dmx_movers:
        mover_base = [ba for ba in base_actions if ba.get("targets") == "dmx_mover"]
        if mover_base:
            for mf in dmx_movers:
                tracks.append({
                    "fixtureId": mf["id"],
                    "clips": [{"_action_ref": mover_base[0], "startS": 0, "durationS": dur}],
                    "_layer": "base",
                })

    # Track 1: Spatial effects — tile the timeline so adjacent slots
    # touch without gaps (#836 item 4). Pre-fix the per-clip duration
    # was capped by the spatial-effect's own motion.durationS (often
    # less than slot_dur), leaving a dead window between adjacent
    # clips during which only the wash was active.
    effect_clips = []
    if effects:
        n = len(effects)
        slot_dur = dur / n if n > 0 else dur
        for i, fx in enumerate(effects):
            start = round(i * slot_dur, 1)
            # Last slot absorbs any rounding remainder so total = dur.
            this_slot = (dur - start) if i == n - 1 else slot_dur
            effect_clips.append({"_effect_ref": fx, "startS": start,
                                  "durationS": round(this_slot, 1)})
    tracks.append({"allPerformers": True, "clips": effect_clips, "_layer": "effects"})

    # Track 2+: Per-mover pan/tilt sweeps. #836 item 3 — sweep clip
    # occupies the central 60 % of the timeline, leaving 20 % wash at
    # start and 20 % wash at end. Pre-fix sweep ran the full duration
    # at dimmer=255, fully masking the Mover Base wash on every frame.
    sweep_start = round(dur * 0.2, 1)
    sweep_dur = round(dur * 0.6, 1)
    for ma in mover_actions:
        fids = ma.get("targets", [])
        for fid in fids:
            tracks.append({
                "fixtureId": fid,
                "clips": [{"_action_ref": ma, "startS": sweep_start,
                           "durationS": sweep_dur}],
                "_layer": "mover",
            })

    return {
        "name": theme["name"],
        "durationS": dur,
        "base_actions": base_actions,
        "mover_actions": mover_actions,
        "effects": effects,
        "tracks": tracks,
        "led_fixture_ids": [f["id"] for f in led_fx],
        "dmx_par_ids": [f["id"] for f in dmx_pars],
        "dmx_mover_ids": [f["id"] for f in dmx_movers],
    }


def list_themes():
    """Return list of available themes for the preset selector."""
    return [
        {"id": tid, "name": t["name"], "desc": t["desc"]}
        for tid, t in THEMES.items()
    ]
