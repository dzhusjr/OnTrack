import Foundation

/// Turns arbitrary-size 16 kHz mono buffers into 320-sample (20 ms) frames.
/// Timestamps count samples from an anchor in host time; re-anchors when the device clock drifts > 200 ms
/// from host time (stall, device switch) so the engine sees the real gap.
public struct FrameChunker {
    public let frameSamples: Int
    public let sampleRate: Double
    public let maxDriftNs: UInt64

    private var pending: [Float] = []
    private var anchorNs: UInt64?
    private var received: UInt64 = 0  // samples received since anchor
    private var emitted: UInt64 = 0  // samples emitted since anchor

    public init(frameSamples: Int = 320, sampleRate: Double = 16_000, maxDriftNs: UInt64 = 200_000_000) {
        self.frameSamples = frameSamples
        self.sampleRate = sampleRate
        self.maxDriftNs = maxDriftNs
    }

    private func ns(_ samples: UInt64) -> UInt64 { UInt64((Double(samples) * 1e9 / sampleRate).rounded()) }

    public mutating func append(_ samples: [Float], hostTimeNs: UInt64) -> [(tNs: UInt64, samples: [Float])] {
        if let anchor = anchorNs {
            let expected = anchor + ns(received)
            let drift = hostTimeNs > expected ? hostTimeNs - expected : expected - hostTimeNs
            if drift > maxDriftNs { reset() }
        }
        if anchorNs == nil {
            anchorNs = hostTimeNs
        }
        pending.append(contentsOf: samples)
        received += UInt64(samples.count)
        var out: [(tNs: UInt64, samples: [Float])] = []
        var start = 0
        while pending.count - start >= frameSamples {
            out.append((anchorNs! + ns(emitted), Array(pending[start..<start + frameSamples])))
            emitted += UInt64(frameSamples)
            start += frameSamples
        }
        if start > 0 { pending.removeFirst(start) }
        return out
    }

    public mutating func reset() {
        pending.removeAll()
        anchorNs = nil
        received = 0
        emitted = 0
    }
}
