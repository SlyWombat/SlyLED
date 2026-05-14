// RootShell — 3-tab TabView (Stage / Control / Status) with a single
// top toolbar: SlyLED logo (long-press → blackout), ConnectionPill
// (center), Settings gear (trailing).

import SwiftUI

enum RootTab: Int { case stage, control, status }

struct RootShell: View {
    @EnvironmentObject var connection: ConnectionState
    @EnvironmentObject var client: OrchestratorClient
    @EnvironmentObject var prefs: ServerPreferences
    @EnvironmentObject var control: ControlViewModel
    @EnvironmentObject var autoBrightness: MicAutoBrightness

    @State private var tab: RootTab = .control
    @State private var showSettings = false

    var body: some View {
        NavigationStack {
            TabView(selection: $tab) {
                LiveStageScreen(client: client)
                    .tabItem { Label("Stage", systemImage: "rectangle.stack.fill") }
                    .tag(RootTab.stage)
                ControlScreen()
                    .tabItem { Label("Control", systemImage: "slider.horizontal.3") }
                    .tag(RootTab.control)
                StatusScreen(client: client)
                    .tabItem { Label("Status", systemImage: "antenna.radiowaves.left.and.right") }
                    .tag(RootTab.status)
            }
            .tint(Color.kpCyanSecondary)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    logo
                }
                ToolbarItem(placement: .principal) {
                    ConnectionPillView(state: connection.state) {
                        connection.retry(client: client)
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button { showSettings = true } label: {
                        Image(systemName: "gearshape.fill")
                    }
                    .tint(Color.kpNearWhite)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .sheet(isPresented: $showSettings) {
                SettingsScreen()
                    .environmentObject(prefs)
                    .environmentObject(client)
                    .environmentObject(autoBrightness)
            }
            .onAppear {
                // Configure UDP client with the current host so Auto Brightness
                // pushes land on the right machine without waiting for the
                // operator to toggle the feature.
                UdpClient.shared.configure(host: client.host)
            }
            .onChange(of: client.host) { _, newHost in
                UdpClient.shared.configure(host: newHost)
            }
        }
    }

    private var logo: some View {
        Text("SlyLED")
            .font(.kpTitleSm)
            .foregroundStyle(Color.kpNearWhite)
            .onLongPressGesture(minimumDuration: 0.6) {
                control.blackout()
            }
    }
}
