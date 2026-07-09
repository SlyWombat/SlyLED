# SlyLED Next Steps — July 2026

**Status:** v1 — planning document. Produced from a full six-subsystem code review (2026-07-09) at HEAD `a9f84e5` (v2.1.1), the 87-issue open backlog, and the new MMwave design (`docs/design/mmwave_tracking.md` v2).
**Author:** opened 2026-07-09.
**Scope:** the whole repo — firmware (`main/`), orchestrator (`desktop/shared/`), SPA, camera node, Android/iOS, tests/build/docs infra — plus the sequencing of the new MMwave radar subproject against the debt that review found.

### Revision history

- **v1** (2026-07-09): initial. Review findings triaged into: critical fixes (§2), MMwave-blocking enablers (§3), the MMwave build itself (§4), guardrails (§5), and debt batches (§6); sequenced in §7; issue mapping in §8.

---

## 1. Where the project stands — honest assessment

The good news first, because it's real: the codebase is much healthier than a 20k-line Flask monolith and a multi-board Arduino sketch usually imply. Wire-protocol handling in firmware is disciplined (consistent length gating, nonce/ACK handshakes, integer-only LED render paths). The orchestrator has zero bare excepts, universal network timeouts, and issue-linked comments everywhere. The Android Control-tab redesign was implemented faithfully to its spec. Test volume is genuinely large (~150 test files, 104 of which drive the Flask app headlessly).

The bad news clusters into three shapes, and it's worth naming them because they explain "how did we get here" (many hands — human and AI — over many sessions):

1. **A few outright dangerous defects** that reviews surfaced (data-loss-grade persistence, unverified OTA, latent buffer overflows). §2.
2. **Half-finished migrations.** The #600 rotation convention landed server-side but the SPA still has pre-#600 reads/writes in live editors; an ES-module refactor was abandoned leaving six orphan files that shadow live code; the #784 aim overhaul deleted files that CLAUDE.md and CI still reference, making the *documented mandatory workflow literally unfollowable*. This is the classic failure mode of AI-assisted sessions that end before the sweep does — each one left a landmine for the next session.
3. **Hand-synced twins with no gate.** The same math/tables exist in Python + JS + Kotlin + Swift (+ C++ for effects), kept aligned by comments saying "must match". They have already drifted (action-name tables, AUTOBRI PDU fields, rotation indices). Only one twin pair (fixture shortcuts, JS side only) has an executable gate.

The fix for shape 1 is patches. The fix for shapes 2 and 3 is **guardrails** (§5) — CI that actually runs, parity tests for every twin, and CLAUDE.md kept truthful — so that future sessions (mine included) physically can't leave these behind silently.

MMwave's place in this: the radar subsystem *depends on* several of the rotten spots (temporal-object fusion, fixture-type handling, the tracker precedent, OTA, the build scripts). Doing MMwave first would mean building on them; the sequencing in §7 pays down exactly the debt on MMwave's critical path and nothing more, in parallel with the C61 bench work.

## 2. Critical fixes — do these regardless of everything else

Ranked. None are large; all are dangerous or already user-visible.

