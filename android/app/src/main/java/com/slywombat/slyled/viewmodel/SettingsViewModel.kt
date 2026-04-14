package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.data.model.DmxStatus
import com.slywombat.slyled.data.model.Settings
import com.slywombat.slyled.data.model.Stage
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.JsonPrimitive
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

    private val _dmxStatus = MutableStateFlow<DmxStatus?>(null)
    val dmxStatus: StateFlow<DmxStatus?> = _dmxStatus

    private val _dmxSettings = MutableStateFlow<JsonObject?>(null)
    val dmxSettings: StateFlow<JsonObject?> = _dmxSettings

    private val _dmxProfiles = MutableStateFlow<List<DmxProfile>>(emptyList())
    val dmxProfiles: StateFlow<List<DmxProfile>> = _dmxProfiles

    init {
        loadSettings()
        loadDmxStatus()
        loadDmxSettings()
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

    suspend fun getStage(): Stage? {
        return try { repository.getStage() } catch (_: Exception) { null }
    }

    fun saveStage(w: Double, h: Double, d: Double) {
        viewModelScope.launch {
            try { repository.saveStage(w, h, d) } catch (_: Exception) {}
        }
    }

    fun saveSettings(name: String, units: Int, canvasW: Int, canvasH: Int, darkMode: Int, logging: Boolean) {
        viewModelScope.launch {
            _isSaving.value = true
            try {
                val body = Settings(
                    name = name,
                    units = units,
                    canvasW = canvasW,
                    canvasH = canvasH,
                    darkMode = darkMode,
                    logging = logging
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

    fun importConfig(json: String) {
        viewModelScope.launch {
            try {
                val obj = kotlinx.serialization.json.Json.parseToJsonElement(json) as JsonObject
                val resp = repository.importConfig(obj)
                if (resp.ok) {
                    _message.emit("Config imported (${resp.added ?: 0} added, ${resp.updated ?: 0} updated)")
                    loadSettings()
                } else {
                    _message.emit(resp.err ?: "Import failed")
                }
            } catch (e: Exception) {
                _message.emit("Import failed: ${e.message}")
            }
        }
    }

    fun importShow(json: String) {
        viewModelScope.launch {
            try {
                val obj = kotlinx.serialization.json.Json.parseToJsonElement(json) as JsonObject
                val resp = repository.importShow(obj)
                if (resp.ok) {
                    _message.emit("Show imported (${resp.actions ?: 0} actions, ${resp.runners ?: 0} runners)")
                } else {
                    _message.emit(resp.err ?: "Import failed")
                }
            } catch (e: Exception) {
                _message.emit("Import failed: ${e.message}")
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

    // ── DMX Control ────────────────────────────────────────────────────

    fun loadDmxSettings() {
        viewModelScope.launch {
            try {
                _dmxSettings.value = repository.getDmxSettings()
            } catch (_: Exception) {
                _dmxSettings.value = null
            }
        }
    }

    fun saveDmxSettings(
        protocol: String,
        frameRate: Int,
        bindIp: String,
        sacnPriority: Int,
        sacnSourceName: String,
        unicastTargets: Map<String, String>
    ) {
        viewModelScope.launch {
            try {
                val body = buildJsonObject {
                    put("protocol", protocol)
                    put("frameRate", frameRate)
                    put("bindIp", bindIp)
                    put("sacnPriority", sacnPriority)
                    put("sacnSourceName", sacnSourceName)
                    put("unicastTargets", buildJsonObject {
                        unicastTargets.forEach { (k, v) -> put(k, v) }
                    })
                }
                val resp = repository.saveDmxSettings(body)
                if (resp.ok) _message.emit("DMX settings saved")
                else _message.emit(resp.err ?: "Save failed")
                loadDmxSettings()
                loadDmxStatus()
            } catch (e: Exception) {
                _message.emit("Save failed: ${e.message}")
            }
        }
    }

    fun loadDmxStatus() {
        viewModelScope.launch {
            try {
                _dmxStatus.value = repository.getDmxStatus()
            } catch (_: Exception) {
                _dmxStatus.value = null
            }
        }
    }

    fun startDmx(protocol: String) {
        viewModelScope.launch {
            try {
                val body = buildJsonObject { put("protocol", protocol) }
                val resp = repository.startDmx(body)
                if (resp.ok) {
                    _message.emit("DMX engine started ($protocol)")
                    loadDmxStatus()
                } else {
                    _message.emit(resp.err ?: "Failed to start DMX")
                }
            } catch (e: Exception) {
                _message.emit("Start DMX failed: ${e.message}")
            }
        }
    }

    fun stopDmx() {
        viewModelScope.launch {
            try {
                val resp = repository.stopDmx()
                if (resp.ok) {
                    _message.emit("DMX engine stopped")
                    loadDmxStatus()
                } else {
                    _message.emit(resp.err ?: "Failed to stop DMX")
                }
            } catch (e: Exception) {
                _message.emit("Stop DMX failed: ${e.message}")
            }
        }
    }

    fun dmxBlackout() {
        viewModelScope.launch {
            try {
                val resp = repository.dmxBlackout()
                if (resp.ok) {
                    _message.emit("DMX blackout sent")
                } else {
                    _message.emit(resp.err ?: "Blackout failed")
                }
            } catch (e: Exception) {
                _message.emit("Blackout failed: ${e.message}")
            }
        }
    }

    fun loadDmxProfiles(category: String? = null) {
        viewModelScope.launch {
            try {
                _dmxProfiles.value = repository.getDmxProfiles(category)
            } catch (e: Exception) {
                _message.emit("Failed to load profiles: ${e.message}")
            }
        }
    }
}
