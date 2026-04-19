#!/usr/bin/env python3
"""Generate individual feature subpages for the SlyLED website."""
import os

OUTDIR = os.path.join(os.path.dirname(__file__))

# Canvas demo snippets per slug — injected into <script> block
# Each returns a string of JS or empty string
CANVAS_DEMOS = {
    "timeline": """
(function(){
  var c=document.getElementById('demo-canvas');
  if(!c)return;
  var ctx=c.getContext('2d');
  var W=c.width=c.offsetWidth*devicePixelRatio;
  var H=c.height=c.offsetHeight*devicePixelRatio;
  ctx.scale(devicePixelRatio,devicePixelRatio);
  W=c.offsetWidth; H=c.offsetHeight;
  var tracks=[
    {label:'Fixture 1',col:'#22d3ee',clips:[{s:.05,e:.35},{s:.55,e:.9}]},
    {label:'Fixture 2',col:'#ec4899',clips:[{s:.12,e:.55},{s:.7,e:.95}]},
    {label:'Fixture 3',col:'#f59e0b',clips:[{s:.02,e:.22},{s:.4,e:.75}]},
    {label:'Fixture 4',col:'#4ade80',clips:[{s:.3,e:.6},{s:.8,e:.98}]},
  ];
  var TH=28, labelW=72, pad=8, rulerH=20;
  var playT=0,dir=1;
  function frame(){
    requestAnimationFrame(frame);
    playT+=.003*dir;
    if(playT>1){playT=1;dir=-1;}
    if(playT<0){playT=0;dir=1;}
    ctx.clearRect(0,0,W,H);
    ctx.fillStyle='#060a10';ctx.fillRect(0,0,W,H);
    // Ruler
    ctx.fillStyle='#1e293b';ctx.fillRect(labelW,0,W-labelW,rulerH);
    for(var i=0;i<=10;i++){
      var rx=labelW+(W-labelW)*i/10;
      ctx.beginPath();ctx.moveTo(rx,0);ctx.lineTo(rx,rulerH);
      ctx.strokeStyle='#334155';ctx.lineWidth=1;ctx.stroke();
      ctx.font='9px system-ui';ctx.fillStyle='#64748b';ctx.textAlign='center';
      ctx.fillText(Math.round(i*30)+'s',rx,rulerH-4);
    }
    tracks.forEach(function(t,i){
      var ty=rulerH+i*(TH+2)+2;
      // label
      ctx.fillStyle='#1e293b';ctx.fillRect(0,ty,labelW,TH);
      ctx.font='9px system-ui';ctx.fillStyle='#94a3b8';ctx.textAlign='left';
      ctx.fillText(t.label,pad,ty+TH/2+3);
      // track bg
      ctx.fillStyle='#0f172a';ctx.fillRect(labelW,ty,W-labelW,TH);
      // clips
      t.clips.forEach(function(cl){
        var cx=labelW+(W-labelW)*cl.s;
        var cw=(W-labelW)*(cl.e-cl.s);
        var active=playT>=cl.s&&playT<=cl.e;
        var r2=parseInt(t.col.slice(1,3),16),g2=parseInt(t.col.slice(3,5),16),b2=parseInt(t.col.slice(5,7),16);
        ctx.fillStyle=active?'rgba('+r2+','+g2+','+b2+',.35)':'rgba('+r2+','+g2+','+b2+',.1)';
        ctx.fillRect(cx,ty+2,cw,TH-4);
        ctx.strokeStyle=active?t.col:'rgba('+r2+','+g2+','+b2+',.3)';
        ctx.lineWidth=active?1.5:.8;
        ctx.strokeRect(cx,ty+2,cw,TH-4);
      });
      // row divider
      ctx.beginPath();ctx.moveTo(0,ty+TH);ctx.lineTo(W,ty+TH);
      ctx.strokeStyle='#1e293b';ctx.lineWidth=1;ctx.stroke();
    });
    // Playhead
    var px=labelW+(W-labelW)*playT;
    ctx.beginPath();ctx.moveTo(px,0);ctx.lineTo(px,H);
    ctx.strokeStyle='rgba(255,255,255,.7)';ctx.lineWidth=1.5;ctx.stroke();
    ctx.beginPath();ctx.moveTo(px-5,0);ctx.lineTo(px+5,0);ctx.lineTo(px,10);ctx.closePath();
    ctx.fillStyle='#fff';ctx.fill();
  }
  frame();
})();
""",
    "spatial-effects": """
(function(){
  var c=document.getElementById('demo-canvas');
  if(!c)return;
  var ctx=c.getContext('2d');
  function resize(){
    c.width=c.offsetWidth*devicePixelRatio;
    c.height=c.offsetHeight*devicePixelRatio;
    ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  }
  resize();
  window.addEventListener('resize',resize);
  var W=function(){return c.offsetWidth;},H=function(){return c.offsetHeight;};
  // Fixtures on a grid
  var fixtures=[
    {x:.15,y:.15,col:'#22d3ee'},{x:.38,y:.12,col:'#ec4899'},
    {x:.62,y:.13,col:'#f59e0b'},{x:.85,y:.15,col:'#4ade80'},
    {x:.22,y:.45,col:'#818cf8'},{x:.75,y:.43,col:'#22d3ee'},
    {x:.5,y:.25,col:'#ec4899'},
  ];
  var t=0;
  function frame(){
    requestAnimationFrame(frame);
    t+=.018;
    var cx=.5+Math.sin(t)*.32, cy=.55+Math.sin(t*.7+1)*.15;
    var radius=.22+Math.sin(t*.5)*.08;
    ctx.clearRect(0,0,W(),H());
    ctx.fillStyle='#060a10';ctx.fillRect(0,0,W(),H());
    // Grid
    ctx.strokeStyle='rgba(51,65,85,.25)';ctx.lineWidth=.5;
    var gs=Math.min(W(),H())*.09;
    for(var gx=0;gx<W();gx+=gs){ctx.beginPath();ctx.moveTo(gx,0);ctx.lineTo(gx,H());ctx.stroke();}
    for(var gy=0;gy<H();gy+=gs){ctx.beginPath();ctx.moveTo(0,gy);ctx.lineTo(W(),gy);ctx.stroke();}
    // Sphere effect volume
    var sr=radius*Math.min(W(),H());
    var sx=cx*W(),sy=cy*H();
    var g=ctx.createRadialGradient(sx,sy,0,sx,sy,sr);
    g.addColorStop(0,'rgba(34,211,238,.18)');
    g.addColorStop(.7,'rgba(34,211,238,.06)');
    g.addColorStop(1,'rgba(34,211,238,0)');
    ctx.beginPath();ctx.arc(sx,sy,sr,0,Math.PI*2);
    ctx.fillStyle=g;ctx.fill();
    ctx.beginPath();ctx.arc(sx,sy,sr,0,Math.PI*2);
    ctx.strokeStyle='rgba(34,211,238,.3)';ctx.lineWidth=1;ctx.stroke();
    // Fixtures + beams
    fixtures.forEach(function(f){
      var fx=f.x*W(),fy=f.y*H();
      var dist=Math.sqrt((cx-f.x)*(cx-f.x)+(cy-f.y)*(cy-f.y));
      var inside=dist<radius;
      var r2=parseInt(f.col.slice(1,3),16),g2=parseInt(f.col.slice(3,5),16),b2=parseInt(f.col.slice(5,7),16);
      var alpha=inside?.8:.2;
      // beam line to sphere center
      if(inside){
        ctx.beginPath();ctx.moveTo(fx,fy);ctx.lineTo(sx,sy);
        ctx.strokeStyle='rgba('+r2+','+g2+','+b2+',.25)';ctx.lineWidth=4;ctx.stroke();
        ctx.beginPath();ctx.moveTo(fx,fy);ctx.lineTo(sx,sy);
        ctx.strokeStyle='rgba('+r2+','+g2+','+b2+',.55)';ctx.lineWidth=1;ctx.stroke();
      }
      // fixture dot
      ctx.beginPath();ctx.arc(fx,fy,inside?6:4,0,Math.PI*2);
      ctx.fillStyle='rgba('+r2+','+g2+','+b2+','+alpha+')';ctx.fill();
    });
    // Sphere center marker
    ctx.beginPath();ctx.arc(sx,sy,4,0,Math.PI*2);
    ctx.fillStyle='rgba(34,211,238,.9)';ctx.fill();
  }
  frame();
})();
""",
    "dmx-artnet": """
(function(){
  var c=document.getElementById('demo-canvas');
  if(!c)return;
  var ctx=c.getContext('2d');
  function resize(){
    c.width=c.offsetWidth*devicePixelRatio;
    c.height=c.offsetHeight*devicePixelRatio;
    ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  }
  resize();
  var W=function(){return c.offsetWidth;},H=function(){return c.offsetHeight;};
  var vals=new Array(32).fill(0).map(function(_,i){return Math.random()*255|0;});
  var targets=vals.slice();
  var t=0;
  function frame(){
    requestAnimationFrame(frame);
    t++;
    if(t%40===0){
      var ch=Math.random()*32|0;
      targets[ch]=Math.random()*255|0;
    }
    vals=vals.map(function(v,i){return v+(targets[i]-v)*.08|0;});
    ctx.clearRect(0,0,W(),H());
    ctx.fillStyle='#060a10';ctx.fillRect(0,0,W(),H());
    var cols=16, rows=2, cw=W()/cols, ch2=H()/rows, pad=2;
    for(var i=0;i<32;i++){
      var col=i%cols, row=Math.floor(i/cols);
      var x=col*cw+pad, y=row*ch2+pad, w=cw-pad*2, h=ch2-pad*2;
      var v=vals[i]/255;
      ctx.fillStyle='rgba(34,211,238,'+(.05+v*.5)+')';
      ctx.fillRect(x,y,w,h);
      ctx.strokeStyle='rgba(34,211,238,'+(.15+v*.4)+')';
      ctx.lineWidth=.5;ctx.strokeRect(x,y,w,h);
      // channel number
      ctx.font='8px monospace';ctx.fillStyle='rgba(148,163,184,.6)';
      ctx.textAlign='center';ctx.fillText(i+1,x+w/2,y+10);
      // value bar
      ctx.fillStyle='rgba(34,211,238,'+(.4+v*.5)+')';
      ctx.fillRect(x+2,y+h-4,Math.max(2,(w-4)*v),3);
    }
    // Art-Net label
    ctx.font='10px monospace';ctx.fillStyle='rgba(34,211,238,.4)';
    ctx.textAlign='left';ctx.fillText('ART-NET  UNIVERSE 1  512ch  40Hz',8,H()-8);
  }
  frame();
})();
""",
    "led-effects": """
(function(){
  var c=document.getElementById('demo-canvas');
  if(!c)return;
  var ctx=c.getContext('2d');
  function resize(){
    c.width=c.offsetWidth*devicePixelRatio;
    c.height=c.offsetHeight*devicePixelRatio;
    ctx.setTransform(devicePixelRatio,0,0,devicePixelRatio,0,0);
  }
  resize();
  var W=function(){return c.offsetWidth;},H=function(){return c.offsetHeight;};
  var N=80, t=0;
  function hsl(h,s,l){return 'hsl('+h+','+s+'%,'+l+'%)';}
  function frame(){
    requestAnimationFrame(frame);
    t+=.03;
    ctx.clearRect(0,0,W(),H());
    ctx.fillStyle='#060a10';ctx.fillRect(0,0,W(),H());
    var stripH=10, stripY=H()/2-stripH/2;
    var pixW=W()/N;
    for(var i=0;i<N;i++){
      var px=i*pixW;
      // Rainbow effect
      var hue=((i/N)*360+t*60)%360;
      var bright=50+30*Math.sin((i/N)*Math.PI*4+t*2);
      var alpha=.7+.3*Math.sin(i/N*Math.PI);
      ctx.fillStyle='hsla('+hue+',100%,'+bright+'%,'+alpha+')';
      ctx.fillRect(px,stripY-stripH*.5,pixW-1,stripH*2);
      // glow
      var g=ctx.createRadialGradient(px+pixW/2,stripY,0,px+pixW/2,stripY,stripH*3);
      g.addColorStop(0,'hsla('+hue+',100%,'+bright+'%,.3)');
      g.addColorStop(1,'hsla('+hue+',100%,'+bright+'%,0)');
      ctx.fillStyle=g;
      ctx.fillRect(px,stripY-stripH*3,pixW,stripH*6);
    }
    ctx.font='10px system-ui';ctx.fillStyle='rgba(148,163,184,.5)';
    ctx.textAlign='center';ctx.fillText('RAINBOW  •  '+N+' pixels  •  40Hz',W()/2,H()-10);
  }
  frame();
})();
""",
}

