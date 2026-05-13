---
title: Privacy Policy
slug: privacy
format: marketing-page
---

# SlyLED Privacy Policy

**Effective date:** 2026-05-13
**Last updated:** 2026-05-13
**Data controller:** Electric RV (Ontario, Canada)
**Privacy contact:** [privacy@electricrv.ca](mailto:privacy@electricrv.ca)

This policy explains what information SlyLED collects, why, and what your rights are. It covers the SlyLED desktop orchestrator (Windows, macOS), the SlyLED iOS and Android operator apps, the SlyLED camera-node firmware, this website (electricrv.ca/slyled), and all official downloads. It does **not** cover third-party fixtures, controllers, or services you choose to use alongside SlyLED.

## Short version

**SlyLED does not collect, transmit, or share any personal data.** No accounts, no logins, no analytics, no telemetry, no crash reports, no advertising trackers. Everything runs on your local network. We never see your shows, your stage layouts, your venue's audio, or anything else.

If that's all you need to know, you can stop reading here.

---

## 1. What SlyLED *is* and *isn't*

SlyLED is a self-hosted stage-lighting control system. The desktop orchestrator runs on your computer; the mobile apps talk to that computer over your venue's local Wi-Fi; the camera nodes are small Linux boards running on the same Wi-Fi. **None of these components contact any Electric RV server.** There is no SlyLED cloud, no SlyLED account, no "sync" feature, no remote-control gateway.

This is deliberate. SlyLED is built so a stage operator can run a show in a basement, a barn, or a venue with no internet at all, and have it work identically to a connected venue.

---

## 2. Information we collect

### 2.1 On the website (electricrv.ca/slyled)

The website is a static HTML site served from a standard web host. The hosting provider keeps the usual server access logs (IP address, request path, timestamp, user-agent string) for normal operations, fraud prevention, and abuse mitigation. These logs are not used to profile visitors, are not shared with third parties, and are deleted on the hosting provider's standard rotation.

We do **not** use:

- Google Analytics or any equivalent
- Tracking cookies, advertising pixels, or fingerprinting scripts
- Embedded social-media widgets that phone home
- Newsletter or contact-form services that profile submitters

The site sets no cookies of its own. Your browser may receive standard HTTP cache headers; those are not cookies and do not identify you.

### 2.2 In the desktop orchestrator (Windows, macOS)

The orchestrator runs entirely on your computer. It stores your project files (`.slyshow`), DMX profiles, fixture layouts, and configuration on your local disk under `%APPDATA%\SlyLED` (Windows) or `~/Library/Application Support/SlyLED` (macOS). **Nothing leaves your computer**, except direct network traffic on your local network (UDP port 4210 for performer nodes, Art-Net/sACN for DMX bridges, HTTP for the operator app, etc.).

The orchestrator does check `firmware/registry.json` for available firmware updates when you open the Firmware tab. Those checks read a local file embedded in the install — they do not contact electricrv.ca. If you choose to download a firmware binary, the download is fetched from the URL in `registry.json` (usually github.com); the hosting service for that download receives standard request logs as in 2.1.

### 2.3 In the mobile apps (iOS, Android)

The mobile apps are operator companions to the desktop orchestrator. They connect over your local Wi-Fi to the orchestrator and exchange UDP and HTTP traffic with it. **They never contact any server other than your own orchestrator.**

The apps request the following device permissions:

- **Local network access** (iOS) — required to discover and talk to the orchestrator over Wi-Fi. Used only for that purpose.
- **Microphone** — used only when *Auto Brightness* is enabled. The mic stream is analysed locally on the phone to extract a loudness envelope; only the envelope (a single 0–255 value) is sent to your orchestrator. The raw audio is never recorded, transmitted, or persisted. Turning Auto Brightness off stops the microphone immediately.
- **Camera** — used only when you tap *Scan QR Code* on the Connect screen. The camera frames are decoded locally to extract the orchestrator's IP and port. No frames are recorded, transmitted, or persisted.
- **Bluetooth, Contacts, Location, Photos, Notifications:** not requested. Not used.

The app stores its UI settings (server IP, last connection, favourite fixtures, Auto Brightness tuning) in the standard per-app sandbox on your phone. That data never leaves the device.

### 2.4 On camera nodes (Linux SBCs running our firmware)

Camera nodes are small Linux computers (Orange Pi, Raspberry Pi, etc.) that you flash with our firmware. They run a local HTTP server on port 5000 and a UDP listener on 4210, and stream camera frames over your local network to the orchestrator for stage-tracking. **Camera footage is processed in memory on the orchestrator and is not saved to disk** unless you explicitly use the orchestrator's calibration-capture feature, in which case the captured images stay on the orchestrator's disk under your project folder.

Camera nodes do not contact electricrv.ca or any other Electric RV system. They contact only the orchestrator on your local network.

### 2.5 TestFlight and Google Play sideload

If you install the iOS app through Apple TestFlight, **Apple** (not Electric RV) receives whatever device and crash data its TestFlight system normally collects. We do not consume that data — Apple's policies apply. See <https://www.apple.com/legal/privacy/> for Apple's terms.

