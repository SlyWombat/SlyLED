# Code review: commit 82320c8 — `fix(#701, #702, #703)`

Reviewed against the basement-rig live-test session (fixture #17, mountedInverted=true) and the operator-pinned IK conventions in `~/.claude/projects/-home-sly-slyled2/memory/project_mover_cal_livetest_2026_04_26.md`.

Empirical reproductions saved at `/tmp/ik_compare.py` and `/tmp/ik_band_test.py` — both run against the live `mover_calibrator` module + `tools/probe_coverage_3d.py` reference IK.

---

## P1 — Bug B fix is geometrically wrong; the new code REGRESSES the basement rig

`desktop/shared/mover_calibrator.py:815-829`. `_effective_mount_rotation` returns `[180.0, 0.0, 0.0]` for a `mountedInverted=True` fixture with zero rotation. Downstream this becomes an `Rx(180°)` rotation in `pan_tilt_to_ray` (`euler_xyz_deg_to_matrix` in `desktop/shared/remote_math.py:180`), which **flips both `dy` and `dz`** in mount-local space. The operator-pinned convention is explicit:

> Inverted-mount flips dz only, NOT dy. A 180° rotation about X (which flips both) is wrong for ceiling-hung fixtures aimed forward; only the tilt sense reverses.
> — `project_mover_cal_livetest_2026_04_26.md`, line 31

Empirical comparison (fixture #17: pos=(600, 0, 1760), home_pan=0.6770, panRange=540, tiltRange=180, mountedInverted=True; `/tmp/ik_compare.py`):

| tilt_norm | new fix `_ray_floor_hit` (x, y) | reference `tools/probe_coverage_3d.py:floor_hit` |
|-----------|----------------------------------|---------------------------------------------------|
| 0.05      | (877, 27)                        | (600, 11112) |
| 0.20      | **(1873, 124)**                  | **(600, 2422)** |
| 0.50      | None (ray horizontal)            | (600, 0) (straight down)|
| 0.70      | None (ray upward)                | (600, -1279) |

Operator confirmed in yesterday's session that beam @ home_pan, tilt-down lands at Y > 0 (toward the audience). New IK puts the beam at **Y < 600** for every downward tilt — i.e. behind the fixture's mount point, into the back wall — with x trending toward stage-left as tilt increases. Reference IK puts the beam where the operator saw it.

Repro for `_camera_visible_tilt_band` with realistic basement-rig camera polygons (cameras at front, FOVs cover Y ∈ [2000, 3700] mm; `/tmp/ik_band_test.py`):

```
Test 1: tilt_range=180 with inverted=True
  RAISED: _camera_visible_tilt_band: at home pan=0.677, no tilt in
          [0.05, 0.95] projects the beam onto any camera FOV polygon...
Test 2: tilt_range=270 (legacy default) with inverted=True
  RAISED: <same message>
```

The IK error means even with the legacy 540/270 defaults preserved, the band still can't be computed. **Cal cannot start at all on the basement rig** with this branch. The combination of (1) Bug B's wrong-direction IK and (2) #703's new fail-loud raise turns a previously-silent miscalibration into a hard cal-start failure.

Fix sketch: mirror `tools/probe_coverage_3d.py:floor_hit` — flip `dz` only when inverted (in mount-local space, before any pan rotation) instead of injecting `rx=180`. The reference implementation also uses pan-relative-to-home (`delta_pan_deg = (pan_norm - home_pan_norm) * pan_range`), which is why home_pan=0.6770 doesn't land 95° off-axis in that path.

Severity: P1, session-killing. This is the same class of regression the `(0.0, 0.5)` legacy fallback was masking.

---

## P1 — `tests/test_battleship_camera_visible.py` is broken by the fail-loud change

`tests/test_battleship_camera_visible.py:126-142`. Three assertions still expect the legacy half-band fallback returns:

```python
ok(fallback_band == (0.5, 1.0), ...)   # line 131 — now raises
ok(no_poly == (0.5, 1.0), ...)         # line 137 — now raises
ok(inv == (0.0, 0.5), ...)             # line 142 — now raises
```

Running the file under linux python3:

```
── _camera_visible_tilt_band — single-camera rig ──
Traceback ... mover_calibrator.CalibrationError: _camera_visible_tilt_band: at home pan=0.000, no tilt in [0.05, 0.95]...
```

The commit's "100/100 mover cal" claim refers to `tests/test_mover_calibration.py`, which doesn't touch `_camera_visible_tilt_band` or `_ray_floor_hit`. The file that DOES test these functions (`test_battleship_camera_visible.py`) is now uncrashable as written, AND zero new test cases were added to assert the fail-loud behaviour or the inverted-mount IK direction.

Severity: P1. Had a test asserted "inverted-mount, home_pan=0.5, beam lands +Y" the geometric inversion in P1 #1 above would have been caught.

---

## P2 — No tests added for any of Bug B / C / D / E or `CalibrationError`

The commit message says "Tests: 100/100 mover cal, 52/52 spatial math, 597/600 parent_server". That's the pre-existing baseline; no new test cases were added. Specifically missing:

- `_effective_mount_rotation` direction test (would have caught the rx=180 vs dz-only issue)
- `_camera_visible_tilt_band` raises on each of the four precondition failures
- `_camera_visible_tilt_band` band correctness for inverted-mount + camera-front-of-fixture geometry
- `api_fixture_dmx_test` 16-bit precision preserved across pan-fine clobber path
- `api_fixture_dmx_test` color-wheel resolution
- `_probe` blackout-then-aim ordering (fixture state machine assertion)

Severity: P2. This is the same anti-pattern the operator pinned: "back up code-reading diagnoses with empirical API-driven repro" — but for this fix the only repro lives in the rig, not in tests.

---

## P2 — `_probe_cell` fallback blackout-then-aim is redundant, not wrong

`desktop/shared/mover_calibrator.py:1372-1378`. The fallback runs when `_beam_detect_flash` returns None. But `_beam_detect_flash` already does its OWN blackout-then-aim (lines 2027-2031) and ends with `dim=255` at the SAME (pan, tilt). The new fallback writes `dim=0` at the **same** (pan, tilt) — there's no head slew between them. The 300 ms hold is wasted; `_beam_detect_verified` would work without it.

Not wrong, but wastes ~300 ms per missed-cell probe (24+ probes/run × 300 ms = ~7 s slower per cal). Severity: P2.

---

## P3 — Bug D: 16-bit precision still gets clobbered when only `dimmer` is sent

`desktop/shared/parent_server.py:3886-3887`. The `pan_tilt_written` guard only protects pan-fine/tilt-fine **on the same call** that writes them. If the SPA later POSTs `/api/fixture/dmx-test` with `{dimmer: 0.5}` only (e.g., to flash the beam without re-aiming), `pan_tilt_written=False` and the defaults loop will set pan-fine/tilt-fine back to 128, destroying the previously-aimed precision.

Not session-critical (cal always sends pan+tilt+dim together) but semantically wrong: the buffer is supposed to retain state across calls. Severity: P3.

---

## P3 — `errorType="calibration-error"` is set but the SPA doesn't branch on it

`desktop/shared/spa/js/calibration.js:1842-1844` shows `r.error` is rendered verbatim. The new `errorType` field is wired into the job dict (legacy / markers / v2 catches) but no SPA code reads it. Operator still sees the (clear, actionable) error message via `r.error`, so this isn't broken — just an unused affordance. Severity: P3, cosmetic.

---

## Bug C verified correct

`desktop/shared/mover_calibrator.py:1189-1218`. The `_probe` function:

1. Writes `(pan, tilt, RGB=0,0,0, dim=0)` and holds 300 ms — head slews dark.
2. Writes `(pan, tilt, RGB=color, dim=255)` and holds 200 ms — light comes on at destination.
3. Returns `_beam_detect`.

The candidate re-settle (lines 1215-1218) does the same dance returning to (pan0, tilt0). All atomic. No `_set_mover_dmx(... dimmer=255)` followed by a new pan/tilt without an interleaved dim=0 write. Matches the `_beam_detect_flash` pattern from #695.

`_set_mover_dmx` writes pan/tilt/RGB/dim in one buffer mutation, and `_hold_dmx` sends the frame; so the new pan and dim=0 land in the same DMX universe frame. Wire-level: head receives "go to (new pan, new tilt) and turn off" simultaneously, then 300 ms later a "turn on at (new pan, new tilt)" frame.

---

## #703 fail-loud verified, with one caveat

The four `CalibrationError` raises in `_camera_visible_tilt_band` (lines 882-921) each have a specific operator-actionable message. The cal-thread wrappers (legacy: `parent_server.py:6036-6045`; markers: 5090-5097; v2: 5723-5731) catch and surface via `job.error` + `errorType="calibration-error"`, blackout the fixture, park at home, release lock. Cleanup is correct.

`/api/calibration/mover/<fid>/start` precondition checks (`parent_server.py:7318-7352`) return HTTP 400 with `errType="profile-incomplete"` or `errType="fixture-not-placed"` — matches issue acceptance.

**Caveat**: combined with the P1 IK bug, the fail-loud message that fires for fixture #17 is misleading. The error says "Set Home anchor is wrong, or the fixture's mountedInverted / rotation does not match its physical mount." — but Set Home is correct and the operator HAS the rotation right; it's the IK that's broken. An operator following the suggested remediation (re-run Set Home) will rotate through every value without finding a working configuration.

---

## Other observations

- `docs/live-test-sessions/2026-04-26/dmx-trace-153630.log` was not in the repo, so I couldn't cross-check the exact timing of the original Bug C symptom against the new ordering. Bug C is verified by code inspection only.
- The `_ray_floor_hit` test at `tests/test_battleship_camera_visible.py:74` asserts that `tilt_norm=0.5` returns None (horizontal beam). This is consistent with `pan_tilt_to_ray`'s mid-range = horizontal convention, but **opposite** the operator-pinned rig convention where `tilt_norm=0` is horizontal. Two contradicting conventions live in this codebase; the new fix didn't reconcile them. (Not strictly a fix-introduced issue — pre-existing — but the IK convention split is the root of why Bug B was even possible.)
- No commented-out code, no TODO markers, no obvious half-finished refactors in the diff. The deferred legacy deletion is appropriately deferred.

---

## Recommended action

Roll back the `rx=180` substitution in `_effective_mount_rotation` and replace with a dz-only flip in mount-local space (mirror `tools/probe_coverage_3d.py:floor_hit`). Add a regression test that asserts `_ray_floor_hit(fx_pos=(600,0,1760), fx_rot=[0,0,0], home_pan=0.5, tilt=0.7, pan_range=540, tilt_range=180, mounted_inverted=True)` lands **+Y** (forward of the fixture), not -Y. Update `tests/test_battleship_camera_visible.py:126-142` to assert the new raise behaviour. Re-test against the basement rig before declaring #702 closed.
