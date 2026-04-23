# Moving-Head Alignment Review Plan

**Status:** Draft (§1–§7 + §0 architectural bet). §8 onward is to be filled
in as questions are answered. **No code changes during the review phase.**
**Date:** 2026-04-23
**Scope owner:** Dave (operator) + Claude (implementation)
**Mirrors the structure of:** `docs/camera-calibration-review.md` (PR #632, merged 2026-04-23)
**Related docs:** `docs/mover-calibration-v2.md` (#488), `docs/gyro-stage-space.md` (#484), `docs/camera-calibration-review.md`, `project_calibration_v2_phase1.md`, `feedback_cal_algorithm.md`

---

## 0. The architectural bet (read first)

> **Fixture-capability participation layer.** Unify every fixture (LED
> string, pixel, flood light, moving head, future hazer/laser) behind one
> data model:
>
> - **Pose in stage mm** (already stored in `_layout.children`).
> - **Declared primitives** — colour, intensity, direction-if-movable,
>   beam-width, position (for emitters that have spatial extent).
> - **Response function** `f(fixture_pose, primitives, t, effect_params) →
>   fixture_output` so an effect evaluates per-fixture **without knowing
>   the fixture type**.
>
> Once this layer exists:
> - Fn 3 (abstract multi-fixture effects) becomes natural — effects are
>   pure spatial functions; LED strings, floods and movers all evaluate
>   the same wave equation.
> - Fn 1 (tracking) reuses it — `aimTarget` becomes "vector from
>   `fixture_pose` to tracked anchor", evaluated by the same primitive.
> - Fn 2 (remote-vector aim) reuses it — the remote's stage-space vector
>   is just another input to the direction primitive.
>
> This is the architectural bet that separates SlyLED from every incumbent
> DMX console (grandMA3, Hog, Titan, MagicQ): industry consoles think in
> attribute tables per fixture type; SlyLED would think in stage-space
> response functions per fixture **capability**. See §5 for the gap
> analysis that supports this bet.

The plan that follows is structured to answer "should we make this bet,
and if so, what's the smallest first step?"

---

## 1. Purpose

The moving-head subsystem exists for exactly **three production-relevant jobs**:

1. **Object tracking.** Aim a beam at a chosen anchor (feet / centre /
   head / arbitrary) on an object placed in the stage view. Builds on
   Q4's `aimTarget` and the auto-track evaluator shipped with the camera
   review (`_evaluate_track_actions`, `parent_server.py:10970`).
2. **Remote-vector aim.** Align the beam with a vector emitted by a
   remote (gyro puck, phone, any device that can produce an
   absolute-space direction). Builds on `MoverControlEngine`
   (`mover_control.py`) and the architecture in `docs/gyro-stage-space.md`
   (#484).
3. **Abstract multi-fixture effect participation.** Moving heads
   participate as **peers** in effects that span fixture types. Operator's
   canonical example: a colour wash across the stage — LED strings phase
   through a colour wave, flood lights do the same, **and the moving
   heads physically sweep across the stage at the wave's speed in the
   wave's colour**. Effects are geometry-driven, not per-fixture-type
   attribute tables.

Calibration accuracy is **out of scope** here — the camera review already
landed `ParametricFixtureModel.inverse(x,y,z) → (pan, tilt)` as the
canonical IK primitive (§9). This review builds on top of that primitive.

---

## 2. Review principles

- **One coordinate system end-to-end.** Stage mm per
  `project_coordinate_system.md` (X=width, Y=depth, Z=height). Any frame
  hop must be explicit and testable.
- **Don't trust silent assumptions.** "1 device → 1 mover" is a valid
  default but should not be a hard limit. Whenever code bakes in a
  one-to-one count, flag it.
- **Operator-first.** The operator wants "the moving head goes where I
  point" and "moving heads participate in scenes I author". Any
  proposal that requires per-show fixture-type-specific authoring is
  out.
- **No backward compatibility required.** First beta release; no shipped
  customers, no saved shows in the wild. Prefer the clean breaking
  change. Mirrors camera-review §2.
- **scipy or any other new tech allowed.** Per camera-review §2 /
  `project_calibration_v2_phase1.md`. The capability layer (§0) may
  reach for whatever library makes the response-function model cleanest
  — operator has explicitly opened the door beyond scipy.
- **Build on the IK primitive.** `ParametricFixtureModel.inverse` is the
  one and only `(stage_xyz) → (pan, tilt)` path. Don't add a second.

---

## 3. Current SlyLED architecture — snapshot

### 3.1 Hardware context

Per `project_basement_rig.md`: three DMX moving heads on the basement
rig — two ceiling-ish movers and one floor-mounted 350 W BeamLight
(`beamlight-350w-16ch`, hybrid RGB+colour-wheel, 16-bit pan/tilt). Two
EMEET 4K cameras feed both tracking (Fn 1) and beam-spot calibration.
ESP32-S3 gyro puck + Android phone are the live remote-vector inputs (Fn 2).

### 3.2 Fn 1 — tracking pipeline (works today)

```
 _temporal_objects (from camera ingest, see camera review)
        │
        ▼
 _evaluate_track_actions(elapsed, engine, dmx_fixtures)         parent_server.py:10970
        │
        │  for each Track action (type 18):
        │    - resolve targets (trackObjectIds | trackObjectType | all temporal moving)
        │    - resolve heads (trackFixtureIds | all DMX with pan/tilt)
        │    - assign heads to targets (cycling chunk / fixed 1:1 / spread)
        │    - aimTarget enum: feet | center | head → pick anchor from obj._anchors
        │    - apply offsets (global + per-fixture + auto-spread)
        │    - clamp to stage bounds
        │    - hybrid affine + geometric IK → DMX pan/tilt
        ▼
 ArtNet engine → DMX bridge → fixture
```

**Status:** functional. Q4/Q5 from the camera review just landed
(`aimTarget` anchors, raw-tier hold-last-good).

### 3.3 Fn 2 — remote-vector aim pipeline (in transition)

```
ESP32 gyro puck  ── CMD_GYRO_ORIENT ──▶ parent UDP listener
Android phone    ── POST /api/mover-control/orient ──▶ Flask
        │
        ▼
 MoverControlEngine                                              mover_control.py
   - claims (one device → one mover, TTL 15s)
   - calibrate-start / -end (frame-alignment capture)
   - 40 Hz DMX write tick
   - colour / dimmer / strobe / flash
   - aim path → ParametricFixtureModel.inverse (when get_mover_model present)
                else affine_pan_tilt fallback
```

**Status:** the file header says "No Euler math, no delta references —
the primitive owns orientation" (per #484 phase 4). Whether that has
**actually shipped end-to-end** vs. still being delta-based downstream
is one of the review questions (§6 Q5). #474 / #484 / #427 are the
Fn 2 architecture issues.

### 3.4 Fn 3 — abstract effect participation (does not exist as a runtime primitive)

What exists today is a **bake-time** hook only:

```
bake_engine._compile_dmx_fixture(clip, effect, fixture_pos, ...)   bake_engine.py:360
   - sample beam cone (5 points along axis + 2 spread points at aim end)
   - evaluate spatial effect at each sample → take brightest colour
   - if effect motion is set: time-slice 1 s segments, recompute pan/tilt
   - emit ACT_DMX_SCENE segments into the timeline bake
```

This is **the closest thing SlyLED has** to Fn 3, but it's narrow:

- **Bake-time only.** Not available to runtime/track/remote actions.
- **Tied to spatial-effect motion** (`startPos → endPos`, linear
  ease). No general "fixture response function" — the mover follows
  the effect's authored sweep, not a generic spatial wave.
- **Per-effect, not per-capability.** Each spatial effect type
  (`sphere_sweep`, `plane_sweep`, `box`) has its own `_compile_*`
  function. Adding a new effect requires adding a new mover-bake path.

Industry-common multi-fixture spatial effects ("colour chase across the
room that LED strings, washes, and movers all participate in") have **no
runtime path today**. This is the largest architectural gap and is what
§0 addresses.

### 3.5 Shared plumbing

- `desktop/shared/parametric_mover.py` — `ParametricFixtureModel.inverse`
  is the canonical IK primitive. Used by both `_evaluate_track_actions`
  and `MoverControlEngine`.
- `_layout.children` holds positions (stage mm). `_fixtures` holds
  capability/profile data. `pos_map` / `fx_lookup` joins them per call —
  no first-class fixture-pose object.
- `desktop/shared/spatial_engine.py` — owns `compute_pan_tilt`,
  `effect_aim_point`, `evaluate_spatial_effect`. Consumed by both
  `_evaluate_track_actions` (runtime) and `_compile_dmx_fixture` (bake).
  Best candidate for the home of the new capability layer.

---

## 4. State of the art — competitor scan

To be verified per claim during §8 (WebSearch / WebFetch). First-pass
positioning:

| Function | grandMA3 / Hog / Titan / MagicQ | Disguise / d3 | Blacktrax / Follow-Me / TAIT | SlyLED today |
|----------|--------------------------------|---------------|------------------------------|--------------|
| **Fn 1: object tracking** | Manual cue-based; no built-in tracker. External system writes pan/tilt over MIDI / OSC / CITP. | Some 3D-stage tracking via xR camera workflows. | **Built for this** — Blacktrax IR beacons (~$50K+ rig), Follow-Me operator-with-trackball, TAIT auto-follow via stage-mounted sensors. Industry standard but $$$. | YOLO + camera nodes, ~$100/camera. Fn 1 implemented. |
| **Fn 2: remote-vector aim** | Faders / encoder wheels / external trackball. No "phone is the beam" UX. | Phone-as-pointer not a first-class feature. | Offstage trackball (Follow-Me) is closest. | Phone + gyro puck → 1:1 stage-space aim (#484 in design, partial code on `main`). |
| **Fn 3: spatial-effect participation across fixture types** | **No.** Effects are per-attribute, per-fixture-type (colour chase ≠ pan chase, separately authored). | Pixel-mapping content can be projected onto stage geometry; movers consume separate cues. | Tracking-driven follow only; no "wave equation for all fixtures". | Bake-time hook for sphere/plane/box effects only. No runtime primitive. |

To validate in §8: GDTF / MVR (open formats from MA Lighting / Vectorworks)
declare fixture capabilities — could that map onto our capability layer
without inventing a new schema? Madrix and Resolume have pixel-level
spatial effects but treat movers as separate; QLC+ does not have spatial
effects at all. Hippotizer "Hippo Looper" does spatial pixel-mapping but
not the wave-equation-across-types model.

---

## 5. Gap analysis

Where SlyLED stands vs. industry, per function:

- **Fn 1 (object tracking) — strong differentiator on price.** Industry
  charges $50K+ for IR beacon rigs (Blacktrax, BlackTrax-like systems);
  Follow-Me requires a trained operator. SlyLED does it with $100 of
  Orange Pi + USB camera and a YOLO model. Accuracy is lower (camera
  + AI vs IR-beacon ground-truth) but adequate for the price point.
  **Recommendation:** keep investing; this is already shipping.
- **Fn 2 (remote-vector aim) — moderate differentiator.** No incumbent
  ships a phone-as-stage-pointer at consumer price. The hardware
  (phone IMU + magnetometer) is there; the UX is the gap. #484 is the
  design doc; this review's job is to confirm the architecture is
  settled and to scope follow-up work.
- **Fn 3 (abstract effect participation) — biggest gap, biggest bet.**
  **No incumbent has this.** Industry consoles are built around the
  attribute table per fixture type (colour wheel ≠ RGB ≠ HSV pan/tilt).
  Authoring a "colour wash across the stage that all fixtures
  participate in" today requires per-fixture-type cue-stacking. The
  fixture-capability participation layer (§0) reframes this: if every
  fixture exposes a response function, the wave-equation evaluates
  uniformly. This is the architectural recommendation; §6 Q8–Q11 are
  the questions that have to be answered before code lands.

---

## 6. Review questions

The review exists to answer these. Every recommendation must cite the
question(s) it answers.

### 6.1 Fn 1 — object tracking

1. **`aimTarget` is per Track action today; should it be promoted to
   per-fixture-on-the-action** (e.g. mover A aims at feet, mover B aims
   at head for a key-light effect)?
2. **Head-to-target assignment policy.** Today the loop assigns by
   index modulo with a cycling chunk (`parent_server.py:11045-11065`);
   is the cycling-chunk model the right UX, or do we want a stable
   Hungarian-style assignment that minimises pan/tilt travel time
   between frames?
3. **Loss UX.** Q5 from the camera review held the head when the
   target tier was `raw`. Do we also want a graceful "fade dimmer to 0
   over N frames" so the operator sees the loss visually instead of a
   silent freeze?
4. **Smoothing and latency.** Track-actions evaluate at the action-eval
   tick (~10–20 Hz); bake-time `_compile_dmx_fixture` slices at 1 s.
   Is the runtime smoothing too jerky for a fast walker, and what's
   the acceptable jitter budget?

### 6.2 Fn 2 — remote-vector aim

5. **Has #484 actually shipped, or is the doc ahead of the code?**
   `mover_control.py` header says "No Euler math, no delta references —
   the primitive owns orientation". Verify that the runtime path
   (claim → calibrate-end → orient) actually computes a 1:1
   stage-space vector via `parametric_mover.inverse`, and that no
   `panScale` / `tiltScale` multipliers remain in any consumer.
6. **Multi-fixture remote claim.** A claim binds one device → one
   mover. Should one phone be able to control N movers simultaneously
   (ensemble follow), via a fixture-cluster claim that broadcasts the
   same stage-space vector to every cluster member?
7. **Remote-as-stage-object.** #484 says the remote is a placed
   fixture with a pose. Has that schema landed (`_remotes`?
   `_fixtures` entry with `kind: "remote"`?), and is it already
   visible in the 3D viewport? If not, it's a precondition for Fn 2 to
   feel right. Cross-link #427 (Android pointer mode).

### 6.3 Fn 3 — abstract effect participation

8. **Approve / reshape the §0 architectural bet.** Does the response
   function `(fixture_pose, primitives, t, effect_params) → output`
   cover wash + chase + position-modulated effects, or do we need a
   separate primitive for direction-modulated effects (e.g. "all heads
   aim at the leading edge of a wave")? Decide before committing to
   the schema.
9. **Bake-time vs runtime.** `_compile_dmx_fixture` is bake-time;
   tracking and remote-vector aim are runtime. Does the new layer
   compile to bake products (fast playback), run at runtime (live
   reactivity), or **both** (bake hot path + runtime override hook)?
   Implications for the spatial-effect engine architecture.
10. **First implementation target.** Pick **one** canonical effect to
    drive the API design. Operator's example is "colour wash across
    the stage". Alternatives: "chase round the room", "everyone looks
    at the bass-drum trigger", "ground-spot follows the audio
    centroid". Recommend: colour wash sweep — simplest, exercises
    colour + position + (optionally) direction primitives.
11. **Compatibility with existing spatial effects.**
    `_compile_sphere_sweep`, `_compile_plane_sweep`, `_compile_box`
    are per-LED-effect today. Do they refactor onto the new layer in
    one pass, or run in parallel as a "legacy effects" path while the
    new layer matures?

### 6.4 Cross-cutting

12. **Capability declaration.** Each fixture's primitives must be
    discoverable. The DMX profile already exposes `panRange`,
    `tiltRange`, `beamWidth`. Is that enough, or do we need explicit
    capability tags (e.g. `caps: ["color.rgb", "intensity.dimmer",
    "direction.pan-tilt", "beam.zoom"]`) to drive
    fixture-type-agnostic effect dispatch? Cross-check against GDTF
    / MVR to avoid reinventing the wheel.
13. **Stage-mm-only contract.** Confirm every fixture type (LED
    string, pixel mapped, flood, mover, future devices) has its pose
    in stage mm in `_layout.children` and that no legacy
    frame-relative or pixel-relative path remains for the runtime
    consumers. Pre-condition for the layer.
14. **Synthetic acceptance test for Fn 3.** What's the prototype
    test? Proposal: "10 movers placed at random stage XY, all
    participate in a colour-wash sweep; assert each fires at the
    expected wall-clock time within ±50 ms and at the expected RGB."
    This is the test that would prove the layer end-to-end **without
    hardware**.

---

## 7. Method

Each question gets an answer from one of these:

- **Static reading** (grep, read the function). Fastest; enough for
  questions about reachable code paths.
- **Basement-rig live test.** For questions about UX, multi-fixture
  follow, remote-vector feel. Capture screenshots + timing data into
  `/mnt/d/temp/live-test-session/` per `feedback_screenshot_folder.md`.
- **Synthetic prototype** — especially for Fn 3. Write the response
  function for a simple "colour wash across X" effect and run it
  against a synthetic 10-fixture layout; assert wall-clock + colour
  match expected.
- **Competitor verification** — WebSearch / WebFetch for the §4 claims
  (especially "no incumbent has Fn 3"). Document each claim with the
  source URL in §8.

Live-test checklist (basement rig, run once per question batch):

1. Fn 1: walk a defined path, capture per-mover aim error vs ground
   truth (ArUco-marked walking pose).
2. Fn 2: hold gyro puck and Android phone; for each, calibrate-start
   on the same mover, sweep through 8 cardinal directions, capture
   commanded vs achieved beam direction. Look for any per-axis
   asymmetry that would indicate residual delta math.
3. Fn 3 (prototype only): synthetic test, no hardware. Plus one
   live demo on the rig once the prototype runs in the orchestrator.

---

## 8. Findings

Empty — to be populated as questions are answered, mirroring camera
review §8.1 (static-reading round) and §8.3 (live-test resolution).

---

## 9. Out of scope

- Calibration accuracy (`ParametricFixtureModel`, marker placement,
  beam-spot detection) — the camera review (PR #632) covers this.
- DMX wire protocol (Art-Net packet layout, channel mapping, GDTF
  import) — separate concern.
- Gobo / colour-wheel / strobe / iris channels beyond pan/tilt. Fn 3
  may eventually need these as primitives, but the first cut is
  position + colour + intensity + direction.
- Cue-list / show-flow authoring UX (#304 cue list, #306 live faders).
  The capability layer is a backend primitive; authoring UX is a
  separate downstream review.

---

## 10. Related open issues

- **#484** — Gyro/phone controller stage-space architecture. Fn 2
  design doc; this review verifies it shipped.
- **#474** — Gyro absolute stage-space orientation mapping. Subsumed
  by #484; close on confirmation.
- **#427** — Android pointer mode (phone as laser pointer).
  Operator-facing application of Fn 2.
- **#610** — Mover calibration discovery / blink-confirm / validation.
  Calibration scope (camera review territory); cross-link only.
- **#488** — `ParametricFixtureModel` + LM solver. The IK primitive
  this review builds on.
- New issues to be filed from §8 will be labelled
  `mover-alignment-review-2026-04-23`.

---

## 11. Change log

- **2026-04-23** — Initial draft (§1–§7 + §0 + §10 cross-references).
  Source TODO: `docs/mover-alignment-review-TODO.md` (deletable once
  this lands). Branch `claude/review-mover-alignment-plan`.

---

## 12. Recommendations for further exploration

To be filled in after §8 lands. Mirrors camera review §12 — a place
for ideas surfaced during the review that aren't in the immediate fix
list but are worth scheduling.
