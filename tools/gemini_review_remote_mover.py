#!/usr/bin/env python3
"""Focused Gemini review of the remote→mover control pipeline.

Targets the recurring "operator presses Start, beam doesn't move / claim
doesn't release / DMX dances" bug class. Modeled on
gemini_review_mover_cal_v2.py: pre-feeds Gemini the issues that have already
been filed/fixed so it does NOT spend budget rediscovering known bugs, and
asks for triage-shaped output (Severity / file:line / reproduction / fix).

Coverage:
  - Puck firmware (main/Gyro*.{h,cpp}, Protocol.h)
  - Server: claim_arbiter, mover_control, remote_orientation, remote_math,
    spatial_engine — full files
  - parent_server.py — only the gyro/remote/claim subset (UDP packet
    handlers, /api/gyros, /api/mover-control/*, /api/remotes/*)
  - Tests that encode the contract (#825 handshake, claim ownership,
    DMX integration, orphan prune)

Output: tools/gyro_remote_review.md

Usage:
    # Either set GEMINI_API_KEY / GOOGLE_API_KEY in env or .env, or:
    python tools/gemini_review_remote_mover.py --key AIza...
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("ERROR: google-genai not installed. Run: pip install google-genai",
          file=sys.stderr)
    sys.exit(1)

ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Source assembly
# ---------------------------------------------------------------------------

FULL_FILES = [
    # Server — claim/orient/control pipeline
    'desktop/shared/claim_arbiter.py',
    'desktop/shared/mover_control.py',
    'desktop/shared/remote_orientation.py',
    'desktop/shared/remote_math.py',
    'desktop/shared/spatial_engine.py',
    # Puck firmware
    'main/Protocol.h',
    'main/GyroBoard.h',
    'main/GyroIMU.h',
    'main/GyroIMU.cpp',
    'main/GyroDisplay.h',
    'main/GyroDisplay.cpp',
    'main/GyroTouch.h',
    'main/GyroTouch.cpp',
    'main/GyroUdp.h',
    'main/GyroUdp.cpp',
    'main/GyroUI.h',
    'main/GyroUI.cpp',
    # GyroLogo.h intentionally omitted — it's a generated bitmap, not logic.
    # Tests that encode the contract under review
    'tests/test_825_gyro_handshake.py',
    'tests/test_claim_ownership_contract.py',
    'tests/test_gyro_dmx_integration.py',
    'tests/test_remote_orphan_prune.py',
]

# Subset of parent_server.py — only the gyro/remote/claim handlers.
# Each tuple is (start_line, end_line_inclusive, label).
PARENT_RANGES = [
    (503, 600,
     "gyro state + helpers: _gyro_fixture_for_ip, _gyro_assigned_mover_id, "
     "_gyro_device_name, _gyro_child_ip_for_fixture, _gyro_send_release_packet, "
     "_gyro_inactive_transition (#801), _apply_gyro_color"),
    (1286, 1748,
     "_udp_listener — CMD_GYRO_START/STOP/HEARTBEAT_REP/ORIENT processing, "
     "claim handshake, orphan-claim 1.5s timer, #819 bit-3 retire, "
     "#825 nonce echo path"),
    (1750, 1860,
     "_send_gyro_claim_denied, _send_gyro_claim_ack, _send_gyro_stop_ack, "
     "_mark_gyro_armed (#825 armer)"),
    (3023, 3110,
     "api_gyro_state, _gyro_child_ip, api_gyro_enable, api_gyro_disable"),
    (11328, 11700,
     "/api/mover-control/* — claim, release, start, calibrate-start, "
     "calibrate-end, orient (compat), color, smoothing, flash"),
    (11697, 12020,
     "_auto_register_remote + /api/remotes/* — list, create, update, grip, "
     "grip-by-device, delete, live, disconnect, diagnostic, "
     "calibrate-start/end, clear-stale, end-session, orient"),
]

PARENT_SERVER_PATH = ROOT / 'desktop' / 'shared' / 'parent_server.py'


def _read_file(rel_path: str) -> str | None:
    p = ROOT / rel_path
    if not p.exists():
        print(f"  [skip] {rel_path} — not found", file=sys.stderr)
        return None
    return p.read_text(encoding='utf-8', errors='replace')


def _read_ranges(path: Path, ranges: list[tuple[int, int, str]]) -> str:
    lines = path.read_text(encoding='utf-8').splitlines()
    out: list[str] = []
    for start, end, label in ranges:
        out.append(f"\n# ─── {label}\n# ─── lines {start}-{end} of {path.name}\n")
        out.extend(lines[start - 1:end])
    return "\n".join(out)


def _resolve_api_key(cli_key: str | None) -> str:
    if cli_key:
        return cli_key
    env_path = ROOT / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                os.environ.setdefault(k.strip(), v.strip())
    key = os.environ.get('GEMINI_API_KEY') or os.environ.get('GOOGLE_API_KEY')
    if not key:
        sys.exit(
            "ERROR: no Gemini API key. Pass --key or set GEMINI_API_KEY / "
            "GOOGLE_API_KEY in env or .env."
        )
    return key


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

CLOSED_ISSUES = """\
The following issues have already been **filed and addressed** in this code.
Do NOT re-report them. Use them only as context for what the recurring bug
class looks like.

