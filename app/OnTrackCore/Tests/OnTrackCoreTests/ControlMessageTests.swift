import XCTest
@testable import OnTrackCore

final class ControlMessageTests: XCTestCase {
    private func goldenLines() throws -> [Data] {
        var dec = NdjsonDecoder()
        return dec.feed(try Golden.data("control_messages.ndjson"))
    }

    func testEveryEngineMessageInGoldenDecodes() throws {
        var decoded = 0
        for line in try goldenLines() {
            let obj = try JSONSerialization.jsonObject(with: line) as! [String: Any]
            let type = obj["type"] as! String
            guard Outbound.typeNames.contains(type) == false else { continue }
            let msg = try Inbound.decode(line)
            if case .unknown = msg { XCTFail("unknown: \(type)") }
            decoded += 1
        }
        XCTAssertEqual(decoded, 16)
    }

    func testOutboundEncodingMatchesGolden() throws {
        let expected: [String: Outbound] = [
            "ping": .ping(id: "p1"),
            "start_session": .startSession(name: "Linear Algebra", sessionId: nil),
            "stop_session": .stopSession,
            "ask": .ask(id: "q1", question: "What did she say about the deadline?"),
            "finalize": .finalize(sessionId: "2026-10-23-1405-linear-algebra", action: .saveAll),
            "retry_summary": .retrySummary(sessionId: "2026-10-23-1405-linear-algebra"),
            "enroll_start": .enrollStart,
            "set_config": .setConfig(["tau": 0.6]),
            "set_live": .setLive(false),
            "set_paused": .setPaused(true),
            "set_session_options": .setSessionOptions(SessionOptions(projectDir: "/Users/alex/PycharmProjects/acme",
                                                                     languages: ["uk"], assist: true, interview: false,
                                                                     names: ["Alex", "Олекса"], connectors: false)),
            "suggest": .suggest(id: "s1"),
            "improve": .improve(sessionId: "2026-10-23-1405-linear-algebra"),
            "defer_processing": .deferProcessing(sessionId: "2026-10-23-1405-linear-algebra"),
            "continue_processing": .continueProcessing(sessionId: "2026-10-23-1405-linear-algebra"),
            "set_ai": .setAi(AiSettings(mode: "local_first", provider: "mlx", quick: "mlx-community/Qwen3-4B-Instruct-2507-4bit")),
            "local_ai": .localAi(.start),
        ]
        var seen = 0
        for line in try goldenLines() {
            let golden = try JSONSerialization.jsonObject(with: line) as! NSDictionary
            guard let out = expected[golden["type"] as! String] else { continue }
            let data = out.encoded()
            XCTAssertEqual(data.last, UInt8(ascii: "\n"))
            let mine = try JSONSerialization.jsonObject(with: data) as! NSDictionary
            XCTAssertEqual(mine, golden)
            seen += 1
        }
        XCTAssertEqual(seen, expected.count)
    }

    func testLineDecodesFields() throws {
        let raw = #"{"type":"line","session_id":"s","seq":4,"speaker":"Other","t_start":1.5,"t_end":2.0,"lang":"ru","text":"Привет"}"#
        guard case let .line(l) = try Inbound.decode(Data(raw.utf8)) else { return XCTFail() }
        XCTAssertEqual(l, TranscriptLine(sessionId: "s", seq: 4, speaker: "Other", tStart: 1.5, tEnd: 2.0, lang: "ru", text: "Привет"))
    }

    func testAiStatusDecodesAndToleratesMissingOptionalFields() throws {
        let raw = #"{"type":"ai_status","state":"ready","mode":"local_only","provider":"ollama","model":"qwen3:4b","installed":["qwen3:4b"],"models":{"quick":"qwen3:4b"},"memory_mb":2400}"#
        guard case let .aiStatus(s) = try Inbound.decode(Data(raw.utf8)) else { return XCTFail() }
        XCTAssertEqual(s, AiStatus(state: "ready", mode: "local_only", provider: "ollama", model: "qwen3:4b",
                                   installed: ["qwen3:4b"], models: ["quick": "qwen3:4b"], memoryMB: 2400))
        guard case let .aiStatus(bare) = try Inbound.decode(Data(#"{"type":"ai_status","state":"off","mode":"claude","provider":"mlx"}"#.utf8))
        else { return XCTFail() }
        XCTAssertEqual(bare, AiStatus())
    }

    func testUnknownTypeIsTolerated() throws {
        guard case let .unknown(t) = try Inbound.decode(Data(#"{"type":"future_thing"}"#.utf8)) else { return XCTFail() }
        XCTAssertEqual(t, "future_thing")
    }

    func testNdjsonDecoderPartialChunks() {
        var dec = NdjsonDecoder()
        XCTAssertEqual(dec.feed(Data("{\"a\":".utf8)).count, 0)
        let out = dec.feed(Data("1}\n\n{\"b\":2}\n".utf8))
        XCTAssertEqual(out.map { String(decoding: $0, as: UTF8.self) }, ["{\"a\":1}", "{\"b\":2}"])
    }
}

final class AskAboutTests: XCTestCase {
    func testAskAboutSavedSessionEncodesSessionId() throws {
        let obj = try JSONSerialization.jsonObject(
            with: Outbound.askAbout(id: "notes-1", question: "when?", sessionId: "2026-10-24-2216-x").encoded()) as! [String: Any]
        XCTAssertEqual(obj["type"] as? String, "ask")
        XCTAssertEqual(obj["session_id"] as? String, "2026-10-24-2216-x")
        XCTAssertEqual(obj["question"] as? String, "when?")
    }
}

final class LightFinalizeTests: XCTestCase {
    func testLightFinalizeKeepsLiveTranscript() throws {
        let obj = try JSONSerialization.jsonObject(
            with: Outbound.finalizeLight(sessionId: "s1", action: .saveAll).encoded()) as! [String: Any]
        XCTAssertEqual(obj["type"] as? String, "finalize")
        XCTAssertEqual(obj["action"] as? String, "save_all")
        XCTAssertEqual(obj["improve"] as? Bool, false)
    }
}
