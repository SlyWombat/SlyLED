## 4. Fixture Setup

### What Are Fixtures?
A fixture is the primary entity on the stage. It wraps physical hardware and adds stage-level attributes:
- **LED fixtures** — linked to a performer child, with LED strings
- **DMX fixtures** — linked to a DMX universe/address, with a profile and aim point

### Adding LED Fixtures
1. Go to **Setup** tab, click **Discover** to find performers
2. Click **Add Fixture** → select "LED" type
3. Link to a performer and configure strings (LED count, length, direction)

### Adding DMX Fixtures (Wizard)
Click **+ DMX Fixture** on the Setup tab to launch the 3-step wizard:
1. **Choose Fixture**: Search the Open Fixture Library (700+ fixtures) or create a custom fixture
2. **Set Address**: Universe, start address, and name — with real-time conflict detection
3. **Confirm**: Review all settings, click "Create Fixture"

### Setting up a HinksPix controller
A HinksPix PRO is a **pixel controller**: SlyLED streams colour to it over the network (sACN or Art-Net) and it drives the light strings plugged into its ports. The controller itself is **hardware, not a fixture** — it appears in **Setup → Hardware**. Each string on one of its ports is an LED fixture you place, target and program like any other.

1. **Add it.** Setup → **Discover** finds HinksPix controllers on your network (or **+ Add** with its IP). It appears as a row in **Hardware** with its status, firmware and a summary such as *1 port configured · 2 universes (1–2) · 200px*. **Rename** gives it a friendly name.
2. **Set up** (the green button on that row) opens the first-time guide:
   - **Your controller** — what SlyLED found, in plain words: the model and firmware, and what each board is. *Long-Range* boards need a receiver box at the far end of each cable; *Local SPI* boards take pixels directly. It also warns if SlyLED's DMX protocol (Settings → DMX) and the controller's input protocol don't match — they must, or nothing lights.
   - **Your layout** — **Import from my xLights show folder** (fastest if you use xLights: choose the folder with `xlights_networks.xml` and `xlights_rgbeffects.xml`, review, accept), or **Set up by hand**.
   - **Your strings** — for each port with a string, enter its pixel count (or its length and pixels-per-metre, then **=**) and a name such as *Garage eaves*. A port holds up to 680 RGB pixels on a PRO V1/V2; the guide refuses more and says why. Pixel type is WS2811, the only type a PRO V1/V2 drives. Saving creates one LED fixture per port, named as you typed. Nothing is sent to the controller yet.
   - **Send** — a plain summary (*Port 17 → Garage eaves, 200 pixels, WS2811, RGB, universes 1–2*) and **Send to the controller…**, which opens the push view: SlyLED snapshots the controller first (so it can always be put back), writes the layout, reboots it and reads it back to verify.
   - **Check** — walk to your strings. **Identify** lights one port red for 8 seconds. **Colour test** lights it red, then green, and asks what you saw; SlyLED works out the string's colour order from your answers and saves it (send again to apply). **Light them all** runs a slow chase on every string so you can confirm direction and full length.
3. **Nothing lit?** Check the layout has been sent, that the DMX protocol matches the controller (step 1 warns), and that the DMX engine is running (Settings → DMX; a show or Identify starts it automatically). If a string still stays dark it may be on another port — Identify the next one — or use the controller's own Test page (**Web UI** on its Hardware row).

**Configure** on the Hardware row opens the full five-step push view (Read · Edit · Review · Apply · Verify) for re-pushes, snapshots and restores; **Setup guide** in that view brings you back here.

### DMX Monitor
Settings → DMX → **DMX Monitor** opens a real-time 512-channel grid per universe. Click any cell to set a value. Color-coded by intensity.

### Fixture Group Control
Settings → DMX → **Group Control** opens a control panel for fixture groups. Master dimmer slider, R/G/B sliders, and quick color preset buttons (Warm, Cool, Red, Off).

### Testing DMX Channels
On the Setup tab, click **Details** on any DMX fixture to open the channel test panel:
- **Sliders** for every channel with live DMX output
- **Quick buttons**: All On, Blackout, White, Red, Green, Blue
- **Capability labels** show what each value range does (e.g., "Strobe slow→fast")
- Changes take effect immediately on the physical fixture via Art-Net/sACN

### Fixture Types
| Type | Description |
|------|-------------|
| **Linear** | LED strip. Pixels along a path. |
| **Point** | DMX light source with beam cone. |
| **Group** | Collection of fixtures targeted as one. |

---

