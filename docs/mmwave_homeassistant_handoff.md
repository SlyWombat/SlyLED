# Rd-03D + ESP32 people detection — handoff notes for the HomeAssistant project

Everything the SlyLED MMwave subproject learned about this hardware that applies to
using the same sensor for room presence/people detection in HomeAssistant. Written
2026-07-19 from bench-validated results (SlyLED #908, hardware confirmed end-to-end
2026-07-10 with a real person). Self-contained — no SlyLED repo access needed —
but reference implementations are pointed at where they help.

## 1. Hardware

| Part | MPN | Notes |
|---|---|---|
| Radar | **Ai-Thinker Rd-03D** (G550 board revision) | 24 GHz FMCW, 1TX/2RX, multi-target tracking |
| MCU (what we use) | ESP32-C61-DevKitC-1-N8R2 | WiFi 6; see §6 for its toolchain pain |
| Harness | JST 1.25 mm 4-pin pigtail (Molex PicoBlade-compatible) | fits the G550's fitted connector |

**You do not need the C61.** The radar only needs 5 V, GND, and one 3.3 V hardware
UART at 256000 baud — any classic ESP32 / C3 / S3 works and saves you the C61's
toolchain grief (§6). If you want an ESPHome path, deliberately pick a chip ESPHome
already supports well; the C61 is fresh silicon (Arduino core support Dec 2025) and
ESPHome support should be verified before committing to it.

### Radar capabilities and hard limits (from Ai-Thinker manual + our bench)

- **Max 3 simultaneous tracked targets** (multi-target mode). Hard ceiling per sensor.
- **~8 m range, ~±60° azimuth** — one sensor covers a wedge, not a house.
- **2D only** (azimuth plane): X/Y in mm + radial speed. No height.
- **Angle is the weak axis** — range is tight, cross-range error grows with distance
  (2-RX phase interferometry). Fine for room presence; don't expect cm-accurate maps.
- **Stationary people fade out.** Static-clutter rejection is baked into the module —
  a motionless person disappears after a while. For occupancy you MUST hold/coast
  presence past target loss (§5). This is the single most important behaviour to
  design around.
- **No RF configurability**: mode switch + basic detection params only. No TX power,
  chirp, or frequency control. Multiple units in one space coexist (asynchronous
  FMCW → occasional transient ghosts, not systematic failure) — debounce ghosts
  with N-consecutive-frames confirmation.
- **~10 Hz frame rate** measured on real G550 hardware (manual says 10–15 Hz).
- Supply 4.5–5.5 V ≥200 mA (~0.2 A); **UART logic is 3.3 V** — no level shifter.

## 2. Wiring (bench-validated)

G550 fitted connector, silk `5V/GND/TX/RX`. Edge pads also exist
(`RX TX GND DEBUG DP DM 5V`) — DEBUG/DP/DM are for Ai-Thinker's PC tool, unused.

| Rd-03D | ESP32 | Note |
|---|---|---|
| 5V | 5 V rail | |
| GND | GND | |
| TX (radar out, a.k.a. OT1) | any free GPIO → UART RX | we use GPIO2 on the C61 |
| RX (radar in) | any free GPIO → UART TX | we use GPIO3 on the C61 |

```cpp
Serial1.begin(256000, SERIAL_8N1, /*rx=*/2, /*tx=*/3);
```

- **Hardware UART required** — 256000 baud is not reliable bit-banged (SoftwareSerial).
- Field diagnostic: if you're on the wrong RX pin you get noise whose byte count
  scales with the read baud, not a decodable stream. Right pin + right baud =
  clean 30-byte frames at 10 Hz, zero parse errors sustained.
- Mounting: antennas face the room; put metal (backplate) behind the module to kill
  the back lobe, and don't park the MCU directly behind the radar. Don't stack the
  radar over the ESP32's WiFi antenna.

## 3. UART protocol — data frames

The module streams binary frames at 256000 8N1, ~10 Hz:

```
AA FF 03 00 | target0 (8 bytes) | target1 (8 bytes) | target2 (8 bytes) | 55 CC
```

30 bytes total: 4-byte header, 3 fixed target slots, 2-byte tail. All words are
**little-endian uint16**. Per-target slot:

