# SlyLED User Manual — 3D Volumetric Lighting System (v8.0)

## Table of Contents
1. [Getting Started with 3D Stage Design](#1-getting-started)
2. [Fixture Setup](#2-fixture-setup)
3. [Creating Spatial Effects](#3-spatial-effects)
4. [Building a Timeline](#4-timeline)
5. [Baking & Playback](#5-baking)
6. [Show Preview Emulator](#6-show-preview)
7. [Preset Shows](#7-presets)
8. [System Limits](#8-limits)
9. [Troubleshooting](#9-troubleshooting)

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
Baking compiles a timeline into minimal action instructions for each performer. The smart bake engine analyzes each clip's spatial geometry directly — it does NOT render frames.

**How it works:**
1. For each spatial effect clip, compute the intersection timing between the effect volume and each pixel's 3D position
2. Detect sweep patterns (brightness moving along a string) → emit WIPE_SEQ with computed speed per pixel and direction
3. For stationary effects → emit SOLID or FADE
4. For classic action clips → pass the action type directly (children already know how to run Chase, Fire, Rainbow, etc.)

**Example: Rainbow Across (sphere sweeping left to right)**
- ESP Dual string 0 (West): `WIPE_SEQ direction=West, speed=34ms/pixel` — pixels light up sequentially from end toward node
- ESP Dual string 1 (East): `WIPE_SEQ direction=East, speed=65ms/pixel` — pixels light up from node outward
- D1 Mini: `WIPE_SEQ at t=13.2s, speed=34ms/pixel` — starts later as sphere reaches it

This produces **2-3 instructions per fixture** instead of 16 averaged color blocks. Each child runs the action locally — the actual per-pixel sweep happens on the hardware.

**Why bake?** Children can't receive per-pixel streaming over WiFi. Baking pre-computes the optimal action type + parameters + timing so each child runs its part independently. Bake time is ~200ms regardless of show duration.

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

## 6. Show Preview Emulator

Both the desktop SPA and Android app include a real-time show preview emulator on the Runtime tab.

### How It Works
- The bake engine generates preview data: 1 dominant color per string per second
- When a show starts, the emulator renders a canvas showing all fixtures at their layout positions
- Per-string colored lines update every second, synced to the server's elapsed time
- Time counter shows current position vs total duration

### Desktop SPA
The emulator canvas appears below the timeline detail section after clicking "Sync & Start". Fixtures use actual string directions from the child config. Dark strings show as dim gray lines.

### Android App
The `ShowEmulatorCanvas` card appears between the "Now Playing" progress card and the timeline list. Fixtures are distributed across the canvas with colored lines and glow effects.

---

## 7. Preset Shows

14 pre-built shows are available from the Runtime tab or Settings:

| Preset | Type | Description |
|--------|------|-------------|
| Rainbow Up | Spatial plane | Moving rainbow from floor to ceiling |
| Rainbow Across | Spatial sphere | Rainbow sweeping left to right |
| Slow Fire | Classic action | Warm fire effect on all fixtures |
| Disco | Classic action | Pastel twinkle sparkles |
| Ocean Wave | Spatial (2 effects) | Blue wave sweep with teal wash |
| Sunset Glow | Mixed | Warm breathe with golden plane sweep |
| Police Lights | Mixed | Red strobe with blue box flash sweep |
| Starfield | Classic action | White sparkles on dark background |
| Aurora Borealis | Spatial (2 effects) | Green curtain with purple shimmer |
| Spotlight Sweep | Spatial (moving heads) | Warm orb sweeps stage — heads track it |
| Concert Wash | Mixed (moving heads) | Magenta flood + amber tracking spot |
| Figure Eight | Spatial (moving heads) | Crossing orbs — heads trace X paths |
| Thunderstorm | Mixed (moving heads) | Lightning strikes — heads chase bolts |
| Dance Floor | Mixed (moving heads) | Fast orbiting spots — rapid tracking |

Each preset creates a timeline with an "All Performers" stage track. Classic actions run on every fixture simultaneously. Spatial effects sweep across the stage based on fixture positions. DMX moving heads automatically track the spatial effect center with pan/tilt.

---

## 8. System Limits

| Resource | Limit | Notes |
|----------|-------|-------|
| Children (performers) | 8 max | Protocol constant `MAX_STR_PER_CHILD` |
| Strings per child | 8 max | ESP32 supports up to 8 GPIO pins |
| LEDs per string | 65535 max | uint16_t addressing (protocol v4) |
| Total LEDs per child | 255 max | `NUM_LEDS` / `MAX_LEDS` in firmware |
| Steps per runner | 16 max | `LoadStepPayload` array limit |
| Timelines | Unlimited | Stored in JSON |
| Tracks per timeline | Unlimited | Expanded per-fixture during bake |
| Clips per track | Unlimited | |
| Bake frame rate | 40 Hz | `BAKE_FPS` constant |
| Bake segments per fixture | 16 max | Fits in runner step limit |
| Per-string segments | 8 max per string | `16 / string_count` |
| Preview resolution | 1 fps | 1 color per string per second |
| Show duration | No hard limit | Memory scales with duration × pixels |
| Sync verify retries | 3 | HTTP status check per performer |
| NTP sync offset | 5 seconds | GO command sent with future epoch |
| UDP packet size | 56 bytes | `LoadStepPayload` (protocol v4) |
| WiFi performers | ~8 practical | UDP broadcast bandwidth limit |

### Memory Estimates
- **Bake RAM**: `duration_s × 40 × pixel_count × 3 bytes` (e.g., 60s × 40fps × 300px = 2.2 MB)
- **Preview RAM**: `duration_s × string_count × 3 bytes` (e.g., 60s × 3 strings = 540 bytes)
- **LSQ file size**: `frames × pixels × 3 bytes` (e.g., 2400 frames × 300px = 2.1 MB)

---

## 9. Troubleshooting

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
