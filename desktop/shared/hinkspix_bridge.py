"""hinkspix_bridge.py — HolidayCoro HinksPix PRO HTTP control surface (#939).

The HinksPix PRO exposes a small JSON-over-CGI API on port 80 with no auth.
There is no vendor specification for it; everything here is derived from the
open-source xLights driver (``xLightsSequencer/xLights``,
``src-core/controllers/HinksPix.{h,cpp}``, read 2026-09-17), which is the de
facto reference implementation. See ``docs/design/hinkspix_integration.md`` §2.

Two things a reader should know before trusting this module:

* **The encoder tables are copied from xLights, not documented by the vendor.**
  ``EncodeStringPortProtocol`` / ``EncodeColorOrder`` / ``EncodeBrightness`` /
  ``EncodeGamma`` are reproduced verbatim below. If a port comes up with wrong
  colours or brightness, suspect these first and confirm one port end-to-end on
  the bench before trusting a bulk push (design doc §8.10).
* **Firmware below MCPU 151 cannot accept network firmware upload.** That gate
  is xLights' ``FirmwareSupportsUpload()`` and it is reproduced here because a
  unit below it also can't be managed the usual way — the operator's own unit
  shipped at MS_149 and hangs on its auto-update check at every boot.

This module is deliberately transport-only: no Flask, no global state. It is
unit-testable against a stub HTTP layer.
"""

import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("slyled.hinkspix")

DEFAULT_TIMEOUT = 6

# ── CGI endpoints (HinksPix.h) ───────────────────────────────────────────────
CGI_BOARD_INFO = "/XLights_BoardInfo.cgi"
CGI_POST_DATA = "/Xlights_PostData.cgi"
CGI_PORT_CONFIG = "/Xlights_Board_Port_Config.cgi"
CGI_DATA_MODE = "/Xlights_Data_Mode.cgi"
CGI_E131_DATA = "/GetE131Data.cgi"
CGI_INFO = "/GetInfo.cgi"
CGI_UNPACK = "/Xlights_UnPack_Config.cgi"

# ── Encoder tables (verbatim from HinksPix.cpp) ──────────────────────────────
PIXEL_PROTOCOLS = {
    "ws2811": 1, "ws2812": 2, "ws2812b": 3, "ws2813": 4,
    "ws2801": 5, "tls3001": 6, "apa102": 7,
}
COLOR_ORDERS = {
    "rgb": 0, "rbg": 1, "grb": 2, "gbr": 3, "brg": 4, "bgr": 5,
    "rgbw": 6, "wrgb": 7,
}
# The controller only accepts these brightness steps.
BRIGHTNESS_STEPS = (15, 20, 30, 40, 50, 60, 70, 80, 90, 100)
# BD1..BD5 expansion board type codes.
EXPANSION_TYPES = {"S": "Local_SPI", "L": "Long_Range", "A": "Local_AC",
                   "N": "Not_Present"}

# Minimum main-CPU version that accepts network firmware/file upload.
MIN_MCPU_UPLOAD = 151        # HinksPix PRO (MS_* / V1-V2, 48 ports)
MIN_MCPU_UPLOAD_V3 = 129     # PRO 80 / hardware V3 (GM_* GigaDevice)

MAX_PORTS = 48
PIXELS_PER_UNIVERSE = 170     # 510 of 512 channels; the xLights convention


class HinksPixError(RuntimeError):
    """Raised when the controller rejects a request or is unreachable."""


def encode_protocol(name):
    """Pixel protocol name -> controller code. Unknown falls back to ws2811,
    matching xLights' post-assert default."""
    return PIXEL_PROTOCOLS.get(str(name or "").lower(), 1)


def encode_color_order(name):
    """Colour-order name -> controller code. Unknown falls back to RGB."""
    return COLOR_ORDERS.get(str(name or "").lower(), 0)


