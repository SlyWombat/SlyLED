// ShortcutControls — SwiftUI widgets that render a ResolvedShortcut from
// FixtureShortcuts.swift. One view per UI kind (TOGGLE / SEGMENTED /
// COLOR_SWATCH / MOMENTARY / LONG_PRESS).

import SwiftUI

struct ShortcutControl: View {
    let fixtureId: Int
    let shortcut: ResolvedShortcut
    let onChannelWrite: (Int, [Int: Int]) -> Void

    var body: some View {
        switch shortcut.ui {
        case .toggle:
            ToggleShortcut(fixtureId: fixtureId, shortcut: shortcut, onWrite: onChannelWrite)
        case .segmented:
            SegmentedShortcut(fixtureId: fixtureId, shortcut: shortcut, onWrite: onChannelWrite)
        case .colorSwatch:
            ColorSwatchShortcut(fixtureId: fixtureId, shortcut: shortcut, onWrite: onChannelWrite)
        case .momentary:
            MomentaryShortcut(fixtureId: fixtureId, shortcut: shortcut, onWrite: onChannelWrite)
        case .longPress:
            LongPressShortcut(fixtureId: fixtureId, shortcut: shortcut, onWrite: onChannelWrite)
        }
    }
}

private struct ToggleShortcut: View {
    let fixtureId: Int
    let shortcut: ResolvedShortcut
    let onWrite: (Int, [Int: Int]) -> Void
    @State private var on = false

    var body: some View {
        Button {
            on.toggle()
            Haptics.fire(.lightTick)
            if let off = shortcut.channelOffset {
                onWrite(fixtureId, [off: on ? shortcut.onValue : shortcut.offValue])
            }
        } label: {
            VStack(spacing: 4) {
                Text(shortcut.icon).font(.system(size: 22))
                Text(shortcut.label).font(.kpLabelSm)
            }
            .frame(minWidth: 80, minHeight: 56)
        }
        .buttonStyle(.bordered)
        .tint(on ? Color.kpCyanSecondary : Color.kpMutedSlate)
    }
}

private struct SegmentedShortcut: View {
    let fixtureId: Int
    let shortcut: ResolvedShortcut
    let onWrite: (Int, [Int: Int]) -> Void
    @State private var selected: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(shortcut.icon)
                Text(shortcut.label).font(.kpLabelSm)
            }
            HStack(spacing: 6) {
                ForEach(shortcut.segments) { seg in
                    Button {
                        selected = seg.value
                        Haptics.fire(.lightTick)
                        if let off = shortcut.channelOffset {
                            onWrite(fixtureId, [off: seg.value])
                        }
                    } label: {
                        Text(seg.label)
                            .font(.kpLabelSm)
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.bordered)
                    .tint(selected == seg.value ? Color.kpCyanSecondary : Color.kpMutedSlate)
                }
            }
        }
    }
}

private struct ColorSwatchShortcut: View {
    let fixtureId: Int
    let shortcut: ResolvedShortcut
    let onWrite: (Int, [Int: Int]) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(shortcut.icon)
                Text(shortcut.label).font(.kpLabelSm)
            }
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 5), spacing: 8) {
                ForEach(shortcut.swatches) { swatch in
                    Button {
                        Haptics.fire(.lightTick)
                        var writes: [Int: Int] = [:]
                        if let offs = shortcut.channelOffsets {
                            if let r = offs["red"]   { writes[r] = swatch.r }
                            if let g = offs["green"] { writes[g] = swatch.g }
                            if let b = offs["blue"]  { writes[b] = swatch.b }
                        }
                        onWrite(fixtureId, writes)
                    } label: {
                        Circle()
                            .fill(Color(red: Double(swatch.r)/255,
                                        green: Double(swatch.g)/255,
                                        blue: Double(swatch.b)/255))
                            .frame(height: 32)
                            .overlay(Circle().stroke(Color.kpMutedSlate, lineWidth: 1))
                    }
                }
            }
        }
    }
}

private struct MomentaryShortcut: View {
    let fixtureId: Int
    let shortcut: ResolvedShortcut
    let onWrite: (Int, [Int: Int]) -> Void
    @State private var pressing = false

    var body: some View {
        Button { } label: {
            VStack(spacing: 4) {
                Text(shortcut.icon).font(.system(size: 22))
                Text(shortcut.label).font(.kpLabelSm)
            }
            .frame(minWidth: 80, minHeight: 56)
        }
        .buttonStyle(.bordered)
        .tint(pressing ? Color.kpOrangeWled : Color.kpMutedSlate)
        .simultaneousGesture(
            LongPressGesture(minimumDuration: 0, maximumDistance: .infinity)
                .onChanged { _ in
                    guard !pressing else { return }
                    pressing = true
                    Haptics.fire(.lowRumble)
                    if let off = shortcut.channelOffset,
                       let v = FixtureShortcuts.strobeMomentaryValue(shortcut) {
                        onWrite(fixtureId, [off: v])
                    }
                }
                .onEnded { _ in
                    pressing = false
                    Haptics.stopRumble()
                    if let off = shortcut.channelOffset {
                        onWrite(fixtureId, [off: FixtureShortcuts.strobeOpenValue(shortcut)])
                    }
                }
        )
    }
}

private struct LongPressShortcut: View {
    let fixtureId: Int
    let shortcut: ResolvedShortcut
    let onWrite: (Int, [Int: Int]) -> Void
    @State private var progress: Double = 0
    @State private var timer: Timer?

    var body: some View {
        VStack(spacing: 4) {
            Text(shortcut.icon).font(.system(size: 22))
            Text(shortcut.label).font(.kpLabelSm)
            ProgressView(value: progress).tint(Color.kpRedError).frame(height: 4)
        }
        .frame(minWidth: 80, minHeight: 64)
        .padding(6)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 8))
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in startTimer() }
                .onEnded { _ in cancelTimer() }
        )
    }

    private func startTimer() {
        guard timer == nil else { return }
        let duration = Double(shortcut.confirmHoldMs) / 1000
        let started = Date()
        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { t in
            let e = Date().timeIntervalSince(started)
            progress = min(1, e / duration)
            if progress >= 1 {
                t.invalidate()
                timer = nil
                Haptics.fire(.heavyThud)
                if let off = shortcut.channelOffset {
                    onWrite(fixtureId, [off: shortcut.onValue])
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                        onWrite(fixtureId, [off: shortcut.offValue])
                    }
                }
                progress = 0
            }
        }
    }

    private func cancelTimer() {
        timer?.invalidate()
        timer = nil
        progress = 0
    }
}
