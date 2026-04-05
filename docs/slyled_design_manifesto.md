# SlyLED: Design \& Layout Manifesto (v1.0)

## 1\. Creative Philosophy: The Kinetic Prism

The "Kinetic Prism" is our core design language. It treats the interface not as a flat surface, but as a dynamic, light-refracting medium. It bridges the gap between the physical vibrancy of addressable LEDs and the digital control interface.

* **Immersive Minimalism:** Stripping away unnecessary chrome to let the light and color be the hero.
* **Visual Feedback:** Every interaction should feel like it's triggering a pulse of light.
* **Glassmorphism 2.0:** Utilizing high-blur, translucent layers to create depth without clutter.

\---

## 2\. Global Design Tokens

### 2.1 Color Palette

* **Core Dark:** `#0A0F13` (Deep Slate/OLED Black) - Provides the infinite-depth background.
* **Primary Accent:** `#0969DA` (Lumina Blue) - Used for primary actions and "active" states.
* **Neon Accents:** Used sparingly for state indicators (e.g., `#ABD600` for "Connected").

### 2.2 Typography

* **Headlines:** `Space Grotesk` - A high-tech, geometric sans-serif that feels engineered yet approachable.
* **Body \& Data:** `Inter` - Optimized for readability, especially for high-density telemetry data.

\---

## 3\. Screen-Specific Layout Strategies

### 3.1 Mobile Experience (The "Thumb Zone" First)

* **Home: Device Discovery:** Uses large, tactile cards. The power toggle is the primary interaction point, positioned for easy thumb reach.
* **Control: Color \& Effects:** Centered around a "Hero Dial." Sliders are vertical and high-intensity, providing a physical sense of "pushing" the light.
* **Segments: Visual Mapping:** A horizontal timeline layout that represents the physical LED strip linearly, making spatial configuration intuitive on a small screen.

### 3.2 Web Dashboard (Spatial Density)

* **Persistent Navigation:** A glassmorphic sidebar keeps global context accessible while maximizing the central workspace.
* **Stage Layout (Canvas):** A grid-based workspace for spatial mapping, allowing for complex drag-and-drop node placement.
* **Runtime View (High-Density):** Combines real-time telemetry with a node-by-node mesh map. Uses "Micro-Glow" indicators to show node status at a glance without text-heavy labels.

\---

## 4\. Interaction Principles

1. **The Bloom Effect:** On-hover or on-active, elements should emit a soft outer glow in their accent color.
2. **Kinetic Transitions:** Screens should transition using subtle "slide and fade" animations, mimicking the way light travels across a mesh network.
3. **Glass Interaction:** Buttons should have a "pressed" state that increases background opacity and blur, creating a tactile "click" sensation.

\---

*This document serves as the visual and structural blueprint for all current and future SlyLED interfaces.*

