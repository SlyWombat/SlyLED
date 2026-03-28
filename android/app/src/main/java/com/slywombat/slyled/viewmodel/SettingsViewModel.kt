package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.Settings
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import javax.inject.Inject

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    private val _settings = MutableStateFlow(Settings())
    val settings: StateFlow<Settings> = _settings

    private val _isSaving = MutableStateFlow(false)
    val isSaving: StateFlow<Boolean> = _isSaving

    private val _exportedJson = MutableSharedFlow<String>()
    val exportedJson: SharedFlow<String> = _exportedJson

    private val _message = MutableSharedFlow<String>()
    val message: SharedFlow<String> = _message

    init {
        loadSettings()
    }

    fun loadSettings() {
        viewModelScope.launch {
            try {
                _settings.value = repository.getSettings()
            } catch (e: Exception) {
                _message.emit("Failed to load settings: ${e.message}")
            }
        }
    }

    fun saveSettings(name: String, units: Int, canvasW: Int, canvasH: Int, darkMode: Int, logging: Boolean) {
        viewModelScope.launch {
            _isSaving.value = true
            try {
                val body = mapOf<String, Any>(
                    "name" to name,
                    "units" to units,
                    "canvasW" to canvasW,
                    "canvasH" to canvasH,
                    "darkMode" to darkMode,
                    "logging" to logging
                )
                val resp = repository.saveSettings(body)
                if (resp.ok) {
                    _message.emit("Settings saved")
                    loadSettings()
                } else {
                    _message.emit(resp.err ?: "Failed to save settings")
                }
            } catch (e: Exception) {
                _message.emit("Save failed: ${e.message}")
            } finally {
                _isSaving.value = false
            }
        }
    }

    fun exportConfig() {
        viewModelScope.launch {
            try {
                val json: JsonObject = repository.exportConfig()
                _exportedJson.emit(json.toString())
                _message.emit("Config exported")
            } catch (e: Exception) {
                _message.emit("Export failed: ${e.message}")
            }
        }
    }

    fun exportShow() {
        viewModelScope.launch {
            try {
                val json: JsonObject = repository.exportShow()
                _exportedJson.emit(json.toString())
                _message.emit("Show exported")
            } catch (e: Exception) {
                _message.emit("Export failed: ${e.message}")
            }
        }
    }

    fun generateDemo() {
        viewModelScope.launch {
            try {
                val resp = repository.generateDemo()
                if (resp.ok) {
                    _message.emit("Demo show generated")
                } else {
                    _message.emit(resp.err ?: "Demo generation failed")
                }
            } catch (e: Exception) {
                _message.emit("Demo failed: ${e.message}")
            }
        }
    }

    fun factoryReset() {
        viewModelScope.launch {
            try {
                val resp = repository.factoryReset()
                if (resp.ok) {
                    _message.emit("Factory reset complete")
                    loadSettings()
                } else {
                    _message.emit(resp.err ?: "Reset failed")
                }
            } catch (e: Exception) {
                _message.emit("Reset failed: ${e.message}")
            }
        }
    }
}
