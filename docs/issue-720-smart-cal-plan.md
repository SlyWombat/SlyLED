---
issue: 720
title: SMART moving-head calibration mode — implementation plan
status: planning
depends_on: []
---

# Issue #720 — SMART calibration mode: implementation plan

This is a multi-PR plan. Each PR section is self-contained and lists the files
it touches, the API/JSON shapes it adds, the test it must ship with, and the
gating criteria for merge. Land in order — later PRs depend on shapes
introduced earlier.

## SMART is the only model — legacy modes are slated for deletion

Every prior calibration mode (`all-auto`, `markers`, `v2`, `legacy`) has
failed in practice. SMART is not "another mode beside them" — it's the
**singular replacement**. Once SMART is proven on real fixtures (PR-6
merge gate), the others get deleted in PR-7. This means:

- The mode `<select>` exists transiently (PR-2 through PR-6) only to allow
  side-by-side validation of SMART against the legacy paths during the
  rollout. After PR-7 the pulldown goes away — the calibration card just
  *is* SMART.
- The calibration record's `method` field becomes informational-only
  (always `"smart"` for any fixture calibrated by the live system).
  Pre-existing `method:"legacy"` / `method:"markers"` / etc. records are
  treated as stale and the operator is prompted to re-run SMART.
- The `aim` path (`POST /api/calibration/mover/<fid>/aim`) does **not** get
  a "fall back to legacy grid interpolation" branch. If a fixture has no
  `model` (no SMART calibration), the aim endpoint returns
  `error: "fixture_not_calibrated"` and the SPA prompts the operator to
  run SMART. Grid interpolation is going away.
- The mover-control engine (gyro + Android remote) currently owns its own
  private pan/tilt math. PR-2.5 unifies it onto the SMART model via the
  new angular aim endpoint (PR-1.5). After PR-2.5 there is exactly **one**
  pan/tilt → DMX implementation in the codebase.
- Tests for the deleted modes (`test_mover_calibration.py` legacy paths,
  `_mover_cal_thread_v2`, `_mover_cal_thread_markers`, etc.) are removed
  in PR-7, not earlier — they protect the legacy code while SMART is being
  validated.

## Clean-break design — no dependency on open issues

SMART is a **greenfield calibration path**. It does not consume the legacy
calibrator's IK, sweep math, or cancel/error plumbing. It reads from one
authoritative source — the fixture record + its profile envelope — and writes
its own samples, model, and residuals into `mover_calibrations.json` under
`method:"smart"`.

What this means concretely:

- **IK:** SMART ships its own canonical fixture-frame IK in
  `desktop/shared/coverage_math.py` from PR-2 onward. It does not import or
  call the legacy `_aimUnitVector` path. If the open IK fix #715 lands later,
  fine; if it doesn't, SMART is unaffected. SMART becomes the reference
  implementation, not a downstream consumer.
- **Tilt envelope:** SMART reads `tiltOffsetDmx16` and `tiltUp` directly from
  `_profile_lib.channel_info(profile_id)`. Those fields already exist in the
  profile JSON; they're just not honored by every legacy code path. SMART
  honors them from PR-2 onward regardless of what the legacy IK does.
- **Cancel / error parking:** SMART owns its own cancel and error-exit
  handlers. PR-4 wires `_smart_cancel(fid)` and `_smart_on_error(fid)` that
  blackout the fixture and slew it to `homePanDmx16` / `homeTiltDmx16`
  directly via the DMX engine. Whatever the legacy `_park_fixture_at_home()`
  does is irrelevant — SMART does its own parking.
- **Cancel button:** the existing `POST /api/calibration/mover/<fid>/cancel`
  route is shared, but its handler in PR-4 dispatches on
  `job["method"] == "smart"` and calls SMART's own cancel.

Sequencing inside this plan still matters (PR-2 needs PR-1's
`homeSecondary` data), but **no PR in this plan is blocked on any other
issue**. PR-1 can start today.

## PR roadmap (one line each)

1. **PR-1** — Wizard captures Home Secondary; persists to `fixtures.json`.
2. **PR-1.5** — New angular aim endpoint `POST /api/mover/<fid>/aim-angles`
   `{panDeg, tiltDeg}` — the canonical low-level move. Uses the
   Home+Secondary 2-pair affine estimate when no SMART model exists yet.
3. **PR-2** — SMART-owned IK + coverage-cone math + 3D viewport on the
   calibration card.
4. **PR-2.5** — Mover-control engine (gyro + Android) switches to
   `aim-angles`. Stores `reference_pan_deg` / `reference_tilt_deg` instead
   of DMX values. Deletes its private DMX-delta pan/tilt math.
5. **PR-3** — Working area (cone ∩ camera-visible-floor) + 16-point probe
   grid (rendered, not probed).
6. **PR-4** — Bound-and-probe loop + SMART-owned cancel/error parking.
7. **PR-5** — IK-first solver, commit format, residual gating, `confidence`
   field. SMART is now the selected mode in the pulldown.
8. **PR-6** — ArUco confirmation pass + commit gate. SMART is now
   production-ready.
