## 2. Walkthrough: First Show in 30 Minutes

This walkthrough builds a complete DMX moving-head show from scratch — hardware discovery, fixture setup, layout, camera registration, actions, timeline, and playback. Every step was validated end-to-end during QA testing (issue #533). Follow in order; each step builds on the last.

**What you need:**
- SlyLED orchestrator running on Windows or Mac
- At least one DMX moving head connected via Art-Net/sACN bridge (e.g. Enttec ODE Mk3)
- At least one USB camera node on the network (Orange Pi or Raspberry Pi)
- All devices on the same LAN subnet as the orchestrator

---

### Step 1 — Launch and Create a New Project

Start the orchestrator:

```powershell
powershell -File desktop\windows\run.ps1
```

Open `http://localhost:8080` in Chrome or Edge. The SPA loads on the Dashboard tab.

![SPA at launch showing Dashboard tab](screenshots/walkthrough-533/01-launch.png)

Go to **Settings** tab → **Project** → click **New Project**, then name it (e.g. "Walkthrough Show").

![New project dialog](screenshots/walkthrough-533/02-new-project.png)

---

### Step 2 — Set Stage Dimensions

In **Settings** → **Stage**, enter the dimensions of your performance area:
- Width: 6000 mm (6 m)
- Height: 3000 mm (3 m)
- Depth: 4000 mm (4 m)

Click **Save**. The layout canvas will resize to match these dimensions.

---

### Step 3a — Discover DMX Hardware

Go to the **Setup** tab. In the **DMX Nodes** section, click **Discover Nodes**. SlyLED broadcasts an ArtPoll packet; Art-Net bridges on the network reply within 3 seconds.

![Setup tab after hardware discovery — Art-Net node shown](screenshots/walkthrough-533/03a-discover-hardware.png)

Any discovered nodes appear in the list with their IP, port, and universe count. If your bridge is not found:
- Confirm it is powered and on the same LAN subnet
- Check that UDP port 6454 is not blocked by a local firewall
- Some bridges require the Art-Net source IP to match their configured subnet

---

### Step 3b — Configure and Start the DMX Engine

Go to **Settings** → **DMX**:

1. **Universe Routing**: Set Universe 1 → your Art-Net node IP (or leave as broadcast `255.255.255.255` to reach all nodes on the subnet).
2. Click **Start Engine**. The status indicator turns green ("Running").

![DMX engine configuration — universe routing and start](screenshots/walkthrough-533/03b-dmx-engine.png)
![DMX routing — universe 1 assigned to bridge](screenshots/walkthrough-533/03-dmx-routing.png)

> **Important:** The engine must be running before adding DMX fixtures or running calibration. If you stop and restart the orchestrator, re-start the engine here.

---

### Step 4 — Add DMX Moving Head Fixtures

Go to the **Setup** tab → click **+ DMX Fixture**. The fixture wizard opens.

**Finding the right profile:**
1. In the **Search** box, type your fixture's name (e.g. "Sly Moving Head Super Mini")
2. Results show Local profiles first, then Community (from the shared library), then OFL (Open Fixture Library)
3. If a community profile download fails ("imported: 0"), it may contain unsupported channel types — fall back to a local generic profile or search OFL directly
4. For a generic 16-channel moving head with no exact match, search OFL for "moving head" and import the closest match

**Fixture 1 (stage left):**
- Name: `MH1 SL`
- Universe: 1, Start Address: 1
- Profile: your moving head profile
- Click **Create Fixture**

![Moving head 1 added to Setup tab](screenshots/walkthrough-533/04a-mh1-sly-added.png)

**Fixture 2 (stage right):**
- Name: `MH2 SR`
- Universe: 1, Start Address: 17
- Profile: same profile
- Click **Create Fixture**

![Moving head 2 added to Setup tab](screenshots/walkthrough-533/04b-mh2-sly-added.png)

---

### Step 5 — Add a Wash or Spot Fixture

Add any additional fixtures (e.g. a 350W wash spot):
- Name: `Spot C`
- Universe: 1, Start Address: 33
- Profile: your spot/wash profile

![350W spot added to Setup tab](screenshots/walkthrough-533/05-350w-spot-added.png)

---

### Step 6 — Register Camera Nodes

Still on the **Setup** tab, scroll to the **Camera Nodes** section. Click **Discover Cameras**. Camera nodes running `slyled-cam` respond to the same UDP broadcast as performers.

Alternatively, enter the camera's IP manually and click **Add**.

**Camera 1 (left):**
- IP: your camera's IP (e.g. `192.168.10.50`)
- Name: `Cam Left`

**Camera 2 (right):**
- IP: your second camera's IP
- Name: `Cam Right`

![Two cameras added to Setup tab](screenshots/walkthrough-533/06-cameras-added.png)

Each camera appears with online/offline status. Click **Snap** to verify the live feed.

![Camera 1 snapshot — left-side view](screenshots/walkthrough-533/06-cam1_left_hires.png)
![Camera 2 snapshot — right-side view](screenshots/walkthrough-533/06-cam2_right.png)

> **Note:** Camera discover sometimes returns 0 nodes on the first broadcast due to UDP timing. If no cameras are found, wait 3 seconds and click **Discover** again. This is a known intermittency (#542) being addressed in a future release.

---

### Step 7 — Position All Fixtures on the Layout

Switch to the **Layout** tab. All added fixtures appear in the left sidebar as "unplaced."

**Place and position each fixture:**

1. Click a fixture in the sidebar to select it
2. Click on the canvas to place it, or drag from the sidebar
3. Double-click the placed fixture to open the edit dialog and enter exact coordinates

| Fixture | X (mm) | Y (mm) | Z (mm) |
|---------|--------|--------|--------|
| MH1 SL | 1500 | 3000 | 500 |
| MH2 SR | 4500 | 3000 | 500 |
| Spot C | 3000 | 3000 | 500 |
| Cam Left | 0 | 2500 | 0 |
| Cam Right | 6000 | 2500 | 0 |

Click **Save** after entering coordinates for each fixture.

![Layout tab — fixtures placed at initial positions](screenshots/walkthrough-533/04c-layout-initial.png)
![Layout tab — all fixtures positioned](screenshots/walkthrough-533/04d-layout-positions.png)
![Camera fixtures positioned on layout](screenshots/walkthrough-533/06c-cameras-positioned.png)

> **Tip:** Use 3D view (toggle in the layout toolbar) to visually verify that movers are elevated on the truss and aimed downward toward the stage floor.

---

### Step 8 — Add a Stage Object

Go to the **Layout** tab → click **+ Object** in the toolbar.

- **Name:** `Music Stand`
- **Type:** Prop (moving — trackable by movers)
- **Position:** X: 3000, Y: 0, Z: 2000 (center stage, at floor level, mid-depth)
- **Size:** 300 × 1200 × 300 mm

Click **Save**. The object appears as a labeled rectangle on the canvas.

![Music stand object on layout](screenshots/walkthrough-533/08-music-object.png)

---

### Step 9 — Run Mover Calibration

Before the moving heads can accurately track positions, calibrate each one. This step requires the camera nodes to be positioned on the layout (Step 7) and the DMX engine running (Step 3b).

In the **Layout** tab, double-click `MH1 SL`. Click **Calibrate**.

![Calibration buttons in fixture edit dialog](screenshots/walkthrough-533/07-calibrate-buttons.png)
![Calibration wizard UI](screenshots/walkthrough-533/07-calibrate-ui.png)

- Select **Green** as the beam color (good contrast on dark floors)
- Click **Start Calibration**
- The wizard runs automatically through eight phases: warmup → discovery → blink-confirm → mapping/convergence → grid build → verification sweep → model fit → held-out parametric gate → save
- Repeat for `MH2 SR`

Calibration typically takes 2–4 minutes per head. For the complete phase-by-phase reference — what each phase does, how long it should take, what fallbacks exist, and what to check when a phase stalls — see [Appendix B — Moving-Head Calibration Pipeline](#appendix-b--moving-head-calibration-pipeline-draft).

---

### Step 10 — Create Actions

Go to the **Actions** tab. You'll create two actions: a static aim and a figure-eight sweep.

**Action 1: Aim Red (static spotlight)**
1. Click **+ New Action**
2. **Name:** `Aim Red`
3. **Type:** `DMX Scene`
4. **Colour:** Red (255, 0, 0)
5. **Dimmer:** 255
6. Click **Save Action**

![Aim Red action — aimed at stage center](screenshots/walkthrough-533/09-aim-red.png)

**Action 2: Figure Eight (dynamic sweep)**
1. Click **+ New Action**
2. **Name:** `Figure Eight`
3. **Type:** `Track`
4. **Target Objects:** leave empty (track all moving objects)
5. **Cycle Time:** 4000 ms
6. Click **Save Action**

![Figure Eight track action](screenshots/walkthrough-533/11d-figure8-action.png)

---

### Step 11 — Build a Timeline

Go to the **Runtime** tab (labeled **Shows** in some versions). Click **+ New Timeline**.

A dialog prompts for the name — enter `Walkthrough Show`. A second dialog prompts for duration — enter `120` (seconds). Click OK.

![Timeline editor with tracks](screenshots/walkthrough-533/11e-timeline.png)

**Add tracks:**

For each fixture or group, click **+ Add Track**:
- Track for `MH1 SL` — add clip: `Aim Red` at 0s, duration 10s
- Track for `MH1 SL` — add clip: `Figure Eight` at 10s, duration 110s
- Track for `MH2 SR` — add clip: `Figure Eight` at 0s, duration 120s
- Track for `All Performers` — add clip with ambient wash color

> The Track action (type 18) evaluates in real-time during playback and doesn't need to be baked per-frame — it reads live object positions at 40 Hz.

---

### Step 12 — Bake and Start Playback

1. Click **Bake** — the engine compiles the timeline into per-fixture action sequences. Progress shows frame count.
2. Click **Start** — NTP-synchronized playback begins.

Watch the **Runtime** view:
- Beam cones animate in 3D as the timeline plays
- The figure-eight pattern moves through the stage space
- DMX output is sent via Art-Net to the physical fixtures

![Runtime view with animated beam cones](screenshots/walkthrough-533/11f-runtime.png)

To test a blackout:
- Click **Stop**, then fire a **Blackout** action from the Settings → Group Control panel

![Blackout state — all beams off](screenshots/walkthrough-533/10-blackout.png)

---

### Step 13 — Save the Project

Go to **Settings** → **Project** → click **Export**. A `.slyshow` file is downloaded containing all fixtures, layout positions, objects, camera registrations, calibration data, actions, and timelines.

To reload: Settings → Project → **Import** → select the `.slyshow` file.

![Project saved — all state bundled in .slyshow file](screenshots/walkthrough-533/12-saved.png)

---

### Walkthrough Troubleshooting

| Problem | Solution |
|---------|----------|
| **No Art-Net nodes discovered** | Confirm bridge is on the same subnet; UDP port 6454 not blocked |
| **DMX engine won't start** | Check Settings → DMX → verify universe routing is configured |
| **Community profile download fails** | Profile has unsupported channel types — use local or OFL profile instead |
| **Fixture position resets to 0,0,0** | Ensure `saveFixture()` completes before switching tabs; use the edit dialog Save button |
| **Camera discover returns 0** | Wait 3s and retry — first broadcast may arrive before socket is bound (#542) |
| **Calibration fails to detect beam** | Dim ambient light, verify beam color contrasts with floor, check camera can see the beam |
| **Figure Eight doesn't move heads** | Verify Track action has no `trackFixtureIds` restriction; confirm engine is running |
| **Timeline tracks missing after create** | Add tracks manually after timeline creation — they are not auto-created |

---

