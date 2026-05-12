// ContentView.swift — root view for the v0.1.0 iOS shell.
// Single tab: a stripped-down Control surface (brightness + STOP) plus
// a Settings sheet for the orchestrator server config.

import SwiftUI

struct ContentView: View {
    @EnvironmentObject var connection: ConnectionState
    @EnvironmentObject var orchestrator: OrchestratorClient
    @State private var showSettings = false
    @State private var brightness: Double = 255
    @State private var draggingBrightness = false

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                // —— Master brightness ——
                VStack(alignment: .leading, spacing: 12) {
                    Text("BRIGHTNESS")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(.secondary)
                    HStack {
                        Text("\(Int(brightness))")
                            .font(.system(size: 36, weight: .bold, design: .monospaced))
                        Spacer()
                        Text("\(Int(brightness / 255 * 100))%")
                            .font(.title3.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    Slider(
                        value: $brightness,
                        in: 0...255,
                        onEditingChanged: { editing in
                            draggingBrightness = editing
                            if !editing {
                                Task { try? await orchestrator.setBrightness(Int(brightness)) }
                                heavyHaptic(.light)
                            }
                        }
                    )
                    HStack(spacing: 8) {
                        Button {
                            brightness = max(0, brightness - 13)
                            Task { try? await orchestrator.setBrightness(Int(brightness)) }
                            heavyHaptic(.soft)
                        } label: {
                            Label("−5%", systemImage: "minus")
                                .frame(maxWidth: .infinity, minHeight: 44)
                        }
                        .buttonStyle(.bordered)
                        Button {
                            brightness = min(255, brightness + 13)
                            Task { try? await orchestrator.setBrightness(Int(brightness)) }
                            heavyHaptic(.soft)
                        } label: {
                            Label("+5%", systemImage: "plus")
                                .frame(maxWidth: .infinity, minHeight: 44)
                        }
                        .buttonStyle(.bordered)
                    }
                }
                .padding(20)
                .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 12))

                // —— STOP show ——
                Button(role: .destructive) {
                    heavyHaptic(.heavy)
                    Task { try? await orchestrator.stopShow() }
                } label: {
                    Label("STOP SHOW", systemImage: "stop.fill")
                        .font(.title3.weight(.bold))
                        .frame(maxWidth: .infinity, minHeight: 56)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)

                Spacer()

                // —— Footer ——
                VStack(spacing: 4) {
                    Text("SlyLED iOS v0.1.0")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                    Text("TestFlight pipeline shake-down — full UI coming in a future build")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                        .multilineTextAlignment(.center)
                }
                .padding(.bottom, 8)
            }
            .padding(16)
            .navigationTitle("SlyLED")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Text("SlyLED")
                        .font(.headline)
                        .onLongPressGesture(minimumDuration: 0.6) {
                            // Long-press logo → blackout
                            heavyHaptic(.heavyDouble)
                            brightness = 0
                            Task { try? await orchestrator.setBrightness(0) }
                        }
                }
                ToolbarItem(placement: .principal) {
                    ConnectionPillView(state: connection.state) {
                        connection.retry(client: orchestrator)
                    }
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape.fill")
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsSheet()
            }
            .task {
                // Initial brightness fetch.
                if let s = try? await orchestrator.getSettings(),
                   let g = s.globalBrightness {
                    brightness = Double(g)
                }
            }
        }
    }
}

// —— Haptics ——
enum Haptic { case light, soft, heavy, heavyDouble }

func heavyHaptic(_ kind: Haptic) {
    switch kind {
    case .light:
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
    case .soft:
        if #available(iOS 13.0, *) {
            UIImpactFeedbackGenerator(style: .soft).impactOccurred()
        }
    case .heavy:
        UIImpactFeedbackGenerator(style: .heavy).impactOccurred()
    case .heavyDouble:
        let g = UIImpactFeedbackGenerator(style: .heavy)
        g.impactOccurred()
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.13) {
            g.impactOccurred()
        }
    }
}