9. **PR-7** — Delete legacy modes. Remove `all-auto`/`markers`/`v2`/
   `legacy` from `mover_calibrator.py` and the SPA pulldown; remove the
   pulldown entirely (calibration card just *is* SMART); remove tests for
   deleted paths; update CLAUDE.md API table; remove the
   `method:"smart"`/etc. branching from the aim path (it's all SMART
   now).

## Axis-by-axis math notes (informs solver + probe budget)

Pan and tilt do **not** behave the same way:

- **Pan depends only on bearing.** From a fixed `fixture.xy`, the map
  `world (x,y) → pan_deg` is `atan2(y - fy, x - fx)` — distance falls out
  entirely. Two targets on the same bearing share a pan, regardless of how
  far away they are or what their `z` is. So `pan_deg → panDmx16` is
  effectively pinned by Home + Secondary alone (`panDmxPerDeg` + bias) —
  more pan probes only help detect a non-vertical pan axis (a second-order
  mechanical defect).
- **Tilt depends on bearing AND distance AND height.**
  `tilt_deg = atan2(z_fixture, sqrt(x_fixture² + y_fixture²))` — both
  horizontal distance and `dz` matter. Two probes (Home + Secondary) at
  90° apart pin a coarse `tiltDmxPerDeg`; intermediate tilt probes inside
  the working area harden the affine fit and reveal any nonlinearity in the
  tilt drive.
- **Cross-coupling** (pan motion shifting tilt aim, or vice versa, from a
  non-orthogonal head) only shows up when probes vary in *both* axes
  simultaneously.

**Probe budget decision: 16 probes stays.** Rationale: not every probe will
be detected (beam-detect misses, occlusions, low-contrast regions). Sampling
16 gives margin to lose 50% and still have enough samples to fit. The
**minimum to commit a SMART calibration is 1 successful probe** (the solver
falls back to the Home + Secondary 2-pair estimate plus that single probe to
sanity-check tilt at a third angle). Anything from 1 to 16 successful
probes commits — fewer probes just means fewer LSQ rows and a less
diagnostic residual; the model itself remains identical.

Implications for downstream PRs:

- **PR-3 sampler:** still generates 16 candidate points. The 150 mm margin
  rule is unchanged. If the working area is so small only `< 16` valid
  points fit, that's already handled by `insufficient: true`.
- **PR-4 abort threshold:** the existing "abort if > 50% miss" heuristic
  becomes "abort only if **0** probes succeed" — drop the 50% gate. With 1
  probe SMART can still commit; it's only when *every* probe misses that
  the run is unrecoverable.
- **PR-5 solver:** must accept `N >= 1` successful probes (not the previous
  `N >= 4`). With `N == 1` the LSQ system is under-determined, so the
  solver falls back to the 2-pair Home+Secondary estimate and uses the
  single probe purely for residual reporting at one extra tilt angle. With
  `N >= 2` the affine fit becomes constrained; with `N >= 4` the residual
  starts being statistically meaningful.
- **Residual gating:** `MAX_RMS_MM = 100` only applies when `N >= 4`. For
  `N < 4`, commit unconditionally (no statistical basis to reject) but flag
  the calibration record with `confidence: "low"` and surface that in the
  SPA so the operator knows to re-run if anything looks off.

---

## PR-1 — Wizard: capture **Home Secondary** vector + persist

