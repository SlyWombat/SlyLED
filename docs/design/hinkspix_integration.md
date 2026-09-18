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

- **Probe:** `GET /XLights_BoardInfo.cgi` → JSON with `Controller` (`"H"` PRO / `"E"` EasyLights),
  `Type` (`"P"` pixel, `"A"` AC, `"8"` PRO 80 = hardware V3), `MaxU` (max input universes:
  145 pre-v111, 402 v111+, 684 PRO 80), `MCPU`/`PCPU`/`ECPU`/`WEB` version strings,
  `BD1..BD5` expansion board types.
- **Commands:** `POST /Xlights_PostData.cgi`, body text `DATA: <json>`; success iff response contains `"OK"`.
  - `{"CMD":"DATA_MODE","MODE":"E131"|"ARTNET"|"DDP"}` (+`DDP_START`,`DDP_CHAN_COUNT` for DDP).
    xLights first POSTs `BLK: 0` to `/Xlights_Data_Mode.cgi` to read the current mode.
  - `{"CMD":"E131","BLK":"<j>","LIST":[{"V":"index,universe,numOfChan,1,hinksStart,hinksEnd"} x6]}`
    — blocks of 6 input-universe rows; unused rows up to `MaxU` are `"index,index,0,1,0,0"`,
    beyond that `"0,0,0,0,0,0"`. Followed by `{"CMD":"BD_INFO","NumU":"<n>"}`.
  - `{"CMD":"PCONFIG","BOARD":"<b>","LIST":[{"V":"output,protocol,startChan,pixels,endChan,direction,colorOrder,nullPixel,brightness,gamma"} x16]}`
    per 16-port board. Integer codes come from `EncodeColorOrder` / `EncodeStringPortProtocol` /
    `EncodeBrightness` / `EncodeGamma` in `HinksPix.cpp` — copy the tables at implementation time.
  - `{"CMD":"DATA_MODE","DMX_ACTIVE":1,"DMX_UNIV":u,"DMX_START":1,"DMX_CHAN_CNT":512,"DDP_DMX_ACTIVE":0,...}`
    — bridges universe `u` to the J3 DMX-512 output.
  - `{"CMD":"OP_MODE","MODE":"ETHERNET"}` — live-data mode; no response, controller reboots.
  - **Read-back:** `GET /GetE131Data.cgi`, `/Xlights_Board_Port_Config.cgi`, `/GetInfo.cgi`
    return comma-separated `key,value,...` text.
- **Upload gate:** `FirmwareSupportsUpload()` = `MCPU >= 151` (PRO) or `>= 129` (PRO 80 / hardware V3).

### 2.2 Raw TCP (port 80, faked HTTP header) — `#pragma pack(2)`

| Struct | Layout | Size |
|---|---|---|
| `Tag_Packet` (file upload, `CMD[0]='F'`) | `char HINK[18]` = `"HINK TCP_CMD  \r\n\r\n"`, `uint8 CMD[4]` = `{'F',0x5a,0xa5,0}`, `u16 TotalSize`, `u16 StructType`, `u16 DataSize`, `uint8 Data[580]` | 608; on the wire only `TotalSize = 28 + DataSize` bytes are sent |
| `StructType` | 0 = first chunk (open temp file, truncate), 1 = append, 2 = close | |
| `Tag_File_Data_Close` (in `Data` when StructType=2) | `char FN[30]`, `u32 DTTM` (FAT date/time word) | 34 → TotalSize 62 |
| `Tag_Dow_TimePacket` (`CMD[0]='D'`) | HINK[18], CMD[4], `u8 hr, min, sec, dow` (dow 0 = Sunday, **local** time; no date) | 26 |
| `Tag_CMD_Packet` (`CMD[0]=mode`) | HINK[18], CMD[4] | 22; `'G'` = master/standalone, `'H'` = remote/slave |
| SD listing (`GetFileInfoFromSDCard(cmd)`) | header + 4-byte CMD; reply `*NAME.HSEQ,date,time!...` | **no caller in xLights; command letter unknown → bench** |

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
           "ports": [{"port": 1, "leds": 300, "mm": 5000, "protocol": "ws2811",
                      "colorOrder": "RGB", "direction": 0, "nullPixels": 0,
                      "brightness": 100, "gamma": 1, "enabled": true}],
           "configPushedAt": 0, "configHash": ""}}
```

**Explicit divergence from the performer model:** `sc=0, strings=[]` guarantees no performer
code path (`_child_led_ranges`, `_load_step_pkt`, sync, `RUNNER_GO`) can address it; the
8-string wire constant never applies. Port inventory is device-level (48 entries); geometry is
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

- `_validate_fixture_strings` (`:3292`) gains: `port` (1..48 int, unique across all fixtures on
  that child, must be `enabled` on the device, `leds` must equal the device port's `leds`).
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
  `ceil(leds/170)` universes (170 px = 510 ch, the xLights convention).
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

`POST /api/hinkspix/<cid>/push-config` runs in order: `DATA_MODE` (from
`_dmx_settings.protocol`), `E131` blocks + `BD_INFO NumU`, `PCONFIG` per board, DMX-out
`DATA_MODE` row, then (if requested) `OP_MODE ETHERNET`. Records `configPushedAt`/`configHash`;
the SPA shows "config differs from device" when the local port-table hash differs.
`GET /api/hinkspix/<cid>/readback` fetches `GetE131Data.cgi` +
`Xlights_Board_Port_Config.cgi` for a side-by-side verify view.

### 4.5 UX surfaces

- **Setup → Hardware**: row with badge "HinksPix" (new `boardColors` entry), status, firmware,
  buttons Refresh / Configure / Web UI (`http://<ip>/`) / Remove.
