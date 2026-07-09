# SlyLED Mobile UI Redesign — Design Doc

**Status:** v3 — operator approved 2026-05-12. Implementation in progress.
**Issue:** [#888](https://github.com/SlyWombat/SlyLED/issues/888)
**Author:** opened 2026-05-12 · revised 2026-05-12 after Gemini critique (v2) · revised 2026-05-12 after operator decisions (v3)
**Scope:** Android operator app + new iOS operator app. Stage and Status tabs largely retained; **Control** tab is rebuilt.

### Revision history

- **v1** (2026-05-12): initial draft. Vertical card stream on Control; iOS strategy framed as Compose Multiplatform vs. SwiftUI; resilience/haptics/conflicts not addressed.
- **v2** (2026-05-12): Gemini 2.5 Pro review folded in. Control re-architected to pager. iOS reframed around distribution. New §6 "Resilience & Feedback". Hybrid shortcut renderer promoted from open question to spec.
- **v3** (2026-05-12): operator decisions on the five §11 open questions:
  - (1) `POST /api/show/next` confirmed for the Now Playing anchor.
  - (2) Pan/tilt arrow on Grab tiles ships in v1 — no "v2 evolution" deferrals; this is the final design.
  - (3) Blackout = long-press SlyLED logo only. **The 🛟 panic sheet is removed**; its actions move to per-page safety buttons (see §6.5).
  - (4) Settings stays at top-right (gear icon), not in the bottom nav. Bottom nav is 3 tabs: Stage / Control / Status.
  - (5) Default cold-start page is **Master**.

---

## 1. Intent

The current Control tab is a fixed LazyColumn of widgets (Now Playing → Stop → Brightness → Phone Control rows → Timelines). It does one thing well — start a timeline — and everything else is either missing or buried:

- **Direct DMX fixture control** (e.g. trigger the Chauvet Hurricane Bubble Haze X2 Q6 to bubble, haze, change LED colour, strobe) — **does not exist** in the app today. Profile-driven UI is desktop-SPA only.
- **Grab a moving head** — exists, but as a flat list of all DMX movers with no favourites, no recent, no preview of where each one is currently aimed.
- **Auto Brightness** — exists (`MicAutoBrightness.kt`, UDP cmd `0x6D` on port 4211) but lives in Settings; not surfaced on Control.
- **Pick + run a show** — playlist exists; transport works; but reaching it from app cold-start is 1 tab + scroll.

The redesign turns Control into a **Command Surface**: a paged screen of quick-access cards, each ≤2 taps from any operator action. The visual language is Kinetic Prism (`docs/slyled_design_manifesto.md`, `docs/design.md` §2). The same design carries to iOS.

## 2. Design principles

This is not a visual-design project; it's a **performance instrument** project. The screen is one tool an operator uses while their attention is on the stage. The principles below are ordered: when they conflict, earlier wins.

1. **Confirm with feel, not just sight.** Every primary action emits a distinct haptic. The operator's eyes are on the stage; the screen's job is to confirm with their thumb that the right thing fired. Haptics are not polish — they are spec.
2. **Two-tap maximum** from cold app start to any of: trigger a bubble, grab a mover, start a show, toggle Auto Brightness, blackout. Scroll-to-find does **not** count as a tap (see §4 IA).
3. **Glanceable.** Information that matters during a show (what's running, what's claimed, connection state) is readable in a half-second peek without parsing.
4. **One-handed, dim room.** 56dp minimum tap target; high-contrast bloom states; no hover dependence; no nested menus that require scrolling.
5. **Resilient.** Network drops, contested claims, and bad profile data are normal operating conditions, not exceptions. Every card has a defined state for each (§6).
6. **Operator-only.** No editing of layout / actions / timelines / profiles ([feedback_android_operator_not_editor](../../memory)). If a surface would require editing to be useful, it belongs on the desktop SPA. **What this gives up** to be award-winning: configurability, completeness, the Resolume-style "do anything" surface. **What it leans into:** speed, calm, the feel of an instrument.
7. **Live always.** Every card reflects current orchestrator state via REST + UDP push channels. No "Refresh" buttons on Control — those are signs of a stale mental model.
8. **Profile-driven, not fixture-typed.** A DMX fixture's controls come from its loaded profile (`channel_map`, capabilities). The same component renders a bubble machine, a hazer, or a wash. No fixture-specific Compose code.
9. **Parity rule.** Anything the mobile Control tab does, the desktop SPA must also do ([feedback_ui_parity](../../memory)). The mobile redesign forces an audit of SPA gaps.
10. **Kinetic Prism, applied — legibility wins.** Dark-first, bloom on active, glass on overlays, Space Grotesk for headings, Inter for body. Tokens from `Theme.kt` — no new colour invention. **Glass legibility is non-negotiable**: overlay foreground content must be readable regardless of what's scrolling behind. High-blur + dark scrim where needed.

## 3. Current state audit

### 3.1 Existing surfaces (Android, v1.7.126)
| Surface | File | Status |
|---------|------|--------|
| Bottom nav (Stage / Control / Status / Settings) | `ui/navigation/Navigation.kt` | Keep shape |
| `ControlScreen` — Now Playing / Stop / Brightness / Phone Control / Timelines | `ui/screens/control/ControlScreen.kt` | **Replace body with pager** |
| `ControllerModeOverlay` (gyro takeover of a mover) | `ui/screens/control/ControllerModeOverlay.kt` | Keep, reachable from new IA |
| `PointerModeOverlay` (dead code post-v1.7.66) | `ui/screens/control/PointerModeOverlay.kt` | **Delete** as part of redesign cleanup |
| `MoverStatusRow` | `ui/screens/control/MoverStatusRow.kt` | Re-skin into a `MoverChip` |
| `LiveStageScreen` (Stage tab) | `ui/screens/livestage/LiveStageScreen.kt` | Cosmetic refresh only |
| `StatusScreen` | `ui/screens/status/StatusScreen.kt` | Cosmetic refresh only |
| `SettingsScreen` | `ui/screens/settings/SettingsScreen.kt` | Auto Brightness toggle moves out to Control; calibration stays |

### 3.2 Existing data + transport (already in place)
- `SlyLedRepository` over Retrofit (`SlyLedApi.kt`) — every route the redesign needs already exists: `/api/brightness`, `/api/mover-control/{claim,release,orient,color,flash}`, `/api/fixtures`, `/api/fixtures/live`, `/api/fixtures/{id}/dmx-test`, `/api/show/{playlist,start,status}`, `/api/actions`.
- `UdpClient.kt` — already handles the binary protocol v4 (incl. AUTOBRI_PUSH 0x6D).
- `ControlViewModel`, `LiveStageViewModel`, `StatusViewModel` — feed state via StateFlow.
- `audio/MicAutoBrightness.kt` + `audio/PlaybackCaptureService.kt` — Auto Brightness pipeline is wired; the redesign only changes where the toggle lives.

### 3.3 What does **not** exist yet
- A profile-driven "direct DMX fixture" sheet. The SPA has it; mobile doesn't. **New component.**
- A "favourites" / "recent" model for fixtures. **New persisted state** (per-device, in `ServerPreferences`).
- A surface that lists shows (timelines + playlists) optimized for one-tap launch. The current `TimelineCard` is close; needs ranking + last-played.
- Any iOS app. **New project**, see §7.
- A formal model for **claim conflicts** (another operator already controls the mover) — see §6.2.
- A **panic menu** for "kill strobes / send movers home / pause all effects" — see §6.5.
- A **disconnected state** UI pattern — see §6.1.

## 4. Information architecture

### 4.1 Top-level navigation
```
┌──────────────────────────────────────────┐
│  SlyLED              ⚡ Connected     ⚙   │   ← top bar: logo · pill · Settings
├──────────────────────────────────────────┤
│  ▶ NOW PLAYING · Disco Inferno  01:24    │   ← persistent anchor (collapsed when idle)
│  ━━━━━━━━━━━━━━━━━━━━━━━━━━━ ■ STOP      │
├──────────────────────────────────────────┤
│  ❖ Master  ║Grab║  Fixtures  Shows       │   ← segmented pager header
│                                          │
│             ACTIVE PAGE CONTENT          │
│             (fills remaining height)     │
│                                          │
├──────────────────────────────────────────┤
│   Stage      ║Control║       Status      │   ← bottom nav (3 tabs), bloom on active
└──────────────────────────────────────────┘
```

Persistent top-bar gestures:
- **Long-press SlyLED logo** → instant blackout (master = 0). Bloom flashes red. Haptic: heavy double-tap. This is the only nuclear option; there is no separate panic sheet (v3 decision).
- **Tap ⚡ connection pill** → reconnect / pick orchestrator (existing flow). When disconnected, the pill turns orange and slow-pulses (§6.1).
- **Tap ⚙ Settings gear** (top-right) → Settings screen.

Bottom nav is **3 tabs** only — Stage / Control / Status. Settings lives at the top-right gear, not in the bottom nav.

### 4.2 Control tab — Command Surface (revised v2)

> **v1 problem:** the vertical LazyColumn stream failed the two-tap goal — Fixtures and Shows landed below the fold once Now Playing + Master + Grab were on screen. Scroll-to-find broke the operator promise.
>
> **v2 solution:** Now Playing is a **persistent anchor** above the content. The remaining four surfaces are **pages** behind a segmented header. One swipe (or one segment tap) reaches any category; one further tap fires the action. Total: ≤2 interactions, guaranteed.

The content area is a `HorizontalPager` with four pages, fronted by a `SegmentedRow`:

| Segment | Page contents | Primary action(s) |
|---------|---------------|-------------------|
| **Master** *(default page on cold start)* | Global brightness slider, Auto Brightness toggle + source picker | Tap brightness; tap Auto switch |
| **Grab** | Favourites + recent movers as horizontal chips, full list below | Tap chip → controller overlay |
| **Fixtures** | Vertical stack of non-mover DMX fixtures with profile-driven shortcuts | Tap shortcut (BUBBLES, HAZE, colour) |
| **Shows** | Ranked list of timelines/playlists with star + last-played | Tap ▶ |

**Why Master as the default page:** the most common operator action mid-show is "the room is too dark / too bright" or "the band's not loud enough for Auto Brightness." Master is always the right thing to land on.

**Card → page mapping rationale:** each former card becomes a page, not stacked together. This costs vertical real-estate variety on first glance but pays it back in scroll-elimination, which was the v1 fail.

### 4.3 Persistent Now Playing anchor

The anchor sits above the segmented header on all four pages. It is the only persistent body element (besides the segments themselves) so the operator always knows what's playing and can stop it from any page.

- **Idle state** (collapsed, ~40dp): single line `"No show running"` in muted text. Tapping it jumps to the Shows page.
- **Playing state** (~96dp): name, loop chip, elapsed / total, progress bar, **STOP** button (full-width inside the anchor; 56dp, red glow on press, heavy haptic), **Next** button when in a playlist.

Stop and Next are the only show-transport controls that exist outside the Shows page. This is intentional — they're the panic moves an operator needs from anywhere.

### 4.4 What does **not** appear on Control

- Per-fixture raw DMX channel sliders. Those are SPA-only (editing-adjacent). The "More controls →" sheet on a Fixtures card exposes capabilities, not byte values.
- Layout editing. SPA only.
- Timeline editing. SPA only.
- Action editing. SPA only.

## 5. Page specs

### 5.1 Master page
**Source:** `GET /api/settings.globalBrightness`, `MicAutoBrightness` state, audio source selection (existing in Settings).

**Layout:** two stacked sections.
- **Brightness** — large slider with bloom-on-drag, current value displayed mono, ±5% step buttons either side (the dim-room finger-precision aid).
- **Auto Brightness** — switch + source dropdown (phone mic / playback capture / off). Below: a live envelope meter (small horizontal bar showing the current mic level → master multiplier). The meter is the answer to "is Auto actually doing anything?" without leaving the page.

**Why move Auto Brightness off Settings:** Auto Brightness is the single most-toggled live control; burying it in Settings forces an operator to tab away during a show. The Settings entry stays as the **calibration** surface (envelope smoothing, gain, gate floor, source-specific config).

### 5.2 Grab page — moving heads
**Source:** `fixtures.filter { fixtureType == "dmx" && profile.panRange > 0 }`, plus a new **favourites** list persisted in `ServerPreferences`.

**Layout:**
- Top row: 88×104dp favourite chips (horizontal LazyRow). Star to favourite/unfavourite.
- Below: full list of all movers in a vertical LazyColumn, sorted by last-grabbed desc.

Each tile / row shows:
- 56dp circular preview: current colour swatch (from `/api/fixtures/live`) with a small radial arrow showing pan/tilt direction (vector projected top-down). **Open question — §10.4**: does the arrow read at 56dp in a dim room, or should the tile be just the colour?
- Fixture name (truncated to 1 line).
- **Claim badge** when another operator has it (§6.2). Greyed, muted-purple bloom, "Held by SPA" caption.

**Tap → opens the existing `ControllerModeOverlay`.** No behavioural change; visual restyle to Kinetic Prism.

**Long-press → mini-menu**: Flash · Send to home · Blackout this fixture · Unfavourite.

**Empty state:** "No moving heads in the layout" + link "Add one in the desktop app".

### 5.3 Fixtures page — direct DMX control (new)
**Source:** `fixtures.filter { fixtureType == "dmx" && profile.panRange == 0 }` (non-movers — bubble machines, hazers, washes, pars, strobes).

**Layout:** vertical stack of fixture cards. Each card renders **profile-driven shortcuts** based on its loaded profile.

#### 5.3.1 Shortcut resolution — hybrid model (promoted from v1 §10.5)

For each channel in a fixture's profile, the renderer resolves a shortcut via three passes, in order:

1. **Explicit annotation** *(profile-author opt-in)*: if the channel carries a `"shortcut"` field (e.g. `"shortcut": "bubble-toggle"`), the renderer uses that mapping directly. Stable contract; community profile authors can guarantee UI behaviour.
2. **Capability-type heuristic**: if no explicit shortcut, look at `capability.type` + `capability.shutterEffect`. `ShutterStrobe Strobe` → ⚡ STROBE button. `WheelSlot` with `color` field → colour swatch row.
3. **Name-match fallback**: if neither of the above applies, match `channel.name.toLowerCase()` against a small dictionary (`"bubble" → bubble-toggle`, `"haze" / "fog" → haze-segmented`, `"fan" / "blower" → fan-segmented`). Graceful degradation for un-annotated community profiles.

Un-resolved channels are not shown as shortcuts — they appear only in the "More controls →" sheet.

#### 5.3.2 Shortcut catalogue (v1)

| Shortcut ID | UI | Writes | Trigger source |
|-------------|-----|--------|---------------|
| `bubble-toggle` | Toggle button 🫧 | dimmer-class channel: 255 / 0 | name contains "bubble" OR explicit |
| `haze-segmented` | LOW / MED / HIGH (64 / 128 / 220) + OFF | dimmer-class channel | name contains "haze"/"fog" OR explicit |
| `fan-segmented` | Slow / Med / Fast | speed channel: 64 / 160 / 255 | name contains "fan"/"blower" OR explicit |
| `color-swatch` | Colour row + colour wheel sheet | red/green/blue (+ uv if present) | profile has RGB triad |
| `uv-toggle` | UV switch | uv channel: 255 / 0 | profile has `uv` channel |
| `strobe-momentary` | ⚡ STROBE press-and-hold | strobe channel into `ShutterStrobe Strobe` range | profile has strobe with Strobe effect |
| `clean-mode` | Maintenance button (long-press confirm) | reset channel: 255 | profile has channel typed `reset` with Maintenance cap |

Channels with no matching shortcut → "More controls →" sheet.

#### 5.3.3 "More controls →" sheet

Full-screen panel. Per-channel capability rendering with labelled sliders / segmented controls / wheel-slot pickers. The only non-mover place an operator can touch raw-ish DMX. Stays profile-driven (capability labels, not byte values).

**All shortcut + sheet writes wire through:** `POST /api/fixtures/{id}/dmx-test` (existing) with the channel-value map. No new server endpoint needed.

#### 5.3.4 Schema extension

The profile schema (see `desktop/shared/dmx_profiles.py`) gains an optional per-channel field:

```json
{ "offset": 0, "name": "Bubble Output", "type": "dimmer", "shortcut": "bubble-toggle", ... }
```

`shortcut` is optional, free-form string keyed against the catalogue in §5.3.2. Profiles without it still work via the heuristic + name-match fallbacks.

A more semantic `capability` field (`"capability": "EFFECT_BUBBLE"`) would decouple profile from UI implementation, but is out of scope for this redesign — `shortcut` ships as the contract.

### 5.4 Shows page
**Source:** `GET /api/timelines`, `GET /api/show/playlist`, and new persisted state: `lastPlayedAt: Map<TimelineId, Instant>` + `starredTimelines: Set<TimelineId>` in `ServerPreferences`.

**Layout:**
- Top: starred shows (★) — large rows, 64dp, single tap to start.
- Middle: recent shows (last 7 days played).
- Bottom: full list.
- Playlist controls in a collapsible footer.

**Tap → starts the timeline.** Long-press → quick actions (Star, Loop, Add to playlist).

**New endpoint required:** `POST /api/show/next` for playlist skip-forward from the Now Playing anchor. Trivial server-side addition.

## 6. Resilience & feedback (new — added v2)

This section is what was missing from v1. A live-control app whose spec only covers the happy path will fail the moment the show is on fire.

### 6.1 Network-loss state

The orchestrator is on the same Wi-Fi the operator is. Wi-Fi drops are not exceptional; they are routine.

**Connection state machine:** `Connected` → `Degraded` (no PONG in 3s) → `Disconnected` (no PONG in 10s) → `Connected` (reachable again).

**UI patterns:**
- **Top bar pill** is the canonical indicator:
  - Connected: `⚡ Connected` in `GreenOnline`.
  - Degraded: `⚠ Reconnecting…` in `OrangeWled`, slow pulse.
  - Disconnected: `✕ Offline` in `RedError`, fast pulse.
- **All cards** during Degraded/Disconnected: 60% opacity overlay, controls remain visible (so the operator sees the layout), but tap targets are disabled with a haptic-no-go (light bump, no action). The disabled state is the *visible* one — not a modal that hides the controls.
- **Auto-reconnect** runs continuously in the background; the operator never has to invoke it. Manual reconnect via tapping the pill is the fallback.
- **Queued writes:** writes attempted during Degraded are queued; if the connection restores within 5s they fire on restore. Writes attempted during Disconnected are dropped with a haptic-no-go. We do not silently queue minutes of stale commands.

### 6.2 Conflict state — claim already held

The mover-control claim is exclusive: only one operator can drive a given mover at a time. The Android app today silently fails the claim if the SPA has it. Unacceptable.

**UI patterns:**
- A `MoverChip` whose mover is claimed by another client shows a small "Held by SPA" / "Held by Phone #2" caption and a muted-purple bloom (not the active CyanSecondary).
- Tap on a held chip → **takeover sheet**: "This mover is held by [client]. Take over?" with Confirm / Cancel. Takeover is loud (medium haptic, dialog confirmation) because it interrupts another operator.
- Server-side: `POST /api/mover-control/claim` already supports a `force=true` parameter; the takeover sheet sets it.

**Discovery:** the client list comes from `GET /api/mover-control/status` (existing) which carries the current holder. The mobile app polls it at 1Hz when the Grab page is visible.

### 6.3 Haptics — first-class

Haptics are spec, not polish. Map:

| Action | Haptic |
|--------|--------|
| Start show / Start timeline | Medium tick |
| Stop show (anchor STOP button) | Heavy thud |
| Blackout (long-press logo, panic) | Heavy double-tap |
| Toggle (Auto Brightness, UV, bubble) | Light tick |
| Mover chip tap (claim acquired) | Light tick |
| Mover chip tap (claim denied / conflict) | Light double-bump (no-go) |
| Slider drag (brightness) | Per-step soft tick at 5% boundaries |
| Strobe momentary press | Continuous low rumble while held |
| Disconnected button press | Light double-bump (no-go) |
| Profile error (shortcut couldn't render) | Single sharp tick |

Android uses `HapticFeedbackConstants` + `VibrationEffect` for haptics 2+. iOS uses `UIImpactFeedbackGenerator` / `UINotificationFeedbackGenerator`. Both layered behind a small `Haptics` abstraction in the shared Compose Multiplatform module.

Each action's haptic is paired with a brief visual confirmation (bloom flash on the affected element). Visual is redundancy, not the primary feedback.

### 6.4 Error states — per card

Every page has a defined error state, not a generic "Something went wrong":

| Page | Error trigger | UI |
|------|---------------|-----|
| Master | `setBrightness` API failed | Slider snaps back to last-known, top-bar pill flashes orange, toast "Brightness write failed" |
| Grab | `claim` API failed (non-conflict) | Chip flashes red, toast with reason |
| Grab | `claim` API failed (conflict) | Takeover sheet (§6.2), not an error |
| Fixtures | Profile shortcut resolution failed | Card shows "More controls →" only, no shortcuts; muted error icon with tooltip "Profile shape issue — see Settings" |
| Fixtures | `dmx-test` write failed | Shortcut flashes red, toast |
| Shows | `startTimeline` failed | Row flashes red, toast |
| Any | Connection lost mid-write | Top bar transitions to Degraded (§6.1) |

Toasts use the Kinetic Prism error token (`RedError`) and disappear in 3s. Errors do not interrupt the operator with modals unless the operator caused the error (e.g. takeover).

### 6.5 Safety actions — distributed across pages (v3)

**The v2 panic sheet is removed.** Operator decision: long-press logo is the only modal "nuclear" gesture (instant blackout). The other panic-adjacent actions move to their natural home page as inline buttons, each one tap from its page:

| Action | Home page | UI |
|--------|-----------|-----|
| BLACKOUT (master = 0) | top bar | Long-press SlyLED logo (1 gesture, no confirmation, heavy haptic) |
| STOP SHOW | NowPlayingAnchor | STOP button (already in §5.1) |
| ALL MOVERS HOME | Grab page header | "Send all home" button + heavy haptic on tap |
| KILL STROBES + STOP BUBBLES/HAZE | Fixtures page header | "Stop all effects" button — calls both `kill-strobes` and `kill-effects` |

Rationale: every panic action is ≤2 taps from any page (1 tap to switch pager segment, 1 tap to fire). A modal sheet adds a tap and a discoverability burden ("where is the panic button?"). The operator's mental model becomes: "what kind of panic? movers / fixtures / show / everything" — and the panic action lives where they'd already go for that category.

**Server-side endpoints (still required):**
- BLACKOUT — existing master=0 via `POST /api/brightness`.
- STOP SHOW — existing.
- ALL MOVERS HOME — new `POST /api/mover-control/all-home` (writes home DMX values to every fixture with `panRange > 0`).
- KILL STROBES — new `POST /api/fixtures/kill-strobes` (writes the Open value to every strobe channel of every fixture).
- STOP BUBBLES + HAZE — new `POST /api/fixtures/kill-effects` (writes 0 to any channel matching `bubble-toggle` / `haze-segmented` shortcuts).

The "Stop all effects" button in Fixtures fires both kill-strobes and kill-effects in parallel.

### 6.6 Accessibility

- **Dynamic Type:** all type scales respect the OS font-size setting up to 130%. Layouts reflow (Master slider can wrap its labels; Fixtures shortcut row can wrap to two rows). No fixed-pixel text dimensions.
- **Content descriptions:** every interactive element gets a Compose `contentDescription` / SwiftUI accessibility label. Non-text-only ChipS get descriptions like "Mover Mover-1, currently red, aimed front-stage".
- **Reduced motion:** if the OS reports reduce-motion, blooms become static fills (no pulse animations) and kinetic transitions become hard crossfades.
- **Colour-blind:** state colour is always paired with an icon or text (green-online = ⚡, red-error = ✕, orange-degraded = ⚠). Never colour alone.

## 7. iOS strategy

### 7.1 Decision (revised 2026-05-14, post-v1.8.2)

Distribution path: **paid Apple Developer Program + TestFlight upload via GitHub Actions on `macos-15` runners.** A tagged commit (`git tag ios-v*`) triggers `.github/workflows/ios-testflight.yml`, which generates the Xcode project from `ios/project.yml` via XcodeGen, archives, exports an App Store IPA, and uploads through `xcrun altool`. Apple's Beta App Review processes the build (15 min – 24 hr for the first build of a new `MARKETING_VERSION`); operator installs through the TestFlight app on their iPhone. The 90-day TestFlight expiry is mitigated by tagging a refresh build every ~75 days.

**Implementation:** **native SwiftUI**, not Compose Multiplatform.

Detail of every surface's port — file layout, REST/UDP/audio shims, theme tokens, persistence schema, single-tag jump to parity — lives in [`ios_parity_spec.md`](ios_parity_spec.md). This section captures only the decision and its rationale.

### 7.2 Stage 0 — Apple Developer setup (operator-side)

Pre-work, handled by the operator on Windows + Apple web portals — no Mac required:

1. Apple Developer Program enrolment ($99/yr).
2. Bundle ID `ca.electricrv.slyled` registered in the developer portal.
3. Apple Distribution cert + App Store Connect API key generated; entered as GitHub repo secrets per [`apple_secrets_setup.md`](apple_secrets_setup.md).
4. Operator added as an internal TestFlight tester.
5. Steps 1–4 detailed in [`apple_developer_setup.md`](apple_developer_setup.md); end-user install steps in [`testflight_install_guide.md`](testflight_install_guide.md).

This is one-time setup. After completion every release is `git tag ios-vX.Y.Z && git push origin ios-vX.Y.Z`.

### 7.3 Why native SwiftUI (not Compose Multiplatform)

The v1 design doc recommended Compose Multiplatform with ad-hoc provisioning. After v1.8.2 shipped on Android, that decision was reversed for three concrete reasons:

- **The Swift shell already exists** (v0.1.0 TestFlight pipeline-validation build) and ships green through `.github/workflows/ios-testflight.yml`. Rebuilding the install path for CMP costs more than re-writing the UI in Swift.
- **TestFlight, not ad-hoc.** The operator runs a Windows machine; ad-hoc provisioning's "build locally in Xcode and AirDrop the IPA" loop doesn't fit the cross-platform CI workflow. TestFlight + a GitHub Actions `macos-15` runner does.
- **Native iOS APIs without Kotlin/Native interop.** `AVAudioEngine` for Auto Brightness, `CoreMotion` for ControllerModeOverlay, `Network.framework` for UDP, `CoreHaptics` for the haptics catalogue — all first-class in Swift, all `expect/actual` shim friction in CMP.

The trade-off is a dual UI codebase: every new control on Android must be ported to Swift, and `FixtureShortcuts.kt` has a literal Swift twin in `FixtureShortcuts.swift`. The mitigation is a shared snapshot-test corpus across Pytest (SPA), JUnit (Android), and XCTest (iOS) — the corpus is the contract; the three implementations must round-trip it identically.

> **Implemented (#906).** The corpus lives at `tests/fixtures/shortcut_corpus/` (profile JSONs + `expected.json`, the latter generated *from* the JS reference resolver — never hand-edited). The three gates: `tests/test_fixture_shortcuts.py` (JS via Node), `android/app/src/test/java/com/slywombat/slyled/FixtureShortcutsTest.kt` (`gradlew test`), and `ios/SlyLEDTests/FixtureShortcutsTests.swift` (the `SlyLEDTests` XCTest target in `ios/project.yml`; corpus bundled as test resources). When a resolver diverges, fix the Kotlin/Swift twin to match the JS.

If the dual-codebase tax ever proves intolerable, re-open the CMP option as a follow-up issue — but only after iOS reaches v0.7 (parity), so we never block parity on a refactor.

### 7.4 What we considered and rejected

- **Compose Multiplatform (the v1 recommendation).** Rejected for the three reasons in §7.3. Re-open post-v0.7 only if the parallel-codebase cost becomes a real burden.
- **Ad-hoc distribution.** Rejected because the operator's machine is Windows-only and the GitHub Actions runner is a cleaner signing host than a manually-managed Mac in the operator's hands.
- **React Native.** No existing JS in the codebase; no benefit over native SwiftUI.

## 8. Component inventory

Every component below maps to existing Kinetic Prism tokens. No new colours invented.

| Component | Background | Border | Text | Bloom |
|-----------|-----------|--------|------|-------|
| `CommandPage` container | `DeepSlate` | — | `NearWhite` | — |
| `SegmentedRow` | `DarkSlate` | `MutedSlate` | `LightSlate` / `NearWhite` selected | accent fill on selected, 200ms ease |
| `NowPlayingAnchor` | `DarkNavy` | `DimSlate` @ 30% | `NearWhite` | CyanSecondary pulse when playing |
| `PrimaryAction` (STOP, Start) | `BluePrimary` | none | white | blue glow on press |
| `DestructiveAction` (Blackout, page-level safety rows: Send all home / Stop all effects) | `RedError` | none | white | red glow on press, heavy haptic |
| `FixtureChip` (Grab tile) | `DarkNavy` | `DmxPurple` @ 40% | `NearWhite` | purple glow when claimed by **this** app, muted-purple when by **another** |
| `ShowRow` | transparent | bottom hairline `DimSlate` | `NearWhite` | cyan glow on current |
| `Switch` (Auto Brightness, UV) | M3 default, accent → `CyanSecondary` | — | — | bloom on thumb when on |
| `Slider` (brightness, sheet sliders) | M3 default, accent → `LuminaBlue` | — | — | bloom on knob while dragging |
| `Segmented` (Haze LOW/MED/HIGH, Fan) | `DarkSlate` | `MutedSlate` | `LightSlate` / `NearWhite` selected | accent fill on selected |
| `ConnectionPill` (top bar) | transparent | accent border (green/orange/red) | accent | pulse at state-dependent rate |
| `GlassOverlay` (panic sheet, takeover, fixture sheet) | `DarkNavy` @ 88% + 24px blur + black scrim | `DimSlate` | `NearWhite` | — |

**Type ramp** (`Theme.kt` colour tokens exist; type ramp needs adding):

| Token | Family | Weight | Size | Letter-spacing | Use |
|-------|--------|--------|------|----------------|-----|
| `display` | Space Grotesk | 700 | 28sp | -0.5 | Page section headings |
| `title` | Space Grotesk | 600 | 18sp | 0 | Fixture / show names, anchor title |
| `body` | Inter | 400 | 14sp | 0.1 | Card body, captions |
| `label` | Inter | 500 | 12sp | 1.2 (caps) | Chips, badges, segment labels |
| `mono` | system mono | 400 | 13sp | 0 | DMX values, RSSI readouts |

Bundle Space Grotesk and Inter as font assets (Android: `app/src/main/res/font/`; iOS: bundle OTFs, register via Info.plist).

## 9. Engineering plan

### 9.1 Stage 0 — design doc approval
This document, plus annotated wireframes (Figma or PDF) for each page. No code lands until operator signs off.

### 9.2 Stage 0.5 — Apple Developer setup
§7.2 pre-work. Runs in parallel with Stage 1 (no iOS code yet, but the operator can complete enrolment).

### 9.3 Stage 1 — profile-driven shortcut renderer (highest risk; SPA first)
- Implement `desktop/shared/spa/js/fixture_shortcuts.js` with the §5.3.1 hybrid resolver.
- Extend `desktop/shared/dmx_profiles.py` schema to accept optional `shortcut` per channel; update validators.
- Annotate the Hurricane Bubble Haze X2 Q6 profile (community upload, already in `dmx_profiles/`) with explicit `shortcut` fields.
- Snapshot test corpus: every built-in + every existing community profile → expected shortcut list.
- SPA renders shortcuts on the Fixtures tab. Operator confirms behaviour on real hardware **before** mobile work begins.

### 9.4 Stage 2 — Android Control rebuild
Replace `ControlScreen.kt`. New file structure:
```
ui/screens/control/
  ControlScreen.kt              ← thin orchestrator: anchor + pager
  NowPlayingAnchor.kt
  pages/
    MasterPage.kt
    GrabPage.kt
    FixturesPage.kt
    ShowsPage.kt
  shortcuts/
    FixtureShortcuts.kt         ← Kotlin twin of fixture_shortcuts.js
    FixtureShortcutsTest.kt     ← shared corpus
  overlays/
    ControllerModeOverlay.kt    ← unchanged in v1
    FixtureSheet.kt             ← new "More controls →"
    TakeoverSheet.kt            ← §6.2 conflict UI
    (v3: no PanicSheet — page-level safety buttons replace it; see §6.5)
  haptics/
    Haptics.kt                  ← expect/actual ready for CMP
  conn/
    ConnectionState.kt          ← §6.1 state machine
    ConnectionPill.kt
```
Compose UI tests for each page state. Roborazzi screenshot tests per state including Disconnected and Conflict.

### 9.5 Stage 3 — Auto Brightness move + Resilience
- Promote Auto Brightness toggle to Master page.
- Wire the connection state machine through `ControlViewModel` + every page.
- Implement haptics catalogue (§6.3).
- Settings keeps Auto Brightness calibration.

### 9.6 Stage 4 — Shows ranking + page-level safety + new endpoints
- Persist `lastPlayedAt` + `starredTimelines` in `ServerPreferences`.
- Server: add `POST /api/show/next`, `POST /api/mover-control/all-home`, `POST /api/fixtures/kill-strobes`, `POST /api/fixtures/kill-effects`.
- Wire "Send all home" button to Grab page header; "Stop all effects" button (fires kill-strobes + kill-effects in parallel) to Fixtures page header. Blackout remains a long-press logo gesture; STOP show stays on the NowPlayingAnchor. No modal panic sheet.

### 9.7 Stage 5 — Compose Multiplatform iOS scaffold
- Move the Android `ui/`, `viewmodel/`, `data/` modules into a CMP shared module.
- Implement iOS `actual` shims (Haptics, AudioCapture, UdpSocket, Storage).
- Bring up `Control` first, then `Stage` / `Status` / `Settings`.
- Build an ad-hoc IPA, install on the operator's iPhone for testing.

### 9.8 Stage 6 — Parity + polish + docs
- SPA gap audit ([feedback_ui_parity](../../memory)): every new mobile surface has a desktop counterpart.
- Screenshot regen across both platforms.
- USER_MANUAL.md + screenshots update.

## 10. Test strategy

| Layer | Tool | Coverage |
|-------|------|----------|
| Shortcut renderer | Pytest (SPA-side) + JUnit (Android/CMP shared) | Every built-in + community profile produces expected shortcut list |
| Compose UI | Compose UI Test + Roborazzi | Each page state (idle, running, claimed, degraded, disconnected, error, conflict, profile-error) |
| Repository / API | Retrofit/Ktor MockServer | All new endpoints (`/api/show/next`, panic endpoints) |
| Connection state machine | JUnit | State transitions on PONG loss/restore, write queuing during Degraded |
| Haptics | Manual | Per-action haptic feel on real device; no automated test |
| iOS UI (CMP) | Compose Multiplatform UI Test on simulator | Same suite as Android |
| End-to-end | Manual on real rig | Per release; operator runs the live test |

Playwright is desktop-only and stays so. The mobile equivalent is screenshot tests committed to git + the manual operator pass; there is no fast emulator pipeline for an Android app on this team's infra, and we explicitly accept that.

## 11. Open questions (resolved)

All v2 open questions resolved by operator on 2026-05-12:

- ~~iOS strategy~~ → §7.1: Compose Multiplatform with ad-hoc paid Developer account.
- ~~Card ordering~~ → moot, IA is paged not stacked.
- ~~Shortcut match strategy~~ → §5.3.1: hybrid (explicit `shortcut` field + heuristic + name-match fallback).
- ~~Now Playing — Next button~~ → **yes**, `POST /api/show/next` ships.
- ~~Pan/tilt arrow on Grab tiles~~ → **ships in v1**. No deferrals; this is the final design.
- ~~Blackout gesture~~ → **long-press logo only**. Panic sheet removed; safety actions distributed across pages (§6.5).
- ~~Settings tab fate~~ → **top-right gear icon**, not bottom nav. Bottom nav is 3 tabs (Stage / Control / Status).
- ~~Default cold-start page~~ → **Master**.

## 12. Out of scope (separate issues if needed)

- Desktop SPA redesign — except the new Fixtures shortcut renderer, which lands there first.
- Editing surfaces on mobile — explicitly not happening.
- Tablet / iPad layouts — design must not preclude them, but no two-column work in v1.
- Localisation — current strings are English; FR translation (#670) tracks separately.
- The semantic-capability evolution of the shortcut schema (`"capability": "EFFECT_BUBBLE"` decoupling) — future work, not in this redesign.

## 13. Glossary

- **Command Surface** — the rebuilt Control tab: persistent Now Playing anchor + segmented pager of Master / Grab / Fixtures / Shows.
- **Anchor** — the persistent Now Playing block above the pager.
- **Page** — one of the four pager surfaces (formerly "cards" in v1).
- **Quick Grab** — the Grab page's horizontal mover row.
- **Shortcut** — a profile-derived single-tap or segmented control on a Fixtures page card.
- **Sheet** — full-screen panel revealed by "More controls →", long-press menu, or takeover.
- **Bloom** — soft outer glow on active or pressed elements (Kinetic Prism §3.1).
- **Anchor / Pager / Sheet / Overlay** — the four surface types in the mobile shell.

---

**Implementation status (post-v1.8.2):** Stages 1–4 implemented and shipped on Android (`android-v1.8.0` through `android-v1.8.2`). Stage 5 (iOS) pivoted from Compose Multiplatform to a minimal native SwiftUI shell — see `apple_developer_setup.md` (TestFlight workflow) + `testflight_install_guide.md` + `ios/README.md`.
