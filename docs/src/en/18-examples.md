## 18. Examples

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

