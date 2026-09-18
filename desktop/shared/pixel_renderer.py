"""pixel_renderer.py — SlyLED Effect Spec v1 (#938).

Server-side per-pixel effect renderer. The SPEC is the SPA implementation in
``desktop/shared/spa/js/pixel_renderer.js``; this module is its Python twin and
must produce byte-identical output for every input. That equality is gated by
``tests/test_pixel_renderer_parity.py``, which runs the JS under Node against
``tests/fixtures/pixel_corpus/``.

Why a server-side renderer exists: before #938 pixel colour was resolved only
in the browser (``bake_engine`` emits *action metadata*, the SPA turned it into
RGB). Streaming to a pixel controller (#940) and generating offline ``.hseq``
files (#941) both need frames on the server.

Why the SPA is the spec rather than the firmware: the renderer must be
STATELESS and seekable, so ``.hseq`` output is reproducible and live streams can
resume from ``go_epoch``. The firmware (``main/ChildLED.cpp``) is stateful for
fire (heat buffer), comet/scanner (frame-to-frame ``nscale8`` trails) and uses
``random8()``. The deterministic hash-based approximations here are a
deliberate, documented divergence from firmware — not a porting bug.

PORTING HAZARDS (all load-bearing; see the parity test):
  * JS ``Math.round`` is half-up; Python ``round``/``np.round`` are banker's.
    Always ``math.floor(x + 0.5)``.
  * JS ``|0`` and ``Math.floor(a/b)`` on non-negative values are ``//``.
  * JS ``>>8`` here is ``// 256`` — every operand is non-negative.
  * ``>>> 0`` is an unsigned 32-bit coercion — ``% 2**32``.

If you change pixel maths you MUST regenerate the corpus and bump
EFFECT_SPEC_VERSION in BOTH this file and pixel_renderer.js.
"""

import math

EFFECT_SPEC_VERSION = 1

# Action type constants (mirrors the SPA's `pc.t`).
FADE, BREATHE, CHASE, RAINBOW = 2, 3, 4, 5
FIRE, COMET, TWINKLE, STROBE = 6, 7, 8, 9
WIPE, SCANNER, SPARKLE, GRADIENT = 10, 11, 12, 13


def _jsround(x):
    """JS ``Math.round``: half away from zero toward +Infinity.

    ``Math.round(2.5) === 3`` but Python ``round(2.5) == 2`` (banker's) and
    ``np.round(2.5) == 2.0``. ``floor(x + 0.5)`` matches JS for negatives too:
    ``Math.round(-2.5) === -2``, ``floor(-2.0) == -2``.
    """
    return math.floor(x + 0.5)