| # | Fix | Where | Why it's critical |
|---|-----|-------|-------------------|
| C1 | **Atomic persistence + loud corrupt-file handling.** `_save` → tmp + `os.replace` (pattern already exists at `remote_orientation.py:654-667`); `_load` → quarantine corrupt JSON as `.corrupt` and log, never silently return defaults. | `parent_server.py:178-186` | A crash mid-write truncates `fixtures.json`/`timelines.json`; next boot silently loads empty and **re-saves over the wreckage**. Single worst data-loss vector; ~1 hour. Likely the root cause class behind P0 **#739** (imported project vanishes on restart). |
| C2 | **OTA SHA-256 verification.** `expectedSha256` is parsed and plumbed through every entry point, then ignored by both `otaStartUpdate` implementations. Registry already ships `otaSha256` per board. | `OtaUpdate.cpp:31-67,106-142` | Firmware flashes over plain HTTP with no integrity check while headers claim verification exists. Also unblocks the #875 poisoned-cache contract. The MMwave node inherits whatever this flow is. |
| C3 | **Parent.cpp `p += snprintf` overflow idiom** — clamp `p` to `end` after every write. | `Parent.cpp:909,943,1365` | Latent memory corruption, reachable with a real 8-child/8-string registry (≈4.7 KB into an 1800-byte buffer). |
| C4 | **SPA #600 rotation violations.** Side-panel displays `rotation[1]` as Pan but saves pan to `[2]` (field snaps back on entry); `showNodeEdit`/`applyNodePos` write the pre-#600 order so the server reads the user's roll as pan; beam cones render panned by roll post-migration. | `app.js:943,966,985-990,1065,1104`; `scene-3d.js:529,604-611,534`; `emulation.js:393-399` | Live editors corrupting mover aim data **today**, in the exact convention CLAUDE.md says never to hand-index. |
| C5 | **Reflected-origin CORS** — allowlist the SPA origin (and app origins) instead of echoing any Origin. | `parent_server.py:20005-20017` | Currently defeats the `/api/shutdown` CSRF header; any web page in the operator's browser can hit shutdown/factory-reset/SSH-deploy. |
| C6 | **Lock the unlocked mutations**: `api_object_delete` rebinds `_objects` outside `_lock`; WLED probe loop mutates children unlocked; fixture PUT tears multi-field updates under live DMX playback. Adopt the rule "any write to a persisted collection holds `_lock`". | `parent_server.py:11000-11007,1367-1375,3030-3140` | Silent object loss and one-frame wrong-address DMX. |
| C7 | **`udpBuf[160]` caps OTA URLs at 83 chars** — packets silently dropped; grow to ≥384 + warn on oversize. Gyro also never calls `otaConfirmBoot`/`otaCheckConfirm` (no rollback confirm on the one board where a wedged build means BOOT-button surgery). | `Globals.h:17`; `main.ino:97-110,344-354` | UDP OTA with a GitHub release URL almost certainly fails silently right now. |
| C8 | **Camera tracker unit bug**: the "500 mm" re-ID gate is actually 500 *pixels* (`_px_to_stage` is never set); and orchestrator fusion orphans the losing cameras' update streams (their `PUT /pos` 404s until TTL). | `firmware/orangepi/tracker.py:19,35,196-208`; `parent_server.py:10865-10967` | Wrong behaviour today, **and this is the code MMwave's fusion was designed to reuse** — fix before inheriting. |
| C9 | **SPA bug #880**: `loadDmxProfiles` referenced but never defined — Settings → Profiles throws. Plus `/test/pin` calls `FastLED.show()` from the HTTP thread while ledTask renders (RMT crash risk, `UdpCommon.cpp:373-396`). | — | Both are known/user-visible breakage. |
| C10 | **Shipped docs breakage**: two walkthrough PNGs missing (validator FAILs at HEAD), homepage hero image missing, FR help-fragments directory absent (13 EN fragments, 0 FR). | `docs/src/*/02-walkthrough.md`; `docs/src/marketing/hero.md:11` | Published manual and public site are broken now. |

## 3. Enablers — the debt on MMwave's critical path

These are refactors the reviews independently recommended *and* the MMwave design needs. Doing them first turns radar integration from a shotgun edit into a registry entry.

- **E1 — Fixture-type registry, server + SPA.** `fixtureType` is a closed enum hand-checked in ~56 places in `parent_server.py` and 7+ `ft==='led'|'dmx'|'camera'` chains in the SPA (side panel, edit modal, both 3D mesh builders, dashboard counts, sidebar). Introduce a per-type descriptor (allowed fields, validator, badge, 3D mesh builder, tracking capability). Without it, adding `radar` is a 2–3 day sprawl-edit; with it, half a day — and the *next* sensor type is free.
- **E2 — Source-agnostic temporal-object fusion.** `_fuse_temporal_objects` (`parent_server.py:10865`) assumes camera pixel provenance (`_pixel_box_to_stage_anchors`). Lift to a per-sensor-weight interface, and fix the fused-away-ID orphaning (C8) at the same time — the radar fusion module plugs into this seam.
- **E3 — UDP dispatch table.** Replace the ~420-line `elif cmd ==` chain in `_udp_listener` (:1521-1913) with `{cmd: handler}` registration and module-level handler functions (precedent: `_handle_autobri_push`). Makes `0x70 MMW_TARGETS` a one-line registration and every handler unit-testable without a socket.
- **E4 — Data-drive `build_release.ps1`'s firmware step from `registry.json`.** Step 3 is unrolled per-board; consequence today: `dmx-bridge-esp32` never auto-builds and a gyro-only edit version-bumps unrelated boards (shared source hash). Loop over registry entries (they already carry `fqbn`/`buildFlags`/`onHold`) with per-board hash scoping, and add an optional **`arduinoDataDir` field — this is precisely the hook the isolated C61 toolchain needs** (design doc §4.2). Lift the machine-specific constants (COM7, JDK/SDK/Inno/OneDrive paths) into a config file while in there.
- **E5 — Truthful protocol documentation.** CLAUDE.md's UDP table is stale against `Protocol.h` (says v4, PONG 133/141; reality v5, 134/142 with fwPatch; 0x50-0x51 and 0x60-0x65/0x68 missing; 0x67 gained a reason byte). Since the MMwave sketch will carry **duplicated wire definitions guarded by a parity test** (design doc §4.2), the source of truth must be right first. Sync the table, then build the `main/Protocol.h` ↔ `mmwave/` parity check against it.

