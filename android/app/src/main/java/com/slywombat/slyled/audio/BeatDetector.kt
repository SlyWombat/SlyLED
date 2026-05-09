package com.slywombat.slyled.audio

/**
 * #820 — beat detector that runs alongside [EnvelopeFollower] in the mic
 * pipeline. Consumes the per-hop raw peak (or onset) and emits discrete
 * beat events plus a rolling BPM estimate.
 *
 * Algorithm — adaptive-threshold spectral flux:
 *   1. Maintain a slow EMA of recent peaks as the baseline noise floor.
 *   2. Onset = (current peak − baseline), clamped non-negative.
 *   3. A beat is declared when onset > [onsetThreshold] AND the gap since
 *      the last beat exceeds [minBeatGapMs] (debounces fast double-hits).
 *   4. Inter-beat intervals are buffered in a 8-deep ring; BPM is the
 *      reciprocal of their median, clamped to [40, 220].
 *
 * Pure Kotlin / no Android imports → unit-testable on JVM.
 */
class BeatDetector(
    // Live-tuning 2026-05-05: with audio in the room, raw peak hovered
    // at 0.04–0.07 (post-AGC mic). 0.04 threshold + 0.10 baselineAlpha
    // caught only the first transient, then the baseline absorbed the
    // signal level inside ~1 s and onsets dropped below threshold.
    // Tightened to be more sensitive: baseline tracks the long-term
    // floor, threshold catches modest swings — good for typical music
    // played at moderate volume in a room.
    var onsetThreshold: Float = 0.012f,
    var minBeatGapMs: Long = 250,    // max ~240 BPM, well above pop/rock
    var baselineAlpha: Float = 0.02f,
) {
    private var baseline = 0f
    private var lastBeatMs = 0L
    private val intervals = ArrayDeque<Long>()
    private val maxIntervals = 8

    /** Last detected onset (per [process]) — public for diagnostics. */
    var lastOnset: Float = 0f
        private set

    /** Last computed BPM (Hz). 0 until at least two beats are seen. */
    var bpm: Float = 0f
        private set

    /**
     * Push one hop. [peak] is the raw normalised amplitude (0..1); [nowMs]
     * is `System.currentTimeMillis()` or equivalent. Returns true on a
     * newly detected beat (caller can flash a pulse + recompute BPM).
     */
    fun process(peak: Float, nowMs: Long): Boolean {
        val onset = (peak - baseline).coerceAtLeast(0f)
        // Update baseline AFTER computing onset so a sudden peak isn't
        // immediately absorbed into the noise floor.
        baseline += baselineAlpha * (peak - baseline)
        lastOnset = onset

        if (onset < onsetThreshold) return false
        val gap = nowMs - lastBeatMs
        if (lastBeatMs != 0L && gap < minBeatGapMs) return false

        if (lastBeatMs != 0L) {
            intervals.addLast(gap)
            while (intervals.size > maxIntervals) intervals.removeFirst()
            recomputeBpm()
        }
        lastBeatMs = nowMs
        return true
    }

    private fun recomputeBpm() {
        if (intervals.size < 2) return
        // Use the median to suppress outliers (a missed beat doubles
        // the interval; a spurious one halves it).
        val sorted = intervals.sorted()
        val medianMs = sorted[sorted.size / 2].toFloat()
        if (medianMs <= 0f) return
        val candidate = 60_000f / medianMs
        bpm = candidate.coerceIn(40f, 220f)
    }

    fun reset() {
        baseline = 0f
        lastBeatMs = 0L
        intervals.clear()
        lastOnset = 0f
        bpm = 0f
    }
}
