---
title: SlyLED boilerplate copy
format: press-kit
---

# SlyLED — Short boilerplate (250 words)

SlyLED is an open-source stage-lighting control stack that collapses
the pro-console price floor. The project ships a Windows/macOS
orchestrator, an Android operator app, and open firmware for three
classes of performer hardware, all exchanging a single UDP binary
protocol. One repository holds the entire path from a designer's click
in the browser-based 3D stage editor to the byte on the DMX wire.

Its central technical claim is that a $30 USB webcam can replace the
$15k–$60k beacon, wand, and trackball systems the lighting industry
has treated as the minimum bar for moving-head calibration. SlyLED's
four-tier calibration ladder — coarse discovery, battleship grid,
blink-confirm, ArUco-marker verify — turns camera auto-cal into a
single button the operator presses. The same camera pipeline powers
runtime person tracking via YOLOv8n and 3D scene understanding via
Depth-Anything-V2.

Every AI surface runs on the operator's own machine. Local-first
vision models (Ollama, Moondream, LLaVA) handle camera auto-tune and
scene review; nothing is shipped to a cloud API. Theatres, rehearsal
rooms, and houses of worship keep their footage inside the building.

SlyLED is authored by an independent developer operating out of
Ontario, Canada. It is a PLASA 2026 Innovation Award submission and
ships under the MIT licence. Hardware choices start at consumer
electronics — Arduino Giga R1 WiFi, ESP32-WROOM, Wemos D1 Mini,
Orange Pi — rather than specialty lighting components.

Further information: electricrv.ca/slyled

---

# SlyLED — Long boilerplate (1000 words)

## The price-floor problem

Professional moving-head control has one fundamental feature:
tracking. A talented lighting designer can program a show in advance,
but to have the beams *follow the singer*, the console needs live
positional data. Until SlyLED, the cheapest sensing option for
production-grade tracking was Follow-Me 3D at around US$15,000. The
industry standard Zactrack and BlackTrax installations cross
US$60,000 before anyone buys a moving head.

That economics decision ripples outward. Community theatre companies
and houses of worship run follow-spots by hand. High-school drama
programs simulate movement with timed fades. Small music venues skip
the effect entirely. Every rig that can't justify a tracking-system
purchase compensates with operator hours.

## What SlyLED does differently

SlyLED replaces the dedicated tracking hardware with a US$30 USB
webcam and the operator's existing PC. The calibration flow runs four
tiers in order: a coarse "battleship" grid discovers the beam
location, a blink-confirm pass rejects false positives from reflective
surfaces, a fine grid refines the model, and a held-out verify stage
scores the fit against ArUco markers surveyed in stage coordinates.
When the ladder completes, the orchestrator has a `ParametricFixtureModel`
it can invert for any target position in the stage volume.

The same camera pipeline also does runtime person tracking (YOLOv8n
via ONNX Runtime) and 3D scene understanding (Depth-Anything-V2). The
orchestrator reconciles multiple cameras into one stage coordinate
system via ArUco marker homographies, so a four-camera install yields
one unified world model.

## Local-first AI

Every AI surface in SlyLED runs on the operator's own hardware. The
camera auto-tune feature (v1.6) uses a local vision-language model via
Ollama — Moondream by default, LLaVA and Qwen-VL interchangeable via
a single environment variable. Nothing ships to a cloud API. The
rehearsal footage on a theatre's security camera stays inside the
theatre.

This is a deliberate contrast with the broader AI tooling wave.
Vision AI for stage lighting has to handle protected footage in
regulated spaces — schools, places of worship, private venues. A
cloud-by-default integration would force operators to make privacy
trade-offs the workflow doesn't need to demand. SlyLED's privacy
posture is an architectural decision, not a compliance afterthought.

## Unified remote control

Live operators use one of three input devices: a gyroscope puck, an
Android phone, or an on-screen slider in the SPA. SlyLED routes all
three through one `MoverControlEngine` in `desktop/shared/mover_control.py`.
Claim/release semantics prevent two devices from latching the same
head; a hold-to-calibrate gesture lets the operator align the remote
against the mover's current aim; fixture profiles normalise the
output so the same gesture works on a 16-bit BeamLight and an 8-bit
budget wash.

Adding a new input device — a WebMIDI fader board, a Stream Deck, a
voice-assistant demo — is a matter of calling the six-verb engine API.

## Open from top to bottom

SlyLED is MIT-licensed, every tier. The Windows installer bundles a
Python orchestrator whose source is in the same repository; the
Android app is Kotlin/Compose in `android/`; the firmware is standard
Arduino C++ in `main/`. No cloud dependency gates the product. No
firmware blob resists inspection. The UDP binary protocol v4 is
documented in a 30-line table in `CLAUDE.md`.

This matters most in institutional settings. A venue IT department
can audit exactly what crosses the LAN. A computer-science educator
can teach students how a console actually talks to a stage.
Community-theatre operators can write fixture profiles for the
specific discount fixture their rig actually uses, without waiting
for a vendor fixture library.

## Test coverage and maintenance

Roughly 700 test assertions across 25+ suites land in the same pull
requests that ship features. Regression tests cover the full fixture
→ layout → timeline → bake → runtime flow. A docs-drift CI runs a
weekly scheduled agent that cross-checks the code + the user manual
and opens a drift-report PR when signatures diverge from documented
behaviour.

## Who it's for

The PLASA 2026 Innovation Award submission names three primary
audiences: community theatre and educational venues that cannot
justify a $60k tracking-system purchase; privacy-sensitive venues
that cannot send footage to cloud APIs; and creative-technology
practitioners building one-off installations where consumer hardware
is the right cost floor.

## Contact

Project lead: dave@drscapital.com
Documentation: electricrv.ca/slyled
Source: github.com/SlyWombat/SlyLED
