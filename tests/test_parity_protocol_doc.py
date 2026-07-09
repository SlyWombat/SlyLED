#!/usr/bin/env python3
"""#903 — executable parity gate: CLAUDE.md's UDP protocol table vs the
firmware headers.

main/Protocol.h is the source of truth for the fleet CMD_ codes and
mmwave/MmwProtocol.h owns the 0x7x range (its fleet-shared subset is
separately parity-checked by tests/test_mmwave_wire_parity.py). CLAUDE.md
documents the same table by hand — this test parses both sides and fails
the suite if they drift again:

  1. Every `CMD_<NAME> = 0x..` constant in the headers must appear in
     CLAUDE.md's table as a `| 0x.. | <NAME> | ...` row with the matching
     hex code (table names drop the `CMD_` prefix).
  2. Every table row must correspond to a header constant with the same
     hex code — no phantom/renamed rows.

The payload/direction columns are free-form documentation and are NOT
checked — the table may carry doc-only annotations there.

Pure text parsing — no compiler needed. Run:
    python3 tests/test_parity_protocol_doc.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
HEADERS = [ROOT / "main" / "Protocol.h", ROOT / "mmwave" / "MmwProtocol.h"]

CMD_RE = re.compile(r"constexpr\s+uint8_t\s+CMD_(\w+)\s*=\s*(0x[0-9A-Fa-f]{2})\s*;")
ROW_RE = re.compile(r"^\|\s*(0x[0-9A-Fa-f]{2})\s*\|\s*([A-Z][A-Z0-9_]*)\s*\|", re.M)


def parse_headers():
    """{name: code} across both headers; duplicated names must agree."""
    cmds = {}
    for hdr in HEADERS:
        text = hdr.read_text(encoding="utf-8")
        found = CMD_RE.findall(text)
        if not found:
            raise AssertionError(f"no CMD_ constants parsed from {hdr}")
        for name, hexcode in found:
            code = int(hexcode, 16)
            if name in cmds and cmds[name] != code:
                raise AssertionError(
                    f"CMD_{name} disagrees across headers: "
                    f"0x{cmds[name]:02X} vs 0x{code:02X} ({hdr.name})")
            cmds[name] = code
    return cmds


def parse_table():
    """{name: code} from CLAUDE.md's protocol table rows."""
    text = CLAUDE_MD.read_text(encoding="utf-8")
    rows = {}
    for hexcode, name in ROW_RE.findall(text):
        code = int(hexcode, 16)
        if name in rows and rows[name] != code:
            raise AssertionError(
                f"CLAUDE.md lists {name} twice with different codes: "
                f"0x{rows[name]:02X} vs 0x{code:02X}")
        rows[name] = code
    if not rows:
        raise AssertionError("no protocol-table rows parsed from CLAUDE.md")
    return rows


def main():
    cmds = parse_headers()
    rows = parse_table()
    failures = []

    # 1. Header constant → table row, matching hex.
    for name, code in sorted(cmds.items(), key=lambda kv: kv[1]):
        if name not in rows:
            failures.append(
                f"CMD_{name} (0x{code:02X}) missing from CLAUDE.md table")
        elif rows[name] != code:
            failures.append(
                f"CMD_{name}: header says 0x{code:02X}, "
                f"CLAUDE.md table says 0x{rows[name]:02X}")

    # 2. Table row → header constant, matching hex.
    for name, code in sorted(rows.items(), key=lambda kv: kv[1]):
        if name not in cmds:
            failures.append(
                f"CLAUDE.md row 0x{code:02X} {name} has no CMD_{name} in the headers")
        # hex mismatch already reported in pass 1 for shared names

    print(f"Protocol-doc parity: headers={len(cmds)} CMD codes, "
          f"CLAUDE.md table={len(rows)} rows")
    if failures:
        print(f"\nFAILED ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("CLAUDE.md UDP protocol table matches main/Protocol.h + mmwave/MmwProtocol.h.")


if __name__ == "__main__":
    main()
