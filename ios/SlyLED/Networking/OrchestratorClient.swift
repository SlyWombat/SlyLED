// OrchestratorClient — SlyLED REST client for the iOS app. Covers every
// endpoint the Android v1.8.2 operator app touches (see Models.kt /
// SlyLedApi.kt for the contract). URLSession + JSONDecoder, no networking
// dependencies.

import Foundation

@MainActor
final class OrchestratorClient: ObservableObject {
    @Published var host: String
    @Published var port: Int

    let prefs: ServerPreferences

    init(prefs: ServerPreferences) {
        self.prefs = prefs
        self.host = prefs.host
        self.port = prefs.port
    }

    func saveServer(host: String, port: Int) {
        self.host = host
        self.port = port
        prefs.host = host
        prefs.port = port
    }

    var baseURL: URL? {
        guard !host.isEmpty else { return nil }
        return URL(string: "http://\(host):\(port)/")
    }

    var deviceId: String { prefs.deviceId }

    // ── Status / Settings ─────────────────────────────────────────────

    func getStatus() async throws -> StatusResponse { try await getDecoded("status") }
    func getSettings() async throws -> Settings { try await getDecoded("api/settings") }
    func saveSettings(_ s: Settings) async throws -> OkResponse {
        try await postDecoded("api/settings", body: s)
    }
    func setBrightness(_ value: Int) async throws {
        try await postVoid("api/brightness", body: BrightnessReq(value: value))
    }

    // ── Children / Status tab ────────────────────────────────────────

    func getChildren() async throws -> [Child] { try await getDecoded("api/children") }
    func refreshAllChildren() async throws -> OkResponse {
        try await postDecoded("api/children/refresh-all", body: EmptyBody())
    }

    // ── Layout / Stage ───────────────────────────────────────────────

    func getLayoutJSON() async throws -> Data { try await getRaw("api/layout") }
    func getStageJSON() async throws -> Data { try await getRaw("api/stage") }

    // ── Fixtures ──────────────────────────────────────────────────────

    func getFixtures() async throws -> [Fixture] { try await getDecoded("api/fixtures") }
    func getFixturesLive() async throws -> [String: FixtureLive] {
        try await getDecoded("api/fixtures/live")
    }

    // ── Stage objects ─────────────────────────────────────────────────

    // Static walls/props + temporal tracked persons (camera + radar,
    // #900/#912). Polled at the Android cadence (1.5 s) by LiveStageViewModel.
    func getObjects() async throws -> [StageObject] { try await getDecoded("api/objects") }

    // Quick-action writes (Flash / Home / Blackout). 0..1 normalized.
    func dmxTest(_ fixtureId: Int, body: DmxTest) async throws -> OkResponse {
        try await postDecoded("api/fixtures/\(fixtureId)/dmx-test", body: body)
    }

    // Raw-offset channel writes for the Fixtures-page shortcut renderer.
    func channelWrite(_ fixtureId: Int, writes: [Int: Int]) async throws -> OkResponse {
        let stringKeyed = Dictionary(uniqueKeysWithValues: writes.map { (String($0.key), $0.value) })
        return try await postDecoded("api/fixtures/\(fixtureId)/channel-write",
                                     body: ChannelWriteReq(writes: stringKeyed))
    }

    // Page-level safety actions.
    func killStrobes() async throws -> OkResponse {
        try await postDecoded("api/fixtures/kill-strobes", body: EmptyBody())
    }
    func killEffects() async throws -> OkResponse {
        try await postDecoded("api/fixtures/kill-effects", body: EmptyBody())
    }
    func moverAllHome() async throws -> OkResponse {
        try await postDecoded("api/mover-control/all-home", body: EmptyBody())
    }

    // ── DMX Profiles ──────────────────────────────────────────────────

