# SlyLED tests

## SLYLED_DATA isolation (#907)

`parent_server.py` resolves its persistence directory at **import
time**: `SLYLED_DATA` env var if set, else `%APPDATA%\SlyLED\data` on
Windows, else repo-local `desktop/shared/data`. Importing the module
(or exercising `app.test_client()`) reads and writes that directory —
so a test run without `SLYLED_DATA` set operates on the live operator
project on Windows.

Every orchestrated entry point isolates automatically (a fresh temp
dir, unless you deliberately preset `SLYLED_DATA` yourself):

- **pytest** — `tests/conftest.py` sets it before any test module is
  imported.
- **`tests/regression/run_all.py`** — exports one fresh temp dir into
  every suite subprocess (shared across the run, so the stage-setup →
  layout → bake → runtime flow still carries state).
- **`tests/docker/run_tests.sh` / `run_dmx_tests.sh`** — set it inside
  the containers.
- **devgui** (`tools/devgui/server.py`) — injects it into every test
  subprocess it spawns.
- **CI** (`.github/workflows/python-tests.yml`) — sets it at the job
  level.

**Residual risk — direct script runs.** `python3 tests/test_foo.py`
bypasses all of the above. Most test files import parent_server at
module level, and there is no clean shared hook that runs before a
directly-executed script's imports (Python only offers
`sitecustomize`/`usercustomize`, which are machine-global — too
invasive for a repo to install). A handful of files self-isolate
(`test_persistence_atomic.py`, `test_893_cors.py`,
`test_896_fused_id_rebind.py`, `test_dmx_bake.py`,
`screenshot_capture.py`); the rest do not. **On Windows, never run a
test file directly without prefixing `SLYLED_DATA`:**

```powershell
$env:SLYLED_DATA = (New-TemporaryFile).DirectoryName + '\slyled-test'
python -X utf8 tests\test_parent.py
```

or run it through pytest / run_all.py / the devgui instead. New tests
that import parent_server should copy the self-isolation preamble from
`test_893_cors.py` (set `SLYLED_DATA` to a `tempfile.mkdtemp()` before
the import, only if not already set).

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
2. `_ps._remotes.add(...)` a gyro Remote with `R_world_to_stage`,
   `calibrated`, `calibrated_against` set against the mover (#847's
   trust-cross-session-cal path).
3. `_ps._mover_engine.claim(...)` + `start_stream(...)`.
4. `remote.update_from_euler_deg(roll, pitch, yaw)` to inject orient.
5. `_ps._mover_engine._tick()` to advance the claim writer.
6. Assert against `_ps._mover_engine._claims[fid].pan_smooth` (or the
   universe buffer via `/api/dmx/monitor/<uni>`).

#851's contract block is the canonical template — clone its setup +
swap the assertions.
