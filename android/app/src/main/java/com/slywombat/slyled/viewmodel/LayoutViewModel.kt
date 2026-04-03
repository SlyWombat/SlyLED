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
                val layoutResp = repository.getLayout()
                _layout.value = layoutResp
                // Use fixtures from layout response (server now returns fixtures[] in layout GET)
                _fixtures.value = layoutResp.fixtures
                _surfaces.value = repository.getSurfaces()
                _stage.value = repository.getStage()
            } catch (e: Exception) { _message.value = "Load error: ${e.message}" }
        }
    }

    fun moveFixture(fixtureId: Int, x: Int, y: Int) {
        val list = _fixtures.value.toMutableList()
        val idx = list.indexOfFirst { it.id == fixtureId }
        if (idx >= 0) {
            list[idx] = list[idx].copy(x = x, y = y, positioned = true)
            _fixtures.value = list
        }
    }

    fun placeFixture(fixtureId: Int) {
        val list = _fixtures.value.toMutableList()
        val idx = list.indexOfFirst { it.id == fixtureId }
        if (idx >= 0) {
            list[idx] = list[idx].copy(x = 5000, y = 2500, positioned = true)
            _fixtures.value = list
            _message.value = "Fixture placed — drag to reposition, then Save"
        }
    }

    fun removeFixture(fixtureId: Int) {
        val list = _fixtures.value.toMutableList()
        val idx = list.indexOfFirst { it.id == fixtureId }
        if (idx >= 0) {
            list[idx] = list[idx].copy(x = 0, y = 0, z = 0, positioned = false)
            _fixtures.value = list
            _message.value = "Fixture removed from layout — Save to persist"
        }
    }

    fun updateFixturePosition(fixtureId: Int, x: Int, y: Int, z: Int) {
        val list = _fixtures.value.toMutableList()
        val idx = list.indexOfFirst { it.id == fixtureId }
        if (idx >= 0) {
            list[idx] = list[idx].copy(x = x, y = y, z = z, positioned = true)
            _fixtures.value = list
        }
    }

    fun saveLayout() {
        val current = _layout.value ?: return
        viewModelScope.launch {
            try {
                val placedFixtures = _fixtures.value.filter { it.positioned }
                repository.saveLayout(current.copy(fixtures = placedFixtures))
                _message.value = "Layout saved"
            } catch (e: Exception) { _message.value = "Save failed: ${e.message}" }
        }
    }
}
