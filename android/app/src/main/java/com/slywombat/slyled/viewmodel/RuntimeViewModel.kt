package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class RuntimeViewModel @Inject constructor(
    private val repository: SlyLedRepository,
) : ViewModel() {

    private val _timelines = MutableStateFlow<List<Timeline>>(emptyList())
    val timelines = _timelines.asStateFlow()

    private val _selectedTimeline = MutableStateFlow<Timeline?>(null)
    val selectedTimeline = _selectedTimeline.asStateFlow()

    private val _bakeStatus = MutableStateFlow<BakeStatus?>(null)
    val bakeStatus = _bakeStatus.asStateFlow()

    private val _syncStatus = MutableStateFlow<SyncStatus?>(null)
    val syncStatus = _syncStatus.asStateFlow()

    private val _timelineStatus = MutableStateFlow<TimelineStatus?>(null)
    val timelineStatus = _timelineStatus.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message = _message.asStateFlow()

    private val _presets = MutableStateFlow<List<ShowPreset>>(emptyList())
    val presets = _presets.asStateFlow()

    private val _actions = MutableStateFlow<List<Action>>(emptyList())
    val actions = _actions.asStateFlow()

    private val _spatialEffects = MutableStateFlow<List<SpatialEffect>>(emptyList())
    val spatialEffects = _spatialEffects.asStateFlow()

    fun load() {
        viewModelScope.launch {
            try {
                _timelines.value = repository.getTimelines()
                _presets.value = repository.getShowPresets()
                _actions.value = repository.getActions()
                _spatialEffects.value = repository.getSpatialEffects()
            } catch (_: Exception) {}
        }
        // Poll timeline status
        viewModelScope.launch {
            while (true) {
                try {
                    val settings = repository.getSettings()
                    val tlId = settings.activeTimeline
                    if (tlId != null && tlId >= 0 && settings.runnerRunning) {
                        _timelineStatus.value = repository.getTimelineStatus(tlId)
                    } else {
                        _timelineStatus.value = null
                    }
                } catch (_: Exception) {}
                delay(3000)
            }
        }
    }

    fun clearMessage() { _message.value = null }

    fun selectTimeline(id: Int) {
        viewModelScope.launch {
            try {
                _selectedTimeline.value = repository.getTimeline(id)
            } catch (_: Exception) {}
        }
    }

    fun createTimeline(name: String, durationS: Int) {
        viewModelScope.launch {
            try {
                val r = repository.createTimeline(Timeline(name = name, durationS = durationS))
                if (r.ok) {
                    _message.value = "Timeline created"
                    _timelines.value = repository.getTimelines()
                }
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun deleteTimeline(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteTimeline(id)
                _timelines.value = repository.getTimelines()
                if (_selectedTimeline.value?.id == id) _selectedTimeline.value = null
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun bakeAndStart(id: Int) {
        viewModelScope.launch {
            try {
                _message.value = "Baking..."
                repository.bakeTimeline(id)
                // Poll bake
                while (true) {
                    delay(500)
                    val bs = repository.getBakeStatus(id)
                    _bakeStatus.value = bs
                    if (bs.done) {
                        if (bs.error != null) { _message.value = "Bake error: ${bs.error}"; return@launch }
                        break
                    }
                }
                _message.value = "Syncing..."
                repository.syncBaked(id)
                // Poll sync
                while (true) {
                    delay(400)
                    val ss = repository.getSyncStatus(id)
                    _syncStatus.value = ss
                    if (ss.done) break
                }
                if (_syncStatus.value?.allReady == true) {
                    _message.value = "Starting..."
                    val r = repository.startTimeline(id)
                    _message.value = if (r.ok) "Show started!" else "Start failed"
                } else {
                    _message.value = "Not all performers ready"
                }
                _bakeStatus.value = null
                _syncStatus.value = null
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun stopTimeline(id: Int) {
        viewModelScope.launch {
            try {
                repository.stopTimeline(id)
                _timelineStatus.value = null
                _message.value = "Show stopped"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun loadPresets() {
        viewModelScope.launch {
            try { _presets.value = repository.getShowPresets() }
            catch (_: Exception) { _message.value = "Could not load presets" }
        }
    }

    fun loadPreset(presetId: String) {
        viewModelScope.launch {
            try {
                val r = repository.loadPreset(mapOf("id" to presetId))
                if (r.ok) {
                    _message.value = "Preset loaded"
                    _timelines.value = repository.getTimelines()
                }
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun addClipToTimeline(timelineId: Int, trackIdx: Int, clip: TimelineClip) {
        viewModelScope.launch {
            try {
                val tl = repository.getTimeline(timelineId)
                val tracks = tl.tracks.toMutableList()
                if (trackIdx < tracks.size) {
                    val track = tracks[trackIdx]
                    tracks[trackIdx] = track.copy(clips = track.clips + clip)
                    repository.updateTimeline(timelineId, tl.copy(tracks = tracks))
                    _selectedTimeline.value = repository.getTimeline(timelineId)
                    _timelines.value = repository.getTimelines()
                    _message.value = "Clip added"
                }
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun removeClipFromTimeline(timelineId: Int, trackIdx: Int, clipIdx: Int) {
        viewModelScope.launch {
            try {
                val tl = repository.getTimeline(timelineId)
                val tracks = tl.tracks.toMutableList()
                if (trackIdx < tracks.size) {
                    val track = tracks[trackIdx]
                    val clips = track.clips.toMutableList()
                    if (clipIdx < clips.size) clips.removeAt(clipIdx)
                    tracks[trackIdx] = track.copy(clips = clips)
                    repository.updateTimeline(timelineId, tl.copy(tracks = tracks))
                    _selectedTimeline.value = repository.getTimeline(timelineId)
                    _timelines.value = repository.getTimelines()
                }
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }
}
