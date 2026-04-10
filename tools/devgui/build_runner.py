"""
build_runner.py -- Version management and build invocation.

Parses version numbers from all tracked files, supports overriding
individual versions, and invokes build scripts.
"""

import os
import re
import json
import subprocess

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))

# Version file definitions: key -> (relative path, regex pattern, replacement template)
VERSION_FILES = {
    'parent_server': {
        'path': 'desktop/shared/parent_server.py',
        'pattern': r'^(VERSION\s*=\s*")[^"]+(")',
        'template': r'\g<1>{version}\2',
    },
    'installer': {
        'path': 'desktop/windows/installer.iss',
        'pattern': r'^(#define\s+AppVersion\s+")[^"]+(")',
        'template': r'\g<1>{version}\2',
    },
    'android': {
        'path': 'android/app/build.gradle.kts',
        'pattern': r'(versionName\s*=\s*")[^"]+(")',
        'template': r'\g<1>{version}\2',
    },
    'camera': {
        'path': 'firmware/orangepi/camera_server.py',
        'pattern': r'^(VERSION\s*=\s*")[^"]+(")',
        'template': r'\g<1>{version}\2',
    },
}


def _read_file(rel_path):
    """Read a project file, return contents or None."""
    full = os.path.join(PROJECT_ROOT, rel_path)
    try:
        with open(full, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except OSError:
        return None


def _extract_version(content, pattern):
    """Extract version string from file content using regex."""
    if not content:
        return None
    m = re.search(pattern, content, re.MULTILINE)
    if m:
        # The version is the part that gets replaced -- extract it
        full = m.group(0)
        # Find quoted version between the groups
        vm = re.search(r'"([^"]+)"', full)
        return vm.group(1) if vm else None
    return None


def get_all_versions():
    """Parse and return all tracked versions.

    Returns:
        dict: {key: {path, version, exists}} for all tracked files
              plus firmware registry entries
    """
    result = {}

    # Standard version files
    for key, info in VERSION_FILES.items():
        content = _read_file(info['path'])
        version = _extract_version(content, info['pattern']) if content else None
        result[key] = {
            'path': info['path'],
            'version': version,
            'exists': content is not None,
        }

    # Firmware registry entries
    registry_path = 'firmware/registry.json'
    content = _read_file(registry_path)
    if content:
        try:
            data = json.loads(content)
            for entry in data.get('firmware', []):
                fid = entry.get('id', '')
                key = f'firmware_{fid}'.replace('-', '_')
                result[key] = {
                    'path': registry_path,
                    'version': entry.get('version'),
                    'exists': True,
                    'firmwareId': fid,
                    'name': entry.get('name', fid),
                }
        except json.JSONDecodeError:
            pass

    return result


def set_version(file_key, new_version):
    """Update a version string in the specified file.

    Args:
        file_key: Key from VERSION_FILES or 'firmware_<id>' for registry
        new_version: New version string (e.g. '1.5.0')

    Returns:
        dict: {ok: bool, message: str}
    """
    # Validate version format
    if not re.match(r'^\d+\.\d+\.\d+$', new_version):
        return {'ok': False, 'message': f'Invalid version format: {new_version}'}

    # Firmware registry entries
    if file_key.startswith('firmware_'):
        firmware_id = file_key.replace('firmware_', '').replace('_', '-')
        return _set_registry_version(firmware_id, new_version)

    # Standard version files
    if file_key not in VERSION_FILES:
        return {'ok': False, 'message': f'Unknown file key: {file_key}'}

    info = VERSION_FILES[file_key]
    full_path = os.path.join(PROJECT_ROOT, info['path'])

    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except OSError as e:
        return {'ok': False, 'message': f'Cannot read {info["path"]}: {e}'}

    template = info['template'].replace('{version}', new_version)
    new_content, count = re.subn(info['pattern'], template, content, count=1, flags=re.MULTILINE)

    if count == 0:
        return {'ok': False, 'message': f'Pattern not found in {info["path"]}'}

    try:
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
    except OSError as e:
        return {'ok': False, 'message': f'Cannot write {info["path"]}: {e}'}

    return {'ok': True, 'message': f'Set {file_key} to {new_version}'}


def _set_registry_version(firmware_id, new_version):
    """Update a firmware version in registry.json."""
    registry_path = os.path.join(PROJECT_ROOT, 'firmware', 'registry.json')
    try:
        with open(registry_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return {'ok': False, 'message': f'Cannot read registry: {e}'}

    found = False
    for entry in data.get('firmware', []):
        if entry.get('id') == firmware_id:
            entry['version'] = new_version
            found = True
            break

    if not found:
        return {'ok': False, 'message': f'Firmware ID not found: {firmware_id}'}

    try:
        with open(registry_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
            f.write('\n')
    except OSError as e:
        return {'ok': False, 'message': f'Cannot write registry: {e}'}

    return {'ok': True, 'message': f'Set {firmware_id} to {new_version}'}


def run_build(build_type='quick'):
    """Start a build subprocess.

    Args:
        build_type: 'quick' (SPA copy), 'exe' (Windows build), 'full' (release)

    Returns:
        subprocess.Popen or None
    """
    if build_type == 'quick':
        # Quick SPA build: just report versions
        return subprocess.Popen(
            ['python', '-c', 'import sys; sys.path.insert(0,"tools/devgui"); '
             'from build_runner import get_all_versions; '
             'import json; v=get_all_versions(); '
             '[print(f"  {k}: {d[\\"version\\"]}") for k,d in v.items()]; '
             'print("Quick build complete.")'],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    elif build_type == 'exe':
        bat_path = os.path.join(PROJECT_ROOT, 'desktop', 'windows', 'build.bat')
        py_path = os.path.join(PROJECT_ROOT, 'build.py')
        if os.path.exists(py_path):
            return subprocess.Popen(
                ['python', py_path],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        elif os.path.exists(bat_path):
            return subprocess.Popen(
                ['cmd', '/c', bat_path],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        else:
            return None
    elif build_type == 'full':
        ps_path = os.path.join(PROJECT_ROOT, 'build_release.ps1')
        if os.path.exists(ps_path):
            return subprocess.Popen(
                ['powershell.exe', '-ExecutionPolicy', 'Bypass', '-File', ps_path],
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        else:
            return None
    return None


if __name__ == '__main__':
    versions = get_all_versions()
    print('All versions:')
    for key, info in sorted(versions.items()):
        status = info['version'] if info['version'] else 'NOT FOUND'
        print(f'  {key:30s} {status:12s}  ({info["path"]})')
