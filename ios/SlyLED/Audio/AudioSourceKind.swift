// AudioSourceKind — iOS Auto Brightness sources.
//
// Per ios_parity_spec.md §9.3 (v2 decision): iOS supports microphone only.
// The "off" state is captured by ServerPreferences.autoBrightnessEnabled
// = false rather than a third enum value.

import Foundation

enum AudioSourceKind: String, CaseIterable, Identifiable {
    case microphone = "MICROPHONE"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .microphone: return "Microphone"
        }
    }
}