def hsv_to_rgb(h, s, v):
    """FastLED-style ``hsv2rgb_rainbow`` approximation (h, s, v: 0-255).

    Twin of ``_hsvToRgb``. The trailing ``Math.round`` in the JS is a no-op —
    every branch already yields an integer — so it is omitted here.
    """
    h &= 0xFF
    s &= 0xFF
    v &= 0xFF
    inv = 255 - s
    sext = h // 43
    frac = (h - sext * 43) * 6
    if sext == 0:
        r, g, b = v, (v * (255 - ((s * (255 - frac)) // 256))) // 256, (v * inv) // 256
    elif sext == 1:
        r, g, b = (v * (255 - ((s * frac) // 256))) // 256, v, (v * inv) // 256
    elif sext == 2:
        r, g, b = (v * inv) // 256, v, (v * (255 - ((s * (255 - frac)) // 256))) // 256
    elif sext == 3:
        r, g, b = (v * inv) // 256, (v * (255 - ((s * frac) // 256))) // 256, v
    elif sext == 4:
        r, g, b = (v * (255 - ((s * (255 - frac)) // 256))) // 256, (v * inv) // 256, v
    else:
        r, g, b = v, (v * inv) // 256, (v * (255 - ((s * frac) // 256))) // 256
    return [r, g, b]


def pal_color(pal_id, idx):
    """Twin of ``_palColor`` — palette lookup, idx 0-255."""
    idx &= 0xFF
    if pal_id == 1:
        return hsv_to_rgb(((idx >> 1) + 120) & 0xFF, 200, min(255, 160 + idx // 3))
    if pal_id == 2:
        return hsv_to_rgb((idx >> 2) & 0xFF, 255,
                          min(255, 200 + _jsround(math.sin(idx * math.pi / 128) * 51)))
    if pal_id == 3:
        return hsv_to_rgb((idx // 3 + 60) & 0xFF, 220,
                          min(255, 100 + _jsround(math.sin(idx * math.pi / 128) * 128)))
    if pal_id == 4:
        return hsv_to_rgb((idx * 3) & 0xFF, 255, 255)
    if pal_id == 5:
        t = idx
        if t < 85:
            return [t * 3, 0, 0]
        if t < 170:
            return [255, (t - 85) * 3, 0]
        return [255, 255, (t - 170) * 3]
    if pal_id == 6:
        return hsv_to_rgb(((idx >> 1) + 140) & 0xFF, 180, min(255, 180 + (idx >> 2)))
    if pal_id == 7:
        return hsv_to_rgb(idx, 100, 255)
    # pal_id 0 and any unknown id
    return hsv_to_rgb(idx, 255, 255)


def pixel(action_type, params, di, dot_count, elapsed_ms):
    """Twin of ``_emuPixel``. Returns ``[r, g, b]``, or ``None`` if the action
    type has no procedural per-pixel form (the caller supplies a flat colour).

    ``di`` is the pixel index, ``dot_count`` the string length.
    """
    p = params or {}
    e = elapsed_ms
    t = action_type

    def pv(key, default):
        # JS `p.x || default` — falsy (0, None, missing) takes the default.
        got = p.get(key)
        return default if not got else got

    if t == RAINBOW:
        spd = pv("speedMs", 50)
        if spd < 1:
            spd = 1
        direction = p.get("direction") or 0
        pal_id = p.get("paletteId") or 0
        time_off = (e // spd) & 0xFF
        idx = (dot_count - 1 - di) if direction in (2, 3) else di
        hue = (idx * 255 // dot_count) + time_off
        return pal_color(pal_id, hue & 0xFF)

    if t == CHASE:
        spd = pv("speedMs", 100)
        if spd < 1:
            spd = 1
        spc = pv("spacing", 3)
        if spc < 2:
            spc = 3
        direction = p.get("direction") or 0
        off = (e // spd) % spc
        idx = (dot_count - 1 - di) if direction in (2, 3) else di
        if (idx + off) % spc == 0:
            return [pv("r", 100), pv("g", 200), pv("b", 255)]
        return [0, 0, 0]

    if t == COMET:
        spd = pv("speedMs", 40)
        if spd < 1:
            spd = 1
        tail = pv("tailLen", 10)
        if tail < 1:
            tail = 10
        direction = p.get("direction") or 0
        head = (e // spd) % (dot_count + tail)
        pos = (dot_count - 1 - head % dot_count) if direction in (2, 3) else (head % dot_count)
        dist = abs(di - pos)
        if head >= dot_count:
            return [0, 0, 0]
        if dist == 0:
            return [pv("r", 255), pv("g", 255), pv("b", 255)]
        if dist <= tail:
            f = 1 - dist / tail
            return [_jsround(pv("r", 255) * f), _jsround(pv("g", 255) * f),
                    _jsround(pv("b", 255) * f)]
        return [0, 0, 0]

    if t == WIPE:
        spd = pv("speedMs", 30)
        if spd < 1:
            spd = 1
        direction = p.get("direction") or 0
        filled = (e // spd) % (dot_count * 2)
        filling = filled < dot_count
        cnt = filled if filling else (dot_count * 2 - filled)
        idx = (dot_count - 1 - di) if direction in (2, 3) else di
        colour = [pv("r", 255), pv("g", 128), p.get("b") or 0]
        if idx < cnt:
            return colour if filling else [0, 0, 0]
        return [0, 0, 0] if filling else colour

    if t == SCANNER:
        spd = pv("speedMs", 30)
        if spd < 1:
            spd = 1
        bar = pv("barWidth", 3)
        if bar < 1:
            bar = 3
        travel = max(dot_count - bar, 1)
        cyc = travel * 2
        pos = (e // spd) % cyc
        if pos >= travel:
            pos = cyc - pos
        if pos <= di < pos + bar:
            return [pv("r", 255), p.get("g") or 0, p.get("b") or 0]
        return [0, 0, 0]

    if t == FADE:
        spd = pv("speedMs", 1000)
        if spd < 1:
            spd = 1
        cyc = spd * 2
        tt = e % cyc
        frac = (tt / spd) if tt < spd else ((cyc - tt) / spd)
        return [_jsround((p.get("r") or 0) * (1 - frac) + (p.get("r2") or 0) * frac),
                _jsround((p.get("g") or 0) * (1 - frac) + (p.get("g2") or 0) * frac),
                _jsround((p.get("b") or 0) * (1 - frac) + (p.get("b2") or 0) * frac)]

    if t == BREATHE:
        per = pv("periodMs", 3000)
        if per < 1:
            per = 3000
        min_b = (p.get("minBri") or 0) / 100
        phase = (e % per) / per * 2 * math.pi
        bri = min_b + (1 - min_b) * (0.5 + 0.5 * math.sin(phase))
        return [_jsround(pv("r", 200) * bri), _jsround(pv("g", 100) * bri),
                _jsround(pv("b", 255) * bri)]

    if t == STROBE:
        per = pv("periodMs", 100)
        duty = pv("dutyPct", 50)
        if e % per < per * duty / 100:
            return [pv("r", 255), pv("g", 255), pv("b", 255)]
        return [0, 0, 0]

    if t == FIRE:
        heat = max(0, min(255, 128 + _jsround(80 * math.sin(di * 0.7 + e * 0.003))
                          + _jsround(40 * math.sin(di * 1.3 + e * 0.007))))
        if heat < 85:
            return [heat * 3, 0, 0]
        if heat < 170:
            return [255, (heat - 85) * 3, 0]
        return [255, 255, min(255, (heat - 170) * 3)]

    if t == TWINKLE:
        seed = (di * 2654435761 + e // 80) % 4294967296
        bri = (seed >> 8) & 0xFF
        if bri > 180:
            return [_jsround(pv("r", 200) * bri / 255), _jsround(pv("g", 200) * bri / 255),
                    _jsround(pv("b", 255) * bri / 255)]
        return [0, 0, 0]

    if t == SPARKLE:
        seed = (di * 2654435761 + e // 50) % 4294967296
        if ((seed >> 16) & 0xFF) > 230:
            return [255, 255, 255]
        return [pv("r", 180), pv("g", 180), pv("b", 220)]

    if t == GRADIENT:
        frac = di / (dot_count - 1) if dot_count > 1 else 0
        return [_jsround((p.get("r") or 0) * (1 - frac) + (p.get("r2") or 0) * frac),
                _jsround((p.get("g") or 0) * (1 - frac) + (p.get("g2") or 0) * frac),
                _jsround((p.get("b") or 0) * (1 - frac) + (p.get("b2") or 0) * frac)]

    return None


# ── Vectorised rendering ─────────────────────────────────────────────────────
# The scalar `pixel()` above is the readable oracle and the parity spec. The
# live path (#940) renders every fixture at 40 Hz, so a per-pixel Python loop
# over thousands of pixels is too slow; `render_string` vectorises with numpy
# where it can and falls back to the scalar path otherwise. Equality between
# the two is asserted in tests/test_pixel_renderer.py — the fast path may never
# disagree with the oracle.

try:
    import numpy as _np
except ImportError:  # numpy is a hard dep of the orchestrator, but keep import-safe
    _np = None


def render_string(action_type, params, n, elapsed_ms):
    """Render a whole string: returns ``bytes`` of length ``n * 3`` (RGB).

    Byte-identical to looping ``pixel()`` over ``range(n)``.
    Returns ``None`` when the action type has no procedural per-pixel form,
    matching ``pixel()``'s contract so callers branch once.
    """
    if n <= 0:
        return b""
    if pixel(action_type, params, 0, n, elapsed_ms) is None:
        return None

    fast = _render_string_vectorised(action_type, params, n, elapsed_ms)
    if fast is not None:
        return fast

    buf = bytearray(n * 3)
    for i in range(n):
        r, g, b = pixel(action_type, params, i, n, elapsed_ms)
        buf[i * 3:i * 3 + 3] = (r, g, b)
    return bytes(buf)


def _render_string_vectorised(action_type, params, n, elapsed_ms):
    """numpy fast paths for the position-dependent effects that dominate cost.

    Returns ``None`` when there is no fast path, so the caller loops. Effects
    whose colour is constant across the string (FADE, BREATHE, STROBE) are
    handled by evaluating once and tiling — exact by construction.
    """
    if _np is None:
        return None
    t = action_type

    # Uniform-across-string effects: evaluate one pixel, tile it.
    if t in (FADE, BREATHE, STROBE):
        rgb = pixel(t, params, 0, n, elapsed_ms)
        return bytes(bytearray(rgb) * n)

    # Position-dependent, integer-only: vectorise index maths, then map through
    # the scalar colour function over the (small) set of distinct results.
    if t in (TWINKLE, SPARKLE):
        p = params or {}
        e = elapsed_ms
        idx = _np.arange(n, dtype=_np.int64)  # int64: di*2654435761 exceeds int32
        step = (e // 80) if t == TWINKLE else (e // 50)
        seed = (idx * 2654435761 + step) % 4294967296
        out = _np.zeros((n, 3), dtype=_np.uint8)
        if t == TWINKLE:
            bri = (seed >> 8) & 0xFF
            hit = bri > 180
            if hit.any():
                pr_, pg_, pb_ = (params or {}).get("r") or 200, \
                                (params or {}).get("g") or 200, \
                                (params or {}).get("b") or 255
                bh = bri[hit].astype(_np.float64)
                out[hit, 0] = _np.floor(pr_ * bh / 255 + 0.5)
                out[hit, 1] = _np.floor(pg_ * bh / 255 + 0.5)
                out[hit, 2] = _np.floor(pb_ * bh / 255 + 0.5)
        else:
            base = [p.get("r") or 180, p.get("g") or 180, p.get("b") or 220]
            out[:] = base
            out[((seed >> 16) & 0xFF) > 230] = (255, 255, 255)
        return out.tobytes()

    return None


# ── Segment selection ────────────────────────────────────────────────────────

def active_segment(segments, string_index, t_s):
    """Pick the segment driving ``string_index`` at show time ``t_s`` seconds.

    Mirrors the LED preview rule in ``bake_engine.py`` exactly (the loop that
    emits ``{"t","p","e"}`` entries), because the SPA's 3D preview consumes that
    output — if this diverges, what the operator previews is not what streams.
    The rule is:

      1. skip segments bound to a different ``stringIndex``
         (``stringIndex`` absent = applies to every string),
      2. take the FIRST segment covering ``t_s``, half-open ``[startS, startS+durationS)``,
      3. ``durationS`` defaults to **1**, not 0.

    Note on priority: the bake pre-sorts by ``(startS, -_pri)``, so ``_pri``
    only breaks ties between segments sharing a ``startS``. An earlier-starting
    segment still shadows a later, higher-priority one that overlaps it. That is
    existing show-engine behaviour, faithfully reproduced here rather than
    "fixed" — changing it would make streamed pixels disagree with the preview.
    """
    for seg in segments or ():
        si = seg.get("stringIndex")
        if si is not None and si != string_index:
            continue
        start = seg.get("startS", 0)
        if start <= t_s < start + seg.get("durationS", 1):
            return seg
    return None


def render_fixture(bake_fixture, strings, t_s):
    """Render every string of a fixture at show time ``t_s``.

    ``bake_fixture`` is the per-fixture entry from the bake result (it carries
    ``segments``); ``strings`` is the fixture's string list, each with ``leds``.
    Returns concatenated RGB bytes in fixture-string order — the layout the
    pixel output map (#939) writes into DMX universes.
    """
    segments = (bake_fixture or {}).get("segments") or []
    out = bytearray()
    for si, s in enumerate(strings or ()):
        n = int(s.get("leds") or 0)
        if n <= 0:
            continue
        seg = active_segment(segments, si, t_s)
        if seg is None:
            out.extend(b"\x00" * (n * 3))
            continue
        elapsed_ms = int((t_s - seg.get("startS", 0)) * 1000)
        params = seg.get("params") or {}
        rendered = render_string(seg.get("type"), params, n, elapsed_ms)
        if rendered is None:
            # Non-procedural action (e.g. SOLID): flat colour across the string.
            rgb = bytes((params.get("r") or 0, params.get("g") or 0,
                         params.get("b") or 0))
            out.extend(rgb * n)
        else:
            out.extend(rendered)
    return bytes(out)
