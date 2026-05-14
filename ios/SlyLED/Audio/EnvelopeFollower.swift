// EnvelopeFollower — pure-DSP port of Android's EnvelopeFollower.kt
// (constants must stay byte-for-byte to keep envelope traces matching
// the Kotlin implementation against the shared WAV test corpus).
//
// Pipeline per 50 ms hop (~400 samples @ 8 kHz):
//   1. Single biquad LPF @ 250 Hz isolates kick/bass.
//   2. RMS energy of the band-passed slice.
//   3. Spectral-flux-style onset = (RMS − slow EMA baseline), clamped ≥0,
//      multiplied by 2.0.
//   4. Asymmetric one-pole smoother (attack 8 ms, release 220 ms).
//   5. Sensitivity gain → 1−exp(−x) soft clip → 0..1.

import Foundation

final class EnvelopeFollower {
    // Biquad LPF (RBJ cookbook coeffs)
    private let b0, b1, b2, a1, a2: Float
    private var z1: Float = 0
    private var z2: Float = 0

    private var rmsBaseline: Float = 0
    private let baselineAlpha: Float = 0.05

    private(set) var smoothed: Float = 0

    var sensitivity: Float = 1.5
    var floor: Float = 0.05
    var ceiling: Float = 1.0
    var attackMs: Float = 8
    var releaseMs: Float = 220

    private let sampleRateHz: Int

    init(sampleRateHz: Int = 8000, cutoffHz: Float = 250) {
        self.sampleRateHz = sampleRateHz
        let w0 = 2 * Float.pi * cutoffHz / Float(sampleRateHz)
        let c = cosf(w0)
        let s = sinf(w0)
        let q: Float = 0.707
        let alpha = s / (2 * q)
        let a0 = 1 + alpha
        b0 = ((1 - c) / 2) / a0
        b1 = (1 - c) / a0
        b2 = ((1 - c) / 2) / a0
        a1 = (-2 * c) / a0
        a2 = (1 - alpha) / a0
    }

    @discardableResult
    func process(_ samples: UnsafePointer<Float>, count: Int) -> Float {
        guard count > 0 else { return smoothed }
        var sumSq: Float = 0
        for i in 0..<count {
            let x = samples[i]
            let y = b0 * x + z1
            z1 = b1 * x - a1 * y + z2
            z2 = b2 * x - a2 * y
            sumSq += y * y
        }
        let rms = sqrtf(sumSq / Float(count))

        let onset = max(0, rms - rmsBaseline) * 2.0
        rmsBaseline += baselineAlpha * (rms - rmsBaseline)

        let target = (rms + onset) * sensitivity
        let clipped = softClip(target)

        let dtMs = (Float(count) / Float(sampleRateHz)) * 1000.0
        let tau = (clipped > smoothed) ? attackMs : releaseMs
        let a: Float = tau <= 0 ? 1.0 : 1.0 - expf(-dtMs / tau)
        smoothed += a * (clipped - smoothed)
        if smoothed < 0 { smoothed = 0 }
        if smoothed > 1 { smoothed = 1 }
        return smoothed
    }

    func toMaster(_ env: Float) -> Int {
        let f = max(0, min(1, floor))
        let c = max(f, min(1, ceiling))
        let mapped = f + (c - f) * max(0, min(1, env))
        return min(255, max(0, Int(mapped * 255 + 0.5)))
    }

    func reset() {
        z1 = 0
        z2 = 0
        rmsBaseline = 0
        smoothed = 0
    }

    private func softClip(_ x: Float) -> Float {
        if x <= 0 { return 0 }
        return 1 - expf(-abs(x))
    }
}
