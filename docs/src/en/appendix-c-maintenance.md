## Appendix C — Documentation Maintenance

> This appendix describes the contract between the calibration appendices above and the source code that implements them. It exists for issue [#662](https://github.com/SlyWombat/SlyLED/issues/662) and is kept short — full details are in `docs/DOCS_MAINTENANCE.md`.

### C.1 Source-of-truth files

Any PR that changes calibration behaviour in one of these files is expected to include an Appendix A or B review in the same PR:

**Mover calibration:** `desktop/shared/mover_calibrator.py`, `mover_control.py`, `parametric_mover.py`, `desktop/shared/spa/js/calibration.js`, `desktop/shared/parent_server.py` (routes `/api/calibration/mover/*`).

**Camera calibration:** `firmware/orangepi/camera_server.py`, `beam_detector.py`, `depth_estimator.py`, `desktop/shared/space_mapper.py`, `surface_analyzer.py`, `camera_math.py`, `desktop/shared/parent_server.py` (routes `/api/aruco/markers*`, `/api/cameras/<fid>/stage-map`, `/api/cameras/<fid>/aruco/*`, `/api/cameras/<fid>/intrinsic*`, `/api/cameras/<fid>/beam-detect`, `/api/space/scan`).

### C.2 Reviewer checklist (short form)

On a calibration-touching PR, confirm:

- Phase names in `mover_calibrator.py` match the Appendix B §B.2 table
- Timeout constants in the §B.7 table still match code
- Endpoint paths + request/response shapes in Appendix A match Flask route signatures
- Rotation-schema v2 (§A.9) still matches `camera_math.py::rotation_from_layout`
- Status strings written to the calibration-status dict match the state machine diagram

The full checklist, including render verification for the Mermaid diagrams under `docs/diagrams/` and the DRAFT-banner removal criteria, is in `docs/DOCS_MAINTENANCE.md`.

### C.3 Regenerating the manual

- Canonical source: `docs/USER_MANUAL.md` (this file).
- `docs/SlyLED_User_Manual.docx` + `.pdf` are **built separately** by `tests/build_manual.py`, which constructs the document from scratch rather than parsing this markdown. The docx/PDF path does not yet include these appendices — follow-up work.
- Diagram sources live in `docs/diagrams/*.mmd`. Mermaid blocks are embedded inline in the markdown so GitHub renders them directly; external renderers like Kroki can generate SVG/PNG from the standalone files for PDF inclusion.

### C.4 Enforcement

No automatic drift-check is wired up yet. Proposed options, in order of cost:

1. PR-template checkbox (`.github/pull_request_template.md`)
2. GitHub Actions grep: fail PRs that touch the source-of-truth list without touching `docs/USER_MANUAL.md`, with a skip-override label
3. Scheduled drift agent (weekly)

These require `.github/` changes and are tracked as follow-ups under #662.

### C.5 DRAFT banner removal

The DRAFT banners on Appendix A and B should be removed once the in-flight items listed in `docs/DOCS_MAINTENANCE.md §"When to bump the DRAFT banner"` are all confirmed merged. At the time this appendix was drafted (2026-04-23), the following are known to be partial or not yet in code: #653 time budgets, #654 held-out parametric gate, #655 full median oversample, #658 blink-confirm on non-battleship path, #659 floor-view polygon target filter, #661 adaptive battleship density.
