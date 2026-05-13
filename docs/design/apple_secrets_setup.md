# Apple Distribution cert + App Store Connect API key — No-Mac Setup

**Audience:** the SlyLED operator, on Windows + WSL, with no Mac access.
**Outcome:** all six GitHub secrets the `ios-testflight` workflow needs, set up entirely from Windows + the Apple Developer / App Store Connect web portals.

Once the secrets are in place, `git push origin ios-v0.1.0` triggers a cloud-Mac build on GitHub Actions that uploads to TestFlight automatically. No Xcode, no Mac, no Keychain Access.

**Prerequisites:**
- Apple Developer Program enrolment (Step 1 of `apple_developer_setup.md`) ✓ done
- Bundle ID `ca.electricrv.slyled` registered (Step 2 of `apple_developer_setup.md`)
- App Store Connect record for SlyLED iOS (Step 4)
- `openssl` available on Windows. Git for Windows includes it; or `winget install ShiningLight.OpenSSL`. Verify: `openssl version` in cmd or WSL.

**Time needed:** 15–25 minutes total, mostly waiting for the Apple portals.

---

## Step A — Generate the Apple Distribution certificate

This is the cert that signs the `.ipa` for TestFlight uploads. Normally Xcode auto-generates this on a Mac via Keychain Assistant; we'll do it via OpenSSL on Windows instead.

### A1. Create the CSR (Certificate Signing Request)

In a Windows or WSL terminal:

```bash
mkdir -p ~/slyled-apple-cert && cd ~/slyled-apple-cert
openssl req -new -newkey rsa:2048 -nodes \
            -keyout slyled-distribution.key \
            -out slyled-distribution.csr \
            -subj "/emailAddress=YOUR_APPLE_ID_EMAIL/CN=SlyLED Distribution/C=CA"
```

Replace `YOUR_APPLE_ID_EMAIL` with the Apple ID email registered under your Developer Program.

This generates two files:
- `slyled-distribution.key` — your private key. **Never upload this anywhere except the secrets step below.** Back it up; if you lose it you lose the ability to use the cert.
- `slyled-distribution.csr` — the certificate signing request. Safe to upload.

### A2. Upload the CSR to Apple

1. Go to <https://developer.apple.com/account/resources/certificates/list>
2. Click the **`+`** to create a new certificate.
3. Select **Apple Distribution** under *Software*. Continue.
4. Click **Choose File** → select `slyled-distribution.csr` from your machine. Continue.
5. Apple generates the cert. Click **Download** — save the file as `slyled-distribution.cer` in the same folder.

### A3. Combine cert + key into a `.p12`

