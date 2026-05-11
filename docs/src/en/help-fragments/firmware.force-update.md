## Force Update

The **Force Update** button runs an OTA flash over HTTP even when the
board is reporting as offline. Use it to recover a board whose PONG
heartbeat has stopped reaching the orchestrator but whose HTTP server
is still listening.

### When to use it vs the regular Update button

- **Update** (default) — only shown when the orchestrator has seen a
  recent PONG and the board's reported firmware version is older than
  the registry's pinned version. This is the normal path.
- **Force Update** — shown when the board's IP is on file but the
  orchestrator hasn't received a PONG within the offline threshold.
  Forces an HTTP POST to `http://<ip>/ota` with the registry binary,
  bypassing the UDP-derived freshness check.

### Why version-equality is the default

OTA is always version-pinned to the registry entry's `firmwareId`. If
the board already reports the pinned version, no upload happens — the
orchestrator returns `{ok:true, alreadyAtVersion:true}` instead of
re-flashing. This keeps repeat clicks safe (you can't accidentally
reflash a healthy board) and dodges the small failure window where an
in-flight reboot during flash bricks the device.

Force Update applies the same SHA-256 + size check (#873) against the
local cache before serving the binary — a corrupted or mismatched
cached file gets refused with a clear error rather than streamed onto
the device.

### When Force Update won't help

- Board IP is unknown (no recent ARP entry). Use the **USB Flash**
  card instead.
- Board is at the bootloader prompt with no working application. USB
  flash via `esptool` is the only path.
- For the gyro puck (ESP32-S3 USB-CDC), a wedged build means no serial
  + no `/ota` route — manual BOOT-button bootloader entry is required.

**More info →** chapter 15, *Firmware & OTA Updates*.
