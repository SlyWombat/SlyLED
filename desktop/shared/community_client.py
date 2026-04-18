"""
Community Profile Server client.
Handles upload/download/search against electricRV.ca community API.
"""

import json
import hashlib
import urllib.request
import urllib.parse
import logging

log = logging.getLogger("slyled")

COMMUNITY_URL = "https://electricrv.ca/api/profiles/index.php"
TIMEOUT = 10


def _api(action, params=None, body=None):
    """Call the community API."""
    url = f"{COMMUNITY_URL}?action={action}"
    if params:
        url += "&" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    headers["User-Agent"] = "SlyLED-Parent"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"ok": False, "error": f"HTTP {e.code}: {body_text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def search(query="", category=None, limit=50, offset=0):
    params = {"q": query, "limit": limit, "offset": offset}
    if category:
        params["category"] = category
    return _api("search", params)


def get_profile(slug):
    return _api("get", {"slug": slug})


def upload(profile):
    return _api("upload", body={"profile": profile})


def update(profile):
    """Overwrite an existing community profile (same slug). The server
    enforces `uploader_ip` matching — if the IP has changed since the
    original upload, the row must be deleted via cPanel phpMyAdmin
    first, then re-uploaded fresh."""
    return _api("update", body={"profile": profile})


def check_duplicate(profile):
    return _api("check", body={"profile": profile})


def recent(limit=20):
    return _api("recent", {"limit": limit})


def popular(limit=20):
    return _api("popular", {"limit": limit})


def stats():
    return _api("stats")


def since(timestamp, limit=100):
    return _api("since", {"ts": timestamp, "limit": limit})


def compute_channel_hash(profile):
    """Compute SHA-1 channel fingerprint matching the server's algorithm."""
    channels = sorted(profile.get("channels", []), key=lambda c: c.get("offset", 0))
    parts = []
    for ch in channels:
        cap_types = sorted(set(cap.get("type", "Generic") for cap in ch.get("capabilities", [])))
        parts.append(f"{ch.get('offset', 0)}:{ch.get('type', 'dimmer')}:{ch.get('bits', 8)}:{','.join(cap_types)}")
    return hashlib.sha1("|".join(parts).encode()).hexdigest()
