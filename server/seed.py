#!/usr/bin/env python3
"""Seed the community server with built-in profiles."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'desktop', 'shared'))

import community_client as cc
from dmx_profiles import ProfileLibrary

lib = ProfileLibrary()
profiles = lib.list_profiles()
print(f"Local profiles: {len(profiles)}")

uploaded = skipped = failed = 0
for p in profiles:
    full = lib.get_profile(p["id"])
    if not full:
        continue
    clean = {k: v for k, v in full.items() if k != "builtin"}
    r = cc.upload(clean)
    if r.get("ok"):
        uploaded += 1
        print(f"  + {p['id']}")
    elif "already exists" in str(r.get("error", "")) or "Duplicate" in str(r.get("error", "")):
        skipped += 1
        print(f"  = {p['id']} (exists)")
    else:
        failed += 1
        print(f"  ! {p['id']}: {r.get('error')}")

print(f"\nDone: {uploaded} uploaded, {skipped} skipped, {failed} failed")
s = cc.stats()
print(f"Community: {s.get('data', {}).get('total', '?')} profiles")
