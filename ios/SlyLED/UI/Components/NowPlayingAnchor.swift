// NowPlayingAnchor — persistent anchor above the Control pager.
//   Idle (40dp): "No show running"; tap → jump to Shows page.
//   Playing (~96dp): name + loop chip + MM:SS + progress bar + STOP +
//     Next (when in a multi-timeline playlist).

import SwiftUI

struct NowPlayingAnchor: View {
    let timelineStatus: TimelineStatus?
    let showStatus: ShowStatus?
    let timelines: [Timeline]
    let onStop: () -> Void
    let onNext: () -> Void
    let onTapIdle: () -> Void

    @State private var pulse = false

    private var running: Bool { (timelineStatus?.running ?? false) || (showStatus?.running ?? false) }

    private var title: String {
        if let id = timelineStatus?.id, let tl = timelines.first(where: { $0.id == id }) {
            return tl.name
        }
        return timelineStatus?.name ?? "Playing"
    }

    private var loop: Bool { timelineStatus?.loop ?? false }
    private var elapsedSec: Int { timelineStatus?.elapsed ?? 0 }
    private var totalSec: Int {
        if let d = timelineStatus?.durationS, d > 0 { return d }
        return 60
    }
    private var hasNext: Bool { (showStatus?.totalTimelines ?? 0) > 1 }

    var body: some View {
        if running {
            playingBody
        } else {
            idleBody
        }
    }

    private var idleBody: some View {
        HStack {
            Text("No show running")
                .font(.kpLabel)
                .foregroundStyle(Color.kpLightSlate)
            Spacer()
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 16)
        .frame(height: 40)
        .background(Color.kpDarkSlate)
        .onTapGesture {
            Haptics.fire(.softTick)
            onTapIdle()
        }
    }

    private var playingBody: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text(title)
                    .font(.kpTitleMid)
                    .foregroundStyle(Color.kpNearWhite)
                    .lineLimit(1)
                if loop {
                    Text("LOOP")
                        .font(.kpLabelSm)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Color.kpCyanSecondary.opacity(0.2),
                                    in: Capsule())
                        .foregroundStyle(Color.kpCyanSecondary)
                }
                Spacer()
                Text(formatTime(elapsedSec) + " / " + formatTime(totalSec))
                    .font(.kpMono)
                    .foregroundStyle(Color.kpLightSlate)
            }
            ProgressView(value: Double(elapsedSec),
                         total: Double(max(1, totalSec)))
                .tint(Color.kpCyanSecondary)
            HStack(spacing: 12) {
                Button(role: .destructive, action: onStop) {
                    Label("STOP", systemImage: "stop.fill")
                        .font(.kpTitleSm)
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(Color.kpRedError)

                if hasNext {
                    Button(action: onNext) {
                        Label("Next", systemImage: "forward.fill")
                            .font(.kpTitleSm)
                            .frame(minWidth: 80, minHeight: 48)
                    }
                    .buttonStyle(.bordered)
                    .tint(Color.kpCyanSecondary)
                }
            }
        }
        .padding(12)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 12))
        .overlay(
            RoundedRectangle(cornerRadius: 12)
                .stroke(Color.kpCyanSecondary.opacity(pulse ? 0.6 : 0.25), lineWidth: 1)
        )
        .onAppear { withAnimation(.easeInOut(duration: 1).repeatForever()) { pulse.toggle() } }
    }

    private func formatTime(_ s: Int) -> String {
        String(format: "%02d:%02d", s / 60, s % 60)
    }
}
