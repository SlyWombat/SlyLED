// ShowsPage — Starred / Recent (≤7 days) / All. Tap row to start; long-
// press for context actions (Star, Loop, Add to playlist). Bottom card:
// playlist controls.

import SwiftUI

struct ShowsPage: View {
    @EnvironmentObject var control: ControlViewModel
    @EnvironmentObject var prefs: ServerPreferences

    private var allTimelines: [Timeline] { control.timelines }

    private var starredTimelines: [Timeline] {
        allTimelines.filter { prefs.starredTimelines.contains($0.id) }
                    .sorted { $0.name < $1.name }
    }
    private var recentTimelines: [Timeline] {
        let cutoff = Int64(Date().timeIntervalSince1970 * 1000) - 7 * 24 * 60 * 60 * 1000
        return allTimelines
            .filter {
                !prefs.starredTimelines.contains($0.id)
                && (prefs.lastPlayedAt[$0.id] ?? 0) >= cutoff
            }
            .sorted { (prefs.lastPlayedAt[$0.id] ?? 0) > (prefs.lastPlayedAt[$1.id] ?? 0) }
    }
    private var otherTimelines: [Timeline] {
        let used = Set(starredTimelines.map(\.id) + recentTimelines.map(\.id))
        return allTimelines.filter { !used.contains($0.id) }.sorted { $0.name < $1.name }
    }

    var body: some View {
        List {
            if !starredTimelines.isEmpty {
                Section("Starred") {
                    ForEach(starredTimelines) { tl in row(for: tl) }
                }
            }
            if !recentTimelines.isEmpty {
                Section("Recent") {
                    ForEach(recentTimelines) { tl in row(for: tl) }
                }
            }
            if !otherTimelines.isEmpty {
                Section("All shows") {
                    ForEach(otherTimelines) { tl in row(for: tl) }
                }
            }
            Section("Playlist") {
                if let pl = control.playlist, let items = pl.items, !items.isEmpty {
                    ForEach(items) { item in
                        HStack {
                            Text(item.name ?? "—").font(.kpBodySm)
                            Spacer()
                            if item.baked == true {
                                Image(systemName: "checkmark.seal.fill")
                                    .foregroundStyle(Color.kpGreenOnline)
                            }
                        }
                    }
                    Button {
                        control.startPlaylist()
                    } label: {
                        Label("Start playlist", systemImage: "play.fill")
                    }
                } else {
                    Text("No playlist configured").font(.kpCaption).foregroundStyle(Color.kpMutedSlate)
                }
            }
        }
        .listStyle(.insetGrouped)
        .scrollContentBackground(.hidden)
    }

    private func row(for tl: Timeline) -> some View {
        Button {
            control.startTimeline(tl.id)
        } label: {
            HStack {
                Image(systemName: prefs.starredTimelines.contains(tl.id) ? "star.fill" : "play.circle.fill")
                    .foregroundStyle(prefs.starredTimelines.contains(tl.id) ? Color.kpOrangeWled : Color.kpCyanSecondary)
                VStack(alignment: .leading, spacing: 2) {
                    Text(tl.name).font(.kpBody).foregroundStyle(Color.kpNearWhite)
                    if let d = tl.durationS {
                        Text("\(d)s").font(.kpCaption).foregroundStyle(Color.kpMutedSlate)
                    }
                }
                Spacer()
            }
        }
        .contextMenu {
            Button(prefs.starredTimelines.contains(tl.id) ? "Unstar" : "Star",
                   systemImage: prefs.starredTimelines.contains(tl.id) ? "star.slash" : "star") {
                prefs.toggleStarredTimeline(tl.id)
            }
        }
    }
}
