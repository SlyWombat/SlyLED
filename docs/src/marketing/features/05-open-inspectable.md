---
slug: open-inspectable
title: One repo, SPA click to DMX byte
icon: open-book
order: 5
---

# No black boxes between you and the hardware

Every SlyLED feature sits in one GitHub repository. When a show
designer clicks "Strobe" in the SPA, the call graph from that click
to the byte on wire is readable in a single editor session:

1. `spa/js/actions.js` posts to `/api/action`.
2. `parent_server.py` resolves the fixture profile and assembles the
   DMX frame in `bake_engine.py`.
3. `dmx_artnet.py` formats the Art-Net packet and sends it.
4. The bridge firmware (`main/main.ino`, `BOARD_GIGA_DMX`) drives the
   UART at the right slow-break timing.

Every step is source-available in the same tree, under the PolyForm
Noncommercial 1.0.0 licence (free for personal / nonprofit use, paid
commercial licence for business or paid engagements). No cloud service
you depend on, no firmware blob you can't read, no plug-in that only
works when the vendor decides it should.

## What open actually buys you

- **Operators can fix things.** Audit the calibration math, add a
  custom fixture profile, wire a new remote — the diff is small
  because the architecture is flat, not because it's toy-scale.
- **Educators can teach with it.** Students can trace how a lighting
  console actually talks to a stage. Most commercial consoles are
  closed at the protocol layer; SlyLED's UDP binary protocol v4 is
  documented in a 30-line table in `CLAUDE.md`.
- **Venues can audit it.** The camera frames never leave your LAN;
  you can verify that with `netstat`. The rehearsal footage on your
  security feed is yours.

## Test coverage travels with the code

700+ test assertions across 25+ suites land in the same PRs that ship
features. Regression tests cover the full fixture → layout → timeline
→ bake → runtime flow. Visual tests use Playwright to catch UI
regressions. Running `pytest` against a fresh checkout produces the
same green bar SlyLED ships with.
