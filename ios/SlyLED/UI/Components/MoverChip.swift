// MoverChip — Grab-page mover tile: 56dp colour swatch + radial pan/tilt
// arrow + name + claim badge. Used in both the favourites LazyHStack and
// the full vertical mover list.

import SwiftUI

struct MoverChip: View {
    let fixture: Fixture
    let live: FixtureLive?
    let isFavourite: Bool
    let heldByOther: Bool
    let heldByName: String?
    let onTap: () -> Void
    let onToggleFavourite: () -> Void
    let onFlash: () -> Void
    let onHome: () -> Void
    let onBlackout: () -> Void

    private var rgbColor: Color {
        let r = Double(live?.r ?? 0) / 255
        let g = Double(live?.g ?? 0) / 255
        let b = Double(live?.b ?? 0) / 255
        if r == 0 && g == 0 && b == 0 { return Color.kpDimSlate }
        return Color(red: r, green: g, blue: b)
    }

    var body: some View {
        VStack(spacing: 6) {
            ZStack {
                Circle()
                    .fill(rgbColor)
                    .frame(width: 56, height: 56)
                    .overlay(Circle().stroke(strokeColor, lineWidth: heldByOther ? 2 : 1))
                if let live, let pan = live.panDeg, let tilt = live.tiltDeg {
                    AimArrow(panDeg: pan, tiltDeg: tilt)
                        .stroke(Color.kpNearWhite.opacity(0.85), lineWidth: 2)
                        .frame(width: 36, height: 36)
                }
                if heldByOther {
                    Image(systemName: "lock.fill")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(Color.kpDmxPurple)
                        .offset(x: 18, y: -18)
                }
            }
            Text(fixture.name)
                .font(.kpLabelSm)
                .foregroundStyle(Color.kpNearWhite)
                .lineLimit(1)
            if heldByOther, let holder = heldByName {
                Text("Held by \(holder)")
                    .font(.kpLabelSm)
                    .foregroundStyle(Color.kpDmxPurple)
                    .lineLimit(1)
            }
        }
        .frame(width: 88)
        .padding(.vertical, 8)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 10))
        .kpBloom(rgbColor, active: !heldByOther && (live?.r ?? 0) + (live?.g ?? 0) + (live?.b ?? 0) > 30)
        .onTapGesture(perform: onTap)
        .contextMenu {
            Button(isFavourite ? "Unfavourite" : "Favourite",
                   systemImage: isFavourite ? "star.slash" : "star",
                   action: onToggleFavourite)
            Button("Flash", systemImage: "bolt.fill", action: onFlash)
            Button("Send home", systemImage: "house.fill", action: onHome)
            Button("Blackout fixture", systemImage: "moon.fill", role: .destructive, action: onBlackout)
        }
    }

    private var strokeColor: Color {
        heldByOther ? Color.kpDmxPurple.opacity(0.6) : Color.kpMutedSlate
    }
}

/// Project the pan / tilt direction onto the chip as a small arrow.
/// `panDeg > 0` aims to stage-left (+X); `tiltDeg < 0` is below horizon (toward floor).
private struct AimArrow: Shape {
    let panDeg: Double
    let tiltDeg: Double

    func path(in rect: CGRect) -> Path {
        var p = Path()
        let cx = rect.midX
        let cy = rect.midY
        let r = min(rect.width, rect.height) / 2 - 2
        let theta = panDeg * .pi / 180     // pan rotates around vertical axis
        // Radial offset shrinks as tilt approaches the horizon
        let radial = cos(tiltDeg * .pi / 180) * Double(r)
        let dx = sin(theta) * radial
        let dy = -cos(theta) * radial      // up on screen = -y
        let head = CGPoint(x: cx + CGFloat(dx), y: cy + CGFloat(dy))
        p.move(to: CGPoint(x: cx, y: cy))
        p.addLine(to: head)
        // Small arrowhead
        let ang = atan2(head.y - cy, head.x - cx)
        let ah1 = CGPoint(x: head.x - cos(ang - .pi / 6) * 6,
                          y: head.y - sin(ang - .pi / 6) * 6)
        let ah2 = CGPoint(x: head.x - cos(ang + .pi / 6) * 6,
                          y: head.y - sin(ang + .pi / 6) * 6)
        p.addLine(to: ah1)
        p.move(to: head)
        p.addLine(to: ah2)
        return p
    }
}
