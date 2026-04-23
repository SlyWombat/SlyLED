# Camera calibration live-test — remote-control session handoff

**From:** remote Claude Code session (mobile, branch
`claude/review-camera-implementation-pqyfH`, sandbox has no LAN access
to 192.168.10.x).
**To:** local Claude Code session (`/remote-control` active on the
Windows dev machine, direct hands on the orchestrator + basement rig).
**Date:** 2026-04-22
**Branch to stay on:** `claude/review-camera-implementation-pqyfH`
**Primary doc:** `docs/camera-calibration-review.md` (read before
starting — §2 principles, §8.1 findings, §8.2 Q6 open).

---

## 1. Why you're being handed this

I finished §8.1 of `docs/camera-calibration-review.md` — 13 of 14
review questions are closed on paper. Only **Q6** (default
mover-cal mode) remains and it needs the basement rig. While we're
in front of the rig, we also want **numeric baselines** for Q1, Q3,
Q7, Q8, Q12 so the P1/P2 implementation work afterwards has
before/after comparisons.

**Rules from §2 of the review doc:**
- No code changes during this session. Measurement only.
- Write every result into `docs/live-test-sessions/2026-04-22/`
  (this folder already exists) and commit — no ephemeral state.
- First beta, no backward-compat concerns; if a test destroys
  calibration state, that's fine, we'll re-run stage-map.

---

## 2. Rig reference (from `project_basement_rig.md`)

- Orchestrator: Flask on Windows, **port 8080** (`parent_server.py`
  at 12918, `--port` defaults to 8080). Call it `ORCH=http://127.0.0.1:8080`.
- Camera node: Orange Pi at **192.168.10.235:5000** (two EMEET 4K
  sensors, one service).
- Three surveyed ArUco markers (DICT_4X4_50, 150 mm, floor Z=0) —
  registry served by `GET /api/aruco/markers`.
- DMX fixtures: two ceiling-ish movers + one floor-mount 350 W
  BeamLight. IDs come from `GET /api/fixtures`.
- Camera-visible floor band Y ∈ [1400, 2967] mm — near-field is
  blind.

Before starting, sanity-check:

```bash
curl -s $ORCH/api/cameras | jq '.[] | {id, name, cameraIp, cameraIdx, fovDeg, rotation}'
curl -s $ORCH/api/fixtures | jq '[.[] | select(.fixtureType=="dmx") | {id, name, dmxProfileId, rotation}]'
curl -s $ORCH/api/aruco/markers | jq
```

If any of those returns empty/error: stop, fix the rig state, then
resume.

---

## 3. Test plan — §6.2 of the review doc

Run in this order. Each step has **commands to execute**, **data to
capture**, and **pass/fail threshold**. Write outputs into
`docs/live-test-sessions/2026-04-22/<stepN>-<descr>.json` (or `.md`
for narrative).

### Step 2 — Stage-map both cameras (closes baseline for Q7, Q12)

For each camera fixture ID (call them `CAM_A`, `CAM_B`):

```bash
curl -s -X POST $ORCH/api/cameras/$CAM_A/stage-map | tee \
  docs/live-test-sessions/2026-04-22/step2-stagemap-camA.json
```

Record in `step2-stagemap-summary.md`:
- `markersMatched`, `matchedIds`, `rmsError` per camera.
- Persisted homography matrix (first row) — sanity check that
  `_calibrations[str(fid)].matrix` and `fixture["homography"]`
  agree after the call (hits **Q7 B2**: two stores today, one
  after the P1 fix):
  ```bash
  curl -s $ORCH/api/cameras/$CAM_A/calibration | jq
  curl -s $ORCH/api/fixtures/$CAM_A | jq '.homography // "absent"'
  ```
  If `fixture.homography` is absent, that's already fine — the
  code writes both. Write what you see.
- `cameraPosStage` (the solvePnP-derived sanity check). We **expect**
  this to disagree with `cameraPos` (layout position) noticeably on
  coplanar markers — **Q8 B3**. Note the disagreement magnitude.

Pass/fail: `markersMatched ≥ 2` and `rmsError < 50 px` per camera.

### Step 3 — Tracking placement, current (broken) code

This is the **pre-fix baseline** for Q1/Q2. The current ingest path
at `parent_server.py:7218` is broken (ignores homography). We want to
measure by how much.

For each of 5 stage positions (pick near-back, near-front, left,
right, centre — within the Y ∈ [1400, 2967] visible band):

1. Place a tripod at known stage `(x, y, 0)` mm. Record the ground
   truth in `step3-groundtruth.json`.
2. Capture a snapshot + run detection:
   ```bash
   curl -s -X POST $ORCH/api/cameras/$CAM_A/scan | tee snapshot-CAM_A.json
   ```
3. Take the tripod pixel bbox manually from the snapshot image
   (download via `/api/cameras/<fid>/snapshot`). This is the raw
   YOLO-style input.
4. POST through the **broken ingest path** exactly like `tracker.py`
   would:
   ```bash
   curl -s -X POST $ORCH/api/objects/temporal \
     -H 'Content-Type: application/json' \
     -d '{
       "ttl": 10,
       "cameraId": '$CAM_A',
       "pixelBox": {"x": PX, "y": PY, "w": PW, "h": PH},
       "frameSize": [FW, FH]
     }'
   ```
5. Read back the placed object:
   ```bash
   curl -s $ORCH/api/objects | jq '.[] | select(._temporal==true) | {id, transform}'
   ```
6. Compute error: `‖placed.pos[xy] − truth[xy]‖` in mm.

Capture into `docs/live-test-sessions/2026-04-22/step3-tracking-error.md`
as a table (5 positions × 2 cameras = 10 rows). Expected: errors
on the order of **1000–3000 mm** — this is the number Q1 fix
eliminates. If errors are unexpectedly small (< 300 mm), the
back-wall assumption happens to match the rig geometry; flag it
but note the P1 fix is still correct in principle.

