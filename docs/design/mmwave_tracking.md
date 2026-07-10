# SlyLED MMwave People Tracking — Design Doc

**Status:** v2 draft — design phase only. Operator decided isolation strategy (see v2 revision note); remaining §11 open questions pending. No implementation yet.
**Issue:** to be created (proposed issue breakdown in §12).
**Author:** opened 2026-07-09.
**Scope:** New **MMwave** firmware subproject — ESP32-C61 + Ai-Thinker Rd-03D radar nodes for people tracking — plus the orchestrator-side ingestion, fusion, and calibration it needs. Camera nodes are **retained** but demoted from primary people-tracking to a complementary role. LED performers, gyro, DMX path: untouched.

### Revision history

- **v1** (2026-07-09): initial draft from feasibility research. Answers the three scoping questions (multi-unit deployment, radar-to-radar visibility, multi-unit coordination); proposes architecture, protocol, calibration, and issue breakdown.
- **v3** (2026-07-09): full-repo review completed; sequencing and prerequisites now live in `docs/NEXT_STEPS.md`. Key review impacts on this design: (a) the temporal-object fusion this design reuses has a camera-provenance assumption and a fused-ID orphaning bug — fixed under NEXT_STEPS C8/E2 before `radar_fusion.py` lands; (b) the tracker precedent (`firmware/orangepi/tracker.py`) has a px-vs-mm re-ID unit bug — do not copy it verbatim; (c) the isolated toolchain (§4.2) is implemented via the registry-driven build refactor (NEXT_STEPS E4, `arduinoDataDir` field) rather than a one-off script; (d) the OTA flow this node inherits gets SHA-256 verification first (NEXT_STEPS C2), and the UDP OTA URL cap (C7) is fixed before this board relies on UDP-pushed OTA; (e) `0x70` registration rides the UDP dispatch-table refactor (E3), and the `radar` fixture type rides the fixture-type registry (E1).
- **v2** (2026-07-09): operator decision on §11 question 1 — **MMwave is treated as separate hardware with its own sketch and its own isolated toolchain; the stable targets and their installed core are not touched.** It shares *behaviour*, not builds: common onboarding (`arduino_secrets.h` + NVS creds), common OTA (`CMD_OTA_UPDATE` 0x50 flow), and the common UDP 4210 protocol. §2.4, §4.1–4.2, §10, §12 reworked accordingly; former v1 recommendation to upgrade the shared core is withdrawn.

---

## 1. Intent

Camera-based people tracking is at its worst in exactly the environment SlyLED creates: darkness punctuated by fast-changing saturated LED light that wrecks exposure and colour-based re-ID. The `firmware/orangepi` tracker works, but it is lighting-dependent, ~2 fps, and each camera is a Linux SBC with real cost and setup burden.

24 GHz radar is indifferent to lighting, sees through thin fabric and plastic (sensors can hide inside props or behind grilles), produces metric positions directly, and — because it captures no imagery and cannot identify anyone — *strengthens* the project's local-first privacy story rather than complicating it.

The goal of this subproject: **multiple small radar nodes feed anonymous person positions into the orchestrator's existing stage model, becoming the primary source for "where are people"**, with cameras remaining for everything vision-shaped (mover calibration, beam detection, AI auto-tune, any future identity needs).

Non-goals for v1: identity ("which performer is this"), vertical position (the Rd-03D is planar), beat-accurate triggering (see latency budget §5.5), outdoor/long-range coverage.

## 2. Hardware

### 2.1 Per-node bill of materials

| Part | MPN | Role |
|---|---|---|
| MCU dev board | **ESP32-C61-DevKitC-1-N8R2** | WiFi 6 RISC-V MCU, 8 MB flash / 2 MB PSRAM |
| Radar module | **Ai-Thinker Rd-03D** | 24 GHz FMCW, 1TX/2RX, multi-target trajectory tracking |
| — | 5 V supply (USB) + 4-wire harness to radar | |

