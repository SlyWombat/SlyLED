# SlyLED iOS Parity Spec — native SwiftUI port of Android v1.8.2 Command Surface

**Status:** v2 — operator-resolved 2026-05-14, full parity jump (no incremental staging).
**Issue:** [#888](https://github.com/SlyWombat/SlyLED/issues/888) (parent: design doc operator-approved 2026-05-12; Android stages 1–4 shipped in v1.8.0–v1.8.2; iOS pivoted to native SwiftUI per the doc footer).
**Author:** 2026-05-14.
**Scope:** turn the v0.1.0 iOS TestFlight shell into a fully operator-capable app that matches Android v1.8.2 surface-for-surface — **single TestFlight tag**, not the staged 0.2 → 0.7 sequence originally proposed.

### Revision history
- **v1** (2026-05-14): initial draft, 6-stage TestFlight build sequence v0.2 → v0.7.
- **v2** (2026-05-14): operator decisions folded (§16). Staged build sequence dropped — full parity ships as a single `ios-v0.7.0` tag. iOS playback-capture source removed (Option 1 — mic-only); §9.3 updated. Bonjour discovery deferred to v0.7 polish window. AppIcon/AccentColor placeholder through v0.7. iPhone-only confirmed.

This document is a port spec, not a UX redesign. The information architecture, page composition, shortcut renderer, haptics catalogue, resilience model, and safety actions are all already specified in [`mobile_ui_redesign.md`](mobile_ui_redesign.md). This spec only covers **how those decisions land in native SwiftUI**: file layout, framework choices, the REST/UDP/audio shim layer, persistence schema, staged TestFlight build sequence, and the open questions the operator must resolve before any iOS code lands beyond v0.2.

When this spec and `mobile_ui_redesign.md` disagree, the design doc wins — flag the discrepancy here, do not silently re-decide it.

---

## 1. Intent

The iOS app today is a v0.1.0 "TestFlight pipeline shake-down" — six Swift files, ~411 LOC, one master-brightness slider, STOP button, long-press blackout, settings sheet, connection pill. Everything else from the Android v1.8.2 Command Surface is missing.

The deliverable for parity is:

- 3-tab bottom nav (Stage / Control / Status) with Settings as a top-bar gear.
- Persistent NowPlayingAnchor + segmented pager of **Master / Grab / Fixtures / Shows**, exactly as specified in `mobile_ui_redesign.md` §4–§5.
- ControllerModeOverlay (mover gyro takeover) with claim → orient stream → calibrate hold → release.
- FixtureSheet, TakeoverSheet, AimWizardDialog.
- Auto Brightness via `AVAudioEngine` (mic + system playback capture) feeding the same envelope/beat math Android runs.
- UDP client over `Network.framework` carrying AUTOBRI_PUSH (cmd `0x6D` on UDP `4211`, 11-byte PDU) — that's the only UDP message the operator app sends today.
- Connection state machine (Connected → Degraded → Disconnected), haptics catalogue, per-card error states, page-level safety buttons — all per `mobile_ui_redesign.md` §6.
- Kinetic Prism colour + type tokens applied via SwiftUI `Color`/`Font` extensions; Space Grotesk + Inter bundled and registered in `Info.plist`.

---

## 2. Current state — iOS v0.1.0 baseline

| File | LOC | Purpose | Reusable as-is? |
|------|-----|---------|-----------------|
| `SlyLEDApp.swift` | 21 | App entry, `@StateObject` graph (ConnectionState + OrchestratorClient) | Yes — extend in v0.2 to inject more view-models |
| `ContentView.swift` | 152 | Single-screen Master slider + STOP + long-press blackout + top toolbar | Replace body with `RootShell` (TabView); reuse the toolbar haptic helpers |
| `Networking/OrchestratorClient.swift` | 81 | `URLSession` + JSON; 4 endpoints (`/status`, `/api/settings`, `/api/brightness`, `/api/show/stop`) | Yes — extend with the full endpoint catalogue (§7) |
| `Networking/ConnectionState.swift` | 54 | 1 Hz `/status` poll, three-state machine, manual retry | Yes — add write-queue policy (§12) |
| `UI/ConnectionPillView.swift` | 52 | Top-bar pill, pulse animation | Yes — only colour tokens need swapping to Kinetic Prism |
| `UI/SettingsSheet.swift` | 51 | Host + port form, About section | Keep as the Settings *server* section; full SettingsScreen lands in v0.4 |
| `project.yml` | 105 | XcodeGen spec, iOS 17.0 deployment target, ARM64, portrait-only iPhone | No change required for v0.2; revisit if iPad ever becomes a target |
| `Info.plist` | 79 | Bonjour `_slyled._tcp`, mic + camera + local-network usage strings | Already declares mic permission for Auto Brightness; no further keys needed for v0.2 |

The shell's three architectural choices are sound and we keep all three:

1. **`@MainActor` `ObservableObject` view models with `@Published` state** (Combine pattern). Mirrors Android's `StateFlow` model. No need for TCA / Redux / Composable Architecture for this app's complexity.
2. **`URLSession.shared.data(for:)` + `JSONDecoder`** for REST. No Alamofire, no networking library.
3. **`UserDefaults` for persistence.** Direct parity with Android `ServerPreferences` (DataStore). Key schema in §11.

---

## 3. Strategy decision — native SwiftUI, not Compose Multiplatform

**Recap.** `mobile_ui_redesign.md` §7.1 originally chose Compose Multiplatform with ad-hoc distribution. The footer (post-v1.8.2) records the actual pivot:

> Stage 5 (iOS) pivoted from Compose Multiplatform to a minimal native SwiftUI shell — see `apple_developer_setup.md` (TestFlight workflow) + `testflight_install_guide.md` + `ios/README.md`.

That pivot is taken as given for this spec. The reasons documented across `ios/README.md`, the `#888` v1.8.1 comment, and the doc footer:

- **TestFlight, not ad-hoc.** The operator runs a `macos-14` GitHub Actions runner; tagged builds upload via `xcrun altool`. There's no Mac in the operator's hands, so the CMP iOS scaffold's "open Xcode and archive locally" loop never works.
- **The Swift shell already exists** and ships green through CI. Replacing it with a Kotlin/Native CMP target means rebuilding both the CI pipeline and the on-device install path.
- **A native client buys us first-class `AVAudioEngine`, `CoreMotion`, `Network.framework`, and `CoreHaptics`** without the Kotlin/Native interop layer or the CMP-iOS frame-pacing risk the design doc explicitly named (§7.4).

**Trade-off accepted:** the UI surface area now exists in two languages. Every new control on Android must be re-implemented in Swift, and the shortcut renderer must be re-implemented from `FixtureShortcuts.kt` (Kotlin) to a Swift twin. We mitigate this by:

- **Locking the shortcut catalogue contract** in `mobile_ui_redesign.md` §5.3.1–§5.3.2 (already done; the table is canonical).
- **Sharing a snapshot-test corpus** of "profile JSON → expected shortcut list" between Pytest (SPA), JUnit (Android), and XCTest (iOS). The corpus is the contract; the three implementations must round-trip it identically.

If the dual-codebase tax later proves intolerable, we re-open the CMP option as a follow-up issue — but only after iOS reaches v0.6 (parity), so we never block parity on a refactor.

---

## 4. Proposed iOS module / file layout

Native SwiftUI app, no Swift packages, no SPM dependencies beyond the platform. Everything ships in the single `SlyLED.app` target.

```
ios/SlyLED/
  SlyLEDApp.swift                 ← app entry; injects all view models
  RootShell.swift                 ← NEW — 3-tab TabView shell + top toolbar
  Info.plist                      ← already wired for mic + camera + local-net
  Assets.xcassets/                ← AppIcon, AccentColor (Kinetic Prism CyanSecondary)

  Networking/
    OrchestratorClient.swift      ← extend: every endpoint in §7
    ConnectionState.swift         ← extend: write-queue policy (§12)
    UdpClient.swift               ← NEW — Network.framework UDP, AUTOBRI_PUSH only

  Audio/
    MicAutoBrightness.swift       ← NEW — AVAudioEngine tap, envelope+beat
    EnvelopeFollower.swift        ← NEW — 250Hz biquad LPF + RMS + smoother
    BeatDetector.swift            ← NEW — adaptive threshold beat tracker
    AudioSourceKind.swift         ← NEW — enum: mic / playback / off

  Persistence/
    ServerPreferences.swift       ← NEW — UserDefaults schema (§11)

  Haptics/
    Haptics.swift                 ← extend: full catalogue (§10); use CoreHaptics
                                    for waveform events

  Theme/
    KineticPrism.swift            ← NEW — Color + Font tokens
    Fonts/                        ← Space Grotesk + Inter OTF/TTF assets

  UI/
    ConnectionPillView.swift      ← keep; swap colour tokens to KineticPrism
    Pages/
      MasterPage.swift            ← NEW
      GrabPage.swift              ← NEW
      FixturesPage.swift          ← NEW
      ShowsPage.swift             ← NEW
    Components/
      NowPlayingAnchor.swift      ← NEW
      MoverChip.swift             ← NEW
      ShortcutControls.swift      ← NEW — toggle / segmented / momentary / etc.
      FixtureShortcuts.swift      ← NEW — Swift twin of FixtureShortcuts.kt
    Overlays/
      ControllerModeOverlay.swift ← NEW — CMMotionManager gyro stream
      FixtureSheet.swift          ← NEW — full per-channel sheet
      TakeoverSheet.swift         ← NEW — claim-conflict confirmation
      AimWizardSheet.swift        ← NEW — three-step quaternion capture
    Screens/
      LiveStageScreen.swift       ← NEW — stage canvas
      StatusScreen.swift          ← NEW
      SettingsScreen.swift        ← extend SettingsSheet.swift; full sections in v0.4

  ViewModels/
    ControlViewModel.swift        ← NEW — fixtures, profiles, live state, claims
    LiveStageViewModel.swift      ← NEW
    StatusViewModel.swift         ← NEW
    SettingsViewModel.swift       ← NEW — Auto Brightness toggle, audio src, etc.
```

The structure deliberately mirrors `android/app/src/main/java/com/slywombat/slyled/` so a contributor looking at both can move between them by translating package names. Naming convention: Android's `FooScreen` → iOS `FooScreen.swift`; Android's `FooPage` → iOS `FooPage.swift`; Android's `Foo.kt` data class → iOS `Foo.swift` `struct`.

---

## 5. Surface port map — view by view

For each Android surface, this section names the SwiftUI counterpart, the StateFlow→@Published bindings, and the REST / UDP / haptic mapping. Behaviour is canonical in `mobile_ui_redesign.md` and the Android implementation; this table is the iOS-side delta.

### 5.1 Root shell + top bar

| Android | iOS |
|---------|-----|
| `MainActivity` + `Navigation.kt` 3-tab bottom nav | `RootShell.swift` — `TabView` with `.tabBar` style; tabs: Stage / Control / Status |
| Top bar: logo (long-press blackout) · pill · ⚙ gear | `.toolbar` on each tab's `NavigationStack`: `ToolbarItem(.navigationBarLeading)` = logo with `.onLongPressGesture(minimumDuration: 0.6)`; `.principal` = `ConnectionPillView`; `.navigationBarTrailing` = ⚙ → `SettingsScreen` sheet |
| Long-press → blackout (`master = 0` via `POST /api/settings`) | Same call; `Haptics.fire(.heavyDouble)`, red bloom on logo for 200 ms |
| Settings gear → SettingsScreen | `.sheet(isPresented:)` presenting `SettingsScreen` |

The 3-tab layout in `mobile_ui_redesign.md` §4.1 puts the page-level segmented pager **inside** the Control tab. Other tabs (Stage, Status) are full-screen content with no pager.

### 5.2 Control tab — NowPlayingAnchor + 4-page pager

| Android | iOS |
|---------|-----|
| `ControlScreen.kt` — `Column { NowPlayingAnchor; SegmentedRow; HorizontalPager }` | `ControlScreen.swift` — `VStack { NowPlayingAnchor; PageSegmentBar; TabView(.page) }` (use `.tabViewStyle(.page(indexDisplayMode: .never))` so swipe gestures work; segments are the user-facing index) |
| Cold-start page = Master | `@State private var page: ControlPage = .master`; pages enum `.master / .grab / .fixtures / .shows` |
| Swipe + segment-tap both switch pages | `TabView(selection: $page)` handles swipe; segment-tap sets `page` with `withAnimation(.easeInOut(duration: 0.2))` |
| Pulse animation when playing (0.5 Hz on anchor border) | `.animation(.easeInOut(duration: 1).repeatForever(autoreverses: true), value: isPlaying)` on a border opacity |

### 5.3 NowPlayingAnchor

| Android | iOS |
|---------|-----|
| Idle: 40 dp single-line "No show running" | `VStack { Text("No show running") }.frame(height: 40)`; `.onTapGesture { page = .shows; Haptics.fire(.softTick) }` |
| Playing: 96 dp with name, loop chip, MM:SS, ProgressView, STOP button, optional Next | `VStack` with `Text(timeline.name)` + `LoopChip` + `MonoTime(elapsed, duration)` + `ProgressView(value: elapsed, total: duration)` + `StopButton` + (`NextButton` if `showStatus.totalTimelines > 1`) |
| STOP → `stopTimeline + stopShow` parallel; heavy haptic | `Task { async let a = client.stopTimeline(id); async let b = client.stopShow(); _ = try? await (a, b) }`; `Haptics.fire(.heavyThud)` |
| Next → `POST /api/show/next`; success haptic | `client.nextShow()`; `Haptics.fire(.successTick)` |
| Polling: `GET /api/timelines/{id}/status` + `GET /api/show/status` every 500 ms when running | `ControlViewModel.startNowPlayingPoll()` Task: 500 ms while running, 2 s while idle, cancel on view disappear |

### 5.4 Master page

| Android | iOS |
|---------|-----|
| Brightness slider 0–255 + ±5 % buttons | `Slider(value: $brightness, in: 0...255)` with `.onEditingChanged` posting `client.setBrightness(Int(brightness))`; ±5 % buttons step by 13 (already present in v0.1.0 `ContentView.swift`) |
| Auto Brightness switch + source picker + envelope meter | `Toggle("Auto Brightness", isOn: $autoBright)`; `Picker("Source", selection: $audioSource)` segmented style with Mic / Playback / Off; horizontal envelope meter via `GeometryReader + Rectangle().frame(width: ...)` updated from `MicAutoBrightness.envelope` |
| Permission gate (RECORD_AUDIO) | iOS `AVCaptureDevice.requestAccess(for: .audio)`; system mic dialog appears on first toggle-on; on grant, start `MicAutoBrightness.start()` |
| Source: PLAYBACK_CAPTURE (Android MediaProjection) | iOS analog = `AVAudioSession`-tapped output buffer (limitations in §9.3) |

### 5.5 Grab page

| Android | iOS |
|---------|-----|
| Top: favourites LazyRow (88×104 dp chips, star icon) | `ScrollView(.horizontal) { LazyHStack { ForEach(favourites) { MoverChip(...) } } }` with frame `88×104` |
| Below: full list of movers, last-grabbed first | `LazyVStack { ForEach(movers.sorted(by: lastGrabbedDesc)) { MoverRow(...) } }` |
| `MoverChip`: 56 dp colour swatch + radial pan/tilt arrow + name + claim badge | `ZStack { Circle().fill(rgb).frame(56); Arrow(panDeg, tiltDeg) }`; `Text(name).lineLimit(1)`; claim badge as a small `Capsule().fill(.purple.opacity(0.4))` when held by another |
| Tap → claim → `ControllerModeOverlay` | `Task { try await client.claim(moverId, force: false); presentController = true }`; on conflict (`error contains " by "`) set `pendingTakeover` |
| Long-press → mini-menu (Flash / Send home / Blackout / Unfavourite) | `.contextMenu { Button("Flash") { ... }; Button("Send to home") { ... }; ... }`; each runs `POST /api/fixtures/{id}/dmx-test` with the right normalized payload |
| "Send all home" page-header button | Red button in the page's header `HStack`; `client.allMoversHome()` (`POST /api/mover-control/all-home`); `Haptics.fire(.heavyThud)` |
| `fixturesLive` polling: 500 ms running / 1500 ms idle | `ControlViewModel.startFixturesLivePoll(...)` Task; cancel/restart on tab switch |
| Claim-conflict detection | If the claim call's response message contains `" by "`, set `@State pendingTakeover = (id, name, holder)`; `.sheet` presenting `TakeoverSheet` |

### 5.6 Fixtures page (profile-driven shortcuts)

| Android | iOS |
|---------|-----|
| Vertical stack of non-mover DMX fixtures | `LazyVStack { ForEach(nonMoverFixtures) { FixtureCard(...) } }` |
| Per-fixture: title + shortcut row + "More controls →" link | `VStack(alignment: .leading) { Text(name); ShortcutRow(shortcuts); Button("More controls →") { sheet = .channels(id) } }` |
| `FixtureShortcuts.kt` three-pass resolver | `FixtureShortcuts.swift`: pure function `resolveShortcuts(profile: Profile) -> [ResolvedShortcut]`. Shared snapshot-test corpus with Pytest + JUnit |
| Shortcut widgets: TOGGLE / SEGMENTED / COLOR_SWATCH / MOMENTARY / LONG_PRESS | One SwiftUI `View` per shortcut kind in `Components/ShortcutControls.swift` |
| Writes via `POST /api/fixtures/{id}/channel-write` | Same endpoint; payload `{"writes": {"<offset>": byte}}` |
| Momentary (strobe): press = open→strobe-band value; release = open band | `.onLongPressGesture(minimumDuration: 0, maximumDistance: .infinity, perform: {}, onPressingChanged: { pressing in ... })`; `Haptics.fire(.lowRumble)` while held |
| Long-press (clean-mode): 1500 ms with progress feedback | Custom timer `@State var holdProgress: Double`; on tick advance; on 1.0 fire write + `.heavyThud` |
| "Stop all effects" page-header button | `client.killStrobes()` + `client.killEffects()` in parallel via `async let`; `.heavyThud` |

### 5.7 Shows page

| Android | iOS |
|---------|-----|
| Ranked sections: Starred → Recent (≤7 d) → All others | `List { Section("Starred") { ... }; Section("Recent") { ... }; Section("All") { ... } }` |
| Tap row → start timeline | `client.startTimeline(id)`; `Haptics.fire(.successTick)` |
| Long-press → context menu (Star / Loop / Add to playlist) | `.contextMenu { ... }`; persist starred + lastPlayedAt in `ServerPreferences` |
| Playlist footer | Collapsible `DisclosureGroup` at bottom with "Start playlist" button → `POST /api/show/start` |

### 5.8 ControllerModeOverlay (gyro mover takeover)

| Android | iOS |
|---------|-----|
| `SensorManager.TYPE_ROTATION_VECTOR` quaternion stream, 50 ms throttle | `CMMotionManager.deviceMotionUpdateInterval = 0.05`; `startDeviceMotionUpdates(using: .xArbitraryZVertical)` → `CMQuaternion` |
| Orient POST: `POST /api/mover-control/orient {moverId, deviceId, roll, pitch, yaw, quat: [w,x,y,z]}` | Same endpoint; convert `CMQuaternion(x:y:z:w:)` → `[w, x, y, z]` (note ordering) |
| Hold-to-calibrate (large button, 100 ms lift debounce) | Custom button view with `.onLongPressGesture` + a 100 ms debounce `Task.sleep` on lift; while held, suspend orient sends and run `calibrate-start` → on lift `calibrate-end` |
| Aim wizard axes (`forwardLocal`, `upLocal`) | Loaded from `ServerPreferences` on overlay open; if stored, republish via `POST /api/remotes/grip` before stream starts |
| Color picker: HSV wheel + dimmer slider | SwiftUI `Canvas` HSV wheel (compute RGB on touch position) + `Slider`; fire `client.moverColor(id, r, g, b, dimmer)` |
| Flash press-and-hold | `.onLongPressGesture` press / release → `client.setFlash(id, on: true/false)` |
| Release on dismiss | `.onDisappear { Task { try? await client.release(id) } }` |
| Status poll: `GET /api/mover-control/status` every 2 s | `ControlViewModel.startClaimStatusPoll(id)` Task; cancel on release |

### 5.9 FixtureSheet (full channel sheet)

| Android | iOS |
|---------|-----|
| Full-screen panel with per-channel slider + labelled capability regions | `.fullScreenCover` presenting `FixtureSheet` (NavigationStack + Form with one `Section` per channel) |
| Slider 0–255 with capability label band | `Slider` + a horizontal capability legend below the slider; highlight current band |
| `onValueChangeFinished` → `channelWrite(fixId, [offset: byte])` | `.onEditingChanged: { if !$0 { post(...) } }`; `Haptics.fire(.softTick)` |

### 5.10 TakeoverSheet

| Android | iOS |
|---------|-----|
| `AlertDialog` "Held by X — Take over?" | `.confirmationDialog` or `.alert` with Confirm / Cancel |
| Confirm → `claim(force: true)` then open ControllerModeOverlay | Same; `Haptics.fire(.heavyThud)` on confirm |

### 5.11 AimWizardSheet (gyro aim-axis calibration)

| Android | iOS |
|---------|-----|
| 3-step modal: Neutral → Pitch Forward → Yaw Left | `NavigationStack` modal with `@State step: 0 / 1 / 2`, instruction text + CAPTURE button per step |
| `SensorManager.getQuaternionFromVector` on each CAPTURE | `CMMotionManager.deviceMotion?.attitude.quaternion`; store as `[w, x, y, z]` |
| Submit: `POST /api/remotes/aim-wizard {deviceId, poses: [{role, quat}, ...]}` | Same endpoint; on success store `forwardLocal` / `upLocal` in `ServerPreferences` |
| Server response: `{ok, forwardLocal, upLocal}` or `{ok: false, err, detail}` | Decode to `AimWizardResult` struct; show success / error toast |

### 5.12 LiveStage tab

| Android | iOS |
|---------|-----|
| Full-screen canvas of stage layout with live colour + pan/tilt arrows per fixture | SwiftUI `Canvas` view (`Canvas { ctx, size in ... }`), draws fixture circles + arrows; supports `MagnificationGesture` + `DragGesture` for pan/zoom |
| Tap fixture → info card | Hit-test inside `Canvas.onTapGesture(coordinateSpace:)`; show bottom-anchored `VStack` with name + live colour + claim state |
| `fixturesLive` poll: 500 ms / 1500 ms | `LiveStageViewModel.startPoll()` Task |
| Auto Brightness envelope + beat overlay button | Optional bottom-right floating toggle replicating Master-page Auto Brightness toggle (read-only here per design doc) |

### 5.13 Status tab

| Android | iOS |
|---------|-----|
| List of children (LED performers, DMX bridges, camera nodes) with health pill + RSSI + uptime | SwiftUI `List` of rows; each row = `ChildRow(child)`; uses `GET /api/children` polled at 5 s |
| Pull-to-refresh | `.refreshable { await viewModel.refresh() }`; calls `POST /api/children/refresh` |
| Tap row → detail | `NavigationLink` to `ChildDetailScreen` |

### 5.14 Settings screen

The v0.1.0 `SettingsSheet.swift` is the *server config* section; the full SettingsScreen lands in v0.4 with these additional sections (per the Android audit §G.3):

| Section | Notes |
|---------|-------|
| App Settings | Device name, Units (m / ft), Theme, Logging |
| Server Config | (already in v0.1.0 sheet — keep, move into the larger screen) |
| Stage Bounds | W / H / D mm — info-only on iOS (editing is SPA-only) |
| Auto Brightness Calibration | Sensitivity / Floor / Ceiling / Attack / Release sliders (Auto Brightness *toggle* lives on Master, *calibration* stays here) |
| Aim Wizard | Button → presents `AimWizardSheet` |
| Import / Export | iOS share sheet via `UIActivityViewController` / `ShareLink` for export; `.fileImporter` for import |
| Factory Reset | Wipes `ServerPreferences`; confirm via `.confirmationDialog` |

---

## 6. Discrepancy with `mobile_ui_redesign.md` §7.1 (Compose Multiplatform)

`mobile_ui_redesign.md` §7.1 still reads:

> Distribution path: **paid Apple Developer account, ad-hoc provisioning for testing soon, App Store later.** [...] **Implementation:** **Compose Multiplatform (Option A)** — the v1 recommendation now stands without the distribution caveat.

The doc footer (post-v1.8.2) records the actual pivot to TestFlight + native SwiftUI but §7.1's body was never updated.

**Action item for the operator:** sign off on this spec, then I'll update `mobile_ui_redesign.md` §7.1 + §7.3 + §7.4 in a single doc-fix commit to reflect the TestFlight + native SwiftUI decision so the design doc and the port spec stop disagreeing.

No iOS code lands until that doc-fix is in.

---

## 7. REST client expansion — `OrchestratorClient.swift`

The v0.1.0 client has 4 endpoints (`/status`, `/api/settings`, `/api/brightness`, `/api/show/stop`). Parity needs the full Android catalogue. Add the following methods on `OrchestratorClient`, organised by surface:

| Surface | Method | HTTP | Body / Query |
|---------|--------|------|--------------|
| Anchor | `nextShow()` | `POST /api/show/next` | — |
| Anchor | `startTimeline(id)` | `POST /api/timelines/{id}/start` | — |
| Anchor | `stopTimeline(id)` | `POST /api/timelines/{id}/stop` | — |
| Anchor | `timelineStatus(id)` | `GET /api/timelines/{id}/status` | — |
| Anchor | `showStatus()` | `GET /api/show/status` | — |
| Master | `setBrightness(value)` | `POST /api/brightness` | `{value: 0...255}` (already present) |
| Master | `getSettings()` | `GET /api/settings` | (already present) |
| Master | `postSettings(globalBrightness, ...)` | `POST /api/settings` | `{globalBrightness, ...}` |
| Grab | `fixtures()` | `GET /api/fixtures` | — |
| Grab | `fixturesLive()` | `GET /api/fixtures/live` | — |
| Grab | `claim(moverId, force)` | `POST /api/mover-control/claim` | `{moverId, deviceId, deviceName, deviceType: "ios", force?: true}` |
| Grab | `release(moverId)` | `POST /api/mover-control/release` | `{moverId, deviceId}` |
| Grab | `orient(moverId, roll, pitch, yaw, quat)` | `POST /api/mover-control/orient` | `{moverId, deviceId, roll, pitch, yaw, quat: [w,x,y,z]}` |
| Grab | `moverColor(moverId, r, g, b, dimmer)` | `POST /api/mover-control/color` | `{moverId, deviceId, r, g, b, dimmer}` |
| Grab | `setFlash(moverId, on)` | `POST /api/mover-control/flash` | `{moverId, deviceId, on: Bool}` |
| Grab | `calibrateStart(moverId, roll, pitch, yaw)` | `POST /api/mover-control/calibrate-start` | `{moverId, deviceId, roll, pitch, yaw}` |
| Grab | `calibrateEnd(moverId, roll, pitch, yaw, quat)` | `POST /api/mover-control/calibrate-end` | `{moverId, deviceId, roll, pitch, yaw, quat: [w,x,y,z]}` |
| Grab | `moverControlStatus()` | `GET /api/mover-control/status` | — |
| Grab | `allMoversHome()` | `POST /api/mover-control/all-home` | — |
| Grab | `dmxTest(fixId, normalized)` | `POST /api/fixtures/{id}/dmx-test` | `{pan?, tilt?, dimmer?}` (0...1) |
| Fixtures | `profile(id)` | `GET /api/dmx-profiles/{id}` | — |
| Fixtures | `channelWrite(fixId, writes)` | `POST /api/fixtures/{id}/channel-write` | `{writes: ["<offset>": Int]}` |
| Fixtures | `killStrobes()` | `POST /api/fixtures/kill-strobes` | — |
| Fixtures | `killEffects()` | `POST /api/fixtures/kill-effects` | — |
| Shows | `timelines()` | `GET /api/timelines` | — |
| Shows | `playlist()` | `GET /api/show/playlist` | — |
| Shows | `startShow()` | `POST /api/show/start` | — |
| Shows | `stopShow()` | `POST /api/show/stop` | (already present) |
| Status | `children()` | `GET /api/children` | — |
| Status | `childrenRefresh()` | `POST /api/children/refresh` | — |
| Settings | `aimWizard(poses)` | `POST /api/remotes/aim-wizard` | `{deviceId, poses: [{role, quat}, ...]}` |
| Settings | `grip(forwardLocal, upLocal)` | `POST /api/remotes/grip` | `{deviceId, forwardLocal, upLocal}` |
| Settings | `remoteDisconnect()` | `POST /api/remotes/disconnect` | `{deviceId}` |

Strategy:

- Keep `get<T: Decodable>(_:)` and `postJSON(_:body:)` as the two primitives.
- Add `postJSONDecoding<T: Decodable, B: Encodable>(_ path: String, body: B) async throws -> T` for endpoints that return useful payloads (e.g. `aimWizard`).
- Replace the `[String: Any]` argument shape with `Encodable` structs (`ClaimRequest`, `OrientRequest`, `ColorRequest`, etc.) so payload shape is compile-checked.
- Every method `throws` and returns `Void` or a decoded model. No `Result` types — `try?` at call sites in view models when failure must be silent (live polls).

**`deviceId` source:** generate once on first launch via `UUID().uuidString`, persist in `UserDefaults` under key `device_id`. On `app launch` re-read; never regenerate.

---

## 8. UDP client — `UdpClient.swift`

iOS doesn't have Android's `DatagramSocket`. Use `Network.framework` `NWConnection` with `.udp` and `.ipv4`.

```swift
final class UdpClient {
    private var connection: NWConnection?
    private let host: NWEndpoint.Host
    private let port: NWEndpoint.Port  // 4211 for AUTOBRI_PUSH

    func sendAutoBrightnessPush(master: UInt8, flags: UInt8, seq: UInt8, epoch: UInt32) {
        var pdu = Data(capacity: 11)
        pdu.append(0x4C); pdu.append(0x53)               // magic LE 0x534C
        pdu.append(0x05)                                  // version
        pdu.append(0x6D)                                  // cmd AUTOBRI_PUSH
        withUnsafeBytes(of: epoch.littleEndian) { pdu.append(contentsOf: $0) }
        pdu.append(master); pdu.append(flags); pdu.append(seq)
        ensureConnection()
        connection?.send(content: pdu, completion: .idempotent)
    }
}
```

**Constraints:**
- The operator app sends **only** `AUTOBRI_PUSH` over UDP today. Every other path uses HTTP REST. Don't speculatively implement other PDUs.
- `NWConnection` lifecycle: lazy-init on first send; recreate on host/port change; tear down `onDisappear` from the Auto-Brightness owner.
- Fire-and-forget: ignore send errors. Log at `os_log` `.error` if `connection.state == .failed`.
- iOS may require `NWParameters` with `prohibitedInterfaceTypes = [.cellular]` to keep UDP on Wi-Fi only — confirm against `apple_developer_setup.md` once a real device is in hand.

---

## 9. Auto Brightness — `MicAutoBrightness.swift`

### 9.1 Audio capture (AVAudioEngine)

```swift
let engine = AVAudioEngine()
let input  = engine.inputNode
let format = input.outputFormat(forBus: 0)
input.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
    self.process(buffer: buffer)
}
try engine.start()
```

- `AVAudioSession.sharedInstance().setCategory(.playAndRecord, mode: .measurement, options: [.mixWithOthers, .allowBluetooth])` so the mic doesn't take audio focus and other apps can play music through the phone simultaneously.
- Request mic permission via `AVCaptureDevice.requestAccess(for: .audio)` on first toggle-on.
- Mic sample rate is whatever the device picks (typically 44.1 / 48 kHz); resample to **8 kHz mono** inside `process(buffer:)` before envelope math so the math matches Android byte-for-byte.

### 9.2 Envelope + beat math

Direct port from `EnvelopeFollower.kt` and `BeatDetector.kt`. The constants (250 Hz LPF, 50 ms hop, attack 8 ms / release 220 ms, baseline EMA α = 0.02, beat threshold 0.012, 250 ms refractory, 40–220 BPM clamp) are part of the contract — do not retune.

**Test:** add a shared corpus of WAV clips in `tests/fixtures/auto_brightness/` and a snapshot test that runs each clip through the Swift implementation and through the Kotlin one; compare envelope traces to within ±1 in 0–255.

### 9.3 Playback capture limitation

iOS does **not** offer the Android equivalent of `AudioPlaybackCaptureConfiguration` (i.e. capturing system audio). Three options for the "Playback" source:

1. **Drop the source from the iOS picker.** Show only "Microphone" and "Off" on iOS. The mic-on-phone use case (phone on the booth picking up FOH) still works; only the on-device-streaming-Spotify case is lost.
2. **AVAudioSession route capture.** Tap the *output* node of an app like Apple Music played through the *phone's own speakers* — works only for the in-app player, not for system-wide playback. Rejected: limited usefulness.
3. **AirPlay receiver mode.** Phone advertises as an AirPlay 2 receiver, captures the audio stream. Big iOS plumbing lift; out of scope for v0.4.

**Decision (v2):** Option 1 — mic-only on iOS. The audio-source picker on iOS shows **Microphone** and **Off**; "Playback" and "USB Audio" / "Remote Submix" are Android-only. Documented in the operator manual. AirPlay-receiver mode re-opens as a separate issue only if real-world use demands it.

### 9.4 Push transport

The Android impl sends the master byte via UDP `AUTOBRI_PUSH` (cmd 0x6D, port 4211, 11-byte PDU) at ~20 Hz. iOS does the same via `UdpClient.sendAutoBrightnessPush(...)`.

`flags` byte layout (matches Android): bit 0 = beat-pulse-on, bit 1 = mic-gated, bits 2–7 reserved. `seq` is a per-session monotonic `UInt8` that wraps.

---

## 10. Haptics — `Haptics.swift`

The v0.1.0 `heavyHaptic(_:)` function covers light / soft / heavy / heavyDouble. Extend to the full catalogue from `mobile_ui_redesign.md` §6.3 and the Android audit §E.1:

| Event | Android | iOS implementation |
|-------|---------|--------------------|
| `lightTick` | 15 ms amp 90 | `UIImpactFeedbackGenerator(style: .light).impactOccurred()` |
| `softTick` | 8 ms amp 60 | `UIImpactFeedbackGenerator(style: .soft).impactOccurred()` |
| `successTick` (medium tick) | 30 ms amp 200 | `UIImpactFeedbackGenerator(style: .medium).impactOccurred()` |
| `heavyThud` | 80 ms full amp | `UIImpactFeedbackGenerator(style: .heavy).impactOccurred()` |
| `heavyDouble` (panic blackout) | 80/50/80 waveform | `.heavy` + 130 ms delay + `.heavy` (already in v0.1.0) |
| `noGoBump` (degraded tap) | 20/60/20 waveform | `UINotificationFeedbackGenerator().notificationOccurred(.error)` |
| `lowRumble` (strobe momentary) | 60 ms amp 70 continuous | `CHHapticEngine` continuous pattern at intensity 0.5, sharpness 0.3, duration = press duration; bail to `.light` if engine fails |
| `profileError` (single sharp) | single sharp tick | `UIImpactFeedbackGenerator(style: .rigid).impactOccurred()` |
| `sliderStep` (per-5 % boundary) | per-step soft | `UISelectionFeedbackGenerator().selectionChanged()` |

`Haptics` becomes a `struct` with static methods; `UISelectionFeedbackGenerator()` instances are cached on `@MainActor` so they're prepared before use.

`CoreHaptics` (CHHapticEngine) is only needed for `lowRumble` (continuous pattern). Wrap in `do/catch`; on unsupported devices (iPhone 7 and earlier), degrade to a `.light` tick fired at 100 ms intervals.

---

## 11. Persistence — `ServerPreferences.swift`

Direct UserDefaults port of Android's `ServerPreferences`. Same keys, same types — operator's `lastPlayed` / `starred` / favourites bookmarks remain meaningful across re-installs of the *same* app, but **do not migrate between Android and iOS** (different device, different operator preferences in practice).

| UserDefaults key | Type | Default | Use |
|------------------|------|---------|-----|
| `host` | String | "" | Orchestrator host (already in v0.1.0) |
| `port` | Int | 8080 | Orchestrator port (already in v0.1.0) |
| `device_id` | String | `UUID().uuidString` on first read | Stable claim identity |
| `user_pos_x_mm` / `user_pos_y_mm` / `user_pos_z_mm` | Double | 0 / 0 / 1700 | Operator stage position |
| `auto_brightness_enabled` | Bool | false | Feature toggle |
| `auto_brightness_sensitivity` | Double | 1.5 | Envelope gain |
| `auto_brightness_floor` | Double | 0.05 | Output floor |
| `auto_brightness_ceiling` | Double | 1.0 | Output ceiling |
| `auto_brightness_attack_ms` | Double | 8 | Attack time constant |
| `auto_brightness_release_ms` | Double | 220 | Release time constant |
| `auto_brightness_audio_source_kind` | String | "MICROPHONE" | Enum name |
| `favourite_movers_csv` | String | "" | "1,5,7" — comma-sep fixture IDs |
| `starred_timelines_csv` | String | "" | comma-sep timeline IDs |
| `last_played_map` | String | "" | "tid=ts,tid=ts" |
| `aim_wizard_forward_x/y/z` | Double | 0 | Calibrated body-frame axes |
| `aim_wizard_up_x/y/z` | Double | 0 | — |
| `aim_wizard_completed_at` | String | "" | ISO-8601 timestamp |

Wrap behind a `final class ServerPreferences: ObservableObject` with `@Published` properties so view models can subscribe. Save on `didSet`.

---

## 12. Connection state machine — extend `ConnectionState.swift`

The v0.1.0 machine handles state transitions; parity adds the **write-queue policy** from `mobile_ui_redesign.md` §6.1:

- **Connected:** writes fire immediately.
- **Degraded:** writes go into a FIFO queue; on transition back to Connected within 5 s the queue is flushed in order. On Degraded→Disconnected transition the queue is dropped and the user gets a `noGoBump` once.
- **Disconnected:** writes are dropped immediately with `noGoBump`.

Implementation: `ConnectionState` exposes `func enqueueOrFire(_ work: () async throws -> Void) async throws` which the view models call instead of awaiting the client directly. Polling cadence stays 1 Hz.

---

## 13. Theme — Kinetic Prism in SwiftUI

Tokens from `mobile_ui_redesign.md` §8 land as:

```swift
// Theme/KineticPrism.swift
extension Color {
    static let deepSlate      = Color(red: 0.06, green: 0.07, blue: 0.10)
    static let darkSlate      = Color(red: 0.10, green: 0.11, blue: 0.14)
    static let darkNavy       = Color(red: 0.07, green: 0.10, blue: 0.16)
    static let mutedSlate     = Color(red: 0.20, green: 0.22, blue: 0.27)
    static let lightSlate     = Color(red: 0.62, green: 0.65, blue: 0.70)
    static let nearWhite      = Color(red: 0.92, green: 0.94, blue: 0.96)
    static let bluePrimary    = Color(red: 0.20, green: 0.46, blue: 0.96)
    static let luminaBlue     = Color(red: 0.30, green: 0.62, blue: 1.00)
    static let cyanSecondary  = Color(red: 0.20, green: 0.86, blue: 0.94)
    static let dmxPurple      = Color(red: 0.62, green: 0.40, blue: 0.92)
    static let greenOnline    = Color(red: 0.32, green: 0.82, blue: 0.45)
    static let orangeWled     = Color(red: 0.96, green: 0.62, blue: 0.18)
    static let redError       = Color(red: 0.93, green: 0.30, blue: 0.35)
}
```

(Pull the exact hex values from `android/app/src/main/java/com/slywombat/slyled/ui/theme/Theme.kt` to ensure byte-for-byte match.)

**Type ramp:**

- Bundle `SpaceGrotesk-Bold.otf`, `SpaceGrotesk-SemiBold.otf`, `Inter-Regular.otf`, `Inter-Medium.otf` in `Theme/Fonts/`.
- Register via `Info.plist` `UIAppFonts` array.
- Expose `Font.display`, `.title`, `.body`, `.label`, `.mono` as static extensions matching the design doc §8 table.

**Bloom:** SwiftUI doesn't have a built-in glow. Implement as `View.shadow(color: tint.opacity(0.5), radius: 12, x: 0, y: 0)` applied on `isPressed` / `isActive`. Encapsulate in a `.bloom(_ tint: Color, active: Bool)` view modifier.

---

## 14. TestFlight build — single-tag jump (v2 decision)

Operator overrode the v1 staged sequence: ship a single `ios-v0.7.0` tag carrying full Android v1.8.2 parity. No incremental TestFlight tags between v0.1 and v0.7.

| Tag | Scope | Acceptance |
|-----|-------|------------|
| `ios-v0.1.0` | (shipped) Master slider + STOP + connection pill + Settings sheet | Operator can install via TestFlight; pill turns green |
| **`ios-v0.7.0`** | **Full parity** — RootShell + NowPlayingAnchor + Master/Grab/Fixtures/Shows pages + ControllerModeOverlay + FixtureSheet + TakeoverSheet + AimWizardSheet + LiveStage + Status + full Settings + UdpClient + MicAutoBrightness + Haptics catalogue + ConnectionState write-queue + KineticPrism tokens | Operator validates parity on the rig side-by-side with Android v1.8.2 |

Bonjour-discovered host picker, custom AppIcon/AccentColor branding, and any operator-found field bugs land in a follow-up `ios-v0.7.1` tag — they are post-parity polish, not parity-blocking.

CI pipeline (`.github/workflows/ios-testflight.yml`) is unchanged: macos-15 / Xcode 26 / iOS 26 SDK / iOS 17.0 deployment target. Tagging `ios-v0.7.0` triggers archive + ExportArchive (App Store IPA) + `xcrun altool` upload. Apple Beta App Review processes 15 min – 24 hr after upload for the first build of the new `MARKETING_VERSION`.

---

## 15. Test strategy

| Layer | Tool | Coverage |
|-------|------|----------|
| Shortcut renderer | XCTest + shared JSON corpus (Pytest / JUnit / XCTest read the same fixtures from `tests/fixtures/profiles/`) | Every built-in + community profile produces expected shortcut list, identical across platforms |
| Auto Brightness envelope/beat | XCTest + shared WAV corpus in `tests/fixtures/auto_brightness/` | Per-clip envelope trace within ±1/255 of Kotlin output |
| Connection state machine | XCTest | All state transitions on PONG loss/restore, write-queue flush + drop |
| View models | XCTest with mock `OrchestratorClient` | Per-page state population, error toasts, claim conflict flow |
| Snapshot tests for screens | SwiftUI `Preview` snapshots via `swift-snapshot-testing` (optional add) | Each page state (idle / running / claimed / degraded / disconnected / error / conflict / profile-error) |
| End-to-end | Manual on real iPhone + rig | Per TestFlight release; operator runs the live test |

No XCUITest. The Android side accepted screenshot-only mobile testing per `mobile_ui_redesign.md` §10; iOS does the same.

---

## 16. Decisions (resolved 2026-05-14)

1. **iOS audio source — mic-only.** Picker shows Microphone + Off. Playback / Remote Submix / USB are Android-only. AirPlay-receiver mode is not pursued.
2. **`mobile_ui_redesign.md` §7.1 doc fix lands first.** A single doc commit replaces the Compose Multiplatform + ad-hoc paragraphs with native SwiftUI + TestFlight, cross-linking this spec. Done before any iOS code.
3. **Single-tag jump.** Skip the v0.2 → v0.6 staged sequence; ship full parity as `ios-v0.7.0`.
4. **Bonjour host-picker deferred** to a follow-up `ios-v0.7.1` polish tag. v0.7 keeps manual host-entry from v0.1.
5. **AppIcon + AccentColor** stay on the v0.1 placeholder through v0.7. Real iconography lands with the v1.0 App Store decision.
6. **iPhone-only confirmed through v0.7.** Portrait-only iPhone. iPad form factor is not a v1 target.

---

## 17. Risks

- **AVAudioEngine mic capture interferes with phone calls / Bluetooth audio.** Mitigation: `.measurement` mode + `.mixWithOthers` option; abort engine on `AVAudioSession.routeChangeNotification` if the new route is unsupported.
- **CMMotionManager rate-limit on background.** When the app backgrounds during a show, sensor updates pause and the orient stream stalls. Mitigation: show a "Live, keep screen on" guidance; on `scenePhase == .background` send `release(moverId)` so the orchestrator releases the claim instead of holding a stale one.
- **`Network.framework` UDP loss on locked screen.** iOS may suspend `NWConnection` while the screen is locked. Auto Brightness only matters while the operator is *using* the phone, so this is acceptable — but verify on hardware.
- **Snapshot drift on the shortcut corpus.** When the SPA or Android renderer changes (new shortcut kind, new heuristic), the iOS twin must update in lockstep or the corpus snapshot fails. Mitigation: the corpus PR must update all three implementations in one commit.
- **TestFlight 90-day expiry.** Same risk Android sideload doesn't have. Mitigation: ship a refresh tag (`ios-v0.2.0-r2` style) on the existing version every ~75 days. Already documented in `ios/README.md`.

---

## 18. Out of scope

- Compose Multiplatform refactor — re-open as a separate issue post-v0.7 if dual-codebase tax becomes intolerable.
- AirPlay-receiver mode for system audio capture — see §9.3 / open question 1.
- iPad layouts — see open question 6.
- App Store public release — TestFlight is the v0.7 distribution path; App Store is post-v1.0.
- Watch / CarPlay companion surfaces — never.

---

## 19. Cross-references

- [`mobile_ui_redesign.md`](mobile_ui_redesign.md) — canonical IA / page composition / shortcut catalogue / haptics map / resilience model / safety actions.
- [`apple_developer_setup.md`](apple_developer_setup.md) — operator-side Apple Developer + TestFlight workflow.
- [`apple_secrets_setup.md`](apple_secrets_setup.md) — GitHub Secrets for the CI signing pipeline.
- [`testflight_install_guide.md`](testflight_install_guide.md) — tester-side install steps.
- [`ios/README.md`](../../ios/README.md) — repo-level iOS quickstart and roadmap.
- [`#888`](https://github.com/SlyWombat/SlyLED/issues/888) — parent design issue (Android stages 1–4 shipped; iOS remains open).
