// SettingsScreen — full Settings replacing the v0.1 sheet. Sections:
//  - Server config (host + port, save & connect)
//  - Auto Brightness calibration (sensitivity / floor / ceiling / attack /
//    release sliders)
//  - Aim Wizard (button → AimWizardSheet)
//  - About + Factory reset
//
// The Auto Brightness *toggle* stays on the Master page; calibration
// (live tunables) belongs here.

import SwiftUI

struct SettingsScreen: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject var prefs: ServerPreferences
    @EnvironmentObject var client: OrchestratorClient
    @EnvironmentObject var autoBrightness: MicAutoBrightness

    @State private var hostField = ""
    @State private var portField = "8080"
    @State private var resetConfirm = false
    @State private var aimWizardOpen = false

    var body: some View {
        NavigationStack {
            Form {
                serverSection
                autoBrightnessSection
                aimWizardSection
                aboutSection
                resetSection
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear {
                hostField = client.host
                portField = String(client.port)
            }
            .sheet(isPresented: $aimWizardOpen) {
                AimWizardSheet()
                    .environmentObject(client)
                    .environmentObject(prefs)
            }
            .confirmationDialog("Factory reset?",
                                isPresented: $resetConfirm,
                                titleVisibility: .visible) {
                Button("Wipe all settings", role: .destructive) {
                    prefs.factoryReset()
                    hostField = ""
                    portField = "8080"
                }
                Button("Cancel", role: .cancel) {}
            } message: {
                Text("This clears favourites, starred shows, last-played history, Auto Brightness calibration, and the aim wizard result.")
            }
        }
    }

    private var serverSection: some View {
        Section("Orchestrator server") {
            TextField("Host (e.g. 192.168.1.42)", text: $hostField)
                .keyboardType(.URL)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
            TextField("Port", text: $portField).keyboardType(.numberPad)
            Button("Save & connect") {
                if let p = Int(portField) {
                    client.saveServer(host: hostField, port: p)
                    Haptics.fire(.successTick)
                }
            }
            .disabled(hostField.isEmpty || Int(portField) == nil)
            HStack {
                Text("Device ID")
                Spacer()
                Text(prefs.deviceId).font(.kpMono).foregroundStyle(Color.kpLightSlate)
            }
        }
    }

    private var autoBrightnessSection: some View {
        Section("Auto Brightness calibration") {
            slider(title: "Sensitivity",
                   value: Binding(get: { prefs.autoBrightnessSensitivity },
                                  set: { prefs.autoBrightnessSensitivity = $0; autoBrightness.configure() }),
                   range: 0.1...8.0,
                   format: "%.2f×")
            slider(title: "Floor",
                   value: Binding(get: { prefs.autoBrightnessFloor },
                                  set: { prefs.autoBrightnessFloor = $0; autoBrightness.configure() }),
                   range: 0...1,
                   format: "%.2f")
            slider(title: "Ceiling",
                   value: Binding(get: { prefs.autoBrightnessCeiling },
                                  set: { prefs.autoBrightnessCeiling = $0; autoBrightness.configure() }),
                   range: 0...1,
                   format: "%.2f")
            slider(title: "Attack",
                   value: Binding(get: { prefs.autoBrightnessAttackMs },
                                  set: { prefs.autoBrightnessAttackMs = $0; autoBrightness.configure() }),
                   range: 1...200,
                   format: "%.0f ms")
            slider(title: "Release",
                   value: Binding(get: { prefs.autoBrightnessReleaseMs },
                                  set: { prefs.autoBrightnessReleaseMs = $0; autoBrightness.configure() }),
                   range: 20...2000,
                   format: "%.0f ms")
            HStack {
                Text("Source")
                Spacer()
                Text("Microphone").foregroundStyle(Color.kpLightSlate)
            }
            Text("iOS supports microphone capture only. The system audio source is Android-only.")
                .font(.kpCaption)
                .foregroundStyle(Color.kpMutedSlate)
        }
    }

    private func slider(title: String, value: Binding<Double>,
                        range: ClosedRange<Double>, format: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer()
                Text(String(format: format, value.wrappedValue))
                    .font(.kpMono)
                    .foregroundStyle(Color.kpLightSlate)
            }
            Slider(value: value, in: range)
                .tint(Color.kpLuminaBlue)
        }
    }

    private var aimWizardSection: some View {
        Section("Mover Controller — Aim Wizard") {
            Button("Run aim wizard") { aimWizardOpen = true }
            if prefs.aimWizardHasResult() {
                Text("Calibrated \(prefs.aimWizardCompletedAt)")
                    .font(.kpCaption)
                    .foregroundStyle(Color.kpMutedSlate)
            } else {
                Text("Not yet calibrated. Run on the first phone you'll use with each fixture.")
                    .font(.kpCaption)
                    .foregroundStyle(Color.kpMutedSlate)
            }
        }
    }

    private var aboutSection: some View {
        Section("About") {
            HStack { Text("Version"); Spacer()
                Text(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "—")
                    .foregroundStyle(Color.kpLightSlate)
            }
            HStack { Text("Build"); Spacer()
                Text(Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "—")
                    .foregroundStyle(Color.kpLightSlate)
            }
        }
    }

    private var resetSection: some View {
        Section {
            Button(role: .destructive) { resetConfirm = true } label: {
                Text("Factory reset")
            }
        }
    }
}
