"""hinkspix_bridge.py — HolidayCoro HinksPix PRO HTTP control surface (#939, #943).

The HinksPix PRO exposes a small JSON-over-CGI API on port 80 with no auth.
There is no vendor specification for it; everything here is derived from the
open-source xLights driver (``xLightsSequencer/xLights``,
``src-core/controllers/HinksPix.{h,cpp}``, read 2026-09-17 and again
2026-09-23), which is the de facto reference implementation. See
``docs/design/hinkspix_integration.md`` §2.

Three things a reader should know before trusting this module:

* **Every call is an HTTP GET with the command in request *headers*.** Not a
  POST, and never a request body. ``HinksPix.cpp:926-975`` and the hardware
  both require this: the same text sent as a POST body comes back
  ``{"CMD":"POST","ERROR":"ERROR"}`` (verified against a real MS_160 unit,
  2026-09-23 — #943 B1). ``_cgi`` is the one place that shape is built.
* **The encoder tables are copied from xLights, not documented by the vendor.**
  ``EncodeStringPortProtocol`` / ``EncodeColorOrder`` / ``EncodeBrightness`` /
  ``EncodeGamma`` are reproduced verbatim below. If a port comes up with wrong
  colours or brightness, suspect these first and confirm one port end-to-end on
  the bench before trusting a bulk push (design doc §8.10).
* **Firmware below MCPU 151 cannot accept network firmware upload.** That gate
  is xLights' ``FirmwareSupportsUpload()`` and it is reproduced here because a
  unit below it also can't be managed the usual way — the operator's own unit
  shipped at MS_149 and hangs on its auto-update check at every boot.

This module is deliberately transport-only: no Flask, no global state, and no
knowledge of SlyLED's port model. What to *send* is ``hinkspix_config.py``;
this module only knows how to put bytes on the wire and read them back.
"""

import gzip
import json
import logging
import time
import urllib.error
import urllib.request

log = logging.getLogger("slyled.hinkspix")

# xLights sets CURLOPT_TIMEOUT to 20 s. A shorter timeout is not enough: an
# EEPROM-writing command that blocks can legitimately take several seconds, and
# the MS_160 genuinely gzips replies it has to assemble (HinksPix.cpp:936, #943
# B19).
DEFAULT_TIMEOUT = 20

# OP_MODE reboots the controller, so it never answers. xLights writes the
# request and gives up after 1 ms (CURLOPT_TIMEOUT_MS, HinksPix.cpp:997). A 1 ms
# *socket* timeout in Python would routinely abort before the request reached
# the wire at all, so this is "don't wait for the reply" rather than "don't
# send": short, and every outcome is swallowed (see fire_and_forget).
REBOOT_TIMEOUT = 2
REBOOT_GAP_S = 0.1            # xLights sleeps 100 ms between the two sends

# ── CGI endpoints (HinksPix.h:198-204) ───────────────────────────────────────
CGI_BOARD_INFO = "/XLights_BoardInfo.cgi"
CGI_POST_DATA = "/Xlights_PostData.cgi"
CGI_PORT_CONFIG = "/Xlights_Board_Port_Config.cgi"
CGI_DATA_MODE = "/Xlights_Data_Mode.cgi"
CGI_E131_DATA = "/GetE131Data.cgi"
CGI_INFO = "/GetInfo.cgi"
CGI_UNPACK = "/Xlights_UnPack_Config.cgi"

# Success is the *quoted* token. A bare `OK` anywhere in the body is not
# enough — the controller's error replies can contain the characters without
# being an acknowledgement (HinksPix.cpp:395,444,459,695,854; #943 B4).
OK_TOKEN = '"OK"'

GZIP_MAGIC = b"\x1f\x8b"

