// FixturesPage — non-mover DMX fixtures with profile-driven shortcuts.
// Page-level safety button: "Stop all effects" (kill-strobes + kill-effects).

import SwiftUI

struct FixturesPage: View {
    @EnvironmentObject var control: ControlViewModel

    private var fixtures: [Fixture] { control.nonMoverDmxFixtures }

    @State private var resolvedByFixture: [Int: [ResolvedShortcut]] = [:]

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if fixtures.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(spacing: 12) {
                        ForEach(fixtures) { fix in
                            card(for: fix)
                                .task { await loadShortcuts(fix) }
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                }
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Direct fixture control")
                .font(.kpTitleSm)
                .foregroundStyle(Color.kpNearWhite)
            Spacer()
            Button(role: .destructive) {
                control.stopAllEffects()
            } label: {
                Label("Stop all effects", systemImage: "bolt.slash.fill")
                    .font(.kpLabelSm)
            }
            .buttonStyle(.bordered)
            .tint(Color.kpRedError)
        }
        .padding(.horizontal, 16)
        .padding(.top, 4)
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Image(systemName: "lightbulb.slash")
                .font(.largeTitle)
                .foregroundStyle(Color.kpMutedSlate)
            Text("No DMX fixtures in the layout")
                .font(.kpBody)
                .foregroundStyle(Color.kpLightSlate)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
    }

    private func card(for fix: Fixture) -> some View {
        let shortcuts = resolvedByFixture[fix.id] ?? []
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(fix.name)
                    .font(.kpTitleMid)
                    .foregroundStyle(Color.kpNearWhite)
                Spacer()
                Button("More controls →") {
                    control.fixtureSheetId = fix.id
                }
                .font(.kpLabelSm)
                .tint(Color.kpCyanSecondary)
            }
            if shortcuts.isEmpty {
                Text("No shortcuts in this profile")
                    .font(.kpCaption)
                    .foregroundStyle(Color.kpMutedSlate)
            } else {
                ForEach(shortcuts) { sc in
                    ShortcutControl(fixtureId: fix.id, shortcut: sc) { fid, writes in
                        control.channelWrite(fid, writes)
                    }
                }
            }
        }
        .padding(16)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 12))
    }

    private func loadShortcuts(_ fix: Fixture) async {
        if resolvedByFixture[fix.id] != nil { return }
        let list = await control.resolveShortcuts(for: fix)
        resolvedByFixture[fix.id] = list
    }
}