- **#802** — `mover_engine` binding bug exposed wrong `engineType` in status.
  Fixed in `mover_control.py` (status now reports engine kind correctly).
- **#813** — gyro lock removed; orient handler is no longer a claim source.
  CMD_GYRO_START is the sole claim trigger.
- **#819** — split CMD_GYRO_STOP (0x69) from orient bit-3. Bit-3-on-orient was
  auto-releasing the live claim ~25 ms after CLAIM_ACK on legacy puck firmware.
  Bit-3 is now reserved-and-logged; STOP is a discrete command.
- **#823** — press-Start now clears `remote.stale_reason` so reclaim isn't
  blocked by a stuck stale flag.
- **#825** — nonce-based claim/stop handshake. CMD_GYRO_START carries a 16-bit
  nonce; orchestrator replies CMD_GYRO_CLAIM_ACK with the same nonce + moverId.
  Puck advances UI only on matching ACK; CLAIM_DENIED reverts to IDLE; ~1.5 s
  total timeout reverts with "NO RESPONSE". CMD_GYRO_STOP also carries a nonce
  and is ACKed via CMD_GYRO_STOP_ACK. CMD_GYRO_HEARTBEAT_REP carries
  `uiState + claimNonce + seq` so divergent state is reconciled (puck IDLE +
  server claim → release; puck ACTIVE + no server claim → reconstruct).
- **#826** — open. Android empirical aim-axis wizard replacing #816 grip publish
  + #824 qz-negate. Three-pose wizard measuring forward_local / up_local.
  Mentioned because: the orient pipeline currently has a sign-error class that
  was masked by the now-deleted bit-3 path; any *new* sign-error finding in
  remote_orientation.py / remote_math.py / mover_control.py is in-scope.
"""

PROMPT = """\
You are a senior embedded + Python systems engineer doing a third-party review
of the **remote → moving-head control pipeline** in SlyLED, an open-source
DMX/Art-Net lighting platform.

# Architecture (high-level)

```
Puck (ESP32-S3, BNO055 IMU)
  ↓ UDP :4210 binary protocol v5 — CMD_GYRO_START / STOP / ORIENT (16) /
  ↓                                CMD_GYRO_HEARTBEAT_REP / CALIBRATE / BATT
Orchestrator (Flask, parent_server.py)
  → ClaimArbiter.claim(mover_id, device_id, ttl)
  → MoverControlEngine.start_stream / orient / release
  → spatial_engine.world_to_fixture_pt → DMX (Art-Net / sACN out)
```

The puck press-Start sends a fresh 16-bit nonce; orchestrator must reply with
CLAIM_ACK echoing the nonce, then arm a 1.5 s orphan-claim timer that releases
the claim if no orient packet arrives. Press-Stop carries a nonce and is
ACKed. A 2 s heartbeat exchanges UI state + claim nonce so divergent state is
reconciled either direction (puck IDLE + server claim → release; puck ACTIVE +
no server claim → reconstruct, the orchestrator-restart bootstrap path).

