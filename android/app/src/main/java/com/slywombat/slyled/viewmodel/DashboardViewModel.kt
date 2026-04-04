package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class DashboardViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {
    val children: StateFlow<List<Child>> = repository.childrenFlow(5000)
        .map { it.getOrDefault(emptyList()) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())

    val settings: StateFlow<Settings> = repository.settingsFlow(3000)
        .map { it.getOrDefault(Settings()) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), Settings())

    val networkError: StateFlow<Boolean> = repository.childrenFlow(5000)
        .map { it.isFailure }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing

    fun refreshAll() {
        viewModelScope.launch {
            _isRefreshing.value = true
            try {
                repository.refreshAllChildren()
            } catch (_: Exception) {
                // network error will surface via networkError flow
            } finally {
                _isRefreshing.value = false
            }
        }
    }

    private val _layout = MutableStateFlow<Layout?>(null)
    val layout: StateFlow<Layout?> = _layout

    private val _surfaces = MutableStateFlow<List<Surface>>(emptyList())
    val surfaces: StateFlow<List<Surface>> = _surfaces

    fun loadStageData() {
        viewModelScope.launch {
            try { _layout.value = repository.getLayout() } catch (_: Exception) {}
            try { _surfaces.value = repository.getSurfaces() } catch (_: Exception) {}
        }
    }

    fun stopRunners() {
        viewModelScope.launch {
            try {
                repository.stopRunners()
            } catch (_: Exception) {
                // ignore
            }
        }
    }

    fun stopTimeline(id: Int) {
        viewModelScope.launch {
            try { repository.stopTimeline(id) } catch (_: Exception) {} }
    }
}
