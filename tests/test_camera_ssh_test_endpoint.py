#!/usr/bin/env python3
"""test_camera_ssh_test_endpoint.py — #690-followup.

The per-node SSH test endpoint used to read only the saved config. If
the operator clicked Test without first clicking Save, the form values
never reached the server and the test ran against stale (or empty)
credentials, surfacing as "Authentication failed".

After the fix, the endpoint accepts ``user`` / ``password`` / ``keyPath``
in the request body. Present fields override saved values; omitted ones
fall through. The connect attempt itself happens against the live host
192.168.10.235, which we don't want to depend on in unit tests, so we
assert that paramiko is invoked with the expected kwargs by patching
``SSHClient.connect``.
"""
import os, sys, json
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


import parent_server
from parent_server import app, _camera_ssh, _encrypt_pw

# Patch paramiko.SSHClient.connect to capture kwargs without hitting the
# network. The endpoint then proceeds to exec_command("whoami"), so we
# stub that too with a fake stdout that returns "root".
import paramiko
_captured = {}

class _FakeChannel:
    def read(self): return b'root'

class _FakeOut:
    def read(self): return b'root'

def _fake_connect(self, **kwargs):
    _captured.clear()
    _captured.update(kwargs)
    # mimic a successful connect — no exception

def _fake_exec(self, cmd, timeout=None):
    return None, _FakeOut(), _FakeOut()

def _fake_close(self):
    pass

paramiko.SSHClient.connect = _fake_connect
paramiko.SSHClient.exec_command = _fake_exec
paramiko.SSHClient.close = _fake_close


# Reset to a known state.
with app.test_client() as c:
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})

# ── Test 1: form values override empty saved state ──────────────────────

section('Form values override saved')
ip = '10.0.0.42'

with app.test_client() as c:
    rv = c.post(f'/api/cameras/node/{ip}/ssh/test',
                json={'user': 'pi', 'password': 'raspberry', 'keyPath': ''})
    j = rv.get_json()
    ok(rv.status_code == 200, f'200 (got {rv.status_code})')
    ok(j.get('ok') is True, f'ok=True (got {j})')

ok(_captured.get('username') == 'pi',
   f'username from body: pi (got {_captured.get("username")!r})')
ok(_captured.get('password') == 'raspberry',
   f'password from body: raspberry (got {_captured.get("password")!r})')
ok('key_filename' not in _captured,
   f'no key_filename when keyPath is empty (got {_captured.get("key_filename")!r})')
ok(_captured.get('look_for_keys') is False,
   f'look_for_keys=False to prevent agent key fallback')
ok(_captured.get('allow_agent') is False,
   f'allow_agent=False to prevent agent key fallback')

# ── Test 2: omitted body fields fall through to saved ───────────────────

section('Omitted fields fall through to saved')

# Manually plant a per-node entry so _get_node_ssh has something to fall
# back to.
parent_server._camera_ssh[ip] = {
    'user': 'admin',
    'password': _encrypt_pw('s3cret'),
    'keyPath': '',
}

with app.test_client() as c:
    # Body completely empty — should use saved admin/s3cret.
    rv = c.post(f'/api/cameras/node/{ip}/ssh/test', json={})
    j = rv.get_json()
    ok(j.get('ok') is True, f'empty body still ok (got {j})')
ok(_captured.get('username') == 'admin',
   f'fall-through user: admin (got {_captured.get("username")!r})')
ok(_captured.get('password') == 's3cret',
   f'fall-through password: s3cret (got {_captured.get("password")!r})')

# ── Test 3: partial override — body provides user only ─────────────────

section('Partial override — body user, saved password')

with app.test_client() as c:
    rv = c.post(f'/api/cameras/node/{ip}/ssh/test',
                json={'user': 'override-user'})
    j = rv.get_json()
    ok(j.get('ok') is True, f'partial body ok (got {j})')
ok(_captured.get('username') == 'override-user',
   f'override-user from body (got {_captured.get("username")!r})')
ok(_captured.get('password') == 's3cret',
   f'password still falls through to saved (got {_captured.get("password")!r})')

# ── Test 4: empty creds give helpful error, not crash ──────────────────

section('Empty creds give an actionable error')

# Wipe the saved entry so nothing falls through.
parent_server._camera_ssh.pop(ip, None)

with app.test_client() as c:
    rv = c.post(f'/api/cameras/node/{ip}/ssh/test',
                json={'user': 'root', 'password': '', 'keyPath': ''})
    j = rv.get_json()
ok(rv.status_code == 200 and j.get('ok') is False,
   f'returns 200 ok=False (got {rv.status_code} {j})')
ok('No password or key' in (j.get('err') or ''),
   f'err mentions missing creds (got {j.get("err")!r})')

# ── Summary ─────────────────────────────────────────────────────────────

print(f'\n{"="*60}')
print(f'  {_pass} passed, {_fail} failed (out of {_pass + _fail})')
if _errors:
    print(f'  Failures: {", ".join(_errors)}')
print(f'{"="*60}')
sys.exit(1 if _fail else 0)
