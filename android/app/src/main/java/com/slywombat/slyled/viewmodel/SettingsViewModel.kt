package com.slywombat.slyled.viewmodel

import android.util.Log
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.audio.MicAutoBrightness
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.data.model.DmxStatus
import com.slywombat.slyled.data.model.Settings
import com.slywombat.slyled.data.model.Stage
import com.slywombat.slyled.data.repository.ServerPreferences
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.JsonPrimitive
import javax.inject.Inject

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val repository: SlyLedRepository,
    private val mic: MicAutoBrightness,
    private val serverPrefs: ServerPreferences,
) : ViewModel() {

    // #804 — shared with LiveStageViewModel; both surfaces drive the same singleton.
    val autoBrightnessState: StateFlow<MicAutoBrightness.Mode> = mic.state
    val autoBrightnessEnvelope: StateFlow<Float> = mic.envelope
    // #820 — raw mic peak (pre-follower) so the UI can show "mic is dead"
    // vs "floor too high" at a glance.
    val autoBrightnessRawPeak: StateFlow<Float> = mic.rawPeak
    // #820 — beat detection (see LiveStageViewModel for rationale).
    val autoBrightnessBeatPulse: StateFlow<Float> = mic.beatPulse
    val autoBrightnessBpm: StateFlow<Float> = mic.bpm
    val autoBrightnessBeatCount: StateFlow<Int> = mic.beatCount
    val autoBrightnessMaster: StateFlow<Float> = mic.master
    // #804 — tunables exposed as flows so Settings + Stage UIs both
    // recompose when either surface (or the persisted-prefs load) edits
    // them. Pre-fix the UI cached values via `remember`, so a Stage
    // modal change wasn't visible from Settings until you closed and
    // reopened the screen — and persisted-prefs load was never visible.
    val autoBrightnessSensitivityFlow: StateFlow<Float> = mic.sensitivityFlow
    val autoBrightnessFloorFlow: StateFlow<Float> = mic.floorFlow
    val autoBrightnessCeilingFlow: StateFlow<Float> = mic.ceilingFlow
    val autoBrightnessAttackMsFlow: StateFlow<Float> = mic.attackMsFlow
    val autoBrightnessReleaseMsFlow: StateFlow<Float> = mic.releaseMsFlow
    // #820 — semantic Audio Source kind. See AudioSourceKind enum.
    val autoBrightnessAudioSourceKindFlow: StateFlow<com.slywombat.slyled.audio.AudioSourceKind> =
        mic.audioSourceKindFlow
    private val _autoBrightnessEnabled = MutableStateFlow(false)
    val autoBrightnessEnabled: StateFlow<Boolean> = _autoBrightnessEnabled.asStateFlow()
    private var lastFastBrightnessJob: Job? = null

    fun configureAutoBrightness(
        sensitivity: Float? = null,
        floor: Float? = null,
        ceiling: Float? = null,
        attackMs: Float? = null,
        releaseMs: Float? = null,
        audioSourceKind: com.slywombat.slyled.audio.AudioSourceKind? = null,
    ) = mic.configure(sensitivity, floor, ceiling, attackMs, releaseMs, audioSourceKind)

    // #820 — preview / live capture lifecycle. See LiveStageViewModel
    // for the rationale; same surface so the Settings panel can drive
    // the meter without committing to live brightness writes.
    fun startAutoBrightnessPreview() = mic.startPreview(viewModelScope)
    fun stopAutoBrightnessPreview() {
        if (!_autoBrightnessEnabled.value) mic.stop()
    }
    fun setAutoBrightnessMediaProjection(
        mp: android.media.projection.MediaProjection?
    ) = mic.setMediaProjection(mp)

    fun setAutoBrightnessEnabled(enabled: Boolean) {
        if (enabled == _autoBrightnessEnabled.value) return
        if (enabled) {
            val started = mic.start(viewModelScope) { master ->
                if (lastFastBrightnessJob?.isActive == true) return@start
                lastFastBrightnessJob = viewModelScope.launch {
                    try { repository.setMasterBrightness(master) }
                    catch (e: Exception) { Log.w("SettingsVM", "fast brightness", e) }
                }
            }
            _autoBrightnessEnabled.value = started
            mic.setPersistedEnabled(started)   // #804 persist intent
        } else {
            mic.stop()
            _autoBrightnessEnabled.value = false
            mic.setPersistedEnabled(false)
        }
    }

    fun autoBrightnessHasPermission(): Boolean = mic.hasPermission()
    fun autoBrightnessSensitivity(): Float = mic.sensitivity
    fun autoBrightnessFloor(): Float = mic.floor
    fun autoBrightnessCeiling(): Float = mic.ceiling
    fun autoBrightnessAttackMs(): Float = mic.attackMs
    fun autoBrightnessReleaseMs(): Float = mic.releaseMs

    // #804 — manual master fallback (when Auto Brightness is off).
    fun setManualBrightness(value: Int) {
        viewModelScope.launch {
            try { repository.saveSettings(Settings(globalBrightness = value.coerceIn(0, 255))) }
            catch (e: Exception) { Log.w("SettingsVM", "setManualBrightness", e) }
        }
    }

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

    // #824 — orchestrator version, fetched from /api/status. Surfaced on the
    // Settings footer alongside the APK's BuildConfig.VERSION_NAME so the
    // operator can spot a half-shipped release where Android lags the
    // server (or vice versa).
    private val _serverVersion = MutableStateFlow("")
    val serverVersion: StateFlow<String> = _serverVersion.asStateFlow()

    init {
        loadSettings()
        loadDmxStatus()
        loadDmxSettings()
        loadServerVersion()
    }

    fun loadServerVersion() {
        viewModelScope.launch {
            try {
                _serverVersion.value = repository.getStatus().version
            } catch (_: Exception) {
                _serverVersion.value = ""
            }
        }
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

    // #826 — empirical aim-axis wizard. The dialog captures three
    // pose quaternions on-device and posts them via this method;
    // server-side derives forward_local / up_local from the rotation
    // deltas (see `_aim_wizard_compute` in parent_server.py).
    //
    // The result is ALSO persisted on-device via `ServerPreferences`.
    // Server persistence alone is not sufficient: every Controller-mode
    // entry calls `publishGripFromSurfaceRotation` which POSTs the
    // legacy `Surface.ROTATION_*` table to `/api/remotes/grip` and
    // overwrites the wizard's axes on the orchestrator. Saving on the
    // phone lets the controller-mode publish read wizard axes from
    // local storage and bypass the legacy table for wizard'd devices.
    suspend fun submitAimWizard(
        poses: List<Pair<String, FloatArray>>,
    ): SlyLedRepository.AimWizardResult {
        val result = repository.submitAimWizard(poses)
        if (result.ok) {
            val fwd = result.forwardLocal
            val up = result.upLocal
            if (fwd != null && fwd.size == 3 && up != null && up.size == 3) {
                try {
                    serverPrefs.saveWizardAxes(
                        forward = fwd, up = up,
                        completedAtIso = java.time.Instant.now().toString(),
                    )
                } catch (e: Exception) {
                    Log.w("SettingsVM", "saveWizardAxes failed: ${e.message}")
                }
            }
        }
        return result
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
