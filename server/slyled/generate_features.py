#!/usr/bin/env python3
"""Generate individual feature subpages for the SlyLED website."""
import os

OUTDIR = os.path.join(os.path.dirname(__file__))

FEATURES = [
    {
        "slug": "spatial-effects",
        "title": "3D Spatial Effects",
        "icon": "&#x1F30C;",
        "color": "#22d3ee",
        "hero": "Design lighting in 3D space",
        "desc": "Spheres, planes, and boxes sweep across your stage, illuminating fixtures based on their physical position. Moving heads automatically track the spatial effect center with pan/tilt.",
        "bullets": [
            "Three shape types: Sphere, Plane, Box",
            "Motion paths with start/end positions and easing",
            "Blend modes: Replace, Add, Multiply, Screen",
            "Moving heads auto-track the effect center",
            "Bake engine samples beam cone volume for DMX intersection",
            "Real-time visualization in stage preview during playback",
        ],
        "screenshots": ["09-layout-3d.png", "11-runtime.png"],
        "captions": ["3D viewport with beam cones and spatial effects", "Runtime emulator with live spatial field visualization"],
    },
    {
        "slug": "dmx-artnet",
        "title": "DMX + Art-Net Output",
        "icon": "&#x1F4A1;",
        "color": "#7c3aed",
        "hero": "Professional DMX-512 control via Art-Net",
        "desc": "Full Art-Net and sACN support for moving heads, RGB pars, dimmers, fog machines, and any DMX fixture. Import profiles from the Open Fixture Library with 700+ fixtures.",
        "bullets": [
            "Art-Net 4 output at 40Hz with ArtPoll discovery",
            "Multi-universe routing to specific bridges",
            "Live 512-channel DMX monitor with click-to-set",
            "Per-channel testing with quick color buttons",
            "Fixture profiles from OFL (search + bulk import)",
            "Community profile sharing at electricrv.ca",
        ],
        "screenshots": ["07-setup-dmx.png", "12-settings.png"],
        "captions": ["DMX fixture setup with OFL profiles", "Settings with DMX routing and community profiles"],
    },
    {
        "slug": "led-effects",
        "title": "14 LED Effects",
        "icon": "&#x1F308;",
        "color": "#22c55e",
        "hero": "Built-in effects for addressable LED strips",
        "desc": "Rainbow, Chase, Fire, Comet, Twinkle, Strobe, Wipe, Scanner, Sparkle, Gradient — each with customizable speed, color, direction, and per-pixel spatial rendering.",
        "bullets": [
            "14 effect types with full parameter control",
            "Per-pixel rendering in the emulator preview",
            "Speed normalization by LED density",
            "Bi-directional string support (folded strips)",
            "WS2812B, WS2811, APA102 LED types",
            "Up to 8 strings per ESP32 on independent GPIO pins",
        ],
        "screenshots": ["10-actions.png", "05-esp32-strings.png"],
        "captions": ["Actions library with effect parameters", "ESP32 string configuration with GPIO pins"],
    },
    {
        "slug": "timeline",
        "title": "Timeline Editor",
        "icon": "&#x1F3AC;",
        "color": "#f59e0b",
        "hero": "Multi-track show design with spatial baking",
        "desc": "Build complex lighting shows with multi-track timelines. Each track targets a fixture or group. Clips reference spatial effects or classic actions. The bake engine compiles everything into optimized per-fixture instructions.",
        "bullets": [
            "Multi-track timeline with parallel clips",
            "Spatial effect clips with 3D position-based rendering",
            "Smart bake: analyzes spatial geometry, emits optimal actions",
            "NTP-synced playback across all performers",
            "14 preset shows (Rainbow, Aurora, Disco, etc.)",
            "Preview emulator with real-time fixture visualization",
        ],
        "screenshots": ["11-runtime.png", "08-layout-2d.png"],
        "captions": ["Runtime with timeline and emulator preview", "2D layout showing fixture positions for bake"],
    },
    {
        "slug": "dmx-monitor",
        "title": "Live DMX Monitor",
        "icon": "&#x1F50D;",
        "color": "#3b82f6",
        "hero": "Real-time 512-channel universe view",
        "desc": "See every DMX channel value in real-time across all universes. Click any cell to set a value. Color-coded by intensity. Auto-refreshes at 4Hz. Per-channel capability labels from fixture profiles.",
        "bullets": [
            "32×16 grid showing all 512 channels",
            "Click any channel to set value via prompt",
            "Color-coded cells by intensity (dark to bright)",
            "Universe selector dropdown",
            "Quick color buttons: White, Red, Green, Blue, Blackout",
            "Capability labels from profile (e.g., 'Gobo 3', 'Strobe fast')",
        ],
        "screenshots": ["12-settings.png", "07-setup-dmx.png"],
        "captions": ["Settings page with DMX monitor access", "DMX fixture Details panel with channel sliders"],
    },
    {
        "slug": "groups",
        "title": "Fixture Groups",
        "icon": "&#x1F91D;",
        "color": "#22d3ee",
        "hero": "Control multiple fixtures as one",
        "desc": "Group DMX fixtures and control them with a master dimmer, RGB color sliders, and quick preset buttons. Groups are targetable in timelines for coordinated shows.",
        "bullets": [
            "Master dimmer slider per group",
            "R/G/B sliders for live color mixing",
            "Quick presets: Warm, Cool, Red, Off",
            "Groups targetable in timeline tracks",
            "Profile-aware channel mapping (finds R/G/B/dimmer channels)",
            "Works with any DMX fixture type",
        ],
        "screenshots": ["06-setup-performer.png", "12-settings.png"],
        "captions": ["Setup with fixture groups", "Group control panel in Settings"],
    },
    {
        "slug": "android",
        "title": "Android Companion",
        "icon": "&#x1F4F1;",
        "color": "#22c55e",
        "hero": "Mobile control and monitoring",
        "desc": "Native Kotlin/Compose app that connects to the desktop server over WiFi. View the stage layout with pinch-to-zoom, monitor show playback, test DMX channels, and load preset shows — all from your phone.",
        "bullets": [
            "6-tab interface: Dashboard, Setup, Layout, Actions, Runtime, Settings",
            "2D canvas with pinch-to-zoom and drag-to-reposition",
            "DMX beam cone visualization on layout",
            "Show emulator with LED dots and beam cones",
            "DMX channel testing with per-channel sliders",
            "Hardware/Performers separation (DMX bridge badge)",
        ],
        "screenshots": ["06-setup-performer.png", "08-layout-2d.png"],
        "captions": ["Setup with performers and fixtures", "2D layout with fixtures and surfaces"],
    },
    {
        "slug": "community",
        "title": "Community Profiles",
        "icon": "&#x1F310;",
        "color": "#7c3aed",
        "hero": "Share and discover fixture profiles",
        "desc": "Upload your custom DMX fixture profiles to the community server. Search by name, manufacturer, or category. Automatic duplicate detection using channel fingerprint hashing. Browse 700+ OFL fixtures.",
        "bullets": [
            "Community server at electricrv.ca with REST API",
            "Search, recent, popular, and browse endpoints",
            "SHA-1 channel hash for duplicate detection",
            "Upload custom profiles with 'Share' button",
            "Download and auto-import into local library",
            "OFL integration: search + browse by manufacturer + bulk import",
        ],
        "screenshots": ["12-settings.png", "10-actions.png"],
        "captions": ["Community browser in Settings", "Profile management"],
    },
]

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SlyLED — {title}</title>
<link rel="icon" type="image/x-icon" href="../slyled.ico">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;600;700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',sans-serif;background:#0A0F13;color:#e2e8f0;line-height:1.6}}
h1,h2,h3{{font-family:'Space Grotesk',sans-serif}}
a{{color:#22d3ee;text-decoration:none}}a:hover{{text-decoration:underline}}
.container{{max-width:800px;margin:0 auto;padding:0 20px}}
header{{padding:40px 0 30px;text-align:center}}
header .icon{{font-size:3em;margin-bottom:12px}}
header h1{{font-size:2.2em;color:{color}}}
header p{{color:#94a3b8;font-size:1.1em;margin-top:8px;max-width:600px;margin-left:auto;margin-right:auto}}
.back{{display:inline-block;margin-top:16px;padding:8px 20px;background:#0f172a;border:1px solid #334155;border-radius:6px;color:#94a3b8;font-size:.85em;transition:all .2s}}
.back:hover{{border-color:#22d3ee;color:#22d3ee;text-decoration:none}}
.bullets{{list-style:none;margin:30px 0;padding:0}}
.bullets li{{padding:10px 0 10px 28px;position:relative;color:#94a3b8;font-size:.95em;border-bottom:1px solid #1e293b}}
.bullets li::before{{content:'\\2713';position:absolute;left:0;color:{color};font-weight:bold}}
.screenshots{{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px;margin:30px 0}}
.screenshots figure{{margin:0;border-radius:10px;overflow:hidden;border:1px solid #334155}}
.screenshots img{{width:100%;display:block}}
.screenshots figcaption{{padding:8px 12px;font-size:.8em;color:#64748b;background:#0f172a}}
.cta{{text-align:center;margin:40px 0}}
.cta a{{display:inline-block;padding:12px 28px;border-radius:8px;font-weight:700;margin:0 6px}}
.cta-dl{{background:linear-gradient(135deg,#0969DA,#22d3ee);color:#fff}}
.cta-demo{{background:#0f172a;border:1px solid #334155;color:#e2e8f0}}
footer{{text-align:center;padding:30px 0;color:#64748b;font-size:.85em;border-top:1px solid #1e293b}}
</style>
</head>
<body>
<header>
  <div class="container">
    <div class="icon">{icon}</div>
    <h1>{title}</h1>
    <p>{hero}</p>
    <a href="../" class="back">&larr; Back to SlyLED</a>
  </div>
</header>
<div class="container">
  <p style="font-size:1.05em;margin-bottom:20px">{desc}</p>
  <ul class="bullets">
{bullets_html}
  </ul>
  <div class="screenshots">
{screenshots_html}
  </div>
  <div class="cta">
    <a href="https://github.com/SlyWombat/SlyLED/releases/latest" class="cta-dl">Download SlyLED</a>
    <a href="../demo/" class="cta-demo">See Full Demo</a>
  </div>
</div>
<footer><p><a href="../">SlyLED Home</a> &bull; <a href="https://github.com/SlyWombat/SlyLED">GitHub</a></p></footer>
</body>
</html>"""

for f in FEATURES:
    bullets_html = "\n".join(f"    <li>{b}</li>" for b in f["bullets"])
    screenshots_html = "\n".join(
        f'    <figure><img src="../demo/{img}" alt="{cap}" loading="lazy"><figcaption>{cap}</figcaption></figure>'
        for img, cap in zip(f["screenshots"], f["captions"])
    )
    html = TEMPLATE.format(
        title=f["title"], icon=f["icon"], color=f["color"],
        hero=f["hero"], desc=f["desc"],
        bullets_html=bullets_html, screenshots_html=screenshots_html,
    )
    outpath = os.path.join(OUTDIR, f["slug"], "index.html")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "w") as fh:
        fh.write(html)
    print(f"  {f['slug']}/index.html")

print(f"\nGenerated {len(FEATURES)} feature pages")
