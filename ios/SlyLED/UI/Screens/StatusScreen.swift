// StatusScreen — list of children (LED performers, DMX bridges, camera
// nodes) with health pill, RSSI bars, uptime. Pull-to-refresh runs
// /api/children/refresh-all.

import SwiftUI

struct StatusScreen: View {
    @StateObject private var vm: StatusViewModel

    init(client: OrchestratorClient) {
        _vm = StateObject(wrappedValue: StatusViewModel(client: client))
    }

    var body: some View {
        List {
            if vm.children.isEmpty {
                Section {
                    Text(vm.loading ? "Loading..." : "No children registered yet")
                        .font(.kpBodySm)
                        .foregroundStyle(Color.kpLightSlate)
                }
            }
            ForEach(vm.children) { c in
                row(c)
            }
        }
        .listStyle(.insetGrouped)
        .refreshable { await vm.refresh() }
        .onAppear { vm.start() }
        .onDisappear { vm.stop() }
        .navigationTitle("Performers")
    }

    private func row(_ c: Child) -> some View {
        HStack(spacing: 12) {
            Circle()
                .fill(c.online ? Color.kpGreenOnline : Color.kpRedError)
                .frame(width: 10, height: 10)
            VStack(alignment: .leading, spacing: 2) {
                Text(c.name ?? c.hostname ?? "child-\(c.id)")
                    .font(.kpTitleSm)
                    .foregroundStyle(Color.kpNearWhite)
                HStack(spacing: 6) {
                    if let ip = c.ip { Text(ip).font(.kpMono).foregroundStyle(Color.kpLightSlate) }
                    if let bt = c.boardType, !bt.isEmpty {
                        Text(bt).font(.kpCaption).foregroundStyle(Color.kpMutedSlate)
                    }
                    if let fw = c.fwVersion {
                        Text("v\(fw)").font(.kpCaption).foregroundStyle(Color.kpMutedSlate)
                    }
                }
            }
            Spacer()
            // #910 — radar nodes ("MMW-" hostname prefix) get the amber
            // RADAR chip, parallel to Android's BoardBadge radar case.
            if c.isRadarNode {
                Text("RADAR")
                    .font(.kpLabelSm)
                    .foregroundStyle(Color.kpOrangeWled)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Color.kpOrangeWled.opacity(0.15), in: Capsule())
            }
            VStack(alignment: .trailing, spacing: 2) {
                if let dbm = c.rssiDbm {
                    Text("\(dbm) dBm").font(.kpCaption).foregroundStyle(Color.kpLightSlate)
                }
                rssiBars(c.signalBars)
            }
        }
        .padding(.vertical, 4)
    }

    private func rssiBars(_ bars: Int) -> some View {
        HStack(spacing: 2) {
            ForEach(0..<4, id: \.self) { i in
                Rectangle()
                    .fill(i < bars ? Color.kpCyanSecondary : Color.kpDimSlate)
                    .frame(width: 4, height: CGFloat(6 + i * 3))
            }
        }
    }
}
