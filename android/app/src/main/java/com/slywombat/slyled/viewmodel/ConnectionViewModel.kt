package com.slywombat.slyled.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeout
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.inject.Inject

@HiltViewModel
class ConnectionViewModel @Inject constructor(
    private val repository: SlyLedRepository
) : ViewModel() {

    enum class State { DISCONNECTED, CONNECTING, CONNECTED }

    private val _state = MutableStateFlow(
        if (repository.isConnected) State.CONNECTED else State.DISCONNECTED
    )
    val state: StateFlow<State> = _state

    private val _serverInfo = MutableStateFlow("")
    val serverInfo: StateFlow<String> = _serverInfo

    // One-shot error events (SharedFlow so they don't replay on recomposition)
    private val _errorEvent = MutableSharedFlow<String>()
    val errorEvent: SharedFlow<String> = _errorEvent

    private val connectMutex = Mutex()

    fun connect(host: String, port: Int) {
        viewModelScope.launch {
            connectMutex.withLock {
                if (_state.value == State.CONNECTING) return@withLock
                _state.value = State.CONNECTING
                _serverInfo.value = "$host:$port"
                try {
                    withTimeout(8000) {
                        repository.connect(host, port)
                        val status = repository.getStatus()
                        if (status.role == "parent") {
                            _serverInfo.value = "${status.hostname} ($host:$port) v${status.version}"
                            _state.value = State.CONNECTED
                        } else {
                            _errorEvent.emit("Server responded but is not a SlyLED orchestrator")
                            repository.disconnect()
                            _state.value = State.DISCONNECTED
                        }
                    }
                } catch (e: TimeoutCancellationException) {
                    _errorEvent.emit("Connection timed out — check the IP address and ensure the server is running")
                    repository.disconnect()
                    _state.value = State.DISCONNECTED
                } catch (e: SocketTimeoutException) {
                    _errorEvent.emit("Server not responding — verify the IP and port are correct")
                    repository.disconnect()
                    _state.value = State.DISCONNECTED
                } catch (e: ConnectException) {
                    _errorEvent.emit("Connection refused — is the SlyLED service running on $host:$port?")
                    repository.disconnect()
                    _state.value = State.DISCONNECTED
                } catch (e: UnknownHostException) {
                    _errorEvent.emit("Unknown host — check the IP address")
                    repository.disconnect()
                    _state.value = State.DISCONNECTED
                } catch (e: Exception) {
                    val msg = e.message ?: e.javaClass.simpleName
                    _errorEvent.emit("Connection failed: $msg")
                    repository.disconnect()
                    _state.value = State.DISCONNECTED
                }
            }
        }
    }

    fun disconnect() {
        repository.disconnect()
        _state.value = State.DISCONNECTED
        _serverInfo.value = ""
    }
}
