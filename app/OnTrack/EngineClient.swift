import Foundation
import OnTrackCore

/// Talks to ontrack-engine over audio.sock (binary frames) and control.sock (NDJSON).
/// Reconnects with backoff; keeps up to 10 s of audio per channel while disconnected (spec §4.1).
final class EngineClient {
    var onMessage: ((Inbound) -> Void)?  // main queue
    var onConnectionChange: ((Bool) -> Void)?  // main queue

    private let controlPath: String
    private let audioPath: String
    private let queue = DispatchQueue(label: "ontrack.engine-client")
    private var control: UnixSocket?
    private var audio: UnixSocket?
    private var gate = AudioGate(maxSeconds: 10)
    private var backoff = Backoff()
    private var running = false
    private var connectScheduled = false
    private(set) var isConnected = false  // main-queue view

    init(directory: URL) {
        controlPath = directory.appendingPathComponent("control.sock").path
        audioPath = directory.appendingPathComponent("audio.sock").path
    }

    func start() {
        queue.async {
            self.running = true
            self.connect()
        }
    }

    func stop() {
        queue.sync {
            running = false
            control?.close()
            audio?.close()
            control = nil
            audio = nil
        }
    }

    func send(_ message: Outbound) {
        let data = message.encoded()
        queue.async {
            guard let c = self.control, c.writeAll(data) else {
                NSLog("OnTrack: dropped %@ (engine not connected)", String(decoding: data, as: UTF8.self))
                return
            }
        }
    }

    func sendAudio(channel: AudioChannel, tNs: UInt64, samples: [Float]) {
        let data = FrameEncoder.encode(channel: channel, tNs: tNs, samples: samples)
        queue.async {
            guard let now = self.gate.offer(data, channel: channel, connected: self.audio != nil) else { return }
            if let a = self.audio, a.writeAll(now) { return }
            self.gate.requeue(now, channel: channel)
        }
    }

    /// Buffer audio (≤ 10 s) until `releaseAudio()` — used until the engine confirms the session is recording.
    func holdAudio() {
        queue.async { self.gate.hold() }
    }

    /// Engine is recording: send buffered audio, then stream live.
    func releaseAudio() {
        queue.async {
            let frames = self.gate.release()
            guard let a = self.audio else {
                frames.forEach { self.gate.requeue($0, channel: .mic) }
                return
            }
            for f in frames where !a.writeAll(f) { break }
        }
    }

    // MARK: - connection management (on `queue`)

    private func connect() {
        connectScheduled = false
        guard running, control == nil else { return }
        guard let c = UnixSocket.connect(path: controlPath) else { return scheduleReconnect() }
        guard let a = UnixSocket.connect(path: audioPath) else {
            c.close()
            return scheduleReconnect()
        }
        control = c
        audio = a
        backoff.reset()
        gate.hold()  // a fresh engine is idle: keep buffered audio until it confirms the session
        // Queue the connection callback BEFORE starting the reader so it runs before any engine message.
        DispatchQueue.main.async {
            self.isConnected = true
            self.onConnectionChange?(true)
        }
        startReader(c)
    }

    private func scheduleReconnect() {
        guard running, !connectScheduled else { return }
        connectScheduled = true
        queue.asyncAfter(deadline: .now() + backoff.next()) { self.connect() }
    }

    private func startReader(_ socket: UnixSocket) {
        let thread = Thread { [weak self] in
            var decoder = NdjsonDecoder()
            while let data = socket.read() {
                for line in decoder.feed(data) {
                    do {
                        let msg = try Inbound.decode(line)
                        DispatchQueue.main.async { self?.onMessage?(msg) }
                    } catch {
                        NSLog("OnTrack: undecodable engine message: %@", String(decoding: line, as: UTF8.self))
                    }
                }
            }
            self?.queue.async { self?.dropConnection(socket) }
        }
        thread.name = "ontrack.control-reader"
        thread.start()
    }

    private func dropConnection(_ socket: UnixSocket) {
        guard control === socket else { return }
        control?.close()
        audio?.close()
        control = nil
        audio = nil
        DispatchQueue.main.async {
            self.isConnected = false
            self.onConnectionChange?(false)
        }
        scheduleReconnect()
    }
}