| Offset | Field | Decoding |
|---|---|---|
| +0 | X, mm (lateral; + = right of boresight from radar's PoV) | signed-magnitude, see below |
| +2 | Y, mm (boresight distance; ≥ 0 for real targets) | signed-magnitude |
| +4 | speed, cm/s (radial; sign = toward/away) | signed-magnitude |
| +6 | "distance resolution", mm (diagnostic) | plain uint16 |

An **all-zero slot is empty** — target count = number of non-zero slots.

**The signed-magnitude encoding is counterintuitive — get this right:**

```c
// MSB SET means POSITIVE: +(raw & 0x7FFF). MSB clear means NEGATIVE: -raw.
int16_t mmwSigned(uint16_t raw) {
  if (raw & 0x8000) return (int16_t)(raw & 0x7FFF);
  return (int16_t)(-(int16_t)raw);
}
```

Parse defensively: hunt the `AA FF 03 00` header byte-by-byte and validate the
`55 CC` tail before accepting a frame — command ACKs (§4) interleave with data
frames on the same line, and a tail check plus resumed header-hunt recovers
cleanly from desync. Reference implementation (zero-alloc state machine, ~120
lines, bench-proven zero parse errors): `mmwave/MmwUart.cpp` in the SlyLED repo.

## 4. UART protocol — commands

The module boots in single-target mode. To enable **multi-target trajectory mode**,
send once after power-up (we send it ~50 ms after opening the UART):

```
FD FC FB FA 02 00 90 00 04 03 02 01
```

(Single-target mode is the same frame with `80 00` in place of `90 00`.) The module
replies with an ACK frame starting `FD FC FB FA` — you can ignore/parse-past it;
the mode change was confirmed working on our bench (mode-command ACK observed,
3-slot frames follow). Command frames also exist for basic detection parameters;
nothing else is configurable.

## 5. Behaviour you must design around (bench-measured)

- **Static-person fade (the big one).** Once someone stops moving, the module stops
  reporting them. For a HomeAssistant occupancy sensor: treat "target present" as a
  trigger and hold occupancy with a generous off-delay (tens of seconds to minutes
  for a living room where people sit still), rather than mapping target-count
  directly to presence. SlyLED coasts tracks 3 s for *tracking* continuity and then
  relies on a fusion layer; pure *occupancy* wants a much longer hold. We are
  measuring the exact fade time next (SlyLED #908 item 4) — ask us for the number,
  or measure it: stand still and time target-loss (our capture tool
  `tools/mmwave_bench.py` reports it directly).
- **Transient ghosts**, especially with two modules facing each other. Require
  N consecutive frames (we use 3) before declaring presence. Two-node ghost-rate
  characterization is also pending on our side — placement rule of thumb: avoid
  direct boresight-to-boresight facing; tile wedges side-by-side with overlap.
- **Close pairs merge.** Two people near each other often blob into one target.
  Target count is a lower bound on occupancy, never an exact people-count.
- **Coverage**: one module per room-wedge. Multiple modules in one ESP or one per
  room both work; they don't interfere beyond the transient ghosts above.

## 6. If you do use the ESP32-C61 (our exact recipe)

Only follow this if you insist on the same MCU; otherwise skip.

- Needs **arduino-esp32 core ≥ 3.3.5** (we use 3.3.10) and Espressif's own package
  index URL (`https://espressif.github.io/arduino-esp32/package_esp32_index.json`) —
  Arduino's default index copy omits the C61 libs tool dependency.
- **No package index ships `esp32c61-libs`** (as of 2026-07): out-of-the-box compile
  fails with `bootloader_qio_80m.elf does not exist`. Graft the libs from
  pioarduino's compile skeleton: download `c61_a9de5ec5b9_compile_skeleton.zip`
  from `pioarduino/platform-espressif32` releases (tag `c61-skeleton`), copy its
  `esp32-arduino-libs/esp32c61/*` into
  `<arduino-data>/packages/esp32/tools/esp32c61-libs/<core version>/`.
  Replace with the official tool when Espressif publishes one.
- FQBN: `esp32:esp32:esp32c61:FlashSize=8M,PartitionScheme=default_8MB` (N8R2 is
  8 MB flash; the default 4 MB scheme leaves no dual-OTA headroom). Add
  `CDCOnBoot=cdc` if you monitor serial over the **native** USB port.
- Flashing over native USB works; esptool auto-detects the C61.
- `WiFi.mode(WIFI_STA)` must precede `WiFi.macAddress()` on this core, or the MAC
  reads all-zeros (cost us a bench session).
- Boot warning `MSPI Timing: Failed to allocate dummy cacheline` with PSRAM
  disabled: benign in our experience so far.
- GPIO map on the C61 DevKitC-1 (why we chose 2/3): GPIO7/8/9 are strapping pins
  (8 = RGB LED), 10/11 are the UART0 console, 12/13 are native USB, 14 is PSRAM
  CS on the N8R2. GPIO0–6 are the free set.

**Multi-node power:** the DevKit's USB-C ports are device-only (diode-isolated,
can't pass power out). Chain nodes through the 5V/GND *header pins* (rail-direct,
bidirectional), ~0.7 A per node, first segment carries the whole chain; never feed
pins and USB simultaneously on one node. Fine to ~2–4 nodes; beyond that, 12–24 V
bus with per-node bucks, or one PoE 802.3af→5 V splitter per node.

## 7. HomeAssistant integration sketches

Options, simplest first:

1. **MQTT from an Arduino sketch** — parse per §3/§4, publish
   `occupancy` (with the §5 hold logic) and optionally `target_count` /
   nearest-target distance to MQTT; HA `mqtt` binary_sensor + sensors. Most
   control, no ESPHome dependency, and our parser code ports as-is.
2. **ESPHome external component** — ESPHome has mature support for the *similar*
   HLK-LD2450 (also 3-target X/Y/speed UART radar), and community external
   components for the Rd-03D exist; frame format differs from LD2450 (different
   header and the §3 signed-magnitude quirk), so verify any component against §3
   before trusting its numbers. A custom UART component wrapping the §3 parser is
   ~an evening of work.
3. **Point at the SlyLED node firmware** (`mmwave/` sketch) only if you want its
   extras (UDP streaming, OTA, status web page with live target table, NVS WiFi
   provisioning) — it speaks SlyLED's orchestrator protocol, not MQTT, so for HA
   you'd replace the UDP layer anyway. The UART + WiFi + web-page parts are the
   reusable bits.

Privacy note worth carrying into any HA writeup: this is presence/position radar —
no camera, no imaging, no identity. It can't tell people apart; it reports up to
three anonymous (x, y, speed) points.

## 8. Reference pointers (SlyLED repo)

- `mmwave/MmwUart.cpp` — the bench-proven frame parser + mode command.
- `mmwave/MmwConfig.h` — pins/baud/staleness constants.
- `tools/mmwave_bench.py` — UDP-side capture/characterization tool (jitter, fade
  timing, packet loss); the *measurement method* applies even if your transport
  is MQTT.
- `docs/design/mmwave_tracking.md` — full design doc: hardware feasibility (§2–§3),
  placement/coexistence rules (§7), calibration ideas (§6).
- Ai-Thinker Rd-03D user manual + specification — the primary sources; design doc
  §13 has the links.
