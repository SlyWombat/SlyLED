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

    private val _objects = MutableStateFlow<List<StageObject>>(emptyList())
    val objects = _objects.asStateFlow()

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
                _objects.value = repository.getObjects()
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

    fun autoArrangeDmx() {
        viewModelScope.launch {
            try {
                val stg = _stage.value
                val stageW = (stg.w * 1000).toInt()
                val stageH = (stg.h * 1000).toInt()
                val stageD = (stg.d * 1000).toInt()
                val topY = (stageH * 0.9).toInt()
                val backZ = (stageD * 0.8).toInt()
                val dmxFixtures = _fixtures.value.filter { it.fixtureType == "dmx" || it.fixtureType == "camera" }
                if (dmxFixtures.isEmpty()) {
                    _message.value = "No DMX/camera fixtures to arrange"
                    return@launch
                }
                val n = dmxFixtures.size
                val margin = (stageW * 0.1).toInt()
                val usableW = stageW - 2 * margin
                val spacing = if (n > 1) usableW.toDouble() / (n - 1) else 0.0

                val list = _fixtures.value.toMutableList()
                dmxFixtures.forEachIndexed { i, f ->
                    val idx = list.indexOfFirst { it.id == f.id }
                    if (idx >= 0) {
                        val x = (margin + i * spacing).toInt()
                        list[idx] = list[idx].copy(
                            x = x, y = topY, z = backZ, positioned = true,
                            aimPoint = listOf(x.toDouble(), 0.0, backZ.toDouble())
                        )
                    }
                }
                _fixtures.value = list

                // Save aim points
                dmxFixtures.forEachIndexed { i, f ->
                    val x = (margin + i * spacing).toInt()
                    try {
                        repository.setAimPoint(f.id, listOf(x.toDouble(), 0.0, backZ.toDouble()))
                    } catch (_: Exception) {}
                }
                _message.value = "Arranged ${dmxFixtures.size} DMX/camera fixtures"
            } catch (e: Exception) { _message.value = "Arrange failed: ${e.message}" }
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

    fun updateObject(id: Int, posX: Int, posY: Int, scaleW: Int, scaleH: Int, opacity: Int) {
        viewModelScope.launch {
            try {
                repository.updateObject(id, posX, posY, scaleW, scaleH, opacity)
                _objects.value = repository.getObjects()
                _message.value = "Object updated"
            } catch (e: Exception) { _message.value = "Update failed: ${e.message}" }
        }
    }

    fun deleteObject(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteObject(id)
                _objects.value = repository.getObjects()
                _message.value = "Object deleted"
            } catch (e: Exception) { _message.value = "Delete failed: ${e.message}" }
        }
    }
}
