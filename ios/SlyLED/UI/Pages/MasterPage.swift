// MasterPage — global brightness slider + Auto Brightness toggle + live
// envelope meter. Per mobile_ui_redesign.md §5.1.

import SwiftUI

struct MasterPage: View {
    @EnvironmentObject var control: ControlViewModel
    @EnvironmentObject var autoBrightness: MicAutoBrightness
    @EnvironmentObject var prefs: ServerPreferences
    @EnvironmentObject var client: OrchestratorClient

    @State private var brightness: Double = 255
    @State private var dragging = false

    private let udp = UdpClient.shared

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                brightnessCard
                autoBrightnessCard
            }
            .padding(16)
        }
        .onAppear {
            if let v = control.settings.globalBrightness {
                brightness = Double(v)
            }
        }
        .onChange(of: control.settings.globalBrightness ?? -1) { _, newValue in
            // Sync slider with server while operator isn't dragging.
            if !dragging, newValue >= 0 {
                brightness = Double(newValue)
            }
        }
    }

    private var brightnessCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("BRIGHTNESS")
                .font(.kpLabel)
                .foregroundStyle(Color.kpLightSlate)
            HStack {
                Text("\(Int(brightness))")
                    .font(.kpMonoBig)
                    .foregroundStyle(Color.kpNearWhite)
                Spacer()
                Text("\(Int(brightness / 255 * 100))%")
                    .font(.kpTitleMid)
                    .foregroundStyle(Color.kpLightSlate)
            }
            Slider(value: $brightness, in: 0...255, onEditingChanged: { editing in
                dragging = editing
                if !editing {
                    control.setBrightness(Int(brightness))
                    Haptics.fire(.lightTick)
                }
            })
            .tint(Color.kpLuminaBlue)
            HStack(spacing: 8) {
                Button {
                    brightness = max(0, brightness - 13)
                    control.setBrightness(Int(brightness))
                    Haptics.fire(.softTick)
                } label: {
                    Label("−5%", systemImage: "minus")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }.buttonStyle(.bordered)
                Button {
                    brightness = min(255, brightness + 13)
                    control.setBrightness(Int(brightness))
                    Haptics.fire(.softTick)
                } label: {
                    Label("+5%", systemImage: "plus")
                        .frame(maxWidth: .infinity, minHeight: 44)
                }.buttonStyle(.bordered)
            }
        }
        .padding(20)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 12))
    }

    private var autoBrightnessCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("AUTO BRIGHTNESS")
                    .font(.kpLabel)
                    .foregroundStyle(Color.kpLightSlate)
                Spacer()
                Toggle("", isOn: Binding(
                    get: { prefs.autoBrightnessEnabled },
                    set: { newVal in
                        prefs.autoBrightnessEnabled = newVal
                        Haptics.fire(.lightTick)
                        Task {
                            if newVal {
                                udp.configure(host: client.host)
                                await autoBrightness.start()
                            } else {
                                autoBrightness.stop()
                            }
                        }
                    }))
                .labelsHidden()
                .tint(Color.kpCyanSecondary)
            }

            HStack(spacing: 12) {
                statusPill
                Spacer()
                if autoBrightness.bpm > 0 {
                    Text("\(Int(autoBrightness.bpm)) BPM")
                        .font(.kpMono)
                        .foregroundStyle(Color.kpCyanSecondary)
                }
            }

            VStack(alignment: .leading, spacing: 4) {
                Text("Mic input")
                    .font(.kpLabelSm)
                    .foregroundStyle(Color.kpLightSlate)
                meterBar(value: autoBrightness.rawPeak, tint: Color.kpLightSlate)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Envelope")
                    .font(.kpLabelSm)
                    .foregroundStyle(Color.kpLightSlate)
                meterBar(value: autoBrightness.envelope, tint: Color.kpLuminaBlue)
            }
            VStack(alignment: .leading, spacing: 4) {
                Text("Output → master")
                    .font(.kpLabelSm)
                    .foregroundStyle(Color.kpLightSlate)
                meterBar(value: autoBrightness.master, tint: Color.kpCyanSecondary)
            }

            Text("Source: Microphone")
                .font(.kpCaption)
                .foregroundStyle(Color.kpMutedSlate)
        }
        .padding(20)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var statusPill: some View {
        let (label, color): (String, Color) = {
            switch autoBrightness.mode {
            case .idle:        return ("Idle",       Color.kpMutedSlate)
            case .listening:   return ("Listening",  Color.kpGreenOnline)
            case .denied:      return ("Mic denied", Color.kpRedError)
            case .noMic:       return ("Mic error",  Color.kpRedError)
            }
        }()
        HStack(spacing: 6) {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(label).font(.kpLabelSm).foregroundStyle(color)
        }
    }

    private func meterBar(value: Float, tint: Color) -> some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                RoundedRectangle(cornerRadius: 4).fill(Color.kpDarkSlate)
                RoundedRectangle(cornerRadius: 4).fill(tint)
                    .frame(width: max(2, CGFloat(value) * geo.size.width))
            }
        }
        .frame(height: 8)
    }
}
