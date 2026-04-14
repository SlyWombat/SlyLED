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
import kotlinx.serialization.json.JsonElement
import javax.inject.Inject

@HiltViewModel
class LiveStageViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

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

    companion object {
        private const val TAG = "LiveStageVM"
    }
}
