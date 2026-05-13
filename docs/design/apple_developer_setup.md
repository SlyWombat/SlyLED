# Apple Developer Setup — TestFlight Distribution for SlyLED iOS

**Audience:** the SlyLED operator. This is operator-side pre-work — Claude cannot do these steps because they require your Apple ID, a credit card, and access to your iPhone for testing.

> **No Mac required.** Once Steps 1–5 below are done, the iOS build runs on a cloud Mac via GitHub Actions on every `ios-v*` tag push. See [`apple_secrets_setup.md`](apple_secrets_setup.md) for the OpenSSL-on-Windows path to generate the signing cert, the App Store Connect API key, and the six GitHub Secrets the workflow needs. Step 3 (install Xcode) is **optional** — only needed if you ever get Mac access and want to iterate locally.

**Outcome:** ability to upload signed builds of the SlyLED iOS app to **Apple TestFlight**, where you (and up to 100 internal testers / 10,000 external testers per build) install it without UDID registration. Builds expire 90 days after upload — re-upload to refresh.

**Why TestFlight (not ad-hoc):**
- No UDID registration. Testers don't need to send you their device IDs. They just need an invite email or a public link.
- Auto-updates. New uploads push to testers' phones automatically through the TestFlight app.
- Operator-friendly install for non-developers. Tester taps a link, taps Install — done.
- Free at this scale (the $99/yr Developer Program covers everything; no per-build cost).

**Tradeoffs vs ad-hoc:**
- **First build of each version** goes through Apple's automated "Beta App Review" — usually 15 minutes, occasionally 24 hours. Subsequent builds of the *same* version (e.g., build #2, #3 of `1.8.2`) are instant. Bumping `versionName` re-triggers review.
- **90-day expiry.** Each TestFlight build is valid for 90 days from upload. Re-upload a fresh build within that window.
- **Apple ID region quirks.** Testers in different App Store regions can hit edge cases; if a tester sees "this app is not available in your country," they may need a US-region Apple ID.

The free 7-day Xcode-only provisioning path is **not** viable for a real testing rhythm — the apps expire weekly and require Xcode-attached install each time.

---

## Step 1 — Enroll in the Apple Developer Program

1. Go to <https://developer.apple.com/programs/>
2. Click **Enroll** (top-right).
3. Sign in with the Apple ID you want to associate with the developer account. **This Apple ID is permanent for code-signing identity** — pick one that won't change (`dave@drscapital.com` is fine; the SlyLED project will live under it for years).
4. Choose **Individual** enrolment (Organization adds 1–2 weeks for D-U-N-S verification; Individual is instant).
5. Pay the **$99 USD/yr** fee. Charged once at enrolment; auto-renews annually.
6. Wait for the confirmation email (usually within 24 hours; often within minutes).

Once active, you'll see your name in <https://developer.apple.com/account/> and can access **Certificates, Identifiers & Profiles** and **App Store Connect**.

---

## Step 2 — Create the Bundle ID

The iOS bundle ID is **`ca.electricrv.slyled`** (mirrors the Android `applicationId = "com.slywombat.slyled"` but registered under the company TLD).

1. Go to <https://developer.apple.com/account/resources/identifiers/list>
2. Click the **`+`** beside *Identifiers*.
3. Select **App IDs**, then **App**.
4. **Description:** `SlyLED Operator`
5. **Bundle ID:** Explicit → `ca.electricrv.slyled`
6. **Capabilities:** check only what's needed:
   - **Background Modes** (for the UDP socket while screen-off so the connection pill keeps polling).
   - **Microphone** (Auto Brightness via `AVAudioEngine`).
   - Leave everything else **unchecked**.
7. **Continue → Register**.

---

## Step 3 — Install Xcode on a Mac

iOS builds require macOS. Options:
- A Mac you own / borrow (cleanest).
- A macOS VM or cloud-mac service (MacStadium, MacInCloud).
- A dedicated Mac mini for CI ($600 hardware, pays back if you ship iOS regularly).

Once you have a Mac:
1. Install **Xcode 16** or newer from the Mac App Store (free, ~10 GB).
2. First launch: accept the license, let it install additional components (~10 minutes).
3. Open Xcode → **Settings → Accounts** → **`+`** → sign in with the Apple ID from Step 1.
4. Click **Manage Certificates…** to verify Xcode picks up your Developer Program membership.

