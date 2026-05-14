// KineticPrism design tokens — colour + type ramp ported byte-for-byte from
// Android Theme.kt. Hex values match the desktop SPA and Android v1.8.2 so
// all three platforms render the same palette.
//
// Font families fall back to SF Pro Display / SF Pro Text / SF Mono — Space
// Grotesk + Inter bundle as a v0.7.1 polish step, matching Android v1.8.x
// which also falls back to the system sans family today.

import SwiftUI

extension Color {
    // Core palette
    static let kpDeepSlate      = Color(red: 0x0A/255, green: 0x0F/255, blue: 0x13/255) // 0xFF0A0F13
    static let kpDarkNavy       = Color(red: 0x0F/255, green: 0x17/255, blue: 0x2A/255) // 0xFF0F172A
    static let kpSurfaceHigh    = Color(red: 0x1E/255, green: 0x29/255, blue: 0x3B/255) // 0xFF1E293B
    static let kpDarkSlate      = Color(red: 0x0B/255, green: 0x11/255, blue: 0x20/255) // 0xFF0B1120
    static let kpMutedSlate     = Color(red: 0x64/255, green: 0x74/255, blue: 0x8B/255) // 0xFF64748B
    static let kpDimSlate       = Color(red: 0x33/255, green: 0x41/255, blue: 0x55/255) // 0xFF334155
    static let kpNearWhite      = Color(red: 0xE2/255, green: 0xE8/255, blue: 0xF0/255) // 0xFFE2E8F0
    static let kpLightSlate     = Color(red: 0x94/255, green: 0xA3/255, blue: 0xB8/255) // 0xFF94A3B8

    // Accents
    static let kpLuminaBlue     = Color(red: 0x09/255, green: 0x69/255, blue: 0xDA/255) // 0xFF0969DA
    static let kpBluePrimary    = Color(red: 0x3B/255, green: 0x82/255, blue: 0xF6/255) // 0xFF3B82F6
    static let kpCyanSecondary  = Color(red: 0x22/255, green: 0xD3/255, blue: 0xEE/255) // 0xFF22D3EE
    static let kpOrangeWled     = Color(red: 0xF5/255, green: 0x9E/255, blue: 0x0B/255) // 0xFFF59E0B
    static let kpRedError       = Color(red: 0xEF/255, green: 0x44/255, blue: 0x44/255) // 0xFFEF4444
    static let kpGreenOnline    = Color(red: 0x22/255, green: 0xC5/255, blue: 0x5E/255) // 0xFF22C55E
    static let kpDmxPurple      = Color(red: 0x7C/255, green: 0x3A/255, blue: 0xED/255) // 0xFF7C3AED
}

extension Font {
    // Type ramp matches Android labelMedium/bodySmall/bodyMedium/titleLarge/displaySmall.
    // Family is system sans / system mono until Space Grotesk + Inter ship.
    static let kpDisplay   = Font.system(size: 28, weight: .bold,     design: .default)
    static let kpTitle     = Font.system(size: 22, weight: .semibold, design: .default)
    static let kpTitleMid  = Font.system(size: 18, weight: .semibold, design: .default)
    static let kpTitleSm   = Font.system(size: 14, weight: .semibold, design: .default)
    static let kpBody      = Font.system(size: 16, weight: .regular,  design: .default)
    static let kpBodySm    = Font.system(size: 14, weight: .regular,  design: .default)
    static let kpCaption   = Font.system(size: 12, weight: .regular,  design: .default)
    static let kpLabel     = Font.system(size: 12, weight: .medium,   design: .default)
    static let kpLabelSm   = Font.system(size: 11, weight: .medium,   design: .default)
    static let kpMono      = Font.system(size: 13, weight: .regular,  design: .monospaced)
    static let kpMonoBig   = Font.system(size: 36, weight: .bold,     design: .monospaced)
}

// Bloom on active / pressed elements. SwiftUI shadow with a tint colour
// is the analog of the Compose bloom on Android.
extension View {
    func kpBloom(_ tint: Color, active: Bool = true, radius: CGFloat = 12) -> some View {
        self.shadow(color: active ? tint.opacity(0.5) : .clear,
                    radius: active ? radius : 0)
    }
}
