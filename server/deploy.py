#!/usr/bin/env python3
"""
deploy.py — Deploy files to electricRV.ca via cPanel API.

Usage:
    python server/deploy.py upload server/api/profiles/index.php api/profiles/index.php
    python server/deploy.py list [path]
    python server/deploy.py test
    python server/deploy.py deploy   # deploy all server files
"""

import sys, os, json, urllib.request, urllib.parse, ssl

# Load .env
ENV = {}
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                ENV[k.strip()] = v.strip()

HOST = ENV.get('CPANEL_HOST', 'electricrv.ca')
PORT = ENV.get('CPANEL_PORT', '2083')
USER = ENV.get('CPANEL_USER', '')
TOKEN = ENV.get('CPANEL_TOKEN', '')
WEB_ROOT = ENV.get('WEB_ROOT', f'/home/{USER}/public_html')

BASE_URL = f"https://{HOST}:{PORT}/execute"
AUTH_HEADER = f"cpanel {USER}:{TOKEN}"

# Disable SSL verification for self-signed cPanel certs
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE


def api_call(module, function, params=None):
    """Call a cPanel UAPI endpoint."""
    url = f"{BASE_URL}/{module}/{function}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": AUTH_HEADER})
    resp = urllib.request.urlopen(req, timeout=15, context=CTX)
    return json.loads(resp.read().decode())


def upload_file(local_path, remote_dir):
    """Upload a file to a directory on the server."""
    import mimetypes
    filename = os.path.basename(local_path)
    boundary = '----SlyLEDDeploy'

    with open(local_path, 'rb') as f:
        file_data = f.read()

    body = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="dir"\r\n\r\n'
        f'{remote_dir}\r\n'
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
        f'1\r\n'
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="file-1"; filename="{filename}"\r\n'
        f'Content-Type: application/octet-stream\r\n\r\n'
    ).encode() + file_data + f'\r\n--{boundary}--\r\n'.encode()

    url = f"{BASE_URL}/Fileman/upload_files"
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={"Authorization": AUTH_HEADER,
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    resp = urllib.request.urlopen(req, timeout=30, context=CTX)
    result = json.loads(resp.read().decode())
    return result.get('status') == 1


def list_files(path="/public_html"):
    """List files in a directory."""
    r = api_call("Fileman", "list_files", {"dir": path, "types": "file|dir"})
    return r.get('data', [])


def ensure_dir(path):
    """Create directory via uploading a placeholder then removing it."""
    # cPanel Fileman doesn't have mkdir — use the file manager create approach
    # Actually, we can upload files to nested paths and cPanel creates dirs
    pass


def deploy_all():
    """Deploy all server files."""
    server_dir = os.path.dirname(__file__)
    files = [
        ('api/profiles/.htaccess', '/public_html/api/profiles'),
        ('api/profiles/config.php', '/public_html/api/profiles'),
        ('api/profiles/index.php', '/public_html/api/profiles'),
    ]

    for local_rel, remote_dir in files:
        local_path = os.path.join(server_dir, local_rel)
        if not os.path.exists(local_path):
            print(f'  SKIP {local_rel} (not found)')
            continue
        ok = upload_file(local_path, remote_dir)
        print(f'  {"OK" if ok else "FAIL"} {local_rel} -> {remote_dir}/')


def deploy_website():
    """Deploy the SlyLED website (slyled/ directory)."""
    server_dir = os.path.dirname(__file__)
    slyled_dir = os.path.join(server_dir, 'slyled')

    # Walk the slyled directory and upload all files
    for root, dirs, filenames in os.walk(slyled_dir):
        for fn in filenames:
            local_path = os.path.join(root, fn)
            # Compute remote directory relative to slyled/
            rel = os.path.relpath(root, slyled_dir).replace('\\', '/')
            if rel == '.':
                remote_dir = '/public_html/slyled'
            else:
                remote_dir = f'/public_html/slyled/{rel}'
            ok = upload_file(local_path, remote_dir)
            rel_file = os.path.relpath(local_path, slyled_dir).replace('\\', '/')
            print(f'  {"OK" if ok else "FAIL"} {rel_file} -> {remote_dir}/')


def test_connection():
    """Test the cPanel API connection."""
    try:
        files = list_files("/public_html")
        print(f'Connected to {HOST} as {USER}')
        print(f'Web root: {WEB_ROOT}')
        print(f'Files in public_html: {len(files)}')
        # Test PHP
        resp = urllib.request.urlopen(f"https://{HOST}/slyled_test.php", timeout=10, context=CTX)
        data = json.loads(resp.read().decode())
        print(f'PHP version: {data.get("php")}')
        print('Connection OK')
    except Exception as e:
        print(f'Connection failed: {e}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: deploy.py [test|list|deploy|upload <local> <remote_dir>]')
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == 'test':
        test_connection()
    elif cmd == 'list':
        path = sys.argv[2] if len(sys.argv) > 2 else '/public_html'
        for f in list_files(path):
            print(f'  {f.get("type","?"):4s} {f.get("file","")}  ({f.get("humansize","")})')
    elif cmd == 'upload':
        if len(sys.argv) < 4:
            print('Usage: deploy.py upload <local_file> <remote_dir>')
            sys.exit(1)
        ok = upload_file(sys.argv[2], sys.argv[3])
        print('Upload OK' if ok else 'Upload FAILED')
    elif cmd == 'deploy':
        print('Deploying server files...')
        deploy_all()
    elif cmd == 'website':
        print('Deploying SlyLED website...')
        deploy_website()
    else:
        print(f'Unknown command: {cmd}')
