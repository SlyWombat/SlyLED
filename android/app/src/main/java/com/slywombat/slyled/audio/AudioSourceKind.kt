package com.slywombat.slyled.audio

/**
 * #820 — semantic audio sources for Auto Brightness, replacing the
 * previous picker that exposed raw `MediaRecorder.AudioSource` constant
 * names ("Unprocessed", "Voice Recognition", "Camcorder", …). Operators
 * think in terms of "where is the audio coming from", not Android's
 * pre-DSP/post-DSP/recognition-tuned variants of the on-device mic.
 *
 * Each kind owns its own AudioRecord construction strategy in
 * [MicAutoBrightness]; some have prerequisites (PLAYBACK_CAPTURE needs
 * MediaProjection consent; USB_AUDIO needs a USB input device plugged in;
 * REMOTE_SUBMIX is privileged on most consumer devices and will fail with
 * a clear error rather than silently delivering zeros).
 */
enum class AudioSourceKind {
    /**
     * Built-in microphone. AudioRecord uses
     * `MediaRecorder.AudioSource.UNPROCESSED` when supported (API 24+
     * + device flag), else falls back to `AudioSource.MIC`. The
     * historical / default option.
     */
    MICROPHONE,

    /**
     * Loopback of audio playing on this device (Spotify, browser, etc.).
     * Implemented via `AudioPlaybackCaptureConfiguration` (Android 10+).
     * Requires the operator to grant a `MediaProjection` consent, just
     * like screen recording — Android shows a system dialog before the
     * first capture. Apps that play DRM-protected audio (some streaming
     * services) flag their output non-capturable; those tracks come
     * through silent.
     */
    PLAYBACK_CAPTURE,

    /**
     * `MediaRecorder.AudioSource.REMOTE_SUBMIX`. Captures the mix that
     * normally goes to remote endpoints (Bluetooth, casting). Privileged
     * on most consumer Android builds — `AudioRecord.STATE_INITIALIZED`
     * will be false on a stock Pixel; we surface that with a clear
     * "Remote Submix not available on this device" hint so the operator
     * doesn't waste time wondering why the meter is dead.
     */
    REMOTE_SUBMIX,

    /**
     * Audio input from a USB-attached source (mic, sound-card, audio
     * interface). Built with `AudioSource.MIC` plus an explicit
     * `setPreferredDevice(AudioDeviceInfo)` pointing at the first USB
     * input enumerated via `AudioManager.getDevices(GET_DEVICES_INPUTS)`.
     * No USB input plugged in → "USB audio device not found" hint.
     */
    USB_AUDIO;

    companion object {
        /**
         * Default for fresh installs and migrations from the legacy
         * `auto_brightness_audio_source` int pref. The previous picker's
         * variants (UNPROCESSED, MIC, VOICE_RECOGNITION, CAMCORDER,
         * DEFAULT, AUTO=-1) all collapse to MICROPHONE under the new
         * semantic naming.
         */
        val DEFAULT = MICROPHONE

        /** Parse the persisted enum-name string; null/unknown → DEFAULT. */
        fun fromName(name: String?): AudioSourceKind = try {
            name?.let { valueOf(it) } ?: DEFAULT
        } catch (_: IllegalArgumentException) {
            DEFAULT
        }
    }
}
