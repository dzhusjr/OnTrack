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
    public let live: Bool  // live transcription on (audio is recorded either way)
    public let paused: Bool  // recording paused: nothing is captured

    public init(state: String, modelReady: Bool, sessionId: String?, degraded: Bool, live: Bool = true,
                paused: Bool = false) {
        self.state = state
        self.modelReady = modelReady
        self.sessionId = sessionId
        self.degraded = degraded
        self.live = live
        self.paused = paused
    }
}

/// Optional local AI (MLX / Ollama): what is chosen and whether a model is loaded right now.
public struct AiStatus: Equatable, Sendable {
    public var state: String  // off | loading | ready | error
    public var mode: String  // claude | local_first | local_only
    public var provider: String  // mlx | ollama
    public var model: String  // the model in memory ("" when none)
    public var message: String  // why it is not ready, in plain words
    public var installed: [String]  // local models found for this provider
    public var models: [String: String]  // chosen per task: quick, summary, project
    public var last: String  // what answered the latest request
    public var memoryMB: Int?
    public var needsSetup: String  // "" | package | model: what Install would add
    public var progress: Double?  // 0...1 while installing, nil when unknown

    public init(state: String = "off", mode: String = "claude", provider: String = "mlx", model: String = "",
                message: String = "", installed: [String] = [], models: [String: String] = [:], last: String = "",
                memoryMB: Int? = nil, needsSetup: String = "", progress: Double? = nil) {
        self.state = state
        self.mode = mode
        self.provider = provider
        self.model = model
        self.needsSetup = needsSetup
        self.progress = progress
        self.message = message
        self.installed = installed
        self.models = models
        self.last = last
        self.memoryMB = memoryMB
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
    case partial(sessionId: String, channel: Int, tStart: Double, text: String)
    case answerDelta(id: String, text: String)
    case answerDone(id: String)
    case sessionStopped(sessionId: String, durationS: Double, lineCount: Int)
    case finalizeProgress(sessionId: String, stage: String, pct: Double)
    case processingDeferred(sessionId: String)
    case finalizeDone(sessionId: String, notesPath: String)
    case enrollDone(score: Double)
    case addressed(sessionId: String, seq: Int, reason: String, text: String)  // someone said your name / asked you
    case suggestionDelta(id: String, text: String)
    case suggestionDone(id: String)
    case aiStatus(AiStatus)
    case error(EngineError)
    case unknown(String)

    private struct Raw: Decodable {
        let type: String
        let id: String?
        let state: String?
        let model_ready: Bool?
        let session_id: String?
        let degraded: Bool?
        let live: Bool?
        let paused: Bool?
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
        let channel: Int?
        let score: Double?
        let reason: String?
        let code: String?
        let message: String?
        let recoverable: Bool?
        let mode: String?
        let provider: String?
        let model: String?
        let installed: [String]?
        let models: [String: String]?
        let last: String?
        let memory_mb: Int?
        let needs_setup: String?
        let progress: Double?
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
                                        sessionId: r.session_id, degraded: r.degraded ?? false, live: r.live ?? true,
                                        paused: r.paused ?? false))
        case "line":
            return .line(TranscriptLine(sessionId: r.session_id ?? "", seq: try need(r.seq), speaker: try need(r.speaker),
                                        tStart: try need(r.t_start), tEnd: try need(r.t_end), lang: r.lang ?? "",
                                        text: try need(r.text)))
        case "partial":
            return .partial(sessionId: r.session_id ?? "", channel: try need(r.channel), tStart: try need(r.t_start),
                            text: try need(r.text))
        case "answer_delta": return .answerDelta(id: try need(r.id), text: try need(r.text))
        case "answer_done": return .answerDone(id: try need(r.id))
        case "session_stopped":
            return .sessionStopped(sessionId: try need(r.session_id), durationS: r.duration_s ?? 0, lineCount: r.line_count ?? 0)
        case "finalize_progress":
            return .finalizeProgress(sessionId: try need(r.session_id), stage: try need(r.stage), pct: r.pct ?? 0)
        case "processing_deferred": return .processingDeferred(sessionId: try need(r.session_id))
        case "finalize_done": return .finalizeDone(sessionId: try need(r.session_id), notesPath: r.notes_path ?? "")
        case "enroll_done": return .enrollDone(score: try need(r.score))
        case "addressed":
            return .addressed(sessionId: r.session_id ?? "", seq: try need(r.seq), reason: try need(r.reason),
                              text: try need(r.text))
        case "suggestion_delta": return .suggestionDelta(id: try need(r.id), text: try need(r.text))
        case "suggestion_done": return .suggestionDone(id: try need(r.id))
        case "ai_status":
            return .aiStatus(AiStatus(state: try need(r.state), mode: try need(r.mode), provider: try need(r.provider),
                                      model: r.model ?? "", message: r.message ?? "", installed: r.installed ?? [],
                                      models: r.models ?? [:], last: r.last ?? "", memoryMB: r.memory_mb,
                                      needsSetup: r.needs_setup ?? "", progress: r.progress))
        case "error":
            return .error(EngineError(code: try need(r.code), message: r.message ?? "", recoverable: r.recoverable ?? true))
        default: return .unknown(r.type)
        }
    }
}

