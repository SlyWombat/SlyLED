package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class ControlViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    private val _timelines = MutableStateFlow<List<Timeline>>(emptyList())
    val timelines: StateFlow<List<Timeline>> = _timelines.asStateFlow()

    private val _settings = MutableStateFlow(Settings())
    val settings: StateFlow<Settings> = _settings.asStateFlow()

    private val _timelineStatus = MutableStateFlow<TimelineStatus?>(null)
    val timelineStatus: StateFlow<TimelineStatus?> = _timelineStatus.asStateFlow()

    private val _playlist = MutableStateFlow<ShowPlaylist?>(null)
    val playlist: StateFlow<ShowPlaylist?> = _playlist.asStateFlow()

    private val _showStatus = MutableStateFlow<ShowStatus?>(null)
    val showStatus: StateFlow<ShowStatus?> = _showStatus.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    private val _fixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val fixtures: StateFlow<List<Fixture>> = _fixtures.asStateFlow()

    // Controller mode state
    private val _controllerFixtureId = MutableStateFlow<Int?>(null)
    val controllerFixtureId: StateFlow<Int?> = _controllerFixtureId.asStateFlow()

    private val _controllerPanRange = MutableStateFlow(540f)
    val controllerPanRange: StateFlow<Float> = _controllerPanRange.asStateFlow()

    private val _controllerTiltRange = MutableStateFlow(270f)
    val controllerTiltRange: StateFlow<Float> = _controllerTiltRange.asStateFlow()

    private val _controllerInitialPan = MutableStateFlow(0.5f)
    val controllerInitialPan: StateFlow<Float> = _controllerInitialPan.asStateFlow()

    private val _controllerInitialTilt = MutableStateFlow(0.5f)
    val controllerInitialTilt: StateFlow<Float> = _controllerInitialTilt.asStateFlow()

    // Orientation signs from calibration — determines which way DMX values
    // physically move the beam. panSign=+1 means increasing pan goes one way,
    // -1 means the opposite. tiltSign defaults to -1 (typical for most fixtures).
    private val _controllerPanSign = MutableStateFlow(1)
    val controllerPanSign: StateFlow<Int> = _controllerPanSign.asStateFlow()

    private val _controllerTiltSign = MutableStateFlow(-1)
    val controllerTiltSign: StateFlow<Int> = _controllerTiltSign.asStateFlow()

    private val _controllerReady = MutableStateFlow(false)
    val controllerReady: StateFlow<Boolean> = _controllerReady.asStateFlow()

    private val _controllerConnected = MutableStateFlow(true)
    val controllerConnected: StateFlow<Boolean> = _controllerConnected.asStateFlow()

    private var aimErrorCount = 0

    private var initialized = false

    fun load() {
        if (initialized) return
        initialized = true

        // Initial fetch
        viewModelScope.launch {
            try { _timelines.value = repository.getTimelines() } catch (e: Exception) { Log.e(TAG, "getTimelines", e) }
            try { _playlist.value = repository.getShowPlaylist() } catch (_: Exception) {}
            try { _fixtures.value = repository.getFixtures() } catch (_: Exception) {}
        }

        // Poll settings every 3s
        viewModelScope.launch {
            while (true) {
                try { _settings.value = repository.getSettings() } catch (_: Exception) {}
                delay(3000)
            }
        }

        // Poll show status every 2s
        viewModelScope.launch {
            while (true) {
                try { _showStatus.value = repository.getShowStatus() } catch (_: Exception) {}
                delay(2000)
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
    }

    fun clearMessage() { _message.value = null }

    fun startTimeline(id: Int) {
        viewModelScope.launch {
            try {
                repository.startTimeline(id)
                _message.value = "Timeline started"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun startShow() {
        viewModelScope.launch {
            try {
                repository.startShow()
                _message.value = "Show started"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun stopShow() {
        viewModelScope.launch {
            try {
                // Stop active timeline
                val s = _settings.value
                val tlId = s.activeTimeline
                if (tlId != null && tlId >= 0) {
                    repository.stopTimeline(tlId)
                }
                // Also try show/stop for playlist
                try { repository.stopShow() } catch (_: Exception) {}
                _message.value = "Show stopped"
                _timelineStatus.value = null
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun setBrightness(value: Int) {
        viewModelScope.launch {
            try {
                repository.saveSettings(Settings(globalBrightness = value))
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun setLoop(enabled: Boolean) {
        viewModelScope.launch {
            try {
                val current = _playlist.value ?: ShowPlaylist()
                // Re-fetch and POST updated playlist with loop flag
                repository.getShowPlaylist() // ensure we have latest
                // The server expects the full playlist object
                _playlist.value = current.copy(loop = enabled)
                _message.value = if (enabled) "Loop enabled" else "Loop disabled"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun refreshTimelines() {
        viewModelScope.launch {
            try { _timelines.value = repository.getTimelines() } catch (_: Exception) {}
            try { _playlist.value = repository.getShowPlaylist() } catch (_: Exception) {}
        }
    }

    // ── Controller mode ─────────────────────────────────────────────

    fun enterControllerMode(fixtureId: Int) {
        _controllerFixtureId.value = fixtureId
        _controllerReady.value = false

        viewModelScope.launch {
            try {
                // Fetch channel info — includes panRange, tiltRange, panSign, tiltSign
                try {
                    val channelInfo = repository.getDmxFixtureChannels(fixtureId)
                    channelInfo["panRange"]?.let {
                        val v = (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                        if (v != null && v > 0) _controllerPanRange.value = v
                    }
                    channelInfo["tiltRange"]?.let {
                        val v = (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                        if (v != null && v > 0) _controllerTiltRange.value = v
                    }
                    channelInfo["panSign"]?.let {
                        val v = (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull()
                        if (v != null) _controllerPanSign.value = v
                    }
                    channelInfo["tiltSign"]?.let {
                        val v = (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull()
                        if (v != null) _controllerTiltSign.value = v
                    }
                    // Server-computed home position (horizontal toward audience)
                    channelInfo["homePan"]?.let {
                        val v = (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                        if (v != null) _controllerInitialPan.value = v
                    }
                    channelInfo["homeTilt"]?.let {
                        val v = (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                        if (v != null) _controllerInitialTilt.value = v
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Could not fetch channel info, using defaults", e)
                }

                // Fetch current live state to set initial pan/tilt
                var usedLiveState = false
                try {
                    val live = repository.getFixturesLive()
                    val fixturesJson = live["fixtures"]
                    if (fixturesJson is kotlinx.serialization.json.JsonArray) {
                        for (elem in fixturesJson) {
                            if (elem is kotlinx.serialization.json.JsonObject) {
                                val id = elem["id"]?.let {
                                    (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toIntOrNull()
                                }
                                if (id == fixtureId) {
                                    val pan = elem["pan"]?.let {
                                        (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                                    } ?: 0f
                                    val panFine = elem["panFine"]?.let {
                                        (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                                    }
                                    val tilt = elem["tilt"]?.let {
                                        (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                                    } ?: 0f
                                    val tiltFine = elem["tiltFine"]?.let {
                                        (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                                    }
                                    val dimmer = elem["dimmer"]?.let {
                                        (it as? kotlinx.serialization.json.JsonPrimitive)?.content?.toFloatOrNull()
                                    } ?: 0f

                                    // Only use live state if fixture is actually active
                                    if (dimmer > 0 || pan > 0 || tilt > 0) {
                                        _controllerInitialPan.value = if (panFine != null) {
                                            (pan * 256f + panFine) / 65535f
                                        } else {
                                            pan / 255f
                                        }
                                        _controllerInitialTilt.value = if (tiltFine != null) {
                                            (tilt * 256f + tiltFine) / 65535f
                                        } else {
                                            tilt / 255f
                                        }
                                        usedLiveState = true
                                    }
                                    break
                                }
                            }
                        }
                    }
                } catch (e: Exception) {
                    Log.w(TAG, "Could not read live fixture state", e)
                }

                // homePan/homeTilt from channel info is already set as default;
                // live state overrides it only if fixture is active

                // Send initial position + turn on white light
                try {
                    val initPan = _controllerInitialPan.value
                    val initTilt = _controllerInitialTilt.value
                    repository.aimFixtureDirect(fixtureId, initPan, initTilt)
                    repository.setFixtureOutput(fixtureId, 1f, 0f, 0f, 0f, 1f, 0f)
                } catch (_: Exception) {}

                _controllerReady.value = true
            } catch (e: Exception) {
                Log.e(TAG, "enterControllerMode", e)
                _message.value = "Error entering controller mode: ${e.message}"
                _controllerFixtureId.value = null
            }
        }
    }

    fun exitControllerMode() {
        val fid = _controllerFixtureId.value
        _controllerFixtureId.value = null
        _controllerReady.value = false
        _controllerConnected.value = true
        aimErrorCount = 0
        // Blackout the fixture on exit
        if (fid != null) {
            viewModelScope.launch {
                try { repository.setFixtureOutput(fid, 0f, 0f, 0f, 0f, 0f, 0f) } catch (_: Exception) {}
            }
        }
    }

    /** Called from overlay at ~20Hz with normalized 0-1 pan/tilt values. */
    fun aimFixture(fixtureId: Int, panNorm: Float, tiltNorm: Float) {
        viewModelScope.launch {
            try {
                repository.aimFixtureDirect(fixtureId, panNorm, tiltNorm)
                if (!_controllerConnected.value) {
                    _controllerConnected.value = true
                    aimErrorCount = 0
                }
            } catch (e: Exception) {
                aimErrorCount++
                // Show disconnected after 3 consecutive failures (~150ms)
                if (aimErrorCount >= 3) {
                    _controllerConnected.value = false
                }
                Log.w(TAG, "aimFixture failed: ${e.message}")
            }
        }
    }

    /** Called when user adjusts color/dimmer/strobe sliders. */
    fun setFixtureChannels(fixtureId: Int, dimmer: Float, red: Float, green: Float,
                           blue: Float, white: Float, strobe: Float) {
        viewModelScope.launch {
            try {
                repository.setFixtureOutput(fixtureId, dimmer, red, green, blue, white, strobe)
            } catch (e: Exception) {
                Log.w(TAG, "setFixtureChannels failed: ${e.message}")
            }
        }
    }

    companion object {
        private const val TAG = "ControlVM"
    }
}