def encode_brightness(value):
    """Round to a brightness step the controller accepts.

    Mirrors ``EncodeBrightness``: truncate to the 10s, and anything under 20
    clamps to 15 (the lowest supported step), not to 0.
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 100
    v = max(0, min(100, v))
    stepped = (v // 10) * 10
    return 15 if stepped < 20 else stepped


def encode_gamma(value):
    """Gamma -> 1..4 integer (``EncodeGamma``)."""
    try:
        g = int(value)
    except (TypeError, ValueError):
        return 1
    if g < 1:
        return 1
    return 4 if g > 4 else g


def encode_direction(value):
    """Direction -> 0 forward / 1 reverse (``EncodeDirection``)."""
    if isinstance(value, str):
        return 1 if value.lower() == "reverse" else 0
    return 1 if value else 0


def supports_upload(mcpu_version, hardware_v3=False):
    """True when this main-CPU version accepts network file/firmware upload.

    Below the gate the controller must be updated over SD card first; xLights
    refuses with "CPU Firmware is too old (v%d)" and so do we, rather than
    starting an upload that will be rejected.
    """
    try:
        v = int(mcpu_version)
    except (TypeError, ValueError):
        return False
    return v >= (MIN_MCPU_UPLOAD_V3 if hardware_v3 else MIN_MCPU_UPLOAD)


def parse_version(value):
    """``"MS_149"`` / ``"WF_112"`` -> ``149`` / ``112``; None when unparseable.

    xLights takes the numeric tail after a 3-character prefix.
    """
    if not value:
        return None
    s = str(value)
    digits = ""
    for ch in reversed(s):
        if ch.isdigit():
            digits = ch + digits
        else:
            break
    return int(digits) if digits else None


# ── Transport ────────────────────────────────────────────────────────────────

def _get(ip, path, timeout=DEFAULT_TIMEOUT, opener=None):
    url = f"http://{ip}{path}"
    try:
        fetch = opener or urllib.request.urlopen
        with fetch(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HinksPixError(f"GET {path} failed: {exc}") from exc


def _post_cmd(ip, payload, timeout=DEFAULT_TIMEOUT, opener=None):
    """POST a JSON command. The body is the literal string ``DATA: {json}``.

    The controller signals success by including "OK" in the response body — it
    does not use HTTP status codes for this.
    """
    body = ("DATA: " + json.dumps(payload, separators=(",", ":"))).encode("utf-8")
    url = f"http://{ip}{CGI_POST_DATA}"
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        fetch = opener or urllib.request.urlopen
        with fetch(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HinksPixError(f"POST {payload.get('CMD')} failed: {exc}") from exc
    if "OK" not in text.upper():
        raise HinksPixError(f"{payload.get('CMD')} rejected: {text[:200]!r}")
    return text


def probe(ip, timeout=DEFAULT_TIMEOUT, opener=None):
    """Identify a controller. Returns a normalised dict, or raises.

    Raw fields come straight off ``XLights_BoardInfo.cgi``; the derived ones
    are what the rest of SlyLED should read.
    """
    raw = _get(ip, CGI_BOARD_INFO, timeout=timeout, opener=opener)
    try:
        info = json.loads(raw)
    except ValueError as exc:
        raise HinksPixError(f"board info was not JSON: {raw[:120]!r}") from exc
    if info.get("Controller") not in ("H", "E"):
        raise HinksPixError(f"not a HinksPix/EasyLights controller: {raw[:120]!r}")

    mcpu = parse_version(info.get("MCPU"))
    hardware_v3 = info.get("Type") == "8"
    return {
        "model": "HinksPix PRO" if info.get("Controller") == "H" else "EasyLights",
        "controller": info.get("Controller"),
        "type": info.get("Type"),
        "hardwareV3": hardware_v3,
        "mcpu": mcpu,
        "mcpuRaw": info.get("MCPU"),
        "pcpu": info.get("PCPU"),
        "ecpu": info.get("ECPU"),
        "web": info.get("WEB"),
        "maxU": int(info.get("MaxU") or 0),
        "numU": int(info.get("NumU") or 0),
        "boards": {k: EXPANSION_TYPES.get(info.get(k), info.get(k))
                   for k in ("BD1", "BD2", "BD3", "BD4", "BD5") if k in info},
        "uploadSupported": supports_upload(mcpu, hardware_v3),
        "raw": info,
    }


def push_data_mode(ip, protocol, dmx_out=None, timeout=DEFAULT_TIMEOUT, opener=None):
    """Set the input protocol, and optionally the J3 DMX-512 output bridge.

    ``protocol`` is ``"e131"`` / ``"artnet"`` / ``"ddp"``. ``dmx_out`` is
    ``{"enabled": bool, "universe": int}`` — when enabled, that universe is
    bridged to the 3-pin DMX output.
    """
    mode = {"e131": "E131", "sacn": "E131", "artnet": "ARTNET",
            "ddp": "DDP"}.get(str(protocol or "").lower())
    if mode is None:
        raise HinksPixError(f"unsupported input protocol {protocol!r}")
    cmd = {"CMD": "DATA_MODE", "MODE": mode}
    if dmx_out and dmx_out.get("enabled"):
        uni = int(dmx_out.get("universe") or 0)
        if uni < 1:
            raise HinksPixError("dmxOut enabled but no universe set")
        cmd.update({"DMX_ACTIVE": 1, "DMX_UNIV": uni,
                    "DMX_START": 1, "DMX_CHAN_CNT": 512,
                    "DDP_DMX_ACTIVE": 0, "DDP_DMX_START": 1,
                    "DDP_DMX_CHAN_CNT": 512})
    else:
        cmd.update({"DMX_ACTIVE": 0, "DDP_DMX_ACTIVE": 0})
    return _post_cmd(ip, cmd, timeout=timeout, opener=opener)


def build_universe_rows(spans, max_universes):
    """Build the ``E131`` input-universe table rows.

    Each row is ``"index,universe,numOfChan,1,hinksStart,hinksEnd"``. Rows the
    map doesn't use are zeroed the way xLights does: up to ``max_universes``
    they carry ``"index,index,0,1,0,0"``, and beyond that ``"0,0,0,0,0,0"``.
    Returns a list of row strings (caller chunks them into blocks of 6).
    """
    rows = []
    for i, span in enumerate(spans):
        rows.append("{},{},{},1,{},{}".format(
            i, span["universe"], span["channels"],
            span["absStart"], span["absStart"] + span["channels"] - 1))
    for i in range(len(spans), max_universes):
        rows.append(f"{i},{i},0,1,0,0")
    return rows


def push_input_universes(ip, spans, max_universes, timeout=DEFAULT_TIMEOUT,
                         opener=None):
    """Push the input-universe table in blocks of 6, then the total count."""
    rows = build_universe_rows(spans, max_universes)
    for blk in range((len(rows) + 5) // 6):
        chunk = rows[blk * 6:blk * 6 + 6]
        _post_cmd(ip, {"CMD": "E131", "BLK": str(blk),
                       "LIST": [{"V": r} for r in chunk]},
                  timeout=timeout, opener=opener)
    return _post_cmd(ip, {"CMD": "BD_INFO", "NumU": str(len(spans))},
                     timeout=timeout, opener=opener)


def build_port_rows(ports):
    """Build ``PCONFIG`` rows for one 16-port board.

    Row order is
    ``output,protocol,startChan,pixels,endChan,direction,colorOrder,nullPixel,brightness,gamma``.
    """
    rows = []
    for p in ports:
        pixels = int(p.get("leds") or 0)
        start = int(p.get("startChannel") or 1)
        rows.append("{},{},{},{},{},{},{},{},{},{}".format(
            int(p.get("port") or 0),
            encode_protocol(p.get("protocol")) if p.get("enabled", True) else 0,
            start, pixels, start + pixels * 3 - 1 if pixels else start,
            encode_direction(p.get("direction")),
            encode_color_order(p.get("colorOrder")),
            int(p.get("nullPixels") or 0),
            encode_brightness(p.get("brightness", 100)),
            encode_gamma(p.get("gamma", 1)),
        ))
    return rows


def push_port_config(ip, board, ports, timeout=DEFAULT_TIMEOUT, opener=None):
    """Push one board's 16-port configuration."""
    return _post_cmd(ip, {"CMD": "PCONFIG", "BOARD": str(board),
                          "LIST": [{"V": r} for r in build_port_rows(ports)]},
                     timeout=timeout, opener=opener)


