// Codable models for the SlyLED REST surface. Mirrors Android's
// data/model/Models.kt and the JSON shapes the orchestrator emits today
// (parent_server.py). Only the fields the operator app actually reads or
// writes are typed — the rest stay as untouched JsonValue for forward
// compatibility.

import Foundation

// ── Status / Settings ─────────────────────────────────────────────────

struct StatusResponse: Codable {
    var role: String?
    var hostname: String?
    var version: String?
    var firmwareVersion: String?
    var uptime: Double?
}

struct Settings: Codable {
    var name: String?
    var globalBrightness: Int?
    var runnerRunning: Bool?
    var activeTimeline: Int?
    var darkMode: Int?
    var logging: Bool?
}

struct OkResponse: Codable {
    var ok: Bool?
    var err: String?
    var detail: String?
    var id: Int?
    var duplicate: Bool?
    var warning: String?
}

// ── Children (StatusScreen) ────────────────────────────────────────────

struct Child: Codable, Identifiable {
    var id: Int
    var ip: String?
    var hostname: String?
    var name: String?
    var desc: String?
    var status: Int?
    var seen: Int64?
    var type: String?
    var boardType: String?
    var fwVersion: String?
    var rssi: Int?
    var startupDone: Bool?

    var online: Bool { (status ?? 0) == 1 }
    var rssiDbm: Int? {
        guard let r = rssi else { return nil }
        return r > 0 ? -r : r
    }
    var signalBars: Int {
        guard let dbm = rssiDbm else { return 0 }
        if dbm >= -50 { return 4 }
        if dbm >= -60 { return 3 }
        if dbm >= -70 { return 2 }
        if dbm >= -80 { return 1 }
        return 0
    }

    // #910 — MMwave radar presence node. The node has no HTTP /status
    // endpoint, so `type` stays the "slyled" default and `boardType` stays
    // blank; identification is the announced hostname prefix ("MMW-",
    // mmwave/MmwConfig.h). The type/boardType checks cover a future server
    // that stamps the registry board id ("mmwave"). Mirrors Android
    // Child.isRadarNode (Models.kt).
    var isRadarNode: Bool {
        if let h = hostname, h.uppercased().hasPrefix("MMW-") { return true }
        if type?.lowercased() == "mmwave" { return true }
        if boardType?.lowercased() == "mmwave" { return true }
        return false
    }
}

// ── Fixtures / Profiles ───────────────────────────────────────────────

struct Fixture: Codable, Identifiable {
    var id: Int
    var name: String
    var type: String?            // linear, point, surface, group
    var fixtureType: String?     // led / dmx / camera
    var dmxProfileId: String?
    var dmxUniverse: Int?
    var dmxStartAddr: Int?
    var dmxChannelCount: Int?
    var x: Int?
    var y: Int?
    var z: Int?
    var rotation: [Double]?
    var positioned: Bool?
    var calibrated: Bool?
    var moverCalibrated: Bool?
}

// ── Stage objects (/api/objects) ──────────────────────────────────────

struct ObjectTransform: Codable {
    var pos: [Double]?      // object CENTER, mm (renderer convention, #Q1)
    var rot: [Double]?
    var scale: [Double]?    // [width(X), height(Z), depth(Y)], mm
}

// #900/#912 — provenance stamp on temporal objects. Absent from older
// servers and for operator-placed objects. type is "radar" (fixtureId +
// node) or "camera" (cameraId).
struct ObjectSource: Codable {
    var type: String?
    var fixtureId: Int?
    var cameraId: Int?
    var node: String?
}

struct StageObject: Codable, Identifiable {
    var id: Int
    var name: String?
    var objectType: String?  // person / prop / wall / custom
    var mobility: String?    // static / moving
    var color: String?
    var opacity: Int?
    var transform: ObjectTransform?
    var temporal: Bool?      // wire key "_temporal"
    var ttl: Double?         // float on the wire for radar persons (2.0)
    var source: ObjectSource?

    enum CodingKeys: String, CodingKey {
        case id, name, objectType, mobility, color, opacity, transform, ttl, source
        case temporal = "_temporal"
    }

    // Same predicate Android's LiveStage canvas uses for the tracked-object pass.
    var isTracked: Bool { (temporal ?? false) || mobility == "moving" }
    var isRadarSourced: Bool { source?.type == "radar" }
}

