import AVFoundation
import CoreMedia
import OnTrackCore
import ScreenCaptureKit

/// Captures the microphone (AVAudioEngine + Apple echo cancellation) and system audio (ScreenCaptureKit),
/// both delivered as 16 kHz mono 20 ms frames with host-clock timestamps (spec §4.1 CaptureService).
final class CaptureService: NSObject, SCStreamOutput, SCStreamDelegate {
    /// Called on capture threads.
    var onFrame: ((AudioChannel, UInt64, [Float]) -> Void)?
    /// Human-readable problems (permissions, device changes). Called on the main queue.
    var onIssue: ((String) -> Void)?

    var echoCancellation = true
    private(set) var isRunning = false

    private var engine = AVAudioEngine()
    private var micResampler: Resampler16k?
    private var micChunker = FrameChunker()
    private let micLock = NSLock()
    private var configObserver: NSObjectProtocol?
    private var silentMicFrames = 0
    private var voiceProcessingOn = false
    private var silenceReported = false
    private var ignoreConfigChangesUntil = Date.distantPast

    private var stream: SCStream?
    private var sysResampler: Resampler16k?
    private var sysChunker = FrameChunker()
    private let sysQueue = DispatchQueue(label: "ontrack.system-audio")
    private var wantSystem = false
    private var systemBackoff = Backoff(initial: 1, cap: 30)
    private var systemIssueReported = false

    // MARK: lifecycle

    func start(micOnly: Bool = false) {
        guard !isRunning else { return }
        isRunning = true
        startMic()
        wantSystem = !micOnly
        if wantSystem { Task { await startSystem() } }
    }

    func stop() {
        guard isRunning else { return }
        isRunning = false
        wantSystem = false
        stopMic()
        if let s = stream {
            stream = nil
            Task { try? await s.stopCapture() }
        }
        sysQueue.async { self.sysChunker.reset() }
    }

    // MARK: microphone

