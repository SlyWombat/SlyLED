// SettingsViewModel — surfaces persisted prefs + Auto Brightness controls
// to the SettingsScreen. ServerPreferences is the source of truth; this
// VM is mainly a façade for the UI to bind to.

import Foundation
import Combine

@MainActor
final class SettingsViewModel: ObservableObject {
    let prefs: ServerPreferences
    let autoBrightness: MicAutoBrightness
    let client: OrchestratorClient
    private let udp = UdpClient.shared

    @Published var autoBrightnessEnabled: Bool {
        didSet {
            prefs.autoBrightnessEnabled = autoBrightnessEnabled
            Task {
                if autoBrightnessEnabled {
                    udp.configure(host: client.host)
                    await autoBrightness.start()
                } else {
                    autoBrightness.stop()
                }
            }
        }
    }

    init(prefs: ServerPreferences, autoBrightness: MicAutoBrightness, client: OrchestratorClient) {
        self.prefs = prefs
        self.autoBrightness = autoBrightness
        self.client = client
        self.autoBrightnessEnabled = prefs.autoBrightnessEnabled
    }

    // Live tunables (write-through to ServerPreferences + MicAutoBrightness).

    func updateSensitivity(_ v: Double) {
        prefs.autoBrightnessSensitivity = v
        autoBrightness.configure()
    }
    func updateFloor(_ v: Double) {
        prefs.autoBrightnessFloor = v
        autoBrightness.configure()
    }
    func updateCeiling(_ v: Double) {
        prefs.autoBrightnessCeiling = v
        autoBrightness.configure()
    }
    func updateAttack(_ v: Double) {
        prefs.autoBrightnessAttackMs = v
        autoBrightness.configure()
    }
    func updateRelease(_ v: Double) {
        prefs.autoBrightnessReleaseMs = v
        autoBrightness.configure()
    }
}
