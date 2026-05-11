# Help-panel smoke test (#881)

Operator-facing checklist for the granular help-key wiring. Run after
any change to:

- `desktop/shared/parent_server.py::_resolve_help_fragment` /
  `api_help`
- `desktop/shared/spa/js/app.js::_helpContextKey` /
  `_helpModalSet` / `_pushModal` / `_popModal` / `closeModal`
- Any help-fragment authoring under `docs/src/en/help-fragments/`
- The release build pipeline (the help fragments are bundled into the
  Windows installer via `desktop/windows/build.py`).

The automated coverage (`tests/test_help_resolution.py` and
`tests/test_help_ui_granularity.py`) drives the same surfaces in a
Playwright harness; this checklist is the live-rig confirmation.

## Setup

1. Build a fresh dist (or run a work-tree orchestrator):
   `powershell.exe -File build_release.ps1` (release path) or
   `python desktop/shared/parent_server.py --port 5600` (work-tree).
2. Open `http://127.0.0.1:5600/` in a browser.

## Checklist

- [ ] **Dashboard** — click `?` → help renders **Getting Started**
      chapter.
- [ ] **Dashboard with Auto Brightness card visible** — register a
      WASAPI loopback or phone source, confirm the card appears in
      the Remote Controllers panel, then click `?` → help renders
      the **Auto Brightness** fragment (mentions `cur`, `range`,
      `globalBrightness`).
- [ ] **Setup tab** — click `?` → help renders chapter 4 (Fixture
      Setup).
- [ ] **Setup → + Add DMX Fixture (wizard step 1)** — click `?` →
      help mentions search across **Local / Community / OFL**.
- [ ] **Wizard step 2 (Address)** — click Next or Browse-then-select
      to land on the universe/address screen, then `?` → help mentions
      **live conflict detection** and the 1-based address convention.
- [ ] **Wizard step 3 (Confirm)** — Next to confirm, then `?` → help
      walks through the **Create Fixture** consequences (incl. the
      Set Home prompt for movers).
- [ ] **Edit DMX fixture** — double-click a DMX fixture in the Setup
      list, `?` → resolves `setup.edit-fixture.dmx`. Currently the
      fragment falls back to the Setup chapter (no per-type fragment
      yet); the URL fetched must still be `setup.edit-fixture.dmx`.
- [ ] **Edit Gyro Controller** — open Setup → click Configure on a
      gyro controller row, `?` → resolves
      `setup.edit-fixture.gyro` and the **Gyro Controller fixture**
      help fragment renders (mentions assignedMover, aim-axis
      wizard, the v1.7.122 smoothing removal).
- [ ] **Layout → Rotate mode (2D)** — switch to Front/Top/Side view,
      press **R**, `?` → renders the **Layout — Rotate mode (2D)**
      fragment (mentions compass ring + R/M keybindings).
- [ ] **Layout → Move mode (2D)** — press **M**, `?` → URL is
      `layout.2d.move`, body falls back to the Stage Layout chapter.
- [ ] **Actions → open a Track action → Advanced expander open** —
      open an action with `type=18`; the Advanced details element
      opens automatically when `trackCycleMs` is set, then `?` →
      resolves `actions.track-editor.advanced` and the body
      mentions **Cycle Time** and **Offset X/Y/Z**.
- [ ] **Shows → Bake panel** — open the bake controls, `?` → resolves
      `shows.bake` and mentions the LOAD_STEP/Sync/Start sequence.
      (Currently `shows` is the tab default; a dedicated
      `shows.bake` helpkey would need a hover-pinned data-help-key on
      the Bake button. Until that lands, the chapter-level fallback
      applies — file a follow-up issue if you need finer granularity
      here.)
- [ ] **Settings → Profiles** — `_setSection('profiles')`, `?` →
      resolves `settings.profiles`, body mentions the profiles
      library.
- [ ] **Settings → Profiles → Community sub-panel** — when the
      Community search is open, `?` → resolves
      `settings.profiles.community`, body mentions
      **Sharing** / **Deduplication**.
- [ ] **Settings → DMX Monitor modal** — click the DMX Monitor button
      under Settings → DMX, `?` → resolves `settings.dmx-monitor`,
      body mentions the **512-channel grid** and click-to-set.
- [ ] **Settings → Group Control modal** — click the Group Control
      button, `?` → resolves `settings.group-control`, body mentions
      **Fixture Group Control** and per-group sliders.
- [ ] **Firmware tab** — `?` → renders chapter 15.
- [ ] **Firmware tab → hover Force Update** — `data-help-key` on the
      Force Update button pins `firmware.force-update`. Hover the
      button and click `?` → body mentions **HTTP `/ota`** and the
      version-equality default.
- [ ] **Help close button (✕)** — close via the panel's X →
      `help-open` class removed from `<body>`; opening again resolves
      whatever the current context is, not the previous one.
- [ ] **Glossary hover** — open any fragment containing a `<strong>`
      glossary term (e.g. "Aim Stage"); hover should pop the
      glossary card with EN definition.
- [ ] **Stub fallback** — visit `/api/help/made-up.thing` directly in
      the browser → JSON body has `source: "stub"` and `html`
      contains a `<a href='/help'>` to the full manual. SPA toggle
      flows never request unknown keys, but the stub is the safety
      net for typos.

## Verifying after a release build

The Windows installer bundles `docs/build/en/help/*.html` into the
PyInstaller datafiles (`desktop/windows/build.py:208`). After
`build_release.ps1` runs, check that the installer's runtime
unpacks the new fragments:

```powershell
# Run the installed SlyLED.exe with --port 5601 against the dist build
& 'C:\Program Files\SlyLED\SlyLED.exe' --port 5601
# In another shell:
curl http://127.0.0.1:5601/api/help/setup.edit-fixture.gyro
```

The response should have `source: "fragment"` and `slug:
"setup.edit-fixture.gyro"`. A `source: "legacy-scan"` or
`source: "stub"` here means the docs build didn't run or the
fragments didn't make it into the PyInstaller bundle.
