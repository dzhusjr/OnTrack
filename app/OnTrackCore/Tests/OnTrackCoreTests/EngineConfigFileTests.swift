import XCTest
@testable import OnTrackCore

final class EngineConfigFileTests: XCTestCase {
    func testReadsTypedValuesAndIgnoresComments() {
        let c = EngineConfigFile(text: """
        # llm_model = "opus"
        llm_model = "sonnet"  # summary
        live_preview = false
        overlay_lines = 4
        suggest_min_gap_s = 12.5
        vocabulary = ["Kubernetes", "eigen # value"]
        """)
        XCTAssertEqual(c.string("llm_model"), "sonnet")
        XCTAssertEqual(c.bool("live_preview"), false)
        XCTAssertEqual(c.int("overlay_lines"), 4)
        XCTAssertEqual(c.double("suggest_min_gap_s"), 12.5)
        XCTAssertEqual(c.strings("vocabulary"), ["Kubernetes", "eigen # value"])
        XCTAssertNil(c.string("fast_llm_model"))
    }

    func testSetReplacesKeepsCommentAndOtherLines() {
        var c = EngineConfigFile(text: "# hello\nllm_model = \"sonnet\"  # summary\ntau = 0.6\n")
        c.set("llm_model", string: "opus")
        XCTAssertEqual(c.text, "# hello\nllm_model = \"opus\"  # summary\ntau = 0.6\n")
    }

    func testSetAppendsBeforeFirstTableAndRemoveDeletes() {
        var c = EngineConfigFile(text: "tau = 0.6\n[other]\nx = 1\n")
        c.set("about_me", string: "an engineer \"Py\"")
        XCTAssertEqual(c.text, "tau = 0.6\nabout_me = \"an engineer \\\"Py\\\"\"\n[other]\nx = 1\n")
        XCTAssertEqual(c.string("about_me"), "an engineer \"Py\"")
        c.set("about_me", string: nil)
        XCTAssertEqual(c.text, "tau = 0.6\n[other]\nx = 1\n")
        XCTAssertNil(c.int("x"))  // keys inside tables are not top-level
    }

    func testListsBoolsAndEmptyFile() {
        var c = EngineConfigFile(text: "")
        c.set("allowed_langs", strings: ["en", "nl"])
        c.set("live_preview", bool: true)
        XCTAssertEqual(c.text, "allowed_langs = [\"en\", \"nl\"]\nlive_preview = true\n")
        XCTAssertEqual(c.strings("allowed_langs"), ["en", "nl"])
        c.set("allowed_langs", strings: [])
        XCTAssertEqual(c.strings("allowed_langs"), [])
    }
}
