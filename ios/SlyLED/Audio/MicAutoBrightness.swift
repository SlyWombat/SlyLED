// MicAutoBrightness — AVAudioEngine tap driving the EnvelopeFollower +
// BeatDetector, pushing master brightness via UdpClient.sendAutoBrightness
// at ~20 Hz (one hop = ~50 ms).
//
// Mic-only on iOS per ios_parity_spec.md §9.3. Permission is requested on
// first .start(); on denial the state machine transitions to .denied.
//
// Mic sample rate is whatever the device picks (usually 44.1/48 kHz); we
// resample to 8 kHz mono inside the tap so the envelope math runs on the
// same byte stream as the Android reference.

import Foundation
import AVFoundation
import Combine

@MainActor
final class MicAutoBrightness: ObservableObject {
    enum Mode {
        case idle
        case listening
        case denied
        case noMic
    }

    @Published private(set) var mode: Mode = .idle
    @Published private(set) var envelope: Float = 0    // smoothed, 0..1
    @Published private(set) var rawPeak: Float = 0     // last hop, 0..1
    @Published private(set) var beatPulse: Float = 0
    @Published private(set) var bpm: Float = 0
    @Published private(set) var beatCount: Int = 0
    @Published private(set) var master: Float = 0     // post floor/ceiling, 0..1

    private let prefs: ServerPreferences
    private let udp = UdpClient.shared
    private let follower = EnvelopeFollower(sampleRateHz: 8000)
    private let beatDetector = BeatDetector()
    private let engine = AVAudioEngine()
    private var tapInstalled = false

    private let targetSampleRate: Float = 8000
    private var resampleAccumulator: [Float] = []
    private static let hopSamples = 400  // 50 ms @ 8 kHz

    // Throttle the UDP push to ~20 Hz so we don't flood the orchestrator.
    private var lastSendAtMs: Int64 = 0
    private static let sendIntervalMs: Int64 = 50

    init(prefs: ServerPreferences) {
        self.prefs = prefs
        applyPrefs()
    }

    private func applyPrefs() {
        follower.sensitivity = Float(prefs.autoBrightnessSensitivity)
        follower.floor       = Float(prefs.autoBrightnessFloor)
        follower.ceiling     = Float(prefs.autoBrightnessCeiling)
        follower.attackMs    = Float(prefs.autoBrightnessAttackMs)
        follower.releaseMs   = Float(prefs.autoBrightnessReleaseMs)
    }

    func configure() {
        applyPrefs()
    }

    func start() async {
        applyPrefs()
        // Permission gate
        let granted = await requestMicPermission()
        guard granted else {
            mode = .denied
            return
        }

        do {
            try AVAudioSession.sharedInstance().setCategory(
                .playAndRecord, mode: .measurement,
                options: [.mixWithOthers, .allowBluetooth, .defaultToSpeaker])
            try AVAudioSession.sharedInstance().setActive(true)
        } catch {
            mode = .noMic
            return
        }

        let input = engine.inputNode
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0 else {
            mode = .noMic
            return
        }
        let sourceSampleRate = Float(format.sampleRate)

        if tapInstalled { input.removeTap(onBus: 0); tapInstalled = false }
        input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.processBuffer(buffer, sourceSampleRate: sourceSampleRate)
        }
        tapInstalled = true

        do {
            try engine.start()
            mode = .listening
            follower.reset()
            beatDetector.reset()
            await MainActor.run {
                envelope = 0
                rawPeak = 0
                beatPulse = 0
                bpm = 0
                beatCount = 0
                master = 0
            }
        } catch {
            mode = .noMic
        }
    }

    func stop() {
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        mode = .idle
        envelope = 0
        rawPeak = 0
        beatPulse = 0
        master = 0
    }

    private func requestMicPermission() async -> Bool {
        await withCheckedContinuation { cont in
            AVAudioApplication.requestRecordPermission { granted in
                cont.resume(returning: granted)
            }
        }
    }

    nonisolated private func processBuffer(_ buffer: AVAudioPCMBuffer,
                                           sourceSampleRate: Float) {
        guard let channelData = buffer.floatChannelData else { return }
        let frameCount = Int(buffer.frameLength)
        guard frameCount > 0 else { return }

        // Mono mix-down — average channels if present.
        let channels = Int(buffer.format.channelCount)
        var mono = [Float](repeating: 0, count: frameCount)
        for c in 0..<channels {
            let ptr = channelData[c]
            for i in 0..<frameCount {
                mono[i] += ptr[i]
            }
        }
        if channels > 1 {
            let inv = 1.0 / Float(channels)
            for i in 0..<frameCount { mono[i] *= inv }
        }

        // Decimate to 8 kHz with a simple polyphase-equivalent picker: take
        // every Nth sample where N = round(sourceSampleRate / targetSampleRate).
        // The EnvelopeFollower's biquad LPF already band-limits to 250 Hz so
        // aliasing from a crude decimator is harmless (we throw away energy
        // above 4 kHz that the LPF would zero anyway).
        let stride = max(1, Int(sourceSampleRate / targetSampleRate))
        var down: [Float] = []
        down.reserveCapacity(frameCount / stride + 1)
        var i = 0
        while i < frameCount {
            down.append(mono[i])
            i += stride
        }

        Task { @MainActor [self, down] in
            self.feedHop(down)
        }
    }

    private func feedHop(_ samples: [Float]) {
        guard !samples.isEmpty else { return }
        resampleAccumulator.append(contentsOf: samples)

        while resampleAccumulator.count >= Self.hopSamples {
            let hop = Array(resampleAccumulator.prefix(Self.hopSamples))
            resampleAccumulator.removeFirst(Self.hopSamples)
            processHop(hop)
        }
    }

    private func processHop(_ hop: [Float]) {
        // Raw peak before any DSP.
        var peak: Float = 0
        for s in hop {
            let a = abs(s)
            if a > peak { peak = a }
        }
        rawPeak = peak

        let env = hop.withUnsafeBufferPointer { ptr -> Float in
            follower.process(ptr.baseAddress!, count: ptr.count)
        }
        envelope = env

        // Beat detection on the raw peak.
        let nowMs = Int64(Date().timeIntervalSince1970 * 1000)
        let gotBeat = beatDetector.process(peak: peak, nowMs: nowMs)
        if gotBeat {
            beatPulse = 1
            beatCount += 1
            bpm = beatDetector.bpm
        } else {
            beatPulse = max(0, beatPulse * 0.717)  // ~150 ms tau per 50 ms hop
        }

        // Master output blends smoothed envelope with beat pulse.
        let drive = max(env, beatPulse)
        let masterByte = follower.toMaster(drive)
        master = Float(masterByte) / 255

        // UDP push, throttled to ~20 Hz.
        if nowMs - lastSendAtMs >= Self.sendIntervalMs {
            lastSendAtMs = nowMs
            udp.sendAutoBrightness(master: UInt8(masterByte),
                                   beatPulse: gotBeat || beatPulse > 0.5,
                                   micGated: true)
        }
    }
}