FEATURES = [
    {
        "slug": "spatial-effects",
        "title": "3D Spatial Effects",
        "icon": "&#x1F30C;",
        "color": "#22d3ee",
        "hero": "Design lighting in 3D space",
        "headline": "Effects that move through the room.",
        "desc": "Spheres, planes, and boxes sweep across your stage. Fixtures light up or go dark based on whether the effect volume intersects their physical position. Moving heads automatically track the spatial effect center with inverse-kinematics pan/tilt.",
        "bullets": [
            "Three shape types: Sphere, Plane, Box",
            "Motion paths with start/end positions and easing",
            "Blend modes: Replace, Add, Multiply, Screen",
            "Moving heads auto-track the effect center in real time",
            "Bake engine samples beam cone volume for DMX intersection",
            "Real-time visualization in stage preview during playback",
        ],
        "screenshots": [("demo/09-layout-3d.png", "3D viewport with beam cones and spatial effect sphere"), ("demo/11-runtime.png", "Runtime emulator with live spatial field visualization")],
        "has_demo": True,
    },
    {
        "slug": "dmx-artnet",
        "title": "DMX + Art-Net Output",
        "icon": "&#x1F4A1;",
        "color": "#7c3aed",
        "hero": "Professional DMX-512 control via Art-Net",
        "headline": "Every DMX fixture. Any manufacturer. 40 Hz.",
        "desc": "Full Art-Net 4 and sACN support for moving heads, RGB pars, dimmers, fog machines, and any DMX fixture. Import profiles from the Open Fixture Library — 700+ fixtures searchable by manufacturer and model — or download community-shared profiles in one click.",
        "bullets": [
            "Art-Net 4 output at 40 Hz with ArtPoll auto-discovery",
            "Multi-universe routing to specific Art-Net nodes",
            "Live 512-channel DMX monitor with click-to-set",
            "Per-channel testing with quick color buttons",
            "Fixture profiles from OFL (700+ fixtures, search + bulk import)",
            "Community profile sharing at electricrv.ca",
        ],
        "screenshots": [("demo/07-setup-dmx.png", "DMX fixture setup with OFL profiles"), ("demo/12-settings.png", "Settings with DMX routing and Art-Net configuration")],
        "has_demo": True,
    },
    {
        "slug": "led-effects",
        "title": "14 LED Effects",
        "icon": "&#x1F308;",
        "color": "#22c55e",
        "hero": "Built-in effects for addressable LED strips",
        "headline": "Fourteen effects. Zero configuration.",
        "desc": "Rainbow, Chase, Fire, Comet, Twinkle, Strobe, Wipe, Scanner, Sparkle, Gradient — each with customizable speed, color, direction, and per-pixel spatial rendering. Multiple ESP32 nodes each run up to 8 independent WS2812B strips simultaneously.",
        "bullets": [
            "14 effect types with full parameter control",
            "Per-pixel rendering in the emulator preview",
            "Speed normalization by LED density",
            "Bi-directional string support (folded strips)",
            "WS2812B, WS2811, APA102 LED types",
            "Up to 8 strings per ESP32 on independent GPIO pins",
        ],
        "screenshots": [("demo/10-actions.png", "Actions library with effect parameters"), ("demo/05-esp32-strings.png", "ESP32 string configuration with GPIO pins")],
        "has_demo": True,
    },
    {
        "slug": "timeline",
        "title": "Timeline Editor",
        "icon": "&#x1F3AC;",
        "color": "#f59e0b",
        "hero": "Multi-track show design with spatial baking",
        "headline": "Design the show once. Run it everywhere.",
        "desc": "Build complex lighting shows with a multi-track timeline editor. Each track targets a fixture or group. Clips reference spatial effects or classic LED actions. The bake engine compiles everything down to optimized per-fixture instructions, NTP-synced across all performers.",
        "bullets": [
            "Multi-track timeline with draggable, resizable clips",
            "Spatial effect clips with 3D position-based rendering",
            "Smart bake engine: analyzes spatial geometry, emits optimal actions",
            "NTP-synced playback with ±1 ms accuracy across all performers",
            "14 preset shows (Rainbow, Aurora, Disco, etc.) as starting points",
            "Preview emulator with real-time fixture visualization before bake",
        ],
        "screenshots": [("demo/11-runtime.png", "Runtime with timeline and emulator preview"), ("demo/08-layout-2d.png", "2D layout showing fixture positions for bake")],
        "has_demo": True,
    },
    {
        "slug": "dmx-monitor",
        "title": "Live DMX Monitor",
        "icon": "&#x1F50D;",
        "color": "#3b82f6",
        "hero": "Real-time 512-channel universe view",
        "headline": "See exactly what's going to your fixtures.",
        "desc": "Every DMX channel value in real time, across all universes, in a 32×16 grid. Click any cell to override a value. Color-coded by intensity. Fixture profile labels show what each channel does — 'Gobo 3', 'Strobe speed', 'Dimmer' — so you can diagnose patching issues instantly.",
        "bullets": [
            "32×16 grid showing all 512 channels simultaneously",
            "Click any channel to set a value directly",
            "Color-coded cells by intensity (dark → bright)",
            "Universe selector with auto-discovered nodes",
            "Quick color buttons: White, Red, Green, Blue, Blackout",
            "Capability labels from fixture profile (e.g., 'Gobo 3', 'Strobe fast')",
        ],
        "screenshots": [("demo/12-settings.png", "Settings page with DMX monitor"), ("demo/07-setup-dmx.png", "DMX fixture panel with channel sliders")],
        "has_demo": False,
    },
    {
        "slug": "groups",
        "title": "Fixture Groups",
        "icon": "&#x1F91D;",
        "color": "#22d3ee",
        "hero": "Control multiple fixtures as one",
        "headline": "One fader. Every fixture in the zone.",
        "desc": "Group any combination of DMX fixtures — moving heads, pars, strobes — and control them with a master dimmer, RGB color mixer, and quick preset buttons. Groups are first-class targets in the timeline editor, so you can program a zone as a unit without setting each fixture individually.",
        "bullets": [
            "Master dimmer slider per group (0–100%)",
            "R/G/B sliders for live color mixing across the group",
            "Quick presets: Warm, Cool, Red, Off",
            "Groups are targetable in timeline tracks",
            "Profile-aware channel mapping (auto-finds R/G/B/dimmer channels)",
            "Mix fixture types freely within a group",
        ],
        "screenshots": [("demo/06-setup-performer.png", "Setup with fixture groups"), ("demo/12-settings.png", "Group control panel in Settings")],
        "has_demo": False,
    },
    {
        "slug": "android",
        "title": "Android Companion",
        "icon": "&#x1F4F1;",
        "color": "#22c55e",
        "hero": "Your stage in your pocket",
        "headline": "Walk the stage. Run the show.",
        "desc": "A live operator tool — not an editor. The Android app shows your 3D stage in real time, lets you start and stop shows with one tap, toggle camera tracking, and aim moving heads by pointing your phone. Connect via QR code or IP and you're controlling the show from anywhere in the venue.",
        "bullets": [
            "Live 3D stage viewport with fixtures, beam cones, and tracked object markers",
            "Pinch-to-zoom and drag-to-pan across the full stage",
            "One-tap timeline start, playlist control, and show stop",
            "Camera tracking toggle — start/stop AI detection from your phone",
            "Pointer Mode — aim moving heads by pointing your phone (gyroscope control)",
            "Device status monitoring: performers, cameras, DMX engine health",
            "Global brightness slider always reachable on the control tab",
            "QR code scan or manual IP connection",
        ],
        "screenshots": [("android-stage-idle.png", "Live stage view with fixtures and beam positions"), ("android-control.png", "Show controls with playlist and pointer mode"), ("android-status.png", "Device monitoring with camera tracking toggles")],
        "has_demo": False,
    },
    {
        "slug": "community",
        "title": "Community Profiles",
        "icon": "&#x1F310;",
        "color": "#7c3aed",
        "hero": "Share and discover fixture profiles",
        "headline": "If someone lit it before, the profile is already there.",
        "desc": "Upload custom DMX fixture profiles to the community server. Search by name, manufacturer, or category. Automatic duplicate detection using SHA-1 channel fingerprint hashing. Browse 700+ Open Fixture Library profiles. Download and auto-import in one click — the fixture is ready to patch immediately.",
        "bullets": [
            "Community server at electricrv.ca with open REST API",
            "Search, recent, popular, and browse by manufacturer",
            "SHA-1 channel hash for automatic duplicate detection",
            "Upload custom profiles with the 'Share' button",
            "Download and auto-import into local library with one click",
            "OFL integration: search + browse + bulk import",
        ],
        "screenshots": [("demo/12-settings.png", "Community browser in Settings tab"), ("demo/10-actions.png", "Profile management and library")],
        "has_demo": False,
    },
    {
        "slug": "3d-calibration",
        "title": "3D Spatial Calibration",
        "icon": "&#x1F4D0;",
        "color": "#f43f5e",
        "hero": "Camera-based venue mapping and fixture calibration",
        "headline": "Your venue, mapped in millimetres.",
        "desc": "ArUco markers on the stage floor calibrate camera positions in 3D space. Depth-Anything-V2 builds a point cloud of the full environment. RANSAC detects floor, walls, and obstacles. Moving heads auto-calibrate their pan/tilt ranges. All of this runs from the orchestrator with any USB camera.",
        "bullets": [
            "ArUco marker detection runs on the orchestrator — any USB camera works",
            "solvePnP computes camera 3D pose from floor marker positions",
            "Depth-Anything-V2 monocular depth for environment point clouds",
            "RANSAC surface detection: floor, walls, and obstacle clusters",
            "Adaptive settle time: 0.8–2.5s with double-capture beam verification",
            "Boundary-aware BFS: auto-stops when beam leaves camera field of view",
            "Per-fixture light maps: (pan, tilt) → (x, y, z) in stage mm",
            "Stereo triangulation from 2+ cameras for 3D object tracking",
            "Point cloud and calibration data saved in .slyshow project files",
        ],
        "screenshots": [("spa-settings-cameras.png", "Camera calibration status in Settings"), ("spa-layout-3d.png", "3D viewport with scanned point cloud and surfaces")],
        "has_demo": False,
    },
    {
        "slug": "mover-calibration",
        "title": "Mover Calibration",
        "icon": "&#x1F527;",
        "color": "#fbbf24",
        "hero": "Automated pan/tilt calibration with camera-verified light maps",
        "headline": "Point at anything. SlyLED does the math.",
        "desc": "The calibration wizard automatically discovers each moving head's visible range, sweeps a 20×15 grid to map (pan, tilt) coordinates to real stage (x, y, z) positions, and builds per-fixture light maps. Inverse lookup uses IDW interpolation so you can aim any fixture at any stage coordinate instantly.",
        "bullets": [
            "Automatic discovery: coarse 8×5 grid + fine spiral refinement",
            "BFS region mapping: explores visible area with adaptive settle (0.8–2.5s)",
            "Light map: 20×15 systematic sweep maps (pan,tilt) to stage (x,y,z)",
            "Inverse lookup: aim at any stage coordinate using IDW interpolation",
            "Manual mode: jog beam to physical markers, build affine transform",
            "Beam color selection: green, magenta, red, or blue for contrast",
            "Double-capture verification ensures beam is settled before recording",
            "Boundary-aware: stops at camera field-of-view edges automatically",
            "Calibration data persists in project files (.slyshow export/import)",
        ],
        "screenshots": [("example-c-calibrate-panel.png", "Mover calibration panel with fixture settings"), ("example-b-layout-3d.png", "3D layout with calibrated movers on truss")],
        "has_demo": False,
    },
    {
        "slug": "camera-tracking",
        "title": "AI Camera Detection",
        "icon": "&#x1F4F7;",
        "color": "#22c55e",
        "hero": "YOLOv8n object detection on Orange Pi camera nodes",
        "headline": "A $35 board. AI detection. Zero cloud.",
        "desc": "Deploy camera firmware to an Orange Pi or Raspberry Pi via SSH from the Firmware tab. Each node runs YOLOv8n for object detection and Depth-Anything-V2 for monocular depth — entirely on-device. The orchestrator receives bounding boxes and converts them to stage coordinates for beam targeting and tracking.",
        "bullets": [
            "YOLOv8n ONNX model (12 MB) with 16 configurable detection classes",
            "Per-camera config: classes, FPS, confidence threshold, TTL, re-ID distance",
            "Depth-Anything-V2 monocular depth for 3D point clouds",
            "ArUco marker detection for camera intrinsic calibration",
            "Homography-based pixel-to-stage coordinate mapping",
            "Live snapshot preview with bounding box overlay in the SPA",
            "Multi-camera support via V4L2 USB cameras (auto-filters SoC nodes)",
            "Systemd service (slyled-cam) for auto-start on boot",
            "SSH+SCP firmware deployment from the desktop Firmware tab",
            "Per-camera fixtures with independent FOV, calibration, and position",
        ],
        "screenshots": [("spa-setup-edit-camera.png", "Camera fixture edit with tracking configuration"), ("spa-cv-status.png", "CV Engine status showing model loading")],
        "has_demo": False,
    },
]

