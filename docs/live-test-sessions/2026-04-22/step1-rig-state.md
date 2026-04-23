# Step 1 — Rig state snapshot

**Captured:** 2026-04-22 (live session re-run)  
**Project:** `TestLED`  
**Orchestrator:** `http://localhost:8080` (branch `claude/review-camera-implementation-pqyfH`)

## Stage
- w = 4.0 m  (4000 mm)
- d = 3.6 m  (3600 mm)
- h = 2.06 m  (2060 mm)
- Spec (#533): 4000 × 3620 × 2060 — **match within 20 mm rounding on depth**.

## Cameras (3)

| id | name | host | IP | cam idx | fov° | res | pos (mm) | rotation | calibrated | online |
|----|------|------|----|---------|------|-----|----------|----------|------------|--------|
| 12 | Stage Right | Tracking | 192.168.10.235 | 0 | 100 | 1920x1080 | (940, 110, 2040) | [30, 0, 0] | True | True |
| 13 | Stage Left | Tracking | 192.168.10.235 | 1 | 90 | 3840x2160 | (1730, 110, 2040) | [30, 0, 0] | True | True |
| 16 | Out Left | RPi-Sly1 | 192.168.10.109 | 0 | 78 | 3840x2160 | (2350, 1670, 905) | [1, 0, 0] | None | True |

## DMX fixtures (3)

| id | name | profile | pos (mm) | rotation |
|----|------|---------|----------|----------|
| 14 | 350W Outdoor Waterproof BeamLight | `beamlight-350w-16ch` | (1700, 0, 550) | [0, 0, 0] |
| 17 | 150W MH Stage Right | `movinghead-150w-12ch` | (600, 0, 1760) | [0, 0, 0] |
| 18 | 150W MH Stage Left | `movinghead-150w-12ch` | (1730, 0, 1760) | [0, 0, 0] |

## ArUco markers (6, dictId=50)

| id | label | pos (mm) | size |
|----|-------|----------|------|
| 0 | Back Right | (500.0, 2280.0, 0.0) | 150.0 |
| 1 | Furnace Door | (2050.0, 3170.0, 0.0) | 150.0 |
| 2 | Pillar Base | (1150.0, 2100.0, 0.0) | 150.0 |
| 3 | Pillar Post | (1150.0, 2280.0, 1368.0) | 150.0 |
| 4 | Stairs | (500.0, 3500.0, 0.0) | 150.0 |
| 5 | Patent | (3120.0, 3090.0, 0.0) | 150.0 |

## Children (performer/bridge nodes) — 1

| id | hostname | IP | board | fw | rssi |
|----|----------|----|----|-----|------|
| 0 | SLYC-1152 | 192.168.10.219 | giga-dmx | 7.5.20 | -47 |

## Stage objects (2)

- **Music** (id=1) type=`custom` pos=(800, 2200, 1250) scale=(100, 100, 10)
- **Pillar** (id=4) type=`pillar` pos=(1150, 2415, 1030) scale=(210, 2060, 270)

Spec (#533) pillar: pos (1150, 2415, 1030), size W×H×D = 210×2060×270 — **match exact**.

## Deviations vs spec (issue #533)

- Fixture names differ from spec (`MH1-Sly`/`MH2-Sly`/`350W-Spot` → `150W MH Stage Right`/`150W MH Stage Left`/`350W Outdoor Waterproof BeamLight`). Cosmetic; positions match.
- Stage depth 3600 mm vs spec 3620 — 20 mm rounding in `/api/stage` (stored in metres).

## Pass/fail

Step 1 passes — all fixtures present and placed, all 6 markers surveyed, DMX bridge online, Pillar obstacle placed correctly.