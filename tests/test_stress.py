#!/usr/bin/env python3
"""
test_stress.py — Incremental stress test for SlyLED parent server.

Scales from 10 to 132 fixtures across 5 tiers, measuring API response times,
memory usage, and network traffic at each level.  Designed to run inside a
Docker container with tshark for packet capture.

Usage:
    python tests/test_stress.py              # run all tiers
    python tests/test_stress.py --tier 3     # run up to tier 3 only
    python tests/test_stress.py --json       # output JSON metrics

Tiers:
    1:  10 fixtures  (8 DMX + 2 LED)
    2:  30 fixtures  (24 DMX + 6 LED)
    3:  66 fixtures  (60 DMX + 6 LED)
    4: 100 fixtures  (88 DMX + 12 LED)
    5: 132 fixtures  (120 DMX + 12 LED)  ← full target from #128
"""

import sys, os, json, time, threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

PORT = 18095
BASE = f'http://127.0.0.1:{PORT}'

# ── Metrics collection ────────────────────────────────────────────────────────

class Metrics:
    def __init__(self):
        self.tiers = []

    def add_tier(self, tier):
        self.tiers.append(tier)

    def as_table(self):
        hdr = ('Tier', 'Fixtures', 'DMX', 'LED', 'Children',
               'Create(s)', 'Layout Save(s)', 'Layout Load(s)',
               'Fixtures GET(s)', 'Bake(s)', 'Patch(s)',
               'Reset(s)', 'Mem MB', 'Net KB Sent', 'Net KB Recv',
               'Errors')
        rows = [hdr]
        for t in self.tiers:
            rows.append((
                t['tier'], t['total'], t['dmx'], t['led'], t['children'],
                f"{t['create_s']:.3f}", f"{t['layout_save_s']:.3f}",
                f"{t['layout_load_s']:.3f}", f"{t['fixtures_get_s']:.3f}",
                f"{t['bake_s']:.3f}", f"{t['patch_s']:.3f}",
                f"{t['reset_s']:.3f}", f"{t['mem_mb']:.1f}",
                f"{t['net_sent_kb']:.1f}", f"{t['net_recv_kb']:.1f}",
                t['errors']
            ))
        return rows

    def markdown(self):
        rows = self.as_table()
        lines = []
        # Header
        lines.append('| ' + ' | '.join(str(c) for c in rows[0]) + ' |')
        lines.append('| ' + ' | '.join('---:' if i > 0 else '---' for i, _ in enumerate(rows[0])) + ' |')
        for row in rows[1:]:
            lines.append('| ' + ' | '.join(str(c) for c in row) + ' |')
        return '\n'.join(lines)

    def json(self):
        return json.dumps(self.tiers, indent=2)


metrics = Metrics()

# ── Network traffic measurement ───────────────────────────────────────────────
# Track bytes at application level (request body + response body sizes).
# More reliable than tshark in containers and captures actual payload.

class NetCounter:
    """Track cumulative HTTP request/response byte counts."""
    def __init__(self):
        self.sent = 0  # request bodies
        self.recv = 0  # response bodies
        self._active = False

    def start(self):
        self.sent = 0
        self.recv = 0
        self._active = True

    def stop(self):
        self._active = False
        return self.sent, self.recv

    def record_request(self, body_bytes):
        if self._active:
            self.sent += body_bytes

    def record_response(self, body_bytes):
        if self._active:
            self.recv += body_bytes

_net = NetCounter()

def reset_capture():
    _net.start()

def stop_capture():
    return _net.stop()

# ── Memory measurement ────────────────────────────────────────────────────────

def get_memory_mb():
    """Get current process RSS in MB."""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return proc.memory_info().rss / (1024 * 1024)
    except ImportError:
        # Fallback: read /proc
        try:
            with open('/proc/self/status') as f:
                for line in f:
                    if line.startswith('VmRSS:'):
                        return int(line.split()[1]) / 1024
        except Exception:
            return 0

# ── Server management ─────────────────────────────────────────────────────────

def start_server():
    import parent_server
    from parent_server import app
    app.config['TESTING'] = True
    def run():
        app.run(host='127.0.0.1', port=PORT, threaded=True, use_reloader=False)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)
    return app

