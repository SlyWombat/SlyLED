## Layout — Rotate mode (2D)

You're in **Rotate** mode on a 2D layout view (Front / Top / Side).
Pressing the rotation gizmo's coloured ring spins the selected fixture
about that axis.

### Reading the gizmo

- The selected fixture's bounding sphere shows three coloured rings:
  - **Red** = pitch (about world X).
  - **Green** = roll (about world Y, stage-forward).
  - **Blue** = yaw / pan (about world Z, stage-up).
- The current view (Front / Top / Side) picks which ring is most
  ergonomic — the others are foreshortened. Switch view to grab a
  different axis.
- A small compass ring under the gizmo shows the current absolute
  angle for the active ring. Drag past it to read off the new angle
  numerically.

### Switching tools

- Press **R** at any time to enter Rotate.
- Press **M** or **G** to return to Move.
- Or click the **Move** / **Rotate** buttons at the top of the layout
  toolbar.

### Sign conventions

Stored as `rotation = [rx, ry, rz]` in degrees, stage frame, Z-up
(per CLAUDE.md and #586/#600):

- `rx > 0` aims **down** (the fixture's forward axis tips toward
  stage `-Z`).
- `ry > 0` rolls the image clockwise as seen from behind.
- `rz > 0` aims toward `+X` (stage-left).

The Setup tab's edit-fixture dialog uses operator-facing labels
("Tilt, Roll, Pan") with **Tilt = -rx** so positive tilt = above
horizon (#783, #788).

### Pitfalls

- The 2D views don't let you orbit; if the gizmo ring is edge-on,
  switch to a different 2D view or to the 3D view to grab it.
- For very small angle adjustments, hold **Shift** while dragging —
  the gizmo step size shrinks 10× for sub-degree authoring.

**More info →** chapter 5, *Stage Layout*.
