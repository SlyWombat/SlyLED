package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.model.Action
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class ActionsViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    private val _actions = MutableStateFlow<List<Action>>(emptyList())
    val actions: StateFlow<List<Action>> = _actions.asStateFlow()

    private val _isLoading = MutableStateFlow(false)
    val isLoading: StateFlow<Boolean> = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error.asStateFlow()

    init {
        loadActions()
    }

    fun loadActions() {
        viewModelScope.launch {
            _isLoading.value = true
            _error.value = null
            try {
                _actions.value = repository.getActions()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to load actions"
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun createAction(action: Action) {
        viewModelScope.launch {
            try {
                repository.createAction(action)
                loadActions()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to create action"
            }
        }
    }

    fun updateAction(id: Int, action: Action) {
        viewModelScope.launch {
            try {
                repository.updateAction(id, action)
                loadActions()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to update action"
            }
        }
    }

    fun deleteAction(id: Int) {
        viewModelScope.launch {
            try {
                repository.deleteAction(id)
                loadActions()
            } catch (e: Exception) {
                _error.value = e.message ?: "Failed to delete action"
            }
        }
    }

    fun clearError() {
        _error.value = null
    }
}