# ── HTTP helpers (use Flask test client for speed, urllib for network traffic) ──

class InstrumentedClient:
    """Wraps Flask test client to count request/response bytes."""
    def __init__(self, client):
        self._c = client

    def _track(self, method, path, **kwargs):
        body = kwargs.get('json')
        req_bytes = len(json.dumps(body).encode()) if body else 0
        _net.record_request(req_bytes + len(path))
        fn = getattr(self._c, method)
        r = fn(path, **kwargs)
        resp_bytes = len(r.data) if r.data else 0
        _net.record_response(resp_bytes)
        return r

    def get(self, path, **kw):    return self._track('get', path, **kw)
    def post(self, path, **kw):   return self._track('post', path, **kw)
    def put(self, path, **kw):    return self._track('put', path, **kw)
    def delete(self, path, **kw): return self._track('delete', path, **kw)

def make_client():
    import parent_server
    from parent_server import app
    return InstrumentedClient(app.test_client())

def timed(fn):
    """Run fn(), return (result, elapsed_seconds)."""
    t0 = time.perf_counter()
    r = fn()
    return r, time.perf_counter() - t0

# ── Fixture generators ────────────────────────────────────────────────────────

DMX_PROFILES = [
    ('generic-moving-head-16bit', 16, 'Moving Head'),
    ('generic-rgb', 3, 'RGB Par'),
    ('generic-rgbw', 5, 'RGBW Wash'),
    ('generic-dimmer', 1, 'Dimmer'),
]

