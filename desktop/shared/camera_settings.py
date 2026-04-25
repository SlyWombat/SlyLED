"""Camera settings + auto-tune helpers (#623).

The camera-node already exposes V4L2 controls at ``GET/POST /camera/controls``
(firmware/orangepi/camera_server.py). The orchestrator proxies those through
``/api/cameras/<fid>/settings`` and drives an auto-tune loop here: pull
a snapshot, score it with an evaluator, nudge one or two settings (exposure,
gain, WB), re-snapshot, re-score. Stops at convergence or ``max_iterations``.

Two evaluators ship:

* **Heuristic** (``evaluate_frame_heuristic``) — pure histogram statistics,
  no external dependency. Default and always available.
* **Local VLM** (``evaluate_frame_ai``) — queries a local Ollama instance
  with a vision model (qwen2.5vl / LLaVA / moondream etc.) running
  entirely on the operator's own hardware. No cloud, no API keys, no
  data leaves the machine. Returns a score plus concrete V4L2-control
  deltas so the loop converges in 1-2 iterations instead of the
  heuristic's gradient search. Falls back to heuristic (with a logged
  note) if Ollama isn't reachable or the model isn't installed.

Ollama setup (one-time):

    curl -fsSL https://ollama.com/install.sh | sh   # Linux / macOS
    ollama pull qwen2.5vl:3b                        # ~3.2 GB, CPU-friendly

Override ``SLYLED_OLLAMA_URL`` (default ``http://localhost:11434``) and
``SLYLED_OLLAMA_MODEL`` (default ``qwen2.5vl:3b``) when running on a
different host or model.

Slot storage is a simple JSON dict keyed by fixture id; the orchestrator
writes it via ``_save("camera_settings_slots", ...)``.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request

log = logging.getLogger("slyled.camera_settings")

# ── V4L2 proxy ─────────────────────────────────────────────────────────

def camera_controls_get(camera_ip, cam_idx=0, timeout=15):
    """GET camera-node /camera/controls?cam=N. Returns the parsed JSON
    (``{ok, cam, controls: [...], saved: {...}}``) or raises on failure.

    Timeout raised from 5 → 15 s because a busy Pi (running YOLO / depth
    / another scan) routinely takes >5 s to return the v4l2-ctl list,
    and the auto-tune caller would surface the bare "timed out" with no
    indication of which step failed.
    """
    url = f"http://{camera_ip}:5000/camera/controls?cam={cam_idx}"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def camera_controls_set(camera_ip, cam_idx, controls, timeout=15):
    """POST camera-node /camera/controls with a controls dict.
    Returns ``{ok, applied}`` from the camera."""
    url = f"http://{camera_ip}:5000/camera/controls"
    payload = {"cam": int(cam_idx), "controls": controls}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ── #682-N — lock camera auto controls for calibration ────────────────
#
# Between flash-detect ON and OFF captures (~0.4-1 s apart), the camera's
# auto-exposure / auto-WB / auto-gain can shift the scene baseline —
# that baseline shift then leaks into the ON-OFF diff as a spurious
# bright delta in any steadily-lit region. Industrial vision pipelines
# standardly lock those controls for the duration of the job. Save the
# operator's current values so they can be restored at cal end.
#
# V4L2 menu conventions (as exposed by the Pi's camera_server):
#   auto_exposure: 1 = Manual, 3 = Aperture Priority (auto)
#   white_balance_automatic: 0 = manual, 1 = auto
#   gain_automatic: 0 = manual, 1 = auto  (optional; not all sensors)
_CAL_LOCK_KEYS = ("auto_exposure", "white_balance_automatic",
                  "gain_automatic")


def lock_auto_controls_for_cal(camera_ip, cam_idx, timeout=15):
    """Save current values of the auto-* controls, then set them to
    manual. Returns a dict ``{prior, applied, locked, notes}`` that the
    caller stores and passes back to ``restore_auto_controls``.

    ``locked`` is False when no auto controls were found (nothing to
    lock) — the caller should still proceed but warn the operator that
    ambient drift may affect flash-detect.
    """
    try:
        raw = camera_controls_get(camera_ip, cam_idx, timeout=timeout)
    except Exception as e:
        return {"prior": {}, "applied": {}, "locked": False,
                "notes": [f"could not read current controls ({e})"]}
    controls = raw.get("controls") or []
    ctrl_by_name = {c.get("name"): c for c in controls}
    prior = {}
    desired = {}
    notes = []
    for key in _CAL_LOCK_KEYS:
        c = ctrl_by_name.get(key)
        if c is None:
            continue
        cur = c.get("value")
        prior[key] = cur
        # auto_exposure=1 is Manual in the UVC menu; 0 is Manual on some
        # cheap cameras. Pick whichever is currently NOT active as the
        # "manual" target — if the camera reports an auto value we keep
        # the opposite.
        if key == "auto_exposure":
            # Most UVC menus: 1=Manual, 3=Aperture. Always target 1.
            desired[key] = 1
        else:
            # boolean toggle — off (0) is manual.
            desired[key] = 0
        if cur != desired[key]:
            notes.append(f"{key}: {cur} → {desired[key]}")
    if not desired:
        return {"prior": prior, "applied": {}, "locked": False,
                "notes": ["no auto controls exposed by this camera — "
                           "ambient drift may affect flash-detect"]}
    try:
        applied = camera_controls_set(camera_ip, cam_idx, desired,
                                        timeout=timeout)
    except Exception as e:
        return {"prior": prior, "applied": {}, "locked": False,
                "notes": notes + [f"could not apply manual mode ({e})"]}
    return {"prior": prior, "applied": applied.get("applied") or desired,
            "locked": True, "notes": notes}


def restore_auto_controls(camera_ip, cam_idx, lock_state, timeout=15):
    """Restore the pre-cal state recorded by ``lock_auto_controls_for_cal``.

    Safe no-op when ``lock_state`` says nothing was locked.
    """
    prior = (lock_state or {}).get("prior") or {}
    if not prior:
        return {"ok": True, "restored": {}}
    # Only restore keys we actually changed (applied vs prior differ).
    applied = (lock_state or {}).get("applied") or {}
    to_restore = {k: v for k, v in prior.items()
                   if k in applied and applied[k] != v}
    if not to_restore:
        return {"ok": True, "restored": {}}
    try:
        camera_controls_set(camera_ip, cam_idx, to_restore, timeout=timeout)
    except Exception as e:
        return {"ok": False, "err": str(e), "tried": to_restore}
    return {"ok": True, "restored": to_restore}


# ── Heuristic evaluator ────────────────────────────────────────────────

def evaluate_frame(frame, intent="general"):
    """Backward-compatible alias for the heuristic evaluator.

    New callers should pick evaluate_frame_heuristic or evaluate_frame_ai
    directly via make_evaluator(mode). This keeps the public surface from
    the first iteration (tests, CVEngine callers) working.
    """
    return evaluate_frame_heuristic(frame, intent=intent)


def evaluate_frame_heuristic(frame, intent="general"):
    """Score a BGR frame for detection suitability via histogram stats.

    Higher score = better. All intents penalise blown-out highlights
    because clipped pixels destroy detail regardless of downstream task.

    Args:
        frame:   BGR numpy array.
        intent:  "general" / "beam" / "aruco" / "yolo".

    Returns a dict with ``score``, ``highlightsClipped``,
    ``shadowsClipped``, ``mean``, ``std``, ``notes``, ``deltaProposal``.
    Caller reads ``score`` to compare candidates and ``deltaProposal``
    (optional) to short-circuit the gradient search.
    """
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy required for evaluate_frame")

    if frame is None or frame.size == 0:
        return {"score": -1.0, "notes": ["empty frame"]}

    # Convert to single-channel luminance.
    if frame.ndim == 3:
        # BT.601 luma. Avoid cv2 dep so the evaluator works in headless tests.
        b = frame[:, :, 0].astype("float32")
        g = frame[:, :, 1].astype("float32")
        r = frame[:, :, 2].astype("float32")
        lum = 0.114 * b + 0.587 * g + 0.299 * r
    else:
        lum = frame.astype("float32")

    total = float(lum.size)
    hi_clip = float((lum >= 250).sum()) / total
    lo_clip = float((lum <= 5).sum()) / total
    mean = float(lum.mean())
    std = float(lum.std())

    notes = []
    # Target: mean near 128 for general, slightly lower for beam-detection
    # (so beam pops without dominant scene), ArUco tolerates mid-range.
    target_mean = {
        "general": 128.0,
        "beam": 80.0,
        "aruco": 128.0,
        "yolo": 128.0,
    }.get(intent, 128.0)

    # Clipping penalties — hard: anything over 2% is bad.
    hi_penalty = min(1.0, hi_clip / 0.02) * 40.0
    lo_penalty = min(1.0, lo_clip / 0.05) * 10.0  # less painful for beam
    # Brightness-away-from-target penalty, normalised to [0, 40].
    bright_penalty = min(40.0, abs(mean - target_mean) * 0.6)
    # Contrast bonus — std in a good range gets rewarded.
    contrast_bonus = 0.0
    if 35.0 <= std <= 75.0:
        contrast_bonus = 10.0
    elif std < 20.0:
        contrast_bonus = -20.0  # washed out

    score = 100.0 - hi_penalty - lo_penalty - bright_penalty + contrast_bonus

    if hi_clip > 0.02:
        notes.append(f"highlights clipped ({hi_clip*100:.1f}% > 2%)")
    if lo_clip > 0.05:
        notes.append(f"shadows clipped ({lo_clip*100:.1f}% > 5%)")
    if mean > target_mean + 30:
        notes.append("scene too bright — reduce exposure or gain")
    elif mean < target_mean - 30:
        notes.append("scene too dark — raise exposure or gain")
    if std < 20:
        notes.append("low contrast — check white balance")

    return {
        "score": round(score, 2),
        "highlightsClipped": round(hi_clip, 4),
        "shadowsClipped": round(lo_clip, 4),
        "mean": round(mean, 1),
        "std": round(std, 1),
        "notes": notes,
        "evaluator": "heuristic",
    }


# ── Analyzer evaluator (deterministic OpenCV — #685 default) ────────────
#
# Replaces moondream as the auto-tune default. The 2026-04-25 matrix run
# proved moondream was unable to drive auto-tune — it copied prompt
# example values regardless of input image. The CV analyzer in this
# section is the operator-built tool ported in from
# tools/cv_analyzer_evaluator.py: histogram + LAB cast + intent-aware
# deltaProposal. Sub-second, deterministic, image-aware. Produced
# 49 → 96.80 on cam16/aruco where moondream peaked at 98.28 with
# extreme variance and required prompt fencing to avoid making the
# well-lit cam12 cells WORSE.
#
# Convention matches evaluate_frame_heuristic: score 0..100,
# `deltaProposal` keyed by V4L2 control names. The orchestrator's
# auto_tune_loop reads `deltaProposal` and applies it directly,
# bypassing the heuristic gradient.

# Tunable thresholds — ratios of pixels at the edges of the histogram
# that count as "clipped". Same defaults as heuristic so the score
# numbers are comparable.
_AN_HI_CLIP_PCT = 0.02
_AN_LO_CLIP_PCT = 0.05

# Intent-specific exposure targets in 8-bit luminance mean.
_AN_INTENT_TARGET = {
    "general": 128.0,
    "beam":     80.0,    # beam needs the rest of the scene dark
    "aruco":   128.0,
    "yolo":    128.0,
}


def _control_value(controls_meta, name):
    """Look up the current value of a V4L2 control by name. Returns
    None when the control isn't in the meta list (camera doesn't have
    it, or the operator passed empty meta for a one-shot eval)."""
    for c in (controls_meta or []):
        if c.get("name") == name:
            return c.get("value")
    return None


def _control_meta(controls_meta, name):
    for c in (controls_meta or []):
        if c.get("name") == name:
            return c
    return None


def _clamp_proposal(meta, value):
    """Clamp a proposed control value to the camera's declared range
    so the orchestrator never tries to write past the V4L2 limits."""
    if meta is None:
        return value
    try:
        lo = int(meta.get("min", 0) or 0)
        hi = int(meta.get("max", 255) or 255)
    except (TypeError, ValueError):
        return value
    return max(lo, min(hi, int(round(value))))


def evaluate_frame_analyzer(frame, intent="general", controls_meta=None):
    """#685 — deterministic CV evaluator. Reads the current V4L2 control
    values out of ``controls_meta`` (so it knows what direction +
    magnitude to suggest) and proposes concrete delta values.

    Returns the same shape as evaluate_frame_heuristic plus a populated
    ``deltaProposal`` when the image has actionable issues. Returns an
    EMPTY ``deltaProposal`` when the image is already good — that's the
    "conservative on good images" property the moondream alternative
    couldn't honour. The auto_tune_loop then reads the empty dict and
    stops the iteration cleanly.
    """
    try:
        import numpy as np
    except ImportError:
        raise RuntimeError("numpy required for evaluate_frame_analyzer")

    if frame is None or frame.size == 0:
        return {"score": -1.0, "notes": ["empty frame"], "deltaProposal": {},
                "evaluator": "analyzer"}

    # ── Luminance + clipping ──────────────────────────────────────────
    if frame.ndim == 3:
        b = frame[:, :, 0].astype("float32")
        g = frame[:, :, 1].astype("float32")
        r = frame[:, :, 2].astype("float32")
        lum = 0.114 * b + 0.587 * g + 0.299 * r
    else:
        lum = frame.astype("float32")
    total = float(lum.size)
    hi_clip = float((lum >= 250).sum()) / total
    lo_clip = float((lum <= 5).sum()) / total
    mean = float(lum.mean())
    std = float(lum.std())

    # ── LAB chroma cast (white-balance signal) ────────────────────────
    # Median a* / b* deviation from neutral (128) flags a cast. Use
    # numpy-only conversion (BGR → XYZ → LAB approximation) so the
    # analyzer works without OpenCV when running headless tests.
    a_med = b_med = 128.0
    try:
        import cv2 as _cv2
        if frame.ndim == 3:
            lab = _cv2.cvtColor(frame, _cv2.COLOR_BGR2LAB)
            a_med = float(np.median(lab[:, :, 1]))
            b_med = float(np.median(lab[:, :, 2]))
    except Exception:
        pass
    # Cast magnitude (Euclidean from neutral, bounded 0..40-ish).
    cast_a = a_med - 128.0
    cast_b = b_med - 128.0
    cast_mag = float(np.hypot(cast_a, cast_b))

    # ── Score (matches heuristic so they're directly comparable) ──────
    target = _AN_INTENT_TARGET.get(intent, 128.0)
    hi_penalty = min(1.0, hi_clip / _AN_HI_CLIP_PCT) * 40.0
    lo_penalty = min(1.0, lo_clip / _AN_LO_CLIP_PCT) * 10.0
    bright_penalty = min(40.0, abs(mean - target) * 0.6)
    contrast_bonus = 0.0
    if 35.0 <= std <= 75.0:
        contrast_bonus = 10.0
    elif std < 20.0:
        contrast_bonus = -20.0
    score = 100.0 - hi_penalty - lo_penalty - bright_penalty + contrast_bonus

    # ── Diagnose (intent-aware, what's actually wrong) ────────────────
    notes = []
    diagnoses = []
    if hi_clip > _AN_HI_CLIP_PCT:
        diagnoses.append("overexposed")
        notes.append(f"highlights clipped ({hi_clip*100:.1f}% > 2%)")
    if mean < target - 25 and hi_clip < _AN_HI_CLIP_PCT:
        diagnoses.append("underexposed")
        notes.append(f"mean {mean:.0f} < target {target:.0f}")
    if (mean > target + 25 and intent != "beam"
            and "overexposed" not in diagnoses):
        diagnoses.append("overexposed")
        notes.append(f"mean {mean:.0f} > target {target:.0f}")
    if std < 25.0:
        diagnoses.append("low_contrast")
        notes.append(f"low contrast (std {std:.0f})")
    if cast_mag > 12.0:
        diagnoses.append("white_balance_cast")
        notes.append(f"WB cast Δa={cast_a:+.0f} Δb={cast_b:+.0f}")
    if not diagnoses:
        notes.append("image is acceptable for this intent")

    # ── Propose deltas ────────────────────────────────────────────────
    delta = {}
    exp_meta = _control_meta(controls_meta, "exposure_time_absolute")
    cur_exp = _control_value(controls_meta, "exposure_time_absolute")
    gain_meta = _control_meta(controls_meta, "gain")
    cur_gain = _control_value(controls_meta, "gain")
    wb_temp_meta = _control_meta(controls_meta, "white_balance_temperature")
    cur_wb = _control_value(controls_meta, "white_balance_temperature")

    if "underexposed" in diagnoses and "overexposed" not in diagnoses:
        # Lift exposure first; if it's near rail, lift gain too.
        if exp_meta is not None and cur_exp is not None:
            new_exp = int(cur_exp * 2.5)
            new_exp = _clamp_proposal(exp_meta, new_exp)
            if new_exp != cur_exp:
                delta["exposure_time_absolute"] = new_exp
        # Add gain only when exposure is already past 80% of its range
        # (lifting noise floor unnecessarily destroys detection quality
        # on cameras that have headroom).
        if (gain_meta is not None and cur_gain is not None and
                exp_meta is not None and cur_exp is not None):
            try:
                exp_max = int(exp_meta.get("max") or 0)
                if exp_max and cur_exp >= 0.8 * exp_max:
                    new_gain = int(cur_gain) + 20
                    new_gain = _clamp_proposal(gain_meta, new_gain)
                    if new_gain != cur_gain:
                        delta["gain"] = new_gain
            except (TypeError, ValueError):
                pass

    elif "overexposed" in diagnoses:
        if exp_meta is not None and cur_exp is not None:
            new_exp = int(cur_exp * 0.6)
            new_exp = _clamp_proposal(exp_meta, max(1, new_exp))
            if new_exp != cur_exp:
                delta["exposure_time_absolute"] = new_exp
        # If already at the floor, drop gain.
        elif gain_meta is not None and cur_gain is not None:
            new_gain = int(cur_gain) - 10
            new_gain = _clamp_proposal(gain_meta, max(0, new_gain))
            if new_gain != cur_gain:
                delta["gain"] = new_gain

    if "white_balance_cast" in diagnoses and intent != "beam":
        # b* > 0 = yellow cast → lower colour temp.  b* < 0 = blue.
        if wb_temp_meta is not None and cur_wb is not None:
            shift = -200 if cast_b > 0 else 200
            new_wb = _clamp_proposal(wb_temp_meta, int(cur_wb) + shift)
            if new_wb != cur_wb:
                delta["white_balance_temperature"] = new_wb

    return {
        "score": round(score, 1),
        "highlightsClipped": round(hi_clip, 4),
        "shadowsClipped": round(lo_clip, 4),
        "mean": round(mean, 1),
        "std": round(std, 1),
        "castA": round(cast_a, 1),
        "castB": round(cast_b, 1),
        "diagnoses": diagnoses,
        "notes": notes,
        "deltaProposal": delta,
        "evaluator": "analyzer",
    }


# ── AI evaluator (local VLM via Ollama) ────────────────────────────────
#
# Runs entirely on the operator's own hardware. No cloud, no API keys, no
# telemetry. Default model is qwen2.5vl:3b (~3.2 GB, CPU-friendly). Any
# model Ollama serves with vision support works — swap via
# SLYLED_OLLAMA_MODEL when a larger GPU is available (llava:13b, qwen2-vl,
# bakllava). Pre-#685 default was moondream but its JSON adherence was
# poor, so the matrix run found AI mode produced no useful deltas.

_OLLAMA_URL = os.environ.get("SLYLED_OLLAMA_URL", "http://localhost:11434")
# #685 architecture decision (post 2026-04-25 matrix): the deterministic
# `analyzer` evaluator is now the auto-tune default, AI is opt-in. No
# model is shipped or auto-pulled. Operators who want an AI evaluator
# pull a model via `ollama pull <name>` and select it on Settings -> AI
# Runtime; the selection persists in _settings["aiAutoTuneModel"].
# Empty string = no default; AI mode raises with a "select a model"
# message until the operator picks one. Env override
# (SLYLED_OLLAMA_MODEL) still wins for headless deployments.
_OLLAMA_MODEL = os.environ.get("SLYLED_OLLAMA_MODEL", "")
# #685 follow-up — moondream's vision encoder takes 30+ s per call on CPU
# even with the existing 768-px downsample, blowing the per-call budget on
# slower laptops. Raise the default so first-call ingestion finishes inside
# one timeout window. Operator can drop the env var on faster GPU rigs.
_OLLAMA_TIMEOUT_S = int(os.environ.get("SLYLED_OLLAMA_TIMEOUT_S", "120"))
# Max long-side of the JPEG sent to the VLM. The exposure/focus/sharpness
# judgement the auto-tune evaluator needs is unchanged below ~640 px and
# vision-encoder cost scales quadratically. 640 lands a 16:9 frame at
# 640×360 ≈ 230 k pixels — about half the recommended 800×600 footprint
# but visually equivalent for tuning purposes. Operator override:
# SLYLED_AI_FRAME_LONG_SIDE.
_AI_FRAME_LONG_SIDE = int(os.environ.get("SLYLED_AI_FRAME_LONG_SIDE", "640"))
_AI_FRAME_JPEG_QUALITY = int(os.environ.get("SLYLED_AI_FRAME_JPEG_QUALITY", "75"))

# #685 follow-up — minimum acceptable score per intent before the heuristic
# auto-tune declares "done". Below threshold, gradient-flat does NOT stop
# the loop; it keeps exploring through max_iterations to give the next
# proposal a chance. Aruco / yolo / general need a balanced exposure to
# read corners and colour; beam tolerates suppressed highlights.
_INTENT_MIN_SCORE_DEFAULT = 70
_INTENT_MIN_SCORE = {
    "aruco":   70,
    "beam":    65,
    "yolo":    70,
    "general": 70,
}


def _ollama_available():
    """Probe Ollama once per auto-tune run. Returns (ok, err) so callers
    can surface the reason the AI path was skipped.

    Post-#685: when no model is configured (env unset and operator hasn't
    selected one in Settings), we report Ollama as "available" if the
    daemon answers — the model-selection error is raised at make_evaluator
    time so the message can name the right Settings panel."""
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tags = json.loads(resp.read().decode())
    except Exception as e:
        return False, (f"Ollama not reachable at {_OLLAMA_URL} ({e}). "
                        f"Install Ollama from https://ollama.com, then pull "
                        f"a vision model from USER_MANUAL Appendix D.")
    if not _OLLAMA_MODEL:
        # Daemon is up but no env-default model is set. Defer the
        # "pick a model" error to the caller.
        return True, None
    names = {m.get("name", "").split(":")[0]
             for m in tags.get("models", [])}
    if _OLLAMA_MODEL.split(":")[0] not in names:
        return False, (f"Ollama is running but model '{_OLLAMA_MODEL}' "
                        f"isn't pulled. Run `ollama pull {_OLLAMA_MODEL}` "
                        f"or pick a different model in Settings → AI Runtime.")
    return True, None


def _frame_to_jpeg_b64(frame, max_side=None, quality=None):
    """BGR array → base64 JPEG. Downsizes for VLM inference speed;
    full-resolution 4K frames slow small models to a crawl without
    improving scene assessment.

    #685 follow-up — defaults moved to module-level
    ``_AI_FRAME_LONG_SIDE`` / ``_AI_FRAME_JPEG_QUALITY`` constants
    (env-tunable) so the basement-rig ingestion-time fix stays visible
    in one place. Logs the resize so the orchestrator log proves the
    downsample happened.
    """
    if max_side is None:
        max_side = _AI_FRAME_LONG_SIDE
    if quality is None:
        quality = _AI_FRAME_JPEG_QUALITY
    try:
        import cv2
    except ImportError:
        raise RuntimeError("opencv required to encode frames for AI evaluator")
    h, w = frame.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        new_w = int(w * scale)
        new_h = int(h * scale)
        frame = cv2.resize(frame, (new_w, new_h),
                           interpolation=cv2.INTER_AREA)
        log.info("[autotune] resized snapshot %dx%d → %dx%d "
                  "before evaluator call", w, h, new_w, new_h)
    ok, buf = cv2.imencode(".jpg", frame,
                            [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return base64.b64encode(buf.tobytes()).decode("ascii")


_AI_SYSTEM_PROMPT = (
    "You are the evaluator for a theatrical-lighting camera tuning loop. "
    "Assess the attached frame for the stated detection intent. Score "
    "0-100 (higher = better) and propose concrete V4L2 control "
    "adjustments that would improve the score. Reply with STRICT JSON "
    "only — no prose, no markdown fences. Schema:\n"
    '{"score": <0-100 int>, '
    '"notes": [<short human string>, ...], '
    '"deltaProposal": {"exposure_time_absolute": <int | null>, '
    '"gain": <int | null>, '
    '"white_balance_automatic": <0|1|null>, '
    '"white_balance_temperature": <int | null>}}\n'
    "Set a key to null when you don't want to change that control; omit "
    "`deltaProposal` when the frame is already good. Keep `notes` to 1-3 "
    "short strings."
)


def evaluate_frame_ai(frame, intent="general", controls_meta=None,
                       resize_long_side=None, model=None):
    """Local VLM evaluator via Ollama.

    `controls_meta` is the list returned by /camera/controls so the model
    knows legal value ranges when proposing deltas.

    `resize_long_side` (px) overrides the module-default ``_AI_FRAME_LONG_SIDE``
    so the SPA Tune modal can let the operator trade VLM speed for image
    detail per #685 follow-up.

    Returns the heuristic schema plus ``deltaProposal`` — a dict of
    {control_name: value} the loop applies directly, bypassing the
    heuristic gradient. Raises RuntimeError if Ollama isn't running or
    the model isn't pulled.
    """
    ok, err = _ollama_available()
    if not ok:
        raise RuntimeError(err)

    b64 = _frame_to_jpeg_b64(frame, max_side=resize_long_side)

    # Summarise current control state so the model has concrete ranges.
    ctrl_brief = []
    for c in (controls_meta or []):
        if c.get("name") in {"exposure_time_absolute", "gain",
                              "white_balance_automatic",
                              "white_balance_temperature",
                              "auto_exposure", "brightness",
                              "contrast", "saturation"}:
            ctrl_brief.append({
                "name": c["name"], "value": c.get("value"),
                "min": c.get("min"), "max": c.get("max"),
            })

    prompt = (
        _AI_SYSTEM_PROMPT
        + f"\n\nIntent: {intent}\nCurrent controls:\n"
        + json.dumps(ctrl_brief, indent=2)
    )

    chosen_model = model or _OLLAMA_MODEL
    payload = {
        "model": chosen_model,
        "prompt": prompt,
        "images": [b64],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 400},
    }
    req = urllib.request.Request(
        f"{_OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_OLLAMA_TIMEOUT_S) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        raise RuntimeError(f"Ollama request failed: {e}")

    raw = (body.get("response") or "").strip()
    # `format: "json"` makes Ollama enforce JSON-shaped output; strip any
    # incidental fences a less-obedient model might still add.
    if raw.startswith("```"):
        lines = [ln for ln in raw.splitlines() if not ln.startswith("```")]
        raw = "\n".join(lines)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Local VLM returned non-JSON: {e} — "
                           f"body was {raw[:200]!r}")

    score = float(parsed.get("score", 0))
    notes = parsed.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    delta = parsed.get("deltaProposal") or {}
    delta = {k: v for k, v in delta.items() if v is not None}

    return {
        "score": round(score, 1),
        "notes": list(notes)[:4],
        "deltaProposal": delta,
        "evaluator": "ai-local-vlm",
        "model": chosen_model,
        "evalMs": int((body.get("total_duration") or 0) / 1e6),
    }


def make_evaluator(mode, resize_long_side=None, model=None):
    """Return a callable (frame, controls_meta, intent) → result-dict for
    the requested mode.

    Modes (#685 architecture, post-2026-04-25 matrix):
      * "analyzer" (DEFAULT) — deterministic OpenCV. No external deps.
                                 Sub-second. Image-aware. Conservative
                                 on good frames (returns empty
                                 deltaProposal).
      * "heuristic"          — histogram-only score; never proposes a
                                 delta. Used by the auto_tune loop as
                                 the objective gate ("did the
                                 analyzer's delta actually help?").
      * "ai"                 — local VLM via Ollama. Optional. Raises
                                 up front when Ollama isn't running or
                                 the operator hasn't selected/pulled a
                                 model.
      * "auto"               — analyzer first; if it returns empty
                                 delta and AI is configured, fall
                                 through to AI; the iteration loop
                                 still gates every applied delta with
                                 the heuristic.

    ``resize_long_side`` (px) overrides the module default for AI mode
    only — analyzer / heuristic ignore it.
    """
    mode = (mode or "analyzer").lower()

    def _run_ai(frame, controls_meta=None, intent="general"):
        return evaluate_frame_ai(frame, intent=intent,
                                  controls_meta=controls_meta,
                                  resize_long_side=resize_long_side,
                                  model=model)

    def _run_heuristic(frame, controls_meta=None, intent="general"):
        return evaluate_frame_heuristic(frame, intent=intent)

    def _run_analyzer(frame, controls_meta=None, intent="general"):
        return evaluate_frame_analyzer(frame, intent=intent,
                                        controls_meta=controls_meta)

    def _run_auto(frame, controls_meta=None, intent="general"):
        # Analyzer first. If it returned an actionable delta, use it.
        result = _run_analyzer(frame, controls_meta=controls_meta,
                                intent=intent)
        if result.get("deltaProposal"):
            return result
        # No actionable delta from analyzer. If AI is configured AND a
        # model is selected, fall through. Otherwise return the
        # analyzer result so the loop's plateau check stops cleanly.
        ai_ok, _err = _ollama_available()
        if ai_ok and model:
            ai_result = evaluate_frame_ai(frame, intent=intent,
                                            controls_meta=controls_meta,
                                            resize_long_side=resize_long_side,
                                            model=model)
            # Tag the chained provenance so the log shows analyzer →
            # ai handover.
            ai_result["evaluator"] = "auto-ai"
            ai_result["chainedFrom"] = "analyzer"
            return ai_result
        result["evaluator"] = "auto-analyzer"
        return result

    if mode == "analyzer":
        return _run_analyzer
    if mode == "heuristic":
        return _run_heuristic
    if mode == "ai":
        ok, err = _ollama_available()
        if not ok:
            raise RuntimeError(err)
        if not model:
            raise RuntimeError(
                "AI evaluator requires an explicit model selection; pull "
                "one via `ollama pull <name>` and select it in Settings → "
                "AI Runtime → Active vision model.")
        return _run_ai
    if mode == "auto":
        # `auto` always returns; the analyzer never raises and the
        # AI-fallback inside _run_auto is gated.
        return _run_auto
    # Unknown mode — fall back to analyzer rather than crashing.
    return _run_analyzer


# ── Auto-tune loop ─────────────────────────────────────────────────────

def _find_control(controls, name):
    """Look up one control record in the /camera/controls list."""
    for c in controls:
        if c.get("name") == name:
            return c
    return None


def _clamp_to_range(ctrl, value):
    """Clamp a proposed value to [min, max] from the control metadata."""
    if ctrl is None:
        return value
    lo = ctrl.get("min", value)
    hi = ctrl.get("max", value)
    return max(lo, min(hi, int(value)))


class AutoTuneCancelled(Exception):
    """#685 follow-up — operator clicked Cancel mid-iteration. Raised by
    the loop's cancel-check callback so the orchestrator route can map
    it to a 499 response, release the per-camera device lock, and clean
    up the cancel flag without conflating with a real failure."""


def auto_tune_loop(camera_ip, cam_idx, intent,
                    fetch_snapshot_fn, max_iterations=6, settle_s=0.5,
                    progress_cb=None, evaluator_mode="heuristic",
                    cancel_check=None, resize_long_side=None,
                    model=None):
    """Iteratively adjust exposure + gain until the scored frame plateaus
    or ``max_iterations`` runs out.

    On auto_exposure boards the loop first switches to manual mode so
    exposure_time_absolute becomes writable. When manual control isn't
    available (no ``auto_exposure`` control, or the camera doesn't expose
    ``exposure_time_absolute``), the loop tunes gain alone.

    When the evaluator returns a ``deltaProposal`` (the local-VLM path
    does this), the loop applies it directly instead of running the
    heuristic gradient — a good VLM converges in 1-2 iterations on
    almost any scene.

    Args:
        camera_ip, cam_idx:   target camera.
        intent:               evaluate_frame intent key.
        fetch_snapshot_fn:    callable(ip, idx) → BGR numpy array.
        max_iterations:       upper bound on setting changes.
        settle_s:             wait after each setting write for the sensor
                              to stabilise before the next snapshot.
        progress_cb:          optional callable({stage, iteration, score,
                              applied, notes}) for job-status streaming.
        evaluator_mode:       "heuristic" | "ai" | "auto". See
                              ``make_evaluator``.

    Returns ``{before, after, applied, history, evaluator}``.
    """
    evaluator = make_evaluator(evaluator_mode,
                                 resize_long_side=resize_long_side,
                                 model=model)
    try:
        initial = camera_controls_get(camera_ip, cam_idx)
    except Exception as e:
        raise RuntimeError(
            f"camera controls GET timed out after 15 s "
            f"(camera {camera_ip}:5000/camera/controls?cam={cam_idx}); "
            f"the Pi may be busy with depth / YOLO — wait, or bump "
            f"camera_controls_get timeout. Cause: {e}") from e
    controls = initial.get("controls", [])
    ae_ctrl = _find_control(controls, "auto_exposure")
    exp_ctrl = _find_control(controls, "exposure_time_absolute")
    gain_ctrl = _find_control(controls, "gain")
    wb_auto_ctrl = _find_control(controls, "white_balance_automatic")

    # Current V4L2 values.
    state = {}
    if ae_ctrl is not None:
        state["auto_exposure"] = ae_ctrl.get("value")
    if exp_ctrl is not None:
        state["exposure_time_absolute"] = exp_ctrl.get("value")
    if gain_ctrl is not None:
        state["gain"] = gain_ctrl.get("value")
    if wb_auto_ctrl is not None:
        state["white_balance_automatic"] = wb_auto_ctrl.get("value")

    # Snap the initial state.
    try:
        frame0 = fetch_snapshot_fn(camera_ip, cam_idx)
    except Exception as e:
        raise RuntimeError(
            f"baseline snapshot timed out "
            f"(camera {camera_ip}:5000/snapshot?cam={cam_idx}); "
            f"increase the fetch_snapshot timeout if the Pi is under "
            f"heavy load. Cause: {e}") from e
    try:
        before = evaluator(frame0, controls_meta=controls, intent=intent)
    except Exception as e:
        # #685 — distinguish the AI evaluator's failure modes so the SPA
        # can show a useful remedy hint instead of "verify Ollama is
        # running" when Ollama IS running but the call timed out / the
        # model isn't pulled / etc.
        cause = str(e)
        cause_lower = cause.lower()
        if evaluator_mode == "heuristic" or "ollama" not in cause_lower:
            raise RuntimeError(
                f"baseline evaluator failed (mode={evaluator_mode}). "
                f"Cause: {cause}") from e
        if "isn't pulled" in cause or "not pulled" in cause_lower:
            hint = (f"the AI evaluator says model {_OLLAMA_MODEL!r} is not "
                     f"pulled. Run `ollama pull {_OLLAMA_MODEL}`.")
        elif "not reachable" in cause_lower:
            hint = (f"the AI evaluator can't reach Ollama at {_OLLAMA_URL}. "
                     "Start the Ollama service (`ollama serve` or system "
                     "service) before retrying.")
        elif "timed out" in cause_lower or "timeout" in cause_lower:
            hint = ("the AI evaluator timed out waiting for the model to "
                     "respond. The model may still be cold-loading; retry "
                     "in a moment or pre-warm via Settings → AI Engines.")
        elif "non-json" in cause_lower:
            hint = ("the AI evaluator returned a malformed response. The "
                     "model may not be vision-capable for this format; try "
                     "a different vision model (qwen2.5vl:3b, llava).")
        else:
            hint = f"AI evaluator error: {cause}"
        raise RuntimeError(
            f"baseline evaluator failed (mode={evaluator_mode}): {hint}") from e
    if progress_cb:
        progress_cb({"stage": "baseline", "iteration": 0,
                     "score": before["score"], "applied": dict(state),
                     "notes": before["notes"]})

    # Engage manual exposure mode so exposure_time_absolute becomes writable.
    # V4L2 UVC menu convention: 1 = Manual, 3 = Aperture Priority.
    if ae_ctrl is not None and state.get("auto_exposure") != 1:
        try:
            camera_controls_set(camera_ip, cam_idx, {"auto_exposure": 1})
            state["auto_exposure"] = 1
            time.sleep(settle_s)
            # Refresh control metadata — exposure_time_absolute flags change.
            controls = camera_controls_get(camera_ip, cam_idx).get("controls", [])
            exp_ctrl = _find_control(controls, "exposure_time_absolute")
            if exp_ctrl is not None:
                state["exposure_time_absolute"] = exp_ctrl.get("value")
        except Exception as e:
            log.warning("auto_tune: couldn't engage manual AE (%s) — "
                        "tuning gain only", e)
            exp_ctrl = None

    history = [{"iteration": 0, **before, "applied": dict(state)}]
    best = before
    best_state = dict(state)

    for it in range(1, max_iterations + 1):
        # #685 follow-up — operator-initiated cancel.  Checked ONCE per
        # iteration before any V4L2 write so the device is left in the
        # last applied state rather than mid-write.
        if cancel_check is not None and cancel_check():
            raise AutoTuneCancelled()
        prev = history[-1]
        delta = {}

        # VLM fast path — if the evaluator returned a concrete deltaProposal,
        # clamp each value to the control's legal range and use it directly.
        proposal = prev.get("deltaProposal") or {}
        if proposal:
            ctrl_by_name = {c["name"]: c for c in controls}
            for name, value in proposal.items():
                ctrl = ctrl_by_name.get(name)
                if ctrl is None:
                    continue
                try:
                    value = int(value) if ctrl.get("type") == "int" \
                        else (1 if value else 0) if ctrl.get("type") == "bool" \
                        else int(value)
                except (TypeError, ValueError):
                    continue
                value = _clamp_to_range(ctrl, value)
                if value != state.get(name):
                    delta[name] = value
            if delta:
                # VLM got its say — apply it and skip heuristic gradient.
                pass

        # Heuristic fallback when the evaluator didn't propose anything
        # (either heuristic mode, or AI judged the frame already good).
        if not delta:
            # Works with either evaluator's fields. AI result may lack
            # highlightsClipped / mean entirely.
            hi = prev.get("highlightsClipped") or 0.0
            lo = prev.get("shadowsClipped") or 0.0
            mean = prev.get("mean")

            # Prefer exposure; fall back to gain when exposure hit its rail.
            target_mean = {"general": 128.0, "beam": 80.0,
                            "aruco": 128.0, "yolo": 128.0}.get(intent, 128.0)
            need_brighten = (mean is not None
                             and mean < target_mean - 10 and lo > 0.02)
            need_darken = ((mean is not None and mean > target_mean + 10)
                           or hi > 0.02)

            if exp_ctrl is not None:
                cur_exp = state.get("exposure_time_absolute") or exp_ctrl.get("default", 100)
                if need_darken:
                    proposal = int(cur_exp * 0.6)
                elif need_brighten:
                    proposal = int(cur_exp * 1.6)
                else:
                    proposal = None
                if proposal is not None:
                    proposal = _clamp_to_range(exp_ctrl, proposal)
                    if proposal != cur_exp:
                        delta["exposure_time_absolute"] = proposal

            if not delta and gain_ctrl is not None:
                cur_gain = state.get("gain") or 0
                step = max(5, (gain_ctrl.get("max", 50) - gain_ctrl.get("min", 0)) // 10)
                if need_darken:
                    proposal = cur_gain - step
                elif need_brighten:
                    proposal = cur_gain + step
                else:
                    proposal = None
                if proposal is not None:
                    proposal = _clamp_to_range(gain_ctrl, proposal)
                    if proposal != cur_gain:
                        delta["gain"] = proposal

        if not delta:
            # Nothing to tune — stop early.
            if progress_cb:
                progress_cb({"stage": "converged", "iteration": it,
                             "score": best["score"], "applied": dict(state),
                             "notes": ["no further adjustment needed"]})
            break

        try:
            camera_controls_set(camera_ip, cam_idx, delta)
            state.update(delta)
            time.sleep(settle_s)
        except Exception as e:
            log.warning("auto_tune: apply %s failed (%s) — aborting", delta, e)
            break

        try:
            frame = fetch_snapshot_fn(camera_ip, cam_idx)
        except Exception as e:
            log.warning("auto_tune iter %d: snapshot timed out (%s) — "
                        "returning best-so-far", it, e)
            break
        try:
            score = evaluator(frame, controls_meta=controls, intent=intent)
        except Exception as e:
            log.warning("auto_tune iter %d: evaluator failed (%s) — "
                        "returning best-so-far", it, e)
            break
        entry = {"iteration": it, **score, "applied": dict(state)}
        history.append(entry)
        if progress_cb:
            progress_cb({"stage": "iterating", "iteration": it,
                         "score": score["score"], "applied": dict(state),
                         "notes": score.get("notes", [])})

        if score["score"] > best["score"] + 0.5:
            best = score
            best_state = dict(state)
        else:
            # #685 follow-up — pre-fix the loop stopped on the FIRST flat
            # gradient even when the absolute score was clearly bad
            # (basement-rig matrix run: heuristic declared aruco "done"
            # at score 49 / 100 with the Sly slot baseline). Now: keep
            # exploring through max_iterations whenever best_score is
            # below the per-intent minimum, so the heuristic can still
            # land a workable score on under-tuned starts.
            min_score = _INTENT_MIN_SCORE.get(intent,
                                                _INTENT_MIN_SCORE_DEFAULT)
            if best["score"] >= min_score:
                log.info("auto_tune: plateaued at iteration %d (best=%.1f, "
                         "now=%.1f) — score >= intent threshold %.0f, stopping",
                         it, best["score"], score["score"], min_score)
                break
            else:
                log.info("auto_tune: gradient flat at iteration %d (best=%.1f) "
                         "but below intent threshold %.0f — continuing exploration",
                         it, best["score"], min_score)

    # Apply best state if the final iteration regressed.
    if best_state != state:
        try:
            camera_controls_set(camera_ip, cam_idx, best_state)
        except Exception as e:
            log.warning("auto_tune: couldn't restore best_state (%s)", e)

    return {
        "before": before,
        "after": best,
        "applied": best_state,
        "history": history,
        "evaluator": before.get("evaluator", "heuristic"),
    }
