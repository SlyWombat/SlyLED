#!/usr/bin/env python3
"""
SlyLED Developer Management GUI -- Flask app on port 9090.

Standalone dev tool for running tests, managing builds, monitoring versions
and git status. Completely separate from the main SlyLED app (port 8080).

Usage:
    python tools/devgui/server.py
    python tools/devgui/server.py --port 9091
"""

import os
import sys
import json
import time
import signal
import threading
import subprocess
import argparse
from datetime import datetime

# Ensure project root is importable
DEVGUI_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(DEVGUI_DIR, '..', '..'))
sys.path.insert(0, DEVGUI_DIR)

from flask import Flask, request, jsonify, Response, send_from_directory
from test_registry import discover_tests
from build_runner import get_all_versions, set_version, run_build

app = Flask(__name__, static_folder=None)

# ── State ───────────────────────────────────────────────────────────────

_test_process = None          # subprocess.Popen or None
_test_lock = threading.Lock()
_test_output_lines = []       # list of output lines from last/current run
_test_running = False
_test_suite_name = ''         # name of currently running suite/file
_last_results = {}            # suite -> {passed, failed, total, timestamp, output}

_build_process = None
_build_lock = threading.Lock()
_build_output_lines = []
_build_running = False

# SSE subscribers
_sse_subscribers = []         # list of queue objects
_sse_lock = threading.Lock()

GH_CLI = None  # resolved path to gh CLI


def _find_gh_cli():
    """Find the gh CLI executable."""
    global GH_CLI
    # Try WSL path to Windows gh.exe first
    win_gh = '/mnt/c/Program Files/GitHub CLI/gh.exe'
    if os.path.exists(win_gh):
        GH_CLI = win_gh
        return
    # Try PATH
    import shutil
    gh = shutil.which('gh') or shutil.which('gh.exe')
    if gh:
        GH_CLI = gh


_find_gh_cli()


# ── SSE Helpers ─────────────────────────────────────────────────────────

def _broadcast_sse(event_type, data):
    """Send an SSE event to all subscribers."""
    msg = f'event: {event_type}\ndata: {json.dumps(data)}\n\n'
    with _sse_lock:
        dead = []
        for q in _sse_subscribers:
            try:
                q.append(msg)
            except Exception:
                dead.append(q)
        for q in dead:
            _sse_subscribers.remove(q)


# ── Routes: SPA ─────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(os.path.join(DEVGUI_DIR, 'spa'), 'index.html')


# ── Routes: Tests ───────────────────────────────────────────────────────

@app.route('/api/tests')
def api_tests():
    tests = discover_tests()
    return jsonify(tests)


@app.route('/api/tests/run', methods=['POST'])
def api_tests_run():
    global _test_process, _test_running, _test_output_lines, _test_suite_name

    with _test_lock:
        if _test_running:
            return jsonify({'ok': False, 'message': 'Tests already running'}), 409

    body = request.get_json(silent=True) or {}
    suite = body.get('suite', '')
    file_path = body.get('file', '')

    # Build command
    if file_path:
        # Run a single test file
        test_file = os.path.join(PROJECT_ROOT, file_path)
        if not os.path.exists(test_file):
            return jsonify({'ok': False, 'message': f'File not found: {file_path}'}), 404
        cmd = [sys.executable, '-X', 'utf8', test_file]
        _test_suite_name = os.path.basename(file_path)
    elif suite == 'quick':
        # Run all quick (unit) tests
        tests = discover_tests()
        quick = [t for t in tests if t['category'] == 'quick']
        if not quick:
            return jsonify({'ok': False, 'message': 'No quick tests found'}), 404
        # Run them sequentially via a wrapper
        cmd = _build_multi_test_cmd([t['file'] for t in quick])
        _test_suite_name = 'Quick Tests'
    elif suite == 'visual':
        tests = discover_tests()
        visual = [t for t in tests if t['category'] == 'visual']
        if not visual:
            return jsonify({'ok': False, 'message': 'No visual tests found'}), 404
        cmd = _build_multi_test_cmd([t['file'] for t in visual])
        _test_suite_name = 'Visual Tests'
    elif suite == 'regression':
        run_all = os.path.join(PROJECT_ROOT, 'tests', 'regression', 'run_all.py')
        if os.path.exists(run_all):
            cmd = [sys.executable, '-X', 'utf8', run_all]
        else:
            tests = discover_tests()
            reg = [t for t in tests if t['category'] == 'regression']
            cmd = _build_multi_test_cmd([t['file'] for t in reg])
        _test_suite_name = 'Regression Tests'
    elif suite == 'all':
        tests = discover_tests()
        all_files = [t['file'] for t in tests if t['category'] in ('quick', 'visual', 'regression')]
        cmd = _build_multi_test_cmd(all_files)
        _test_suite_name = 'All Tests'
    else:
        return jsonify({'ok': False, 'message': 'Specify suite or file'}), 400

    # Start subprocess
    with _test_lock:
        _test_output_lines = []
        _test_running = True

    _broadcast_sse('test_start', {'suite': _test_suite_name})

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env={**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'},
        )
    except Exception as e:
        with _test_lock:
            _test_running = False
        return jsonify({'ok': False, 'message': str(e)}), 500

    _test_process = proc

    # Start reader thread
    t = threading.Thread(target=_read_test_output, args=(proc,), daemon=True)
    t.start()

    return jsonify({'ok': True, 'suite': _test_suite_name, 'pid': proc.pid})


