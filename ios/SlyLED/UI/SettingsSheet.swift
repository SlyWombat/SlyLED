// SettingsSheet.swift — server config + version info.
// v0.1.0 surface; iOS-only fields land in future builds.

import SwiftUI

struct SettingsSheet: View {
    @Environment(\.dismiss) private var dismiss
    @EnvironmentObject var orchestrator: OrchestratorClient
    @State private var hostField = ""
    @State private var portField = "8080"

    var body: some View {
        NavigationStack {
            Form {
                Section("Orchestrator server") {
                    TextField("Host (e.g. 192.168.1.42)", text: $hostField)
                        .keyboardType(.URL)
                        .autocorrectionDisabled()
                        .textInputAutocapitalization(.never)
                    TextField("Port", text: $portField)
                        .keyboardType(.numberPad)
                    Button("Save & connect") {
                        if let p = Int(portField) {
                            orchestrator.saveServer(host: hostField, port: p)
                            dismiss()
                        }
                    }
                    .disabled(hostField.isEmpty || Int(portField) == nil)
                }
                Section("About") {
                    HStack { Text("Version"); Spacer(); Text("0.1.0").foregroundStyle(.secondary) }
                    HStack { Text("Build"); Spacer(); Text("1").foregroundStyle(.secondary) }
                    Text("TestFlight pipeline shake-down. Full UI coming in subsequent builds.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .onAppear {
                hostField = orchestrator.host
                portField = String(orchestrator.port)
            }
        }
    }
}