# ── Encoder tables (verbatim from HinksPix.cpp) ──────────────────────────────
PIXEL_PROTOCOLS = {
    "ws2811": 1, "ws2812": 2, "ws2812b": 3, "ws2813": 4,
    "ws2801": 5, "tls3001": 6, "apa102": 7,
}
COLOR_ORDERS = {
    "rgb": 0, "rbg": 1, "grb": 2, "gbr": 3, "brg": 4, "bgr": 5,
    "rgbw": 6, "wrgb": 7,
}
# Colour orders 6 and 7 are 4-channel (RGBW) nodes — see channels_per_pixel.
RGBW_COLOR_ORDERS = (6, 7)

# The controller only accepts these brightness steps.
BRIGHTNESS_STEPS = (15, 20, 30, 40, 50, 60, 70, 80, 90, 100)
# BD1..BD5 expansion board type codes.
EXPANSION_TYPES = {"S": "Local_SPI", "L": "Long_Range", "A": "Local_AC",
                   "N": "Not_Present"}
# Board types that carry pixel ports. A Not_Present (or Local_AC) board gets no
# PCONFIG at all (HinksPix.cpp:670-675, #943 B7).
PIXEL_BOARD_TYPES = ("Local_SPI", "Long_Range")
BOARD_INPUT_PROTOCOLS = ("e131", "sacn", "artnet", "ddp")
BOARD_MODES = {"e131": "E131", "sacn": "E131", "artnet": "ARTNET", "ddp": "DDP"}

# Minimum main-CPU version that accepts network firmware/file upload.
MIN_MCPU_UPLOAD = 151        # HinksPix PRO (MS_* / V1-V2, 48 ports)
MIN_MCPU_UPLOAD_V3 = 129     # PRO 80 / hardware V3 (GM_* GigaDevice)

PORTS_PER_BOARD = 16
MAX_BOARDS = 5
MAX_PORTS = 48               # 3 addressable boards x 16
PIXELS_PER_UNIVERSE = 170     # 510 of 512 channels; the xLights convention
UNIVERSES_PER_BLOCK = 6      # xLights UN_PER — the E131 table is sent 6 rows at a time


class HinksPixError(RuntimeError):
    """Raised when the controller rejects a request or is unreachable."""


# ── Encoders (HinksPix.cpp:1024-1102) ────────────────────────────────────────

def encode_protocol(name):
    """Pixel protocol name -> controller code. Unknown falls back to ws2811,
    matching xLights' post-assert default."""
    return PIXEL_PROTOCOLS.get(str(name or "").lower(), 1)


def encode_color_order(name):
    """Colour-order name -> controller code. Unknown falls back to RGB."""
    return COLOR_ORDERS.get(str(name or "").lower(), 0)


def channels_per_pixel(color_order):
    """3 for RGB nodes, 4 for RGBW/WRGB.

    ``setControllerChannels`` is the only place xLights decides this
    (``HinksPix.cpp:166-173``) and it feeds both the port row's end channel and
    how far the packed controller-absolute channel counter advances. Getting it
    wrong makes every downstream port's start channel short by 25% on an RGBW
    strip (#943 B11).
    """
    code = color_order if isinstance(color_order, int) else encode_color_order(color_order)
    return 4 if code in RGBW_COLOR_ORDERS else 3


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


def supports_unpack(mcpu_version, hardware_v3=False):
    """True when the controller understands the UnPack remap command.

    ``IsUnPackSupported_Hinks`` gates a PRO V1/V2 at MCPU 151 and a PRO V3 at
    129 (``HinksPix.cpp:1966-1985``) — the same thresholds as the file-upload
    gate, but a separate decision in xLights and kept separate here.
    """
    return supports_upload(mcpu_version, hardware_v3)


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

def cgi_headers(data=None, blk=None, row=None):
    """The request headers xLights builds, in a form both urllib and the
    dry-run preview can use.

    xLights appends whole lines to a curl_slist (``"DATA: {json}"``,
    ``"BLK: 0"``), which is the same thing as a header named ``DATA``/``BLK``
    whose value is the rest of the line.
    """
    h = {"Content-type": "text/plain"}
    if row is not None:
        h["ROW"] = str(row)
    if blk is not None:
        h["BLK"] = str(blk)
    if data is not None:
        h["DATA"] = data
    return h