- **Device modal** (new `hinkspix.js`): port table (48 rows: enabled, leds, mm, protocol,
  colour order, null pixels, brightness, gamma), base universe, protocol readout, DMX-out
  toggle + universe, "Create fixtures from ports", "Push config", "Read back",
  mode indicator (Live / Standalone / unknown), "Set clock".
- **Add device**: `POST /api/children` already tries PING then WLED; add a HinksPix probe
  (`XLights_BoardInfo.cgi`) in the same fall-through. No mDNS/ArtPoll guarantee → manual IP add
  (like WLED) is the supported path; an ArtPoll reply, if the unit answers, folds through the
  existing `_artnet_oneshot_poll` merge with type promotion to `hinkspix`.
- **Layout / 3D / Control**: nothing new — fixtures are `led`. `fixture-types.js led` gets a
  badge chip "HinksPix P<port>" and `panelDetailHtml` shows port numbers.

---

## 5. Live output path

### 5.1 Module `desktop/shared/pixel_output.py`

- `PixelOutputMap` (§4.3) + `fixture_spans(fixture)`.
- `write_fixture_frame(engine, fixture, rgb_bytes)` →
  `engine.get_universe(u).set_channels(start, slice)` per span (marks dirty; the engine thread
  transmits). One call per fixture per tick.

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
  (608-byte `Tag_Packet` chunks of <= 580, `|FOK` per chunk, `StructType` 0/1/2),
  `set_time(now_local)`, `set_mode('G'|'H')`, `list_files(cmd)`; 5 s per-ACK timeout;
  raises `HinksPixError` with the controller's reply text.
- **`hinkspix_bridge.py`** — HTTP side (§2.1): `probe`, `push_data_mode`,
  `push_input_universes`, `push_port_config`, `push_dmx_out`, `op_mode_ethernet`, `readback`.
- **`orch_hinkspix.py`** — Blueprint: device CRUD/probe/push/readback, deploy job, schedule CRUD,
  mode switch, inventory.

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

## 9. Test strategy (repo gates)

Offline first, all under the `unit` job in `.github/workflows/python-tests.yml`
(`SLYLED_DATA` isolation via `tests/conftest.py`):

| Suite | Covers |
|---|---|
| `tests/test_pixel_renderer.py` | corpus golden vectors; determinism; every action type; `active_segment` rule vs `_dmx_playback_loop`; n=1 edge cases; `EFFECT_SPEC_VERSION` equality parsed from `pixel_renderer.js` |
| `tests/test_pixel_renderer_parity.py` | Node runs `spa/js/pixel_renderer.js` on the same corpus (pattern: `test_fixture_shortcuts.py`; skips without node) |
| `tests/test_hinkspix_wire.py` | static: `struct.calcsize` = 608/34/26/22, 18-byte header literal, `TotalSize = 28 + DataSize`, FAT word round-trip, `.hseq` header bytes at every documented offset, `.ply`/`.sched` exact text, schedule validation, short-name rules |
| `tests/test_hinkspix_tcp.py` | in-process fake HinksPix TCP server (parses chunks, reassembles files, replies `\|FOK`, injects failure/timeouts); asserts reassembled bytes, close-packet name/DTTM, time-packet fields, mode packet; errors surfaced |
| `tests/test_hinkspix_device.py` | Flask: add device with mocked probe; port-table CRUD + collision 400s; fixtures-from-ports; strings `port` validation; universeRoutes upsert; `_is_performer` guards (no RUNNER_GO/LOAD_STEP/PING to hinkspix); sweep marks offline via HTTP probe |
| `tests/test_hinkspix_output.py` | show start with a baked timeline → after one loop tick `peek_universe(u).get_data()` holds renderer output at the right offsets; LED-only show no longer idles; blackout on stop; master-brightness scaling on sACN pixel universes |
| `tests/test_hinkspix_hseq.py` | deploy render: size = 336 + frames x channels; spot-check frames equal `render_fixture`; DMX-out trailing span; channel map identical to the live map |
| `tests/test_dmx_engines.py` (extend) | sACN send-time master scaling; `all_intensity` fast path |
| `tests/regression/run_all.py` | unchanged; add a hinkspix fixture to `test_full_show.py` under `SLYLED_LIVE_RIG` only |

**Live-rig checklist:** Wireshark E1.31 capture shows expected universes at 40 Hz; port 1 lights
match the 3D preview; deploy a 2-minute schedule window, power off the orchestrator, observe
playback; confirm items 1-5 and 7 of §8 and record results in `docs/live-test-sessions/`.

---

## 10. Implementation issues (dependency-ordered, complete PRs)

**Status as of 2026-09-18: #938, #939, #940 and #941 are implemented.**
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

Order: **#938 ∥ #939 → #940 → #941.** A and B are independently landable complete units; C is the first PR
where both halves must change together and is scoped as one coordinated PR; D likewise.
