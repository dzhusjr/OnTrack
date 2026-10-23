import AVFoundation

/// Converts any PCM format to 16 kHz mono float32 (downmixing multi-channel input).
final class Resampler16k {
    static let outputFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!

    let inputFormat: AVAudioFormat
    private let converter: AVAudioConverter

    init?(from format: AVAudioFormat) {
        guard let c = AVAudioConverter(from: format, to: Resampler16k.outputFormat) else { return nil }
        c.downmix = true
        inputFormat = format
        converter = c
    }

    /// Mono float32 format at the input's sample rate (used to take channel 0 of multi-channel input,
    /// e.g. the voice-processing unit, instead of downmixing reference channels into it).
    static func firstChannelFormat(of format: AVAudioFormat) -> AVAudioFormat {
        AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: format.sampleRate, channels: 1, interleaved: false)!
    }

    static func firstChannel(of buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        let fmt = firstChannelFormat(of: buffer.format)
        guard let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: buffer.frameLength),
              let dst = out.floatChannelData?[0] else { return nil }
        out.frameLength = buffer.frameLength
        let n = Int(buffer.frameLength)
        if let src = buffer.floatChannelData {
            let stride = buffer.stride  // >1 when interleaved
            for i in 0..<n { dst[i] = src[0][i * stride] }
        } else if let src = buffer.int16ChannelData {
            let stride = buffer.stride
            for i in 0..<n { dst[i] = Float(src[0][i * stride]) / 32768 }
        } else if let src = buffer.int32ChannelData {
            let stride = buffer.stride
            for i in 0..<n { dst[i] = Float(src[0][i * stride]) / 2_147_483_648 }
        } else {
            return nil
        }
        return out
    }

    func convert(_ input: AVAudioPCMBuffer) -> [Float] {
        let ratio = Resampler16k.outputFormat.sampleRate / input.format.sampleRate
        let capacity = AVAudioFrameCount(Double(input.frameLength) * ratio + 64)
        guard let out = AVAudioPCMBuffer(pcmFormat: Resampler16k.outputFormat, frameCapacity: capacity) else { return [] }
        var consumed = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, inputStatus in
            if consumed {
                inputStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            inputStatus.pointee = .haveData
            return input
        }
        guard status != .error, let channel = out.floatChannelData else { return [] }
        return Array(UnsafeBufferPointer(start: channel[0], count: Int(out.frameLength)))
    }
}
