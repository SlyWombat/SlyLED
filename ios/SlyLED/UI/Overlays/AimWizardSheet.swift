// AimWizardSheet — three-step quaternion capture for the gyro aim-axis
// calibration wizard. Mirrors Android AimWizardDialog (#826). Submits to
// POST /api/remotes/aim-wizard; persists derived axes in ServerPreferences
// on success.

import SwiftUI
import CoreMotion

struct AimWizardSheet: View {
    @EnvironmentObject var client: OrchestratorClient
    @EnvironmentObject var prefs: ServerPreferences
    @Environment(\.dismiss) private var dismiss

    @State private var step: Int = 0
    @State private var poses: [AimWizardPose] = []
    @State private var submitting = false
    @State private var result: AimWizardResult?
    @State private var errorMessage: String?
    @StateObject private var motion = AimWizardMotion()

    private let steps: [(role: String, title: String, instruction: String)] = [
        ("neutral",       "Step 1 — Neutral",        "Hold the phone in your normal grip pointing at the head. Then tap CAPTURE."),
        ("pitch_forward", "Step 2 — Pitch forward",  "Tip the phone forward (head-down gesture). Then tap CAPTURE."),
        ("yaw_left",      "Step 3 — Yaw to stage left", "Yaw the phone to stage left (audience right). Then tap CAPTURE."),
    ]

    var body: some View {
        NavigationStack {
            VStack(spacing: 24) {
                if let result, result.ok == true {
                    success(result)
                } else if let err = errorMessage {
                    failure(err)
                } else {
                    instructions
                }
            }
            .padding(24)
            .navigationTitle("Aim Wizard")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button("Cancel") { dismiss() }
                }
            }
            .onAppear { motion.start() }
            .onDisappear { motion.stop() }
        }
    }

    private var instructions: some View {
        VStack(spacing: 20) {
            ProgressView(value: Double(step + 1), total: Double(steps.count))
                .tint(Color.kpCyanSecondary)
            Text(steps[step].title).font(.kpTitleMid).foregroundStyle(Color.kpNearWhite)
            Text(steps[step].instruction).font(.kpBody).foregroundStyle(Color.kpLightSlate)
                .multilineTextAlignment(.center)
            Spacer()
            Button {
                capture()
            } label: {
                Text(submitting ? "Submitting…" : "CAPTURE")
                    .font(.kpTitleSm)
                    .frame(maxWidth: .infinity, minHeight: 56)
            }
            .buttonStyle(.borderedProminent)
            .tint(Color.kpLuminaBlue)
            .disabled(submitting)
        }
    }

    private func success(_ r: AimWizardResult) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "checkmark.circle.fill")
                .font(.largeTitle).foregroundStyle(Color.kpGreenOnline)
            Text("Aim wizard complete").font(.kpTitleMid).foregroundStyle(Color.kpNearWhite)
            if let f = r.forwardLocal, let u = r.upLocal {
                Text("forward = [\(format(f))]\nup = [\(format(u))]")
                    .font(.kpMono)
                    .foregroundStyle(Color.kpLightSlate)
                    .multilineTextAlignment(.center)
            }
            Button("Done") { dismiss() }
                .buttonStyle(.borderedProminent)
                .tint(Color.kpLuminaBlue)
        }
    }

    private func failure(_ err: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.largeTitle).foregroundStyle(Color.kpOrangeWled)
            Text("Wizard failed").font(.kpTitleMid).foregroundStyle(Color.kpNearWhite)
            Text(err).font(.kpBodySm)
                .foregroundStyle(Color.kpLightSlate)
                .multilineTextAlignment(.center)
            Button("Retry") {
                step = 0
                poses.removeAll()
                errorMessage = nil
            }
            .buttonStyle(.bordered)
        }
    }

    private func capture() {
        let q = motion.currentQuat
        poses.append(AimWizardPose(role: steps[step].role, quat: q))
        Haptics.fire(.successTick)
        if step + 1 < steps.count {
            step += 1
        } else {
            submit()
        }
    }

    private func submit() {
        submitting = true
        Task {
            do {
                let resp = try await client.submitAimWizard(poses)
                submitting = false
                if resp.ok == true, let f = resp.forwardLocal, let u = resp.upLocal {
                    prefs.saveWizardResult(forward: f, up: u)
                    result = resp
                } else {
                    errorMessage = resp.detail ?? resp.err ?? "Wizard rejected"
                }
            } catch {
                submitting = false
                errorMessage = "Network error"
            }
        }
    }

    private func format(_ v: [Double]) -> String {
        v.map { String(format: "%.2f", $0) }.joined(separator: ", ")
    }
}

@MainActor
private final class AimWizardMotion: ObservableObject {
    private let manager = CMMotionManager()
    var currentQuat: [Double] = [1, 0, 0, 0]

    func start() {
        guard manager.isDeviceMotionAvailable else { return }
        manager.deviceMotionUpdateInterval = 0.05
        manager.startDeviceMotionUpdates(using: .xArbitraryZVertical, to: .main) { [weak self] motion, _ in
            guard let self, let motion else { return }
            let q = motion.attitude.quaternion
            self.currentQuat = [q.w, q.x, q.y, q.z]
        }
    }
    func stop() { manager.stopDeviceMotionUpdates() }
}
