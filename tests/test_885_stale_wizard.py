#!/usr/bin/env python3
"""
test_885_stale_wizard.py — Regression tests for #885.

When a Remote is stale (orient session ended, puck/phone idle, lost
heartbeat, etc.), its cached ``last_quat_world`` is no longer a live
reading. The SPA aim-axis wizard polled the diagnostic endpoint three
times in a row, got the same cached quat, and the wizard math
correctly rejected with ``insufficient_pitch`` because all three
input poses were byte-identical — but the operator had visibly moved
the device. The fix:

  * Option 1 — ``/api/remotes/<id>/diagnostic`` returns ``rawQuat =
    None`` when ``stale_reason`` is set, so the SPA's existing
    null-check surfaces a truthful message.
  * Option 3 — ``/api/remotes/<id>/aim-wizard`` and the deviceId
    sibling reject stale remotes server-side with
    ``err: gyro_not_streaming``, ``detail: <operator-friendly
    sentence>``.

Usage:
    python tests/test_885_stale_wizard.py
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..',
                                'desktop', 'shared'))

import parent_server as _ps
from remote_orientation import KIND_GYRO

results = []


def ok(name, cond, detail=''):
    results.append((name, bool(cond), detail))


def _quat_from_axis_angle(axis, angle):
    """Mini reimpl so this file stays self-contained — full version
    lives in remote_math but importing it for one helper bloats the
    test's surface."""
    x, y, z = axis
    n = math.sqrt(x * x + y * y + z * z)
    if n == 0:
        return (1.0, 0.0, 0.0, 0.0)
    s = math.sin(angle / 2.0) / n
    return (math.cos(angle / 2.0), x * s, y * s, z * s)


def _seed_remote(stale_reason=None, q=(1.0, 0.0, 0.0, 0.0),
                 device_id='gyro-test-885'):
    """Reset the registry and seed one Remote with a known stale state.
    Returns the registered Remote so the test can drive its id/device_id
    directly."""
    with _ps.app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    r = _ps._remotes.add(name='Test Gyro', kind=KIND_GYRO,
                          device_id=device_id)
    r.last_quat_world = q
    r.stale_reason = stale_reason
    return r


