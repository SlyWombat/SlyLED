#!/usr/bin/env python3
"""qa_brightness_watch.py — watch globalBrightness change at 100 ms cadence.

Emits one line each time the value changes. Designed for QA-lead session
where the operator drives Android Auto Brightness and we want to see the
POST /api/brightness stream land server-side.
"""
import json, time, urllib.request, urllib.error
from datetime import datetime

URL = 'http://localhost:8080/api/settings'

def now():
    n = datetime.now()
    return f"{n.strftime('%H:%M:%S')}.{n.microsecond // 1000:03d}"

def fetch():
    try:
        with urllib.request.urlopen(URL, timeout=1.0) as r:
            return json.loads(r.read())
    except Exception:
        return None

prev = None
print(f'{now()}  === qa_brightness_watch started ===', flush=True)
while True:
    s = fetch()
    if s is not None:
        v = s.get('globalBrightness')
        if v != prev:
            print(f'{now()}  globalBrightness = {v}', flush=True)
            prev = v
    time.sleep(0.1)
