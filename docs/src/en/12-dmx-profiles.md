## 12. DMX Fixture Profiles

### Built-in Profiles
| Profile | Channels | Features |
|---------|----------|----------|
| Generic RGB | 3 | Red, Green, Blue |
| Generic RGBW | 5 | Red, Green, Blue, White, Dimmer |
| Generic Dimmer | 1 | Intensity only |
| Moving Head 16-bit | 16 | Pan, Tilt, Dimmer, Color, Gobo, Prism |

### Profile Editor — Step-by-Step (#527)

The fixture profile editor maps a DMX channel to what it *does* — this
red channel here, that pan channel there — and records any behaviour
the fixture firmware expects on each channel (gobo slot ranges, strobe
rate curves, colour-wheel slots). Once a profile exists the whole
orchestrator can drive that fixture with semantic calls like "set
colour to red" or "aim at stage (1150, 2100)" instead of raw DMX.

#### 1. Where to find it

Settings tab → **Profiles** sub-section. The list view shows every
built-in and custom profile, filterable by category
(par / wash / spot / moving-head / laser / effect). Each row has:

- **Edit** — opens the editor on the selected profile (disabled for
  built-in profiles; clone first if you want to diverge from one).
- **Clone** — copy a built-in or community profile into your local
  library under a new id; the copy is editable.
- **Share** — upload a custom profile to the community server
  (requires an internet connection, rate-limited per IP).
- **Delete** — remove a custom profile (built-in profiles cannot be
  deleted).

Click **New Profile** to start a fresh editor on a blank profile. You
can also reach the editor from any DMX fixture card by clicking the
profile name under the fixture's **Edit Profile** button.

#### 2. Top-level fields

- **Name** — operator-visible label shown in fixture cards + the
  profile picker.
- **Manufacturer** — free text; used for grouping in the Community
  browser and for dedup matching.
- **Category** — `par`, `wash`, `spot`, `moving-head`, `laser`,
  `effect`, `other`. Drives the preset-show generator.
- **Channel count** — total DMX slots the fixture uses. Auto-updated
  as you add channels; can also be set explicitly.
- **Colour mode** — `rgb`, `cmy`, `rgbw`, `rgba`, `single` (monochrome
  dimmer), or `color-wheel-only`. Drives how the show engine resolves
  a requested colour.
- **Pan range** / **Tilt range** — maximum mechanical sweep in
  degrees. Used by mover calibration to normalise DMX→angle.
- **Beam width** — degrees of the beam cone. Used for 3D beam-cone
  rendering and for marker-coverage prediction.

#### 3. Channels

Every channel has:

- **Offset** — 0-based channel number within the fixture's address
  range (not the universe). A 16-channel fixture has offsets 0..15.
- **Name** — operator-facing label. Matches the fixture's
  documentation.
- **Type** — the *semantic* role. Common types:
  `pan`, `pan-fine`, `tilt`, `tilt-fine`, `dimmer`, `red`, `green`,
  `blue`, `white`, `amber`, `uv`, `color-wheel`, `gobo`, `prism`,
  `focus`, `zoom`, `frost`, `strobe`, `macro`, `reset`.
  The type is what downstream code reads when it wants to control
  "the dimmer" — you can rename the channel but the type is the
  contract.
- **Bits** — 8 (one DMX slot) or 16 (two slots, coarse at this
  offset + fine at offset+1). Use 16-bit for pan and tilt if the
  fixture supports it; everything else is typically 8-bit.
- **Default** — the value the engine writes when no effect is
  overriding the channel. Leave blank for "set to 0 at idle." Use a
  non-zero default for channels the fixture needs lit to function
  (e.g. a lamp-on macro, shutter-open slot).

#### 4. Capabilities

Each channel can carry a list of capabilities that describe what DMX
value ranges mean to the fixture:

- **WheelSlot** — colour or gobo wheel position. Range `[min, max]`,
  label (`"Red"`, `"Open"`, `"Pattern 3"`), and — for colour wheels —
  an optional **`color` hex** like `#FF0000`. The orchestrator's
  RGB→slot resolver (used by show bake and mover calibration) picks
  the closest-matching slot by Euclidean distance in RGB space, so
  every colour-labelled slot needs the hex filled in. Without the
  hex the RGB pipeline silently falls through to slot 0 (white/open),
  which is the #624 footgun.
- **WheelRotation** — rotating-wheel range for cycle effects
  (`"CW cycle fast-slow"`, `"CCW cycle slow-fast"`).
- **WheelShake** — jitter ranges on gobo wheels.
- **ShutterStrobe** — a range with a `shutterEffect` of `"Open"`,
  `"Closed"`, or `"Strobe"`. The orchestrator's "open the shutter
  during calibration" helper walks these caps to find the right DMX
  value.
- **Prism**, **PrismRotation**, **Effect**, **NoFunction** — same
  pattern: `range`, `label`, optional type-specific fields.

Each capability row lets you pick the type from a dropdown, set
`min`/`max`, add a label, and (for `WheelSlot` on colour wheels) a
colour hex swatch.

#### 5. Saving + sharing

- **Save** persists the profile to `desktop/shared/data/dmx_profiles/`
  (gitignored per-install) and updates the SPA list.
- **Share to Community** uploads the profile JSON to the electricrv.ca
  server. The server dedups by channel-hash, so submitting a profile
  someone else already uploaded produces a "this fixture is already
  covered" response with a link to the existing entry.
- **Export** downloads every custom profile as a single JSON bundle.
  Use this to transfer a profile library between installs without
  going through the community server.

#### 6. When to create your own vs import from OFL

- **Import from OFL** first — 700+ fixtures are already there, and
  importing is one click. The Open Fixture Library volunteers have
  spent years curating the capability lists.
- **Clone and edit** if the fixture is close to an OFL profile but a
  channel or two differs (firmware update, mode variant).
- **Create from scratch** only when the fixture is genuinely not in
  OFL and not in the community. When you're done, share it so nobody
  else has to.

### Legacy quick-reference

Settings tab → **Profiles** → **New Profile** or **Edit**:
- Define channels with name, type (red/green/blue/dimmer/pan/tilt/etc.), default value
- Set beam width, pan/tilt range for moving heads
- Import from Open Fixture Library (OFL) JSON format

### Browsing the Open Fixture Library
Click **Search OFL** in Settings → Profiles to access 700+ fixtures from the [Open Fixture Library](https://open-fixture-library.org):

**Search**: Type a fixture name, manufacturer, or keyword → results show with Import buttons.

**Browse by Manufacturer**: Click **Manufacturers** to see all brands with fixture counts. Click a manufacturer to see all their fixtures. Click **Import All** to import every fixture from that manufacturer at once.

**Bulk Import**: From search results, click **Import All** to import all matching fixtures. From a manufacturer page, click **Import All** for the entire brand catalog.

Multi-mode fixtures create one SlyLED profile per mode automatically.

### Community Fixture Library
Share and discover profiles with other SlyLED users:

1. **Browse**: Click **Community** in Settings > Profiles to search, view recent, or popular
2. **Download**: Click Download — imported to your local library immediately
3. **Share**: Click **Share** on any custom profile to upload to the community
4. **Dedup**: Server detects duplicates by channel fingerprint (same channels = same fixture)
5. **Unified search**: When adding a DMX fixture, search queries Local + Community + OFL at once

Community server: https://electricrv.ca/api/profiles/

### Import/Export
- **Community**: Share/download profiles with other users
- **Search OFL**: Browse, search, and bulk import from the Open Fixture Library
- **Paste OFL**: Paste raw OFL JSON for offline fixtures
- **Import Bundle**: Load previously exported profile pack
- **Export**: Download all custom profiles as JSON
- **Built-in profiles** cannot be edited or deleted

---