## 4. The MMwave subproject itself

Sequenced issue list lives in the design doc (`docs/design/mmwave_tracking.md` §12); summary of how it slots against §3:

1. **Isolated C61 toolchain** (design Issue 1) — lands *via* E4's `arduinoDataDir` mechanism rather than as a one-off script hack.
2. **Bench validation** (design Issue 2, checklist §10) — pure hardware work, **can start immediately**; needs no repo changes.
3. **`mmwave/` sketch** (Issue 3) — onboarding/OTA/PONG per the shared flows; inherits C2 (SHA verify) and C7 (URL cap) fixes.
4. **`0x70 MMW_TARGETS`** (Issue 4) — one-line registration once E3 lands; parity test per E5.
5. **`radar` fixture type** (Issue 5) — a registry entry once E1 lands.
6. **`radar_fusion.py`** (Issue 6) — plugs into E2's seam; borrows the fixed (C8) association/coast logic; adds Kalman + M-of-N.
7. **Calibration walk + solver** (Issue 7), **multi-unit field test** (Issue 8), **FTM spike** (Issue 9, optional), **privacy line** (Issue 10), **manual chapter EN+FR** (Issue 11).

Still-open operator decisions (design doc §11): target venue/node count, driving use case (follow-spot vs zone effects — sets the latency/accuracy bar), mounting doctrine, calibration-walk UI home. Note the existing track-action issues **#634/#640/#641** (EMA smoothing, per-fixture aimTarget, Hungarian head assignment) are exactly the consumers radar tracking feeds — they become more valuable, not obsolete.

## 5. Guardrails — so the next session's mess is caught, not archived

Direct response to the recurring pattern in §1. Cheap, high-leverage, mostly one-time:

