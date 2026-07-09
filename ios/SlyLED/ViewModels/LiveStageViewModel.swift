// LiveStageViewModel — drives the Stage tab's stage-space canvas with
// fixture positions + live colour + pan/tilt arrows.

import Foundation
import SwiftUI

@MainActor
final class LiveStageViewModel: ObservableObject {
    @Published var fixtures: [Fixture] = []
    @Published var live: [String: FixtureLive] = [:]
    @Published var settings: Settings = Settings()
    @Published var objects: [StageObject] = []

    private let client: OrchestratorClient
    private var pollTask: Task<Void, Never>?
    private var objectsTask: Task<Void, Never>?

    init(client: OrchestratorClient) {
        self.client = client
    }

    func start() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self else { return }
            do { self.fixtures = try await self.client.getFixtures() } catch {}
            while !Task.isCancelled {
                do { self.live = try await self.client.getFixturesLive() } catch {}
                do { self.settings = try await self.client.getSettings() } catch {}
                let interval: UInt64 = (self.settings.runnerRunning == true) ? 500_000_000 : 1_500_000_000
                try? await Task.sleep(nanoseconds: interval)
            }
        }
        // Tracked objects (temporal persons — camera + radar, #912) poll at
        // a fixed 1.5 s, matching Android LiveStageViewModel's objects loop.
        objectsTask?.cancel()
        objectsTask = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                do { self.objects = try await self.client.getObjects() } catch {}
                try? await Task.sleep(nanoseconds: 1_500_000_000)
            }
        }
    }

    func stop() {
        pollTask?.cancel()
        pollTask = nil
        objectsTask?.cancel()
        objectsTask = nil
    }
}