COMMON_CSS = """
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0a0f13;--surface:#0f172a;--surface2:#1e293b;--border:#334155;
  --text:#e2e8f0;--dim:#94a3b8;--dim2:#64748b;
}
html{scroll-behavior:smooth}
body{font-family:system-ui,-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);line-height:1.65;overflow-x:hidden}
h1,h2,h3{font-weight:700;letter-spacing:-.02em}
a{color:{color};text-decoration:none}
a:hover{opacity:.8}

/* Nav */
.nav{position:fixed;top:0;left:0;right:0;z-index:100;padding:13px 24px;display:flex;align-items:center;gap:14px;background:rgba(10,15,19,.85);backdrop-filter:blur(12px);border-bottom:1px solid rgba(51,65,85,.45);transition:transform .3s}
.nav.hide{transform:translateY(-100%)}
.nav-back{display:flex;align-items:center;gap:7px;color:var(--dim);font-size:.83em;text-decoration:none;padding:5px 13px;border:1px solid var(--border);border-radius:6px;transition:all .2s}
.nav-back:hover{border-color:{color};color:{color};opacity:1}
.nav-title{font-size:.88em;color:var(--dim);flex:1}
.nav-cta{padding:7px 17px;background:{color};color:#fff;border-radius:7px;font-weight:700;font-size:.83em;text-decoration:none;transition:opacity .2s;color:#fff!important}
.nav-cta:hover{opacity:.85}

/* Hero */
.hero{padding:120px 24px 80px;text-align:center;background:radial-gradient(ellipse 70% 50% at 50% 0%, rgba({rgb},.12), transparent)}
.hero-badge{display:inline-flex;align-items:center;gap:8px;padding:5px 15px;border:1px solid rgba({rgb},.35);border-radius:100px;color:{color};font-size:.75em;font-weight:600;letter-spacing:.07em;text-transform:uppercase;margin-bottom:18px;background:rgba({rgb},.07)}
.hero h1{font-size:clamp(2rem,5.5vw,4rem);line-height:1.07;color:#fff;max-width:760px;margin:0 auto 16px}
.hero h1 em{font-style:normal;color:{color}}
.hero-sub{font-size:clamp(.9rem,1.8vw,1.15rem);color:var(--dim);max-width:560px;margin:0 auto 32px}
.hero-btns{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}
.btn-primary{padding:12px 26px;background:{color};color:#fff;border-radius:9px;font-weight:700;font-size:.92em;text-decoration:none!important;transition:transform .2s,opacity .2s;display:inline-block}
.btn-primary:hover{opacity:.88;transform:translateY(-2px)}
.btn-ghost{padding:12px 26px;background:var(--surface);border:1px solid var(--border);color:var(--text);border-radius:9px;font-weight:600;font-size:.92em;text-decoration:none!important;transition:border-color .2s,color .2s;display:inline-block}
.btn-ghost:hover{border-color:{color};color:{color};opacity:1}

/* Demo canvas */
.demo-wrap{max-width:820px;margin:0 auto 64px;border-radius:12px;overflow:hidden;border:1px solid var(--border);background:#000}
#demo-canvas{width:100%;height:180px;display:block}

/* Bullets section */
.bullets-section{max-width:820px;margin:0 auto;padding:0 24px 64px}
.bullets-section h2{font-size:clamp(1.3rem,3vw,2rem);margin-bottom:24px}
.bullets{list-style:none}
.bullets li{display:flex;gap:12px;padding:11px 0;border-bottom:1px solid var(--surface2);color:var(--dim);font-size:.92em;align-items:flex-start}
.bullets li::before{content:'\\2713';color:{color};font-weight:700;flex-shrink:0;margin-top:1px}
.bullets li span{color:var(--text)}

/* Screenshots */
.screens{max-width:900px;margin:0 auto;padding:0 24px 64px}
.screens h2{font-size:clamp(1.2rem,2.5vw,1.7rem);margin-bottom:24px;text-align:center;color:var(--dim)}
.screens-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px}
.screens-grid figure{margin:0;border-radius:10px;overflow:hidden;border:1px solid var(--border);cursor:zoom-in}
.screens-grid img{width:100%;display:block;transition:transform .4s}
.screens-grid figure:hover img{transform:scale(1.04)}
.screens-grid figcaption{padding:8px 12px;font-size:.77em;color:var(--dim2);background:var(--surface)}

/* CTA */
.cta-section{background:var(--surface);border-top:1px solid var(--border);border-bottom:1px solid var(--border);padding:64px 24px;text-align:center;margin-bottom:0}
.cta-section h2{font-size:clamp(1.4rem,3vw,2.2rem);margin-bottom:12px}
.cta-section p{color:var(--dim);max-width:500px;margin:0 auto 28px;font-size:.95em}

/* Footer */
footer{border-top:1px solid var(--border);padding:24px;text-align:center;color:var(--dim2);font-size:.8em}
footer a{color:var(--dim);text-decoration:none}footer a:hover{color:{color}}

/* Lightbox */
#lb{display:none;position:fixed;inset:0;background:rgba(0,0,0,.9);z-index:999;align-items:center;justify-content:center;padding:20px}
#lb.open{display:flex}
#lb img{max-width:92vw;max-height:92vh;border-radius:8px;border:1px solid var(--border)}
#lb::after{content:'✕';position:absolute;top:16px;right:20px;color:#fff;font-size:1.3rem;cursor:pointer}

/* Fade-in */
.fi{opacity:0;transform:translateY(20px);transition:opacity .6s,transform .6s}
.fi.visible{opacity:1;transform:none}

@media(prefers-reduced-motion:reduce){.fi{opacity:1;transform:none;transition:none}#demo-canvas{display:none}}
"""

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SlyLED — {title}</title>
<meta name="description" content="{desc_meta}">
<link rel="icon" type="image/x-icon" href="../slyled.ico">
<style>
{css}
</style>
</head>
<body>

