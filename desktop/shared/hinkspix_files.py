"""hinkspix_files.py — on-SD file formats for HinksPix standalone playback (#941).

Pure writers for the four file types a HinksPix PRO reads from its SD card when
running a show with the orchestrator switched off:

  ``<NAME>.hseq``   frame data (336-byte header + raw frames)
  ``<NAME>.au``     Sun AU audio (optional)
  ``<NAME>.ply``    playlist — which sequences play, in order
  ``<DAY>.sched``   schedule — when that playlist plays, per weekday

None of this is vendor-documented. Every offset, field and text form is taken
from the xLights exporter (``src-ui-wx/controllers/HinksPixExportDialog.{h,cpp}``,
read 2026-09-17), which in turn notes that the format was copied from Joe
Hinkle's HSA 2.0 JavaScript. See ``docs/design/hinkspix_integration.md`` §2.3.

Two constraints are worth knowing before designing UI on top of this:

* **Schedules cannot span midnight.** The controller validates ``end > start``
  within one day, so an 8pm-1am window must be split across two weekday rows.
* **Sequence names are uppercase alphanumeric, max 20 chars, and unique.**
  Anything else is rejected by the controller.

No device I/O here — that's ``hinkspix_tcp.py``. This module is unit-testable
with nothing but a byte buffer.
"""

import math
import re

HSEQ_HEADER_LEN = 336
HSEQ_FORMAT_VERSION = 3
AU_HEADER_LEN = 24
MAX_SEQ_NAME = 20

# The controller accepts only these step times (ms/frame).
VALID_STEP_MS = (25, 50)

DAYS = ("SUNDAY", "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY",
        "SATURDAY")

# u16 numFrames at offset 334 caps a sequence at 65535 frames — 27.3 min at
# 25 ms. The u32 at offset 20 carries the same value; which one the firmware
# reads is a bench question (design doc §8.4), so we honour the smaller.
MAX_FRAMES = 0xFFFF


class HinksPixFileError(ValueError):
    """Raised when content can't be represented in an on-SD format."""


def short_name(name, taken=()):
    """Controller-safe sequence name: uppercase alphanumerics, <= 20 chars.

    Mirrors xLights' ``createUniqueShortName`` including its collision suffix,
    so a show exported by either tool lands on the same filenames.
    """
    base = re.sub(r"[^A-Za-z0-9]", "", str(name or "")).upper()[:MAX_SEQ_NAME]
    if not base:
        base = "SEQ"
    if base not in taken:
        return base
    index = 1
    candidate = base
    while candidate in taken:
        suffix = str(index)
        trimmed = base[:MAX_SEQ_NAME - len(suffix)]
        candidate = trimmed + suffix
        index += 1
    return candidate


def fat_datetime_word(dt):
    """Pack a datetime into the FAT date/time dword the close packet carries."""
    year = max(1980, dt.year)
    date = ((year - 1980) << 9) | (dt.month << 5) | dt.day
    time_ = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    return ((date << 16) | time_) & 0xFFFFFFFF


def _u16(buf, off, val):
    buf[off] = val & 0xFF
    buf[off + 1] = (val >> 8) & 0xFF


def _u32(buf, off, val):
    buf[off] = val & 0xFF
    buf[off + 1] = (val >> 8) & 0xFF
    buf[off + 2] = (val >> 16) & 0xFF
    buf[off + 3] = (val >> 24) & 0xFF


