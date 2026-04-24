## 20. Glossary

SlyLED touches lighting, networking, computer vision, and embedded firmware — which means a lot of acronyms and jargon. This section expands every acronym used elsewhere in the manual and defines the domain terms that don't have a literal expansion ("baking", "universe", "blink-confirm", etc.).

Entries are alphabetised on the **Term** column. For acronyms that cluster around a common concept (e.g. `RX` / `RY` / `RZ`) the cluster appears under the first member.

| Term | Expansion | Plain-language definition | Where it shows up |
|------|-----------|---------------------------|-------------------|
| **API** | Application Programming Interface | The set of HTTP endpoints a program exposes for other programs to call. | §19 API Quick Reference; `/api/*` routes throughout. |
| **ARM** | Advanced RISC Machine | CPU architecture used by the Giga R1, Raspberry Pi, and Orange Pi. "Slow on ARM" in the manual means these boards. | §14 Camera Nodes (depth-estimation runtimes). |
| **Art-Net** | — | DMX-over-Ethernet protocol by Artistic Licence. The orchestrator sends `ArtDMX` packets to an Art-Net bridge, which relays them to DMX fixtures. | §2 Walkthrough Step 3b; §12 DMX Profiles. |
| **ArtDMX** | Art-Net DMX packet | One 512-channel Art-Net data packet. | §2 Walkthrough Step 3b. |
| **ArtPoll** | Art-Net discovery packet | The Art-Net discovery broadcast used to find bridges. | §2 Walkthrough Step 3a. |
| **baking** | — | Compiling a timeline into a pre-computed DMX scene stream so playback doesn't need to recompute effects on every frame. | §10 Baking & Playback. |
| **battleship search** | — | Calibration-discovery strategy that probes a coarse grid across the full pan/tilt range before refining — faster than a dense scan when the beam's reachable region is small. | Appendix B §B.3 Discovery. |
| **BFS** | Breadth-First Search | Graph-traversal algorithm that explores outward from a seed point one ring at a time. Used in mover calibration to map the visible-region boundary from a first detected beam position. | Appendix B §B.3 Mapping. |
| **blink-confirm** | — | Reflection-rejection check: after detecting a candidate beam pixel, nudge pan and tilt slightly and verify the detected pixel actually moves. A reflection stays put; a real beam moves. | Appendix B §B.3; issue #658. |
| **CPU** | Central Processing Unit | The main processor. | §14 Camera Nodes. |
| **CRGB** | Color RGB | FastLED's C++ struct for a single RGB pixel. | Firmware modules (`GigaLED.h`). |
| **CRUD** | Create, Read, Update, Delete | Shorthand for "all four basic database-style operations." | §4 Fixture Setup; §12 DMX Profiles. |
| **CSI** | Camera Serial Interface | Raspberry Pi's native ribbon-cable camera port (not supported in SlyLED v1.x — use USB cameras). | §14 Camera Nodes. |
| **dark reference** | — | Snapshot captured with all calibration beams off, subtracted from subsequent frames so beam detection isn't fooled by ambient lighting. | Appendix A §A.5; issue #651. |
| **DHCP** | Dynamic Host Configuration Protocol | How devices on a network get an IP address. The board's hostname shows up in DHCP so routers list it by name. | §14 Camera Nodes deployment. |
| **DMX** | Digital Multiplex | Industry-standard lighting-control protocol — 512 channels per universe, carried over a twisted-pair cable or over Ethernet (Art-Net / sACN). | §2 Walkthrough; §12 DMX Profiles; Appendix B. |
| **DOF** | Degrees of Freedom | Independent axes a system can move along. SlyLED's parametric mover model has 6 DOF (yaw, pitch, roll, pan offset, tilt offset, plus scale). | Appendix B §B.3 Model fit. |
| **ESP32** | — | Espressif microcontroller family used for LED-performer nodes (WiFi + dual-core, up to 8 LED strings). | §4 Fixture Setup; §15 Firmware. |
| **extrinsic** | — | A camera's pose (position + rotation) in stage/world space. The solvePnP output. Pair with **intrinsic**. | Appendix A §A.4. |
| **FastLED** | — | Arduino library for driving WS2812B-style addressable LED strips. Used on ESP32 and D1 Mini; **not** reliable on the Giga R1 (custom PWM path instead). | §15 Firmware; CLAUDE.md hardware quirks. |
| **fixture** | — | Any addressable lighting device — an LED strip, a DMX wash, a moving head, or a camera (which registers as a placeable "fixture" so it has a layout position). | §4 Fixture Setup; throughout. |
| **FOV** | Field of View | The angular width a camera or lens sees. Stored as `fovDeg` + `fovType` (horizontal/vertical/diagonal). Used as an intrinsic fallback when true calibrated intrinsics aren't available. | Appendix A §A.3. |
| **FPS** | Frames Per Second | Update rate for live playback or emulation. | §11 Show Preview Emulator. |
| **FQBN** | Fully Qualified Board Name | The arduino-cli identifier for a board target, e.g. `arduino:mbed_giga:giga`. | §15 Firmware. |
| **GET / POST / PUT / DELETE** | — | HTTP methods. GET reads, POST creates/triggers, PUT updates, DELETE removes. | §19 API Quick Reference. |
| **GPIO** | General-Purpose Input/Output | A configurable pin on a microcontroller — used for LED data lines on the ESP32. | §4 Fixture Setup (ESP32 only). |
| **homography** | — | A 3×3 matrix that maps points on one plane to points on another via projective transform. SlyLED uses a pixel↔floor homography as a fast alternative to full 3D extrinsics during calibration. | Appendix A §A.4. |
| **HSV** | Hue, Saturation, Value | A colour representation used for colour-filter beam detection (hue bands identify "the green beam" regardless of brightness). | Appendix A §A.8 Beam detection. |
| **HTML** | HyperText Markup Language | The markup the SPA is built from. | §3 Platform Guide. |
| **HUD** | Heads-Up Display | An overlay showing live state (used in the 3D viewport). | §5 Stage Layout. |
| **ID** | Identifier | Any short key that uniquely names something (fixture ID, ArUco marker ID, etc.). | Throughout. |
| **IK** | Inverse Kinematics | Given a target point, compute the pan/tilt values that aim the beam there. The parametric mover model provides IK once calibration completes. | Appendix B §B.3 Model fit. |
| **intrinsic** | — | A camera's internal optical parameters: focal length (`fx`, `fy`), principal point (`cx`, `cy`), and lens distortion. Independent of where the camera is — that's the **extrinsic**. | Appendix A §A.3. |
| **IP** | Internet Protocol | Addressing scheme for networked devices (`192.168.x.y`). | §14 Camera Nodes. |
| **JPEG** | Joint Photographic Experts Group | Compressed image format used for camera snapshots. | §14 Camera Nodes. |
| **JSON** | JavaScript Object Notation | The text format used for API request/response bodies and persisted data files. | §19 API Quick Reference. |
| **kinematic model** | — | Mathematical model that describes how a fixture's motors translate pan/tilt DMX values into an aim direction in stage space. SlyLED fits a 6-DOF kinematic model per calibrated moving head. | Appendix B §B.3. |
| **LAN** | Local Area Network | The physical/WiFi network the orchestrator and performers share. | §2 Walkthrough. |
| **LED** | Light-Emitting Diode | Addressable RGB LEDs (WS2812B and similar) are the primary fixture type. | §4 Fixture Setup. |
| **LM** | Levenberg–Marquardt | Nonlinear least-squares solver used to fit the parametric mover model to calibration samples. | Appendix B §B.3 Model fit. |
| **LSQ** | Least-Squares | The fitting technique LM refines. When calibration falls back to "median-based" fitting, it's because LSQ wouldn't converge. | Appendix A §A.6. |
| **Mbed OS** | — | The real-time operating system running on the Arduino Giga R1. Explains why `analogWrite()` and some libraries behave differently on the Giga. | CLAUDE.md hardware quirks. |
| **mDNS** | Multicast DNS | Zero-config DNS over multicast — how "SLYC-1234.local" resolves on the LAN without a DNS server. | §14 Camera Nodes deployment. |
| **NTP** | Network Time Protocol | How performers sync their clocks so runner start times are coordinated. | Protocol (`Globals.cpp`). |
| **NVS** | Non-Volatile Storage | ESP32 flash-backed key/value store. SlyLED uses the `"slyled"` namespace. Equivalent to EEPROM on the D1 Mini. | §4 Fixture Setup. |
| **ONNX** | Open Neural Network Exchange | Portable neural-network file format. YOLOv8n and Depth-Anything-V2 ship as ONNX files so they run via `onnxruntime` on ARM. | §14 Camera Nodes. |
| **OFL** | Open Fixture Library | Community-maintained DMX-fixture profile database. SlyLED can import OFL JSON. | §12 DMX Profiles. |
| **orchestrator** | — | The desktop (Windows/Mac) or Giga-parent Flask server that hosts the SPA, designs shows, and drives performers and cameras. One of the three tiers. | §1 Getting Started. |
| **OS** | Operating System | — | CLAUDE.md hardware. |
| **OTA** | Over-the-Air | Firmware update pushed over WiFi instead of via USB. | §15 Firmware & OTA Updates. |
| **PDF** | Portable Document Format | The packaged manual format. Generated by `tests/build_manual.py`. | Appendix C. |
| **performer** | — | An ESP32, D1 Mini, or Giga-child LED execution node. One of the three tiers. | §1 Getting Started. |
| **PnP / solvePnP** | Perspective-n-Point | OpenCV algorithm that computes a camera's 3D pose from ≥3 known 2D↔3D point correspondences. `SOLVEPNP_SQPNP` is the preferred solver; `SOLVEPNP_ITERATIVE` is the fallback. | Appendix A §A.4. |
| **PNG** | Portable Network Graphics | Lossless image format used for screenshots. | §2 Walkthrough. |
| **PR** | Pull Request | Git/GitHub workflow — a proposed change on a branch, reviewed before merge. | Appendix C §C.4. |
| **PWM** | Pulse-Width Modulation | Dimming technique where the LED is switched on and off fast. On the Giga R1 this is implemented in software because `analogWrite()` is banned on the onboard RGB pins. | CLAUDE.md hardware quirks. |
| **QA** | Quality Assurance | Testing role — in SlyLED's workflow, QA runs the Playwright + test suites and files issues rather than patching source. | Appendix C. |
| **QR** | Quick Response (code) | 2D barcode. Not the same as an ArUco marker — ArUco is designed for solvePnP, QR for data payloads. | — |
| **RANSAC** | Random Sample Consensus | Robust plane-fitting algorithm — samples random small subsets, finds the model with the most inliers. SlyLED uses it to detect floor and wall planes in noisy point clouds. | Appendix A §A.7. |
| **reprojection RMS** | — | After solvePnP, project the 3D points back through the solved pose and measure the pixel distance to the detected corners. Reported as root-mean-square across all points. <2 px is excellent, 2–5 px is usable, >5 px means something is wrong. | Appendix A §A.4. |
| **RGB / RGBW** | Red, Green, Blue [, White] | Standard LED colour models. RGBW adds a dedicated white LED for purer whites. | §4 Fixture Setup. |
| **RMS** | Root-Mean-Square | Quadratic-mean aggregation of errors (`sqrt(mean(x²))`). More sensitive to outliers than a plain mean — which is why it's used as a calibration quality metric. | Appendix A §A.4. |
| **Rodrigues** | — | Mathematical conversion between a rotation vector (`rvec` from solvePnP) and a 3×3 rotation matrix. `cv2.Rodrigues()`. | Appendix A §A.4. |
| **RSSI** | Received Signal Strength Indicator | How strong a WiFi signal a performer is hearing. Reported in dBm; the orchestrator stores it as an unsigned magnitude so "69" means "−69 dBm". | UDP protocol PONG payload. |
| **RTOS** | Real-Time Operating System | An OS with deterministic timing guarantees. Mbed OS on the Giga is an RTOS. | CLAUDE.md hardware. |
| **runner** | — | A step sequencer loaded into a performer. Each step is an action (colour, pattern, LED range) with a duration; the runner loops the step list in sync with the orchestrator. | §4 Fixture Setup; §13 Preset Shows. |
| **RX / RY / RZ** | — | Rotations about the X, Y, Z axes of the stage frame, in degrees. In schema v2: `rx` = pitch, `ry` = roll, `rz` = yaw/pan. Never read `rotation[1]` or `rotation[2]` directly — always go through `rotation_from_layout()`. | Appendix A §A.9. |
| **sACN** | Streaming ACN | DMX-over-Ethernet alternative to Art-Net, defined by RFC 7724. SlyLED speaks both. | §12 DMX Profiles. |
| **SCP** | Secure Copy Protocol | File transfer over SSH. How camera firmware reaches the Orange Pi / Raspberry Pi. | §15 Firmware → Camera deploy. |
| **solvePnP** | — | See **PnP**. | Appendix A §A.4. |
| **SPA** | Single-Page Application | The desktop orchestrator UI is one HTML page that loads JavaScript modules instead of navigating between pages. | §3 Platform Guide. |
| **SQPNP** | — | A specific solvePnP algorithm variant (`cv2.SOLVEPNP_SQPNP`) chosen because it tolerates fewer correspondences than the iterative solver. | Appendix A §A.4. |
| **SRAM** | Static Random-Access Memory | The fast, volatile RAM on a microcontroller. Tight budget on the D1 Mini — the manual warns against String objects and heap allocation. | CLAUDE.md performance rules. |
| **SSH** | Secure Shell | Encrypted remote-login protocol. How the orchestrator reaches camera-node shells for firmware deployment. | §15 Firmware → Camera deploy. |
| **SVG** | Scalable Vector Graphics | Vector image format used by diagram exporters. | Appendix C. |
| **TCP** | Transmission Control Protocol | Reliable, connection-based networking. HTTP traffic (config pages, API calls) rides on TCP. | UDP protocol discussion. |
| **tiling** | — | Sliced-Aided Hyper-Inference (SAHI)-style detection: break a large image into overlapping patches, run the detector on each, stitch results. Improves small-object detection accuracy at the cost of runtime. Controlled by the `tile` option on `/scan`. | §14 Camera Nodes. |
| **TTL** | Time-To-Live | A timeout after which a resource (e.g. a mover claim) auto-expires. Mover-control claims have a 15 s TTL. | Appendix B §B.7. |
| **UDP** | User Datagram Protocol | Connectionless, best-effort networking. Used for all orchestrator↔performer traffic (discovery, actions, runner control) because it's low-latency and the wire protocol tolerates occasional packet loss. | Wire protocol; CLAUDE.md §UDP binary protocol. |
| **UI** | User Interface | — | Throughout. |
| **universe** | — | A DMX addressing space — 1–512 channels. A show typically spans multiple universes; Art-Net addresses them as `net.subnet.universe`. | §12 DMX Profiles. |
| **URL** | Uniform Resource Locator | Web address. | §14 Camera Nodes. |
| **USB** | Universal Serial Bus | — | §15 Firmware USB flash. |
| **V4L2** | Video for Linux 2 | The kernel video-capture API used by camera nodes (`cv2.VideoCapture` on Orange Pi / Raspberry Pi). SoC ISP video nodes like `sunxi-vin` and `bcm2835-isp` are filtered out — only regular USB cameras register. | §14 Camera Nodes. |
| **WiFi** | — | 802.11 wireless networking. Performers and camera nodes join the orchestrator's LAN over WiFi. | §2 Walkthrough. |
| **WLED** | — | Popular open-source firmware for ESP32/8266-based LED controllers. SlyLED includes a bridge so WLED devices can appear as performers. | §4 Fixture Setup; `desktop/shared/wled_bridge.py`. |
| **WS2812B** | — | Common addressable-RGB LED chip (aka "NeoPixel"). The ESP32 RMT peripheral drives it in hardware; the D1 Mini bit-bangs it in software. | §4 Fixture Setup. |
| **YOLO** | You Only Look Once | Single-pass object-detection neural network. SlyLED camera nodes run YOLOv8n via ONNX Runtime for person/object detection on `POST /scan`. | §14 Camera Nodes. |
| **ZIP** | — | Archive file format, used for the release bundle. | §15 Firmware Registry. |

> **Not sure what something means?** If a term appears in the manual but isn't in this table, that's a bug in the glossary — open an issue or PR against [#663](https://github.com/SlyWombat/SlyLED/issues/663).

---

<a id="appendix-a"></a>

