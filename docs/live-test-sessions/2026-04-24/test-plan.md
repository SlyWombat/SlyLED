# Live test plan — 2026-04-24 — mover calibration

**Session lead:** user (on basement rig, Windows host orchestrator, port 8080)
**Session support:** QA agent (this machine, WSL2, monitoring + screenshots + issue updates)
**Rig:** 2 movers + 1 floor-mount BeamLight, 3 ArUco markers (DICT_4X4_50 150 mm, floor), Orange Pi camera node at `192.168.10.235:5000` (2× EMEET 4K).

## Scope — open issues to exercise

| Issue | What to observe | Expected |
|-------|-----------------|----------|
| **#679 P1 #2** | `_cal_blackout` universe-wide zero — lit fixture B goes dark when cal on A ends | Reproduces: fixture B goes dark at cal end |
| **#679 P2 #5** | `_aruco_multi_snapshot_detect` universe-wide zero — fixture B flickers during markers pre-scan | Reproduces: fixture B flickers ~450 ms every pre-scan |
| **#679 P2 #6** | `/api/calibration/mover/<fid>/aim` POST mid-cal — cal thread's DMX corrupted | Reproduces: `/aim` returns 200, writes DMX during cal |
| **#679 P3 #7** | `FitQuality` after a run with no `force_signs` — check `/status` response | `mirror_ambiguity` flag absent (confirms bug) |
| **#679 P3 #8** | Restart orchestrator mid-cal — fixture stays hot at last-commanded aim | Reproduces: fixture lit after restart, no blackout |
| **#610 / #357** | Battleship default discovery path for an unseeded cal | Log shows `battleship_discover: N×M coarse probes` |
| **#651** | Dark-reference frame captured at cal start | Log / API shows `/dark-reference` call at t=0 |
| **#652** | `verify_signs` runs **before** fit_model | Log shows `verify_signs` → single `fit_model` call, not 4-combo search |
| **#653** | Phase budgets present in log; no `pendingTier2Handoff` on a successful run | Log: `phase '%s' budget N.0s`; status: `pendingTier2Handoff != true` |
| **#654** | `moverCalibrated = true` only after held-out parametric check passes | Status: `phase: "holdout"` then `complete`, flag set |
| **#655** | Adaptive settle escalation observed on real rig (beam movement between captures) | Log: `_wait_settled` escalation levels `0.4, 0.8, 1.5` |
| **#658** | Battleship confirm → second blink-rejection step | Log: `reflection-rejection blink` message on at least one probe |
| **#660** | Refine pass after battleship hit | Log: `coarse (%.3f, %.3f) → refined (%.3f, %.3f)` |
| **#661** | Adaptive grid density on 540°/270° fixture | Log: `N×M coarse probes (pan_range=540° tilt_range=270° beam=15°)` with N=8, M=6 |
| **#625** | Bracket-retry engages when beam is lost mid-convergence | Log: `bracket_step *= 0.5` sequence when a probe returns None |
| **#626** | Multi-snapshot ArUco aggregation (best-per-id by perimeter) | Log / status: `markersDetected`, ≥3 snapshots taken |
| **#627** | Auxiliary channels (laser / strobe / macro) zeroed at cal start | DMX monitor: all non-pan/tilt/dimmer channels read 0 mid-cal |
| **#647** | `engine_health` signal when engine stops mid-cal | Stop Art-Net engine during cal; observe health + error |
| **#678** | Android Live Stage — pillar renders from mid-stage up (bug) vs SPA correct rendering | Reproduces: Android pillar base at Z=1030 mm |
| **#662 (docs drift)** | `BRACKET_FLOOR` actual value ≈ 0.0039, not 0.002 | Appendix B §B.7 wrong |
| **#662 (docs drift)** | Legacy discovery actually uses adaptive settle | Appendix B wrong — `_wait_settled` is called from legacy path |

## Test sequence

### T0 — Pre-flight sanity (before any calibration)

