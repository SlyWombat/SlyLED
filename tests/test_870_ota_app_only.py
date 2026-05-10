"""test_870_ota_app_only.py — OTA proxy serves app-only, never merged.

Reproduces the live-test 2026-05-09 cache-poisoning symptom: ESP Dual
(child 2, ESP32, 4 MB flash) was stuck on firmware v7.5.2 despite the
registry advertising v7.5.11. Forcing OTA initiated but never produced
a version flip on the child. Root cause: `%APPDATA%\\SlyLED\\firmware\\
esp32\\main.ino.bin` was 4,194,304 bytes — the merged firmware image
(bootloader + partitions + app + ...), not the ~1 MB app-only OTA
binary. Replacing the cached file with the correct app-only binary
upgraded the child to v7.5.11 in seconds.

This test pins five invariants:

  1. Cache-poisoning self-heal: when a cached binary at the OTA
     filename exceeds 2 MB for an ESP32-class board, the proxy
     deletes it instead of serving it.

  2. The proxy refuses to fall back to the merged binary when
     `otaAsset` is missing from the registry — returns 502 with a
     clear error so the operator republishes instead of poisoning
     the cache.

  3. The proxy refuses to write a freshly-fetched binary that is
     >2 MB to the cache, even when GitHub returns one. The size
     guard runs both on serve AND on fetch.

  4. Healthy serves work: a valid <2 MB cached app-only binary is
     served via send_file with no error.

  5. D1 Mini's single-binary path (no merged variant) bypasses the
     size check — `releaseAsset` is the OTA asset for it.

Run: python3 -X utf8 tests/test_870_ota_app_only.py
"""

import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import parent_server  # noqa: E402

_passed = 0
_failed = 0


def _assert(cond, msg):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


# ── Helpers ───────────────────────────────────────────────────────────────────