# What's already been fixed (do NOT re-report)

{closed_issues}

# What I want from this review

The operator's framing is "**continued issues controlling moving heads with
remotes**" — so the recurring bug class is what matters, not stylistic
nitpicks.

## §1. Latent bugs in the claim/orient/release pipeline

Find NEW bugs (i.e. **not** in the closed-issues list above). Per finding,
output:

  **Severity** (P1 must-fix / P2 should-fix / P3 nice-to-have)
  **file:line**
  **Reproduction** (concrete operator steps or packet sequence)
  **Fix** (specific edit, not a vague suggestion)

Concentrate on:

  1. **Claim release-leak paths** — every exit branch of the orient handler,
     every error path in `_udp_listener`, every `_set_calibrating(...)` /
     `MoverControlEngine.release(...)` pair. The pattern that has burned us
     repeatedly: a claim is taken on Start but a non-happy-path code branch
     returns before the matching release.

  2. **Race conditions** —
       a. CMD_GYRO_START handler vs. the orphan-claim 1.5 s timer fire.
       b. CMD_GYRO_STOP handler vs. an in-flight orient packet from the same
          puck (UDP reordering is real on shared WiFi).
       c. /api/remotes/<id>/disconnect or /clear-stale racing the heartbeat
          reconcile path.
       d. `_gyro_inactive_transition` racing a reclaim from the same puck.
       e. ClaimArbiter timeout vs. heartbeat refresh.

  3. **Sign-error / convention bugs** in the orient path —
     `remote_orientation.py`, `remote_math.py`, the puck's `GyroIMU.cpp` /
     `GyroUdp.cpp` orient packing. The open #826 work explicitly suspects a
     latent sign error here, masked previously by the deleted bit-3 branch.
     Look for: quaternion handedness assumptions (qx,qy,qz,qw vs.
     w-first), axis-order assumptions (X-forward vs. Y-forward), mismatched
     pan/tilt sign conventions between firmware and server, body-frame vs.
     stage-frame composition order. CLAUDE.md spells out the canonical
     conventions:
       - rotation: rx=pitch about X, ry=roll about Y(stage-forward),
         rz=yaw about Z(stage-up). rz>0 → toward +X (stage-left).
       - panDeg>0 → beam toward +X. tiltDeg>0 → beam above horizon (+Z).
       - Canonical: `coverage_math.world_to_fixture_pt` —
         tilt_deg=atan2(mz, hypot(mx,my)), pan_deg=atan2(mx,my).
     Anything that produces (panDeg, tiltDeg) must round-trip with that.

  4. **Protocol drift** — Protocol.h vs. parent_server.py packet parsing.
     Wire struct widths, endianness (`<` little-endian assumed), payload
     length checks, behaviour when an older puck (pre-#825) sends a
     header-only START/STOP, behaviour when a newer puck talks to an older
     orchestrator (forward-compat).

  5. **Heartbeat reconcile correctness** — the bidirectional reconcile
     (`uiState + claimNonce`) is subtle. What happens if a HB_REP arrives
     during the orphan-claim 1.5 s window? What if two pucks share an IP
     transiently after DHCP? What if `claimNonce==0` (the "no claim" sentinel)
     collides with a legitimate nonce that happens to be 0?

  6. **Orphan-prune lifecycle** — `test_remote_orphan_prune.py` exists for a
     reason; verify the prune and the registration path don't tag-team into a
     "stuck-stale" loop that #823 was meant to break.

## §2. Verification of the §825 invariants

Walk the actual code paths and confirm or refute each invariant. Cite
file:line for every claim.

  (a) Press-Start with nonce N → server replies CLAIM_ACK(N, mover) iff claim
      succeeds; otherwise CLAIM_DENIED.
  (b) Puck UI advances ONLY on matching nonce — i.e. server cannot induce a
      stale-nonce ACK that fakes the puck into ACTIVE.
  (c) Server arms a 1.5 s orphan timer after CLAIM_ACK; first orient cancels
      it; on fire, claim is released.
  (d) Press-Stop with nonce M → server replies STOP_ACK(M); claim released
      regardless of whether ACK arrives.
  (e) Heartbeat reconcile: puck IDLE + server claim ⇒ release;
      puck ACTIVE + server no-claim ⇒ reconstruct (bootstrap).
  (f) Legacy header-only START/STOP from pre-#825 puck still works.
  (g) ACK never crosses puck boundaries — i.e. an ACK from a START on puck A
      cannot arrive at puck B and grant it a claim.

If any invariant is *not* enforced by the code as written, that's a P1 finding
in §1.

## §3. Cross-file invariant disagreements

Anywhere puck firmware and server disagree about wire format, struct width,
field order, units (deg vs. rad, mm vs. m, signed vs. unsigned), or claim
state machine. One specific concern: `Protocol.h` defines the C++ struct
layout; the Python `struct.pack` / `unpack` calls in `parent_server.py` and
`remote_orientation.py` must mirror it byte-for-byte. Flag any divergence.

## §4. Top 5 prioritised fixes

End with the top 5 NEW findings to fix first. For each, one line:
`P<n> – file:line – one-line description` (n is 1, 2, or 3). Concrete enough
that the operator can open the file and apply the change without re-reading
the full review.

---

# Source under review

{source}
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gemini code review of the remote→mover control pipeline"
    )
    parser.add_argument('--key', help='Gemini API key (else GEMINI_API_KEY / '
                                      'GOOGLE_API_KEY env or .env)')
    parser.add_argument('--out', default='tools/gyro_remote_review.md',
                        help='Output markdown path (default: tools/gyro_remote_review.md)')
    parser.add_argument('--model', default='gemini-2.5-pro',
                        help='Gemini model (default: gemini-2.5-pro)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Build the prompt and print sizes, do NOT call API')
    args = parser.parse_args()

    print("Loading source files...", file=sys.stderr)
    blocks: list[str] = []
    total_chars = 0
    for rel in FULL_FILES:
        content = _read_file(rel)
        if content is None:
            continue
        block = f"\n### {rel}\n```\n{content}\n```\n"
        blocks.append(block)
        total_chars += len(content)
        print(f"  [+] {rel} ({len(content):,} chars)", file=sys.stderr)

    print(f"  ··· parent_server.py — extracting {len(PARENT_RANGES)} ranges",
          file=sys.stderr)
    parent_subset = _read_ranges(PARENT_SERVER_PATH, PARENT_RANGES)
    blocks.append(
        f"\n### desktop/shared/parent_server.py (gyro/remote subset — "
        f"{len(parent_subset):,} chars)\n```python\n{parent_subset}\n```\n"
    )
    total_chars += len(parent_subset)
    print(f"  [+] parent_server.py subset ({len(parent_subset):,} chars)",
          file=sys.stderr)

    source_text = "".join(blocks)
    prompt = PROMPT.format(closed_issues=CLOSED_ISSUES, source=source_text)
    print(f"\nTotal source: {total_chars:,} chars", file=sys.stderr)
    print(f"Total prompt: {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)",
          file=sys.stderr)

    if args.dry_run:
        print("--dry-run: not calling API", file=sys.stderr)
        return

    api_key = _resolve_api_key(args.key)

    client = genai.Client(api_key=api_key)
    print(f"\nSending to {args.model}…", file=sys.stderr)
    response = client.models.generate_content(
        model=args.model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=32768,
        ),
    )
    review = response.text

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import datetime
    header = (
        f"# Gemini Review — Remote → Moving-Head Control Pipeline\n\n"
        f"_Generated {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"via `tools/gemini_review_remote_mover.py`._\n\n"
        f"**Model:** {args.model}  \n"
        f"**Source:** {total_chars:,} chars across "
        f"{len(FULL_FILES)} full files + parent_server.py subset  \n"
        f"**Closed issues pre-fed:** #802, #813, #819, #823, #825 "
        f"(open: #826)\n\n---\n\n"
    )
    out_path.write_text(header + review, encoding='utf-8')
    print(f"\nSaved to {out_path.relative_to(ROOT)}", file=sys.stderr)
    print("---")
    print(review)


if __name__ == '__main__':
    main()
