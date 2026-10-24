import XCTest
@testable import OnTrackCore

final class OverlayReducerTests: XCTestCase {
    private func line(_ seq: Int, _ text: String, session: String = "s1") -> OverlayEvent {
        .engine(.line(TranscriptLine(sessionId: session, seq: seq, speaker: "Me", tStart: Double(seq), tEnd: Double(seq) + 1, lang: "en", text: text)))
    }

    private func recording(_ s: inout OverlayState, _ id: String = "s1") {
        _ = reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: id, degraded: false))))
    }

    func testRecordingShowsCollapsedAndKeepsLastNLines() {
        var s = OverlayState(maxLines: 3)
        recording(&s)
        XCTAssertEqual(s.mode, .collapsed)
        for i in 0..<5 { _ = reduce(&s, line(i, "t\(i)")) }
        XCTAssertEqual(s.lines.map(\.text), ["t2", "t3", "t4"])
    }

    func testDuplicateSeqIgnored() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, line(0, "a"))
        _ = reduce(&s, line(1, "b"))
        _ = reduce(&s, line(1, "b"))
        _ = reduce(&s, line(0, "a"))
        XCTAssertEqual(s.lines.map(\.text), ["a", "b"])
    }

    func testLinesFromOtherSessionIgnored() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, line(0, "x", session: "old"))
        XCTAssertTrue(s.lines.isEmpty)
    }

    func testToggleCycle() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, .toggle)
        XCTAssertEqual(s.mode, .expanded)
        _ = reduce(&s, .toggle)  // empty question → hide
        XCTAssertEqual(s.mode, .hidden)
        _ = reduce(&s, .toggle)
        XCTAssertEqual(s.mode, .collapsed)
    }

    func testAskStreamsAnswer() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, .toggle)
        _ = reduce(&s, .questionChanged("When is the deadline?"))
        let cmds = reduce(&s, .submitQuestion(id: "q1"))
        XCTAssertEqual(cmds, [.ask(id: "q1", question: "When is the deadline?")])
        XCTAssertTrue(s.asking)
        XCTAssertEqual(s.question, "")
        _ = reduce(&s, .engine(.answerDelta(id: "q1", text: "It is ")))
        _ = reduce(&s, .engine(.answerDelta(id: "zz", text: "ignored")))
        _ = reduce(&s, .engine(.answerDelta(id: "q1", text: "Friday")))
        _ = reduce(&s, .engine(.answerDone(id: "q1")))
        XCTAssertEqual(s.answer, "It is Friday")
        XCTAssertFalse(s.asking)
        XCTAssertEqual(reduce(&s, .submitQuestion(id: "q2")), [])  // empty question
    }

    func testEndOfSessionSaveFlow() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 2820, lineCount: 312)))
        XCTAssertEqual(s.mode, .endCard)
        XCTAssertEqual(s.endCard, EndCard(sessionId: "s1", durationS: 2820, lineCount: 312, confirmDiscard: false))
        XCTAssertEqual(reduce(&s, .choose(.saveAll)), [.finalize(sessionId: "s1", action: .saveAll)])
        XCTAssertEqual(s.mode, .finalizing)
        _ = reduce(&s, .engine(.finalizeProgress(sessionId: "s1", stage: "summarizing", pct: 0.7)))
        XCTAssertEqual(s.progress, FinalizeProgress(stage: "summarizing", pct: 0.7))
        _ = reduce(&s, .engine(.finalizeDone(sessionId: "s1", notesPath: "/n/s1")))
        XCTAssertEqual(s.mode, .done)
        XCTAssertEqual(s.notesPath, "/n/s1")
        _ = reduce(&s, .dismiss)
        XCTAssertEqual(s.mode, .hidden)
    }

    func testDiscardNeedsSecondClick() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 10, lineCount: 1)))
        XCTAssertEqual(reduce(&s, .choose(.discard)), [])
        XCTAssertEqual(s.endCard?.confirmDiscard, true)
        XCTAssertEqual(reduce(&s, .choose(.discard)), [.finalize(sessionId: "s1", action: .discard)])
    }

    func testErrorShownAndCleared() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, .engine(.error(EngineError(code: "llm_missing", message: "Claude Code CLI not found", recoverable: true))))
        XCTAssertEqual(s.error, "Claude Code CLI not found")
        _ = reduce(&s, .clearError)
        XCTAssertNil(s.error)
    }

    func testNewSessionClearsOldLines() {
        var s = OverlayState()
        recording(&s, "s1")
        _ = reduce(&s, line(0, "old"))
        _ = reduce(&s, .engine(.status(EngineStatus(state: "idle", modelReady: true, sessionId: nil, degraded: false))))
        recording(&s, "s2")
        XCTAssertTrue(s.lines.isEmpty)
        _ = reduce(&s, line(0, "new", session: "s2"))
        XCTAssertEqual(s.lines.map(\.text), ["new"])
    }
}