The signing process on the runner needs both the cert and the private key in one file (PKCS#12 / `.p12` format):

```bash
cd ~/slyled-apple-cert
# Convert the .cer (DER) to PEM
openssl x509 -inform DER -in slyled-distribution.cer -out slyled-distribution.pem
# Bundle into .p12 — pick a STRONG password; you'll save it as a GitHub secret.
# IMPORTANT: -legacy is mandatory. OpenSSL 3.x defaults to PBES2/AES-256
# encryption, which macOS `security import` on the GitHub Actions runner
# rejects with the misleading error "MAC verification failed (wrong
# password?)". -legacy uses PBE-SHA1-3DES / PBE-SHA1-RC2-40, the legacy
# format the Apple keychain tool accepts.
openssl pkcs12 -export -legacy \
               -inkey slyled-distribution.key \
               -in slyled-distribution.pem \
               -out slyled-distribution.p12 \
               -name "Apple Distribution" \
               -password pass:YOUR_STRONG_PASSWORD
```

Replace `YOUR_STRONG_PASSWORD` with a 20+ character random password (use a password manager). Save the password — you'll enter it as a GitHub secret in Step C.

### A4. Base64-encode the `.p12` for GitHub Secrets storage

GitHub Secrets are text. Encode the binary `.p12` as a single line of base64:

```bash
base64 -w 0 slyled-distribution.p12 > slyled-distribution.p12.b64
# On Windows cmd:   certutil -encode slyled-distribution.p12 slyled-distribution.p12.b64.tmp && type slyled-distribution.p12.b64.tmp | findstr /v "^-" > slyled-distribution.p12.b64
```

Open `slyled-distribution.p12.b64` in a text editor and copy its entire content (one long line).

---

## Step B — Create an App Store Connect API key

This lets the GitHub Actions runner authenticate with Apple to upload TestFlight builds, without your password and without 2FA prompts.

### B1. Generate the key

1. Go to <https://appstoreconnect.apple.com/access/integrations/api>
2. Tap **Team Keys** tab (top).
3. Click **Generate API Key** (or the **+** if you already have one).
4. **Name:** `SlyLED CI` (any descriptive name).
5. **Access:** **App Manager**. (Developer is also enough; App Manager is safer if you ever add team members.)
6. **Apps:** check **All Apps**, or specifically SlyLED.
7. **Generate**.

Apple now shows the key info. Note **two values** immediately:
- **Issuer ID** (UUID format, shown at the top of the Team Keys page; same for all your keys).
- **Key ID** (10-character alphanumeric, shown next to the new key).

### B2. Download the `.p8` private key

Beside the new key, click **Download API Key** — you get a file named `AuthKey_<KEY_ID>.p8`. **This download is one-time.** If you lose the file, you must revoke the key and create a new one. Save it.

### B3. Base64-encode the `.p8`

```bash
cd ~/slyled-apple-cert
base64 -w 0 AuthKey_*.p8 > apikey.p8.b64
# Windows cmd:  certutil -encode AuthKey_*.p8 apikey.p8.b64.tmp && type apikey.p8.b64.tmp | findstr /v "^-" > apikey.p8.b64
```

Open `apikey.p8.b64`, copy its content.

---

## Step C — Find your Team ID

Go to <https://developer.apple.com/account/> → **Membership** (left sidebar) → note your **Team ID**. It's a 10-character alphanumeric string like `A1B2C3D4E5`.

---

## Step D — Enter the secrets into GitHub

Go to <https://github.com/SlyWombat/SlyLED/settings/secrets/actions> (you need repo admin access — your operator account does).

Click **New repository secret** six times and create:

| Secret name | Value | Source |
|-------------|-------|--------|
| `BUILD_CERT_P12_B64` | Content of `slyled-distribution.p12.b64` (one long base64 line) | Step A4 |
| `BUILD_CERT_PASSWORD` | The strong password from Step A3 | Step A3 |
| `APP_STORE_CONNECT_KEY_ID` | The 10-character Key ID | Step B1 |
| `APP_STORE_CONNECT_ISSUER_ID` | The UUID Issuer ID | Step B1 |
| `APP_STORE_CONNECT_API_KEY_B64` | Content of `apikey.p8.b64` | Step B3 |
| `APPLE_TEAM_ID` | Your 10-char team id | Step C |

After all six are entered, the workflow can run.

---

## Step E — Fire the first TestFlight build

From your Windows/WSL working tree:

```bash
cd /mnt/d/SlyLED
git tag ios-v0.1.0
git push origin ios-v0.1.0
```

Watch progress at <https://github.com/SlyWombat/SlyLED/actions> → **iOS TestFlight build** workflow. The run takes 8–15 minutes.

Once it finishes green, the build appears at App Store Connect → SlyLED → TestFlight → with status **"Processing"** (~30 min), then **"Waiting for Review"** (only on a new `MARKETING_VERSION`), then **"Ready to Test"** after Apple's Beta App Review (~15 min – 24 hr the first time).

When status hits **"Ready to Test"**, the invite email lands on the testers you added in App Store Connect → TestFlight → Internal Testing. Testers follow `docs/design/testflight_install_guide.md` from there.

---

## Subsequent builds — bump the version + push a new tag

```bash
# In ios/project.yml, bump MARKETING_VERSION (e.g. "0.1.0" → "0.2.0").
# Commit the change.
git commit -am "ios: bump to v0.2.0"
git tag ios-v0.2.0
git push origin main && git push origin ios-v0.2.0
```

The workflow's build-number (CFBundleVersion) increments automatically from the GitHub Actions run number, so you never have to manage it by hand. Re-uploads for the same `MARKETING_VERSION` (e.g., bug-fix iteration without a marketing version bump) just need a new tag like `ios-v0.1.1-build-2`.

---

## When things expire / rotate

| Asset | Lifespan | Refresh action |
|-------|----------|----------------|
| Apple Developer Program | 1 year | Auto-renews $99 |
| Apple Distribution certificate | 1 year | Redo Steps A1–A4, replace `BUILD_CERT_P12_B64` + `BUILD_CERT_PASSWORD` secrets |
| App Store Connect API key | doesn't expire | Revoke + replace via Step B if compromised |
| TestFlight build | 90 days | Re-fire the workflow (`git tag ios-v0.X.Y-refresh && git push origin ios-v0.X.Y-refresh`) |

Calendar reminders for the cert (day 305 of cert age) + Dev Program renewal (60 days out) + TestFlight refresh (day 75 of upload age) keep this from biting mid-show.

---

## Troubleshooting

### Workflow fails on "Import signing cert" with `SecKeychainCreate` errors

The base64 secret got mangled in copy-paste. Re-encode the `.p12` and re-enter the `BUILD_CERT_P12_B64` secret. Make sure your terminal didn't add line wrapping (`base64 -w 0` forces a single line on Linux/WSL).

### Workflow fails on archive: "No signing certificate 'iOS Distribution' found"

The `.p12` cert didn't match what's registered at Apple. Verify:
- The CSR you uploaded was the *same one* you used to generate the .key file.
- The CSR was uploaded under **Apple Distribution**, not Apple Development.
- The `.p12` actually contains the cert AND key: `openssl pkcs12 -in slyled-distribution.p12 -nokeys -info` should show "Apple Distribution: …".

### Workflow fails on upload: "Authentication credentials are missing or invalid"

The `.p8` API key was generated for a different Apple ID, or the Key ID / Issuer ID secrets got swapped. Double-check the secrets — Key ID is 10 chars alphanumeric, Issuer ID is UUID-formatted.

### Beta App Review rejects the build

Apple sends a rejection reason via email. Most common for SlyLED:
- **"Missing privacy strings"** — check `ios/SlyLED/Info.plist` has `NSLocalNetworkUsageDescription` + `NSMicrophoneUsageDescription` (both ship in v0.1.0; this shouldn't fire).
- **"App crashes on launch"** — usually a signing issue or a missing capability. Check the runner's archive log + the build artifact (workflow uploads it as `slyled-ios-<tag>`).

### "Processing" stays forever (more than 1 hour)

Apple's processing queue occasionally stalls. Email Apple Developer Support; usually unsticks within a business day. The build can also be removed and re-uploaded if it's been > 24 hr.

---

## Why no Mac is actually needed

The chicken-and-egg "iOS needs a Mac" problem dissolves because:

1. **The `.xcodeproj`** is generated from `ios/project.yml` at build time by XcodeGen running on the macos-14 runner — we never have to hand-author the fragile `.pbxproj` XML on Linux.
2. **The Distribution cert** is generated via OpenSSL on Windows + the Apple Developer web portal — Keychain Access isn't required.
3. **The provisioning profile** is created on-the-fly by xcodebuild's `-allowProvisioningUpdates` flag, authenticated via the App Store Connect API key.
4. **The TestFlight upload** is via `xcrun altool` with API key auth — no `iTMSTransporter` JNLP nonsense, no manual signing identity matching.
5. **The macOS runner** is Apple-licensed and provided free by GitHub Actions (200 macOS min/month for private repos).

The whole pipeline is Windows-friendly + zero local Mac dependency. Total operator effort per release: bump version in `ios/project.yml`, commit, tag, push. ~15 minutes to TestFlight.
