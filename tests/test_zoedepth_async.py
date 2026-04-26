#!/usr/bin/env python3
"""test_zoedepth_async.py — #696.

POST /api/space/scan/zoedepth used to block synchronously through ~15 s
of ZoeDepth inference per camera. Any rig with >2 cameras tripped the
SPA's 30 s XHR timeout, surfacing as "Failed: unknown" while the
orchestrator silently completed the scan and saved the cloud.

Two changes are now in place:

1. The endpoint kicks off a background thread and returns immediately
   with `{ok: True, started: True}`.
2. New GET /api/space/scan/zoedepth/status returns running flag,
   progress 0..100, current message, full per-stage log buffer, and
   either a result summary or a clear error string.

The SPA polls the status endpoint and renders the log as it grows.
"""
import os, sys, time, threading
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


def section(s):
    print(f'\n── {s} ──')


import parent_server
from parent_server import app

# ── Status endpoint exists + returns the new shape ──────────────────────

section('GET /api/space/scan/zoedepth/status — fresh state')

with app.test_client() as c:
    rv = c.get('/api/space/scan/zoedepth/status')
    j = rv.get_json()
    ok(rv.status_code == 200, f'200 (got {rv.status_code})')
    for field in ('running', 'progress', 'message', 'log', 'result',
                  'error', 'startedAt'):
        ok(field in j, f'response carries {field!r}')
    ok(j['running'] is False, f'running=False on cold state')
    ok(isinstance(j['log'], list), f'log is a list')


# ── 409 when a scan is already running ─────────────────────────────────

section('POST returns 409 when already running')

# Plant a "running" state without starting a real thread.
parent_server._zoe_scan_state['running'] = True
parent_server._zoe_scan_state['progress'] = 42
with app.test_client() as c:
    rv = c.post('/api/space/scan/zoedepth', json={})
    j = rv.get_json()
    ok(rv.status_code == 409, f'409 (got {rv.status_code})')
    ok('already in progress' in (j.get('err') or ''),
       f'err mentions in-progress (got {j})')
    ok(j.get('progress') == 42, f'returns current progress')
parent_server._zoe_scan_state['running'] = False


# ── Empty-camera-set returns a clean 400 (not async-running) ───────────

section('No positioned cameras → 400, NOT silent async failure')

parent_server._zoe_scan_state['running'] = False
# Force empty fixtures + layout for this assertion.
saved_fix = parent_server._fixtures
saved_layout = parent_server._layout
parent_server._fixtures = []
parent_server._layout = {'children': []}
try:
    with app.test_client() as c:
        rv = c.post('/api/space/scan/zoedepth', json={})
        ok(rv.status_code == 400,
           f'400 with no cameras (got {rv.status_code})')
        ok((rv.get_json() or {}).get('err'),
           f'err present in response')
finally:
    parent_server._fixtures = saved_fix
    parent_server._layout = saved_layout


# ── _zoe_log appends to the buffer + sets message ──────────────────────

section('_zoe_log appends to the log buffer')

parent_server._zoe_scan_state['log'] = []
parent_server._zoe_scan_state['message'] = ''
parent_server._zoe_log('info', 'first stage')
parent_server._zoe_log('warn', 'something to flag')
parent_server._zoe_log('error', 'fatal here')
log_buf = parent_server._zoe_scan_state['log']
ok(len(log_buf) == 3, f'three entries in log (got {len(log_buf)})')
ok(log_buf[0]['level'] == 'info' and log_buf[0]['message'] == 'first stage',
   f'info entry recorded')
ok(log_buf[1]['level'] == 'warn',
   f'warn entry recorded')
ok(log_buf[2]['level'] == 'error',
   f'error entry recorded')
ok(parent_server._zoe_scan_state['message'] == 'fatal here',
   f'message reflects most recent log line')
for e in log_buf:
    ok('ts' in e and e['ts'].endswith('Z'),
       f'log entry carries ISO ts ({e})')


# ── Status reflects an in-progress scan with log entries ───────────────

section('Status returns log + progress while running')

parent_server._zoe_scan_state.update({
    'running': True, 'progress': 55, 'message': 'cam 2/3 inference',
    'error': None, 'result': None,
})
with app.test_client() as c:
    rv = c.get('/api/space/scan/zoedepth/status')
    j = rv.get_json()
    ok(j['running'] is True, 'running=True')
    ok(j['progress'] == 55, f'progress=55 (got {j["progress"]})')
    ok(j['message'] == 'cam 2/3 inference', 'message echoes')
    ok(len(j['log']) >= 3, f'log entries returned ({len(j["log"])})')

# Cleanup.
parent_server._zoe_scan_state.update({
    'running': False, 'progress': 0, 'message': '', 'log': [],
    'result': None, 'error': None,
})


# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
