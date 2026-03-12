# SlyLED HTTP API Reference

Base URL: `http://<board-ip>/`
Board hostname: `slyled` (appears in DHCP leases as `slyled`)
Default IP: `192.168.10.219` (assigned by DHCP; check your router if different)

All JSON responses use `Content-Type: application/json`.
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
**Response:** `200 OK`, `application/json`

```json
{"ok": true}
```

---

## POST /led/siren/on

Enable the **Siren** pattern (alternating red/blue). Disables Rainbow if active.

**Request body:** empty
**Response:** `200 OK`, `application/json`

```json
{"ok": true}
```

---

## POST /led/off

Disable all onboard LED patterns.

**Request body:** empty
**Response:** `200 OK`, `application/json`

```json
{"ok": true}
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

Returns `404 Not Found` immediately. This prevents the browser's automatic favicon fetch from occupying the single WiFiServer connection slot during page loads.

---

## Error handling

The board does not return structured error responses. Unknown routes fall through to serving the main SPA page (`GET /`). If a request arrives before the WiFi client sends data, the server waits up to 500 ms before reading the request line.

---

## Example: curl

```bash
# Check status
curl http://192.168.10.219/status

# Enable rainbow
curl -X POST http://192.168.10.219/led/on

# Enable siren
curl -X POST http://192.168.10.219/led/siren/on

# Disable all
curl -X POST http://192.168.10.219/led/off
```
