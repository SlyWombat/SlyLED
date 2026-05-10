## 17. Troubleshooting

The table below covers the symptoms operators most often raise. Every
row points to the orchestrator version where the underlying behaviour
was last touched, so an operator running an older build can decide
whether to update or work around.

| Problem | What you'll see | Fix or version |
| --- | --- | --- |
| **Runtime view empty** | The 3D Runtime tab shows the stage but no fixtures. | Check that fixtures are positioned in the **Layout** tab. DMX-only rigs render correctly since v1.7.30. |
| **Beam cone wrong direction** | The 3D viz cone aims at the wrong wall. | Beam direction comes from the fixture's `rotation = [rx, ry, rz]` in stage space. Z is up; rx > 0 aims down. See chapter 4 for the full convention. |
| **3D viz cone disagrees with the physical head** | Cone in the viz points stage-left but the moving head is aimed stage-right. | Fixed in v1.7.52 (#806/#809): the canonical aim vector is the source of truth and the physical IK derives from it. If you still see the disagreement on v1.7.52+, re-save the fixture's Home and Secondary in the Set Home wizard. |
| **Calibrate-end pan jump** | Pressing release on calibrate snaps the head to a different pose than the gyro was reporting. | Fixed in v1.7.52 (#805). Pre-fix the legacy IK fallback was capturing the wrong aim vector at release time. Operators on v1.7.52+ who still see a jump should report it with the gyro firmware version (must be ≥ v1.2.4). |
| **Press Start blinks back to "start" on the gyro** | Operator presses Start after a WiFi gap, the gyro UI flashes claim-acknowledge for a frame, then reverts to IDLE while the orchestrator holds an orphan claim. | Fixed in v1.7.83 (#812 / #813 / #825). Press-Start now uses a 16-bit nonce + CLAIM_ACK, with HB_REP heartbeats to reconcile divergent state. If you see the symptom on v1.7.83+, check that the gyro firmware is ≥ v1.2.7 (registry will warn). |
| **Auto Brightness has no effect on the lights** | The Android Auto Brightness UI shows the master sliding with the music, but DMX heads and LED strips don't dim. | Fixed in v1.7.83 (#843). The fast-path POST now broadcasts `CMD_SET_BRIGHTNESS` to LED children and gamma-scales DMX dimmer / RGB at render time. Operators on older builds can fall back to the manual Settings → Global Brightness slider until they update. |
| **Looping playlist blacks out between iterations** | A single-item or multi-item playlist set to **Loop All** flashes everything to zero for one frame at every wrap. | Fixed in v1.7.83 (#840). Single-item loops route through the modulo-wrap playback path; multi-item loops pass `is_final=False` to suppress the natural-end blackout sweep until the playlist actually stops. |
| **Track action blacks out movers in unrelated timelines** | A timeline that doesn't reference a particular Track action still has its movers go dark whenever that action exists in the action library. | Fixed in v1.7.83 (#835). Track actions now only evaluate on timelines that reference them; orphan actions stay dormant. |
| **Show preset says "moving heads track / follow / chase X" but they don't** | A theme description promises tracking, but the rig just sweeps. | Fixed in v1.7.83 (#837). Theme descriptions now match the actual implementation: only Figure Eight and Spotlight Follow Person emit a Track action; the others sweep. |
| **Colour Wheel field appears in DMX Scene action editor** | The action editor's DMX Scene / PT-Move / Gobo Select panes had a "Colour Wheel" input that didn't do what the operator expected on hybrid RGB+wheel fixtures. | Fixed in v1.7.83 (#841 / #842). The Colour Wheel slot is now type-17 only; the bake/render layer derives the slot from RGB via `rgb_to_wheel_slot` for every other action type. A one-shot migration strips stale `colorWheel: 0` fields on first start of v1.7.83+. |
| **3D viewport not rendering** | Black canvas where the stage should be. | Use Chrome / Firefox / Edge with WebGL support. Check `chrome://gpu` for hardware acceleration. |
| **Performers not syncing** | A child shows offline in Setup but is powered up. | Check that the orchestrator and the child are on the same WiFi subnet. The Setup tab's **Refresh** rescans via mDNS + UDP broadcast. |
| **Canvas wrong size** | The Layout canvas is much smaller or larger than the room. | Stage dimensions (Settings → Stage) drive canvas size: `canvasW = stage.w × 1000`. Adjust stage width/height in metres rather than canvas pixels. |
| **OTA flash refused with SHA mismatch** | Firmware tab refuses to update with `sha256 mismatch`. | Fixed in v1.7.61 (#814). The orchestrator now falls back to the GitHub release for the registered `releaseTag` when the on-disk binary disagrees with the registry. If you still see this, click **Refresh** on the Firmware tab to re-fetch `registry.json` from GitHub. |
| **Gyro stale-reason latch never clears** | "Connection lost" stays on a gyro status row even after the gyro resumes streaming. | Fixed in v1.7.62 (#821) and again in v1.7.63 (#823). Press-Start clears the remote stale_reason; cache self-destructs on a transient read failure. |

If you hit something not in this table, the orchestrator's log
(Settings → Logging → enable file logging) captures every UDP send and
DMX render decision tagged by issue number — open an issue on GitHub
with the relevant section attached and a description of what the rig
was doing at the moment.

---

