#!/usr/bin/env python3
"""test_firmware_platform.py — #948 Phase 0.4 / 0.5: firmware_manager's
OS-dependent pieces.

  * detect_chip() runs esptool in-process (the old `sys.executable -m
    esptool` subprocess was dead in every frozen build). esptool's
    detect_chip is monkeypatched — no serial hardware needed.
  * _find_arduino_cli() checks per-OS install locations before PATH, and
    the not-found message names the OS's installer.

Run:
    python3 tests/test_firmware_platform.py
"""

import os
import stat
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "desktop", "shared"))

import firmware_manager as fm  # noqa: E402

results = []


def ok(name, cond, detail=""):
    results.append((name, bool(cond), detail))


def eq(name, got, want):
    ok(name, got == want, f"got {got!r}, want {want!r}")


class _FakePort:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakeLoader:
    def __init__(self, chip):
        self.CHIP_NAME = chip
        self._port = _FakePort()
        self.reset = False

    def hard_reset(self):
        print("Hard resetting via RTS pin...")  # must be swallowed
        self.reset = True


def run():
    # ── chip name → board id ─────────────────────────────────────────────
    eq("ESP32-S3 -> esp32s3", fm._chip_to_board("ESP32-S3"), "esp32s3")
    eq("ESP32 -> esp32", fm._chip_to_board("ESP32"), "esp32")
    eq("ESP8266 -> d1mini", fm._chip_to_board("ESP8266"), "d1mini")
    eq("unknown -> None", fm._chip_to_board("RP2040"), None)

    # ── detect_chip in-process (esptool.cmds.detect_chip mocked) ─────────
    try:
        import esptool.cmds as ecmds
    except Exception:
        ecmds = None
    ok("esptool importable (in requirements)", ecmds is not None)
    if ecmds is not None:
        saved = ecmds.detect_chip
        loaders = []
        try:
            def fake(port, **kw):
                print("Connecting....")  # must not leak to stdout
                ld = _FakeLoader({"/dev/s3": "ESP32-S3", "/dev/d1": "ESP8266"}[port])
                loaders.append((port, kw, ld))
                return ld
            ecmds.detect_chip = fake
            eq("detect_chip ESP32-S3 port", fm.detect_chip("/dev/s3"), "esp32s3")
            eq("detect_chip ESP8266 port", fm.detect_chip("/dev/d1"), "d1mini")
            ok("port closed after detect", all(ld._port.closed for _, _, ld in loaders))
            ok("board hard-reset out of the bootloader", all(ld.reset for _, _, ld in loaders))
            ok("no subprocess: called with the port directly",
               [p for p, _, _ in loaders] == ["/dev/s3", "/dev/d1"])

            def boom(port, **kw):
                raise RuntimeError("Failed to connect to Espressif device")
            ecmds.detect_chip = boom
            eq("detect_chip failure -> None", fm.detect_chip("/dev/none"), None)
        finally:
            ecmds.detect_chip = saved

    # ── arduino-cli candidates per OS ────────────────────────────────────
    home = Path("/home/op")
    win = fm._arduino_cli_candidates("win32", {"LOCALAPPDATA": "/L", "ProgramFiles": "/PF"}, home)
    eq("win32 candidates", win,
       [Path("/L/Arduino/arduino-cli.exe"), Path("/PF/Arduino CLI/arduino-cli.exe")])
    mac = fm._arduino_cli_candidates("darwin", {}, home)
    eq("darwin checks Homebrew (Apple Silicon) first", mac[0], Path("/opt/homebrew/bin/arduino-cli"))
    ok("darwin checks Intel Homebrew", Path("/usr/local/bin/arduino-cli") in mac)
    lin = fm._arduino_cli_candidates("linux", {}, home)
    ok("linux checks ~/.local/bin", home / ".local/bin/arduino-cli" in lin)

    ok("win32 hint names winget", "winget" in fm.arduino_cli_install_hint("win32"))
    ok("darwin hint names brew", "brew install arduino-cli" in fm.arduino_cli_install_hint("darwin"))
    ok("linux hint does not name winget", "winget" not in fm.arduino_cli_install_hint("linux"))

    # ── _find_arduino_cli: candidate beats PATH; PATH used otherwise ─────
    saved_c = fm._arduino_cli_candidates
    saved_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as d:
        exe_name = "arduino-cli.exe" if sys.platform == "win32" else "arduino-cli"
        cand = Path(d) / "cand" / exe_name
        onpath = Path(d) / "bin" / exe_name
        for p in (cand, onpath):
            p.parent.mkdir()
            p.write_text("#!/bin/sh\n")
            p.chmod(p.stat().st_mode | stat.S_IXUSR)
        try:
            os.environ["PATH"] = str(onpath.parent)
            fm._arduino_cli_candidates = lambda: [cand]
            eq("well-known location preferred", fm._find_arduino_cli(), str(cand))
            fm._arduino_cli_candidates = lambda: [Path(d) / "missing" / exe_name]
            ok("falls back to PATH",
               fm._find_arduino_cli() and Path(fm._find_arduino_cli()) == onpath,
               fm._find_arduino_cli())
            os.environ["PATH"] = str(Path(d) / "empty")
            eq("nowhere -> None", fm._find_arduino_cli(), None)
        finally:
            fm._arduino_cli_candidates = saved_c
            os.environ["PATH"] = saved_path


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
