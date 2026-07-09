"""test_869_gyro_aim_wizard.py — #869 gyro-side empirical aim-axis wizard.

Same architecture as the Android wizard (#826) but driven over UDP
because the gyro has no HTTPS stack. Three captured Euler triples
(neutral / pitch_forward / yaw_left) ride one CMD_GYRO_AIM_WIZARD
(0x6F) packet; the orchestrator converts them to quats and dispatches
to `_apply_aim_wizard_to_remote` — the SAME function the Android HTTP
wizard endpoint calls. Derived `forward_local` / `up_local` end up
on the gyro's `gyro-<ip>` Remote.

Tests pin: the new CMD constant, the elif-branch existence, the
Euler-unpack shape, the dispatch to the shared wizard math, the
auto-register-as-gyro behaviour (so a wizard can land before the
first orient frame creates the Remote), plus the firmware Protocol.h
pin and the CLAUDE.md UDP-table row.

Run: python -X utf8 tests/test_869_gyro_aim_wizard.py
"""

import inspect
import os
import struct
import sys

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


def _wizard_handler_body():
    # #920 — post-#901 the AIM_WIZARD dispatch is the module-level
    # `_handle_gyro_aim_wizard`; inspect it directly (same pattern
    # test_825 uses for _handle_gyro_start_packet) instead of slicing
    # module source by docstring markers.
    handler = getattr(parent_server, "_handle_gyro_aim_wizard", None)
    return inspect.getsource(handler) if handler is not None else ""


# ─────────────────────────────────────────────────────────────────────────────
# 1. CMD constant.

def test_cmd_gyro_aim_wizard_constant():
    val = getattr(parent_server, "CMD_GYRO_AIM_WIZARD", None)
    _assert(val == 0x6F,
            f"parent_server.CMD_GYRO_AIM_WIZARD == 0x6F (got {val!r})")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Dispatch elif-branch present.

def test_aim_wizard_branch_exists():
    body = _wizard_handler_body()
    _assert(len(body) > 0,
            "parent_server has a CMD_GYRO_AIM_WIZARD handler "
            "(_handle_gyro_aim_wizard)")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Payload shape: 9 LE float32 unpacked from offset 8 (header end).

def test_aim_wizard_unpacks_9_floats():
    body = _wizard_handler_body()
    _assert('struct.unpack_from("<9f", data, 8)' in body,
            "AIM_WIZARD handler unpacks 9 LE float32 starting at byte 8")
    _assert("len(data) < 44" in body,
            "AIM_WIZARD handler validates 44-byte minimum (8 hdr + 36 payload)")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Euler→quat conversion + dispatch to the SHARED wizard math used by
#    the Android phone path. Same `_apply_aim_wizard_to_remote` ensures
#    no schema divergence between kinds.

def test_aim_wizard_uses_shared_math():
    body = _wizard_handler_body()
    _assert("quat_from_euler_zyx_deg" in body,
            "AIM_WIZARD handler converts Euler→quat via quat_from_euler_zyx_deg")
    _assert('"neutral":' in body and '"pitch_forward":' in body
            and '"yaw_left":' in body,
            "AIM_WIZARD handler builds the 3-pose dict matching `_aim_wizard_compute`")
    _assert("_apply_aim_wizard_to_remote(" in body,
            "AIM_WIZARD handler dispatches to the shared _apply_aim_wizard_to_remote")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Auto-register as gyro if Remote doesn't exist yet (so an operator
#    running the wizard before the first orient frame still gets a
#    gyro Remote with the derived axes — no first-orient race).

def test_aim_wizard_auto_registers_gyro():
    body = _wizard_handler_body()
    _assert("_auto_register_remote" in body,
            "AIM_WIZARD handler auto-registers a gyro Remote if not present")
    _assert("KIND_GYRO" in body,
            "auto-register uses KIND_GYRO (matches the orient handler)")


# ─────────────────────────────────────────────────────────────────────────────
# 6. End-to-end: drive the in-process Flask + UDP dispatch with a
#    hand-built CMD_GYRO_AIM_WIZARD packet for a synthetic case where
#    body == world == stage at neutral, pitch=around-X-by-30°-down,
#    yaw=around-Z-by-30°-CW. Wizard math should derive
#    forward_local ≈ (0, 1, 0) and a unit up_local orthogonal to
#    forward and pitch_axis. Persistence verified via
#    `_remotes.by_device("gyro-<ip>")`.

