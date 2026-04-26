#!/usr/bin/env python3
"""test_cal_orphan_recovery.py — #691 fix-fix + #693.

Two related defects:

1. #691 commit 2a2507c inserted `def _warm_start_from_home(f)` at
   column 0 inside `_mover_cal_thread_markers_body`. Python parses
   the rest of the function as dead code under the new helper, so
   the thread runs through the anchor-log line and exits silently —
   no terminal status set, cal lock wedged, only a restart recovers.

2. #693 — even without the parsing accident, any control-flow bug
   that exits a cal thread body without setting a terminal status
   leaves the lock held forever. The wrappers must detect the
   non-terminal exit and force `status=error` + release the lock.
   The /cancel and /start endpoints must also recover from a dead
   worker thread (the original symptom: cancel returns ok but the
   lock stays held).

Asserts:
  - `_warm_start_from_home` is a module-level function (not
    accidentally re-nested inside the body again).
  - `_mover_cal_thread_markers_body` has its full ~530-line body
    intact (the dark-reference + marker prescan code that got
    orphaned by #691 commit 2a2507c is back inside the function).
  - All three cal-thread wrappers (legacy, v2, markers) detect a
    body that returned with non-terminal status and force error.
  - `/api/calibration/mover/<fid>/start` overwrites a job whose
    worker thread is dead.
  - `/api/calibration/mover/<fid>/cancel` releases the lock when
    the worker is already dead.
"""
import os, sys, ast, threading, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

_pass = 0
_fail = 0
_errors = []


def ok(cond, name):
    global _pass, _fail
    if cond:
        _pass += 1
    else:
        _fail += 1
        _errors.append(name)
        print(f'  [FAIL] {name}')


def section(s):
    print(f'\n── {s} ──')


# ── Static check: function structure ────────────────────────────────────

section('AST: _warm_start_from_home is module-level, body is intact')

with open('desktop/shared/parent_server.py', encoding='utf-8-sig') as fh:
    tree = ast.parse(fh.read())
fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
ok('_warm_start_from_home' in fns,
   '_warm_start_from_home defined at module scope')
ok('_mover_cal_thread_markers_body' in fns,
   '_mover_cal_thread_markers_body defined at module scope')
if '_mover_cal_thread_markers_body' in fns:
    body_fn = fns['_mover_cal_thread_markers_body']
    body_lines = body_fn.end_lineno - body_fn.lineno + 1
    # Was 533 lines pre-bug; ~74 lines when accidentally truncated.
    ok(body_lines > 400,
       f'_mover_cal_thread_markers_body has full body ({body_lines} lines, '
       f'must be > 400 — was 74 with the #691 parse accident)')
if '_warm_start_from_home' in fns:
    helper_lines = (fns['_warm_start_from_home'].end_lineno
                    - fns['_warm_start_from_home'].lineno + 1)
    ok(helper_lines < 50,
       f'_warm_start_from_home is small ({helper_lines} lines — '
       f'should be ~24, was 480+ when it ate the body)')


# ── Wrapper orphan-guard behaviour ──────────────────────────────────────

import parent_server
from parent_server import (_mover_cal_thread, _mover_cal_thread_v2,
                            _mover_cal_thread_markers,
                            _mover_cal_jobs, _set_calibrating)

section('Wrapper forces terminal status when body returns silently')

# Stub the bodies so they exit without setting status — simulating the
# #691 parse-accident or any future control-flow bug.
def _silent_body_legacy(*_a, **_kw): pass
def _silent_body_v2(*_a, **_kw): pass
def _silent_body_markers(*_a, **_kw): pass
parent_server._mover_cal_thread_body = _silent_body_legacy
parent_server._mover_cal_thread_v2_body = _silent_body_v2
parent_server._mover_cal_thread_markers_body = _silent_body_markers

# Stub the cleanup helpers so we don't need a live engine / camera lock.
parent_server._restore_camera_lock = lambda *_a, **_kw: None
parent_server._close_cal_trace = lambda *_a, **_kw: None
parent_server._park_fixture_at_home = lambda *_a, **_kw: None
parent_server._set_calibrating = lambda fid, on: None

