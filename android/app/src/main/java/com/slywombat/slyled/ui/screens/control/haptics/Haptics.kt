package com.slywombat.slyled.ui.screens.control.haptics

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.ui.platform.LocalContext

// Haptics catalogue — design doc §6.3. Every primary mobile action emits
// a distinct haptic so the operator can confirm with their thumb without
// looking at the screen. iOS port will swap the impl for
// UIImpactFeedbackGenerator + UINotificationFeedbackGenerator via
// expect/actual when CMP scaffolds. #888.

enum class HapticEvent {
    /** Start show, start timeline, claim acquired. */
    SUCCESS_TICK,
    /** STOP show, blackout, panic actions. */
    HEAVY_THUD,
    /** Blackout — doubled. */
    HEAVY_DOUBLE,
    /** Toggle (Auto Brightness, UV, Bubble), chip select. */
    LIGHT_TICK,
    /** Denied — claim conflict, disconnected button press, profile error. */
    NO_GO_BUMP,
    /** Slider 5% step boundaries. */
    SOFT_TICK,
    /** Strobe momentary press-and-hold begin. */
    LOW_RUMBLE,
}

object Haptics {

    fun fire(context: Context, event: HapticEvent) {
        val vib = vibrator(context) ?: return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            vib.vibrate(buildEffect(event))
        } else {
            // API 26+ is our minSdk per build.gradle.kts, so this branch
            // is unreachable in practice but kept for completeness.
            @Suppress("DEPRECATION")
            vib.vibrate(when (event) {
                HapticEvent.HEAVY_THUD,
                HapticEvent.HEAVY_DOUBLE  -> 80L
                HapticEvent.SUCCESS_TICK  -> 35L
                HapticEvent.LIGHT_TICK,
                HapticEvent.SOFT_TICK     -> 15L
                HapticEvent.NO_GO_BUMP    -> 25L
                HapticEvent.LOW_RUMBLE    -> 60L
            })
        }
    }

    private fun buildEffect(event: HapticEvent): VibrationEffect = when (event) {
        HapticEvent.SUCCESS_TICK ->
            // 30ms strong click
            VibrationEffect.createOneShot(30L, 200)
        HapticEvent.HEAVY_THUD ->
            // 80ms full amplitude
            VibrationEffect.createOneShot(80L, VibrationEffect.DEFAULT_AMPLITUDE)
        HapticEvent.HEAVY_DOUBLE ->
            // 80ms / 50ms gap / 80ms — felt as double-thump
            VibrationEffect.createWaveform(longArrayOf(0L, 80L, 50L, 80L), -1)
        HapticEvent.LIGHT_TICK ->
            VibrationEffect.createOneShot(15L, 90)
        HapticEvent.NO_GO_BUMP ->
            // light double-bump — the "thing went wrong" pattern
            VibrationEffect.createWaveform(longArrayOf(0L, 20L, 60L, 20L), -1)
        HapticEvent.SOFT_TICK ->
            VibrationEffect.createOneShot(8L, 60)
        HapticEvent.LOW_RUMBLE ->
            // Used at start of strobe press; renderer keeps the visual
            // glow going while the operator holds. 60ms low amplitude.
            VibrationEffect.createOneShot(60L, 70)
    }

    private fun vibrator(context: Context): Vibrator? {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val mgr = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
            mgr?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
    }
}

/** Composable convenience — call from any composable to fire haptics. */
@Composable
@ReadOnlyComposable
fun rememberHaptics(): (HapticEvent) -> Unit {
    val ctx = LocalContext.current.applicationContext
    return { event -> Haptics.fire(ctx, event) }
}
