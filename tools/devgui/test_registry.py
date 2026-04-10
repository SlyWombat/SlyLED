"""
test_registry.py -- Auto-discover test files, parse assertion counts, classify.

Returns structured metadata for every test file found in tests/ and
tests/regression/.
"""

import os
import re

# Project root (two levels up from this file)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# IP pattern for hardware tests
_IP_RE = re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b')

# Assertion patterns used across the codebase
_ASSERT_PATTERNS = [
    re.compile(r'\bok\('),         # ok('name', cond) — parent/camera/spa style
    re.compile(r'\bcheck\('),      # check('name', cond) — regression style
    re.compile(r'\bself\.assert'), # unittest style (if any)
    re.compile(r'\bassert\b'),     # bare assert
]


def _count_assertions(filepath):
    """Count assertion calls in a Python test file."""
    total = 0
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                stripped = line.lstrip()
                # Skip comments
                if stripped.startswith('#'):
                    continue
                for pat in _ASSERT_PATTERNS:
                    total += len(pat.findall(line))
    except OSError:
        pass
    return total


def _detect_imports(filepath):
    """Scan file for notable imports and patterns."""
    imports = {
        'playwright': False, 'cv2': False, 'requests': False,
        'subprocess': False, 'test_client': False,
    }
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                stripped = line.strip()
                if 'playwright' in stripped and ('import' in stripped or 'from' in stripped):
                    imports['playwright'] = True
                if 'cv2' in stripped and ('import' in stripped or 'from' in stripped):
                    imports['cv2'] = True
                if 'import requests' in stripped or 'from requests' in stripped:
                    imports['requests'] = True
                if 'import subprocess' in stripped or 'from subprocess' in stripped:
                    imports['subprocess'] = True
                # In-process Flask test client (unit test, not hardware)
                if 'test_client' in stripped:
                    imports['test_client'] = True
    except OSError:
        pass
    return imports


def _has_hardware_ips(filepath):
    """Check if file references specific IP addresses (hardware tests)."""
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if _IP_RE.search(line) and not line.strip().startswith('#'):
                    return True
    except OSError:
        pass
    return False


def _classify(filepath, rel_path, imports, has_ips):
    """Classify a test into a category."""
    basename = os.path.basename(filepath)

    # Regression folder
    if 'regression' in rel_path:
        return 'regression'

    # Visual / Playwright tests
    if imports['playwright']:
        return 'visual'

    # Tests using Flask test_client are unit tests even if they reference
    # IP addresses in test data (e.g. test_parent.py, test_profiles.py)
    if imports['test_client']:
        return 'quick'

    # Hardware tests reference IPs or require live devices
    if has_ips:
        return 'hardware'

    # Everything else is a quick unit test
    return 'quick'


def discover_tests():
    """Scan for test files and return metadata list.

    Returns:
        list of dict: [{file, name, category, assertionCount,
                        requiresPlaywright, requiresOpenCV}, ...]
    """
    tests_dir = os.path.join(PROJECT_ROOT, 'tests')
    results = []

    scan_dirs = [
        (tests_dir, 'tests'),
        (os.path.join(tests_dir, 'regression'), 'tests/regression'),
    ]

    for dir_path, rel_prefix in scan_dirs:
        if not os.path.isdir(dir_path):
            continue
        for fname in sorted(os.listdir(dir_path)):
            if not fname.startswith('test_') or not fname.endswith('.py'):
                continue
            full_path = os.path.join(dir_path, fname)
            rel_path = f'{rel_prefix}/{fname}'

            imports = _detect_imports(full_path)
            has_ips = _has_hardware_ips(full_path)
            assertion_count = _count_assertions(full_path)
            category = _classify(full_path, rel_path, imports, has_ips)

            # Human-readable name from filename
            name = fname.replace('test_', '').replace('.py', '').replace('_', ' ').title()

            results.append({
                'file': rel_path,
                'name': name,
                'category': category,
                'assertionCount': assertion_count,
                'requiresPlaywright': imports['playwright'],
                'requiresOpenCV': imports['cv2'],
            })

    return results


if __name__ == '__main__':
    import json
    tests = discover_tests()
    print(json.dumps(tests, indent=2))
    totals = {}
    for t in tests:
        cat = t['category']
        totals.setdefault(cat, {'count': 0, 'assertions': 0})
        totals[cat]['count'] += 1
        totals[cat]['assertions'] += t['assertionCount']
    print(f'\nSummary: {len(tests)} test files')
    for cat, info in sorted(totals.items()):
        print(f'  {cat}: {info["count"]} files, {info["assertions"]} assertions')
