## Gyro Controller fixture

A **Gyro Controller fixture** binds a physical gyro puck (the round-LCD
ESP32-S3 device) to one assigned moving head. When the puck is active,
its orientation drives the head's aim in real time — point the puck at
the floor, the beam follows.

### Key fields

- **Name** — operator-facing label; appears on the Dashboard and Status
  page so you can tell multiple pucks apart.
- **Assigned Mover** — the moving-head fixture this puck controls. A
  puck without an assignment is registered but inert.
- **Active toggle** — the first control on the Configure dialog. Green
  emerald = streaming orient to the mover; slate = idle. Switching it
  off releases the claim cleanly (head goes to blackout, not a frozen
  pose).
- **Aim-axis wizard** — sets the puck's body-frame to stage-frame
  rotation (`forward_local`, `up_local`). Run it once per physical
  mount orientation; the result persists on the puck record.

### Common pitfalls

- The mover must have a **Home position set** before the gyro can drive
  it. The setup wizard prompts for Home when you create a moving head;
  if you skipped it, the calibration card will show "Home not set"
  and the gyro claim will be refused.
- Smoothing was removed in v1.7.122 (#877). The puck's orientation is
  pure pass-through — any position the gyro points at is a valid
  vector, and the moving head handles its own mechanical motion via
  the `pan-tilt-speed` DMX channel (when the profile has one).
- If two operators press Start simultaneously, only one wins the
  claim; the loser sees "NO RESPONSE" on the puck LCD. This is by
  design — the orchestrator arbitrates so the head never flips
  between aim sources mid-show.

**More info →** the *Remote Control* appendix in the full manual.