**Goal:** add a second known (vector, DMX) pair so PR-3 can solve DMX-per-degree.
No SMART mode yet, no UI changes outside the home wizard. Pure data layer + a
wizard step.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/parent_server.py` (~line 1851) | Extend `POST /api/fixtures/<fid>/home` to accept optional `secondary` block; extend `DELETE` to clear both; persist to `fixtures.json`. |
| `desktop/shared/parent_server.py` (`_park_fixture_at_home`, line 5007) | Unchanged — still parks at primary. |
| `desktop/shared/spa/js/calibration.js` (home wizard modal) | After Home capture, drive a "Secondary" step: slew tilt to mid, slew pan ±25%, prompt user for stage-frame angle, POST. |
| `desktop/shared/mover_control.py` | Helper to compute "safe pan offset 25% within clamps" and "tilt mid DMX". Pure function, no IO. |

### Persistence (extend `fixtures.json`, additive — no migration needed)

```jsonc
{
  "id": 17,
  // existing
  "homePanDmx16": 32768,
  "homeTiltDmx16": 13107,
  "homeSetAt": "2026-04-27T14:00:00Z",
  // NEW
  "homeSecondary": {
    "panDmx16": 49152,        // primary + 25% of pan range, sign chosen to stay inside clamp
    "tiltDmx16": 32768,       // mid-range or half-tick (per spec rule)
    "operatorTiltDeg": -10.0, // operator-supplied stage-frame tilt at this pose (down = negative)
    "capturedAt": "2026-04-27T14:01:30Z"
  }
}
```

`operatorTiltDeg` is stage-frame degrees from horizon (positive = up). Pan
delta is implicit: it is the DMX difference from `homePanDmx16` and the world
direction is computed by the solver in PR-3.

### API

- `POST /api/fixtures/<fid>/home` body now accepts an optional
  `secondary: {panDmx16, tiltDmx16, operatorTiltDeg}`.
- `GET /api/fixtures/<fid>/home` returns both records (or `null` for
  `secondary` when not set).
- `DELETE /api/fixtures/<fid>/home` clears both atomically.

### Wizard flow (SPA)

1. Existing Home capture (unchanged).
2. New step: server-side slew. Server computes `secondary.panDmx16` and
   `secondary.tiltDmx16` from profile envelope, writes via the live DMX engine,
   waits for settle (configurable, default 1.2s).
3. SPA shows the fixture's `tiltRange`/`tiltOffsetDmx16` block from
   `_profile_lib.channel_info()` so the operator knows what range to enter.
4. Operator enters the stage-frame tilt angle (free text, validated to
   `[-90, +90]`).
5. POST to `/api/fixtures/<fid>/home` with the `secondary` block.

### Tests

- `tests/test_parent.py` — extend the home-set test: round-trip with
  `secondary`, then DELETE clears both.
- `tests/test_mover_calibration.py` — unit-test the "25% pan offset chooses
  sign that stays inside clamp" helper.

### Merge gate

- All existing tests still pass.
- A live-test session captures Home + Secondary on fid 14 and fid 17, written
  to `fixtures.json`. Operator visually confirms the secondary pose looks
  correct.

---

## PR-1.5 — Angular aim endpoint (canonical low-level move)

**Goal:** introduce `POST /api/mover/<fid>/aim-angles` `{panDeg, tiltDeg}`
as the single canonical way to point a moving head at a fixture-frame
angle. After this PR, both the home wizard's secondary slew (PR-1, retro)
and SMART's probe loop (PR-4) and ArUco validation (PR-6) all use it.
After PR-2.5 the mover-control engine uses it too, collapsing all
pan/tilt → DMX math into one place.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/parent_server.py` | NEW route `POST /api/mover/<fid>/aim-angles` `{panDeg, tiltDeg, settleMs?}`. Resolves `(panDmx16, tiltDmx16)` via the affine model; clamps to fixture envelope; writes via the live DMX engine; optionally waits `settleMs` before returning. |
| `desktop/shared/coverage_math.py` (NEW module) | Lands the full SMART canonical IK and 2-pair affine estimate in one PR so the angular aim endpoint can resolve `(panDeg, tiltDeg) → (panDmx16, tiltDmx16)` from Home+Secondary alone, with no SMART model present. Contents: `world_to_fixture_pt(world_xyz, fixture_xyz, rotation) → (panDeg, tiltDeg)`, `fixture_aim_to_world(panDeg, tiltDeg, fixture_xyz, rotation) → (axis_unit, world_aim_xyz_at_floor)`, `solve_dmx_per_degree(home, secondary, fixture, profile) → {panDmxPerDeg, tiltDmxPerDeg, panSign, tiltSign, panBiasDmx, tiltBiasDmx}` (the 2-pair affine estimate; needs the canonical IK to compute `home_tilt_deg` from `fixture.rotation`), `angles_to_dmx(panDeg, tiltDeg, model_or_estimate, profile) → (panDmx16, tiltDmx16)`, `dmx_to_angles(panDmx16, tiltDmx16, model_or_estimate) → (panDeg, tiltDeg)`. Forward IK and inverse IK must be exact inverses by construction (same module, shared constants). |
| `desktop/shared/parent_server.py` (existing `/api/calibration/mover/<fid>/aim`) | Refactor to: do world-XYZ → fixture-frame angles via the new IK helper, then call the same `angles_to_dmx` helper. The two endpoints become two faces of one implementation. |

### Resolution priority for `model_or_estimate`

1. **SMART model** present (`mover_calibrations.json` has `model` for this
   fid) → use its four scalars.
2. Otherwise, **2-pair Home+Secondary estimate** computed on the fly from
   `fixtures.json` (the same `solve_dmx_per_degree` from PR-2). Returns
   `confidence: "estimate"` in the response.
3. Otherwise (no Home+Secondary either) → 400
   `error: "fixture_not_calibrated"`.

There is **no fall-through to legacy grid interpolation**. Grid mode is
gone after PR-7; the aim endpoint is the only consumer of the model.

### Tests

- `tests/test_parent.py` — round-trip: POST `aim-angles` with a calibrated
  fixture writes the expected DMX into the live engine. POST with only
  Home+Secondary returns success + `confidence: "estimate"`. POST with
  neither returns 400.
- `tests/test_coverage_math.py` — `angles_to_dmx` and `dmx_to_angles` are
  exact inverses on synthetic models.

### Merge gate

- All existing API tests pass (the refactored `/aim` endpoint must remain
  byte-for-byte compatible with current SPA callers).
- Live-test: aim-angles call to fid 14 with Home+Secondary set
  (calibration not yet run) slews to a sensible pose.

---

## PR-2 — Coverage cone math + 3D viewport on the calibration card

