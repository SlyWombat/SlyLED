# Gyro / Puck Claim Lifecycle — Architectural Contract

**Status:** authoritative spec.
**Source of truth:** this document. Issue #813 originated the design; #819, #823, #825 added wire-protocol primitives the spec depends on. When code review or a new fix needs to make a decision about claim lifecycle, the answer must come from this document, not from a per-issue comment block.

**Scope:** the operator-driven control flow that binds an ESP32-S3 gyro puck to a moving-head fixture. Covers the press-Start handshake, the in-claim steady state, and every release path. Does **not** cover: stage-space orientation math (see `gyro-stage-space.md`), Android-phone claim flow (separate operator-driven path via REST), or the DMX-side claim arbitration between mover-control writers and Track-action writers (#763).

**Audience:** anyone modifying `desktop/shared/parent_server.py` UDP listener, `desktop/shared/mover_control.py` claim arbiter, or the `BOARD_GYRO` firmware in `main/`. Read this first; argue with the code second.

---

## 1. Operator mental model — the rock-solid principle

Quoting #813 (operator, 2026-05-05):

> "The gyro needs to be rock solid. Maybe a lock does not have to be sent from the orchestration engine every 5 seconds. When the operator presses and holds the Start button, the gyro sends a claim. If the orchestration engine has the gyro defined as Active, then it locks the moving head to the gyro."

The contract this implies, restated as the primary invariant:

> **A claim is created by an explicit operator gesture (press-Start). It survives indefinitely until another explicit operator gesture (press-Stop or fixture-Inactive toggle) ends it. The orchestrator MUST NOT release a claim on the basis of a quiet-sounding signal — silence on a wire, a paused calibrate screen, a brief network blip — because each of those is consistent with the operator legitimately using the system as designed.**

The only failure-mode release path that fires without operator action is a long all-comms-silence threshold (default 600 s = 10 min), reserved for "the puck has fallen off the network entirely" recovery. That threshold is intentionally generous; it is NOT a "is the operator paying attention" check.

### 1.1 Press-Start does immediate work — calibration is optional

Press-Start is not just "create an inert claim and wait". The operator's expectation, restated:

> **Press-Start claims the mover AND turns the lights on (if they were off). The puck's pose is taken as authoritative immediately — the head locks to whatever orientation the puck is currently in. Calibrate is a separate, optional gesture for the case the puck's chip-frame doesn't already align with the operator's intended aim direction.**

A puck the operator is already holding in the correct orientation produces useful light output the instant `CMD_GYRO_CLAIM_ACK` lands. This rules out architectures that gate output on "wait for the operator to calibrate first" or "wait for orient stream to start".

### 1.2 Press-Stop releases to whatever was driving the fixture before

Press-Stop is not "blackout the fixture". The operator's expectation, restated:

> **Press-Stop ends the claim. The fixture returns to whatever DMX writer was driving it before the gyro session began — the running timeline, an active Track action, or the #800 park-at-home idle state if nothing else was running. This matches Android-controller-mode dismiss semantics.**

Concretely, that means `_mover_engine.release(blackout=False)` so the next writer's first frame doesn't have to fight a forced dimmer-zero, AND the press-Stop path does not write any DMX itself other than what the engine release does. The #763 claim-arbiter hands control back to the next writer cleanly.

This invariant is the gravitational centre of the spec. Every wire packet, every state, every timer either supports it or is defective.

---

## 2. State machines

### 2.1 Puck UI states

| State | Display | Sends | Receives |
|---|---|---|---|
| `LOGO` | Boot splash, WiFi-connecting | nothing | nothing |
| `IDLE` | START button (green when WiFi up, yellow when not) | `CMD_GYRO_BATT` (10 s), `CMD_GYRO_HEARTBEAT_REP` (2 s) | `CMD_GYRO_HEARTBEAT` |
| `WAITING_ACK` | "CONNECT / waiting…" splash | `CMD_GYRO_START` (with retries up to 5×, 150 ms cadence), `CMD_GYRO_BATT`, `CMD_GYRO_HEARTBEAT_REP` | `CMD_GYRO_CLAIM_ACK` (advance), `CMD_GYRO_CLAIM_DENIED` (revert), timeout (revert with "NO RESP") |
| `ACTIVE` (`page = 0` Calibrate / `1` Colour / `2` Status / `3` Stop / `4` Settings) | per-page UI | `CMD_GYRO_ORIENT` (20 Hz), `CMD_GYRO_BATT`, `CMD_GYRO_HEARTBEAT_REP`, `CMD_GYRO_COLOR`, `CMD_GYRO_CALIBRATE`, `CMD_GYRO_STOP` | `CMD_GYRO_HEARTBEAT`, `CMD_GYRO_CLAIM_ACK` (idempotent retry replay) |

**Page advance on calibrate-release (firmware UX):** when the operator performs a hold-to-calibrate gesture on page 0 and releases, the firmware ships `CMD_GYRO_CALIBRATE(calibrating=0)` AND auto-advances the UI to page 1 (Colour). The calibrate page exists only to perform the gesture; once done, the operator is taken to the next useful surface. (Pre-realignment, the UI stayed on page 0 after calibrate-release — operator had to swipe manually.)
| `STOPPING` | (transient — covered by `IDLE` after press-Stop completes) | `CMD_GYRO_STOP` (with retries up to 5×, 150 ms cadence) | `CMD_GYRO_STOP_ACK` (advance to IDLE) |

`HEARTBEAT_REP` is sent every 2 s in **all** states (even IDLE) so the orchestrator can reconcile divergence — see §5.3 restart-bootstrap.

### 2.2 Orchestrator claim state

`MoverClaim` lives in `MoverControlEngine` (`desktop/shared/mover_control.py`). For a gyro-driven claim:

| Field | Value during gyro session |
|---|---|
| `mover_id` | Mover fixture id from `gf["assignedMoverId"]` |
| `device_id` | `f"gyro-{ip}"` |
| `device_name` | Resolved from fixture `name` or `gyro-{ip}` |
| `kind` | `"gyro"` |
| `state` | `"streaming"` for the lifetime of the claim (gyro pucks never sit in `claimed` — start-stream fires immediately after claim()) |
| `claimed_at` / `last_write_ts` | timestamp bookkeeping |

Three transitions are legitimate:

1. **Created** by `_mover_engine.claim(...)` + `_mover_engine.start_stream(...)` in the `CMD_GYRO_START` handler.
2. **Released** by `_mover_engine.release(...)`. Always followed by `Remote.end_session()`. See §6 for the exhaustive list of legitimate triggers.
3. **Reconstructed** by the same code path as (1), invoked from the `CMD_GYRO_HEARTBEAT_REP` orchestrator-restart-bootstrap path (§5.3) — semantically identical to a fresh press-Start.

Any other transition is a bug.

---

## 3. Wire protocol

UDP, port 4210, header is 8 bytes (`<HBBI` = magic `0x534C` + version `5` + cmd byte + epoch). Defined in `main/Protocol.h` and mirrored in `desktop/shared/parent_server.py`.

### 3.1 Packet table

| CMD | Name | Direction | Payload | Purpose | Idempotency / ACK |
|---|---|---|---|---|---|
| `0x60` | `GYRO_ORIENT` | gyro → parent | `roll100, pitch100, yaw100, fps, flags` (8 B) | Stream orientation at puck-configured rate (20 Hz default) while in ACTIVE. | None — fire-and-forget; loss tolerated, server uses last-known. |
| `0x61` | `GYRO_CTRL` | parent → gyro | `enabled, targetFps` (2 B) | Legacy control packet. **Currently used only on Inactive transition** (`enabled=0`) to ask the puck to stop streaming. Never used to *initiate* a claim post-#813. |
| `0x62` | `GYRO_RECAL` | parent → gyro | header only | Operator-driven re-zero of the IMU reference (rare; bypasses normal calibrate flow). |
| `0x63` | `GYRO_COLOR` | gyro → parent | `r, g, b, flags` (4 B) | Operator-selected colour / flash from the puck's colour wheel page. |
| `0x64` | `GYRO_CALIBRATE` | gyro → parent | `calibrating, roll100, pitch100, yaw100` (7 B) | Hold-to-calibrate gesture start (`calibrating=1`) and end (`calibrating=0`). |
| `0x65` | `GYRO_HEARTBEAT` | parent → gyro | `state, claimActive` (2 B) | Liveness ping. Sent every 2 s by `_heartbeat_loop` per active claim, plus once immediately after `CLAIM_ACK` so the puck has something to reply to before any other timer is meaningful. |
| `0x66` | `GYRO_START` | gyro → parent | `nonce` (2 B; legacy header-only accepted) | Operator press-Start. Idempotent: same nonce ≤ 5 s ago replays the cached response (`CLAIM_ACK` or `CLAIM_DENIED`); no second engine call. Retry budget on the puck side: 5 × 150 ms = 750 ms. |
| `0x67` | `GYRO_CLAIM_DENIED` | parent → gyro | `reason` (1 B; legacy header-only accepted) | Refusal with reason code (#872). Puck firmware ≥ v1.2.11 renders distinct messages per reason; older firmware sees the trailing byte ignored and renders the legacy "BUSY" indication. Reason codes in §3.6. |
| `0x68` | `GYRO_BATT` | gyro → parent | `vbat100, pct, flags` (4 B) | Battery telemetry, 10 s cadence in all UI states. |
| `0x69` | `GYRO_STOP` | gyro → parent | `nonce` (2 B; legacy header-only accepted) | Operator press-Stop. Idempotent: same nonce ≤ 5 s ago replays the cached `STOP_ACK`. Retry budget on the puck side: 5 × 150 ms. |
| `0x6A` | `GYRO_CLAIM_ACK` | parent → gyro | `nonce, moverId` (4 B) | Confirms `CMD_GYRO_START` accepted. Puck advances `WAITING_ACK` → `ACTIVE` only on a CLAIM_ACK whose nonce matches the START it sent. |
| `0x6B` | `GYRO_STOP_ACK` | parent → gyro | `nonce` (2 B) | Confirms `CMD_GYRO_STOP` accepted. Puck stops retransmitting STOP. |
| `0x6C` | `GYRO_HEARTBEAT_REP` | gyro → parent | `uiState, claimNonce, seq` (5 B) | Puck's view of its own state. Sent every 2 s in **all** UI states. Provides the orchestrator-restart-bootstrap signal (§5.3) and a divergence indicator (§7 anti-patterns). |

### 3.6 CLAIM_DENIED reason codes (#872)

The 1-byte reason field is appended to `CMD_GYRO_CLAIM_DENIED` so the puck firmware can render an actionable message instead of the legacy single "Mover held by other" string that conflated four distinct failure modes.

| Reason | Code | Server condition | Puck UI |
|---|---|---|---|
| `IDLE` | `0` | Reserved / unspecified | "Press Start failed" |
| `CONTROLLER_INACTIVE` | `1` | `gyroEnabled=false` on the gyro fixture | "Gyro is disabled — enable in Setup" |
| `ALREADY_CLAIMED` | `2` | `_mover_engine.claim()` returns `False` (mover held by another `device_id`) | "Mover held by another remote" |
| `NO_MOVER_ASSIGNED` | `3` | `target_mover_id` is `None` | "No moving head assigned" |
| `ENGINE_NOT_AVAILABLE` | `4` | `_mover_engine` is None / engine not running | "DMX engine not running" |

**Back-compat:** the `parent_server.py` sender always emits 9 bytes (8 header + 1 reason). Firmware versions < 1.2.11 receive the same packet, parse only the header, and render the legacy generic message. UDP_VERSION is NOT bumped because the byte is purely additive.

**Idempotent retransmission:** when the orchestrator replays a cached DENIED response (same nonce within the 5 s dedupe window), it MUST cache the reason code along with the response and replay the original reason — a second press of the same nonce after the operator fixed the underlying condition does not re-evaluate the condition; the dedupe replays the original verdict. The operator must press Start with a *fresh* nonce to retry. This matches CLAIM_ACK's idempotency contract.

### 3.2 Idempotency guarantees

The wire protocol survives drops by making two operations idempotent and ACKed:

- **`CMD_GYRO_START` ↔ `CMD_GYRO_CLAIM_ACK`** — same nonce within a 5 s dedupe window replays the cached response without re-running the engine claim/start. If the puck never receives the ACK, it retransmits START with the same nonce; the orchestrator replays the cached ACK. Eventually one ACK gets through.
- **`CMD_GYRO_STOP` ↔ `CMD_GYRO_STOP_ACK`** — same nonce within the dedupe window replays the cached STOP_ACK. Server does NOT re-release the claim on a duplicate STOP.

These two retries-with-idempotency replace the older "arm-check timer" approach (which tried to release the claim if no orient arrived in N seconds). The arm-check is anti-pattern §7.1.

### 3.3 Packet diagram — successful press-Start

```
puck (WAITING_ACK)                    orchestrator
      │                                    │
      │── CMD_GYRO_START(nonce=N) ───────► │  resolve fixture+mover; claim(); start_stream()
      │                                    │
      │ ◄── CMD_GYRO_CLAIM_ACK(N, mid) ────│  cache (N → "ack") for 5 s dedupe
      │                                    │
      │ ◄── CMD_GYRO_HEARTBEAT(active=1) ──│  immediate first HB (so puck has something to reply to)
      │                                    │
puck advances → ACTIVE / page 0              │
      │── CMD_GYRO_HEARTBEAT_REP(ACTIVE)──► │
      │── CMD_GYRO_ORIENT (20 Hz) ────────► │
      │── CMD_GYRO_BATT (10 s) ───────────► │
      │ ◄── CMD_GYRO_HEARTBEAT (2 s) ──────│
```

### 3.4 Packet diagram — operator press-Stop

```
puck (ACTIVE / page 3 STOP)           orchestrator
      │                                    │
      │── CMD_GYRO_STOP(nonce=M) ────────► │  release(blackout=True); end_session(); cache (M → "ack")
      │                                    │
      │ ◄── CMD_GYRO_STOP_ACK(M) ──────────│
      │                                    │
puck advances → IDLE                         │
      │── CMD_GYRO_HEARTBEAT_REP(IDLE) ───► │  reconciliation: server already released, no-op
      │── CMD_GYRO_BATT (10 s) ───────────► │
```

### 3.5 Packet diagram — dropped CLAIM_ACK (recovery via idempotent retry)

```
puck (WAITING_ACK)                    orchestrator
      │                                    │
      │── CMD_GYRO_START(N) ──────X        │  ✗ packet lost
      │                                    │
      │ (150 ms timeout)                   │
      │── CMD_GYRO_START(N) ─────────────► │  *no* fixture entry yet → first run
      │                                    │   claim(); start_stream(); cache
      │ ◄── CMD_GYRO_CLAIM_ACK(N, mid) ─X  │  ✗ ACK lost
      │                                    │
      │ (150 ms timeout)                   │
      │── CMD_GYRO_START(N) ─────────────► │  same nonce, within 5 s window
      │                                    │   replay cached "ack" — NO second claim() call
      │ ◄── CMD_GYRO_CLAIM_ACK(N, mid) ────│
      │                                    │
puck advances → ACTIVE                       │
```

The retry budget is **5 × 150 ms = 750 ms total**. If all 5 ACKs are lost, the puck's UI reverts to IDLE with a "NO RESP" indication; the operator presses Start again and a fresh nonce starts a new session. The orphan claim from the first attempt is released by the all-comms-silence path (§6.3) when the puck eventually goes silent for >600 s, OR superseded immediately by the next press-Start since `_mover_engine.claim()` on the same `device_id` is idempotent (re-binds the same mover to the same device, no second claim entry).

---

## 4. The press-Start handshake — full detail

### 4.1 Pre-conditions

- Fixture record (`fixtureType=="gyro"`) exists with `gyroChildId` matching a discovered child whose `ip` is the source of the START packet.
- `gf.get("gyroEnabled") is True` (Active toggle, #801).
- `gf.get("assignedMoverId")` resolves to a DMX mover fixture with home anchors set (#800).
- `_mover_engine` is initialised.

If any pre-condition fails, server replies `CMD_GYRO_CLAIM_DENIED` and the puck reverts with the BUSY indication.

### 4.2 Server handler steps (`parent_server.py` `CMD_GYRO_START` branch)

1. Parse optional 2-byte `nonce` from payload (legacy header-only START accepted with `nonce=None`).
2. Dedupe check: if `_gyro_handshake[device_id].start_nonce == nonce` and `time.time() - ack_sent_ts < 5.0`, replay the cached response (`CLAIM_ACK` or `CLAIM_DENIED`) and return. **No second engine call.**
3. Pre-condition check: gyroEnabled, mover-id, engine availability. On any failure → cache "denied" + send DENIED + return.
4. **Stale clear (#823):** `Remote.clear_stale()` if any prior session left `stale_reason` set. Press-Start IS the operator's "I'm using this remote now" gesture; pre-fix, the engine tick would auto-release the brand-new claim ~25 ms later because the Remote still carried `session-ended` from the prior STOP.
5. `_mover_engine.claim(target_mover_id, device_id, dname, "gyro", smoothing=...)`. Returns `(ok, reason)`. On `not ok`, cache "denied" + send DENIED + return.
6. **Lights-on, dimmer, default colour (§1.1):** set the mover's dimmer to its `lampOnDimmer` (or 255 if unspecified) and a sensible default RGB / colour-wheel slot via the engine's claim-side write path. Mirrors #814's "default-on-when-idle" intent. This is the work that makes the head visibly come alive at the instant `CMD_GYRO_CLAIM_ACK` lands; the operator should not need to perform a calibrate gesture to see output.
7. `_mover_engine.start_stream(target_mover_id, device_id)`. Transition the claim to `streaming`. From this tick onward, `Remote.aim_stage` drives pan/tilt; the claim's first orient packet (which may already be in flight from the puck) takes the head to the puck's current pose.
8. `_send_gyro_claim_ack(ip, nonce, target_mover_id)`.
9. `_send_gyro_heartbeat(ip)` — **immediate** first HB so puck has something to reply to.
10. Cache `(start_nonce, "ack", time.time(), mover_id)` in `_gyro_handshake[device_id]` for 5 s dedupe.

There is no timer scheduled by this handler. Once the claim is created it stays created until §6 fires.

### 4.3 Puck-side flow (`main/GyroUI.cpp` + `GyroUdp.cpp`)

Press-and-hold release while in IDLE:
1. Allocate fresh nonce (monotonic per-puck counter, never 0).
2. `gyroUdpSendStartWithNonce(nonce)` — ships the first START frame, stamps the retry slot.
3. UI advances to `WAITING_ACK` with "CONNECT / waiting…" splash.
4. Background loop retransmits the same nonce every 150 ms up to 5 retries.
5. On `CMD_GYRO_CLAIM_ACK` with matching nonce → clear retry slot, advance to `ACTIVE` page 0.
6. On `CMD_GYRO_CLAIM_DENIED` → clear retry slot, revert to IDLE with brief BUSY indication.
7. On 1500 ms wall-clock timeout from press-release with neither ACK nor DENIED → revert to IDLE with "NO RESP" indication.

Mismatched-nonce ACKs (e.g. a stray retry from a prior gesture) are silently ignored.

---

## 5. The in-claim steady state

Once the claim exists, the orchestrator drives DMX from `Remote.aim_stage` per `_evaluate_track_actions` and the `MoverControlEngine` tick. The puck's responsibilities are:

1. **Stream `CMD_GYRO_ORIENT` at 20 Hz while on Calibrate / Stop / Status / Settings pages** — drives mover pan/tilt.
2. **Send `CMD_GYRO_HEARTBEAT_REP` every 2 s** — the orchestrator-side reconciliation signal (§5.3).
3. **Send `CMD_GYRO_BATT` every 10 s** — surfaced in `/api/gyros` and the SPA.
4. **Send `CMD_GYRO_COLOR` on colour wheel touches** — overrides RGB via the mover-control engine.
5. **Send `CMD_GYRO_CALIBRATE` on hold-to-calibrate gesture** (optional — see §5.0) — server runs `Remote.calibrate()` and resumes streaming.

### 5.0 Calibrate is optional

A puck whose chip-frame already aligns with the operator's intended aim direction (e.g. the operator picks it up off a table where it was sitting LCD-up and pointing forward) can drive the head from the moment `CMD_GYRO_CLAIM_ACK` lands, with no calibrate gesture. The orient-to-stage transform (`R_world_to_stage`) defaults to identity until the first calibrate. That identity transform is correct for any operator who positions the puck as the firmware expects.

Calibrate is the gesture that says "this orientation, right now, is what 'aimed at the calibration target' means." It is required only when the operator's grip / puck pose disagrees with the stage / mover pose at session start. Many operator workflows never need it.

The orchestrator MUST NOT gate any feature on the operator having performed a calibrate. In particular:

- DMX writes start the moment the claim is created (§4.2 step 6/7).
- The 600 s all-comms-silence fallback (§6.3) does NOT count "no calibrate" as a silence indicator — heartbeat-rep + battery + orient packets all reset the silence clock.

Performing a calibrate later in the session is supported any time. After `CMD_GYRO_CALIBRATE(calibrating=0)` the firmware advances the puck UI to the colour-picker page (§2.1).

The orchestrator's responsibilities are:

1. **Send `CMD_GYRO_HEARTBEAT` every 2 s** to each gyro device with an active claim — `_heartbeat_loop`.
2. **Translate `CMD_GYRO_ORIENT` updates to mover DMX writes** — `Remote.update_from_euler_deg()` + engine tick.
3. **Hold DMX writes during calibrate** — `MoverControlEngine.calibrate_start/end`.
4. **Maintain `last_data` freshness** — used only by the §6.3 600 s all-comms-silence fallback.

### 5.1 Calibrate-screen pause is legitimate

A puck on the calibrate screen waiting for the operator's hold-to-calibrate gesture is **not silent** — it's still sending `CMD_GYRO_HEARTBEAT_REP` every 2 s and `CMD_GYRO_BATT` every 10 s. Most pages also stream `CMD_GYRO_ORIENT` continuously. There is no scenario in normal operation where the orchestrator goes 600 s without seeing a packet from a working puck.

The orchestrator MUST NOT release the claim because orient packets paused, because the operator is studying the screen, or because a heartbeat-rep happened to be late. See §7.1.

### 5.2 Network blip mid-claim

If WiFi drops for <600 s, the orchestrator's `last_data` timestamp on the Remote stops advancing but the claim persists. When the puck rejoins, the next `CMD_GYRO_ORIENT` updates `last_data`, the §6.3 stale-hard threshold is reset, and the head reacquires within ~50 ms (one orient packet → one engine tick → DMX write).

`Remote.stale_reason == "connection-lost"` is auto-cleared by `_apply_quat` on first orient resumption (#812). The claim is **not** released during the blip; the `_evaluate_track_actions` engine tick simply has no fresh aim to write, so the head holds its last position. This is per #813 §"Eliminated bug classes".

### 5.3 Orchestrator restart while a claim is held

If the orchestrator restarts mid-session, its `_claims` dict starts empty. **The orchestrator does NOT reconstruct the claim automatically.** The operator presses Start again to re-establish the lock. Concretely:

1. Orchestrator restart clears `_claims`.
2. Puck continues sending `CMD_GYRO_HEARTBEAT_REP(uiState=ACTIVE, claimNonce=N)` every 2 s.
3. The orchestrator logs the heartbeat for diagnostics (§7.6) and DOES NOTHING ELSE — no reconstruct, no implicit re-claim.
4. The puck UI continues to show ACTIVE; orient packets continue to arrive; the orchestrator silently drops them because no claim exists for this device_id.
5. Operator notices the head is no longer responding (it reverted to whatever DMX writer was driving it before the gyro session). Operator presses Start. Normal §3.3 flow re-claims.

**Why this is the right trade-off (#872, operator 2026-05-09):**

> "Once Start is pressed, when we lock and hold."

Press-Start is the operator's contract for "I am taking control of this fixture". A heartbeat is not a contract — it's diagnostic telemetry. Auto-reclaim from a heartbeat creates a class of failure modes (orphan claim revival, race-against-operator-release, and #872's "I released and it came back") that are eliminated when the orchestrator's claim lifecycle has exactly one entry trigger. The few-seconds inconvenience after a rare server restart is the correct price.

**Implementation:** the HB_REP handler in `parent_server.py` MUST NOT call `_mover_engine.claim()`. It is allowed to: parse the packet, log it, update `Remote.last_data` for the §6.3 silence-timer, and reply with `CMD_GYRO_HEARTBEAT` (so the puck knows the orchestrator is reachable). It is NOT allowed to: claim, start_stream, send `CMD_GYRO_CLAIM_ACK`, or send `CMD_GYRO_CLAIM_DENIED`.

This invariant is enforced by the spec; it is not a per-issue patch. See §7.2 for the symmetric treatment of HB_REP-IDLE.

---

## 6. Release paths — the canonical list

A claim ends if and only if one of these three triggers fires. Anything else is anti-pattern (§7).

### 6.1 Operator press-Stop (`CMD_GYRO_STOP`)

The primary release path. Operator holds the STOP button on ACTIVE page 3; firmware sends `CMD_GYRO_STOP` with a nonce. Server:

1. Dedupe check on `stop_nonce` (5 s window) — replay cached `STOP_ACK` if a duplicate.
2. **`_mover_engine.release(mover_id, device_id, blackout=False)`** — claim ends, the next DMX writer takes over. The fixture returns to whatever it was doing before the gyro session: the running timeline, an active Track action, or the #800 park-at-home idle if nothing else is running. The press-Stop path does NOT force dimmer-zero; that would create a visible flicker if a timeline / Track-action writer is about to overwrite the same channel on the next tick (§1.2). This matches Android-controller-mode dismiss semantics and #800 idle-trigger behaviour.
3. `Remote.end_session()` + `_remotes.save()`.
4. `_send_gyro_stop_ack(ip, stop_nonce)`.
5. Cache `(stop_nonce, "ack", time.time())` in `_gyro_handshake[device_id]`.

If no other writer is driving the fixture at press-Stop time, the engine's #800 park-at-home logic snaps the fixture to its `homePan/Tilt/Dmx16` with lamp off — visually identical to a blackout, but produced by the idle-fallback path rather than a forced zero write.

### 6.2 Operator-driven Inactive transition (`gyroEnabled=false`)

Operator toggles the fixture's Active state off in the SPA. `_gyro_inactive_transition` (called by the `PUT /api/fixtures/<fid>` write path):

1. `_mover_engine.release(...)` — releases the claim if one exists.
2. `_gyro_send_release_packet(ip)` — sends `CMD_GYRO_CTRL(enabled=0)` so the puck stops streaming and reverts UI to IDLE.

### 6.3 All-comms silence > 600 s (defensive recovery)

If the orchestrator hasn't received **any** packet from the puck (orient, heartbeat-rep, battery, color, calibrate, stop) for more than 600 s, the puck has fallen off the network entirely — power loss, hardware failure, sustained WiFi outage. The defensive release path:

1. Engine tick (`MoverControlEngine.tick`) calls `Remote.check_staleness()`.
2. If `time.time() - last_data > STALE_HARD_SECS` (currently 60 s — see migration note below), `Remote.stale_reason = "connection-lost"` is latched.
3. On the same tick, `mover_control.py:541-559` sees `claim.state == "streaming"` AND `remote.stale_reason is not None` and calls `release(blackout=True)`.

**Migration note from #813 §Status #2 (operator, 2026-05-06):** the threshold should rise from `STALE_HARD_SECS = 60` to **600 s** and be repurposed from "orient silence" to "all comms silence". Current `Remote.last_data` is updated only by `_apply_quat` (orient packets). To match the spec, `last_data` MUST be updated by **every** incoming packet from the puck — orient, heartbeat-rep, battery, color, calibrate, stop. This change has not yet landed in code; it's planned as part of the realignment that this document supports.

The 600 s threshold is intentionally generous. It is not a "is the operator still paying attention" check. It is a "the puck appears to have died" check. An operator who walks away with the puck still on a table will see the claim survive — and the head holding its last aim — for a full 10 minutes before the orchestrator concludes the puck is gone.

---

## 7. Anti-patterns — what we explicitly DON'T do, and why

These are bug families that have been introduced and removed across #819, #823, #825, #832 and the realignment that this document supports. Listing them here so future code review can reject them by name.

### 7.1 Speculative arm-check timers

**Anti-pattern:** "if the puck doesn't send {orient | calibrate | heartbeat-rep} within N seconds of CLAIM_ACK, release the claim."

**Why it's wrong:** N is always either too short (calibrate-screen pause kills the claim) or too long (it's redundant with §6.3 anyway). The dropped-CLAIM_ACK case is already handled by the puck's idempotent START retransmission (§3.2) — the server's cache replays the same ACK and eventually one gets through. If all retries fail, the puck reverts to IDLE on its own and the next press-Start supersedes; the orphan claim from the failed first attempt is released by the §6.3 fallback when the puck eventually goes silent.

**History:** #825 pass-1 added `GYRO_ARM_DEADLINE_S = 1.5` and `_schedule_arm_check`. This violated #813's §"Eliminated bug classes": "Press-Start timing race: gone." Live test showed the calibrate-screen pause failure within hours. Pass-2 of #825 raised the window to 3 s and wired `_mark_gyro_armed` into more handlers, which masked the symptom for the immediate-tilt case but kept the calibrate-pause case broken. The realignment this document supports deletes the entire arm-check subsystem.

### 7.2 HB_REP → claim mutation (any direction)

**Anti-pattern:** "the heartbeat-rep can mutate the orchestrator's claim state in either direction" — release on IDLE, OR reconstruct on ACTIVE.

**Why it's wrong:**
- HB_REP-IDLE → release: the puck's UI may transiently show IDLE for reasons unrelated to operator intent — firmware reboot, brief WiFi reset, button-edge bounce. Per #813 §1, only an explicit operator gesture releases.
- HB_REP-ACTIVE → reconstruct: the heartbeat is diagnostic, not a contract. Reconstructing creates the bug class identified in #872: SPA Release / press-Stop is undone within 2 s by the next heartbeat, because the puck UI is still ACTIVE and the reconstruct branch can't distinguish "operator just released" from "orchestrator just restarted". Both look identical from a heartbeat's perspective.

Per operator 2026-05-09 (#872): **press-Start is the sole orchestrator-side claim entry trigger.** No auto-reclaim, no bootstrap, no shortcut. HB_REP is one-way diagnostics from puck → orchestrator: "this is what I think I'm doing." The orchestrator never acts on it for claim lifecycle.

**History:**
- #825 pass-1 introduced HB_REP-IDLE → release. Pass-2 of the realignment removed it.
- #825 also introduced HB_REP-ACTIVE → reconstruct as the orchestrator-restart-bootstrap path. Operator initially accepted that trade-off (avoiding an operator-visible UI blink across server restarts). #872 reversed the decision: the bug class it enabled (SPA Release oscillating with auto-reclaim) is intolerable in the field. The reconstruct path is removed in the #872 PR.

**Implementation invariant:** `parent_server.py`'s `CMD_GYRO_HEARTBEAT_REP` handler MUST NOT call `_mover_engine.claim`, `_mover_engine.release`, `_mover_engine.start_stream`, `_send_gyro_claim_ack`, `_send_gyro_claim_denied`, or any function that mutates the `_claims` dict. It MAY: parse the packet, log it, update `Remote.last_data`, and respond with `CMD_GYRO_HEARTBEAT` for liveness.

### 7.3 Periodic orchestrator-pushed lock packets

**Anti-pattern:** "fire `CMD_GYRO_CTRL(enabled=1)` to every Active gyro every 5 s to keep the lock alive."

**Why it's wrong:** introduces a polling race with operator press-Start; produces 0.2 Hz background UDP per gyro fleet member; #812's hard-stale latch could block lock packets while a puck was actively transmitting.

**History:** the original pre-#813 design. Removed by #813 itself / #801 cleanup. Listed here so it doesn't get re-introduced under a different name.

### 7.4 Coupling claim release to show / timeline state

**Anti-pattern:** "stop the show → release all gyro claims so the heads can park."

**Why it's wrong:** show / timeline / Track-action writers and the gyro claim are operator-owned at orthogonal levels. Stopping a show stops Track action-driven movement; it must NOT touch a press-Start-driven claim. The operator can keep using the puck to drive a head between shows.

The DMX-side claim arbiter (#763) handles the case where a Track action and a gyro claim are both pointing at the same fixture — claim wins, Track action keeps computing but skips the DMX write. That arbiter is orthogonal to claim lifecycle.

### 7.5 Single-side patches without auditing the union of contracts

**Anti-pattern:** "operator reports symptom X, I'll patch the surface that surfaced X."

**Why it's wrong:** see §"The single drift" in #813's audit comment. Five issues' worth of mental models, each correct in isolation, compound into bugs at their boundaries. Code review on this subsystem MUST argue against this document.

If a proposed change appears to violate this document, the change description must say so explicitly and propose a doc edit alongside the code edit. Drive-by fixes that contradict the spec are rejected.

---

## 8. Acceptance contract

The realigned design is correct iff all of these pass on hardware:

1. **Press-and-hold Start → CLAIM_ACK lands within ~500 ms → puck UI transitions to ACTIVE page 0 (Calibrate).** (#813 §Acceptance #1.)
2. **At the moment the claim is created the head turns on (dimmer up, default colour).** No calibrate gesture is required for the head to produce visible output (§1.1). The head's pan/tilt locks to the puck's current pose on the first orient packet (~50 ms).
3. **Press-Start → puck UI on Calibrate page → operator pauses any length of time → claim survives.** No timer fires from a quiet calibrate-screen state. (Subsumes #813 §Acceptance #1 with the calibrate-pause specifically named.)
4. **Operator never performs a calibrate gesture for the entire session → head still drives correctly from puck gestures the whole time → press-Stop ends cleanly.** Calibrate is optional (§5.0).
5. **Operator performs a hold-to-calibrate gesture mid-session and releases → server runs `Remote.calibrate()` → puck UI auto-advances to the Colour page (page 1).** (§2.1 page advance.)
6. **Press-Stop → claim ends → fixture returns to whatever was driving it before the gyro session: timeline / Track-action / #800 park-at-home idle.** The `_mover_engine.release(blackout=False)` path is taken; no forced dimmer-zero write. (§6.1, §1.2.)
7. **Toggle `gyroEnabled=false` in SPA mid-session → claim released → puck UI returns to IDLE.** (#813 §Acceptance #2; §6.2.)
8. **Network blip 30 s mid-session → orient stream resumes → head reacquires within ~50 ms.** Claim does NOT release. (#813 §Acceptance #3, #812 auto-clear, §5.2.)
9. **Power-cycle puck mid-session.** No `CMD_GYRO_STOP` is sent. Server holds the claim until 600 s of all-comms silence elapses; after that, releases with the same `blackout=False` semantics as press-Stop (the next writer takes over, or #800 park-at-home if nothing else). Operator can claim from another puck before that fallback fires. (§6.3.)
10. **Power-cycle orchestrator mid-session.** Puck's heartbeat-rep with `uiState=ACTIVE` triggers the bootstrap path; head reacquires within one heartbeat cycle (~2 s). No operator-visible blink. (§5.3.)
11. **Two pucks press-Start the same mover within 100 ms.** First wins the claim; second receives `CMD_GYRO_CLAIM_DENIED` and reverts with BUSY. (#813 §Acceptance #4.)
12. **Stale replay attack:** test harness records a `CMD_GYRO_START` packet and resends 30 s later. Server's nonce-dedupe window has expired (5 s); the packet is treated as a fresh START (idempotent on the same `device_id` — `claim()` returns the existing claim). Puck UI does not advance because the original sender is the only one in `WAITING_ACK`. No orphan claim created.
13. **Drop 1-in-3 CLAIM_ACK packets in a test harness.** Press-Start cycle still completes within ~750 ms median (5 retries × 150 ms). Puck UI advances. No orphan claims because the same nonce replays the cached ACK on the server.
14. **Drop 1-in-3 STOP_ACK packets.** Symmetric: cycle completes; no orphan.

---

## 9. Implementation pointers (current state, 2026-05-06)

**Server (`desktop/shared/parent_server.py`):**
- UDP listener: `_udp_listener` (loop with `cmd ==` dispatch).
- Press-Start handler: `CMD_GYRO_START` branch (~line 1438).
- Press-Stop handler: `CMD_GYRO_STOP` branch (~line 1366).
- Calibrate handler: `CMD_GYRO_CALIBRATE` branch (~line 1582).
- Heartbeat-rep handler: `CMD_GYRO_HEARTBEAT_REP` branch (~line 1635).
- ACK senders: `_send_gyro_claim_ack`, `_send_gyro_claim_denied`, `_send_gyro_stop_ack`.
- Heartbeat sender: `_send_gyro_heartbeat` (single-shot), `_heartbeat_loop` (2 s cadence).
- Dedupe state: `_gyro_handshake` dict, `_gyro_handshake_lock`, `GYRO_HANDSHAKE_DEDUPE_S = 5.0`.

**Engine (`desktop/shared/mover_control.py`):**
- Claim lifecycle: `MoverClaim.claim`, `start_stream`, `release`, `tick` (line 541-559 is the §6.3 fallback).
- Threshold constant: `STALE_HARD_SECS` in `remote_orientation.py` (currently 60 s; needs migration to 600 s + last_data semantics change per §6.3 migration note).

**Firmware (`main/`):**
- Protocol constants: `Protocol.h`.
- UI state machine: `GyroUI.cpp` (`UIState` enum line 92, retry budget constants line 102-104).
- UDP handlers: `GyroUdp.cpp` (`gyroUdpHandleCmd`, `gyroUdpSendStartWithNonce`, `gyroUdpSendStop`).

**Tests:**
- `tests/test_825_gyro_handshake.py` — protocol-level assertions for the wire format and handshake.
- `tests/test_819_gyro_stop_split.py` — STOP-vs-orient-bit-3 regression.
- `tests/test_813_gyro_lock_removed.py` — old auto-lock loop must stay deleted.

Code that needs updating to match this spec (tracked in the realignment work this document supports):

**Server (`parent_server.py`):**
- Delete `GYRO_ARM_DEADLINE_S`, `_schedule_arm_check`, `_arm_check_release`, `_mark_gyro_armed`, and all call sites. (§7.1.)
- Delete the HB_REP-IDLE → release branch in the `CMD_GYRO_HEARTBEAT_REP` handler. (§7.2.) Keep the HB_REP-ACTIVE bootstrap branch (§5.3).
- In the `CMD_GYRO_START` success branch, after `start_stream`, write the mover's lampOn dimmer + default colour via the engine before sending `CLAIM_ACK`. (§1.1, §4.2 step 6.)
- In the `CMD_GYRO_STOP` handler, change `release(blackout=True)` to `release(blackout=False)` so the next writer takes over without a forced dimmer-zero. Same change in any inactive-transition / cleanup paths that release a gyro claim, to keep the semantics consistent. (§1.2, §6.1.)

**Engine (`mover_control.py` + `remote_orientation.py`):**
- Migrate `Remote.last_data` from "orient packets only" to "any incoming packet from this device" so §6.3 measures all-comms silence. Possible API: a new `Remote.touch()` method called from every gyro CMD branch in the UDP listener.
- Bump `STALE_HARD_SECS` from 60 s to 600 s. Consider renaming to `STALE_COMMS_SILENCE_SECS` for clarity.

**Firmware (`main/GyroUI.cpp`):**
- On `CMD_GYRO_CALIBRATE(calibrating=0)` ship (i.e. on the operator's release-of-hold gesture on page 0), after the existing latched-orientation send, advance `s_page = 1` and redraw. The Colour page is the next useful surface; the Calibrate page is "transient until the gesture happens". (§2.1 page advance.)

**Tests (`tests/`):**
- Update `tests/test_825_gyro_handshake.py` to drop the two arm-check assertions and add assertions for §8 acceptance criteria 3 (calibrate-screen pause), 4 (no-calibrate session), 5 (calibrate → page 1 advance), 6 (Stop blackout=False), and 9 (puck power-cycle 600 s fallback).

---

## 10. Provenance

Every constraint in this document traces to either #813 (the spec issue) or to a follow-up that added a wire-protocol primitive needed to implement #813's intent. Specifically:

- §1, §6.1, §6.2, §8 acceptance — #813 directly.
- §1.1 (lights-on at Start, calibrate optional) — operator clarification 2026-05-06 on #813 audit thread.
- §1.2 (Stop releases to prior writer, not blackout) — operator clarification 2026-05-06 on #813 audit thread; matches Android-controller-mode dismiss semantics.
- §2.1 page-advance on calibrate-release — operator clarification 2026-05-06.
- §3 protocol additions: `CMD_GYRO_STOP` (#819), `CMD_GYRO_CLAIM_ACK` / `CLAIM_DENIED` / `STOP_ACK` / `HEARTBEAT_REP` / nonces (#825).
- §4.2 step 4 (stale-clear on press-Start) — #823.
- §4.2 step 6 (lights-on / default colour at claim) — operator clarification 2026-05-06; semantically aligns with #814 default-on-when-idle intent.
- §5.0 (calibrate is optional) — operator clarification 2026-05-06.
- §5.2 (auto-clear `connection-lost`) — #812.
- §5.3 (HB_REP-active bootstrap) — #825 pass-1.
- §6.1 `blackout=False` on press-Stop — operator clarification 2026-05-06.
- §6.3 600 s threshold + all-comms semantics — #813 §Status #2 edit (operator, 2026-05-06).
- §7.1 / §7.2 (arm-check / HB_REP-IDLE anti-patterns) — #813 §Proposed re-alignment edits (operator, 2026-05-06).
- §7.4 (claim-release / show coupling) — operator architectural directive (2026-04-30 mover-control session memory).

When you change this document, log the issue / commit / live-test session in the git history of this file. The drift this document is here to prevent only stays prevented if every change is traceable.
