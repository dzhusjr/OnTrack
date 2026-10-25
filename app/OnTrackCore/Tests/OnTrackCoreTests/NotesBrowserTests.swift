import XCTest
@testable import OnTrackCore

final class MarkdownTests: XCTestCase {
    func testBlocks() {
        let html = Markdown.html("""
        # Linear Algebra

        _Started 2026-10-24 22:16_

        ## TL;DR
        - **Eigenvalues** next week
        - see `A = QΛQ⁻¹`
          - nested point
        1. first
        2. second

        > a quote

        ---
        ```
        <raw> **not bold**
        ```
        """)
        XCTAssertTrue(html.contains("<h1>Linear Algebra</h1>"))
        XCTAssertTrue(html.contains("<p><em>Started 2026-10-24 22:16</em></p>"))
        XCTAssertTrue(html.contains("<h2>TL;DR</h2>"))
        XCTAssertTrue(html.contains("<ul><li><strong>Eigenvalues</strong> next week</li><li>see <code>A = QΛQ⁻¹</code>"))
        XCTAssertTrue(html.contains("<ul><li>nested point</li></ul></li></ul>"))
        XCTAssertTrue(html.contains("<ol><li>first</li><li>second</li></ol>"))
        XCTAssertTrue(html.contains("<blockquote><p>a quote</p></blockquote>"))
        XCTAssertTrue(html.contains("<hr>"))
        XCTAssertTrue(html.contains("<pre><code>&lt;raw&gt; **not bold**</code></pre>"))
    }

    func testTranscriptLineAndEscaping() {
        let html = Markdown.html("`00:01:06` **Speaker A:** a <b>tag</b> & snake_case_name [00:00:53]")
        XCTAssertEqual(html, "<p><code class=\"ts\">00:01:06</code> <strong>Speaker A:</strong> a &lt;b&gt;tag&lt;/b&gt; "
                           + "&amp; snake_case_name <span class=\"ts\">[00:00:53]</span></p>")
    }

    func testLinksOnlyForSafeSchemes() {
        XCTAssertTrue(Markdown.html("[docs](https://example.com)").contains(#"<a href="https://example.com">docs</a>"#))
        XCTAssertFalse(Markdown.html("[x](javascript:alert(1))").contains("<a"))
    }
}

final class NotesLibraryTests: XCTestCase {
    private var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    private func write(_ id: String, _ files: [String: String]) throws {
        let f = dir.appendingPathComponent(id)
        try FileManager.default.createDirectory(at: f, withIntermediateDirectories: true)
        for (name, text) in files { try text.write(to: f.appendingPathComponent(name), atomically: true, encoding: .utf8) }
    }

    func testScanSortsNewestFirstAndReadsTitles() throws {
        try write("2026-10-23-1405-linear-algebra", ["summary.md": "# Linear Algebra — Eigen\n\nok", "transcript.md": "x"])
        try write("2026-10-24-2216-session-22-16", ["transcript.md": "# Session 22:16\n", "qa.md": "## Q"])
        try write("2026-10-22-0900-empty", [:])
        try write("random", ["notes.md": "plain"])
        let s = NotesLibrary.scan(dir)
        XCTAssertEqual(s.map(\.id), ["2026-10-24-2216-session-22-16", "2026-10-23-1405-linear-algebra", "random"])
        XCTAssertEqual(s[0].title, "Session 22:16")
        XCTAssertEqual(s[0].kinds, [.transcript, .qa])
        XCTAssertEqual(s[1].title, "Linear Algebra — Eigen")
        XCTAssertEqual(s[1].kinds, [.summary, .transcript])
        XCTAssertEqual(s[2].title, "random")
        let c = Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: s[0].date!)
        XCTAssertEqual([c.year, c.month, c.day, c.hour, c.minute], [2026, 10, 24, 22, 16])
    }

    func testFallbackTitleAndSearch() throws {
        try write("2026-10-23-1405-linear-algebra", ["transcript.md": "no heading, eigenvalue talk"])
        try write("2026-10-24-1000-history", ["summary.md": "# History\nRome"])
        let s = NotesLibrary.scan(dir)
        XCTAssertEqual(s.last?.title, "Linear Algebra")
        XCTAssertEqual(NotesLibrary.filter(s, query: "EIGEN").map(\.id), ["2026-10-23-1405-linear-algebra"])
        XCTAssertEqual(NotesLibrary.filter(s, query: "rome", deep: false).count, 0)
        XCTAssertEqual(NotesLibrary.filter(s, query: "  ").count, 2)
    }

    func testMissingDirectoryIsEmpty() {
        XCTAssertTrue(NotesLibrary.scan(dir.appendingPathComponent("nope")).isEmpty)
    }
}
