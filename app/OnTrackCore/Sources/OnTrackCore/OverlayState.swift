import Foundation

public struct OverlayLine: Equatable, Sendable {
    public let seq: Int
    public let speaker: String
    public let text: String
    public let tStart: Double
}

public struct EndCard: Equatable, Sendable {
    public let sessionId: String
    public let durationS: Double
    public let lineCount: Int
    public var confirmDiscard: Bool

    public init(sessionId: String, durationS: Double, lineCount: Int, confirmDiscard: Bool) {
        self.sessionId = sessionId
        self.durationS = durationS
        self.lineCount = lineCount
        self.confirmDiscard = confirmDiscard
    }
}

public struct FinalizeProgress: Equatable, Sendable {
    public let stage: String
    public let pct: Double

    public init(stage: String, pct: Double) {
        self.stage = stage
        self.pct = pct
    }
}

public enum OverlayMode: Equatable, Sendable {
    case hidden, collapsed, expanded, endCard, finalizing, done
}

/// Everything the overlay shows (spec §6). Pure value; changed only by `reduce`.
public struct OverlayState: Equatable {
    public var maxLines: Int
    public var mode: OverlayMode = .hidden
    public var lines: [OverlayLine] = []
    public var recording = false
    public var sessionId: String?
    public var engineState = "disconnected"
    public var modelReady = false
    public var degraded = false
    public var question = ""
    public var answer = ""
    public var lastQuestion = ""
    public var asking = false
    public var askId: String?
    public var endCard: EndCard?
    public var progress: FinalizeProgress?
    public var notesPath: String?
    public var error: String?
    public var toast: String?
    private var lastSeq = -1

    public init(maxLines: Int = 3) {
        self.maxLines = max(1, min(5, maxLines))
    }

    fileprivate mutating func lineArrived(_ l: TranscriptLine) {
        guard let sid = sessionId, l.sessionId == sid, l.seq > lastSeq else { return }
        lastSeq = l.seq
        lines.append(OverlayLine(seq: l.seq, speaker: l.speaker, text: l.text, tStart: l.tStart))
        if lines.count > maxLines { lines.removeFirst(lines.count - maxLines) }
    }

    fileprivate mutating func beginSession(_ id: String) {
        sessionId = id
        lines = []
        lastSeq = -1
        answer = ""
        lastQuestion = ""
        endCard = nil
        progress = nil
        notesPath = nil
    }
}

public enum OverlayEvent: Equatable {
    case engine(Inbound)
    case toggle
    case questionChanged(String)
    case submitQuestion(id: String)
    case escape
    case choose(FinalizeAction)
    case dismiss
    case clearError
    case disconnected
}

/// Applies an event and returns the commands to send to the engine.
@discardableResult
public func reduce(_ s: inout OverlayState, _ e: OverlayEvent) -> [Outbound] {
    switch e {
    case let .engine(msg):
        return reduceEngine(&s, msg)
    case .toggle:
        switch s.mode {
        case .hidden: s.mode = (s.endCard != nil) ? .endCard : .collapsed
        case .collapsed: s.mode = .expanded
        case .expanded: s.mode = s.question.isEmpty ? .hidden : .collapsed
        case .endCard, .finalizing, .done: s.mode = .hidden
        }
    case let .questionChanged(q):
        s.question = q
    case let .submitQuestion(id):
        let q = s.question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !s.asking else { return [] }
        s.asking = true
        s.askId = id
        s.answer = ""
        s.lastQuestion = q
        s.question = ""
        return [.ask(id: id, question: q)]
    case .escape:
        if s.mode == .expanded { s.mode = .collapsed }
        if s.endCard?.confirmDiscard == true { s.endCard?.confirmDiscard = false }
    case let .choose(action):
        guard var card = s.endCard, s.mode == .endCard else { return [] }
        if action == .discard && !card.confirmDiscard {
            card.confirmDiscard = true
            s.endCard = card
            return []
        }
        s.mode = .finalizing
        s.progress = FinalizeProgress(stage: "starting", pct: 0)
        return [.finalize(sessionId: card.sessionId, action: action)]
    case .dismiss:
        if s.mode == .done || s.mode == .endCard {
            s.endCard = nil
            s.progress = nil
            s.mode = s.recording ? .collapsed : .hidden
        }
    case .clearError:
        s.error = nil
        s.toast = nil
    case .disconnected:
        s.engineState = "disconnected"
    }
    return []
}

private func reduceEngine(_ s: inout OverlayState, _ msg: Inbound) -> [Outbound] {
    switch msg {
    case let .status(st):
        s.engineState = st.state
        s.modelReady = st.modelReady
        s.degraded = st.degraded
        let nowRecording = st.state == "recording" || st.state == "stopping"
        if nowRecording, let sid = st.sessionId, sid != s.sessionId || !s.recording {
            if sid != s.sessionId { s.beginSession(sid) }
            if s.mode == .hidden || s.mode == .done { s.mode = .collapsed }
        }
        if st.state == "recording" { s.recording = true }
        if st.state == "idle" { s.recording = false }
    case let .line(l):
        s.lineArrived(l)
    case let .answerDelta(id, text):
        if id == s.askId { s.answer += text }
    case let .answerDone(id):
        if id == s.askId { s.asking = false }
    case let .sessionStopped(sid, duration, count):
        s.recording = false
        s.endCard = EndCard(sessionId: sid, durationS: duration, lineCount: count, confirmDiscard: false)
        s.mode = .endCard
    case let .finalizeProgress(_, stage, pct):
        s.progress = FinalizeProgress(stage: stage, pct: pct)
    case let .finalizeDone(_, path):
        s.notesPath = path.isEmpty ? nil : path
        s.progress = nil
        s.mode = path.isEmpty ? (s.recording ? .collapsed : .hidden) : .done
        if path.isEmpty { s.endCard = nil }
    case let .enrollDone(score):
        s.toast = String(format: "Voiceprint saved (match %.2f)", score)
    case let .error(err):
        s.error = err.message
        if s.mode == .finalizing { s.mode = .endCard }
    case .pong, .unknown:
        break
    }
    return []
}
