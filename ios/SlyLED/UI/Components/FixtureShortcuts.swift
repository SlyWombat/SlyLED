// FixtureShortcuts — Swift twin of Android FixtureShortcuts.kt.
//
// Three-pass resolver, identical to the Kotlin reference:
//   1. Explicit `channel.shortcut` field (profile-author opt-in).
//   2. Capability-type heuristic (strobe + ShutterStrobe/Strobe → STROBE_MOMENTARY,
//      uv type → UV_TOGGLE).
//   3. Name-match fallback for dimmer / intensity / speed / reset types.
//
// The shared snapshot-test corpus (tests/fixtures/profiles/) must produce
// identical output across the SPA JS, Android Kotlin, and this Swift port.

import Foundation

enum ShortcutId: String, CaseIterable {
    case bubbleToggle    = "bubble-toggle"
    case hazeSegmented   = "haze-segmented"
    case fanSegmented    = "fan-segmented"
    case colorSwatch     = "color-swatch"
    case uvToggle        = "uv-toggle"
    case strobeMomentary = "strobe-momentary"
    case cleanMode       = "clean-mode"

    static func fromTag(_ tag: String?) -> ShortcutId? {
        guard let tag else { return nil }
        return ShortcutId(rawValue: tag)
    }

    var order: Int {
        switch self {
        case .bubbleToggle:    return 0
        case .hazeSegmented:   return 1
        case .fanSegmented:    return 2
        case .colorSwatch:     return 3
        case .uvToggle:        return 4
        case .strobeMomentary: return 5
        case .cleanMode:       return 6
        }
    }
}

enum ShortcutUi {
    case toggle
    case segmented
    case colorSwatch
    case momentary
    case longPress
}

struct SegmentedOption: Identifiable {
    let label: String
    let value: Int
    var id: String { label }
}

struct ColorSwatchOption: Identifiable {
    let label: String
    let r: Int
    let g: Int
    let b: Int
    var id: String { label }
}

struct ResolvedShortcut: Identifiable {
    let id: ShortcutId
    let ui: ShortcutUi
    let icon: String
    let label: String
    var channelOffset: Int?
    var channelName: String?
    var anchorOffset: Int?
    var channelOffsets: [String: Int]?
    var onValue: Int = 255
    var offValue: Int = 0
    var confirmHoldMs: Int = 0
    var segments: [SegmentedOption] = []
    var swatches: [ColorSwatchOption] = []
    var capabilities: [[String: Any]] = []

    static func == (lhs: ResolvedShortcut, rhs: ResolvedShortcut) -> Bool {
        lhs.id == rhs.id && lhs.channelOffset == rhs.channelOffset
    }
}

enum FixtureShortcuts {
    private static let nameHeuristic: [(String, ShortcutId)] = [
        ("bubble", .bubbleToggle),
        ("haze",   .hazeSegmented),
        ("fog",    .hazeSegmented),
        ("fan",    .fanSegmented),
        ("blower", .fanSegmented),
        ("clean",  .cleanMode),
    ]
    private static let eligibleNameTypes: Set<String> = ["dimmer", "intensity", "speed", "reset"]

    static func specFor(_ id: ShortcutId) -> ResolvedShortcut {
        switch id {
        case .bubbleToggle:
            return ResolvedShortcut(id: id, ui: .toggle, icon: "🫧", label: "BUBBLES",
                                    onValue: 255, offValue: 0)
        case .hazeSegmented:
            return ResolvedShortcut(id: id, ui: .segmented, icon: "💨", label: "HAZE",
                                    segments: [
                                        .init(label: "OFF",  value: 0),
                                        .init(label: "LOW",  value: 64),
                                        .init(label: "MED",  value: 128),
                                        .init(label: "HIGH", value: 220),
                                    ])
        case .fanSegmented:
            return ResolvedShortcut(id: id, ui: .segmented, icon: "🌀", label: "FAN",
                                    segments: [
                                        .init(label: "SLOW", value: 64),
                                        .init(label: "MED",  value: 160),
                                        .init(label: "FAST", value: 255),
                                    ])
        case .colorSwatch:
            return ResolvedShortcut(id: id, ui: .colorSwatch, icon: "💡", label: "COLOUR",
                                    swatches: [
                                        .init(label: "Red",     r: 255, g:   0, b:   0),
                                        .init(label: "Orange",  r: 255, g: 128, b:   0),
                                        .init(label: "Yellow",  r: 255, g: 255, b:   0),
                                        .init(label: "Green",   r:   0, g: 255, b:   0),
                                        .init(label: "Cyan",    r:   0, g: 255, b: 255),
                                        .init(label: "Blue",    r:   0, g:   0, b: 255),
                                        .init(label: "Magenta", r: 255, g:   0, b: 255),
                                        .init(label: "White",   r: 255, g: 255, b: 255),
                                        .init(label: "Off",     r:   0, g:   0, b:   0),
                                    ])
        case .uvToggle:
            return ResolvedShortcut(id: id, ui: .toggle, icon: "🟣", label: "UV",
                                    onValue: 255, offValue: 0)
        case .strobeMomentary:
            return ResolvedShortcut(id: id, ui: .momentary, icon: "⚡", label: "STROBE")
        case .cleanMode:
            return ResolvedShortcut(id: id, ui: .longPress, icon: "🧼", label: "CLEAN",
                                    onValue: 255, offValue: 0, confirmHoldMs: 1500)
        }
    }

