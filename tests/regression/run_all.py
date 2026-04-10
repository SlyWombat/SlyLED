"""Run all regression tests sequentially and print combined summary.

Run: python -X utf8 tests/regression/run_all.py
"""
import subprocess, sys, os, time

os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

TESTS = [
    ('test_stage_setup.py',     'Stage Setup (API)'),
    ('test_layout_edit.py',     'Layout Edit (Playwright)'),
    ('test_timeline_bake.py',   'Timeline Bake (API)'),
    ('test_runtime_3d_show.py', 'Runtime 3D Show (Playwright)'),
    ('test_full_show.py',       'Full Show Integration'),
]

results = []
total_passed = 0
total_failed = 0
start_all = time.time()

for filename, label in TESTS:
    path = os.path.join('tests', 'regression', filename)
    print(f'\n{"#"*60}')
    print(f'# {label} ({filename})')
    print(f'{"#"*60}')

    start = time.time()
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', path],
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
        timeout=300,
    )
    elapsed = time.time() - start
    ok = result.returncode == 0

    results.append((label, ok, elapsed))

    if ok:
        total_passed += 1
    else:
        total_failed += 1

# ── Combined Summary ─────────────────────────────────────────────────────
elapsed_all = time.time() - start_all
print(f'\n{"="*60}')
print(f'  REGRESSION SUITE SUMMARY')
print(f'{"="*60}')
for label, ok, elapsed in results:
    status = 'PASS' if ok else 'FAIL'
    print(f'  [{status}] {label} ({elapsed:.1f}s)')
print(f'{"="*60}')
print(f'  {total_passed} suites passed, {total_failed} suites failed')
print(f'  Total time: {elapsed_all:.1f}s')
print(f'{"="*60}')

sys.exit(1 if total_failed else 0)