def _decompress(raw):
    """Bytes -> text, gunzipping when the body is gzip.

    The MS_160 gzips replies it has to assemble. xLights never notices because
    it never asks curl to decode, and its JSON parse then fails (HinksPix.cpp
    sets no CURLOPT_ACCEPT_ENCODING — #943 B19). We detect on the magic number
    rather than on a Content-Encoding header, and a body that claims to be gzip
    but isn't decodes as text so the caller reports the real bytes.
    """
    if raw[:2] == GZIP_MAGIC:
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError) as exc:
            log.warning("HinksPix reply looked gzipped but did not decompress: %s", exc)
    return raw.decode("utf-8", errors="replace")


def _cgi(ip, path, data=None, blk=None, row=None, timeout=DEFAULT_TIMEOUT,
         opener=None):
    """One CGI call: HTTP GET, command in the headers, reply as text.

    ``data``/``blk``/``row`` become the ``DATA:``, ``BLK:`` and ``ROW:`` request
    headers. This is the *only* place a request is constructed — every read and
    every write below goes through it, so the transport cannot drift per call
    site (#943 B1).
    """
    url = f"http://{ip}{path}"
    req = urllib.request.Request(url, method="GET")
    for k, v in cgi_headers(data=data, blk=blk, row=row).items():
        req.add_header(k, v)
    try:
        fetch = opener or urllib.request.urlopen
        with fetch(req, timeout=timeout) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HinksPixError(f"GET {path} failed: {exc}") from exc
    return _decompress(raw)


def matches_ok(text):
    """True when a reply is an acknowledgement (quoted ``"OK"``)."""
    return OK_TOKEN in (text or "")


def command(ip, payload, path=CGI_POST_DATA, timeout=DEFAULT_TIMEOUT, opener=None):
    """Send one command and require the quoted ``"OK"`` acknowledgement.

    ``payload`` is a dict, serialised compactly in insertion order, or a
    pre-serialised string. The string form exists for the UnPack reset, whose
    header value is not valid JSON: xLights wraps the object in an extra brace
    pair — ``DATA: {{"BLK":"0",...}}`` (``HinksPix.cpp:487``). Reproducing that
    literally is the point; do not "fix" it into a dict.
    """
    body = payload if isinstance(payload, str) else json.dumps(
        payload, separators=(",", ":"))
    text = _cgi(ip, path, data=body, timeout=timeout, opener=opener)
    if not matches_ok(text):
        name = body[:60]
        raise HinksPixError(f"{name!r} rejected: {text[:200]!r}")
    return text


def fire_and_forget(ip, payload, path=CGI_POST_DATA, timeout=REBOOT_TIMEOUT,
                    gap=REBOOT_GAP_S, sends=2, opener=None):
    """Send a command that reboots the controller, twice, and never wait.

    ``OP_MODE ETHERNET`` puts the controller into live-Ethernet mode and reboots
    it, so there is no reply to wait for. xLights sends it unconditionally after
    every configuration upload and sends it twice 100 ms apart
    (``HinksPix.cpp:1494-1499``) — it is a fire-and-forget command, not an
    optional extra, and a config that is written but never loaded looks exactly
    like success in the UI (#943 B8).

    Returns a list of ``{"sent": bool, "err": str}``, one per attempt, so a
    caller can report that the reboot did not leave the machine while still not
    failing the upload over it.
    """
    body = payload if isinstance(payload, str) else json.dumps(
        payload, separators=(",", ":"))
    out = []
    for i in range(sends):
        if i:
            time.sleep(gap)
        try:
            _cgi(ip, path, data=body, timeout=timeout, opener=opener)
            out.append({"sent": True, "err": ""})
        except HinksPixError as exc:
            # Expected: the controller reboots mid-request and drops the
            # connection. Logged, never raised.
            log.info("fire-and-forget to %s did not complete (normal for a "
                     "reboot command): %s", ip, exc)
            out.append({"sent": False, "err": str(exc)})
    return out