final class OverlayReducerReviewTests: XCTestCase {
    private func recording(_ s: inout OverlayState, _ id: String) {
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: id, degraded: false))))
    }

    func testDisconnectWhileFinalizingReturnsToEndCardWithError() {  // review M2 #7
        var s = OverlayState()
        recording(&s, "s1")
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 60, lineCount: 3)))
        reduce(&s, .choose(.saveAll))
        XCTAssertEqual(s.mode, .finalizing)
        reduce(&s, .disconnected)
        XCTAssertEqual(s.mode, .endCard)
        XCTAssertNotNil(s.error)
        XCTAssertEqual(reduce(&s, .choose(.saveAll)), [.finalize(sessionId: "s1", action: .saveAll)])  // retry possible
    }

    func testPreviousSessionSavedWhileRecordingNextKeepsLiveView() {  // review M2 #6
        var s = OverlayState()
        recording(&s, "s2")
        reduce(&s, .engine(.finalizeDone(sessionId: "s1", notesPath: "/notes/s1")))
        XCTAssertEqual(s.mode, .collapsed)
        XCTAssertEqual(s.toast, "Previous session saved to notes")
    }
}

final class LivePreviewTests: XCTestCase {
    private func start(_ s: inout OverlayState) {
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
    }

    func testPartialShownThenClearedByMatchingUtterance() {
        var s = OverlayState(maxLines: 3)
        start(&s)
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 4.0, text: "so the")))
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 4.0, text: "so the eigenvalues")))
        XCTAssertEqual(s.partials.map(\.text), ["so the eigenvalues"])
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 2.0, text: "")))  // older utterance: keep preview
        XCTAssertEqual(s.partials.count, 1)
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 4.0, text: "")))
        XCTAssertTrue(s.partials.isEmpty)
    }

    func testPartialsPerChannelAndOtherSessionIgnored() {
        var s = OverlayState()
        start(&s)
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 1, text: "me talking")))
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 1, tStart: 1.5, text: "zoom talking")))
        reduce(&s, .engine(.partial(sessionId: "old", channel: 0, tStart: 9, text: "stale")))
        XCTAssertEqual(s.partials.map(\.text), ["me talking", "zoom talking"])
    }

    func testVisibleLinesLeaveRoomForPreview() {
        var s = OverlayState(maxLines: 3)
        start(&s)
        for i in 0..<3 {
            reduce(&s, .engine(.line(TranscriptLine(sessionId: "s1", seq: i, speaker: "Me", tStart: Double(i), tEnd: Double(i) + 1, lang: "en", text: "l\(i)"))))
        }
        XCTAssertEqual(s.visibleLines.map(\.text), ["l0", "l1", "l2"])
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 5, text: "live")))
        XCTAssertEqual(s.visibleLines.map(\.text), ["l1", "l2"])  // 2 final + 1 preview = 3 rows
    }

    func testStoppingClearsPreviews() {
        var s = OverlayState()
        start(&s)
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 1, text: "half a sen")))
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 5, lineCount: 0)))
        XCTAssertTrue(s.partials.isEmpty)
    }
}

final class FinalizeWarningTests: XCTestCase {
    func testWarningsDuringSaveDoNotReopenTheEndCard() {
        var s = OverlayState()
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 180, lineCount: 39)))
        reduce(&s, .choose(.saveAll))
        reduce(&s, .engine(.error(EngineError(code: "diarization_unavailable", message: "403", recoverable: true))))
        XCTAssertEqual(s.mode, .finalizing)  // saving continues; the warning is only shown
        XCTAssertEqual(s.error, "403")
        reduce(&s, .engine(.error(EngineError(code: "finalize_failed", message: "disk full", recoverable: true))))
        XCTAssertEqual(s.mode, .endCard)  // a real failure lets you retry
    }
}

final class HideOverlayTests: XCTestCase {
    private func rec(_ s: inout OverlayState) {
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
    }

    func testEscapeAndHideButtonHideTheLiveBar() {
        var s = OverlayState()
        rec(&s)
        XCTAssertEqual(s.mode, .collapsed)
        reduce(&s, .escape)
        XCTAssertEqual(s.mode, .hidden)
        reduce(&s, .toggle)
        reduce(&s, .hide)
        XCTAssertEqual(s.mode, .hidden)
        XCTAssertTrue(s.recording)  // hiding never stops recording
    }

    func testNoAutoShowWhenDisabled() {
        var s = OverlayState()
        s.autoShow = false
        rec(&s)
        XCTAssertEqual(s.mode, .hidden)
        reduce(&s, .toggle)
        XCTAssertEqual(s.mode, .collapsed)
    }

    func testEndCardStillAppearsWhenHidden() {
        var s = OverlayState()
        s.autoShow = false
        rec(&s)
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 60, lineCount: 4)))
        XCTAssertEqual(s.mode, .endCard)
    }
}
