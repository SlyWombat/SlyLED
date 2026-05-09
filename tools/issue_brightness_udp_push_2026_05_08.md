# Architecture: replace HTTP-based auto-brightness with UDP push

**Status:** Architecture decision. Replaces today's HTTP `POST /api/brightness` fast-path for Android Auto Brightness.

## Why the current architecture is fragile

The Android `MicAutoBrightness` driver runs a 20 Hz envelope follower and currently calls `repository.setMasterBrightness(master)` — a `POST /api/brightness` over HTTP — on every audio hop. Mic-driven brightness for music at 100 BPM (sharp beat attacks) needs sub-50 ms end-to-end latency to render the attack envelope cleanly. HTTP POST at audio rate fails this in several ways:

| Issue | Impact at 20 Hz |
|---|---|
| Per-POST WiFi latency variance (5–100 ms jitter) | Beat-aligned flashes arrive late and smear |
| TCP retransmit / connection-pool churn | Whole bursts of POSTs drop silently |
| Per-call rate-limit guard (`lastBrightnessJob?.isActive` skip in `LiveStageViewModel`) | One hung POST → all subsequent hops silently dropped (live test 2026-05-08: zero auto-brightness POSTs landed in 3.5 min of music despite UI showing master bouncing 150–255) |
| Server `_lock` contention with 40 Hz DMX tick + UDP listener | Tail-latency spikes; correlation between gyro UDP load and brightness latency |
| Phone audio clock ≠ orchestrator DMX tick clock | Aliasing; no shared timebase |

Removing the rate-limit guard alone is not enough; the protocol mismatch is the real cause.

## Decision

Android Auto Brightness publishes `master` as a UDP push packet to the orchestrator on `:4210`, mirroring the existing puck telemetry pattern. The orchestrator coalesces the latest value per DMX tick by simply writing `_settings["globalBrightness"]` on each packet — the next tick reads it.

## Wire format

New UDP CMD on the existing `:4210` binary protocol (UDP_VERSION = 5). Header is the standard 8-byte `<HBBI` (`magic=0x534C, version=5, cmd, epoch`) per CLAUDE.md.

| Cmd | Name | Direction | Payload |
|------|------|-----------|---------|
| `0x6D` | `CMD_AUTOBRI_PUSH` | phone → parent | **3 bytes**: `<BBB` = `master(1) flags(1) seq(1)` |

- `master` — 0..255, the value Android currently passes to `onMaster`.
- `flags` — bit 0 = `clipping`, bit 1 = `beat_pulse_active`. Reserved bits = 0.
- `seq` — wraps every 256 hops; lets the server detect drops in logs without per-packet ACK.

Picked `0x6D` because `0x66/0x67/0x69/0x6A/0x6B/0x6C` are gyro handshake; `0x6D` is the next free byte in the same logical group.

## Server side

`desktop/shared/parent_server.py::_udp_listener` adds a branch:

```python
elif cmd == CMD_AUTOBRI_PUSH and len(data) >= 11:
    master, flags, seq = struct.unpack_from("<BBB", data, 8)
    with _lock:
        _settings["globalBrightness"] = int(master)
    # Optional: stash flags / seq / source ip in _autobri_state for /api/auto-brightness/status
```

That's the entire server change. No coalescing logic needed — `_settings["globalBrightness"]` IS the coalesce buffer; the next DMX tick reads it.

The HTTP `POST /api/brightness` route stays as the canonical manual-slider path (SPA + Android slider both keep using it). Remove it from the Android auto-brightness loop only.

## Android side

`MicAutoBrightness.start(scope, onMaster)` keeps the same public callback shape. The repository call inside `LiveStageViewModel.kt` swaps:

```kotlin
// before — HTTP POST per hop
repository.setMasterBrightness(master)
```
```kotlin
// after — UDP fire-and-forget
udpClient.sendAutoBrightnessPush(master, flags, seq)
```

Drop the `lastBrightnessJob?.isActive` guard entirely — UDP send is non-blocking, no in-flight to track. Drop the `Log.w(TAG, "fast brightness POST", e)` exception handler scope; UDP send doesn't throw under normal conditions, and `DatagramSocket` send failures should log at INFO + a counter, not interrupt the loop.

Discovery: phone reads orchestrator IP from existing `ServerPreferences` (already used for HTTP). UDP socket is created once on Auto Brightness enable, closed on disable.

The manual slider in Settings / Live Stage continues to call `repository.setMasterBrightness(value)` over HTTP — that path is fine for a once-per-second human input and keeps `settings.json` write semantics consistent.

## Rate budget

- Audio hop: 20 Hz (50 ms) — same as today.
- UDP packet size: 8 byte header + 3 byte payload = **11 bytes**.
- Phone egress at 20 Hz: 220 B/s. Negligible.
- Server ingress at 20 Hz: trivially below the existing UDP listener load (puck PONGs + ACTION_EVENTs are larger and faster).

## Future direction (context, not part of this issue)

Long-term plan is for the orchestrator to use its own local microphone and run the envelope follower / beat detector locally — audio is **not** streamed from Android in that future world. Designing this issue's UDP path so it cleanly coexists with that future is the only forward-compat constraint: write to the same `_settings["globalBrightness"]` field a local-mic path would also write to. No client-API churn either way.

## What gets removed

- The `repository.setMasterBrightness(...)` call inside `LiveStageViewModel.setAutoBrightnessEnabled` and the `lastBrightnessJob` guard around it.
- The `Log.w(TAG, "fast brightness POST", e)` warning (no longer applicable).

## What stays unchanged

- `POST /api/brightness` HTTP route — manual-slider canonical path.
- The Android slider in Settings / Live Stage (still HTTP).
- `MicAutoBrightness` capture / envelope follower / beat detector logic on Android.

## Acceptance criteria

1. Android Auto Brightness ON + music playing → orchestrator's `_settings["globalBrightness"]` updates at ≥10 Hz under nominal WiFi conditions.
2. The fixture's actual DMX dimmer channel tracks the master value within 1 DMX tick (~25 ms at 40 Hz).
3. Disable Auto Brightness → no UDP packets sent; HTTP slider still works.
4. Toggling Auto Brightness rapidly does not leak UDP sockets (counter on the Android side: open == close at idle).
5. Test harness: extend `tests/test_global_brightness_contract.py` with a new cell that fires a `CMD_AUTOBRI_PUSH` packet via `socket.sendto` and asserts `_settings["globalBrightness"]` updates within one tick.

## Files expected to change

- `main/Protocol.h` — add `CMD_AUTOBRI_PUSH = 0x6D` constant. (Server-side parent_server.py also defines it.)
- `desktop/shared/parent_server.py` — add UDP listener branch. ~10 lines.
- `android/app/src/main/java/com/slywombat/slyled/audio/MicAutoBrightness.kt` — no change to capture loop; only wire the new sender.
- `android/app/src/main/java/com/slywombat/slyled/network/UdpClient.kt` (new) — small `DatagramSocket` wrapper with `sendAutoBrightnessPush(master, flags, seq)`.
- `android/app/src/main/java/com/slywombat/slyled/viewmodel/LiveStageViewModel.kt` — swap HTTP call for UDP send; drop rate-limit guard.
- `tests/test_global_brightness_contract.py` — new cell (per acceptance #5).
- `CLAUDE.md` — extend the UDP protocol table with `0x6D CMD_AUTOBRI_PUSH`.
