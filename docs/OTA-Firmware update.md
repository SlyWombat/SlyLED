# PROJECT SPECIFICATION: Foolproof FOTA (Firmware Over-The-Air) System

## 1. ROLE & OBJECTIVE
Act as a Senior Embedded Systems Architect. Design and implement a resilient, fail-safe FOTA update mechanism for all of the slyled children. Firmware is stored in GitHub releases and the clients have a check for update.
**Target Hardware:** [ESP32, ESP8266]
**RTOS/OS:** [use the correct applicable approach, update this section with what is being used]
**Connectivity:** [WiFi, and optional BLE if available with android parent]

## 2. CORE ARCHITECTURE (Dual-Bank A/B)
To ensure the device can NEVER be bricked, you must implement a **Dual-Bank** or **Active/Passive** partition scheme.
*   **Partition Map:**
    *   `BOOTLOADER`: Immutable, secure bootloader.
    *   `SLOT_0` (Active): Currently running firmware.
    *   `SLOT_1` (Passive): Storage for the incoming update.
    *   `STORAGE`: Non-volatile storage for flags/logs.
*   **Update Logic:**
    1.  App downloads new image to `SLOT_1`.
    2.  App verifies integrity (Hash) and authenticity (Signature).
    3.  App sets a "Pending Swap" flag in non-volatile memory.
    4.  System reboots.
    5.  Bootloader detects flag, validates `SLOT_1` again.
    6.  **Atomic Swap:** Bootloader swaps images (or updates a pointer) effectively making `SLOT_1` active.
    7.  **Watchdog Confirmation:** New firmware must run for [1] minutes and set a "Commit" flag. If it crashes or Watchdog resets before confirmation, the Bootloader auto-reverts to the old image.

## 3. SECURITY REQUIREMENTS (Non-Negotiable)
*   **Code Signing:** Use ECDSA (e.g., secp256r1) or RSA-2048.
    *   The device must hold the *Public Key*.
    *   The update binary must be signed with a *Private Key* (offline).
    *   Bootloader MUST verify the signature before booting any image.
*   **Anti-Rollback:** Reject firmware with a `version_id` lower than the current running version to prevent replay attacks.
*   **Encryption (Optional):** If the firmware contains sensitive IP, updates must be AES-encrypted (AES-CTR or AES-CBC) and decrypted on-the-fly during the write process.

## 4. RESILIENCE & "FOOLPROOF" MECHANISMS
*   **Power-Loss Safety:** The update process must be resumable or safely abortable.
    *   If power fails during download: Resume from last received chunk (HTTP Range / MQTT offset).
    *   If power fails during flash write: The device must boot into the old (valid) image.
*   **Chunking:** Firmware must be downloaded in small blocks (e.g., 4KB) to accommodate unstable networks.
*   **Sanity Checks:**
    *   Check `Magic Bytes` at the start of the binary.
    *   Verify `CRC32` or `SHA-256` of the entire image before setting the "Pending Swap" flag.

## 5. DELIVERABLES
Please generate the code in the following order:
1.  **Partition Table:** specific memory addresses for the specific MCU.
2.  **Bootloader Logic:** The C code responsible for checking flags, verifying signatures, and swapping banks.
3.  **Update Agent (Application Side):** The C/C++ code to download chunks, write to flash, and trigger the reboot.
4.  **Python Signing Script:** A script to wrap a raw binary with a header (version, size, signature) for the device to parse.

## 6. CONSTRAINTS
*   Code must be strictly typed and commented.
*   Use specific flash memory drivers 
*   Error handling: Every flash write/erase operation must be checked.
