// StatusViewModel — Status tab. Polls /api/children every 5s; pull-to-
// refresh calls /api/children/refresh-all.

import Foundation

@MainActor
final class StatusViewModel: ObservableObject {
    @Published var children: [Child] = []
    @Published var loading: Bool = false

    private let client: OrchestratorClient
    private var pollTask: Task<Void, Never>?

    init(client: OrchestratorClient) {
        self.client = client
    }

    func start() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.reload()
                try? await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
    }

    func reload() async {
        do { children = try await client.getChildren() } catch {}
    }

    func refresh() async {
        loading = true
        defer { loading = false }
        _ = try? await client.refreshAllChildren()
        await reload()
    }
}
