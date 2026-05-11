## Auto Brightness

The **Auto Brightness** card on the Dashboard shows live local-audio
input driving the global brightness scalar. When the phone (or a
WASAPI loopback source) is streaming amplitude, every fixture's output
is multiplied by the live envelope value before the bake step writes
DMX — so the lights "breathe" with the music without any per-action
authoring.

### What the card shows

- **cur** — instantaneous envelope value (0–255). Updates at the
  registered source's sample rate.
- **range** — the configured min/max bounds. Values below the floor
  clamp to 0; values above the ceiling clamp to 1. Both are tunable
  per source in **Settings → General → Auto Brightness**.
- **globalBrightness** — the scalar currently applied to all fixtures
  (0–255). This is what the bake engine multiplies into every channel
  value.
- **last** — seconds since the last data packet. Anything > a few
  seconds usually means the source dropped — toggle the phone's
  controller-mode off/on to reconnect.

### How to verify the phone is streaming

1. Open the Android app's Settings tab.
2. Pick an input device (WASAPI loopback on the orchestrator host, or
   the phone microphone).
3. The Dashboard card should turn green with `last < 1.0s` within a
   second or two.
4. Move the **Sensitivity** slider; you should see `cur` track in real
   time.

### Pitfalls

- WASAPI loopback works on Windows orchestrator hosts only. On Mac,
  use the phone microphone (#879).
- An entry with no `range` means the orchestrator never got a
  registration packet — usually a stale APK that doesn't speak the
  v1.7.126 protocol (#879).
- Auto Brightness ≠ master dimmer. The card scales **everything**,
  including the master. To dim independently use **Settings → DMX →
  Master**.

**More info →** *Remote Control* appendix.
