# SlyLED API Reference

## Windows Parent (`http://localhost:8080/`)

All JSON endpoints return `Content-Type: application/json` and a `Content-Length` header.
All HTML responses include `Cache-Control: no-cache, no-store, must-revalidate`.

---

### GET /

Returns the 6-tab Single Page Application.

**Response:** `200 OK`, `text/html`

Unknown URL paths also return the SPA (client-side routing).

---

### GET /status

**Response:** `200 OK`, `application/json`

```json
{"role": "parent", "hostname": "WIN-HOSTNAME", "version": "3.6"}
```

---

### GET /favicon.ico

**Response:** `404 Not Found`

---

## Children API

### GET /api/children

List all known children.

**Response:** `200 OK`, array of child objects.

```json
[
  {
    "id": 0,
    "ip": "192.168.1.100",
    "hostname": "SLYC-A1B2",
    "altName": "Front strip",
    "desc": "Living room",
    "xMm": 1000, "yMm": 500, "zMm": 0,
    "stringCount": 2,
    "strings": [
      {"ledCount": 60, "lengthMm": 1000, "ledType": 0,
       "cableDir": 0, "cableMm": 200, "stripDir": 0}
    ]
  }
]
```

---

### POST /api/children

Add a child by IP (parent sends CMD_PING and waits for CMD_PONG). The ping runs outside the request lock so concurrent requests are not blocked.

**Request body:** `{"ip": "192.168.1.100"}`

**Response:** `200 OK`

```json
{"ok": true, "id": 0}
```

---

### DELETE /api/children/:id

Remove a child from the registry.

**Response:** `200 OK`

```json
{"ok": true}
```

Error if `id` not found: `{"ok": false, "err": "not found"}`

---

### GET /api/children/:id/status

Send `CMD_STATUS_REQ` to the child and wait up to 300 ms for `CMD_STATUS_RESP`.

**Response:** `200 OK`

```json
{"ok": true, "action": 0, "runner": false, "step": 0, "rssi": -65, "uptime": 3721}
```

On timeout: `{"ok": false, "err": "timeout"}`

On bad id: `{"ok": false, "err": "not found"}`

---

### POST /api/children/:id/refresh

Re-send CMD_PING to a specific child IP to refresh its PONG data.

**Response:** `200 OK`, `{"ok": true}`

---

### GET /api/children/discover

Broadcast a `CMD_PING` to `255.255.255.255:4210` and collect `CMD_PONG` responses for 1.5 seconds. Returns children found on the network that are **not** already in the known-children table (filtered by IP address).

**Response:** `200 OK`, array of child objects (same schema as `GET /api/children`, no `id` field — children are not added automatically).

```json
[
  {
    "ip": "192.168.1.105",
    "hostname": "SLYC-C3D4",
    "name": "SLYC-C3D4",
    "desc": "",
    "sc": 2,
    "strings": [...],
    "status": 1,
    "seen": 1710000000
  }
]
```

Returns an empty array if no new children respond within 1.5 seconds.

---

### GET /api/children/export

Export all children as a JSON array (for backup / transfer).

**Response:** `200 OK`, array (same schema as GET /api/children)

---

### POST /api/children/import

Import a JSON array of children; deduplicates by hostname (existing entry is updated, not duplicated).

**Request body:** array of child objects (only `ip` is required; other fields optional)

**Response:** `200 OK`

```json
{"ok": true, "added": 2, "updated": 1}
```

---

## Layout API

### GET /api/layout

Returns canvas dimensions and the position of every registered child. Children without a saved position default to `x=0, y=0` with `positioned=false`.

**Response:** `200 OK`

```json
{
  "canvasW": 10000,
  "canvasH": 5000,
  "children": [
    {
      "id": 0, "hostname": "SLYC-A1B2", "name": "Front strip",
      "x": 2000, "y": 1500,
      "positioned": true
    },
    {
      "id": 1, "hostname": "SLYC-C3D4", "name": "SLYC-C3D4",
      "x": 0, "y": 0,
      "positioned": false
    }
  ]
}
```

`positioned: false` means no layout position has been explicitly saved for that child yet. The SPA shows unpositioned children in a sidebar list; drag them onto the canvas to place them. Each child object also includes full `strings` data (LED count, length, direction) which the canvas uses to render detailed string visualizations.

---

### POST /api/layout

Save canvas positions for all children. Only `id`, `x`, and `y` are read from each element; other fields are ignored.

**Request body:**

```json
{
  "children": [
    {"id": 0, "x": 2000, "y": 1500},
    {"id": 1, "x": 7000, "y": 3000}
  ]
}
```

**Response:** `200 OK`, `{"ok": true}`

---

## Settings API

### GET /api/settings

**Response:** `200 OK`

```json
{
  "name": "SlyLED",
  "units": 0,
  "canvasW": 10000,
  "canvasH": 5000,
  "darkMode": true,
  "runnerRunning": false,
  "activeRunner": -1
}
```

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Parent display name |
| `units` | int | `0` = metric (mm), `1` = imperial (inches) |
| `canvasW` / `canvasH` | int | Canvas dimensions in mm |
| `darkMode` | bool | UI dark mode |
| `runnerRunning` | bool | Whether a runner is currently executing |
| `activeRunner` | int | ID of the active runner, or `-1` |

---

### POST /api/settings

Save settings (partial updates accepted — omitted fields are unchanged).

**Request body:** any subset of the GET response fields

**Response:** `200 OK`, `{"ok": true}`

---

## Runners API