The Android app is currently distributed as a sideload-able `.apk`; no app-store telemetry is involved.

---

## 3. How we use information

Because we don't collect any personal data, there's no "use" of it. The standard web-host access logs covered in §2.1 are used only for hosting operations (serving the site, blocking abuse, billing the host). They are not sold, shared, or correlated with any other source.

---

## 4. Sharing with third parties

**We do not share data with third parties.** Specifically:

- We do not sell or rent any data.
- We do not share data with advertising networks, data brokers, or analytics services.
- We do not embed third-party scripts, pixels, or beacons in the website or apps.
- We do not honour requests from non-governmental third parties for user data — there is nothing to give them.

If a Canadian law-enforcement or government body served us a valid legal order for information we hold, we would respond within the scope of that order. In practice the only thing in scope would be the web-host access logs in §2.1 — we hold no SlyLED user data outside that.

---

## 5. Where data lives

| Surface | Storage location | Operator-owned? |
|---|---|---|
| Website access logs | Hosting provider (Canada) | No — hosting provider's standard policy |
| Orchestrator project + config | Your computer's local disk | Yes |
| Phone app settings | Your phone's per-app sandbox | Yes |
| Camera-node configuration | The Linux SBC's local disk | Yes |
| TestFlight crash logs (if you opt in) | Apple servers | No — Apple's policy applies |

Electric RV operates no servers that hold SlyLED operator data.

---

## 6. Retention

We do not retain operator data because we never receive it. Web-host access logs follow the hosting provider's standard retention schedule (typically 30–90 days).

---

## 7. Your rights

Even though we hold almost no data about you, the following rights apply if you are in a jurisdiction that grants them (Canada PIPEDA, EU/UK GDPR, California CCPA, Quebec Law 25, and similar):

- **Right of access** — ask what we hold about you. Honest answer is almost always "nothing"; we'll confirm in writing.
- **Right to correction** — if any record we hold contains incorrect information, we'll correct it.
- **Right to deletion** — request deletion of any personal data tied to you. For the web-host access logs we typically cannot pinpoint a specific user, but if you give us your IP address and a date range we can ask the host to redact those entries.
- **Right to portability** — receive a copy of any personal data we hold in a structured machine-readable format.
- **Right to object / withdraw consent** — applicable to any processing based on consent. Since SlyLED itself collects no data, the most relevant action is uninstalling the apps and not visiting the site; both are unilateral.
- **Right to lodge a complaint** with your local supervisory authority (Office of the Privacy Commissioner of Canada, your provincial commissioner, an EU data-protection authority, the California Attorney General, etc.).

To exercise any of these, email **[privacy@electricrv.ca](mailto:privacy@electricrv.ca)** with a clear description of what you want. We aim to reply within 30 days.

---

## 8. Children

SlyLED is a professional and hobbyist stage-lighting tool, not directed at children. We do not knowingly collect personal data from anyone under 13 (or under 16 in jurisdictions where that threshold applies). Since we collect no personal data at all, this is structurally enforced rather than policy-dependent. Operators using SlyLED in school or youth-theatre contexts retain full control of any data the orchestrator stores on their own machines.

---

## 9. Security

The orchestrator and apps speak unencrypted UDP and HTTP **on your local network only**. We assume the local Wi-Fi the operator chooses is itself secured (WPA2/WPA3 at the minimum). The mobile app's mDNS discovery and the UDP performer protocol are not encrypted because they are not designed to traverse untrusted networks; they live inside the venue.

If you connect SlyLED across an untrusted network (e.g. a VPN bridging two sites), the operator is responsible for whatever encryption that bridge provides. SlyLED itself does not encrypt traffic to performers or DMX bridges.

The website is served over HTTPS (TLS 1.2+) with HSTS.

---

## 10. International users

SlyLED is developed and hosted in Canada. If you use SlyLED from outside Canada, the limited information described in §2.1 (web-host access logs) is processed in Canada. We have entered into no international data-transfer agreements because we never transfer operator data — there is no operator data to transfer.

---

## 11. Changes to this policy

We may update this policy when the law changes, when SlyLED's features change, or when our hosting arrangement changes. The **Last updated** date at the top reflects the most recent revision. Material changes (anything that newly *collects* user data) will be announced on this page at least 30 days before the change takes effect. The previous version of this policy remains available on request.

Git history for this page is public at <https://github.com/SlyWombat/SlyLED/commits/main/docs/src/marketing/privacy.md>.

---

## 12. Contact

- **Email:** [privacy@electricrv.ca](mailto:privacy@electricrv.ca)
- **Postal:** Electric RV, Ontario, Canada *(full address available on request to the email above; we are a small operation and do not publish a postal address publicly)*

We aim to acknowledge privacy inquiries within 5 business days and to resolve them within 30 days.

---

*This policy is published under the same PolyForm Noncommercial 1.0.0 licence as the rest of the SlyLED project. You may reuse it as a starting point for your own privacy policy as long as you adapt it to your actual data practices and remove the Electric-RV-specific clauses.*
