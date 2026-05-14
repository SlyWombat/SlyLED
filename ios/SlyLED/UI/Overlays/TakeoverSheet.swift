// TakeoverSheet — confirms a claim takeover when another client already
// holds the mover. Confirm fires claim(force: true) with a heavy haptic.

import SwiftUI

struct TakeoverSheet: View {
    let pending: ControlViewModel.PendingTakeover
    let onConfirm: () -> Void
    let onCancel: () -> Void

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: "person.2.crop.square.stack.fill")
                .font(.largeTitle)
                .foregroundStyle(Color.kpDmxPurple)
            Text("Mover already held")
                .font(.kpTitleMid)
                .foregroundStyle(Color.kpNearWhite)
            Text("\(pending.fixtureName) is currently controlled by \(pending.heldBy). Taking over will release their session.")
                .font(.kpBodySm)
                .foregroundStyle(Color.kpLightSlate)
                .multilineTextAlignment(.center)
            HStack(spacing: 12) {
                Button("Cancel", action: onCancel)
                    .frame(maxWidth: .infinity, minHeight: 48)
                    .buttonStyle(.bordered)
                Button(role: .destructive, action: onConfirm) {
                    Text("Take over")
                        .frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
                .tint(Color.kpRedError)
            }
        }
        .padding(24)
        .background(Color.kpDarkNavy)
        .presentationDetents([.medium])
    }
}
