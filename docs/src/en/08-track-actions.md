## 8. Track Action

The Track action (type 18) is the bridge between the Objects tab
(chapter 6) and the moving-head rig: while a clip with a Track action
is playing, every assigned moving head computes its pan / tilt aim
from the live position of one or more target objects, every frame
(40 Hz). When the clip ends — or the action's last referencing clip
finishes — the assigned heads park at their Home pose (#807).

### How a Track action runs

1. Place the moving objects you want chased on the **Layout / Objects**
   tab (chapter 6) — props, custom moving objects, ribbon targets, or
   camera-detected people coming in as temporal objects.
2. Create a Track action on the **Actions** tab. Set its name, scope,
   and target list.
3. Drop a clip on a track in the **Timeline** that references the
   Track action.
4. During playback, the 40 Hz DMX loop reads each target's current
   position, picks (or assigns) one or more heads to chase it, runs
   the canonical aim-vector IK (#806 / #809), and writes pan / tilt
   to the universe buffer. The 3D viz's beam cone is driven from the
   same aim vector, so the visualisation always matches what the
   physical head is doing.

### Assignment algorithm

The assignment is recomputed every frame from `trackFixtureIds` (the
heads in scope) and the resolved target list:

| Situation | Behaviour |
| --- | --- |
| Equal number of heads and objects | 1 : 1 mapping by index |
| More heads than objects, **trackAutoSpread = false** | Each object is chased by exactly one head; extra heads sit idle (still on, just not assigned). |
| More heads than objects, **trackAutoSpread = true** | Spread the heads across each object's width — useful for washing a single target with multiple movers. |
| More objects than heads, default | Cycle through objects with one head landing on a different target every `trackCycleMs` (default 2000 ms). |
| More objects than heads, **trackFixedAssignment = true** | Each head locks to one target by index; extras are ignored until a head frees up. |

Per-fixture offsets (`trackFixtureOffsets`) allow asymmetric rigs to
aim each head at a slightly different point on the same target — for
example, a stage-left mover aims at the performer's head while a
stage-right mover aims at the feet.

### Action editor — Advanced expander (#811)

The Action editor's DMX Scene-family panes group the simple fields
(name, scope, RGB, dimmer) at the top, with an **Advanced** expander
underneath holding the per-action fine controls. For Track actions
the Advanced section exposes:

| Field | What it controls | Range |
| --- | --- | --- |
| **Cycle Time (ms)** | `trackCycleMs` — only used when there are more objects than heads | 100 – 10 000 ms (clamped) |
| **Offset X / Y / Z (mm)** | Global aim offset added to every target. Useful for "aim 30 cm above the floor where the marker actually is" or "aim at head height instead of centre of mass". | ± 10 000 mm |
| **Auto-spread** | Toggles `trackAutoSpread` for the more-heads-than-objects case | — |
| **Fixed assignment** | Toggles `trackFixedAssignment` — disables cycling, locks each head to one target | — |
| **Track dimmer** | `trackDimmer` — overrides the dimmer the heads run at while the Track action is active. Defaults to 255 (full); operators sometimes lower this so the followed beam doesn't overpower the rest of the rig. | 0 – 255 |

### Target object selection

| Field | What it does |
| --- | --- |
| **`trackObjectIds`** | An explicit list of object IDs to track. Wins over `trackObjectType` when set. Searches the **whole** moving-object set (including patrol props), so an `on-demand` patrol prop's id resolves cleanly here. |
| **`trackObjectType`** | Filter moving objects by type — e.g. `"prop"`, `"ribbon-target"`, `"custom"`. Useful when the target set is "every ribbon target" without listing IDs. |
| **`trackMode`** | Only consulted when `trackObjectIds` and `trackObjectType` are both empty. `"camera-moving"` *(default)* — track only camera-detected people (temporal objects). `"all-moving"` — track patrol props **and** camera detections together. |

If a Track action has explicit `trackObjectIds` but every listed
object has been deleted, the action skips the frame entirely rather
than blacking out the assigned heads — pre-fix (v1.7.78 and earlier)
the missing-target case would zero the dimmer of every head in scope.

### Cooperation with shows and remotes (#763 / #835)

Track actions are full citizens of the show timeline:

- An orphan Track action (one that lives in `_actions` but is not
  referenced by any clip in the running timeline) does **not**
  evaluate (#835). This stops a leftover preset action from
  blacking out movers in unrelated timelines.
- When a remote (Android phone or gyro puck) claims a head via the
  mover-control claim arbiter (chapter on Remote control), the
  claimed head is muted from the Track action for the duration of the
  claim — operator gestures take priority over the show. Releasing
  the claim returns the head to the show without a re-bake.
- Track actions write the canonical aim-vector through the same
  `_set_canonical_aim_stage` path the rest of the orchestrator uses,
  so calibrate-end during a Track-driven show observes the head's
  true direction without an inverse-IK round-trip.

---