def _build_multi_test_cmd(file_list):
    """Build a command to run multiple test files sequentially."""
    # Create inline script that runs each file
    files_json = json.dumps(file_list)
    script = (
        f'import subprocess, sys, os; '
        f'files = {files_json}; '
        f'total_pass = total_fail = 0; '
        f'[print(f"\\n=== {{f}} ===") or '
        f'subprocess.run([sys.executable, "-X", "utf8", f], '
        f'env={{**os.environ, "PYTHONIOENCODING": "utf-8"}}) '
        f'for f in files]; '
        f'print("\\nAll suites complete.")'
    )
    return [sys.executable, '-c', script]


def _read_test_output(proc):
    """Read stdout from test subprocess, line by line."""
    global _test_running
    passed = 0
    failed = 0

    try:
        for line in proc.stdout:
            line = line.rstrip('\n')
            with _test_lock:
                _test_output_lines.append(line)

            # Detect pass/fail
            if '[PASS]' in line:
                passed += 1
            elif '[FAIL]' in line:
                failed += 1

            _broadcast_sse('test_line', {
                'line': line,
                'passed': passed,
                'failed': failed,
            })
    except Exception:
        pass
    finally:
        proc.wait()
        with _test_lock:
            _test_running = False

        _last_results[_test_suite_name] = {
            'passed': passed,
            'failed': failed,
            'total': passed + failed,
            'exitCode': proc.returncode,
            'timestamp': datetime.now().isoformat(),
            'lineCount': len(_test_output_lines),
        }

        _broadcast_sse('test_done', {
            'suite': _test_suite_name,
            'passed': passed,
            'failed': failed,
            'exitCode': proc.returncode,
        })


@app.route('/api/tests/stream')
def api_tests_stream():
    """SSE endpoint for live test/build output."""
    queue = []
    with _sse_lock:
        _sse_subscribers.append(queue)

    def generate():
        # Send any buffered output first
        with _test_lock:
            for line in _test_output_lines:
                yield f'event: test_line\ndata: {json.dumps({"line": line})}\n\n'

        while True:
            if queue:
                msg = queue.pop(0)
                yield msg
            else:
                # Keep-alive
                yield ': keepalive\n\n'
                time.sleep(0.5)

    resp = Response(generate(), mimetype='text/event-stream')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['X-Accel-Buffering'] = 'no'
    return resp


@app.route('/api/tests/cancel', methods=['POST'])
def api_tests_cancel():
    global _test_process, _test_running
    with _test_lock:
        if not _test_running or _test_process is None:
            return jsonify({'ok': False, 'message': 'No test running'})
        try:
            _test_process.kill()
        except Exception:
            pass
        _test_running = False
    _broadcast_sse('test_done', {'suite': _test_suite_name, 'cancelled': True})
    return jsonify({'ok': True, 'message': 'Cancelled'})


@app.route('/api/tests/results')
def api_tests_results():
    return jsonify({
        'results': _last_results,
        'running': _test_running,
        'currentSuite': _test_suite_name if _test_running else None,
    })


# ── Routes: Versions ───────────────────────────────────────────────────

@app.route('/api/versions')
def api_versions():
    return jsonify(get_all_versions())


