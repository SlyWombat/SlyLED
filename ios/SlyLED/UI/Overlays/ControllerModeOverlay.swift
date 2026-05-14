// ControllerModeOverlay — phone-as-pointer gyro takeover for a moving
// head. CMMotionManager DeviceMotion stream at 50 ms cadence; orient
// POSTs through OrchestratorClient.moverOrient. Hold-to-calibrate gesture
// captures the reference pose at finger-down and locks it at finger-up
// (100 ms lift debounce).

import SwiftUI
import CoreMotion

struct ControllerModeOverlay: View {
    @EnvironmentObject var control: ControlViewModel
    @EnvironmentObject var prefs: ServerPreferences

    let fixtureId: Int
    let fixtureName: String

    @StateObject private var motion = MotionStreamer()
    @State private var dimmer: Double = 255
    @State private var hue: Double = 0
    @State private var saturation: Double = 1
    @State private var holding = false
    @State private var calStartedAt: Date?
    @State private var flashHeld = false

    var body: some View {
        VStack(spacing: 12) {
            header
            colourWheelCard
            dimmerCard
            calibrateBar
            HStack {
                flashButton
                Spacer()
                releaseButton
            }
        }
        .padding(16)
        .background(Color.kpDeepSlate.ignoresSafeArea())
        .onAppear { startStream() }
        .onDisappear { stopStream() }
    }

    private var header: some View {
        HStack {
            Text(fixtureName)
                .font(.kpTitleMid)
                .foregroundStyle(Color.kpNearWhite)
            Spacer()
            if let status = control.moverStatus?.claims?.first(where: { $0.moverId == fixtureId }) {
                Text(status.state ?? "—")
                    .font(.kpLabelSm)
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.kpCyanSecondary.opacity(0.2), in: Capsule())
                    .foregroundStyle(Color.kpCyanSecondary)
            }
        }
    }

    private var colourWheelCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("COLOUR").font(.kpLabel).foregroundStyle(Color.kpLightSlate)
            HSVWheel(hue: $hue, saturation: $saturation) { h, s in
                applyColour(hue: h, saturation: s)
            }
            .frame(height: 220)
        }
        .padding(16)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 12))
    }

    private var dimmerCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("DIMMER").font(.kpLabel).foregroundStyle(Color.kpLightSlate)
            Slider(value: $dimmer, in: 0...255, onEditingChanged: { editing in
                if !editing {
                    applyColour(hue: hue, saturation: saturation)
                    Haptics.fire(.lightTick)
                }
            })
            .tint(Color.kpLuminaBlue)
            Text("\(Int(dimmer))").font(.kpMono).foregroundStyle(Color.kpLightSlate)
        }
        .padding(16)
        .background(Color.kpDarkNavy, in: RoundedRectangle(cornerRadius: 12))
    }

    private var calibrateBar: some View {
        Button {} label: {
            Text(holding ? "CALIBRATING — release to lock" : "Hold to calibrate")
                .font(.kpTitleSm)
                .foregroundStyle(Color.kpNearWhite)
                .frame(maxWidth: .infinity, minHeight: 80)
        }
        .background(holding ? Color.kpOrangeWled : Color.kpDarkSlate,
                    in: RoundedRectangle(cornerRadius: 12))
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in handleCalibrateBegin() }
                .onEnded { _ in handleCalibrateEnd() }
        )
    }

    private var flashButton: some View {
        Button {} label: {
            Label("Flash", systemImage: "bolt.fill")
                .frame(minWidth: 120, minHeight: 56)
        }
        .buttonStyle(.bordered)
        .tint(flashHeld ? Color.kpOrangeWled : Color.kpMutedSlate)
        .simultaneousGesture(
            DragGesture(minimumDistance: 0)
                .onChanged { _ in
                    if !flashHeld {
                        flashHeld = true
                        Haptics.fire(.lightTick)
                        Task { try? await control.client.moverFlash(fixtureId, on: true) }
                    }
                }
                .onEnded { _ in
                    flashHeld = false
                    Task { try? await control.client.moverFlash(fixtureId, on: false) }
                }
        )
    }

    private var releaseButton: some View {
        Button(role: .destructive) {
            Haptics.fire(.heavyThud)
            control.releaseMover()
        } label: {
            Label("Release", systemImage: "xmark.circle.fill")
                .frame(minWidth: 120, minHeight: 56)
        }
        .buttonStyle(.borderedProminent)
        .tint(Color.kpRedError)
    }

    // ── Stream wiring ─────────────────────────────────────────────────

    private func startStream() {
        // Publish stored aim-wizard axes (or sensible iPhone defaults) so
        // the server knows which body-frame axis is "forward".
        let forward: [Double]
        let up: [Double]
        if prefs.aimWizardHasResult() {
            forward = [prefs.aimWizardForwardX, prefs.aimWizardForwardY, prefs.aimWizardForwardZ]
            up      = [prefs.aimWizardUpX, prefs.aimWizardUpY, prefs.aimWizardUpZ]
        } else {
            // iPhone-portrait default: forward = +Y (out the top), up = +Z.
            forward = [0, 1, 0]
            up      = [0, 0, 1]
        }
        Task { try? await control.client.publishRemoteGrip(forward: forward, up: up) }

        motion.start { quat, euler in
            // Already throttled inside the streamer.
            Task { @MainActor in
                if holding { return }
                try? await control.client.moverOrient(
                    fixtureId,
                    roll: euler.roll, pitch: euler.pitch, yaw: euler.yaw,
                    quat: quat
                )
            }
        }
    }

    private func stopStream() {
        motion.stop()
    }

    private func applyColour(hue: Double, saturation: Double) {
        let rgb = hsvToRgb(h: hue, s: saturation, v: 1)
        Task {
            try? await control.client.moverColor(
                fixtureId,
                r: Int(rgb.0 * 255), g: Int(rgb.1 * 255), b: Int(rgb.2 * 255),
                dimmer: Int(dimmer))
        }
    }

    private func handleCalibrateBegin() {
        if holding { return }
        holding = true
        calStartedAt = Date()
        Haptics.fire(.lightTick)
        Task {
            let e = motion.lastEuler
            try? await control.client.moverCalibrateStart(
                fixtureId, roll: e.roll, pitch: e.pitch, yaw: e.yaw)
        }
    }

    private func handleCalibrateEnd() {
        guard holding else { return }
        // 100 ms lift debounce — mirrors Android #755 fix.
        let started = calStartedAt
        Task {
            try? await Task.sleep(nanoseconds: 100_000_000)
            await MainActor.run {
                if calStartedAt == started {
                    holding = false
                    Haptics.fire(.successTick)
                    let e = motion.lastEuler
                    let q = motion.lastQuat
                    Task {
                        try? await control.client.moverCalibrateEnd(
                            fixtureId, roll: e.roll, pitch: e.pitch, yaw: e.yaw, quat: q)
                    }
                }
            }
        }
    }
}

