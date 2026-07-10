"""orch_project — settings IO, actions library, config/show/project IO (B1).

Mechanically extracted from parent_server.py: the /api/settings GET+POST
routes, the "Actions library", "Config export-import" (incl. show
presets/export/import), and "Project file (complete save/load)" sections.
Route paths, names, and behaviour are byte-identical; only the decorator
target changed (@app → @bp) and parent_server-owned state/helpers are
reached through the orch_state bridge (ps.*) so rebinds (project import
replaces ps._fixtures, ps._layout, …) land on the module attributes tests
and the rest of parent_server read.

The brightness/intensity/gamma/local-audio machinery that shares the
"Settings" banner stays in parent_server — it is hot-path code wired into
the DMX engines and the UDP listener, and tests getsource/patch it there.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

import orch_state

ps = orch_state.ps  # the live parent_server module (bound before this import)
assert ps is not None, "orch_state.bind() must run before importing orch_project"

bp = Blueprint("project", __name__)

@bp.get("/api/settings")
def api_settings_get():
    s = dict(ps._settings)
    # Compute elapsed dynamically from start epoch
    if s.get("runnerRunning") and s.get("runnerStartEpoch"):
        s["runnerElapsed"] = max(0, int(time.time()) - s["runnerStartEpoch"])
    # #680 — surface the calibration-tuning spec (defaults + clamps +
    # tooltips) alongside the current overrides so the UI can render a
    # single Advanced panel without a second round-trip.
    s["calibrationTuning"] = dict(ps._settings.get("calibrationTuning") or {})
    s["calibrationTuningSpec"] = ps.CAL_TUNING_SPEC
    return jsonify(s)

@bp.post("/api/settings")
def api_settings_save():
    body = request.get_json(silent=True) or {}
    # #680 — validate calibrationTuning overrides BEFORE committing any of
    # the simple settings fields. An OOR value rejects the whole write.
    if "calibrationTuning" in body:
        cleaned, errors = ps._validate_cal_tuning(body["calibrationTuning"])
        if errors:
            return jsonify(err="calibrationTuning validation failed",
                            details=errors), 400
    # #843 — capture the pre-write brightness so we know whether to
    # broadcast after the save commits. Only emit a UDP frame on change.
    bri_changed = False
    bri_new = None
    with ps._lock:
        bri_prev = ps._settings.get("globalBrightness", 255)
        for k in ("name", "units", "canvasW", "canvasH", "darkMode", "runnerLoop",
                  "globalBrightness", "logging", "logPath", "autoStartShow",
                  # #685 follow-up — operator-selected vision model for AI
                  # auto-tune. None / empty string falls back to the env
                  # default. Validated minimally: must be a non-empty
                  # string when present, else cleared.
                  "aiAutoTuneModel"):
            if k in body:
                v = body[k]
                if k == "aiAutoTuneModel":
                    if v is None or (isinstance(v, str) and not v.strip()):
                        ps._settings.pop(k, None)
                        continue
                    if not isinstance(v, str):
                        return jsonify(err="aiAutoTuneModel must be a string"), 400
                    v = v.strip()
                ps._settings[k] = v
        if "calibrationTuning" in body:
            ps._settings["calibrationTuning"] = cleaned
        ps._layout["canvasW"] = ps._settings["canvasW"]
        ps._layout["canvasH"] = ps._settings["canvasH"]
        ps._save("settings", ps._settings)
        # Sync stage dimensions (meters) from canvas (mm)
        ps._stage["w"] = ps._settings["canvasW"] / 1000.0
        ps._stage["h"] = ps._settings["canvasH"] / 1000.0
        ps._save("stage", ps._stage)
        bri_new = ps._settings.get("globalBrightness", 255)
        bri_changed = (bri_new != bri_prev)
    # Toggle file logging if changed
    if "logging" in body:
        ps._apply_logging(body["logging"], body.get("logPath"))
    # #843 — manual-slider brightness change must reach the lights too.
    if bri_changed and bri_new is not None:
        ps._broadcast_brightness(bri_new)
    return jsonify(ok=True)

#  "  "  Actions library  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

@bp.get("/api/actions")
def api_actions():
    return jsonify(ps._actions)

_ACTION_FIELDS = ("name", "type", "scope", "canvasEffect", "targetIds", "r", "g", "b",
                  "r2", "g2", "b2",           # Fade second colour
                  "speedMs", "periodMs", "spawnMs",  # timing
                  "minBri", "spacing", "paletteId",  # Breathe/Chase/Rainbow
                  "cooling", "sparking",              # Fire
                  "direction", "tailLen", "density",  # Chase/Comet/Twinkle
                  "decay", "fadeSpeed",               # Comet/Twinkle
                  "onMs", "offMs", "wipeDir", "wipeSpeedPct",  # legacy compat
                  "wledFxOverride", "wledPalOverride", "wledSegId",  # WLED overrides
                  "trackObjectIds", "trackCycleMs", "trackOffset",  # Track action
                  "trackFixtureIds", "trackFixtureOffsets", "trackAutoSpread", "trackFixedAssignment", "trackDimmer",
                  "dimmer", "pan", "tilt", "strobe", "gobo", "colorWheel", "prism",  # DMX channels
                  "ptStartPos", "ptEndPos",  # Pan/Tilt Move: stage coordinate positions [x,y,z] mm
                  # #688 — bake_engine still honours panStart/panEnd/
                  # tiltStart/tiltEnd as a legacy-DMX-normalised fall-
                  # back when ptStartPos/ptEndPos aren't supplied (line
                  # 555-559 in bake_engine.py). Pre-fix the whitelist
                  # didn't include them, so /api/actions silently
                  # stripped these fields and the test action's
                  # tiltStart=0.3 / tiltEnd=0.7 dropped to defaults
                  # (0.5 / 0.5).
                  "panStart", "panEnd", "tiltStart", "tiltEnd")

@bp.post("/api/actions")
def api_actions_create():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    with ps._lock:
        a = {"id": ps._nxt_a}
        for k in _ACTION_FIELDS:
            if k in body:
                a[k] = body[k]
        a.setdefault("name", name)
        a.setdefault("type", 1)
        # #841 — colorWheel is type-17-only. Drop it for any other type
        # so old SPA payloads / API clients can't reintroduce stale
        # wheel-slot values that defeat rgb_to_wheel_slot at render time.
        if a.get("type") != 17:
            a.pop("colorWheel", None)
        ps._actions.append(a)
        ps._nxt_a += 1
        ps._save("actions", ps._actions)
    return jsonify(ok=True, id=a["id"])

@bp.get("/api/actions/<int:aid>")
def api_action_get(aid):
    a = next((x for x in ps._actions if x["id"] == aid), None)
    if not a:
        return jsonify(ok=False, err="not found"), 404
    return jsonify(a)

@bp.put("/api/actions/<int:aid>")
def api_action_put(aid):
    a = next((x for x in ps._actions if x["id"] == aid), None)
    if not a:
        return jsonify(ok=False, err="not found"), 404
    body = request.get_json(silent=True) or {}
    with ps._lock:
        for k in _ACTION_FIELDS:
            if k in body:
                a[k] = body[k]
        # #841 — colorWheel is type-17-only. Strip after merge in case
        # the type changed or the SPA sent a 0 alongside other fields.
        if a.get("type") != 17:
            a.pop("colorWheel", None)
        ps._save("actions", ps._actions)
    return jsonify(ok=True)

@bp.delete("/api/actions/<int:aid>")
def api_action_delete(aid):
    with ps._lock:
        n = len(ps._actions)
        ps._actions = [x for x in ps._actions if x["id"] != aid]
        if len(ps._actions) == n:
            return jsonify(ok=False, err="not found"), 404
        ps._save("actions", ps._actions)
    return jsonify(ok=True)

#  "  "  Config export-import  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

CONFIG_SCHEMA_VERSION = 3  # bump when export format changes incompatibly
CONFIG_MIN_IMPORT_VERSION = 1  # oldest version we can still import

@bp.get("/api/config/export")
def api_config_export():
    """Bundle children + fixtures + layout as a portable config file.

    Schema v3: strip internal-only fields (aimPoint, orientation, _placed,
    _beamWidth, status, moverCalibrated, calibrated, _temporal, _ttl).
    """
    # Strip internal/transient fields from children
    _CHILD_STRIP = {"status", "_temporal", "_ttl"}
    clean_children = []
    for c in ps._children:
        cc = {k: v for k, v in c.items() if k not in _CHILD_STRIP}
        clean_children.append(cc)

    # Strip internal/transient fields from fixtures
    _FIX_STRIP = {"aimPoint", "orientation", "_placed", "_beamWidth",
                  "moverCalibrated", "calibrated", "rangeCalibrated",
                  "_temporal", "_ttl", "positioned"}
    clean_fixtures = []
    for f in ps._fixtures:
        cf = {k: v for k, v in f.items() if k not in _FIX_STRIP}
        clean_fixtures.append(cf)

    return jsonify({
        "type": "slyled-config",
        "schemaVersion": CONFIG_SCHEMA_VERSION,
        "version": CONFIG_SCHEMA_VERSION,  # backward compat
        "children": clean_children,
        "fixtures": clean_fixtures,
        "layout": ps._layout,
    })

@bp.post("/api/config/import")
def api_config_import():
    """Merge children by hostname, auto-create fixtures, remap layout IDs."""
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-config":
        return jsonify(ok=False, err="Not a SlyLED config file (missing type field)"), 400
    # Schema version check — accept v1-v3, reject future incompatible versions
    sv = data.get("schemaVersion") or data.get("version") or 1
    if sv > CONFIG_SCHEMA_VERSION:
        return jsonify(ok=False, err=f"Config file is version {sv}, but this app only supports up to version {CONFIG_SCHEMA_VERSION}. Please update SlyLED."), 400
    if sv < CONFIG_MIN_IMPORT_VERSION:
        return jsonify(ok=False, err=f"Config file is version {sv}, which is too old. Minimum supported version is {CONFIG_MIN_IMPORT_VERSION}."), 400
    imported_children = data.get("children", [])
    imported_layout = data.get("layout")
    added = updated = fixtures_created = 0
    child_id_map = {}  # old_child_id -> new_child_id
    fixture_id_map = {}  # old_layout_id -> new_fixture_id
    with ps._lock:
        # Import children
        for c in imported_children:
            old_id = c.get("id", -1)
            ex = next((x for x in ps._children
                        if x.get("hostname") == c.get("hostname")), None)
            if ex:
                child_id_map[old_id] = ex["id"]
                ex.update({k: v for k, v in c.items() if k != "id"})
                updated += 1
            else:
                c = dict(c)
                c["id"] = ps._nxt_c
                child_id_map[old_id] = ps._nxt_c
                ps._nxt_c += 1
                ps._children.append(c)
                added += 1
        ps._save("children", ps._children)

        # Auto-create fixtures for children that don't already have one
        for c in ps._children:
            cid = c["id"]
            # Skip if fixture already exists for this child
            if any(f.get("childId") == cid for f in ps._fixtures):
                continue
            # DMX bridges never get auto-fixtures (they ARE the transport, not a light)
            if c.get("type") == "dmx" or c.get("boardType") in ("giga-dmx", "DMX Bridge"):
                continue
            # Create LED fixture if child has strings with LEDs
            sc = c.get("sc", 0)
            strings = c.get("strings", [])[:sc]
            if not strings or not any(s.get("leds", 0) > 0 for s in strings):
                continue
            f = {
                "id": ps._nxt_fix,
                "name": c.get("name") or c.get("hostname") or f"Fixture {ps._nxt_fix}",
                "fixtureType": "led", "type": "linear", "childId": cid,
                "strings": [{"leds": s.get("leds", 0), "mm": s.get("mm", 1000),
                              "sdir": s.get("sdir", 0)} for s in strings if s.get("leds", 0) > 0],
                "rotation": [0, 0, 0], "aoeRadius": 1000,
            }
            ps._fixtures.append(f)
            # Map: if layout had an entry for this child's old ID, remap to new fixture ID
            for old_cid, new_cid in child_id_map.items():
                if new_cid == cid:
                    fixture_id_map[old_cid] = ps._nxt_fix
            fixture_id_map[cid] = ps._nxt_fix
            ps._nxt_fix += 1
            fixtures_created += 1
        ps._save("fixtures", ps._fixtures)

        # Remap layout position IDs
        if imported_layout:
            ps._layout = imported_layout
            for lc in ps._layout.get("children", []):
                old_id = lc.get("id")
                # Try fixture map first (old fixture/child ID → new fixture ID)
                new_id = fixture_id_map.get(old_id)
                if new_id is None:
                    # Try child map
                    new_cid = child_id_map.get(old_id)
                    if new_cid is not None:
                        new_id = fixture_id_map.get(new_cid, new_cid)
                if new_id is not None:
                    lc["id"] = new_id
            ps._save("layout", ps._layout)

        # Import explicit fixtures from config (v2+ includes fixtures array)
        imported_fixtures = data.get("fixtures", [])
        for f in imported_fixtures:
            old_fid = f.get("id", -1)
            # Skip if we already auto-created a fixture for this child
            cid = f.get("childId")
            if cid is not None:
                new_cid = child_id_map.get(cid, cid)
                if any(ef.get("childId") == new_cid for ef in ps._fixtures):
                    # Already exists — update fixture_id_map for layout remapping
                    existing = next(ef for ef in ps._fixtures if ef.get("childId") == new_cid)
                    fixture_id_map[old_fid] = existing["id"]
                    continue
            # Create the fixture with a new ID
            f = dict(f)
            new_fid = ps._nxt_fix
            fixture_id_map[old_fid] = new_fid
            f["id"] = new_fid
            if cid is not None:
                f["childId"] = child_id_map.get(cid, cid)
            ps._fixtures.append(f)
            ps._nxt_fix += 1
            fixtures_created += 1
        ps._save("fixtures", ps._fixtures)

        # Re-remap layout IDs with the complete fixture_id_map
        if imported_layout:
            for lc in ps._layout.get("children", []):
                old_id = lc.get("id")
                new_id = fixture_id_map.get(old_id)
                if new_id is not None:
                    lc["id"] = new_id
            ps._save("layout", ps._layout)

    ps.log.info("CONFIG IMPORT: %d children added, %d updated, %d fixtures created, child_map=%s, fix_map=%s",
             added, updated, fixtures_created, child_id_map, fixture_id_map)
    return jsonify(ok=True, added=added, updated=updated, fixturesCreated=fixtures_created)

@bp.post("/api/show/preset")
def api_show_preset():
    """Install a preset show by theme ID from request body."""
    body = request.get_json(silent=True) or {}
    preset_id = body.get("id", "")
    return _install_preset_show(preset_id)

def _install_preset_show(preset_id):
    """Install a preset show as a timeline with spatial effects and actions.

    Dynamically generates a show based on the selected theme and the user's
    actual fixtures, positions, and capabilities. Every fixture gets coverage
    so there are no dark periods.
    """

    from show_generator import generate_show, THEMES
    if preset_id not in THEMES:
        return jsonify(ok=False, err=f"Unknown preset: {preset_id}"), 404

    # Prerequisite check for live-tracking presets (#382)
    warnings = []
    theme = THEMES.get(preset_id, {})
    if theme.get("live_track"):
        needs_camera = not theme.get("patrol_objects")  # patrol shows don't need cameras
        has_camera = any(f.get("fixtureType") == "camera" for f in ps._fixtures)
        has_mover = any(
            f.get("fixtureType") == "dmx" and ps._profile_lib and
            (ps._profile_lib.channel_info(f.get("dmxProfileId", "")) or {}).get("panRange", 0) > 0
            for f in ps._fixtures
        )
        warnings = []
        if needs_camera and not has_camera:
            warnings.append("No camera node registered — person detection will not work")
        if not has_mover:
            warnings.append("No moving head fixtures found — tracking requires DMX movers with pan/tilt")
        # Allow loading but include warnings in response
        if warnings:
            ps.log.warning("Preset %s prerequisites: %s", preset_id, "; ".join(warnings))

    show = generate_show(preset_id, ps._fixtures, ps._layout, ps._stage, ps._profile_lib)
    if not show:
        return jsonify(ok=False, err="Failed to generate show"), 500
    # #837 — `generate_show` now returns a {"error", "msg"} dict for
    # missing-prerequisite themes (e.g. live_track preset on a rig
    # without movers) instead of silently degrading to a non-tracking
    # show that doesn't match the preset name.
    if isinstance(show, dict) and show.get("error"):
        return jsonify(ok=False, err=show.get("msg", show["error"]),
                        code=show["error"]), 400

    with ps._lock:
        dur = show["durationS"]

        # Create patrol objects first so their IDs can be linked to Track actions
        patrol_obj_ids = []
        obj_count = 0
        for po in show.get("patrol_objects", []):
            obj = {
                "id": ps._nxt_obj, "name": po.get("name", f"Patrol {ps._nxt_obj}"),
                "objectType": po.get("objectType", "custom"),
                "mobility": "moving",
                "color": po.get("color", "#00DCFF"),
                "opacity": po.get("opacity", 40),
                "transform": {"pos": [0, 0, 0], "rot": [0, 0, 0],
                               "scale": po.get("scale", [500, 500, 500])},
                "patrol": po.get("patrol", {}),
            }
            ps._objects.append(obj)
            patrol_obj_ids.append(ps._nxt_obj)
            ps._nxt_obj += 1
            obj_count += 1
        if obj_count:
            ps._save("objects", ps._objects)

        # Create action records and build id lookup (#531 — dedupe by
        # (presetId, name, type) so re-loading a preset doesn't clone the
        # same action into the library. Preset-generated actions are
        # tagged with ``presetSource`` so they're distinguishable from
        # user-created entries and only previously-generated presets are
        # considered match candidates — an operator's manually-created
        # action with the same name is never overwritten).
        action_ref_map = {}
        action_count = 0
        existing_preset_by_key = {}
        for a in ps._actions:
            src = a.get("presetSource")
            if not src:
                continue
            key = (src, a.get("name"), a.get("type"))
            existing_preset_by_key[key] = a

        for act_info in show.get("base_actions", []) + show.get("mover_actions", []):
            act_data = act_info.get("action", act_info) if isinstance(act_info, dict) and "action" in act_info else act_info
            key = (preset_id, act_data.get("name"), act_data.get("type"))
            existing = existing_preset_by_key.get(key)
            if existing is not None:
                # Update in place — a preset redefinition is allowed to
                # bump parameters without duplicating the record.
                existing.update(act_data)
                existing["presetSource"] = preset_id
                if existing.get("type") == 18 and patrol_obj_ids:
                    existing["trackObjectIds"] = patrol_obj_ids
                action_ref_map[id(act_info)] = existing["id"]
                continue
            act = {"id": ps._nxt_a, **act_data, "presetSource": preset_id}
            if act.get("type") == 18 and patrol_obj_ids:
                act["trackObjectIds"] = patrol_obj_ids
            ps._actions.append(act)
            existing_preset_by_key[key] = act
            action_ref_map[id(act_info)] = ps._nxt_a
            action_count += 1
            ps._nxt_a += 1
        ps._save("actions", ps._actions)

        # Create spatial effect records
        effect_ref_map = {}  # maps python id() of effect dict -> assigned effect id
        for fx in show.get("effects", []):
            fx_rec = {"id": ps._nxt_sfx, **fx}
            fx_rec.setdefault("fixtureIds", [])
            ps._spatial_fx.append(fx_rec)
            effect_ref_map[id(fx)] = ps._nxt_sfx
            ps._nxt_sfx += 1
        ps._save("spatial_fx", ps._spatial_fx)

        # Build timeline tracks from generator's track structure
        # Tracks are ordered: lower index = lower priority (background)
        tracks = []
        for gen_track in show.get("tracks", []):
            track = {}
            if gen_track.get("allPerformers"):
                track["allPerformers"] = True
            elif "fixtureId" in gen_track:
                # Compare presence, not truthiness — fixture id 0 is a
                # legal id and was being silently dropped here. Symptom:
                # the very first fixture created on a fresh project (id
                # auto-starts at 0) had no per-fixture base-wash track in
                # any preset's timeline, so it sat dark or only saw
                # allPerformers segments. Caught by the template-sweep
                # participation test.
                track["fixtureId"] = gen_track["fixtureId"]
            else:
                continue

            clips = []
            for gen_clip in gen_track.get("clips", []):
                clip = {
                    "startS": gen_clip.get("startS", 0),
                    "durationS": gen_clip.get("durationS", dur),
                }
                # Resolve action or effect reference
                aref = gen_clip.get("_action_ref")
                eref = gen_clip.get("_effect_ref")
                if aref and id(aref) in action_ref_map:
                    clip["actionId"] = action_ref_map[id(aref)]
                    act_data = aref.get("action", aref) if isinstance(aref, dict) and "action" in aref else aref
                    clip["name"] = act_data.get("name", "Action")
                elif eref and id(eref) in effect_ref_map:
                    clip["effectId"] = effect_ref_map[id(eref)]
                    clip["name"] = eref.get("name", "Effect")
                else:
                    continue  # skip clips with no resolved reference
                clips.append(clip)

            if clips:
                track["clips"] = clips
                tracks.append(track)

        tl = {
            "id": ps._nxt_tl, "name": show["name"],
            "durationS": dur,
            "tracks": tracks,
            "loop": True,
        }
        ps._timelines.append(tl)
        ps._nxt_tl += 1
        ps._save("timelines", ps._timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in ps._show_playlist.get("order", []):
            ps._show_playlist.setdefault("order", []).append(tl["id"])
            ps._save("show_playlist", ps._show_playlist)

    resp = {"ok": True, "name": show["name"], "timelineId": tl["id"],
            "actions": action_count, "effects": len(effect_ref_map),
            "objects": obj_count}
    if theme.get("live_track") and warnings:
        resp["warnings"] = warnings
    return jsonify(resp)


def _api_show_preset_old():
    """LEGACY: hardcoded preset shows — kept as fallback reference."""
    body = request.get_json(silent=True) or {}
    preset_id = body.get("id", "")

    PRESETS = {
        "rainbow-up": {
            "name": "Rainbow Up",
            "durationS": 30,
            "actions": [{"name": "Rainbow Classic", "type": 5, "speedMs": 60,
                         "paletteId": 0, "direction": 1}],
        },
        "rainbow-across": {
            "name": "Rainbow Across",
            "durationS": 30,
            "actions": [{"name": "Rainbow Classic", "type": 5, "speedMs": 50,
                         "paletteId": 0, "direction": 0}],
        },
        "slow-fire": {
            "name": "Slow Fire",
            "durationS": 60,
            "actions": [{"name": "Fire Effect", "type": 6, "r": 255, "g": 80, "b": 0,
                         "speedMs": 40, "cooling": 45, "sparking": 100}],
        },
        "disco": {
            "name": "Disco",
            "durationS": 60,
            "actions": [{"name": "Disco Twinkle", "type": 8, "r": 200, "g": 100, "b": 255,
                         "spawnMs": 80, "density": 5, "fadeSpeed": 15}],
        },
        "ocean-wave": {
            "name": "Ocean Wave",
            "durationS": 40,
            "effects": [{"name": "Blue Wave", "category": "spatial-field", "shape": "plane",
                         "r": 0, "g": 80, "b": 220, "size": {"normal": [1,0,0], "thickness": 800},
                         "motion": {"startPos": [0,2500,0], "endPos": [10000,2500,0], "durationS": 10, "easing": "ease-in-out"},
                         "blend": "add"},
                        {"name": "Teal Wash", "category": "spatial-field", "shape": "sphere",
                         "r": 0, "g": 180, "b": 160, "size": {"radius": 2500},
                         "motion": {"startPos": [8000,1000,0], "endPos": [0,3000,0], "durationS": 12, "easing": "ease-in-out"},
                         "blend": "screen"}],
        },
        "sunset": {
            "name": "Sunset Glow",
            "durationS": 45,
            "actions": [{"name": "Warm Breathe", "type": 3, "r": 255, "g": 100, "b": 20,
                         "periodMs": 4000, "minBri": 30}],
            "effects": [{"name": "Golden Sweep", "category": "spatial-field", "shape": "plane",
                         "r": 255, "g": 160, "b": 30, "size": {"normal": [0,1,0], "thickness": 1000},
                         "motion": {"startPos": [5000,5000,0], "endPos": [5000,0,0], "durationS": 20, "easing": "ease-out"},
                         "blend": "screen"}],
        },
        "police": {
            "name": "Police Lights",
            "durationS": 30,
            "actions": [{"name": "Red Strobe", "type": 9, "r": 255, "g": 0, "b": 0,
                         "periodMs": 200, "p8a": 50}],
            "effects": [{"name": "Blue Flash Sweep", "category": "spatial-field", "shape": "box",
                         "r": 0, "g": 0, "b": 255, "size": {"width": 2000, "height": 5000, "depth": 3000},
                         "motion": {"startPos": [0,2500,0], "endPos": [10000,2500,0], "durationS": 2, "easing": "linear"},
                         "blend": "add"}],
        },
        "starfield": {
            "name": "Starfield",
            "durationS": 60,
            "actions": [{"name": "Star Sparkle", "type": 12, "r": 5, "g": 5, "b": 20,
                         "spawnMs": 60, "density": 4}],
        },
        "aurora": {
            "name": "Aurora Borealis",
            "durationS": 40,
            "effects": [{"name": "Green Curtain", "category": "spatial-field", "shape": "plane",
                         "r": 0, "g": 255, "b": 80, "size": {"normal": [1,0.3,0], "thickness": 1500},
                         "motion": {"startPos": [0,2000,0], "endPos": [10000,3000,0], "durationS": 15, "easing": "ease-in-out"},
                         "blend": "screen"},
                        {"name": "Purple Shimmer", "category": "spatial-field", "shape": "sphere",
                         "r": 120, "g": 0, "b": 200, "size": {"radius": 2000},
                         "motion": {"startPos": [8000,3000,0], "endPos": [1000,1500,0], "durationS": 12, "easing": "ease-in-out"},
                         "blend": "add"}],
        },
        # ── Moving-head-aware presets ──────────────────────────────────
        # These use spatial effects with motion paths. LED fixtures get
        # color washes; DMX moving heads also track the effect center
        # with pan/tilt, creating beam sweeps across the stage.
        "spotlight-sweep": {
            "name": "Spotlight Sweep",
            "durationS": 20,
            "effects": [
                {"name": "Sweep Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 240, "b": 200, "size": {"radius": 3000},
                 "motion": {"startPos": [0, 2500, 2500], "endPos": [10000, 2500, 2500],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Return Orb", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 180, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [10000, 2500, 2500], "endPos": [0, 2500, 2500],
                            "durationS": 8, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "concert-wash": {
            "name": "Concert Wash",
            "durationS": 30,
            "actions": [{"name": "Slow Breathe Blue", "type": 3, "r": 0, "g": 40, "b": 200,
                         "periodMs": 5000, "minBri": 20}],
            "effects": [
                {"name": "Magenta Flood", "category": "spatial-field", "shape": "plane",
                 "r": 220, "g": 0, "b": 180, "size": {"normal": [1, 0, 0], "thickness": 2000},
                 "motion": {"startPos": [0, 2500, 5000], "endPos": [10000, 2500, 5000],
                            "durationS": 12, "easing": "ease-in-out"},
                 "blend": "screen"},
                {"name": "Amber Spot", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 160, "b": 40, "size": {"radius": 3000},
                 "motion": {"startPos": [8000, 2500, 3000], "endPos": [2000, 2500, 7000],
                            "durationS": 15, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "figure-eight": {
            "name": "Figure Eight",
            "durationS": 24,
            "effects": [
                # Two spheres crossing at center stage — moving heads track each
                {"name": "Cyan Path A", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [1000, 2500, 2000], "endPos": [9000, 2500, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Cyan Path B", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 220, "b": 255, "size": {"radius": 3000},
                 "motion": {"startPos": [9000, 2500, 2000], "endPos": [1000, 2500, 8000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return A", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 3000},
                 "motion": {"startPos": [9000, 2500, 8000], "endPos": [1000, 2500, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
                {"name": "Gold Return B", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 200, "b": 50, "size": {"radius": 3000},
                 "motion": {"startPos": [1000, 2500, 8000], "endPos": [9000, 2500, 2000],
                            "durationS": 6, "easing": "ease-in-out"},
                 "blend": "add"},
            ],
        },
        "thunderstorm": {
            "name": "Thunderstorm",
            "durationS": 30,
            "actions": [{"name": "Deep Blue Base", "type": 1, "r": 5, "g": 5, "b": 30}],
            "effects": [
                # Lightning bolts — fast-moving spheres that moving heads chase
                {"name": "Lightning Strike 1", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 255, "b": 240, "size": {"radius": 3000},
                 "motion": {"startPos": [3000, 5000, 5000], "endPos": [3000, 0, 5000],
                            "durationS": 0.3, "easing": "ease-in"},
                 "blend": "add"},
                {"name": "Lightning Strike 2", "category": "spatial-field", "shape": "sphere",
                 "r": 200, "g": 200, "b": 255, "size": {"radius": 2500},
                 "motion": {"startPos": [7000, 5000, 3000], "endPos": [7000, 0, 3000],
                            "durationS": 0.3, "easing": "ease-in"},
                 "blend": "add"},
                {"name": "Rolling Thunder", "category": "spatial-field", "shape": "plane",
                 "r": 30, "g": 20, "b": 80, "size": {"normal": [1, 0, 0], "thickness": 3000},
                 "motion": {"startPos": [0, 2500, 5000], "endPos": [10000, 2500, 5000],
                            "durationS": 8, "easing": "linear"},
                 "blend": "screen"},
            ],
        },
        "dance-floor": {
            "name": "Dance Floor",
            "durationS": 20,
            "actions": [{"name": "Chase Pulse", "type": 4, "r": 255, "g": 0, "b": 128,
                         "speedMs": 30, "spacing": 6, "tailLen": 3, "direction": 0}],
            "effects": [
                # Fast orbiting spots — moving heads rapidly track
                {"name": "Red Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 255, "g": 0, "b": 50, "size": {"radius": 2500},
                 "motion": {"startPos": [1000, 2500, 2000], "endPos": [9000, 2500, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Blue Orbit", "category": "spatial-field", "shape": "sphere",
                 "r": 50, "g": 0, "b": 255, "size": {"radius": 2500},
                 "motion": {"startPos": [9000, 2500, 2000], "endPos": [1000, 2500, 8000],
                            "durationS": 3, "easing": "linear"},
                 "blend": "add"},
                {"name": "Green Flash", "category": "spatial-field", "shape": "sphere",
                 "r": 0, "g": 255, "b": 80, "size": {"radius": 3000},
                 "motion": {"startPos": [5000, 5000, 5000], "endPos": [5000, 1000, 5000],
                            "durationS": 2, "easing": "ease-in"},
                 "blend": "add"},
            ],
        },
    }

    preset = PRESETS.get(preset_id)
    if not preset:
        return jsonify(ok=False, err=f"Unknown preset: {preset_id}"), 404

    with ps._lock:
        # Create actions from preset
        action_ids = []
        for a in preset.get("actions", []):
            act = {"id": ps._nxt_a, **a}
            ps._actions.append(act)
            action_ids.append(ps._nxt_a)
            ps._nxt_a += 1
        ps._save("actions", ps._actions)

        # Create spatial effects from preset
        effect_ids = []
        for fx in preset.get("effects", []):
            fx_rec = {"id": ps._nxt_sfx, **fx}
            fx_rec.setdefault("fixtureIds", [])
            ps._spatial_fx.append(fx_rec)
            effect_ids.append(ps._nxt_sfx)
            ps._nxt_sfx += 1
        ps._save("spatial_fx", ps._spatial_fx)

        # Build timeline with one "all performers" track
        clips = []
        t = 0
        for aid in action_ids:
            dur = preset.get("durationS", 30)
            clips.append({"actionId": aid, "startS": 0, "durationS": dur})
        for eid in effect_ids:
            dur = preset.get("durationS", 30)
            clips.append({"effectId": eid, "startS": 0, "durationS": dur})

        tl = {
            "id": ps._nxt_tl, "name": preset["name"],
            "durationS": preset.get("durationS", 30),
            "tracks": [{"allPerformers": True, "clips": clips}],
            "loop": True,
        }
        ps._timelines.append(tl)
        ps._nxt_tl += 1
        ps._save("timelines", ps._timelines)
        # Auto-add new timeline to playlist order (fixes #312)
        if tl["id"] not in ps._show_playlist.get("order", []):
            ps._show_playlist.setdefault("order", []).append(tl["id"])
            ps._save("show_playlist", ps._show_playlist)

    return jsonify(ok=True, name=preset["name"], timelineId=tl["id"],
                   actions=len(action_ids), effects=len(effect_ids))

@bp.get("/api/show/presets")
def api_show_presets():
    """List available preset shows."""
    from show_generator import list_themes
    presets = list_themes()
    return jsonify(presets)

@bp.post("/api/show/demo")
def api_show_demo():
    """Generate a demo show using a random preset theme and existing fixtures."""
    from show_generator import THEMES
    import random
    theme_id = random.choice(list(THEMES.keys()))
    return _install_preset_show(theme_id)

@bp.get("/api/show/export")
def api_show_export():
    """Bundle actions + spatial effects + timelines as a portable show file."""
    return jsonify({"type": "slyled-show", "version": 1,
                    "actions": ps._actions, "spatialEffects": ps._spatial_fx,
                    "timelines": ps._timelines})

# #838 — content-based merge keys for `/api/show/import`. Pre-fix
# the import endpoint replaced the entire actions / spatialEffects /
# timelines lists with the file's contents, destroying every operator-
# created record that wasn't in the imported file. The fix merges by
# content: each incoming action/effect is checked against the existing
# library; if a record with matching name + type + key params exists,
# its id is reused (and the record is NOT overwritten, so operator-
# tweaked fields aren't silently mutated). New records get fresh ids
# and timeline clip refs are remapped to the resolved targets.

# Action key params per type. All types match on (name, type) plus the
# type-specific extras below. Lists are converted to tuples and dicts
# to sorted tuple-of-tuples by `_freeze` so the resulting key is hashable.
_ACTION_KEY_PARAMS_LED = (
    "r", "g", "b", "speedMs", "periodMs", "paletteId",
    "cooling", "sparking", "direction", "density",
    "fadeSpeed", "spawnMs", "minBri", "tailLen",
    "decay", "spacing",
)
_ACTION_KEY_PARAMS_DMX_SCENE = ("dimmer", "pan", "tilt",
                                  "strobe", "gobo", "colorWheel")
_ACTION_KEY_PARAMS_DMX_PT = ("panStart", "panEnd",
                               "tiltStart", "tiltEnd",
                               "ptStartPos", "ptEndPos")
_ACTION_KEY_PARAMS_TRACK = ("trackObjectType", "trackDimmer",
                              "trackFixtureIds", "trackObjectIds",
                              "trackMode", "trackOffset",
                              "trackCycleMs")


def _freeze(v):
    """Recursively convert a value to a hashable form for content-key
    matching. Lists → tuples; dicts → sorted tuple of (k, frozen_v).
    """
    if isinstance(v, dict):
        return tuple(sorted((k, _freeze(vv)) for k, vv in v.items()))
    if isinstance(v, list):
        return tuple(_freeze(x) for x in v)
    return v


def _action_content_key(action):
    """#838 — hashable content key for show-import action dedup.

    Matches actions on `name + type + type-specific params`. Two
    actions with identical content (same name, same type, same
    behaviour-driving params) collapse to one library record on
    import. The operator's fields aren't overwritten — match means
    reuse, not update.
    """
    a_type = action.get("type")
    keys = ["name", "type"]
    if isinstance(a_type, int):
        if 1 <= a_type <= 13:
            keys.extend(_ACTION_KEY_PARAMS_LED)
        elif a_type == 14:  # DMX_SCENE — color + DMX scene channels
            keys.extend(_ACTION_KEY_PARAMS_LED)
            keys.extend(_ACTION_KEY_PARAMS_DMX_SCENE)
        elif a_type == 15:  # DMX_PT_MOVE
            keys.extend(_ACTION_KEY_PARAMS_DMX_PT)
        elif a_type == 16:  # Gobo Select — gobo channel
            keys.append("gobo")
        elif a_type == 17:  # Colour Wheel — wheel slot
            keys.append("colorWheel")
        elif a_type == 18:  # Track
            keys.extend(_ACTION_KEY_PARAMS_TRACK)
    return tuple((k, _freeze(action.get(k))) for k in keys)


def _effect_content_key(effect):
    """#838 — hashable content key for spatial effect dedup. Matches on
    name, category, shape, size, motion (per the issue's spec)."""
    return tuple((k, _freeze(effect.get(k)))
                 for k in ("name", "category", "shape", "size", "motion"))


@bp.post("/api/show/import")
def api_show_import():
    """Merge actions, spatial effects, and timelines from a show file
    into the operator's library (#838).

    Pre-fix this endpoint replaced the entire library with the file's
    contents, destroying every operator-created record that wasn't in
    the imported show. The new behaviour matches incoming
    actions/effects against existing records by content key (see
    `_action_content_key` / `_effect_content_key`):

    - **Match found** → reuse the existing id; the existing record is
      NOT overwritten (operator fields are preserved).
    - **No match** → append with a fresh id and remap timeline clip
      references to that fresh id.

    Timelines themselves are always added with fresh ids; never
    overwrite an existing timeline by id. Clip ``actionId`` /
    ``effectId`` references are remapped to the resolved (existing or
    new) target ids.

    `/api/project/import` is intentionally a different scope — it is
    a full project-state restore (fixtures, layout, calibrations,
    settings) so replacement semantics are correct there.
    """
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-show":
        return jsonify(ok=False, err="not a slyled-show file"), 400

    in_actions = data.get("actions") or []
    in_effects = data.get("spatialEffects") or []
    in_timelines = data.get("timelines") or []

    actions_reused = 0
    actions_created = 0
    effects_reused = 0
    effects_created = 0
    timelines_created = 0

    with ps._lock:
        # Build content-key indices over the existing library so each
        # incoming record is an O(1) lookup.
        existing_actions_by_key = {_action_content_key(a): a
                                    for a in ps._actions}
        existing_effects_by_key = {_effect_content_key(e): e
                                    for e in ps._spatial_fx}

        action_id_remap = {}  # incoming id → resolved id
        for in_a in in_actions:
            key = _action_content_key(in_a)
            existing = existing_actions_by_key.get(key)
            in_id = in_a.get("id")
            if existing is not None:
                action_id_remap[in_id] = existing["id"]
                actions_reused += 1
            else:
                new_a = {**in_a, "id": ps._nxt_a}
                ps._actions.append(new_a)
                existing_actions_by_key[key] = new_a
                action_id_remap[in_id] = ps._nxt_a
                ps._nxt_a += 1
                actions_created += 1

        effect_id_remap = {}
        for in_e in in_effects:
            key = _effect_content_key(in_e)
            existing = existing_effects_by_key.get(key)
            in_id = in_e.get("id")
            if existing is not None:
                effect_id_remap[in_id] = existing["id"]
                effects_reused += 1
            else:
                new_e = {**in_e, "id": ps._nxt_sfx}
                ps._spatial_fx.append(new_e)
                existing_effects_by_key[key] = new_e
                effect_id_remap[in_id] = ps._nxt_sfx
                ps._nxt_sfx += 1
                effects_created += 1

        for in_t in in_timelines:
            new_t = {**in_t, "id": ps._nxt_tl}
            # Remap clip references to the resolved (existing or new)
            # action / effect ids. Tracks/clips are nested dicts; copy
            # them so we don't mutate the caller's payload.
            remapped_tracks = []
            for tr in (in_t.get("tracks") or []):
                new_clips = []
                for cl in (tr.get("clips") or []):
                    new_cl = dict(cl)
                    if "actionId" in new_cl and new_cl["actionId"] in action_id_remap:
                        new_cl["actionId"] = action_id_remap[new_cl["actionId"]]
                    if "effectId" in new_cl and new_cl["effectId"] in effect_id_remap:
                        new_cl["effectId"] = effect_id_remap[new_cl["effectId"]]
                    new_clips.append(new_cl)
                remapped_tracks.append({**tr, "clips": new_clips})
            new_t["tracks"] = remapped_tracks
            ps._timelines.append(new_t)
            ps._nxt_tl += 1
            timelines_created += 1

        ps._save("actions", ps._actions)
        ps._save("spatial_fx", ps._spatial_fx)
        ps._save("timelines", ps._timelines)

    return jsonify(ok=True,
                   actions={"reused": actions_reused,
                            "created": actions_created},
                   spatialEffects={"reused": effects_reused,
                                    "created": effects_created},
                   timelines={"created": timelines_created})

#  "  "  Project file (complete save/load)  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "

PROJECT_SCHEMA_VERSION = 2   # bumped from 1 → 2 for spatial data (#336)


def _compress_cloud(cloud):
    """Gzip-compress point cloud data for .slyshow portability (#336)."""
    import gzip, base64, io
    raw = json.dumps(cloud.get("points", [])).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb') as f:
        f.write(raw)
    result = {k: v for k, v in cloud.items() if k != "points"}
    result["points"] = {"compressed": True,
                        "data": base64.b64encode(buf.getvalue()).decode("ascii")}
    return result


def _decompress_cloud(cloud):
    """Decompress gzip-compressed point cloud from import (#336)."""
    import gzip, base64, io
    pts = cloud.get("points")
    if isinstance(pts, dict) and pts.get("compressed"):
        raw = gzip.decompress(base64.b64decode(pts["data"]))
        cloud["points"] = json.loads(raw)
    return cloud


@bp.get("/api/project/export")
def api_project_export():
    """Bundle ALL state into a complete project file (.slyshow)."""
    # Strip transient fields from children
    _CHILD_STRIP = {"status", "_temporal", "_ttl"}
    clean_children = [{k: v for k, v in c.items() if k not in _CHILD_STRIP}
                      for c in ps._children]
    # Strip transient fields from fixtures
    _FIX_STRIP = {"_placed", "_beamWidth", "_temporal", "_ttl", "positioned"}
    clean_fixtures = [{k: v for k, v in f.items() if k not in _FIX_STRIP}
                      for f in ps._fixtures]
    # Camera SSH: export per-node config with passwords stripped (not portable)
    clean_camera_ssh = {}
    for ip, ssh in ps._camera_ssh.items():
        clean = dict(ssh)
        clean.pop("password", None)  # encrypted passwords are machine-specific
        clean_camera_ssh[ip] = clean
    # Settings minus transient runtime state
    clean_settings = {k: v for k, v in ps._settings.items()
                      if k not in ("runnerRunning", "runnerElapsed")}
    # Point cloud: compress if large (#336)
    cloud_export = None
    if ps._point_cloud and ps._point_cloud.get("points"):
        cloud_export = _compress_cloud(ps._point_cloud)
    # Light maps from mover calibrations (#336)
    light_maps = {}
    for fid, cal in ps._mover_cal.items():
        lm = cal.get("lightMap")
        if lm:
            light_maps[fid] = lm
    # Collect custom DMX profiles referenced by fixtures (#337)
    profile_ids = set()
    for f in ps._fixtures:
        pid = f.get("dmxProfileId")
        if pid:
            profile_ids.add(pid)
    export_profiles = []
    for pid in profile_ids:
        p = ps._profile_lib.get_profile(pid)
        if p and not p.get("builtin"):
            export_profiles.append(p)
    return jsonify({
        "type": "slyled-project",
        "schemaVersion": PROJECT_SCHEMA_VERSION,
        "appVersion": ps.VERSION,
        "savedAt": datetime.utcnow().isoformat() + "Z",
        "name": ps._settings.get("name", "SlyLED"),
        "stage": ps._stage,
        "children": clean_children,
        "fixtures": clean_fixtures,
        "layout": ps._layout,
        "actions": ps._actions,
        "spatialEffects": ps._spatial_fx,
        "timelines": ps._timelines,
        "objects": ps._objects,
        "dmxSettings": {k: v for k, v in ps._dmx_settings.items()},
        "calibrations": ps._calibrations,
        "rangeCalibrations": ps._range_cal,
        "moverCalibrations": ps._mover_cal,
        "cameraSsh": clean_camera_ssh,
        "showPlaylist": ps._show_playlist,
        "profiles": export_profiles,
        "settings": clean_settings,
        # Spatial data (#336)
        "pointCloud": cloud_export,
        "lightMaps": light_maps if light_maps else None,
        # ArUco marker registry (#596) — surveyed ground-truth tags
        "arucoMarkers": list(ps._aruco_markers),
    })


@bp.post("/api/project/import")
def api_project_import():
    """Load a complete project file, replacing ALL state."""
    data = request.get_json(silent=True) or {}
    if data.get("type") != "slyled-project":
        return jsonify(ok=False, err="Not a SlyLED project file"), 400
    sv = data.get("schemaVersion", 1)
    if sv > PROJECT_SCHEMA_VERSION:
        return jsonify(ok=False, err=f"Project file is version {sv}, but this app only supports version {PROJECT_SCHEMA_VERSION}. Please update SlyLED."), 400
    # Stop active playback
    ps._dmx_playback_stop.set()
    pkt_stop = ps._hdr(ps.CMD_RUNNER_STOP)
    pkt_off = ps._hdr(ps.CMD_ACTION_STOP)
    for c in ps._children:
        if c.get("ip"):
            ps._send(c["ip"], pkt_stop)
            ps._send(c["ip"], pkt_off)
    ps._live_events.clear()
    ps._bake_result.clear()
    with ps._lock:
        ps._children = data.get("children", [])
        for c in ps._children:
            c["status"] = 0  # all offline until next ping
        ps._fixtures = data.get("fixtures", [])
        ps._layout = data.get("layout", {"canvasW": 3000, "canvasH": 2000, "children": []})
        # #600 — migrate rotation-array convention on import. Old
        # .slyshow files used [rx, ry=pan, rz=roll]; new convention is
        # [rx, ry=roll, rz=yaw]. Detect via layout.rotationSchemaVersion
        # and swap ry↔rz on every persisted rotation.
        if ps._layout.get("rotationSchemaVersion") != ps._ROTATION_SCHEMA_VERSION:
            _swap = 0
            for _f in ps._fixtures:
                _r = _f.get("rotation")
                if isinstance(_r, list) and len(_r) >= 3:
                    _f["rotation"] = [_r[0], _r[2], _r[1]]
                    _swap += 1
            for _c in (ps._layout.get("children") or []):
                _r = _c.get("rotation")
                if isinstance(_r, list) and len(_r) >= 3:
                    _c["rotation"] = [_r[0], _r[2], _r[1]]
                    _swap += 1
            ps._layout["rotationSchemaVersion"] = ps._ROTATION_SCHEMA_VERSION
            if _swap:
                ps.log.info("#600 project import: migrated %d rotation arrays", _swap)
        # #780 P1 — bake mountedInverted into rotation[1] on import.
        if ps._layout.get("mountedInvertedSchemaVersion") != ps._MOUNTED_INVERTED_SCHEMA_VERSION:
            _baked = 0
            for _f in ps._fixtures:
                if ps._normalise_mounted_inverted(_f):
                    _baked += 1
            ps._layout["mountedInvertedSchemaVersion"] = ps._MOUNTED_INVERTED_SCHEMA_VERSION
            if _baked:
                ps.log.info("#780 P1 project import: baked %d mountedInverted into rotation[1]", _baked)
        ps._stage = data.get("stage", {"w": 10.0, "h": 5.0, "d": 10.0})
        ps._actions = data.get("actions", [])
        ps._spatial_fx = data.get("spatialEffects", [])
        ps._timelines = data.get("timelines", [])
        ps._objects = data.get("objects", [])
        ps._dmx_settings = data.get("dmxSettings", dict(ps._DMX_SETTINGS_DEFAULTS))
        # Reconfigure and restart engine with imported settings (#350)
        if ps._artnet.running:
            ps._artnet.stop()
        if ps._sacn.running:
            ps._sacn.stop()
        ps._apply_dmx_settings()
        _proto = ps._dmx_settings.get("protocol", "artnet")
        _eng = ps._artnet if _proto == "artnet" else ps._sacn if _proto == "sacn" else None
        if _eng and ps._dmx_settings.get("universeRoutes"):
            _eng._bind_ip = "0.0.0.0"  # always use wildcard — saved IP may be stale (#345)
            try:
                _eng.start()
            except Exception:
                pass
            if _eng.running:
                ps._apply_profile_defaults(_eng)
        ps._calibrations.clear()
        ps._calibrations.update(data.get("calibrations", {}))
        ps._range_cal.clear()
        ps._range_cal.update(data.get("rangeCalibrations", {}))
        ps._mover_cal.clear()
        ps._mover_cal.update(data.get("moverCalibrations", {}))
        # #784 PR-7 — was: rebuild manual-cal grids via `_mcal.build_grid`.
        # Manual-cal grids are no longer consumed by any runtime path; the
        # legacy module is gone, so the rebuild step is a no-op.
        # Restore show playlist — prune any orphan IDs that reference deleted timelines
        ps._show_playlist.clear()
        ps._show_playlist.update(data.get("showPlaylist", {"order": [], "loopAll": False}))
        valid_tl_ids = {t["id"] for t in ps._timelines}
        ps._show_playlist["order"] = [tid for tid in ps._show_playlist.get("order", []) if tid in valid_tl_ids]
        # Auto-populate playlist if empty but timelines exist (fixes #312)
        if not ps._show_playlist.get("order") and ps._timelines:
            ps._show_playlist["order"] = [t["id"] for t in ps._timelines]
        # Restore per-node camera SSH (passwords stripped — user must re-enter)
        imported_cam_ssh = data.get("cameraSsh", {})
        if imported_cam_ssh:
            ps._camera_ssh.update(imported_cam_ssh)
            ps._save("camera_ssh", ps._camera_ssh)
        # Merge imported settings (preserve runtime-only fields)
        imp_settings = data.get("settings", {})
        for k, v in imp_settings.items():
            ps._settings[k] = v
        ps._settings["runnerRunning"] = False
        ps._settings["runnerElapsed"] = 0
        # Recompute sequence counters
        ps._nxt_c = max((c["id"] for c in ps._children), default=-1) + 1
        ps._nxt_fix = max((f["id"] for f in ps._fixtures), default=-1) + 1
        ps._nxt_a = max((a["id"] for a in ps._actions), default=-1) + 1
        ps._nxt_obj = max((o["id"] for o in ps._objects), default=-1) + 1
        ps._nxt_sfx = max((f["id"] for f in ps._spatial_fx), default=-1) + 1
        ps._nxt_tl = max((t["id"] for t in ps._timelines), default=-1) + 1
        # Restore spatial data (#336)
        cloud = data.get("pointCloud")
        if cloud:
            ps._point_cloud = _decompress_cloud(cloud)
            ps._save("pointcloud", ps._point_cloud)
        # Restore light maps into mover calibrations (#336)
        light_maps = data.get("lightMaps")
        if light_maps:
            for fid_str, lm in light_maps.items():
                if fid_str in ps._mover_cal:
                    ps._mover_cal[fid_str]["lightMap"] = lm
        # #596 — restore ArUco marker registry from the project file.
        # Silently skip records that fail schema validation rather than
        # aborting the whole import.
        ps._aruco_markers.clear()
        for rec in data.get("arucoMarkers", []) or []:
            try:
                ps._aruco_markers.append(ps._aruco_marker_normalise(rec))
            except (ValueError, TypeError):
                continue
        ps._aruco_markers.sort(key=lambda m: m["id"])
        ps._save("aruco_markers", ps._aruco_markers)
        # Import custom DMX profiles referenced by fixtures (#337).
        # Embedded profiles may or may not exist in the community — we
        # try to stamp `_community` provenance on any that do so the
        # SPA can detect staleness later (#534). Collect the slugs we
        # ended up with so we can batch check_updates after the import.
        # #607 — the .slyshow project file IS the source of truth for
        # profile content embedded inside it (same as fixtures, layout,
        # calibrations, timelines). Previously the import only wrote
        # profiles when no local copy existed, which silently dropped
        # every user-authored edit in the embedded version. New rule:
        # write through, but preserve any community provenance so later
        # check-updates works. A log line records every overwrite so
        # it's visible rather than silent.
        _imported_community_slugs = []
        _imported_profile_diff = []  # (pid, action) for the audit log
        for p in data.get("profiles", []):
            pid = p.get("id")
            if not pid:
                continue
            existing = ps._profile_lib.get_profile(pid)
            if existing is None:
                ps._profile_lib.save_profile(p)
                _imported_profile_diff.append((pid, "added"))
            else:
                # Overwrite with the embedded version — the project
                # file is the source of truth. Preserve _community
                # provenance if the embedded copy dropped it but the
                # local copy had it (check-updates still works).
                if not p.get("_community") and existing.get("_community"):
                    p["_community"] = existing["_community"]
                # Diff before save, ignoring stamped-by-save fields.
                _stamped = ("builtin", "channelCount")
                _e = {k: v for k, v in existing.items() if k not in _stamped}
                _p = {k: v for k, v in p.items() if k not in _stamped}
                changed = (_e != _p)
                ps._profile_lib.save_profile(p)
                _imported_profile_diff.append(
                    (pid, "overwritten" if changed else "unchanged"))
            if p.get("_community") and p["_community"].get("slug"):
                _imported_community_slugs.append(p["_community"]["slug"])
        if _imported_profile_diff:
            _overwritten = sum(1 for _, a in _imported_profile_diff if a == "overwritten")
            _added = sum(1 for _, a in _imported_profile_diff if a == "added")
            ps.log.info("Project import profiles: %d added, %d overwritten, "
                     "%d unchanged (#607 — embedded version is authoritative)",
                     _added, _overwritten,
                     sum(1 for _, a in _imported_profile_diff if a == "unchanged"))
        # Fetch missing profiles from community server (#351) — and
        # stamp them with _community provenance while we're at it.
        _missing_pids = set()
        for f in ps._fixtures:
            pid = f.get("dmxProfileId")
            if pid and not ps._profile_lib.get_profile(pid):
                _missing_pids.add(pid)
        if _missing_pids:
            try:
                import community_client as cc
                for pid in _missing_pids:
                    result = cc.get_profile(pid)
                    if result and result.get("ok"):
                        prof = result.get("data", result)
                        if isinstance(prof, dict) and "id" in prof:
                            ps._stamp_community_provenance(prof, pid)
                            ps._profile_lib.import_profiles([prof])
                            _imported_community_slugs.append(pid)
                            ps.log.info("Project import: fetched missing profile '%s' from community", pid)
                        else:
                            ps.log.warning("Project import: community returned invalid data for '%s'", pid)
                    else:
                        ps.log.warning("Project import: could not fetch profile '%s' from community", pid)
            except Exception as e:
                ps.log.warning("Project import: community profile fetch failed: %s", e)
        # Persist everything
        ps._save("children", ps._children)
        ps._save("fixtures", ps._fixtures)
        ps._save("layout", ps._layout)
        ps._save("stage", ps._stage)
        ps._save("actions", ps._actions)
        ps._save("spatial_fx", ps._spatial_fx)
        ps._save("timelines", ps._timelines)
        ps._save("objects", ps._objects)
        ps._save("dmx_settings", ps._dmx_settings)
        ps._save("calibrations", ps._calibrations)
        ps._save("range_calibrations", ps._range_cal)
        ps._save("mover_calibrations", ps._mover_cal)
        ps._save("show_playlist", ps._show_playlist)
        ps._save("settings", ps._settings)
    ps._apply_dmx_settings()
    name = data.get("name", "Untitled")
    # Report camera nodes that need SSH credentials re-entered
    ssh_needed = []
    for ip, ssh in ps._camera_ssh.items():
        if ssh.get("authType") == "password" and not ssh.get("password"):
            ssh_needed.append({"ip": ip, "user": ssh.get("user", "root"), "authType": "password"})
        elif ssh.get("authType") == "key" and ssh.get("keyPath") and not Path(os.path.expanduser(ssh["keyPath"])).exists():
            ssh_needed.append({"ip": ip, "user": ssh.get("user", "root"), "authType": "key", "keyPath": ssh["keyPath"]})
    # #534 — post-import community update check. Batch-check every
    # profile we just stamped with _community provenance; surface the
    # stale count so the SPA can toast "3 embedded profiles have
    # community updates available". Failures are non-fatal — if the
    # community server is unreachable we just report 0.
    stale_profiles = 0
    stale_detail = []
    if _imported_community_slugs:
        try:
            import community_client as cc
            pairs = []
            for slug in set(_imported_community_slugs):
                p = ps._profile_lib.get_profile(slug) or {}
                ts = (p.get("_community") or {}).get("uploadTs", "")
                pairs.append({"slug": slug, "knownTs": ts})
            result = cc.check_updates(pairs) or {}
            if result.get("ok"):
                stale_detail = (result.get("data") or {}).get("updates") or []
                stale_profiles = len(stale_detail)
        except Exception as e:
            ps.log.warning("Project import: community check_updates failed: %s", e)
    # #737 Issue 1 — surface mover fixtures that landed without a Home
    # anchor. SMART cal, mover-control, and gyro/Android remote all
    # hard-require home; flagging at import time saves the operator from
    # discovering it later via a fixture_not_calibrated error.
    movers_need_home = []
    for f in ps._fixtures:
        if f.get("fixtureType") != "dmx":
            continue
        pid = f.get("dmxProfileId")
        if not pid:
            continue
        cmap = ps._profile_lib.channel_map(pid) or {}
        if "pan" not in cmap or "tilt" not in cmap:
            continue
        if f.get("homePanDmx16") is None or f.get("homeTiltDmx16") is None:
            movers_need_home.append({"id": f.get("id"), "name": f.get("name", "")})
    # #785 — project import replaces every fixture / profile / layout
    # record. Drop the entire aim-sphere cache so the next /aim call
    # rebuilds against the fresh state.
    ps._aim_invalidate_all_spheres()
    return jsonify(ok=True, name=name,
                   children=len(ps._children), fixtures=len(ps._fixtures),
                   actions=len(ps._actions), timelines=len(ps._timelines),
                   objects=len(ps._objects), sshNeeded=ssh_needed,
                   communityStaleProfiles=stale_profiles,
                   communityStaleDetail=stale_detail,
                   moversNeedHome=movers_need_home)


@bp.get("/api/project/name")
def api_project_name_get():
    return jsonify(name=ps._settings.get("name", "SlyLED"))


@bp.post("/api/project/name")
def api_project_name_set():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify(ok=False, err="name required"), 400
    ps._settings["name"] = name
    ps._save("settings", ps._settings)
    return jsonify(ok=True, name=name)
