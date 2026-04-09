package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.Child
import com.slywombat.slyled.data.model.DmxProfile
import com.slywombat.slyled.data.model.Fixture
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import javax.inject.Inject

@HiltViewModel
class SetupViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    private val _children = MutableStateFlow<List<Child>>(emptyList())
    val children: StateFlow<List<Child>> = _children

    private val _discovered = MutableStateFlow<List<Child>>(emptyList())
    val discovered: StateFlow<List<Child>> = _discovered

    private val _isDiscovering = MutableStateFlow(false)
    val isDiscovering: StateFlow<Boolean> = _isDiscovering

    private val _isAdding = MutableStateFlow(false)
    val isAdding: StateFlow<Boolean> = _isAdding

    private val _isRefreshing = MutableStateFlow(false)
    val isRefreshing: StateFlow<Boolean> = _isRefreshing

    private val _fixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val fixtures: StateFlow<List<Fixture>> = _fixtures

    private val _dmxProfiles = MutableStateFlow<List<DmxProfile>>(emptyList())
    val dmxProfiles: StateFlow<List<DmxProfile>> = _dmxProfiles

    private val _cameras = MutableStateFlow<List<Fixture>>(emptyList())
    val cameras: StateFlow<List<Fixture>> = _cameras

    private val _message = MutableSharedFlow<String>()
    val message: SharedFlow<String> = _message

    init {
        loadChildren()
    }

    fun loadChildren() {
        viewModelScope.launch {
            try {
                _children.value = repository.getChildren()
            } catch (e: Exception) {
                _message.emit("Failed to load children: ${e.message}")
            }
        }
        loadFixtures()
        loadDmxProfiles()
        loadCameras()
    }

    fun loadFixtures() {
        viewModelScope.launch {
            try {
                _fixtures.value = repository.getFixtures()
            } catch (e: Exception) {
                _message.emit("Failed to load fixtures: ${e.message}")
            }
        }
    }

    fun loadDmxProfiles() {
        viewModelScope.launch {
            try {
                _dmxProfiles.value = repository.getDmxProfiles()
            } catch (e: Exception) {
                _message.emit("Failed to load DMX profiles: ${e.message}")
            }
        }
    }

    fun createFixture(fixture: Fixture) {
        viewModelScope.launch {
            try {
                val resp = repository.createFixture(fixture)
                if (resp.ok) {
                    _message.emit("Fixture created")
                    loadFixtures()
                } else {
                    _message.emit(resp.err ?: "Failed to create fixture")
                }
            } catch (e: Exception) {
                _message.emit("Create fixture failed: ${e.message}")
            }
        }
    }

    fun updateFixture(id: Int, fixture: Fixture) {
        viewModelScope.launch {
            try {
                val resp = repository.updateFixture(id, fixture)
                if (resp.ok) {
                    _message.emit("Fixture updated")
                    loadFixtures()
                } else {
                    _message.emit(resp.err ?: "Failed to update fixture")
                }
            } catch (e: Exception) {
                _message.emit("Update fixture failed: ${e.message}")
            }
        }
    }

    fun deleteFixture(id: Int) {
        viewModelScope.launch {
            try {
                val resp = repository.deleteFixture(id)
                if (resp.ok) {
                    _message.emit("Fixture deleted")
                    loadFixtures()
                } else {
                    _message.emit(resp.err ?: "Failed to delete fixture")
                }
            } catch (e: Exception) {
                _message.emit("Delete fixture failed: ${e.message}")
            }
        }
    }

    fun discover() {
        viewModelScope.launch {
            _isDiscovering.value = true
            try {
                val all = repository.discoverChildren()
                val registeredIps = _children.value.map { it.ip }.toSet()
                _discovered.value = all.filter { it.ip !in registeredIps }
                if (_discovered.value.isEmpty()) {
                    _message.emit("No new fixtures found")
                }
            } catch (e: Exception) {
                _message.emit("Discovery failed: ${e.message}")
            } finally {
                _isDiscovering.value = false
            }
        }
    }

    fun addChild(ip: String) {
        viewModelScope.launch {
            _isAdding.value = true
            try {
                val resp = repository.addChild(ip)
                if (resp.ok) {
                    _message.emit("Added fixture at $ip")
                    _discovered.value = _discovered.value.filter { it.ip != ip }
                    loadChildren()
                } else {
                    _message.emit(resp.err ?: "Failed to add fixture")
                }
            } catch (e: Exception) {
                _message.emit("Add failed: ${e.message}")
            } finally {
                _isAdding.value = false
            }
        }
    }

    fun removeChild(id: Int) {
        viewModelScope.launch {
            try {
                val resp = repository.deleteChild(id)
                if (resp.ok) {
                    _message.emit("Fixture removed")
                    loadChildren()
                } else {
                    _message.emit(resp.err ?: "Failed to remove fixture")
                }
            } catch (e: Exception) {
                _message.emit("Remove failed: ${e.message}")
            }
        }
    }

    fun refreshChild(id: Int) {
        viewModelScope.launch {
            try {
                repository.refreshChild(id)
                loadChildren()
            } catch (e: Exception) {
                _message.emit("Refresh failed: ${e.message}")
            }
        }
    }

    fun rebootChild(id: Int) {
        viewModelScope.launch {
            try {
                val resp = repository.rebootChild(id)
                if (resp.ok) {
                    _message.emit("Reboot command sent")
                } else {
                    _message.emit(resp.err ?: "Reboot failed")
                }
            } catch (e: Exception) {
                _message.emit("Reboot failed: ${e.message}")
            }
        }
    }

    fun loadCameras() {
        viewModelScope.launch {
            try {
                _cameras.value = repository.getCameras()
            } catch (_: Exception) {}
        }
    }

    fun registerCamera(ip: String, name: String? = null) {
        viewModelScope.launch {
            try {
                val resp = repository.registerCamera(ip, name)
                if (resp.ok) {
                    _message.emit("Camera registered at $ip")
                    loadFixtures()
                    loadCameras()
                } else {
                    _message.emit(resp.err ?: "Failed to register camera")
                }
            } catch (e: Exception) {
                _message.emit("Register camera failed: ${e.message}")
            }
        }
    }

    fun unregisterCamera(id: Int) {
        viewModelScope.launch {
            try {
                val resp = repository.unregisterCamera(id)
                if (resp.ok) {
                    _message.emit("Camera removed")
                    loadFixtures()
                    loadCameras()
                } else {
                    _message.emit(resp.err ?: "Failed to remove camera")
                }
            } catch (e: Exception) {
                _message.emit("Remove camera failed: ${e.message}")
            }
        }
    }

    suspend fun loadFixtureChannels(fixtureId: Int): JsonObject? {
        return try {
            repository.getDmxFixtureChannels(fixtureId)
        } catch (_: Exception) {
            null
        }
    }

    suspend fun testFixtureChannel(fixtureId: Int, offset: Int, value: Int) {
        try {
            val body = buildJsonObject {
                put("channels", buildJsonArray {
                    add(buildJsonObject { put("offset", offset); put("value", value) })
                })
            }
            repository.testDmxFixture(fixtureId, body)
        } catch (_: Exception) {}
    }

    fun refreshAll() {
        viewModelScope.launch {
            _isRefreshing.value = true
            try {
                repository.refreshAllChildren()
                loadChildren()
            } catch (e: Exception) {
                _message.emit("Refresh all failed: ${e.message}")
            } finally {
                _isRefreshing.value = false
            }
        }
    }

    // OFL search + import
    private val _oflResults = MutableStateFlow<List<JsonObject>>(emptyList())
    val oflResults: StateFlow<List<JsonObject>> = _oflResults
    private val _oflSearching = MutableStateFlow(false)
    val oflSearching: StateFlow<Boolean> = _oflSearching

    fun oflSearch(query: String) {
        viewModelScope.launch {
            _oflSearching.value = true
            try {
                _oflResults.value = repository.oflSearch(query)
            } catch (e: Exception) {
                _message.emit("OFL search failed: ${e.message}")
                _oflResults.value = emptyList()
            } finally {
                _oflSearching.value = false
            }
        }
    }

    fun oflImport(manufacturer: String, fixture: String, onDone: (Boolean) -> Unit = {}) {
        viewModelScope.launch {
            try {
                val resp = repository.oflImport(manufacturer, fixture)
                if (resp.ok) {
                    _message.emit("Imported fixture profile")
                    loadDmxProfiles()
                    onDone(true)
                } else {
                    _message.emit("Import failed: ${resp.err ?: "unknown"}")
                    onDone(false)
                }
            } catch (e: Exception) {
                _message.emit("Import failed: ${e.message}")
                onDone(false)
            }
        }
    }
}