def run():
    qn = (1.0, 0.0, 0.0, 0.0)
    qp = _quat_from_axis_angle((1.0, 0.0, 0.0), -math.radians(30))
    qy = _quat_from_axis_angle((0.0, 0.0, 1.0), -math.radians(30))

    # ── Option 1: diagnostic returns rawQuat=None when stale ──────
    r_stale = _seed_remote(stale_reason='session-ended',
                            q=(0.5, 0.5, 0.5, 0.5))
    with _ps.app.test_client() as c:
        resp = c.get(f'/api/remotes/{r_stale.id}/diagnostic')
        body = resp.get_json() or {}
        ok('stale remote: diagnostic returns 200',
           resp.status_code == 200, f'status={resp.status_code}')
        ok('stale remote: rawQuat is null',
           body.get('rawQuat') is None,
           f'rawQuat={body.get("rawQuat")}')
        ok('stale remote: bodyForwardInWorld is null',
           body.get('bodyForwardInWorld') is None,
           f'bodyForwardInWorld={body.get("bodyForwardInWorld")}')
        ok('stale remote: bodyUpInWorld is null',
           body.get('bodyUpInWorld') is None,
           f'bodyUpInWorld={body.get("bodyUpInWorld")}')
        # The stale_reason field itself still surfaces so the SPA can
        # render the truthful state.
        ok('stale remote: staleReason still surfaces',
           body.get('staleReason') == 'session-ended',
           f'staleReason={body.get("staleReason")}')

    # ── Live remote: rawQuat round-trips ─────────────────────────
    r_live = _seed_remote(stale_reason=None,
                           q=(0.1, 0.2, 0.3, 0.9))
    with _ps.app.test_client() as c:
        resp = c.get(f'/api/remotes/{r_live.id}/diagnostic')
        body = resp.get_json() or {}
        rq = body.get('rawQuat')
        ok('live remote: rawQuat returns the cached quat',
           rq is not None and len(rq) == 4
           and abs(rq[0] - 0.1) < 1e-6,
           f'rawQuat={rq}')

    # ── Option 3: wizard rejects stale remote with gyro_not_streaming ──
    r_stale = _seed_remote(stale_reason='lost')
    with _ps.app.test_client() as c:
        resp = c.post(f'/api/remotes/{r_stale.id}/aim-wizard', json={
            'poses': [
                {'role': 'neutral',       'quat': list(qn)},
                {'role': 'pitch_forward', 'quat': list(qp)},
                {'role': 'yaw_left',      'quat': list(qy)},
            ],
        })
        body = resp.get_json() or {}
        ok('stale wizard: returns 400 (not 200 insufficient_pitch)',
           resp.status_code == 400,
           f'status={resp.status_code} body={body}')
        ok('stale wizard: err == gyro_not_streaming',
           body.get('err') == 'gyro_not_streaming',
           f'err={body.get("err")}')
        ok('stale wizard: detail is the operator-friendly sentence',
           body.get('detail', '').startswith('Gyro is idle'),
           f'detail={body.get("detail")}')
        ok('stale wizard: staleReason surfaces too',
           body.get('staleReason') == 'lost',
           f'staleReason={body.get("staleReason")}')

    # The same protection on the deviceId-keyed sibling route, when
    # the Remote already exists.
    r_stale = _seed_remote(stale_reason='session-ended',
                            device_id='gyro-1.2.3.4')
    with _ps.app.test_client() as c:
        resp = c.post('/api/remotes/aim-wizard', json={
            'deviceId': 'gyro-1.2.3.4',
            'poses': [
                {'role': 'neutral',       'quat': list(qn)},
                {'role': 'pitch_forward', 'quat': list(qp)},
                {'role': 'yaw_left',      'quat': list(qy)},
            ],
        })
        body = resp.get_json() or {}
        ok('stale wizard (by device): returns 400',
           resp.status_code == 400,
           f'status={resp.status_code} body={body}')
        ok('stale wizard (by device): err == gyro_not_streaming',
           body.get('err') == 'gyro_not_streaming',
           f'err={body.get("err")}')

    # ── Live remote: wizard happy-path still works ───────────────
    r_live = _seed_remote(stale_reason=None)
    with _ps.app.test_client() as c:
        resp = c.post(f'/api/remotes/{r_live.id}/aim-wizard', json={
            'poses': [
                {'role': 'neutral',       'quat': list(qn)},
                {'role': 'pitch_forward', 'quat': list(qp)},
                {'role': 'yaw_left',      'quat': list(qy)},
            ],
        })
        body = resp.get_json() or {}
        ok('live wizard: returns 200',
           resp.status_code == 200,
           f'status={resp.status_code} body={body}')
        ok('live wizard: ok=True', body.get('ok') is True,
           f'body={body}')
        ok('live wizard: derived forwardLocal returned',
           isinstance(body.get('forwardLocal'), list) and
           len(body['forwardLocal']) == 3,
           f'forwardLocal={body.get("forwardLocal")}')
        # #885 follow-up — every wizard response (success or failure)
        # carries a diagnostics block so the SPA's debug renderer can
        # show captured quats + math intermediates on the failure
        # modal.
        diag = body.get('diagnostics') or {}
        ok('live wizard: diagnostics block present',
           isinstance(diag, dict) and bool(diag), f'diag={diag}')
        ok('live wizard: diagnostics.pitchAngleDeg present',
           isinstance(diag.get('pitchAngleDeg'), (int, float)),
           f'pitchAngleDeg={diag.get("pitchAngleDeg")}')
        ok('live wizard: diagnostics.crossMagnitude > 0.7',
           (diag.get('crossMagnitude') or 0) > 0.7,
           f'crossMagnitude={diag.get("crossMagnitude")}')
        ok('live wizard: diagnostics.forwardLocal matches response',
           diag.get('forwardLocal') == body.get('forwardLocal'),
           f'diag.forwardLocal={diag.get("forwardLocal")} '
           f'body.forwardLocal={body.get("forwardLocal")}')

    # ── Failure case: degenerate axes returns diagnostics with the
    #    captured quats + the pitch/yaw math, so the SPA can show
    #    the operator exactly which capture was bad. ──────────────
    r_live2 = _seed_remote(stale_reason=None,
                            device_id='gyro-degen-test')
    # Same axis for pitch and yaw → cross magnitude ≈ 0 → rejected.
    same_axis_pitch = _quat_from_axis_angle((1.0, 0.0, 0.0),
                                              -math.radians(30))
    same_axis_yaw   = _quat_from_axis_angle((1.0, 0.0, 0.0),
                                              -math.radians(30))
    with _ps.app.test_client() as c:
        resp = c.post(f'/api/remotes/{r_live2.id}/aim-wizard', json={
            'poses': [
                {'role': 'neutral',       'quat': list(qn)},
                {'role': 'pitch_forward', 'quat': list(same_axis_pitch)},
                {'role': 'yaw_left',      'quat': list(same_axis_yaw)},
            ],
        })
        body = resp.get_json() or {}
        ok('degenerate wizard: returns 400',
           resp.status_code == 400, f'status={resp.status_code}')
        ok('degenerate wizard: err = degenerate_axes',
           body.get('err') == 'degenerate_axes',
           f'err={body.get("err")}')
        diag = body.get('diagnostics') or {}
        ok('degenerate wizard: diagnostics block present',
           isinstance(diag, dict) and bool(diag), f'diag={diag}')
        ok('degenerate wizard: input quats echoed in diagnostics',
           isinstance(diag.get('inputQuats'), dict) and
           'pitch_forward' in diag['inputQuats'],
           f'inputQuats={diag.get("inputQuats")}')
        ok('degenerate wizard: pitchAngleDeg computed before reject',
           isinstance(diag.get('pitchAngleDeg'), (int, float)),
           f'pitchAngleDeg={diag.get("pitchAngleDeg")}')
        ok('degenerate wizard: crossMagnitude surfaces (the value '
           'the error message quotes)',
           isinstance(diag.get('crossMagnitude'), (int, float)) and
           diag['crossMagnitude'] < 0.7,
           f'crossMagnitude={diag.get("crossMagnitude")}')

    # ── Insufficient-pitch rejection: diagnostics shows the angle
    #    that fell short. ────────────────────────────────────────
    r_live3 = _seed_remote(stale_reason=None,
                            device_id='gyro-tinypitch-test')
    tiny_pitch = _quat_from_axis_angle((1.0, 0.0, 0.0),
                                         -math.radians(2))
    with _ps.app.test_client() as c:
        resp = c.post(f'/api/remotes/{r_live3.id}/aim-wizard', json={
            'poses': [
                {'role': 'neutral',       'quat': list(qn)},
                {'role': 'pitch_forward', 'quat': list(tiny_pitch)},
                {'role': 'yaw_left',      'quat': list(qy)},
            ],
        })
        body = resp.get_json() or {}
        ok('insufficient_pitch: err code present',
           body.get('err') == 'insufficient_pitch',
           f'err={body.get("err")}')
        diag = body.get('diagnostics') or {}
        ok('insufficient_pitch: pitchAngleDeg < 10',
           isinstance(diag.get('pitchAngleDeg'), (int, float)) and
           diag['pitchAngleDeg'] < 10,
           f'pitchAngleDeg={diag.get("pitchAngleDeg")}')
        # Even though the rejection happened before yaw axes were
        # computed, the input quats should still be echoed.
        ok('insufficient_pitch: inputQuats echoed',
           isinstance(diag.get('inputQuats'), dict) and
           len(diag['inputQuats']) == 3,
           f'inputQuats={diag.get("inputQuats")}')

    # ── First-time deviceId auto-register still works (the guard
    #    intentionally skips not-yet-registered remotes so the very
    #    first call can land). ────────────────────────────────────
    with _ps.app.test_client() as c:
        c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
        resp = c.post('/api/remotes/aim-wizard', json={
            'deviceId': 'gyro-9.9.9.9',
            'poses': [
                {'role': 'neutral',       'quat': list(qn)},
                {'role': 'pitch_forward', 'quat': list(qp)},
                {'role': 'yaw_left',      'quat': list(qy)},
            ],
        })
        body = resp.get_json() or {}
        ok('first-time auto-register: not blocked by stale guard '
           '(returns 200)',
           resp.status_code == 200,
           f'status={resp.status_code} body={body}')

    # ── Print results ────────────────────────────────────────────
    passed = sum(1 for _, v, _ in results if v)
    failed = sum(1 for _, v, _ in results if not v)
    for name, v, detail in results:
        status = 'PASS' if v else 'FAIL'
        line = f'  [{status}] {name}'
        if detail and not v:
            line += f'  ({detail})'
        print(line, flush=True)
    print(f'\n{passed} passed, {failed} failed out of {len(results)} tests')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(run())
