---
title: PLASA Innovation Award 2026 submission (draft)
status: draft
submission_deadline: 2026-10-01
award_link: https://www.plasashow.com/awards
---

# SlyLED — PLASA Innovation Award 2026 (draft submission)

> **This is the source of the PLASA submission narrative.** Judging
> criteria come from PLASA's call for entries; this draft maps each
> criterion to specific engineering work already on `main`. Update
> when the official 2026 template is released.

## Summary (250 words)

SlyLED is a source-available, three-tier stage-lighting controller that
does the jobs normally reserved for $60 000 consoles on hardware most
community theatres can already afford. A Windows orchestrator serves
a 3D browser UI; ESP32 and D1 Mini performers run LED effects; an
Arduino Giga R1 WiFi bridges Art-Net to DMX; Orange Pi or Raspberry
Pi camera nodes provide stage vision.

The innovation sits in two layers that nobody else in the consumer
price bracket attempts:

1. **Camera-assisted moving-head calibration** — a battleship-search
   flash-detection pipeline localises each moving head's reachable
   cone with a single USB webcam, rejects reflections with a blink
   confirmation, and solves a parametric pan/tilt model to within
   100 mm of a commanded stage-mm point at 3 m throw. No beacons,
   no IR wands, no pucks — commodity hardware only.

2. **Local-first vision AI** — an integrated Ollama runtime runs
   small vision-language models on the operator's own machine to
   auto-tune camera exposure / gain / white balance per detection
   intent (beam, ArUco, YOLO). The design of the system explicitly
   rules out cloud telemetry; every AI decision runs inside the
   venue's own network.

Together they let a $250 rig (one moving head + one camera + a PC)
deliver the calibrated-tracking experience that used to require a
$30 000 Zactrack install. The full stack is documented in a
bilingual (EN + FR) user manual with calibration-pipeline appendices
that cite the code line-by-line.

## 1000-word expansion

### The problem

Every moving-head rig needs an answer to the question "where is the
beam pointing?" Pro consoles let the operator answer it by eye;
trackers like Follow-Me 3D, BlackTrax, and Zactrack bolt on dedicated
sensing hardware that starts at $15 000 and ends above $60 000. For
community theatres, experimental venues, and education programs, the
price floor has been impenetrable.

Consumer lighting software (QLC+, Freestyler DMX, Lightjams) sidesteps
the problem — they are palette editors that hope the operator will
calibrate by eye. None integrates vision; none solves pose from
camera data.

SlyLED's thesis is that the problem is solvable with a webcam.

### The innovation

The **mover-calibration pipeline** treats the camera as the sensor
network. It sweeps each moving head through a coarse pan/tilt grid,
uses flash detection (beam on vs off) to find the reachable band, runs
a blink-confirm test to reject reflections off polished floors, and
fits a parametric pose model so the runtime can answer "point at (x,
y, z) in stage mm" with a closed-form inverse. The whole loop is
documented against its own source code in the
[Moving-Head Calibration Reliability Review](https://github.com/SlyWombat/SlyLED/blob/main/docs/mover-calibration-reliability-review.md),
an 800-line engineering audit of every phase with timing budgets,
failure modes, and a four-tier fallback ladder so the operator is
never stuck.

The **local-first vision AI** is SlyLED's answer to the "but isn't
modern vision AI cloud-only?" concern. A vision-language model running
in Ollama on the operator's own hardware scores each camera frame
(clipped highlights, dynamic range, colour balance) and proposes
concrete V4L2 control deltas. The auto-tune converges in 1–2
iterations instead of the heuristic's gradient search. No frames
leave the venue.

### Why it matters for the lighting industry

Two structural trends meet at this project:

- Cameras have become ubiquitous and cheap enough that every venue
  already has one pointed at the stage for security or rehearsal
  archiving. Using that same sensor for fixture calibration and
  performer tracking collapses two budget lines into one.
- Small vision-language models have become good enough, and portable
  enough, to run productively on modest GPUs or even CPUs. The
  dependency on cloud AI is no longer mandatory for lighting-grade
  quality.

SlyLED is the first open project to make both these observations the
centre of its architecture rather than optional add-ons.

### Evidence of quality

- **700+ test assertions** across unit, integration, and Playwright
  regression suites — `python tests/regression/run_all.py` exercises
  the full show-design → bake → runtime loop on every push.
- **A living reliability review** — the calibration series (#610,
  #651–#661, #357) was executed as an architectural audit before
  implementation. Every change cites the specific question in the
  review it answers.
- **Transparent code path** — the 300 lines from a SPA click to a DMX
  byte on the wire are readable in a single sitting.
- **Bilingual documentation** — English + French, PDF + DOCX + HTML
  + inline hover-glossary in the app, all rebuilt deterministically
  from markdown sources on every release.

### Why PLASA is the right audience

The PLASA Innovation Award recognises entries that advance the
professional AV industry. SlyLED aligns with the Award's emphasis on:

- **Accessibility** — bringing pro-tool workflows to community-price
  hardware.
- **Openness** — the full stack is source-available for inspection
  and independent auditing.
- **Hardware-software integration** — the project spans firmware
  (three board families), orchestrator (Windows/macOS/Android), and
  vision (four model families) rather than picking one layer.
- **Verification** — the reliability review pattern (the PR series
  audit-before-implement) is itself an artefact that other teams can
  adopt.

### What we need from the award

Recognition from PLASA legitimises SlyLED for the venues, educators,
and integrators who would benefit most but can't take a chance on an
unknown project. The award accelerates conversations with:

- Lighting-design programmes (theatre schools) who want to teach
  calibration theory against a system students can actually run on
  their own laptops.
- Community theatre networks — regional festivals, amateur-theatre
  associations — who have been priced out of the tracked-mover
  category.
- Integrators serving museums, escape rooms, and immersive theatre
  who need a stack that does sensor work (calibration + tracking)
  with commodity components.

A PLASA Innovation nomination is the clearest signal that a
consumer-priced, source-available alternative to the tracker-console
monoculture is credible.

## Criteria mapping (internal notes for the submission writer)

Map each of PLASA's published criteria to specific commits and test
artefacts. The 2026 template is not yet published at the time of
drafting; rework this section when it lands. Known historical criteria
include:

- "Innovative use of technology" → mover calibration pipeline + local-
  first vision AI.
- "Engineering quality" → test coverage, reliability review series,
  code-doc drift CI.
- "Real-world application" → basement-rig + (pending) theatre case
  study.
- "Presentation of submission" → this site, PDF/DOCX manuals,
  Mermaid-rendered architecture diagrams.