def op_mode_ethernet(ip, timeout=DEFAULT_TIMEOUT, opener=None):
    """Put the controller into live-Ethernet mode. It reboots and does not
    reply, so a transport error here is expected and not treated as failure."""
    try:
        _post_cmd(ip, {"CMD": "OP_MODE", "MODE": "ETHERNET"},
                  timeout=timeout, opener=opener)
    except HinksPixError as exc:
        log.info("OP_MODE ETHERNET sent to %s (no reply expected): %s", ip, exc)
    return True


def readback(ip, timeout=DEFAULT_TIMEOUT, opener=None):
    """Read the controller's own view of its config, for a side-by-side verify.

    Each value is returned raw — these endpoints emit comma-separated text
    rather than JSON, and the key set is not documented (design doc §8.7), so
    the SPA renders them as-is rather than parsing speculatively.
    """
    out = {}
    for key, path in (("boardInfo", CGI_BOARD_INFO),
                      ("e131", CGI_E131_DATA),
                      ("portConfig", CGI_PORT_CONFIG),
                      ("info", CGI_INFO)):
        try:
            out[key] = _get(ip, path, timeout=timeout, opener=opener)
        except HinksPixError as exc:
            out[key] = None
            out.setdefault("errors", {})[key] = str(exc)
    return out
