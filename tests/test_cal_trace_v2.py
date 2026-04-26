#!/usr/bin/env python3
"""test_cal_trace_v2.py — #686 v2 path coverage.

Acceptance criterion #1: every mover-cal path (markers / v2 / legacy)
writes a cal-trace NDJSON. Pre-fix, only markers + legacy were
instrumented; v2 silently produced no trace.

Light static check — verifies the v2 body imports/uses CalTraceRecorder
and record_decision per-target. Full end-to-end requires a live
fixture + camera which the suite intentionally avoids.
"""
import os, sys, ast
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        print(f'  [FAIL] {name}')


# Read the v2 body source and assert it references CalTraceRecorder
# at least once + record_decision at least once.
with open('desktop/shared/parent_server.py', encoding='utf-8-sig') as f:
    src = f.read()
tree = ast.parse(src)

v2_body_node = None
for node in ast.walk(tree):
    if (isinstance(node, ast.FunctionDef)
            and node.name == '_mover_cal_thread_v2_body'):
        v2_body_node = node
        break

ok(v2_body_node is not None, 'v2 body function exists')

if v2_body_node:
    body_src = ast.unparse(v2_body_node)
    ok('CalTraceRecorder' in body_src,
       'v2 body instantiates CalTraceRecorder')
    ok('record_seed' in body_src,
       'v2 body calls record_seed')
    ok('record_decision' in body_src,
       'v2 body calls record_decision')
    ok('mode="v2"' in body_src or "mode='v2'" in body_src,
       'v2 body tags trace with mode=v2')

# Sanity: GET /api/calibration/traces still resolves.
import parent_server
from parent_server import app

with app.test_client() as c:
    rv = c.get('/api/calibration/traces')
    ok(rv.status_code == 200, f'GET /api/calibration/traces → 200 (got {rv.status_code})')
    j = rv.get_json()
    ok('traces' in j, f'response has traces field (got {list(j.keys())})')

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
