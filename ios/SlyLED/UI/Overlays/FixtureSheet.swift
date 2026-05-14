// FixtureSheet — full-screen "More controls →" panel. Loads the full
// profile JSON, renders one Section per channel with a 0..255 slider and
// the capability labels for the band the slider sits in.

import SwiftUI

struct FixtureSheet: View {
    @EnvironmentObject var control: ControlViewModel
    @Environment(\.dismiss) private var dismiss

    let fixtureId: Int

    @State private var profile: [String: Any]?
    @State private var channelValues: [Int: Int] = [:]
    @State private var loading = true

    private var fixture: Fixture? {
        control.fixtures.first(where: { $0.id == fixtureId })
    }

    var body: some View {
        NavigationStack {
            content
                .navigationTitle(fixture?.name ?? "Channels")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .navigationBarTrailing) {
                        Button("Done") { dismiss() }
                    }
                }
                .task { await load() }
        }
    }

    @ViewBuilder
    private var content: some View {
        if loading {
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if let channels = channelList() {
            Form {
                ForEach(0..<channels.count, id: \.self) { idx in
                    let ch = channels[idx]
                    let offset = (ch["offset"] as? Int) ?? idx
                    Section((ch["name"] as? String) ?? "Channel \(idx + 1)") {
                        channelEditor(channel: ch, offset: offset)
                    }
                }
            }
        } else {
            Text("Couldn't load profile").font(.kpBody).padding()
        }
    }

    private func channelList() -> [[String: Any]]? {
        profile?["channels"] as? [[String: Any]]
    }

    private func channelEditor(channel: [String: Any], offset: Int) -> some View {
        let value = Double(channelValues[offset] ?? 0)
        let binding = Binding<Double>(
            get: { value },
            set: { newVal in
                channelValues[offset] = Int(newVal)
            }
        )
        return VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text((channel["type"] as? String)?.uppercased() ?? "—")
                    .font(.kpLabelSm).foregroundStyle(Color.kpLightSlate)
                Spacer()
                Text("\(Int(value))").font(.kpMono)
            }
            Slider(value: binding, in: 0...255, onEditingChanged: { editing in
                if !editing {
                    control.channelWrite(fixtureId, [offset: Int(value)])
                    Haptics.fire(.softTick)
                }
            })
            if let caps = channel["capabilities"] as? [[String: Any]] {
                let label = activeCapability(caps: caps, value: Int(value)) ?? ""
                if !label.isEmpty {
                    Text(label).font(.kpCaption).foregroundStyle(Color.kpMutedSlate)
                }
            }
        }
    }

    private func activeCapability(caps: [[String: Any]], value: Int) -> String? {
        for cap in caps {
            if let range = cap["range"] as? [Any], range.count >= 2,
               let lo = range[0] as? Int, let hi = range[1] as? Int,
               value >= lo, value <= hi {
                if let lbl = cap["label"] as? String { return lbl }
                if let t = cap["type"] as? String { return t }
            }
        }
        return nil
    }

    private func load() async {
        guard let id = fixture?.dmxProfileId else { loading = false; return }
        profile = await control.fetchProfile(id)
        loading = false
    }
}
