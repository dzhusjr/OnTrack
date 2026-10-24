import XCTest
@testable import OnTrackCore

final class FrameChunkerTests: XCTestCase {
    func testChunksInto320SampleFramesWithSampleCountTimestamps() {
        var c = FrameChunker()
        var out = c.append([Float](repeating: 0.1, count: 500), hostTimeNs: 1_000_000_000)
        XCTAssertEqual(out.count, 1)
        XCTAssertEqual(out[0].tNs, 1_000_000_000)
        out = c.append([Float](repeating: 0.1, count: 500), hostTimeNs: 1_000_000_000 + 31_250_000)
        XCTAssertEqual(out.map(\.tNs), [1_020_000_000, 1_040_000_000])
        XCTAssertTrue(out.allSatisfy { $0.samples.count == 320 })
    }

    func testSmallJitterDoesNotReanchor() {
        var c = FrameChunker()
        _ = c.append([Float](repeating: 0, count: 320), hostTimeNs: 0)
        let out = c.append([Float](repeating: 0, count: 320), hostTimeNs: 20_000_000 + 50_000_000)  // 50 ms late
        XCTAssertEqual(out.first?.tNs, 20_000_000)
    }

    func testLargeDriftReanchorsToHostTime() {
        var c = FrameChunker()
        _ = c.append([Float](repeating: 0, count: 400), hostTimeNs: 0)  // 80 samples pending
        let out = c.append([Float](repeating: 0, count: 320), hostTimeNs: 5_000_000_000)  // device stalled 5 s
        XCTAssertEqual(out.map(\.tNs), [5_000_000_000])
    }
}