<nav class="nav" id="nav">
  <a href="../" class="nav-back">
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><polyline points="15 18 9 12 15 6"/></svg>
    SlyLED
  </a>
  <span class="nav-title">{title}</span>
  <a href="https://github.com/SlyWombat/SlyLED/releases/latest" class="nav-cta">Download Free</a>
</nav>

<section class="hero">
  <div class="hero-badge">{icon_text}</div>
  <h1>{headline_html}</h1>
  <p class="hero-sub">{desc}</p>
  <div class="hero-btns">
    <a href="https://github.com/SlyWombat/SlyLED/releases/latest" class="btn-primary">Download SlyLED Free</a>
    <a href="../" class="btn-ghost">All Features</a>
  </div>
</section>

{demo_block}

<div class="bullets-section fi">
  <h2>What you get</h2>
  <ul class="bullets">
{bullets_html}
  </ul>
</div>

<div class="screens fi">
  <h2>Screenshots</h2>
  <div class="screens-grid">
{screenshots_html}
  </div>
</div>

<section class="cta-section fi">
  <h2>Ready to try it?</h2>
  <p>Download SlyLED free. MIT licence. No subscription, no account, no cloud.</p>
  <div class="hero-btns">
    <a href="https://github.com/SlyWombat/SlyLED/releases/latest" class="btn-primary">Download SlyLED Free</a>
    <a href="../" class="btn-ghost">Explore All Features</a>
  </div>
