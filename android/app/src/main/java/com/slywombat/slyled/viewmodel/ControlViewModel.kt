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

    private val _pointerFixtureId = MutableStateFlow<Int?>(null)
    val pointerFixtureId: StateFlow<Int?> = _pointerFixtureId.asStateFlow()

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

    // ── Pointer mode ─────────────────────────────────────────────
    fun enterPointerMode(fixtureId: Int) { _pointerFixtureId.value = fixtureId }
    fun exitPointerMode() { _pointerFixtureId.value = null }

    fun aimFixture(fixtureId: Int, pan: Float, tilt: Float) {
        viewModelScope.launch {
            try { repository.aimFixture(fixtureId, pan, tilt) } catch (_: Exception) {}
        }
    }

    companion object {
        private const val TAG = "ControlVM"
    }
}
