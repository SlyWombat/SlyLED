package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.ServerPreferences
import com.slywombat.slyled.data.repository.SlyLedRepository
import com.slywombat.slyled.data.repository.UserPosition
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class ControlViewModel @Inject constructor(
    private val repository: SlyLedRepository,
    private val serverPrefs: ServerPreferences,
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

    // #479 — live mover-control status (engine + my claim). Null when
    // no controller session is active or the poll hasn't returned yet.
    private val _controllerStatus = MutableStateFlow<MoverControlClaim?>(null)
    val controllerStatus: StateFlow<MoverControlClaim?> = _controllerStatus.asStateFlow()

    private val _engineRunning = MutableStateFlow(true)
    val engineRunning: StateFlow<Boolean> = _engineRunning.asStateFlow()

    // #427 — Pointer mode session state. Independent of controllerFixtureId
    // so the overlay branch in ControlScreen can pick the right composable.
    private val _pointerFixtureId = MutableStateFlow<Int?>(null)
    val pointerFixtureId: StateFlow<Int?> = _pointerFixtureId.asStateFlow()

    private val _pointerReady = MutableStateFlow(false)
    val pointerReady: StateFlow<Boolean> = _pointerReady.asStateFlow()

    // #427 — operator stage position (mm). Backed by ServerPreferences so
    // it survives across sessions; defaults to stage centre at standing height.
    private val _userPosition = MutableStateFlow(UserPosition(2000f, 2000f, 1700f))
    val userPosition: StateFlow<UserPosition> = _userPosition.asStateFlow()

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
            // #427 — restore the operator's saved stage position so pointer
            // mode is usable without re-entering it every launch.
            try { _userPosition.value = serverPrefs.loadUserPosition() } catch (_: Exception) {}
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

        // #479 — poll /api/mover-control/status every 2s while a
        // controller OR pointer session is active. Surfaces the
        // engine-running signal + claim freshness so the operator can
        // see whether the server is actually receiving + transmitting
        // their input.
        viewModelScope.launch {
            while (true) {
                val activeFid = _controllerFixtureId.value ?: _pointerFixtureId.value
                if (activeFid != null) {
                    try {
                        val status = repository.getMoverControlStatus()
                        _engineRunning.value = status.engine.running
                        _controllerStatus.value = status.claims
                            .firstOrNull { it.moverId == activeFid }
                    } catch (_: Exception) {
                        // Don't blank out on transient errors — keep
                        // last good values; the orient streamer's own
                        // disconnect signal handles hard staleness.
                    }
                } else {
                    _controllerStatus.value = null
                }
                delay(2000)
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
        // Release claim — server handles blackout. #754 BUG-C: also fire
        // the going-offline signal so the SPA dashboard hides the row
        // immediately instead of falling through to "slow/reconnecting".
        if (fid != null) {
            viewModelScope.launch {
                try { repository.moverRelease(fid) } catch (_: Exception) {}
                try { repository.disconnectRemote() } catch (_: Exception) {}
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

    /** Called from overlay at ~20Hz with raw device orientation in degrees.
     *  Optionally passes the native rotation-vector quaternion (#485) so the
     *  server primitive can skip our Euler→quat reconstruction. */
    fun sendOrientation(fixtureId: Int, roll: Float, pitch: Float, yaw: Float,
                         quat: FloatArray? = null) {
        viewModelScope.launch {
            try {
                repository.moverOrient(fixtureId, roll, pitch, yaw, quat)
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

    /** Momentary strobe (#482). press=true on finger-down, false on lift. */
    fun setFlash(fixtureId: Int, on: Boolean) {
        viewModelScope.launch {
            try {
                repository.moverFlash(fixtureId, on)
            } catch (e: Exception) {
                Log.w(TAG, "moverFlash failed: ${e.message}")
            }
        }
    }

    /** EMA smoothing factor 0.05-1.0 (#481). */
    fun setSmoothing(fixtureId: Int, smoothing: Float) {
        viewModelScope.launch {
            try {
                repository.moverSmoothing(fixtureId, smoothing)
            } catch (e: Exception) {
                Log.w(TAG, "moverSmoothing failed: ${e.message}")
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

    // ── Pointer mode (#427) ────────────────────────────────────────────
    //
    // Pointer mode treats the phone as a laser pointer in stage space.
    // The overlay computes phone-aim → ray-floor intersection client side
    // and POSTs {targetX,targetY,targetZ} (mm) to /api/calibration/mover/
    // <fid>/aim. The server runs the SMART path when present (#720) or
    // returns 400 fixture_not_calibrated when world-XYZ aim isn't
    // supported yet — we gate the toggle on the capability check.

    fun enterPointerMode(fixtureId: Int) {
        _pointerFixtureId.value = fixtureId
        _pointerReady.value = false

        viewModelScope.launch {
            // Pre-flight: pointer mode aims by world XYZ, which requires
            // a SMART calibration (#738). Bounce out cleanly if the
            // fixture is angular-only or has no home anchors yet.
            try {
                val cal = repository.getMoverCalibrationStatus(fixtureId)
                if (!cal.capabilities.worldXYZ) {
                    _message.value = "Pointer mode needs SMART calibration on this fixture"
                    _pointerFixtureId.value = null
                    return@launch
                }
            } catch (e: Exception) {
                _message.value = "Couldn't read calibration status: ${e.message}"
                _pointerFixtureId.value = null
                return@launch
            }

            try {
                // Same claim+start dance as Controller mode — exclusivity
                // + light on so the operator sees the beam land.
                val claimResult = repository.moverClaim(fixtureId)
                if (!claimResult.ok) {
                    _message.value = claimResult.err ?: "Mover claimed by another device"
                    _pointerFixtureId.value = null
                    return@launch
                }
                val startResult = repository.moverStart(fixtureId)
                if (!startResult.ok) {
                    _message.value = "Failed to start mover stream"
                    try { repository.moverRelease(fixtureId) } catch (_: Exception) {}
                    _pointerFixtureId.value = null
                    return@launch
                }
                _pointerReady.value = true
            } catch (e: Exception) {
                Log.e(TAG, "enterPointerMode", e)
                _message.value = "Error entering pointer mode: ${e.message}"
                _pointerFixtureId.value = null
                try { repository.moverRelease(fixtureId) } catch (_: Exception) {}
            }
        }
    }

    fun exitPointerMode() {
        val fid = _pointerFixtureId.value
        _pointerFixtureId.value = null
        _pointerReady.value = false
        _controllerConnected.value = true
        orientErrorCount = 0
        // Same #754 BUG-C disconnect signal as Controller mode.
        if (fid != null) {
            viewModelScope.launch {
                try { repository.moverRelease(fid) } catch (_: Exception) {}
                try { repository.disconnectRemote() } catch (_: Exception) {}
            }
        }
    }

    /** POST stage-XYZ target (mm) to /api/calibration/mover/<fid>/aim. The
     *  overlay calls this at ~20 Hz from its sensor listener. Bounces back
     *  to the mode selector on fixture_not_calibrated (the SMART model was
     *  cleared mid-session). */
    fun aimPointerTarget(fixtureId: Int, targetX: Double, targetY: Double,
                          targetZ: Double) {
        viewModelScope.launch {
            try {
                val res = repository.moverAim(fixtureId, targetX, targetY, targetZ)
                if (!res.ok) {
                    val err = res.err ?: ""
                    if (err.contains("not calibrated", ignoreCase = true)) {
                        _message.value = "Pointer mode needs SMART calibration on this fixture"
                        exitPointerMode()
                    }
                }
                if (!_controllerConnected.value) {
                    _controllerConnected.value = true
                    orientErrorCount = 0
                }
            } catch (e: retrofit2.HttpException) {
                // Server returns HTTP 400 {err:"Fixture not calibrated"} when
                // the SMART model was deleted between toggle and aim — bounce
                // out gracefully so the operator isn't stuck in a dead overlay.
                val body = try { e.response()?.errorBody()?.string().orEmpty() } catch (_: Exception) { "" }
                if (e.code() == 400 && body.contains("not calibrated", ignoreCase = true)) {
                    _message.value = "Pointer mode needs SMART calibration on this fixture"
                    exitPointerMode()
                } else {
                    orientErrorCount++
                    if (orientErrorCount >= 3) _controllerConnected.value = false
                    Log.w(TAG, "aimPointerTarget HTTP ${e.code()}: $body")
                }
            } catch (e: Exception) {
                orientErrorCount++
                if (orientErrorCount >= 3) _controllerConnected.value = false
                Log.w(TAG, "aimPointerTarget failed: ${e.message}")
            }
        }
    }

    /** Persist the operator's stage position in mm for the next session. */
    fun setUserPosition(xMm: Float, yMm: Float, zMm: Float) {
        val pos = UserPosition(xMm, yMm, zMm)
        _userPosition.value = pos
        viewModelScope.launch {
            try { serverPrefs.saveUserPosition(pos) } catch (_: Exception) {}
        }
    }

    companion object {
        private const val TAG = "ControlVM"
    }
}
