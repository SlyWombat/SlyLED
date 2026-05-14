// ConnectionPillView.swift — top-bar widget showing link state.
// Tap to manually retry.

import SwiftUI

struct ConnectionPillView: View {
    let state: LinkState
    let onTap: () -> Void
    @State private var pulse = false

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(state.rawValue)
                .font(.caption.weight(.medium))
                .foregroundStyle(color)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 4)
        .overlay(
            RoundedRectangle(cornerRadius: 12).stroke(color.opacity(0.4), lineWidth: 1)
        )
        .opacity(state == .connected ? 1.0 : (pulse ? 1.0 : 0.55))
        .animation(
            state == .connected ? .default :
                .easeInOut(duration: state == .degraded ? 1.2 : 0.5).repeatForever(autoreverses: true),
            value: pulse
        )
        .onAppear { pulse = true }
        .onTapGesture { onTap() }
    }

    private var color: Color {
        switch state {
        case .connected: return Color.kpGreenOnline
        case .degraded: return Color.kpOrangeWled
        case .disconnected: return Color.kpRedError
        }
    }
}

#Preview {
    VStack(spacing: 12) {
        ConnectionPillView(state: .connected, onTap: {})
        ConnectionPillView(state: .degraded, onTap: {})
        ConnectionPillView(state: .disconnected, onTap: {})
    }
    .padding()
    .preferredColorScheme(.dark)
}