</section>

<footer>
  <p><a href="../">SlyLED Home</a> &bull; <a href="https://github.com/SlyWombat/SlyLED">GitHub</a> &bull; <a href="../community/">Community Profiles</a></p>
</footer>

<div id="lb" onclick="this.classList.remove('open')"><img id="lb-img" src="" alt=""></div>

<script>
// Nav hide/show
(function(){{
  var nav=document.getElementById('nav'),last=0;
  window.addEventListener('scroll',function(){{
    var s=window.scrollY;
    if(s>last+40&&s>160)nav.classList.add('hide');
    else if(s<last-10)nav.classList.remove('hide');
    last=s;
  }});
}})();

// Fade-in observer
(function(){{
  if(!window.IntersectionObserver)return;
  var obs=new IntersectionObserver(function(entries){{
    entries.forEach(function(e){{if(e.isIntersecting){{e.target.classList.add('visible');obs.unobserve(e.target);}}}});
  }},{{threshold:.1}});
  document.querySelectorAll('.fi').forEach(function(el){{obs.observe(el);}});
}})();

// Lightbox
(function(){{
  var lb=document.getElementById('lb'),img=document.getElementById('lb-img');
  document.querySelectorAll('.screens-grid figure').forEach(function(f){{
    f.addEventListener('click',function(){{img.src=f.dataset.src||f.querySelector('img').src;lb.classList.add('open');}});
  }});
  document.addEventListener('keydown',function(e){{if(e.key==='Escape')lb.classList.remove('open');}});
}})();