### 2.2 Rd-03D — capabilities and hard limits

Facts from the Ai-Thinker user manual and specification (links in §13):

| Property | Value | Design consequence |
|---|---|---|
| Band | 24.0–24.25 GHz ISM, FMCW, 1TX/2RX | Angle comes from 2-RX phase difference → angle is the *weak* axis (§3.3) |
| Targets | **max 3 simultaneous tracked targets** (multi-target mode) | Per-sensor ceiling; more people needs more sensors + fusion |
| Range | ~8 m detection | One node covers a wedge, not a hall |
| Field of view | ~±60° azimuth | Tile wedges with overlap |
| Output | Binary UART frames, **256000 baud** 8N1, ~10–15 Hz: per-target X (mm), Y (mm), speed (cm/s), distance-resolution word; sign carried in the MSB per the Ai-Thinker encoding | Node firmware = fixed-size integer parser; fits repo firmware constraints as-is |
| Configuration | Command frames to switch single-/multi-target mode and basic detection parameters | **No control over TX power, chirp timing, modulation, or frequency** (§3.2) |
| Sensing plane | 2D (azimuth plane of the module) | No Z; assume targets on stage floor plane, mount per §7 |
| Motion filter | Static clutter is rejected by design | Stationary people fade; the fusion layer must coast tracks (§5.2) |
| Power / logic | 5 V supply, 3.3 V UART logic | Direct wiring to C61, no level shifter |

### 2.3 Wiring

The boards in hand are the "G550" Rd-03D revision: a fitted 4-pin connector labeled `5V/GND/TX/RX` — per the Ai-Thinker spec a **1×4P 1.25 mm-pitch socket** (buy a "JST 1.25 mm 4-pin" / Molex PicoBlade-compatible pigtail; supply is 4.5–5.5 V at ≥200 mA, fine on the DevKit's USB 5 V rail), plus edge pads `RX TX GND DEBUG DP DM 5V` (`DEBUG` = chip debug port, `DP`/`DM` = USB for Ai-Thinker's PC tool — all unused; generic docs call the UART pins `OT1`/`RX`):

| Rd-03D (G550 silk) | C61 DevKitC-1 | Note |
|---|---|---|
| 5V | 5V (J1 pin 14) | USB 5 V rail |
| GND | GND (J1 pin 15) | adjacent to 5V |
| TX (radar out; a.k.a. OT1) | GPIO2 → UART1 RX | bench-validated 2026-07-10 |
| RX (radar in) | GPIO3 → UART1 TX | |

