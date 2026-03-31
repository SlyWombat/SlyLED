# PROJECT SPECIFICATION: Upgrade to the layout and runtime actions to support 3D Volumetric Lighting & Compiled Sequence Engine


## 1. ROLE & OBJECTIVE
Act as a Senior Software Architect. Design and implement slyled having children that are abstracted, that might be lines of lights in different 3d dimensions or single  lights that have an area of effect.  The design should support 3d layout of a stage with placeable solid surfaces that can have effects pointed at, or if using strings of leds, they are point lights that cover the surface they are on. Effects and actions can be performed on a timeline, some effects may be only on a fixture or group of fixtures, some effects are across the stage in a direction, or parts of the stage. The software will have to precompute the show and organize actions and times that need to be preloaded to each fixture, a live stream to each fixture for led intense layouts will not be able to be supported and will continue to use the actions that are supported locally in WLED, but will be dynamically defined based on the stage layout. DMX fixtures will be able to be streamed (DMX specification to come in future phase)

## 3D Volumetric Lighting & Compiled Sequence Engine

### 1. Architectural Vision
SlyLED is a 3D-aware lighting design suite designed to handle high-density LED layouts by treating the stage as a spatial volume. The core innovation is a **Hybrid Playback System**:
* **Real-time Streamed (DMX/sACN/Art-Net/Android):** For low-channel count fixtures (Moving heads, Pars).
* **Pre-computed (Baked Binary of local actions using the known direction of the child and local wled actions):** For high-density LED strings/surfaces where per-pixel streaming exceeds network bandwidth.

---

### 2. Spatial Data Model

#### 2.1 Coordinate System
* **Global Space:** Right-Handed Y-Up (Meters).
* **Mapping:** Every LED pixel is assigned a fixed $(x, y, z)$ coordinate in Global Space loaded from the known children, but the ability to move around on the stage.

#### 2.2 Fixture Abstractions

A **fixture** is the primary stage entity. It wraps one or more children, DMX devices, or other fixtures as a logical group.

* **A child IS a fixture** — auto-created when hardware is registered. Inherits string config, LED counts, and strip direction from the child's PONG data.
* **Fixtures can override child attributes** — a string wired as "east" on the ESP32 can be placed vertically on the 3D stage by setting the fixture's rotation. The baking engine resolves pixel positions from the **fixture's transform** (position + rotation), not the child's raw config.
* **Fixture types:**
  * **Linear Fixture (LED Strings):** Defined by a 3D spline or rotation override. The system calculates pixel positions based on `pixel_pitch` or `total_count` along the curve, applying the fixture's transform.
  * **Point/Volumetric Fixture:** A source with a 3D position and an **Area of Effect (AoE)**. It can be a simple point or a conical beam. Suitable for DMX pars, spots, or single-LED children.
  * **Solid Surfaces (Canvases):** 3D meshes (OBJ/STL) placed in the scene. These act as "projection targets." Effects can be mapped to the surface, and any LED string "adhered" to the surface will sample colors from the mesh's UV coordinates.
  * **Group Fixture:** A named collection of child fixtures that can be targeted as a single entity by spatial effects and timeline clips.
* **DMX fixtures** (future phase): Streamed in real-time via Art-Net/sACN. Treated as point/volumetric fixtures with channel mappings instead of pixel positions.

---

### 3. Effect Engine Logic

#### 3.1 Local vs. Global Effects
1.  **Fixture-Local:** Mathematical patterns (Sine waves, Noise, Sawtooth) calculated based on the internal index $[0...N]$ of a fixture.
2.  **Global Spatial Fields:** 3D primitives (Spheres, Planes, Boxes) that move through the stage.
    * *Calculation:* For every frame, if $Pixel_{pos}$ is inside $Field_{volume}$, the pixel color is transformed by the field's properties.
3.  **Surface Projection:** Mapping a 2D video or procedural texture onto a 3D Surface.

---

### 4. The Compilation & Playback Pipeline

#### 4.1 The "Baking" Process
To support high-density layouts without live-streaming lag, the software must "Bake" the show:
1.  **Iterate:** Step through the timeline at a fixed frequency (e.g., 40Hz).
2.  **Flatten:** At each step, calculate the final RGB for every pixel by blending all active effects.
3.  **Pack:** Write these values into a binary `.LSQ` (Lumen Sequence) file per fixture.

#### 4.2 Distributed Playback
* **Pre-loading:** `.LSQ` files are pushed to hardware controllers (ESP32/FPGA) via SFTP/TCP before the show.
* **Triggering:** The software broadcasts an **OSC/UDP Sync Packet** containing a Master Timestamp. Fixtures play their local buffer in lock-step with this clock.

---

### 5. Deliverables (Phased Output)


#### **D1: Core Math & Geometry Library**
* **`SpatialResolver`**: Module to convert 3D Splines into discrete Pixel coordinates.
* **`Intersector`**: Logic for Sphere/Box/Plane collisions with Pixel coordinates.
* **`UVMapper`**: Logic to sample 3D mesh UVs for color data.

#### **D2: Timeline & Effect Controller**
* **`SequenceEngine`**: A track-based system capable of managing keyframes for Effect Fields.
* **`EffectLibrary`**: A collection of shaders/functions for both local (index-based) and global (space-based) effects.

#### **D3: I/O & Compiler**
* **`BinaryBaker`**: Multi-threaded module that exports per-fixture `.LSQ` files.
* **`SyncProvider`**: UDP broadcast module for Master Timecode synchronization.
* **`DMXBridge`**: Real-time Art-Net/sACN output driver.

#### **D4: Integration into SLYLED Orchestration Engine

---

### 6. Technical Stack Requirements
* **Language:** C++20 or Rust (for performance math).
* **Math:** GLM (OpenGL Mathematics).
* **3D Viewport:** OpenGL or Vulkan.
* **Protocols:** sACN (E1.31), Art-Net, OSC (for Sync), SlyLED current protocol via UDP