### Step 4+5 — Beam → homography round-trip (closes Q7, Q8)

Per camera, for each of the 3 ArUco marker IDs:

1. Manually aim a mover's beam at the marker centre (operator
   uses the SPA's DMX monitor, or console, to set pan/tilt).
2. Trigger the beam detector on the node:
   ```bash
   curl -s -X POST http://192.168.10.235:5000/beam-detect \
     -H 'Content-Type: application/json' \
     -d '{"cam": 0}' | tee beam-marker$ID-cam$CAM.json
   ```
3. Apply the persisted homography to the detected pixel (you have
   it from step 2). Compute `stage_coord_via_H`:
   ```python
   # quick Python: use the H matrix, H @ [px, py, 1] then /w
   ```
4. Compare against the surveyed marker stage coord from
   `GET /api/aruco/markers`.

Capture as `step4-5-beam-homography-roundtrip.md`. Per-marker
residual in mm. Expected: **≤ 30 mm RMS inside the marker hull**.
If residuals exceed 100 mm, the homography is bad and Q6 (mover-cal)
will fail regardless of mode.

### Step 6 — Mover calibration, all three modes (closes Q6)

For MH1 and the 350 W BeamLight (two fixtures, total four runs —
legacy gets skipped if markers/v2 succeed and we're tight on time).

Start a run:
```bash
curl -s -X POST $ORCH/api/calibration/mover/$MOVER_FID/start \
  -H 'Content-Type: application/json' \
  -d '{"mode": "markers", "cameraId": '$CAM_A'}' \
  | tee mover-cal-start.json

# Poll
while true; do
  STATUS=$(curl -s $ORCH/api/calibration/mover/$MOVER_FID/status)
  echo "$STATUS" | jq -c '{phase, progress, message}'
  DONE=$(echo "$STATUS" | jq -r '.status')
  [[ "$DONE" == "done" || "$DONE" == "error" ]] && break
  sleep 2
done

# Grab the final cal
curl -s $ORCH/api/calibration/mover/$MOVER_FID \
  | tee docs/live-test-sessions/2026-04-22/step6-MH1-markers.json
```

Repeat with `"mode": "v2"` and, if time allows, `"mode": "legacy"`.

**Metrics to record per run** (put in `step6-summary.md`):
- Final `fit.rmsErrorDeg` (angular residual).
- `fit.maxErrorDeg`.
- `model.panSign`, `model.tiltSign`, `model.mountYaw`.
- Whether the sign combo matches operator's intuition — nudge
  pan +0.02 manually, does the beam move the direction the SPA's
  aim-preview shows? This is the **Q10 sign-probe we specced on
  paper**; you'll simulate it by eye. Note which combos were
  wrong.
- Total elapsed time, phase-by-phase.

**Q6 decision criteria:**
- Markers ≤ 2° RMS and completes without operator intervention →
  markers is the default.
- v2 needs a pre-placed target and gives ≤ 1° RMS → v2 is
  "advanced" option.
- Legacy is deprecated regardless of residual (operator-hostile
  per `feedback_cal_algorithm.md`).

### Step 7 — Deferred

Multi-camera fusion (Q3 constants) requires the Q1 + Q3 code fixes
to land first. Skip this step; we'll do it in a follow-up session
after the P1 PR merges.

### Step 8 — Deferred

End-to-end auto-track beam-to-person error requires Q1 fix. Skip.

---

## 4. How to report back

When the test session ends:

1. Commit the `docs/live-test-sessions/2026-04-22/` folder
   contents to `claude/review-camera-implementation-pqyfH`.
2. Append a short "Live-test results" section to §11 (change log)
   of `docs/camera-calibration-review.md` summarising Q6 outcome
   and any numeric baselines:

   ```markdown
   - **2026-04-22 (live test)** — basement rig, steps 2/3/4/5/6
     executed. Q6 closed: default mode = <markers|v2|…>. Q7/Q12
     baselines: stage-map RMS = X px, homography round-trip
     residual = Y mm. Q1 baseline: tracking error = Z mm
     (pre-fix). Full data at `docs/live-test-sessions/2026-04-22/`.
   ```

3. `git push origin claude/review-camera-implementation-pqyfH`.
4. The remote (mobile) session picks it up on next fetch and
   updates §8.1 Q6 + §8.3 priority list accordingly.

---

## 5. What NOT to do in this session

- **Don't implement any P1/P2 fix.** Review rule §2: no code
  changes until §8 is signed off. Measurement only.
- **Don't commit to `main`.** Stay on
  `claude/review-camera-implementation-pqyfH`.
- **Don't post GitHub comments on the cross-referenced issues
  (§10.1 / §10.2)** — that's a separate sign-off step after §8 is
  approved.
- **Don't delete existing calibrations** unless a test needs a
  clean slate — if you do, note it in the summary so we can
  explain any state divergence in the repo.

---

## 6. State at handoff (current branch HEAD)

Branch: `claude/review-camera-implementation-pqyfH`
Latest commit (pick up from here):
```
docs: camera review — close Q3, Q11, Q13, Q14 (no-hardware batch)
```

Section 8 tally: 13/14 closed, only Q6 open.
Section 10 (related issues): 4 will close (#611, #612, #357,
#423), 7 need updates (#610, #600, #597, #484, #474, #427, #510),
4 cross-link Q14 (#533, #409, #277, #280).

---

## 7. Contact back

If a command is ambiguous, annotate with "UNCLEAR — need
remote-session input" and commit that note so the mobile side can
answer without a round trip. Otherwise just push results as you
go; I'll read them on fetch.
