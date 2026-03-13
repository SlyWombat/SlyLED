# SlyLED HTTP API Reference

Base URL: `http://<board-ip>/` or `http://slyled/`

The board registers hostname `slyled` via DHCP option 12. Most routers will resolve `http://slyled/` on the local network. If not, check your router's DHCP lease table for the assigned IP.

All JSON responses use `Content-Type: application/json` and include a `Content-Length` header so HTTP clients can read the exact byte count without waiting for the connection to close.
All HTML responses include `Cache-Control: no-cache, no-store`.

---

## GET /

Returns the full Single Page Application.

**Response:** `200 OK`, `text/html`

The page auto-polls `/status` every 2 seconds and updates badges and the header status line without reloading.

---

## GET /status

Returns the current state of all modules.

**Response:** `200 OK`, `application/json`

```json
{
  "onboard_led": {
    "active": true,
    "feature": "rainbow"
  }
}
```

| Field | Type | Values |
|-------|------|--------|
| `onboard_led.active` | bool | `true` if any pattern is running |
| `onboard_led.feature` | string | `"rainbow"` \| `"siren"` \| `"none"` |

---

## POST /led/on

Enable the **Rainbow** pattern. Disables Siren if active.

**Request body:** empty
**Response:** `200 OK`, `application/json`, `Content-Length: 11`

```json
{"ok":true}
```

---

## POST /led/siren/on

Enable the **Siren** pattern (alternating red/blue). Disables Rainbow if active.

**Request body:** empty
**Response:** `200 OK`, `application/json`, `Content-Length: 11`

```json
{"ok":true}
```

---

## POST /led/off

Disable all LED patterns.

**Request body:** empty
**Response:** `200 OK`, `application/json`, `Content-Length: 11`

```json
{"ok":true}
```

---

## GET /log

Returns the event log as an HTML page.

**Response:** `200 OK`, `text/html`

Table columns: `#`, `Timestamp`, `Feature`, `State`, `Source`, `IP`

| Column | Description |
|--------|-------------|
| # | Entry number (1 = oldest visible) |
| Timestamp | UTC datetime (NTP-synced) or `T+Xs` uptime if NTP unavailable |
| Feature | `Rainbow`, `Siren`, or `-` (for off events) |
| State | `ON` or `OFF` |
| Source | `Boot` (initial state at power-on) or `Web` (triggered via HTTP) |
| IP | Client IP address, or `-` for boot entries |

Entries are displayed newest-first. The buffer holds the last 50 entries.

---

## GET /favicon.ico

Returns `404 Not Found` immediately. This prevents the browser's automatic favicon fetch from occupying the single `WiFiServer` connection slot during page loads.

---

## Error handling

Unknown routes fall through to serving the main SPA page (`GET /`). If a request arrives before the WiFi client sends data, the server waits up to 500 ms (Giga, ESP32) or 100 ms (D1 Mini) before reading the request line.

The ESP8266 (D1 Mini) may send a TCP RST instead of a graceful FIN when closing connections. All JSON responses include `Content-Length` so clients do not need to wait for connection close — read exactly `Content-Length` bytes and the response is complete.

---

## Example: curl

```bash
# Check status
curl http://slyled/status

# Enable rainbow
curl -X POST http://slyled/led/on

# Enable siren
curl -X POST http://slyled/led/siren/on

# Disable all
curl -X POST http://slyled/led/off
```

## Example: Python

```python
import urllib.request, json

BASE = "http://slyled"

def get_status():
    with urllib.request.urlopen(BASE + "/status") as r:
        return json.loads(r.read())

def set_rainbow():
    urllib.request.urlopen(urllib.request.Request(BASE + "/led/on", method="POST"))

def set_off():
    urllib.request.urlopen(urllib.request.Request(BASE + "/led/off", method="POST"))
```