// ── HSV wheel ──────────────────────────────────────────────────────────

private struct HSVWheel: View {
    @Binding var hue: Double         // 0...1
    @Binding var saturation: Double  // 0...1
    let onChange: (Double, Double) -> Void

    var body: some View {
        GeometryReader { geo in
            let size = min(geo.size.width, geo.size.height)
            ZStack {
                AngularGradient(
                    gradient: Gradient(colors: [.red, .yellow, .green, .cyan, .blue, .purple, .red]),
                    center: .center
                )
                .clipShape(Circle())
                .frame(width: size, height: size)
                .overlay(
                    RadialGradient(
                        gradient: Gradient(colors: [.white, .clear]),
                        center: .center, startRadius: 0, endRadius: size / 2)
                        .clipShape(Circle())
                        .frame(width: size, height: size)
                )

                let pos = wheelPosition(in: size)
                Circle()
                    .strokeBorder(Color.white, lineWidth: 2)
                    .background(Circle().fill(currentColor))
                    .frame(width: 18, height: 18)
                    .offset(x: pos.x, y: pos.y)
            }
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { v in
                        update(pt: v.location, size: size)
                        Haptics.fire(.sliderStep)
                    }
                    .onEnded { v in
                        update(pt: v.location, size: size)
                        onChange(hue, saturation)
                    }
            )
        }
    }

    private var currentColor: Color {
        let (r, g, b) = hsvToRgb(h: hue, s: saturation, v: 1)
        return Color(red: r, green: g, blue: b)
    }

    private func wheelPosition(in size: CGFloat) -> CGPoint {
        let radius = size / 2 * saturation
        let theta = hue * 2 * .pi - .pi / 2
        return CGPoint(x: cos(theta) * radius, y: sin(theta) * radius)
    }

    private func update(pt: CGPoint, size: CGFloat) {
        let center = CGPoint(x: size / 2, y: size / 2)
        let dx = pt.x - center.x
        let dy = pt.y - center.y
        let radius = size / 2
        let r = min(sqrt(dx * dx + dy * dy), radius)
        saturation = r / radius
        var theta = atan2(dy, dx) + .pi / 2
        if theta < 0 { theta += 2 * .pi }
        hue = theta / (2 * .pi)
    }
}

// ── Motion streamer ────────────────────────────────────────────────────

@MainActor
private final class MotionStreamer: ObservableObject {
    private let manager = CMMotionManager()
    private var lastSendAt = Date.distantPast
    private(set) var lastEuler: (roll: Double, pitch: Double, yaw: Double) = (0, 0, 0)
    private(set) var lastQuat: [Double] = [1, 0, 0, 0]

    func start(callback: @escaping ([Double], (roll: Double, pitch: Double, yaw: Double)) -> Void) {
        guard manager.isDeviceMotionAvailable else { return }
        manager.deviceMotionUpdateInterval = 0.02   // 50 Hz
        manager.startDeviceMotionUpdates(using: .xArbitraryZVertical, to: .main) { [weak self] motion, _ in
            guard let self, let motion else { return }
            let q = motion.attitude.quaternion
            // CMQuaternion is (x, y, z, w); orchestrator wants [w, x, y, z].
            self.lastQuat = [q.w, q.x, q.y, q.z]
            self.lastEuler = (
                roll: motion.attitude.roll,
                pitch: motion.attitude.pitch,
                yaw: motion.attitude.yaw
            )
            // 50 ms throttle on the callback to match Android.
            let now = Date()
            if now.timeIntervalSince(self.lastSendAt) >= 0.05 {
                self.lastSendAt = now
                callback(self.lastQuat, self.lastEuler)
            }
        }
    }

    func stop() {
        manager.stopDeviceMotionUpdates()
    }
}

// HSV → RGB
private func hsvToRgb(h: Double, s: Double, v: Double) -> (Double, Double, Double) {
    let i = floor(h * 6)
    let f = h * 6 - i
    let p = v * (1 - s)
    let q = v * (1 - f * s)
    let t = v * (1 - (1 - f) * s)
    switch Int(i) % 6 {
    case 0: return (v, t, p)
    case 1: return (q, v, p)
    case 2: return (p, v, t)
    case 3: return (p, q, v)
    case 4: return (t, p, v)
    default: return (v, p, q)
    }
}
