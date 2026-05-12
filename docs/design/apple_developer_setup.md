# Apple Developer Setup — iOS Sideload (Ad-Hoc) for SlyLED

**Audience:** the SlyLED operator. This is operator-side pre-work — Claude cannot do these steps because they require your Apple ID, a credit card, and physical access to your iPhone for UDID registration.

**Outcome:** ability to build a signed `.ipa` and install it on registered iPhones for testing the SlyLED iOS app (the future Compose Multiplatform target tracked in [#888](https://github.com/SlyWombat/SlyLED/issues/888)).

**Why ad-hoc, not TestFlight:** TestFlight builds expire every 90 days, forcing a constant rebuild-and-redistribute dance. Ad-hoc provisioning lets you sideload an `.ipa` to up to **100 registered devices per year** with no expiry on the install (the *provisioning profile* expires after 1 year, at which point you re-sign and re-install). Decided in [`mobile_ui_redesign.md` §7.4](mobile_ui_redesign.md).

**Why not free provisioning (the 7-day Xcode-only path):** free provisioning works without enrolment but caps install duration at 7 days and limits you to apps installed via Xcode directly — which means the operator must have Xcode on the machine they want the phone connected to, every week. Not viable for a real testing rhythm.

---

## Step 1 — Enroll in the Apple Developer Program

1. Go to <https://developer.apple.com/programs/>
2. Click **Enroll** (top-right).
3. Sign in with the Apple ID you want to associate with the developer account. **This Apple ID will be permanent for code-signing identity** — pick one that won't change (`dave@drscapital.com` is fine).
4. Choose **Individual** enrolment (Organization adds 1–2 weeks for D-U-N-S verification; Individual is instant).
5. Pay the **$99 USD/yr** fee. Charged once at enrolment; auto-renews annually.
6. Wait for the confirmation email (usually within 24 hours; sometimes within minutes).

Once active, you'll see your name in <https://developer.apple.com/account/> and can access **Certificates, Identifiers & Profiles**.

---

## Step 2 — Create the Bundle ID

Per the SlyLED convention, the iOS bundle ID is **`ca.electricrv.slyled`** (mirrors the Android `applicationId = "com.slywombat.slyled"` but registered under the company TLD).

1. Go to <https://developer.apple.com/account/resources/identifiers/list>
2. Click the **`+`** button beside *Identifiers*.
3. Select **App IDs**, then **App**.
4. **Description:** `SlyLED Operator`
5. **Bundle ID:** Explicit → `ca.electricrv.slyled`
6. **Capabilities:** check only what's needed. For v1 of the iOS port:
   - **Background Modes** (for UDP socket while screen-off).
   - **Microphone** (Auto Brightness via `AVAudioEngine`).
   - **Network Extensions** — not needed; standard sockets are fine.
   - Leave **Push Notifications**, **HealthKit**, **HomeKit**, **iCloud**, **In-App Purchase**, **Sign in with Apple**, etc. **unchecked**.
7. **Continue → Register**.

The Bundle ID is now reserved against your account.

---

## Step 3 — Install Xcode (build host)

The build machine must run macOS. SlyLED's primary work tree is Windows, so the iOS build either runs:
- **On a Mac you own / borrow** (cleanest), or
- **In a macOS VM / cloud-mac service** (MacStadium, MacInCloud, GitHub Actions `macos-14`), or
- **On a Mac mini dedicated to CI** (cheapest long-term if you're shipping iOS regularly).

For this initial setup, assume you have a Mac available.

1. Install **Xcode 16** or newer from the Mac App Store (free).
2. First launch: accept the license, let it install additional components (~10 minutes).
3. Open Xcode → **Settings → Accounts** → **`+`** → sign in with the Apple ID from Step 1.
4. Select the account, click **Manage Certificates…** to verify it picks up your Developer Program membership.

---

## Step 4 — Generate the Apple Distribution certificate

This is the cert that signs the ad-hoc `.ipa`.

**Option A — Xcode-managed (recommended):**
1. In Xcode → **Settings → Accounts → [your account] → Manage Certificates…**
2. Click the **`+`** in the bottom-left → **Apple Distribution**.
3. Xcode generates the cert + private key in your Keychain. Done.

**Option B — Manual (web portal):**
1. Open **Keychain Access** on Mac → **Keychain Access menu → Certificate Assistant → Request a Certificate from a Certificate Authority**.
2. Enter your email (the Apple ID one), leave CA email blank, choose **Saved to disk**. Save the `.certSigningRequest` (CSR) file.
3. Go to <https://developer.apple.com/account/resources/certificates/list>
4. Click **`+`**, select **Apple Distribution**, continue.
5. Upload the CSR. Download the resulting `.cer`.
6. Double-click the `.cer` to install it into Keychain.

**Backup:** export the cert + private key from Keychain Access as a single `.p12` file (right-click the cert → **Export "Apple Distribution: …"** → choose `.p12` format, set a strong password). Store this `.p12` somewhere safe (encrypted backup drive, password manager). **If you lose it and the original Mac, you cannot re-sign builds without revoking and re-issuing the cert** — old `.ipa`s remain valid until the embedded provisioning profile expires.

---

## Step 5 — Register your iPhone's UDID

The ad-hoc provisioning profile must list every device permitted to install the `.ipa`.

1. Connect the iPhone to the Mac via USB.
2. Open **Finder** (macOS Catalina+) → select the iPhone in the sidebar.
3. Click the line under the device name (it cycles between Capacity / Phone Number / Model / Serial → keep clicking until **UDID** appears).
4. Right-click the UDID → **Copy**.

Alternatively, on the iPhone itself: open Safari, navigate to <https://udid.tech>, follow the prompt to install a temporary profile that reveals the UDID. Copy it.

Then register it:
1. Go to <https://developer.apple.com/account/resources/devices/list>
2. Click **`+`**.
3. **Platform:** iOS. **Device Name:** `Operator iPhone` (or whatever's meaningful). **Device ID (UDID):** paste.
4. **Continue → Register**.

**Limit:** 100 devices total per year across all platforms. You can't *remove* a device mid-year to free a slot — Apple resets the device list once per year on your Developer Program renewal date. Don't register devices casually.

Repeat for each additional test device (e.g. a second iPhone, an iPad).

---

## Step 6 — Create the ad-hoc provisioning profile

1. Go to <https://developer.apple.com/account/resources/profiles/list>
2. Click **`+`**.
3. **Distribution → Ad Hoc → Continue**.
4. **App ID:** select `ca.electricrv.slyled` (from Step 2). **Continue**.
5. **Certificates:** check the Apple Distribution cert from Step 4. **Continue**.
6. **Devices:** check every device that should be allowed to install this build. **Continue**.
7. **Provisioning Profile Name:** `SlyLED Ad-Hoc` (any descriptive name). **Generate**.
8. **Download** the resulting `.mobileprovision` file.
9. Double-click it on the Mac → it auto-imports into Xcode (`~/Library/MobileDevice/Provisioning Profiles/`).

**Expiry:** the profile is valid for **1 year** from issue. After expiry, re-generate (steps 6.3 onwards) — the cert stays, only the profile rotates.

---

## Step 7 — Wire signing into the Xcode project

This step is for the **future iOS project** once the Compose Multiplatform module is scaffolded. Documented here so it's ready when the code is.

When the iOS app target exists in `ios/SlyLED.xcodeproj`:
1. Open the project in Xcode.
2. Select the **SlyLED** target → **Signing & Capabilities** tab.
3. **Team:** select your Developer Program team.
4. **Bundle Identifier:** `ca.electricrv.slyled`.
5. **Provisioning Profile:** `SlyLED Ad-Hoc` (the one from Step 6).
6. **Signing Certificate:** Apple Distribution (from Step 4).
7. Build settings → Product → **Archive**.
8. In the Organizer that appears: **Distribute App → Ad Hoc → Next** → choose the profile → **Export** → save the `.ipa`.

---

## Step 8 — Install the `.ipa` on a registered iPhone

Three ways:

**A. Apple Configurator 2** (free Mac app):
1. Install from the Mac App Store.
2. Connect iPhone via USB.
3. Drag the `.ipa` onto the device icon in Configurator.

**B. Xcode → Window → Devices and Simulators**:
1. Connect iPhone via USB.
2. Select it in the device list.
3. Drag the `.ipa` into the **Installed Apps** section.

**C. iTunes / Finder** (older method; only works on macOS 10.15+ via Finder):
1. Connect iPhone via USB.
2. Open Finder → iPhone → **Files** tab → drag `.ipa`. (May not work on newer macOS — A or B are more reliable.)

The first launch on the iPhone may show **"Untrusted Developer"**. Resolve via:
- iPhone → **Settings → General → VPN & Device Management → [your developer cert]** → **Trust**.

---

## Step 9 — When the cert / profile expires

| Asset | Lifespan | Renewal action |
|-------|----------|----------------|
| Apple Developer Program membership | 1 year | Auto-renews, $99 USD again. If you miss the renewal, cert + profile are revoked. |
| Apple Distribution certificate | 1 year | Re-generate via Step 4. **Keep the old `.p12` backup so already-distributed apps with the old cert still verify until the device removes them.** |
| Ad-hoc provisioning profile | 1 year | Re-generate via Step 6. Re-sign and re-install the `.ipa` on each device. |

Set a calendar reminder 60 days before expiry — the renewal flow is fast (~10 min) but easy to forget until users hit "this app can no longer be used."

---

## What Claude can do once these steps are complete

Once **Step 6** is done (provisioning profile downloaded), Claude can:
- Scaffold the Compose Multiplatform iOS module in `ios/`.
- Configure the Xcode project's signing fields via `xcodebuild`-readable settings.
- Run `xcodebuild archive` + `xcodebuild -exportArchive` to produce the `.ipa` — provided the Mac build host is reachable from the work environment (SSH, or run the commands locally on the Mac).
- Help iterate on the iOS-specific shims (Haptics via `UIImpactFeedbackGenerator`, AudioCapture via `AVAudioEngine`, UdpSocket via `Network.framework`).

Until Step 6 is complete, Claude can write the Compose Multiplatform Kotlin code (the `expect` declarations and the shared UI) but cannot produce a working `.ipa` — there's nothing to sign with.

---

## Quick reference — links

- **Enroll:** <https://developer.apple.com/programs/enroll/>
- **Account home:** <https://developer.apple.com/account/>
- **Identifiers (Bundle IDs):** <https://developer.apple.com/account/resources/identifiers/list>
- **Devices (UDIDs):** <https://developer.apple.com/account/resources/devices/list>
- **Certificates:** <https://developer.apple.com/account/resources/certificates/list>
- **Profiles:** <https://developer.apple.com/account/resources/profiles/list>
- **Xcode download:** <https://apps.apple.com/us/app/xcode/id497799835>
- **Apple Configurator 2:** <https://apps.apple.com/us/app/apple-configurator-2/id1037126344>
- **Ad-hoc distribution overview (Apple docs):** <https://developer.apple.com/documentation/xcode/distributing-your-app-to-registered-devices>

---

**Status checklist** (tick as you complete):

- [ ] Step 1 — Enrolled in Apple Developer Program (Individual, $99)
- [ ] Step 2 — Bundle ID `ca.electricrv.slyled` registered
- [ ] Step 3 — Xcode 16+ installed, account added
- [ ] Step 4 — Apple Distribution cert generated, `.p12` backed up
- [ ] Step 5 — iPhone UDID(s) registered (count: ___ / 100)
- [ ] Step 6 — Ad-hoc provisioning profile generated + downloaded
- [ ] Step 7 — Xcode project signing wired (deferred until iOS code exists)
- [ ] Step 8 — First `.ipa` installed on a device (deferred)
- [ ] Step 9 — Calendar reminder set for cert/profile expiry (60 days before)

Once Steps 1–6 are done, tell Claude to start the iOS port.