OP_MODE_ETHERNET = {"CMD": "OP_MODE", "MODE": "ETHERNET"}
UNPACK_RESET = '{"BLK":"0","NUM":"0","LEFT":"0","LIST":[]}'


def op_mode_ethernet(ip, timeout=REBOOT_TIMEOUT, opener=None):
    """Switch the controller to live-Ethernet mode (it reboots, no reply)."""
    return fire_and_forget(ip, OP_MODE_ETHERNET, timeout=timeout, opener=opener)


# ── Device reads ─────────────────────────────────────────────────────────────

def read_board_info(ip, timeout=DEFAULT_TIMEOUT, opener=None):
    """Raw ``XLights_BoardInfo.cgi`` JSON — boards, versions, universe count."""
    text = _cgi(ip, CGI_BOARD_INFO, timeout=timeout, opener=opener)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise HinksPixError(f"board info was not JSON: {text[:120]!r}") from exc


def read_data_mode(ip, blk=0, timeout=DEFAULT_TIMEOUT, opener=None):
    """The controller's current input mode + DMX-out bridge.

    ``BLK: 0`` is what selects the main board; without it the endpoint answers
    for its default block (``HinksPix.cpp:333``, #943 B3). The reply's key set
    is not documented — ``MODE`` is read when present and left None when not.
    """
    text = _cgi(ip, CGI_DATA_MODE, blk=blk, timeout=timeout, opener=opener)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise HinksPixError(f"data mode was not JSON: {text[:120]!r}") from exc
    if "CMD" not in data:
        raise HinksPixError(f"data mode reply had no CMD: {text[:120]!r}")
    return data


def read_board_ports(ip, board, timeout=DEFAULT_TIMEOUT, opener=None):
    """One 16-port board's configuration, as row strings.

    ``board`` is the 0-based ``BOARD`` value; the device selects the block with
    ``BLK: <board>``. On this hardware ``BLK n`` answers for BD(n+1), so board 1
    is ports 17-32 (#943 B2 — the operator's garage-eaves port is 17, BLK 1).
    Exactly 16 rows must come back; anything else means the reply was for the
    wrong board and is rejected rather than silently padding.
    """
    text = _cgi(ip, CGI_PORT_CONFIG, blk=board, timeout=timeout, opener=opener)
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise HinksPixError(f"port config was not JSON: {text[:120]!r}") from exc
    rows = [(r or {}).get("V") for r in (data.get("LIST") or [])]
    if len(rows) != PORTS_PER_BOARD:
        raise HinksPixError(
            f"board {board} returned {len(rows)} rows, expected "
            f"{PORTS_PER_BOARD} — the reply is not for this board")
    return rows


def read_e131_text(ip, row_index, timeout=DEFAULT_TIMEOUT, opener=None):
    """The input-universe table via ``GetE131Data.cgi`` with a ``ROW:`` header.

    Faithful to ``GetControllerE131Data`` (``HinksPix.cpp:1517-1520``) — which
    xLights **declares and never calls**, so the ROW semantics are unverified on
    hardware and this is a read-only diagnostic, never part of the write path.
    It is not used to decide whether a push succeeded.
    """
    return _cgi(ip, CGI_E131_DATA, row=row_index, timeout=timeout, opener=opener)


def read_info_row(ip, row_index, data=None, timeout=DEFAULT_TIMEOUT, opener=None):
    """``GetInfo.cgi`` with a ``ROW:`` header — the EasyLights path.

    Not used for a HinksPix PRO (``HinksPix.cpp:1511-1514`` reaches it only for
    the EasyLights upload sequence; #943 B20). Kept so the EasyLights branch is
    not a rewrite when it is needed.
    """
    return _cgi(ip, CGI_INFO, data=data, row=row_index, timeout=timeout,
                opener=opener)


