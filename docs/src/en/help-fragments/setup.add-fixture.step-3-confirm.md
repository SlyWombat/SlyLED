## Add Fixture — Step 3: Confirm

The final review screen before the fixture is created. Everything on
this card lands in `fixtures.json` on **Create Fixture**.

### What you're confirming

- **Name** — final operator label.
- **Universe** + **Start Address** — DMX patching. Re-check against
  the conflict detector if you came here via Back/Forward.
- **Channels** — channel count occupied by this fixture.
- **Profile** — the library profile id (Local / Community / OFL).
  Blank means a generic channel layout — useful for one-off custom
  fixtures, but the bake engine can't drive pan/tilt/gobo/etc.
  intelligently without channel-type metadata.

### What happens on Create Fixture

1. The orchestrator writes a new fixture record to `fixtures.json`.
2. If the profile carries **pan + tilt** channels (it's a moving
   head), the wizard prompts to **Set Home now**. Home is the DMX
   value where the beam aims along the fixture's rotation vector;
   SMART calibration, gyro / Android remote control, and aim-by-XYZ
   all require it before they'll start.
3. The Setup tab refreshes — your new fixture appears in the list and
   on the Layout tab's 3D scene at the origin (drag it into position
   on the layout canvas).

### Pitfalls

- The fixture starts at the **origin** `(0, 0, 0)` until you place it
  on Layout. Spatial actions and aim-XYZ moves will all aim toward
  the origin until then. Bake outputs will still play, but the
  geometry will look wrong on the 3D preview.
- If you skip the Home prompt, the calibration card on the Setup tab
  will keep showing "Home not set" until you walk the wizard. Set
  Home early — every aim-driven feature (gyro, SMART, track actions
  on movers) depends on it.

**More info →** chapter 4, *Fixture Setup*; appendix B, *Mover
Calibration*.
