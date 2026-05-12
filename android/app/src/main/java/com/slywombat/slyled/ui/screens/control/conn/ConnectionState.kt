package com.slywombat.slyled.ui.screens.control.conn

// Connection-state machine for the operator app (design doc §6.1). #888.
//
// Connected → Degraded (no PONG in 3s) → Disconnected (no PONG in 10s)
//                                      → back to Connected on next PONG.
//
// Each Control-tab card watches this state to decide whether to enable
// taps, queue writes, or refuse with a haptic-no-go.

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.slywombat.slyled.data.repository.SlyLedRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

enum class LinkState {
    /** PONG seen in the last 3s. All controls live. */
    CONNECTED,
    /** No PONG in 3-10s. Cards visible but tap-targets emit no-go haptic
     *  for new writes; queued writes flushed when state returns to
     *  CONNECTED. */
    DEGRADED,
    /** No PONG in 10s+. Cards 60% opacity, taps fully disabled. */
    DISCONNECTED,
}

private const val DEGRADED_AFTER_MS = 3_000L
private const val DISCONNECTED_AFTER_MS = 10_000L
private const val POLL_INTERVAL_MS = 1_000L

@HiltViewModel
class LinkStateViewModel @Inject constructor(
    private val repository: SlyLedRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(LinkState.CONNECTED)
    val state: StateFlow<LinkState> = _state.asStateFlow()

    @Volatile private var lastOkAtMs: Long = System.currentTimeMillis()

    init {
        viewModelScope.launch { poll() }
    }

    /**
     * Background poll: ping `/status` once per second. Each success
     * resets the last-ok timestamp; failure leaves it stale, and the
     * state transitions trigger off the elapsed-since-ok delta.
     */
    private suspend fun poll() {
        while (true) {
            try {
                repository.getStatus()
                lastOkAtMs = System.currentTimeMillis()
            } catch (_: Throwable) {
                // Swallow — the elapsed counter does the work.
            }
            val elapsed = System.currentTimeMillis() - lastOkAtMs
            val newState = when {
                elapsed < DEGRADED_AFTER_MS    -> LinkState.CONNECTED
                elapsed < DISCONNECTED_AFTER_MS -> LinkState.DEGRADED
                else                            -> LinkState.DISCONNECTED
            }
            if (_state.value != newState) {
                _state.value = newState
            }
            delay(POLL_INTERVAL_MS)
        }
    }

    /** Operator manually triggered a reconnect attempt (pill tap). */
    fun retry() {
        viewModelScope.launch {
            try {
                repository.getStatus()
                lastOkAtMs = System.currentTimeMillis()
                _state.value = LinkState.CONNECTED
            } catch (_: Throwable) { /* state machine catches up next tick */ }
        }
    }
}
