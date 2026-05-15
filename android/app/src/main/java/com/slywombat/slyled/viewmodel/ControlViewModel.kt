package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.ServerPreferences
import com.slywombat.slyled.data.repository.SlyLedRepository
import com.slywombat.slyled.data.repository.UserPosition
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.putJsonObject
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

    // #888 — DMX-profile catalogue + live fixture state for Grab + Fixtures pages.
    // The orchestrator exposes profiles via `/api/dmx-profiles`; we use the
    // catalogue to look up panRange + shortcuts per fixture.
    private val _profiles = MutableStateFlow<List<DmxProfile>>(emptyList())
    val profiles: StateFlow<List<DmxProfile>> = _profiles.asStateFlow()

    private val _fixturesLive = MutableStateFlow<Map<String, kotlinx.serialization.json.JsonElement>>(emptyMap())
    val fixturesLive: StateFlow<Map<String, kotlinx.serialization.json.JsonElement>> = _fixturesLive.asStateFlow()

    // #888 — Grab-page favourites + Shows ranking, persisted per-device.
    private val _favouriteMovers = MutableStateFlow<Set<Int>>(emptySet())
    val favouriteMovers: StateFlow<Set<Int>> = _favouriteMovers.asStateFlow()

    private val _starredTimelines = MutableStateFlow<Set<Int>>(emptySet())
    val starredTimelines: StateFlow<Set<Int>> = _starredTimelines.asStateFlow()

    private val _lastPlayedAt = MutableStateFlow<Map<Int, Long>>(emptyMap())
    val lastPlayedAt: StateFlow<Map<Int, Long>> = _lastPlayedAt.asStateFlow()

    // #888 §6.2 — claim-conflict pending takeover. Set when the operator
    // tries to grab a mover currently held by another client; cleared
    // on confirm (which retries with force=true) or cancel.
    data class PendingTakeover(val fixtureId: Int, val fixtureName: String, val heldBy: String)
    private val _pendingTakeover = MutableStateFlow<PendingTakeover?>(null)
    val pendingTakeover: StateFlow<PendingTakeover?> = _pendingTakeover.asStateFlow()

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
    // #759 — first failure timestamp of the current sustained-error window.
    // Cleared on the next successful orient. After kReleaseAfterFailureMs of
    // continuous failures, the active overlay is exited (claim release +
    // blackout) so an unreachable orchestrator doesn't leave the operator
    // stuck on a frozen screen.
    private var firstOrientFailureTs: Long = 0L
    private val kReleaseAfterFailureMs = 5_000L

    private var initialized = false

    fun load() {
        if (initialized) return
        initialized = true

        // Initial fetch. Each fetch is isolated: pre-fix a single
        // sequential coroutine meant one fetch throwing a non-Exception
        // Throwable (Error, etc.) — which `catch (Exception)` misses —
        // killed the whole coroutine, so every fetch AFTER the failing
        // one silently never ran. Symptom: getFixtures dies → both
        // getDmxProfiles and the prefs restores never happen → the Grab
        // and Fixtures pages are permanently empty with no log.
        // `_safeLoad` catches Throwable (rethrowing CancellationException
        // so structured concurrency still works) and logs every failure.
        viewModelScope.launch {
            _safeLoad("getTimelines")     { _timelines.value = repository.getTimelines() }
            _safeLoad("getShowPlaylist")  { _playlist.value = repository.getShowPlaylist() }
            _safeLoad("getFixtures")      { _fixtures.value = repository.getFixtures() }
            _safeLoad("getDmxProfiles")   { _profiles.value = repository.getDmxProfiles() }
            // #427 — restore the operator's saved stage position so pointer
            // mode is usable without re-entering it every launch.
            _safeLoad("loadUserPosition") { _userPosition.value = serverPrefs.loadUserPosition() }
            // #888 — restore Grab favourites + Shows starred + last-played from prefs.
            _safeLoad("loadFavouriteMovers")  { _favouriteMovers.value = serverPrefs.loadFavouriteMovers() }
            _safeLoad("loadStarredTimelines") { _starredTimelines.value = serverPrefs.loadStarredTimelines() }
            _safeLoad("loadLastPlayedAt")     { _lastPlayedAt.value = serverPrefs.loadLastPlayedAt() }
        }

        // #888 — Poll fixtures-live every 1.5s for Grab-tile colour + arrow.
        // Pace matches LiveStageViewModel; both subscribers consume independently.
        viewModelScope.launch {
            while (true) {
                try { _fixturesLive.value = repository.getFixturesLive() } catch (_: Exception) {}
                delay(if (_settings.value.runnerRunning) 500L else 1500L)
            }
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

    /** Run one load step, surviving any failure. Catches Throwable (not
     *  just Exception) so an Error in one step can't kill the rest of
     *  the load coroutine; rethrows CancellationException so coroutine
     *  cancellation still propagates. Failures are logged, never silent. */
    private suspend fun _safeLoad(name: String, block: suspend () -> Unit) {
        try {
            block()
        } catch (e: CancellationException) {
            throw e
        } catch (t: Throwable) {
            Log.e(TAG, "load step '$name' failed: "
                       + "${t.javaClass.simpleName}: ${t.message}", t)
        }
    }

    // #888 — Grab + Shows persistence helpers.

    fun toggleFavouriteMover(fid: Int) {
        val cur = _favouriteMovers.value
        val next = if (fid in cur) cur - fid else cur + fid
        _favouriteMovers.value = next
        viewModelScope.launch {
            try { serverPrefs.saveFavouriteMovers(next) } catch (_: Exception) {}
        }
    }

    fun toggleStarredTimeline(tid: Int) {
        val cur = _starredTimelines.value
        val next = if (tid in cur) cur - tid else cur + tid
        _starredTimelines.value = next
        viewModelScope.launch {
            try { serverPrefs.saveStarredTimelines(next) } catch (_: Exception) {}
        }
    }

    fun recordTimelineStart(tid: Int) {
        val now = System.currentTimeMillis()
        val next = _lastPlayedAt.value + (tid to now)
        _lastPlayedAt.value = next
        viewModelScope.launch {
            try { serverPrefs.saveLastPlayedAt(next) } catch (_: Exception) {}
        }
    }

    // Best-effort: pull a holder hint out of a server err message.
    // Server sends strings like "Mover claimed by Phone-Bob" or
    // "Mover already held by SPA". Anything between `by ` and end-of-
    // string is treated as the client name. Returns null if no match.
    private fun extractHolderName(err: String): String? {
        val idx = err.lowercase().indexOf(" by ")
        if (idx < 0) return null
        val tail = err.substring(idx + 4).trim().trimEnd('.', ',', ';')
        return tail.ifBlank { null }
    }

    fun cancelTakeover() { _pendingTakeover.value = null }

    fun confirmTakeover() {
        val pending = _pendingTakeover.value ?: return
        _pendingTakeover.value = null
        enterControllerMode(pending.fixtureId, force = true)
    }

    // #888 — Page-level safety actions backing v3 design §6.5.

    fun sendAllMoversHome() {
        viewModelScope.launch {
            try {
                val r = repository.moverAllHome()
                // OkResponse just carries `ok`; the moved/skipped detail
                // lives in the response body we don't currently surface.
                _message.value = if (r.ok) "Movers sent home"
                                  else "All-home failed"
            } catch (e: Exception) {
                _message.value = "All-home failed: ${e.message}"
            }
        }
    }

    fun stopAllEffects() {
        viewModelScope.launch {
            // Fire both in parallel — they're independent server-side.
            // Track per-side outcomes so the snackbar reflects reality
            // instead of always claiming success.
            var strobesOk = false; var strobesErr: String? = null
            var effectsOk = false; var effectsErr: String? = null
            val strobes = launch {
                try { strobesOk = repository.killStrobes().ok }
                catch (e: Exception) { strobesErr = e.message }
            }
            val effects = launch {
                try { effectsOk = repository.killEffects().ok }
                catch (e: Exception) { effectsErr = e.message }
            }
            strobes.join(); effects.join()
            _message.value = when {
                strobesOk && effectsOk -> "Strobes + effects stopped"
                strobesOk && !effectsOk -> "Strobes stopped; effects failed${effectsErr?.let { " — $it" } ?: ""}"
                !strobesOk && effectsOk -> "Effects stopped; strobes failed${strobesErr?.let { " — $it" } ?: ""}"
                else -> "Stop all effects failed${strobesErr?.let { " — $it" } ?: ""}"
            }
        }
    }

    // #888 — single-fixture quick actions for the Grab long-press menu.
    // All go through aimFixtureDirect (/api/fixtures/<fid>/dmx-test)
    // which takes normalised pan/tilt/dimmer — no claim required.

    fun flashFixture(fid: Int) {
        viewModelScope.launch {
            try {
                val on = buildJsonObject { put("dimmer", JsonPrimitive(1.0)) }
                val off = buildJsonObject { put("dimmer", JsonPrimitive(0.0)) }
                repository.aimFixtureDirect(fid, on)
                delay(180)
                repository.aimFixtureDirect(fid, off)
            } catch (e: Exception) {
                _message.value = "Flash failed: ${e.message}"
            }
        }
    }

    fun homeFixture(fid: Int) {
        viewModelScope.launch {
            try {
                val body = buildJsonObject {
                    put("pan", JsonPrimitive(0.5))
                    put("tilt", JsonPrimitive(0.5))
                    put("dimmer", JsonPrimitive(0.0))
                }
                repository.aimFixtureDirect(fid, body)
                _message.value = "Fixture sent home"
            } catch (e: Exception) {
                _message.value = "Home failed: ${e.message}"
            }
        }
    }

    fun blackoutFixture(fid: Int) {
        viewModelScope.launch {
            try {
                val body = buildJsonObject { put("dimmer", JsonPrimitive(0.0)) }
                repository.aimFixtureDirect(fid, body)
            } catch (e: Exception) {
                _message.value = "Blackout failed: ${e.message}"
            }
        }
    }

    fun nextShow() {
        viewModelScope.launch {
            try {
                repository.nextShow()
            } catch (e: Exception) {
                _message.value = "Next failed: ${e.message}"
            }
        }
    }

    /**
     * #888 — Fixtures-page shortcut renderer needs full profile JSON.
     * Cache by profileId so N fixtures sharing one profile don't each
     * fire concurrent GETs (Pixel WiFi on a busy rig can RTT > 500 ms).
     * In-flight requests share a Deferred so only one network call
     * lands per profileId.
     */
    private val _profileFullCache = mutableMapOf<String, kotlinx.serialization.json.JsonObject>()
    private val _profileFullInFlight = mutableMapOf<String, Deferred<kotlinx.serialization.json.JsonObject>>()
    private val _profileFullMutex = Mutex()

    suspend fun fetchProfileFull(profileId: String): kotlinx.serialization.json.JsonObject {
        _profileFullCache[profileId]?.let { return it }
        val deferred: Deferred<kotlinx.serialization.json.JsonObject>
        _profileFullMutex.lock()
        try {
            _profileFullCache[profileId]?.let { cached ->
                _profileFullMutex.unlock()
                return cached
            }
            deferred = _profileFullInFlight[profileId] ?: viewModelScope.async(start = CoroutineStart.LAZY) {
                try {
                    val full = repository.getDmxProfileFull(profileId)
                    _profileFullCache[profileId] = full
                    full
                } finally {
                    _profileFullInFlight.remove(profileId)
                }
            }.also { _profileFullInFlight[profileId] = it }
        } finally {
            if (_profileFullMutex.isLocked) _profileFullMutex.unlock()
        }
        return deferred.await()
    }

    /** #888 — write raw bytes to specific channel offsets. */
    fun channelWrite(fid: Int, offsetToByte: Map<Int, Int>) {
        if (offsetToByte.isEmpty()) return
        viewModelScope.launch {
            try {
                val writes = kotlinx.serialization.json.buildJsonObject {
                    putJsonObject("writes") {
                        for ((off, byte) in offsetToByte) {
                            put(off.toString(), kotlinx.serialization.json.JsonPrimitive(byte))
                        }
                    }
                }
                repository.channelWrite(fid, writes)
            } catch (e: Exception) {
                _message.value = "Channel write failed: ${e.message}"
            }
        }
    }

    fun startTimeline(id: Int) {
        viewModelScope.launch {
            try {
                repository.startTimeline(id)
                _message.value = "Timeline started"
                // #888 — track for Shows ranking
                recordTimelineStart(id)
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
                _playlist.value = current.copy(loopAll = enabled)
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

    fun enterControllerMode(fixtureId: Int, force: Boolean = false) {
        // #752 — explicit clear of stale per-fixture state from any
        // previous session. Without this, `_controllerStatus`,
        // `_controllerConnected`, and the orient-failure counters
        // retain their values from the last fixture and the operator
        // sees the wrong status for the new claim until the 2 s poll
        // lands. Sometimes long enough that the gyro times out and
        // the claim reverts before the UI catches up — exactly the
        // "per-fixture stale-state inversion" the issue describes.
        _controllerStatus.value = null
        _controllerConnected.value = true
        _engineRunning.value = true
        orientErrorCount = 0
        firstOrientFailureTs = 0L
        _controllerFixtureId.value = fixtureId
        _controllerReady.value = false

        viewModelScope.launch {
            try {
                // Step 1: Claim the mover
                val claimResult = repository.moverClaim(fixtureId, force = force)
                if (!claimResult.ok) {
                    // #888 §6.2 — detect claim-conflict and surface
                    // the takeover sheet instead of a silent snackbar.
                    val errMsg = claimResult.err ?: ""
                    val isConflict = "claim" in errMsg.lowercase()
                        || "another" in errMsg.lowercase()
                        || "held" in errMsg.lowercase()
                    if (isConflict && !force) {
                        val name = _fixtures.value.find { it.id == fixtureId }?.name
                            ?: "Fixture $fixtureId"
                        val heldBy = extractHolderName(errMsg) ?: "another client"
                        _pendingTakeover.value = PendingTakeover(fixtureId, name, heldBy)
                    } else {
                        _message.value = errMsg.ifBlank { "Mover claimed by another device" }
                    }
                    _controllerFixtureId.value = null
                    return@launch
                }
                _pendingTakeover.value = null

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

    // #816 — publish per-grip body-frame pointer axes so the server's
    // quat→aim path uses (0,1,0) for a portrait phone instead of the
    // default (1,0,0). Without this, pitching the phone forward
    // produces no head tilt and pan reads reversed. Called by the
    // Controller-mode overlay every time it composes (display rotation
    // change → fresh publish), so a portrait→landscape mid-session
    // updates server-side mapping without operator action.
    fun publishGripFromSurfaceRotation(surfaceRotation: Int) {
        // #826 — wizard-first publish. If the operator has run the aim
        // wizard on this device, replay the wizard-derived axes to the
        // server; otherwise fall back to the legacy Surface.ROTATION_*
        // table for un-wizarded devices. Same wire shape either way:
        // POST /api/remotes/grip with {forwardLocal, upLocal}.
        //
        // Persistence has to live on the phone (not just the server)
        // because Controller-mode entry races with the wizard write —
        // pre-fix this very call clobbered the wizard's axes every time
        // the operator entered Controller mode, making the wizard a
        // no-op end-to-end (operator comment on #826 2026-05-09).
        viewModelScope.launch {
            val wizardAxes = try {
                serverPrefs.loadWizardAxes()
            } catch (e: Exception) {
                Log.w(TAG, "loadWizardAxes failed (non-fatal): ${e.message}")
                null
            }
            val (forward, up) = if (wizardAxes != null) {
                wizardAxes.forward to wizardAxes.up
            } else {
                // Legacy Surface.ROTATION_* table — pointer axis in the
                // phone's NATURAL device frame (matches what
                // getQuaternionFromVector produces). Only reached when
                // no wizard has run on this device.
                when (surfaceRotation) {
                    1 /* ROTATION_90  */ -> floatArrayOf(1f, 0f, 0f) to floatArrayOf(0f, 0f, 1f)
                    2 /* ROTATION_180 */ -> floatArrayOf(0f, -1f, 0f) to floatArrayOf(0f, 0f, 1f)
                    3 /* ROTATION_270 */ -> floatArrayOf(-1f, 0f, 0f) to floatArrayOf(0f, 0f, 1f)
                    else /* ROTATION_0 */ -> floatArrayOf(0f, 1f, 0f) to floatArrayOf(0f, 0f, 1f)
                }
            }
            try {
                repository.publishRemoteGrip(forward, up)
            } catch (e: Exception) {
                Log.w(TAG, "publishRemoteGrip failed (non-fatal): ${e.message}")
            }
        }
    }

    fun exitControllerMode() {
        val fid = _controllerFixtureId.value
        _controllerFixtureId.value = null
        _controllerReady.value = false
        _controllerConnected.value = true
        orientErrorCount = 0
        firstOrientFailureTs = 0L
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

    /** Called when user releases the calibrate button (finger up).
     *  #805 — accepts the native rotation-vector quaternion alongside
     *  Euler. The server prefers `quat` because Android's
     *  `getOrientation` is in a different axis convention than
     *  aerospace ZYX, so locking against Euler at calibrate-end
     *  produces a different physical orientation than the next /orient
     *  packet (which sends the native quat). */
    fun calibrateEnd(
        fixtureId: Int,
        roll: Float, pitch: Float, yaw: Float,
        quat: FloatArray? = null,
    ) {
        viewModelScope.launch {
            try {
                repository.moverCalibrateEnd(fixtureId, roll, pitch, yaw, quat)
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
                firstOrientFailureTs = 0L
            } catch (e: Exception) {
                orientErrorCount++
                // Show disconnected after 3 consecutive failures (~150ms)
                if (orientErrorCount >= 3) {
                    _controllerConnected.value = false
                }
                Log.w(TAG, "sendOrientation failed: ${e.message}")

                // #759 — release the claim only after a SUSTAINED failure
                // window so a single dropped packet (or a 200 ms WiFi hiccup)
                // doesn't blackout the fixture mid-show.
                val now = System.currentTimeMillis()
                if (firstOrientFailureTs == 0L) {
                    firstOrientFailureTs = now
                } else if (now - firstOrientFailureTs > kReleaseAfterFailureMs) {
                    Log.w(TAG, "sustained orient failure (>${kReleaseAfterFailureMs}ms) — releasing claim")
                    firstOrientFailureTs = 0L
                    if (_controllerFixtureId.value != null) exitControllerMode()
                    if (_pointerFixtureId.value != null) exitPointerMode()
                }
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

    // `setSmoothing` removed in #877 — operator-facing smoothing
    // slider was deleted across the stack. The repository's
    // `moverSmoothing` method is gone too; the orchestrator's
    // POST `/api/mover-control/smoothing` is a no-op for back-compat.

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
        // #752 — clear stale per-fixture state (matches the
        // controller-mode entry's reset). Same flow shares the
        // mover-control status poller, so leaks from the previous
        // session would surface here too.
        _controllerStatus.value = null
        _controllerConnected.value = true
        _engineRunning.value = true
        orientErrorCount = 0
        firstOrientFailureTs = 0L
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
        firstOrientFailureTs = 0L
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
