## OTA updates

OTA (over-the-air) firmware updates push the registry-pinned binary to
each performer board over HTTP, no USB cable required.

### How it works

1. Each performer reports its current firmware version on every
   PING/PONG cycle.
2. The orchestrator compares it to the registry entry pinned for that
   board track (e.g. `child-led-esp32 v7.5.11`,
   `esp32s3-gyro-firmware v1.2.6`).
3. Outdated rows surface an **Update** button (v1.7.119: orange "vX
   available" badge). Click it; the orchestrator serves
   `http://<orchestrator>/api/firmware/serve/<board>` and the device
   pulls + flashes via Arduino-OTA.
4. After flashing, the board reboots and re-PONGs with the new
   version. The row flips back to a green "Up to date" badge.

### v1.7.119 changes

- **App-only OTA assets** (#870, #874). The orchestrator now serves
  only the application binary — not a bootloader/partitions/app
  merged image. This is what the OTA handler on every board has
  always expected; the merged image was a one-time mistake that
  bricked boards mid-update on older fleets.
- **otaSha256 pin** (#873). Every registry entry carries a SHA-256 of
  the canonical binary. The proxy verifies the cached file's hash
  before each serve; mismatch returns HTTP 502 instead of pushing a
  corrupted update.
- **Gyro START extraction** (#874). The OTA endpoint now extracts
  exactly the application partition from the merged build artifact
  on the build server, so what lands in the registry cache matches
  what the gyro's OTA handler can accept.

### Asking for OTA freshness

Click **Check for Updates** at the top of the Firmware tab to force a
GitHub release-list fetch — useful when you've just published a new
firmware tag and want the orchestrator to see it without waiting for
the periodic refresh.

**More info →** chapter 15, *Firmware & OTA Updates*.