    private func startMic(voiceProcessing: Bool? = nil) {
        if AVCaptureDevice.authorizationStatus(for: .audio) != .authorized {
            report("Microphone access is off. Allow OnTrack in System Settings → Privacy & Security → Microphone, then restart OnTrack.")
        }
        engine = AVAudioEngine()
        let input = engine.inputNode
        ignoreConfigChangesUntil = Date().addingTimeInterval(1.5)  // enabling voice processing itself posts a change
        voiceProcessingOn = false
        silentMicFrames = 0
        if voiceProcessing ?? echoCancellation {
            do {
                try input.setVoiceProcessingEnabled(true)
                voiceProcessingOn = true
                // Don't turn down Zoom/Meet/music while we listen.
                input.voiceProcessingOtherAudioDuckingConfiguration =
                    AVAudioVoiceProcessingOtherAudioDuckingConfiguration(enableAdvancedDucking: false, duckingLevel: .min)
            } catch {
                report("Echo cancellation unavailable (\(error.localizedDescription)); continuing without it.")
            }
        }
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else {
            report("No microphone input available.")
            return
        }
        // Voice processing is an I/O unit: the output side must be part of the running graph,
        // otherwise macOS delivers all-zero input buffers.
        if voiceProcessingOn {
            engine.mainMixerNode.outputVolume = 0
        }
        micResampler = Resampler16k(from: Resampler16k.firstChannelFormat(of: format))
        micLock.lock()
        micChunker.reset()
        micLock.unlock()
        input.installTap(onBus: 0, bufferSize: 4096, format: format) { [weak self] buffer, time in
            self?.handleMic(buffer, time)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            if voiceProcessingOn {
                NSLog("OnTrack: mic failed with voice processing (%@). Retrying without", error.localizedDescription)
                input.removeTap(onBus: 0)
                engine.stop()
                startMic(voiceProcessing: false)
                return
            }
            report("Microphone failed to start: \(error.localizedDescription)")
            return
        }
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            guard let self, self.isRunning, Date() > self.ignoreConfigChangesUntil else { return }
            self.report("[audio source changed]")
            self.stopMic()
            self.startMic()
        }
    }

    private func stopMic() {
        if let o = configObserver { NotificationCenter.default.removeObserver(o) }
        configObserver = nil
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }

    private func handleMic(_ buffer: AVAudioPCMBuffer, _ time: AVAudioTime) {
        guard let resampler = micResampler, let mono = Resampler16k.firstChannel(of: buffer) else { return }
        let samples = resampler.convert(mono)
        checkSilence(samples)
        let host = time.isHostTimeValid ? Self.ns(hostTime: time.hostTime) : Self.nowNs()
        micLock.lock()
        let frames = micChunker.append(samples, hostTimeNs: host)
        micLock.unlock()
        for f in frames { onFrame?(.mic, f.tNs, f.samples) }
    }

    /// All-zero mic input for 3 s means the capture path is broken (not a quiet room, which still has noise).
    private func checkSilence(_ samples: [Float]) {
        guard !samples.isEmpty else { return }
        if samples.contains(where: { $0 != 0 }) {
            silentMicFrames = 0
            return
        }
        silentMicFrames += samples.count
        guard silentMicFrames > 3 * 16_000 else { return }
        silentMicFrames = 0
        if voiceProcessingOn {
            NSLog("OnTrack: mic delivers digital silence with voice processing. Restarting without echo cancellation")
            DispatchQueue.main.async { [weak self] in
                guard let self, self.isRunning else { return }
                self.stopMic()
                self.startMic(voiceProcessing: false)
                self.report("Echo cancellation was turned off: this Mac's mic returned silence with it on. Use headphones on calls.")
            }
        } else if !silenceReported {
            silenceReported = true
            report("The microphone is delivering silence. Check System Settings → Privacy & Security → Microphone (OnTrack on) and Sound → Input.")
        }
    }

    // MARK: system audio

    private func startSystem() async {
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first else {
                report("System audio: no display found.")
                return
            }
            let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
            let cfg = SCStreamConfiguration()
            cfg.capturesAudio = true
            cfg.excludesCurrentProcessAudio = true
            cfg.sampleRate = 48_000
            cfg.channelCount = 2
            cfg.width = 2  // video is required by the API but unused
            cfg.height = 2
            cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            let s = SCStream(filter: filter, configuration: cfg, delegate: self)
            try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: sysQueue)
            try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: sysQueue)
            try await s.startCapture()
            guard wantSystem else {
                try? await s.stopCapture()
                return
            }
            stream = s
            systemBackoff.reset()
            systemIssueReported = false
        } catch {
            if !systemIssueReported {
                systemIssueReported = true
                report("System audio unavailable. Allow OnTrack in System Settings → Privacy & Security → Screen & System Audio Recording. (\(error.localizedDescription))")
            }
            scheduleSystemRetry()
        }
    }

    private func scheduleSystemRetry() {
        let delay = systemBackoff.next()
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
            guard let self, self.isRunning, self.wantSystem, self.stream == nil else { return }
            Task { await self.startSystem() }
        }
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid, let pcm = Self.pcmBuffer(from: sampleBuffer) else { return }
        if sysResampler == nil || sysResampler?.inputFormat != pcm.format {
            sysResampler = Resampler16k(from: pcm.format)
        }
        guard let resampler = sysResampler else { return }
        let samples = resampler.convert(pcm)
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let host = pts.isValid ? UInt64(max(0, pts.seconds) * 1e9) : Self.nowNs()
        for f in sysChunker.append(samples, hostTimeNs: host) { onFrame?(.system, f.tNs, f.samples) }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        NSLog("OnTrack: system audio stopped: %@. Retrying", error.localizedDescription)
        DispatchQueue.main.async { [weak self] in
            self?.stream = nil
            self?.scheduleSystemRetry()
        }
    }

    // MARK: helpers

    private func report(_ text: String) {
        DispatchQueue.main.async { self.onIssue?(text) }
    }

    static func ns(hostTime: UInt64) -> UInt64 { UInt64(AVAudioTime.seconds(forHostTime: hostTime) * 1e9) }
    static func nowNs() -> UInt64 { ns(hostTime: mach_absolute_time()) }

    static func pcmBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
        guard let desc = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc),
              let format = AVAudioFormat(streamDescription: asbd)
        else { return nil }
        let frames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return nil }
        buffer.frameLength = frames
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer, at: 0, frameCount: Int32(frames), into: buffer.mutableAudioBufferList
        )
        return status == noErr ? buffer : nil
    }
}