---

## Step 4 — Create the App Store Connect record

App Store Connect is where TestFlight builds live. You register an "app record" once; every TestFlight build attaches to it.

1. Go to <https://appstoreconnect.apple.com/>
2. Sign in with the Apple ID from Step 1.
3. **My Apps → `+` → New App**.
4. **Platform:** iOS.
5. **Name:** `SlyLED` (this is the App Store display name, max 30 chars).
6. **Primary Language:** English (Canada).
7. **Bundle ID:** select `ca.electricrv.slyled` from Step 2.
8. **SKU:** `slyled-ios` (any unique internal id; not shown to users).
9. **User Access:** Full Access.
10. **Create**.

You're done with App Store Connect setup. You're **not** submitting to the App Store — you'll never click "Submit for Review" on the App Store side; only TestFlight is in play.

---

## Step 5 — Generate the Apple Distribution certificate

This is the cert that signs the `.ipa` you upload to TestFlight.

**Xcode-managed (recommended):**
1. In Xcode → **Settings → Accounts → [your account] → Manage Certificates…**
2. Click the **`+`** in the bottom-left → **Apple Distribution**.
3. Xcode generates the cert + private key in your Keychain. Done.

**Backup the cert.** Open **Keychain Access** → find "Apple Distribution: [your name]" → right-click → **Export** → save as `.p12` with a strong password. Store this `.p12` somewhere safe (encrypted backup drive, password manager). **If you lose it and your Mac dies, you'll need to revoke and re-issue the cert** — old uploaded TestFlight builds remain valid until they expire naturally; future uploads need the new cert.

You do NOT need to create a provisioning profile manually. Xcode auto-manages a "Provisioning Profile (App Store)" profile for TestFlight uploads.

---

## Step 6 — Build and upload the first TestFlight build

**Prerequisite:** the SlyLED iOS source must exist as a buildable Xcode project. Claude will scaffold the Compose Multiplatform iOS module — see `ios/` directory in the repo once that lands. Build steps below assume the scaffolding is in place.

### Option A — Xcode UI (recommended for the first build):

1. Open the iOS project in Xcode (`ios/SlyLED.xcodeproj` or similar — confirm path once the CMP scaffold lands).
2. Select **Any iOS Device (arm64)** in the device dropdown (NOT a simulator).
3. **Product → Archive**. Xcode compiles + signs + packages the `.ipa`.
4. The Organizer window opens automatically. Select the new archive.
5. **Distribute App → App Store Connect → Upload → Next** through the dialogs (leave defaults; Xcode auto-manages signing).
6. Upload takes 2–10 minutes depending on network.

### Option B — `xcodebuild` CLI (for automated builds):

```bash
cd ios/
xcodebuild -scheme SlyLED -archivePath build/SlyLED.xcarchive \
           -destination "generic/platform=iOS" archive
xcodebuild -exportArchive -archivePath build/SlyLED.xcarchive \
           -exportOptionsPlist ExportOptions.plist \
           -exportPath build/ipa/
xcrun altool --upload-app -f build/ipa/SlyLED.ipa -t ios \
             -u <your-apple-id> -p <app-specific-password>
```

The `app-specific-password` is generated at <https://appleid.apple.com/account/manage> → **Sign-In and Security** → **App-Specific Passwords**.

---

## Step 7 — Wait for Beta App Review

After the upload completes:
1. Go to <https://appstoreconnect.apple.com/> → My Apps → SlyLED → **TestFlight** tab.
2. The new build appears with **"Processing"** status (15–30 minutes).
3. Once processed, it moves to **"Waiting for Review"** (only on the FIRST build of each new `versionName`).
4. Apple's automated review usually takes 15 minutes to 24 hours.
5. Once approved, status becomes **"Ready to Test"**.

For subsequent builds with the *same* `versionName` (different build number), review is automatic + instant.

---

## Step 8 — Add internal testers

**Internal testers** = members of your Apple Developer team. Up to 100. No App Review required for them; they get builds the moment they finish processing.

