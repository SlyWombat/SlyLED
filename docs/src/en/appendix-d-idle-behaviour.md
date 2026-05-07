## Appendix D — Idle Behaviour

A "rock-solid" rig is one where, at any moment a show isn't playing
and an operator isn't actively driving a head, every moving head
parks at a known pose with the lamp closed. Operators expect that
contract because the alternative — a head sitting on whatever pose
the last writer happened to leave it in — looks broken even when
nothing is wrong. This appendix is the canonical list of when the
orchestrator parks, when it doesn't, and what "parked" means in
practice.

### What "parked" means

A parked moving head:

- Aims at its **Home** pose (the pose saved in the Set Home wizard;
  fallback to mechanical centre when no Home is saved).
- Holds **dimmer = 0** so the lamp doesn't bleed onto the rig.
- Closes the **shutter** if the profile carries a strobe channel
  with a `Closed` capability — fixtures with mechanical shutters
  benefit from the explicit close.
- Releases any colour-wheel slot it was holding back to slot 0
  (open / white) so the next show frame doesn't inherit a stale
  filter.

### When the orchestrator parks a head

| Trigger | Path | Notes |
| --- | --- | --- |
| **Cold start** | Orchestrator boot | Every DMX fixture parks once the engine comes up. Avoids the "head was left aimed at the back wall last night" surprise. |
| **Timeline natural end** | `_dmx_playback_loop` exit | Heads driven by the timeline's bake park when the show finishes. Track-action-driven heads also park (#807) — pre-fix only the bake-driven heads parked, leaving any tracker-claimed mover stuck at its last pose. |
| **Operator presses Stop** | `_dmx_playback_stop` set | Same as natural end. The blackout sweep applies only on stop or final-iteration end (#840), not between loop iterations. |
| **Claim release** | Mover-control claim arbiter | When an Android phone or gyro puck releases a claim, the head returns to the show if a show is running, otherwise parks. The release is instant — no slewed easing in v1.7.83+. |
| **Power-cycle re-settle** | First PONG from a child after a boot | When a fixture's child board power-cycles, the orchestrator resends the current globalBrightness (#843) and the next show frame writes a known pose. Pre-v1.7.83 the child could come up at full brightness for one frame; the PONG-time top-up closes that window. |

### What does NOT trigger a park

These intentionally do not park heads — operators sometimes expect
them to, but parking on every one-shot would steal the head from the
show.

- **One-shot `/api/mover/<fid>/aim`** — these are operator-direct
  test pulses; the rig holds whatever pose the route sets until the
  next show frame.
- **DMX-test sliders on the Settings tab** — same reasoning. The
  sliders override show output for as long as the operator is
  driving them.
- **Brief gaps within an active claim** — a phone or puck temporarily
  losing WiFi for a second doesn't release the claim. The remote-
  control claim TTL is 15 s; a head only parks when the TTL elapses
  without a heartbeat (#813 §6.3 "all-comms silence").
- **Calibration probes** — beam discovery and convergence sweeps
  hold the head wherever the probe lands. The calibration session
  parks the head explicitly when it finishes (success or abort).

### How to verify on your rig

1. Park a moving head at a known wall (Set Home → save).
2. Stop any running show.
3. Aim the head at the floor with `/api/mover/<fid>/aim`.
4. Press play on a 5-second blank timeline. The head should NOT
   move (rule three: one-shot aims hold).
5. Stop the timeline. The head should return to Home with the lamp
   closed within ~50 ms.

If step 5 doesn't happen, check (a) the fixture has a Home pose
saved, (b) the timeline ended naturally rather than crashing,
(c) no claim is held on the fixture (the Setup tab shows claim
state per fixture).

---

