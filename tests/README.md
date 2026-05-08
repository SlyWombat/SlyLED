# SlyLED tests

## Simulator-coverage policy (#852)

**Every gyro / claim / mover-control bug filed from now on MUST include a
simulator regression test as part of its fix PR.** The test must, when
run against the pre-fix code, fail with an assertion specific to the
bug. Reviewers reject PRs without that test.

Why: bugs like #847 (calibrated-gate dropping writes), #848 invariant 1
(lamp-on default colour), and #851 (orient → panNorm refresh) each cost
a live-rig session to discover. The fixes are simple once located but
the *cycles* — press-Start, observe nothing, audit log, sample wire,
re-test, file issue, fix, re-flash, re-test — are expensive. A
simulator-driven test catches them at PR time before they reach the
operator.

Exemption: if the bug genuinely requires physical-rig behaviour to
surface (mechanical mover lag, wire-level Art-Net specifics), the test
plan must call that out explicitly with a "physical-only — out of
simulator scope" note. **#847, #848, #851 do NOT qualify** — all three
are observable purely from `mover-control/status` and the universe
buffer, both of which the simulator already mocks.

## What lives where

- `test_parent.py` — flask test-client suite (~750+ assertions). Most
  bugs in the orchestrator are reproducible from here. Includes the
  end-to-end orient → panNorm contract (#851), claim arbiter mute on
  stop (#848), aim-axis wizard math (#826), show-import merge (#838).
- `test_825_gyro_handshake.py` — press-Start handshake (CLAIM_ACK +
  initial HB + uiState reconciliation).
- `test_orient_contract.py` — 73-cell axis-convention contract matrix.
  Static convention tests; not the in-claim aim path.
- `test_show_generator.py` — preset themes + bake structure.
- `test_mover_calibration.py`, `test_beam_detector.py`,
  `test_surface_analyzer.py`, etc. — domain-specific suites.
- `regression/` — split end-to-end flow (stage setup → layout → bake →
  3D runtime).
- `diag_orient_transitions.py`, `diag_orient_transitions.py` —
  preserved diagnostic harnesses for axis-convention debugging.
  Manual / interactive, not asserted.

## Adding a gyro/claim test

The pattern lands in `test_parent.py` under the existing `with
app.test_client() as c:` block. For a bug touching orient → claim
state:

1. Create the mover fixture with Home + Secondary (so AimSphere
   resolves).
2. `_ps._remotes.add(...)` a puck Remote with `R_world_to_stage`,
   `calibrated`, `calibrated_against` set against the mover (#847's
   trust-cross-session-cal path).
3. `_ps._mover_engine.claim(...)` + `start_stream(...)`.
4. `remote.update_from_euler_deg(roll, pitch, yaw)` to inject orient.
5. `_ps._mover_engine._tick()` to advance the claim writer.
6. Assert against `_ps._mover_engine._claims[fid].pan_smooth` (or the
   universe buffer via `/api/dmx/monitor/<uni>`).

#851's contract block is the canonical template — clone its setup +
swap the assertions.