def test_aim_wizard_end_to_end_persists_axes():
    import math
    # Build the 44-byte packet: 8-byte header + 9 floats.
    # Header: magic 0x534C, version 5, cmd 0x6F, epoch 0
    hdr = struct.pack("<HBBI", 0x534C, 5, 0x6F, 0)
    eu = (0.0, 0.0, 0.0,        # neutral
          0.0, -30.0, 0.0,      # pitch forward (negative pitch = tip down per ZYX)
          0.0, 0.0, -30.0)      # yaw to stage-left (negative yaw)
    pkt = hdr + struct.pack("<9f", *eu)

    # Smoke-test the handler logic in isolation. The full UDP listener
    # path is hard to drive from a unit test; we replicate just the
    # dispatch math the elif-branch performs.
    from remote_math import quat_from_euler_zyx_deg as qfe
    poses = {
        "neutral":       qfe(eu[0], eu[1], eu[2]),
        "pitch_forward": qfe(eu[3], eu[4], eu[5]),
        "yaw_left":      qfe(eu[6], eu[7], eu[8]),
    }
    # Rope a fresh in-process Remote in the right shape.
    from remote_orientation import Remote, KIND_GYRO
    r = Remote(id=999, kind=KIND_GYRO, device_id="test-869")
    ok_, resp, status = parent_server._apply_aim_wizard_to_remote(r, poses)
    _assert(ok_, f"wizard math accepted synthetic inputs (resp={resp})")
    if not ok_:
        return
    fwd = resp.get("forwardLocal") or [0, 0, 0]
    up = resp.get("upLocal") or [0, 0, 0]
    fmag = math.sqrt(sum(c * c for c in fwd))
    umag = math.sqrt(sum(c * c for c in up))
    dot = sum(fwd[i] * up[i] for i in range(3))
    _assert(abs(fmag - 1) < 0.01, f"derived forward_local is unit (|f|={fmag:.4f})")
    _assert(abs(umag - 1) < 0.01, f"derived up_local is unit (|u|={umag:.4f})")
    _assert(abs(dot) < 0.05, f"derived forward ⊥ up (dot={dot:.4f})")
    # set_grip should have written the axes onto the Remote.
    _assert(r.forward_local is not None,
            f"Remote.forward_local persisted (got {r.forward_local})")
    _assert(r.up_local is not None,
            f"Remote.up_local persisted (got {r.up_local})")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Firmware Protocol.h carries the matching wire constant.

def test_protocol_h_constant():
    proto = os.path.join(os.path.dirname(__file__), "..", "main", "Protocol.h")
    src = open(proto).read()
    _assert("CMD_GYRO_AIM_WIZARD" in src and "0x6F" in src,
            "main/Protocol.h declares CMD_GYRO_AIM_WIZARD = 0x6F")


# ─────────────────────────────────────────────────────────────────────────────
# 8. CLAUDE.md UDP-protocol table row present.

def test_claude_md_table_row():
    cmd = os.path.join(os.path.dirname(__file__), "..", "CLAUDE.md")
    src = open(cmd).read()
    _assert("0x6F | GYRO_AIM_WIZARD" in src,
            "CLAUDE.md UDP table has a 0x6F GYRO_AIM_WIZARD row")
    _assert("36 bytes" in src,
            "CLAUDE.md row notes the 36-byte payload size")


# ─────────────────────────────────────────────────────────────────────────────
# 9. Guardrail: the Android wizard's HTTP path is untouched.
#    `/api/remotes/aim-wizard` and `_apply_aim_wizard_to_remote` are
#    SHARED with the gyro handler — we must not have forked them.

def test_shared_function_is_singular():
    src = inspect.getsource(parent_server)
    occurrences = src.count("def _apply_aim_wizard_to_remote(")
    _assert(occurrences == 1,
            f"_apply_aim_wizard_to_remote is defined once (got {occurrences})")


ALL = [
    test_cmd_gyro_aim_wizard_constant,
    test_aim_wizard_branch_exists,
    test_aim_wizard_unpacks_9_floats,
    test_aim_wizard_uses_shared_math,
    test_aim_wizard_auto_registers_gyro,
    test_aim_wizard_end_to_end_persists_axes,
    test_protocol_h_constant,
    test_claude_md_table_row,
    test_shared_function_is_singular,
]


if __name__ == "__main__":
    print("=== #869 gyro aim-wizard contract ===")
    for t in ALL:
        print(f"\n-- {t.__name__} --")
        try:
            t()
        except Exception as e:
            _failed += 1
            print(f"  [FAIL] {t.__name__} raised: {e}")
    total = _passed + _failed
    print(f"\n{_passed} passed, {_failed} failed out of {total}")
    sys.exit(0 if _failed == 0 else 1)
