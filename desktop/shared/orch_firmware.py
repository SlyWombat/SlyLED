"""orch_firmware — Firmware management + OTA firmware update blueprint (B1).

Mechanically extracted from parent_server.py ("Firmware management" and
"OTA firmware update" sections). Route paths, names, and behaviour are
byte-identical; only the decorator target changed (@app → @bp) and
parent_server-owned names are reached through the orch_state bridge (ps.*).

Stays in parent_server (reached via ps at call time, so test monkeypatching
of parent_server attributes keeps working — see tests/test_870_ota_app_only.py):
  _FW_DIR, _FW_CACHE_DIR, _wifi, _decrypt_pw, _children, log, and the
  _resolve_registry *alias* (the function is defined here, re-exported there,
  and always called through ps so a patched parent_server._resolve_registry
  wins).
"""

import json
import os
import re
import socket
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file

import orch_state

ps = orch_state.ps  # the live parent_server module (bound before this import)
assert ps is not None, "orch_state.bind() must run before importing orch_firmware"

bp = Blueprint("firmware", __name__)

try:
    from firmware_manager import list_ports, load_registry, flash_board, get_flash_status, detect_chip, query_serial
    _fw_available = True
except ImportError:
    _fw_available = False

def _parent_wifi_hash():
    """Compute the same djb2 hash as the firmware for SSID+password comparison."""
    ssid = ps._wifi.get("ssid", "")
    pw = ps._decrypt_pw(ps._wifi.get("password", ""))
    h = 5381
    for c in ssid:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    for c in pw:
        h = (h * 33 + ord(c)) & 0xFFFFFFFF
    return format(h, 'X')

@bp.get("/api/firmware/ports")
def api_fw_ports():
    """Fast port list - no serial queries. Use /api/firmware/query for per-port info."""
    if not _fw_available:
        return jsonify(ok=False, err="pyserial not installed"), 500
    return jsonify(list_ports())

@bp.post("/api/firmware/query")
def api_fw_query_port():
    """Query a single port via serial for version + wifi hash. Slow (~2s)."""
    if not _fw_available:
        return jsonify(ok=False, err="pyserial not available"), 500
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    if not port:
        return jsonify(ok=False, err="port required"), 400
    info = query_serial(port, timeout=2.0)
    if not info:
        return jsonify(ok=True, fwVersion=None, fwBoard=None, wifiMatch=None)
    parent_hash = _parent_wifi_hash()
    bmap = {"esp32": "esp32", "d1mini": "d1mini", "giga-child": "giga", "giga-parent": "giga"}
    return jsonify(ok=True,
                   fwVersion=info.get("version"),
                   fwBoard=info.get("board"),
                   board=bmap.get(info.get("board", ""), None),
                   wifiHash=info.get("wifiHash"),
                   wifiMatch=(info.get("wifiHash") == parent_hash) if info.get("wifiHash") else None)

def _resolve_registry():
    """#832 — single source of truth for "which `firmware/registry.json`
    do I read?". Returns the parsed dict using a fixed precedence:

      1. APPDATA override (`ps._FW_CACHE_DIR/registry.json`) — what
         `build_release.ps1` writes after a local firmware bump, so a
         freshly-built version is visible without committing to git.
      2. GitHub `main` branch (`_fetch_remote_registry`) — covers the
         "installed exe is N releases behind" case from #807.
      3. PyInstaller-bundled `ps._FW_DIR/registry.json` — install-time
         fallback, also handled by `firmware_manager.load_registry`.

    All four firmware endpoints (`api_fw_library`, `api_fw_fetch`,
    `api_fw_refresh_all`, `api_fw_flash`) MUST go through this helper.
    Pre-fix the same precedence block was copy-pasted into three of
    them and forgotten in `api_fw_flash`, so USB flash silently used
    GitHub-main's stale sha256 even when the APPDATA override pointed
    at a freshly-built v1.2.7 binary. Centralising the precedence
    eliminates that class of omission entirely (#832 pass-3).
    """
    override = ps._FW_CACHE_DIR / "registry.json"
    if override.exists():
        try:
            data = json.loads(override.read_text(encoding="utf-8-sig"))
            if isinstance(data.get("firmware"), list):
                return data
        except Exception:
            pass
    remote = _fetch_remote_registry()
    if remote and isinstance(remote.get("firmware"), list):
        return remote
    return load_registry(ps._FW_DIR, ps._FW_CACHE_DIR)


def _resolve_registry_entry(fw_id):
    """Convenience wrapper — return the single `firmware[]` entry whose
    `id` matches `fw_id`, or None. Same precedence as `ps._resolve_registry`."""
    reg = ps._resolve_registry()
    return next((e for e in reg.get("firmware", []) if e.get("id") == fw_id), None)


@bp.get("/api/firmware/registry")
def api_fw_registry():
    """Return the registry, with `diagnostic: true` entries filtered
    out so developer-only builds (e.g. `gyro-test-esp32s3`) never
    surface in operator-facing OTA UI. Diagnostic builds stay in the
    on-disk registry.json for the build pipeline; this endpoint just
    hides them from the SPA + Android."""
    reg = ps._resolve_registry()
    public = [e for e in reg.get("firmware", []) if not e.get("diagnostic")]
    return jsonify({**reg, "firmware": public})


