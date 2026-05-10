"""Run all regression tests sequentially and print combined summary.

Run: python -X utf8 tests/regression/run_all.py

Gating flags:
- ``--live-rig`` (or ``SLYLED_LIVE_RIG=1``) — adds #682-EE/-FF live-rig
  suites that probe the orchestrator + camera nodes against a calibrated
  mover. CI skips by default.
- ``--weekly`` (or ``SLYLED_WEEKLY=1``) — adds slow Playwright suites
  whose underlying wiring rarely changes; running them every CI cycle is
  overkill. Intended for a once-a-week scheduled run.
"""
import argparse
import os
import subprocess
import sys
import time

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

# Always-run regression tests.
TESTS = [
    ('test_stage_setup.py',     'Stage Setup (API)'),
    ('test_layout_edit.py',     'Layout Edit (Playwright)'),
    ('test_timeline_bake.py',   'Timeline Bake (API)'),
    ('test_runtime_3d_show.py', 'Runtime 3D Show (Playwright)'),
    ('test_full_show.py',       'Full Show Integration'),
    # #784 PR-7 — SMART cal-pipeline emulator deleted along with the
    # legacy IK modules (mover_calibrator, coverage_math, sphere_model,
    # parametric_mover). The new `aim/` package is the only IK now;
    # `tests/aim/test_sphere.py` + `tests/aim/test_routes.py` are the
    # offline gates.
    # #741 (#739 follow-up) — save/restore round-trip. Builds a
    # non-trivial scene, exports to .slyshow, factory-resets, imports,
    # asserts every category survived to disk. Catches the next #739:
    # any future change that drops/zeros a state category on import
    # produces a red regression here, not a red show on stage.
    ('test_save_restore.py',    'Save/Restore Round-Trip (#741)'),
]

# Live-rig regression tests (gated, see module docstring).
# `test_post_cal_confirm.py` was deleted under #789 (Tier 1 SMART-
# pipeline sweep, 2026-05-03) — the post-cal confirm flow no longer
# exists under the #784/#785/#798 anchor-sphere model.
LIVE_RIG_TESTS = [
    ('test_beam_detect_canary.py',  'Beam-Detect Canary (live rig)'),
]

# Weekly-only regressions. Slow Playwright suites whose underlying
# wiring rarely changes — running them every CI cycle is overkill.
# Gated by `--weekly` or `SLYLED_WEEKLY=1`. Each suite still uses the
# same one-process-per-test isolation as the always-run TESTS.
WEEKLY_TESTS = [
    ('test_3d_vectors_cones.py',
     '3D vectors + cones toggle (Playwright, all 3 tabs)'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live-rig', action='store_true',
                    help='also run #682-EE/-FF live-rig regressions '
                         '(implies SLYLED_LIVE_RIG=1)')
    ap.add_argument('--weekly', action='store_true',
                    help='also run slow weekly Playwright regressions '
                         '(implies SLYLED_WEEKLY=1)')
    args = ap.parse_args()

    tests = list(TESTS)
    live_rig_on = args.live_rig or os.environ.get('SLYLED_LIVE_RIG') == '1'
    if live_rig_on:
        tests += LIVE_RIG_TESTS
    weekly_on = args.weekly or os.environ.get('SLYLED_WEEKLY') == '1'
    if weekly_on:
        tests += WEEKLY_TESTS

    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    if args.live_rig:
        env['SLYLED_LIVE_RIG'] = '1'
    if args.weekly:
        env['SLYLED_WEEKLY'] = '1'

    results = []
    total_passed = 0
    total_failed = 0
    start_all = time.time()

    for filename, label in tests:
        path = os.path.join('tests', 'regression', filename)
        print(f'\n{"#"*60}')
        print(f'# {label} ({filename})')
        print(f'{"#"*60}')

        start = time.time()
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', path],
            env=env,
            timeout=600,
        )
        elapsed = time.time() - start
        ok = result.returncode == 0

        results.append((label, ok, elapsed))

        if ok:
            total_passed += 1
        else:
            total_failed += 1

    elapsed_all = time.time() - start_all
    print(f'\n{"="*60}')
    print(f'  REGRESSION SUITE SUMMARY')
    print(f'{"="*60}')
    for label, ok, elapsed in results:
        status = 'PASS' if ok else 'FAIL'
        print(f'  [{status}] {label} ({elapsed:.1f}s)')
    print(f'{"="*60}')
    print(f'  {total_passed} suites passed, {total_failed} suites failed')
    if not live_rig_on:
        print(f'  (live-rig suites skipped — pass --live-rig or '
              f'SLYLED_LIVE_RIG=1 to include)')
    print(f'  Total time: {elapsed_all:.1f}s')
    print(f'{"="*60}')

    return 1 if total_failed else 0


if __name__ == '__main__':
    sys.exit(main())
