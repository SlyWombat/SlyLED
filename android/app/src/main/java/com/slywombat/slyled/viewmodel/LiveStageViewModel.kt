package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.audio.MicAutoBrightness
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import com.slywombat.slyled.network.UdpClient
import dagger.hilt.android.lifecycle.HiltViewModel
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
    private val udpClient: UdpClient,
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

    // #861 — wraps every 256 hops; only used by the orchestrator's
    // hop log for debugging out-of-order arrivals (UDP gives no
    // delivery guarantee). Not load-bearing for correctness.
    private var brightnessSeq: Int = 0

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
                // #861 — UDP fire-and-forget replaces the prior HTTP
                // POST /api/brightness fast path. The HTTP path failed
                // under live load (live-test 2026-05-08: zero POSTs
                // landed in 3.5 min of music despite UI showing master
                // bouncing 150–255) due to TCP retransmit + OkHttp
                // connection-pool churn at audio rate. UDP mirrors the
                // existing gyro telemetry pattern on :4210 and survives
                // packet loss the same way audio always does — drop a
                // hop, the next one (50 ms later) replaces it.
                val host = repository.baseUrl
                    ?.removePrefix("http://")
                    ?.substringBefore(':')
                    ?.substringBefore('/')
                if (host.isNullOrBlank()) {
                    Log.w(TAG, "AUTOBRI_PUSH skipped — no baseUrl")
                    return@start
                }
                val seq = brightnessSeq.also { brightnessSeq = (it + 1) and 0xFF }
                // #906 — flags aligned with iOS UdpClient.swift semantics:
                // bit0 = beat-pulse active (iOS: `gotBeat || beatPulse > 0.5`;
                // here a fresh beat snaps the pulse StateFlow to 1.0 before
                // this callback fires, so `> 0.5` covers both cases),
                // bit1 = mic-gated (this push only ever originates from the
                // live audio-capture loop, matching iOS's unconditional
                // `micGated: true`). Server treats flags as diagnostic-only.
                var flags = 0x02
                if (mic.beatPulse.value > 0.5f) flags = flags or 0x01
                udpClient.sendAutoBrightnessPush(host, master, flags = flags, seq = seq)
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
