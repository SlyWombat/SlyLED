package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.audio.MicAutoBrightness
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonElement
import javax.inject.Inject

@HiltViewModel
class LiveStageViewModel @Inject constructor(
    private val repository: SlyLedRepository,
    private val mic: MicAutoBrightness,
) : ViewModel() {

    // #804 — Auto Brightness state mirrors mic driver and feeds Stage button.
    val autoBrightnessState: StateFlow<MicAutoBrightness.Mode> = mic.state
    val autoBrightnessEnvelope: StateFlow<Float> = mic.envelope
    // #820 — raw mic peak (pre-follower) so the UI can show "mic is dead"
    // vs "floor too high" at a glance.
    val autoBrightnessRawPeak: StateFlow<Float> = mic.rawPeak
    // #820 — beat detection. UI renders a pulsing dot from beatPulse
    // (snaps to 1.0 on each beat, decays exponentially), and a BPM
    // readout that persists between beats so the operator sees the
    // tempo carry forward.
    val autoBrightnessBeatPulse: StateFlow<Float> = mic.beatPulse
    val autoBrightnessBpm: StateFlow<Float> = mic.bpm
    val autoBrightnessBeatCount: StateFlow<Int> = mic.beatCount
    // #820 — final master brightness (0..1, post-floor/ceiling). The
    // value the lights actually see — distinct from raw mic input
    // (pre-DSP) and envelope (post-follower, pre-floor).
    val autoBrightnessMaster: StateFlow<Float> = mic.master
    // Tunables exposed as flows so Stage and Settings UIs both recompose
    // when the *other* surface (or the persisted-prefs load) changes them.
    val autoBrightnessSensitivityFlow: StateFlow<Float> = mic.sensitivityFlow
    val autoBrightnessFloorFlow: StateFlow<Float> = mic.floorFlow
    val autoBrightnessCeilingFlow: StateFlow<Float> = mic.ceilingFlow
    val autoBrightnessAttackMsFlow: StateFlow<Float> = mic.attackMsFlow
    val autoBrightnessReleaseMsFlow: StateFlow<Float> = mic.releaseMsFlow
    // #820 — semantic Audio Source kind. See AudioSourceKind enum.
    val autoBrightnessAudioSourceKindFlow: StateFlow<com.slywombat.slyled.audio.AudioSourceKind> =
        mic.audioSourceKindFlow

    private val _autoBrightnessEnabled = MutableStateFlow(false)
    val autoBrightnessEnabled: StateFlow<Boolean> = _autoBrightnessEnabled.asStateFlow()

    // Drop new fast-path hops if the previous POST is still in flight.
    private var lastBrightnessJob: Job? = null

    private val _fixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val fixtures: StateFlow<List<Fixture>> = _fixtures.asStateFlow()

    private val _fixturesLive = MutableStateFlow<Map<String, JsonElement>>(emptyMap())
    val fixturesLive: StateFlow<Map<String, JsonElement>> = _fixturesLive.asStateFlow()

    private val _objects = MutableStateFlow<List<StageObject>>(emptyList())
    val objects: StateFlow<List<StageObject>> = _objects.asStateFlow()

    private val _layout = MutableStateFlow<Layout?>(null)
    val layout: StateFlow<Layout?> = _layout.asStateFlow()

    private val _stage = MutableStateFlow(Stage())
    val stage: StateFlow<Stage> = _stage.asStateFlow()

    private val _settings = MutableStateFlow(Settings())
    val settings: StateFlow<Settings> = _settings.asStateFlow()

    private val _timelineStatus = MutableStateFlow<TimelineStatus?>(null)
    val timelineStatus: StateFlow<TimelineStatus?> = _timelineStatus.asStateFlow()

    private val _timelines = MutableStateFlow<List<Timeline>>(emptyList())
    val timelines: StateFlow<List<Timeline>> = _timelines.asStateFlow()

    val showRunning: Boolean
        get() = _settings.value.runnerRunning

    private var initialized = false

    fun load() {
        if (initialized) return
        initialized = true

        // One-time loads
        viewModelScope.launch {
            try { _stage.value = repository.getStage() } catch (e: Exception) { Log.e(TAG, "getStage", e) }
            try { _layout.value = repository.getLayout() } catch (e: Exception) { Log.e(TAG, "getLayout", e) }
            try { _fixtures.value = repository.getFixtures() } catch (e: Exception) { Log.e(TAG, "getFixtures", e) }
            try { _timelines.value = repository.getTimelines() } catch (e: Exception) { Log.e(TAG, "getTimelines", e) }
        }

        // Poll settings every 3s
        viewModelScope.launch {
            while (true) {
                try { _settings.value = repository.getSettings() } catch (_: Exception) {}
                delay(3000)
            }
        }

        // Poll fixtures live (fast when show running, slow otherwise)
        viewModelScope.launch {
            while (true) {
                try { _fixturesLive.value = repository.getFixturesLive() } catch (_: Exception) {}
                delay(if (_settings.value.runnerRunning) 500L else 3000L)
            }
        }

        // Poll objects every 1.5s
        viewModelScope.launch {
            while (true) {
                try { _objects.value = repository.getObjects() } catch (_: Exception) {}
                delay(1500)
            }
        }

        // Poll timeline status every 1s when running
        viewModelScope.launch {
            while (true) {
                try {
                    val s = _settings.value
                    val tlId = s.activeTimeline
                    if (s.runnerRunning && tlId != null && tlId >= 0) {
                        _timelineStatus.value = repository.getTimelineStatus(tlId)
                    } else {
                        _timelineStatus.value = null
                    }
                } catch (_: Exception) {}
                delay(1000)
            }
        }

        // Refresh fixtures periodically (10s)
        viewModelScope.launch {
            while (true) {
                delay(10000)
                try { _fixtures.value = repository.getFixtures() } catch (_: Exception) {}
            }
        }
    }

    fun toggleShow() {
        viewModelScope.launch {
            try {
                val s = _settings.value
                if (s.runnerRunning) {
                    val tlId = s.activeTimeline
                    if (tlId != null && tlId >= 0) {
                        repository.stopTimeline(tlId)
                    }
                    // Also try show/stop for playlist mode
                    try { repository.stopShow() } catch (_: Exception) {}
                    // Clear live data immediately for visual feedback (#414)
                    _fixturesLive.value = emptyMap()
                    _timelineStatus.value = null
                } else {
                    // Try show/start first (plays playlist), fall back to active timeline
                    try {
                        repository.startShow()
                    } catch (_: Exception) {
                        val tlId = s.activeTimeline
                        if (tlId != null && tlId >= 0) {
                            repository.startTimeline(tlId)
                        }
                    }
                }
            } catch (e: Exception) { Log.e(TAG, "toggleShow", e) }
        }
    }

    fun setBrightness(value: Int) {
        viewModelScope.launch {
            try {
                repository.saveSettings(Settings(globalBrightness = value))
            } catch (e: Exception) { Log.e(TAG, "setBrightness", e) }
        }
    }

    // #804 — auto-brightness control surface. #820 adds audioSourceKind.
    fun configureAutoBrightness(
        sensitivity: Float? = null,
        floor: Float? = null,
        ceiling: Float? = null,
        attackMs: Float? = null,
        releaseMs: Float? = null,
        audioSourceKind: com.slywombat.slyled.audio.AudioSourceKind? = null,
    ) = mic.configure(sensitivity, floor, ceiling, attackMs, releaseMs, audioSourceKind)

    // #820 — preview / live capture lifecycle. Preview lets the modal
    // show the raw + envelope meters whenever the operator opens it,
    // even with Auto Brightness toggled off, so they can audition each
    // Audio Source without driving the lights.
    fun startAutoBrightnessPreview() = mic.startPreview(viewModelScope)
    fun stopAutoBrightnessPreview() {
        if (!_autoBrightnessEnabled.value) mic.stop()
    }
    fun setAutoBrightnessMediaProjection(
        mp: android.media.projection.MediaProjection?
    ) = mic.setMediaProjection(mp)

    fun setAutoBrightnessEnabled(enabled: Boolean) {
        if (enabled == _autoBrightnessEnabled.value) return
        if (enabled) {
            val started = mic.start(viewModelScope) { master ->
                // #854 — latest-wins: cancel the prior in-flight POST and
                // replace it with the new value. Pre-fix the in-flight
                // guard `if (lastBrightnessJob?.isActive == true) return`
                // permanently dropped every subsequent hop after the
                // FIRST stuck POST (TLS handshake delay, network blip,
                // OkHttp retry loop), because the stuck job kept
                // isActive=true for the full default Retrofit timeout
                // (~10 s). Operator saw "Listening" UI animate locally
                // while ZERO POSTs reached the orchestrator.
                //
                // Combined with the 2 s withTimeout below: the most
                // recent value always gets sent (current beat, not 5
                // beats ago); stuck calls get cancelled and replaced;
                // the failure mode "stuck forever after first hang"
                // becomes "self-healing latest-wins."
                lastBrightnessJob?.cancel()
                lastBrightnessJob = viewModelScope.launch {
                    try {
                        kotlinx.coroutines.withTimeout(2000L) {
                            repository.setMasterBrightness(master)
                        }
                    } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
                        Log.w(TAG, "fast brightness POST timed out (2s) value=$master")
                    } catch (e: kotlinx.coroutines.CancellationException) {
                        // Expected — superseded by a newer hop.
                    } catch (e: Exception) {
                        Log.w(TAG, "fast brightness POST failed value=$master", e)
                    }
                }
            }
            _autoBrightnessEnabled.value = started
            // #804 — persist intent so the next app launch resumes mic
            // capture without operator action (subject to permission).
            mic.setPersistedEnabled(started)
        } else {
            mic.stop()
            _autoBrightnessEnabled.value = false
            mic.setPersistedEnabled(false)
        }
    }

    fun autoBrightnessHasPermission(): Boolean = mic.hasPermission()
    fun autoBrightnessSensitivity(): Float = mic.sensitivity
    fun autoBrightnessFloor(): Float = mic.floor
    fun autoBrightnessCeiling(): Float = mic.ceiling
    fun autoBrightnessAttackMs(): Float = mic.attackMs
    fun autoBrightnessReleaseMs(): Float = mic.releaseMs

    override fun onCleared() {
        mic.stop()
        super.onCleared()
    }

    companion object {
        private const val TAG = "LiveStageVM"
    }
}