cam_stub = {"id": 99, "name": "Stub Cam", "cameraIp": ""}

# Markers wrapper.
fid = 100
_mover_cal_jobs[str(fid)] = {"status": "running", "phase": "starting",
                             "progress": 0, "error": None}
_mover_cal_thread_markers(fid, cam_stub, "127.0.0.1", [0, 255, 0])
job = _mover_cal_jobs[str(fid)]
ok(job["status"] == "error",
   f'markers wrapper flagged orphan as error (got status={job["status"]!r})')
ok("orphaned" in (job.get("error") or ""),
   f'error mentions orphaned (got {job.get("error")!r})')

# v2 wrapper.
fid = 101
_mover_cal_jobs[str(fid)] = {"status": "running", "phase": "starting",
                             "progress": 0, "error": None}
_mover_cal_thread_v2(fid, cam_stub, "127.0.0.1", [0, 255, 0])
job = _mover_cal_jobs[str(fid)]
ok(job["status"] == "error",
   f'v2 wrapper flagged orphan as error (got status={job["status"]!r})')
ok("orphaned" in (job.get("error") or ""),
   f'v2 error mentions orphaned (got {job.get("error")!r})')

# Legacy wrapper.
fid = 102
_mover_cal_jobs[str(fid)] = {"status": "running", "phase": "starting",
                             "progress": 0, "error": None}
_mover_cal_thread(fid, cam_stub, "127.0.0.1", [0, 255, 0])
job = _mover_cal_jobs[str(fid)]
ok(job["status"] == "error",
   f'legacy wrapper flagged orphan as error (got status={job["status"]!r})')
ok("orphaned" in (job.get("error") or ""),
   f'legacy error mentions orphaned (got {job.get("error")!r})')

# Negative case: body that DOES set status=done leaves it alone.
fid = 103
def _good_body(*a, **kw):
    parent_server._mover_cal_jobs[str(fid)]["status"] = "done"
parent_server._mover_cal_thread_markers_body = _good_body
_mover_cal_jobs[str(fid)] = {"status": "running", "phase": "starting",
                             "progress": 0, "error": None}
_mover_cal_thread_markers(fid, cam_stub, "127.0.0.1", [0, 255, 0])
ok(_mover_cal_jobs[str(fid)]["status"] == "done",
   f'happy-path body keeps its done status (got '
   f'{_mover_cal_jobs[str(fid)]["status"]!r})')


# ── Endpoint recovery: /start + /cancel against dead worker thread ─────

section('/start: dead-thread orphan is overwritten, not 409')

from parent_server import app

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

    # Plant an orphan: status=running, but `thread` is a dead Thread.
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    parent_server._mover_cal_jobs["999"] = {
        "status": "running", "phase": "starting",
        "progress": 0, "error": None,
        "thread": dead,
    }

    # /start should NOT return 409 — the worker is dead, recover.
    rv = c.post('/api/calibration/mover/999/start',
                json={'mode': 'legacy'})
    # Will fail later with "DMX fixture not found" because we didn't
    # create one, but the 409-orphan path should NOT be hit. Acceptable
    # statuses: 404 (fixture missing — expected) or 200 (started).
    ok(rv.status_code != 409,
       f'/start cleared orphan instead of 409 (got {rv.status_code} {rv.get_json()})')

section('/cancel: dead-thread orphan releases the lock')

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    parent_server._mover_cal_jobs["888"] = {
        "status": "running", "phase": "starting",
        "progress": 0, "error": None,
        "thread": dead,
    }
    rv = c.post('/api/calibration/mover/888/cancel')
    j = rv.get_json()
    ok(rv.status_code == 200, f'/cancel returns 200 (got {rv.status_code})')
    ok(j.get("orphan") is True,
       f'/cancel reports orphan flag (got {j})')
    ok(parent_server._mover_cal_jobs["888"]["status"] == "cancelled",
       f'job marked cancelled (got '
       f'{parent_server._mover_cal_jobs["888"]["status"]!r})')


# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
