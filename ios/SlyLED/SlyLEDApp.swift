// SlyLEDApp — entry point. Wires the `@StateObject` graph: ServerPreferences
// is the persistence root, OrchestratorClient + MicAutoBrightness consume
// it, ControlViewModel depends on client + connection state + prefs.

import SwiftUI

@main
struct SlyLEDApp: App {
    @StateObject private var prefs: ServerPreferences
    @StateObject private var client: OrchestratorClient
    @StateObject private var connection = ConnectionState()
    @StateObject private var control: ControlViewModel
    @StateObject private var autoBrightness: MicAutoBrightness

    init() {
        let prefs = ServerPreferences()
        let client = OrchestratorClient(prefs: prefs)
        let conn = ConnectionState()
        let control = ControlViewModel(client: client, prefs: prefs, connection: conn)
        let autoBri = MicAutoBrightness(prefs: prefs)

        _prefs = StateObject(wrappedValue: prefs)
        _client = StateObject(wrappedValue: client)
        _connection = StateObject(wrappedValue: conn)
        _control = StateObject(wrappedValue: control)
        _autoBrightness = StateObject(wrappedValue: autoBri)
    }

    var body: some Scene {
        WindowGroup {
            RootShell()
                .environmentObject(prefs)
                .environmentObject(client)
                .environmentObject(connection)
                .environmentObject(control)
                .environmentObject(autoBrightness)
                .preferredColorScheme(.dark)
                .onAppear {
                    connection.startPolling(client: client)
                    control.startPolling()
                }
        }
    }
}
