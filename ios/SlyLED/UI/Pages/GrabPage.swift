// GrabPage — moving-head grab. Top: favourite chips. Below: full list of
// movers sorted by last grabbed. Top-right safety button "Send all home".

import SwiftUI

struct GrabPage: View {
    @EnvironmentObject var control: ControlViewModel
    @EnvironmentObject var prefs: ServerPreferences

    private var movers: [Fixture] { control.movers }
    private var favouriteMovers: [Fixture] {
        movers.filter { prefs.favouriteMovers.contains($0.id) }
    }
    private var otherMovers: [Fixture] {
        movers.filter { !prefs.favouriteMovers.contains($0.id) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            header
            if movers.isEmpty {
                emptyState
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        if !favouriteMovers.isEmpty {
                            Text("FAVOURITES")
                                .font(.kpLabel)
                                .foregroundStyle(Color.kpLightSlate)
                                .padding(.horizontal, 16)
                            ScrollView(.horizontal, showsIndicators: false) {
                                LazyHStack(spacing: 10) {
                                    ForEach(favouriteMovers) { fix in
                                        chip(for: fix)
                                    }
                                }
                                .padding(.horizontal, 16)
                            }
                        }
                        Text("ALL MOVERS")
                            .font(.kpLabel)
                            .foregroundStyle(Color.kpLightSlate)
                            .padding(.horizontal, 16)
                        LazyVStack(spacing: 8) {
                            ForEach(otherMovers) { fix in
                                row(for: fix)
                            }
                        }
                        .padding(.horizontal, 16)
                    }
                    .padding(.vertical, 8)
                }
            }
        }
    }

    private var header: some View {
        HStack {
            Text("Grab a moving head")
                .font(.kpTitleSm)
                .foregroundStyle(Color.kpNearWhite)
            Spacer()
            Button(role: .destructive) {
                control.sendAllMoversHome()
            } label: {
                Label("Send all home", systemImage: "house.fill")
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
            Text("No moving heads in the layout")
                .font(.kpBody)
                .foregroundStyle(Color.kpLightSlate)
            Text("Add one in the desktop app")
                .font(.kpCaption)
                .foregroundStyle(Color.kpMutedSlate)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(40)
    }

    private func chip(for fix: Fixture) -> some View {
        let live = control.fixturesLive[String(fix.id)]
        let claimedByOther = isHeldByOther(fix)
        return MoverChip(
            fixture: fix,
            live: live,
            isFavourite: prefs.favouriteMovers.contains(fix.id),
            heldByOther: claimedByOther,
            heldByName: holderName(for: fix),
            onTap: { control.claimMover(fix) },
            onToggleFavourite: { prefs.toggleFavouriteMover(fix.id) },
            onFlash: { control.flashFixture(fix.id) },
            onHome: { control.homeFixture(fix.id) },
            onBlackout: { control.blackoutFixture(fix.id) }
        )
    }

    private func row(for fix: Fixture) -> some View {
        HStack(spacing: 12) {
            chip(for: fix)
            VStack(alignment: .leading, spacing: 4) {
                Text(fix.name).font(.kpTitleSm).foregroundStyle(Color.kpNearWhite)
                Text(fix.dmxProfileId ?? "no profile")
                    .font(.kpCaption)
                    .foregroundStyle(Color.kpMutedSlate)
            }
            Spacer()
        }
        .padding(.vertical, 4)
    }

    private func isHeldByOther(_ fix: Fixture) -> Bool {
        guard let claims = control.moverStatus?.claims else { return false }
        guard let claim = claims.first(where: { $0.moverId == fix.id }) else { return false }
        return claim.deviceId != nil && claim.deviceId != control.client.deviceId
    }

    private func holderName(for fix: Fixture) -> String? {
        guard let claims = control.moverStatus?.claims else { return nil }
        return claims.first(where: { $0.moverId == fix.id })?.deviceName
    }
}
