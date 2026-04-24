## Calibration-pipeline diagrams

Text-source diagrams for the camera and moving-head calibration appendices in `docs/USER_MANUAL.md` (Appendix A, B).

The `.mmd` files are [Mermaid](https://mermaid.js.org/) source. They render inline on GitHub and in any Mermaid-capable viewer. The same blocks are embedded in the user-manual appendices so the manual is self-contained — these standalone files exist so diagrams are independently editable and can be rendered server-side (e.g. via `https://kroki.io/mermaid/svg/...`) when the Word/PDF manual is regenerated.

| File | Used in |
|------|---------|
| `mover-calibration-flow.mmd` | Appendix B — §B.1 pipeline overview |
| `mover-calibration-states.mmd` | Appendix B — §B.2 phase state machine |
| `mover-calibration-sequence.mmd` | Appendix B — §B.3 orchestrator ↔ camera ↔ fixture |
| `camera-calibration-flow.mmd` | Appendix A — §A.1 pipeline overview |
| `camera-calibration-sequence.mmd` | Appendix A — §A.4 stage-map solvePnP sequence |
| `rotation-schema-v2.mmd` | Appendix A — §A.9 axis convention |

### Keeping these in sync with code

These diagrams describe runtime behaviour in `desktop/shared/mover_calibrator.py`, `desktop/shared/parametric_mover.py`, `desktop/shared/mover_control.py`, `firmware/orangepi/camera_server.py`, `firmware/orangepi/beam_detector.py`, `desktop/shared/space_mapper.py`, and `desktop/shared/surface_analyzer.py`. See `docs/DOCS_MAINTENANCE.md` for the drift-check workflow (issue #662).
