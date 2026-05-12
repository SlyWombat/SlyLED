# SlyLED iOS — first TestFlight build

This directory holds the Swift sources for the **v0.1.0 iOS shell**. Goal: validate the TestFlight pipeline end-to-end without committing to a full UI port yet. Once a build successfully reaches your iPhone via TestFlight, subsequent builds will fill in the Master / Grab / Fixtures / Shows pages from the design doc.

**What's in v0.1.0:**
- Connection state pill (green/orange/red) with retry on tap
- Server settings (host + port, persisted in UserDefaults)
- Master brightness slider with ±5% steppers
- STOP SHOW button
- Long-press app title for instant blackout (master → 0)

**What's NOT in v0.1.0:**
- Stage / Grab / Fixtures / Shows pages (coming after the pipeline is proven)
- Auto Brightness (microphone permission is wired in `Info.plist` ready for v0.2)
- Mover controller mode
- Gyro / orient streaming

---

## Prerequisites — operator-side

Complete Steps 1–5 of `docs/design/apple_developer_setup.md` first:
- Apple Developer Program enrolment ($99/yr)
- Bundle ID `ca.electricrv.slyled` registered
- App Store Connect record for SlyLED iOS
- Apple Distribution cert generated + backed up

You also need:
- A Mac running macOS 14 or newer
- Xcode 16 or newer installed
- This repo cloned to the Mac (or copied via Git / file share from your Windows machine)

---

## Step 1 — Create the Xcode project

The repo doesn't include a pre-generated `.xcodeproj` because Xcode project files are notoriously fragile to hand-author. You'll create one from a template and pull the Swift files in.

1. Open Xcode → **File → New → Project…**
2. Pick **iOS** tab → **App** → **Next**.
3. Fill in:
   - **Product Name:** `SlyLED`
   - **Team:** select your Apple Developer team (from Step 1 of the Apple setup doc)
   - **Organization Identifier:** `ca.electricrv` — Xcode auto-combines this with the product name to form bundle id `ca.electricrv.SlyLED`. **You must change the bundle ID to lowercase `ca.electricrv.slyled`** (matches Step 2 of the Apple setup doc). Do this under **Signing & Capabilities → Bundle Identifier**.
   - **Interface:** SwiftUI
   - **Language:** Swift
   - **Storage:** None
   - **Include Tests:** unchecked
4. **Next** → save the project somewhere convenient. **Do NOT save it inside the `slyled/ios/` directory.** Save it to `slyled/ios/SlyLEDXcode/` (a sibling folder) — that way the repo's Swift sources stay clean.

---

## Step 2 — Replace the template files with the repo's Swift sources

Xcode created `SlyLEDApp.swift` and `ContentView.swift` for you. Replace them:

1. In the Xcode project navigator (left sidebar), **delete** the template's `SlyLEDApp.swift` and `ContentView.swift` (right-click → **Delete** → **Move to Trash**).
2. In Finder, navigate to this repo's `ios/SlyLED/` directory. You'll see:
   ```
   SlyLEDApp.swift
   ContentView.swift
   Info.plist
   Networking/
     OrchestratorClient.swift
     ConnectionState.swift
   UI/
     ConnectionPillView.swift
     SettingsSheet.swift
   ```
3. **Drag the entire `SlyLED/` folder** from Finder into the Xcode project navigator (drop it onto the yellow "SlyLED" group at the top).
4. In the dialog that pops up:
   - **Copy items if needed:** UNCHECKED (we want the Swift files to stay in the repo, not get duplicated into the Xcode project folder).
   - **Create groups:** checked.
   - **Add to targets:** **SlyLED** checked.
5. Click **Finish**.

---

## Step 3 — Wire the Info.plist

Xcode's modern templates use generated Info.plist values from build settings. We need to switch to the repo's hand-written Info.plist:

1. In the project navigator, select the top-level **SlyLED** project (the blue icon).
2. Select the **SlyLED** target → **Build Settings** tab.
3. Search for **"info.plist file"**.
4. Set **Info.plist File** to the path of the repo's Info.plist relative to the project: `../ios/SlyLED/Info.plist` (or wherever the relative path lands — Xcode shows a tooltip).
5. Search for **"Generate Info.plist File"** and set it to **No**.

Alternative path if the relative path is finicky: copy `ios/SlyLED/Info.plist` into the Xcode project folder and let Xcode reference it locally. (Updates won't sync back to the repo automatically — that's fine for v0.1.)

---

## Step 4 — Verify signing

1. Select the **SlyLED** target → **Signing & Capabilities** tab.
2. **Automatically manage signing:** checked.
3. **Team:** your Developer Program team.
4. **Bundle Identifier:** `ca.electricrv.slyled` (lowercase — verify).
5. Xcode shows "Signing Certificate: Apple Development: [your name]". For the TestFlight upload it'll switch to Distribution automatically when you archive.

