# SlyLED Design Standards & Technical Specs (v2.0)

Based on the **Kinetic Prism** design manifesto — the interface is a dynamic, light-refracting medium that bridges physical LED vibrancy with digital control.

## 1. Creative Philosophy

- **Immersive Minimalism:** Strip away unnecessary chrome. Light and color are the hero.
- **Visual Feedback:** Every interaction triggers a pulse of light (bloom effect, glow states).
- **Glassmorphism 2.0:** High-blur translucent layers create depth without clutter.
- **Dark-First:** All surfaces use deep navy/slate backgrounds across all platforms.

## 2. Design Tokens

### 2.1 Color Palette

| Role | Name | Hex | Usage |
|------|------|-----|-------|
| Background | Deep Slate | `#0A0F13` | OLED-black infinite-depth background |
| Surface | Dark Navy | `#0f172a` | Cards, elevated surfaces |
| Surface High | Slate | `#1e293b` | Modals, panels |
| Primary | Lumina Blue | `#0969DA` | Primary actions, active states |
| Secondary | Cyan | `#22d3ee` | Highlights, active accents |
| Tertiary | Orange | `#f59e0b` | WLED, warnings |
| Error | Red | `#ef4444` | Errors, destructive, offline |
| Success | Green | `#22c55e` | Online, success states |
| DMX Purple | Violet | `#7c3aed` | DMX fixtures, profiles, community |
| Outline | Muted | `#64748b` | Borders, inactive text |
| On Surface | Near White | `#e2e8f0` | Primary text |
| On Surface Variant | Light Slate | `#94a3b8` | Secondary text, labels |

### 2.2 Typography

| Context | Font | Usage |
|---------|------|-------|
| Headlines | Space Grotesk | Bold, uppercase, wide letter-spacing. Engineered yet approachable. |
| Body & Data | Inter | Optimized for readability in high-density telemetry. |
| Monospace | System mono | Code, DMX values, JSON |

### 2.3 Spacing & Radius

- Spacing: 4px / 8px / 16px / 24px / 32px
- Border radius: Buttons 5px, Cards 6px, Badges 12px (pill)

## 3. Interaction Principles

### 3.1 The Bloom Effect
On hover/active, elements emit a soft outer glow in their accent color. CSS: `box-shadow: 0 0 12px rgba(accent, 0.3)`.

### 3.2 Kinetic Transitions
Screens transition with "slide and fade" animations mimicking light traveling across a mesh. Duration: 200-300ms, ease-out.

### 3.3 Glass Interaction
Buttons have a pressed state that increases background opacity and blur. Active modals use `backdrop-filter: blur(4px)`.

### 3.4 Micro-Glow Indicators
Status indicators use colored glow dots instead of text labels where space is limited. Online = green pulse, Offline = dim gray.

## 4. Platform-Specific Rules

### 4.1 Windows Desktop SPA (`desktop/shared/spa/index.html`)
- 7-tab horizontal navigation with active glow underline
- Canvas backgrounds: `#0d0d0d` with `#1e1e1e` grid lines
- Glassmorphic modal: `backdrop-filter: blur(4px)`, semi-transparent background
- Compact data tables with uppercase headers
- Logo in top bar or tab strip

### 4.2 Android App (`android/`)
- Material 3 Dark theme with custom colors matching the palette above
- Bottom navigation bar with 6 tabs (Compose `NavigationBar`)
- Cards use `surfaceContainerLow` matching `#0f172a`
- Compose Canvas for layout/emulator (matches SPA canvas colors)
- Logo on connection screen and dashboard header

### 4.3 Marketing Website (`electricrv.ca/slyled/`)
- Full-bleed dark background `#0f172a`
- Gradient text on hero (`linear-gradient(135deg, cyan, green)`)
- Feature cards with `#1e293b` background + `#334155` border
- Community stats fetched live from API
- Logo prominently displayed in header
- Responsive: mobile-first with `max-width: 960px` container

### 4.4 Firmware Config UI (ESP32/D1 Mini/Giga)
- Dark HTML served by device: `#0d0d0d` background
- Minimal CSS — no external fonts (device serves all assets)
- 3-tab structure: Dashboard, Settings, Config
- Form inputs: dark background, light text, rounded borders
- Status indicators: colored dots (green/gray/red)

## 5. Logo Usage

- **File**: `images/slyled.png` (PNG with transparency)
- **Icon**: `images/slyled.ico` (Windows icon)
- **Placement**: Top-left of SPA header, Android connection screen, marketing site hero
- **Minimum size**: 32px height
- **Background**: Always on dark backgrounds. Never on light.

## 6. Component Library

### Buttons
| Variant | Background | Text | Glow Color |
|---------|-----------|------|------------|
| Primary | `#3b82f6` | white | blue |
| Success | `#14532d` | `#86efac` | green |
| DMX/Community | `#7c3aed` | `#e9d5ff` | purple |
| Danger | `#7f1d1d` | `#fca5a5` | red |
| Ghost | transparent | `#94a3b8` | none |

### Badges
| State | Background | Text |
|-------|-----------|------|
| Online | `#14532d` | `#86efac` |
| Offline | `#1e1e1e` | `#888` |
| DMX Bridge | `#7c3aed` | `#e9d5ff` |
| ESP32 | `#2563eb` | `#93c5fd` |
| WLED | `#f59e0b` | white |

### Cards
- Background: `#0f172a`, border: 1px solid `#334155` at 30%
- Title: uppercase, letter-spacing 0.1em, weight 600
- Padding: 1rem. Radius: 6px.

## 7. Enforcement

All UI code (SPA, Android, firmware, marketing site) must follow these tokens and patterns. When adding new UI elements:
1. Use existing design tokens — never hardcode colors
2. Apply bloom/glow on interactive elements
3. Test on both dark and light modes (SPA)
4. Ensure mobile responsiveness (SPA + marketing)
5. Match fixture type colors: LED = green, DMX = purple, WLED = orange
