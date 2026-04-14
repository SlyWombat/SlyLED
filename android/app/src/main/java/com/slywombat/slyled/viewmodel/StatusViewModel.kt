package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class StatusViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    private val _children = MutableStateFlow<List<Child>>(emptyList())
    val children: StateFlow<List<Child>> = _children.asStateFlow()

    private val _fixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val fixtures: StateFlow<List<Fixture>> = _fixtures.asStateFlow()

    private val _cameraFixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val cameraFixtures: StateFlow<List<Fixture>> = _cameraFixtures.asStateFlow()

    private val _trackingState = MutableStateFlow<Map<Int, Boolean>>(emptyMap())
    val trackingState: StateFlow<Map<Int, Boolean>> = _trackingState.asStateFlow()

    private val _cameraOnline = MutableStateFlow<Map<Int, Boolean>>(emptyMap())
    val cameraOnline: StateFlow<Map<Int, Boolean>> = _cameraOnline.asStateFlow()

    private val _cameraStats = MutableStateFlow<Map<Int, CameraStatus>>(emptyMap())
    val cameraStats: StateFlow<Map<Int, CameraStatus>> = _cameraStats.asStateFlow()

    private val _dmxStatus = MutableStateFlow<DmxStatus?>(null)
    val dmxStatus: StateFlow<DmxStatus?> = _dmxStatus.asStateFlow()

    private val _settings = MutableStateFlow(Settings())
    val settings: StateFlow<Settings> = _settings.asStateFlow()

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing.asStateFlow()

    private val _message = MutableSharedFlow<String>()
    val message: SharedFlow<String> = _message

    private var initialized = false

    fun load() {
        if (initialized) return
        initialized = true

        // Initial fetch
        refreshAll()

        // Poll children every 5s
        viewModelScope.launch {
            while (true) {
                delay(5000)
                try { _children.value = repository.getChildren() } catch (_: Exception) {}
            }
        }

        // Poll DMX status every 5s
        viewModelScope.launch {
            while (true) {
                delay(5000)
                try {
                    _dmxStatus.value = repository.getDmxStatus()
                } catch (_: Exception) {}
            }
        }

        // Poll settings every 5s
        viewModelScope.launch {
            while (true) {
                delay(5000)
                try { _settings.value = repository.getSettings() } catch (_: Exception) {}
            }
        }
    }

    fun refreshAll() {
        viewModelScope.launch {
            _isRefreshing.value = true
            try {
                try { _children.value = repository.getChildren() } catch (e: Exception) { Log.e(TAG, "getChildren", e) }
                try {
                    val all = repository.getFixtures()
                    _fixtures.value = all
                    _cameraFixtures.value = all.filter { it.fixtureType == "camera" }
                } catch (e: Exception) { Log.e(TAG, "getFixtures", e) }
                try {
                    _dmxStatus.value = repository.getDmxStatus()
                } catch (_: Exception) {}
                try { _settings.value = repository.getSettings() } catch (_: Exception) {}
                // Load tracking + online state + full stats for each camera
                val trackMap = mutableMapOf<Int, Boolean>()
                val onlineMap = mutableMapOf<Int, Boolean>()
                val statsMap = mutableMapOf<Int, CameraStatus>()
                _cameraFixtures.value.forEach { cam ->
                    try {
                        val status = repository.getCameraStatus(cam.id)
                        trackMap[cam.id] = status.tracking
                        // If getCameraStatus succeeds, the camera is online
                        // (the response may not include an explicit "online" field)
                        onlineMap[cam.id] = true
                        statsMap[cam.id] = status.copy(online = true)
                    } catch (_: Exception) {
                        trackMap[cam.id] = false
                        onlineMap[cam.id] = false
                    }
                }
                _trackingState.value = trackMap
                _cameraOnline.value = onlineMap
                _cameraStats.value = statsMap
            } finally {
                _isRefreshing.value = false
            }
        }
    }

    fun toggleTracking(fixtureId: Int) {
        viewModelScope.launch {
            try {
                val currentlyTracking = _trackingState.value[fixtureId] ?: false
                if (currentlyTracking) {
                    repository.stopTracking(fixtureId)
                    _trackingState.value = _trackingState.value.toMutableMap().apply {
                        this[fixtureId] = false
                    }
                    _message.emit("Tracking stopped")
                } else {
                    repository.startTracking(fixtureId)
                    _trackingState.value = _trackingState.value.toMutableMap().apply {
                        this[fixtureId] = true
                    }
                    val camName = _cameraFixtures.value.find { it.id == fixtureId }?.name
                        ?: "Camera #$fixtureId"
                    _message.emit("Tracking started on $camName")
                }
            } catch (e: Exception) {
                Log.e(TAG, "toggleTracking", e)
                _message.emit("Track failed: ${e.message}")
            }
        }
    }

    companion object {
        private const val TAG = "StatusVM"
    }
}