### GET /api/runners

List all runners.

**Response:** `200 OK`, array of runner objects.

```json
[
  {"id": 0, "name": "Sunrise", "computed": true, "steps": [...]}
]
```

---

### POST /api/runners

Create a new runner. Maximum 4 runners.

**Request body:** `{"name": "My Runner"}`

**Response:** `200 OK`

```json
{"ok": true, "id": 0}
```

Error if at capacity: `{"ok": false, "err": "max runners reached"}` (HTTP 400)

---

### GET /api/runners/:id

Get a single runner by ID.

**Response:** `200 OK`, runner object. Error if not found: `{"ok": false, "err": "not found"}`

---

### PUT /api/runners/:id

Replace a runner's steps. Resets `computed` to `false`.

**Request body:**

```json
{
  "name": "Sunrise",
  "steps": [
    {
      "action": {"type": 1, "r": 255, "g": 128, "b": 0, "onMs": 0, "offMs": 0, "wDir": 0, "wSpd": 10},
      "area": {"x1": 0, "y1": 0, "x2": 5000, "y2": 5000},
      "durationS": 5
    }
  ]
}
```

**Response:** `200 OK`, `{"ok": true, "stepCount": 1}`

---

### DELETE /api/runners/:id

Delete a runner.

**Response:** `200 OK`, `{"ok": true}`. Error if not found.

---

### POST /api/runners/:id/compute

Pre-compute LED ranges for every step × child × string based on current canvas layout. Sets `computed = true`.

**Response:** `200 OK`, `{"ok": true}`

---

### POST /api/runners/:id/sync

Send all pre-computed steps to all children via `CMD_LOAD_STEP` and wait for `CMD_LOAD_ACK`.

**Response:** `200 OK`, `{"ok": true}`

---

### POST /api/runners/:id/start

Broadcast `CMD_RUNNER_GO` with `epoch + 2` seconds so all children start simultaneously.

**Response:** `200 OK`, `{"ok": true}`

---

### POST /api/runners/stop

Broadcast `CMD_RUNNER_STOP` to all children. Updates `runnerRunning = false`.

**Response:** `200 OK`, `{"ok": true}`

---

## Factory Reset

### POST /api/reset

Clear all children, runners, and layout data, and restore default settings. This is equivalent to a full wipe — the operation is **not reversible**.

**Request body:** `{}` (empty)

**Response:** `200 OK`

```json
{"ok": true}
```

Default settings restored: `name="SlyLED"`, `units=0`, `canvasW=10000`, `canvasH=5000`, `darkMode=1`, `runnerRunning=false`, `activeRunner=-1`.

---

## Action API

### POST /api/action

Send an immediate action to one child or all children.

**Request body:**

```json
{
  "target": "all",
  "type": 1,
  "r": 255, "g": 0, "b": 0,
  "onMs": 500, "offMs": 500,
  "wDir": 0, "wSpd": 20,
  "ledStart": [0, 0, 255, 255, 255, 255, 255, 255],
  "ledEnd":   [59, 255, 255, 255, 255, 255, 255, 255]
}
```

| Field | Description |
|-------|-------------|
| `target` | `"all"` or child ID as string (e.g. `"0"`) |
| `type` | `0`=Off, `1`=Solid, `2`=Flash, `3`=Wipe |
| `r/g/b` | Colour (0–255) |
| `onMs/offMs` | Flash timing in milliseconds |
| `wDir` | Wipe direction: `0`=E, `1`=N, `2`=W, `3`=S |
| `wSpd` | Wipe speed (LEDs/s) |
| `ledStart/ledEnd` | 8-byte arrays: per-string start/end LED index; `0xFF` = string not included |

**Response:** `200 OK`, `{"ok": true}`

Error if specific target not found: `{"ok": false, "err": "target not found"}` (HTTP 404)

---

### POST /api/action/stop

Send `CMD_ACTION_STOP` to one child or all children.

**Request body:** `{"target": "all"}` (or specific child ID string)

**Response:** `200 OK`, `{"ok": true}`

Error if specific target not found: `{"ok": false, "err": "target not found"}` (HTTP 404)

---

## Child HTTP Routes

Children expose a minimal HTTP interface on **port 80**.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | 302 redirect → `/config` |
| GET | `/status` | `{"role":"child","hostname":"SLYC-XXXX","action":N}` |
| GET | `/config` | 3-tab self-config SPA (Dashboard / Settings / Config) |
| POST | `/config` | Save config to EEPROM, broadcast CMD_PONG, 303 redirect |
| POST | `/config/reset` | Factory reset to defaults, 303 redirect |
| GET | `/favicon.ico` | 404 |

`POST /config` form fields: `an` (altName), `desc` (description), `sc` (stringCount 1–`CHILD_MAX_STRINGS`); per-string: `lc` (ledCount), `lm` (lengthMm), `lt` (ledType), `sd` (stripDir).

---

## Example: curl

```bash
# List children
curl http://localhost:8080/api/children

# Send all LEDs solid red
curl -X POST http://localhost:8080/api/action \
  -H "Content-Type: application/json" \
  -d '{"target":"all","type":1,"r":255,"g":0,"b":0,"onMs":0,"offMs":0,"wDir":0,"wSpd":0,"ledStart":[0,255,255,255,255,255,255,255],"ledEnd":[255,255,255,255,255,255,255,255]}'

# Stop everything
curl -X POST http://localhost:8080/api/action/stop -H "Content-Type: application/json" -d '{"target":"all"}'
```
