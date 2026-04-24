# Docs maintenance — calibration appendices + source split

This file describes how every piece of generated documentation is kept in
sync with the source code. Originally authored for issue
[#662](https://github.com/SlyWombat/SlyLED/issues/662); expanded 2026-04-24
to cover the full docs-overhaul series (#665–#676) and the source-file split.

## Source tree (post-#668)

Edit markdown here; everything downstream is derived.

```
docs/src/
  en/                    English canonical — one file per top-level section
  fr/                    French mirror — same filenames (stubs until #670)
  marketing/             Landing hero, feature cards, press kit (#672)
  diagrams/*.mmd         Mermaid sources — rendered by tools/docs/ (#667)
```

The committed `docs/USER_MANUAL.md` and `_fr.md` files are assembled from
the split by `tools/docs/build.py --format assembled-md` and checked in
so GitHub previews and legacy link anchors keep working. Do not hand-edit
them.

## Scope of this file

- **Appendix A** — Camera calibration pipeline (`docs/src/en/appendix-a-camera-calibration.md`)
- **Appendix B** — Moving-head calibration pipeline (`docs/src/en/appendix-b-mover-calibration.md`)
- **Appendix C** — Documentation-maintenance reference (`docs/src/en/appendix-c-maintenance.md`)
- **§20 Glossary** — `docs/src/en/20-glossary.md` (issue [#663](https://github.com/SlyWombat/SlyLED/issues/663))
- Diagram sources — `docs/diagrams/*.mmd`

The appendices were previously marked **DRAFT** because they assumed the
calibration reliability series (#610, #651–#661, #357) had merged. With
all of those now on `main` (2026-04-23), the DRAFT banners are candidates
for removal on the next doc pass — reviewer should run through the "When
to bump the DRAFT banner" list below before removing.

## Source-of-truth file list

Any PR that touches these files should include an Appendix A or B review in the same PR:

### Mover-calibration surface
- `desktop/shared/mover_calibrator.py`
- `desktop/shared/mover_control.py`
- `desktop/shared/parametric_mover.py`
- `desktop/shared/spa/js/calibration.js`
- Orchestrator routes `/api/calibration/mover/*` in `desktop/shared/parent_server.py`
- `CAL_TUNING_SPEC` in `desktop/shared/parent_server.py` (operator-tunable mover-cal knobs #680) — any change to defaults, clamps, keys, or tooltips MUST update Appendix B §B.7 "Tuning-parameter reference" in both EN and FR

### Camera-calibration surface
- `firmware/orangepi/camera_server.py` — endpoints `/snapshot`, `/scan`, `/point-cloud`, `/beam-detect*`, `/dark-reference`, `/calibrate/intrinsic/*`
- `firmware/orangepi/beam_detector.py`
- `firmware/orangepi/depth_estimator.py`
- `desktop/shared/space_mapper.py`
- `desktop/shared/surface_analyzer.py`
- `desktop/shared/camera_math.py` — rotation schema v2 canonical helpers
- Orchestrator routes `/api/aruco/markers*`, `/api/cameras/<fid>/stage-map`, `/api/cameras/<fid>/aruco/*`, `/api/cameras/<fid>/intrinsic`, `/api/cameras/<fid>/beam-detect`, `/api/space/scan` in `desktop/shared/parent_server.py`

## Reviewer checklist

When reviewing a PR that touches any file above, confirm:

- [ ] Phase names in `mover_calibrator.py` still match Appendix B §B.2
- [ ] Timeout constants cited in Appendix B (`SETTLE`, `MAX_SAMPLES`, claim TTL, bracket floor, phase budgets #653) still match code
- [ ] `CAL_TUNING_SPEC` (#680) defaults, clamps, and tooltip wording still match Appendix B §B.7
- [ ] Endpoint paths and request/response shapes in Appendix A match the Flask route signatures
- [ ] Rotation schema v2 (Appendix A §A.9) still matches `camera_math.py::rotation_from_layout` and the SPA mirror helper
- [ ] Status strings written to the calibration-status dict match the state machine in `docs/diagrams/mover-calibration-states.mmd`
- [ ] Mermaid diagrams in `docs/diagrams/*.mmd` render without error (render with `https://kroki.io/mermaid/svg/<base64>` or locally via `mmdc`)
- [ ] `tests/build_manual.py` regenerated the PDF/docx if the appendices changed and are expected to appear in the packaged manual — the docx build is a separate code path that constructs from scratch, it does NOT parse `USER_MANUAL.md`. The canonical markdown stays in sync; PDF/docx parity is a follow-up item tracked in the Appendix A/B "Maintenance" note.

## Suggested automation (not yet implemented)

Two options to enforce sync, in decreasing cost:

1. **PR-template checkbox** — add a "If this PR changes calibration behaviour, I have reviewed Appendix A / Appendix B" checkbox to `.github/pull_request_template.md`. Cheapest, but relies on discipline.
2. **CI grep** — GitHub Actions workflow that fails if the PR diff touches any file on the source-of-truth list *and* does not touch `docs/USER_MANUAL.md`, with a skip-override label (e.g. `docs-not-needed`). More reliable; still cheap to run.
3. **Scheduled drift agent** — routine Claude Code agent that reads the source files and the appendices weekly, opens a "drift detected" PR when signatures / phase names / timing constants diverge. Catches semantic drift that grep misses. Best coverage but highest cost per run.

Neither option 1 nor 2 is wired up yet; they require `.github/` changes, which are out of scope for this draft. Pick one in the follow-up PR.

## When to bump the DRAFT banner

Remove the DRAFT banner on Appendix A once **all** of these items are confirmed:

- Per-camera intrinsic calibration is shipped (currently partially present)
- ArUco stage-map solve is the default extrinsic path and matches §A.4
- Dark-reference capture (#651) is integrated into the mover-calibration pipeline
- Depth-anchor fallback thresholds and cross-cam filter behaviour match §A.6

Remove the DRAFT banner on Appendix B once **all** of these items are confirmed merged:

- Time-budget + blackout-on-timeout per phase (#653)
- Held-out parametric verification gating the `moverCalibrated` flag (#654)
- Oversample + median BFS probes (#655)
- Blink-confirm reflection rejection present in both discovery paths (#658 — currently battleship path only)
- `pick_calibration_targets` camera floor-view polygon filter (#659 — currently uses simple FOV cone)
- Adaptive battleship density scaling with `pan_range` and `beam_width` (#661)
- Coarse-to-fine refine on the battleship path (#660 — currently on BFS convergence path only)

## Glossary (§20) — maintenance contract

Any PR that introduces a new acronym or domain-jargon term into the manual (body, walkthroughs, appendices, examples, troubleshooting tables) must also add an entry to §20 Glossary in the same PR. Entries stay alphabetised on the **Term** column.

**Quick drift check** (run locally before declaring the glossary complete):

```bash
# Every uppercase token used in the manual
grep -oE '\b[A-Z]{2,}[A-Z0-9]*\b' docs/USER_MANUAL.md | sort -u > /tmp/used.txt

# Every Term column entry in the glossary table (rough heuristic)
awk -F'|' '/^\| \*\*/ {gsub(/\*/,"",$2); print $2}' docs/USER_MANUAL.md \
    | tr -d ' ' | tr '/' '\n' | sort -u > /tmp/defined.txt

comm -23 /tmp/used.txt /tmp/defined.txt
```

The `comm -23` output is the set of acronyms used in the manual but missing from the Glossary. Some tokens (status strings like `OFF`, `ON`, `DOWN`; mermaid-diagram direction tokens like `TD`, `LR`; specific identifiers like `MH1`, `FF0000`) are legitimately skipped — don't blindly add every result.

**French mirror:** `docs/USER_MANUAL_fr.md` carries a stub pointing to the English glossary. Full French translation is deferred until the English content stabilises. When the English glossary stops churning, translate the whole table at once rather than piecemeal — mixed-language entries in the same table are a worse operator experience than a stub.

## Related issues

- #662 — calibration appendices (standing issue; not closed after first draft)
- #663 — glossary (sibling to #662; standing issue)
- #637 — `/help` packaging (affects how the SPA surfaces these appendices)
- #644 — `/help` contextual side-panel redesign
