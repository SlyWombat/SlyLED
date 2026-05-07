## 15. Firmware & OTA Updates

The Firmware tab is the operator's single window onto every flashable
device on the rig: LED performers, the DMX-bridge, the gyro puck, and
the camera nodes. Every flashable device reports its current firmware
version up to the orchestrator on each PING/PONG cycle, so a stale
device shows as "outdated" within seconds of the orchestrator booting.

### Current production versions (orchestrator v1.7.83)

| Device | Track | Current | Channel |
| --- | --- | --- | --- |
| Orchestrator (Windows / macOS) | app | **v1.7.83** | installer (`SlyLED-Setup.exe`) |
| Android operator app | app | matches orchestrator track | sideload APK from `dist/slyled-android.apk` |
| LED Performer (ESP32) | `child-led-esp32` | **v7.5.11** | OTA |
| LED Performer (D1 Mini) | `child-led-d1mini` | **v7.5.10** | OTA |
| LED Performer (Giga child) | `child-led-giga` | **v7.5.2** | OTA |
| DMX Bridge (ESP32) | `dmx-bridge-esp32` | **v7.5.20** | OTA |
| DMX Bridge (Giga R1) | `dmx-bridge-giga` | **v7.5.20** | OTA |
| Parent firmware (Giga R1) | `parent-giga` | **v7.5.24** *(on hold — desktop orchestrator is the recommended runtime)* | USB only |
| Gyro Controller (ESP32-S3) | `gyro-esp32s3` | **v1.2.8** | OTA |
| Camera node (Linux SBC) | `camera-node` | **v1.6.3** | SSH deploy from Firmware tab |

The Firmware tab queries `firmware/registry.json` to know what the
"current" version is, so this table is regenerated automatically every
release; the operator never has to keep it in their head.

### USB Flash

1. Go to the **Firmware** tab.
2. Click the **USB Flash** card. The dropdown lists every binary the
   registry knows about for boards that flash over USB (LED ESP32 / D1
   Mini / Giga child / DMX bridge variants / Gyro Controller).
3. Plug the target board in and pick its COM port from the second
   dropdown.
4. Click **Flash** — progress shows percentage and a final
   "verification OK" before the board reboots into the new firmware.

The Gyro Controller (ESP32-S3) ships with USB-CDC serial in the
firmware. If a wedged build leaves the puck unable to enumerate over
USB, hold the **BOOT** button while plugging in to enter the manual
ROM bootloader; the Firmware tab then re-flashes through esptool's
recovery path.

### OTA (Over-the-Air)

1. Set WiFi credentials on the Firmware tab — these get pushed to every
   newly-flashed device.
2. Click **Check for Updates**. The tab shows a per-device comparison:
   reported version → registry version, with an **Update** button on
   anything outdated.
3. Click **Update** on any outdated performer. Mid-flash status comes
   back live; the device reboots automatically after verification.
4. New since v1.7.83: when a registry SHA-256 mismatches the on-disk
   binary (a download mid-update or a hand-edited registry), the
   orchestrator falls back to the GitHub release for that board's
   `releaseTag` rather than refusing the flash.

The diagnostic / development gyro builds (`esp32s3-gyro-test-firmware.bin`)
are deliberately hidden from the operator OTA UI — the Firmware tab only
offers production builds. The diagnostic binary is still present in
`dist/` for engineers running paired-board debugging.

### Firmware Registry

`firmware/registry.json` is the single source of truth for what version
the orchestrator believes ships with each release. Each entry carries:

- `id` and `name` for the OTA UI label.
- `version` (3-part semver) — what the operator's device should be
  running.
- `releaseTag` and `releaseAsset` — the GitHub release tag and the
  asset filename inside it, used by the OTA fallback.
- `sha256` — verification hash that the orchestrator checks before and
  after flashing.

Editing `registry.json` by hand is not recommended; `build_release.ps1`
keeps it in sync with the actual binary hashes on every release. The
Firmware tab refreshes the registry from GitHub on demand from the
**Refresh** button so a freshly-pulled installer immediately sees the
versions corresponding to its release tag.

---