def generate_dmx_fixtures(count):
    """Generate DMX fixture definitions spread across 4 universes."""
    fixtures = []
    per_uni = count // 4
    remainder = count % 4
    addr = [1, 1, 1, 1]  # per-universe address counters
    for i in range(count):
        uni = min(i // per_uni, 3) if per_uni > 0 else 0
        if i >= per_uni * 4:
            uni = i % 4
        prof_idx = uni % len(DMX_PROFILES)
        prof_id, ch_count, label = DMX_PROFILES[prof_idx]
        fixtures.append({
            'name': f'{label} {i+1}',
            'type': 'point',
            'fixtureType': 'dmx',
            'dmxUniverse': uni + 1,
            'dmxStartAddr': addr[uni],
            'dmxChannelCount': ch_count,
            'dmxProfileId': prof_id,
            'aimPoint': [5000, 0, 5000],
        })
        addr[uni] += ch_count
    return fixtures

def generate_led_children(count):
    """Generate LED child + fixture pairs."""
    children = []
    for i in range(count):
        children.append({
            'ip': f'10.0.{i // 256}.{(i % 256) + 10}',
            'leds': 150,
            'mm': 5000,
            'name': f'LED String {i+1}',
        })
    return children

def place_fixtures(fixture_ids, canvas_w=10000, canvas_h=5000):
    """Generate layout positions in a grid."""
    positions = []
    cols = max(1, int(len(fixture_ids) ** 0.5) + 1)
    for i, fid in enumerate(fixture_ids):
        row = i // cols
        col = i % cols
        x = int((col + 0.5) / cols * canvas_w)
        y = int((row + 0.5) / (len(fixture_ids) // cols + 1) * canvas_h)
        positions.append({'id': fid, 'x': x, 'y': y, 'z': 0})
    return positions

# ── Tier definition ───────────────────────────────────────────────────────────

TIERS = [
    {'tier': 1, 'dmx':   8, 'led':  2},
    {'tier': 2, 'dmx':  24, 'led':  6},
    {'tier': 3, 'dmx':  60, 'led':  6},
    {'tier': 4, 'dmx':  88, 'led': 12},
    {'tier': 5, 'dmx': 120, 'led': 12},
]

# ── Thresholds (seconds) ─────────────────────────────────────────────────────

LIMITS = {
    'create':      {'target': 2.0, 'hard': 5.0},
    'layout_save': {'target': 1.0, 'hard': 3.0},
    'layout_load': {'target': 0.5, 'hard': 2.0},
    'fixtures_get':{'target': 0.5, 'hard': 2.0},
    'bake':        {'target': 5.0, 'hard': 15.0},
    'patch':       {'target': 0.2, 'hard': 1.0},
    'reset':       {'target': 2.0, 'hard': 5.0},
}

def check_limit(name, elapsed):
    lim = LIMITS.get(name, {})
    target = lim.get('target', 999)
    hard = lim.get('hard', 999)
    status = 'PASS' if elapsed <= target else 'WARN' if elapsed <= hard else 'FAIL'
    return status

# ── Run one tier ──────────────────────────────────────────────────────────────

def run_tier(c, tier_def, verbose=True):
    tier_num = tier_def['tier']
    n_dmx = tier_def['dmx']
    n_led = tier_def['led']
    total = n_dmx + n_led
    errors = 0

    if verbose:
        print(f'\n{"="*60}')
        print(f'  TIER {tier_num}: {total} fixtures ({n_dmx} DMX + {n_led} LED)')
        print(f'{"="*60}')

    # Reset state
    c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'})
    c.post('/api/settings', json={'name': f'Stress T{tier_num}', 'canvasW': 10000, 'canvasH': 5000})
    c.post('/api/stage', json={'w': 20.0, 'h': 10.0, 'd': 20.0})

    # Reset network capture
    reset_capture()
    mem_before = get_memory_mb()

    # ── Create fixtures ───────────────────────────────────────────
    t0 = time.perf_counter()
    fixture_ids = []

    # LED children + fixtures
    led_defs = generate_led_children(n_led)
    for ld in led_defs:
        r = c.post('/api/children', json={'ip': ld['ip']})
        cid = r.get_json().get('id')
        r = c.post('/api/fixtures', json={
            'name': ld['name'], 'type': 'linear', 'fixtureType': 'led', 'childId': cid,
            'strings': [{'leds': ld['leds'], 'mm': ld['mm'], 'sdir': 0}]
        })
        fid = r.get_json().get('id')
        if fid is not None:
            fixture_ids.append(fid)
        else:
            errors += 1

    # DMX fixtures
    dmx_defs = generate_dmx_fixtures(n_dmx)
    for df in dmx_defs:
        r = c.post('/api/fixtures', json=df)
        fid = r.get_json().get('id')
        if fid is not None:
            fixture_ids.append(fid)
        else:
            errors += 1

    create_s = time.perf_counter() - t0
    st = check_limit('create', create_s)
    if verbose:
        print(f'  Create {total} fixtures: {create_s:.3f}s [{st}]')

    # Verify count
    r = c.get('/api/fixtures')
    got = len(r.get_json() or [])
    if got != total:
        if verbose:
            print(f'  ERROR: expected {total} fixtures, got {got}')
        errors += 1

    # Check ID uniqueness
    id_set = set(fixture_ids)
    if len(id_set) != len(fixture_ids):
        if verbose:
            print(f'  ERROR: ID collision! {len(fixture_ids)} fixtures but {len(id_set)} unique IDs')
        errors += 1

    # ── GET /api/fixtures timing ──────────────────────────────────
    _, fixtures_get_s = timed(lambda: c.get('/api/fixtures'))
    st = check_limit('fixtures_get', fixtures_get_s)
    if verbose:
        print(f'  GET /api/fixtures: {fixtures_get_s:.3f}s [{st}]')

    # ── Layout save ───────────────────────────────────────────────
    positions = place_fixtures(fixture_ids)
    _, layout_save_s = timed(lambda: c.post('/api/layout', json={'children': positions}))
    st = check_limit('layout_save', layout_save_s)
    if verbose:
        print(f'  Save layout ({len(positions)} positions): {layout_save_s:.3f}s [{st}]')

    # ── Layout load ───────────────────────────────────────────────
    _, layout_load_s = timed(lambda: c.get('/api/layout'))
    st = check_limit('layout_load', layout_load_s)
    if verbose:
        print(f'  Load layout: {layout_load_s:.3f}s [{st}]')

    # Verify all positions persisted
    r = c.get('/api/layout')
    lay = r.get_json() or {}
    if len(lay.get('children', [])) != total:
        if verbose:
            print(f'  ERROR: layout has {len(lay.get("children",[]))} positions, expected {total}')
        errors += 1

    # ── Patch view ────────────────────────────────────────────────
    _, patch_s = timed(lambda: c.get('/api/dmx/patch'))
    st = check_limit('patch', patch_s)
    if verbose:
        print(f'  Patch view: {patch_s:.3f}s [{st}]')

    # Verify patch data — response is {universes: {1: [...], 2: [...]}, conflicts: [...]}
    r = c.get('/api/dmx/patch')
    patch = r.get_json() or {}
    patch_unis = patch.get('universes', {})
    dmx_in_patch = sum(len(v) for v in patch_unis.values())
    n_universes = len(patch_unis)
    n_conflicts = len(patch.get('conflicts', []))
    if verbose:
        print(f'  Patch: {dmx_in_patch} DMX fixtures across {n_universes} universes, {n_conflicts} conflicts')

    # ── Bake timeline ─────────────────────────────────────────────
    # Create a simple timeline with one action
    r = c.post('/api/actions', json={'name': 'Stress Solid', 'type': 1, 'r': 255, 'g': 100, 'b': 50})
    act_id = r.get_json().get('id')

    # Create spatial effect for the sweep
    r = c.post('/api/spatial-effects', json={
        'name': 'Stress Sweep', 'category': 'spatial-field', 'shape': 'sphere',
        'r': 255, 'g': 100, 'b': 50, 'size': {'radius': 3000},
        'motion': {'startPos': [0, 2500, 5000], 'endPos': [10000, 2500, 5000],
                   'durationS': 30, 'easing': 'linear'},
        'blend': 'add'
    })
    sfx_id = r.get_json().get('id')

    r = c.post('/api/timelines', json={'name': 'Stress TL', 'durationS': 30})
    tl_id = r.get_json().get('id')

    # Add a clip for each fixture (allPerformers approach — one clip, spatial effect handles distribution)
    clips = []
    for fid in fixture_ids[:min(len(fixture_ids), 50)]:  # Cap clips to avoid test timeout
        clips.append({
            'fixtureId': fid, 'actionId': act_id,
            'startS': 0, 'durationS': 30,
            'spatialEffectId': sfx_id
        })
    c.put(f'/api/timelines/{tl_id}', json={
        'name': 'Stress TL', 'durationS': 30, 'clips': clips
    })

    # Bake
    bake_s = 0.0
    bake_error = None
    r = c.post(f'/api/timelines/{tl_id}/bake')
    rd = r.get_json() or {}
    if r.status_code == 200 and rd.get('ok'):
        t0 = time.perf_counter()
        # Poll bake status — allow up to 60s for large bakes
        d = {}
        for _ in range(600):
            time.sleep(0.1)
            r = c.get(f'/api/timelines/{tl_id}/baked/status')
            d = r.get_json() or {}
            if d.get('done') or d.get('error'):
                break
        bake_s = time.perf_counter() - t0
        if d.get('error'):
            bake_error = d.get('error')
            if verbose:
                print(f'  Bake error: {bake_error}')
            errors += 1
    else:
        bake_s = 0
        bake_error = rd.get('err', f'HTTP {r.status_code}')
        if verbose:
            print(f'  Bake request failed: {bake_error}')
        errors += 1

    st = check_limit('bake', bake_s)
    if verbose:
        print(f'  Bake 30s timeline ({len(clips)} clips): {bake_s:.3f}s [{st}]')

    # Check bake result — fixtures is a dict keyed by fixture ID
    r = c.get(f'/api/timelines/{tl_id}/baked')
    baked = r.get_json() or {}
    if isinstance(baked.get('fixtures'), dict):
        baked_fixtures = len(baked['fixtures'])
    elif isinstance(baked.get('fixtures'), list):
        baked_fixtures = len(baked['fixtures'])
    else:
        baked_fixtures = 0
    bake_frames = baked.get('totalFrames', 0)
    bake_lsq = baked.get('lsqSize', 0)
    if verbose:
        print(f'  Baked: {baked_fixtures} fixtures, {bake_frames} frames, {bake_lsq} LSQ bytes')

    # ── Preview data ──────────────────────────────────────────────
    r = c.get(f'/api/timelines/{tl_id}/baked/preview')
    preview = r.get_json() or {}
    if isinstance(preview, dict) and 'err' not in preview:
        preview_keys = list(preview.keys())
    else:
        preview_keys = []
    if verbose:
        print(f'  Preview keys: {preview_keys[:5]}')

    # ── Factory reset timing ──────────────────────────────────────
    _, reset_s = timed(lambda: c.post('/api/reset', headers={'X-SlyLED-Confirm': 'true'}))
    st = check_limit('reset', reset_s)
    if verbose:
        print(f'  Factory reset: {reset_s:.3f}s [{st}]')

    # Verify reset cleaned up
    r = c.get('/api/fixtures')
    post_reset = len(r.get_json() or [])
    if post_reset != 0:
        if verbose:
            print(f'  ERROR: {post_reset} fixtures after reset (expected 0)')
        errors += 1

    # ── Collect metrics ───────────────────────────────────────────
    mem_after = get_memory_mb()
    net_sent, net_recv = stop_capture()

    tier_metrics = {
        'tier': tier_num,
        'total': total,
        'dmx': n_dmx,
        'led': n_led,
        'children': n_led,
        'create_s': round(create_s, 3),
        'layout_save_s': round(layout_save_s, 3),
        'layout_load_s': round(layout_load_s, 3),
        'fixtures_get_s': round(fixtures_get_s, 3),
        'bake_s': round(bake_s, 3),
        'patch_s': round(patch_s, 3),
        'reset_s': round(reset_s, 3),
        'mem_mb': round(mem_after, 1),
        'net_sent_kb': round(net_sent / 1024, 1),
        'net_recv_kb': round(net_recv / 1024, 1),
        'errors': errors,
        'baked_fixtures': baked_fixtures,
        'bake_frames': bake_frames,
        'bake_lsq_bytes': bake_lsq,
        'bake_error': bake_error,
        'clips': len(clips),
        'dmx_in_patch': dmx_in_patch,
        'universes': n_universes,
        'conflicts': n_conflicts,
    }
    metrics.add_tier(tier_metrics)

    # Threshold violations
    violations = []
    for name, elapsed in [('create', create_s), ('layout_save', layout_save_s),
                           ('layout_load', layout_load_s), ('fixtures_get', fixtures_get_s),
                           ('bake', bake_s), ('patch', patch_s), ('reset', reset_s)]:
        if check_limit(name, elapsed) == 'FAIL':
            violations.append(f'{name}={elapsed:.3f}s')

    if verbose:
        if violations:
            print(f'  THRESHOLD VIOLATIONS: {", ".join(violations)}')
        print(f'  Memory: {mem_after:.1f} MB | Network: {net_sent/1024:.1f} KB sent, {net_recv/1024:.1f} KB recv')
        print(f'  Errors: {errors}')

    return errors, violations


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    max_tier = 5
    output_json = '--json' in sys.argv
    for i, a in enumerate(sys.argv):
        if a == '--tier' and i + 1 < len(sys.argv):
            max_tier = int(sys.argv[i + 1])

    verbose = not output_json

    if verbose:
        print('=' * 60)
        print('  SlyLED Stress Test — Incremental Scaling')
        print('=' * 60)
        print(f'  Tiers: 1..{max_tier}')
        print(f'  Target: {TIERS[max_tier-1]["dmx"]} DMX + {TIERS[max_tier-1]["led"]} LED = {TIERS[max_tier-1]["dmx"]+TIERS[max_tier-1]["led"]} fixtures')

    if verbose:
        print('\nStarting server...')
    app = start_server()
    c = make_client()

    total_errors = 0
    all_violations = []

    for tier_def in TIERS[:max_tier]:
        errs, violations = run_tier(c, tier_def, verbose=verbose)
        total_errors += errs
        all_violations.extend([(tier_def['tier'], v) for v in violations])

    # ── Output ────────────────────────────────────────────────────
    if output_json:
        print(metrics.json())
    else:
        print(f'\n{"="*60}')
        print('  RESULTS')
        print(f'{"="*60}\n')
        print(metrics.markdown())
        print()

        if all_violations:
            print('THRESHOLD VIOLATIONS:')
            for tier, v in all_violations:
                print(f'  Tier {tier}: {v}')
            print()

        if total_errors == 0:
            print(f'\033[32mALL TIERS PASSED — 0 errors\033[0m')
        else:
            print(f'\033[31m{total_errors} ERRORS across {max_tier} tiers\033[0m')

    sys.exit(0 if total_errors == 0 else 1)


if __name__ == '__main__':
    main()
