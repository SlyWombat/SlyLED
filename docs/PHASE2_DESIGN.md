# Phase 2 Design — Parent/Child Network LED Controller

## Table of Contents

1. [System Topology](#1-system-topology)
2. [Role Separation](#2-role-separation)
3. [Coordinate System](#3-coordinate-system)
4. [Parent Data Structures](#4-parent-data-structures)
5. [Child Data Structures](#5-child-data-structures)
6. [Wire Protocol (UDP)](#6-wire-protocol-udp)
7. [Parent HTTP API](#7-parent-http-api)
8. [Pre-Computation Algorithm](#8-pre-computation-algorithm)
9. [Runner Execution and Sync](#9-runner-execution-and-sync)
10. [Web UI — Tab Data Models](#10-web-ui--tab-data-models)
11. [Memory Budget](#11-memory-budget)
12. [Implementation Roadmap](#12-implementation-roadmap)

---

## 1. System Topology

```
  ┌─────────────────────────────────────────────────────────┐
  │  Home WiFi LAN (2.4 GHz)                                │
  │                                                         │
  │  ┌──────────────────┐        UDP port 4210             │
  │  │  PARENT           │◄──────────────────────────────┐ │
  │  │  Arduino Giga     │──────────────────────────────►│ │
  │  │  "slyled"         │                               │ │
  │  │  No LEDs attached │       HTTP port 80            │ │
  │  │  Serves SPA UI    │◄── browser ──────────────────  │ │
  │  └──────────────────┘                               │ │
  │                                                     │ │
  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │ │
  │  │  CHILD A    │  │  CHILD B    │  │  CHILD C    │ │ │
  │  │  ESP32      │  │  ESP32      │  │  D1 Mini    │ │ │
  │  │  8× WS2812B │  │  8× WS2812B │  │  8× WS2812B │ │ │
  │  │  UDP :4210  │  │  UDP :4210  │  │  UDP :4210  │ │ │
  │  └─────────────┘  └─────────────┘  └─────────────┘ │ │
  │         └─────────────────────────────────────────────┘ │
  └─────────────────────────────────────────────────────────┘
```

- **One parent** (Giga): manages layout, computes runner payloads, serves the browser UI, coordinates timing.
- **Many children** (ESP32 / D1 Mini): own the LEDs, execute actions locally, report status and configuration.
- **Communication**: UDP on port 4210 (commands and status). All nodes share a WiFi LAN.
- **Timing sync**: NTP — already implemented on all three boards. Parent and children all sync to `pool.ntp.org`. Sync'd epoch timestamps make coordinated execution deterministic without a dedicated protocol.

---

## 2. Role Separation

### Parent (Giga)

| Responsibility | Notes |
|----------------|-------|
| Serve browser SPA | Five tabs: DASHBOARD, SETUP, LAYOUT, ACTIONS, RUNTIME |
| Child registry | Discover children via UDP broadcast; store config |
| Canvas layout | Position children and their LED strings in 2D space |
| Action definitions | Store Solid, Flash, Wipe, Off configs |
| Runner definitions | Sequences of (action, area, duration) |
| Pre-computation | For each runner step, calculate which LEDs on each child are in the area of effect |
| Runner dispatch | Send per-child step payloads over UDP; send GO at synced epoch |
| No LEDs | All `#ifdef BOARD_GIGA` LED pattern code removed in Phase 2 |

### Children (ESP32 / D1 Mini)

| Responsibility | Notes |
|----------------|-------|
| Own LED strips | FastLED WS2812B on GPIO 2 |
| Auto hostname | Set to `SLYC-XXXX` where XXXX is the last 4 hex digits of the MAC address (e.g. `SLYC-A4F3`) |
| Config webpage | Minimal local web UI (`GET /config`, `POST /config`) — user sets string count, LED count, length, LED type, cable direction/distance per string, alternate name, description |
| EEPROM persistence | String config, alternate name, and description saved to EEPROM on child — survive power cycles |
| Report config | Sends hostname, altName, description, and all string configs in CMD_PONG |
| Store local patterns | Rainbow, Siren remain as fallback when no runner is active |
| Execute runner steps | Apply action to assigned LED ranges at the right epoch time |
| Respond to UDP | CMD_PING → CMD_PONG with current status and full config |

---

## 3. Coordinate System

### Physical space

All physical measurements are stored as **`int16_t` millimetres** (range ±32,767 mm = ±32.7 m).

```
      Y (mm)
      ▲
      │       ← canvas physical extents (configurable, default 10,000 × 5,000 mm)
      │
      │    [child B]────►string
      │       ▲
      │       │
      │    [child A]────►string
      │
      └─────────────────────────► X (mm)
   (0,0)
```

- Origin (0,0) is the bottom-left of the canvas.
- Z-axis (height) is stored but the initial UI renders a top-down 2D view.

### Canvas UI space

Area-of-effect and browser rendering use **percentage coordinates** stored as `uint16_t` in units of 0.01%:

```
0       = 0.00%   (left / bottom edge)
10000   = 100.00% (right / top edge)
```

This avoids all floating-point arithmetic on the parent.

Conversion:
```
canvas_x = (phys_x_mm * 10000) / canvasWidthMm    // no float
phys_x_mm = (canvas_x * canvasWidthMm) / 10000
```

### String direction encoding

```cpp
enum Direction : uint8_t {
    DIR_E = 0,  // right  (+X)
    DIR_N = 1,  // up     (+Y)
    DIR_W = 2,  // left   (-X)
    DIR_S = 3   // down   (-Y)
};
```

Unit vectors for each direction:

| DIR | dx | dy |
|-----|----|----|
| E   | +1 |  0 |
| N   |  0 | +1 |
| W   | -1 |  0 |
| S   |  0 | -1 |

### LED position formula

For LED index `i` on a string with known origin (`sx`, `sy`) and strip direction (`sdir`):

```
led_x_mm = sx + (i * stringLengthMm / (ledCount - 1)) * dx[sdir]
led_y_mm = sy + (i * stringLengthMm / (ledCount - 1)) * dy[sdir]
```

Where string origin:
```
sx = childX + cableMm * dx[cableDir]
sy = childY + cableMm * dy[cableDir]
```

Integer arithmetic only — multiply before divide to preserve precision.

---

## 4. Parent Data Structures

All parent structures live in Giga SRAM. Giga has 524 KB free; all structures below total < 10 KB.

### Constants

```cpp
constexpr uint8_t  MAX_CHILDREN      = 8;
constexpr uint8_t  MAX_STRINGS       = 4;    // strings per child
constexpr uint8_t  MAX_RUNNERS       = 4;
constexpr uint8_t  MAX_STEPS         = 16;   // steps per runner
constexpr uint8_t  HOSTNAME_LEN      = 10;   // "SLYC-XXXX\0"
constexpr uint8_t  CHILD_NAME_LEN    = 16;   // alternate name, including null
constexpr uint8_t  CHILD_DESC_LEN    = 32;   // description, including null
constexpr uint8_t  RUNNER_NAME_LEN   = 16;   // including null
```

### LedType — LED strip hardware type

```cpp
enum LedType : uint8_t {
    LED_WS2812B = 0,   // current hardware on all ESP boards
    LED_WS2811  = 1,
    LED_APA102  = 2,
};
```

### StringInfo — one LED strip attached to a child

Each string has two separate directions: the **cable direction** (which way the cable runs from the controller node to where the strip starts) and the **strip direction** (which way the strip itself runs once it begins). These are independent — e.g. a cable can run East from the node for 500 mm, then the strip itself runs North.

```cpp
struct StringInfo {                     // 10 bytes
    uint16_t ledCount;      // number of LEDs on this strip
    uint16_t lengthMm;      // physical length of the strip in mm (max 65.5 m)
    uint8_t  ledType;       // LedType enum
    uint8_t  cableDir;      // Direction: cable from node to strip start
    uint16_t cableMm;       // cable distance from node to strip start (mm)
    uint8_t  stripDir;      // Direction: which way the strip runs from its start point
};
```

### ChildNode — one registered child

```cpp
enum ChildStatus : uint8_t {
    CHILD_UNKNOWN = 0,
    CHILD_ONLINE  = 1,
    CHILD_OFFLINE = 2
};

struct ChildNode {                          // 112 bytes
    uint8_t     ip[4];                      // IPv4 address
    char        hostname[HOSTNAME_LEN];     // auto: "SLYC-XXXX\0"
    char        name[CHILD_NAME_LEN];       // user-set alternate name
    char        description[CHILD_DESC_LEN]; // user-set description
    int16_t     xMm;                        // position on canvas X (mm)
    int16_t     yMm;                        // position on canvas Y (mm)
    int16_t     zMm;                        // height above floor (mm)
    uint8_t     stringCount;                // 1–MAX_STRINGS
    StringInfo  strings[MAX_STRINGS];       // 4 × 10 = 40 bytes
    ChildStatus status;
    uint32_t    lastSeenEpoch;              // Unix timestamp of last UDP contact
    bool        configFetched;              // string config received from child
    bool        inUse;                      // slot occupied
};
// Parent array: ChildNode children[MAX_CHILDREN]  →  8 × 112 = 896 bytes
```

### Action — one LED effect

```cpp
enum ActionType : uint8_t {
    ACT_OFF   = 0,
    ACT_SOLID = 1,
    ACT_FLASH = 2,
    ACT_WIPE  = 3
};

struct Action {                         // 10 bytes
    ActionType type;        // 1 byte
    uint8_t    r, g, b;    // RGB colour (ignored for ACT_OFF)
    uint16_t   onMs;        // ACT_FLASH: on duration (ms)
    uint16_t   offMs;       // ACT_FLASH: off duration (ms)
    uint8_t    wipeDir;     // ACT_WIPE: Direction enum
    uint8_t    wipeSpeedPct; // ACT_WIPE: % of canvas width covered per second (1–100)
};
```

### AreaRect — area of effect on the canvas

```cpp
struct AreaRect {                       // 8 bytes
    uint16_t x0;   // left   edge (0–10000, units of 0.01%)
    uint16_t y0;   // bottom edge
    uint16_t x1;   // right  edge
    uint16_t y1;   // top    edge
};
// "All" == { 0, 0, 10000, 10000 }
```

### ChildStepPayload — pre-computed LED selection for one child/step pair

```cpp
struct ChildStepPayload {               // 8 bytes
    uint8_t ledStart[MAX_STRINGS];   // first LED index in area (0xFF = string not affected)
    uint8_t ledEnd[MAX_STRINGS];     // last  LED index in area (inclusive)
};
```

Stores a contiguous LED range per string. For Wipe actions the range is computed at runtime on the child using elapsed time — `ledStart`/`ledEnd` represent the full eligible range, not a moment in time.

### RunnerStep — one step in a runner

```cpp
struct RunnerStep {                     // 20 bytes
    Action   action;
    AreaRect area;
    uint16_t durationS;   // how long to run this step (seconds)
};
```

### Runner — a named sequence with pre-computed payloads

```cpp
struct Runner {                                         // 1,363 bytes
    char             name[RUNNER_NAME_LEN];             // 16 bytes
    uint8_t          stepCount;                         // 1 byte
    bool             computed;                          // payloads are valid
    bool             inUse;                             // slot occupied
    RunnerStep       steps[MAX_STEPS];                  // 16 × 20 = 320 bytes
    ChildStepPayload payload[MAX_STEPS][MAX_CHILDREN];  // 16 × 8 × 8 = 1,024 bytes
};
// Parent array: Runner runners[MAX_RUNNERS]  →  4 × 1,363 = 5,452 bytes
```

### AppSettings

```cpp
enum Units : uint8_t { UNITS_METRIC = 0, UNITS_IMPERIAL = 1 };

struct AppSettings {                    // 24 bytes
    Units    units;                     // metric / imperial
    uint8_t  darkMode;                  // 0 = light, 1 = dark
    uint16_t canvasWidthMm;             // physical canvas width  (default 10,000 mm)
    uint16_t canvasHeightMm;            // physical canvas height (default  5,000 mm)
    char     parentName[16];            // display name for the parent node
    uint8_t  activeRunner;              // 0xFF = none; index into runners[]
    bool     runnerRunning;             // currently executing
};
```

### Total parent RAM for Phase 2 data

| Structure | Size |
|-----------|------|
| `children[8]` | 896 bytes |
| `runners[4]` | 5,452 bytes |
| `AppSettings` | 24 bytes |
| **Total** | **6,372 bytes** |

Well within the Giga's 524 KB SRAM.

---

## 5. Child Data Structures

Children carry minimal state. The goal is to keep the D1 Mini's SRAM impact under 1 KB for Phase 2 additions.

### Child self-config (stored in child EEPROM, reported to parent)

```cpp
// Child-side constants (match parent values)
constexpr uint8_t CHILD_MAX_STRINGS = 4;
constexpr uint8_t CHILD_HOSTNAME_LEN = 10;  // "SLYC-XXXX\0"
constexpr uint8_t CHILD_NAME_LEN     = 16;
constexpr uint8_t CHILD_DESC_LEN     = 32;

struct ChildStringCfg {                 // 10 bytes (matches StringInfo)
    uint16_t ledCount;
    uint16_t lengthMm;
    uint8_t  ledType;       // LedType enum
    uint8_t  cableDir;      // Direction enum — cable from node to strip start
    uint16_t cableMm;       // cable length in mm
    uint8_t  stripDir;      // Direction enum — which way the strip runs
};

struct ChildSelfConfig {                // 83 bytes
    char           hostname[CHILD_HOSTNAME_LEN]; // auto: "SLYC-XXXX\0" (from MAC)
    char           altName[CHILD_NAME_LEN];      // user-set, e.g. "UPPER 1"
    char           description[CHILD_DESC_LEN];  // user-set, e.g. "upper left strings"
    uint8_t        stringCount;
    ChildStringCfg strings[CHILD_MAX_STRINGS];   // 4 × 10 = 40 bytes
};
```

**EEPROM layout on child:**

```
Byte  0     : magic byte 0xA5 (detect uninitialised EEPROM)
Bytes 1–83  : ChildSelfConfig (83 bytes)
```

On boot: read EEPROM. If `magic != 0xA5`, populate defaults (stringCount=1, 8 LEDs, WS2812B, 500 mm, all DIR_E) and write. This avoids requiring any initial configuration before the child can operate.

**Hostname generation** (called once in `setup()`, before `WiFi.begin()`):

```cpp
// ESP32
uint8_t mac[6];
WiFi.macAddress(mac);
snprintf(cfg.hostname, CHILD_HOSTNAME_LEN, "SLYC-%02X%02X", mac[4], mac[5]);
WiFi.setHostname(cfg.hostname);

// D1 Mini
WiFi.macAddress(mac);
snprintf(cfg.hostname, CHILD_HOSTNAME_LEN, "SLYC-%02X%02X", mac[4], mac[5]);
WiFi.hostname(cfg.hostname);
```

### Child config webpage

The child extends its existing SPA with two new routes:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/config` | Serve config form (HTML) |
| POST | `/config` | Save updated config to EEPROM and RAM |

The config form fields:
- **Alternate name** — text input, max 15 chars
- **Description** — text input, max 31 chars
- **Number of strings** — select 1–4
- Per string (repeat for each):
  - LED count — number input
  - Strip length (mm or inches, per settings) — number input
  - LED type — select (WS2812B / WS2811 / APA102)
  - Cable direction from node — select (E / N / W / S)
  - Cable length — number input
  - Strip direction — select (E / N / W / S)

On `POST /config`, the child parses the form body (URL-encoded, small enough to fit in `char buf[256]`), updates `cfg` in RAM, and writes to EEPROM. Returns redirect to `/config` (HTTP 303).

The child does **not** implement AJAX for config — a full page reload is acceptable here since config changes are rare.

### Child runner buffer (loaded by parent before GO)

```cpp
constexpr uint8_t CHILD_MAX_STEPS = 16;

struct ChildRunnerStep {                // 20 bytes
    uint8_t  actionType;               // ActionType enum (uint8_t to avoid prototype issue)
    uint8_t  r, g, b;
    uint16_t onMs;
    uint16_t offMs;
    uint8_t  wipeDir;
    uint8_t  wipeSpeedPct;
    uint16_t durationS;
    uint8_t  ledStart[CHILD_MAX_STRINGS]; // 0xFF = string not in this step
    uint8_t  ledEnd[CHILD_MAX_STRINGS];
};
// Buffer: ChildRunnerStep childRunner[CHILD_MAX_STEPS]  →  16 × 20 = 320 bytes

uint8_t  childStepCount    = 0;
uint8_t  childStepLoaded   = 0;     // how many steps received so far
uint32_t childRunnerStart  = 0;     // epoch to start execution
bool     childRunnerArmed  = false; // all steps loaded, awaiting GO
bool     childRunnerActive = false;
```

Phase 2 SRAM additions on child: ~370 bytes. D1 Mini headroom is sufficient.

---

## 6. Wire Protocol (UDP)

**Port**: 4210 on all nodes.
**Transport**: UDP unicast for directed messages; UDP broadcast (`255.255.255.255`) for discovery.
**Max packet size**: 128 bytes (all packets below are smaller — well within WiFi MTU, no IP fragmentation).

### Header (8 bytes — present in every packet)

```cpp
constexpr uint16_t UDP_MAGIC   = 0x534C;  // 'S','L'
constexpr uint8_t  UDP_VERSION = 2;

struct UdpHeader {     // 8 bytes
    uint16_t magic;    // 0x534C — identifies SlyLED packets
    uint8_t  version;  // UDP_VERSION
    uint8_t  cmd;      // UdpCmd enum
    uint32_t epoch;    // sender's current Unix timestamp (NTP time)
};
```

### Command codes

```cpp
enum UdpCmd : uint8_t {
    // Discovery
    CMD_PING        = 0x01,  // parent → broadcast: are you there?
    CMD_PONG        = 0x02,  // child  → parent:    I'm here + full config

    // Immediate actions (no runner)
    CMD_ACTION      = 0x10,  // parent → child: execute action now (no duration limit)
    CMD_ACTION_STOP = 0x11,  // parent → child: stop immediate action, return to local pattern

    // Runner loading
    CMD_LOAD_STEP   = 0x20,  // parent → child: load one runner step into buffer
    CMD_LOAD_ACK    = 0x21,  // child  → parent: step received OK (echoes stepIndex)

    // Runner execution
    CMD_RUNNER_GO   = 0x30,  // parent → child: start runner at given epoch
    CMD_RUNNER_STOP = 0x31,  // parent → child: stop runner now

    // Status
    CMD_STATUS_REQ  = 0x40,  // parent → child: send status summary
    CMD_STATUS_RESP = 0x41,  // child  → parent: status summary
};
```

### Packet definitions

#### CMD_PING — 8 bytes (header only)

```
UdpHeader (cmd=CMD_PING)
```

Sent by parent to `255.255.255.255:4210` on startup and every 30 s. Children respond with CMD_PONG.

---

#### CMD_PONG — 100 bytes

```cpp
struct PongPacket {             // 100 bytes total
    UdpHeader hdr;              // 8 bytes
    char      hostname[10];     // "SLYC-XXXX\0" — auto-generated from MAC
    char      altName[16];      // user-set alternate name
    char      description[32];  // user-set description
    uint8_t   stringCount;      // 1 byte
    struct {
        uint16_t ledCount;
        uint16_t lengthMm;
        uint8_t  ledType;       // LedType enum
        uint8_t  cableDir;      // Direction: cable from node to strip start
        uint16_t cableMm;       // cable length mm
        uint8_t  stripDir;      // Direction: which way the strip runs
    } strings[4];               // 4 × 10 = 40 bytes
};
```

---

#### CMD_ACTION — 26 bytes

```cpp
struct ActionPacket {       // 26 bytes total
    UdpHeader        hdr;   // 8 bytes
    uint8_t          actionType;
    uint8_t          r, g, b;
    uint16_t         onMs;
    uint16_t         offMs;
    uint8_t          wipeDir;
    uint8_t          wipeSpeedPct;
    uint8_t          ledStart[4]; // which LEDs affected per string
    uint8_t          ledEnd[4];
};
```

---

#### CMD_LOAD_STEP — 38 bytes

```cpp
struct LoadStepPacket {     // 38 bytes total
    UdpHeader hdr;          // 8 bytes
    uint8_t   stepIndex;    // 0-based index into child's buffer
    uint8_t   totalSteps;   // total steps in this runner
    uint8_t   actionType;
    uint8_t   r, g, b;
    uint16_t  onMs;
    uint16_t  offMs;
    uint8_t   wipeDir;
    uint8_t   wipeSpeedPct;
    uint16_t  durationS;
    uint8_t   ledStart[4];
    uint8_t   ledEnd[4];
};
```

Parent sends steps sequentially. Child writes each into `childRunner[stepIndex]`, sends CMD_LOAD_ACK. Parent waits for ACK before sending next step (simple stop-and-wait — low overhead for ≤16 steps).

#### CMD_LOAD_ACK — 9 bytes

```cpp
struct LoadAckPacket {      // 9 bytes
    UdpHeader hdr;
    uint8_t   stepIndex;    // echoes the step just received
};
```

---

#### CMD_RUNNER_GO — 12 bytes

```cpp
struct RunnerGoPacket {     // 12 bytes
    UdpHeader hdr;
    uint32_t  startEpoch;   // Unix timestamp: execute step 0 at this moment
};
```

Parent sets `startEpoch = now + 2` (2 seconds in the future) to give all children time to receive it. Children compare against their NTP-synced `time(nullptr)`.

---

#### CMD_RUNNER_STOP / CMD_ACTION_STOP — 8 bytes (header only)

```cpp
// UdpHeader only, cmd = CMD_RUNNER_STOP or CMD_ACTION_STOP
```

---

#### CMD_STATUS_RESP — 16 bytes

```cpp
struct StatusRespPacket {   // 16 bytes
    UdpHeader hdr;
    uint8_t   activeAction;    // ActionType currently running
    bool      runnerActive;
    uint8_t   currentStep;     // 0-based step index if runner active
    uint8_t   wifiRssi;        // abs(RSSI) — e.g. 70 means -70 dBm
    uint32_t  uptimeS;         // seconds since boot
};
```

---

### Sequence diagrams

#### Discovery

```
Parent                                     Children
  │                                            │
  │  UDP broadcast CMD_PING                   │
  │──────────────────────────────────────────►│
  │                                            │
  │◄── CMD_PONG (child A) ─────────────────── │
  │◄── CMD_PONG (child B) ─────────────────── │
  │  (parent registers both)                  │
```

#### Load and start a runner

```
Parent                                     Child
  │                                            │
  │  CMD_LOAD_STEP (step 0)                   │
  │──────────────────────────────────────────►│
  │◄── CMD_LOAD_ACK (step 0) ─────────────── │
  │  CMD_LOAD_STEP (step 1)                   │
  │──────────────────────────────────────────►│
  │◄── CMD_LOAD_ACK (step 1) ─────────────── │
  │  ...                                      │
  │  CMD_RUNNER_GO (startEpoch = now+2)       │  ← broadcast or unicast to each child
  │──────────────────────────────────────────►│
  │                          (at T=startEpoch)│
  │                          child executes   │
  │                          step 0 locally   │
```

---

## 7. Parent HTTP API

All existing routes (`/`, `/status`, `/led/*`, `/log`, `/favicon.ico`) are preserved on the parent Giga for backward compatibility during transition.

New API routes added for Phase 2:

### Children

| Method | Path | Body | Response | Description |
|--------|------|------|----------|-------------|
| GET | `/api/children` | — | JSON array | List all registered children |
| POST | `/api/children` | `{ip}` | `{ok, id}` | Register a child by IP (parent sends CMD_PING to that IP; name/config arrive via CMD_PONG) |
| DELETE | `/api/children/:id` | — | `{ok}` | Remove child |
| GET | `/api/children/:id/status` | — | JSON | Poll child status via UDP |
| POST | `/api/children/:id/refresh` | — | `{ok}` | Force re-fetch child config via CMD_PING |
| GET | `/api/children/export` | — | JSON | Export selected children (query param: `?ids=0,2,5` or omit for all) |
| POST | `/api/children/import` | JSON array | `{ok, added, skipped}` | Import children; body is same format as export; existing children (matched by hostname) are updated not duplicated |

### Layout

| Method | Path | Body | Response | Description |
|--------|------|------|----------|-------------|
| GET | `/api/layout` | — | JSON | Canvas extents + all child positions |
| POST | `/api/layout` | JSON | `{ok}` | Save positions and canvas size |

### Runners

| Method | Path | Body | Response | Description |
|--------|------|------|----------|-------------|
| GET | `/api/runners` | — | JSON array | List all runners |
| POST | `/api/runners` | `{name}` | `{ok, id}` | Create empty runner |
| PUT | `/api/runners/:id` | JSON | `{ok}` | Save runner steps |
| DELETE | `/api/runners/:id` | — | `{ok}` | Delete runner |
| POST | `/api/runners/:id/compute` | — | `{ok}` | Run pre-computation |
| POST | `/api/runners/:id/sync` | — | `{ok}` | Load steps to all children |
| POST | `/api/runners/:id/start` | — | `{ok}` | Send CMD_RUNNER_GO |
| POST | `/api/runners/stop` | — | `{ok}` | Send CMD_RUNNER_STOP to all |

### Settings

| Method | Path | Body | Response | Description |
|--------|------|------|----------|-------------|
| GET | `/api/settings` | — | JSON | Current app settings |
| POST | `/api/settings` | JSON | `{ok}` | Update settings |

### Response format conventions

- All JSON responses include `Content-Length` (existing practice).
- `:id` is a 0-based index (0–7 for children, 0–3 for runners), passed as a single path segment.
- Request bodies are minimal flat JSON — no nested objects in write requests.
- Error response: `{"ok":false,"err":"<reason>"}` — `err` is a short ASCII string, no spaces (fits in a `char[32]`).

### Import/export JSON format

```json
[
  {
    "hostname": "SLYC-A4F3",
    "name": "UPPER 1",
    "desc": "upper left corner strings",
    "ip": "192.168.1.42",
    "x": 1000, "y": 2000, "z": 500,
    "sc": 2,
    "s": [
      {"lc": 8, "lm": 1000, "lt": 0, "cd": 0, "cm": 200, "sd": 1},
      {"lc": 8, "lm": 800,  "lt": 0, "cd": 1, "cm": 150, "sd": 0}
    ]
  }
]
```

Short keys keep the export compact (target < 200 bytes per child, < 1,600 bytes for 8 children). This fits comfortably in a static `char buf[1700]` on the Giga for building the response.

For import, the Giga reads the POST body up to `Content-Length` bytes (max 1,700 bytes accepted) into the same buffer, then parses with `strstr` / manual extraction. The existing `serveClient()` must be extended to read `Content-Length` from request headers and then read the body for POST routes that require it.

### Route parsing

`serveClient()` already uses `strstr()` on the request line. Phase 2 extends this with a secondary parse for `:id`:

```cpp
// Pattern: strstr(req, " /api/children/") returns non-null
// Extract id: req[len("/api/children/")] - '0'  (single-digit index)
```

Single-digit index (0–9) avoids `atoi()` and keeps parsing zero-allocation.

`/api/children/export` and `/api/children/import` are matched before the `/:id` check (longer path = checked first).

---

## 8. Pre-Computation Algorithm

Triggered by `POST /api/runners/:id/compute`. Runs entirely on the parent in the main loop (no LED thread involvement). Giga is fast enough (480 MHz M7) that this completes in milliseconds.

### Input
- `runners[id]`: all steps with `Action` and `AreaRect`
- `children[]`: all registered children with positions and string configs
- `settings`: canvas physical dimensions

### For each step S (0 to stepCount-1)

For each child C (0 to MAX_CHILDREN-1 where `inUse`):

1. Compute **string origin** in mm:
   ```
   sx = children[C].xMm + children[C].strings[j].cableMm * dx[cableDir]
   sy = children[C].yMm + children[C].strings[j].cableMm * dy[cableDir]
   ```

2. Compute **area of effect in mm**:
   ```
   axMin = (steps[S].area.x0 * settings.canvasWidthMm)  / 10000
   axMax = (steps[S].area.x1 * settings.canvasWidthMm)  / 10000
   ayMin = (steps[S].area.y0 * settings.canvasHeightMm) / 10000
   ayMax = (steps[S].area.y1 * settings.canvasHeightMm) / 10000
   ```

3. For each string J on child C: find the **LED range** that falls within the AoE rectangle:
   - Walk LEDs from 0 to ledCount-1
   - LED i is at: `(sx + i*stepX, sy + i*stepY)` where `stepX = lengthMm*dx[stripDir]/(ledCount-1)`
   - First LED inside AoE → `ledStart`
   - Last  LED inside AoE → `ledEnd`
   - If none → `ledStart = 0xFF` (string not in this step)

4. Store result in `runners[id].payload[S][C]`.

### Output

`runners[id].computed = true`. Each `payload[S][C]` is now valid for dispatch.

### Integer arithmetic note

All intermediate products fit in `int32_t`. The maximum product is `65535 * 10000 = 655,350,000` which fits in `int32_t` (max ~2.1 billion).

---

## 9. Runner Execution and Sync

### Dispatch sequence (parent)

```cpp
void syncRunner(uint8_t runnerId) {
    Runner& r = runners[runnerId];
    // For each child:
    for (uint8_t c = 0; c < MAX_CHILDREN; c++) {
        if (!children[c].inUse) continue;
        for (uint8_t s = 0; s < r.stepCount; s++) {
            sendLoadStep(c, s, r);   // UDP CMD_LOAD_STEP
            waitForAck(c, s, 200);   // 200 ms timeout per step
        }
    }
    settings.activeRunner  = runnerId;
    settings.runnerRunning = false;  // armed but not yet started
}

void startRunner() {
    uint32_t startEpoch = (uint32_t)time(nullptr) + 2;  // 2 s from now
    broadcastRunnerGo(startEpoch);
    settings.runnerRunning = true;
}
```

### Execution on child

```cpp
void ledTask() {
    while (true) {
        if (childRunnerActive) {
            uint32_t now     = (uint32_t)time(nullptr);
            uint32_t elapsed = now - childRunnerStart;
            // Walk steps to find which one is current
            uint32_t acc = 0;
            uint8_t  cur = 0;
            for (uint8_t i = 0; i < childStepCount; i++) {
                acc += childRunner[i].durationS;
                if (elapsed < acc) { cur = i; break; }
            }
            if (elapsed >= acc) {
                childRunnerActive = false;  // runner finished
            } else {
                applyStep(cur, elapsed);
            }
        } else {
            // Fall back to local patterns (Rainbow / Siren)
            applyLocalPattern();
        }
        delay(20);
    }
}
```

### applyStep — child LED execution

```cpp
void applyStep(uint8_t stepIdx, uint32_t elapsedS) {
    ChildRunnerStep& s = childRunner[stepIdx];
    switch (s.actionType) {
    case ACT_SOLID:
        for (uint8_t j = 0; j < childStringCount; j++) {
            if (s.ledStart[j] == 0xFF) continue;
            fill_solid(leds + s.ledStart[j],
                       s.ledEnd[j] - s.ledStart[j] + 1,
                       CRGB(s.r, s.g, s.b));
        }
        FastLED.show();
        break;

    case ACT_FLASH:
        // Use millis() for sub-second flash timing within the step
        // ...

    case ACT_WIPE:
        // Compute leading edge position from elapsedMs within step
        // wipeSpeedPct tells how many percent of the string per second
        // ...

    case ACT_OFF:
        fill_solid(leds, NUM_LEDS, CRGB::Black);
        FastLED.show();
        break;
    }
}
```

### Clock accuracy

NTP sync accuracy on a local network is typically ±10–50 ms. At `RAINBOW_DELAY = 20 ms` and `SIREN_HALF_MS = 350 ms`, animation sync across children will be visually tight. For Wipe effects where position matters, a 50 ms jitter = ~5 mm at 1 m/s wipe speed — acceptable.

---

## 10. Web UI — Tab Data Models

The SPA is served as a single HTML response. Each tab section is hidden/shown with CSS `display:none/block`. JavaScript manages all API calls.

### DASHBOARD tab

```
Data: GET /api/children (every 5 s)
      GET /api/runners  (once on load, updated on change)

Renders:
  - Table: name | IP | status badge | string count | last seen
  - Active runner card: name, current step thumbnail, elapsed/total time
  - [Stop] button  → POST /api/runners/stop
  - [Go]   button  → POST /api/runners/:id/start  (uses activeRunner from settings)
```

### SETUP tab

```
Data: GET /api/children
      GET /api/settings

─── Children ─────────────────────────────────────────────────────
Child table:
  Columns: hostname | name | description | IP | strings | status
  - [Add]     → modal: enter IP → POST /api/children
               (parent pings the IP; name/config auto-populate from CMD_PONG)
  - [Remove]  → DELETE /api/children/:id
  - [Details] → modal: shows all string configs (ledCount, length,
                ledType, cableDir, cableMm, stripDir per string)
               + link to child config page: http://<ip>/config
  - [Refresh] → POST /api/children/:id/refresh (re-fetches via CMD_PING)

─── Import / Export ──────────────────────────────────────────────
Checkboxes beside each child row enable per-child selection.
  - [Select All] / [Select None] toggles
  - [Export Selected] → GET /api/children/export?ids=0,2,5
                        browser downloads as slyled_children.json
  - [Import] → file picker → reads JSON → POST /api/children/import
               response shows: "Added 2, updated 1, skipped 0"

─── Settings ─────────────────────────────────────────────────────
  - Units toggle (metric / imperial)
  - Dark mode toggle
  - Canvas size inputs (width × height)
  - Parent name
  → POST /api/settings on change

[View Log] link → GET /log (existing page)
```

### LAYOUT tab

```
Data: GET /api/layout
      GET /api/children

Canvas element (HTML5 <canvas>, served inline):
  - Renders children as labelled nodes at (xMm, yMm)
  - Renders each string as a line segment from the node
  - Mouse drag repositions a child → staged; [Save Layout] commits
  → POST /api/layout

Unit display: metric (mm/cm/m) or imperial (in/ft) based on settings
```

### ACTIONS tab

```
Actions are not stored as named library items — they are defined inline
within Runner steps. This tab is a reference/preview panel only.

Displays:
  - Description of each action type (Solid, Flash, Wipe, Off) with parameter docs
  - Live preview: pick a child from dropdown, send CMD_ACTION immediately
    → POST /api/children/:id/action (temporary action, no runner needed)
```

### RUNTIME tab

```
Data: GET /api/runners

Runner list:
  - [New Runner] → name input → POST /api/runners
  - Each runner: name | step count | computed? | [Edit] [Compute] [Sync] [Delete]

Runner editor (inline, visible when editing):
  Step table: # | Action | Colour | Params | Area | Duration | [↑][↓][✕]
  [+ Add Step] → appends row with defaults (Solid White, All, 5 s)
  [Save] → PUT /api/runners/:id

  Area-of-effect picker:
    - Visual mini-canvas showing area rectangle
    - x0%, y0%, x1%, y1% inputs with "All" shortcut

  [Compute]  → POST /api/runners/:id/compute  (prerequisite: layout saved)
  [Sync]     → POST /api/runners/:id/sync     (loads steps to children via UDP)
  [Activate] → sets activeRunner in settings, enables [Go] on Dashboard
```

---

## 11. Memory Budget

### Giga (parent)

| Resource | Phase 1 | Phase 2 additions | Projected |
|----------|---------|-------------------|-----------|
| Flash (2 MB) | 277 KB (14%) | +~80 KB (UDP, API routes, SPA tabs) | ~357 KB (18%) |
| SRAM (524 KB) | 63 KB (12%) | +6 KB (data structures) +~4 KB (UDP buffers, HTTP parse) | ~73 KB (14%) |

Flash headroom is large. SRAM is comfortable.

### ESP32 (child)

| Resource | Phase 1 | Phase 2 additions | Projected |
|----------|---------|-------------------|-----------|
| Flash (1,280 KB) | 1,006 KB (78%) | +~35 KB (UDP, EEPROM, runner, config page HTML) | ~1,041 KB (81%) |
| SRAM (320 KB) | 50 KB (15%) | +~1.5 KB (runner buffer + ChildSelfConfig + udpBuf) | ~52 KB (16%) |

**Flash is the critical constraint on ESP32.** At 81% projected, adding features later will be tight. Mitigations:
- Use `F()` macro on all new string literals including config page HTML
- The child does **not** serve the parent SPA tabs — config page is the only new HTML
- Giga-specific `#ifdef BOARD_GIGA` code is already excluded by preprocessor — no Flash cost
- Compile-check Flash after each Phase 2 sub-step before proceeding

### D1 Mini (child)

| Resource | Phase 1 | Phase 2 additions | Projected |
|----------|---------|-------------------|-----------|
| Flash / IROM (1,024 KB) | 268 KB (26%) | +~35 KB | ~303 KB (30%) |
| IRAM (30 KB) | 27 KB (91%) | +~0.5 KB (child runner, no new ISR code) | ~27.5 KB (92%) |
| RAM (80 KB) | 35 KB (44%) | +~1.5 KB | ~37 KB (46%) |

**IRAM is the critical constraint on D1 Mini.** FastLED's clockless driver already occupies most of it. Phase 2 adds no new ISR code so IRAM impact is minimal. Monitor carefully after each sub-step.

### UDP buffer sizes

```cpp
// One buffer shared for receive and transmit (never concurrent)
uint8_t udpBuf[128];  // 128 bytes — covers all defined packets (largest: PongPacket = 100 bytes)
```

---

## 12. Implementation Roadmap

### Phase 2a — Communication foundation

1. Add `WiFiUDP udp` and `udp.begin(4210)` on all boards (already included in Phase 1 code)
2. Implement `CMD_PING` / `CMD_PONG` on all boards
3. Implement `CMD_STATUS_REQ` / `CMD_STATUS_RESP` on children
4. Add child registry (`children[]` array) to parent
5. Add `GET /api/children` and `POST /api/children` routes to parent
6. **Test**: parent discovers children, table appears in DASHBOARD

### Phase 2b — Layout

1. Add `GET /api/layout` and `POST /api/layout` to parent
2. Build LAYOUT tab canvas (HTML5 `<canvas>` + mouse drag)
3. Add SETUP tab child management UI
4. **Test**: drag children around, save layout, refresh — positions persist

### Phase 2c — Immediate actions

1. Implement `CMD_ACTION` and `CMD_ACTION_STOP` on children
2. Add `POST /api/children/:id/action` route to parent
3. Build ACTIONS tab preview UI
4. **Test**: send Solid/Flash/Wipe to a single child, verify LED response

### Phase 2d — Runner basics

1. Add `runners[]` array to parent
2. Implement runner CRUD routes (`/api/runners`)
3. Build RUNTIME tab editor (steps table, area picker)
4. **Test**: create a 3-step runner, edit, delete

### Phase 2e — Pre-computation

1. Implement the pre-computation algorithm (§8)
2. Add `POST /api/runners/:id/compute` route
3. Display computed LED ranges in the runner editor for verification
4. **Test**: compute a runner with known canvas layout, verify LED ranges

### Phase 2f — Execution

1. Implement `CMD_LOAD_STEP` / `CMD_LOAD_ACK` on all boards
2. Implement `CMD_RUNNER_GO` / `CMD_RUNNER_STOP` on all boards
3. Implement `applyStep()` and `applyLocalPattern()` on children
4. Add sync and start routes on parent
5. **Test**: 2-step Solid runner across two children — same colour at the same time

### Phase 2g — Polish

1. Wipe action with per-child leading edge calculation
2. Flash action sub-second timing
3. DASHBOARD runner thumbnail and progress display
4. Dark mode CSS
5. Imperial/metric unit display conversion in LAYOUT tab
6. Full test suite extension (`test_web.py` + manual sync tests)

### Phase 2h — Child config webpage + EEPROM

This can run in parallel with Phase 2a–2g (child-side only, no parent changes needed until Phase 2b).

1. Add EEPROM read/write (`Preferences` on ESP32, `EEPROM.h` on D1 Mini) with magic byte check and defaults
2. Generate hostname from MAC in `setup()`, set via `WiFi.setHostname()` / `WiFi.hostname()` before `WiFi.begin()`
3. Implement `GET /config` — serve the config form HTML
4. Implement `POST /config` — parse URL-encoded body, update RAM + EEPROM, redirect to `GET /config`
5. Announce updated config via CMD_PONG to parent after each save
6. **Test**: configure a child via browser, power-cycle, verify settings persist; verify parent receives updated config in CMD_PONG
