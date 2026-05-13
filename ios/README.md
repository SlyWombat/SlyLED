# SlyLED iOS — first TestFlight build

This directory holds the Swift sources for the **v0.1.0 iOS shell**. Goal: validate the TestFlight pipeline end-to-end without committing to a full UI port yet. Subsequent builds fill in the Master / Grab / Fixtures / Shows pages from the design doc.

**What's in v0.1.0:**
- Connection state pill (green/orange/red) with retry on tap
- Server settings (host + port, persisted in UserDefaults)
- Master brightness slider with ±5% steppers
- STOP SHOW button
- Long-press app title for instant blackout (master → 0)

**What's NOT in v0.1.0:**
- Stage / Grab / Fixtures / Shows pages (subsequent builds)
- Auto Brightness (microphone permission wired in `Info.plist` for v0.2)
- Mover controller mode
- Gyro / orient streaming

---

## You don't need a Mac

iOS builds are produced by GitHub Actions on a `macos-14` runner. The operator stays on Windows / WSL; a tag push triggers a cloud build that uploads to TestFlight automatically.

The chicken-and-egg "you need a Mac to make an `.xcodeproj`" problem is solved by **XcodeGen** — the runner generates the Xcode project from `ios/project.yml` at build time. The repo never tracks the fragile `.pbxproj` XML.

---

## End-to-end flow

```
Operator (Windows)                      GitHub Actions (cloud Mac)        Apple
──────────────────                      ──────────────────────────        ─────
1. Apple Developer enrolment ─────────────────────────────────────────────► ✓
2. Bundle ID ─────────────────────────────────────────────────────────────► ✓
3. App Store Connect record ──────────────────────────────────────────────► ✓
4. OpenSSL CSR → Apple Distribution cert ─────────────────────────────────► ✓
5. App Store Connect API key downloaded ──────────────────────────────────► ✓
6. Six GitHub Secrets entered
7. git tag ios-v0.1.0 + push  ─────►  XcodeGen ► xcodebuild archive ─────►  TestFlight processing
                                       ► xcrun altool upload
8. Tester invite email arrives  ◄─────────────────────────────────────────  Beta App Review OK
9. Install via TestFlight app on iPhone
```

Steps 1–6 are one-time setup. After that every release is just `git tag ios-vX.Y.Z && git push`.

---

## Setup checklist for the operator

In order:

1. **Apple Developer Program enrolment** ($99 USD/yr). See `docs/design/apple_developer_setup.md` Step 1.
2. **Bundle ID** `ca.electricrv.slyled`. Apple Dev portal. See Step 2.
3. **App Store Connect record** for SlyLED iOS. See Step 4.
4. **Distribution cert + API key + GitHub Secrets.** Full step-by-step in [`docs/design/apple_secrets_setup.md`](apple_secrets_setup.md). All done from Windows + Apple web portals — no Mac.
5. **Add yourself as an internal tester.** App Store Connect → SlyLED → TestFlight → Internal Testing → `+`. See Step 8.

Once those are done:

```bash
cd /mnt/d/SlyLED
git tag ios-v0.1.0
git push origin ios-v0.1.0
```

Watch the build at <https://github.com/SlyWombat/SlyLED/actions> (workflow: **iOS TestFlight build**). It takes ~8–15 minutes. When it finishes green, App Store Connect shows the build "Processing" (~30 min) → "Waiting for Review" (first build of each new MARKETING_VERSION) → "Ready to Test" (15 min – 24 hr after upload, Apple's automated review).

When status hits "Ready to Test", testers get the invite email. They follow [`docs/design/testflight_install_guide.md`](testflight_install_guide.md) to install on their iPhone.

---

## Files in this directory

```
ios/
  README.md               — this file
  project.yml             — XcodeGen project spec (the runner uses this)
  SlyLED/
    SlyLEDApp.swift       — app entry point
    ContentView.swift     — root view (brightness + STOP + long-press blackout)
    Info.plist            — bundle metadata, privacy strings, Bonjour services
    Networking/
      OrchestratorClient.swift   — minimal REST client (4 endpoints)
      ConnectionState.swift      — Connected / Degraded / Disconnected machine
    UI/
      ConnectionPillView.swift   — top-bar link state widget
      SettingsSheet.swift        — server config + version info
```

The runner regenerates `SlyLED.xcodeproj` from `project.yml` on every build; that file is gitignored.

---

## Subsequent builds (the easy version)

For routine TestFlight refreshes:

1. Bump `MARKETING_VERSION` in `ios/project.yml` (e.g. `0.1.0` → `0.2.0`).
2. Commit + tag + push:
   ```bash
   git commit -am "ios: bump to v0.2.0 — Master/Grab pages"
   git tag ios-v0.2.0
   git push origin main
   git push origin ios-v0.2.0
   ```
3. Wait for the green workflow run.

The build number (`CFBundleVersion`) increments automatically from the GitHub Actions run id — no manual management.

For a TestFlight refresh without a `MARKETING_VERSION` bump (e.g., expiring 90-day build), tag with a `-rNN` suffix:

```bash
git tag ios-v0.1.0-r2
git push origin ios-v0.1.0-r2
```

The workflow fires on any `ios-v*` tag. No App Review since `MARKETING_VERSION` is unchanged.

---

## What happens after v0.1.0 ships

Once the operator confirms a tester can install SlyLED v0.1.0 from TestFlight and the connection pill turns green, the pipeline is proven. Roadmap:

- **v0.2** — full Compose Multiplatform refactor of the Android UI, so Master / Grab / Fixtures / Shows ship to both platforms from a single codebase. The Swift shell becomes the iOS host for the CMP entry point.
- **v0.3** — Auto Brightness via `AVAudioEngine` (iOS analog to Android's `MicAutoBrightness`).
- **v0.4** — Mover controller mode via `CMMotionManager` (iOS analog to Android's gyro streaming).
- **v0.5** — UDP socket via `Network.framework` for direct-to-orchestrator paths.

Each ships as a TestFlight build via the same `git tag ios-v*` flow.

---

## Building locally on a Mac (optional, not required)

If you ever get Mac access and want to iterate without waiting for CI:

```bash
brew install xcodegen
cd ios/
xcodegen generate
open SlyLED.xcodeproj
```

Xcode opens with the generated project. Standard Run / Archive flow from there. Local archives can be uploaded via Xcode Organizer or `xcrun altool` with the same API key the CI uses.
