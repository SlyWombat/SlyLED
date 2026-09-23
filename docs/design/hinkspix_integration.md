# HinksPix PRO Integration — Design

**Status:** v1 draft — design only, no implementation.
**Date:** 2026-09-17
**Scope:** Make the HolidayCoro HinksPix PRO (V4.1, PN #925) a first-class SlyLED output
device over **wired Ethernet**: live pixel streaming from the orchestrator, plus offline
scheduled playback from the controller's SD card with the orchestrator powered off.
No WiFi work — transport is Ethernet only.
**Prior art:** xLights `src-core/controllers/HinksPix.{h,cpp}` and
`src-ui-wx/controllers/HinksPixExportDialog.{h,cpp}` (read 2026-09-17 via `gh api`).

---

## 1. Findings that shape the design (verified in this repo)

| Finding | Where | Consequence |
|---|---|---|
| No server-side per-pixel producer. Bake emits *segments* (action type + params + startS/durationS + optional ledOffset/ledCount/stringIndex). The SPA computes pixels client-side. | `bake_engine.py:657-713`, `spa/js/emulation.js:608 _emuPixel` | A server renderer is the prerequisite for both live streaming and `.hseq` generation (§3). |
| The SPA renderer is a **stateless, deterministic** approximation of the firmware; the firmware (`main/ChildLED.cpp`) is **stateful** for fire (heat buffer), comet/scanner (frame-to-frame `nscale8` trails) and uses `random8()` for twinkle/sparkle/fire. | `emulation.js:577-701`, `ChildLED.cpp:191-410` | The server renderer must be stateless/seekable. Therefore the **SPA `_emuPixel` semantics are the spec**, not the firmware. Parity is enforced SPA↔server, not against firmware. |
| DMX show playback is a 40 Hz loop computing `elapsed = time.time() - go_epoch`, selecting the active segment per fixture, writing into `DMXUniverse` buffers. LED-only shows short-circuit ("idling"). | `parent_server.py:15565 _dmx_playback_loop`, `:16218 _dmx_playback_single`, `:15622-15630` | The streamed-LED path slots into this loop as a third fixture class. The LED-only early return must learn about streamed LED fixtures. |
| `sACNEngine` sends only dirty universes, **multicast only**, and does **not** apply #853 master brightness at send time. Art-Net does, via `get_data_scaled` + `_get_intensity_offsets`. | `dmx_sacn.py:289` (`uni.get_data()`) vs `dmx_artnet.py:370-375, :503-511` | Pixel universes are all-intensity; sACN must gain the same send-time scaling, and both engines need an "all channels are intensity" shortcut. |
| Art-Net unicast routing already exists (`universeRoutes` → `unicast_targets`). | `parent_server.py:13735 _routes_to_unicast`, `dmx_artnet.py:528` | Either protocol reaches a HinksPix with zero transport code. Device registration should auto-add `universeRoutes` rows. |
| Performer wire structs are sized for 8 strings (`MAX_STR_PER_CHILD`), `ledStart`/`ledEnd` u16. | `parent_server.py:1237, :1338`, `main/Protocol.h:151-174` | A HinksPix must never enter the performer packet path. It is a **streamed** child (`sc=0, strings=[]`); the 8-string wire constant never applies. |
| Fixture `strings` have no length cap; `_validate_fixture_strings` validates shape only; #864/#866 per-string x/y/z + rotation is the geometry model; `resolve_fixture` yields stage-mm pixel positions. | `parent_server.py:3292`, `spatial_engine.py:200-244` | HinksPix-backed fixtures stay `fixtureType:"led"`. No new fixture type. Geometry is stage-mm via the existing editor. |
| Third-party device precedent: WLED is a child `type:"wled"` with HTTP probing in the periodic sweep and `type == "wled"` guards scattered in the server. | `parent_server.py:1432-1450, :2859-2877, :17577` | Reuse the pattern but introduce `_is_performer(child)` so a fourth non-performer type does not multiply `if type == ...` chains. |
| Blueprint split pattern for new server sections. | `orch_state.py`, `orch_firmware.py` | New routes go in `orch_hinkspix.py` reaching state via `orch_state.ps`. |
| JS↔other-runtime parity is gated by a Node-executed corpus test. | `tests/test_fixture_shortcuts.py`, `tests/fixtures/shortcut_corpus/` | Same mechanism gates the SPA↔Python pixel renderer. |
| Wire-parity tests parse struct definitions statically. | `tests/test_mmwave_wire_parity.py` | Same style for the HinksPix TCP structs and the `.hseq` header. |
| Timelines have no audio reference today. | `spa/js/timelines.js` | `.au` upload is optional; playlist items default `"A":"NONE"`. |

---

## 2. The HinksPix control surface (from xLights, exact)

### 2.1 HTTP JSON (port 80, no auth)

> **Corrected under #943.** This section originally documented a POST-with-body transport
> (`POST /Xlights_PostData.cgi`, `DATA: <json>` in the body) and 0-based universe rows. Both
> were wrong; §8b records how the bench found out. Every claim below is read off
> `src-core/controllers/HinksPix.cpp` in the xLights tree and asserted in
> `tests/test_hinkspix_wire.py`.

**Transport: every call is an HTTP GET with the command in request headers.** There is no POST
and no body, anywhere. `Content-type: text/plain` always. Writes carry the JSON in a `DATA`
header; reads select a board with `BLK`; the EasyLights path selects a row with `ROW`. Header
names are spelled here as they appear in `cgi_headers()` (`hinkspix_bridge.py`) — urllib
canonicalises them on the wire (`BLK` → `Blk`), which is harmless.

| Header | Value | Used by |
|--------|-------|---------|
| `Content-type` | `text/plain` | every request |
| `DATA` | `{"CMD":…}` | every write |
| `BLK` | `0`…`4` | board-scoped reads; `BLK n` = BD(n+1), so `BLK: 0` = ports 1-16, `BLK: 1` = ports 17-32 |
| `ROW` | row index | `/GetInfo.cgi` (EasyLights path) and `/GetE131Data.cgi` — see the caveat below |

- **Success** is the **quoted** `"OK"` (`ret.find("\"OK\"")`), not a loose substring. Replies
  the MS_160 has to assemble come back **gzipped** (`1f 8b`); `_decompress` gunzips on the magic
  number and surfaces the raw bytes rather than a decode error when the stream is bogus (#943 B19).
- **Probe:** `GET /XLights_BoardInfo.cgi` → JSON with `Controller` (`"H"` PRO / `"E"` EasyLights),
  `Type` (`"P"` pixel, `"A"` AC, `"8"` PRO 80 = hardware V3), `MaxU` (max input universes:
  145 pre-v111, 402 v111+, 684 PRO 80), `MCPU`/`PCPU`/`ECPU`/`WEB` version strings,
  `BD1..BD5` expansion board types.
- **Command sequence** (`hinkspix_config.build_commands`): read mode → `DATA_MODE` → E131 blocks
  → `BD_INFO` → `PCONFIG` per fitted board → serial `DATA_MODE` → `UnPack` reset → reboot. The
  dry run (`GET …/plan`) and the upload (`POST …/apply`) are built by that one function, so a
  preview cannot drift from what the upload sends.
  - `{"CMD":"DATA_MODE","MODE":"E131"|"ARTNET"|"DDP"}`, plus `DDP_START`/`DDP_CHAN_COUNT` when
    the mode is DDP. Sent **twice**: this one, and a second 7-key serial `DATA_MODE`
    (`DMX_*`/`DDP_DMX_*`) describing the J3 DMX-512 bridge, *after* `PCONFIG` (#943 B9).
    sACN is written as `E131` — the controller has no sACN-specific mode.
  - `{"CMD":"E131","BLK":"<j>","LIST":[{"V":"index,universe,numOfChan,1,hinksStart,hinksEnd"} x6]}`
    — blocks of 6 input-universe rows, **1-based** (`index` 1..`MaxU`); unused rows up to `MaxU`
    are `"index,index,0,1,0,0"`, beyond it `"0,0,0,0,0,0"`. `hinksStart`/`hinksEnd` are the
    **controller-absolute** channel range (`end = start + pixels*chpp - 1`), which is what makes
    each port start where the previous one ended rather than at 1 (#943 B5/B6). Followed by
    `{"CMD":"BD_INFO","NumU":"<used>"}` — the used count, not `MaxU` (#943 B12).
  - `{"CMD":"PCONFIG","BOARD":"<b>","LIST":[{"V":"output,protocol,startChan,pixels,endChan,direction,colorOrder,nullPixel,brightness,gamma"} x16]}`
    — a full 16 rows for **every fitted pixel board** (`Local_SPI` / `Long_Range`), at the
    **controller-absolute port number** (board 2's first row is `17,…`, not `1,…`), and no rows
    at all for a `Not_Present` board (#943 B6/B7). A *factory* port reads protocol `0` **with** a
    pixel count, so "unused" keys off `pixels == 0` (#943 B10). Integer codes come from
    `EncodeColorOrder` / `EncodeStringPortProtocol` / `EncodeBrightness` / `EncodeGamma` in
    `HinksPix.cpp` — copied into `hinkspix_bridge.py` at implementation time.
  - `{"CMD":"OP_MODE","MODE":"ETHERNET"}` — live-data mode; no response, controller reboots.
    Sent **twice, 100 ms apart, fire-and-forget, unconditionally after every upload** (#943 B8).
    A config upload that does not reboot is a config that did not take.
- **Read-back:** `/Xlights_BoardInfo.cgi`, `/Xlights_Data_Mode.cgi` (`BLK`), and
  `/Xlights_Board_Port_Config.cgi` (`BLK`) — the last replies JSON `{"LIST":[{"V":…}]}` and
  **must** carry exactly 16 rows or it is rejected rather than padded
  (`read_board_ports`). `/GetE131Data.cgi` and `/GetInfo.cgi` return comma-separated text.
  xLights never reads the universe table back, so `diff` reports that section as *unread*
  rather than manufacturing `MaxU` phantom differences.
  **Caveat:** xLights *declares* `GetControllerE131Data` and never calls it, so the exact `ROW`
  semantics are unverified on hardware. Reading the table is a read-only diagnostic and is never
  part of the write path, and never used to decide whether a push succeeded.
- **UnPack reset:** `GET /Xlights_UnPack_Config.cgi` with a **double-braced**
  `DATA: {{"BLK":"0","NUM":"0","LEFT":"0","LIST":[]}}` — the extra braces are xLights' and are
  load-bearing. Gated on `MCPU >= 151` (PRO) / `>= 129` (PRO 80 = hardware V3) (#943 B13).
- **Upload gate:** `FirmwareSupportsUpload()` = `MCPU >= 151` (PRO) or `>= 129` (PRO 80 / hardware V3).
- **DDP** (#943 B14) carries its channel range on the mode command and **skips** the E131 table
  and `BD_INFO` entirely; `PCONFIG` is still sent. Not offered for PRO V1/V2 hardware — the API
  rejects it with a V3 hint, and the picker's protocol list is server-supplied.

### 2.2 Raw TCP (port 80, faked HTTP header) — `#pragma pack(2)`

| Struct | Layout | Size |
|---|---|---|
| `Tag_Packet` (file upload, `CMD[0]='F'`) | `char HINK[18]` = `"HINK TCP_CMD  \r\n\r\n"`, `uint8 CMD[4]` = `{'F',0x5a,0xa5,0}`, `u16 TotalSize`, `u16 StructType`, `u16 DataSize`, `uint8 Data[580]` | 608; on the wire only `TotalSize` bytes are sent |
| `StructType` | 0 = first chunk (open temp file, truncate), 1 = append, 2 = close | |
| `Tag_File_Data_Close` (in `Data` when StructType=2) | `char FN[30]`, `u32 DTTM` (FAT date/time word) | 34 → TotalSize 62, but the **DataSize field is 0** (#944 B17) |
| `Tag_Dow_TimePacket` (`CMD[0]='D'`) | HINK[18], CMD[4], `u8 hr, min, sec, dow` (dow 0 = Sunday, **local** time; no date) | 26 |
| `Tag_CMD_Packet` (`CMD[0]=mode`) | HINK[18], CMD[4] | 22; `'G'` = master/standalone, `'H'` = remote/slave |
| SD listing (`GetFileInfoFromSDCard(cmd)`) | header + 4-byte CMD; reply `*NAME.HSEQ,date,time!...` | **no caller in xLights; command letter unknown → bench** |

An upload is one `StructType 0` chunk, then `StructType 1` chunks to the end of the file, then
the close. **Framing follows `TotalSize`, not `DataSize`** — the close declares `DataSize 0`
while still sending its 34-byte body, so a reader that trusts the field mis-frames the packet.
A file whose length is an **exact multiple of 580** gets one extra zero-length `StructType 1`
before the close: xLights tests the previous read's length at the top of its loop
(`HinksPix.cpp:1706-1743`), so the last full chunk never takes the "fully sent" branch, it reads
once more, gets 0, and sends that packet. Without it the file may never be finalised
(#944 B17b). `needs_flush_chunk()`, `build_close()` and the upload loop implement this.

Every command/chunk is ACKed by a line containing `|FOK` (skip until `'|'`, collect printable
chars, stop at non-printable / 5 s timeout). One TCP connection per file.

`DTTM = (((year-1980)<<9 | month<<5 | day) << 16) | (hour<<11 | min<<5 | sec//2)`

### 2.3 On-card file formats

- **`.hseq`** — 336-byte header then raw frames of `channelCount` bytes each (V1 FSEQ body,
  no padding). Header (little-endian): `[0..3]="HSEQ"`, `[4]=3`, `[9]=slaveCount (0)`,
  `[16..17]=u16 44100*stepMs/1000` (25 ms → 1102, 50 ms → 2205), `[20..23]=u32 numFrames`,
  `[24..27]=u32 totalChannels`, `[28..]=master IP as NUL-terminated string`,
  `[68..69]=u16 masterChannels`, `[72..219]` slave blocks (zero), `[320..323]="PSEQ"`,
  `[324]=0x40`, `[327]=1`, `[328]=28`, `[330..331]=u16 originalChannelCount`,
  `[334..335]=u16 numFrames`. Step time must be 25 or 50 ms.
  Sequence names: uppercase alphanumerics, <= 20 chars, unique.
- **`.au`** — Sun AU: `magic 0x2e736e64` (written LE by xLights, bytes `64 6e 73 2e`),
  data offset 24, data size, encoding 3 (16-bit PCM), 44100, 2 channels, interleaved int16.
- **`<Name>.ply`** — JSON array text: `[{"H":"NAME.hseq","A":"NAME.au"|"NONE","D":2},...]`.
- **`<DAY>.sched`** (DAY in SUNDAY..SATURDAY) —
  `[{"S":"HHMM","E":"HHMM","P":"NAME.ply","Q":0},...]` sorted by start, enabled rows only;
  `Q` = repeat count (0 = infinite). Validation: 0-23/0-59, end after start, **no overnight
  ranges** (an 8pm-1am window must be split across two days).

**Deploy sequence used by xLights:** upload `.hseq`/`.au` → upload `.ply` →
upload `<DAY>.sched` x7 → `UploadTimeToController()` → `UploadModeToController('G')`.

---

## 3. Prerequisite: server-side per-pixel renderer

### 3.1 Spec decision

The canonical **SlyLED Effect Spec v1** is the current SPA `_emuPixel` behaviour: a pure
function `pixel(type, params, i, N, elapsedMs) -> (r,g,b)`. Rationale: stateless and seekable
(required for reproducible `.hseq` and for resuming/looping live streams from `go_epoch`),
and already what operators see in the 3D preview. The firmware remains as-is; the deliberate,
documented divergence is that fire/twinkle/sparkle are hash-based deterministic approximations
and comet/scanner use analytic tails instead of frame-to-frame decay.

Integer semantics: `_hsvToRgb`/`_palColor` use `>>8` truncation; other effects use `Math.round`.
The Python port replicates each operation exactly (`//` vs `round()`; note Python `round` is
banker's rounding — use `math.floor(x + 0.5)`), and the corpus test proves it.

### 3.2 Modules

- **`desktop/shared/pixel_renderer.py`** (new, no Flask):
  - `EFFECT_SPEC_VERSION = 1`
  - `render_string(action_type, params, n, elapsed_ms) -> np.ndarray[n,3] uint8` —
    numpy-vectorised over `i = arange(n)`. A scalar Python loop is ~100x too slow for
    40 Hz x 8160 px.
  - `active_segment(segments, string_index, t_s)` — same selection rule as
    `_dmx_playback_loop` / `_ftLedRuntimeUpdate`.
  - `render_fixture(bake_fixture_entry, string_layout, t_s) -> bytes`.
- **`desktop/shared/spa/js/pixel_renderer.js`** (new): `_hsvToRgb`, `_palColor`, `_emuPixel`
  moved verbatim out of `emulation.js` (which keeps calling them), plus
  `var EFFECT_SPEC_VERSION = 1;`. No DOM/THREE references so Node can load it (same trick as
  `fixture_shortcuts.js`). Add to `index.html` before `emulation.js`.
- **`tests/fixtures/pixel_corpus/corpus.json`** — every action type: several param sets x
  N in {1, 2, 7, 50, 170, 600} x elapsed in {0, 1, 33, 999, 1000, 12345, 3600000};
  `expected.json` generated once from Python, checked by both runtimes.
- **`bake_engine.py`**: the 64-segment cap (`:632-639`) becomes "cap only when the fixture's
  child is a performer" — streamed LED fixtures get the DMX treatment (unbounded).

### 3.3 Master brightness for pixel universes

- `DMXUniverse` gains `all_intensity: bool` (set by the pixel output map when it first claims
  a universe). `get_data_scaled` fast-paths `all_intensity` (vectorised LUT over all 512 bytes).
- `sACNEngine._send_universe` mirrors `ArtNetEngine._send_all_universes`
  (`get_global_brightness` + `get_intensity_offsets` + `gamma_lut` constructor hooks, wired in
  `parent_server.py:762-769` alongside Art-Net). `_get_intensity_offsets(uni)` returns the
  sentinel "all" for `all_intensity` universes. Keeps #853's "single point at send time" contract.

---

## 4. First-class device model

### 4.1 Child record (`data/children.json`)

```json
{"id": 7, "type": "hinkspix", "boardType": "HinksPix PRO", "ip": "192.168.10.60",
 "hostname": "HINKSPIX-60", "name": "Roofline controller", "status": 1, "seen": 1758000000,
 "fwVersion": "MAIN:151,POWER:..,WIFI:..,WEB:..",
 "sc": 0, "strings": [],
 "hinks": {"model": "HinksPix PRO", "hardwareV3": false, "mcpu": 151, "maxU": 402,
           "boards": {"BD1": "Local_SPI", "BD2": "Not_Present"},
           "uploadSupported": true,
           "protocol": "sacn",
           "baseUniverse": 100,
           "dmxOut": {"enabled": false, "universe": null},
           "defaults": {"brightness": 100, "gamma": 1},
           "ports": [{"port": 1, "leds": 300, "mm": 5000, "protocol": "ws2811",
                      "colorOrder": "RGB", "direction": 0, "startNulls": 0,
                      "brightness": 100, "gamma": 1, "enabled": true,
                      "smartRemote": null, "smartRemoteType": null}],
           "configPushedAt": 0, "configHash": ""}}
```

**Explicit divergence from the performer model:** `sc=0, strings=[]` guarantees no performer
code path (`_child_led_ranges`, `_load_step_pkt`, sync, `RUNNER_GO`) can address it; the
8-string wire constant never applies. Port inventory is device-level (48 rows on a PRO V1/V2,
80 on a PRO V3 — §4.7); geometry is
fixture-level. A new helper `_is_performer(child)` (`type in (None, "slyled")`) replaces the
ad-hoc `type == "wled"` guards in `_periodic_ping`, `_refresh_bg`, `api_show_start`
(RUNNER_GO fan-out), `/baked/sync`, `api_children_reboot`, `api_show_stop` (`:17577`).
`_probe_child_http(child)` dispatches WLED and HinksPix probes in the 30 s sweep.

### 4.2 Fixtures

HinksPix-backed fixtures are ordinary `fixtureType:"led", type:"linear"` fixtures with
`childId` = the HinksPix child and each string bound to a port:

```json
{"id": 12, "name": "Roofline L", "fixtureType": "led", "type": "linear", "childId": 7,
 "strings": [{"port": 1, "leds": 300, "mm": 5000, "x": 0, "y": 6000, "z": 3200,
              "rotation": [0,0,90]}]}
```

- `_validate_fixture_strings` (`:3292`) gains: `port` (1..`MAX_PORTS` = 80 int, unique across
  all fixtures on that child). `_validate_fixture_ports` (`:3575`) then checks it against the
  controller: within the *model's* port count, on a board that is fitted (since #946 — see §4.7),
  carrying the port table's `leds` and enabled. Device wins for `leds`; `mm` is operator-owned
  geometry.
  `leds`/`mm` are copied from the port table on creation and re-synced when it changes
  (device wins for `leds`; `mm` is operator-owned geometry).
- Default is **one fixture per enabled port** ("Create fixtures from ports" in the device modal);
  the operator can merge ports into a multi-string fixture with the existing editor
  (#864/#866). Strings above 8 are fine: the editor iterates `leds>0` entries,
  `resolve_fixture` iterates all strings.
- Positions: **stage-mm** via the existing layout store + per-string x/y/z/rotation —
  never DMX fractions. `mm` defaults to `leds x 16.67` (60 px/m) with a UI hint.
- `api_timeline_bake`'s child-string enrichment must not run for hinkspix children
  (fixture strings are authoritative; the child has none).

### 4.3 Universe / channel assignment

`pixel_output.PixelOutputMap.build(child)`:
- Enabled ports in port order. Each port starts on a fresh universe:
  `universe = baseUniverse + running`, channel 1; a port of `leds` pixels spans
  `ceil(leds/pixels_per_universe)` universes. One universe is 512 channels, so that is
  **170 px for RGB and 128 px for RGBW/WRGB** (`channels_per_pixel` is 4 for colour orders
  6/7 — #943 B11; the two constants live in `hinkspix_bridge` and `pixel_output` imports them
  rather than keeping a second copy).
  Spans: `(port, universe, uniChannelStart, count_px, absStart)`, where `absStart` is the packed
  controller-absolute channel — exactly the `hinksPixStartChannel` in the `E131` table and the
  frame layout of `.hseq`.
- Optional trailing DMX-out span: if `dmxOut.enabled`, universe `dmxOut.universe` maps to 512
  channels after the pixels ("DMX after pixels", as xLights asserts).
- Collision check (400 on save): against other hinkspix children's ranges and every DMX
  fixture's `dmxUniverse`.
- On save, upsert `dmx_settings.universeRoutes` rows
  `{universe, destination: child.ip, label: "hinkspix:<id>"}` so Art-Net unicast works with no
  operator action; sACN multicast needs nothing.

### 4.4 Device configuration push (explicit, operator-triggered)

Rewritten under #943 into a preview-then-upload pair, then under #945 into a job that snapshots
first and verifies after, so the operator sees the exact requests before anything touches the
device and can put the device back afterwards:

- `GET /api/hinkspix/<cid>/plan` — dry run. Returns the protocol, `maxUniverses`,
  `universesUsed`, the `boards` that will receive `PCONFIG`, the full request list (`kind`,
  `method`, `path`, `headers`), the intended `DeviceConfig` and the `findings` (§4.6). Touches
  nothing.
- `POST /api/hinkspix/<cid>/apply` — sends that same sequence (`hc.build_commands` builds both,
  so they cannot drift). **Asynchronous since #945**: `202` + `state`, or `409` when a finding is
  unacknowledged or another job is already running. `wait: true` runs it to completion and
  answers `200`. Progress is `GET /api/hinkspix/<cid>/apply`; `POST /backups` is refused while a
  job runs.
- `GET /api/hinkspix/<cid>/device-config` — reads `BoardInfo` (`MaxU`), the current `DATA_MODE`
  (`BLK 0`) and the port table of every fitted pixel board, then returns `device` and a `diff`
  against the intended config. The SPA shows "config differs from device" from that diff; an
  unread section is reported as unread (`diffError`), never as a difference.

### 4.5 UX surfaces

- **Setup → Hardware**: row with badge "HinksPix" (new `boardColors` entry), status, firmware,
  buttons Refresh / Configure / Web UI (`http://<ip>/`) / Remove.
- **Device modal** (`hinkspix.js`): the port table the model addresses — one section per board
  (`BD1` … `BDn`), each headed with what that board is, with rows on a `Not_Present` board
  disabled and the reason shown. Columns: enabled, leds, mm, protocol, colour order, skip
  (start nulls), brightness, gamma, and — on Long Range boards only — the receiver's ID
  (`A`-`P`) and type (#946, §4.7). Also: base universe, new-port defaults (brightness, gamma),
  protocol readout, DMX-out toggle + universe, "Create fixtures from ports", "Defaults from
  fixtures", "Probe", mode indicator (Live / Standalone / unknown), "Set clock", and two buttons
  into the wizard below. Since #945 this modal **never writes the controller** — everything that
  touches the device moved to `hinkspix_config.js`; since #946 a save comes back with its
  findings and marks the offending rows.
- **Add device**: `POST /api/children` already tries PING then WLED; add a HinksPix probe
  (`XLights_BoardInfo.cgi`) in the same fall-through. No mDNS/ArtPoll guarantee → manual IP add
  (like WLED) is the supported path; an ArtPoll reply, if the unit answers, folds through the
  existing `_artnet_oneshot_poll` merge with type promotion to `hinkspix`.
- **Layout / 3D / Control**: nothing new — fixtures are `led`. `fixture-types.js led` gets a
  badge chip "HinksPix P<port>" and `panelDetailHtml` shows port numbers.

### 4.6 Configuration management — snapshots, the push job, and verify (#945)

`desktop/shared/spa/js/hinkspix_config.js` is one five-step wizard over
`orch_hinkspix.py`'s config endpoints. It exists because the failure mode that matters here is
not "the request was refused" — it is **the controller was left half-written**, and that state
has no description to reason from. So every push is bracketed by a read of the device.

**Read → Edit → Review → Apply → Verify.** Nothing on the device changes before step 4, and
step 2 edits only SlyLED's copy. The port editor is opened *on top of* the wizard
(`_pushModal()` + `hinksConfigure(cid, nested=true)`), so `closeModal()` returns to the wizard
rather than closing the modal — the editor's own `_hpRender` skips its stack-clear when it was
opened that way.

**Snapshot first, or not at all.** `_config_worker` takes the snapshot before the first write;
if the read fails, nothing is written and the job fails with `backupId: null` ("nothing to
restore"). A snapshot stores the **raw row strings** the controller handed over, replayed
verbatim — re-deriving them from our decoded model would quietly "fix" anything the decoder
misreads, which is the opposite of what a restore is for. Blocks arrive six rows at a time, so
`flatten_blocks()` puts them back into wire order for the summary, the restore and the
post-restore diff.

**What a snapshot cannot hold.** UnPack and SCONFIG have no read CGI in xLights (`HinksPix.cpp`
has no `GetSmartReceiverData`; `UploadUnPack` has no counterpart), so a restore **resets** them
rather than recreating them. This is a documented limitation of the controller, surfaced in
`restore_commands`' warnings before the operator confirms — not hidden. E131 blocks the read
missed are named the same way and left as they are, never blanked.

**A failed push is never resumed.** The sequence stops at the failed request (every later
request would be written against a device in an unknown state) and reports `failedAt` plus the
snapshot id. The way forward is `POST /restore`, not a retry — the wizard's failure panel
carries the Restore button.

**Restore is a job too**, and it snapshots first, so a restore is itself undoable. Its preview
(`GET /restore?backupId=…`) reports the summary, the request count and the warnings the
operator is agreeing to. `BACKUP_KEEP = 10` snapshots per device, newest first.

**Verify is the only evidence.** After the reboot the wizard waits up to `REBOOT_WAIT_S = 90` s
for the device, then reads it back and diffs it against what was sent; the result lands in
`lastVerify` and is shown as "the controller holds what was sent" / "differs from what was
sent", with the differing rows listed. A device that never comes back reports
`unknown: True` — unread is reported as unread, never as agreement.

**"In sync" (`inSync`/`configHash`) means one thing only**: the stored config has not changed
since the last successful push. It is not a claim about the device. A restore clears it
deliberately — a restored device holds a state this orchestrator does not describe.

**Findings.** Every `error` blocks the push outright; every `warn` blocks until the request
names its code in `ack`. The gate is enforced server-side (`_blocking_findings` → `409`), and
the SPA renders the same list from `GET /plan` so the two can never disagree about what is
refused. `code` is a stable slug (`text` may be reworded freely) and `port` marks the row the
finding is about, so #946's per-port guidance can key on both. The split is in place here; the
guidance table itself — per-port channel caps, board-type rules, fixture binding — is #946.

**Poll lifetime.** `_hwPollTick` stops when the wizard is no longer the visible modal
(`_hwOpen()` tests visibility *and* presence, because `closeModal()` only sets
`display:none` and leaves `#modal-body` intact). A poll that outlives its panel is a request
loop nobody can see or switch off.

---

### 4.7 Knowing the controller — model caps, boards, smart receivers (#946)

#945 made a push reversible. #946 makes the editor *correct*: it stops SlyLED offering
configurations the reference client would refuse, and it explains the ones the hardware makes
non-obvious. Three audit items land here — B12 (no `SCONFIG`), B16 (a pixel-protocol list and
per-port channel cap that came from nowhere), B21 (48 ports hard-coded, so a PRO 80 could not
be configured at all).

**The capability table.** `hinkspix_config.CAPABILITIES` holds one row per model, transcribed
verbatim from `hinkspix.xcontroller`:

| key | model | boards / ports | ch per port | universes | pixel protocols | input protocols | receivers |
|---|---|---|---|---|---|---|---|
| `pro_v12` | PRO V1/V2 | 3 / 48 | 2040 | 402 | ws2811 | e131, artnet | 4, 16, 16AC |
| `pro_v3` | PRO V3 (PRO 80) | 5 / 80 | 3072 | 684 | ws2811 | e131, artnet, ddp | 4, 16, 16AC |
| `easylights` | EasyLights Pix16 | 1 / 16 | 2040 | 65 | ws2811, ws2801, tls3001, apa102 | e131, artnet, ddp | — |

`boards` and `max_pixels(order)` are *derived* from the port count and the channel cap rather
than stored a second time, so the table has two numbers per model that can be wrong and the
rest is arithmetic on them. Three field names have to be read rather than one: `Controller`
(`"E"` = EasyLights), `Type` (`"8"` = PRO 80, `HinksPix.cpp:378-382`) and `MaxU` (65/402/684
are distinct) — `caps_key()` takes the most specific witness available, so a child probed before
#946 is still read correctly instead of being assumed to be a PRO V1/V2. `MAX_PORTS = 80` is
the *protocol* ceiling and lives in `hinkspix_bridge`; each module reads it rather than
repeating a 48, which is how the three drifted apart in the first place.

**DDP is offered only where the model lists it.** xLights still accepts `DATA_MODE DDP` on a
PRO V1/V2 (`:378-382`), but the controller does not serve it, and a mode that is written,
accepted and inert is worse than one that is refused (#943 B14). `input_protocols_for()` is the
single source for the picker, the PUT validation and the `input_protocol_unsupported` finding;
`sacn` is appended beside `e131` because it *is* E131 on the wire — one controller mode with
two names, and it is not a row of the caps table.

**Boards are the frame the port table is read in.** `board_of(port)` gives BD1..BDn, and
`board_bank(port)` splits a port the way `CalculateSmartReceivers` does (`HinksPix.cpp:860-867`):
which board, which bank of four outputs, which of those four. The editor groups the port table
by board and labels each group with what that board *is* — a `Local_SPI` board drives pixels
directly, a `Long_Range` board does not, and a `Not_Present` board's rows are rendered disabled
with the reason, which is the operator's first question on opening the editor. A port beyond the
model's boards is refused at write time (`caps.max_pixel_port`); a port within the model but on
a board that is not fitted is *stored* and reported as `port_on_absent_board`, so a board that is
about to be fitted can be laid out first. The device fact stays a finding; only the model limit
blocks.

**Smart receivers.** A Long Range board's 16 outputs are four differential cables of four, and
what sits at the far end is a *receiver* that may pass the signal on — the operator's BD1 is one
of these, so ports 1-16 can only drive pixels through receivers, and before #946 there was no way
to say so. `calculate_smart_receivers(ports)` is a port of `CalculateSmartReceivers`
(`:860-918`); `build_commands` emits one `SCONFIG` per bank on each Long_Range board, addressed
by **0-based** expansion and bank (`"BOARD": "0"`, `"Port4": "0"`), between `BD_INFO` and the
`PCONFIG` writes (`:1437-1439`). The three rules that are easy to get wrong and are therefore
pinned in `tests/test_hinkspix_smart.py`:

- **A 16-port receiver is a group of four ids** starting on a multiple of four; only the id in
  use is placed, the other three are announced so the controller knows the group is there
  (`:885-899`).
- **A 16AC counts start pixels in its own three-channel terms** — `(channels so far / 3) + 1`,
  not the port's node width (`:901-905`). On an RGBW port the two differ from the first
  receiver onward.
- **A bank with no receiver gets no request at all**, not an empty `LIST`:
  `UploadSmartReceiverData` returns before sending when the list is empty (`:835-838`). An empty
  list would be a statement we have no evidence the controller accepts.

The receiver id is stored as the letter the operator reads off the dial (`"A"`-`"P"`), accepted
as either letter or number on input, and the type as xLights' own string (`hinkspix_4`,
`hinkspix_16`, `hinkspix_16ac`) because `CalculateSmartReceivers` switches on the *text*, not on
a code. A receiver with no type is refused rather than defaulted, and `smart_on_non_long_range`
is an **error**, not a warning: xLights' own `CheckSmartReceivers` (`:1948-1963`) refuses the
export, so shipping it would be a config we know cannot work.

**Nothing reads SCONFIG back.** There is no `GetSmartReceiverData` in xLights, so a snapshot
cannot restore receivers and a wrong list is only ever found on the hardware, as dark pixels.
That asymmetry is why the editor explains the rules in the column headers instead of assuming
the operator knows them, and why the receiver columns appear only on Long_Range board groups.

**Findings, and which ones block.** Every code `validate()` can raise, with the level it carries
and why:

| code | level | why that level |
|---|---|---|
| `not_probed`, `no_boards` | error | nothing can be checked; the upload would be written blind |
| `upload_unsupported` | error | the controller would refuse it (MS_151, MS_129 on V3) |
| `port_on_absent_board` | error | the port does not exist or the board is not fitted |
| `port_channels_exceed` | error | more channels than the port carries (`caps`) |
| `protocol_unsupported`, `input_protocol_unsupported` | error | the model does not serve it |
| `smart_on_non_long_range` | error | xLights itself refuses (`:1948-1963`) |
| `fixture_leds_mismatch` | error | the controller would drive pixels the fixture does not have |
| `universes_exceed_maxu`, `universe_overlap`, `channel_overlap` | error | two rows would write each other's pixels (restored/imported tables) |
| `empty_config` | warn | a legitimate choice, but it blanks the output |
| `board_long_range` | warn | guidance, not a fault — and only while *no* receiver is set on the board, because a warning that returns on every push is one nobody reads |
| `port_unbound` | warn | the port is written and never receives data |
| `engine_protocol_mismatch` | warn | frames will not reach the device until the two agree |
| `reboot_required` | warn | every apply reboots; the pixels go dark for up to 90 s |

`firmware_below_101` is **not** implemented, though #946 listed it. It cannot be reached: the
upload gate already blocks every firmware old enough to trip it (MS_151, or MS_129 on a PRO V3),
so the check would be dead code, and a floor above the upload gate would be a number with no
source. xLights has no such gate either — `UploadSmartReceivers` sends to whatever answered the
probe. The reasoning is recorded where the branch would have gone (`hinkspix_config.validate`)
and in the bridge's constants.

**Fixture-derived defaults.** `POST /api/hinkspix/<cid>/defaults-from-fixtures` proposes a port
table from the fixtures already bound to the controller: pixels and length are the **sum** of the
strings on the port (which is what makes a multi-fixture output come out right), the colour order
comes from the declared strip type (`LED_TYPE_COLOR_ORDER`: WS2812B-class → GRB, WS2811 → RGB,
APA102-class → BGR), and everything else — including the receiver, which is a fact about the
wiring rather than the fixture — is left as the port has it. It returns `ports`, `changes`
(what differs, in the operator's units), `unbound` (enabled ports driving nothing) and `caps`,
and **stores nothing**: the rows go into the editor for review. A "fill it in for me" button that
silently rewrote a port the operator never looked at would be the same class of mistake as a push
that silently half-succeeds.

**Provenance gaps to settle on the bench.** The upload gates here are `MIN_MCPU_UPLOAD = 151`
and `MIN_MCPU_UPLOAD_V3 = 129`, cited from `IsUnPackSupported_Hinks` (`HinksPix.cpp:1966-1985`);
#946's text says "MS_152", and no source in `xLights` names 152. The gates are the same
comparison in xLights as the UnPack gate, so 151/129 is the reading with a citation and the
issue text is the one that should give way — but a field note against MS_151 and MS_129 would
settle it. The `Type "8"` = PRO 80 mapping is likewise read from `:378-382` rather than from a
probe of that hardware; the operator's unit is Type `"P"`.

### 4.8 Importing the layout from an xLights show folder (#947)

**Why an import at all.** xLights is where this operator's layout actually lives. Which output
drives which strip, how many pixels are on it and what the model is called were typed into
xLights years ago; retyping them into SlyLED is tedious *and* a chance to get one wrong, and a
wrong port is a dark line on the house on the night. So the two files xLights keeps the layout in
are read, and a port table is **proposed** from them. Nothing is written to the controller, and
nothing is stored, until the operator has seen the diff against what SlyLED already holds and
accepted it. Pushing is still §4.4's job.

`desktop/shared/hinkspix_xlights_import.py` is pure: XML text in, plain dicts out — no Flask, no
sockets, no filesystem. The caller reads the files (path or upload) and hands over their bytes, so
every rule below is testable against captured XML and nothing in the module can touch a controller.

**The two files**, both copied from the operator's real show folder
(`…/SlyMega Art Inc/Projects/Xlights - Home Eves`) into `tests/fixtures/xlights_home_eves/`, which
is the acceptance corpus:

| File | Carries | Read? |
|---|---|---|
| `xlights_networks.xml` | one `<Controller>` per controller, one `<network>` child per universe | yes |
| `xlights_rgbeffects.xml` | one `<model>` per model, with its binding to an output | yes |
| `hinks_export.json` | the xLights *export dialog's* own state — upload-tab controller list, schedule day list, folder picker | **deliberately never** — it carries no layout |

**The networks file, and the one quirk worth writing down.** A controller element looks like

```xml
<Controller Name="Ethernet_" Description="Garage" Vendor="HinksPix" Model="PRO V1/V2"
            IP="192.168.2.10" Protocol="E131" FullxLightsControl="TRUE"
            DefaultBrightnessUnderFullControl="100" DefaultGammaUnderFullControl="1">
  <network ComPort="192.168.2.10" BaudRate="1" NetworkType="E131" MaxChannels="510"/>
```

— and **the E1.31 universe number is in `BaudRate`**, with `ComPort` holding the IP a second time.
That is not a guess: it is what the operator's own file says, and reading `ComPort` as the universe
(or `BaudRate` as a serial rate, as the attribute name invites) would produce a plausible-looking
table bound to the wrong universes. The universes are 1-based and contiguous from the first row,
so `baseUniverse` is the first and the count is the number of rows; a file whose universes are
*not* contiguous is imported from its first universe with a warning naming the gap, because SlyLED
lays universes out contiguously from its base and cannot reproduce holes. `MaxChannels` is the
row width, used only to resolve a channel index back into a universe + channel.
`FullxLightsControl` off means xLights is not driving this controller, so the universes are what it
*would* use rather than what the unit is running — a warning, and a line in the dialog.
`Default*UnderFullControl` become the port table's `defaults` (`hb.encode_brightness` /
`encode_gamma`).

**The models file.** One `<model>` per model, and the attributes that matter:

| In the file | Becomes |
|---|---|
| `name` | the fixture name, and the row label in the dialog |
| `DisplayAs` | which node rule applies (`Single Line`, `Tree`, `Matrix`, `Arches`, `Circle`, `Custom`) |
| `StringType` | channels per node (RGB 3, RGBW 4, single colour 1, any permutation likewise) |
| `parm1` / `parm2` | strings × nodes per string |
| `Controller` | which controller's models this row belongs to |
| `StartChannel` | the universe/channel the model begins at — shown in the diff, never used to bind |
| `<ControllerConnection Protocol Port>` | the output this model drives, and the port's pixel protocol |

**One string per output is the rule that matters most.** `parm1` strings of `parm2` nodes, so a
three-string model takes three *consecutive* outputs of `parm2` pixels each and its total is
`parm1 × parm2`. A port set to the model's *total* would drive three times the channels the strip
has — the expensive kind of wrong — so the per-output number is the one proposed.
`parm3` is **not** interpreted: its meaning differs between model types (for some it groups strands
onto one output, for others it does not), and every model in the operator's file has it at 1;
anything outside `{0, 1}` is a warning that says how the importer read the file rather than a
guess. A pixel protocol with no code in the bridge (`ws2812x`, a strip type the controller does not
serve) is proposed as `ws2811` with a warning naming what was asked for, since a protocol the
upload cannot encode would store a port that cannot be pushed.

**`StartChannel` forms.** All six are resolved, with a cycle guard on the chained ones:
`!Controller:N` (absolute channel within that controller's universe block — the operator's form),
`#U:C`, `#ip:U:C`, a bare integer (xLights' show-wide channel space, walking every controller's
universes in file order), and `>Model:N` / `@Model:N` (after / at another model's end). The channel
position is only ever *shown* — the binding is the `<ControllerConnection Port>`, which is a
statement about wiring rather than about channels.

**Two readings are derived, not captured**, and both say so where they are implemented: the
`CustomModel` text header (the `strings,strands,nodesPerString` header sizes the model, and its
`;`-separated rows are only checked against it), and the chained `>Model:N` / `@Model:N` offsets.
Neither shape appears in the operator's folder, so neither could be checked against real bytes.
Both therefore fail **closed**: anything unreadable comes back with a reason and falls back to
`parm1 × parm2` (or an error naming the channel), so a wrong reading surfaces as a warning or a
refused row the operator can see — never as a plausible-looking port table.

**The routes.**

| Route | Body | Does |
|---|---|---|
| `POST /api/hinkspix/import/xlights` | `{showFolder, cid?, controller?}` — read by the orchestrator, so a Windows path and its `/mnt/…` twin both work — or a multipart form with the two XML files | reads, resolves, proposes. **Stores nothing, opens no socket to the unit** |
| `POST /api/hinkspix/<cid>/import/xlights/accept` | `{proposal, createFixtures}` | writes the port table, then a fixture per accepted model |

The preview returns the proposal (`controller`, `universes`, `hinks`, `fixtures`, `models`,
`warnings`), plus three things the editor draws: `source` (what was read), `diff` — `added` /
`changed` / `kept` / `withdrawn` / `settings` — and `notes` in words. A show with more than one
controller is a 400 carrying `controllers: [names]`, so the dialog offers the names instead of
making the operator retype one the file already gave. `models[]` is the per-row view: a row with an
**error**-level problem comes back `accepted: false`, because a row the port table cannot hold must
not be applied by default (the operator would have to find the one bad row by hand) — warnings
never un-accept a row. Ports beyond the target model's output count are warnings here and errors at
the accept, which is the same ordering as a hand-typed edit: the editor lets you type anything, and
saving is what checks it.

**An import is held to the editor's own rules.** `_apply_config_body` is the PUT's body extracted
into one function; the accept calls *it*, so base-universe range, protocol list, DMX-out,
brightness/gamma encoding, per-port ranges, duplicate outputs, LED counts, start nulls and
smart-receiver checks are the same code on both paths — an import cannot store a config the editor
would have refused, and five refusals leave `child["hinks"]` byte-identical (asserted in
`tests/test_hinkspix_xlights_routes.py`). One duplicate-output check sits in the accept rather than
in the shared validator, because the accept keys its port set by output number and a duplicate
would collapse there before the validator ever saw it; a body naming output 17 twice is refused as
a layout that disagrees with itself rather than resolved by keeping the last.

**What is written, and what is left alone.** Rows the operator accepted, minus rows they unticked,
laid over the ports the controller already has. Ports the proposal never mentions are **kept**, and
so is a port the preview could not accept: an xLights folder describes the models it knows about,
not every output on the unit, and importing a one-model folder into a configured controller must
not clear the other 47 outputs. Unticking means *not written*, not *removed* — for an output the
unit already has, that is leaving it as it was; for a new one, not adding it. A base-universe move
is reported in words on top of the numeric diff, because it renumbers every page below it *and*
rewrites the routes derived from it.

**Fixtures.** One per accepted model, named after it, bound to the outputs `ControllerConnection`
names — and the pixel count is read from the row that was just applied rather than from the
proposal's own copy, so a fixture can never disagree with the output it drives. A name or an output
already taken is **skipped with the reason** rather than renamed or rebound, and creating no
fixture at all is a legitimate outcome the dialog states plainly. The port table and the fixtures
are one transaction: if creating a fixture raises, the config, the fixture list and `_nxt_fix` are
put back, both are saved, and the routes are republished from the config that survived.

**The surface.** "Import from xLights…" sits in the port table (not a top-level tab — the import is
a way to fill *this* controller's table), and opens a modal over it so Cancel gives the table back.
Inside: the folder box (with the two files as a picker, for the case where the browser and the
orchestrator cannot see the same disk), the controller chooser when the show is ambiguous, the
proposal as a table with the diff and the notes above it, a tick per row, the create-fixtures
toggle, and a result screen with one way on — back to the port table, re-read from the server
rather than patched in place. Reading again clears the previous result, so a second read always
presents the new proposal with its Apply button. The dialog only ever talks to the orchestrator:
`tests/test_hinkspix_xlights_spa.py` records every request the panel makes and asserts they are all
`/api/hinkspix/` paths and none of them the unit's address — a dialog that configured the
controller from the browser would be a second, unverified write path.

---

## 5. Live output path

### 5.1 Module `desktop/shared/pixel_output.py`

- `PixelOutputMap` (§4.3) + `fixture_spans(fixture)`.
- `write_fixture_frame(engine, fixture, rgb_bytes)` →
  `engine.get_universe(u).set_channels(start, slice)` per span (marks dirty; the engine thread
  transmits). One call per fixture per tick.
- `to_wire_frame(rgb, pixels, chpp)` widens the renderer's packed RGB to the port's channel
  width — 3 bytes pass through, 4-byte ports get a zero channel inserted per pixel (#943 B11).
  **One thing the bench must confirm:** whether a `wrgb` port wants `R,G,B,0` (the controller
  permutes) or `0,R,G,B` (we permute). Sending canonical RGBW and letting the controller apply
  the colour order is the reading consistent with the 3-channel path — see the docstring.

### 5.2 Playback integration (`parent_server.py`)

- `_dmx_playback_loop` (`:15565`) / `_dmx_playback_single` (`:16218`): collect
  `streamed_led_fixtures` = LED fixtures whose child is `hinkspix` and that have bake segments;
  each tick after DMX writes, `rgb = pixel_renderer.render_fixture(...)` then
  `write_fixture_frame`. Remove the "LED-only show: idling" early return (`:15622-15630`) when
  `streamed_led_fixtures` is non-empty.
- Blackout on stop / `is_final` sweep: zero the pixel universes too
  (`_blackout_unclaimed_fixtures` gains a pixel-universe branch; pixel universes are never claimed).
- Frame pacing: the loop already targets `_dmx_settings.frameRate` (40 Hz = 25 ms, the
  HinksPix-accepted step). Sync uses the same `go_epoch` timebase the DMX path uses.
  Note `api_show_start` sends `RUNNER_GO` with `go_epoch = now+2` while `_show_playback_loop`
  calls `_dmx_playback_single(tid, time.time(), ...)` — see §8.9.

### 5.3 Engine selection

Write to whichever engine is running (`_artnet` else `_sacn`), same as DMX. sACN multicast is
the zero-config default; Art-Net works through the auto-added unicast routes. The device's
`DATA_MODE` is pushed from `_dmx_settings.protocol`, and the SPA warns when the pushed mode
differs from the current protocol.

### 5.4 Live actions and monitor

- `POST /api/children/<cid>/action` for a hinkspix child: instead of `CMD_ACTION`, register
  `{fixtureId/strings, type, params, startedAt}` in a `_live_pixel_actions` dict consumed by a
  lightweight 40 Hz ticker (started on demand, stopped when empty). `action/stop` clears it and
  blacks out the spans. `SET_BRIGHTNESS` is already global (#853 path).
- `/api/fixtures/live`: for streamed LED fixtures, report `effect`/`active` from the ticker or
  the current show segment, and average RGB from `peek_universe`, so the Fixture Monitor tile is
  truthful.
- 3D preview needs no change: `/baked/preview` is keyed by fixture id and the SPA renders with
  the same spec.

---

## 6. Offline scheduled playback

### 6.1 Modules

- **`hinkspix_files.py`** — pure writers: `write_hseq(fp, frames, channel_count, step_ms, master_ip)`,
  `write_au(...)`, `playlist_text(items)`, `schedule_text(day_rows)`,
  `short_name(name, taken)` (uppercase alnum <= 20, unique), `fat_datetime_word(dt)`,
  `validate_schedule(rows)` (mirrors xLights `isValid`).
- **`hinkspix_tcp.py`** — `HinksPixTcp(ip)`: `upload(name, data, mtime, progress_cb)`
  (608-byte `Tag_Packet` chunks of <= 580, `|FOK` per chunk, `StructType` 0/1/2, close packet
  with `DataSize` 0, zero-length flush chunk for exact-multiple files — §2.2, #944 B17/B17b),
  `set_time(now_local)`, `set_mode('G'|'H')`, `list_files(cmd)`; 5 s per-ACK timeout;
  raises `HinksPixError` with the controller's reply text.
- **`hinkspix_bridge.py`** — transport and encoders (§2.1): `cgi_headers`, `command`,
  `fire_and_forget`, `op_mode_ethernet`, `probe`, `read_board_info`, `read_data_mode`,
  `read_board_ports`, `read_e131_text`, `read_info_row`, the `parse_*` row decoders, and the
  encode/support tables (`encode_protocol`, `encode_color_order`, `channels_per_pixel`,
  `encode_brightness`, `encode_gamma`, `encode_direction`, `supports_upload`, `supports_unpack`).
- **`hinkspix_config.py`** — the pure config layer (§2.1's command sequence): `PortRow`,
  `UniverseRow`, `SerialRow`, `DeviceConfig`, `intended_config`, `universe_table` /
  `build_universe_rows` / `universe_blocks`, `build_commands` → `[CgiRequest]`,
  `decode_device_config`, `diff`, `present_boards` / `pixel_boards`,
  `input_protocols_supported`. No Flask, no sockets, no global state — so the plan can be
  asserted without standing up the app.
- **`orch_hinkspix.py`** — Blueprint: device CRUD/probe, `plan`/`apply`/`device-config`,
  deploy job, schedule CRUD, mode switch, inventory.

### 6.2 Data — `data/hinkspix_deploy.json`

```json
{"7": {"playlistName": "SHOW",
       "items": [{"timelineId": 3}, {"timelineId": 5}],
       "schedule": [{"days": ["MON","TUE","WED","THU","FRI","SAT","SUN"],
                     "start": "20:00", "end": "23:00", "repeat": 0, "enabled": true}],
       "lastDeploy": {"at": 1758000000, "ok": true,
                      "files": [{"name": "SHOW3.hseq", "bytes": 12345678,
                                 "sha256": "...", "ack": true}],
                      "mode": "G", "clockSetAt": 1758000000}}}
```

The playlist defaults to `show_playlist.order`; deploy config is per device because each
controller only receives its own channels.

### 6.3 Deploy job (`POST /api/hinkspix/<cid>/deploy`, background, `BakeProgress`-style status)

1. **Preconditions:** device online, `uploadSupported`, every playlist timeline baked, port
   config pushed (hash matches) — else 409 with reasons.
2. **Render:** for each timeline, iterate `frame = 0..ceil(durationS x 40)`, `t = frame x 0.025`,
   build the frame buffer from `PixelOutputMap` (pixels for every streamed LED fixture on this
   device; DMX-out span from the DMX bake segments via the existing helpers writing into a
   scratch `DMXUniverse` when `dmxOut.enabled`). Apply master brightness + gamma exactly as the
   live path does (§3.3), so what was previewed is what plays. Write to
   `data/hinkspix/<cid>/<NAME>.hseq`, keep sha256.
3. **Upload:** `.hseq` files, `<PLAYLIST>.ply`, seven `<DAY>.sched` (days without rows get an
   empty `[]` so stale schedules are cleared), then `set_time()`, then `set_mode('G')`.
4. **Record** `lastDeploy` manifest; UI shows per-file progress. Chunks are 580 B, so a 30 MB
   sequence is ~52k round-trips — expect minutes; show an ETA like xLights.

`POST /api/hinkspix/<cid>/mode` `{"mode":"live"|"standalone"}`: live → `OP_MODE ETHERNET`
(reboots; poll until probe succeeds); standalone → `set_mode('G')`.
`POST .../set-clock` on demand and automatically before every deploy.

**Firmware gate (#944 B18).** Every raw-TCP operation — `set-clock`, standalone `mode`, deploy,
and each of the uploads above — is refused with **409** and the reason when
`FirmwareSupportsUpload()` fails: MCPU **>= 151**, or **>= 129** on hardware V3. xLights checks
the same predicate before each of these (`HinksPixExportDialog.cpp:471/505/520/546/655/685`),
because below it the controller drops the connection rather than answering — which reaches the
operator as a bare socket error naming nothing. `_upload_gate()` in `orch_hinkspix.py` is the
single implementation; `GET .../deploy` carries the same verdict as a `gate` object so the
standalone view can disable its TCP buttons with the reason shown instead of failing on click.
`mode: "live"` is deliberately **outside** the gate: it is the HTTP config path (§2.1
`OP_MODE ETHERNET`), not a TCP upload, and xLights has no analogue — so a controller that cannot
be managed over TCP can still be returned to live mode.

> **On "#944 says MS_152".** The issue title and its test note name an MS_152 threshold, but
> `152` appears nowhere in `HinksPix.cpp`, `HinksPix.h` or `HinksPixExportDialog.cpp`; the only
> upload-floor constants in the reference are `V2UPLOADFIRMEWARE = 151` and
> `V3UPLOADFIRMEWARE = 129` (checked 2026-09-23 against `master`). The gate is implemented on
> `hb.supports_upload()`, i.e. 151 / 129. If MS_152 came from a field observation rather than the
> source, it is a different claim and needs a bench capture to pin down.

### 6.4 Operator UX — "8pm-11pm daily"

Shows tab (`show-runtime.js`) gains a **"Standalone controllers"** panel per hinkspix device:
playlist (defaults to the show playlist), a schedule editor (day chips, start, end, repeat,
enabled — with the overnight-split hint), buttons Deploy / Set clock / Standalone / Live, and
an **"On the controller"** panel showing the last-deploy manifest (file names, sizes, acks,
clock-set time) plus the live probe (mode, firmware). Verification of what is actually on the
card: SD listing if the command letter is confirmed on the bench (§8.3); otherwise the manifest
plus a link to the controller's own web UI file page. `Deploy` is disabled with an explanation
while the port-config hash is stale or timelines are unbaked.

---

## 7. DMX-out path (J3)

Works **today** with zero code: patch a DMX fixture to universe `u`, enable `dmxOut` on the
device with universe `u` (§4.4 pushes the `DATA_MODE` DMX row), and the existing Art-Net/sACN
engines drive it live. In the offline path the DMX universe is appended as the trailing span of
the `.hseq` (§6.3 step 2), so movers patched behind the HinksPix also play unattended. The only
new code is the config row and the trailing-span writer; both live in the device-config and
deploy issues.

---

## 8. Risks / open questions for the bench (operator's actual unit)

1. **Upload firmware gate** — read `MCPU`; PRO needs >= 151, PRO 80 (`Type "8"`) >= 129.
   Below that, offline playback is impossible without a HolidayCoro firmware update.
2. **Mode semantics** — does `'G'` (standalone) suppress live E1.31 input? Does
   `OP_MODE ETHERNET` stop the schedule? Is a reboot needed either way? Does the schedule run in
   ETHERNET mode when no data is arriving? Determines the Live/Standalone toggle and whether
   "live at 7pm, scheduled at 8pm" is possible without a mode switch.
3. **SD listing command letter** — `GetFileInfoFromSDCard(cmd)` has no caller in xLights;
   reply format `*NAME.HSEQ,date,time!`. Try candidates while watching the controller's web UI;
   if none works, verification stays manifest-based.
4. **`.hseq` frame-count width** — header carries `numFrames` as u32 at 20 and u16 at 334;
   whichever the firmware reads caps sequences at 65535 frames (27.3 min at 25 ms).
   Mitigation: reject/split timelines > 27 min, or use 50 ms for long shows.
5. **Frame-rate word** — 25 ms encodes as 1102 (truncated 1102.5); confirm the controller plays
   at 40 fps, not 39.98.
6. **Multicast on the operator's switch / Windows NIC** — sACN multicast egress needs `bindIp`
   when multiple NICs exist; Art-Net unicast is the fallback (routes auto-added).
7. **`GetInfo.cgi` / read-back key names** on V4.1 firmware — the key set is not in the xLights
   source; capture once and pin in tests.
8. **Clock** — `Tag_Dow_TimePacket` sets time-of-day + weekday only, in the orchestrator's local
   zone; DST changes require a re-sync. Add a nightly "set clock" option when the orchestrator is
   running; document that unattended DST drift is 1 h.
9. **Existing performer/DMX 2 s offset** — `RUNNER_GO` uses `now+2`, DMX loops use `time.time()`;
   confirm on the rig whether pixels should follow DMX (proposed) or performers.
10. **Port config encoders** — copy `EncodeColorOrder` / `EncodeStringPortProtocol` /
    `EncodeBrightness` / `EncodeGamma` tables from `HinksPix.cpp` and confirm one port
    end-to-end before trusting a bulk push.
11. **Upload throughput** — 580-byte chunks with a round-trip ACK each; measure and surface an
    ETA. Consider a 50 ms step for long shows to halve file size.

---

## 8a. Field findings, 2026-09-17..19 (operator's unit, PN #925, bought Jan 2021)

Answers to several §8 questions, measured on real hardware. The unit is **MCPU MS_149**
and could not be updated, so the #938-#941 code has still never run against a HinksPix.

**§8.1 upload gate — CONFIRMED, and worse than documented.** `MS_149` is below the
`MS 151` network-upload gate, so xLights refuses. Raw TCP upload also fails: the controller
resets the connection immediately, consistent with `MS 152 — "Enabled remote transfer of
firmware and standalone files"`. Firmware older than 152 has no remote file transfer at all,
so `hinkspix_tcp.py` cannot work below that version. Worth surfacing in the UI as a
capability check, not just the `uploadSupported` flag.

**MS_149 deadlocks at boot on a SUCCESSFUL DNS lookup** (the auto-update check removed in
`MS 151`). Measured with DNS + firewall logging: a valid answer produces **one query then a
deadlock with zero outbound packets** — it never opens a socket; a failed lookup produces
**33 retries over ~11 s**, then the error branch runs and boot completes normally. Any
mechanism that makes the lookup fail avoids it (NXDOMAIN, or simply no Ethernet link).
**Operator workaround: boot with Ethernet unplugged, then replug.** Never "fix" its DNS —
working DNS is what breaks it. Consequence: such a unit cannot self-recover from a power cut.

**§8.7 readback keys — partially captured.** The controller's own menu can write its E1.31
config to SD as `E131_Config\Current_E131.SYS`, in a readable text form that matches the
shape `hinkspix_bridge.build_universe_rows()` produces:

    ~U,32
    ~E,<idx>,<universe>,<chans>,1,<absStart>,<absEnd>,...

That is useful independent validation of the universe-table encoding in #939.

**Debug page is the SD diagnostic** (`GET /debug.html` on normal firmware) — reports
`SDC Active Mounted`, `SD Error Recoveries`, `Perform EE Op Error`, heap and E1.31 counters.
Note `EE OP ERR n` in an error message is that **counter**, not an error code.

**SD firmware update did not trigger on MS_149** under any layout tried: the full vendor zip
at the card root, a minimal `SD_HFW.sys`+`.joe`, `Fix.zip` as shipped, and `.joe` files in an
`FW\` subdirectory (HSA V3's own code references `\FW\*.joe`). Card confirmed mounted and
writable throughout. There is no firmware-from-SD item in the button menus on this firmware.
Unresolved; reported to HolidayCoro 2026-09-19.

**Emergency web flash rejects every filename** — `BAD FILE NAME`, stalling ~18 KB into a
279 KB file regardless of file, name, or card state. The expected filename/format is unknown
and is the open question with the vendor.

**Vendor web-server bug worth coding around:** the emergency image sends
`Content-Encoding: gzip` with an **uncompressed** body and `Content-Type: (null)`, so it is
blank in any browser. Normal firmware serves correctly (and genuinely gzips). Any client we
write should not trust `Content-Encoding` from this device.

## 8b. Bench session, 2026-09-23 — unit recovered, wire protocol audited

The unit is now on **MCPU MS_160** (PCPU PS_39, ECPU EZ_40, WEB WF_113; BD1 = Long_Range,
BD2 = Local_SPI, BD3 = Not_Present, MaxU 402). `probe()` reports `uploadSupported: true`, so the
§8a boot deadlock and upload gate no longer apply. All 48 ports are still at factory defaults.

First contact with real hardware showed that §2.1 had the transport wrong. xLights sends **every**
CGI call as an HTTP **GET** with the payload in request **headers**: `DATA: {json}` for writes,
`BLK: n` to select the board on reads (`BLK n` = expansion n+1, ports 16n+1..16n+16). An
idempotent `DATA_MODE` write sent as POST-with-body returns `{"CMD":"POST","ERROR":"ERROR"}`; the
same write as GET-with-header returns `{"CMD":"POST","OK":"OK"}`. A line-by-line audit against the
xLights driver found 22 mismatches in total, including 0-based universe rows (the device is 1-based)
and every port being written with start channel 1. Tracked in **#943** (wire protocol) and **#944**
(raw TCP); the guided configuration UI, guidance/validation and xLights show-folder import that build
on it are **#945**, **#946** and **#947**. §2.1 has been rewritten under #943 to match what the
bench found.

The operator's real layout is one 200-pixel WS2811 RGB string (garage eaves) on **port 17**, start
channel 1, universes 1-2, taken from their xLights show folder (`xlights_rgbeffects.xml`).

---

## 9. Test strategy (repo gates)

Offline first, all under the `unit` job in `.github/workflows/python-tests.yml`
(`SLYLED_DATA` isolation via `tests/conftest.py`):

| Suite | Covers |
|---|---|
| `tests/test_pixel_renderer.py` | corpus golden vectors; determinism; every action type; `active_segment` rule vs `_dmx_playback_loop`; n=1 edge cases; `EFFECT_SPEC_VERSION` equality parsed from `pixel_renderer.js` |
| `tests/test_pixel_renderer_parity.py` | Node runs `spa/js/pixel_renderer.js` on the same corpus (pattern: `test_fixture_shortcuts.py`; skips without node) |
| `tests/test_hinkspix_offline.py` | static: `struct.calcsize` = 608/34/26/22, 18-byte header literal, `TotalSize = 28 + DataSize`, FAT word round-trip, `.hseq` header bytes at every documented offset, `.ply`/`.sched` exact text, schedule validation, short-name rules. **Also the in-process fake TCP controller** (parses chunks, reassembles files, replies `\|FOK`, injects failure/timeouts): reassembled bytes, close-packet name/DTTM and its `DataSize 0` field, the exact-multiple flush chunk, time-packet fields, mode packet, errors surfaced |
| `tests/test_hinkspix_wire.py` | HTTP wire protocol (#943): GET + headers, quoted `"OK"`, `BLK` board select, gzip replies, the 1-based universe table, per-port start channels, full-board `PCONFIG`, `UnPack` gating, reboot-last, DDP. **Light self-check only** — it asserts the request shape and the command sequence. Standing up a gated in-process `http.server` fake, replaying golden MS_160 captures, and wiring the hinkspix suites into this job are deferred to the QA lane |
| `tests/test_hinkspix_apply.py` | (#945) the push lifecycle against a **stateful in-process fake** that stores what it is told: the snapshot before the first write and the two invariants that matter — nothing is written to a controller that cannot be read, and a failed request stops the sequence, leaves the reboot unsent and names the snapshot to restore. Plus restore round-trip (rows and table back, `inSync` cleared), restore refusals, the findings gate + `ack`, one-job-at-a-time, and `BACKUP_KEEP` retention per device |
| `tests/test_hinkspix_config_spa.py` | (#945) Playwright against **stubbed `fetch`** — the panel only: the five-step flow, the editor opening *on top of* the wizard and `closeModal()` returning to it, the acknowledgement gate, the apply poll stopping when the job ends and when the modal closes. Every recorded request must be an orchestrator path: a wizard that reached the controller from the browser would be a second, unverified configuration path |
| `tests/test_hinkspix_smart.py` | (#946) the caps table and `caps_key` for every witness (Controller E / Type 8 / MaxU 65-402-684 / V3 / un-probed); port → board/bank/sub-port; five `calculate_smart_receivers` cases pinned to `HinksPix.cpp:860-918` (per-sub-port slots, a chain on one output, the 16-port group of four, the 16AC's `/3` start pixel, a non-Long_Range board skipped); the `SCONFIG` payload and its place between `BD_INFO` and `PCONFIG`, with no request at all for an empty bank; a five-board PRO V3 at port 80; every `validate` code and its level; the PUT/GET contract (`caps`, caps-filtered protocols, `startNulls` + its `nullPixels` alias, receiver id/type accept-reject, the model's port ceiling) and `defaults-from-fixtures` storing nothing. The bridge's device entry points are replaced with ones that raise for the whole file, so the suite cannot reach a controller |
| `tests/test_hinkspix_xlights_import.py` | (#947) the pure importer against the operator's real show folder (`tests/fixtures/xlights_home_eves/`): the `BaudRate`-is-the-universe quirk, non-contiguous and mixed-width universe blocks, `FullxLightsControl` off, every `StartChannel` form with a cycle guard, the per-output rule (`parm1 × parm2`, one string per output) for every `DisplayAs`, `StringType` → channels per node, a `parm3` outside `{0,1}`, the unknown-protocol fallback, and the two derived-not-captured shapes (Custom header, chain offsets) failing closed. No socket is opened — the module has none to open |
| `tests/test_hinkspix_xlights_routes.py` | (#947) the two routes through Flask with **the wire shut** (both transports patched to raise for the whole file): path spellings incl. a Windows path, its WSL twin and a pasted *file* path; uploads; the multi-controller chooser; the targeted preview and its diff/notes; the accept happy path, the idempotent re-accept, an unticked row, a port already bound, `createFixtures` off, and five refusals that leave the stored config byte-identical to the editor's own — the parity that makes an import safe. Plus a monkeypatched mid-write failure: config, fixtures and routes all rolled back |
| `tests/test_hinkspix_xlights_spa.py` | (#947) Playwright against **stubbed `fetch`** — the panel only, with the canned bodies produced by calling the real routes in-process rather than written by hand: the modal over the port table, the folder box and its upload fallback, the controller chooser, the proposal rendered as a diff, the tick state that travels back as `accepted`, the result screen, and re-reading after an apply. Every recorded request must be an orchestrator path, never the unit's address |
| `tests/test_hinkspix_device.py` | Flask: add device with mocked probe; port-table CRUD + collision 400s; fixtures-from-ports; strings `port` validation; universeRoutes upsert; `_is_performer` guards (no RUNNER_GO/LOAD_STEP/PING to hinkspix); sweep marks offline via HTTP probe; the #944 firmware gate on `set-clock`/`mode` (asserted by capturing that no connection is attempted) |
| `tests/test_hinkspix_output.py` | show start with a baked timeline → after one loop tick `peek_universe(u).get_data()` holds renderer output at the right offsets; LED-only show no longer idles; blackout on stop; master-brightness scaling on sACN pixel universes |
| *not yet covered — QA lane* | deploy render (`render_hseq_frames`): size = 336 + frames x channels; frames equal `render_fixture`; DMX-out trailing span; hseq channel map identical to the live map. No suite asserts this today |
| `tests/test_dmx_engines.py` (extend) | sACN send-time master scaling; `all_intensity` fast path |
| `tests/regression/run_all.py` | unchanged; add a hinkspix fixture to `test_full_show.py` under `SLYLED_LIVE_RIG` only |

**Live-rig checklist:** Wireshark E1.31 capture shows expected universes at 40 Hz; port 1 lights
match the 3D preview; deploy a 2-minute schedule window, power off the orchestrator, observe
playback; confirm items 1-5 and 7 of §8 and record results in `docs/live-test-sessions/`.

---

## 10. Implementation issues (dependency-ordered, complete PRs)

**Status as of 2026-09-23: #938, #939, #940, #941, #943, #944, #945, #946 and #947 are
implemented.** The 2026-09-23 bench session (§8b) found the wire protocol broken in
several places, so #943-#947 were filed and worked in order — the write path first
(#943, #944, #945), then guidance (#946) and import (§4.8). What remains for the QA lane is
the harness work §9 defers: the shared gated `http.server` fake, replaying golden MS_160
captures, and wiring the hinkspix suites into `python-tests.yml`.
Deviations from this plan, and why, are recorded in each commit message. The
notable ones: the 64-segment bake cap moved from #938 to #939 (bake_timeline had
no `children` parameter), and the standalone scheduling UI lives in the HinksPix
device modal rather than a Shows-tab panel, keeping all controller UI in one
place.

| Issue | Title | Depends on |
|---|---|---|
| #938 | `feat: server-side per-pixel effect renderer with SPA parity corpus` | — |
| #939 | `feat: HinksPix PRO as a first-class device — probe, port model, fixture binding, config push` | — |
| #940 | `feat: HinksPix live output — streamed LED fixtures in show playback and live actions` | #938, #939 |
| #941 | `feat: HinksPix offline playback — hseq/ply/sched writers, raw-TCP upload, RTC sync, mode switch, Deploy UI` | #938, #939, #940 |
| #943 | `fix: HinksPix protocol parity against the xLights reference` (bench audit; the wire-protocol QA harness landed as `tests/test_hinkspix_wire.py`) | #939 |
| #944 | `fix: firmware gate on the upload path` (`FirmwareSupportsUpload()`; issue's `MS_152` floor vs source `151`/`129` still needs a bench capture, §6.3) | #943 |
| #945 | `feat: HinksPix configuration management — snapshots, push job, verify wizard` (§4.6) | #943, #944 |
| #946 | `feat: HinksPix configuration guidance — capability table, smart receivers, the finding table` (§4.7) | #945 |
| #947 | `feat: import an existing xLights layout — networks/models → fixtures` (§4.8) | #946 |

Order: **#938 ∥ #939 → #940 → #941**, then the bench-driven run **#943 → #944 → #945 → #946 → #947**.
A and B are independently landable complete units; C is the first PR where both halves must change
together and is scoped as one coordinated PR; D likewise. #945 is the last of the bench findings to
touch the write path; #946 and #947 are additive (guidance, then import) and neither changes what an
upload sends without the operator asking for it.
