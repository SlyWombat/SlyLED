# SlyLED iOS — v0.7.0 parity build

This directory holds the Swift sources for the SlyLED iOS operator app. As of `ios-v0.7.0` the iOS app reaches **functional parity with Android v1.8.2** and ships through TestFlight via GitHub Actions.

For the full port plan and surface-by-surface mapping, see [`docs/design/ios_parity_spec.md`](../docs/design/ios_parity_spec.md). For the underlying mobile UI design, see [`docs/design/mobile_ui_redesign.md`](../docs/design/mobile_ui_redesign.md).

## What's in v0.7.0

- **3-tab shell** (Stage / Control / Status) + top toolbar with logo long-press blackout, ConnectionPill, Settings gear.
- **NowPlayingAnchor** above the Control pager (idle 40 dp; playing 96 dp with progress + STOP + Next).
- **4-page Control pager**:
  - **Master** — brightness slider + ±5 % buttons + Auto Brightness toggle + live envelope / output meters.
  - **Grab** — moving-head favourite chips + full list, claim → controller takeover overlay, "Send all home" safety button.
  - **Fixtures** — profile-driven shortcut renderer (bubble / haze / fan / colour / UV / strobe / clean) + "More controls →" sheet + "Stop all effects" safety button.
  - **Shows** — Starred → Recent (7 d) → All sections, one-tap start, playlist footer.
- **ControllerModeOverlay** — `CMMotionManager` gyro stream at 50 Hz, hold-to-calibrate gesture, HSV wheel + dimmer, flash press-and-hold, release.
- **FixtureSheet** — full per-channel slider with capability labels, channel-write writes through `/api/fixtures/{id}/channel-write`.
- **TakeoverSheet** — claim-conflict confirmation; `force=true` retry on confirm.
- **AimWizardSheet** — three-step quaternion capture (`/api/remotes/aim-wizard`); derived axes persist to `UserDefaults`.
- **LiveStageScreen** — SwiftUI `Canvas` of fixture positions with live colour + pan/tilt arrows; pinch-zoom + drag-pan.
- **StatusScreen** — child list with online/offline pill + RSSI bars; pull-to-refresh.
- **SettingsScreen** — server config, Auto Brightness calibration (5 sliders), Aim Wizard launcher, factory reset.
- **`AVAudioEngine` Auto Brightness** — mic-only (iOS doesn't expose system playback capture, per [`ios_parity_spec.md`](../docs/design/ios_parity_spec.md) §9.3); envelope follower + beat detector ported byte-for-byte from Android's Kotlin; pushes the master byte over UDP `4211` via `Network.framework`.
- **Haptics catalogue** matching Android (`Haptics.swift`), with `CoreHaptics` for the strobe-press low rumble.
- **Connection state machine** — `Connected → Degraded → Disconnected` with a 5 s write-queue policy.

## Post-v0.7.0 (unreleased, on main)

- **Tracked-object rendering (B5, #912)** — `LiveStageScreen` polls `/api/objects` at the Android cadence (1.5 s) and draws temporal person footprints on the stage canvas: amber when radar-sourced (`source.type == "radar"`, matching the SPA and Android), pink otherwise. `StatusScreen` shows an amber RADAR chip for `MMW-*` radar nodes. Closes the objects-rendering gap vs Android v1.8.2 (Android renders tracked objects; iOS v0.7.0 did not). Not yet validated by a macOS CI build.

## Roadmap from here

| Tag | Scope |
|-----|-------|
| `ios-v0.7.1` | Polish: Bonjour-discovered host picker (`NWBrowser._slyled._tcp`) in SettingsScreen, custom AppIcon + AccentColor, Space Grotesk + Inter font bundle, any operator-found field bugs from `ios-v0.7.0` live test. |
| `ios-v0.8.x` | Iterate based on operator field feedback. |
| `ios-v1.0`   | App Store submission (post-TestFlight validation). |

## You don't need a Mac

iOS builds are produced by GitHub Actions on a `macos-15` runner with Xcode 26. Tagging a release triggers a cloud build that uploads to TestFlight automatically. The chicken-and-egg "you need a Mac to make an `.xcodeproj`" problem is solved by **XcodeGen** — the runner generates the Xcode project from `ios/project.yml` at build time.

## File layout (v0.7.0)

```
ios/
  README.md                       — this file
  project.yml                     — XcodeGen project spec (iOS 17.0, iPhone-only)
  SlyLED/
    SlyLEDApp.swift               — app entry, ObservableObject graph
    RootShell.swift               — 3-tab TabView + top toolbar
    Info.plist                    — bundle metadata, mic + Bonjour permission strings
    Theme/
      KineticPrism.swift          — Color + Font tokens (matches Android Theme.kt hex)
    Persistence/
      ServerPreferences.swift     — UserDefaults schema port
    Networking/
      OrchestratorClient.swift    — REST client, full Android endpoint catalogue
      ConnectionState.swift       — link state machine + write-queue policy
      UdpClient.swift             — Network.framework, AUTOBRI_PUSH only
      Models.swift                — Codable structs for every endpoint
    Audio/
      MicAutoBrightness.swift     — AVAudioEngine tap → envelope + beat → UDP
      EnvelopeFollower.swift      — 250 Hz LPF + RMS + asymmetric smoother
      BeatDetector.swift          — adaptive-threshold spectral flux
      AudioSourceKind.swift       — iOS supports microphone only
    Haptics/
      Haptics.swift               — UIImpactFeedbackGenerator + CoreHaptics catalogue
    ViewModels/
      ControlViewModel.swift
      LiveStageViewModel.swift
      StatusViewModel.swift
      SettingsViewModel.swift
    UI/
      ConnectionPillView.swift    — top-bar link state
      Pages/
        MasterPage.swift
        GrabPage.swift
        FixturesPage.swift
        ShowsPage.swift
      Components/
        NowPlayingAnchor.swift
        MoverChip.swift
        ShortcutControls.swift
        FixtureShortcuts.swift    — Swift twin of Android FixtureShortcuts.kt
      Overlays/
        ControllerModeOverlay.swift
        FixtureSheet.swift
        TakeoverSheet.swift
        AimWizardSheet.swift
      Screens/
        ControlScreen.swift
        LiveStageScreen.swift
        StatusScreen.swift
        SettingsScreen.swift
```

## Releasing

```bash
cd /mnt/d/SlyLED
git tag ios-v0.7.0
git push origin ios-v0.7.0
```

Watch the build at <https://github.com/SlyWombat/SlyLED/actions> (workflow: **iOS TestFlight build**). It takes ~8–15 minutes. When green, App Store Connect shows the build "Processing" (~30 min) → "Waiting for Review" (first build of each new `MARKETING_VERSION`) → "Ready to Test" (15 min – 24 hr after upload, Apple's automated review). Testers get an email when status hits "Ready to Test".

For routine TestFlight refreshes — bump `MARKETING_VERSION` in `project.yml`, commit, tag (e.g. `ios-v0.7.1`), push.

For a TestFlight refresh without a `MARKETING_VERSION` bump (e.g. expiring 90-day build), tag with a `-rNN` suffix:

```bash
git tag ios-v0.7.0-r2
git push origin ios-v0.7.0-r2
```

## Building locally on a Mac (optional, not required)

```bash
brew install xcodegen
cd ios/
xcodegen generate
open SlyLED.xcodeproj
```

Standard Run / Archive flow from there.
