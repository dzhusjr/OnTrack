import XCTest
@testable import OnTrackCore

final class PendingTranscriptionTests: XCTestCase {
    func testRecoveryExcludesSavedAndActiveRecordings() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let recordings = root.appendingPathComponent("sessions")
        let audio = root.appendingPathComponent("audio")
        let notes = root.appendingPathComponent("notes")
        for id in ["unsaved", "saved", "active", "no-audio"] {
            let dir = recordings.appendingPathComponent(id)
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try Data("{\"name\":\"Lecture\"}".utf8).write(to: dir.appendingPathComponent("meta.json"))
            if id != "no-audio" {
                try FileManager.default.createDirectory(at: audio.appendingPathComponent(id), withIntermediateDirectories: true)
            }
        }
        let savedDir = notes.appendingPathComponent("renamed")
        try FileManager.default.createDirectory(at: savedDir, withIntermediateDirectories: true)
        try Data("saved".utf8).write(to: savedDir.appendingPathComponent(".session_id"))
        try Data("# Done".utf8).write(to: savedDir.appendingPathComponent("transcript.md"))
        let saved = NotesLibrary.scan(notes)
        let found = NotesLibrary.pending(sessionsDir: recordings, audioDir: audio, saved: saved, excluding: "active")
        XCTAssertEqual(found.map(\.sessionId), ["unsaved"])
        XCTAssertTrue(found[0].pendingTranscription)
        // Interrupted improvements remain recoverable alongside the older saved transcript.
        try Data("{}".utf8).write(to: recordings.appendingPathComponent("saved/.pending_processing"))
        XCTAssertEqual(Set(NotesLibrary.pending(sessionsDir: recordings, audioDir: audio, saved: saved,
                                                excluding: "active").map(\.sessionId)), ["saved", "unsaved"])
    }

    func testFinishLaterWaitsForAcknowledgmentAndContinueStartsProcessing() throws {
        var state = OverlayState()
        XCTAssertEqual(reduce(&state, .continueProcessing("lecture")), [.continueProcessing(sessionId: "lecture")])
        XCTAssertEqual(state.mode, .finalizing)
        XCTAssertEqual(reduce(&state, .finishLater), [.deferProcessing(sessionId: "lecture")])
        XCTAssertEqual(state.progress?.stage, "deferring")
        let msg = try Inbound.decode(Data("{\"type\":\"processing_deferred\",\"session_id\":\"lecture\"}".utf8))
        _ = reduce(&state, .engine(msg))
        XCTAssertNil(state.progress)
        XCTAssertEqual(state.mode, .hidden)
    }
}
