# SlyLED Design Standards & Technical Specs

## 1. Visual Language

### Cyber-Industrial Aesthetic
The interface uses a "dark-ops" aesthetic with high contrast in critical control areas and subtle depth through translucency and glows. Information density is high but clearly stratified.

### Typography
- **Headlines / Accents:** Space Grotesk (bold, uppercase, wide letter-spacing)
- **Body / Data / Controls:** Inter (regular/medium)

### Design Principles
- Dark-first: all surfaces use deep navy/slate backgrounds
- Glow accents: interactive elements emit colored glow on hover/active
- High density: tables and data views are compact with clear hierarchy
- Scanline aesthetic: subtle grid patterns on canvas/timeline backgrounds

## 2. Design Tokens

### Color Palette

| Role | Name | Hex | Usage |
|------|------|-----|-------|
| Primary | Blue | `#3b82f6` | Primary actions, active tabs, links |
| Secondary | Cyan | `#22d3ee` | Highlights, active states, accents |
| Tertiary | Orange | `#f59e0b` | WLED devices, warnings, tertiary actions |
| Error | Red | `#ef4444` | Errors, destructive actions, offline |
| Success | Green | `#22c55e` | Online status, success states |
| Surface | Dark Navy | `#020617` | Page background |
| Surface High | Slate | `#0f172a` | Cards, elevated surfaces |
| Surface Container | Dark Slate | `#0b1120` | Side panels, containers |
| Outline | Muted Slate | `#64748b` | Borders, inactive text |
| Outline Variant | Dim Slate | `#334155` | Subtle borders, dividers |
| On Surface | Near White | `#f1f5f9` | Primary text |
| On Surface Variant | Light Slate | `#94a3b8` | Secondary text, labels |

### Light Mode Overrides

| Token | Light Value |
|-------|-------------|
| Surface | `#f8fafc` |
| Surface High | `#ffffff` |
| On Surface | `#0f172a` |
| On Surface Variant | `#475569` |
| Outline | `#94a3b8` |
| Outline Variant | `#e2e8f0` |

### Spacing Scale
- `xs`: 0.25rem (4px)
- `sm`: 0.5rem (8px)
- `md`: 1rem (16px)
- `lg`: 1.5rem (24px)
- `xl`: 2rem (32px)

### Border Radius
- Default: 4px
- Cards: 6px
- Buttons: 5px
- Badges: 12px (pill shape)
- Full: 9999px (circles)

## 3. Shared Components

### Navigation
- **Top bar:** Fixed, height 48px, app name left, status right
- **Tab bar:** Horizontal tabs below top bar, uppercase labels, active indicator underline with glow
- **Mobile:** Bottom navigation bar (hidden on desktop)

### Buttons

| Variant | Background | Text | Border |
|---------|-----------|------|--------|
| Primary | `#3b82f6` | white | none |
| Secondary | transparent | `#22d3ee` | `#22d3ee` 30% |
| Danger | `#7f1d1d` | `#fca5a5` | none |
| Ghost | transparent | `#94a3b8` | `#334155` |
| Success | `#14532d` | `#86efac` | none |

### Cards
- Background: `#0f172a`
- Border: 1px solid `#334155` at 30% opacity
- Padding: 1rem
- Title: uppercase, letter-spacing 0.1em, font-weight 600

### Badges (Status Pills)
- Online: background `#14532d`, text `#86efac`
- Offline: background `#1e1e1e`, text `#888`
- WLED: background `#f59e0b`, text white
- SlyLED: background `#3b82f6` at 20%, text `#93c5fd`
- Checking: background `#334155`, text `#94a3b8`

### Tables
- Header: uppercase, letter-spacing 0.1em, `#94a3b8` text
- Rows: alternating subtle background (`#0f172a` / transparent)
- Compact padding: 0.4em vertical

### Canvas / Timeline
- Background: `#0d0d0d` with grid lines at `#1e1e1e`
- Playhead: white 2px line with triangle marker
- Active blocks: 90% opacity with white border glow
- Inactive blocks: 25% opacity with dim border
- Time axis: `#888` text, `#222` grid lines

## 4. System Architecture

### Three-Tier Communication
```
Orchestrator (Desktop Flask / Giga R1)
    |  UDP port 4210 binary protocol v3
    |  HTTP JSON API for WLED devices
    v
Performers (SlyLED: ESP32/D1 Mini/Giga Child | WLED devices)
```

### Protocol Layers
- **UDP Binary (port 4210):** PING/PONG, ACTION, LOAD_STEP, RUNNER_GO/STOP, ACTION_EVENT, STATUS
- **HTTP JSON:** WLED device control via `/json/state`, device info via `/json/info`
- **WebSocket (future):** Real-time pixel streaming for advanced effects

### Discovery
- **SlyLED devices:** UDP broadcast PING on port 4210, persistent listener collects PONGs
- **WLED devices:** HTTP probe of `/json/info` on manual IP add
- **Future:** mDNS/Zeroconf browsing for `_wled._tcp` and `_slyled._tcp`

### Sync-Clock
- NTP-synced epoch timestamps in RUNNER_GO packets
- Children start execution at the specified epoch for frame-perfect sync
- ACTION_EVENT packets pushed by children on step transitions (no polling)

## 5. SPA Tab Structure

| Tab | Purpose | Key Components |
|-----|---------|---------------|
| Dashboard | System overview | Performer table, live runner timeline canvas |
| Setup | Device management | Add/discover/remove/reboot performers, export/import |
| Layout | Spatial mapping | Drag-and-drop canvas, string visualization, live preview |
| Actions | Effect library | Create/edit 14 action types with parameter editors |
| Runtime | Show control | Runner list, sync/start/stop, timeline preview |
| Settings | App config | Name, units, canvas size, dark mode, logging |
| Firmware | OTA updates | Port detection, version query, flash management |
