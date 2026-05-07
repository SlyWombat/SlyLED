## 6. Stage Objects

Objects represent physical elements on the stage — walls, floors,
trusses, screens, and props or performers — plus the abstract
"targets" that Track actions chase. Anything a moving head needs to
aim at lives in the Objects tab.

> The tab was previously called "Surfaces"; the rename to **Objects**
> shipped with v1.7.30. Old project files import cleanly.

### Object Types

| Type | Default mobility | Description |
| --- | --- | --- |
| **Wall** | Static | Back wall, stage-locked to stage width × height |
| **Floor** | Static | Stage floor, stage-locked to stage width × (depth + 1 m) |
| **Truss** | Static | Lighting truss bar |
| **Screen** | Static | Projection surface |
| **Prop** | Moving | Performer, set piece, or mobile element |
| **Custom** | Moving | User-defined object |
| **Ribbon target** | Moving | Travelling stage-coord anchor used by the Aurora Curtain template (#839) — a coordinated rig moves through the same point along a chosen axis |

### Stage-Locked Objects

Wall and floor objects can be locked to stage dimensions. When you
change the stage size in Settings → Stage, locked objects resize
automatically.

### Mobility

- **Static** — fixed position. Cannot be tracked by moving heads.
- **Moving** — position can change at runtime. Trackable by DMX moving
  heads via the Track action (chapter 8).

### Patrol Motion

Moving objects can patrol during playback. Each patrol carries a
**pattern**, an axis or shape, a cycle time, and an optional easing
curve. The patrol evaluator runs at 40 Hz inside the DMX playback
loop, immediately before Track actions read object positions, so a
patrolling target's pose is always one frame fresh by the time a
mover reads it.

#### Patterns

| Pattern | Geometry | Use it for |
| --- | --- | --- |
| **Ping-pong** | Travels from one bounding-box corner to the opposite, then reverses | A side-stage performer's predicted path |
| **Circle** | Constant-radius loop around the bounding-box centre | A turntable performer or a rotating stage gag |
| **Figure-8** | Lemniscate (two-lobe) inside the bounding box | A complex path that visits two foci — useful for cross-stage chases |
| **Square** | Four straight-line legs around the bounding-box rim | A "patrol perimeter" feel used in security / industrial themes |
| **Ribbon** *(new in v1.7.83)* | A single travelling anchor on a chosen axis (left-right, front-back, up-down, cross, figure-8); multiple movers ride the ribbon at phase offsets so a sweep visibly travels the rig instead of every head moving in unison | The Aurora Curtain template's coordinated curtain effect |

#### Speed

- **Slow** — 20 s cycle.
- **Medium** — 10 s cycle.
- **Fast** — 5 s cycle.
- **Custom** — set `cycleS` directly. The default `speedPreset` is
  `medium`; ribbon patrol objects ship with `speedPreset: "custom"`
  so the template's `cycleS: 12` wins instead of the medium default.

#### Range

Bounding-box percentage (default 10 %–90 %). The patrol stays inside
the box; targets near a wall don't lead a mover into IK gimbal lock.

#### Easing

- **Sine** — smooth acceleration / deceleration (default).
- **Linear** — constant velocity.

#### `patrolMode`

Patrol objects can be set to one of:

- `always` *(default)* — patrol motion runs whenever the orchestrator
  is up. Useful for "the prop is alive even before the show starts".
- `on-demand` — patrol motion is suspended until an active timeline
  references this object via a Track action. The instant a referencing
  clip starts playing, the patrol resumes from where it would have
  been if it had been running all along (so a Track action that
  expects a moving target sees one immediately, not a stationary
  prop). Operators are sometimes surprised to find an `on-demand`
  patrol prop sitting still on the stage view — that's the intended
  behaviour.

### Temporal Objects

External systems can create short-lived objects via
`POST /api/objects/temporal`:

- Always in-memory; never saved to disk.
- Require `ttl > 0` (time-to-live in seconds).
- Auto-expire when TTL elapses.
- Position updates refresh the TTL.
- Shown in the runtime viewer with a dashed outline and a countdown
  badge.
- The camera tracker pushes detected people in via this route — each
  detection becomes a temporal object that the Spotlight Follow
  Person preset's Track action chases.

The temporal-object scale uses the renderer's `[width, height (Z),
depth (Y)]` ordering — call sites that want a "person-shaped"
detection should ship `[0.6, 1.8, 0.6]` (60 cm wide, 1.8 m tall, 60 cm
deep), not `[0.6, 0.6, 1.8]`.

---

