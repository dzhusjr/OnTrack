import Foundation

public struct OverlayLine: Equatable, Sendable {
    public let seq: Int
    public let speaker: String
    public let text: String
    public let tStart: Double
}

/// One finished question and its answer in the overlay's Q&A thread.
public struct QAExchange: Equatable, Sendable {
    public let question: String
    public let answer: String

    public init(question: String, answer: String) {
        self.question = question
        self.answer = answer
    }
}

/// A drafted reply for when someone addresses you (streams in, then stays until dismissed or replaced).
public struct Suggestion: Equatable, Sendable {
    public let id: String
    public var text: String
    public var done: Bool

    public init(id: String, text: String = "", done: Bool = false) {
        self.id = id
        self.text = text
        self.done = done
    }
}

/// Grey live preview of the phrase currently being spoken on one channel.
public struct PreviewLine: Equatable, Sendable {
    public let channel: Int
    public let tStart: Double
    public let text: String
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
    public var autoShow = true  // show the live bar automatically when recording starts
    public var liveTranscription = true  // off = recording only; transcribed when the session is saved
    public var paused = false  // recording paused: nothing captured until resumed
    public var suggestion: Suggestion?
    public var qaHistory: [QAExchange] = []  // earlier questions this session (newest last), shown above the latest
    public var addressedSeqs: Set<Int> = []  // lines where someone said your name / asked you
    public var mode: OverlayMode = .hidden
    public var lines: [OverlayLine] = []
    public var partials: [PreviewLine] = []  // at most one per channel, mic first

    /// Final lines to draw so that final + preview rows never exceed `maxLines`.
    public var visibleLines: [OverlayLine] {
        let room = max(1, maxLines - partials.count)
        return Array(lines.suffix(room))
    }
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
        partials = []
        lastSeq = -1
        suggestion = nil
        addressedSeqs = []
        qaHistory = []
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
    case hide
    case toggleLive
    case togglePause
    case requestSuggestion(id: String)
    case dismissSuggestion
    case deferredUntilPower
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
        case .hidden:
            // saving keeps running while hidden: reopen on its progress, never on the choice card again
            if s.progress != nil { s.mode = .finalizing } else { s.mode = (s.endCard != nil) ? .endCard : .collapsed }
        case .collapsed: s.mode = .expanded
        case .expanded: s.mode = s.question.isEmpty ? .hidden : .collapsed
        case .endCard, .finalizing, .done: s.mode = .hidden
        }
    case let .questionChanged(q):
        s.question = q
    case let .submitQuestion(id):
        let q = s.question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !s.asking else { return [] }
        if !s.lastQuestion.isEmpty, !s.answer.isEmpty {  // keep the thread: the new question may be a follow-up
            s.qaHistory.append(QAExchange(question: s.lastQuestion, answer: s.answer))
            if s.qaHistory.count > 3 { s.qaHistory.removeFirst(s.qaHistory.count - 3) }
        }
        s.asking = true
        s.askId = id
        s.answer = ""
        s.lastQuestion = q
        s.question = ""
        return [.ask(id: id, question: q)]
    case .escape:
        if s.mode == .collapsed { s.mode = .hidden } else if s.mode == .expanded { s.mode = .collapsed }
        if s.endCard?.confirmDiscard == true { s.endCard?.confirmDiscard = false }
    case .deferredUntilPower:
        s.endCard = nil
        s.progress = nil
        s.mode = s.recording ? .collapsed : .hidden
        s.toast = "Saved — transcription will finish when your Mac is plugged in"
    case let .requestSuggestion(id):
        guard s.recording || s.sessionId != nil else { return [] }
        s.suggestion = Suggestion(id: id)
        if s.mode == .hidden { s.mode = .collapsed }
        return [.suggest(id: id)]
    case .dismissSuggestion:
        s.suggestion = nil
    case .togglePause:
        guard s.recording else { return [] }
        s.paused.toggle()
        if s.paused { s.partials = [] }
        return [.setPaused(s.paused)]
    case .toggleLive:
        s.liveTranscription.toggle()
        if !s.liveTranscription { s.partials = [] }
        return s.recording ? [.setLive(s.liveTranscription)] : []
    case .hide:
        if s.mode == .collapsed || s.mode == .expanded { s.mode = .hidden }
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
        if s.mode == .finalizing || (s.mode == .hidden && s.progress != nil) {  // engine went away mid-save: let the user retry (saving is idempotent)
            s.mode = .endCard
            s.progress = nil
            s.error = "The engine restarted while saving — choose again to retry."
        }
    }
    return []
}

