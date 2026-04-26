# Mover-cal run 2026-04-26 17:04 — analysis

Inputs:
- `cal-status-170450.ndjson` / `.log` — 15 of 24 grid probes before manual cancel at 21:08:11Z
- `dmx-trace-170450.ndjson` / `.log` — universe 1 trace, 64 events, 50 Hz watch
- `probe-coverage-170450.html` — 3D viewer, fixture #17, 46 probe rays + 8 candidates

Fixture #17: 150W MH Stage Right, layout pos `[X=600, Y=0, Z=1760]`, `inverted=true`,
home aim `[0, +1, 0]` (forward-horizontal). Memory notes actual Z ≈ 1435 mm and
actual X ≈ 500 mm (layout drift, #699 wizard fixes); the cal still uses the recorded values.

## 1. Cal grid is mostly OFF-STAGE — confirms operator's "behind the stage" intuition

The 3D viewer projects every grid target onto Z=0 using the corrected operator-relative IK
(same convention pinned 2026-04-26: pan handedness CCW from above, tilt 0..180 with both
endpoints horizontal, inverted-mount flips dz only). For the 15 grid targets reached:

| pan_norm | DMX panDeg | Floor X (mm) | Floor Y (mm) | Status |
|---------:|-----------:|-------------:|-------------:|--------|
| 0.542 | 292.5 | +2581 .. +3552 | +603 .. +899 | **ON STAGE** |
| 0.625 | 337.5 | +1310 .. +2051 | +1333 .. +2723 | **ON STAGE** |
| 0.708 | 382.5 | -363 .. +133 | +1532 .. +3162 | **OFF-STAGE LEFT** (X<0) |
| 0.792 | 427.5 | -2317 .. -813 | +753 .. +1555 | **WAY OFF-STAGE LEFT** |
| 0.875 | 472.5 | -2562 .. -845 | -1153 .. -440 | **BEHIND THE STAGE** (Y<0) |

3 of 5 pan columns (9 of 15 grid targets) are aimed at floor coords outside the stage
polygon. The pan=0.875 column (probes 13/14/15) is *behind the stage wall* — Y is negative
relative to the back wall the fixture is hung from. Stage bounds are 0..3620 (X) × 0..4000 (Y).

Pan-offset from home (0.677 → DMX 365.6°) for these columns:
- 0.542 = -73° from home (stage-right of forward, on-stage)
- 0.625 = -28° (slight stage-right, on-stage)
- 0.708 = +17° (slight stage-left, off-stage)
- 0.792 = +62° (large stage-left, way off-stage)
- 0.875 = +107° (behind fixture entirely)

**The redesigned battleship grid (#698) selects a tilt band that's camera-visibility-aware,
but the pan columns span ±107° from home with no visibility filter.** With the fixture at the
back wall (Y=0), any pan offset >≈±60° puts the projected aim past the stage edge or into
negative Y.

## 2. "Beam Found" candidates include the off-stage probes — plausibility gate is too lenient

Out of 15 grid targets, the cal flagged 8 as "Beam found" (probes 1, 2, 6, 7, 11, 13, 14, 15).
Cross-referencing with on-stage status:

- Probes 1, 2 (pan=0.708): **off-stage left** — flagged Beam Found
- Probe 6 (pan=0.625): on-stage — legitimate
- Probe 7 (pan=0.792): **way off-stage left** — flagged Beam Found
- Probe 11 (pan=0.542): on-stage — legitimate
- Probes 13, 14, 15 (pan=0.875): **behind the stage** — all flagged Beam Found

Five of 8 candidates are physically impossible — the beam was aimed at empty floor outside
the camera FOVs, but the cameras still reported a peak. Most likely cause: scatter from beam
hitting the back wall or ceiling spillage being picked up by a camera as a faint blob, then
the #682-DD plausibility gate (continuity + proportionality + 5×beam-width cap) accepting it
because the off-stage azimuths produce *no* prior valid sample to compare against.

If the cal converges on this data the resulting calibration grid will be poisoned —
pan/tilt mappings learned from off-stage azimuths will be wrong for any subsequent aim.

## 3. Colour wheel cycles green ⇄ white on every probe — the "lights cycling" symptom

39 colour-slot changes across 64 DMX events. State distribution:

| Slot | Dimmer | Count |
|------|-------:|------:|
| green | 255 | 38 |
| white | 0 | 24 |
| red | (init) | 1 |

Pattern: **every blackout writes `colour=white` and every probe writes `colour=green`.**
On a colour-wheel fixture this rotates the wheel mechanically between every probe — what
the operator perceived as "lights cycling as if it was resetting the colour each time."

This is wasted motion (mechanical wear + ~200-400ms wheel-rotation delay added to every
probe transition) and produces visible flicker. Recommended fix: hold the colour at green
throughout the cal run; cycling the dimmer between 0/255 already produces the on/off the
cameras need. Writing white during blackout has no observable effect (dim=0 emits nothing).

## 4. DMX dimmer-on-travel — clean (1 borderline)

Across 17 grid-to-grid traversals, 16 correctly cycled `dim=0 → move → dim=255`. One
borderline event at 21:07:43 had a 5% tilt move with `dim=255` held — looks like a confirm
nudge that exceeded the small-step threshold rather than a real travel. Not a #695
regression.

## 5. Pan trajectory — no rollover, slews are reasonable

Pan walked the columns 0.708 → 0.625 → 0.792 → 0.542 → 0.875 (max-min-distance traversal).
Largest single transition was 0.708 → 0.625 → 0.792 (a 27° + 18° pan move) over ~10s.
No 0/65535 wraparound, no suspicious 16-bit fine-byte jitter.

## 6. Probe order doesn't match the predicted max-min-distance traversal

In the prior session (memory `project_mover_cal_livetest_2026_04_26.md`) I predicted the
first four probes of the redesigned cal would be:

| n | predicted target (X, Y) | predicted intent |
|---|-----------------------:|------------------|
| 1 | (600, 1500) | straight forward, close — confirm FOV center |
| 2 | (4000, 3500) | far stage-left + far forward — far corner |
| 3 | (1441, 3500) | recenter + far forward |
| 4 | (2000, 2100) | center mid — fill |

What the cal actually did (projected onto floor by 3D viewer):

| n | (pan, tilt) | actual floor (X, Y) | comment |
|---|-------------|---------------------:|---------|
| 1 | (0.708, 0.165) | (-299, 2952) | off-stage LEFT, near-far |
| 2 | (0.708, 0.215) | (-40, 2101)  | off-stage edge, mid |
| 3 | (0.708, 0.265) | (+133, 1532) | on-stage edge, near |
| 4 | (0.625, 0.165) | (+2051, 2723) | on-stage stage-right, near-far |

The cal walked **tilt-first within a fixed pan column** (probes 1-3 are all pan=0.708),
then stepped pan and walked tilt again (probes 4-6 are all pan=0.625). That's a regular
nested sweep — *not* the "max-min-distance traversal across the visible-floor polygon"
that #698 was supposed to ship. Only #696's tilt-first ordering appears to have landed.

Two possible explanations:
- The max-min-distance traversal logic in #698 didn't land, only the tilt-first ordering did.
- It did land but degenerated because the visible-floor polygon evaluator is empty/wrong
  (consistent with the off-stage pan columns in §1 — the visibility filter isn't doing its
  job, so the traversal has no constraints to push probes apart).

Either way, the observable behaviour does not match the predicted #698 design.

## Recommended issues to file

1. **#698 follow-up: battleship grid needs camera-visibility-aware pan-column filter.**
   The tilt band is filtered for visible floor; the pan columns are not. With the fixture
   at the stage back-wall, columns >±60° from home aim at empty space outside the stage
   polygon. Any pan column whose floor projection falls outside both the stage bounds and
   any camera's frustum should be dropped before probing.

2. **Tighten plausibility gate for off-stage probes.** Probes whose IK projection lands
   outside the stage polygon should require a much higher detection-confidence threshold
   (or be rejected outright) — not the same gate as on-stage probes. Five false-positive
   candidates this run, all from off-stage pan columns.

3. **Hold colour during cal — don't cycle green/white per probe.** Write the cal colour
   once at start, leave it held through every probe. Saves wheel wear and ~200-400ms
   per probe transition (≈10s saved over a 24-probe run). White-during-blackout has no
   observable effect.

4. **#699 verify-pose wizard would have caught this.** Fixture #17's actual pose differs
   from configured (Z 1760→1435, X 600→500). The cal grid is computed from the wrong
   pose, which contributes to off-stage projection. Running #699 first would gate out
   this whole cal run.

5. **#698 max-min-distance traversal not observed in run output.** The probe order is a
   straight nested sweep (tilt-inner, pan-outer). Either the traversal didn't ship or
   the visible-floor polygon it draws from is empty/degenerate. Add a "first 4 probes
   are A/B/C/D" sanity log so this regression is detectable from cal-status alone.

6. **Candidate root cause — `#698` partition may be destroyed by the next sort (P0 to investigate).**
   In `desktop/shared/mover_calibrator.py`, three scenarios all match the observed
   "probe 1 lands off-stage" behaviour. Need to look at the server log to discriminate.

   - **(a) Sort destroys partition.** Lines 1122-1135 split probes into `inside` /
     `outside` and rebuild `grid = inside + outside`. Lines 1148-1152 then re-sort
     the whole grid by `(abs(pan-seed_pan), abs(tilt-seed_tilt))`, which mixes the
     partition. In this run, home pan = 0.677 (cal-anchor) — probe 1 was pan=0.708
     (Δ=0.031), closer to home than pan=0.625 (Δ=0.052), so the sort would float it
     to the front even if the FOV filter had marked it off-stage.

   - **(b) `grid_filter is None`.** If `camera_polygons` was empty when the cal
     started, the whole `if grid_filter is not None:` block is skipped and every
     probe is queued un-filtered. Patching (a) wouldn't fix this.

   - **(c) `grid_filter` returned True for off-stage probes.** If the polygon math
     itself wrongly accepts pan=0.708 as on-stage, the bug is in the filter
     not the sort.

   Discriminator: the server log line at line 1133 is
   `battleship_discover: camera-FOV filter kept N/M probes in view; D deferred to
   tail of queue`. Whichever box runs the orchestrator should grep for that string
   in the log around 21:05Z. If it's missing → (b). If it shows
   `kept ≪ M` → (a). If it shows `kept ≈ M` (filter accepted everything) → (c).

   Also worth noting: the off-stage projection uses `probe_coverage_3d.py`'s
   operator-relative IK. The cal *also* logs its own predicted first-floor at
   line 1165 (`battleship_discover: first probe ... predicted floor (X, Y)`).
   If those two IKs disagree, the off-stage finding collapses. The #704 fix is
   supposed to have unified them — verifying that line in the server log
   confirms or refutes the off-stage claim independently.

## Additional logging the cal needs (operator request)

Tonight's analysis took 30+ minutes of forensic NDJSON parsing because cal-status
omits the diagnostic state needed to distinguish (a) / (b) / (c) above. Proposed
additions to the cal-status stream — these turn this analysis into a 30-second read.

| New field | When emitted | Purpose |
|-----------|--------------|---------|
| `cameraPolygons: {count, totalPanCoverageDeg, polygons:[…]}` | once at `phase=starting` | Distinguishes "no polygons reached this call" from "polygons present but rejected probes" |
| `fovFilter: {kept, deferred, total}` | once at `phase=battleship-init` (new) | Direct discriminator for scenario (a)/(b)/(c) above |
| `firstProbePredictedFloor: [x, y, onStage]` | once at `phase=battleship-init` (new) | Surfaces the existing `line 1165` server log into cal-status; lets QA cross-check IK before the sweep starts |
| `predictedFloor: [x, y]` on every `currentProbe` | every probe event | Lets QA project the *whole queue* in real time without reverse-engineering IK |
| `sortKeyAfter: ["inside", deltaPan, deltaTilt]` on every probe | every probe event | Confirms partition order survived the post-filter sort |
| `confirmGate: {accepted, rejectedReasons:{…}}` | every confirm-end | Quantifies plausibility-gate behaviour per probe; today we have no visibility into why an off-stage probe got CONFIRMED vs REJECTED |
| `cameraDetect: {camIdx, peakIntensity, px, py, threshold}` | every detect attempt | Today we only see "Beam found" — no detection metadata. Without it we can't tell ambient-noise-FP from real beam-on-wall scatter |

The existing `first probe` sanity log at server-line 1165 should be promoted from
server log to a cal-status `phase: "battleship-init"` event with all of the above
fields populated. That single event would have made tonight's "is this aimed at the
stage?" question instant.

Artefacts: `probe-coverage-170450.html` (open in browser — orbit-camera shows the off-stage
probe rays vs stage polygon explicitly).
