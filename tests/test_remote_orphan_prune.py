#!/usr/bin/env python3
"""test_remote_orphan_prune.py — #690.

Orphan remotes (registered but never sent live data) must:
  - get registered_at timestamped at construction
  - flip soft_stale after STALE_NEVER_SOFT_SECS
  - flip hard-stale (stale_reason="never-active") after STALE_NEVER_HARD_SECS
  - be auto-pruned from RemoteRegistry.live_list once hard-stale
  - persist registered_at across save / load
  - be idempotently removed via DELETE /api/remotes/<id> (200 + removed bool)
"""
import os, sys, time, tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import remote_orientation as ro

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


# ── Construction stamps registered_at ───────────────────────────────────

section('Remote.registered_at')
before = time.time()
r = ro.Remote(id=1, name='Puck 1', kind=ro.KIND_PUCK)
after = time.time()
ok(before <= r.registered_at <= after,
   f'registered_at within construction window (got {r.registered_at})')

# ── never-active staleness path ─────────────────────────────────────────

section('check_staleness — never-active path')

r = ro.Remote(id=2, name='Orphan')
# Pretend it was registered well in the past — fresh.
r.registered_at = time.time() - 1
r.check_staleness()
ok(not r.soft_stale and r.stale_reason is None,
   f'fresh orphan: not stale (soft={r.soft_stale} reason={r.stale_reason})')

# Past soft threshold but not hard.
r.registered_at = time.time() - (ro.STALE_NEVER_SOFT_SECS + 10)
r.check_staleness()
ok(r.soft_stale and r.stale_reason is None,
   f'soft-stale orphan (soft={r.soft_stale} reason={r.stale_reason})')

# Past hard threshold.
r.registered_at = time.time() - (ro.STALE_NEVER_HARD_SECS + 10)
r.check_staleness()
ok(r.stale_reason == 'never-active',
   f'hard-stale: stale_reason="never-active" (got {r.stale_reason!r})')
ok(not r.soft_stale, f'soft cleared once hard latches (got {r.soft_stale})')
ok(r.connection_state == 'stale',
   f'connection_state=stale (got {r.connection_state!r})')

# Receiving data resets the path: now treated like a normal active remote.
r2 = ro.Remote(id=3, name='Active')
r2.registered_at = time.time() - (ro.STALE_NEVER_HARD_SECS + 10)
r2.last_data = time.time()      # data did arrive
r2.check_staleness()
ok(r2.stale_reason is None,
   f'data arrived → not never-active (got {r2.stale_reason!r})')

# ── Calibrated remotes are NOT promoted via never-active path ───────────

section('Calibrated remotes use the comms-silence path, not never-active')

r3 = ro.Remote(id=4, name='Calibrated')
r3.calibrated = True
r3.R_world_to_stage = (1.0, 0.0, 0.0, 0.0)
r3.calibrated_at = time.time()
r3.last_data = time.time() - (ro.STALE_HARD_SECS + 10)
r3.registered_at = time.time() - 5
r3.check_staleness()
ok(r3.stale_reason == 'connection-lost',
   f'comms-silence path picks "connection-lost", not "never-active" '
   f'(got {r3.stale_reason!r})')

# ── live_list auto-prunes never-active orphans ──────────────────────────

section('RemoteRegistry.live_list auto-pruning')

with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
    path = f.name
try:
    reg = ro.RemoteRegistry(data_path=path)
    fresh = reg.add(name='Fresh Puck', kind=ro.KIND_PUCK,
                     device_id='gyro-1.2.3.4')
    orphan = reg.add(name='Orphan Puck', kind=ro.KIND_PUCK,
                     device_id='gyro-9.9.9.9')
    # Backdate the orphan's registered_at past the hard cutoff.
    orphan.registered_at = time.time() - (ro.STALE_NEVER_HARD_SECS + 10)

    # First call: orphan should be pruned, fresh remains.
    snap = reg.live_list()
    ids = sorted(s['id'] for s in snap)
    ok(ids == [fresh.id],
       f'orphan pruned, fresh remains (got ids {ids})')
    ok(reg.get(orphan.id) is None,
       f'orphan removed from registry too (got {reg.get(orphan.id)})')
    ok(reg.get(fresh.id) is not None,
       f'fresh remote still in registry')

    # Second call: nothing further to prune.
    snap2 = reg.live_list()
    ok(len(snap2) == 1 and snap2[0]['id'] == fresh.id,
       f'second call stable (got {[(s["id"]) for s in snap2]})')

    # registered_at survives save+load.
    reg.save()
    reg2 = ro.RemoteRegistry(data_path=path)
    reg2.load()
    survivor = reg2.get(fresh.id)
    ok(survivor is not None and abs(survivor.registered_at - fresh.registered_at) < 0.01,
       f'registered_at persists across save/load')
finally:
    os.unlink(path)

# ── DELETE endpoint idempotency ─────────────────────────────────────────

section('DELETE /api/remotes/<id> — idempotent')

# Use the Flask test client.
import parent_server
from parent_server import app, _remotes as live_reg

with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    rv = c.post('/api/remotes', json={'name': 'TestPuck', 'kind': 'gyro-puck'})
    ok(rv.status_code == 200, f'create remote → 200 (got {rv.status_code})')
    rid = rv.get_json()['remote']['id']

    rv = c.delete(f'/api/remotes/{rid}')
    j = rv.get_json()
    ok(rv.status_code == 200 and j.get('ok') is True and j.get('removed') is True,
       f'first DELETE → 200 ok=True removed=True (got {rv.status_code} {j})')

    rv = c.delete(f'/api/remotes/{rid}')
    j = rv.get_json()
    ok(rv.status_code == 200 and j.get('ok') is True and j.get('removed') is False,
       f'second DELETE → 200 ok=True removed=False (got {rv.status_code} {j})')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
