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
  with a vision model (Moondream / LLaVA / qwen2-vl etc.) running entirely
  on the operator's own hardware. No cloud, no API keys, no data leaves
  the machine. Returns a score plus concrete V4L2-control deltas so the
  loop converges in 1-2 iterations instead of the heuristic's gradient
  search. Falls back to heuristic (with a logged note) if Ollama isn't
  reachable or the model isn't installed.

Ollama setup (one-time):

    curl -fsSL https://ollama.com/install.sh | sh   # Linux / macOS
    ollama pull moondream                           # ~1.7 GB, CPU-only OK

Override ``SLYLED_OLLAMA_URL`` (default ``http://localhost:11434``) and
``SLYLED_OLLAMA_MODEL`` (default ``moondream``) when running on a
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


# ── AI evaluator (local VLM via Ollama) ────────────────────────────────
#
# Runs entirely on the operator's own hardware. No cloud, no API keys, no
# telemetry. Default model is Moondream (~1.7 GB, CPU-only OK). Any model
# Ollama serves with vision support works — swap via SLYLED_OLLAMA_MODEL
# when a larger GPU is available (llava:13b, qwen2-vl, bakllava).

_OLLAMA_URL = os.environ.get("SLYLED_OLLAMA_URL", "http://localhost:11434")
_OLLAMA_MODEL = os.environ.get("SLYLED_OLLAMA_MODEL", "moondream")
_OLLAMA_TIMEOUT_S = int(os.environ.get("SLYLED_OLLAMA_TIMEOUT_S", "60"))


def _ollama_available():
    """Probe Ollama once per auto-tune run. Returns (ok, err) so callers
    can surface the reason the AI path was skipped."""
    try:
        req = urllib.request.Request(f"{_OLLAMA_URL}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tags = json.loads(resp.read().decode())
    except Exception as e:
        return False, (f"Ollama not reachable at {_OLLAMA_URL} ({e}). "
                        f"Install: `curl -fsSL https://ollama.com/install.sh | sh` "
                        f"then `ollama pull {_OLLAMA_MODEL}`.")
    names = {m.get("name", "").split(":")[0]
             for m in tags.get("models", [])}
    if _OLLAMA_MODEL.split(":")[0] not in names:
        return False, (f"Ollama is running but model '{_OLLAMA_MODEL}' "
                        f"isn't pulled. Run `ollama pull {_OLLAMA_MODEL}` "
                        f"to enable AI auto-tune.")
    return True, None


def _frame_to_jpeg_b64(frame, max_side=768, quality=80):
    """BGR array → base64 JPEG. Downsizes for VLM inference speed;
    full-resolution 4K frames slow small models to a crawl without
    improving scene assessment."""
    try:
        import cv2
    except ImportError:
        raise RuntimeError("opencv required to encode frames for AI evaluator")
    h, w = frame.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    if scale < 1.0:
        frame = cv2.resize(frame, (int(w * scale), int(h * scale)),
                           interpolation=cv2.INTER_AREA)
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


def evaluate_frame_ai(frame, intent="general", controls_meta=None):
    """Local VLM evaluator via Ollama.

    `controls_meta` is the list returned by /camera/controls so the model
    knows legal value ranges when proposing deltas.

    Returns the heuristic schema plus ``deltaProposal`` — a dict of
    {control_name: value} the loop applies directly, bypassing the
    heuristic gradient. Raises RuntimeError if Ollama isn't running or
    the model isn't pulled.
    """
    ok, err = _ollama_available()
    if not ok:
        raise RuntimeError(err)

    b64 = _frame_to_jpeg_b64(frame)

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

    payload = {
        "model": _OLLAMA_MODEL,
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
        "model": _OLLAMA_MODEL,
        "evalMs": int((body.get("total_duration") or 0) / 1e6),
    }


def make_evaluator(mode):
    """Return a callable (frame, controls_meta, intent) → result-dict for
    the requested mode.

    Modes:
      * "heuristic" (default) — always available, no ML dep.
      * "ai"                  — local VLM via Ollama; raises up front when
                                 Ollama isn't running or the model isn't
                                 pulled.
      * "auto"                — prefer AI, fall back silently to heuristic
                                 when the local VLM is unavailable.
    """
    mode = (mode or "heuristic").lower()

    def _run_ai(frame, controls_meta=None, intent="general"):
        return evaluate_frame_ai(frame, intent=intent,
                                  controls_meta=controls_meta)

    def _run_heuristic(frame, controls_meta=None, intent="general"):
        return evaluate_frame_heuristic(frame, intent=intent)

    if mode == "heuristic":
        return _run_heuristic
    if mode == "ai":
        ok, err = _ollama_available()
        if not ok:
            raise RuntimeError(err)
        return _run_ai
    if mode == "auto":
        ok, _err = _ollama_available()
        if ok:
            return _run_ai
        log.info("auto-tune evaluator=auto: local VLM unavailable, using heuristic")
        return _run_heuristic
    raise ValueError(f"unknown evaluator mode '{mode}'")


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
                    cancel_check=None):
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
    evaluator = make_evaluator(evaluator_mode)
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
                     "a different vision model (moondream, llava).")
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
            # No meaningful improvement — stop.
            log.info("auto_tune: plateaued at iteration %d (best=%.1f, now=%.1f)",
                     it, best["score"], score["score"])
            break

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
