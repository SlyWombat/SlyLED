"""
Per-OS locations for SlyLED's writable directories (#948 Phase 0.1).

One module owns the "where does the orchestrator write things" question so
the data dir, the firmware download cache and the depth-runtime venv stop
each inventing their own convention.

                 user_data_root()                    user_local_root()                cache_root()
  Windows        %APPDATA%\\SlyLED                    %LOCALAPPDATA%\\SlyLED            %LOCALAPPDATA%\\SlyLED\\Cache
  macOS          ~/Library/Application Support/SlyLED   (same as data root)             ~/Library/Caches/SlyLED
  Linux/other    $XDG_DATA_HOME/SlyLED                  (same as data root)             $XDG_CACHE_HOME/SlyLED
                 (default ~/.local/share/SlyLED)                                          (default ~/.cache/SlyLED)

Every function takes an optional `platform` / `environ` / `home` so the
per-OS answers can be asserted from any host (tests/test_platform_paths.py).
Nothing here creates directories — callers mkdir what they use.
"""

import os
import sys
from pathlib import Path

APP_NAME = "SlyLED"


def _plat(platform):
    return sys.platform if platform is None else platform


def _env(environ):
    return os.environ if environ is None else environ


def _home(home):
    return Path.home() if home is None else Path(home)


def is_frozen():
    """True inside a PyInstaller bundle (exe, .app, or onedir tarball)."""
    return bool(getattr(sys, "frozen", False))


def user_data_root(platform=None, environ=None, home=None):
    """Per-user, persistent (roaming on Windows) SlyLED root."""
    plat, env, h = _plat(platform), _env(environ), _home(home)
    if plat == "win32":
        base = env.get("APPDATA")
        return (Path(base) if base else h / "AppData" / "Roaming") / APP_NAME
    if plat == "darwin":
        return h / "Library" / "Application Support" / APP_NAME
    xdg = env.get("XDG_DATA_HOME")
    return (Path(xdg) if xdg else h / ".local" / "share") / APP_NAME


def user_local_root(platform=None, environ=None, home=None):
    """Per-user, per-machine root for large non-roaming content (runtimes,
    model weights). Only Windows distinguishes this from user_data_root()."""
    plat, env, h = _plat(platform), _env(environ), _home(home)
    if plat == "win32":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else h / "AppData" / "Local") / APP_NAME
    return user_data_root(plat, env, h)


def cache_root(platform=None, environ=None, home=None):
    """Per-user cache root — content here may be deleted at any time."""
    plat, env, h = _plat(platform), _env(environ), _home(home)
    if plat == "win32":
        return user_local_root(plat, env, h) / "Cache"
    if plat == "darwin":
        return h / "Library" / "Caches" / APP_NAME
    xdg = env.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else h / ".cache") / APP_NAME


def data_dir(source_base, frozen=None, platform=None, environ=None, home=None):
    """Project/settings persistence directory (parent_server.DATA).

    Precedence:
      1. SLYLED_DATA — verbatim; tests and screenshot tools point it at a
         throwaway dir so they can never clobber a live operator project.
      2. Windows — %APPDATA%\\SlyLED\\data, frozen or not (pre-#948 behaviour).
      3. Frozen elsewhere — <user_data_root>/data. `source_base` would be
         PyInstaller's extraction dir, which is wiped on exit.
      4. Source checkout elsewhere — <source_base>/data, so dev runs stay
         self-contained (gitignored as desktop/shared/data/).
    """
    plat, env = _plat(platform), _env(environ)
    frozen = is_frozen() if frozen is None else frozen
    if env.get("SLYLED_DATA"):
        return Path(env["SLYLED_DATA"])
    if plat == "win32" and env.get("APPDATA"):
        return user_data_root(plat, env, home) / "data"
    if frozen:
        return user_data_root(plat, env, home) / "data"
    return Path(source_base) / "data"


def firmware_cache_dir(source_fw_dir, frozen=None, platform=None, environ=None,
                       home=None):
    """Writable cache for firmware binaries + the registry.json override
    (#568, #832). A source checkout reuses the repo firmware tree so locally
    built binaries are picked up without a download round-trip."""
    frozen = is_frozen() if frozen is None else frozen
    if not frozen:
        return Path(source_fw_dir)
    return user_data_root(platform, environ, home) / "firmware"


def runtime_dir(platform=None, environ=None, home=None):
    """Root for optional add-on runtimes (depth venv + weights)."""
    return user_local_root(platform, environ, home) / "runtimes"