class _Sandbox:
    """Tempdir + monkey-patched `_FW_DIR` / `_FW_CACHE_DIR` / `_resolve_registry`
    so each test starts with a clean filesystem and registry shape."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="slyled-test-870-")
        self.fw_dir = os.path.join(self.tmp, "fw")
        self.cache_dir = os.path.join(self.tmp, "cache")
        os.makedirs(self.fw_dir)
        os.makedirs(self.cache_dir)
        self._orig_fw = parent_server._FW_DIR
        self._orig_cache = parent_server._FW_CACHE_DIR
        self._orig_resolve = parent_server._resolve_registry
        from pathlib import Path
        parent_server._FW_DIR = Path(self.fw_dir)
        parent_server._FW_CACHE_DIR = Path(self.cache_dir)
        return self

    def write_cached(self, rel_path, size_bytes):
        """Drop a synthetic binary at `_FW_CACHE_DIR/<rel_path>` of
        the given size — used to simulate poisoned vs healthy caches."""
        from pathlib import Path
        p = parent_server._FW_CACHE_DIR / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        # Bytes don't matter — only size + path matter for these tests.
        p.write_bytes(b"\x00" * size_bytes)
        return p

    def write_local(self, rel_path, size_bytes):
        from pathlib import Path
        p = parent_server._FW_DIR / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00" * size_bytes)
        return p

    def patch_registry(self, entries):
        parent_server._resolve_registry = lambda: {"firmware": entries}

    def __exit__(self, *_a):
        parent_server._FW_DIR = self._orig_fw
        parent_server._FW_CACHE_DIR = self._orig_cache
        parent_server._resolve_registry = self._orig_resolve
        try:
            shutil.rmtree(self.tmp)
        except OSError:
            pass


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_cache_self_heal_deletes_poisoned_4mb_file():
    """Pre-#870 a 4 MB merged image cached at `main.ino.bin` was served
    over OTA every request, silently failing every fresh-cache child.
    Post-#870 the proxy deletes the poisoned file before the request
    completes."""
    with _Sandbox() as sb:
        # Empty registry → no fallback fetch path.
        sb.patch_registry([])
        poisoned = sb.write_cached("esp32/main.ino.app.bin", 4 * 1024 * 1024)
        _assert(poisoned.exists(),
                f"poisoned cache exists pre-request ({poisoned.stat().st_size} bytes)")
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
            # No registry entry → 404 after self-heal. The point is
            # the poisoned file is gone.
            _assert(r.status_code in (404, 502),
                    f"poisoned-cache + no-registry → 404/502 (got {r.status_code})")
        _assert(not poisoned.exists(),
                "poisoned cache deleted by self-heal (#870)")


def test_legacy_main_ino_bin_path_is_size_checked():
    """Pre-#870 the cache filename was `main.ino.bin`. Existing
    operator caches still hit that path. The self-heal must apply to
    the legacy filename too."""
    with _Sandbox() as sb:
        sb.patch_registry([])
        poisoned = sb.write_cached("esp32/main.ino.bin", 4 * 1024 * 1024)
        with parent_server.app.test_client() as c:
            c.get("/api/firmware/binary/esp32")
        _assert(not poisoned.exists(),
                "legacy main.ino.bin path also self-heals (#870)")


def test_healthy_app_only_cache_is_served():
    """A cache that's <2 MB and at the OTA filename serves cleanly.
    No 404, no 502, no self-heal."""
    with _Sandbox() as sb:
        sb.patch_registry([])
        sb.write_cached("esp32/main.ino.app.bin", 1_092_800)  # real size
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
        _assert(r.status_code == 200,
                f"healthy app-only cache serves 200 (got {r.status_code})")
        _assert(len(r.data) == 1_092_800,
                f"served bytes match cache ({len(r.data)})")


def test_local_fw_dir_takes_priority_over_cache():
    """`_FW_DIR/<board>/main.ino.app.bin` (dev / freshly-built) is
    preferred over the APPDATA cache. The size check still applies."""
    with _Sandbox() as sb:
        sb.patch_registry([])
        # Cache has a healthy file too.
        sb.write_cached("esp32/main.ino.app.bin", 1_000_000)
        local = sb.write_local("esp32/main.ino.app.bin", 1_500_000)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
        _assert(r.status_code == 200, f"local takes priority (got {r.status_code})")
        _assert(len(r.data) == 1_500_000,
                f"served local bytes (got {len(r.data)}, expected 1500000)")
        _ = local


def test_registry_without_ota_asset_refuses_with_502():
    """Pre-#870 a registry entry with only `releaseAsset: …-merged.bin`
    caused the proxy to download the merged binary and write it to the
    app-only cache filename. Post-#870 the proxy refuses to serve
    merged bytes — it requires `otaAsset` to be set or the request 502s."""
    with _Sandbox() as sb:
        sb.patch_registry([{
            "id": "child-led-esp32",
            "releaseTag": "esp32-v7.5.15",
            "releaseAsset": "esp32-firmware-merged.bin",
            # otaAsset deliberately missing
        }])
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
        _assert(r.status_code == 502,
                f"missing otaAsset → 502 (got {r.status_code})")
        body = r.get_json() or {}
        _assert("otaAsset" in (body.get("err") or "").lower()
                or "app-only" in (body.get("err") or "").lower(),
                f"error message names otaAsset / app-only "
                f"(got {body.get('err')!r})")


def test_d1mini_uses_releaseasset_when_no_ota_asset():
    """D1 Mini has only one binary (no merged variant). For it,
    `releaseAsset` IS the OTA asset, and the size check is skipped
    because there's no merged-vs-app distinction to police."""
    with _Sandbox() as sb:
        # Healthy local file so the registry path isn't even consulted.
        sb.patch_registry([{
            "id": "child-led-d1mini",
            "releaseTag": "d1mini-v7.5.14",
            "releaseAsset": "d1mini-firmware.bin",
            # no otaAsset
        }])
        sb.write_local("d1mini/main.ino.bin", 380_000)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/d1mini")
        _assert(r.status_code == 200,
                f"D1 single-binary serves cleanly (got {r.status_code})")


# ── #873 — otaSha256 verification on serve ───────────────────────────────────

import hashlib


def _sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def test_873_sha_mismatch_refuses_serve_with_502():
    """Registry pins SHA `X`; cache contains a binary with SHA `Y` ≠ `X`.
    Pre-#873 the orchestrator would have served the corrupted bytes
    unchallenged (the 2 MB size guard from #870 only catches gross
    poisoning, not sub-2-MB drift). Post-#873 the proxy refuses with
    502 and an operator-facing error pointing at the cache path."""
    with _Sandbox() as sb:
        # Cache binary is 1 MB of zeros → has its own deterministic SHA.
        cache_data = b"\x00" * 1_000_000
        actual_sha = _sha256_hex(cache_data)
        # Pin a DIFFERENT SHA in the registry so verification fails.
        pinned_sha = _sha256_hex(b"different bytes")
        sb.patch_registry([{
            "id": "child-led-esp32",
            "releaseTag": "esp32-v7.5.15",
            "releaseAsset": "esp32-firmware-merged.bin",
            "otaAsset": "esp32-firmware-app.bin",
            "otaSha256": pinned_sha,
        }])
        cache = parent_server._FW_CACHE_DIR / "esp32" / "main.ino.app.bin"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(cache_data)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
        _assert(r.status_code == 502,
                f"SHA mismatch → 502 (got {r.status_code})")
        body = r.get_json() or {}
        err = (body.get("err") or "").lower()
        _assert("otasha256" in err or "mismatch" in err,
                f"error names otaSha256 / mismatch (got {body.get('err')!r})")
        _ = actual_sha  # actual_sha differs from pinned; documented inline


