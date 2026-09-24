#!/usr/bin/env python3
"""test_platform_paths.py — #948 Phase 0.1: per-OS writable directories.

Asserts desktop/shared/app_dirs.py answers for win32 / darwin / linux from
any host (platform, environ and home are injected), plus the one piece that
reads process state — `sys.frozen` — by monkeypatching it.

Run:
    python3 tests/test_platform_paths.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import app_dirs  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def eq(name, got, want):
    ok(name, got == want, f"got {got!r}, want {want!r}")


HOME = Path("/home/op")
# A git checkout (REPO/.git exists) vs an installed copy of the same tree
# (INST, no .git — what desktop/linux/install.sh lays down in /opt/slyled).
_TMP = Path(tempfile.mkdtemp(prefix="slyled-paths-"))
REPO, INST = _TMP / "repo", _TMP / "opt-slyled"
for _root in (REPO, INST):
    (_root / "desktop" / "shared").mkdir(parents=True)
    (_root / "firmware").mkdir()
(REPO / ".git").mkdir()
SRC, FW = REPO / "desktop" / "shared", REPO / "firmware"
INST_SRC, INST_FW = INST / "desktop" / "shared", INST / "firmware"
WIN_ENV = {"APPDATA": "/w/Roaming", "LOCALAPPDATA": "/w/Local"}


def run():
    # ── user_data_root / user_local_root / cache_root ─────────────────────
    eq("win32 data root = %APPDATA%\\SlyLED",
       app_dirs.user_data_root("win32", WIN_ENV, HOME), Path("/w/Roaming/SlyLED"))
    eq("win32 local root = %LOCALAPPDATA%\\SlyLED",
       app_dirs.user_local_root("win32", WIN_ENV, HOME), Path("/w/Local/SlyLED"))
    eq("win32 cache root under LOCALAPPDATA",
       app_dirs.cache_root("win32", WIN_ENV, HOME), Path("/w/Local/SlyLED/Cache"))
    eq("win32 data root without APPDATA falls back under home",
       app_dirs.user_data_root("win32", {}, HOME), HOME / "AppData/Roaming/SlyLED")

    eq("darwin data root = ~/Library/Application Support/SlyLED",
       app_dirs.user_data_root("darwin", {}, HOME),
       HOME / "Library/Application Support/SlyLED")
    eq("darwin local root = data root",
       app_dirs.user_local_root("darwin", {}, HOME),
       HOME / "Library/Application Support/SlyLED")
    eq("darwin cache root = ~/Library/Caches/SlyLED",
       app_dirs.cache_root("darwin", {}, HOME), HOME / "Library/Caches/SlyLED")
    eq("darwin ignores XDG vars",
       app_dirs.user_data_root("darwin", {"XDG_DATA_HOME": "/x"}, HOME),
       HOME / "Library/Application Support/SlyLED")

    eq("linux data root default = ~/.local/share/SlyLED",
       app_dirs.user_data_root("linux", {}, HOME), HOME / ".local/share/SlyLED")
    eq("linux data root honours XDG_DATA_HOME",
       app_dirs.user_data_root("linux", {"XDG_DATA_HOME": "/xdg/data"}, HOME),
       Path("/xdg/data/SlyLED"))
    eq("linux cache root default = ~/.cache/SlyLED",
       app_dirs.cache_root("linux", {}, HOME), HOME / ".cache/SlyLED")
    eq("linux cache root honours XDG_CACHE_HOME",
       app_dirs.cache_root("linux", {"XDG_CACHE_HOME": "/xdg/cache"}, HOME),
       Path("/xdg/cache/SlyLED"))
    eq("empty XDG_DATA_HOME treated as unset",
       app_dirs.user_data_root("linux", {"XDG_DATA_HOME": ""}, HOME),
       HOME / ".local/share/SlyLED")

    # ── data_dir: SLYLED_DATA override, per-OS frozen, source fallback ────
    for plat in ("win32", "darwin", "linux"):
        env = dict(WIN_ENV, SLYLED_DATA="/tmp/isolated")
        for frozen in (True, False):
            eq(f"{plat} frozen={frozen}: SLYLED_DATA wins verbatim",
               app_dirs.data_dir(SRC, frozen, plat, env, HOME), Path("/tmp/isolated"))

    eq("win32 source run still uses %APPDATA%\\SlyLED\\data (pre-#948)",
       app_dirs.data_dir(SRC, False, "win32", WIN_ENV, HOME), Path("/w/Roaming/SlyLED/data"))
    eq("win32 frozen = %APPDATA%\\SlyLED\\data",
       app_dirs.data_dir(SRC, True, "win32", WIN_ENV, HOME), Path("/w/Roaming/SlyLED/data"))
    eq("darwin frozen = Application Support/SlyLED/data",
       app_dirs.data_dir(SRC, True, "darwin", {}, HOME),
       HOME / "Library/Application Support/SlyLED/data")
    eq("linux frozen = ~/.local/share/SlyLED/data",
       app_dirs.data_dir(SRC, True, "linux", {}, HOME), HOME / ".local/share/SlyLED/data")
    eq("darwin git checkout stays self-contained (BASE/data)",
       app_dirs.data_dir(SRC, False, "darwin", {}, HOME), SRC / "data")
    eq("linux git checkout stays self-contained (BASE/data)",
       app_dirs.data_dir(SRC, False, "linux", {}, HOME), SRC / "data")
    wt = _TMP / "worktree"
    (wt / "desktop" / "shared").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: /elsewhere\n")
    eq("linked worktree (.git file) counts as a checkout",
       app_dirs.data_dir(wt / "desktop" / "shared", False, "linux", {}, HOME),
       wt / "desktop" / "shared" / "data")
    eq("installed source copy (no .git) -> per-user dir, not the install tree",
       app_dirs.data_dir(INST_SRC, False, "linux", {}, HOME), HOME / ".local/share/SlyLED/data")
    eq("installed copy honours XDG_DATA_HOME (systemd unit sets it)",
       app_dirs.data_dir(INST_SRC, False, "linux", {"XDG_DATA_HOME": "/var/lib/slyled"}, HOME),
       Path("/var/lib/slyled/SlyLED/data"))
    eq("installed copy on macOS -> Application Support",
       app_dirs.data_dir(INST_SRC, False, "darwin", {}, HOME),
       HOME / "Library/Application Support/SlyLED/data")

    # ── firmware cache + runtime dirs ────────────────────────────────────
    eq("git checkout: firmware cache = repo firmware tree",
       app_dirs.firmware_cache_dir(FW, False, "darwin", {}, HOME), FW)
    eq("installed copy: firmware cache under XDG data, not the root-owned tree",
       app_dirs.firmware_cache_dir(INST_FW, False, "linux",
                                   {"XDG_DATA_HOME": "/var/lib/slyled"}, HOME),
       Path("/var/lib/slyled/SlyLED/firmware"))
    eq("frozen beats a stray .git: firmware cache per-user",
       app_dirs.firmware_cache_dir(FW, True, "linux", {}, HOME),
       HOME / ".local/share/SlyLED/firmware")
    eq("win32 frozen firmware cache = %APPDATA%\\SlyLED\\firmware",
       app_dirs.firmware_cache_dir(FW, True, "win32", WIN_ENV, HOME),
       Path("/w/Roaming/SlyLED/firmware"))
    eq("darwin frozen firmware cache under Application Support",
       app_dirs.firmware_cache_dir(FW, True, "darwin", {}, HOME),
       HOME / "Library/Application Support/SlyLED/firmware")
    eq("linux frozen firmware cache under XDG data",
       app_dirs.firmware_cache_dir(FW, True, "linux", {}, HOME),
       HOME / ".local/share/SlyLED/firmware")
    eq("win32 runtime dir = %LOCALAPPDATA%\\SlyLED\\runtimes",
       app_dirs.runtime_dir("win32", WIN_ENV, HOME), Path("/w/Local/SlyLED/runtimes"))
    eq("darwin runtime dir under Application Support",
       app_dirs.runtime_dir("darwin", {}, HOME),
       HOME / "Library/Application Support/SlyLED/runtimes")
    eq("linux runtime dir = ~/.local/share/SlyLED/runtimes (pre-#948 path)",
       app_dirs.runtime_dir("linux", {}, HOME), HOME / ".local/share/SlyLED/runtimes")

    # ── sys.frozen monkeypatch: defaults read the live process ───────────
    had = hasattr(sys, "frozen")
    saved = getattr(sys, "frozen", None)
    try:
        sys.frozen = True
        ok("is_frozen() sees sys.frozen", app_dirs.is_frozen())
        eq(f"{sys.platform} frozen (live sys.frozen, SLYLED_DATA unset) -> per-user data dir",
           app_dirs.data_dir(SRC, environ={k: v for k, v in os.environ.items()
                                           if k != "SLYLED_DATA"}),
           app_dirs.user_data_root() / "data")
        ok("frozen data dir is never inside the source/_MEIPASS base",
           SRC not in app_dirs.data_dir(SRC, environ={}, home=HOME).parents)
    finally:
        if had:
            sys.frozen = saved
        else:
            del sys.frozen
    ok("is_frozen() false from source", not app_dirs.is_frozen())


def main():
    run()
    passed = sum(1 for _, c, _ in results if c)
    failed = len(results) - passed
    for name, cond, detail in results:
        tag = "PASS" if cond else "FAIL"
        extra = f"  ({detail})" if (detail and not cond) else ""
        print(f"  [{tag}] {name}{extra}")
    print("=" * 60)
    print(f"  {passed} passed, {failed} failed out of {len(results)} tests")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
