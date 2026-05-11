# Website screenshots — capture checklist

The HTML pages under `server/slyled/` reference these image filenames.
Until the images exist the browser falls back to a broken-image icon,
which is a more honest signal than the previous "wrong screenshot with
misleading caption".

## Status — 2026-05-11

The marketing landing's SPA screenshot grid has been re-captured against
v1.7.119 and now sources from `tests/capture_manual_screenshots_v1_7_119.py`
output. Previous v1.7.6-era captures archived under
`server/slyled/v1.7.6-archive/`.

`profile-library.png` is now filled from the v1.7.119 Profiles tab capture
(closes the screenshot half of #575).

## Captures filled in v1.7.119

| Filename in `server/slyled/` | Sourced from |
|---|---|
| `spa-dashboard.png` | `docs/screenshots/v1.7.119/dashboard-tab.png` |
| `spa-layout-3d.png` | `docs/screenshots/v1.7.119/layout-tab-3d.png` |
| `spa-layout-2d.png` | `docs/screenshots/v1.7.119/layout-tab.png` |
| `spa-runtime.png` | `docs/screenshots/v1.7.119/timeline-tab.png` (Runtime = Shows-tab timeline view) |
| `spa-actions.png` | `docs/screenshots/v1.7.119/actions-tab.png` |
| `spa-firmware.png` | `docs/screenshots/v1.7.119/firmware-tab.png` |
| `profile-library.png` | `docs/screenshots/v1.7.119/profiles-tab.png` |

## Remaining — need a populated demo project

| Target filename | Issue | What to capture |
|---|---|---|
| `dmx-monitor-screenshot.png` | #573 | Settings → DMX Monitor. 512-channel grid with some channels lit; at least one moving head's pan/tilt values visible; universe selector in frame. |
| `groups-control.png` | #574 | Settings → Group Control. At least two named groups with per-group brightness / action controls visible. |
| `community-browser.png` | #575 | Settings → Community Profiles. Profile list loaded from electricrv.ca, 3–4 profiles visible, Import button in frame. |
| `dmx-artnet-settings.png` | #572 | Settings → DMX Engine: Art-Net enabled/running, at least one universe configured, routing fields visible. |

These four cannot be captured from a freshly-launched orchestrator —
the panels render empty without groups defined, the DMX engine running
with lit channels, or the community feed populated. They need an
operator-side demo-populate step before the capture.

## Capture specifications

- Viewport: 1280×800 or larger, PNG
- Crop top-nav + main content area
- Use the Kinetic Prism dark theme
- Include French screenshots when doc prose is mirrored — drop suffix `_fr.png`

## Workflow

1. Launch the orchestrator and populate the demo state (groups, DMX
   engine running with at least one lit channel, Community Profiles
   loaded with a working electricrv.ca connection).
2. Extend `tests/capture_manual_screenshots_v1_7_119.py` to take the
   four Settings sub-views above (its existing harness handles tab
   navigation + modal open/close + Playwright lifecycle). The script
   is read-only on server state (no Save/POST clicks) so adding more
   captures is safe.
3. Run it, then copy the resulting PNGs from
   `docs/screenshots/v1.7.119/` into `server/slyled/` with the
   filenames above.

## Relationship to issues

Closing #573 / #574 / #575 (DMX Monitor / Groups / Community pages)
requires the images AND the HTML references (already updated to
point at the new filenames as of the commit that added this file).
The HTML change alone is tracked here; the image capture is an
operator-run step that requires populated state.

#572 (DMX Engine settings) is the same shape: HTML already in
place, capture pending populated state.
