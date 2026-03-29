# SlyLED User Manual — 3D Volumetric Lighting System

## Table of Contents
1. [Getting Started with 3D Stage Design](#1-getting-started)
2. [Fixture Setup](#2-fixture-setup)
3. [Creating Spatial Effects](#3-spatial-effects)
4. [Building a Timeline](#4-timeline)
5. [Baking & Playback](#5-baking)
6. [Classic Mode](#6-classic-mode)
7. [Troubleshooting](#7-troubleshooting)

---

## 1. Getting Started with 3D Stage Design

### Switching Between 2D and 3D
The Layout tab offers two views via the toggle buttons at the top:
- **2D Canvas**: The original flat layout — drag performers onto a grid. Best for simple setups.
- **3D Viewport**: An interactive Three.js scene. Best for complex multi-level installations.

Both views share the same position data. Switching between them is instant and non-destructive.

### Navigating the 3D Viewport
| Action | Control |
|--------|---------|
| **Orbit** (rotate view) | Left-click + drag |
| **Zoom** | Scroll wheel |
| **Pan** (shift view) | Right-click + drag |
| **Select performer** | Left-click on a node |
| **Move performer** | Drag the colored arrows after selecting |
| **Edit performer** | Double-click on a node |
| **Place from sidebar** | Drag an unplaced performer into the viewport |

### Coordinate System
- **X-axis** (red): Width — left to right
- **Y-axis** (green): Height — ground to ceiling
- **Z-axis** (blue): Depth — front to back
- **Origin**: Bottom-left-front corner of the stage
- **Units**: Internally stored in millimeters; displayed in your chosen unit (Settings tab)

### Stage Dimensions
The stage is shown as a wireframe box in the 3D viewport. Default size: 10m x 5m x 10m. Configure via the Stage API or from your orchestrator settings. The ground plane grid shows 1-meter squares.

### Saving Positions
Click **Save Layout** after positioning performers. In 3D mode, positions are read directly from the Three.js scene, including height (Y) and depth (Z).

---

## 2. Fixture Setup

### What Are Fixtures?
A fixture is the primary entity on the 3D stage. It wraps physical hardware and adds stage-level attributes.

- **A child IS a fixture** — auto-created when hardware is registered via Setup
- **Fixtures can override child attributes** — e.g., a string wired as "east" on the ESP32 can be rotated vertical on the stage
- **Fixtures can also be** DMX lights (future), groups of children, or standalone definitions
- The **baking engine** uses the fixture's position and rotation, not the child's raw config

### Fixture Types

| Type | Description | Visual |
|------|-------------|--------|
| **Linear** | LED strip/string. Pixels spaced along a path. | Colored dots along a line |
| **Point** | Single light source with area of effect. | Translucent sphere |
| **Surface** | 3D mesh (OBJ) as a projection target. | Semi-transparent mesh |
| **Group** | Named collection of fixtures targeted as one | No direct visual |

### Linear Fixtures
When a child is registered, a linear fixture is auto-created based on its string configuration:
- **LED count** and **string length** come from the child's PONG data
- **Direction** is inferred from the `stripDir` field (E/N/W/S)
- **Pixel positions** are computed by evenly spacing LEDs along the string path

**Rotation override**: Set `rotation: [rx, ry, rz]` (degrees) on a fixture to override the child's strip direction. Example: a string configured as "east" (horizontal) can be made vertical with `rotation: [0, 0, 90]`.

For curved installations, define custom control points via the Fixture API. The system uses Catmull-Rom spline interpolation to place pixels along smooth curves.

### Point Fixtures
For spotlights, pars, or single-LED devices. Defined by a position and an area-of-effect radius. Future DMX fixtures will use this type with channel mappings.

### Resolving Pixel Positions
Use the **Resolve** button (or `POST /api/fixtures/:id/resolve`) to compute the 3D coordinates of every pixel. This must be done before spatial effects can evaluate which pixels they illuminate.

---

## 3. Creating Spatial Effects

### Spatial Effects vs Classic Actions
- **Classic Actions** (Solid, Chase, Rainbow, etc.): Run locally on each child. Pattern based on pixel index. Good for simple animations.
- **Spatial Effects**: Operate in 3D space. A sphere of red light sweeping across the stage will illuminate different fixtures at different times based on their physical position.

### Creating a Spatial Effect
Navigate to the **Actions tab** and click **+ New Spatial Effect**.

#### Spatial Fields
A 3D volume that moves through the stage:

| Field | Description |
|-------|-------------|
| **Shape** | Sphere, Plane, or Box |
| **Color** | RGB color applied to pixels inside the field |
| **Size** | Radius (sphere), thickness (plane), or width/height/depth (box) |
| **Motion Start** | Starting position (x, y, z) in millimeters |
| **Motion End** | Ending position (x, y, z) in millimeters |
| **Duration** | How long the field takes to travel from start to end |
| **Easing** | Linear, ease-in, ease-out, or ease-in-out |
| **Blend** | How this effect combines with others: Replace, Add, Multiply, Screen |

#### Fixture-Local Effects
Wraps one of the 14 classic action types (Chase, Rainbow, etc.) and targets it to specific fixtures. Used in timelines when you want a classic pattern on specific hardware at specific times.

### Blend Modes
When multiple effects overlap on the same pixel:
- **Replace**: Last effect wins completely
- **Add**: Colors are added (clamps at 255)
- **Multiply**: Colors are multiplied (darkens)
- **Screen**: Inverse multiply (brightens)

### Previewing Effects
After creating an effect, use the 3D viewport to preview it. The viewport shows affected pixels lighting up in real-time as the spatial field moves through the stage.

---

## 4. Building a Timeline

### Timeline vs Classic Runners
- **Classic Runners**: Sequential steps — one action after another. Simple, reliable.
- **Timelines**: Multi-track, overlapping effects with precise timing. For complex 3D shows.

Switch between modes using the **[Runners (Classic)] [Timeline (3D)]** toggle in the Runtime tab.

### Creating a Timeline
1. Click **+ New Timeline** and enter a name and duration
2. Select the timeline from the dropdown to open the editor

### The Timeline Editor
The editor shows:
- **Time ruler** at the top (in seconds)
- **Tracks** stacked vertically — one per fixture
- **Clips** as colored rectangles on each track
- **Playhead** — the vertical cyan line showing current time

### Working with Tracks
- Click **+ Add Track** to add a track for a specific fixture
- Each track targets one fixture (or fixture group)
- A "Stage" track can hold global spatial fields that affect all fixtures

### Working with Clips
- Click **+ clip** in the track header to add an effect clip
- Each clip references a spatial effect, with a start time and duration
- Click a clip to edit its timing or change the effect
- Clips can overlap — they blend according to their effect's blend mode

### Scrubbing and Preview
- **Play Preview**: Animates the playhead across the timeline, updating the time display
- **Stop**: Resets playhead to the start
- The time display shows current position in MM:SS.s format

---

## 5. Baking & Playback

### What Is Baking?
Baking is the process of pre-computing a show. The system:
1. Steps through every frame of the timeline (at 40 frames/second)
2. Evaluates all active spatial effects for every pixel
3. Analyzes the resulting color streams to find matching action types
4. Outputs per-fixture action sequences that children already understand

**Why bake?** High-density LED layouts have too many pixels to stream in real-time over WiFi. Baking pre-computes the show so each child runs its part independently.

### Starting a Bake
1. Open a timeline in the Runtime tab
2. Click the **Bake** button
3. A progress modal shows:
   - Frame progress (e.g., "Frame 1200 / 2400")
   - Per-fixture segment counts
   - Total file size when complete

### Bake Output
- **LSQ files**: Raw per-pixel RGB data at 40Hz per fixture
- **Action segments**: Sequences of the 14 classic action types (Solid, Fade, etc.)
- **ZIP bundle**: Download all LSQ files as a single archive

### Syncing to Performers
After baking, click **Sync to Performers** to push the baked action sequences to children using the existing UDP `LoadStepPayload` protocol. Children receive the same packets they always have — the intelligence is in the parent's compilation.

### Starting Playback
Click **Start** to begin synchronized playback:
1. All children receive a `RUNNER_GO` command with a future timestamp (now + 5 seconds)
2. Children begin executing their loaded steps at the same NTP-synced moment
3. The Dashboard shows per-fixture playback status

### Stopping
Click **Stop** to send `RUNNER_STOP` to all children and halt playback.

### Long Shows
Shows with more than 16 steps per fixture are automatically "paged" — the parent re-syncs the next batch of steps during playback with overlap for seamless transitions.

---

## 6. Classic Mode

### When to Use Classic Mode
Classic runners are ideal for:
- Simple sequential shows (one effect after another)
- Quick testing of individual effects
- Shows that don't need spatial awareness
- Backward compatibility with existing setups

### Classic Workflow
1. **Actions tab**: Create action presets (Solid, Chase, Rainbow, etc.)
2. **Runtime tab** (Classic mode): Create a runner with sequential steps
3. **Compute**: Calculate per-performer delays for canvas-scoped effects
4. **Sync**: Load steps to all performers
5. **Start**: Begin synchronized execution

### Flights and Shows
- **Flights**: Assign a runner to a group of performers with priority
- **Shows**: Run multiple flights simultaneously

All classic features continue to work unchanged alongside the new 3D system.

---

## 7. Troubleshooting

### 3D Viewport Not Rendering
- **Cause**: Browser doesn't support WebGL
- **Fix**: Use Chrome, Firefox, or Edge (latest versions). Safari may have limited WebGL support.
- **Check**: Open browser console (F12) and look for Three.js errors

### Performers Not Syncing
- **Cause**: Children are offline or on a different network
- **Fix**: Check the Setup tab for online status. Ensure all devices are on the same WiFi network.
- **Check**: Try refreshing children in the Setup tab

### Bake Errors
- **"No fixtures"**: Add fixtures in the Layout/Fixtures section before baking
- **"No clips"**: Add at least one clip to a track in the timeline
- **Memory**: Large shows (>1000 pixels, >120 seconds) may need significant RAM

### Preview Is Slow
- **Reduce pixel count**: Use fewer LEDs per string for testing
- **Lower preview rate**: The preview polls the server at ~10fps; reduce timeline duration
- **Close other tabs**: The 3D viewport uses GPU resources

### NTP Sync Issues
Synchronized playback requires all devices to agree on the current time:
- All ESP32/D1 Mini children sync via NTP on boot
- The parent uses system time
- A 5-second countdown before start allows for NTP alignment
- If sync is poor, children may start effects slightly out of time

### Factory Reset
Settings tab -> **Factory Reset** clears all data including fixtures, spatial effects, timelines, and baked files. Use with caution.

---

## API Quick Reference

### Stage & Layout
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/layout` | Layout with x, y, z positions |
| GET/POST | `/api/stage` | Stage dimensions (w, h, d meters) |

### Fixtures
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/fixtures` | List / create fixtures |
| GET/PUT/DELETE | `/api/fixtures/:id` | CRUD |
| POST | `/api/fixtures/:id/resolve` | Compute pixel positions |

### Spatial Effects
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/spatial-effects` | List / create |
| GET/PUT/DELETE | `/api/spatial-effects/:id` | CRUD |
| POST | `/api/spatial-effects/:id/evaluate?t=` | Evaluate at time t |

### Timelines
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/timelines` | List / create |
| GET/PUT/DELETE | `/api/timelines/:id` | CRUD |
| POST | `/api/timelines/:id/frame?t=` | Evaluate frame |
| POST | `/api/timelines/:id/bake` | Start baking |
| GET | `/api/timelines/:id/baked/status` | Bake progress |
| GET | `/api/timelines/:id/baked` | Bake result |
| GET | `/api/timelines/:id/baked/download` | Download LSQ zip |
| POST | `/api/timelines/:id/baked/sync` | Sync to children |
| POST | `/api/timelines/:id/start` | Start playback |
| POST | `/api/timelines/:id/stop` | Stop playback |
| GET | `/api/timelines/:id/status` | Playback status |
