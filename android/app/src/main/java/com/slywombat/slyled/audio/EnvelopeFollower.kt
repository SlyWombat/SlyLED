package com.slywombat.slyled.audio

import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.sqrt

/**
 * #804 — Pure-DSP envelope follower for Android Auto Brightness.
 *
 * Pipeline per buffer (8 kHz mono, ~50 ms hops):
 *   1. Single biquad low-pass at ~250 Hz isolates kicks/bass.
 *   2. RMS energy of the band-passed slice (the "instantaneous" level).
 *   3. Onset peak-pick: spectral-flux-style positive difference vs. EMA of past RMS.
 *   4. Attack / release one-pole smoother on (RMS + onset bonus).
 *   5. Sensitivity gain → soft clip 0..1 → floor/ceiling map → 0..255.
 *
 * Pure Kotlin; no Android imports → unit-testable on JVM.
 */
class EnvelopeFollower(
    private val sampleRateHz: Int = 8000,
    cutoffHz: Float = 250f,
) {
    // Biquad LPF state (RBJ cookbook coeffs)
    private val b0: Float
    private val b1: Float
    private val b2: Float
    private val a1: Float
    private val a2: Float
    private var z1 = 0f
    private var z2 = 0f

    // Onset detector state — slow EMA of past RMS for spectral-flux baseline.
    private var rmsBaseline = 0f
    private val baselineAlpha = 0.05f

    // Output smoother — separate attack & release time constants.
    private var smoothed = 0f

    var sensitivity: Float = 1.5f
    var floor: Float = 0.05f
    var ceiling: Float = 1.0f
    var attackMs: Float = 8f
    var releaseMs: Float = 220f

    init {
        // RBJ low-pass biquad
        val w0 = 2f * Math.PI.toFloat() * cutoffHz / sampleRateHz
        val cos = kotlin.math.cos(w0)
        val sin = kotlin.math.sin(w0)
        val q = 0.707f
        val alpha = sin / (2f * q)
        val a0 = 1f + alpha
        b0 = ((1f - cos) / 2f) / a0
        b1 = (1f - cos) / a0
        b2 = ((1f - cos) / 2f) / a0
        a1 = (-2f * cos) / a0
        a2 = (1f - alpha) / a0
    }

    /**
     * Push one buffer of mono PCM samples in the range [-1f, 1f] and return
     * the smoothed envelope value 0..1 after this hop.
     */
    fun process(samples: FloatArray, length: Int = samples.size): Float {
        if (length <= 0) return smoothed
        var sumSq = 0f
        for (i in 0 until length) {
            // Biquad direct-form-II transposed
            val x = samples[i]
            val y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            sumSq += y * y
        }
        val rms = sqrt(sumSq / length)

        // Onset bonus = positive deviation above slow baseline
        val onset = (rms - rmsBaseline).coerceAtLeast(0f) * 2f
        rmsBaseline += baselineAlpha * (rms - rmsBaseline)

        val target = (rms + onset) * sensitivity
        val clipped = softClip(target)

        // One-pole asymmetric smoother. dt is buffer length / sampleRate.
        val dtMs = (length.toFloat() / sampleRateHz) * 1000f
        val tau = if (clipped > smoothed) attackMs else releaseMs
        val a = if (tau <= 0f) 1f else 1f - exp(-dtMs / tau)
        smoothed += a * (clipped - smoothed)
        if (smoothed < 0f) smoothed = 0f
        if (smoothed > 1f) smoothed = 1f
        return smoothed
    }

    /** Map current envelope to a 0..255 master brightness. */
    fun toMaster(env: Float = smoothed): Int {
        val f = floor.coerceIn(0f, 1f)
        val c = ceiling.coerceIn(f, 1f)
        val mapped = f + (c - f) * env.coerceIn(0f, 1f)
        return (mapped * 255f + 0.5f).toInt().coerceIn(0, 255)
    }

    fun reset() {
        z1 = 0f; z2 = 0f
        rmsBaseline = 0f
        smoothed = 0f
    }

    /** Last smoothed envelope value (0..1) without re-processing. */
    val envelope: Float get() = smoothed

    private fun softClip(x: Float): Float {
        // 1 - exp(-x) — smooth knee at 1.0, never exceeds 1.
        if (x <= 0f) return 0f
        return 1f - exp(-abs(x))
    }
}
