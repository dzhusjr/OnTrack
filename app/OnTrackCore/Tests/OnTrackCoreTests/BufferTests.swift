import XCTest
@testable import OnTrackCore

final class BufferTests: XCTestCase {
    func testBackoffDoublesAndCaps() {
        var b = Backoff()
        XCTAssertEqual([b.next(), b.next(), b.next(), b.next(), b.next(), b.next()], [0.1, 0.2, 0.4, 0.8, 1.6, 2.0])
        b.reset()
        XCTAssertEqual(b.next(), 0.1)
    }

    func testRingBufferKeepsNewestTenSeconds() {
        var r = AudioRingBuffer(maxSeconds: 10)
        for i in 0..<1200 { r.append(Data([UInt8(i % 256)]), channel: .mic) }  // 24 s of 20 ms frames
        XCTAssertEqual(r.count, 500)
        let drained = r.drain()
        XCTAssertEqual(drained.first, Data([UInt8(700 % 256)]))
        XCTAssertEqual(r.count, 0)
    }

    func testRingBufferPerChannelBudget() {
        var r = AudioRingBuffer(maxSeconds: 1)
        for _ in 0..<60 { r.append(Data([1]), channel: .mic); r.append(Data([2]), channel: .system) }
        XCTAssertEqual(r.count, 100)
    }
}

final class AudioGateTests: XCTestCase {  // review M2 #1
    func testFramesHeldUntilSessionConfirmedThenReleasedInOrder() {
        var g = AudioGate()
        g.hold()
        XCTAssertNil(g.offer(Data([1]), channel: .mic, connected: true))
        XCTAssertNil(g.offer(Data([2]), channel: .system, connected: true))
        XCTAssertEqual(g.release(), [Data([1]), Data([2])])
        XCTAssertEqual(g.offer(Data([3]), channel: .mic, connected: true), Data([3]))
    }

    func testDisconnectedFramesBufferedAndKeptUntilRelease() {
        var g = AudioGate()
        XCTAssertNil(g.offer(Data([1]), channel: .mic, connected: false))
        g.hold()  // reconnected, waiting for the engine to resume the session
        XCTAssertNil(g.offer(Data([2]), channel: .mic, connected: true))
        XCTAssertEqual(g.release(), [Data([1]), Data([2])])
    }
}
