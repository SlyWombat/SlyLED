// ControlScreen — assembles the Control tab: persistent NowPlayingAnchor
// above a 4-page pager (Master / Grab / Fixtures / Shows). Routes the
// overlay sheets (ControllerMode, FixtureSheet, TakeoverSheet) off the
// ControlViewModel's state.

import SwiftUI

enum ControlPage: Int, CaseIterable, Identifiable {
    case master, grab, fixtures, shows
    var id: Int { rawValue }
    var label: String {
        switch self {
        case .master:   return "Master"
        case .grab:     return "Grab"
        case .fixtures: return "Fixtures"
        case .shows:    return "Shows"
        }
    }
    var icon: String {
        switch self {
        case .master:   return "sun.max.fill"
        case .grab:     return "hand.tap.fill"
        case .fixtures: return "lightbulb.fill"
        case .shows:    return "play.rectangle.fill"
        }
    }
}

struct ControlScreen: View {
    @EnvironmentObject var control: ControlViewModel
    @State private var page: ControlPage = .master
    @State private var showController = false
    @State private var fixtureSheetId: Int?

    var body: some View {
        VStack(spacing: 0) {
            NowPlayingAnchor(
                timelineStatus: control.timelineStatus,
                showStatus: control.showStatus,
                timelines: control.timelines,
                onStop: control.stopEverything,
                onNext: control.nextInPlaylist,
                onTapIdle: { page = .shows }
            )
            .padding(.horizontal, 12)
            .padding(.top, 6)
            segmentBar
            TabView(selection: $page) {
                MasterPage().tag(ControlPage.master)
                GrabPage().tag(ControlPage.grab)
                FixturesPage().tag(ControlPage.fixtures)
                ShowsPage().tag(ControlPage.shows)
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
        }
        .background(Color.kpDeepSlate.ignoresSafeArea())
        .onChange(of: control.controllerFixtureId) { _, newVal in
            showController = (newVal != nil)
        }
        .onChange(of: control.fixtureSheetId) { _, newVal in
            fixtureSheetId = newVal
        }
        .fullScreenCover(isPresented: $showController) {
            controllerOverlay
        }
        .sheet(isPresented: Binding(get: { fixtureSheetId != nil },
                                    set: { if !$0 { control.fixtureSheetId = nil; fixtureSheetId = nil } })) {
            if let fid = fixtureSheetId {
                FixtureSheet(fixtureId: fid)
            }
        }
        .sheet(isPresented: Binding(get: { control.pendingTakeover != nil },
                                    set: { if !$0 { control.cancelTakeover() } })) {
            if let pending = control.pendingTakeover {
                TakeoverSheet(pending: pending,
                              onConfirm: control.confirmTakeover,
                              onCancel: control.cancelTakeover)
            }
        }
    }

    private var segmentBar: some View {
        HStack(spacing: 6) {
            ForEach(ControlPage.allCases) { p in
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) { page = p }
                    Haptics.fire(.softTick)
                } label: {
                    VStack(spacing: 2) {
                        Image(systemName: p.icon).font(.title3)
                        Text(p.label).font(.kpLabelSm)
                    }
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .padding(.vertical, 4)
                }
                .background(
                    page == p
                    ? Color.kpCyanSecondary.opacity(0.18)
                    : Color.clear,
                    in: RoundedRectangle(cornerRadius: 8))
                .foregroundStyle(page == p ? Color.kpCyanSecondary : Color.kpLightSlate)
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .padding(.bottom, 4)
    }

    @ViewBuilder
    private var controllerOverlay: some View {
        if let fid = control.controllerFixtureId,
           let fix = control.fixtures.first(where: { $0.id == fid }) {
            ControllerModeOverlay(fixtureId: fid, fixtureName: fix.name)
        } else {
            // Race: controllerFixtureId set then cleared — dismiss.
            Color.kpDeepSlate
                .ignoresSafeArea()
                .onAppear { showController = false }
        }
    }
}
