# Person Tracking Show — End-User Test Procedure

**Date:** 2026-04-13
**Project file:** `tests/user/basement/basement.slyshow`
**Goal:** Load the basement show and create a show where cameras detect people and moving heads track them.

## Hardware Required

| Item | Details |
|------|---------|
| Moving heads | Sly MH 1 (ID 2, addr 14/uni 1), Sly MH 2 (ID 7, addr 1/uni 1) — both calibrated, inverted |
| Camera node | Orange Pi at 192.168.10.235 (Left Cam Wide ID 12 + Right High Res ID 13) |
| DMX bridge | Giga at 192.168.10.219 (SLYC-1152) |
| Network | All on same subnet (192.168.10.x, ktown WiFi) |

## Expected Behavior

| People in view | Moving head assignment |
|----------------|----------------------|
| 1 person | Both MH 1 and MH 2 track the same person |
| 2 people | MH 1 tracks person 1, MH 2 tracks person 2 (1:1) |
| 3+ people | MH 1 tracks person 1, MH 2 cycles between remaining people (2s cycle) |

> **Note:** The "3+ people" behavior doesn't match the ideal (3rd person gets nothing).
> See GitHub issue #374 for the fixed-assignment feature request.

## Pre-Flight Checks

- [ ] SlyLED orchestrator running (`desktop/windows/run.ps1`)
- [ ] DMX bridge online (Setup tab shows Giga with green status)
- [ ] Camera node online (Setup tab shows Orange Pi cameras)
- [ ] Both movers responding to DMX (test: Actions tab → manual DMX Scene action → fire)
- [ ] Mover calibrations loaded (Layout tab → click mover → "Calibrated" badge visible)

## Test Steps

### Step 1: Load Basement Project

1. Open SlyLED in browser (http://localhost:5000)
2. Go to **Settings** tab
3. Click **Import Project**
4. Select `tests/user/basement/basement.slyshow`
5. Wait for import confirmation

**Verify:** Setup tab shows 2 DMX fixtures (Sly MH 1, Sly MH 2) and camera fixtures.

### Step 2: Start Camera Tracking

1. Go to **Layout** tab
2. In the 3D viewport, locate the camera fixture (Left Cam Wide, ID 12)
3. Click the camera fixture to select it
4. Click **Track** toggle button to start person tracking
5. Status bar should show: *"Tracking active — watching for people"*

**Verify:** Walk in front of camera. After ~2s, a pink dashed-outline "person" object should appear in the 3D viewport. It should move as you move. It disappears ~5s after leaving the frame.

### Step 3: Create a Track Action

1. Go to **Actions** tab
2. Click **+ New Action**
3. Set **Name:** `Person Follow`
4. Set **Scope:** `All Fixtures`
5. Set **Type:** `Track` (last option in dropdown, index 18)
6. Leave **Target Objects** empty (this means "follow ALL detected people" — see #375)
7. Leave **Cycle Time** at 2000ms default
8. Leave offsets at 0
9. Click **Save Action**

**Verify:** New action "Person Follow" appears in the actions list with type "Track".

> **Known bug (#373):** The cycle time, offsets, and auto-spread fields are saved with
> wrong field names and silently dropped. The defaults (all objects, all movers, 2s cycle)
> still work because the engine uses sensible defaults for missing fields.

### Step 4: Create a Tracking Timeline

1. Go to **Shows** tab (or Runtime tab → timeline area)
2. Click **+ New Timeline**
3. Set **Name:** `Person Tracking`
4. Set **Duration:** 300 (5 minutes — long enough for testing)
5. Check **Loop:** enabled
6. Click **+ Add Track** → select **Stage (All)**
7. Click **+ Add Clip** on the new track
8. Select the "Person Follow" action
9. Set clip **Start:** 0s, **Duration:** 300s (full timeline)
10. Click **Save**

### Step 5: Bake and Play

1. Click **Bake** on the Person Tracking timeline
2. Wait for bake to complete (should be fast — Track actions are real-time, not pre-baked)
3. Click **Start** to begin playback

**Verify:** The playback progress bar starts advancing.

### Step 6: Test Person Tracking

#### Test 6a: Single Person
1. Walk in front of the camera
2. Wait ~2s for detection

**Expected:** Both moving heads aim at you. They should follow as you move left/right across the room.

#### Test 6b: Two People
1. Have a second person enter the camera view
2. Stand apart (>500mm to avoid re-ID merge)

**Expected:** Each moving head locks onto a different person. MH 1 tracks person closer to its "chunk" in the target list, MH 2 tracks the other.

#### Test 6c: Three People
1. Have a third person enter the camera view

**Expected:** Two heads track two people. The remaining person is covered by cycling (one head alternates between two people every 2 seconds).

#### Test 6d: Exit and Re-enter
1. All people leave the camera view
2. Wait 5+ seconds (TTL expiry)

**Expected:** Moving heads stop updating (hold last position). Temporal objects disappear from 3D viewport.

3. One person re-enters

**Expected:** New temporal object created. Both heads converge on the single person.

### Step 7: Stop

1. Click **Stop** on the timeline playback
2. Go to Layout tab → click camera → click **Track** toggle to stop tracking

**Verify:** Moving heads hold final position, then blackout when playback fully stops.

## Known Issues Discovered

| Issue | Severity | Workaround |
|-------|----------|------------|
| #373 — Track action field name mismatch | Critical | Defaults work for simple case (all objects, all movers) |
| #374 — No fixed assignment mode | Enhancement | Accept cycling behavior for 3+ people |
| #375 — Temporal objects not in target picker | UX | Leave targets empty = auto-track all |
| #376 — No end-to-end workflow docs | Documentation | This test document serves as interim guide |

## Calibration Verification

If aiming seems off, check calibration:

1. Layout tab → click a moving head
2. Verify "Calibrated" badge is shown
3. If not calibrated, run the Mover Calibration wizard:
   - Click **Calibrate** → follow the manual calibration steps
   - Sample at least 4 stage positions
   - Save calibration

The basement show includes calibration data for both movers:
- MH 1: Pan 0.298–0.400, Tilt 0.753–1.000, 4 sample points
- MH 2: Pan 0.286–0.459, Tilt 0.776–1.000, 4 sample points

## Architecture Reference

```
Camera (Orange Pi)
  └─ tracker.py: YOLO person detection @ 2fps
       └─ POST /api/objects/temporal {objectType:"person", mobility:"moving", ttl:5}
            └─ Orchestrator: _temporal_objects[] (auto-expire after TTL)

DMX Playback Loop (40Hz)
  └─ _evaluate_track_actions()
       ├─ Reads _actions where type==18
       ├─ Collects _objects + _temporal_objects where mobility=="moving"
       ├─ Assigns heads to targets (spread / 1:1 / cycle)
       ├─ compute_pan_tilt_calibrated() or compute_pan_tilt()
       └─ Writes pan/tilt to DMX universe buffer → Art-Net → bridge → light
```