**Goal:** with Home Primary + Home Secondary in hand, render the fixture's
floor coverage as a translucent volume on a 3D viewport mounted inside the
calibration card. No probing yet.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/coverage_math.py` | Add `coverage_polygon(fixture_xyz, rotation, profile, floor_z) → [[x,y], ...]`. The canonical IK and `solve_dmx_per_degree` already exist from PR-1.5 — `coverage_polygon` ray-marches the fixture's pan/tilt envelope corners through `fixture_aim_to_world` and convex-hulls the floor footprint. No new IK conventions; reuses PR-1.5's helpers verbatim. |
| `desktop/shared/parent_server.py` | NEW route `GET /api/fixtures/<fid>/coverage` → `{cone: {apex_xyz, axis, halfAngleDeg}, floorPolygon: [[x,y],...], floorZ: float}`. Reads `fixtures.json` + `_profile_lib.channel_info(profile_id)` + floor RANSAC result. |
| `desktop/shared/spa/js/scene-3d.js` | **Preferred:** add a parallel `_mcal3d` singleton (own renderer, own scene, own canvas) that reuses the same Three.js helpers (lighting, floor grid, fixture mesh loader) as `_s3d` via extracted module-private helpers — but does **not** share `_s3d` state. Old `_s3d` and Dashboard behavior unchanged. **Alternative-A** (only if `_mcal3d` becomes a copy-paste mess): refactor `_s3d` to a `Scene3D(mountId, opts)` class — but that risks Dashboard regression and must ship Playwright coverage on Dashboard *and* SMART card before merge. |
| `desktop/shared/spa/js/calibration.js` (~line 1175) | When mode=`smart` is selected, swap the existing snapshot panel for a `<div id="mcal-3d">` and call `_mcal3d.mount('mcal-3d')`. Render the **coverage cone** as a true 3D translucent volume: apex at `fixture.xyz`, base = `coveragePoly` extruded down to `floor.z`, side faces translucent (Three.js `BufferGeometry` from apex to each polygon vertex). Floor footprint also drawn as a slightly brighter polygon overlay so the operator can see the floor intersection clearly. |
| `desktop/shared/spa/js/calibration.js` mode `<select>` | Add `<option value="smart">SMART — automatic, camera+floor aware (default)</option>` as the first option. **Do not yet** make it the default at the line — flag-gated until PR-5 is shipped. |

### Coverage math (in `coverage_math.py`)

`solve_dmx_per_degree(home, secondary, profile)`:

```
delta_pan_dmx  = secondary.panDmx16  - home.panDmx16
delta_tilt_dmx = secondary.tiltDmx16 - home.tiltDmx16

# pan delta in degrees comes from the wizard's known pan offset
# (25% of profile.panRange, with sign chosen at PR-1):
delta_pan_deg  = sign * 0.25 * profile.panRange
# tilt delta: operatorTiltDeg - (tilt at home, computed from rotation IK)
delta_tilt_deg = secondary.operatorTiltDeg - home_tilt_deg

panDmxPerDeg  = delta_pan_dmx  / delta_pan_deg
tiltDmxPerDeg = delta_tilt_dmx / delta_tilt_deg
```

`coverage_polygon(...)`: ray-march the corners of the fixture's pan/tilt
envelope (clamped by `tiltOffsetDmx16` + `tiltUp` read from
`_profile_lib.channel_info(profile_id)`) onto the floor plane
(`z = floor.z`); take the convex hull. Return as polygon in stage XY.

**Note on linearity:** the `solve_dmx_per_degree` numbers above describe the
mapping from fixture-frame `(pan_deg, tilt_deg)` to DMX, which is affine in
both axes. The mapping from world `(x,y,z)` to fixture-frame angles is
**only linear for pan if you parameterize on bearing** — pan depends on
`atan2(y - fy, x - fx)`, which loses distance entirely (so two world points
on the same bearing share a pan, regardless of their `z`). Tilt is
genuinely nonlinear in world coordinates because it depends on bearing
distance *and* `dz`. PR-5's solver always converts samples through the IK
first and then fits affine — see PR-5 for details.

### Tests

- `tests/test_coverage_math.py` — extend with `coverage_polygon` cases on
  synthetic fixtures (downward-mounted, sideways, asymmetric tilt range).
  IK + `solve_dmx_per_degree` tests already exist from PR-1.5.
- `tests/test_parent.py` — `GET /api/fixtures/<fid>/coverage` returns a sane
  polygon for the basement fid 14 fixture.
- `tests/test_unified_3d.py` (Playwright) — opening the calibration card with
  a fid that has Home+Secondary shows the 3D viewport and a non-empty floor
  overlay.

### Merge gate

- Coverage polygon visually matches the operator's expectation on fid 14 and
  fid 17 (live-test session).
- Old Dashboard 3D viewport unchanged — refactor is non-breaking.

---

## PR-2.5 — Mover-control engine adopts angular aim

**Goal:** delete the third pan/tilt math implementation. The mover-control
engine (gyro + Android remote) currently holds `reference_pan_dmx` /
`reference_tilt_dmx` from calibrate-start and computes its own DMX deltas
from device orientation. Replace that with `reference_pan_deg` /
`reference_tilt_deg` and route every move through `aim-angles`.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/mover_control.py` | `MoverControlEngine.calibrate_start()` captures `reference_pan_deg` / `reference_tilt_deg` by inverting the SMART model (or 2-pair estimate) on the current DMX values via `coverage_math.dmx_to_angles`. `orient()` computes target angles from device delta and POSTs to `/api/mover/<fid>/aim-angles` instead of writing DMX directly. Delete the private DMX-delta math entirely. |
| `desktop/shared/parent_server.py` (mover-control HTTP handlers) | No shape change; still `POST /api/mover-control/orient` `{panDelta, tiltDelta}` from devices. The handler now forwards to `aim-angles`. |

