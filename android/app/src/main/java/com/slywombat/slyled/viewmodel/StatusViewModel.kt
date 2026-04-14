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

    private val _dmxStatus = MutableStateFlow<DmxStatus?>(null)
    val dmxStatus: StateFlow<DmxStatus?> = _dmxStatus.asStateFlow()

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing.asStateFlow()

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
                // Load tracking + online state for each camera
                val trackMap = mutableMapOf<Int, Boolean>()
                val onlineMap = mutableMapOf<Int, Boolean>()
                _cameraFixtures.value.forEach { cam ->
                    try {
                        val status = repository.getCameraStatus(cam.id)
                        trackMap[cam.id] = status.tracking
                        onlineMap[cam.id] = status.online
                    } catch (_: Exception) {
                        trackMap[cam.id] = false
                        onlineMap[cam.id] = false
                    }
                }
                _trackingState.value = trackMap
                _cameraOnline.value = onlineMap
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
                } else {
                    repository.startTracking(fixtureId)
                }
                _trackingState.value = _trackingState.value.toMutableMap().apply {
                    this[fixtureId] = !currentlyTracking
                }
            } catch (e: Exception) { Log.e(TAG, "toggleTracking", e) }
        }
    }

    companion object {
        private const val TAG = "StatusVM"
    }
}