@bp.post("/api/firmware/registry/refresh")
def api_fw_registry_refresh():
    """Force-refresh the firmware registry from GitHub-main and persist
    it as the APPDATA override.

    The default `ps._resolve_registry` precedence is APPDATA > GitHub >
    bundle. That's correct for the dev workflow (build_release.ps1
    writes APPDATA at version-bump time so the next orchestrator
    process sees the new pin without a re-install) but it means a stale
    APPDATA copy permanently shadows newer GitHub-main publishes —
    operators see "v1.2.7 available" forever even after gyro-v1.2.8
    ships, regardless of how many times they reinstall (the bundle
    inside the new installer never wins against the older APPDATA
    file). Per the operator directive, "Reload List" must fetch the
    fresh registry from GitHub.

    This endpoint:
      1. Busts the in-memory remote-registry TTL cache.
      2. Force-fetches `firmware/registry.json` from GitHub `main`.
      3. Writes the result to `ps._FW_CACHE_DIR/registry.json` (APPDATA
         override) so it survives the next orchestrator restart and
         flips the precedence chain back into a healthy state.
      4. Returns the refreshed registry (same shape as `/api/firmware/
         registry`) so the SPA can re-render without a second round-
         trip.
    """
    # 1 — bust the in-memory TTL cache so step 2 hits GitHub fresh
    # rather than handing back whatever we read 4 minutes ago.
    cache = _remote_registry_cache
    cache["data"] = None
    cache["ts"] = 0
    cache["ok"] = False

    # 2 — force fetch.
    fresh = _fetch_remote_registry()
    if fresh is None or not isinstance(fresh.get("firmware"), list):
        return jsonify(ok=False,
                        err="could not fetch registry from GitHub main "
                            "— check network connectivity and "
                            "raw.githubusercontent.com"), 502

    # 3 — persist as APPDATA override. This is what makes the refresh
    # durable across orchestrator restarts; without it, every restart
    # would fall back to the stale bundled registry from the installer.
    try:
        ps._FW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        override_path = ps._FW_CACHE_DIR / "registry.json"
        override_path.write_text(json.dumps(fresh, indent=2),
                                  encoding="utf-8")
        ps.log.info("Registry refresh: wrote %d entries to %s",
                 len(fresh.get("firmware", [])), override_path)
    except Exception as e:
        # Fetch succeeded but write failed — surface the error so the
        # operator knows the refresh isn't durable. The in-memory cache
        # still serves the fresh data for this session.
        ps.log.warning("Registry refresh: APPDATA write failed: %s", e)
        return jsonify(ok=False,
                        err=f"fetched but couldn't persist: {e}",
                        firmware=[e for e in fresh.get("firmware", [])
                                   if not e.get("diagnostic")]), 500

    # 4 — return the fresh registry so the SPA can re-render directly.
    public = [e for e in fresh.get("firmware", []) if not e.get("diagnostic")]
    return jsonify(ok=True, **{**fresh, "firmware": public})


@bp.get("/api/firmware/library")
def api_fw_library():
    """#567 — return every registry entry annotated with its local
    availability so the Firmware Library section can render Download
    buttons for the missing ones.

    #769 — `local` is sha256-aware. A row is only "Local" when an on-disk
    candidate's sha256 matches the registry's pinned `sha256`; a stale
    file (present but mismatched) reports `local=False` so the SPA
    renders Download exactly like a missing entry. Without this, the
    Library said "Local" for the gyro entry while the flash path
    deleted the stale cache and failed to find the asset.

    2026-05-04 — entries flagged `diagnostic: true` (e.g. the
    gyro-test build) are stripped here so the operator never sees a
    developer-only firmware in the OTA install list. Build pipeline
    keeps building them; only the public surface is filtered.
    """
    from firmware_manager import _verify_sha256
    reg = ps._resolve_registry()  # #832 pass-3 — single helper for all four endpoints
    entries = []
    for e in reg.get("firmware", []):
        if e.get("diagnostic"):
            continue
        fname = e.get("file") or ""
        cache_path = ps._FW_CACHE_DIR / fname if fname else None
        bundle_path = ps._FW_DIR / fname if fname else None
        expected = e.get("sha256")
        local = False
        local_path = ""
        unreadable_path = ""
        for p in (cache_path, bundle_path):
            if not (p and p.is_file()):
                continue
            # #821 — tristate. True = Local, False = mismatched (skip),
            # None = present-but-unreadable (locked / ACL / OneDrive
            # cloud-only). Surface unreadable distinctly so the SPA can
            # show "Local but unreadable — check file lock / OneDrive
            # availability" rather than the misleading "Not downloaded"
            # that pre-fix sent operators on a wild-goose chase.
            v = _verify_sha256(p, expected) if expected else True
            if v is True:
                local = True
                local_path = str(p)
                break
            if v is False:
                continue
            # v is None
            unreadable_path = str(p)
            break
        entry_out = {**e, "local": local, "localPath": local_path,
                     "hasReleaseAsset": bool(e.get("releaseAsset"))}
        if not local and unreadable_path:
            entry_out["unreadable"] = True
            entry_out["localPath"] = unreadable_path
        entries.append(entry_out)
    return jsonify(firmware=entries)


