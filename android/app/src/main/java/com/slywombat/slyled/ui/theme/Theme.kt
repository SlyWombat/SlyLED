package com.slywombat.slyled.ui.theme

import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

// ── Kinetic Prism Design Tokens (design.md v2.0) ────────────────────────

// Core palette
val DeepSlate = Color(0xFF0A0F13)     // OLED-black infinite-depth background
val DarkNavy = Color(0xFF0F172A)      // Cards, surfaces
val SurfaceHigh = Color(0xFF1E293B)   // Modals, panels
val DarkSlate = Color(0xFF0B1120)     // Side panels, containers
val MutedSlate = Color(0xFF64748B)    // Borders, inactive text
val DimSlate = Color(0xFF334155)      // Subtle borders
val NearWhite = Color(0xFFE2E8F0)     // Primary text
val LightSlate = Color(0xFF94A3B8)    // Secondary text, labels

// Accents
val LuminaBlue = Color(0xFF0969DA)    // Primary actions
val BluePrimary = Color(0xFF3B82F6)   // Links, active states
val CyanSecondary = Color(0xFF22D3EE) // Highlights, active accents
val OrangeWled = Color(0xFFF59E0B)    // WLED, warnings
val RedError = Color(0xFFEF4444)      // Errors, destructive
val GreenOnline = Color(0xFF22C55E)   // Online, success
val DmxPurple = Color(0xFF7C3AED)     // DMX fixtures, profiles, community

private val DarkColorScheme = darkColorScheme(
    primary = LuminaBlue,
    secondary = CyanSecondary,
    tertiary = OrangeWled,
    background = DeepSlate,
    surface = DarkNavy,
    surfaceVariant = DarkSlate,
    surfaceContainerLow = DarkNavy,
    surfaceContainer = SurfaceHigh,
    surfaceContainerHigh = SurfaceHigh,
    onBackground = NearWhite,
    onSurface = NearWhite,
    onSurfaceVariant = LightSlate,
    outline = MutedSlate,
    outlineVariant = DimSlate,
    error = RedError,
    onError = Color.White,
)

private val LightColorScheme = lightColorScheme(
    primary = LuminaBlue,
    secondary = CyanSecondary,
    tertiary = OrangeWled,
    background = Color(0xFFF8FAFC),
    surface = Color.White,
    surfaceVariant = Color(0xFFE2E8F0),
    onBackground = Color(0xFF0F172A),
    onSurface = Color(0xFF0F172A),
    onSurfaceVariant = Color(0xFF475569),
    outline = Color(0xFF94A3B8),
    outlineVariant = Color(0xFFE2E8F0),
    error = RedError,
)

@Composable
fun SlyLedTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme
    MaterialTheme(
        colorScheme = colorScheme,
        content = content
    )
}