def hseq_header(num_frames, channel_count, step_ms, master_ip,
                original_channel_count=None):
    """Build the 336-byte .hseq header.

    Offsets are load-bearing and undocumented; each is annotated with the
    xLights line it came from so a future reader can re-derive them.
    """
    if step_ms not in VALID_STEP_MS:
        raise HinksPixFileError(
            f"step time {step_ms}ms unsupported — the controller accepts "
            f"{VALID_STEP_MS} only")
    if num_frames > MAX_FRAMES:
        raise HinksPixFileError(
            f"{num_frames} frames exceeds the {MAX_FRAMES}-frame header field "
            f"({MAX_FRAMES * step_ms / 60000:.1f} min at {step_ms}ms) — split "
            f"the timeline or use a 50ms step")
    if num_frames < 0 or channel_count <= 0:
        raise HinksPixFileError("num_frames and channel_count must be positive")

    h = bytearray(HSEQ_HEADER_LEN)
    h[0:4] = b"HSEQ"
    h[4] = HSEQ_FORMAT_VERSION
    h[9] = 0                                   # slave controller count
    _u16(h, 16, (44100 * step_ms) // 1000)     # 25ms -> 1102, 50ms -> 2205
    _u32(h, 20, num_frames)
    _u32(h, 24, channel_count)                 # total across all controllers
    ip = str(master_ip or "").encode("ascii", "ignore")[:38]
    h[28:28 + len(ip)] = ip                    # NUL-terminated master IP
    _u16(h, 68, channel_count)                 # master's own channel count
    # 72..219 are slave blocks — zero, we never emit a master/slave set.
    h[320:324] = b"PSEQ"
    h[324] = 0x40
    h[327] = 1
    h[328] = 28
    _u16(h, 330, original_channel_count
         if original_channel_count is not None else channel_count)
    _u16(h, 334, num_frames)
    return bytes(h)


def write_hseq(fp, frames, channel_count, step_ms, master_ip,
               original_channel_count=None):
    """Stream a .hseq to a binary file object.

    ``frames`` is any iterable of per-frame byte buffers, each exactly
    ``channel_count`` long. Frames are written raw with no padding, so the file
    is ``336 + frames * channel_count`` bytes. Returns that total.

    Takes an iterable rather than a list so a long show can be generated lazily
    — a 27-minute 8k-channel sequence is ~500 MB and must not be built in RAM.
    """
    frames = list(frames) if not hasattr(frames, "__len__") else frames
    n = len(frames)
    fp.write(hseq_header(n, channel_count, step_ms, master_ip,
                         original_channel_count))
    written = HSEQ_HEADER_LEN
    for i, f in enumerate(frames):
        if len(f) != channel_count:
            raise HinksPixFileError(
                f"frame {i} is {len(f)} bytes, expected {channel_count}")
        fp.write(f)
        written += len(f)
    return written


def write_au(fp, pcm_stereo_int16, sample_rate=44100):
    """Write a Sun AU file (16-bit PCM stereo).

    Note the magic is written LITTLE-endian (bytes ``64 6e 73 2e``), which is
    what xLights does and therefore what the controller expects — a
    conventional big-endian AU writer produces a file it will not play.
    """
    data = bytes(pcm_stereo_int16)
    h = bytearray(AU_HEADER_LEN)
    _u32(h, 0, 0x2E736E64)      # ".snd", little-endian on the wire
    _u32(h, 4, AU_HEADER_LEN)   # data offset
    _u32(h, 8, len(data))
    _u32(h, 12, 3)              # encoding 3 = 16-bit linear PCM
    _u32(h, 16, sample_rate)
    _u32(h, 20, 2)              # channels
    fp.write(bytes(h))
    fp.write(data)
    return AU_HEADER_LEN + len(data)


def playlist_text(items):
    """Serialise a playlist. ``items`` is [{"hseq": "NAME.hseq", "au": "X.au"}].

    ``D`` is a per-item delay; xLights hard-codes 2 and notes it may be
    settable, so we match rather than experiment on someone's controller.
    """
    parts = []
    for it in items:
        hseq = it.get("hseq") or ""
        au = it.get("au") or "NONE"
        parts.append('{"H":"%s","A":"%s","D":2}' % (hseq, au))
    return "[" + ",".join(parts) + "]"


def playlist_filename(name):
    return f"{short_name(name)}.ply"


def validate_schedule_row(row):
    """Return an error string, or None when the row is acceptable.

    Mirrors xLights' ``ScheduleItem::isValid`` exactly, including the rule that
    an end time must be later the SAME day — the controller has no concept of a
    window crossing midnight.
    """
    try:
        sh, sm = _hhmm(row.get("start"))
        eh, em = _hhmm(row.get("end"))
    except (TypeError, ValueError) as exc:
        return f"unparseable time: {exc}"
    for label, v, lo, hi in (("start hour", sh, 0, 23), ("start minute", sm, 0, 59),
                             ("end hour", eh, 0, 23), ("end minute", em, 0, 59)):
        if not lo <= v <= hi:
            return f"{label} {v} outside {lo}-{hi}"
    if eh < sh:
        return "end hour is before start hour"
    if eh == sh and em <= sm:
        return "end minute is not after start minute"
    return None


def _hhmm(value):
    """Accept "HH:MM", "HHMM" or (h, m)."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return int(value[0]), int(value[1])
    s = str(value).strip()
    if ":" in s:
        h, m = s.split(":", 1)
        return int(h), int(m)
    if len(s) == 4 and s.isdigit():
        return int(s[:2]), int(s[2:])
    raise ValueError(f"expected HH:MM, got {value!r}")


def schedule_text(rows, playlist_name):
    """Serialise one day's schedule.

    Only enabled rows are written, sorted by start time — matching
    ``Schedule::saveAsFile``. An empty list produces ``[]``, which is how a day
    with no playback is expressed (and how a stale schedule gets cleared).
    """
    out = []
    enabled = [r for r in rows or [] if r.get("enabled", True)]
    for row in enabled:
        err = validate_schedule_row(row)
        if err:
            raise HinksPixFileError(f"invalid schedule row: {err}")
    for row in sorted(enabled, key=lambda r: _hhmm(r.get("start"))):
        sh, sm = _hhmm(row.get("start"))
        eh, em = _hhmm(row.get("end"))
        out.append('{"S":"%02d%02d","E":"%02d%02d","P":"%s.ply","Q":%d}'
                   % (sh, sm, eh, em, short_name(playlist_name),
                      int(row.get("repeat") or 0)))
    return "[" + ",".join(out) + "]"


def schedule_filename(day):
    day = str(day or "").upper()
    if day not in DAYS:
        raise HinksPixFileError(f"{day!r} is not a weekday name {DAYS}")
    return f"{day}.sched"


def frames_for_duration(duration_s, step_ms):
    """Frame count for a timeline, matching the live loop's 40 Hz tick."""
    return int(math.ceil(max(0.0, float(duration_s)) * 1000.0 / step_ms))


def split_overnight(start, end):
    """Split a window that crosses midnight into same-day parts.

    Returns [(start, end)] when the window is already valid, or
    [(start, "23:59"), ("00:00", end)] when it wraps — the caller assigns the
    second part to the following weekday. Offered because "8pm-1am" is a
    thoroughly reasonable thing for an operator to ask for and the controller
    simply cannot express it.
    """
    sh, sm = _hhmm(start)
    eh, em = _hhmm(end)
    if (eh, em) > (sh, sm):
        return [(f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}")]
    return [(f"{sh:02d}:{sm:02d}", "23:59"), ("00:00", f"{eh:02d}:{em:02d}")]
