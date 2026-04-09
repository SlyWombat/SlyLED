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

    private val _presets = MutableStateFlow<List<ShowPreset>?>(null) // null = loading, empty = error/none
    val presets = _presets.asStateFlow()

    private val _actions = MutableStateFlow<List<Action>>(emptyList())
    val actions = _actions.asStateFlow()

    private val _spatialEffects = MutableStateFlow<List<SpatialEffect>>(emptyList())
    val spatialEffects = _spatialEffects.asStateFlow()

    private val _previewData = MutableStateFlow<Map<String, List<List<List<Int>>>>>(emptyMap())
    val previewData = _previewData.asStateFlow()

    private val _previewSecond = MutableStateFlow(0)
    val previewSecond = _previewSecond.asStateFlow()

    private val _stageChildren = MutableStateFlow<List<Child>>(emptyList())
    val stageChildren = _stageChildren.asStateFlow()

    private val _stageLayout = MutableStateFlow<Layout?>(null)
    val stageLayout = _stageLayout.asStateFlow()

    private val _stageFixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val stageFixtures = _stageFixtures.asStateFlow()

    private val _stageObjects = MutableStateFlow<List<StageObject>>(emptyList())
    val stageObjects = _stageObjects.asStateFlow()

    fun load() {
        viewModelScope.launch {
            try { _timelines.value = repository.getTimelines() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getTimelines failed", e) }
            try { _presets.value = repository.getShowPresets() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getPresets failed", e) }
            try { _actions.value = repository.getActions() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getActions failed", e) }
            try { _spatialEffects.value = repository.getSpatialEffects() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getSpatialEffects failed", e) }
            try { _stageChildren.value = repository.getChildren() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getChildren failed", e) }
            try { _stageLayout.value = repository.getLayout() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getLayout failed", e) }
            try { _stageFixtures.value = repository.getFixtures() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getFixtures failed", e) }
            try { _stageObjects.value = repository.getObjects() } catch (e: Exception) { android.util.Log.e("RuntimeVM", "getObjects failed", e) }
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
                    // Load preview for emulator
                    try { _previewData.value = repository.getBakePreview(id) } catch (_: Exception) {}
                    // Start emulator polling
                    startEmulator(id)
                } else {
                    _message.value = "Not all fixtures ready"
                }
                _bakeStatus.value = null
                _syncStatus.value = null
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    private fun startEmulator(tlId: Int) {
        viewModelScope.launch {
            while (true) {
                delay(1000)
                try {
                    val settings = repository.getSettings()
                    if (!settings.runnerRunning) { _previewData.value = emptyMap(); break }
                    val epoch = settings.runnerStartEpoch ?: continue
                    _previewSecond.value = maxOf(0, (System.currentTimeMillis() / 1000 - epoch).toInt())
                } catch (_: Exception) { break }
            }
        }
    }

    fun stopTimeline(id: Int) {
        viewModelScope.launch {
            try {
                repository.stopTimeline(id)
                _timelineStatus.value = null
                _previewData.value = emptyMap()
                _previewSecond.value = 0
                _message.value = "Show stopped"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun loadPresets() {
        _presets.value = null // reset to loading state
        viewModelScope.launch {
            try {
                val result = repository.getShowPresets()
                android.util.Log.d("RuntimeVM", "Loaded ${result.size} presets")
                _presets.value = result
            } catch (e: Exception) {
                android.util.Log.e("RuntimeVM", "loadPresets failed", e)
                _message.value = "Could not load presets: ${e.message}"
                _presets.value = emptyList() // empty = error, stops spinner
            }
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

    fun updateTimeline(id: Int, name: String, durationS: Int, loop: Boolean) {
        viewModelScope.launch {
            try {
                val tl = repository.getTimeline(id)
                repository.updateTimeline(id, tl.copy(name = name, durationS = durationS, loop = loop))
                _selectedTimeline.value = repository.getTimeline(id)
                _timelines.value = repository.getTimelines()
                _message.value = "Timeline updated"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun setBrightness(value: Int) {
        viewModelScope.launch {
            try {
                repository.saveSettings(Settings(globalBrightness = value))
                _message.value = "Brightness set to $value"
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }

    fun addTrackToTimeline(timelineId: Int) {
        viewModelScope.launch {
            try {
                val tl = repository.getTimeline(timelineId)
                val newTrack = TimelineTrack(allPerformers = true, clips = emptyList())
                repository.updateTimeline(timelineId, tl.copy(tracks = tl.tracks + newTrack))
                _selectedTimeline.value = repository.getTimeline(timelineId)
                _timelines.value = repository.getTimelines()
                _message.value = "Track added"
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

    fun moveTrack(timelineId: Int, trackIdx: Int, direction: Int) {
        viewModelScope.launch {
            try {
                val tl = repository.getTimeline(timelineId)
                val tracks = tl.tracks.toMutableList()
                val to = trackIdx + direction
                if (to in tracks.indices) {
                    val tmp = tracks[trackIdx]
                    tracks[trackIdx] = tracks[to]
                    tracks[to] = tmp
                    repository.updateTimeline(timelineId, tl.copy(tracks = tracks))
                    _selectedTimeline.value = repository.getTimeline(timelineId)
                    _timelines.value = repository.getTimelines()
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