struct DmxProfile: Codable, Identifiable {
    var id: String
    var name: String
    var manufacturer: String?
    var category: String?
    var channelCount: Int?
    var colorMode: String?
    var beamWidth: Int?
    var panRange: Int?
    var tiltRange: Int?
    var builtin: Bool?
}

// ── Timelines / Shows ─────────────────────────────────────────────────

struct Timeline: Codable, Identifiable {
    var id: Int
    var name: String
    var durationS: Int?
    var loop: Bool?
}

struct TimelineStatus: Codable {
    var id: Int?
    var name: String?
    var running: Bool?
    var elapsed: Int?
    var durationS: Int?
    var loop: Bool?
    var activeTimeline: Int?
}

struct ShowStatus: Codable {
    var running: Bool?
    var currentTimeline: Int?
    var currentIndex: Int?
    var totalTimelines: Int?
    var elapsed: Double?
    var duration: Double?
    var loop: Bool?
}

struct PlaylistItem: Codable, Identifiable {
    var id: Int
    var name: String?
    var durationS: Double?
    var baked: Bool?
}

struct ShowPlaylist: Codable {
    var order: [Int]?
    var loopAll: Bool?
    var items: [PlaylistItem]?
    var totalDurationS: Double?
}

// ── Mover Control ─────────────────────────────────────────────────────

struct MoverClaim: Codable {
    var moverId: Int?
    var deviceId: String?
    var deviceName: String?
    var deviceType: String?
    var state: String?
    var lastWriteAge: Double?
    var calibrated: Bool?
    var panNorm: Double?
    var tiltNorm: Double?
}

struct MoverEngineHealth: Codable {
    var running: Bool?
    var engineType: String?
    var droppedWrites: Int?
}

struct MoverControlStatus: Codable {
    var claims: [MoverClaim]?
    var engine: MoverEngineHealth?
}

// ── Aim Wizard ────────────────────────────────────────────────────────

struct AimWizardPose: Codable {
    let role: String        // "neutral" | "pitch_forward" | "yaw_left"
    let quat: [Double]      // [w, x, y, z]
}

struct AimWizardSubmit: Codable {
    let deviceId: String
    let poses: [AimWizardPose]
}

struct AimWizardResult: Codable {
    var ok: Bool?
    var err: String?
    var detail: String?
    var forwardLocal: [Double]?
    var upLocal: [Double]?
}

// ── Generic encodable request bodies (typed so we get compile checks) ─

struct ValueWrapper<V: Encodable>: Encodable {
    let value: V
}

struct DmxTest: Encodable {
    var pan: Double?
    var tilt: Double?
    var dimmer: Double?
}

struct ClaimReq: Encodable {
    let moverId: Int
    let deviceId: String
    let deviceName: String
    let deviceType: String
    let force: Bool?
}

struct ReleaseReq: Encodable {
    let moverId: Int
    let deviceId: String
}

struct StartReq: Encodable {
    let moverId: Int
    let deviceId: String
}

struct OrientReq: Encodable {
    let moverId: Int
    let deviceId: String
    let roll: Double
    let pitch: Double
    let yaw: Double
    let quat: [Double]
}

struct ColorReq: Encodable {
    let moverId: Int
    let deviceId: String
    let r: Int
    let g: Int
    let b: Int
    let dimmer: Int?
}

struct FlashReq: Encodable {
    let moverId: Int
    let deviceId: String
    let on: Bool
}

struct CalibrateStartReq: Encodable {
    let moverId: Int
    let deviceId: String
    let roll: Double
    let pitch: Double
    let yaw: Double
}

struct CalibrateEndReq: Encodable {
    let moverId: Int
    let deviceId: String
    let roll: Double
    let pitch: Double
    let yaw: Double
    let quat: [Double]?
}

struct GripReq: Encodable {
    let deviceId: String
    let forwardLocal: [Double]
    let upLocal: [Double]
}

struct DisconnectReq: Encodable {
    let deviceId: String
}

// /api/fixtures/<id>/channel-write — body is {writes:{"<offset>": byte}}
struct ChannelWriteReq: Encodable {
    let writes: [String: Int]
}

// /api/brightness fast path
struct BrightnessReq: Encodable {
    let value: Int
}

// ── Live state ────────────────────────────────────────────────────────

// fixtures-live response: { "<fixtureId>": { r, g, b, panDeg, tiltDeg, ... } }
struct FixtureLive: Codable {
    var r: Int?
    var g: Int?
    var b: Int?
    var panDeg: Double?
    var tiltDeg: Double?
}
