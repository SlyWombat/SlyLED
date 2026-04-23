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
11. **Refactor path for existing spatial effects.**
    `_compile_sphere_sweep`, `_compile_plane_sweep`, `_compile_box`
    are per-LED-effect today. Per §2 (no backward compatibility),
    they refactor onto the new layer in one pass — no parallel
    "legacy effects" path. Question: what's the smallest unit of
    that one-pass refactor, and does it land in the same PR as the
    first capability-layer primitive or immediately after it?

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

### 8.1 Static-reading round (2026-04-23)

Nine code-answerable questions (Q1–Q7, Q12, Q13) resolved from the
runtime path — `_evaluate_track_actions`, `MoverControlEngine`,
`ParametricFixtureModel`, `dmx_profiles`, `remote_orientation`. No
hardware required.

**Q1 — per-fixture `aimTarget` (Fn 1).** `aimTarget` is one enum
value per Track action, shared by every assigned mover
(`parent_server.py:11077`; `_ACTION_FIELDS`, line 12157). The schema
is trivially extensible: the action already carries per-fixture
dicts for offsets (`trackFixtureOffsets`, line 11083), so
`aimTargets: {fixtureId: enum}` follows the same pattern. No data
migration. **Verdict:** ship when Fn 1 gets its next polish pass.

**Q2 — head-to-target assignment (Fn 1).** Index-based, no
travel-time minimisation. Three branches
(`parent_server.py:11045-11065`):
`n_heads > n_targets` → modulo spread; `fixed_assign` + excess
targets → first-N 1:1; otherwise cycling chunk driven by
`trackCycleMs` (default 2000 ms, line 11026). **Smell:** lines
11045 and 11063 are literally identical (modulo fallback) — one
path is unreachable. **Smell:** no nearest-current-aim assignment,
so two movers can swap targets frame-to-frame under jitter,
stressing pan/tilt mechanics unnecessarily. Hungarian assignment
would be the targeted fix.

**Q3 — loss UX (Fn 1).** Inconsistent. **Full loss** (target list
empty): mover blacks out — dimmer written to 0
(`parent_server.py:11193-11201`); pan/tilt simply stop being
re-written, so they freeze by default (no explicit hold). **Raw
tier** (low-confidence placement, `_method == "raw"`, per the
camera review's Q5 fix): head stays assigned, aim computation is
skipped, dimmer keeps writing — so the beam freezes in place at
full intensity. Two different behaviours for two flavours of loss.
Operator sees silent freeze on raw, silent blackout on full loss
— no visual cue distinguishes "lost" from "paused". Likely new
issue: unify on "fade dimmer to 0 over ~0.5 s" for both, or add a
`trackLossBehaviour: hold | fade | black` enum.

**Q4 — smoothing and latency (Fn 1).** Runtime ticks at **40 Hz**
(`parent_server.py:11257`, `interval = 0.025`). No smoothing on
the Track-action path — each tick writes pan/tilt directly to DMX
(`parent_server.py:11171`). Camera jitter lands on the mover
unfiltered. Contrast: `MoverControlEngine` applies an EMA via
`claim.smoothing` (`mover_control.py:311-313`). Fn 1 should
inherit the same primitive — likely a new issue to add a
`trackSmoothingAlpha` field. Bake-time
(`_compile_dmx_fixture`, 1 s time-slices) remains unchanged —
coarser-than-runtime, which is correct for baked playback.

**Q5 — Has #484 shipped? (Fn 2)** **Yes, the runtime path is
stage-space end-to-end.** `MoverControlEngine._aim_to_pan_tilt`
(`mover_control.py:334`) prefers `ParametricFixtureModel.inverse`
with a stage-mm target point
(`aim_stage * 3000 mm`, `mover_control.py:345-351`); falls back
to affine (still stage-mm, lines 355-365) then generic
`aim_to_pan_tilt` (stage-mm aim vector, lines 374-379). No
`delta_pan`, `delta_tilt`, `reference_pan`, `reference_tilt`,
`panScale`, `tiltScale` survive in the runtime consumers.
**Dead code:** `desktop/shared/gyro_engine.py` still contains the
old delta-based math (`panScale`/`tiltScale` at lines 12-13) but
is **not imported** by `parent_server.py` — superseded by
`MoverControlEngine`. Recommend deleting `gyro_engine.py` outright
(no backward-compat concerns per §2). Likely new issue.

**Q6 — multi-fixture remote claim (Fn 2).** Single claim per
device today: `self._claims = {mover_id → MoverClaim}`
(`mover_control.py:115`); `.claim()` takes one `mover_id`
(`mover_control.py:138-154`); `_tick()` iterates mover-by-mover
(`mover_control.py:264-325`). **No downstream code assumes
one-mover-per-device**, so the minimum change is
(1) `_claims` becomes `device_id → set[mover_id]`,
(2) `_tick()` broadcasts the same aim/colour to every member of
the set, (3) release-by-device releases all members. Clean
refactor, no data-model churn.

**Q7 — remote-as-stage-object (Fn 2).** Schema exists, rendering
doesn't. `Remote` carries a `pos` field (stage mm, defaulted to
[0, 0, 1600]; `remote_orientation.py:82-91`), persisted through
`RemoteRegistry` into `remotes.json`
(`parent_server.py:8704-8705`), mutable via
`/api/remotes/*` (lines 9146-9162). **But** remotes are **not** in
`_layout.children` and not drawn in the 3D viewport — the
operator cannot see where the remote is placed, cannot drag it,
cannot visually verify calibration. This is the
precondition gap for both #484 UX and #427 (Android pointer mode):
three steps to close — render a gizmo in the Layout tab, make the
gizmo draggable (writes `Remote.pos`), show it in Runtime. Likely
new issue.

