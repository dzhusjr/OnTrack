import Foundation

public enum AudioChannel: UInt8, Sendable {
    case mic = 0
    case system = 1
}

/// audio.sock wire format (spec §5): little-endian `magic:u16=0x4F54 | channel:u8 | flags:u8 | t_ns:u64 | n:u32 | n×float32`.
public enum FrameEncoder {
    public static let magic: UInt16 = 0x4F54
    public static let headerSize = 16

    public static func encode(channel: AudioChannel, tNs: UInt64, samples: [Float]) -> Data {
        var d = Data(capacity: headerSize + samples.count * 4)
        appendLE(&d, magic)
        d.append(channel.rawValue)
        d.append(0)  // flags
        appendLE(&d, tNs)
        appendLE(&d, UInt32(samples.count))
        for s in samples { appendLE(&d, s.bitPattern) }
        return d
    }

    private static func appendLE<T: FixedWidthInteger>(_ d: inout Data, _ v: T) {
        withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) }
    }
}
