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
{"role": "parent", "hostname": "WIN-HOSTNAME"}
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

Add a child by IP (parent sends CMD_PING and waits for CMD_PONG).

**Request body:** `{"ip": "192.168.1.100"}`

**Response:** `200 OK`

```json
{"ok": true}
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

**Response:** `200 OK`

```json
{
  "canvasW": 10000,
  "canvasH": 5000,
  "children": [
    {"id": 0, "xMm": 1000, "yMm": 500}
  ]
}
```

---

### POST /api/layout

Save canvas positions for all children.

**Request body:** same schema as GET response

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
| GET | `/` | Config form (same as `/config`) |
| GET | `/status` | `{"role":"child","hostname":"SLYC-XXXX","action":N}` |
| GET | `/config` | Self-config HTML form |
| POST | `/config` | Save config to EEPROM, broadcast CMD_PONG |

`POST /config` form fields: `an` (altName), `de` (description), `sc` (stringCount), `s0`–`s3` (active string index), per-string: `lc` (ledCount), `ll` (lengthMm), `lt` (ledType), `cd` (cableDir), `cm` (cableMm), `sd` (stripDir).

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
