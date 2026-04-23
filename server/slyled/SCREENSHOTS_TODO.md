# Website screenshots — capture checklist

The HTML pages under `server/slyled/` reference these image filenames.
They need to be captured from a running orchestrator with a populated
demo project and copied into `server/slyled/`. Until the images exist
the browser falls back to a broken-image icon, which is a more honest
signal than the previous "wrong screenshot with misleading caption".

| Target filename | Issue | What to capture |
|---|---|---|
| `dmx-monitor-screenshot.png` | #573 | Settings → DMX Monitor section. Show the 512-channel grid with some channels lit (non-zero values); at least one moving head's pan/tilt values visible; universe selector in frame. |
| `groups-control.png` | #574 | Settings → Group Control. At least two named groups with per-group brightness / action controls visible. |
| `community-browser.png` | #575 | Settings → Community Profiles section, profile list loaded from electricrv.ca, 3–4 profiles visible with manufacturer / name, Import button in frame. |
| `profile-library.png` | #575 | Profile management view (import, export, update) — NOT the Actions tab. |

## Capture specifications

- Viewport: 1280×800 or larger, PNG
- Crop top-nav + main content area
- Use the Kinetic Prism dark theme (`feedback_design_manifesto.md`)
- Include French screenshots too when doc prose is mirrored — drop suffix `_fr.png`

## Workflow

Run `tests/screenshot_capture.py` against a fully-populated demo project
(Fixtures, Groups, DMX engine started with one test cue lit, Community
Profiles loaded with a working electricrv.ca connection). The script
currently captures the 17 SPA tabs; extend it to also capture the four
Settings sub-views above.

## Relationship to issues

Closing #573 / #574 / #575 requires the images AND the HTML references
(already updated to point at the new filenames as of the commit that
added this file). The HTML change alone is tracked here; the image
capture is an operator-run step.
