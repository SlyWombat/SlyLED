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
        "energy": 0.2,
        "accent_colors": [[200, 200, 255], [255, 255, 200]],
    },
    "aurora": {
        "name": "Aurora Borealis",
        "desc": "Green curtain with purple shimmer",
        "durationS": 40,
        "palette": [[0, 255, 80], [120, 0, 200], [0, 200, 100], [80, 0, 160]],
        "base_action": {"type": 3, "r": 0, "g": 80, "b": 40, "periodMs": 5000, "minBri": 10},
        "sweep_dir": "left-right",
        "sweep_shape": "plane",
        "sweep_speed": 15,
        "energy": 0.3,
        "accent_colors": [[0, 255, 80], [120, 0, 200]],
    },
    "spotlight-sweep": {
        "name": "Spotlight Sweep",
        "desc": "Warm orb sweeps stage — moving heads track it",
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
        "desc": "Magenta flood + amber spot — moving heads follow",
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
        "desc": "Crossing orbs — moving heads trace paths",
        "durationS": 24,
        "palette": [[0, 220, 255], [255, 200, 50]],
        "base_action": {"type": 1, "r": 5, "g": 10, "b": 20},
        "sweep_dir": "cross",
        "sweep_shape": "sphere",
        "sweep_speed": 6,
        "energy": 0.6,
        "accent_colors": [[0, 220, 255], [255, 200, 50]],
    },
    "thunderstorm": {
        "name": "Thunderstorm",
        "desc": "Lightning on deep blue — heads chase strikes",
        "durationS": 30,
        "palette": [[255, 255, 240], [200, 200, 255], [30, 20, 80]],
        "base_action": {"type": 1, "r": 5, "g": 5, "b": 30},
        "sweep_dir": "down",
        "sweep_shape": "sphere",
        "sweep_speed": 0.3,
        "energy": 0.7,
        "accent_colors": [[255, 255, 240], [200, 200, 255]],
    },
    "dance-floor": {
        "name": "Dance Floor",
        "desc": "Fast orbiting spots + chase pulse — rapid tracking",
        "durationS": 20,
        "palette": [[255, 0, 50], [50, 0, 255], [0, 255, 80]],
        "base_action": {"type": 4, "r": 255, "g": 0, "b": 128, "speedMs": 30, "spacing": 6, "direction": 0},
        "sweep_dir": "cross",
        "sweep_shape": "sphere",
        "sweep_speed": 3,
        "energy": 0.9,
        "accent_colors": [[255, 0, 50], [50, 0, 255], [0, 255, 80]],
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
        else:
            led_fixtures.append(f)

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
        return [j(cx), yMin, j(cz)], [j(cx), yMax, j(cz)]
    elif direction == "down":
        return [j(cx), yMax, j(cz)], [j(cx), yMin, j(cz)]
    elif direction == "cross":
        # Diagonal
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
    sweep_speed = theme["sweep_speed"]
    shape = theme["sweep_shape"]
    direction = theme["sweep_dir"]
    energy = theme["energy"]
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
            # Determine normal from direction
            if d in ("left-right", "right-left"):
                normal = [1, 0, 0]
            elif d in ("up", "down"):
                normal = [0, 1, 0]
            else:
                normal = [1, 0.3, 0]
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
                "name": f"{theme['name']} Tracker",
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

    # Thunderstorm special: add lightning bolts at random fixture positions
    if theme.get("sweep_speed", 1) < 1 and fixture_positions:
        positions = list(fixture_positions.values())
        for i in range(min(4, len(positions))):
            pos = random.choice(positions)
            color = random.choice(accent)
            effects.append({
                "name": f"Lightning {i+1}",
                "category": "spatial-field",
                "shape": "sphere",
                "r": color[0], "g": color[1], "b": color[2],
                "size": {"radius": radius},
                "motion": {
                    "startPos": [pos[0] + random.randint(-500, 500),
                                 bounds["yMax"] + 1000,
                                 pos[2] + random.randint(-500, 500)],
                    "endPos": [pos[0], bounds["yMin"], pos[2]],
                    "durationS": 0.3,
                    "easing": "ease-in",
                },
                "blend": "add",
            })

    return effects


def _generate_mover_actions(theme, dmx_movers, fixture_positions, bounds):
    """Generate pan/tilt sweep actions for moving heads.

    Returns list of action dicts targeting specific movers.
    """
    if not dmx_movers:
        return []

    actions = []
    palette = theme["palette"]
    energy = theme["energy"]

    for i, mover in enumerate(dmx_movers):
        color = palette[i % len(palette)]

        # Pan sweep range based on energy
        pan_range = 0.3 + energy * 0.5
        pan_start = max(0.0, 0.5 - pan_range / 2)
        pan_end = min(1.0, 0.5 + pan_range / 2)

        # Tilt follows energy: high energy = more movement
        tilt_start = max(0.0, 0.4 - energy * 0.2)
        tilt_end = min(1.0, 0.6 + energy * 0.2)

        # Alternate pan direction for adjacent movers
        if i % 2 == 1:
            pan_start, pan_end = pan_end, pan_start

        speed = max(2000, int(8000 / (energy + 0.5)))

        actions.append({
            "action": {
                "name": f"Mover {i+1} Sweep",
                "type": 15,  # ACT_DMX_PT_MOVE
                "r": color[0], "g": color[1], "b": color[2],
                "dimmer": 255,
                "panStart": round(pan_start, 2),
                "panEnd": round(pan_end, 2),
                "tiltStart": round(tilt_start, 2),
                "tiltEnd": round(tilt_end, 2),
                "speedMs": speed,
            },
            "targets": [mover["id"]],
        })

    return actions


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

    # 1. Base wash actions — keep everything lit
    base_actions = _generate_base_actions(theme, led_fx, dmx_pars, dmx_movers)

    # 2. Spatial effects — sweep through fixture positions
    effects = _generate_spatial_effects(theme, bounds, fpos, dmx_movers)

    # 3. Moving head pan/tilt actions
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

    # Track 1: Spatial effects — sequenced so they don't overlap
    effect_clips = []
    if effects:
        # Sequence effects: each gets its own time slot
        # Effects have their own motion durationS; space them across the show
        n = len(effects)
        slot_dur = dur / n if n > 0 else dur
        for i, fx in enumerate(effects):
            fx_motion_dur = fx.get("motion", {}).get("durationS", slot_dur)
            clip_dur = min(slot_dur, max(fx_motion_dur, 1))
            start = round(i * slot_dur, 1)
            effect_clips.append({"_effect_ref": fx, "startS": start, "durationS": round(clip_dur, 1)})
    tracks.append({"allPerformers": True, "clips": effect_clips, "_layer": "effects"})

    # Track 2+: Per-mover pan/tilt sweeps
    for ma in mover_actions:
        fids = ma.get("targets", [])
        for fid in fids:
            tracks.append({
                "fixtureId": fid,
                "clips": [{"_action_ref": ma, "startS": 0, "durationS": dur}],
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
