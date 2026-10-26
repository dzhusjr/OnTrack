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
        XCTAssertEqual(decoded, 14)
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
            "set_session_options": .setSessionOptions(SessionOptions(projectDir: "/Users/dzhus/PycharmProjects/cetl",
                                                                     languages: ["uk"], assist: true, interview: false)),
            "suggest": .suggest(id: "s1"),
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
