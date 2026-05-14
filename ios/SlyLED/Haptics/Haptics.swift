// Haptics — full catalogue from mobile_ui_redesign.md §6.3 / Android
// Haptics.kt. UIImpactFeedbackGenerator + UINotificationFeedbackGenerator
// cover most events; CoreHaptics CHHapticEngine drives the strobe-press
// continuous low rumble.

import Foundation
import UIKit
import CoreHaptics

enum HapticEvent {
    case lightTick        // tap, toggle, slider release
    case softTick         // ±5% step buttons, sub-page navigation
    case successTick      // start show, start timeline (medium)
    case heavyThud        // STOP, blackout actions, takeover confirm
    case heavyDouble      // panic blackout (long-press logo)
    case noGoBump         // disconnected write, claim conflict denied
    case lowRumble        // strobe momentary press (continuous, while held)
    case profileError     // profile shape error tooltip
    case sliderStep       // per-5% slider boundary
}

@MainActor
enum Haptics {
    private static var engine: CHHapticEngine?
    private static var rumblePlayer: CHHapticAdvancedPatternPlayer?

    static func fire(_ event: HapticEvent) {
        switch event {
        case .lightTick:
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        case .softTick:
            UIImpactFeedbackGenerator(style: .soft).impactOccurred()
        case .successTick:
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        case .heavyThud:
            UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
        case .heavyDouble:
            let g = UIImpactFeedbackGenerator(style: .heavy)
            g.impactOccurred()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.13) {
                g.impactOccurred()
            }
        case .noGoBump:
            UINotificationFeedbackGenerator().notificationOccurred(.error)
        case .lowRumble:
            startRumble()
        case .profileError:
            UIImpactFeedbackGenerator(style: .rigid).impactOccurred()
        case .sliderStep:
            UISelectionFeedbackGenerator().selectionChanged()
        }
    }

    /// Start the strobe-press low rumble. Returns immediately; the rumble
    /// runs until `stopRumble()` is called.
    static func startRumble() {
        guard CHHapticEngine.capabilitiesForHardware().supportsHaptics else {
            // Older device: fall back to a light pulse — visual feedback
            // is the operator's primary signal anyway.
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
            return
        }
        do {
            if engine == nil {
                let e = try CHHapticEngine()
                e.resetHandler = { [weak e] in try? e?.start() }
                try e.start()
                engine = e
            }
            // Continuous event: intensity 0.5, sharpness 0.3, duration 30s
            // (we stop it manually on release; 30s is just a ceiling).
            let intensity = CHHapticEventParameter(parameterID: .hapticIntensity, value: 0.5)
            let sharpness = CHHapticEventParameter(parameterID: .hapticSharpness, value: 0.3)
            let event = CHHapticEvent(eventType: .hapticContinuous,
                                      parameters: [intensity, sharpness],
                                      relativeTime: 0,
                                      duration: 30)
            let pattern = try CHHapticPattern(events: [event], parameters: [])
            let player = try engine?.makeAdvancedPlayer(with: pattern)
            try player?.start(atTime: 0)
            rumblePlayer = player
        } catch {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
    }

    static func stopRumble() {
        try? rumblePlayer?.stop(atTime: 0)
        rumblePlayer = nil
    }
}