    func getDmxProfiles() async throws -> [DmxProfile] {
        try await getDecoded("api/dmx-profiles")
    }
    // Full profile JSON (channels + capabilities). Returned as raw Data
    // because channel shapes are open-ended and the shortcut renderer
    // walks a [String: Any] structure that doesn't decode to a fixed
    // Codable struct.
    func getDmxProfileFull(_ id: String) async throws -> [String: Any] {
        let data = try await getRaw("api/dmx-profiles/\(id)")
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ClientError.badStatus
        }
        return json
    }

    // ── Mover Control ─────────────────────────────────────────────────

    /// Returns `OkResponse` so callers can inspect `err` for the takeover
    /// sheet — see ControlViewModel detection of " by " in the error.
    func moverClaim(_ moverId: Int, deviceName: String, force: Bool = false) async throws -> OkResponse {
        let body = ClaimReq(moverId: moverId, deviceId: deviceId, deviceName: deviceName,
                            deviceType: "ios", force: force ? true : nil)
        return try await postDecoded("api/mover-control/claim", body: body)
    }

    func moverRelease(_ moverId: Int) async throws {
        try await postVoid("api/mover-control/release",
                           body: ReleaseReq(moverId: moverId, deviceId: deviceId))
    }

    func moverStart(_ moverId: Int) async throws -> OkResponse {
        try await postDecoded("api/mover-control/start",
                              body: StartReq(moverId: moverId, deviceId: deviceId))
    }

    func moverOrient(_ moverId: Int, roll: Double, pitch: Double, yaw: Double,
                     quat: [Double]) async throws {
        try await postVoid("api/mover-control/orient",
                           body: OrientReq(moverId: moverId, deviceId: deviceId,
                                           roll: roll, pitch: pitch, yaw: yaw, quat: quat))
    }

    func moverColor(_ moverId: Int, r: Int, g: Int, b: Int, dimmer: Int? = nil) async throws {
        try await postVoid("api/mover-control/color",
                           body: ColorReq(moverId: moverId, deviceId: deviceId,
                                          r: r, g: g, b: b, dimmer: dimmer))
    }

    func moverFlash(_ moverId: Int, on: Bool) async throws {
        try await postVoid("api/mover-control/flash",
                           body: FlashReq(moverId: moverId, deviceId: deviceId, on: on))
    }

    func moverCalibrateStart(_ moverId: Int, roll: Double, pitch: Double, yaw: Double) async throws {
        try await postVoid("api/mover-control/calibrate-start",
                           body: CalibrateStartReq(moverId: moverId, deviceId: deviceId,
                                                   roll: roll, pitch: pitch, yaw: yaw))
    }

    func moverCalibrateEnd(_ moverId: Int, roll: Double, pitch: Double, yaw: Double,
                           quat: [Double]?) async throws {
        try await postVoid("api/mover-control/calibrate-end",
                           body: CalibrateEndReq(moverId: moverId, deviceId: deviceId,
                                                 roll: roll, pitch: pitch, yaw: yaw, quat: quat))
    }

    func getMoverControlStatus() async throws -> MoverControlStatus {
        try await getDecoded("api/mover-control/status")
    }

    // ── Remotes (grip + wizard + disconnect) ─────────────────────────

    func publishRemoteGrip(forward: [Double], up: [Double]) async throws {
        try await postVoid("api/remotes/grip",
                           body: GripReq(deviceId: deviceId, forwardLocal: forward, upLocal: up))
    }

    func disconnectRemote() async throws {
        try await postVoid("api/remotes/disconnect", body: DisconnectReq(deviceId: deviceId))
    }

    func submitAimWizard(_ poses: [AimWizardPose]) async throws -> AimWizardResult {
        try await postDecoded("api/remotes/aim-wizard",
                              body: AimWizardSubmit(deviceId: deviceId, poses: poses))
    }

    // ── Timelines / Shows ─────────────────────────────────────────────

    func getTimelines() async throws -> [Timeline] { try await getDecoded("api/timelines") }
    func startTimeline(_ id: Int) async throws -> OkResponse {
        try await postDecoded("api/timelines/\(id)/start", body: EmptyBody())
    }
    func stopTimeline(_ id: Int) async throws -> OkResponse {
        try await postDecoded("api/timelines/\(id)/stop", body: EmptyBody())
    }
    func timelineStatus(_ id: Int) async throws -> TimelineStatus {
        try await getDecoded("api/timelines/\(id)/status")
    }

    func showStatus() async throws -> ShowStatus { try await getDecoded("api/show/status") }
    func showPlaylist() async throws -> ShowPlaylist { try await getDecoded("api/show/playlist") }
    func startShow() async throws -> OkResponse {
        try await postDecoded("api/show/start", body: EmptyBody())
    }
    func stopShow() async throws -> OkResponse {
        try await postDecoded("api/show/stop", body: EmptyBody())
    }
    func nextShow() async throws -> OkResponse {
        try await postDecoded("api/show/next", body: EmptyBody())
    }

    // ── Internals ─────────────────────────────────────────────────────

    private func getRaw(_ path: String) async throws -> Data {
        guard let base = baseURL else { throw ClientError.noServer }
        let url = base.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: req)
        try assertOk(response)
        return data
    }

    private func getDecoded<T: Decodable>(_ path: String) async throws -> T {
        let data = try await getRaw(path)
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func postVoid<B: Encodable>(_ path: String, body: B) async throws {
        _ = try await postRaw(path, body: body)
    }

    private func postDecoded<B: Encodable, T: Decodable>(_ path: String, body: B) async throws -> T {
        let data = try await postRaw(path, body: body)
        if data.isEmpty, let empty = "{}".data(using: .utf8) {
            return try JSONDecoder().decode(T.self, from: empty)
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func postRaw<B: Encodable>(_ path: String, body: B) async throws -> Data {
        guard let base = baseURL else { throw ClientError.noServer }
        let url = base.appendingPathComponent(path)
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(body)
        req.timeoutInterval = 5
        let (data, response) = try await URLSession.shared.data(for: req)
        try assertOk(response)
        return data
    }

    private func assertOk(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else { throw ClientError.badStatus }
        guard (200..<300).contains(http.statusCode) else { throw ClientError.badStatus }
    }
}

enum ClientError: Error { case noServer, badStatus }

struct EmptyBody: Encodable {}
