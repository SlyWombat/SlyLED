// ServerPreferences — UserDefaults-backed persistence; direct schema port
// of Android's data/repository/ServerPreferences.kt. Same keys, same types
// so the operator's mental model (favourite movers, starred shows,
// last-played map, aim-wizard axes, auto-brightness calibration) carries
// across platforms even though the actual data doesn't migrate.

import Foundation
import Combine

@MainActor
final class ServerPreferences: ObservableObject {
    // ── Server connection ──────────────────────────────────────────────
    @Published var host: String {
        didSet { ud.set(host, forKey: Keys.host) }
    }
    @Published var port: Int {
        didSet { ud.set(port, forKey: Keys.port) }
    }
    @Published var deviceId: String {
        didSet { ud.set(deviceId, forKey: Keys.deviceId) }
    }

    // ── Operator stage position (mm) ───────────────────────────────────
    @Published var userPosXmm: Double {
        didSet { ud.set(userPosXmm, forKey: Keys.userX) }
    }
    @Published var userPosYmm: Double {
        didSet { ud.set(userPosYmm, forKey: Keys.userY) }
    }
    @Published var userPosZmm: Double {
        didSet { ud.set(userPosZmm, forKey: Keys.userZ) }
    }

    // ── Auto Brightness ────────────────────────────────────────────────
    @Published var autoBrightnessEnabled: Bool {
        didSet { ud.set(autoBrightnessEnabled, forKey: Keys.abEnabled) }
    }
    @Published var autoBrightnessSensitivity: Double {
        didSet { ud.set(autoBrightnessSensitivity, forKey: Keys.abSensitivity) }
    }
    @Published var autoBrightnessFloor: Double {
        didSet { ud.set(autoBrightnessFloor, forKey: Keys.abFloor) }
    }
    @Published var autoBrightnessCeiling: Double {
        didSet { ud.set(autoBrightnessCeiling, forKey: Keys.abCeiling) }
    }
    @Published var autoBrightnessAttackMs: Double {
        didSet { ud.set(autoBrightnessAttackMs, forKey: Keys.abAttack) }
    }
    @Published var autoBrightnessReleaseMs: Double {
        didSet { ud.set(autoBrightnessReleaseMs, forKey: Keys.abRelease) }
    }
    // iOS supports MICROPHONE only (mic-only per spec §9.3); the key stays
    // for forward-compat if AirPlay-receiver mode is added later.
    @Published var autoBrightnessAudioSourceKind: String {
        didSet { ud.set(autoBrightnessAudioSourceKind, forKey: Keys.abSourceKind) }
    }

    // ── Grab favourites / Shows ranking ───────────────────────────────
    @Published var favouriteMovers: Set<Int> {
        didSet { ud.set(favouriteMovers.map(String.init).joined(separator: ","), forKey: Keys.favourites) }
    }
    @Published var starredTimelines: Set<Int> {
        didSet { ud.set(starredTimelines.map(String.init).joined(separator: ","), forKey: Keys.starred) }
    }
    @Published var lastPlayedAt: [Int: Int64] {
        didSet {
            let raw = lastPlayedAt.map { "\($0.key)=\($0.value)" }.joined(separator: ",")
            ud.set(raw, forKey: Keys.lastPlayed)
        }
    }

    // ── Aim Wizard derived axes ───────────────────────────────────────
    @Published var aimWizardForwardX: Double { didSet { ud.set(aimWizardForwardX, forKey: Keys.wizFx) } }
    @Published var aimWizardForwardY: Double { didSet { ud.set(aimWizardForwardY, forKey: Keys.wizFy) } }
    @Published var aimWizardForwardZ: Double { didSet { ud.set(aimWizardForwardZ, forKey: Keys.wizFz) } }
    @Published var aimWizardUpX: Double { didSet { ud.set(aimWizardUpX, forKey: Keys.wizUx) } }
    @Published var aimWizardUpY: Double { didSet { ud.set(aimWizardUpY, forKey: Keys.wizUy) } }
    @Published var aimWizardUpZ: Double { didSet { ud.set(aimWizardUpZ, forKey: Keys.wizUz) } }
    @Published var aimWizardCompletedAt: String { didSet { ud.set(aimWizardCompletedAt, forKey: Keys.wizDone) } }

    private let ud = UserDefaults.standard

