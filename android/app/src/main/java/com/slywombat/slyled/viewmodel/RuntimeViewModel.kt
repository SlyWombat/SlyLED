package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class RuntimeViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    private val _runners = MutableStateFlow<List<RunnerSummary>>(emptyList())
    val runners: StateFlow<List<RunnerSummary>> = _runners.asStateFlow()

    private val _flights = MutableStateFlow<List<Flight>>(emptyList())
    val flights: StateFlow<List<Flight>> = _flights.asStateFlow()

    private val _shows = MutableStateFlow<List<Show>>(emptyList())
    val shows: StateFlow<List<Show>> = _shows.asStateFlow()

    private val _children = MutableStateFlow<List<Child>>(emptyList())
    val children: StateFlow<List<Child>> = _children.asStateFlow()

    val settings: StateFlow<Settings> = repository.settingsFlow(3000)
        .map { it.getOrDefault(Settings()) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), Settings())

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()

    init {
        loadAll()
    }

    fun loadAll() {
        viewModelScope.launch {
            _isLoading.value = true
            _error.value = null
            try {
                _runners.value = repository.getRunners()
                _flights.value = repository.getFlights()
                _shows.value = repository.getShows()
                _children.value = repository.getChildren()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to load data"
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun loadRunners() {
        viewModelScope.launch {
            try {
                _runners.value = repository.getRunners()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to load runners"
            }
        }
    }

    fun loadFlights() {
        viewModelScope.launch {
            try {
                _flights.value = repository.getFlights()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to load flights"
            }
        }
    }

    fun loadShows() {
        viewModelScope.launch {
            try {
                _shows.value = repository.getShows()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to load shows"
            }
        }
    }

    fun createRunner(name: String) {
        viewModelScope.launch {
            try {
                repository.createRunner(name)
                loadRunners()
                _message.value = "Runner created"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to create runner"
            }
        }
    }

    fun deleteRunner(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteRunner(id)
                loadRunners()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to delete runner"
            }
        }
    }

    fun computeRunner(id: Int) {
        viewModelScope.launch {
            try {
                repository.computeRunner(id)
                loadRunners()
                _message.value = "Runner computed"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to compute runner"
            }
        }
    }

    fun syncRunner(id: Int) {
        viewModelScope.launch {
            try {
                repository.syncRunner(id)
                _message.value = "Runner synced to performers"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to sync runner"
            }
        }
    }

    fun startRunner(id: Int) {
        viewModelScope.launch {
            try {
                repository.startRunner(id)
                _message.value = "Runner started"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to start runner"
            }
        }
    }

    fun stopRunners() {
        viewModelScope.launch {
            try {
                repository.stopRunners()
                _message.value = "All runners stopped"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to stop runners"
            }
        }
    }

    fun createFlight(flight: Flight) {
        viewModelScope.launch {
            try {
                repository.createFlight(flight)
                loadFlights()
                _message.value = "Flight created"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to create flight"
            }
        }
    }

    fun updateFlight(id: Int, flight: Flight) {
        viewModelScope.launch {
            try {
                repository.updateFlight(id, flight)
                loadFlights()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to update flight"
            }
        }
    }

    fun deleteFlight(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteFlight(id)
                loadFlights()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to delete flight"
            }
        }
    }

    fun createShow(show: Show) {
        viewModelScope.launch {
            try {
                repository.createShow(show)
                loadShows()
                _message.value = "Show created"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to create show"
            }
        }
    }

    fun updateShow(id: Int, show: Show) {
        viewModelScope.launch {
            try {
                repository.updateShow(id, show)
                loadShows()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to update show"
            }
        }
    }

    fun deleteShow(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteShow(id)
                loadShows()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to delete show"
            }
        }
    }

    fun startShow(id: Int) {
        viewModelScope.launch {
            try {
                repository.startShow(id)
                _message.value = "Show started"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to start show"
            }
        }
    }

    fun stopShows() {
        viewModelScope.launch {
            try {
                repository.stopShows()
                _message.value = "All shows stopped"
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to stop shows"
            }
        }
    }

    fun saveSettings(brightness: Int, loop: Boolean) {
        viewModelScope.launch {
            try {
                repository.saveSettings(Settings(
                    globalBrightness = brightness,
                    runnerLoop = loop
                ))
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to save settings"
            }
        }
    }

    fun clearError() { _error.value = null }
    fun clearMessage() { _message.value = null }
}
