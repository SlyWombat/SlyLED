# Moving-Head Alignment Review — TODO (resume later)

**Status:** Scoping not yet written. Branch created, clean tree.
**Branch:** `claude/review-mover-alignment-plan`
**Date started:** 2026-04-23
**Session attempts crashed twice before a plan doc could land; this file captures the prompt for the next session.**

---

## Ask from the operator

Do the same depth of review as the camera calibration review
(`docs/camera-calibration-review.md`, merged via PR #632) but for
**moving-head alignment**. The scope is broader than calibration —
it's about moving heads as first-class spatial-effect participants.

### Three production-relevant functions

The moving-head subsystem exists for exactly these three jobs:

1. **Object tracking.** Aim at a point on an object represented in
   the stage view (feet / center / head / arbitrary anchor). Builds
   on Q4's `aimTarget` and the auto-track evaluator shipped in the
   camera review.
2. **Remote-vector aim.** Align the beam with a given vector from a
   remote (gyro-equipped phone, puck, or any device that can emit
   an absolute-space direction). Builds on `MoverControlEngine`
   (`claim` / `orient`) and issues #474 / #484 / #427.
3. **Abstract multi-fixture effect participation.** Moving heads
   participate as peers in effects that span fixture types. Example:
   a "color wash across the stage" — LED strings phase through a
   colour wave, flood lights do the same, **and the moving heads
   physically sweep across the stage at the wave's speed, in the
   wave's colour**. Effects are geometry-driven, not
   per-fixture-type attribute tables.

### Additional asks

- **Compare against state of the art and competitors** (grandMA3,
  Chamsys MagicQ, Avolites Titan, Hog, Disguise/d3, Blacktrax,
  Follow-Me, Madrix, QLC+, GDTF/MVR, etc.). Which of the three
  functions exist elsewhere, which don't, where is SlyLED uniquely
  positioned.
- **"What is next"** — a prioritisation recommendation. The user
  already approved direct-to-`main` implementation (first beta, no
  compat per §2 of the camera review).

### Operator's existing guidance

- Calibration accuracy is already solved per the camera review
  ("there is existing mechanisms for accurately determining this")
   — the `ParametricFixtureModel.inverse(x,y,z) → (pan, tilt)`
   primitive shipped in `aa67fc3` / `876b875`. Don't re-do
   calibration; build on top.
- Product has not shipped; breaking changes welcome.
- scipy or any other new tech allowed (see camera review §2).

---

## What the next session should produce

A review plan doc at `docs/mover-alignment-review.md` mirroring the
camera review's structure, with this shape:

- **§1 Purpose** — the three functions above.
- **§2 Review principles** — same as camera review §2 (one
  coordinate system, no silent assumptions, operator-first, no
  compat, scipy allowed).
- **§3 Current SlyLED architecture** — brief survey of what's on
  `main` for each function:
  - Fn 1: `_evaluate_track_actions`, `aimTarget` enum, temporal
    objects, tracking tier (`_method`).
  - Fn 2: `MoverControlEngine.orient` HTTP path, gyro CMD_GYRO_*
    UDP, Android HTTP POSTs.
  - Fn 3: **does not exist yet** — effects are per-fixture-type
    today (timeline bakes DMX per mover step; no spatial-effect
    abstraction). Flag this as the largest architectural gap.
- **§4 State of the art** — competitor survey. Structure as a
  per-function table, honest about where SlyLED is behind, at
  parity, or ahead.
- **§5 Gap analysis** — where SlyLED is uniquely positioned vs.
  where it trails industry. Expected conclusions:
  - Fn 1 (tracking): uniquely cheap (~$100 camera rig vs Blacktrax
    $50K+); strong differentiator.
  - Fn 2 (remote-vector aim): no industry analogue at phone price
    point; moderate differentiator.
  - Fn 3 (abstract effects): **industry has nothing like this**.
    Biggest differentiator; also biggest implementation gap.
- **§6 Review questions** — roughly 10–14 questions in the same
  shape as the camera review Q1–Q14. Split by function.
- **§7 Method** — static reading, basement-rig live test, synthetic
  prototype (especially for Fn 3), competitor WebSearch/WebFetch
  for claim verification.
- **§8 Findings** — empty, to be populated as questions are
  answered.
- **§9 Out of scope** — e.g., cal accuracy (camera review covers),
  DMX wire protocol, gobo/effect channels beyond pan/tilt, authoring
  UX for cue stacks.
- **§10 Related open issues** — cross-reference placeholder.
- **§11 Change log** — initial draft entry.
- **§12 Recommendations for further exploration** — placeholder.

### "What is next" recommendation shape

After drafting §1–§7 the doc should include a prominent §0 or a
bullet at the top of §5 stating the architectural bet:

> **Fixture-capability participation layer.** Unify every fixture
> (LED strings, flood lights, moving heads, pixels) behind a single
> data model:
> - Position in stage mm (already stored).
> - Declared primitives (colour, intensity, direction-if-movable,
>   beam-width).
> - Response function `(position, time, effect_params) → fixture
>   output` so an effect can evaluate per-fixture without knowing
>   fixture type.
>
> Once this layer exists, Fn 3 becomes natural (effects are pure
> spatial functions), Fn 1 reuses it (`aimTarget` → vector from
> fixture to tracked anchor), Fn 2 reuses it (remote vector is the
> fixture's direction).
>
> This is the architectural bet that separates SlyLED from every
> incumbent DMX console.

---

## Practical notes for the next session

- **Keep initial plan-doc length under ~300 lines.** Previous
  attempts crashed on large single-file writes; split depth across
  follow-up commits if needed.
- **Draft in one Write, then commit, then push** — don't chain
  large reads + large writes + large GitHub operations in one
  message.
- **Branch name:** `claude/review-mover-alignment-plan` (already
  exists locally, not yet pushed; check `git branch` first).
- **Don't open a PR** until §1–§7 are landed and the operator has
  had a chance to reshape scope — matches the camera review's
  "plan first, approve, then findings, then approve, then merge"
  rhythm.
- **Per the camera review pattern**, the first commit on the branch
  should be `docs: mover-alignment review plan` with the §1–§7
  skeleton, same as the camera review's initial commit `3068d65`.

---

## Source material to consult

- `docs/camera-calibration-review.md` — structural template.
- `desktop/shared/parametric_mover.py` — the aim primitive.
- `desktop/shared/mover_control.py` — Fn 2 path.
- `desktop/shared/parent_server.py:_evaluate_track_actions` — Fn 1
  path.
- `desktop/shared/bake_engine.py` — current timeline→DMX flow for
  movers; where Fn 3 abstraction would plug in.
- `docs/mover-calibration-v2.md` (#488).
- Issues #474, #484, #427 (Fn 2 consumers), #610 (mover-cal
  operator experience).

This file can be deleted when the plan doc lands.
