// BeatDetector — port of Android BeatDetector.kt. Constants are part of
// the contract and must not be retuned without re-running the shared
// audio test corpus across Kotlin + Swift.

import Foundation

final class BeatDetector {
    var onsetThreshold: Float = 0.012
    var minBeatGapMs: Int64 = 250
    var baselineAlpha: Float = 0.02

    private var baseline: Float = 0
    private var lastBeatMs: Int64 = 0
    private var intervals: [Int64] = []
    private let maxIntervals = 8

    private(set) var lastOnset: Float = 0
    private(set) var bpm: Float = 0

    /// Returns true on a newly detected beat. `peak` is normalised 0..1,
    /// `nowMs` is current epoch milliseconds.
    @discardableResult
    func process(peak: Float, nowMs: Int64) -> Bool {
        let onset = max(0, peak - baseline)
        baseline += baselineAlpha * (peak - baseline)
        lastOnset = onset

        guard onset >= onsetThreshold else { return false }
        let gap = nowMs - lastBeatMs
        if lastBeatMs != 0 && gap < minBeatGapMs { return false }

        if lastBeatMs != 0 {
            intervals.append(gap)
            while intervals.count > maxIntervals { intervals.removeFirst() }
            recomputeBpm()
        }
        lastBeatMs = nowMs
        return true
    }

    private func recomputeBpm() {
        guard intervals.count >= 2 else { return }
        let sorted = intervals.sorted()
        let median = Float(sorted[sorted.count / 2])
        guard median > 0 else { return }
        let candidate = 60_000 / median
        bpm = max(40, min(220, candidate))
    }

    func reset() {
        baseline = 0
        lastBeatMs = 0
        intervals.removeAll()
        lastOnset = 0
        bpm = 0
    }
}
