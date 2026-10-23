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
    private var ring = AudioRingBuffer(maxSeconds: 10)
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
            if let a = self.audio, a.writeAll(data) { return }
            self.ring.append(data, channel: channel)
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
        for frame in ring.drain() { _ = a.writeAll(frame) }
        startReader(c)
        DispatchQueue.main.async {
            self.isConnected = true
            self.onConnectionChange?(true)
        }
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
