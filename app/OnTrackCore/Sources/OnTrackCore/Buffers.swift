import Foundation

/// Reconnect delays: 100 ms doubling to a 2 s cap (spec §4.1 EngineClient).
public struct Backoff {
    private var current: Double = 0
    public let initial: Double
    public let cap: Double

    public init(initial: Double = 0.1, cap: Double = 2.0) {
        self.initial = initial
        self.cap = cap
    }

    public mutating func next() -> Double {
        current = current == 0 ? initial : min(cap, current * 2)
        return (current * 1000).rounded() / 1000
    }

    public mutating func reset() { current = 0 }
}

/// Encoded audio frames kept while the engine is unreachable: newest `maxSeconds` per channel (20 ms frames).
public struct AudioRingBuffer {
    private var frames: [(AudioChannel, Data)] = []
    private var perChannel: [AudioChannel: Int] = [:]
    public let maxFramesPerChannel: Int

    public init(maxSeconds: Double = 10) {
        maxFramesPerChannel = Int(maxSeconds / 0.02)
    }

    public var count: Int { frames.count }

    public mutating func append(_ frame: Data, channel: AudioChannel) {
        frames.append((channel, frame))
        perChannel[channel, default: 0] += 1
        if perChannel[channel, default: 0] > maxFramesPerChannel,
           let i = frames.firstIndex(where: { $0.0 == channel }) {
            frames.remove(at: i)
            perChannel[channel, default: 1] -= 1
        }
    }

    public mutating func drain() -> [Data] {
        defer {
            frames.removeAll()
            perChannel.removeAll()
        }
        return frames.map(\.1)
    }
}
