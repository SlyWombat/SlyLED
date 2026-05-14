// ControlViewModel — orchestrates state for the Control tab (Master /
// Grab / Fixtures / Shows pages + the persistent NowPlayingAnchor).
//
// Mirrors Android's ControlViewModel.kt: polls fixtures-live + settings +
// show-status + timeline-status, manages claim/release for movers,
// surfaces takeover conflicts, exposes favourites + starred + lastPlayed
// persistence helpers.

import Foundation
import SwiftUI

@MainActor
final class ControlViewModel: ObservableObject {
    // ── State ─────────────────────────────────────────────────────────
    @Published var settings: Settings = Settings()
    @Published var timelines: [Timeline] = []
    @Published var fixtures: [Fixture] = []
    @Published var profiles: [DmxProfile] = []
    @Published var fixturesLive: [String: FixtureLive] = [:]
    @Published var timelineStatus: TimelineStatus?
    @Published var showStatus: ShowStatus?
    @Published var playlist: ShowPlaylist?
    @Published var moverStatus: MoverControlStatus?
    @Published var message: String?

    // Per-page selection / overlay state
    @Published var controllerFixtureId: Int?
    @Published var controllerReady: Bool = false
    @Published var fixtureSheetId: Int?
    @Published var pendingTakeover: PendingTakeover?
    @Published var aimWizardOpen: Bool = false

    // Profile cache (full JSON, keyed by profileId).
    private var profileCache: [String: [String: Any]] = [:]

    let client: OrchestratorClient
    let prefs: ServerPreferences
    let connection: ConnectionState

    private var pollTask: Task<Void, Never>?

    struct PendingTakeover: Identifiable {
        let id = UUID()
        let fixtureId: Int
        let fixtureName: String
        let heldBy: String
    }

    init(client: OrchestratorClient, prefs: ServerPreferences, connection: ConnectionState) {
        self.client = client
        self.prefs = prefs
        self.connection = connection
    }

    // ── Lifecycle ─────────────────────────────────────────────────────

    func startPolling() {
        pollTask?.cancel()
        pollTask = Task { [weak self] in
            guard let self else { return }
            // Initial fetch
            await self.reloadStatic()

            // Continuous polls
            while !Task.isCancelled {
                await self.pollLive()
                let interval: UInt64 = (self.settings.runnerRunning == true) ? 500_000_000 : 1_500_000_000
                try? await Task.sleep(nanoseconds: interval)
            }
        }
    }

    func stopPolling() {
        pollTask?.cancel()
        pollTask = nil
    }

    private func reloadStatic() async {
        do { timelines = try await client.getTimelines() } catch { }
        do { fixtures = try await client.getFixtures() } catch { }
        do { profiles = try await client.getDmxProfiles() } catch { }
        do { playlist = try await client.showPlaylist() } catch { }
    }

    private func pollLive() async {
        do { settings = try await client.getSettings() } catch {}
        do { showStatus = try await client.showStatus() } catch {}
        if let tlId = settings.activeTimeline, settings.runnerRunning == true {
            do { timelineStatus = try await client.timelineStatus(tlId) } catch {}
        } else {
            timelineStatus = nil
        }
        do { fixturesLive = try await client.getFixturesLive() } catch {}
        if controllerFixtureId != nil {
            do { moverStatus = try await client.getMoverControlStatus() } catch {}
        }
    }

    // ── Computed views ───────────────────────────────────────────────

    var movers: [Fixture] {
        fixtures.filter { fix in
            (fix.fixtureType ?? "led") == "dmx" && (profilePanRange(fix.dmxProfileId) > 0)
        }
    }
    var nonMoverDmxFixtures: [Fixture] {
        fixtures.filter { fix in
            (fix.fixtureType ?? "led") == "dmx" && (profilePanRange(fix.dmxProfileId) == 0)
        }
    }

    private func profilePanRange(_ profileId: String?) -> Int {
        guard let id = profileId else { return 0 }
        return profiles.first(where: { $0.id == id })?.panRange ?? 0
    }

    // ── Brightness ───────────────────────────────────────────────────

    func setBrightness(_ value: Int) {
        guard connection.dispatchWrite({ [weak self] in
            try? await self?.client.setBrightness(value)
        }) else {
            Haptics.fire(.noGoBump)
            return
        }
    }

    // ── Anchor controls ──────────────────────────────────────────────

    func stopEverything() {
        Haptics.fire(.heavyThud)
        let tlId = settings.activeTimeline
        connection.dispatchWrite { [client] in
            if let tlId, tlId >= 0 { _ = try? await client.stopTimeline(tlId) }
            _ = try? await client.stopShow()
        }
    }

    func nextInPlaylist() {
        Haptics.fire(.successTick)
        connection.dispatchWrite { [client] in
            _ = try? await client.nextShow()
        }
    }

    // ── Shows ────────────────────────────────────────────────────────

    func startTimeline(_ id: Int) {
        Haptics.fire(.successTick)
        prefs.recordTimelineStart(id)
        connection.dispatchWrite { [client] in
            _ = try? await client.startTimeline(id)
        }
    }

