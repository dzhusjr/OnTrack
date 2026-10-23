import Foundation

// MARK: - Engine → app

public struct TranscriptLine: Equatable, Sendable {
    public let sessionId: String
    public let seq: Int
    public let speaker: String
    public let tStart: Double
    public let tEnd: Double
    public let lang: String
    public let text: String

    public init(sessionId: String, seq: Int, speaker: String, tStart: Double, tEnd: Double, lang: String, text: String) {
        self.sessionId = sessionId
        self.seq = seq
        self.speaker = speaker
        self.tStart = tStart
        self.tEnd = tEnd
        self.lang = lang
        self.text = text
    }
}

public struct EngineStatus: Equatable, Sendable {
    public let state: String
    public let modelReady: Bool
    public let sessionId: String?
    public let degraded: Bool

    public init(state: String, modelReady: Bool, sessionId: String?, degraded: Bool) {
        self.state = state
        self.modelReady = modelReady
        self.sessionId = sessionId
        self.degraded = degraded
    }
}

public struct EngineError: Equatable, Sendable {
    public let code: String
    public let message: String
    public let recoverable: Bool

    public init(code: String, message: String, recoverable: Bool) {
        self.code = code
        self.message = message
        self.recoverable = recoverable
    }
}

public enum Inbound: Equatable, Sendable {
    case pong(id: String?, state: String)
    case status(EngineStatus)
    case line(TranscriptLine)
    case answerDelta(id: String, text: String)
    case answerDone(id: String)
    case sessionStopped(sessionId: String, durationS: Double, lineCount: Int)
    case finalizeProgress(sessionId: String, stage: String, pct: Double)
    case finalizeDone(sessionId: String, notesPath: String)
    case enrollDone(score: Double)
    case error(EngineError)
    case unknown(String)

    private struct Raw: Decodable {
        let type: String
        let id: String?
        let state: String?
        let model_ready: Bool?
        let session_id: String?
        let degraded: Bool?
        let seq: Int?
        let speaker: String?
        let t_start: Double?
        let t_end: Double?
        let lang: String?
        let text: String?
        let duration_s: Double?
        let line_count: Int?
        let stage: String?
        let pct: Double?
        let notes_path: String?
        let score: Double?
        let code: String?
        let message: String?
        let recoverable: Bool?
    }

    public struct DecodeError: Error, Equatable {
        public let type: String
    }

    public static func decode(_ data: Data) throws -> Inbound {
        let r = try JSONDecoder().decode(Raw.self, from: data)
        func need<T>(_ v: T?) throws -> T {
            guard let v else { throw DecodeError(type: r.type) }
            return v
        }
        switch r.type {
        case "pong": return .pong(id: r.id, state: try need(r.state))
        case "status":
            return .status(EngineStatus(state: try need(r.state), modelReady: r.model_ready ?? false,
                                        sessionId: r.session_id, degraded: r.degraded ?? false))
        case "line":
            return .line(TranscriptLine(sessionId: r.session_id ?? "", seq: try need(r.seq), speaker: try need(r.speaker),
                                        tStart: try need(r.t_start), tEnd: try need(r.t_end), lang: r.lang ?? "",
                                        text: try need(r.text)))
        case "answer_delta": return .answerDelta(id: try need(r.id), text: try need(r.text))
        case "answer_done": return .answerDone(id: try need(r.id))
        case "session_stopped":
            return .sessionStopped(sessionId: try need(r.session_id), durationS: r.duration_s ?? 0, lineCount: r.line_count ?? 0)
        case "finalize_progress":
            return .finalizeProgress(sessionId: try need(r.session_id), stage: try need(r.stage), pct: r.pct ?? 0)
        case "finalize_done": return .finalizeDone(sessionId: try need(r.session_id), notesPath: r.notes_path ?? "")
        case "enroll_done": return .enrollDone(score: try need(r.score))
        case "error":
            return .error(EngineError(code: try need(r.code), message: r.message ?? "", recoverable: r.recoverable ?? true))
        default: return .unknown(r.type)
        }
    }
}

// MARK: - App → engine

public enum FinalizeAction: String, Equatable, Sendable {
    case saveAll = "save_all"
    case transcriptOnly = "transcript_only"
    case discard
}

public enum Outbound: Equatable, Sendable {
    case ping(id: String)
    case startSession(name: String, sessionId: String?)
    case stopSession
    case ask(id: String, question: String)
    case finalize(sessionId: String, action: FinalizeAction)
    case retrySummary(sessionId: String)
    case enrollStart
    case setConfig([String: Double])

    public static let typeNames: Set<String> = [
        "ping", "start_session", "stop_session", "ask", "finalize", "retry_summary", "enroll_start", "set_config",
    ]

    public var jsonObject: [String: Any] {
        switch self {
        case let .ping(id): return ["type": "ping", "id": id]
        case let .startSession(name, sid): return ["type": "start_session", "name": name, "session_id": sid ?? NSNull()]
        case .stopSession: return ["type": "stop_session"]
        case let .ask(id, q): return ["type": "ask", "id": id, "question": q]
        case let .finalize(sid, action): return ["type": "finalize", "session_id": sid, "action": action.rawValue]
        case let .retrySummary(sid): return ["type": "retry_summary", "session_id": sid]
        case .enrollStart: return ["type": "enroll_start"]
        case let .setConfig(cfg): return ["type": "set_config", "config": cfg]
        }
    }

    /// One NDJSON line (UTF-8 JSON + "\n").
    public func encoded() -> Data {
        var d = (try? JSONSerialization.data(withJSONObject: jsonObject, options: [])) ?? Data("{}".utf8)
        d.append(UInt8(ascii: "\n"))
        return d
    }
}

// MARK: - NDJSON framing

public struct NdjsonDecoder {
    private var buffer = Data()

    public init() {}

    public mutating func feed(_ data: Data) -> [Data] {
        buffer.append(data)
        var out: [Data] = []
        while let nl = buffer.firstIndex(of: UInt8(ascii: "\n")) {
            let line = buffer[buffer.startIndex..<nl]
            buffer = Data(buffer[buffer.index(after: nl)...])
            if !line.allSatisfy({ $0 == 0x20 || $0 == 0x09 || $0 == 0x0D }) {
                out.append(Data(line))
            }
        }
        return out
    }
}
