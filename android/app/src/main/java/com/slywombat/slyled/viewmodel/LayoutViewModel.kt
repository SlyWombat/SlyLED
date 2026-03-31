package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.*
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class LayoutViewModel @Inject constructor(
    private val repository: SlyLedRepository,
) : ViewModel() {

    private val _layout = MutableStateFlow<Layout?>(null)
    val layout = _layout.asStateFlow()

    private val _children = MutableStateFlow<List<Child>>(emptyList())
    val children = _children.asStateFlow()

    private val _surfaces = MutableStateFlow<List<Surface>>(emptyList())
    val surfaces = _surfaces.asStateFlow()

    private val _fixtures = MutableStateFlow<List<Fixture>>(emptyList())
    val fixtures = _fixtures.asStateFlow()

    private val _stage = MutableStateFlow(Stage())
    val stage = _stage.asStateFlow()

    private val _message = MutableStateFlow<String?>(null)
    val message = _message.asStateFlow()

    fun load() {
        viewModelScope.launch {
            try {
                _children.value = repository.getChildren()
                _layout.value = repository.getLayout()
                _surfaces.value = repository.getSurfaces()
                _fixtures.value = repository.getFixtures()
                _stage.value = repository.getStage()
            } catch (e: Exception) { _message.value = "Load error: ${e.message}" }
        }
    }

    fun moveChild(id: Int, x: Int, y: Int) {
        val current = _layout.value ?: return
        val children = current.children.toMutableList()
        val idx = children.indexOfFirst { it.id == id }
        if (idx >= 0) children[idx] = children[idx].copy(x = x, y = y)
        else children.add(LayoutChild(id = id, x = x, y = y))
        _layout.value = current.copy(children = children)
    }

    fun placeChild(id: Int, x: Int, y: Int) {
        moveChild(id, x, y)
        _message.value = "Performer placed — drag to reposition, then Save"
    }

    fun saveLayout() {
        val current = _layout.value ?: return
        viewModelScope.launch {
            try {
                val placed = current.children.filter { it.x != 0 || it.y != 0 }
                repository.saveLayout(current.copy(children = placed))
                _message.value = "Layout saved"
            } catch (e: Exception) { _message.value = "Save failed: ${e.message}" }
        }
    }

    fun autoCreateFixtures() {
        viewModelScope.launch {
            try {
                val r = repository.migrateLayout()
                _message.value = "Fixtures created: ${r.added ?: 0}"
                _fixtures.value = repository.getFixtures()
            } catch (e: Exception) { _message.value = "Error: ${e.message}" }
        }
    }
}
