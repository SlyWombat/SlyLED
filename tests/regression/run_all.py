"""Run all regression tests sequentially and print combined summary.

Run: python -X utf8 tests/regression/run_all.py

Live-rig (#682-EE/-FF) suites are gated behind ``--live-rig`` or
``SLYLED_LIVE_RIG=1``. They probe the orchestrator + camera nodes and
require a calibrated mover, so CI skips them by default.
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
]

# Live-rig regression tests (gated, see module docstring).
LIVE_RIG_TESTS = [
    ('test_beam_detect_canary.py',  'Beam-Detect Canary (live rig)'),
    ('test_post_cal_confirm.py',    'Post-Cal Confirm (live rig)'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--live-rig', action='store_true',
                    help='also run #682-EE/-FF live-rig regressions '
                         '(implies SLYLED_LIVE_RIG=1)')
    args = ap.parse_args()

    tests = list(TESTS)
    live_rig_on = args.live_rig or os.environ.get('SLYLED_LIVE_RIG') == '1'
    if live_rig_on:
        tests += LIVE_RIG_TESTS

    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    if args.live_rig:
        env['SLYLED_LIVE_RIG'] = '1'

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
