import XCTest
@testable import OnTrackCore

final class IdleMonitorTests: XCTestCase {
    let t0 = Date(timeIntervalSince1970: 1_000_000)
    func at(_ min: Double) -> Date { t0.addingTimeInterval(min * 60) }
    let loud = [Float](repeating: 0.1, count: 480)
    let hiss = [Float](repeating: 0.001, count: 480)

    func testOffNeverStops() {
        let m = IdleMonitor(now: t0)
        XCTAssertFalse(m.shouldStop(minutes: 0, live: true, paused: false, now: at(600)))
    }

    func testLiveOnCountsFromTheLastRecognisedSpeechNotFromSound() {
        let m = IdleMonitor(now: t0)
        m.noteAudio(loud, now: at(14))  // music, a fan, a loud room: not speech
        XCTAssertFalse(m.shouldStop(minutes: 15, live: true, paused: false, now: at(14.9)))
        XCTAssertTrue(m.shouldStop(minutes: 15, live: true, paused: false, now: at(15)))
        m.noteSpeech(now: at(15))
        XCTAssertFalse(m.shouldStop(minutes: 15, live: true, paused: false, now: at(29)))
        XCTAssertTrue(m.shouldStop(minutes: 15, live: true, paused: false, now: at(30)))
    }

    func testLiveOffCountsFromTheLastSoundAndIgnoresRoomNoise() {
        let m = IdleMonitor(now: t0)
        m.noteAudio(hiss, now: at(9))  // below the threshold
        XCTAssertTrue(m.shouldStop(minutes: 10, live: false, paused: false, now: at(10)))
        m.noteAudio(loud, now: at(10))
        XCTAssertFalse(m.shouldStop(minutes: 10, live: false, paused: false, now: at(19.9)))
        XCTAssertTrue(m.shouldStop(minutes: 10, live: false, paused: false, now: at(20)))
    }

    func testPausedNeverStopsAndResetRestartsTheClock() {
        let m = IdleMonitor(now: t0)
        XCTAssertFalse(m.shouldStop(minutes: 10, live: true, paused: true, now: at(60)))
        m.reset(now: at(60))
        XCTAssertFalse(m.shouldStop(minutes: 10, live: true, paused: false, now: at(69)))
        XCTAssertTrue(m.shouldStop(minutes: 10, live: true, paused: false, now: at(70)))
    }

    func testRms() {
        XCTAssertEqual(IdleMonitor.rms([]), 0)
        XCTAssertEqual(IdleMonitor.rms([0.5, -0.5]), 0.5, accuracy: 1e-6)
    }
}