    /// Resolve a single channel dict (from profile JSON) to a ShortcutId, or nil.
    static func resolveChannelShortcut(_ channel: [String: Any]) -> ShortcutId? {
        // Pass 1: explicit annotation.
        if let explicit = ShortcutId.fromTag(channel["shortcut"] as? String) {
            return explicit
        }

        let type = (channel["type"] as? String) ?? ""
        let caps = (channel["capabilities"] as? [[String: Any]]) ?? []

        // Pass 2: capability heuristic.
        if type == "strobe" {
            let hasStrobeRange = caps.contains { cap in
                (cap["type"] as? String) == "ShutterStrobe"
                && (cap["shutterEffect"] as? String) == "Strobe"
            }
            if hasStrobeRange { return .strobeMomentary }
        }
        if type == "uv" { return .uvToggle }

        // Pass 3: name-match for eligible types.
        if eligibleNameTypes.contains(type) {
            let name = ((channel["name"] as? String) ?? "").lowercased()
            for (needle, sid) in nameHeuristic where name.contains(needle) {
                return sid
            }
        }
        return nil
    }

    /// Walk a profile JSON dict and return the ordered, deduped resolved shortcuts.
    static func resolveShortcutsForProfile(_ profile: [String: Any]) -> [ResolvedShortcut] {
        let channels = (profile["channels"] as? [[String: Any]]) ?? []
        if channels.isEmpty { return [] }

        // channel_map fallback — rebuild from `channels` list if not provided.
        var channelMap: [String: Int] = [:]
        if let provided = profile["channel_map"] as? [String: Any] {
            for (k, v) in provided {
                if let n = v as? NSNumber { channelMap[k] = n.intValue }
                else if let i = v as? Int { channelMap[k] = i }
            }
        } else {
            for ch in channels {
                let t = (ch["type"] as? String) ?? ""
                if !t.isEmpty && channelMap[t] == nil {
                    channelMap[t] = (ch["offset"] as? Int) ?? 0
                }
            }
        }

        var out: [ResolvedShortcut] = []
        var swatchAdded = false

        for ch in channels {
            guard let sid = resolveChannelShortcut(ch) else { continue }
            var resolved = specFor(sid)

            if sid == .colorSwatch {
                if swatchAdded { continue }
                guard channelMap["red"] != nil,
                      channelMap["green"] != nil,
                      channelMap["blue"] != nil else { continue }
                swatchAdded = true
                var offsets: [String: Int] = [
                    "red":   channelMap["red"]!,
                    "green": channelMap["green"]!,
                    "blue":  channelMap["blue"]!,
                ]
                if let uv = channelMap["uv"] { offsets["uv"] = uv }
                resolved.anchorOffset = (ch["offset"] as? Int) ?? 0
                resolved.channelOffsets = offsets
                out.append(resolved)
                continue
            }

            resolved.channelOffset = (ch["offset"] as? Int) ?? 0
            resolved.channelName = ch["name"] as? String
            resolved.capabilities = (ch["capabilities"] as? [[String: Any]]) ?? []
            out.append(resolved)
        }

        return out.sorted { $0.id.order < $1.id.order }
    }

    /// Midpoint of the first ShutterStrobe Strobe capability range, for the
    /// momentary press value. Returns nil if no such capability exists.
    static func strobeMomentaryValue(_ shortcut: ResolvedShortcut) -> Int? {
        for cap in shortcut.capabilities {
            guard (cap["type"] as? String) == "ShutterStrobe",
                  (cap["shutterEffect"] as? String) == "Strobe",
                  let range = cap["range"] as? [Any],
                  range.count >= 2,
                  let lo = range[0] as? Int,
                  let hi = range[1] as? Int else { continue }
            return (lo + hi) / 2
        }
        return nil
    }

    /// Midpoint of the first ShutterStrobe Open range, for the release value.
    static func strobeOpenValue(_ shortcut: ResolvedShortcut) -> Int {
        for cap in shortcut.capabilities {
            guard (cap["type"] as? String) == "ShutterStrobe",
                  (cap["shutterEffect"] as? String) == "Open",
                  let range = cap["range"] as? [Any],
                  range.count >= 2,
                  let lo = range[0] as? Int,
                  let hi = range[1] as? Int else { continue }
            return (lo + hi) / 2
        }
        return 0
    }
}
