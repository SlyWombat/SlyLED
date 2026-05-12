// ConnectionState.swift — connection state machine for the iOS shell.
// Mirrors the Android `LinkState` from #888 §6.1:
//   Connected → Degraded (no PONG in 3s) → Disconnected (no PONG in 10s) → reconnect.

import Foundation
import Combine

enum LinkState: String {
    case connected = "Connected"
    case degraded = "Reconnecting…"
    case disconnected = "Offline"
}

@MainActor
final class ConnectionState: ObservableObject {
    @Published var state: LinkState = .connected

    private var lastOk: Date = .distantPast
    private var pollTask: Task<Void, Never>?

    func startPolling(client: OrchestratorClient) {
        pollTask?.cancel()
        lastOk = .distantPast
        pollTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.tick(client: client)
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    func retry(client: OrchestratorClient) {
        Task { await tick(client: client) }
    }

    private func tick(client: OrchestratorClient) async {
        if (try? await client.getStatus()) != nil {
            lastOk = Date()
        }
        let elapsed = Date().timeIntervalSince(lastOk)
        let newState: LinkState
        switch elapsed {
        case ..<3:
            newState = .connected
        case ..<10:
            newState = .degraded
        default:
            newState = .disconnected
        }
        if newState != state { state = newState }
    }

    deinit { pollTask?.cancel() }
}
