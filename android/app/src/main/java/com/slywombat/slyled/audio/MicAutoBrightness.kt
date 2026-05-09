package com.slywombat.slyled.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioPlaybackCaptureConfiguration
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.projection.MediaProjection
import android.os.Build
import android.util.Log
import androidx.core.content.ContextCompat
import com.slywombat.slyled.data.repository.AutoBrightnessPrefs
import com.slywombat.slyled.data.repository.ServerPreferences
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * #804 — AudioRecord driver that feeds [EnvelopeFollower] and emits live state.
 *
 * Lifecycle:
 *   [start]         → live capture; each hop drives `onMaster(0..255)` to the brightness POST.
 *   [startPreview]  → same capture loop without the brightness callback; lets the
 *                     UI render the raw + envelope meters while the operator is
 *                     just auditioning sources (Auto Brightness toggle still off).
 *   [stop]          → release the AudioRecord.
 *
 * The semantic source is owned by [audioSourceKindFlow] and stored as the
 * [AudioSourceKind] enum (#820). Each kind has its own AudioRecord
 * construction strategy:
 *   - MICROPHONE        : built-in mic via UNPROCESSED → MIC fallback.
 *   - PLAYBACK_CAPTURE  : AudioPlaybackCaptureConfiguration; needs
 *                         MediaProjection consent set via [setMediaProjection].
 *   - REMOTE_SUBMIX     : AudioSource.REMOTE_SUBMIX (privileged on most devices).
 *   - USB_AUDIO         : MIC source routed to the first USB input via
 *                         setPreferredDevice(AudioDeviceInfo).
 */
class MicAutoBrightness(
    private val context: Context,
    private val prefs: ServerPreferences,
) {
    /**
     * Capture pipeline state.
     *
     * `NeedsPlaybackConsent` (#820): emitted when the operator picks
     * PLAYBACK_CAPTURE without having yet granted MediaProjection. UI shows
     * a "Tap to grant playback access" CTA. Once the activity-level launcher
     * delivers a MediaProjection and [setMediaProjection] is called,
     * capture can proceed.
     */
    enum class Mode {
        Idle, Listening, NoMic, PermissionDenied, Clipping,
        NeedsPlaybackConsent, NoUsbDevice, RemoteSubmixDenied,
    }

    private val _state = MutableStateFlow(Mode.Idle)
    val state: StateFlow<Mode> = _state.asStateFlow()

    private val _envelope = MutableStateFlow(0f)
    val envelope: StateFlow<Float> = _envelope.asStateFlow()

    // #820 — beat detection state. `beatPulse` flashes to 1.0 on each
    // detected onset and decays exponentially (~150 ms tau); the UI
    // renders a pulsing dot from this. `bpm` carries the rolling-median
    // tempo forward between beats so the operator sees the reading
    // persist while a song is mid-bar instead of resetting on silences.
    // Brightness output blends the smoothed envelope with this pulse so
    // the lights flash on beats (max(env, beatPulse) → master).
    private val _beatPulse = MutableStateFlow(0f)
    val beatPulse: StateFlow<Float> = _beatPulse.asStateFlow()
    private val _bpm = MutableStateFlow(0f)
    val bpm: StateFlow<Float> = _bpm.asStateFlow()
    private val _beatCount = MutableStateFlow(0)
    val beatCount: StateFlow<Int> = _beatCount.asStateFlow()
    private val beatDetector = BeatDetector()

    // #820 — final master brightness value (0..1) being driven onto the
    // wire after floor/ceiling mapping and beat-pulse layering. UI shows
    // this as the "Brightness output" bar — the operator-meaningful
    // signal: with floor=0.4, the bar baselines at 40 % and swings up
    // to 100 % on each beat. Pre-fix the modal only showed the raw
    // envelope (0..1, pre-floor) so raising the floor didn't visibly
    // do anything in the meter.
    private val _master = MutableStateFlow(0f)
    val master: StateFlow<Float> = _master.asStateFlow()

    // #820 — raw peak amplitude of the last 50 ms hop, before envelope
    // smoothing. 0 = perfectly silent buffer (root cause of the "envelope
    // never moves" symptom). UI shows this as a separate bar so the
    // operator can immediately tell "source is dead" from "floor too high".
    private val _rawPeak = MutableStateFlow(0f)
    val rawPeak: StateFlow<Float> = _rawPeak.asStateFlow()

    private val _sensitivity = MutableStateFlow(1.5f)
    val sensitivityFlow: StateFlow<Float> = _sensitivity.asStateFlow()
    private val _floor = MutableStateFlow(0.05f)
    val floorFlow: StateFlow<Float> = _floor.asStateFlow()
    private val _ceiling = MutableStateFlow(1.0f)
    val ceilingFlow: StateFlow<Float> = _ceiling.asStateFlow()
    private val _attackMs = MutableStateFlow(8f)
    val attackMsFlow: StateFlow<Float> = _attackMs.asStateFlow()
    private val _releaseMs = MutableStateFlow(220f)
    val releaseMsFlow: StateFlow<Float> = _releaseMs.asStateFlow()

    // #820 — semantic audio source kind (replaces the int-based picker).
    private val _audioSourceKind = MutableStateFlow(AudioSourceKind.DEFAULT)
    val audioSourceKindFlow: StateFlow<AudioSourceKind> = _audioSourceKind.asStateFlow()

    private val _persistedEnabled = MutableStateFlow(false)
    val persistedEnabled: StateFlow<Boolean> = _persistedEnabled.asStateFlow()

    private val follower = EnvelopeFollower(sampleRateHz = SAMPLE_RATE)
    private var captureJob: Job? = null
    private var record: AudioRecord? = null

    // Last live invocation context so configure(audioSource=…) can restart
    // capture under the same scope/callback transparently.
    private var lastScope: CoroutineScope? = null
    private var lastOnMaster: ((Int) -> Unit)? = null
    private var inPreview: Boolean = false

    // #820 — MediaProjection token, set by the activity-level result
    // launcher when the operator grants screen-capture consent. Without
    // this, PLAYBACK_CAPTURE construction fails. Cleared on stop().
    private var mediaProjection: MediaProjection? = null

    private val ioScope = MainScope()

    init {
        ioScope.launch {
            try {
                val saved = prefs.loadAutoBrightness()
                applyPrefs(saved, persist = false)
                _persistedEnabled.value = saved.enabled
            } catch (e: Exception) {
                Log.w(TAG, "Auto Brightness prefs load failed", e)
            }
        }
    }

    val sensitivity: Float get() = follower.sensitivity
    val floor: Float get() = follower.floor
    val ceiling: Float get() = follower.ceiling
    val attackMs: Float get() = follower.attackMs
    val releaseMs: Float get() = follower.releaseMs
    val audioSourceKind: AudioSourceKind get() = _audioSourceKind.value

    fun configure(
        sensitivity: Float? = null,
        floor: Float? = null,
        ceiling: Float? = null,
        attackMs: Float? = null,
        releaseMs: Float? = null,
        audioSourceKind: AudioSourceKind? = null,
    ) {
        sensitivity?.let {
            val v = it.coerceIn(0.1f, 8f)
            follower.sensitivity = v; _sensitivity.value = v
        }
        floor?.let {
            val v = it.coerceIn(0f, 1f)
            follower.floor = v; _floor.value = v
        }
        ceiling?.let {
            val v = it.coerceIn(0f, 1f)
            follower.ceiling = v; _ceiling.value = v
        }
        attackMs?.let {
            val v = it.coerceIn(1f, 200f)
            follower.attackMs = v; _attackMs.value = v
        }
        releaseMs?.let {
            val v = it.coerceIn(20f, 2000f)
            follower.releaseMs = v; _releaseMs.value = v
        }
        // #820 — switching source restarts capture under whichever mode
        // (live or preview) was active. The operator gets immediate
        // feedback on the new source via the raw meter.
        audioSourceKind?.let {
            if (it != _audioSourceKind.value) {
                _audioSourceKind.value = it
                val cb = lastOnMaster
                val sc = lastScope
                val wasPreview = inPreview
                if (captureJob?.isActive == true) {
                    stop()
                    if (sc != null) {
                        if (wasPreview) startPreview(sc)
                        else if (cb != null) start(sc, cb)
                    }
                }
            }
        }
        persist()
    }

    private fun applyPrefs(p: AutoBrightnessPrefs, persist: Boolean) {
        val sen = p.sensitivity.coerceIn(0.1f, 8f)
        val fl = p.floor.coerceIn(0f, 1f)
        val ce = p.ceiling.coerceIn(0f, 1f)
        val atk = p.attackMs.coerceIn(1f, 200f)
        val rel = p.releaseMs.coerceIn(20f, 2000f)
        follower.sensitivity = sen; _sensitivity.value = sen
        follower.floor = fl; _floor.value = fl
        follower.ceiling = ce; _ceiling.value = ce
        follower.attackMs = atk; _attackMs.value = atk
        follower.releaseMs = rel; _releaseMs.value = rel
        _audioSourceKind.value = p.audioSourceKind
        if (persist) this.persist()
    }

    private fun persist(enabled: Boolean? = null) {
        val want = AutoBrightnessPrefs(
            enabled = enabled ?: _persistedEnabled.value,
            sensitivity = follower.sensitivity,
            floor = follower.floor,
            ceiling = follower.ceiling,
            attackMs = follower.attackMs,
            releaseMs = follower.releaseMs,
            audioSourceKind = _audioSourceKind.value,
        )
        if (enabled != null) _persistedEnabled.value = enabled
        ioScope.launch {
            try { prefs.saveAutoBrightness(want) }
            catch (e: Exception) { Log.w(TAG, "Auto Brightness prefs save failed", e) }
        }
    }

    fun setPersistedEnabled(enabled: Boolean) {
        if (enabled == _persistedEnabled.value) return
        persist(enabled = enabled)
    }

    fun hasPermission(): Boolean = ContextCompat.checkSelfPermission(
        context, Manifest.permission.RECORD_AUDIO
    ) == PackageManager.PERMISSION_GRANTED

    /**
     * #820 — register a MediaProjection token (obtained at the activity
     * level via [android.media.projection.MediaProjectionManager.createScreenCaptureIntent]).
     * Required for PLAYBACK_CAPTURE; ignored by other source kinds.
     */
    fun setMediaProjection(mp: MediaProjection?) {
        mediaProjection = mp
        // If the operator was waiting on consent, kick the pipeline
        // back into life now that we have a token. The foreground
        // service has to be started BEFORE MediaProjection.createAudioRecord
        // on Android 14+, so we start it here and stop it in [stop].
        if (mp != null) {
            try { PlaybackCaptureService.start(context) } catch (_: Exception) {}
        }
        if (mp != null && _state.value == Mode.NeedsPlaybackConsent) {
            val sc = lastScope
            val cb = lastOnMaster
            val wasPreview = inPreview
            if (sc != null) {
                if (wasPreview) startPreview(sc)
                else if (cb != null) start(sc, cb)
            }
        }
    }

    /**
     * Live capture. `onMaster` fires after each hop with the latest 0..255
     * brightness — caller pipes that to `/api/brightness`. Returns true if
     * capture started; false on permission / device errors (state flow
     * carries the diagnostic in that case).
     */
    fun start(scope: CoroutineScope, onMaster: (Int) -> Unit): Boolean {
        return startInternal(scope, onMaster, preview = false)
    }

    /**
     * #820 — preview capture. Same loop as [start] but without invoking
     * the brightness callback, so the operator can see the raw + envelope
     * meters track each AudioSourceKind without driving the lights.
     */
    fun startPreview(scope: CoroutineScope): Boolean {
        return startInternal(scope, onMaster = null, preview = true)
    }

    private fun startInternal(
        scope: CoroutineScope,
        onMaster: ((Int) -> Unit)?,
        preview: Boolean,
    ): Boolean {
        // #862 — pre-fix: a single early-return `if (captureJob?.isActive == true)
        // return true` masked a real footgun. When the Settings panel was open
        // it called `startPreview()` which spun up a capture in preview mode
        // (`onMaster=null`); a subsequent tap on the Stage Auto Brightness
        // toggle then called `start(scope, onMaster)` which hit this branch and
        // returned success WITHOUT swapping in the new onMaster — UI showed
        // "Listening" but zero UDP packets were sent. Now: keep the early
        // return only when the job is alive AND the (preview, callback) pair
        // matches; otherwise tear down and restart so live mode actually fires.
        val activeJob = captureJob
        if (activeJob != null && activeJob.isActive) {
            val sameMode = (inPreview == preview) && (lastOnMaster === onMaster)
            if (sameMode) return true
            // Mode change requested (preview→live or live→preview). Stop the
            // current capture cleanly, then fall through to start a fresh one
            // with the new onMaster wiring.
            stop()
        }
        if (!hasPermission()) {
            _state.value = Mode.PermissionDenied
            return false
        }
        val rec = buildAudioRecord(_audioSourceKind.value) ?: return false
        record = rec
        rec.startRecording()
        _state.value = Mode.Listening
        follower.reset()
        beatDetector.reset()
        _beatPulse.value = 0f
        _bpm.value = 0f
        _beatCount.value = 0
        lastScope = scope
        lastOnMaster = onMaster
        inPreview = preview

        captureJob = scope.launch(Dispatchers.IO) {
            val pcm = ShortArray(HOP_SAMPLES)
            val floats = FloatArray(HOP_SAMPLES)
            // Track running peak across the last 1 s window so the
            // periodic log line shows the headroom even when the ~50 ms
            // hop happens to land on a quiet sample. Reset on each log.
            var windowPeak = 0f
            var windowStart = System.currentTimeMillis()
            try {
                while (isActive) {
                    val n = rec.read(pcm, 0, HOP_SAMPLES)
                    if (n <= 0) continue
                    var clipped = false
                    var peak = 0
                    for (i in 0 until n) {
                        val s = pcm[i].toInt()
                        if (s >= 32760 || s <= -32760) clipped = true
                        val abs = if (s < 0) -s else s
                        if (abs > peak) peak = abs
                        floats[i] = s / 32768f
                    }
                    val rawPeakNow = peak / 32768f
                    _rawPeak.value = rawPeakNow
                    if (rawPeakNow > windowPeak) windowPeak = rawPeakNow
                    val env = follower.process(floats, n)
                    _envelope.value = env

                    // #820 — beat detection on the raw peak. On a fresh
                    // beat: snap pulse to 1.0, increment counter, push
                    // the rolling-median BPM forward. Otherwise decay
                    // the existing pulse (tau ≈ 150 ms → ~70 % per
                    // 50 ms hop).
                    val nowMs = System.currentTimeMillis()
                    val gotBeat = beatDetector.process(rawPeakNow, nowMs)
                    if (gotBeat) {
                        _beatPulse.value = 1f
                        _beatCount.value = _beatCount.value + 1
                        _bpm.value = beatDetector.bpm
                    } else {
                        _beatPulse.value = (_beatPulse.value * 0.717f).coerceAtLeast(0f)
                    }

                    // Beat pulse takes over when stronger than the
                    // smoothed envelope so the lights actually flash on
                    // each onset rather than averaging through the
                    // follower's release tail. Master is computed in
                    // BOTH live and preview modes so the UI can show
                    // the operator-meaningful "what the lights are
                    // doing" value; only LIVE actually POSTs.
                    val driveValue = maxOf(env, _beatPulse.value)
                    val masterByte = follower.toMaster(driveValue)
                    _master.value = masterByte / 255f
                    if (!preview && onMaster != null) {
                        withContext(Dispatchers.Main) { onMaster(masterByte) }
                    }
                    _state.value = if (clipped) Mode.Clipping else Mode.Listening
                    // #820 — periodic log so adb logcat can confirm the
                    // capture loop is genuinely receiving audio + beat
                    // detection. Once per second; cheap.
                    val now = System.currentTimeMillis()
                    if (now - windowStart >= 1000) {
                        Log.i(TAG,
                            "tick: peak1s=%.4f env=%.3f bpm=%.0f beats=%d kind=%s preview=%s"
                            .format(windowPeak, env, _bpm.value, _beatCount.value,
                                    _audioSourceKind.value, preview))
                        windowPeak = 0f
                        windowStart = now
                    }
                }
            } catch (e: Exception) {
                Log.e(TAG, "capture loop", e)
                _state.value = Mode.NoMic
            }
        }
        return true
    }

    /**
     * #820 — build an AudioRecord for the chosen [AudioSourceKind]. Returns
     * null and sets the appropriate [Mode] if the source is unavailable;
     * caller surrenders the start.
     */
    private fun buildAudioRecord(kind: AudioSourceKind): AudioRecord? {
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) {
            _state.value = Mode.NoMic
            return null
        }
        val bufBytes = maxOf(minBuf, HOP_SAMPLES * 2)

        return when (kind) {
            AudioSourceKind.MICROPHONE -> buildMicrophone(bufBytes)
            AudioSourceKind.PLAYBACK_CAPTURE -> buildPlaybackCapture(bufBytes)
            AudioSourceKind.REMOTE_SUBMIX -> buildRemoteSubmix(bufBytes)
            AudioSourceKind.USB_AUDIO -> buildUsbAudio(bufBytes)
        }
    }

    private fun buildMicrophone(bufBytes: Int): AudioRecord? {
        // #820 — use MediaRecorder.AudioSource.MIC, NOT UNPROCESSED.
        //
        // Live test 2026-05-05 on Pixel 9 Pro XL: with audio playing in
        // the room, AudioSource.UNPROCESSED returned a peak amplitude
        // of 0.001 (essentially silence), whereas the screen-dump
        // showed the loop was iterating fine. UNPROCESSED bypasses the
        // OS's DSP chain (AGC, echo cancellation, gain stages) which
        // is what most audio-meter apps actually want; the down-side
        // is some Pixel firmwares route UNPROCESSED to a near-zero
        // pre-amp, defeating our purpose. AudioSource.MIC gives the
        // post-DSP signal — louder, more reliable, normalised by AGC
        // — and that's what the operator's mental model is anyway
        // ("the microphone").
        Log.i(TAG, "Microphone capture: AudioSource.MIC")
        val rec = try {
            @Suppress("MissingPermission")
            AudioRecord(
                MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufBytes,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Microphone AudioRecord ctor", e)
            _state.value = Mode.NoMic
            return null
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            _state.value = Mode.NoMic
            return null
        }
        return rec
    }

    /**
     * Loopback of audio playing on this device. Requires `MediaProjection`
     * consent obtained at the Activity level via
     * `MediaProjectionManager.createScreenCaptureIntent()`. The token is
     * registered via [setMediaProjection].
     *
     * Some apps (DRM-protected music, certain video players) flag their
     * AudioAttributes as non-capturable; those tracks come through silent
     * even when capture is wired correctly.
     */
    private fun buildPlaybackCapture(bufBytes: Int): AudioRecord? {
        if (Build.VERSION.SDK_INT < 29) {
            Log.w(TAG, "Playback Capture requires Android 10 (API 29)")
            _state.value = Mode.NoMic
            return null
        }
        val mp = mediaProjection
        if (mp == null) {
            Log.i(TAG, "Playback Capture: awaiting MediaProjection consent")
            _state.value = Mode.NeedsPlaybackConsent
            return null
        }
        val cfg = AudioPlaybackCaptureConfiguration.Builder(mp)
            .addMatchingUsage(AudioAttributes.USAGE_MEDIA)
            .addMatchingUsage(AudioAttributes.USAGE_GAME)
            .addMatchingUsage(AudioAttributes.USAGE_UNKNOWN)
            .build()
        val format = AudioFormat.Builder()
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .build()
        Log.i(TAG, "Playback Capture: starting via MediaProjection")
        val rec = try {
            AudioRecord.Builder()
                .setAudioPlaybackCaptureConfig(cfg)
                .setAudioFormat(format)
                .setBufferSizeInBytes(bufBytes)
                .build()
        } catch (e: Exception) {
            Log.w(TAG, "Playback Capture AudioRecord build", e)
            _state.value = Mode.NoMic
            return null
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            _state.value = Mode.NoMic
            return null
        }
        return rec
    }

    /**
     * Remote Submix is privileged on most consumer Android builds. The
     * AudioRecord ctor will succeed but `state` will be UNINITIALIZED, or
     * the ctor will throw a SecurityException — either way we surface
     * `RemoteSubmixDenied` so the operator gets a clear hint instead of a
     * silent zero meter.
     */
    private fun buildRemoteSubmix(bufBytes: Int): AudioRecord? {
        Log.i(TAG, "Remote Submix capture: AudioSource.REMOTE_SUBMIX")
        val rec = try {
            @Suppress("MissingPermission")
            AudioRecord(
                MediaRecorder.AudioSource.REMOTE_SUBMIX, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufBytes,
            )
        } catch (e: Exception) {
            Log.w(TAG, "Remote Submix AudioRecord ctor", e)
            _state.value = Mode.RemoteSubmixDenied
            return null
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            _state.value = Mode.RemoteSubmixDenied
            return null
        }
        return rec
    }

    /**
     * USB Audio. Builds a normal MIC AudioRecord, then routes it to the
     * first USB input device discovered. If no USB input is plugged in,
     * surface `NoUsbDevice` rather than silently capturing the built-in
     * mic.
     */
    private fun buildUsbAudio(bufBytes: Int): AudioRecord? {
        val am = context.getSystemService(Context.AUDIO_SERVICE) as? AudioManager
        if (am == null) {
            _state.value = Mode.NoMic
            return null
        }
        val usbInput = am.getDevices(AudioManager.GET_DEVICES_INPUTS).firstOrNull {
            it.type == AudioDeviceInfo.TYPE_USB_DEVICE
                || it.type == AudioDeviceInfo.TYPE_USB_HEADSET
                || it.type == AudioDeviceInfo.TYPE_USB_ACCESSORY
        }
        if (usbInput == null) {
            Log.i(TAG, "USB Audio: no USB input device plugged in")
            _state.value = Mode.NoUsbDevice
            return null
        }
        Log.i(TAG, "USB Audio: routing to ${usbInput.productName} (${usbInput.type})")
        val rec = try {
            @Suppress("MissingPermission")
            AudioRecord(
                MediaRecorder.AudioSource.MIC, SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufBytes,
            )
        } catch (e: Exception) {
            Log.w(TAG, "USB Audio AudioRecord ctor", e)
            _state.value = Mode.NoMic
            return null
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            _state.value = Mode.NoMic
            return null
        }
        // setPreferredDevice requires API 23+ (covered by minSdk).
        if (!rec.setPreferredDevice(usbInput)) {
            Log.w(TAG, "USB Audio: setPreferredDevice rejected ${usbInput.productName}")
            // Continue anyway — system may still route to USB if it's the
            // active audio interface, just not by our explicit preference.
        }
        return rec
    }

    fun stop() {
        captureJob?.cancel()
        captureJob = null
        try { record?.stop() } catch (_: Exception) {}
        try { record?.release() } catch (_: Exception) {}
        record = null
        // Tear down the MediaProjection token so a fresh consent is
        // required next session — Android caches the consent for the
        // process lifetime, so this is a defence-in-depth measure rather
        // than a hard requirement.
        try { mediaProjection?.stop() } catch (_: Exception) {}
        mediaProjection = null
        // Drop the playback-capture foreground service notification.
        try { PlaybackCaptureService.stop(context) } catch (_: Exception) {}
        inPreview = false
        _envelope.value = 0f
        _rawPeak.value = 0f
        _beatPulse.value = 0f
        _master.value = 0f
        _state.value = Mode.Idle
        // BPM + beatCount intentionally NOT reset here so the operator
        // can still see the last-detected tempo for ~2 s of "carry
        // forward" after capture stops; reset() in startInternal clears
        // them on the next session.
    }

    companion object {
        private const val TAG = "MicAutoBrightness"
        private const val SAMPLE_RATE = 8000
        // 50 ms hop @ 8 kHz = 400 samples — meets <80 ms latency target.
        private const val HOP_SAMPLES = 400
    }
}