def probe(ip, timeout=DEFAULT_TIMEOUT, opener=None):
    """Identify a controller. Returns a normalised dict, or raises.

    Raw fields come straight off ``XLights_BoardInfo.cgi``; the derived ones
    are what the rest of SlyLED should read.
    """
    raw = _cgi(ip, CGI_BOARD_INFO, timeout=timeout, opener=opener)
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
        "unpackSupported": supports_unpack(mcpu, hardware_v3),
        "raw": info,
    }


# ── Device decode (reply text -> structured rows) ────────────────────────────
#
# The write path lives in `hinkspix_config.py`; these parse what the controller
# hands back so the two can be compared. Both directions use the same field
# order, which is the point: port rows are
#   output,protocol,startChan,pixels,endChan,direction,colorOrder,nullPixel,brightness,gamma
# (HinksPixOutput::SetConfig / BuildCommand, HinksPix.cpp:127-158) and universe
# rows are index,universe,numOfChan,1,hinksStart,hinksEnd
# (HinksPixInputUniverse::BuildCommand, HinksPix.cpp:266-272).

PORT_FIELDS = ("output", "protocol", "start", "pixels", "end", "direction",
               "color_order", "null_pixels", "brightness", "gamma")
UNIVERSE_FIELDS = ("index", "universe", "channels", "flag", "start", "end")


def _split_rows(text):
    """``"a,b,c"`` -> ``["a", "b", "c"]``; '' -> []."""
    return [f.strip() for f in str(text or "").split(",")]


def parse_port_row(text):
    """One PCONFIG row -> dict, or None when it is not a row.

    A row is 10 comma-separated fields. Field 1 may be the literal string
    ``undefined``, which xLights reads as protocol 0 (``HinksPix.cpp:137-141``)
    — and note that a *factory* port reads protocol 0 **with** a non-zero pixel
    count, so 0 does not mean "off" (#943 B10).
    """
    f = _split_rows(text)
    if len(f) != len(PORT_FIELDS):
        return None

    def num(i):
        try:
            return int(f[i])
        except ValueError:
            return None

    out = {k: num(i) for i, k in enumerate(PORT_FIELDS)}
    if f[1] == "undefined":
        out["protocol"] = 0
    return out if out["output"] is not None else None


def parse_port_rows(rows):
    """PCONFIG rows -> ``{output: row}``, dropping anything unparseable."""
    out = {}
    for r in rows or ():
        row = parse_port_row(r)
        if row:
            out[row["output"]] = row
    return out


def parse_universe_row(text):
    """One E131 table row -> dict, or None when it is not a row.

    ``index`` is 1-based: the controller's own saved table reads
    ``1,1,300,1,1,300,2,2,300,1,301,600,...`` (see
    ``docs/reference/hinkspix_Current_E131.SYS.example``), and the in-force
    firmware echoes the same shape. Rows past the used count are
    ``index,index,0,1,0,0`` and rows past MaxU are ``0,0,0,0,0,0``.
    """
    f = _split_rows(text)
    if len(f) != len(UNIVERSE_FIELDS):
        return None
    try:
        return {k: int(f[i]) for i, k in enumerate(UNIVERSE_FIELDS)}
    except ValueError:
        return None


def parse_universes(rows):
    """E131 table rows -> ``{index: row}``."""
    out = {}
    for r in rows or ():
        row = parse_universe_row(r)
        if row:
            out[row["index"]] = row
    return out


def parse_serial(data_mode):
    """A ``DATA_MODE`` read reply -> the J3 DMX-out bridge fields.

    The same seven keys the serial write carries (``HinksPixSerial::SetConfig``,
    ``HinksPix.cpp:189-198``). Missing keys are None rather than defaulted, so a
    diff against a device that does not report them stays honest.
    """
    keys = ("DMX_ACTIVE", "DMX_UNIV", "DMX_START", "DMX_CHAN_CNT",
            "DDP_DMX_ACTIVE", "DDP_DMX_START", "DDP_DMX_CHAN_CNT")
    out = {}
    for k in keys:
        try:
            out[k] = int((data_mode or {}).get(k))
        except (TypeError, ValueError):
            out[k] = None
    return out
