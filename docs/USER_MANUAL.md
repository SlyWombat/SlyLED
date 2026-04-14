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
17. [Examples](#17-examples)
18. [API Quick Reference](#18-api)

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
Live operator tool for running shows from your phone. Connects to the desktop server over WiFi.

**Install:** Transfer `SlyLED-debug.apk` to your phone and install.
**Connect:** Scan the QR code on the desktop Settings tab, or enter the server IP and port manually.

![Android Stage View](screenshots/android/android-stage-idle.png)

**Android Screens:**
- **Stage** — live viewport showing all fixtures (LED, DMX, cameras) with beam cones, tracked object markers, and grid floor. Pinch-to-zoom and drag-to-pan. HUD shows current show status. Play/Stop button and brightness slider at bottom.
- **Control** — one-tap timeline start, playlist with loop toggle, global brightness slider, and Pointer Mode for aiming DMX moving heads with your phone's gyroscope.
- **Status** — device monitoring (performers online/offline, RSSI, firmware), camera nodes with Track button to start/stop person tracking, and Art-Net/DMX engine status.
- **Settings** — server name, stage dimensions, dark mode, config export/import, disconnect.

![Android Control](screenshots/android/android-control.png)
![Android Status](screenshots/android/android-status.png)

**Pointer Mode:** Select a DMX moving head on the Control tab and tap its name under Pointer Mode. Hold your phone and point where you want the light — the fixture's pan/tilt follows your phone orientation in real-time at 20 Hz. Tap Recenter to calibrate, X to exit.

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
- More objects than heads (cycling mode): Cycle through objects (default 2s per target)
- More objects than heads (fixed mode): Each head locks to one target, extras ignored

**Fields:**
| Field | Description |
|-------|-------------|
| trackObjectIds | Target object IDs (empty = all moving objects, including camera-detected people) |
| trackCycleMs | Cycle time when cycling (default 2000ms) |
| trackOffset | Global [x,y,z] offset in mm |
| trackFixtureIds | Specific fixture IDs (empty = all moving heads) |
| trackFixtureOffsets | Per-fixture [x,y,z] overrides |
| trackAutoSpread | Spread multiple heads across object width |
| trackFixedAssignment | Fixed 1:1 assignment — each head gets one target, extra targets ignored |

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

### Tracking Configuration

Each camera fixture has per-camera tracking settings accessible from the **Edit** dialog on the Setup tab. These control what the camera detects and how it behaves during live tracking.

![Camera edit with tracking config](screenshots/spa-setup-edit-camera.png)

**Detect Classes** — Multi-select the object types to track. The YOLOv8n model supports 80 COCO classes; 16 stage-relevant classes are available:

| Category | Classes |
|----------|---------|
| People | Person |
| Animals | Cat, Dog, Horse |
| Props | Chair, Backpack, Suitcase, Sports Ball, Bottle, Cup, Umbrella, Teddy Bear |
| Vehicles | Bicycle, Skateboard, Car, Truck |

By default only **Person** is selected. Adding more classes has zero performance impact — YOLO always evaluates all classes in one pass and filters afterward.

**Parameters:**

| Parameter | Default | Range | Description |
|-----------|---------|-------|-------------|
| FPS | 2 | 0.5–10 | Detection frames per second. Higher = more responsive but more CPU on the camera node. |
| Threshold | 0.4 | 0.1–0.95 | Minimum confidence to accept a detection. Lower = more sensitive but more false positives. |
| TTL (s) | 5 | 1–60 | Seconds before a lost track expires and its stage marker is removed. |
| Re-ID (mm) | 500 | 50–5000 | Maximum distance to match a new detection to an existing tracked object. |

**Starting tracking:** Click the **Track** button on the Setup tab (next to Snap) or in the fixture edit modal on the Layout tab. The camera node begins continuous detection using your configured classes and parameters. Detected objects appear as labeled markers in the 3D viewport.

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

## 17. Examples

### Example A: Camera Tracking — Moving Heads Follow a Person (#376)

Make DMX moving heads automatically follow people detected by a camera.

**Prerequisites:**
- At least one camera node online (Firmware tab → deploy + verify)
- At least one DMX moving head fixture placed on the Layout tab
- Moving head profile configured with pan/tilt range
- Art-Net/sACN engine running (Settings → DMX → Start)
- Mover calibration completed (see Example C) for accurate aiming

**Steps:**

1. **Verify hardware** — Open the Setup tab. Confirm your movers show green status and camera nodes show online. If cameras are offline, check WiFi and deploy firmware from the Firmware tab.

2. **Start camera tracking** — Click the **Track** button on the Setup tab (next to Snap), or go to the Layout tab, click a camera fixture, and click the **Track** button in the edit modal. The camera node begins running YOLO detection using the classes and parameters configured in the camera's tracking settings (see [Tracking Configuration](#tracking-configuration)). Detected objects appear as labeled markers in the 3D viewport.

3. **Create a Track action** — Go to the Actions tab. Click **+ New Action**.
   - **Name:** `Person Follow`
   - **Type:** `Track` (last option in the dropdown)
   - **Colour:** Pick the beam color (e.g. red for a spotlight)
   - Leave **Target Objects** empty — this means "follow ALL detected people"
   - **Cycle Time:** 2000 ms (how fast heads switch if cycling)
   - Check **Fixed assignment** if you want strict 1:1 (head 1 = person 1, extras ignored)
   - Click **Save Action**

4. **Create a timeline** — Go to the Shows tab. Click **+ New Timeline**, name it "Person Tracking", set duration to 600s, enable **Loop**. The timeline can be empty — Track actions evaluate globally during any playback.

5. **Start playback** — Click **Bake**, then **Start**. The 40 Hz DMX playback loop begins. The Track action reads all moving temporal objects (detected people), computes pan/tilt for each head, sets dimmer and color, and sends Art-Net packets to the bridge.

6. **Test** — Walk in front of the camera. Within 2 seconds a pink person marker appears in the 3D viewport. The moving heads should light up in your chosen color and aim at you.

**Assignment behavior:**

| People in view | With 2 moving heads |
|----------------|---------------------|
| 1 person | Both heads aim at the same person |
| 2 people | One head per person (1:1) |
| 3+ people (cycling) | Heads cycle through people every 2s |
| 3+ people (fixed) | First 2 tracked, 3rd ignored |

**Troubleshooting:**

| Problem | Solution |
|---------|----------|
| No person markers in 3D | Check camera node status — is tracking running? Try a manual Scan to verify detection works. |
| Person detected but heads don't move | Check Art-Net engine is running. Check mover calibration. Verify timeline playback is active. |
| Heads light up but aim at wrong position | Run mover calibration (Example C). Without calibration, the system uses geometric estimates which may be inaccurate. |
| Heads respond with delay | Normal — detection runs at 2 fps with ~1s capture latency. Temporal objects have 5s TTL. |

---

### Example B: Mover Tracking with Spatial Effects (#379)

Make moving heads follow a virtual object sweeping across the stage — no camera required. This example walks through the complete workflow from stage setup to live 3D preview with animated beam cones.

**Prerequisites:**
- SlyLED orchestrator running (Windows or Mac)
- No physical hardware required — this example runs entirely in the emulator

**Part 1 — Stage and Fixture Setup**

1. **Set stage dimensions** — Open the Settings tab. Under **Stage**, enter the dimensions of your performance area:
   - Width: 6000 mm (6 m)
   - Height: 3000 mm (3 m)
   - Depth: 4000 mm (4 m)
   - Click **Save**. The 3D viewport will resize to match these dimensions.

2. **Create a DMX profile** — Go to Settings → **Profiles** → click **New Profile**. This defines the channel layout of your moving head:
   - **Name:** `Narrow Spot`
   - **Beam Width:** 8 (degrees — narrow beam for visible tracking)
   - **Pan Range:** 540, **Tilt Range:** 270
   - **Channels:** Add 6 channels in this order:
     - Ch 0: Pan (16-bit) — pan coarse
     - Ch 1: Pan Fine — pan fine (auto-linked)
     - Ch 2: Tilt (16-bit) — tilt coarse
     - Ch 3: Tilt Fine — tilt fine (auto-linked)
     - Ch 4: Dimmer
     - Ch 5: Red, Ch 6: Green, Ch 7: Blue
   - Click **Save Profile**

![Profile editor with narrow spot configuration](screenshots/example-b-profile.png)

3. **Add two movers** — Go to the Setup tab. Click **+ Add Fixture** twice to create two DMX moving heads:
   - **Fixture 1:** Name: `Mover SL` (Stage Left), Universe: 1, Start Address: 1, Profile: `Narrow Spot`
   - **Fixture 2:** Name: `Mover SR` (Stage Right), Universe: 1, Start Address: 14, Profile: `Narrow Spot`

   Both fixtures appear in the Setup table with purple "DMX" badges and the profile name.

**Part 2 — 3D Layout and Spatial Effect**

4. **Position movers on the truss** — Switch to the Layout tab. In the sidebar, you'll see both movers listed as "unplaced." Drag each one into the 3D viewport:
   - **Mover SL:** Position at X: 1500, Y: 0, Z: 2800 (stage left, on truss). Set rotation to tilt: -30, pan: -15.
   - **Mover SR:** Position at X: 4500, Y: 0, Z: 2800 (stage right, on truss). Set rotation to tilt: -30, pan: 15.

   Switch to 3D view to confirm both movers are elevated on the truss and aimed downward toward the stage floor. The beam cones should be visible as translucent triangles.

![Layout tab — 3D view with two movers positioned on truss](screenshots/example-b-layout-3d.png)

5. **Create a spatial effect** — Go to the Actions tab. Click **+ New Action**:
   - **Name:** `Sweep Green`
   - **Type:** Spatial Effect
   - **Shape:** Sphere
   - **Radius:** 800 mm
   - **Color:** Green (0, 255, 0)
   - **Motion Start:** X: 1000, Y: 2000, Z: 0 (stage left, mid-depth, floor level)
   - **Motion End:** X: 5000, Y: 2000, Z: 0 (stage right, same depth and height)
   - **Duration:** 8 seconds
   - **Easing:** Linear
   - Click **Save Action**

   This creates a green sphere of light that sweeps from stage left to stage right over 8 seconds. When applied to moving heads, they will track the sphere's center position.

![Actions tab with Sweep Green spatial effect configured](screenshots/example-b-action.png)

**Part 3 — Timeline, Bake, and Playback**

6. **Create a timeline** — Go to the Shows tab. Click **+ New Timeline**:
   - **Name:** `Mover Tracking Demo`
   - **Duration:** 20 seconds
   - **Loop:** Enabled
   - Add a track targeting **All Performers**
   - Add a clip referencing the `Sweep Green` effect, starting at 0s with 8s duration

![Shows tab with timeline containing the spatial effect clip](screenshots/example-b-timeline.png)

7. **Bake the timeline** — Click the **Bake** button. The bake engine computes per-fixture pan/tilt angles for each time slice:
   - For each 25ms frame, it calculates the sphere's position along the motion path
   - For each mover, it computes the pan/tilt angles needed to aim at that position
   - Dimmer is set to 255 and color channels are set to green
   - Wait for "Bake complete" confirmation (typically < 1 second)

8. **Start playback and verify** — Switch to the Runtime tab. Click **Start**:
   - The 3D viewport shows both beam cones animated in real-time
   - At T=0s, both beams aim at the starting position (stage left)
   - As the effect sweeps, the beams track the green sphere across the stage
   - At T=8s, both beams have followed the sphere to stage right
   - The timeline loops, and the sweep restarts

![Runtime — beam cones at start position (T=0s)](screenshots/example-b-tracking-t0.png)
![Runtime — beams tracking mid-sweep (T=5s)](screenshots/example-b-tracking-t5.png)
![Runtime — beams at end position (T=10s)](screenshots/example-b-tracking-t10.png)

**What to look for:**
- Both beam cones should be green (matching the effect color)
- The cones should move smoothly from left to right
- The beam intensity (opacity) should be > 0 during the sweep, indicating active output
- If beam cones don't appear, ensure fixtures are positioned in the Layout tab and the timeline is baked

**Variations:**
- Change the spatial effect shape to **Plane** for a wall of light sweeping across
- Add a second effect on a separate track with different timing for crossing patterns
- Try the **Figure Eight** preset show (Runtime → Load Show) for a ready-made crossing pattern

---

### Example C: Manual Mover Calibration (#381)

Calibrate a moving head so the system knows exactly where its beam lands for any pan/tilt position. This two-part process first discovers the beam's visible range (pan/tilt grid) and then builds a light map that maps every pan/tilt position to real stage coordinates.

**Prerequisites:**
- At least one camera node online and positioned on the Layout tab
- Camera calibration complete — the camera must have a valid stage map (see Example D)
- Moving head fixture added in Setup and positioned on the Layout tab
- Art-Net/sACN engine running (Settings → DMX → Start)
- Dim ambient lighting — the beam must be clearly visible to the camera against the floor
- The beam should be aimed at the floor within the camera's field of view, not directly at the camera

**Part 1 — Pan/Tilt Discovery and Grid Calibration**

1. **Open the calibration panel** — Go to the Layout tab. Double-click the moving head fixture you want to calibrate. In the edit dialog, click the **Calibrate** button. The calibration wizard opens showing the fixture name, current calibration status (if any), and available calibration modes.

![Calibration panel before starting — shows fixture name and calibration options](screenshots/example-c-calibrate-panel.png)

2. **Choose beam color** — Select a color that contrasts well with your floor surface:
   - **Green** works best on dark floors (wood, dark carpet)
   - **Magenta** works best on light floors (white, concrete)
   - **Red** or **Blue** are alternatives if the default choices blend with your environment
   - The color matters because the camera uses color filtering to isolate the beam from ambient light

3. **Run discovery** — Click **Start Calibration**. The system runs an automatic discovery sequence:
   - **Phase 1 — Coarse grid scan:** The fixture sweeps through ~40 pan/tilt positions (8 columns x 5 rows) across its full range. The camera watches for the beam appearing on the floor after each move.
   - **Phase 2 — Fine refinement:** Once the beam is found, the system spirals outward from that position to refine the exact center of the visible region.
   - Discovery typically completes in 30-60 seconds. The progress indicator shows "Discovering..." with the current scan position.

![Discovery in progress — coarse grid scan with camera watching for beam](screenshots/example-c-discovery.png)

4. **BFS mapping** — After discovery, the system automatically maps the full visible region:
   - Starting from the discovered beam position, it steps in 4 directions (up/down/left/right in pan/tilt space)
   - At each position, the camera captures a frame and detects the beam centroid
   - The system records the beam's pixel position and converts it to stage millimeters using the camera's homography
   - Mapping stops at boundaries where the beam leaves the camera's field of view or falls off the stage
   - Collects up to 60 sample positions, typically completing in 2-3 minutes
   - The system uses adaptive settle times (0.8-2.5s) per move and double-capture verification to ensure the beam has stopped before recording

5. **Grid build and review** — The collected samples are compiled into a bilinear interpolation grid:
   - The calibration summary displays:
     - **Sample count:** Number of successfully detected positions (aim for 30+)
     - **Pan range:** Normalized range (e.g., 0.15-0.85 means the beam is visible across 70% of pan range)
     - **Tilt range:** Normalized range
     - **Grid density:** How finely the grid was sampled
   - The grid enables fast forward lookup: given a (pan, tilt) value, compute the stage (X, Y) where the beam lands

![Grid calibration complete — summary showing sample count, pan/tilt range, and grid density](screenshots/example-c-grid-result.png)

**Part 2 — Light Map Calibration (stage coordinates to pan/tilt lookup)**

6. **Build the light map** — Click **Build Light Map**. This extends the calibration by sweeping a systematic 20x15 grid across the discovered visible region:
   - For each grid position, the fixture moves to the pan/tilt value
   - The camera detects the beam and records the exact stage X/Y/Z where it lands
   - This builds a comprehensive (pan, tilt) → (stageX, stageY, stageZ) lookup table
   - Progress shows as "Building light map... N/300" with real-time updates
   - Typical completion time: 5-10 minutes for a full 20x15 grid

![Light map build in progress — systematic sweep with stage coordinate mapping](screenshots/example-c-light-map.png)

7. **Verify inverse lookup** — After the light map is built, use the **Aim** button to test the inverse mapping:
   - Enter a target stage position (e.g., center stage: X=3000, Y=2000, Z=0)
   - Click **Aim** — the system uses inverse-distance weighted interpolation of the 4 nearest light map samples to compute the exact pan/tilt values
   - The fixture moves to the computed position
   - Verify visually that the beam lands on (or very near) the target point on stage
   - Try 3-4 different targets across the stage to confirm accuracy
   - Good calibration should place the beam within 100-200mm of the target at typical stage distances

![Aim verification — beam aimed at target stage position using calibrated light map](screenshots/example-c-aim-verify.png)

8. **Save calibration** — Calibration data is automatically saved with the fixture. The light map and grid data persist across sessions and are included in project file exports (.slyshow).
   - Track actions use the light map to aim at detected people
   - Pan/Tilt Move actions use it for smooth interpolated sweeps
   - The 3D viewport uses it to render accurate beam cone directions

**Manual calibration (alternative — no camera required):**

If automated calibration isn't available (no camera, or camera can't see the beam), use the manual calibration wizard:

1. Layout tab → double-click mover → click **Manual Calibrate**
2. **Define marker positions** — Add 4-6 physical markers at known stage positions. Enter each marker's X, Y, Z coordinates (in mm). Spread markers across the stage: front-left, front-right, back-center at minimum.
3. **Jog to each marker** — For each marker, use the pan/tilt sliders to manually aim the beam until it lands exactly on the physical marker. Click **Record** to save the (pan, tilt) → (stageX, stageY, stageZ) sample.
4. **Add at least 4 samples** spread across the stage for a good affine fit. More samples (6+) improve accuracy, especially at stage edges.
5. Click **Compute** — the system fits a 3D affine transform from your samples:
   - `pan = a1*stageX + b1*stageY + c1*stageZ + d1`
   - `tilt = a2*stageX + b2*stageY + c2*stageZ + d2`
   - The affine transform extrapolates beyond calibrated points for full-stage coverage

**When to re-calibrate:**
- Fixture physically moved to a new position or angle
- Venue change (different stage dimensions or floor surface)
- After firmware update that changes pan/tilt range or motor behavior
- If aim accuracy degrades over time (motor drift)
- After changing the fixture's mounting orientation (upright vs. inverted)

---

### Example D: Camera Calibration with ArUco Markers (#380)

Calibrate a camera so pixel coordinates can be mapped to real stage positions. This is a prerequisite for beam detection, person tracking, and mover calibration — without it, the system cannot convert what the camera sees into real-world stage millimeters.

**Prerequisites:**
- Camera node online and reachable on the network (deploy firmware from the Firmware tab if needed)
- Camera fixture registered in the system (Setup tab → Discover, or Settings → Cameras → add manually)
- Camera fixture placed on the Layout tab at its physical position
- A printer to print the ArUco marker sheet (standard A4/Letter paper)
- A tape measure to record marker positions on stage
- The camera must have a clear view of the stage floor where markers will be placed

**Part 1 — Prepare and Place ArUco Markers**

1. **Print ArUco markers** — Go to Settings → Cameras. Click the **Print ArUco Markers** button. A modal opens with 6 printable ArUco 4x4 markers (IDs 0-5), each 150mm x 150mm:
   - Click **Download** or use the browser's print dialog to print the marker sheet
   - Print at 100% scale (no scaling/fit-to-page) — the physical size must match the expected 150mm for accurate calibration
   - Markers can be printed on regular white paper, but card stock is more durable

![ArUco marker print dialog — 6 markers ready to print](screenshots/example-d-print-markers.png)

2. **Place markers on the stage floor** — Position the printed markers at known locations on the stage:
   - **Minimum:** 3 markers (enough for a basic homography)
   - **Recommended:** 4-6 markers for better accuracy
   - **Placement strategy:**
     - Spread markers across the entire camera's field of view
     - Place at least one marker near each corner of the visible area
     - Place markers flat on the floor — tilted markers reduce accuracy
     - Measure each marker's position from the stage origin (back-right corner at floor level):
       - X = distance from stage right (mm)
       - Y = distance from back wall (mm)
       - Z = 0 (floor level)
   - Record the marker ID and its (X, Y) coordinates — you'll enter these in step 5

**Part 2 — Register and Position the Camera**

3. **Register the camera** — If the camera node is not already registered:
   - Go to the Setup tab and click **Discover** — camera nodes respond to UDP broadcast
   - Or go to Settings → Cameras → enter the camera's IP address manually
   - Each USB camera sensor on the node appears as a separate fixture
   - Verify the camera is online: its status should show "Online" with a green indicator

![Camera configuration panel in Settings — camera list with IP, status, and calibration badges](screenshots/example-d-camera-config.png)

4. **Position the camera in 3D** — Switch to the Layout tab:
   - Find the camera fixture in the sidebar (listed as "unplaced" if new)
   - Drag it into the 3D viewport at the camera's real physical position
   - Set the rotation to match the camera's actual aim direction:
     - A camera mounted on a wall at 2m height, aimed down at 30 degrees would have rotation Z=2000, tilt=-30
   - In 3D view, the camera appears as a frustum (pyramid) showing its field of view
   - Verify the frustum covers the area where you placed the ArUco markers

**Part 3 — Run Calibration and Verify**

5. **Run ArUco calibration** — In the Layout tab, click on the camera fixture to select it. Click the **Calibrate** button:
   - The wizard opens and fetches a live snapshot from the camera
   - The system automatically detects all visible ArUco markers and highlights them with green overlays
   - **For each detected marker:**
     - The marker ID is shown on the overlay
     - Enter the marker's real-world stage coordinates (X, Y in mm) that you measured in step 2
     - Click **Record** to save the pixel-to-stage mapping for this marker
   - After recording all markers, click **Compute** — the system builds a homography matrix that maps any pixel coordinate to stage floor coordinates

![Camera snapshot with ArUco markers detected — green overlays showing marker IDs](screenshots/example-d-detection.png)

6. **Review calibration results** — The calibration summary shows:
   - **Reprojection error:** How accurately the computed homography matches the recorded points. Lower is better:
     - < 10mm: Excellent — suitable for precision tracking
     - 10-20mm: Good — adequate for most use cases
     - 20-50mm: Fair — consider adding more markers or re-measuring positions
     - > 50mm: Poor — re-check marker measurements and try again
   - **Reference points:** Number of markers used (should match what you recorded)
   - **Coverage area:** The stage area covered by the calibration (larger is better)

![Calibration complete — reprojection error, reference points, and coverage summary](screenshots/example-d-result.png)

7. **Save and apply** — Click **Save** to persist the calibration:
   - The camera fixture badge updates to show a green "Cal" checkmark
   - All features that depend on pixel-to-stage conversion now use this calibration:
     - **Person tracking:** Detected bounding boxes are converted to stage positions
     - **Beam detection:** Beam centroids become stage coordinates for mover calibration
     - **Mover calibration:** The entire mover calibration wizard (Example C) requires this
   - Calibration data is included in project file exports (.slyshow) for portability

**Tips for accurate calibration:**
- **Marker size matters:** Use the 150mm markers at standard print size. Smaller markers are harder to detect at distance.
- **Flat placement is critical:** Even a slight tilt (marker on a crumpled surface) can shift the detected center by 10-20mm.
- **Cover the edges:** The homography is most accurate within the convex hull of your reference markers. Place markers at the extremes of the camera's view, not just the center.
- **Lighting conditions:** ArUco detection works in most lighting, but avoid direct glare on the printed markers (glossy paper under bright lights).
- **Re-calibrate when:**
  - The camera is physically moved (even slightly)
  - The camera lens is changed or zoom is adjusted
  - Stage dimensions change (markers would be at different positions)
  - Accuracy of tracking or beam detection degrades

---

### Example E: Spotlight Follow Person — Live Tracking Preset (#382)

Use the built-in **Spotlight: Follow Person** preset to make moving heads automatically follow people detected by a camera in real-time.

**Prerequisites:**
- At least one camera node online with person detection working (verify with a manual Scan first)
- At least one DMX moving head fixture placed on the Layout tab
- Camera calibration complete (see Example D) for accurate stage positioning
- Mover calibration complete (see Example C) for accurate pan/tilt aiming
- Art-Net/sACN engine running

**Steps:**

1. **Load the preset** — Go to the Runtime tab. Click **Load Show** (or the preset dropdown). Select **Spotlight: Follow Person** from the preset list.
   - If no camera node is registered, a warning appears: "No camera node registered — person detection will not work"
   - If no moving heads are configured, a warning appears about missing movers
   - The preset loads even with warnings — you can add the missing hardware later

2. **What it creates** — The preset automatically configures:
   - A **Track action** (type 18) on every available moving head, targeting `objectType: "person"`
   - A warm spotlight color (255, 240, 200) at full dimmer for the beam
   - A dim blue ambient wash (10, 5, 30) on all LED fixtures for atmospheric framing
   - A 10-minute looping timeline that keeps the DMX playback loop running

3. **Start camera tracking** — Click the **Track** button on the Setup tab or in the camera fixture edit modal. The camera node begins running detection using the configured tracking classes and parameters (see [Tracking Configuration](#tracking-configuration)). Detected objects appear as labeled markers in the 3D viewport.

4. **Start playback** — Click **Bake**, then **Start**. The 40 Hz DMX playback loop begins. The Track action reads all temporal person objects and computes pan/tilt for each head in real-time.

5. **Walk on stage** — Within 2 seconds of entering the camera's view, a pink person marker appears. Moving heads light up with the warm spotlight color and aim at you. As you move, the beams follow.

**Behavior with multiple people:**
- 1 person, 2 heads: Both heads aim at the same person
- 2 people, 2 heads: One head per person (auto-spread)
- 3+ people, 2 heads: Heads cycle through people every 2 seconds

**When no one is detected:**
- Heads dim to 0 (blackout) and hold their last position
- As soon as a person is detected again, heads immediately re-aim and light up

**Tips:**
- Use a narrow beam profile (8-15 degrees) for a dramatic spotlight effect
- Ensure the room is dim enough for the camera to distinguish the beam from ambient light
- If tracking seems jittery, increase the camera's capture FPS or reduce the confidence threshold
- The Track action works alongside other timeline effects — you can add spatial color washes on lower-priority tracks

---

## 18. API Quick Reference

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