1. App Store Connect → SlyLED → TestFlight → **Internal Testing** (left sidebar).
2. Click **`+`** beside *Testers*.
3. Add tester Apple IDs. Each tester gets an email invite.

For yourself: you're already on the team, so just add your own Apple ID as an internal tester.

**External testers** (up to 10,000, requires Beta App Review per build) — skip for now; internal is enough for the operator's testing.

---

## Step 9 — Tester installs

Send testers the install guide at `docs/design/testflight_install_guide.md`. It's written for a novice iPhone user and walks through:
1. Accept the email invite.
2. Install Apple's TestFlight app from the App Store.
3. Open the invite link / redeem code.
4. Tap Install — done.

Updates push automatically as long as TestFlight is open at least once a week.

---

## Step 10 — Re-upload before expiry

TestFlight builds expire **90 days** after upload. To refresh:
1. Bump the iOS build number (in Xcode: Project → General → Identity → Build).
2. Archive + upload (same process as Step 6).
3. New build replaces the old one for testers; auto-update fires.

If the `versionName` is unchanged, no Beta App Review.

Set a calendar reminder for day 75 to upload a refresh well before day 90.

---

## Step 11 — When the cert expires

| Asset | Lifespan | Renewal action |
|-------|----------|----------------|
| Apple Developer Program membership | 1 year | Auto-renews, $99 USD. Missing the renewal revokes the cert. |
| Apple Distribution certificate | 1 year | Re-generate via Step 5. Old uploaded TestFlight builds keep working until 90-day expiry; new uploads need the new cert. |
| TestFlight build | 90 days | Re-upload (Step 10). |
| Bundle ID | doesn't expire | — |
| App Store Connect record | doesn't expire | — |

Set calendar reminders for the membership renewal (60 days out) and TestFlight refresh (15 days out).

---

## What Claude can do once the CMP iOS scaffold lands

Once Claude has built out `ios/` with a buildable Xcode project (next session, with operator on macOS to verify), Claude can:
- Generate the `ExportOptions.plist` for `xcodebuild -exportArchive`.
- Write a build script (`tools/ios_build.sh`) that does archive + export + upload in one command.
- Add a GitHub Actions workflow that builds on `macos-14` runners and uploads to TestFlight on every tagged release.
- Help debug Swift shim code (Haptics, AudioCapture, UdpSocket) once the operator can run it.

Until then, Claude cannot produce a working `.ipa` — no macOS in this environment.

---

## Quick reference — links

- **Enroll:** <https://developer.apple.com/programs/enroll/>
- **Account home:** <https://developer.apple.com/account/>
- **App Store Connect:** <https://appstoreconnect.apple.com/>
- **Identifiers (Bundle IDs):** <https://developer.apple.com/account/resources/identifiers/list>
- **Certificates:** <https://developer.apple.com/account/resources/certificates/list>
- **App-specific passwords:** <https://appleid.apple.com/account/manage>
- **Xcode download:** <https://apps.apple.com/us/app/xcode/id497799835>
- **Transporter app (alt to xcodebuild upload):** <https://apps.apple.com/us/app/transporter/id1450874784>
- **TestFlight overview:** <https://developer.apple.com/testflight/>
- **Beta App Review guidelines:** <https://developer.apple.com/app-store/review/guidelines/#beta>

---

## Status checklist

Tick as you complete:

- [ ] Step 1 — Enrolled in Apple Developer Program (Individual, $99/yr)
- [ ] Step 2 — Bundle ID `ca.electricrv.slyled` registered
- [ ] Step 3 — Xcode 16+ installed on a Mac, account added
- [ ] Step 4 — App Store Connect app record created (SlyLED iOS)
- [ ] Step 5 — Apple Distribution cert generated, `.p12` backed up
- [ ] Step 6 — First `.ipa` archived + uploaded *(deferred until CMP scaffold lands)*
- [ ] Step 7 — Beta App Review passed *(automatic after Step 6)*
- [ ] Step 8 — Internal testers added (count: ___ / 100)
- [ ] Step 9 — First tester install confirmed working
- [ ] Step 10 — Calendar reminder set for 75-day refresh upload
- [ ] Step 11 — Calendar reminder set for 305-day cert / membership renewal

Once Steps 1–5 are done, tell Claude to scaffold the iOS module and write the build script.
