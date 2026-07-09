"""
test_mmwave_wire_parity.py — #906/#910: the MMwave sketch (mmwave/MmwProtocol.h)
duplicates wire definitions from main/Protocol.h by design (isolated toolchain,
no cross-tree includes). This test fails the suite if the two headers drift.

Checks:
  1. Shared constants (magic, version, port, name lengths, shared CMD codes)
     have identical values in both headers.
  2. Shared packed structs (UdpHeader, PongString, PongPayload,
     StatusRespPayload) have identical field sequences (type + name + array).
  3. The MMwave 0x7x command codes don't collide with any command main/ defines.

Pure text parsing — no compiler needed. Run: python3 tests/test_mmwave_wire_parity.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN_H = ROOT / "main" / "Protocol.h"
MMW_H = ROOT / "mmwave" / "MmwProtocol.h"

SHARED_CONSTANTS = [
    "HOSTNAME_LEN", "CHILD_NAME_LEN", "CHILD_DESC_LEN", "MAX_STR_PER_CHILD",
    "UDP_PORT", "UDP_MAGIC", "UDP_VERSION",
    "CMD_PING", "CMD_PONG", "CMD_STATUS_REQ", "CMD_STATUS_RESP",
    "CMD_OTA_UPDATE", "CMD_OTA_STATUS",
]

SHARED_STRUCTS = ["UdpHeader", "PongString", "PongPayload", "StatusRespPayload"]

CONST_RE = re.compile(
    r"constexpr\s+\w+(?:_t)?\s+(\w+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*;"
)


def parse_constants(text):
    return {m.group(1): int(m.group(2), 0) for m in CONST_RE.finditer(text)}


def parse_struct_fields(text, name):
    m = re.search(
        r"struct\s+__attribute__\(\(packed\)\)\s+" + name + r"\s*\{(.*?)\};",
        text,
        re.DOTALL,
    )
    if not m:
        return None
    fields = []
    for line in m.group(1).splitlines():
        line = line.split("//")[0].strip()
        fm = re.match(r"(\w+)\s+(\w+)(\[\w+\])?\s*(?:,\s*[\w\s,\[\]]+)?;", line)
        if not fm:
            continue
        # Expand comma-separated declarations like "uint8_t r, g, b;"
        decl = line.rstrip(";")
        parts = decl.split(None, 1)
        ftype, rest = parts[0], parts[1]
        for piece in rest.split(","):
            piece = piece.strip()
            am = re.match(r"(\w+)(\[(\w+)\])?$", piece)
            if am:
                fields.append((ftype, am.group(1), am.group(3) or ""))
    return fields


def main():
    failures = []
    main_text = MAIN_H.read_text(encoding="utf-8")
    mmw_text = MMW_H.read_text(encoding="utf-8")
    main_consts = parse_constants(main_text)
    mmw_consts = parse_constants(mmw_text)

    # 1. Shared constants agree
    for name in SHARED_CONSTANTS:
        if name not in main_consts:
            failures.append(f"main/Protocol.h no longer defines {name}")
        elif name not in mmw_consts:
            failures.append(f"mmwave/MmwProtocol.h is missing {name}")
        elif main_consts[name] != mmw_consts[name]:
            failures.append(
                f"{name} drifted: main={main_consts[name]:#x} "
                f"mmwave={mmw_consts[name]:#x}"
            )

    # 2. Shared struct layouts agree
    for sname in SHARED_STRUCTS:
        mf = parse_struct_fields(main_text, sname)
        wf = parse_struct_fields(mmw_text, sname)
        if mf is None:
            failures.append(f"struct {sname} missing from main/Protocol.h")
            continue
        if wf is None:
            failures.append(f"struct {sname} missing from mmwave/MmwProtocol.h")
            continue
        if mf != wf:
            failures.append(
                f"struct {sname} drifted:\n  main:   {mf}\n  mmwave: {wf}"
            )

    # 3. MMwave 0x7x codes don't collide with anything main/ defines
    main_cmds = {v for k, v in main_consts.items() if k.startswith("CMD_")}
    for k, v in mmw_consts.items():
        if k.startswith("CMD_MMW_") and v in main_cmds:
            failures.append(
                f"{k}={v:#x} collides with a command already defined in main/Protocol.h"
            )

    # Sanity: the payload the orchestrator will parse has the documented size.
    # 2 (seq) + 1 (count) + 1 (flags) + 3 targets x 8 bytes = 28.
    tgt = parse_struct_fields(mmw_text, "MmwTarget")
    pay = parse_struct_fields(mmw_text, "MmwTargetsPayload")
    if not tgt or len(tgt) != 4:
        failures.append("MmwTarget must have exactly 4 fields (x, y, speed, res)")
    if not pay or pay[0][1] != "seq" or pay[-1][2] != "MMW_MAX_TARGETS":
        failures.append("MmwTargetsPayload layout unexpected (seq first, targets[] last)")

    if failures:
        print(f"{len(failures)} FAILED:")
        for f in failures:
            print(f"  [FAIL] {f}")
        return 1
    checks = len(SHARED_CONSTANTS) + len(SHARED_STRUCTS) + 2
    print(f"{checks} checks passed — mmwave wire definitions in parity with main/Protocol.h")
    return 0


if __name__ == "__main__":
    sys.exit(main())