1. `curl $ORCH/api/fixtures` — confirm ≥2 DMX movers on **the same universe** (needed for #679 P1 #2 test).
2. `curl $ORCH/api/cameras` — confirm camera reachable, has `fovDeg` + `rotation` set (post-#611 should be `fovType=diagonal` default).
3. `curl $ORCH/api/aruco/markers` — confirm ≥3 surveyed markers.
4. `curl $ORCH/api/aruco/markers/coverage` — confirm per-camera visibility.
5. `curl $ORCH/api/stage` — confirm `auto` sub-dict present (post-#628). If `stageBoundsManual != true`, bounds should match fixture + marker hull.
6. `curl $ORCH/api/settings` — snapshot for diff at end of session.
7. Screenshot: SPA Layout tab (3D viewport shows stage + fixtures + markers).

### T1 — Baseline successful markers-mode calibration

**Target:** mover A (pick one with a profile that has battleship range metadata).

Steps:
1. Fixture B illuminated via manual aim or test action — any colour, non-zero dimmer. Record the colour/state so we can verify it survives.
2. Launch cal: `POST /api/calibration/mover/<A>/start` with `{mode: "markers", warmup: true}`.
3. Poll `/status` every 500 ms → append to `snapshots/t1-status-timeline.ndjson`.
4. Capture SPA screenshots at each phase transition: warmup, battleship, confirming, mapping, fitting, holdout, complete.
5. Visual: watch fixture B — does it flicker during `_aruco_multi_snapshot_detect` (#679 P2 #5)?
6. At cal complete — does fixture B go dark (#679 P1 #2)?

Artifacts: `t1-status-timeline.ndjson`, `t1-orchestrator.log` (operator paste), `snapshots/t1-phase-*.png`.

### T2 — `/aim` mid-calibration race (#679 P2 #6)

1. Start another cal run (mover B this time).
2. Wait until phase = `battleship` (pan-probe visible).
3. `curl -X POST $ORCH/api/calibration/mover/<B>/aim -d '{"targetX":2000,"targetY":3000}'`.
4. Observe: does the POST return 200 and does the fixture visibly jerk? (Reproduces #679 P2 #6.)
5. After cal completes (or times out), check the fit quality in `/status`. Expect degraded RMS because of the injected perturbation.

Artifacts: `t2-aim-race.json` (the POST response + `/status` snapshot at the moment).

### T3 — Restart-mid-cal (#679 P3 #8)

1. Start a cal run. Wait until phase = `mapping`.
2. Operator hits Ctrl-C on the orchestrator window, relaunches.
3. Observe the fixture physical state: beam on, aimed somewhere? (Reproduces #679 P3 #8.)
4. `curl $ORCH/api/fixtures` — `isCalibrating` should be cleared. Flag that no blackout happened.

Artifacts: `t3-restart-state.json`.

### T4 — Cancel mid-cal (sanity + #679 P1 #2 confirm)

1. Fixture B illuminated again.
2. Start cal on A.
3. Wait until phase = `battleship` then `POST /api/calibration/mover/<A>/cancel`.
4. Expected: A blacks out immediately (foreground), B stays lit (targeted `/cancel` blackout per `:5600`).
5. Compare to T1 completion behaviour where B goes dark — that's the P1 bug.

Artifacts: `t4-cancel-state.json`.

### T5 — Engine health (#647)

1. Start a cal run. Wait until phase = `battleship`.
2. Operator toggles `POST /api/dmx/stop` (stops Art-Net).
3. Observe: does cal error out with an informative message, or does it loop silently?
4. Expected: `/status` surfaces an engine-health error within one tick.

Artifacts: `t5-engine-stop.json`.

### T6 — Docs-drift observations (#662 feedback)

Pull from the T1 log:
- Grep for `BRACKET_FLOOR` or the `bracket_step` sequence — note actual value.
- Grep for `_wait_settled` calls — confirm they fire in the legacy `discover` path, not just mapping.
- Grep for `verify_signs` — confirm it runs before `fit_model`.
- Grep for `battleship_discover: N×M` — confirm adaptive grid (8×6 for 540°/270°).

### T7 — Android 3D viewport (#678) — if Android device available

1. Install latest debug APK on phone, point at orchestrator.
2. Navigate to Live Stage screen on a project that has a Pillar stage object.
3. Capture screenshot of pillar rendering.
4. Open SPA on desktop, same project, Layout 3D viewport — capture screenshot for comparison.
5. Expected: Android shows pillar base at mid-stage (bug); SPA shows pillar base at floor (correct post-`5c3626f`).

## Tooling (ready on this machine)

- `tools/live_test_monitor.py` — long-running status poller. Tails `/status` every 500 ms, writes NDJSON per run, logs phase transitions to console.
- `tools/live_test_screenshot.py` — Playwright driver that takes SPA screenshots at named waypoints. Invoked by the monitor when a phase transition fires.
- `docs/live-test-sessions/2026-04-24/snapshots/` — output destination.

## Handshake — what I need from the operator before starting

- **Orchestrator URL reachable from WSL.** From WSL2 the Windows host is at `192.168.10.1` on the basement LAN or at the host.docker.internal mapping. Confirm one works: `curl http://<host>:8080/api/settings`. If neither does (firewall), we can proxy via a camera-ready Pi or run the monitor on Windows side with `cmd.exe`.
- **Fixture IDs** for mover A + mover B (so I can pre-bake URLs for the scripts).
- **The two movers should be on the same DMX universe** for the #679 P1 #2 test to fire. Verify via `/api/fixtures` — same `dmxUniverse` field on both.
- **Which mover is 540°/270° range?** (Makes T1 T6 "adaptive grid 8×6" test meaningful.)
- **Orchestrator log visibility.** Best path: tee the PowerShell `run.ps1` output to a file that's readable from WSL (e.g. `desktop/windows/orchestrator.log`), and share the tail with me between runs.

## Not in scope today

- Bracket-floor 16-bit fixture bug (#679 P1 #1) — needs a specific 16-bit fixture the basement doesn't have.
- Pixel-scoring 1080p centre bug (#679 P2 #4) — empirically verifiable but needs targeted tooling; skip in favour of field observations.
- `_median` n=2 bug (#679 P2 #3) — observable only in degraded scenarios; skip.
- Building docs/Gemini review flows — this session is QA, not dev.

## Artefact conventions

Match the 2026-04-22 session pattern:
- `step1-*` for T1, etc. (aligns with prior convention) OR `t1-*` (clearer in this doc — picking `t*` for this session).
- NDJSON for polled state timelines (one JSON object per line — easy to tail + grep).
- Markdown for human-readable summaries.
- PNG for screenshots, named by phase: `snapshots/t1-phase-<name>.png`.
- End-of-session `summary.md` cross-linking each test → what reproduced → which issue comment to post.
