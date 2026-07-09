// LiveStageScreen — stage-space canvas of fixture positions + live colour
// + pan/tilt arrows. SwiftUI Canvas with pinch-zoom + pan gestures.

import SwiftUI

struct LiveStageScreen: View {
    @StateObject private var vm: LiveStageViewModel
    @State private var scale: CGFloat = 1
    @State private var lastScale: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var lastOffset: CGSize = .zero

    init(client: OrchestratorClient) {
        _vm = StateObject(wrappedValue: LiveStageViewModel(client: client))
    }

    private var bounds: (Int, Int) {
        // Fall back to a sensible default if the stage hasn't loaded yet.
        // Real stage bounds come from /api/stage; for now derive from fixture spread.
        let xs = vm.fixtures.compactMap(\.x)
        let ys = vm.fixtures.compactMap(\.y)
        let maxX = max(10000, (xs.max() ?? 10000) + 1000)
        let maxY = max(5000, (ys.max() ?? 5000) + 1000)
        return (maxX, maxY)
    }

    var body: some View {
        GeometryReader { geo in
            let (sw, sh) = bounds
            let stageW = Double(sw)
            let stageH = Double(sh)
            let pxPerMm = min(geo.size.width / stageW, geo.size.height / stageH)

            ZStack {
                Color.kpDeepSlate.ignoresSafeArea()
                Canvas { ctx, _ in
                    // Stage outline
                    let stageRect = CGRect(x: offset.width,
                                           y: offset.height,
                                           width: stageW * pxPerMm * Double(scale),
                                           height: stageH * pxPerMm * Double(scale))
                    ctx.stroke(Path(roundedRect: stageRect, cornerRadius: 6),
                               with: .color(Color.kpDimSlate), lineWidth: 1)

                    for fix in vm.fixtures {
                        guard let fx = fix.x, let fy = fix.y else { continue }
                        let px = offset.width + Double(fx) * pxPerMm * Double(scale)
                        let py = offset.height + Double(fy) * pxPerMm * Double(scale)
                        let live = vm.live[String(fix.id)]
                        let r = Double(live?.r ?? 0) / 255
                        let g = Double(live?.g ?? 0) / 255
                        let b = Double(live?.b ?? 0) / 255
                        let dot = CGRect(x: px - 6, y: py - 6, width: 12, height: 12)
                        let dotColor: Color = (r + g + b > 0.05)
                            ? Color(red: r, green: g, blue: b)
                            : Color.kpMutedSlate
                        ctx.fill(Path(ellipseIn: dot), with: .color(dotColor))

                        if let pan = live?.panDeg, let tilt = live?.tiltDeg {
                            let theta = pan * .pi / 180
                            let radial = cos(tilt * .pi / 180) * 18
                            let hx = px + sin(theta) * radial
                            let hy = py - cos(theta) * radial
                            var arrow = Path()
                            arrow.move(to: CGPoint(x: px, y: py))
                            arrow.addLine(to: CGPoint(x: hx, y: hy))
                            ctx.stroke(arrow, with: .color(Color.kpNearWhite.opacity(0.7)), lineWidth: 1.5)
                        }
                    }

                    // Tracked objects (temporal persons — camera + radar,
                    // #912). Top-down footprint at transform.pos (object
                    // centre, mm) sized from scale [width(X), height(Z),
                    // depth(Y)]. Amber = radar-sourced (SPA parity:
                    // emulation.js tints the person body 0xfbbf24 when
                    // source.type === 'radar'); pink otherwise — matching
                    // Android's LiveStage canvas.
                    for obj in vm.objects where obj.isTracked {
                        guard let pos = obj.transform?.pos, pos.count >= 2 else { continue }
                        let px = offset.width + pos[0] * pxPerMm * Double(scale)
                        let py = offset.height + pos[1] * pxPerMm * Double(scale)
                        let scl = obj.transform?.scale ?? []
                        let wMm = scl.count > 0 ? scl[0] : 500
                        let dMm = scl.count > 2 ? scl[2] : 500
                        let w = max(10, wMm * pxPerMm * Double(scale))
                        let d = max(10, dMm * pxPerMm * Double(scale))
                        let col: Color = obj.isRadarSourced
                            ? Color(red: 0xFB/255, green: 0xBF/255, blue: 0x24/255)
                            : Color(red: 0xF4/255, green: 0x72/255, blue: 0xB6/255)
                        let footprint = CGRect(x: px - w / 2, y: py - d / 2, width: w, height: d)
                        let capsule = Path(roundedRect: footprint,
                                           cornerRadius: min(w, d) / 2)
                        ctx.fill(capsule, with: .color(col.opacity(0.3)))
                        ctx.stroke(capsule, with: .color(col.opacity(0.8)), lineWidth: 1.5)
                        if let name = obj.name, !name.isEmpty {
                            ctx.draw(Text(name).font(.kpLabelSm).foregroundStyle(col),
                                     at: CGPoint(x: px, y: py - d / 2 - 8))
                        }
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .contentShape(Rectangle())
                .gesture(
                    SimultaneousGesture(
                        MagnificationGesture()
                            .onChanged { v in scale = max(0.3, min(4, lastScale * v)) }
                            .onEnded { _ in lastScale = scale },
                        DragGesture()
                            .onChanged { v in
                                offset = CGSize(width: lastOffset.width + v.translation.width,
                                                height: lastOffset.height + v.translation.height)
                            }
                            .onEnded { _ in lastOffset = offset }
                    )
                )
                VStack {
                    Spacer()
                    HStack {
                        Text("\(vm.fixtures.count) fixtures")
                            .font(.kpLabel)
                            .padding(8)
                            .background(Color.kpDarkNavy.opacity(0.6), in: Capsule())
                            .foregroundStyle(Color.kpNearWhite)
                        Spacer()
                        Button {
                            scale = 1; lastScale = 1
                            offset = .zero; lastOffset = .zero
                            Haptics.fire(.softTick)
                        } label: {
                            Image(systemName: "arrow.up.left.and.arrow.down.right.circle.fill")
                                .font(.title2)
                                .foregroundStyle(Color.kpCyanSecondary)
                        }
                    }
                    .padding(16)
                }
            }
        }
        .onAppear { vm.start() }
        .onDisappear { vm.stop() }
    }
}
