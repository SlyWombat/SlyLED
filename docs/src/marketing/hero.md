---
title: SlyLED
tagline: Open, three-tier stage-lighting control with local-first AI calibration.
layout: hero
cta_primary:
  label: Download for Windows
  href: /slyled/downloads/SlyLED-Setup.exe
cta_secondary:
  label: Watch the 90-second demo
  href: /slyled/demo
hero_image: /slyled/screenshots/spa-dashboard.png
---

# Stage lighting that belongs to you

Design shows in a true 3D stage model. Calibrate every moving head from
a $30 USB webcam. Run the rig from your browser, a phone in your
pocket, or an ESP32 puck on the wall — all of it over your own
network, with none of your data leaving the building.

SlyLED is a complete orchestrator + performer + bridge stack for
theatres, event productions, and creative-tech spaces that want the
capabilities of a grandMA3 console without the $60 000 entry ticket.
It ships as a single Windows installer (or macOS bundle, or Android
APK) and open-source firmware that runs on hardware you can buy at
your neighbourhood electronics shop.

## What makes it different

- **Camera-assisted mover calibration** — no beacons, no IR pucks, no
  wands. A webcam watching the stage is enough to solve every moving
  head's pan/tilt within 100 mm at 3 m throw, fully automatic.
- **Local-first vision AI** — camera exposure, gain, and white
  balance tuned by a vision model running on the operator's own
  machine via Ollama. Cloud is optional, never required.
- **Three control surfaces, one engine** — DMX faders in the browser,
  gyroscope aim from a phone, wall-mounted gyro pucks. Claim / release
  semantics keep two operators from fighting over the same beam.
- **Bilingual documentation, code-synchronised** — the user manual is
  a build artefact. Edit the markdown sources in English or French,
  rerun the build, and every PDF / DOCX / in-app help fragment updates
  together. The glossary hovers in the app; the diagrams render from
  the same Mermaid source that ships with the code.
- **Three-tier, open, inspectable** — read the code path from the SPA
  click all the way to the DMX byte on the wire. One repository.

## Who it's for

- Community theatres priced out of enterprise consoles.
- Experimental / devising companies who want the lighting logic under
  version control with the rest of the production.
- Maker spaces, escape rooms, immersive installations where the stack
  needs to do double duty as a sensor platform.
- Integrators who want a consumer-price demo rig that reads the same
  MVR / GDTF profiles the pro world exchanges.