### Behavior change worth flagging

A fixture with no Home+Secondary set at all (brand new install, never went
through the wizard) will lose remote-control until Home is captured.
Previously a fixture could be remote-controlled from cold start with no
setup beyond placing it in the layout, because the engine worked in pure
DMX-delta space. With unification, remote-control needs *some* angle→DMX
model — even just the 2-pair estimate. This is the deliberate cost of
collapsing the three implementations into one.

The home-position wizard becomes a hard prerequisite for **any** form of
mover control (calibration, remote, automated), not just for SMART
calibration.

### Tests

- `tests/test_parent.py` — mover-control orient round-trip routes through
  `aim-angles`. Cold-start fixture (no Home) returns 400
  `error: "fixture_not_calibrated"` from orient.
- `tests/test_mover_control.py` (or equivalent) — calibrate-start captures
  reference angles, orient deltas resolve to expected angle targets.

### Merge gate

- Live-test: gyro device controls fid 14 through the unified path; pose
  matches what the prior DMX-delta path produced (within mechanical
  repeatability).
- Code search confirms zero remaining `_dmx16` arithmetic inside
  `mover_control.py` outside of the inverse-model helper.

---

## PR-3 — Working area + 16-point probe grid (rendered, not probed)

**Goal:** intersect coverage polygon with camera-visible floor, generate the
16-point probe grid, render points on the calibration 3D viewport. Still no
beam-detect.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/surface_analyzer.py` | Promote the in-flight helper `camera_floor_polygon()` into a stable export. Add `union_camera_floor_polygons(camera_polys) → polygon`. |
| `desktop/shared/coverage_math.py` | Add `working_area(coverage_poly, camera_visible_poly, margin_mm=150) → polygon` (Sutherland–Hodgman clip + inward buffer). Add `sample_grid(working_poly, n=16, min_edge_margin_mm=150) → [[x,y], ...]`. |
| `desktop/shared/parent_server.py` | NEW route `GET /api/calibration/mover/<fid>/smart/preview` → `{coveragePoly, cameraVisiblePoly, workingPoly, probePoints, abortReason?}`. `abortReason` is set when working area is empty or smaller than `MIN_WORKING_AREA_MM2 = 500_000` (50 cm²). |
| `desktop/shared/spa/js/calibration.js` | When SMART is selected, fetch `/smart/preview` and render `workingPoly` as a brighter overlay and `probePoints` as small spheres on the 3D viewport. Disable "Calibrate" button when `abortReason` is present, with the reason shown inline. |

### Sampling rule

`sample_grid` uses Lloyd's relaxation seeded with a 4×4 grid clipped by
`workingPoly`. Reject any candidate within `min_edge_margin_mm` of the
polygon boundary. The 16-point target is intentional: SMART expects to
**lose probes** (beam-detect misses, occlusions, low-contrast spots), and
the spec target is for the run to remain useful even when only a fraction
of probes succeed. The minimum to commit a calibration is **1** successful
probe (see PR-5). Bias point placement to spread *tilt* angles across the
working area — the tilt axis is where the extra samples earn their keep
(pan is essentially solved by Home + Secondary alone). If fewer than 16
valid points exist, return what we have
plus an `insufficient: true` flag (UI shows a warning but still allows SMART
to proceed with fewer probes — solver handles >=8 points).

### Tests

- `tests/test_coverage_math.py` — `working_area` clip and `sample_grid` margin
  enforcement (synthetic polygons).
- `tests/test_parent.py` — `/smart/preview` returns `abortReason: "no_overlap"`
  when fixture cone misses every camera; returns 16 points for the basement
  test layout.

### Merge gate

- Live-test: SMART preview on fid 14 and fid 17 shows a working area and 16
  points that look correct against the basement floor.

---

## PR-4 — Bound-and-probe loop

**Goal:** SMART can now actually run. For each probe point, predict
`(panDmx16, tiltDmx16)`, slew, beam-detect, record `(predicted_xyz,
measured_xyz, dmx_pair)`. No solver fit yet — just collect samples.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/mover_calibrator.py` | NEW method `_smart_probe_run(fid, probe_points, panDmxPerDeg, tiltDmxPerDeg, panSign, tiltSign)`. Per point: clamp to bounded envelope, slew, settle, call existing `_beam_detect()` + `_depth_at_pixel()`, append to `samples`. |
| `desktop/shared/parent_server.py` (~line 7646) | Add `mode == "smart"` branch in the start handler. Computes envelope (PR-2 helpers) + working area (PR-3 helpers) + invokes `_smart_probe_run`. |
| `desktop/shared/parent_server.py` (`_mover_cal_jobs[...]["status"]`) | Add status strings: `smart_preview`, `smart_probing`, `smart_solving`, `smart_validating`. |