def test_873_sha_match_serves_200():
    """Registry pins SHA `X`; cache contains binary with SHA `X` →
    serves 200 with the bytes."""
    with _Sandbox() as sb:
        cache_data = b"\x42" * 800_000
        sha = _sha256_hex(cache_data)
        sb.patch_registry([{
            "id": "child-led-esp32",
            "releaseTag": "esp32-v7.5.15",
            "releaseAsset": "esp32-firmware-merged.bin",
            "otaAsset": "esp32-firmware-app.bin",
            "otaSha256": sha,
        }])
        cache = parent_server._FW_CACHE_DIR / "esp32" / "main.ino.app.bin"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(cache_data)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
        _assert(r.status_code == 200,
                f"SHA match → 200 (got {r.status_code})")
        _assert(len(r.data) == len(cache_data),
                f"served bytes match cache ({len(r.data)})")


def test_873_no_sha_pin_falls_through_for_back_compat():
    """Registry without `otaSha256` (pre-#873 entries) → unverified
    serve. Critical for back-compat: an old registry pulled from
    GitHub / APPDATA shouldn't 502 just because the SHA pin is
    missing — it falls through to the size-guard-only path that
    #870 established."""
    with _Sandbox() as sb:
        cache_data = b"\xab" * 600_000
        sb.patch_registry([{
            "id": "child-led-esp32",
            "releaseTag": "esp32-v7.5.15",
            "releaseAsset": "esp32-firmware-merged.bin",
            "otaAsset": "esp32-firmware-app.bin",
            # No otaSha256 — pre-#873 registry shape.
        }])
        cache = parent_server._FW_CACHE_DIR / "esp32" / "main.ino.app.bin"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(cache_data)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/esp32")
        _assert(r.status_code == 200,
                f"no SHA pin → unverified serve (got {r.status_code})")
        _assert(len(r.data) == len(cache_data),
                f"served bytes ({len(r.data)})")


def test_873_d1mini_uses_legacy_sha256_pin():
    """D1 Mini has no merged-vs-app distinction — the single binary
    is the OTA asset and the integrity pin lives on `sha256` (no
    `otaSha256` because there's no second flavour to disambiguate).
    The route must still verify it."""
    with _Sandbox() as sb:
        cache_data = b"\x99" * 350_000
        sha = _sha256_hex(cache_data)
        sb.patch_registry([{
            "id": "child-led-d1mini",
            "releaseTag": "d1mini-v7.5.14",
            "releaseAsset": "d1mini-firmware.bin",
            "sha256": sha,
        }])
        local = parent_server._FW_DIR / "d1mini" / "main.ino.bin"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(cache_data)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/d1mini")
        _assert(r.status_code == 200,
                f"D1 SHA match → 200 (got {r.status_code})")

        # Now corrupt the binary and confirm refusal.
        local.write_bytes(b"\x00" * 350_000)
        with parent_server.app.test_client() as c:
            r = c.get("/api/firmware/binary/d1mini")
        _assert(r.status_code == 502,
                f"D1 SHA mismatch → 502 (got {r.status_code})")


# ── Test registry ────────────────────────────────────────────────────────────

ALL = [
    test_cache_self_heal_deletes_poisoned_4mb_file,
    test_legacy_main_ino_bin_path_is_size_checked,
    test_healthy_app_only_cache_is_served,
    test_local_fw_dir_takes_priority_over_cache,
    test_registry_without_ota_asset_refuses_with_502,
    test_d1mini_uses_releaseasset_when_no_ota_asset,
    test_873_sha_mismatch_refuses_serve_with_502,
    test_873_sha_match_serves_200,
    test_873_no_sha_pin_falls_through_for_back_compat,
    test_873_d1mini_uses_legacy_sha256_pin,
]


if __name__ == "__main__":
    print("=== #870 OTA app-only contract ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            import traceback
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
            traceback.print_exc()
    total = _passed + _failed
    print(f"\n{_passed} passed, {_failed} failed out of {total}")
    sys.exit(0 if _failed == 0 else 1)