// MARK: - App → engine

/// Per-session choices sent to the engine (menu): project folder for Claude, language lock, reply helper.
public struct SessionOptions: Equatable, Sendable {
    public var projectDir: String?
    public var languages: [String]  // empty = automatic
    public var assist: Bool  // auto-draft a reply when someone says your name / a trigger word
    public var interview: Bool  // … and for every question someone asks
    public var names: [String]  // your name, nicknames and any trigger words; empty = engine default
    public var connectors: Bool  // let Claude use Claude Code's connectors (Jira, mail, …): richer but slower

    public init(projectDir: String? = nil, languages: [String] = [], assist: Bool = true, interview: Bool = false,
                names: [String] = [], connectors: Bool = false) {
        self.connectors = connectors
        self.projectDir = projectDir
        self.languages = languages
        self.assist = assist
        self.interview = interview
        self.names = names
    }
}

/// Which AI answers what. Choosing a mode never loads anything; "Load" (`.localAi(.start)`) does.
public struct AiSettings: Equatable, Sendable {
    public var mode: String  // claude (default) | local_first (local when loaded, Claude otherwise) | local_only
    public var provider: String  // mlx | ollama
    public var quick: String  // model for live answers and replies
    public var summary: String  // "" = Claude (local_first) / the quick model (local_only)
    public var project: String  // "" = same rule, for questions about the project folder

    public init(mode: String = "claude", provider: String = "mlx", quick: String = "", summary: String = "",
                project: String = "") {
        self.mode = mode
        self.provider = provider
        self.quick = quick
        self.summary = summary
        self.project = project
    }
}

public enum LocalAiAction: String, Equatable, Sendable {
    case start, stop, refresh, install
}

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
    case setLive(Bool)
    case setPaused(Bool)
    case setSessionOptions(SessionOptions)
    case suggest(id: String)
    case askAbout(id: String, question: String, sessionId: String)  // a saved session (Notes window)
    case finalizeLight(sessionId: String, action: FinalizeAction)  // keep the live transcript + audio (no heavy pass)
    case deferProcessing(sessionId: String)
    case continueProcessing(sessionId: String)
    case setAi(AiSettings)
    case localAi(LocalAiAction)  // the local model is loaded only on .start
    case improve(sessionId: String)  // later: accurate re-transcription of a saved session from its kept audio

    public static let typeNames: Set<String> = [
        "ping", "start_session", "stop_session", "ask", "finalize", "retry_summary", "enroll_start", "set_config",
        "set_live", "set_paused", "set_session_options", "suggest", "improve", "defer_processing", "continue_processing",
        "set_ai", "local_ai",
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
        case let .setLive(on): return ["type": "set_live", "enabled": on]
        case let .setPaused(on): return ["type": "set_paused", "paused": on]
        case let .setSessionOptions(o):
            return ["type": "set_session_options", "project_dir": o.projectDir ?? NSNull(), "languages": o.languages,
                    "assist": o.assist, "interview": o.interview, "names": o.names, "connectors": o.connectors]
        case let .suggest(id): return ["type": "suggest", "id": id]
        case let .askAbout(id, q, sid): return ["type": "ask", "id": id, "question": q, "session_id": sid]
        case let .finalizeLight(sid, action):
            return ["type": "finalize", "session_id": sid, "action": action.rawValue, "improve": false]
        case let .deferProcessing(sid): return ["type": "defer_processing", "session_id": sid]
        case let .continueProcessing(sid): return ["type": "continue_processing", "session_id": sid]
        case let .improve(sid): return ["type": "improve", "session_id": sid]
        case let .setAi(a):
            return ["type": "set_ai", "mode": a.mode, "provider": a.provider, "quick": a.quick, "summary": a.summary,
                    "project": a.project]
        case let .localAi(action): return ["type": "local_ai", "action": action.rawValue]
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