### Failure handling

- Skip-and-continue per point on beam-detect miss; record `{found: false}`.
- Abort the run **only when zero probes succeed** — losing probes is
  expected. With 1+ successful probes, hand off to PR-5's solver and let it
  decide whether the fit is good enough to commit. On the zero-probe case,
  status → `error`, then call SMART's own `_smart_on_error(fid)` which
  writes blackout DMX and slews the fixture to its `homePanDmx16` /
  `homeTiltDmx16` directly (does **not** call legacy
  `_park_fixture_at_home()`).
- Cancel button: `POST /api/calibration/mover/<fid>/cancel` is shared with
  legacy modes, but PR-4 adds a `job["method"] == "smart"` branch that calls
  `_smart_cancel(fid)` instead of the legacy `_mcal.request_cancel()`.
  `_smart_cancel` blackouts + parks at home, exactly the same way as
  `_smart_on_error`. SMART checks its own cancel flag between probe points.
- After SMART abort (zero-probe case), the fixture is left uncalibrated
  (or with its prior SMART calibration intact) and the operator sees a
  card with: the failure reason, any partial samples that *were* detected,
  and a single "Re-run SMART" button. There is **no fall-back to another
  mode** — SMART is the only mode. The operator's recourse is to fix the
  underlying problem (camera occluded, fixture aim wrong, working area
  empty) and re-run.

### Tests

- `tests/test_mover_calibration.py` — mock the camera HTTP layer; assert
  predicted DMX is inside the clamp window for every point. Cover the
  zero-probe abort path explicitly (all 16 mocked to `found: false` →
  status `error`, fixture parks at home). Cover the partial-probe path
  (e.g. 3 of 16 succeed → run completes, hands off to solver).

### Merge gate

- Live-test: SMART runs end-to-end on fid 14, produces a `samples` array
  with at least 1 detected point. (Probe success rate is informational, not
  a gate — the gate is "the run terminated cleanly and parked at home if
  it failed".)

---

## PR-5 — Solver: IK-first fit → corrected aim model + commit format

**Goal:** with Home + Secondary + 1..16 successful probes (so 3..18 pairs),
fit a corrected pan/tilt → DMX model and commit it as the SMART
calibration. Make SMART the default in the SPA.

### Axis behavior — pan and tilt are not symmetric

Pan and tilt fit fundamentally differently and the solver respects that:

- **Pan** depends only on bearing: `pan_deg = atan2(y - fy, x - fx)`.
  Distance and `z` fall out entirely. Two probes that share a bearing share
  a pan angle, so the *effective* pan information from 16 probes might come
  from only 4–5 distinct bearings. Crucially, **Home + Secondary alone are
  sufficient to pin `panDmxPerDeg` + `panBiasDmx`** — extra pan probes only
  catch second-order mechanical defects (non-vertical pan axis, gear
  backlash) and improve residual diagnostics.
- **Tilt** depends on bearing distance and `dz`:
  `tilt_deg = atan2(z_fixture, sqrt(x_fixture² + y_fixture²))`. Genuinely
  nonlinear in world coordinates. Home + Secondary pin two coarse tilt
  angles 90° apart; intermediate tilt probes harden the affine fit.
- **Cross-coupling** (non-orthogonal head, gimbal-lock-ish effects) only
  shows up when probes vary in *both* axes — another reason to keep the
  16-point grid spread across the working area rather than collapsing to
  fewer points.

### Solver geometry — important

The mapping from **world XYZ** to **(pan_deg, tilt_deg)** is nonlinear in
world coordinates for both axes (pan is `atan2`, tilt is `atan2` of a
square root). Linear-fitting `(x,y,z) → (panDmx16, tiltDmx16)` directly
would bake the IK error into the model and break worse near the fixture's
vertical (where `atan2` swings hardest).

The fit must be IK-first:

1. For each sample `{x, y, z, panDmx16, tiltDmx16, found:true}`:
   - Use SMART's own canonical IK (PR-2's `coverage_math.world_to_fixture_pt`,
     the same one that built the coverage cone) to convert `(x,y,z)` →
     fixture-frame `(pan_deg, tilt_deg)`. The forward IK used to *render* the
     cone and the inverse IK used to *fit* the model must be exact inverses
     of each other — write them in the same module so they share constants.
