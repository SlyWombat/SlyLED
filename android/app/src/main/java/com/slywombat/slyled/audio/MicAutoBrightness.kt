package com.slywombat.slyled.audio

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
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
 * Lifecycle: [start] grabs the mic, runs a capture coroutine on Dispatchers.IO,
 * pushes 50 ms hops through the follower, and updates [state]/[envelope]
 * StateFlows. [stop] releases the mic.
 *
 * Tunables (sensitivity / floor / ceiling / attack / release) and the enabled
 * flag persist via [ServerPreferences] across app restarts. The follower's
 * in-memory values are seeded from DataStore at construction; every
 * [configure] write fires a debounced save back to DataStore.
 *
 * Latency budget: AudioRecord min buffer + 50 ms hop is well under the 80 ms
 * mic-to-DMX target on a stock Pixel.
 */
class MicAutoBrightness(
    private val context: Context,
    private val prefs: ServerPreferences,
) {
    enum class Mode { Idle, Listening, NoMic, PermissionDenied, Clipping }

    private val _state = MutableStateFlow(Mode.Idle)
    val state: StateFlow<Mode> = _state.asStateFlow()

    private val _envelope = MutableStateFlow(0f)
    val envelope: StateFlow<Float> = _envelope.asStateFlow()

    // #804 — expose tunables as StateFlows so Compose UI recomposes when
    // either the Stage modal OR the Settings panel mutates them, AND when
    // the persisted prefs land asynchronously after construction. Pre-fix
    // the UI cached one-shot values via `remember`, which never refreshed.
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
    // #804 — last persisted enabled flag. The Stage / Settings UI reads
    // this on app start to decide whether to auto-resume the mic.
    private val _persistedEnabled = MutableStateFlow(false)
    val persistedEnabled: StateFlow<Boolean> = _persistedEnabled.asStateFlow()

    private val follower = EnvelopeFollower(sampleRateHz = SAMPLE_RATE)
    private var captureJob: Job? = null
    private var record: AudioRecord? = null

    // Owns the persistence read + debounced writes. Lives for the full
    // singleton lifetime; the app is killed → scope dies with the process.
    private val ioScope = MainScope()

    init {
        // Async load of persisted tunables. The follower is constructed
        // with EnvelopeFollower defaults; once DataStore returns, we
        // overwrite both the follower fields and the StateFlows so any
        // observing UI recomposes.
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

    fun configure(
        sensitivity: Float? = null,
        floor: Float? = null,
        ceiling: Float? = null,
        attackMs: Float? = null,
        releaseMs: Float? = null,
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
        if (persist) this.persist()
    }

    /** Persist the current tunables + enabled flag to DataStore. */
    private fun persist(enabled: Boolean? = null) {
        val want = AutoBrightnessPrefs(
            enabled = enabled ?: _persistedEnabled.value,
            sensitivity = follower.sensitivity,
            floor = follower.floor,
            ceiling = follower.ceiling,
            attackMs = follower.attackMs,
            releaseMs = follower.releaseMs,
        )
        if (enabled != null) _persistedEnabled.value = enabled
        ioScope.launch {
            try { prefs.saveAutoBrightness(want) }
            catch (e: Exception) { Log.w(TAG, "Auto Brightness prefs save failed", e) }
        }
    }

    /** Record the desired enabled flag — survives app restart. */
    fun setPersistedEnabled(enabled: Boolean) {
        if (enabled == _persistedEnabled.value) return
        persist(enabled = enabled)
    }

    fun hasPermission(): Boolean = ContextCompat.checkSelfPermission(
        context, Manifest.permission.RECORD_AUDIO
    ) == PackageManager.PERMISSION_GRANTED

    /**
     * Start capture. [onMaster] fires after each hop with the latest 0..255
     * brightness. Returns true if capture started; false on permission /
     * device errors (state flow reflects the reason).
     */
    fun start(scope: CoroutineScope, onMaster: (Int) -> Unit): Boolean {
        if (captureJob?.isActive == true) return true
        if (!hasPermission()) {
            _state.value = Mode.PermissionDenied
            return false
        }
        val minBuf = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        if (minBuf <= 0) {
            _state.value = Mode.NoMic
            return false
        }
        val bufBytes = maxOf(minBuf, HOP_SAMPLES * 2)
        val rec = try {
            @Suppress("MissingPermission")
            AudioRecord(
                MediaRecorder.AudioSource.UNPROCESSED.takeIf { supportsUnprocessed() }
                    ?: MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufBytes,
            )
        } catch (e: Exception) {
            Log.w(TAG, "AudioRecord ctor", e)
            _state.value = Mode.NoMic
            return false
        }
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            _state.value = Mode.NoMic
            return false
        }
        record = rec
        rec.startRecording()
        _state.value = Mode.Listening
        follower.reset()

        captureJob = scope.launch(Dispatchers.IO) {
            val pcm = ShortArray(HOP_SAMPLES)
            val floats = FloatArray(HOP_SAMPLES)
            try {
                while (isActive) {
                    val n = rec.read(pcm, 0, HOP_SAMPLES)
                    if (n <= 0) continue
                    var clipped = false
                    for (i in 0 until n) {
                        val s = pcm[i].toInt()
                        if (s >= 32760 || s <= -32760) clipped = true
                        floats[i] = s / 32768f
                    }
                    val env = follower.process(floats, n)
                    _envelope.value = env
                    val master = follower.toMaster(env)
                    withContext(Dispatchers.Main) { onMaster(master) }
                    _state.value = if (clipped) Mode.Clipping else Mode.Listening
                }
            } catch (e: Exception) {
                Log.e(TAG, "capture loop", e)
                _state.value = Mode.NoMic
            }
        }
        return true
    }

    fun stop() {
        captureJob?.cancel()
        captureJob = null
        try { record?.stop() } catch (_: Exception) {}
        try { record?.release() } catch (_: Exception) {}
        record = null
        _envelope.value = 0f
        _state.value = Mode.Idle
    }

    private fun supportsUnprocessed(): Boolean = try {
        // UNPROCESSED requires API 24+; Manifest minSdk gates this.
        android.os.Build.VERSION.SDK_INT >= 24
    } catch (_: Throwable) { false }

    companion object {
        private const val TAG = "MicAutoBrightness"
        private const val SAMPLE_RATE = 8000
        // 50 ms hop @ 8 kHz = 400 samples — meets <80 ms latency target.
        private const val HOP_SAMPLES = 400
    }
}
