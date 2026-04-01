PROJECT SPECIFICATION: DMX \& Network Streaming Module

1\. ROLE \& OBJECTIVE

Act as a Senior Software Architect. Design and implement slyled having the ability to define a DMX bridge (hardware on an ESP board to send DMX commands or ArtNet sACN) and to create and manage existing fixtures. The current fixture concept that exists will be expanded with the ability to select a type of fixture, a SlyLED performer or a DMX fixture

Target Hardware: \[ESP32 with DMX board CQRobot] \[ Ethernet based ArtNet sACN ]
Various DMX fixtures from moving heads to multi function lights



\### 1. Functional Overview

The DMX Provider is responsible for the real-time translation of 3D fixture states into DMX512 channel data. This module must support industry-standard Ethernet protocols (\*\*Art-Net 4\*\* and \*\*sACN E1.31\*\*) to communicate with lighting consoles and nodes.



\---



\### 2. The Fixture Manager

The software must be able to map 3D Fixture Entities to physical DMX addresses.



\#### 2.1 Fixture Profiles (GDTF/JSON)

Each DMX fixture requires a definition file containing:

\* \*\*Channels:\*\* Mapping of offsets (e.g., Ch 1 = Dimmer, Ch 2 = Pan, Ch 3 = Tilt).

\* \*\*Resolution:\*\* Support for 8-bit (0-255) and 16-bit (0-65535) coarse/fine parameters.

\* \*\*Beams:\*\* 3D vector definition for the light beam origin and rotation limits.



\#### 2.2 Universe Mapping

\* \*\*Logical Universes:\*\* 1 to 32,768.

\* \*\*Output Assignment:\*\* Mapping logical universes to specific Network Interface Cards (NICs) and IP Unicast/Multicast destinations.



\---



\### 3. Spatial-to-DMX Translation Logic



\#### 3.1 Moving Head Geometry (Inverse Kinematics)

The system must calculate Pan and Tilt values based on a 3D "Target" point in the stage volume.

\* \*\*Input:\*\* Target Coordinate $(x, y, z)$.

\* \*\*Process:\*\* Calculate the Euler angles from the Fixture Origin to the Target.

\* \*\*Output:\*\* Map angles to DMX values (e.g., if Pan range is 540°, 270° = DMX 128).



\#### 3.2 Color Mapping

\* \*\*RGB/CMY:\*\* Translate 3D color field data (sampled at the fixture's origin) into the fixture's specific color mixing mode.

\* \*\*Virtual Dimmer:\*\* If a fixture lacks a mechanical dimmer, the software must scale RGB values mathematically.



\---



\### 4. Technical Deliverables (D5: DMX Module)



The AI Agent shall implement the following:



\* \*\*`DMXUniverse` Class:\*\* A buffer of 512 bytes with thread-safe read/write access.

\* \*\*`ArtNetEngine`\*\*: 

&#x20;   \* Implementation of ArtPoll/ArtPollReply for node discovery.

&#x20;   \* High-frequency UDP broadcasting of ArtDMX packets.

\* \*\*`sACN\_Engine`\*\*:

&#x20;   \* Support for E1.31 Multicast groups.

&#x20;   \* Priority level handling (0-200).

\* \*\*`FixtureProfileParser`\*\*: A utility to load JSON-based fixture definitions. Ability to keep a local library of fixtures and load from existing DMX public domain libraries



\---



\### 5. Performance Requirements

\* \*\*Frame Consistency:\*\* DMX packets must be dispatched at a stable interval (Default: 40Hz / 25ms).

\* \*\*Jitter Management:\*\* Use a high-resolution multimedia timer to prevent "stuttering" in moving light sweeps.

\* \*\*Network Overhead:\*\* Implement \*\*Unicast\*\* by default for large patches to prevent network flooding.



\---



\## 6. Considerations



The DMX fixture needs to have a beam width and when pointed away from the camera it will illuminate any placed objects in the 3d space, the timeline as it sweeps across the stage a DMX fixture will respond as soon as a stage item is illuminated by the fixture and the fixture will change colour based on the average colour based the capability of the fixture. if the fixture is pointed to the audience, it acts like a single point led. The fixtures beam width will be used to determine its start and stop point.



\### 7. Example JSON Entry

```json

{

&#x20; "fixture\_id": "MOVER\_01",

&#x20; "type": "Moving\_Head\_Spot",

&#x20; "universe": 1,

&#x20; "start\_address": 1,

&#x20; "mode": "16-bit-extended",

&#x20; "world\_position": {"x": 10.5, "y": 5.0, "z": -2.0},

&#x20; "rotation\_limits": {"pan": 540, "tilt": 270}

}