{canvas_js}

fetch('/api/analytics/index.php?action=hit&page='+encodeURIComponent(location.pathname),{{method:'POST',body:JSON.stringify({{page:location.pathname,referrer:document.referrer,sw:screen.width}}),headers:{{'Content-Type':'application/json'}}}}).catch(function(){{}});
</script>
</body>
</html>"""


def hex_to_rgb(h):
    h = h.lstrip('#')
    return ','.join(str(int(h[i:i+2], 16)) for i in (0, 2, 4))


for f in FEATURES:
    # Skip person-tracking — it has its own hand-crafted page
    if f["slug"] == "person-tracking":
        continue

    color = f["color"]
    rgb = hex_to_rgb(color)
    css = COMMON_CSS.replace('{color}', color).replace('{rgb}', rgb)

    # headline — wrap last word in <em> for color accent
    headline = f["headline"]
    words = headline.rsplit(' ', 1)
    if len(words) == 2:
        headline_html = words[0] + ' <em>' + words[1] + '</em>'
    else:
        headline_html = '<em>' + headline + '</em>'

    desc_meta = f["desc"][:155].rstrip()

    bullets_html = '\n'.join(
        f'    <li><span>{b}</span></li>' for b in f["bullets"]
    )

    screens = []
    for img_path, cap in f["screenshots"]:
        # Determine relative path
        if '/' in img_path:
            src = '../' + img_path
        else:
            src = '../' + img_path
        screens.append(
            f'    <figure data-src="{src}"><img src="{src}" alt="{cap}" loading="lazy"><figcaption>{cap}</figcaption></figure>'
        )
    screenshots_html = '\n'.join(screens)

    demo_block = ''
    canvas_js = CANVAS_DEMOS.get(f["slug"], '')
    if canvas_js.strip():
        demo_block = '<div class="demo-wrap fi"><canvas id="demo-canvas"></canvas></div>'

    html = TEMPLATE.format(
        title=f["title"],
        desc_meta=desc_meta,
        css=css,
        icon_text=f["icon"] + ' ' + f["title"],
        headline_html=headline_html,
        desc=f["desc"],
        demo_block=demo_block,
        bullets_html=bullets_html,
        screenshots_html=screenshots_html,
        canvas_js=canvas_js,
    )

    outpath = os.path.join(OUTDIR, f["slug"], "index.html")
    os.makedirs(os.path.dirname(outpath), exist_ok=True)
    with open(outpath, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"  wrote {f['slug']}/index.html")

print(f"\nDone — {len(FEATURES)-1} feature pages generated (person-tracking skipped)")
