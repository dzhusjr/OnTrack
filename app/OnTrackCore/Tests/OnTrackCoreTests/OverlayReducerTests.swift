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

    func testHidingWhileSavingKeepsSavingAndNeverReshowsChoiceCard() {
        var s = OverlayState()
        recording(&s)
        _ = reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 600, lineCount: 50)))
        _ = reduce(&s, .choose(.saveAll))
        XCTAssertEqual(reduce(&s, .toggle), [])  // ⌥Space: hide only, no engine command
        XCTAssertEqual(s.mode, .hidden)
        _ = reduce(&s, .engine(.finalizeProgress(sessionId: "s1", stage: "retranscribing", pct: 0.3)))
        XCTAssertEqual(s.mode, .hidden)
        _ = reduce(&s, .toggle)  // reopen → progress, not the Save/Discard card
        XCTAssertEqual(s.mode, .finalizing)
        XCTAssertEqual(reduce(&s, .choose(.saveAll)), [])  // Enter can't re-send finalize
        _ = reduce(&s, .toggle)
        _ = reduce(&s, .engine(.finalizeDone(sessionId: "s1", notesPath: "/n/s1")))
        XCTAssertEqual(s.mode, .hidden)  // stays hidden, just a toast
        XCTAssertEqual(s.toast, "Saved to notes")
        XCTAssertNil(s.endCard)
        _ = reduce(&s, .toggle)
        XCTAssertEqual(s.mode, .collapsed)
    }

    func testPauseOnlyWhileRecordingAndFollowsEngine() {
        var s = OverlayState()
        XCTAssertEqual(reduce(&s, .togglePause), [])  // idle: nothing to pause
        recording(&s)
        reduce(&s, .engine(.partial(sessionId: "s1", channel: 0, tStart: 1, text: "hal")))
        XCTAssertEqual(reduce(&s, .togglePause), [.setPaused(true)])
        XCTAssertTrue(s.paused)
        XCTAssertTrue(s.partials.isEmpty)
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false,
                                                paused: true))))
        XCTAssertTrue(s.paused)
        XCTAssertEqual(reduce(&s, .togglePause), [.setPaused(false)])
        XCTAssertFalse(s.paused)
        reduce(&s, .togglePause)
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 100, lineCount: 3)))
        XCTAssertFalse(s.paused)  // a new session never starts paused
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

final class EmptySessionTests: XCTestCase {
    private func rec(_ s: inout OverlayState) {
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
    }

    func testEmptyShortSessionIsDiscardedWithoutACard() {
        var s = OverlayState()
        rec(&s)
        let cmds = reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 1, lineCount: 0)))
        XCTAssertEqual(cmds, [.finalize(sessionId: "s1", action: .discard)])
        XCTAssertNil(s.endCard)
        XCTAssertEqual(s.mode, .hidden)
        XCTAssertEqual(s.toast, "Empty session discarded")
    }

    func testSessionWithLinesStillGetsTheCard() {
        var s = OverlayState()
        rec(&s)
        XCTAssertEqual(reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 2, lineCount: 1))), [])
        XCTAssertEqual(s.mode, .endCard)
    }

    func testLongSilentSessionStillGetsTheCard() {  // 0 lines after minutes: probably a mic problem, let the user decide
        var s = OverlayState()
        rec(&s)
        XCTAssertEqual(reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 300, lineCount: 0))), [])
        XCTAssertEqual(s.mode, .endCard)
    }

    func testDiscardResultDoesNotShowTheDoneCard() {
        var s = OverlayState()
        rec(&s)
        reduce(&s, .engine(.sessionStopped(sessionId: "s1", durationS: 1, lineCount: 0)))
        reduce(&s, .engine(.finalizeDone(sessionId: "s1", notesPath: "")))
        XCTAssertEqual(s.mode, .hidden)
    }
}