Capabilities to add (click **+ Capability**):
- **Background Modes** → check **Audio, AirPlay, and Picture in Picture** (so future Auto Brightness mic capture survives screen-lock).

---

## Step 5 — Build for the simulator first (smoke test)

Before archiving for TestFlight, confirm the app even compiles + runs:

1. In the device dropdown next to the Run button (▶), pick **iPhone 15** or any simulator.
2. Hit **▶** (or **⌘R**).
3. Wait for the simulator to boot. SlyLED launches with the dark UI, "Offline" pill (no server configured), and the brightness slider.
4. Tap the ⚙ gear → enter your dev machine's IP (e.g. `192.168.1.42`) + port `8080` → **Save & connect**.
5. The pill should turn green within a few seconds if the orchestrator is running on that IP and port.

If anything fails to compile, the error is most likely:
- **"Use of unresolved identifier"** — a Swift file didn't get added to the target. In the project navigator, click each `.swift` file and verify the **Target Membership** checkbox (right inspector) is checked for SlyLED.
- **"Cannot find type 'OrchestratorClient'"** — same issue; the `Networking/` group wasn't included.

---

## Step 6 — Archive for TestFlight

1. In the device dropdown, pick **Any iOS Device (arm64)** (NOT a simulator).
2. **Product → Archive**. Xcode compiles + signs + packages the `.ipa`. This takes 1–3 minutes.
3. When done, the Organizer window opens automatically.
4. Select your new archive (top of the list).
5. Click **Distribute App** (right side).
6. Pick **App Store Connect** → **Next**.
7. Pick **Upload** → **Next**.
8. Leave all sign options at default (Xcode auto-managed) → **Next**.
9. Click **Upload**. Takes 2–10 minutes depending on network.

When done, log into <https://appstoreconnect.apple.com/> → My Apps → SlyLED → **TestFlight** tab. The build appears with status **"Processing"** (15–30 min), then **"Waiting for Review"** (since this is the first build of v0.1.0), then **"Ready to Test"** (15 min to 24 hours later).

---

## Step 7 — Add yourself as an internal tester

1. App Store Connect → SlyLED → TestFlight → **Internal Testing**.
2. Click **+** beside *Testers* → add your Apple ID.
3. Apple emails you the invite (check spam if it doesn't arrive in 10 minutes).
4. On your iPhone: install **TestFlight** from the App Store, tap the invite link in the email, tap **Install**.

The novice install walkthrough at `docs/design/testflight_install_guide.md` covers the tester side in detail. Send that to anyone else you add as a tester.

---

## Build script (optional)

Once you've done Step 6 once via the GUI, you can automate subsequent builds:

```bash
#!/bin/bash
# tools/ios_build.sh — operator runs this on macOS to upload a fresh TestFlight build.
set -e
cd ios/SlyLEDXcode  # adjust to your path
xcodebuild -scheme SlyLED -archivePath build/SlyLED.xcarchive \
    -destination "generic/platform=iOS" \
    -allowProvisioningUpdates archive
xcodebuild -exportArchive -archivePath build/SlyLED.xcarchive \
    -exportOptionsPlist ../ExportOptions.plist \
    -exportPath build/ipa/
xcrun altool --upload-app -f build/ipa/SlyLED.ipa -t ios \
    -u "$APPLE_ID" -p "$APP_SPECIFIC_PASSWORD"
```

`ExportOptions.plist` template (also save under `ios/`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>method</key>
    <string>app-store</string>
    <key>signingStyle</key>
    <string>automatic</string>
    <key>teamID</key>
    <string>YOUR_TEAM_ID_HERE</string>
</dict>
</plist>
```

(Replace `YOUR_TEAM_ID_HERE` with your 10-character team ID from <https://developer.apple.com/account/> → Membership.)

---

## What happens after v0.1.0 ships

Once the operator confirms a tester can install SlyLED v0.1.0 from TestFlight and the connection pill turns green, the pipeline is proven. Next iterations:

- **v0.2** — full Compose Multiplatform refactor of the Android UI so the Master / Grab / Fixtures / Shows pages ship to both platforms from a single codebase. The Swift shell from v0.1 becomes the host for the CMP entry point.
- **v0.3** — Auto Brightness via `AVAudioEngine` + `AVAudioPlayerNode` (the iOS analog to Android's `MicAutoBrightness`).
- **v0.4** — gyro / orient streaming via `Network.framework` UDP sockets (the iOS analog to Android's `UdpClient`).

Each ships as a TestFlight build along the way; no App Store submission until the operator decides v1.0 is ready.
