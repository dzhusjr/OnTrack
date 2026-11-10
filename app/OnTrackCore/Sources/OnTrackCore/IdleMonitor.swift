import Foundation

/// Knows how long a recording has been quiet, so a forgotten session can stop itself.
/// Live transcription on: quiet means no recognised speech. Off: nothing was transcribed, so quiet means
/// no sound above room noise on either channel. Thread-safe: audio frames arrive on audio threads.
public final class IdleMonitor: @unchecked Sendable {
    /// RMS level (about -40 dBFS) that counts as sound. Speech is far above it, room noise is below.
    public static let soundThreshold: Float = 0.01
    /// The choices offered in Settings. 0 = off.
    public static let choices = [0, 10, 15, 30, 60]

    private let lock = NSLock()
    private var lastSound: Date
    private var lastSpeech: Date

    public init(now: Date = Date()) {
        lastSound = now
        lastSpeech = now
    }

    public static func rms(_ samples: [Float]) -> Float {
        guard !samples.isEmpty else { return 0 }
        return (samples.reduce(0) { $0 + $1 * $1 } / Float(samples.count)).squareRoot()
    }

    /// Start counting from now: a new session, a resume from pause, live switched on or off.
    public func reset(now: Date = Date()) {
        lock.lock(); defer { lock.unlock() }
        lastSound = now
        lastSpeech = now
    }

    public func noteAudio(_ samples: [Float], now: Date = Date()) {
        guard Self.rms(samples) >= Self.soundThreshold else { return }
        lock.lock(); defer { lock.unlock() }
        lastSound = now
    }

    public func noteSpeech(now: Date = Date()) {
        lock.lock(); defer { lock.unlock() }
        lastSpeech = now
    }

    public func idleSeconds(live: Bool, now: Date = Date()) -> TimeInterval {
        lock.lock(); defer { lock.unlock() }
        return now.timeIntervalSince(live ? lastSpeech : lastSound)
    }

    public func shouldStop(minutes: Int, live: Bool, paused: Bool, now: Date = Date()) -> Bool {
        guard minutes > 0, !paused else { return false }
        return idleSeconds(live: live, now: now) >= Double(minutes) * 60
    }
}