@bp.post("/api/firmware/fetch")
def api_fw_fetch():
    """#567 — download a single registry entry's binary from the matching
    GitHub release asset into the writable cache directory. Idempotent:
    a redownload overwrites the cached copy.

    #768 — when the entry pins a `releaseTag`, fail loudly with the tag
    name when the GitHub release is missing or doesn't carry the asset.
    The previous generic "asset missing or network error" sent operators
    chasing whether GitHub was down when the real cause was a registry
    bump that pointed at a release that was never published.
    """
    from firmware_manager import download_firmware, _registry_fetch_assets
    body = request.get_json(silent=True) or {}
    fid = body.get("id") or ""
    entry = _resolve_registry_entry(fid)  # #832 pass-3
    if not entry:
        return jsonify(ok=False, err="unknown firmware id"), 404
    if not entry.get("releaseAsset"):
        return jsonify(ok=False, err="registry entry has no releaseAsset"), 400
    release_tag = entry.get("releaseTag")
    asset_name = entry.get("releaseAsset")
    if release_tag:
        # Pre-flight: surface "tag not found" / "asset missing from tag"
        # distinctly so the operator (and a future Library 'why' tooltip)
        # can tell the cases apart without reading server logs.
        per_release = _registry_fetch_assets(release_tag=release_tag)
        if per_release is None:
            return jsonify(ok=False,
                           err=f"release tag '{release_tag}' not found on GitHub "
                               "(check firmware/registry.json against published releases)"), 502
        if asset_name not in per_release:
            return jsonify(ok=False,
                           err=f"release '{release_tag}' has no asset named "
                               f"'{asset_name}' (publish the asset or fix releaseAsset)"), 502
    path = download_firmware(entry, ps._FW_CACHE_DIR)
    if not path:
        return jsonify(ok=False,
                       err="download or sha256 verification failed — see server log"), 502
    ps.log.info("Firmware library fetch: %s → %s", fid, path)
    return jsonify(ok=True, id=fid, path=path)


@bp.post("/api/firmware/refresh-all")
def api_fw_refresh_all():
    """#567 — bulk-download every registry entry that has a release asset
    but isn't cached locally. Skips entries already on disk so repeat
    clicks are cheap."""
    from firmware_manager import download_firmware, _registry_fetch_assets, _verify_sha256
    assets = _registry_fetch_assets()
    if assets is None:
        return jsonify(ok=False, err="could not reach GitHub releases"), 502
    reg = ps._resolve_registry()  # #832 pass-3
    results = []
    for e in reg.get("firmware", []):
        fname = e.get("file") or ""
        if not fname:
            continue
        expected = e.get("sha256")
        cache_p = ps._FW_CACHE_DIR / fname
        bundle_p = ps._FW_DIR / fname
        # sha-aware "already local". A v1.2.6 bin sitting in the cache
        # must NOT mask a v1.2.7 registry entry — the previous "any
        # file is good enough" check made #832-style local bumps
        # invisible to refresh-all.
        already_local = False
        for p in (cache_p, bundle_p):
            if not p.is_file():
                continue
            if not expected:
                already_local = True
                break
            if _verify_sha256(p, expected) is True:
                already_local = True
                break
        if already_local:
            results.append({"id": e.get("id"), "status": "already-local"})
            continue
        if not e.get("releaseAsset"):
            results.append({"id": e.get("id"), "status": "no-release-asset"})
            continue
        path = download_firmware(e, ps._FW_CACHE_DIR, assets_by_name=assets)
        results.append({"id": e.get("id"),
                        "status": "downloaded" if path else "download-failed"})
    downloaded = sum(1 for r in results if r["status"] == "downloaded")
    return jsonify(ok=True, results=results, downloaded=downloaded)