- **G1 — CI that runs the tests.** Nothing in `.github/workflows/` executes the Python suite despite ~850 assertions and a ready `tests/regression/run_all.py` + `tests/docker/`. Add `python-tests.yml` (PR: run_all; cron: `--weekly`), and wire in `tools/docs/validate_screenshots.py` (currently failing) and `drift_check.py`. Add an Android workflow running the existing 6 JVM test files (today they never run automatically). iOS: at minimum an XCTest target exists so tests *can* be added.
- **G2 — Fix the unfollowable CLAUDE.md.** The #733 cal-pipeline checklist mandates running `tools/emulate_smart_pipeline.py` against `tests/fixtures/cal/corpus.json` — both deleted in #784. Replace with the real gates (`tests/aim/test_sphere.py`, `test_routes.py`); fix the test table (`test_mover_calibration.py` doesn't exist) and the `coverage_math.py` references; sync the protocol table (E5). Also update `docs-drift.yml` + `drift_check.py` SOT lists, which still watch the deleted `mover_calibrator.py`/`parametric_mover.py` instead of `desktop/shared/aim/**` — real IK changes currently trigger nothing.
- **G3 — Parity gates for every hand-synced twin.** The recipe exists (`tests/test_fixture_shortcuts.py` runs the JS via node against a corpus). Extend to: `rotationFromLayout`/`camera_math`, `_aimUnitVector`, `_s3dStringDirFromRot`; the 4-copy action-name table (serve it from the server, or gate it); the spec-promised `FixtureShortcutsTest.kt` and a Swift XCTest against the same corpus (grow the corpus past its single profile); and — new — the `main/Protocol.h` ↔ `mmwave/` wire-parity test.
- **G4 — Finish test-data isolation.** `SLYLED_DATA` is set in 2 of ~104 orchestrator-importing tests; an ad-hoc Windows test run can still write into the live operator project (the exact failure commit `4add89f` was written to prevent). One shared bootstrap/`conftest.py` closes it.
- **G5 — Kill the trap files.** Delete: `camera/` (byte-verified stale mirror of `firmware/orangepi/`, 10+ releases behind, zero references), the six orphaned SPA ES-module files (shadow live code with *different* behavior), dead 2D renderers `emuDraw`/`_dashEmuDraw` (the last consumers of the legacy rotation convention), `PointerModeOverlay.kt` (spec'd for deletion, still ships), root `quinled_esp32.ino` + `quinled_esp32/` (divergent orphan sketches, one includes `arduino_secrets.h`), `tests/user/test_pt_sweep_show.py` (loads a deleted `.slyshow`), `capture_screenshots.py` (hardcoded old-project OneDrive path), `test.ps1` (Giga-era duplicate), and the fossil root `MEMORY.md` (still points at the Giga-LED-Project remote — superseded by CLAUDE.md).

## 6. Remaining debt batches (scheduled, not urgent)

- **B1 — parent_server.py blueprint split.** 20,329 lines, 309 routes, one 7,900-line "Fixtures" section holding five subsystems. Split mechanically along the existing section banners, one section per PR, shared state module first. Do **after** E1–E3 (they shrink and de-tangle the seams the split will cut along).
- **B2 — Serve the SPA/orchestrator behind waitress** instead of the Flask dev server; optional shared-token gate on destructive endpoints (shutdown, factory reset, flash, SSH deploy).
- **B3 — Firmware polish batch:** Mbed Giga WiFi reconnect supervision (a Giga that loses the AP stays offline until power-cycle); Giga-child brightness no-op (`GigaLEDInternal::setBrightness` never called — `CMD_SET_BRIGHTNESS` does nothing on Giga); implement-or-delete `CMD_OTA_STATUS` (0x51: defined, tracked, never sent); right-size truncating HTTP bodies (`Child.cpp:914` config saves silently mangled at >400 B, then persisted to NVS); DMX bridge UART re-init 40×/s → proper break generation; strip the legacy embedded Giga SPA (~700 lines serving 4 of 13 action types) to JSON-API + redirect.
- **B4 — SPA hygiene/perf:** route `refreshLiveGrid` through the shared 5 Hz poller (#859's missed spot); stop `_remotesTimer` polling forever from hidden tabs; LED dots → `THREE.InstancedMesh` (a 20-fixture rig approaches ~8k draw calls today); a connection-state pill (Android parity — a dead orchestrator currently freezes silently); three.js r137 upgrade consideration.
- **B5 — Mobile:** close the iOS↔Android drift (parity pinned at v1.8.2, Android at v1.8.13; align the AUTOBRI flags/epoch fields; decide the Degraded write-queue question — iOS implements §6.1, Android doesn't); iOS `/api/objects` rendering (prerequisite for radar people showing on iPhone); radar-node badge cases in both Status screens when the node exists.
- **B6 — Repo weight:** 239 MiB pack; firmware `.bin`s, PDFs, DOCX re-committed every release. Move built artifacts to releases/LFS and stop tracking them (the `.gitignore` patterns already exist but are dead letters for tracked files).
- **B7 — Docs debt:** FR chapter-numbering off-by-one (8 chapters); GitHub-free scrub leftovers (social icon in `astro.config.mjs`, 11 marketing links); `validate_screenshots.py` blind spots (marketing/, subdirectories); mojibake in `registry.json` descriptions.

## 7. Suggested sequencing

```
Phase 0  (days)     C1–C10 critical fixes  +  G2 (truthful CLAUDE.md)  +  G5 (trap-file deletions)
                    └ independent, small, high value; many are one-sitting fixes

Phase 1  (1–2 wks)  G1 CI + G4 data isolation + G3 parity gates          [guardrails]
         parallel:  E4 registry-driven build (+arduinoDataDir)           [build]
         parallel:  MMwave Issues 1–2: C61 toolchain + radar bench       [hardware — no repo deps]

Phase 2  (2–3 wks)  E1 fixture-type registry → E2 fusion seam → E3 UDP dispatch → E5 protocol sync
         then:      MMwave Issues 3–5: sketch, 0x70, radar fixture type

Phase 3  (2–4 wks)  MMwave Issues 6–8: fusion, calibration walk, multi-unit field test
         parallel:  B5 mobile radar surfacing; B1 blueprint split can start

Ongoing             B2–B4, B6–B7 as filler; MMwave Issues 9–11 (FTM spike, privacy line, manual chapter)
```

Rationale: Phase 0/1 items are exactly what makes every later phase safe to do with AI assistance at speed — after them, a regression shows up in CI instead of in the next review. The radar bench work (the fun part) starts on day one because it depends on nothing here.

## 8. Issue mapping

**Existing issues this plan directly advances:** #739 (P0 — C1 is the likely fix), #875 (C2), #880 (C9), #859 (B4), #888/#15-#19 (B5 closes the loose ends), #787–#797 test overhaul program (G1/G4 are its backbone), #634/#640/#641 track actions (consumers of §4), #409 simulated person-tracking validation (natural acceptance test for radar fusion), #806 aim-vector-as-canonical-state (align with E1/E2 design), #1 mDNS discovery + #2 WebSocket streaming (unchanged long-horizon).

**New issues to file** (≈24): one per C1–C10 (10, some combined), E1–E5 (5), G1–G5 (5, G2+G5 could be one "truth sweep"), plus the 11 MMwave issues from the design doc (of which Issue 1 merges into E4's). Suggest filing Phase-0 and MMwave issues immediately and batching the rest as milestones.

---

*Review provenance: six parallel subsystem reviews (firmware, orchestrator, SPA, camera node, mobile, infra) run 2026-07-09 against HEAD `a9f84e5`. Findings above cite file:line as of that commit.*