@app.route('/api/versions/override', methods=['POST'])
def api_versions_override():
    body = request.get_json(silent=True) or {}
    file_key = body.get('file', '')
    version = body.get('version', '')
    if not file_key or not version:
        return jsonify({'ok': False, 'message': 'file and version required'}), 400
    result = set_version(file_key, version)
    status = 200 if result['ok'] else 400
    return jsonify(result), status


# ── Routes: Builds ──────────────────────────────────────────────────────

@app.route('/api/builds/run', methods=['POST'])
def api_builds_run():
    global _build_process, _build_running, _build_output_lines

    with _build_lock:
        if _build_running:
            return jsonify({'ok': False, 'message': 'Build already running'}), 409

    body = request.get_json(silent=True) or {}
    build_type = body.get('type', 'quick')

    proc = run_build(build_type)
    if proc is None:
        return jsonify({'ok': False, 'message': f'No build script for type: {build_type}'}), 404

    with _build_lock:
        _build_output_lines = []
        _build_running = True
        _build_process = proc

    _broadcast_sse('build_start', {'type': build_type})

    t = threading.Thread(target=_read_build_output, args=(proc, build_type), daemon=True)
    t.start()

    return jsonify({'ok': True, 'type': build_type, 'pid': proc.pid})


def _read_build_output(proc, build_type):
    """Read stdout from build subprocess."""
    global _build_running
    try:
        for line in proc.stdout:
            line = line.rstrip('\n')
            with _build_lock:
                _build_output_lines.append(line)
            _broadcast_sse('build_line', {'line': line})
    except Exception:
        pass
    finally:
        proc.wait()
        with _build_lock:
            _build_running = False
        _broadcast_sse('build_done', {
            'type': build_type,
            'exitCode': proc.returncode,
        })


@app.route('/api/builds/cancel', methods=['POST'])
def api_builds_cancel():
    global _build_process, _build_running
    with _build_lock:
        if not _build_running or _build_process is None:
            return jsonify({'ok': False, 'message': 'No build running'})
        try:
            _build_process.kill()
        except Exception:
            pass
        _build_running = False
    _broadcast_sse('build_done', {'cancelled': True})
    return jsonify({'ok': True, 'message': 'Cancelled'})


# ── Routes: Git ─────────────────────────────────────────────────────────

@app.route('/api/git')
def api_git():
    result = {}

    # Branch
    try:
        r = subprocess.run(
            ['git', 'branch', '--show-current'],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        )
        result['branch'] = r.stdout.strip()
    except Exception:
        result['branch'] = 'unknown'

    # Status (clean/dirty)
    try:
        r = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        )
        lines = [l for l in r.stdout.strip().split('\n') if l.strip()]
        result['dirty'] = len(lines) > 0
        result['changedFiles'] = len(lines)
        # Summarize changes
        status_lines = []
        for l in lines[:20]:
            status_lines.append(l.strip())
        result['status'] = status_lines
    except Exception:
        result['dirty'] = None
        result['changedFiles'] = 0
        result['status'] = []

    # Recent commits
    try:
        r = subprocess.run(
            ['git', 'log', '--oneline', '-10'],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=10,
        )
        commits = []
        for line in r.stdout.strip().split('\n'):
            if line.strip():
                parts = line.strip().split(' ', 1)
                commits.append({
                    'hash': parts[0] if parts else '',
                    'message': parts[1] if len(parts) > 1 else '',
                })
        result['commits'] = commits
    except Exception:
        result['commits'] = []

    return jsonify(result)


# ── Routes: Issues ──────────────────────────────────────────────────────

@app.route('/api/issues')
def api_issues():
    if not GH_CLI:
        return jsonify({'count': None, 'error': 'gh CLI not found'})
    try:
        r = subprocess.run(
            [GH_CLI, 'issue', 'list', '--state', 'open', '--json', 'number'],
            cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            issues = json.loads(r.stdout)
            return jsonify({'count': len(issues)})
        else:
            return jsonify({'count': None, 'error': r.stderr.strip()})
    except Exception as e:
        return jsonify({'count': None, 'error': str(e)})


# ── Main ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SlyLED Developer Management GUI')
    parser.add_argument('--port', type=int, default=9090, help='Port (default 9090)')
    parser.add_argument('--host', default='127.0.0.1', help='Host (default 127.0.0.1)')
    args = parser.parse_args()

    print(f'SlyLED DevGUI starting on http://{args.host}:{args.port}')
    print(f'Project root: {PROJECT_ROOT}')
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == '__main__':
    main()
