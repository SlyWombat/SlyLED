# SlyLED User Manual — 3D Volumetric Lighting System (v1.0)

## Table of Contents
1. [Getting Started](#1-getting-started)
2. [Platform Guide](#2-platforms)
3. [Fixture Setup](#3-fixture-setup)
4. [Stage Layout](#4-layout)
5. [Stage Objects](#5-stage-objects)
6. [Creating Spatial Effects](#6-spatial-effects)
7. [Track Action](#7-track-action)
8. [Building a Timeline](#8-timeline)
9. [Baking & Playback](#9-baking)
10. [Show Preview Emulator](#10-show-preview)
11. [DMX Fixture Profiles](#11-dmx-profiles)
12. [Preset Shows](#12-presets)
13. [Camera Nodes](#13-cameras)
14. [Firmware & OTA Updates](#14-firmware)
15. [System Limits](#15-limits)
16. [Troubleshooting](#16-troubleshooting)
17. [API Quick Reference](#17-api)

---

## 1. Getting Started

SlyLED is a three-tier LED and DMX lighting control system:
- **Orchestrator** (Windows/Mac desktop app or Android app) — design shows and control playback
- **Performers** (ESP32/D1 Mini) — run LED effects on hardware
- **DMX Bridge** (Giga R1 WiFi) — output Art-Net/sACN to DMX fixtures

### Quick Start
1. Launch the desktop app: `powershell -File desktop\windows\run.ps1` (Windows) or `bash desktop/mac/run.sh` (Mac)
2. Open the browser at `http://localhost:8080`
3. Go to **Setup** tab, click **Discover** to find performers on your network
4. Go to **Layout** tab to position fixtures on the stage
5. Go to **Runtime** tab, load a **Preset Show**, click **Bake & Start**

---

## 2. Platform Guide

### Windows Desktop (SPA)
The primary design and control interface. Full-featured 7-tab SPA with 2D/3D layout, timeline editor, spatial effects, DMX profiles, and firmware management.

**Launch:** `powershell -File desktop\windows\run.ps1` or run `SlyLED.exe`
**Install:** Run `SlyLED-Setup.exe` (includes system tray icon)

### Android App
Mobile companion for monitoring and playback control. Available on the same WiFi network as the desktop server.

**Install:** Transfer `SlyLED.apk` to your phone and install.
**Connect:** Enter the server IP address and port (shown on desktop Settings tab).

**Android Features:**
- **Dashboard** — performer status, online/offline indicators
- **Setup** — view fixtures, discover performers
- **Layout** — 2D canvas with pinch-to-zoom, drag-to-reposition, tap-to-place, DMX beam cones, object visualization, layout quick-view buttons, patrol display for moving objects
- **Actions** — browse and create LED effects
- **Runtime** — show emulator with LED string dots and DMX beam cones, timeline bake/sync/play, preset shows
- **Settings** — server name, brightness, factory reset

### Firmware Config (ESP32/D1 Mini)
Each performer serves a 3-tab config page at `http://<device-ip>/config`:
- **Dashboard** — hostname, firmware version, active action status
- **Settings** — device name, description, string count
- **Config** — per-string LED count, length, direction, GPIO pin (ESP32)

---

## 3. Fixture Setup

### What Are Fixtures?
A fixture is the primary entity on the stage. It wraps physical hardware and adds stage-level attributes:
- **LED fixtures** — linked to a performer child, with LED strings
- **DMX fixtures** — linked to a DMX universe/address, with a profile and aim point

### Adding LED Fixtures
1. Go to **Setup** tab, click **Discover** to find performers
2. Click **Add Fixture** → select "LED" type
3. Link to a performer and configure strings (LED count, length, direction)

### Adding DMX Fixtures (Wizard)
Click **+ DMX Fixture** on the Setup tab to launch the 3-step wizard:
1. **Choose Fixture**: Search the Open Fixture Library (700+ fixtures) or create a custom fixture
2. **Set Address**: Universe, start address, and name — with real-time conflict detection
3. **Confirm**: Review all settings, click "Create Fixture"

### DMX Monitor
Settings → DMX → **DMX Monitor** opens a real-time 512-channel grid per universe. Click any cell to set a value. Color-coded by intensity.

### Fixture Group Control
Settings → DMX → **Group Control** opens a control panel for fixture groups. Master dimmer slider, R/G/B sliders, and quick color preset buttons (Warm, Cool, Red, Off).

### Testing DMX Channels
On the Setup tab, click **Details** on any DMX fixture to open the channel test panel:
- **Sliders** for every channel with live DMX output
- **Quick buttons**: All On, Blackout, White, Red, Green, Blue
- **Capability labels** show what each value range does (e.g., "Strobe slow→fast")
- Changes take effect immediately on the physical fixture via Art-Net/sACN

### Fixture Types
| Type | Description |
|------|-------------|
| **Linear** | LED strip. Pixels along a path. |
| **Point** | DMX light source with beam cone. |
| **Group** | Collection of fixtures targeted as one. |

---

## 4. Stage Layout

### 2D Canvas
The Layout tab shows a 2D front view of the stage. Stage dimensions (width × height) are set in Settings.

The layout toolbar provides: Save, 2D/3D mode toggle (shows current mode as text), Recenter, Top view, Front view, Auto-arrange DMX, Show/hide LED strings. Active toggles highlight in green.

Use the `?tab=` URL parameter for deep-linking directly to any tab (e.g., `?tab=layout`).

| Action | Desktop | Android |
|--------|---------|---------|
| **Place fixture** | Drag from sidebar | Tap fixture then tap canvas |
| **Move fixture** | Drag on canvas | Drag on canvas |
| **Remove fixture** | Double-click → Remove | Tap → Edit → Remove |
| **Zoom** | Scroll wheel | Pinch gesture |
| **Pan** | — | Two-finger drag |
| **Edit coordinates** | Double-click | Tap placed fixture |
| **Edit object** | Double-click | Tap object in list |

**What's rendered:**
- Grid lines at 1-meter spacing
- Stage dimension labels
- **LED fixtures**: Green nodes with colored string lines (direction arrows)
- **DMX fixtures**: Purple nodes with beam cone triangles toward aim point
- **Objects**: Semi-transparent rectangles with name labels (clipped to stage)
- **Aim dots**: Red circles at DMX aim points

### 3D Viewport (Desktop Only)
Toggle to 3D mode for an interactive Three.js scene:
- Orbit camera with mouse drag
- Beam cones as 3D geometry
- Draggable aim spheres for DMX fixtures
- Object planes/boxes with transparency

### Move / Rotate Mode

The layout canvas has two interaction modes, toggled with keyboard shortcuts or the toolbar button (which displays **M** or **R** to show the active mode):

| Key | Mode | Description |
|-----|------|-------------|
| **M** | Move | Drag any placed fixture to reposition it (default mode) |
| **R** | Rotate | Click a DMX fixture or camera fixture to show a compass ring; drag around the ring to aim the fixture |

**Rotate mode details:**
- A compass ring appears around the selected fixture when you enter Rotate mode
- Drag clockwise or counter-clockwise to set the horizontal aim direction
- The beam cone updates in real-time as you drag
- In 3D viewport, Rotate mode activates Three.js **TransformControls** in rotate mode — drag the colored arcs to rotate on any axis
- Press **Ctrl+Z** to undo the last move or rotation

**Typical workflow:** Place all fixtures in Move mode, then switch to Rotate mode to aim DMX moving heads and cameras toward their intended focus areas before running calibration.

### Coordinate System
- **aimPoint[0]** = X (horizontal position, mm)
- **aimPoint[1]** = Y (height from floor, mm) — used for 2D canvas vertical axis
- **aimPoint[2]** = Z (depth, mm) — used in 3D viewport only
- **canvasW** = stage width × 1000 (mm)
- **canvasH** = stage height × 1000 (mm)

---

## 5. Stage Objects

Objects represent physical elements on the stage — walls, floors, trusses, screens, and props/performers.

### Object Types
| Type | Default Mobility | Description |
|------|-----------------|-------------|
| **Wall** | Static | Back wall, stage-locked to stage width x height |
| **Floor** | Static | Stage floor, stage-locked to stage width x (depth + 1m) |
| **Truss** | Static | Lighting truss bar |
| **Screen** | Static | Projection surface |
| **Prop** | Moving | Performer, set piece, or mobile element |
| **Custom** | Moving | User-defined object |

### Stage-Locked Objects
Wall and floor objects can be locked to stage dimensions. When you change the stage size in Settings, locked objects automatically resize.

### Mobility
- **Static**: Fixed position. Cannot be tracked by moving heads.
- **Moving**: Position can change at runtime. Trackable by DMX moving heads via Track action.

### Patrol Motion
Moving objects can patrol (oscillate) back and forth during playback:
- **Axis**: Side-to-side (X), front-to-back (Z), or diagonal (X+Z)
- **Speed presets**: Slow (20s cycle), Medium (10s), Fast (5s), or Custom
- **Range**: Start/end percentage of stage dimension (default 10%--90%)
- **Easing**: Smooth (sine) or Linear

Patrol is evaluated at 40Hz in the DMX playback loop, before Track actions read object positions.

### Temporal Objects
External systems can create short-lived objects via `POST /api/objects/temporal`:
- Always in-memory (never saved to disk)
- Require `ttl` > 0 (time-to-live in seconds)
- Auto-expire when TTL elapses
- Position updates refresh the TTL
- Shown in runtime viewer with dashed outline and countdown badge
- Useful for camera tracking integration

---

## 6. Creating Spatial Effects

### Spatial Effects vs Classic Actions
- **Classic Actions** (Solid, Chase, Rainbow, etc.): Run locally on each performer. Pattern based on pixel index. When assigned to DMX fixtures, classic actions are automatically converted to DMX Scene segments with appropriate dimmer, pan/tilt defaults.
- **DMX Actions**: Control DMX-specific features directly:
  - **DMX Scene** — Set exact values for dimmer, pan, tilt, strobe, gobo, color wheel, prism
  - **Pan/Tilt Move** — Animate pan/tilt from start to end position over time
  - **Gobo Select** — Select a gobo wheel position
  - **Color Wheel** — Select a color wheel position
  - **Track** (Type 18) — Make moving heads follow moving objects in real-time (see [Track Action](#7-track-action))
- **Spatial Effects**: Operate in 3D space. A sphere of light sweeping across the stage illuminates different fixtures at different times.

SlyLED supports 19 action types in total: 14 classic LED actions plus 5 DMX/spatial actions (DMX Scene, Pan/Tilt Move, Gobo Select, Color Wheel, Track).

### Creating a Spatial Effect
Navigate to **Actions** tab → **+ New Spatial Effect**.

| Field | Description |
|-------|-------------|
| **Shape** | Sphere, Plane, or Box |
| **Color** | RGB color applied to pixels inside the field |
| **Size** | Radius (sphere), thickness (plane), or dimensions (box) |
| **Motion Start/End** | 3D positions in millimeters |
| **Duration** | Travel time from start to end |
| **Easing** | Linear, ease-in, ease-out, ease-in-out |
| **Blend** | Replace, Add, Multiply, Screen |

---

## 7. Track Action

### Track Action (Type 18)
Makes DMX moving heads follow moving objects in real-time during playback.

**How it works:**
1. Create moving objects (props/performers) on the Layout tab
2. Create a Track action on the Actions tab
3. Select target objects and configure assignment
4. During playback, the 40Hz loop computes pan/tilt for each head

**Assignment algorithm:**
- Equal heads and objects: 1:1 mapping
- More heads than objects: Spread evenly across objects
- More objects than heads: Cycle through objects (default 2s per target)

**Fields:**
| Field | Description |
|-------|-------------|
| trackObjectIds | Target object IDs (empty = all moving objects) |
| trackCycleMs | Cycle time when cycling (default 2000ms) |
| trackOffset | Global [x,y,z] offset in mm |
| trackFixtureIds | Specific fixture IDs (empty = all moving heads) |
| trackFixtureOffsets | Per-fixture [x,y,z] overrides |
| trackAutoSpread | Spread multiple heads across object width |

---

## 8. Building a Timeline

1. Go to **Runtime** tab → **+ New Timeline**
2. Set name and duration
3. **+ Add Track** for each fixture (or "All Performers")
4. **+ Add Clip** to assign effects with start time and duration
5. Clips can overlap — they blend according to their effect's blend mode

---

## 9. Baking & Playback

### Bake
Compiles a timeline into minimal action instructions per performer:
1. Click **Bake** → progress shows frame count and segments
2. Click **Sync** to push instructions to performers via UDP
3. Click **Start** for synchronized NTP-timed playback

### Output
- **Action segments**: Sequences of the 19 action types (14 classic + 5 DMX/spatial)
- **LSQ files**: Raw per-pixel RGB data at 40Hz (downloadable as ZIP)
- **Preview data**: 1 color per string per second for emulator

---

## 10. Show Preview Emulator

Both desktop and Android include a real-time show preview:

### Dashboard Preview
When a show is running, the Dashboard tab shows a live stage preview canvas alongside the performer status table and playback progress bar.

### Desktop SPA
The emulator canvas appears on the Runtime tab below the timeline. Shows:
- **LED fixtures**: Colored dots along string paths with glow effects
- **DMX fixtures**: Beam cone triangles with preview-driven colors
- **Aim dots**: Red circles at aim points
- **Fixture labels**: Names below each node
- **Time counter**: MM:SS elapsed / total

### Android App
The `ShowEmulatorCanvas` card shows:
- Same LED string dots and DMX beam cones as desktop
- Objects rendered as background rectangles
- Preview colors update every second during playback

### Spatial Field Visualization
During show playback, the runtime emulator renders the active spatial effects moving across the stage:
- **Sphere**: translucent colored circle moving along the motion path
- **Plane**: translucent horizontal or vertical band sweeping across the stage
- **Box**: translucent rectangle at the effect's current position
- Effect names shown as labels at their current position
- Updates every frame, synced to playback elapsed time

### DMX-Only Rigs
The emulator correctly renders DMX-only setups (no LED performers). Static purple beam cones always visible, with live colors when a show is running.

---

## 11. DMX Fixture Profiles

### Built-in Profiles
| Profile | Channels | Features |
|---------|----------|----------|
| Generic RGB | 3 | Red, Green, Blue |
| Generic RGBW | 5 | Red, Green, Blue, White, Dimmer |
| Generic Dimmer | 1 | Intensity only |
| Moving Head 16-bit | 16 | Pan, Tilt, Dimmer, Color, Gobo, Prism |

### Profile Editor
Settings tab → **Profiles** → **New Profile** or **Edit**:
- Define channels with name, type (red/green/blue/dimmer/pan/tilt/etc.), default value
- Set beam width, pan/tilt range for moving heads
- Import from Open Fixture Library (OFL) JSON format

### Browsing the Open Fixture Library
Click **Search OFL** in Settings → Profiles to access 700+ fixtures from the [Open Fixture Library](https://open-fixture-library.org):

**Search**: Type a fixture name, manufacturer, or keyword → results show with Import buttons.

**Browse by Manufacturer**: Click **Manufacturers** to see all brands with fixture counts. Click a manufacturer to see all their fixtures. Click **Import All** to import every fixture from that manufacturer at once.

**Bulk Import**: From search results, click **Import All** to import all matching fixtures. From a manufacturer page, click **Import All** for the entire brand catalog.

Multi-mode fixtures create one SlyLED profile per mode automatically.

### Community Fixture Library
Share and discover profiles with other SlyLED users:

1. **Browse**: Click **Community** in Settings > Profiles to search, view recent, or popular
2. **Download**: Click Download — imported to your local library immediately
3. **Share**: Click **Share** on any custom profile to upload to the community
4. **Dedup**: Server detects duplicates by channel fingerprint (same channels = same fixture)
5. **Unified search**: When adding a DMX fixture, search queries Local + Community + OFL at once

Community server: https://electricrv.ca/api/profiles/

### Import/Export
- **Community**: Share/download profiles with other users
- **Search OFL**: Browse, search, and bulk import from the Open Fixture Library
- **Paste OFL**: Paste raw OFL JSON for offline fixtures
- **Import Bundle**: Load previously exported profile pack
- **Export**: Download all custom profiles as JSON
- **Built-in profiles** cannot be edited or deleted

---

## 12. Preset Shows

14 pre-built shows available from Runtime tab → **Load Show** → **Presets**:

| Preset | Description |
|--------|-------------|
| Rainbow Up | Rainbow plane rising floor to ceiling |
| Rainbow Across | Rainbow sphere sweeping left to right |
| Slow Fire | Warm fire effect on all fixtures |
| Disco | Pastel twinkle sparkles |
| Ocean Wave | Blue wave sweep with teal wash |
| Sunset Glow | Warm breathe with golden sweep |
| Police Lights | Red strobe with blue flash sweep |
| Starfield | White sparkles on dark background |
| Aurora Borealis | Green curtain with purple shimmer |
| Spotlight Sweep | Warm orb — moving heads track it |
| Concert Wash | Magenta flood + amber tracking spot |
| Figure Eight | Crossing orbs — heads trace X paths |
| Thunderstorm | Lightning strikes — heads chase bolts |
| Dance Floor | Fast orbiting spots — rapid tracking |

---

## 13. Camera Nodes

Camera nodes are Orange Pi or Raspberry Pi single-board computers with **USB cameras**. They provide live snapshots and AI-powered object detection for stage setup.

> **Note:** Only USB cameras are supported. Pi CSI ribbon cameras (e.g. Pi Camera Module, Freenove FNK0056) are not supported in v1.x. Use USB webcams instead.

### Adding a Camera Node
1. Flash an Orange Pi with the supported OS image
2. Connect it to the same WiFi network as the orchestrator
3. In the **Firmware** tab, configure SSH credentials (default: `root` / `orangepi`)
4. Click **Scan for Boards** to find the device on the network
5. Click **Install** to deploy the camera firmware via SSH+SCP

### Camera Config Page
Each camera node serves a local web interface at `http://<camera-ip>:5000/config`:
- **Dashboard** — board info, per-camera cards with live capture and detection
- **Settings** — device name, reboot, factory reset

### Snapshots
Click **Capture Frame** on any camera card to take a JPEG snapshot. Uses OpenCV for fast capture with fswebcam fallback.

### Object Detection
Click **Detect Objects** (purple button) to run YOLOv8n AI detection on the current camera frame:
- Bounding boxes with labels and confidence percentages are drawn on a canvas overlay
- **Threshold slider** (0.1–0.9) — filter by detection confidence
- **Resolution** (320/640) — lower is faster, higher is more accurate
- **Auto checkbox** — continuously detect every 3 seconds
- Typical latency: ~500ms capture + ~500ms inference on Orange Pi 4A

Detection requires the YOLOv8n ONNX model (`models/yolov8n.onnx`, 12 MB), which is uploaded automatically during firmware deployment.

### Camera Deploy
The deploy process (from the **Firmware** tab) uploads all camera files via SCP:
- `camera_server.py`, `detector.py`, `requirements.txt`, `slyled-cam.service`
- `models/yolov8n.onnx` (detection model)
- Installs system packages (`python3-opencv`, `python3-numpy`, `v4l-utils`)
- Installs Python packages (`flask`, `zeroconf`, `onnxruntime`)
- Sets up the `slyled-cam` systemd service for auto-start on boot
- Shows version comparison and supports force-reinstall

### Multi-Camera Support
Each node can host multiple USB cameras. The firmware auto-detects connected cameras and filters out internal SoC video nodes. Each camera gets its own card in the config page with independent capture and detection controls.

### Environment Scanning
The **Scan Environment** button on the Layout toolbar captures a 3D point cloud of the physical space:
1. Each positioned camera captures a frame and runs depth estimation
2. Pixels are back-projected to 3D using camera FOV and depth
3. Point clouds from all cameras are merged into stage coordinates
4. **Surface analysis** identifies floor, walls, and obstacles (pillars, furniture)
5. Detected surfaces can be automatically created as named stage objects

The point cloud can be viewed as colored dots in the 3D viewport (toggle with the point cloud button). This gives a visual map of the physical environment that the lights will illuminate.

### Per-Camera Fixtures
Each USB camera sensor on a camera node registers as a **separate fixture** in the layout. A node with 2 cameras creates 2 fixtures, each with:
- Its own position on the stage (independently placeable)
- Its own FOV and resolution
- Its own rest direction vector (cyan arrow)

### Mover Calibration

The mover calibration wizard builds an interpolation grid that maps every stage position to the exact pan/tilt angles required for a DMX moving head. A positioned camera node is required.

**Prerequisites:**
- At least one camera node positioned on the Layout tab
- Art-Net engine running (`POST /api/dmx/start`)
- Moving head fixture placed on the layout with its profile configured

**Starting calibration:**
1. Go to the **Layout** tab and double-click a DMX moving head fixture
2. Click the **Calibrate** button in the fixture edit dialog
3. Choose a beam color — options are Green, Magenta, Red, Blue (pick one that contrasts with your stage)
4. Click **Start Calibration** — the wizard takes over automatically

**What happens automatically:**
1. **Discover** — the head sweeps through a coarse pan/tilt grid; the camera detects where the beam lands on the stage floor
2. **Map visible region** — the pan/tilt range that keeps the beam within the camera's field of view is identified
3. **Build interpolation grid** — the head systematically samples points across the visible region; at each point the camera records the exact stage coordinates

**Progress:** A real-time progress panel shows the current phase, percentage complete, and a live thumbnail from the camera.

**Result:** The interpolation grid is saved to the fixture and used automatically by the Track action and any Pan/Tilt Move actions to convert stage-space coordinates into hardware pan/tilt values.

> **Tip:** Run calibration in dim ambient lighting so the beam is clearly visible to the camera. Use the **Beam Color** option that gives the highest contrast against your floor surface.

### Fixture Orientation Test

Before running full calibration, use the orientation test to confirm pan and tilt are wired in the expected directions. Incorrect orientation causes calibration to converge on wrong positions.

**Running the test:**
1. Double-click a DMX moving head on the Layout tab to open the fixture edit dialog
2. Click **Orientation Test** (below the channel map)
3. The fixture moves through four probe positions: pan left, pan right, tilt up, tilt down
4. Watch the physical beam and compare with the on-screen arrows showing expected direction

**Interpreting results:**
| Observation | Action |
|-------------|--------|
| Beam matches arrows | Orientation is correct — proceed to calibration |
| Pan moves opposite direction | Enable **Invert Pan** in the fixture settings |
| Tilt moves opposite direction | Enable **Invert Tilt** in the fixture settings |
| Pan and tilt axes are swapped | Enable **Swap Pan/Tilt** in the fixture settings |

**Saving:** After adjusting orientation flags, click **Save** in the fixture edit dialog. The flags are stored with the fixture and applied automatically during all subsequent calibration and playback.

---

## 14. Firmware & OTA Updates

### USB Flash
1. Go to **Firmware** tab
2. Select COM port and firmware binary
3. Click **Flash** — progress shows percentage

### OTA (Over-the-Air)
1. Set WiFi credentials on the Firmware tab
2. Click **Check for Updates** — shows per-device version comparison
3. Click **Update** on any outdated performer
4. Device reboots automatically after flash

### Firmware Registry
`firmware/registry.json` lists available binaries with board type and version. The OTA system compares the registry version against each performer's reported firmware.

---

## 15. System Limits

| Resource | Tested | Recommended Max |
|----------|--------|-----------------|
| DMX fixtures | 120 | 500+ |
| LED performers | 12 | 50 |
| Total fixtures | 132 | 500+ |
| Universes | 4 | 32,768 (Art-Net) |
| LEDs per string | 65535 | uint16 addressing |
| Strings per child | 8 | Protocol constant |
| Timeline clips | 50 | 200+ |
| Preset shows | 14 | Built-in (expandable) |
| API response (132 fixtures) | < 1ms | Sub-millisecond |
| Memory (132 fixtures) | 46 MB | Flat scaling |
| Network (132 fixtures) | 221 KB | Per test cycle |

See `docs/STRESS_TEST.md` for full benchmark data.

---

## 16. Troubleshooting

| Problem | Solution |
|---------|----------|
| **Runtime view empty** | Check fixtures are positioned in Layout. DMX-only rigs now render (v8.1 fix). |
| **Beam cone wrong direction** | aimPoint[1] is height (Y), not depth (Z). Check aim point values. |
| **Android JSON crash** | Update to v8.1 — aimPoint changed from Int to Double. Factory reset: now requires confirm header. |
| **Save Show error** | Update to v8.1 — `/api/show/export` endpoint was missing. |
| **Firmware check fails** | Update to v8.1 — registry.json UTF-8 BOM and dict iteration bugs fixed. |
| **3D viewport not rendering** | Use Chrome/Firefox/Edge with WebGL support. |
| **Performers not syncing** | Check all devices on same WiFi network. Refresh in Setup tab. |
| **Canvas wrong size** | Stage dimensions (Settings) drive canvas size: canvasW = stage.w × 1000. |

---

## 17. API Quick Reference

### Stage & Layout
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/layout` | Layout with fixtures and positions |
| GET/POST | `/api/stage` | Stage dimensions (w, h, d meters) |
| GET/POST | `/api/objects` | Stage objects (walls, floors, trusses, props) |
| POST | `/api/objects/temporal` | Create temporal objects (TTL-based) |

### Fixtures
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/fixtures` | List / create |
| GET/PUT/DELETE | `/api/fixtures/:id` | CRUD |
| PUT | `/api/fixtures/:id/aim` | Set aim point |

### Shows & Timelines
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/api/timelines` | List / create |
| POST | `/api/timelines/:id/bake` | Start baking |
| POST | `/api/timelines/:id/start` | Start playback |
| GET | `/api/show/presets` | List preset shows |
| GET/POST | `/api/show/export`, `/api/show/import` | Save/load show file |

### DMX
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/dmx-profiles` | List profiles |
| GET | `/api/dmx/patch` | Universe address map |
| POST | `/api/dmx/start`, `/api/dmx/stop` | Engine control |

### Cameras
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cameras` | List registered camera fixtures |
| POST | `/api/cameras` | Register a camera node as fixture |
| DELETE | `/api/cameras/:id` | Remove camera fixture |
| GET | `/api/cameras/:id/snapshot` | Proxy JPEG snapshot |
| GET | `/api/cameras/:id/status` | Live status from camera node |
| POST | `/api/cameras/:id/scan` | Object detection (proxy to node `/scan`) |
| GET | `/api/cameras/discover` | Find camera nodes on network |
| GET/POST | `/api/cameras/ssh` | SSH credentials for deployment |
| POST | `/api/cameras/deploy` | Deploy firmware to camera node via SSH+SCP |
| GET | `/api/cameras/deploy/status` | Poll deploy progress |

### Camera Node Local API (port 5000)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Node status, capabilities, camera list |
| GET | `/config` | HTML config page with detection UI |
| GET | `/snapshot?cam=N` | JPEG snapshot from camera N |
| POST | `/scan` | Object detection (JSON: cam, threshold, resolution, classes) |
| GET | `/health` | Health check |
