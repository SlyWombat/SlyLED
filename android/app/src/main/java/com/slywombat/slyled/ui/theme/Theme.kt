package com.slywombat.slyled.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// SlyLED design tokens — matches desktop SPA
val DarkNavy = Color(0xFF020617)
val Slate = Color(0xFF0F172A)
val DarkSlate = Color(0xFF0B1120)
val MutedSlate = Color(0xFF64748B)
val DimSlate = Color(0xFF334155)
val NearWhite = Color(0xFFF1F5F9)
val LightSlate = Color(0xFF94A3B8)
val BluePrimary = Color(0xFF3B82F6)
val CyanSecondary = Color(0xFF22D3EE)
val OrangeWled = Color(0xFFF59E0B)
val RedError = Color(0xFFEF4444)
val GreenOnline = Color(0xFF22C55E)

private val DarkColorScheme = darkColorScheme(
    primary = BluePrimary,
    secondary = CyanSecondary,
    tertiary = OrangeWled,
    background = DarkNavy,
    surface = Slate,
    surfaceVariant = DarkSlate,
    onBackground = NearWhite,
    onSurface = NearWhite,
    onSurfaceVariant = LightSlate,
    outline = MutedSlate,
    outlineVariant = DimSlate,
    error = RedError,
)

private val LightColorScheme = lightColorScheme(
    primary = BluePrimary,
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
