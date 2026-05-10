## Appendix E — Remote Control: Android Phone & Gyro Gyro

Two remote controllers can drive moving heads in real time alongside
a running show: an Android phone running the SlyLED operator app and
a Waveshare ESP32-S3 round-LCD gyro controller. Both go through the same
claim arbiter on the orchestrator, both follow the same handshake
protocol, and both cooperate with the show timeline through the
mover-control claim arbiter. This appendix describes the full
lifecycle, the gestures, and how claim arbitration interacts with
preset shows.

### Claim lifecycle

The claim is the orchestrator's "this remote currently owns mover
N" lock. It carries a 16-bit nonce, a TTL, and a current pose so
the system can reconcile the gyro's UI state with the orchestrator's
arbiter state when one of them reboots or drops a packet.

```
1. IDLE on remote.
2. Operator presses Start (gyro) or Claim (Android).
3. Remote ships CMD_GYRO_START / claim request with a fresh 16-bit nonce.
4. Orchestrator allocates a mover, replies CLAIM_ACK with the
   nonce + assigned moverId. The remote advances UI to ACTIVE
   only on a matching ACK; CLAIM_DENIED reverts; ~1.5 s overall
   timeout reverts with "NO RESPONSE".
5. Remote streams orient quaternions at ~50 Hz; orchestrator
   converts to aim-stage and writes pan / tilt to the head.
6. Both ends exchange 2 s heartbeats (HB_REP carries uiState +
   claimNonce + seq) so divergent state is reconciled.
7. Operator presses Stop / Release; remote ships nonce; orchestrator
   replies STOP_ACK and releases the claim.
```

The full state-machine spec lives in `docs/gyro-claim-lifecycle.md`
and is the source of truth for any change to the protocol.

#### What the operator sees

- **Press Start on the gyro** — page advances to "ACTIVE" within
  ~150 ms. If the orchestrator can't claim a mover (none online,
  none available), the page reverts to IDLE with a denial reason.
- **Press Stop on the gyro** — page returns to IDLE; the head
  returns to whatever the show was driving (or parks if no show is
  running).
- **Calibrate** — hold the **Calibrate** button (gyro or Android)
  for as long as you need; release to capture the new reference
  pose. The screen advances to the colour picker page on the gyro;
  the Android app advances to the gesture page.
- **Connection lost** — both remotes show a stale-reason badge if
  the orchestrator stops hearing heartbeats. The gyro self-clears
  when it resumes streaming (#812 / #821 / #823); operator can also
  force-clear via `POST /api/remotes/<id>/clear-stale`.

### Gestures

Once active, both remotes drive the same `aim_stage` semantic — the
head's beam aims at a stage-coordinate point computed from the
remote's orientation.

#### Phone (Android)

- **Pitch** (tip phone forward / back) — beam pitches up / down on
  the head.
- **Roll** (tilt phone left / right) — beam pans across the stage.
- **Yaw** (rotate phone around vertical) — beam pans across the
  stage.
- **Volume buttons** — fine dimmer up / down (Android operator app
  configurable).
- **Auto Brightness** (chapter on Brightness) — the app can drive
  the orchestrator's master brightness from the local mic envelope
  at ~20 Hz, gamma-scaled to the rig (#820, #843).

The phone-specific yaw axis is mirrored relative to the gyro (#824)
because the phone's natural-portrait orientation puts the operator's
"left" 90 ° offset from the gyro's body frame. The operator never
needs to think about this; the orchestrator's `_apply_quat` for
`KIND_PHONE` handles the negation.

#### Gyro gyro

- **Pitch** (tip gyro forward / back) — beam pitches up / down.
- **Yaw** (rotate around the gyro's vertical axis) — beam pans.
- **Roll** (tilt left / right) — colour-wheel selection on profiles
  with a colour wheel; ignored on RGB-only profiles.
- **Press Start** — claim mover and start streaming.
- **Press Stop** — release claim.
- **Press Calibrate** — capture new reference pose.

### Claim arbitration with shows (#763)

Claims take priority over the show timeline:

- A claimed head is **muted** from `_evaluate_track_actions` and
  from the bake-driven `set_fixture_dimmer` / `set_fixture_pan_tilt`
  writes. The claim writer owns the head until release.
- Other heads in the rig keep playing the show normally — the
  claim affects only the assigned mover.
- On release the head **rejoins the show within one frame**: no
  slew, no fade. If the show has moved on, the head jumps to
  wherever the show currently has it. This was a deliberate choice
  per #763 — slewing back in took the operator out of the moment;
  snap-rejoin is what real consoles do.
- Track actions evaluate every frame, so a head that was claimed
  during a sweep returns to wherever the sweep is **right now**,
  not to where the sweep would have been mid-claim.

### Colour & dimmer during a claim (#814)

A claim doesn't take over colour or dimmer:

- The remote's gestures drive **only pan / tilt** (and colour wheel
  for the gyro's roll axis on profiles that support it).
- The head's dimmer and RGB stay under the show's control. If the
  show is dim, the claimed head stays dim — the operator picks pan
  and tilt; the show paints colour and intensity.
- This applies to global brightness (#843) the same way: a claim
  during Auto Brightness inherits the auto-driven master.

### Recovery from divergent state

The handshake's heartbeats include both ends' state, so divergent
combinations are reconciled:

| Gyro UI | Orchestrator | What happens |
| --- | --- | --- |
| ACTIVE | claim held | Normal — heartbeats keep TTL alive. |
| ACTIVE | no claim | Orchestrator reconstructs the claim (orchestrator-restart bootstrap). |
| IDLE | claim held | Orphan claim — orchestrator releases it. |
| IDLE | no claim | Normal idle. |

The orphan-claim guard fires 1.5 s after CLAIM_ACK if no orient
arrives, releasing the claim so a wedged remote can't squat on a
mover indefinitely.

---

