import XCTest
@testable import OnTrackCore

final class FrameEncoderTests: XCTestCase {
    func testHeaderLayoutMatchesSpec() {
        let d = FrameEncoder.encode(channel: .system, tNs: 1, samples: [0, 0])
        XCTAssertEqual(d.count, 16 + 8)
        XCTAssertEqual([UInt8](d.prefix(16)), [0x54, 0x4F, 0x01, 0x00, 1, 0, 0, 0, 0, 0, 0, 0, 2, 0, 0, 0])
    }

    func testMatchesPythonGoldenFile() throws {
        let a = FrameEncoder.encode(channel: .mic, tNs: 0, samples: [Float](repeating: 0.5, count: 320))
        let ramp = (0..<320).map { Float(-1.0 + 2.0 * Double($0) / 319.0) }
        let b = FrameEncoder.encode(channel: .system, tNs: 20_000_000, samples: ramp)
        let golden = try Golden.data("audio_frames.bin")
        XCTAssertEqual(golden.count, a.count + b.count)
        XCTAssertEqual(golden.prefix(a.count), a)
        // numpy.linspace and this ramp may differ in the last float bit; compare header + samples with tolerance
        let gb = golden.suffix(b.count)
        XCTAssertEqual([UInt8](gb.prefix(16)), [UInt8](b.prefix(16)))
        let gs = gb.dropFirst(16).withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
        for (x, y) in zip(gs, ramp) { XCTAssertEqual(x, y, accuracy: 1e-6) }
    }
}
