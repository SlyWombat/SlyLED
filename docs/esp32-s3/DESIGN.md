# Design System Documentation: Stage Lighting & Technical Control

## 1. Overview & Creative North Star: "The Neon Precision"
This design system is engineered for the high-stakes, low-light environment of professional stage management. The Creative North Star is **The Neon Precision**—an aesthetic that marries the technical rigor of a cockpit with the high-contrast vibrancy of a live performance. 

Unlike standard watch interfaces that rely on generic lists and boxes, this system utilizes the circular canvas to create a radial command center. We break the "template" look through **Intentional Asymmetry**—placing critical controls on organic arcs and using overlapping "lens" effects to represent light beams. The goal is to move beyond a simple app and create a professional instrument that feels like part of the lighting rig itself.

---

## 2. Colors: High-Voltage Contrast
The color palette is anchored in `#0e0e0e` (Background) to ensure the hardware bezel of the watch disappears into the UI, leaving only the "light" visible.

### The "No-Line" Rule
**Strict Mandate:** 1px solid borders are prohibited for sectioning. Boundaries must be defined through background color shifts. To separate a light group from a master fader, place a `surface-container-low` (`#131313`) element against the `surface` (`#0e0e0e`) background. 

### Surface Hierarchy & Nesting
Treat the circular screen as a series of nested physical lenses.
*   **Base:** `surface` (#0e0e0e) for the global background.
*   **Secondary Controls:** `surface-container` (#191919) for inactive modules.
*   **Active Focus:** `surface-container-highest` (#262626) for the currently selected lighting fixture or active zone.

### The "Glass & Gradient" Rule
To avoid a flat "flat-design" look, CTAs and active states should utilize subtle gradients. Transitioning from `primary` (#8ff5ff) to `primary-container` (#00eefc) creates a "glow" effect reminiscent of an LED indicator. Use **Glassmorphism** (semi-transparent surface colors with 20px backdrop-blur) for overlays that slide in from the edges, ensuring the stage status remains partially visible beneath the controls.

---

## 3. Typography: Technical Legibility
We use two distinct typefaces to balance character with utility.

*   **Display & Headlines (Space Grotesk):** This is our "Technical" voice. The wide apertures and geometric forms of Space Grotesk ensure that at `headline-lg` (2rem), a light intensity percentage is readable at a glance from a distance.
*   **Body & Titles (Inter):** Our "Functional" voice. Inter provides maximum legibility for complex technical labels at `body-sm` (0.75rem).

**Hierarchy Strategy:**
*   **Urgency:** Use `display-md` in `secondary` (#ff7350) for critical heat or battery warnings.
*   **Control Labels:** Use `label-md` in `on-surface-variant` (#ababab) for non-interactive descriptions, ensuring they don't distract from the primary data.

---

## 4. Elevation & Depth: Tonal Layering
On a circular watch face, traditional drop shadows often feel cluttered. Instead, we use **Tonal Layering**.

*   **The Layering Principle:** Achieve depth by stacking. A `surface-container-lowest` card placed on a `surface-container-low` section creates a recessed "well" effect, perfect for grouping secondary toggle switches.
*   **Ambient Shadows:** If a floating action button (FAB) is required, use an extra-diffused shadow (24px blur) at 8% opacity, tinted with `primary` (#8ff5ff) to mimic light spilling onto the interface.
*   **The "Ghost Border" Fallback:** For high-density data where tonal shifts aren't enough, use a **Ghost Border**. Apply the `outline-variant` (#484848) at 15% opacity. Never use 100% opaque lines.

---

## 5. Components: The Instrument Set

### Precision Faders (Custom)
The primary interaction for stage lighting. Use an arc-based slider following the curve of the watch face. The "track" uses `surface-variant`, while the "thumb" or "active fill" uses a gradient of `primary` to `primary-dim`.

### Buttons
*   **Primary (Action):** Full rounded (`full`: 9999px). Background: `primary`. Text: `on-primary`. Used for "Blackout" or "Flash."
*   **Secondary (State):** `md` (1.5rem) roundedness. Background: `surface-container-high`. Used for switching between DMX universes.

### Chips
*   **Status Chips:** Use `tertiary` (#deffac) with `on-tertiary` text for "Live" indicators.
*   **Selection Chips:** Use `secondary_container` with `sm` (0.5rem) corners to indicate selected fixtures.

### Cards & Lists
**Constraint:** Absolute prohibition of divider lines. Separate list items using `0.5rem` of vertical space or by alternating between `surface-container-low` and `surface-container-lowest` backgrounds. This creates a "ribbon" effect that is much cleaner on small screens.

### Radial Gauges
For stage temperature or power consumption. Use `tertiary` for safe ranges and `error` (#ff716c) for critical thresholds. The gauge background should always be `surface-container-highest` to maintain the "recessed" look.

---

## 6. Do's and Don'ts

### Do
*   **Do** utilize the `full` roundedness for buttons to make them tactile and easy to hit with a thumb.
*   **Do** use `secondary` (#ff7350) for "Warm" light controls and `primary` (#8ff5ff) for "Cool" light controls to provide an intuitive mental model.
*   **Do** leverage `display-lg` for the single most important number on the screen (e.g., Global Dimmer %).

### Don't
*   **Don't** use pure white for text unless it is a primary heading. Use `on-surface-variant` (#ababab) for secondary info to reduce eye strain in dark theaters.
*   **Don't** use standard "Material Design" shadows. They look muddy on deep black backgrounds. Use tonal shifts instead.
*   **Don't** use more than three colors on a single screen. This system relies on high-contrast "pops" against the black; too many colors will result in visual noise during a live show.