private func reduceEngine(_ s: inout OverlayState, _ msg: Inbound) -> [Outbound] {
    switch msg {
    case let .status(st):
        s.engineState = st.state
        if st.state == "recording" { s.liveTranscription = st.live; s.paused = st.paused }
        if st.state == "idle" { s.paused = false }
        s.modelReady = st.modelReady
        s.degraded = st.degraded
        let nowRecording = st.state == "recording" || st.state == "stopping"
        if nowRecording, let sid = st.sessionId, sid != s.sessionId || !s.recording {
            if sid != s.sessionId { s.beginSession(sid) }
            if s.mode == .done || (s.mode == .hidden && s.autoShow) { s.mode = s.autoShow ? .collapsed : .hidden }
        }
        if st.state == "recording" { s.recording = true }
        if st.state == "idle" { s.recording = false }
    case let .line(l):
        s.lineArrived(l)
    case let .partial(sid, channel, tStart, text):
        guard sid == s.sessionId else { return [] }
        if text.isEmpty {
            s.partials.removeAll { $0.channel == channel && abs($0.tStart - tStart) < 0.05 }
        } else {
            s.partials.removeAll { $0.channel == channel }
            s.partials.append(PreviewLine(channel: channel, tStart: tStart, text: text))
            s.partials.sort { $0.channel < $1.channel }
        }
    case let .answerDelta(id, text):
        if id == s.askId { s.answer += text }
    case let .answerDone(id):
        if id == s.askId { s.asking = false }
    case let .sessionStopped(sid, duration, count):
        s.recording = false
        s.paused = false
        s.partials = []
        if count == 0 && duration < 10 {  // accidental start/stop: nothing to save, don't ask
            s.endCard = nil
            s.mode = .hidden
            s.toast = "Empty session discarded"
            return [.finalize(sessionId: sid, action: .discard)]
        }
        s.endCard = EndCard(sessionId: sid, durationS: duration, lineCount: count, confirmDiscard: false)
        s.mode = .endCard
    case let .finalizeProgress(_, stage, pct):
        s.progress = FinalizeProgress(stage: stage, pct: pct)
    case let .finalizeDone(sid, path):
        if s.recording, sid != s.sessionId {  // previous session finished saving while a new one records
            if !path.isEmpty { s.toast = "Previous session saved to notes" }
            return []
        }
        s.notesPath = path.isEmpty ? nil : path
        s.progress = nil
        if s.mode == .hidden {  // user hid the overlay while saving: stay out of the way, just confirm
            s.endCard = nil
            if !path.isEmpty { s.toast = "Saved to notes" }
            return []
        }
        s.mode = path.isEmpty ? (s.recording ? .collapsed : .hidden) : .done
        if path.isEmpty { s.endCard = nil }
    case let .addressed(sid, seq, _, _):
        guard sid == s.sessionId else { return [] }
        s.addressedSeqs.insert(seq)
        if s.mode == .hidden && s.recording { s.mode = .collapsed }  // you were asked something: show the bar
    case let .suggestionDelta(id, text):
        if s.suggestion?.id != id { s.suggestion = Suggestion(id: id) }
        s.suggestion?.text += text
        if s.mode == .hidden && s.recording { s.mode = .collapsed }
    case let .suggestionDone(id):
        guard s.suggestion?.id == id else { return [] }
        s.suggestion?.done = true
        let t = s.suggestion?.text.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if t.isEmpty || t == "—" || t == "-" { s.suggestion = nil }  // nothing worth saying
    case let .enrollDone(score):
        s.toast = String(format: "Voiceprint saved (match %.2f)", score)
    case let .error(err):
        s.error = err.message
        // only a failed save reopens the card; warnings (speaker separation, summary) arrive while saving continues
        if (s.mode == .finalizing || s.progress != nil) && err.code == "finalize_failed" {
            s.mode = .endCard
            s.progress = nil
        }
    case .pong, .unknown:
        break
    }
    return []
}
