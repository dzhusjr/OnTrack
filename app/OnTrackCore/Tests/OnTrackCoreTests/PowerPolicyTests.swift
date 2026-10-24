import XCTest
@testable import OnTrackCore

final class PowerPolicyTests: XCTestCase {
    func testDefersHeavyWorkOnlyOnBatteryWhenEnabled() {
        XCTAssertTrue(PowerPolicy.deferFinalize(.saveAll, onBattery: true, waitForPower: true))
        XCTAssertTrue(PowerPolicy.deferFinalize(.transcriptOnly, onBattery: true, waitForPower: true))
        XCTAssertFalse(PowerPolicy.deferFinalize(.saveAll, onBattery: false, waitForPower: true))
        XCTAssertFalse(PowerPolicy.deferFinalize(.saveAll, onBattery: true, waitForPower: false))
    }

    func testDiscardIsNeverDeferred() {
        XCTAssertFalse(PowerPolicy.deferFinalize(.discard, onBattery: true, waitForPower: true))
    }

    func testQueueRoundTripAndOrder() throws {
        var q = FinalizeQueue()
        q.add(sessionId: "a", action: .saveAll)
        q.add(sessionId: "b", action: .transcriptOnly)
        q.add(sessionId: "a", action: .saveAll)  // no duplicates
        XCTAssertEqual(q.items.map(\.sessionId), ["a", "b"])
        let restored = try FinalizeQueue(json: q.json())
        XCTAssertEqual(restored, q)
        XCTAssertEqual(restored.next?.sessionId, "a")
        var r = restored
        r.remove(sessionId: "a")
        XCTAssertEqual(r.next, FinalizeQueue.Item(sessionId: "b", action: .transcriptOnly))
    }

    func testCorruptStoredQueueIsEmpty() {
        XCTAssertEqual(FinalizeQueue(jsonOrEmpty: Data("nonsense".utf8)).items, [])
    }
}

final class DeferredSaveTests: XCTestCase {
    func testDeferredSaveClosesTheCardWithANote() {
        var s = OverlayState()
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 600, lineCount: 50)))
        reduce(&s, .deferredUntilPower)
        XCTAssertNil(s.endCard)
        XCTAssertEqual(s.mode, .hidden)
        XCTAssertEqual(s.toast, "Saved — transcription will finish when your Mac is plugged in")
    }
}
