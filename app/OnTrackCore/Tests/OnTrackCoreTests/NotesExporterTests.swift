import XCTest
@testable import OnTrackCore

final class NotesExporterTests: XCTestCase {
    private var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: tmp) }

    private func makeNotes() throws -> URL {
        let notes = tmp.appendingPathComponent("notes/2026-10-24-1630-session-16-30")
        try FileManager.default.createDirectory(at: notes, withIntermediateDirectories: true)
        for name in ["transcript.md", "summary.md", "qa.md"] {
            try Data("# \(name)".utf8).write(to: notes.appendingPathComponent(name))
        }
        try Data().write(to: notes.appendingPathComponent(".pending_sync"))
        try Data("code".utf8).write(to: notes.appendingPathComponent("summary_pending"))
        return notes
    }

    func testCopiesMarkdownIntoSessionSubfolder() throws {
        let notes = try makeNotes()
        let dest = tmp.appendingPathComponent("Courses/Linear Algebra")
        let out = try NotesExporter.export(notesDir: notes, into: dest)
        XCTAssertEqual(out.lastPathComponent, "2026-10-24-1630-session-16-30")
        let files = try FileManager.default.contentsOfDirectory(atPath: out.path).sorted()
        XCTAssertEqual(files, ["qa.md", "summary.md", "transcript.md"])  // no internal marker files
    }

    func testExportTwiceOverwrites() throws {
        let notes = try makeNotes()
        let dest = tmp.appendingPathComponent("Out")
        _ = try NotesExporter.export(notesDir: notes, into: dest)
        try Data("# updated".utf8).write(to: notes.appendingPathComponent("summary.md"))
        let out = try NotesExporter.export(notesDir: notes, into: dest)
        XCTAssertEqual(try String(contentsOf: out.appendingPathComponent("summary.md"), encoding: .utf8), "# updated")
    }

    func testMissingNotesFolderThrows() {
        XCTAssertThrowsError(try NotesExporter.export(notesDir: tmp.appendingPathComponent("nope"), into: tmp))
    }
}