    func startPlaylist() {
        Haptics.fire(.successTick)
        connection.dispatchWrite { [client] in
            _ = try? await client.startShow()
        }
    }

    // ── Grab (movers) ────────────────────────────────────────────────

    func claimMover(_ fix: Fixture, force: Bool = false) {
        connection.dispatchWrite { [weak self] in
            guard let self else { return }
            let deviceName = "iPhone"
            do {
                let resp = try await self.client.moverClaim(fix.id, deviceName: deviceName, force: force)
                if resp.ok == true {
                    _ = try? await self.client.moverStart(fix.id)
                    await MainActor.run {
                        self.controllerFixtureId = fix.id
                        self.controllerReady = true
                        Haptics.fire(.lightTick)
                    }
                } else {
                    let err = resp.err ?? ""
                    await MainActor.run { self.handleClaimError(err: err, fixture: fix) }
                }
            } catch {
                await MainActor.run {
                    self.message = "Claim failed"
                    Haptics.fire(.noGoBump)
                }
            }
        }
    }

    private func handleClaimError(err: String, fixture: Fixture) {
        let lower = err.lowercased()
        if lower.contains(" by ") || lower.contains("held") || lower.contains("claim") {
            let holder: String = {
                if let range = lower.range(of: " by ") {
                    let tail = err[range.upperBound...].trimmingCharacters(
                        in: CharacterSet.whitespaces.union(CharacterSet(charactersIn: ".,;")))
                    return tail.isEmpty ? "another client" : tail
                }
                return "another client"
            }()
            pendingTakeover = PendingTakeover(fixtureId: fixture.id,
                                              fixtureName: fixture.name,
                                              heldBy: holder)
            Haptics.fire(.noGoBump)
        } else {
            message = err.isEmpty ? "Claim failed" : err
            Haptics.fire(.noGoBump)
        }
    }

    func confirmTakeover() {
        guard let pending = pendingTakeover else { return }
        pendingTakeover = nil
        if let fix = fixtures.first(where: { $0.id == pending.fixtureId }) {
            Haptics.fire(.heavyThud)
            claimMover(fix, force: true)
        }
    }

    func cancelTakeover() {
        pendingTakeover = nil
    }

    func releaseMover() {
        guard let fid = controllerFixtureId else { return }
        controllerFixtureId = nil
        controllerReady = false
        Task { [client] in
            try? await client.moverRelease(fid)
            try? await client.disconnectRemote()
        }
    }

    // Quick-action long-press menu (#888)

    func flashFixture(_ id: Int) {
        Haptics.fire(.successTick)
        connection.dispatchWrite { [client] in
            _ = try? await client.dmxTest(id, body: DmxTest(dimmer: 1.0))
            try? await Task.sleep(nanoseconds: 180_000_000)
            _ = try? await client.dmxTest(id, body: DmxTest(dimmer: 0.0))
        }
    }

    func homeFixture(_ id: Int) {
        Haptics.fire(.heavyThud)
        connection.dispatchWrite { [client] in
            _ = try? await client.dmxTest(id, body: DmxTest(pan: 0.5, tilt: 0.5, dimmer: 0.0))
        }
    }

    func blackoutFixture(_ id: Int) {
        Haptics.fire(.heavyThud)
        connection.dispatchWrite { [client] in
            _ = try? await client.dmxTest(id, body: DmxTest(dimmer: 0.0))
        }
    }

    // Page-level safety

    func sendAllMoversHome() {
        Haptics.fire(.heavyThud)
        connection.dispatchWrite { [weak self] in
            _ = try? await self?.client.moverAllHome()
            await MainActor.run { self?.message = "Movers sent home" }
        }
    }

    func stopAllEffects() {
        Haptics.fire(.heavyThud)
        connection.dispatchWrite { [weak self] in
            async let s = self?.client.killStrobes()
            async let e = self?.client.killEffects()
            _ = try? await s
            _ = try? await e
            await MainActor.run { self?.message = "Strobes + effects stopped" }
        }
    }

    // ── Fixtures page channel-write ──────────────────────────────────

    func channelWrite(_ fid: Int, _ writes: [Int: Int]) {
        connection.dispatchWrite { [weak self] in
            _ = try? await self?.client.channelWrite(fid, writes: writes)
        }
    }

    func fetchProfile(_ id: String) async -> [String: Any]? {
        if let cached = profileCache[id] { return cached }
        if let full = try? await client.getDmxProfileFull(id) {
            profileCache[id] = full
            return full
        }
        return nil
    }

    func resolveShortcuts(for fixture: Fixture) async -> [ResolvedShortcut] {
        guard let id = fixture.dmxProfileId else { return [] }
        guard let profile = await fetchProfile(id) else { return [] }
        return FixtureShortcuts.resolveShortcutsForProfile(profile)
    }

    // ── Blackout (long-press logo) ───────────────────────────────────

    func blackout() {
        Haptics.fire(.heavyDouble)
        connection.dispatchWrite { [client] in
            try? await client.setBrightness(0)
        }
    }
}
