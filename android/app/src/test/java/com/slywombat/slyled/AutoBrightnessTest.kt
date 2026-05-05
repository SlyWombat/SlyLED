package com.slywombat.slyled

import com.slywombat.slyled.audio.EnvelopeFollower
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.math.PI
import kotlin.math.sin

/**
 * #804 — DSP envelope-follower unit tests. Validates the math the Android
 * Auto Brightness feature relies on. Pure JVM, no Android imports.
 */
class AutoBrightnessTest {

    private fun pump(env: EnvelopeFollower, samples: FloatArray, hops: Int): Float {
        var last = 0f
        repeat(hops) { last = env.process(samples) }
        return last
    }

    /** Silence in → silence out (baseline never lifts). */
    @Test
    fun `silent buffer yields zero envelope`() {
        val env = EnvelopeFollower(sampleRateHz = 8000).apply { floor = 0f }
        val silence = FloatArray(400) { 0f }
        val out = pump(env, silence, hops = 10)
        assertEquals(0f, out, 1e-3f)
        assertEquals(0, env.toMaster())
    }

    /** A 100 Hz tone (well inside the 250 Hz LPF passband) lifts the envelope. */
    @Test
    fun `bass tone drives envelope above baseline`() {
        val env = EnvelopeFollower(sampleRateHz = 8000).apply {
            sensitivity = 2f
            attackMs = 5f
            releaseMs = 100f
        }
        val tone = FloatArray(400) { i ->
            (0.5f * sin(2f * PI.toFloat() * 100f * i / 8000f)).toFloat()
        }
        val out = pump(env, tone, hops = 30)
        assertTrue("bass tone must produce envelope > 0.1, got $out", out > 0.1f)
    }

    /** Floor/ceiling clamp the master output range. */
    @Test
    fun `floor and ceiling map envelope to brightness band`() {
        val env = EnvelopeFollower().apply { floor = 0.2f; ceiling = 0.8f }
        // env = 0 → master = round(0.2 * 255) = 51
        assertEquals(51, env.toMaster(0f))
        // env = 1 → master = round(0.8 * 255) = 204
        assertEquals(204, env.toMaster(1f))
        // env = 0.5 → midway = round(0.5 * 255) = 128 (half-up rounding)
        assertEquals(128, env.toMaster(0.5f))
    }

    /** Clipping at floor=ceiling pins master to a fixed value. */
    @Test
    fun `floor equal to ceiling pins brightness`() {
        val env = EnvelopeFollower().apply { floor = 0.5f; ceiling = 0.5f }
        assertEquals(128, env.toMaster(0f))
        assertEquals(128, env.toMaster(1f))
    }

    /** Reset() collapses smoother and baseline back to zero. */
    @Test
    fun `reset clears smoothed state`() {
        val env = EnvelopeFollower(sampleRateHz = 8000).apply { sensitivity = 4f }
        val tone = FloatArray(400) { i ->
            (sin(2f * PI.toFloat() * 100f * i / 8000f)).toFloat()
        }
        pump(env, tone, hops = 40)
        assertTrue(env.envelope > 0f)
        env.reset()
        assertEquals(0f, env.envelope, 1e-6f)
    }

    /** toMaster() always returns a valid 0..255 byte regardless of input. */
    @Test
    fun `master output is clamped to byte range`() {
        val env = EnvelopeFollower().apply { floor = 0f; ceiling = 1f }
        assertEquals(0, env.toMaster(-1f))
        assertEquals(255, env.toMaster(2f))
    }

    /** Out-of-range floor/ceiling are coerced inside [0,1] by toMaster. */
    @Test
    fun `out of range floor ceiling are coerced`() {
        val env = EnvelopeFollower().apply { floor = -0.5f; ceiling = 2f }
        assertEquals(0, env.toMaster(0f))
        assertEquals(255, env.toMaster(1f))
    }

    /** Onset spikes (kick drums) decay back down on the release tau. */
    @Test
    fun `release smoothing decays envelope after silence`() {
        val env = EnvelopeFollower(sampleRateHz = 8000).apply {
            sensitivity = 3f
            attackMs = 5f
            releaseMs = 80f
        }
        val tone = FloatArray(400) { i ->
            (sin(2f * PI.toFloat() * 100f * i / 8000f)).toFloat()
        }
        val silence = FloatArray(400) { 0f }
        // Pump signal up
        pump(env, tone, hops = 30)
        val peak = env.envelope
        // Then silence — envelope should fall.
        pump(env, silence, hops = 30)
        val tail = env.envelope
        assertTrue("envelope must decay after silence (peak=$peak, tail=$tail)", tail < peak)
    }
}
