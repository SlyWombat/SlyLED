package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.Child
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
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
    }

    fun discover() {
        viewModelScope.launch {
            _isDiscovering.value = true
            try {
                val all = repository.discoverChildren()
                val registeredIps = _children.value.map { it.ip }.toSet()
                _discovered.value = all.filter { it.ip !in registeredIps }
                if (_discovered.value.isEmpty()) {
                    _message.emit("No new performers found")
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
                    _message.emit("Added performer at $ip")
                    _discovered.value = _discovered.value.filter { it.ip != ip }
                    loadChildren()
                } else {
                    _message.emit(resp.err ?: "Failed to add performer")
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
                    _message.emit("Performer removed")
                    loadChildren()
                } else {
                    _message.emit(resp.err ?: "Failed to remove performer")
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
}