final class LiveToggleTests: XCTestCase {
    private func rec(_ s: inout OverlayState, live: Bool = true) {
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false, live: live))))
    }

    func testToggleLiveSendsCommandAndUpdatesOptimistically() {
        var s = OverlayState()
        rec(&s)
        XCTAssertTrue(s.liveTranscription)
        XCTAssertEqual(reduce(&s, .toggleLive), [.setLive(false)])
        XCTAssertFalse(s.liveTranscription)
        XCTAssertTrue(s.partials.isEmpty)
        XCTAssertEqual(reduce(&s, .toggleLive), [.setLive(true)])
        XCTAssertTrue(s.liveTranscription)
    }

    func testStatusFromEngineWins() {
        var s = OverlayState()
        rec(&s, live: false)
        XCTAssertFalse(s.liveTranscription)
    }

    func testStatusWithoutLiveFieldMeansOn() throws {
        let msg = try Inbound.decode(Data(#"{"type":"status","state":"idle","model_ready":true,"session_id":null,"degraded":false}"#.utf8))
        guard case let .status(st) = msg else { return XCTFail() }
        XCTAssertTrue(st.live)
    }

    func testToggleWhileIdleOnlyChangesTheDefault() {
        var s = OverlayState()
        XCTAssertEqual(reduce(&s, .toggleLive), [])
        XCTAssertFalse(s.liveTranscription)
    }
}

final class SuggestionReducerTests: XCTestCase {
    private func recording(_ s: inout OverlayState) {
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
    }

    func testAddressedMarksLineAndShowsHiddenBar() {
        var s = OverlayState()
        recording(&s)
        reduce(&s, .hide)
        XCTAssertEqual(s.mode, .hidden)
        reduce(&s, .engine(.addressed(sessionId: "s1", seq: 4, reason: "name", text: "Роман, скажи")))
        XCTAssertEqual(s.addressedSeqs, [4])
        XCTAssertEqual(s.mode, .collapsed)
        reduce(&s, .engine(.addressed(sessionId: "old", seq: 9, reason: "name", text: "x")))
        XCTAssertEqual(s.addressedSeqs, [4])
    }

    func testSuggestionStreamsAndEmptyOneDisappears() {
        var s = OverlayState()
        recording(&s)
        reduce(&s, .engine(.suggestionDelta(id: "auto-4", text: "У п'ятницю ")))
        reduce(&s, .engine(.suggestionDelta(id: "auto-4", text: "буде готово.")))
        reduce(&s, .engine(.suggestionDone(id: "auto-4")))
        XCTAssertEqual(s.suggestion, Suggestion(id: "auto-4", text: "У п'ятницю буде готово.", done: true))
        reduce(&s, .engine(.suggestionDelta(id: "auto-9", text: "—")))
        reduce(&s, .engine(.suggestionDone(id: "auto-9")))
        XCTAssertNil(s.suggestion)
    }

    func testManualRequestSendsSuggestAndCanBeDismissed() {
        var s = OverlayState()
        XCTAssertEqual(reduce(&s, .requestSuggestion(id: "m1")), [])  // nothing to answer yet
        recording(&s)
        XCTAssertEqual(reduce(&s, .requestSuggestion(id: "m1")), [.suggest(id: "m1")])
        XCTAssertEqual(s.suggestion, Suggestion(id: "m1"))
        reduce(&s, .dismissSuggestion)
        XCTAssertNil(s.suggestion)
    }

    func testSessionOptionsEncoding() throws {
        let o = Outbound.setSessionOptions(SessionOptions(projectDir: nil, languages: [], assist: true, interview: true))
        let obj = try JSONSerialization.jsonObject(with: o.encoded()) as! [String: Any]
        XCTAssertTrue(obj["project_dir"] is NSNull)
        XCTAssertEqual(obj["languages"] as? [String], [])
        XCTAssertEqual(obj["interview"] as? Bool, true)
    }
}

final class FollowUpTests: XCTestCase {
    func testAskingAgainKeepsTheThread() {
        var s = OverlayState()
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s1", degraded: false))))
        for (i, q) in ["when is the deploy?", "and the migration?", "who owns it?", "why?", "ok?"].enumerated() {
            reduce(&s, .questionChanged(q))
            reduce(&s, .submitQuestion(id: "q\(i)"))
            reduce(&s, .engine(.answerDelta(id: "q\(i)", text: "A\(i)")))
            reduce(&s, .engine(.answerDone(id: "q\(i)")))
        }
        XCTAssertEqual(s.lastQuestion, "ok?")
        XCTAssertEqual(s.answer, "A4")
        XCTAssertEqual(s.qaHistory.map(\.question), ["and the migration?", "who owns it?", "why?"])  // last 3 kept
        reduce(&s, .engine(.status(EngineStatus(state: "recording", modelReady: true, sessionId: "s2", degraded: false))))
        XCTAssertTrue(s.qaHistory.isEmpty)  // new session, new thread
    }
}