**Q12 — capability declaration (cross-cutting).** Profile schema
(`dmx_profiles.py:8-27`) carries `panRange`, `tiltRange`,
`beamWidth`, `category`, `colorMode`, and a per-channel
`capabilities[]` list (GDTF-flavoured type names —
`ColorIntensity`, `ShutterStrobe`, `WheelSlot`, etc., lines 34-39,
59-70). **No top-level `caps: ["colour.rgb", "direction.pan-tilt",
...]` list** — fixture-type-agnostic dispatch today has to
synthesise capability from channel-map probing (e.g. runtime code
at `parent_server.py:1176-1185` grepping the channel map for
"red" / "color-wheel"). For §0 (capability layer) a top-level
`caps[]` list computed once from the profile at load-time is the
minimal change. GDTF/MVR alignment is partial — borrowing type
names but not the full struct; a proper GDTF import would need a
mapping layer, out of scope for first cut.

**Q13 — stage-mm-only contract (cross-cutting).** Clean. Every
path that feeds mover aim works in stage mm: Track actions
(`parent_server.py:11082`, `11097-11099`), MoverControlEngine
(`mover_control.py:348-350`), bake-time
(`_compile_dmx_fixture`), and the IK primitive itself
(`parametric_mover.py:108-131`). Pixel-relative code exists only
in the camera-calibration / stereo path, which outputs stage-mm
positions *before* temporal-object ingest. Legacy v1 calibration
data is not consulted on any aim path. Pre-condition for §0
already holds.

### 8.2 Cross-question synthesis

- **Q3 + Q4: Fn 1 polish gap.** No smoothing on the 40 Hz runtime
  path plus inconsistent loss UX = Track actions feel rough next
  to MoverControlEngine. A single-PR fix (EMA smoothing + unified
  loss behaviour) would close both. **→ file issue.**
- **Q5 + Q6 cleanup.** `gyro_engine.py` is dead; delete it. **→
  file issue.**
- **Q5 + Q1 code duplication.** Three-tier IK fallback exists in
  both `_evaluate_track_actions` (parent_server.py) and
  `MoverControlEngine._aim_to_pan_tilt` (mover_control.py).
  Extract to a shared
  `compute_pan_tilt_with_fallback(mover, aim_stage_or_point, …)`
  helper in `parametric_mover.py` or `spatial_engine.py`. **→
  file issue after Fn 3 architecture decisions settle** (the
  helper may also serve the capability layer).
- **Q7 blocks Fn 2 feel.** Until remotes render in the 3D
  viewport, the one-phone-one-mover UX (never mind the
  multi-mover claim in Q6) will feel ghost-like. **→ file issue;
  precondition for #427.**
- **§0 architectural bet is feasible.** Q12 confirms the profile
  schema can carry a `caps[]` list with no migration; Q13
  confirms the stage-mm contract holds end-to-end; Q6 confirms
  the claim model isn't painted into a corner. The blocker isn't
  plumbing — it's the design of the response function itself
  (Q8–Q11, still open).

### 8.3 Live-test resolution

To be populated after the next basement-rig session. Per §7:
Fn 1 walk-path accuracy, Fn 2 gyro/phone per-axis sweep, Fn 3
synthetic then live demo.

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

### 12.1 Velocity-lead / velocity-lag aim (future)

Once tracked temporal objects expose a **motion vector** (direction +
speed) — planned as a camera-pipeline enhancement — the Fn 1 aim
logic gets a lead/lag modifier "for free" under the capability layer
(§0). The moving head simply locks onto a **projected anchor**:

```
lead_anchor = obj.anchor(aimTarget) + motion_vector * lead_seconds
```

`lead_seconds` becomes a per-Track-action parameter:
- **Positive lead** → spot arrives ahead of the performer (dramatic
  "the light is chasing them" or "the light is waiting for them").
- **Zero** → current behaviour (lock on current position).
- **Negative lead** → spot trails behind (comet tail, "where they
  were a moment ago").

No new aim primitive is required — `ParametricFixtureModel.inverse`
still takes a stage-mm point; the caller just hands it a
velocity-projected point instead of the raw anchor. This is the kind
of feature the capability layer (§0) is designed to make trivial:
aim direction is a pure function of `(fixture_pose, target_point)`,
and `target_point` can be any function of the tracked object's
state.

Depends on: temporal object ingest publishing a stable motion vector
(camera-pipeline scope, not moving-head scope).