**Bench-validated (#908, 2026-07-10):** 256000 8N1 confirmed on real G550 hardware — 10 Hz frames, zero parse errors over sustained runs, mode-command ACK observed, live single-target tracking. Field notes: wrong-RX-pin symptom is noise whose byte count scales with the read baud (not a decodable stream); the C61 DevKit's *native* USB port needs `CDCOnBoot=cdc` for sketch serial output (add it to the compile fqbn when monitoring via that port); `WiFi.mode(WIFI_STA)` must precede `WiFi.macAddress()` on this core or the MAC reads all-zeros.

Direct stacking on the DevKit headers was evaluated and rejected: no consecutive header run matches the pad sequence (radar wants 5V/GND four positions apart; J1 has them adjacent, J3 has no 5V), the pad pitch isn't 2.54 mm, and a board stacked over the DevKit sits on its WiFi antenna while putting the MCU in the radar's back lobe. Mechanical pattern for a compact node: radar at the enclosure face (antennas outward, metal backplate behind), DevKit behind/beside on standoffs, 4-wire pigtail between.

`Serial1.begin(256000, SERIAL_8N1, /*rx=*/2, /*tx=*/3)` (pins from `MmwConfig.h`). No level shifter — radar UART logic is 3.3 V. Pin choice rationale: GPIO7/8/9 are strapping (8 = RGB LED), GPIO10/11 are the UART0 console (keep for flashing/logs), GPIO12/13 are native USB, and GPIO14 is PSRAM `SPICS1` on the N8R2 — GPIO0–6 are the free set; any pair works if 2/3 is inconvenient. Hardware UART required; 256000 baud is not reliable bit-banged.

### 2.4 ESP32-C61 — the "newer board" risk, named explicitly

The C61 is fresh silicon. What that means concretely:

- **Arduino core support only landed in arduino-esp32 v3.3.5 (Dec 2025)**; ESP-IDF needs ≥5.4. The FQBN will be `esp32:esp32:esp32c61` once a ≥3.3.5 core is installed.
- The repo does **not** pin a core version — every stable ESP32 target (`esp32`, `esp32s3` gyro, `esp32-dmx`) builds against whatever core is installed on the build box, and those targets are stable and shall stay that way. **Decision (v2): the C61 core lives in an isolated arduino-cli installation** (separate `ARDUINO_DIRECTORIES_DATA`, own `arduino-cli-mmwave.yaml`) so the stable targets' core never moves. MMwave carries its own toolchain the way the Giga targets already carry `arduino:mbed_giga` — a different platform that happens to share the repo. Setting this up is Issue 1 in §12.
- **Toolchain gotcha found 2026-07-09 (the "newer board" risk made concrete):** the C61 board definition ships in core 3.3.x, but **no package index — Arduino's, Espressif's stable, or dev — publishes the `esp32c61-libs` prebuilt-libraries tool**, so an out-of-the-box `esp32:esp32:esp32c61` compile fails with `bootloader_qio_80m.elf does not exist`. Working recipe (proven, sketch compiles clean): install core 3.3.10 into the isolated data dir, then graft the C61 libs from pioarduino's compile skeleton — download `c61_a9de5ec5b9_compile_skeleton.zip` from `pioarduino/platform-espressif32` releases (tag `c61-skeleton`; contents are the standard per-chip layout built against IDF 5.5) and copy `esp32-arduino-libs/esp32c61/*` into `.arduino-mmwave/data/packages/esp32/tools/esp32c61-libs/3.3.10/`. Replace with the official tool the moment Espressif publishes one; a self-built alternative is `esp32-arduino-lib-builder` master (Docker), which lists `esp32c61` as a target.
- **Board options:** compile with `esp32:esp32:esp32c61:FlashSize=8M,PartitionScheme=default_8MB` — the N8R2 has 8 MB flash and the default 4 MB scheme leaves the sketch at 78% of a 1.2 MB app slot with no dual-OTA headroom.
- Ecosystem mileage is low. Its sibling C6 shipped a WiFi FTM errata (couldn't act as initiator) — a cautionary tale about assuming listed features work on fresh silicon. Every C61 feature this design touches gets bench-validated before the design relies on it (§10).
- Upside of the new chip: WiFi 6 OFDMA/TWT helps precisely when many small nodes share one AP, and the node's workload (UART parse + UDP) is core-level, so library maturity barely matters. No FastLED involvement on this board.

## 3. Feasibility findings — the three scoping questions

### 3.1 Multiple ESP/Rd-03D units in one environment — **yes; the design assumes it**

One node = one ~8 m, ±60° wedge with a 3-person ceiling. Real coverage means tiling wedges with deliberate overlap. All units chirp in the same ISM band with no coordination protocol; asynchronous FMCW chirps rarely align coherently, so mutual interference manifests as occasional *transient ghost detections*, not systematic failure. Mitigations, in order: placement rules (§7), and orchestrator-side M-of-N track confirmation (§5.2) which kills transient ghosts for free.

### 3.2 Can units tune/pulse their transmission so other units can "see" where they are — **no, ruled out twice over**

1. **Closed firmware.** The Rd-03D command set exposes mode switching and detection parameters only. There is no command for TX power, chirp timing, modulation, or frequency — nothing to "pulse" in a controlled way.
2. **Non-coherent physics.** An FMCW receiver finds range by mixing echoes of *its own* chirp. Another unit's transmission is non-coherent with that chirp: after mixing it appears as broadband noise or a sporadic ghost at a random range — never a stable, locatable point. Additionally these modules reject static returns, and a mounted module is a stationary, tiny radar cross-section — it would not register as a target even if illuminated. Cooperative radar-to-radar localization requires radars with full chirp control and shared timing (TI dev-kit territory), not closed modules.

**The underlying goal — units learning their mutual geometry — is met differently**, and becomes this design's calibration story (§6): manual survey seed → track-correlation self-calibration (one person walks the space; every sensor sees the same trajectory; solve the poses) → optional WiFi FTM cross-check.

### 3.3 Can multiple units be coordinated for larger coverage or better resolution — **yes, at the fusion level; no, at the RF level**

RF-level coordination (synchronized chirps, coherent MIMO across modules) is impossible for the §3.2 reasons. Data-level coordination in the orchestrator delivers three real gains:

- **Coverage** — tiled wedges, fused tracks, handoff as people cross zone boundaries.
- **Position accuracy in overlap zones** — these modules measure *range* tightly and *angle* coarsely (cross-range error grows with distance). Two units viewing the same person from different directions intersect two tight range arcs — triangulation — cutting position error in overlap zones well below what either unit achieves alone.
- **Effective target count and separation** — the 3-target ceiling and close-pair blob-merging are per-sensor limits. People who merge from one viewpoint often resolve from another angle, and the fused picture can hold more than 3 tracks across the space.

Not improved by fusion: each sensor's intrinsic angular resolution and its ~10–15 Hz frame rate.

## 4. System architecture

### 4.1 Placement in the three-tier model

```
Orchestrator (Flask, parent_server.py)
     ▲  UDP 4210: PONG discovery + OTA 0x50/0x51 + new 0x70 MMW_TARGETS
     │
MMwave node = ESP32-C61 + Rd-03D        mmwave/mmwave.ino  (own sketch, own core)
     ▲  UART1 @ 256000, binary frames
     │
Rd-03D radar (closed firmware)
```

**Decision (v2): MMwave is a separate sketch (`mmwave/`), not a board define in `main/`.** The stable targets are working and shall not be touched — no shared-core upgrade, no new `#ifdef` paths through `main/`, no requirement that shared modules compile under two core generations. What MMwave *shares* with the fleet is behaviour on the wire, not source in the build:

- **Common communication** — same UDP 4210 binary protocol (magic `0x534C`, v4/v5 header), same PING/PONG discovery and STATUS_REQ/RESP, its own `0x7x` command range (§4.3). To the orchestrator it is just another child in `children.json`.
- **Common onboarding** — same `arduino_secrets.h` compiled credentials + NVS-stored WiFi config surviving OTA (`NetUtils` pattern), same boot-time broadcast PONG self-announce, same registration flow and Firmware-tab presence.
- **Common OTA** — implements the existing `CMD_OTA_UPDATE` (0x50, URL + SHA-256 payload) / `CMD_OTA_STATUS` (0x51) flow: HTTP-fetch the `.bin` from the orchestrator, verify SHA-256, dual-bank apply (the C61's 8 MB flash takes standard dual OTA partitions). Deployable from the Firmware tab like every ESP node.

### 4.2 Build integration

- New sketch dir **`mmwave/`** with its own modules (`MmwUart.{h,cpp}` frame parser, `MmwUdp.{h,cpp}` reporting, ports of the Protocol/NetUtils/OtaUpdate patterns). It carries **copies** of the wire-level definitions it needs rather than including from `main/` — arduino-cli builds one sketch tree, and cross-tree includes or symlinks would re-couple the builds the decision just decoupled. The sync rule: **wire constants and struct layouts in `mmwave/` must match `main/Protocol.h`**, enforced by a small parity test (a Python check that parses both headers and asserts the shared CMD codes, magic, header shape, and PONG layout agree — §12 Issue 4) so drift is caught mechanically, not by memory.
- Isolated toolchain: arduino-esp32 ≥3.3.5 under its own `ARDUINO_DIRECTORIES_DATA` + `arduino-cli-mmwave.yaml`; a `build_mmwave.ps1` wrapper (or a `-Board mmwave` path in `build_release.ps1` that swaps the data dir) so day-to-day use stays one command. The stable targets' core installation is never touched.
- New entry in `firmware/registry.json`: `id: mmwave`, `fqbn: esp32:esp32:esp32c61`, `flashMethod: esptool`, output `firmware/esp32c61-mmwave/`. Own version track (`mmwave/version.h`) per the repo versioning rule.
- Firmware constraints apply unchanged: fixed `char` buffers, `F()` literals, smallest integer types, no float in the hot path (the Rd-03D already emits integer mm / cm·s⁻¹ — nothing needs floats).

### 4.3 Wire protocol — new `0x7x` command range

Unknown CMDs are ignored by old firmware/orchestrators, so this is back-compat-safe by construction.

| Cmd | Name | Direction | Payload |
|---|---|---|---|
| 0x70 | MMW_TARGETS | node→parent | 28 bytes: `seq(u16) count(u8) flags(u8)` + 3 × `{x i16 mm, y i16 mm, speed i16 cm/s, res u16}` — fixed 3 slots, matching the protocol's fixed-size-struct style; unused slots zeroed. Source of truth: `mmwave/MmwProtocol.h::MmwTargetsPayload` |
| 0x71 | MMW_CONFIG | parent→node | reserved (mode switch, report-rate cap) — not in v1 unless bench validation shows a need |

Coordinates in MMW_TARGETS are **sensor-frame** (Rd-03D local axes, MSB-sign already decoded to two's-complement by the node). Sent only when `count > 0`, plus a 1 Hz empty heartbeat frame; node health otherwise rides the existing STATUS_REQ/RESP.

Discovery: on boot the node broadcasts a standard PONG (like every other node) with `stringCount = 0`, hostname `mmw-<id>`, description identifying the radar. It registers in `children.json` and appears in the Firmware tab like any performer.

Sender→fixture binding (#910, refined by the synthetic E2E test): packets bind to the radar fixture whose `radarNode` equals the sender's PONG-announced hostname; if the hostname matches nothing and exactly one enabled radar fixture exists, that single fixture is assumed (logged once). **A hostname match on a `radarEnabled: false` fixture drops the packet** — disabling a radar is a hard operator gate, never a fall-through to another fixture. Unbindable/malformed packets increment `mmwUnbound`/`mmwMalformed` counters on `/api/status`.

### 4.4 Coordinate handling — node dumb, orchestrator smart

**Decision: the node reports raw sensor-frame millimetres; all projection to stage space happens on the orchestrator.** This mirrors the camera design (camera sends `pixelBox` + `cameraId`; `camera_math.py` projects using the fixture's calibrated pose) and keeps every piece of tunable math server-side where it can iterate without re-flashing nodes.

Each radar is a **placeable fixture** in `fixtures.json` (new fixture type `radar`) with `pos` and `rotation` under the standard conventions: stage frame Z-up right-handed millimetres; `rotation = [rx, ry, rz]` read only via `rotation_from_layout()` (#586/#600). Projection: sensor (x, y) → a point in the sensor's horizontal plane → transformed by the fixture pose → dropped to the stage floor plane (z = 0), since the Rd-03D is planar. Mounting tilt (§7) is captured by `rx` and handled by the same transform — no special-case math.

## 5. Orchestrator ingestion and fusion

### 5.1 Output: temporal person objects (reuse, don't reinvent)

The camera tracker already pushes tracked people as **temporal stage objects** (`objectType: "person"`, TTL, pink `#f472b6`) via `/api/objects/temporal` + `/api/objects/{id}/pos`, and downstream features — 3D view, **Spotlight Follow Person** preset, show generator track actions — consume those objects. The MMwave pipeline terminates in the *same* object type, created internally by the fusion layer (no HTTP loopback needed since we're already inside `parent_server.py`). Everything downstream works unchanged, and camera + radar tracks are automatically co-displayed.

### 5.2 Fusion pipeline (new module, `desktop/shared/radar_fusion.py`)

Per UDP frame → per sensor: project to stage frame (§4.4) → **gated nearest-neighbour association** to existing tracks (gate ~500 mm, matching the camera tracker's `REID_THRESHOLD_MM`) → per-track **constant-velocity Kalman filter** (smooths the modules' angle jitter, coasts through dropouts *and through the static-person fade* noted in §2.2, up to a TTL).

- **Ghost rejection:** a new track must be confirmed M-of-N (e.g. seen in 3 consecutive frames) before it becomes a person object. Interference ghosts (§3.1) are transient and uncorrelated across sensors; real people persist. Cross-sensor corroboration in overlap zones raises confidence further.
- **Cross-sensor merge:** tracks from different sensors within the association gate fuse into one person object, covariance-weighted so the overlap-zone triangulation gain (§3.3) is realized. Track IDs live at the fused level and survive handoff between sensors.
- **Capacity:** total simultaneous people = min(sum of per-sensor ceilings in view, whatever association can keep apart) — the fused ceiling exceeds 3 as long as coverage overlaps are designed in.

### 5.3 Latency budget (stated so nobody expects beat-accuracy)

| Stage | Budget |
|---|---|
| Radar frame period | 66–100 ms |
| UART parse + UDP send (node) | < 10 ms |
| WiFi + orchestrator ingest | 5–20 ms |
| Kalman + M-of-N confirmation lag | 100–200 ms (new tracks); ~1 frame (established tracks) |
| Object update → effect engine | < 50 ms |

**End-to-end ≈ 200–350 ms for an established track.** Fine for zone triggers, follow-spot with the mover's own slew, and spatial effects; **not** suitable for beat-synchronized triggering. This is a design commitment, not an optimization target.

## 6. Calibration — how units learn where they are

Three layers, cheapest first; all persist alongside the existing calibration stores (`calibrations.json` family):

1. **Manual survey (seed + fallback).** Operator places the radar fixture in the SPA stage layout exactly as cameras are placed today — position + rotation. Always works; accuracy limited by tape-measure diligence.
2. **Track-correlation self-calibration (the real mechanism).** A calibration routine prompts one person to walk a loop covering the sensor overlap zones. Every sensor reports the same physical trajectory in its own frame; the orchestrator solves each sensor's rigid transform (2D rotation + translation, point-set registration least-squares) against the fused/reference trajectory, seeded by layer 1. Accuracy approaches the sensors' own precision, requires zero extra hardware, and is exactly the trick the moving-head webcam calibration already sells ("no beacons, no wands"). Can also run passively during normal shows to flag a bumped sensor.
3. **WiFi FTM cross-check (optional experiment).** The C61 is listed as a supported target for ESP-IDF's 802.11mc FTM example (station-initiator ↔ SoftAP-responder). Inter-node distances at ~1 m accuracy could sanity-check layer 2 or auto-group nodes by room — but it yields distance only (no heading), the C6 errata history demands bench proof on real C61 silicon, and Arduino-core exposure of the FTM API is unverified. **Strictly a spike, never a dependency.**

## 7. RF coexistence and placement rules

Operator-facing rules (destined for the user manual when this ships):

- Do not aim two radars directly at each other; prefer viewing the space from different walls with crossing — not opposing — boresights (crossing views also maximize the §3.3 triangulation gain).
- Separate units physically as far as coverage allows.
- Metal backplate/shield behind each module to suppress the back lobe (per Hi-Link's guidance for the equivalent LD2450).
- Mount roughly 1.5–2 m high, level or slightly down-tilted, per the Ai-Thinker manual's intended human-tracking geometry; the fixture `rx` records any tilt.
- Expect and ignore rare transient ghosts — the fusion layer's M-of-N confirmation exists for exactly this. If a persistent ghost appears, re-aim per the rules above.

## 8. Camera complement (cameras stay)

| Job | Owner |
|---|---|
| Where are people (positions, tracks) | **MMwave** (primary) |
| Moving-head + camera calibration, beam detection | Cameras (unchanged) |
| AI auto-tune (exposure/gain/WB via Ollama) | Cameras (unchanged) |
| Anything identity- or appearance-shaped (future) | Cameras |
| People tracking when radar coverage is absent | Cameras (existing tracker remains functional) |

Because both pipelines terminate in the same person objects (§5.1), a venue can mix modalities zone by zone, and a later camera↔radar cross-modal fusion (radar position + camera appearance) is a natural extension — explicitly out of scope for v1.

## 9. Privacy

Radar tracking is anonymous by physics — no imagery, no identity. It stays within the published policy's "no cloud, no telemetry, local-network-only" claims (traffic is UDP 4210 on the local subnet). The privacy policy (`docs/src/marketing/privacy.md`) should get a one-line addition describing radar presence data as locally-processed and non-identifying **before** any release that ships MMwave, per the established policy-first rule. Camera claims are unaffected since cameras remain.

## 10. Hardware validation checklist (bench phase — precedes and gates implementation)

Run on real hardware; results recorded back into this doc as a revision:

1. Isolated toolchain stood up (core ≥3.3.5 in its own data dir); `esp32:esp32:esp32c61` compiles and flashes a trivial sketch; **confirm a stable target (e.g. gyro) still builds untouched via the normal path** — proving the isolation, not regressing the fleet.
2. UART1 at 256000 baud sustains parse of Rd-03D frames with zero loss for 10 min.
3. Multi-target mode: 1/2/3 people produce sane, stable X/Y at 2 m / 5 m / 8 m; characterize angle jitter vs range (feeds Kalman tuning).
4. Static-person behaviour: how fast does a motionless person fade? (feeds coasting TTL).
5. Two nodes, boresights crossing: ghost rate and character with both radiating.
6. Sustained UDP at frame rate under WiFi 6 to the orchestrator; packet-loss measurement.
7. PSRAM (the R2) recognized — nice-to-have; the workload shouldn't need it.
8. FTM spike (optional): C61↔C61 ranging works at all; initiator role confirmed on silicon.

## 11. Open questions (operator decisions needed)

1. ~~**Core upgrade strategy.**~~ **Decided (v2, 2026-07-09):** MMwave is separate hardware with its own sketch and isolated toolchain; the stable targets and their core are not touched. Shared onboarding/OTA/communication happen at the protocol level (§4.2). The parity test keeps the wire definitions honest.
2. **Target venue + node count for v1.** How large a space, and how many people simultaneously? Drives how much §5.2 fusion sophistication v1 actually needs (2 nodes/one room is nearest-neighbour-easy; 6 nodes/hall needs the full treatment).
3. **Driving use case.** Is follow-spot (movers aiming at a tracked person) the headline, or zone-reactive effects? Follow-spot puts pressure on the latency budget and on calibration accuracy; zones are forgiving.
4. **Mounting doctrine.** Wall-mount at 1.5–2 m (manual-recommended, assumed above) vs ceiling-mount looking down (changes the planar-projection model in §4.4 and needs its own bench validation).
5. **Where the calibration walk lives in the UI.** Extend the existing calibration wizard vs a new radar-specific flow.

## 12. Proposed GitHub issues

**Filed 2026-07-09:** 1 → #902 (merged into the registry-driven build refactor), 2 → #908, 3 → #909, 4 → #910, 5 → #911, 6 → #912, 7 → #913, 8 → #914, 9 → #915, 10 → #916, 11 → #917. Full cross-repo sequencing in `docs/NEXT_STEPS.md` §7–§8.

Ordered; 1–2 are blocking, 3–8 largely serial, 9–11 parallel/optional:

1. **Toolchain: isolated C61 build environment** — arduino-esp32 ≥3.3.5 under its own `ARDUINO_DIRECTORIES_DATA` + `arduino-cli-mmwave.yaml` + `build_mmwave.ps1` wrapper; verify a stable target still builds untouched via the normal path. *Blocks all; touches no stable target.*
2. **Bench: C61 + Rd-03D validation checklist (§10 items 2–6)** — outcome is a revision of this doc with measured numbers.
3. **Firmware: `mmwave/` sketch** — WiFi onboarding + NVS creds, PONG discovery, `CMD_OTA_UPDATE`/`CMD_OTA_STATUS` OTA flow, `MmwUart` parser, `mmwave/version.h`, registry entry.
4. **Protocol: `0x70 MMW_TARGETS`** — firmware sender + orchestrator listener, CLAUDE.md protocol-table row, and the `main/Protocol.h` ↔ `mmwave/` **wire-parity test** (§4.2).
5. **Orchestrator: `radar` fixture type** — fixtures.json schema, SPA stage-layout placement, sensor→stage projection via `camera_math` conventions.
6. **Fusion: `radar_fusion.py`** — association, Kalman, M-of-N, cross-sensor merge, temporal person objects; unit tests with synthetic tracks (new `tests/test_radar_fusion.py`).
7. **Calibration: track-correlation solver + calibration-walk routine** (seeded by manual placement).
8. **Multi-unit field test** — 2–3 nodes, coverage/handoff/ghost characterization in a real room; placement rules validated → user-manual draft notes.
9. **Spike (optional): WiFi FTM on C61** — ranging accuracy, initiator-role confirmation. Timeboxed.
10. **Privacy: policy §2 line for radar presence data** — before first MMwave release.
11. **Docs: user-manual chapter** (EN + FR) — placement rules (§7), calibration walk; when the feature stabilizes.

## 13. References

- [Rd-03D Multi-Target Trajectory Tracking User Manual (Ai-Thinker)](https://aithinker-static.oss-cn-shenzhen.aliyuncs.com/docs/_media_old/rd-03d_multi-target_trajectory_tracking_user_manual.pdf)
- [Rd-03D Specification V1.0.0 (Ai-Thinker)](https://en.ai-thinker.com/Uploads/file/20231016/20231016032622_13559.pdf)
- [ESPHome RD-03D component](https://esphome.io/components/sensor/rd03d/) — independent protocol implementation, useful cross-check for the parser
- [arduino-esp32 releases](https://github.com/espressif/arduino-esp32/releases) — C61 support landed v3.3.5
- [ESP-IDF WiFi FTM example](https://github.com/espressif/esp-idf/blob/master/examples/wifi/ftm/README.md) — ESP32-C61 listed as supported target
- [ESP32-C6 FTM initiator errata](https://docs.espressif.com/projects/esp-chip-errata/en/latest/esp32c6/03-errata-description/esp32c6/wifi-ftm.html) — why §6.3 demands bench proof
- [HLK-LD2450 placement guidance (Hi-Link)](https://www.hlktech.net/index.php?id=1157) — equivalent-module coexistence/mounting advice
- In-repo: `CLAUDE.md` (protocol, conventions), `docs/ARCHITECTURE.md`, `firmware/orangepi/tracker.py` (camera-tracker precedent), `main/Gyro*.cpp` (sensor-node firmware precedent), `desktop/shared/camera_math.py` (coordinate authority)