@bp.post("/api/firmware/download")
def api_fw_download():
    """Download latest firmware from GitHub Releases and save locally for USB flashing."""
    body = request.get_json(silent=True) or {}
    board = body.get("board", "")
    if board not in ("esp32", "d1mini"):
        return jsonify(ok=False, err="board must be esp32 or d1mini"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    # USB flash needs merged binary; OTA needs app-only. Download both for ESP32.
    # For D1 Mini there's only one binary that works for both.
    downloads = {
        "esp32": [
            ("esp32-firmware-merged.bin", "esp32/main.ino.merged.bin"),
            ("esp32-firmware-app.bin",    "esp32/main.ino.bin"),
        ],
        "d1mini": [
            ("d1mini-firmware.bin", "d1mini/main.ino.bin"),
        ],
    }
    assets_available = {a["name"]: a["url"] for a in rel.get("assets", [])}
    pairs = downloads.get(board, [])
    downloaded = 0
    import urllib.request as _ur
    try:
        for asset_name, target_path in pairs:
            url = assets_available.get(asset_name)
            if not url:
                ps.log.warning("Asset %s not in release, skipping", asset_name)
                continue
            ps.log.info("Downloading %s from %s", asset_name, url)
            req = _ur.Request(url, headers={"User-Agent": "SlyLED-Parent"})
            resp = _ur.urlopen(req, timeout=60)
            data = resp.read()
            dest = ps._FW_DIR / target_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            ps.log.info("Downloaded %s (%d bytes)  -' %s", asset_name, len(data), dest)
            downloaded += 1
        if downloaded == 0:
            return jsonify(ok=False, err=f"No firmware assets for {board} in release"), 404
        # Update local registry version
        reg_path = ps._FW_DIR / "registry.json"
        if reg_path.exists():
            reg = json.loads(reg_path.read_text())
            for fw in reg.get("firmware", []):
                if fw.get("board") == board and "child" in fw.get("id", ""):
                    fw["version"] = rel["version"]
            reg_path.write_text(json.dumps(reg, indent=2))
        return jsonify(ok=True, version=rel["version"], downloaded=downloaded)
    except Exception as e:
        ps.log.error("Download failed: %s", e)
        return jsonify(ok=False, err=str(e)), 502

# #870 — OTA-only filename split. ESP32 OTA appends bytes to the
# inactive OTA app partition (~1.5 MB on 4 MB flash). The merged
# binary (~4 MB: bootloader + partition table + app + ...) overflows
# the partition AND fails magic-byte validation. Cache filenames must
# carry an indicator of which flavour (app-only vs merged) they hold
# so a future fetch can't write merged bytes under an app-only name.
_OTA_APP_FILENAME = {
    "esp32":   "esp32/main.ino.app.bin",
    "esp32s3": "esp32s3/main.ino.app.bin",
    "d1mini":  "d1mini/main.ino.bin",  # D1 has no merged variant
}
# OTA-served binaries are app-only; sane upper bound for any ESP32-
# class app partition is well under 2 MB. A larger cached file is
# almost certainly a merged-image cache poisoning (#870) and must be
# treated as invalid — delete + re-fetch on next request.
_OTA_MAX_APP_BYTES = 2 * 1024 * 1024


def _ota_cache_is_poisoned(board, path):
    """Return True if a cached OTA binary at `path` has the size
    signature of a merged-image cache poisoning. Caller deletes +
    re-fetches. D1 Mini is single-binary (no merged variant) so the
    check is skipped."""
    if board == "d1mini":
        return False
    try:
        return path.stat().st_size > _OTA_MAX_APP_BYTES
    except OSError:
        return False


@bp.get("/api/firmware/binary/<board>")
def api_fw_binary(board):
    """Serve a firmware binary for OTA — child downloads from parent
    over plain HTTP because the ESP32 client can't do HTTPS to GitHub.

    Resolution order:
      1. Local file in `ps._FW_DIR/<board>/main.ino.app.bin` (dev /
         freshly built firmware — `build_release.ps1` drops the
         app-only binary alongside the merged image).
      2. Legacy local at `ps._FW_DIR/<board>/main.ino.bin` (pre-#870
         pipelines stored the app-only here; size-checked before
         serving so a stale merged-image at this path can't slip
         through).
      3. Cached file in APPDATA cache dir (downloads land here on
         first OTA after a fresh install).
      4. Per-board release-tag download from GitHub. The registry
         pins `releaseTag` (e.g. `esp32-v7.5.15`) and **`otaAsset`**
         (the app-only asset name, e.g. `esp32-firmware-app.bin`).
         Pre-#870 the proxy fell back to `releaseAsset` (the merged
         asset name) and saved 4 MB of merged image under the
         app-only filename — every subsequent OTA served the merged
         binary to the child, which silently failed at the OTA
         partition write. Releases lacking `otaAsset` now return 502
         with a clear error so the operator republishes instead of
         poisoning the cache.
    """
    from firmware_manager import _registry_fetch_assets, _verify_sha256
    rel_path = _OTA_APP_FILENAME.get(board)
    if not rel_path:
        return jsonify(ok=False, err=f"unknown board: {board}"), 404
    legacy_rel_path = {"esp32": "esp32/main.ino.bin",
                        "esp32s3": "esp32s3/main.ino.bin",
                        "d1mini": "d1mini/main.ino.bin"}.get(board)

    # #873 — registry entry resolved up-front so the otaSha256 pin is
    # available for verification regardless of which path produced
    # bin_path (local FW dir / cache / fresh GitHub fetch). Pre-#873
    # the entry was only loaded inside the GitHub-fetch branch, so
    # SHA verification couldn't run on cache hits — the audit gap
    # the issue describes.
    reg = ps._resolve_registry()
    board_to_fwid = {"esp32": "child-led-esp32",
                      "d1mini": "child-led-d1mini",
                      "esp32s3": "gyro-esp32s3"}
    fwid = board_to_fwid.get(board)
    entry = next((e for e in reg.get("firmware", [])
                  if e.get("id") == fwid), None) if fwid else None
    ota_asset = entry.get("otaAsset") if entry else None
    # D1 Mini's single binary serves both flash + OTA, so
    # `releaseAsset` is the OTA asset for it. ESP32 boards must
    # carry `otaAsset` explicitly.
    if not ota_asset and board == "d1mini" and entry:
        ota_asset = entry.get("releaseAsset")
    expected_sha = entry.get("otaSha256") if entry else None
    # D1 Mini SHA pin (when present) lives on `sha256` because there's
    # no merged-vs-app distinction.
    if not expected_sha and board == "d1mini" and entry:
        expected_sha = entry.get("sha256")

    candidates = [ps._FW_DIR / rel_path, ps._FW_DIR / legacy_rel_path,
                  ps._FW_CACHE_DIR / rel_path, ps._FW_CACHE_DIR / legacy_rel_path]
    bin_path = None
    for cand in candidates:
        if cand.exists():
            if _ota_cache_is_poisoned(board, cand):
                ps.log.warning("OTA cache poisoned at %s (%d bytes > "
                             "_OTA_MAX_APP_BYTES) — deleting (#870)",
                             cand, cand.stat().st_size)
                try:
                    cand.unlink()
                except OSError:
                    pass
                continue
            bin_path = cand
            break

    if bin_path is None:
        if entry and entry.get("releaseTag") and ota_asset:
            try:
                import urllib.request as _ur
                assets = _registry_fetch_assets(
                    release_tag=entry["releaseTag"]) or {}
                asset_url = assets.get(ota_asset)
                if asset_url:
                    ps.log.info("Proxy fetch: board=%s tag=%s otaAsset=%s",
                             board, entry["releaseTag"], ota_asset)
                    req = _ur.Request(asset_url,
                                       headers={"User-Agent": "SlyLED-Parent"})
                    resp = _ur.urlopen(req, timeout=60)
                    data = resp.read()
                    if (board != "d1mini"
                            and len(data) > _OTA_MAX_APP_BYTES):
                        ps.log.error("Proxy fetch refused: %s/%s is %d bytes "
                                   "(>2 MB) — registry pinned a merged "
                                   "binary as otaAsset (#870)",
                                   entry["releaseTag"], ota_asset, len(data))
                        return jsonify(
                            ok=False,
                            err=f"otaAsset '{ota_asset}' is "
                                f"{len(data) // 1024} kB — too large "
                                f"for OTA. The registry must pin the "
                                f"app-only asset, not the merged "
                                f"image."), 502
                    cache_path = ps._FW_CACHE_DIR / rel_path
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_bytes(data)
                    bin_path = cache_path
                else:
                    ps.log.warning("Proxy fetch: otaAsset %s not found in "
                                "release %s", ota_asset, entry["releaseTag"])
            except Exception as e:
                ps.log.error("Proxy fetch failed: %s", e)
    if bin_path is None or not bin_path.exists():
        # No app-only asset anywhere. Refuse loudly instead of
        # silently serving a merged image — the operator can republish
        # the release with the app-only asset, or USB-flash the merged.
        if entry and not ota_asset:
            err = (f"registry entry for {board} lacks otaAsset — OTA "
                   f"serves the app-only binary, not the merged image. "
                   f"Republish the release with the app-only asset.")
        else:
            err = (f"firmware binary not available for {board} "
                   f"(registry entry missing releaseTag/otaAsset, or "
                   f"GitHub fetch failed)")
        return jsonify(ok=False, err=err), 502 if entry and not ota_asset else 404

    # #873 — verify the resolved binary against the registry's
    # otaSha256 pin before serving. `_verify_sha256` is tristate:
    #   True  → match, serve.
    #   False → confirmed mismatch (corrupted / wrong file at the
    #           expected name); refuse with 502 and ps.log. Operator
    #           can clear the cache or republish the release.
    #   None  → file couldn't be read (transient I/O, AV lock); log
    #           a warning and serve anyway. The size-guard already
    #           rejected the obvious case (merged binary), and a
    #           transient read error here is no worse than the
    #           pre-#873 behaviour where the same byte stream went
    #           out unchallenged.
    # Entries without otaSha256 fall through to the unverified-
    # serve path so the route stays back-compat with registries
    # that haven't been re-pinned by build_release.ps1 yet.
    if expected_sha:
        verdict = _verify_sha256(bin_path, expected_sha)
        if verdict is False:
            ps.log.error("OTA SHA mismatch for %s: expected %s; refusing serve",
                       board, expected_sha[:12])
            return jsonify(
                ok=False,
                err=f"otaSha256 mismatch for {board} — cached binary "
                    f"does not match the registry pin. Clear "
                    f"%APPDATA%\\SlyLED\\firmware\\{board}\\ and retry."), 502
        if verdict is None:
            ps.log.warning("OTA SHA verify could not read %s; serving "
                         "without integrity check (transient I/O?)",
                         bin_path)

    return send_file(str(bin_path), mimetype="application/octet-stream",
                     download_name=f"slyled-{board}.bin")

@bp.post("/api/firmware/detect")
def api_fw_detect():
    """Detect chip type on an ambiguous port."""
    if not _fw_available:
        return jsonify(ok=False, err="esptool not available"), 500
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    if not port:
        return jsonify(ok=False, err="port required"), 400
    chip = detect_chip(port)
    return jsonify(ok=True, board=chip)

@bp.post("/api/firmware/flash")
def api_fw_flash():
    """Flash firmware to a board in a background thread."""
    if not _fw_available:
        return jsonify(ok=False, err="esptool not available"), 500
    if not ps._wifi.get("ssid") or not ps._wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required before flashing - set them on the Firmware tab first"), 400
    body = request.get_json(silent=True) or {}
    port = body.get("port", "")
    fw_id = body.get("firmwareId", "")
    board = body.get("board", "")
    # #761 §C-4 — optional device IP for post-flash verification. When the
    # SPA passes the IP of the target device (it usually knows from the
    # Setup tab / firmware-query flow), we'll poll http://<ip>/status after
    # esptool reports success and confirm the device's reported `version`
    # actually changed. Skipping this loses the only signal that
    # distinguishes "esptool wrote flash" from "esptool aborted silently".
    verify_ip = (body.get("verifyIp") or "").strip()
    if not port or not fw_id:
        return jsonify(ok=False, err="port and firmwareId required"), 400
    # #832 pass-3 — single helper so USB Flash sees the same registry
    # snapshot the Library does. Pre-fix this route ran a separate
    # GitHub-first lookup, so a freshly-built local v1.2.7 was visible
    # in the Library (post-pass-2) but Flash silently used main's stale
    # v1.2.6 sha and failed the post-download verification.
    fw = _resolve_registry_entry(fw_id)
    if not fw:
        return jsonify(ok=False, err="firmware not found in registry"), 404
    # #568 — if the binary is missing locally, auto-download it from the
    # matching GitHub release asset before kicking off the flash. The
    # installer no longer bundles binaries, so the first flash of any
    # given board will pull from the cloud on demand.
    from firmware_manager import resolve_binary_path, _verify_sha256
    bin_path_str = resolve_binary_path(fw, ps._FW_CACHE_DIR, ps._FW_DIR, auto_download=True)
    if not bin_path_str:
        # #821 — distinguish the genuine missing case from "file is
        # there but unreadable" (locked / ACL / OneDrive cloud-only).
        # Pre-fix the error always said "binary not found locally" even
        # when the operator was looking right at the file in Explorer.
        fname = fw.get("file") or ""
        expected = fw.get("sha256")
        unreadable_hits = []
        for label, base in (("cache", ps._FW_CACHE_DIR), ("bundle", ps._FW_DIR)):
            p = base / fname if fname else None
            if not (p and p.is_file()):
                continue
            v = _verify_sha256(p, expected) if expected else True
            if v is None:
                unreadable_hits.append(f"{label} ({p})")
        if unreadable_hits:
            return jsonify(ok=False,
                           err=("binary exists at " + ", ".join(unreadable_hits)
                                + " but the orchestrator could not read it. "
                                "Check file lock / antivirus / OneDrive "
                                "cloud-only placeholder; copy the file via "
                                "Windows Explorer to ensure local "
                                "availability.")), 502
        return jsonify(ok=False,
                       err=f"binary not found locally and release asset "
                           f"'{fw.get('releaseAsset') or fw.get('file')}' "
                           "could not be downloaded"), 502
    verify_url = f"http://{verify_ip}/status" if verify_ip else None
    # Flash in background thread
    def _do_flash():
        flash_board(port, bin_path_str, board or fw["board"],
                    wifi_ssid=ps._wifi.get("ssid"),
                    wifi_pass=ps._decrypt_pw(ps._wifi.get("password", "")),
                    verify_status_url=verify_url)
    threading.Thread(target=_do_flash, daemon=True).start()
    return jsonify(ok=True, message="Flashing started")

@bp.get("/api/firmware/flash/status")
def api_fw_flash_status():
    if not _fw_available:
        return jsonify(running=False, progress=0, message="not available")
    return jsonify(get_flash_status())

#  "  "  OTA firmware update  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  "  " 

_github_release_cache = {"data": None, "ts": 0}
_GITHUB_RELEASE_TTL = 3600  # 1 hour cache

# Cached remote registry pulled from raw.githubusercontent. The orchestrator
# bundle (PyInstaller) ships a snapshot of `firmware/registry.json` at
# build time, so an installer that's a few releases behind would compare
# its gyro against an outdated pinned-version. Pulling the live registry
# from GitHub at OTA-check time means an old orchestrator can still
# discover newer firmware bumps without reinstalling — just hits "Check
# for updates" and gets the current main-branch versions.
_remote_registry_cache = {"data": None, "ts": 0, "ok": False}
_REMOTE_REGISTRY_TTL = 300   # 5 min cache; OTA check is operator-driven, low traffic
_REMOTE_REGISTRY_URL = (
    "https://raw.githubusercontent.com/SlyWombat/SlyLED/main/firmware/registry.json"
)


def _fetch_remote_registry():
    """Pull `firmware/registry.json` from main branch on GitHub. Returns
    the parsed dict on success, or None on network/parse failure (caller
    falls back to the locally-bundled registry). TTL-cached so a single
    "Check for updates" click doesn't issue multiple HTTP requests.
    """
    import urllib.request as _ur
    now = time.time()
    cache = _remote_registry_cache
    if cache["data"] is not None and now - cache["ts"] < _REMOTE_REGISTRY_TTL:
        return cache["data"]
    try:
        req = _ur.Request(
            _REMOTE_REGISTRY_URL,
            headers={"User-Agent": "SlyLED-Parent",
                     "Accept": "application/json"})
        resp = _ur.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8-sig")  # strip BOM if present
        data = json.loads(raw)
        if not isinstance(data, dict) or "firmware" not in data:
            ps.log.warning("Remote registry: unexpected shape — falling back to local")
            cache["ok"] = False
            return None
        cache["data"] = data
        cache["ts"] = now
        cache["ok"] = True
        ps.log.info("Remote registry: fetched %d firmware entries from main",
                 len(data.get("firmware", [])))
        return data
    except Exception as e:
        ps.log.debug("Remote registry fetch failed: %s — using local bundle", e)
        cache["ok"] = False
        # Return the stale cache if we have one, so a transient network
        # blip doesn't immediately drop the operator back to a stale
        # bundled registry mid-session.
        return cache["data"]


def _load_registry_for_ota():
    """Return the firmware list to use for OTA-version comparison: the
    GitHub-main copy when available, else the locally-bundled snapshot.
    """
    remote = _fetch_remote_registry()
    if remote and isinstance(remote.get("firmware"), list):
        return remote.get("firmware", [])
    return load_registry(ps._FW_DIR, ps._FW_CACHE_DIR).get("firmware", [])


def _fetch_github_release():
    """Fetch latest release info from GitHub API. Returns dict or None."""
    import urllib.request as _ur
    now = time.time()
    if _github_release_cache["data"] and now - _github_release_cache["ts"] < _GITHUB_RELEASE_TTL:
        return _github_release_cache["data"]
    try:
        req = _ur.Request(
            "https://api.github.com/repos/SlyWombat/SlyLED/releases/latest",
            headers={"Accept": "application/vnd.github.v3+json", "User-Agent": "SlyLED-Parent"})
        resp = _ur.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        tag = data.get("tag_name", "").lstrip("v")
        assets = []
        for a in data.get("assets", []):
            assets.append({
                "name": a["name"],
                "size": a.get("size", 0),
                "url": a.get("browser_download_url", ""),
            })
        result = {"version": tag, "tag": data.get("tag_name", ""), "assets": assets,
                  "url": data.get("html_url", "")}
        _github_release_cache["data"] = result
        _github_release_cache["ts"] = now
        ps.log.info("GitHub release: v%s (%d assets)", tag, len(assets))
        return result
    except Exception as e:
        ps.log.debug("GitHub release fetch failed: %s", e)
        return _github_release_cache.get("data")  # return stale cache if available

@bp.get("/api/firmware/latest")
def api_firmware_latest():
    """Return latest firmware version from GitHub Releases."""
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info from GitHub"), 502
    # Include registry firmware version + whether release has firmware binaries.
    # Use the live GitHub registry so an installer that's a few releases
    # behind sees the actual current firmware versions.
    registry = _load_registry_for_ota()
    reg_versions = {e.get("board"): e.get("version", "0.0") for e in registry}
    has_fw = any(a.get("name", "").endswith(".bin") for a in rel.get("assets", []))
    return jsonify(**rel, registryVersion=max(reg_versions.values(), default="0.0"),
                   hasFirmware=has_fw,
                   registrySource=("github-main" if _remote_registry_cache.get("ok") else "local-bundle"))

@bp.get("/api/firmware/check")
def api_firmware_check():
    """Compare each child's firmware against the registry entry for its
    specific board. Each board track is independent — gyros compare against
    gyro-esp32s3, D1 Mini against child-led-d1mini, etc."""
    if not ps._wifi.get("ssid") or not ps._wifi.get("password"):
        return jsonify(ok=False, err="WiFi credentials required - set them on the Firmware tab before checking for updates"), 400
    rel = _fetch_github_release()
    if not rel:
        return jsonify(ok=False, err="Could not fetch release info"), 502
    # Pull the live GitHub-main registry so an old installer can still
    # discover current firmware versions without reinstalling. Falls
    # back to the locally-bundled registry on network failure.
    registry = _load_registry_for_ota()
    # Index registry entries by their `id` (e.g. "gyro-esp32s3", "child-led-esp32").
    reg_by_id = {e.get("id"): e for e in registry}

    # Map a child's detected board onto its registry id.
    board_to_regid = {
        "esp32":     "child-led-esp32",
        "d1mini":    "child-led-d1mini",
        "giga":      "child-led-giga",
        "giga-dmx":  "dmx-bridge-esp32",
        "dmx":       "dmx-bridge-esp32",
        "gyro":      "gyro-esp32s3",
    }

    def _detect_board(c):
        """Return a normalised board key for this child."""
        if c.get("type") == "wled":
            return "wled"
        if c.get("type") == "gyro":
            return "gyro"
        if c.get("type") == "dmx":
            # DMX bridge — prefer Giga-DMX when hostname is SLYC-* or boardType says so
            if (c.get("boardType") or "").lower().startswith("giga"):
                return "giga-dmx"
            return "dmx"
        bt = (c.get("boardType") or "").lower()
        if "gyro" in bt:
            return "gyro"
        if bt in ("esp32",):
            return "esp32"
        if "d1" in bt or bt == "d1mini":
            return "d1mini"
        if "giga" in bt:
            return "giga"
        return "esp32"  # last-resort fallback

    def _cmp(cur, latest):
        try:
            cur_parts  = [int(x) for x in (cur or "0.0").split(".")]
            lat_parts  = [int(x) for x in (latest or "0.0").split(".")]
            while len(cur_parts) < 3: cur_parts.append(0)
            while len(lat_parts) < 3: lat_parts.append(0)
            return lat_parts > cur_parts
        except (ValueError, IndexError):
            return (cur or "") != (latest or "")

    results = []
    for c in ps._children:
        board = _detect_board(c)
        reg_entry = reg_by_id.get(board_to_regid.get(board))
        latest = reg_entry.get("version", "0.0") if reg_entry else "0.0"
        fw = c.get("fwVersion", "0.0") or "0.0"
        needs_update = _cmp(fw, latest) if board != "wled" else False

        # OTA download URL (ESP32/D1 only — Gigas are DFU-only, gyro flashes over USB).
        asset_prefs = {
            "esp32":  ["esp32-firmware-app.bin", "esp32-firmware-merged.bin"],
            "d1mini": ["d1mini-firmware.bin"],
        }
        download_url = ""
        for name in asset_prefs.get(board, []):
            for a in rel.get("assets", []):
                if a["name"] == name:
                    download_url = a["url"]
                    break
            if download_url:
                break

        results.append({
            "id": c["id"], "hostname": c.get("hostname"), "name": c.get("name", ""),
            "ip": c.get("ip", ""),
            "currentVersion": fw, "latestVersion": latest,
            "needsUpdate": needs_update, "board": board,
            "type": c.get("type", ""),
            "status": c.get("status", 0),
            "downloadUrl": download_url,
            # #922 — live OTA progress from CMD_OTA_STATUS (0x51), recorded
            # by parent_server._handle_ota_status keyed by sender IP:
            # {status, statusName, progress, updatedAt} or None when the
            # child has never reported. Read through ps at call time so
            # the store is the live one (orch_state bridge contract).
            "ota": ps._ota_status_live.get(c.get("ip") or ""),
        })

    # #866 — `latest` field removed. Previously the fleet-max version
    # across all board tracks (gyro v1.x, ESP32 v7.x, DMX bridge
    # v7.5.20, etc.), which was meaningless once tracks diverged and
    # caused the Firmware tab to mis-label gyro v1.2.7 as "v7.5.20
    # available". The SPA now reads per-row `latestVersion` and
    # aggregates per board for the header. Each `c.latestVersion` in
    # the children list IS authoritative for that child's board track.
    return jsonify({"children": results})

@bp.post("/api/firmware/ota/<int:cid>")
def api_firmware_ota(cid):
    """Trigger OTA update on a specific child.

    Accepts ``?force=1`` (or body ``{"force": true}``) to bypass the
    `status==1` (online) precondition. The "online" flag derives from
    UDP PING/PONG round-trips; an older firmware whose PONG/STATUS_RESP
    format the orchestrator no longer parses will look offline forever
    even though the board is on the network and its HTTP `/ota`
    endpoint is healthy. Force mode is the recovery path for that:
    the orchestrator's HTTP push goes through if the board's TCP
    socket opens, regardless of UDP listener health. The board still
    has to be reachable on its IP for the OTA to actually start.
    """
    child = next((c for c in ps._children if c["id"] == cid), None)
    if not child:
        return jsonify(ok=False, err="child not found"), 404
    if child.get("type") == "wled":
        return jsonify(ok=False, err="WLED devices update through their own UI"), 400
    body_in = request.get_json(silent=True) or {}
    force = (request.args.get("force", "").lower() in ("1", "true", "yes")
             or bool(body_in.get("force")))
    if not force and child.get("status") != 1:
        return jsonify(ok=False,
                        err="child is offline (use force=1 to override "
                            "when board is HTTP-reachable but UDP-stale)"), 400

    # Require WiFi credentials to be configured before OTA
    if not ps._wifi.get("ssid"):
        return jsonify(ok=False, err="WiFi credentials not configured - set them on the Firmware tab first"), 400

    # Push WiFi credentials to child before OTA (so new firmware can reconnect)
    ip = child["ip"]
    try:
        import urllib.request as _ur
        wifi_body = json.dumps({"ssid": ps._wifi["ssid"],
                                "password": ps._decrypt_pw(ps._wifi.get("password", ""))}).encode()
        wifi_req = _ur.Request(f"http://{ip}/wifi", data=wifi_body, method="POST",
                               headers={"Content-Type": "application/json"})
        _ur.urlopen(wifi_req, timeout=3)
        ps.log.info("OTA: pushed WiFi credentials to %s", ip)
    except Exception as e:
        ps.log.warning("OTA: failed to push WiFi to %s: %s (continuing anyway)", ip, e)

    # Determine board type from stored boardType
    bt = child.get("boardType", "")
    board = "esp32" if bt in ("ESP32", "esp32") else "d1mini" if bt in ("D1 Mini", "d1mini") else "esp32"

    # Look up the registry entry for THIS board. Pre-fix this used
    # `_fetch_github_release()` which returns the orchestrator app's
    # latest release (v1.7.x) — its assets don't include any firmware
    # bin, so the proxy fetch silently failed. The registry pins each
    # firmware id's release tag and version independently from the
    # app track; reading the registry entry guarantees we OTA to the
    # version actually listed for this board.
    reg = ps._resolve_registry()
    board_to_fwid = {"esp32": "child-led-esp32",
                      "d1mini": "child-led-d1mini"}
    fwid = board_to_fwid.get(board)
    entry = next((e for e in reg.get("firmware", []) if e.get("id") == fwid), None) if fwid else None
    if not entry:
        return jsonify(ok=False, err=f"no registry entry for board {board}"), 404
    latest = entry.get("version", "0.0")
    try:
        parts = latest.split(".")
        new_major = int(parts[0])
        new_minor = int(parts[1]) if len(parts) > 1 else 0
        new_patch = int(parts[2]) if len(parts) > 2 else 0
    except (ValueError, IndexError):
        return jsonify(ok=False, err=f"registry entry {fwid} has invalid version"), 500

    # Send OTA command   " use parent as proxy (child can't do HTTPS to GitHub)
    ip = child["ip"]
    # Determine parent's LAN IP for the proxy URL
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        parent_ip = s.getsockname()[0]
        s.close()
    except Exception:
        parent_ip = "127.0.0.1"
    # Use the actual Flask port from the incoming request (not hardcoded 8080)
    parent_port = request.host.split(":")[-1] if ":" in request.host else "8080"
    proxy_url = f"http://{parent_ip}:{parent_port}/api/firmware/binary/{board}"
    ps.log.info("OTA: triggering update on %s (%s) to v%s via proxy %s", ip, child.get("hostname"), latest, proxy_url)
    try:
        import urllib.request as _ur
        body = json.dumps({"url": proxy_url, "sha256": "", "major": new_major, "minor": new_minor, "patch": new_patch}).encode()
        req = _ur.Request(f"http://{ip}/ota", data=body, method="POST",
                          headers={"Content-Type": "application/json"})
        _ur.urlopen(req, timeout=5)
    except Exception as e:
        ps.log.warning("OTA trigger to %s failed: %s", ip, e)
        # Child may have already started updating and dropped the connection   " that's OK
        pass

    return jsonify(ok=True, version=latest, board=board)
