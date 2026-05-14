// ConnectionState — link-state machine + write-queue policy per
// mobile_ui_redesign.md §6.1.
//   Connected   → live, all controls firing immediately.
//   Degraded    → 3..10 s since last PONG; new writes queue, flushed on
//                 transition back to Connected within 5 s; otherwise
//                 dropped with a noGoBump on the next attempt.
//   Disconnected→ 10+ s since last PONG; all writes dropped immediately
//                 with a noGoBump.
//
// Polls GET /status every 1 s; manual retry via `retry()` (pill tap).

import Foundation
import Combine

enum LinkState: String {
    case connected = "Connected"
    case degraded = "Reconnecting…"
    case disconnected = "Offline"
}

@MainActor
final class ConnectionState: ObservableObject {
    @Published var state: LinkState = .disconnected

    private var lastOk: Date = .distantPast
    private var pollTask: Task<Void, Never>?

    // Write queue policy: each pending write is a closure that, when fired,
    // performs the side effect (REST call, UDP send, ...). On Degraded the
    // closure enters the queue with a wallclock timestamp; on transition to
    // Connected within 5 s of enqueue the queue is drained in order, on
    // transition to Disconnected the queue is dropped.
    private struct PendingWrite {
        let enqueued: Date
        let work: () async -> Void
    }
    private var queue: [PendingWrite] = []
    private static let queueWindow: TimeInterval = 5

    // MARK: lifecycle

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

    deinit { pollTask?.cancel() }

    // MARK: state transitions

    private func tick(client: OrchestratorClient) async {
        if (try? await client.getStatus()) != nil {
            lastOk = Date()
        }
        let elapsed = Date().timeIntervalSince(lastOk)
        let newState: LinkState
        switch elapsed {
        case ..<3:  newState = .connected
        case ..<10: newState = .degraded
        default:    newState = .disconnected
        }
        guard newState != state else { return }
        let prev = state
        state = newState
        if newState == .connected {
            await flushQueue()
        } else if newState == .disconnected {
            queue.removeAll()
        }
        _ = prev
    }

    // MARK: write gate

    /// Single entry point for any side-effecting write. Connected fires
    /// immediately; Degraded queues; Disconnected drops (caller fires the
    /// noGoBump haptic itself when this returns `false`).
    @discardableResult
    func dispatchWrite(_ work: @escaping () async -> Void) -> Bool {
        switch state {
        case .connected:
            Task { await work() }
            return true
        case .degraded:
            queue.append(PendingWrite(enqueued: Date(), work: work))
            return true
        case .disconnected:
            return false
        }
    }

    private func flushQueue() async {
        let now = Date()
        let toFlush = queue.filter { now.timeIntervalSince($0.enqueued) <= Self.queueWindow }
        queue.removeAll()
        for pending in toFlush {
            await pending.work()
        }
    }
}
