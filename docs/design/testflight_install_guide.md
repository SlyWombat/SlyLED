# Installing SlyLED on Your iPhone — A Guide for First-Time Testers

This guide walks you through installing SlyLED on your iPhone using **TestFlight**, Apple's official tool for testing apps before they hit the App Store. You don't need to be a developer or know anything technical — just follow these steps in order.

**What you'll need:**
- An iPhone running iOS 17 or newer.
- An Apple ID (the email + password you use for the App Store).
- An invite email from the SlyLED operator (the person who sent you here).
- About 5 minutes.

**What you won't need:**
- A computer.
- A credit card.
- Any developer tools or technical knowledge.

---

## Step 1 — Find the invite email

The SlyLED operator added your Apple ID email to the testing list. Apple sent you an email titled something like **"You're invited to test SlyLED"**.

It comes from `noreply@email.apple.com` or similar. Check your inbox (and spam folder).

The email contains either:
- A green **"View in TestFlight"** button, **OR**
- A redemption code that looks like `XXXX-XXXX-XXXX`.

Keep the email handy. You'll come back to it in Step 3.

---

## Step 2 — Install Apple's TestFlight app

TestFlight is a free app made by Apple. It's how beta versions of apps get installed on your phone.

1. On your iPhone, open the **App Store**.
2. Search for **"TestFlight"**.
3. The first result has a blue icon with a white propeller. The publisher is **Apple Inc.** — make sure it's Apple's app, not a copycat.
4. Tap **Get** (or the cloud icon if you've installed it before).
5. Authenticate with Face ID / Touch ID / Apple ID password as prompted.
6. Wait for the install to finish. The icon appears on your home screen.

You only do this once. From now on, TestFlight handles all beta apps you're invited to.

---

## Step 3 — Accept the invite

Go back to the invite email from Step 1.

**If the email has a "View in TestFlight" button:**
1. Tap **View in TestFlight**.
2. Your iPhone opens TestFlight automatically.
3. SlyLED appears with a blue **Accept** button. Tap it.

**If the email has a redemption code:**
1. Open the TestFlight app you installed in Step 2.
2. Tap **Redeem** (top-right corner).
3. Type or paste the code from the email.
4. SlyLED appears with a blue **Accept** button. Tap it.

You only accept the invite once. From now on, SlyLED appears in your TestFlight app every time you open it.

---

## Step 4 — Install SlyLED

After accepting:
1. TestFlight shows the SlyLED app details page.
2. Tap the blue **Install** button.
3. Wait 30 seconds to a couple minutes — the download progresses with a circle that fills up.
4. When it's done, the button changes to **Open**.
5. Tap **Open** to launch SlyLED.

The SlyLED icon also appears on your home screen — you can launch it from there in future.

---

## Step 5 — First launch

The first time you open SlyLED, iOS will ask for two permissions:

1. **"SlyLED would like to access the microphone"** — required for Auto Brightness (the feature that follows the music). Tap **Allow**. If you tap Don't Allow, you can change it later in Settings → SlyLED → Microphone.
2. **"SlyLED would like to use Bluetooth"** — not used by SlyLED today; if asked, you can tap Allow or Don't Allow either way.

After permissions, SlyLED asks for your orchestrator's IP address. The operator will tell you what to enter:
- **Server IP:** something like `192.168.1.42`
- **Port:** usually `8080` or `5600`

Tap **Connect**. If your phone is on the same Wi-Fi network as the orchestrator computer, the green "Connected" pill appears at the top.

You're done. You can now use SlyLED.

---

## Updates happen automatically

When the operator uploads a new test build, your TestFlight app handles the update for you:

- If TestFlight is set to **Automatic Updates** (the default), the new version installs in the background within a few hours.
- You can also open TestFlight any time, find SlyLED, and tap **Update** if a newer build is available.
- iOS shows an orange dot ● next to the SlyLED app icon when a beta update is waiting.

You don't need to do anything for normal updates. Just keep TestFlight on your phone.

---

## Test builds expire after 90 days

Beta builds aren't forever. Each one is valid for **90 days** from when the operator uploaded it. If a build expires before a new one is uploaded, SlyLED will refuse to launch and show "This beta has expired."

Tell the operator if you see that message. They'll upload a fresh build, and TestFlight will install it automatically within a few hours.

You'll usually see a smaller warning starting around day 80 ("This beta expires in 10 days"). That's normal — no action needed; just notify the operator if no new build appears by day 88.

---

## Troubleshooting

### "This app is not available in your country or region"

Your Apple ID is set to a country where Apple doesn't allow beta testing the SlyLED app. Options:
- Switch your Apple ID region to the US or Canada (Settings → [your name] → Media & Purchases → View Account → Country/Region). This affects all your App Store purchases — don't switch lightly.
- Easier: tell the operator. They can issue you a *public link* that bypasses the regional check.

### "Couldn't install — error 0xE800003A" or similar cryptic error

Almost always one of:
- **iPhone storage full.** Free up at least 1 GB (Settings → General → iPhone Storage).
- **iOS too old.** Update to iOS 17 or newer (Settings → General → Software Update).
- **TestFlight needs an update.** Open the App Store, search TestFlight, tap Update if available.

### The invite email never arrived

1. Check spam / junk folder.
2. Confirm with the operator that they used the exact Apple ID email you gave them. A typo (`.com` vs `.ca`) blocks delivery silently.
3. Apple's invite emails sometimes take up to 30 minutes. Wait, then ask the operator to resend.

### "Beta App Review is required" — but I just need to install

If you see this, the operator just uploaded the build and Apple is reviewing it (usually 15 minutes to a couple hours, occasionally a full day). Try again later. There's nothing you can do to speed it up.

### SlyLED launches but the connection pill stays red

You're connected to TestFlight successfully, but SlyLED can't reach the orchestrator computer over Wi-Fi. Check:
- Both your iPhone and the orchestrator computer are on the *same* Wi-Fi network. Not a guest network — the real one.
- The IP address and port the operator gave you are correct (the operator can re-check at SlyLED → Settings on the computer).
- The orchestrator app is actually running on the computer.

### I want to stop testing

In TestFlight, open SlyLED, scroll to the bottom, tap **Stop Testing**. The app icon disappears from your home screen. No data is sent anywhere.

You can rejoin later as long as the operator hasn't removed you from the testing list.

---

## Privacy & data

- TestFlight builds **may** crash. If they do, anonymous crash logs are sent to the SlyLED operator. No personal data, photos, contacts, or messages are accessed.
- The microphone is only listened to when Auto Brightness is enabled inside the app. iOS shows an orange dot at the top of the screen whenever the mic is active.
- SlyLED communicates with the orchestrator computer over your local Wi-Fi only — nothing leaves your network.

---

## Quick reference — what each app does

- **App Store (Apple's regular app):** for downloading public, finished apps. Won't have SlyLED in it.
- **TestFlight (Apple's testing app):** for beta versions like SlyLED. This is where you check for updates and stop testing.
- **SlyLED (the actual app):** what you use to control the lighting rig at a show.

You'll see all three on your iPhone after this guide. Only SlyLED is yours to use; the other two are just plumbing.

---

If anything in this guide didn't match what you see on your phone, tell the operator. Apple changes the TestFlight UI occasionally, and the guide may need a refresh.