2. Always include Home + Secondary as the first two pairs of the LSQ system
   (they're noise-free reference pairs from the wizard, not probe-detected).
   Then append the `N` successful probe pairs. Total rows in the system =
   `2 + N` (range: 3 to 18).
3. Linear-least-squares fit the affine model:
   ```
   panDmx16  = panSign  * panDmxPerDeg  * pan_deg  + panBiasDmx
   tiltDmx16 = tiltSign * tiltDmxPerDeg * tilt_deg + tiltBiasDmx
   ```
   `panSign`/`tiltSign` are seeded from PR-2's two-point estimate (so we
   don't accidentally flip 180°), then held fixed; the four scalars
   (`panDmxPerDeg`, `tiltDmxPerDeg`, `panBiasDmx`, `tiltBiasDmx`) are the
   regression unknowns.
4. Compute residuals by re-projecting each `(panDmx16, tiltDmx16)` back
   through the model + IK to a predicted XYZ and measuring distance to the
   measured XYZ. Residuals are reported per-axis and overall RMS.

### Minimum probes & residual gating

| Successful probes (N) | Behavior |
|----------------------|----------|
| 0  | PR-4 already aborted; solver never runs. |
| 1  | LSQ is under-determined for tilt with only 3 rows. Solver falls back to the 2-pair Home+Secondary estimate (`model.panDmxPerDeg` / `tiltDmxPerDeg` from PR-2's `solve_dmx_per_degree`); the single probe is recorded for residual reporting only. Commit with `confidence: "low"`. |
| 2–3 | Affine fit becomes constrained on tilt. Commit with `confidence: "medium"`. No RMS gate. |
| 4–18 | Standard LSQ fit. Commit with `confidence: "high"`. **RMS gate active**: if `rmsMm > MAX_RMS_MM` (100 mm), status → `error_high_residual`, the calibration is *not* committed, the prior calibration record is left untouched, and the SPA shows the residual table and offers to re-run the wizard's Secondary capture (most likely cause: `operatorTiltDeg` was wrong). |

The RMS gate is only meaningful with enough probes to over-determine the
fit — applying it to `N < 4` would reject calibrations that the math has
no statistical basis to reject. With `N < 4` we commit unconditionally and
flag confidence so the operator knows to verify before relying on it.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/mover_calibrator.py` | NEW `_smart_solve(samples, home, secondary, fixture, profile) → {model, residuals}`. Per-sample IK conversion uses the existing canonical aim helper (do not duplicate). Linear LSQ via numpy `lstsq`. |
| `desktop/shared/parent_server.py` | After `_smart_probe_run` returns, call `_smart_solve`. On success, write to `_mover_cal[str(fid)]` with `method: "smart"`, `samples`, `model`, `residuals`. On `error_high_residual`, leave the prior calibration record untouched. |
| `desktop/shared/spa/js/calibration.js` | Make `smart` the **selected** option in the mode pulldown; keep others in the list. Show residuals table after solve (reuse `s3dShowResidualsForFixture`). |

### Calibration record (extend `mover_calibrations.json`)

The `model` field is the only consumer of the calibration record going
forward. There is **no grid-interpolation fallback** — if `model` is
missing the aim endpoint returns `error: "fixture_not_calibrated"` and
the SPA prompts the operator to run SMART. The legacy `grid` field on
existing records is read-only / ignored after PR-7.

```jsonc
{
  // existing
  "method": "smart",
  "samples": [{"x": 1200, "y": 800, "z": 0,
                "panDmx16": 32100, "tiltDmx16": 14250,
                "found": true}, ...],
  "model": {
    "panDmxPerDeg":  220.5,
    "tiltDmxPerDeg": 305.1,
    "panSign":  +1,
    "tiltSign": -1,
    "panBiasDmx":  -12,
    "tiltBiasDmx": +37
  },
  "residuals": {
    "rmsMm": 35.2,
    "maxMm": 78.0,
    "perPoint": [...]
  },
  "confidence": "high"  // "low" (N=1) | "medium" (N=2..3) | "high" (N>=4)
}
```

### Tests

- `tests/test_mover_calibration.py` — fit synthetic samples drawn from a
  known DMX-per-degree, assert recovered values within 1%.
- `tests/test_parent.py` — POST `/api/calibration/mover/<fid>/aim` with a
  SMART-method calibration uses `model` first, falls back to `grid` only when
  `model` is absent.

### Merge gate

- Live-test on fid 14 and fid 17 with all 16 probes succeeding — RMS
  residual under 60 mm, max under 120 mm. (Numbers calibrated against #682
  / #698 / #710 baselines; revisit if unrealistic.)
- Live-test with intentionally-occluded probes (cover camera view of half
  the working area) — solver still commits, `confidence` field correctly
  reports `medium` or `low`, and the SPA surfaces it.

---

## PR-6 — ArUco confirmation pass

**Goal:** after solve, slew to each surveyed marker inside the working area,
ask the operator to confirm. On all-yes, commit. On any no, status → `error`,
calibration stays uncommitted, operator can re-run.

### Touched files

| File | Change |
|------|--------|
| `desktop/shared/parent_server.py` | NEW routes `POST /api/calibration/mover/<fid>/smart/validate/start` and `POST .../validate/confirm` (operator yes/no per marker). Status transitions in `_mover_cal_jobs`. |
| `desktop/shared/spa/js/calibration.js` | After solve, present each marker as a card on the SMART panel: "Slew to marker N → confirm hit". |

### Tests

- `tests/test_parent.py` — endpoint round-trip; abort path leaves calibration
  uncommitted.

### Merge gate

- Live-test: at least 3 markers in working area, all pass, SMART calibration
  committed and visible in `mover_calibrations.json`.

---

## PR-7 — Delete legacy modes

**Goal:** SMART has now been validated end-to-end on real fixtures (PR-6
merge gate). Delete the four obsolete modes and their supporting code.
This is a deletion-only PR — no new behavior, no regressions.

### Deletions

| File | What goes |
|------|-----------|
| `desktop/shared/mover_calibrator.py` | `_mover_cal_thread` (legacy BFS), `_mover_cal_thread_markers`, `_mover_cal_thread_v2`, `pick_calibration_targets`, `converge_on_stage_target`, `warmup_sweep`, the battleship/BFS grid logic, the v2 target-driven solver. Keep only `_smart_probe_run`, `_smart_solve`, `_smart_cancel`, `_smart_on_error`, `_dark_reference`, `_beam_detect`, `_depth_at_pixel`. |
| `desktop/shared/parent_server.py` (start handler ~line 7646) | Mode dispatch collapses to a single `_smart_probe_run` call. Remove the `mode` field from the request body — there's only one mode. |
| `desktop/shared/parent_server.py` (`/api/calibration/mover/<fid>/aim`) | Remove the `grid` interpolation branch entirely. The endpoint becomes: world-XYZ → fixture-frame angles → `angles_to_dmx` (PR-1.5). Returns `error: "fixture_not_calibrated"` when no `model` exists. |
| `desktop/shared/spa/js/calibration.js` (~line 1175) | Remove the `<select id="mcal-mode">` element entirely. The calibration card just *is* SMART. Update `_moverCalGo()` to drop the mode parameter. |
| `tests/test_mover_calibration.py` | Delete tests covering the removed paths. Keep only SMART solver tests, IK round-trip tests, and probe-loop tests. |
| `mover_calibrations.json` migration | One-shot on startup: any record without a `model` field is renamed to `legacy_method` (informational), and the SPA flags those fixtures as "needs SMART recalibration" in the calibration card. |
| `CLAUDE.md` | Remove the calibration-mode discussion from the Unified mover control section. Update the `/api/calibration/mover/<fid>/start` row in the API table to drop the mode parameter. |

### Retained

- `_dark_reference()`, `_beam_detect()`, `_depth_at_pixel()` — SMART uses
  all three.
- The `/api/calibration/mover/<fid>/{start,status,cancel,aim,delete}`
  routes — same names, simplified bodies.
- ArUco marker registry (used by SMART validation pass).

### Tests

- Run the full test suite — nothing should reference deleted symbols.
- Live-test: full stage of fixtures recalibrated under SMART, all
  fixtures aim within tolerance.

### Merge gate

- All tests pass. CI green.
- `grep -r "all-auto\|markers-only\|_mover_cal_thread_v2\|battleship" desktop/ spa/`
  returns nothing.
- All fixtures in the basement test stage produce a `method:"smart"`
  record with `confidence: "high"`.

---

## Cross-cutting checks (every PR)

- Stage-coordinate invariant (issue spec §1): every new persisted field uses
  mm + degrees, stage frame, no nested coordinate systems. CR rule: any new
  vector saved without unit/frame in the field name (or in a comment in
  `parent_server.py`) blocks the PR.
- New API routes added to the **CLAUDE.md** API table in the same PR.
- New tests added to the relevant suite invoked from `tools/devgui` so the
  weekly regression catches them.

## Out of scope reminders

- Straight-up / straight-down Home — explicit error from the SMART preview
  endpoint, deferred to v2. **Detection rule:** compute the home-aim unit
  vector via SMART's own IK; if `abs(home_aim.z) / norm(home_aim) > 0.95`
  (i.e., aim is within ~18° of vertical), `/smart/preview` returns
  `abortReason: "home_near_vertical_v2_deferred"` and the SPA tells the
  operator to re-run Home pointing closer to horizontal or use a different
  mode.
- Multi-fixture parallel calibration — single fixture at a time.
- Closed-loop tracking during a show — separate issue.
- Pi CSI cameras — already excluded system-wide in v1.

## Risk register

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `Scene3D` refactor breaks Dashboard viewport | Medium | PR-2 ships with `tests/test_unified_3d.py` Playwright run before merge. |
| Coverage polygon over-reports because tilt envelope isn't honored | Medium | PR-2 reads `tiltOffsetDmx16` + `tiltUp` directly from `channel_info()` — these fields exist in the profile JSON today, regardless of whether legacy IK consumes them. SMART honors them from day one. |
| 16 points are too few for a usable solve in large stages | Low | PR-3 returns `insufficient: true` when fewer than 16 valid; PR-5 solver accepts ≥1 with confidence ladder. |
| Mover-control regression after PR-2.5 unification | Medium | PR-2.5 ships side-by-side validation: gyro pose under unified path measured against pose from prior DMX-delta path on the same fixture. Don't merge until they match within mechanical repeatability. |
| Fixtures with stale `method:"legacy"` records silently break after PR-7 | High | PR-7 migration flags those fixtures in the SPA with a "needs SMART recalibration" badge. The `/aim` endpoint returns `fixture_not_calibrated` (loud failure), not silent garbage. Operator is forced to re-run SMART before the fixture works again. |
| Working area clip math has a degenerate case (concave polygon) | Medium | Convex hull of camera-visible polygon before clip, document in `coverage_math.py`. |
| Operator-supplied `operatorTiltDeg` in PR-1 is wrong → solver biased | High | PR-5 residuals report flags this — RMS > 100 mm prompts re-running the wizard step. |
