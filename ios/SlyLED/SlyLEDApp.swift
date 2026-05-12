// SlyLEDApp.swift — entry point for the iOS shell.
// First TestFlight build (v0.1.0 — "TestFlight pipeline shake-down").
// Full UI parity with Android v1.8.1 comes in subsequent builds.

import SwiftUI

@main
struct SlyLEDApp: App {
    @StateObject private var connection = ConnectionState()
    @StateObject private var orchestrator = OrchestratorClient()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(connection)
                .environmentObject(orchestrator)
                .preferredColorScheme(.dark)
                .onAppear { connection.startPolling(client: orchestrator) }
        }
    }
}
