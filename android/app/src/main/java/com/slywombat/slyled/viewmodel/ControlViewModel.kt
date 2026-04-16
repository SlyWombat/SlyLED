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

    private val _controllerReady = MutableStateFlow(false)
    val controllerReady: StateFlow<Boolean> = _controllerReady.asStateFlow()

    private val _controllerConnected = MutableStateFlow(true)
    val controllerConnected: StateFlow<Boolean> = _controllerConnected.asStateFlow()

    private var orientErrorCount = 0

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

    // ── Controller mode (unified mover-control API) ───────────────────

    fun enterControllerMode(fixtureId: Int) {
        _controllerFixtureId.value = fixtureId
        _controllerReady.value = false

        viewModelScope.launch {
            try {
                // Step 1: Claim the mover
                val claimResult = repository.moverClaim(fixtureId)
                if (!claimResult.ok) {
                    _message.value = claimResult.err ?: "Mover claimed by another device"
                    _controllerFixtureId.value = null
                    return@launch
                }

                // Step 2: Start streaming (turns on light via server)
                val startResult = repository.moverStart(fixtureId)
                if (!startResult.ok) {
                    _message.value = "Failed to start mover stream"
                    try { repository.moverRelease(fixtureId) } catch (_: Exception) {}
                    _controllerFixtureId.value = null
                    return@launch
                }

                _controllerReady.value = true
            } catch (e: Exception) {
                Log.e(TAG, "enterControllerMode", e)
                _message.value = "Error entering controller mode: ${e.message}"
                _controllerFixtureId.value = null
                // Best effort release
                try { repository.moverRelease(fixtureId) } catch (_: Exception) {}
            }
        }
    }

    fun exitControllerMode() {
        val fid = _controllerFixtureId.value
        _controllerFixtureId.value = null
        _controllerReady.value = false
        _controllerConnected.value = true
        orientErrorCount = 0
        // Release claim — server handles blackout
        if (fid != null) {
            viewModelScope.launch {
                try { repository.moverRelease(fid) } catch (_: Exception) {}
            }
        }
    }

    /** Called when user presses the calibrate button (finger down). */
    fun calibrateStart(fixtureId: Int, roll: Float, pitch: Float, yaw: Float) {
        viewModelScope.launch {
            try {
                repository.moverCalibrateStart(fixtureId, roll, pitch, yaw)
            } catch (e: Exception) {
                Log.w(TAG, "calibrateStart failed: ${e.message}")
            }
        }
    }

    /** Called when user releases the calibrate button (finger up). */
    fun calibrateEnd(fixtureId: Int, roll: Float, pitch: Float, yaw: Float) {
        viewModelScope.launch {
            try {
                repository.moverCalibrateEnd(fixtureId, roll, pitch, yaw)
            } catch (e: Exception) {
                Log.w(TAG, "calibrateEnd failed: ${e.message}")
            }
        }
    }

    /** Called from overlay at ~20Hz with raw device orientation in degrees. */
    fun sendOrientation(fixtureId: Int, roll: Float, pitch: Float, yaw: Float) {
        viewModelScope.launch {
            try {
                repository.moverOrient(fixtureId, roll, pitch, yaw)
                if (!_controllerConnected.value) {
                    _controllerConnected.value = true
                    orientErrorCount = 0
                }
            } catch (e: Exception) {
                orientErrorCount++
                // Show disconnected after 3 consecutive failures (~150ms)
                if (orientErrorCount >= 3) {
                    _controllerConnected.value = false
                }
                Log.w(TAG, "sendOrientation failed: ${e.message}")
            }
        }
    }

    /** Called when user adjusts color via color wheel or dimmer slider.
     *  Sends RGB 0-255 + optional dimmer 0-255 to server. */
    fun setMoverColor(fixtureId: Int, r: Int, g: Int, b: Int, dimmer: Int? = null) {
        viewModelScope.launch {
            try {
                repository.moverColor(fixtureId, r, g, b, dimmer)
            } catch (e: Exception) {
                Log.w(TAG, "setMoverColor failed: ${e.message}")
            }
        }
    }

    companion object {
        private const val TAG = "ControlVM"
    }
}