    init() {
        host = ud.string(forKey: Keys.host) ?? ""
        let savedPort = ud.integer(forKey: Keys.port)
        port = savedPort == 0 ? 8080 : savedPort
        // Stable per-install identifier; persists for the life of the app on
        // this device, regenerated only on uninstall.
        if let existing = ud.string(forKey: Keys.deviceId), !existing.isEmpty {
            deviceId = existing
        } else {
            let fresh = "ios-\(UUID().uuidString.prefix(8))"
            ud.set(fresh, forKey: Keys.deviceId)
            deviceId = fresh
        }

        userPosXmm = ud.object(forKey: Keys.userX) as? Double ?? 2000
        userPosYmm = ud.object(forKey: Keys.userY) as? Double ?? 2000
        userPosZmm = ud.object(forKey: Keys.userZ) as? Double ?? 1700

        autoBrightnessEnabled = ud.bool(forKey: Keys.abEnabled)
        autoBrightnessSensitivity = ud.object(forKey: Keys.abSensitivity) as? Double ?? 1.5
        autoBrightnessFloor = ud.object(forKey: Keys.abFloor) as? Double ?? 0.05
        autoBrightnessCeiling = ud.object(forKey: Keys.abCeiling) as? Double ?? 1.0
        autoBrightnessAttackMs = ud.object(forKey: Keys.abAttack) as? Double ?? 8
        autoBrightnessReleaseMs = ud.object(forKey: Keys.abRelease) as? Double ?? 220
        autoBrightnessAudioSourceKind = ud.string(forKey: Keys.abSourceKind) ?? "MICROPHONE"

        favouriteMovers = Self.parseIntSet(ud.string(forKey: Keys.favourites))
        starredTimelines = Self.parseIntSet(ud.string(forKey: Keys.starred))
        lastPlayedAt = Self.parseLastPlayed(ud.string(forKey: Keys.lastPlayed))

        aimWizardForwardX = ud.object(forKey: Keys.wizFx) as? Double ?? 0
        aimWizardForwardY = ud.object(forKey: Keys.wizFy) as? Double ?? 0
        aimWizardForwardZ = ud.object(forKey: Keys.wizFz) as? Double ?? 0
        aimWizardUpX = ud.object(forKey: Keys.wizUx) as? Double ?? 0
        aimWizardUpY = ud.object(forKey: Keys.wizUy) as? Double ?? 0
        aimWizardUpZ = ud.object(forKey: Keys.wizUz) as? Double ?? 0
        aimWizardCompletedAt = ud.string(forKey: Keys.wizDone) ?? ""
    }

    // ── Helpers ────────────────────────────────────────────────────────

    func toggleFavouriteMover(_ id: Int) {
        var s = favouriteMovers
        if s.contains(id) { s.remove(id) } else { s.insert(id) }
        favouriteMovers = s
    }

    func toggleStarredTimeline(_ id: Int) {
        var s = starredTimelines
        if s.contains(id) { s.remove(id) } else { s.insert(id) }
        starredTimelines = s
    }

    func recordTimelineStart(_ id: Int) {
        var m = lastPlayedAt
        m[id] = Int64(Date().timeIntervalSince1970 * 1000)
        lastPlayedAt = m
    }

    func aimWizardHasResult() -> Bool {
        let forward = (aimWizardForwardX, aimWizardForwardY, aimWizardForwardZ)
        let up = (aimWizardUpX, aimWizardUpY, aimWizardUpZ)
        return forward != (0, 0, 0) && up != (0, 0, 0)
    }

    func saveWizardResult(forward: [Double], up: [Double]) {
        guard forward.count == 3, up.count == 3 else { return }
        aimWizardForwardX = forward[0]
        aimWizardForwardY = forward[1]
        aimWizardForwardZ = forward[2]
        aimWizardUpX = up[0]
        aimWizardUpY = up[1]
        aimWizardUpZ = up[2]
        let iso = ISO8601DateFormatter().string(from: Date())
        aimWizardCompletedAt = iso
    }

    func factoryReset() {
        let domain = Bundle.main.bundleIdentifier ?? ""
        ud.removePersistentDomain(forName: domain)
        // Re-seed device id immediately so the next claim still identifies.
        let fresh = "ios-\(UUID().uuidString.prefix(8))"
        ud.set(fresh, forKey: Keys.deviceId)
        deviceId = fresh
    }

    // ── Static parsing ────────────────────────────────────────────────

    private static func parseIntSet(_ raw: String?) -> Set<Int> {
        guard let raw, !raw.isEmpty else { return [] }
        return Set(raw.split(separator: ",").compactMap { Int($0) })
    }

    private static func parseLastPlayed(_ raw: String?) -> [Int: Int64] {
        guard let raw, !raw.isEmpty else { return [:] }
        var m: [Int: Int64] = [:]
        for pair in raw.split(separator: ",") {
            let parts = pair.split(separator: "=")
            guard parts.count == 2,
                  let k = Int(parts[0]),
                  let v = Int64(parts[1]) else { continue }
            m[k] = v
        }
        return m
    }

    private enum Keys {
        static let host = "host"
        static let port = "port"
        static let deviceId = "device_id"
        static let userX = "user_pos_x_mm"
        static let userY = "user_pos_y_mm"
        static let userZ = "user_pos_z_mm"
        static let abEnabled = "auto_brightness_enabled"
        static let abSensitivity = "auto_brightness_sensitivity"
        static let abFloor = "auto_brightness_floor"
        static let abCeiling = "auto_brightness_ceiling"
        static let abAttack = "auto_brightness_attack_ms"
        static let abRelease = "auto_brightness_release_ms"
        static let abSourceKind = "auto_brightness_audio_source_kind"
        static let favourites = "favourite_movers_csv"
        static let starred = "starred_timelines_csv"
        static let lastPlayed = "last_played_map"
        static let wizFx = "aim_wizard_forward_x"
        static let wizFy = "aim_wizard_forward_y"
        static let wizFz = "aim_wizard_forward_z"
        static let wizUx = "aim_wizard_up_x"
        static let wizUy = "aim_wizard_up_y"
        static let wizUz = "aim_wizard_up_z"
        static let wizDone = "aim_wizard_completed_at"
    }
}
