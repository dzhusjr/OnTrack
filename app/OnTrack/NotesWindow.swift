import AppKit
import OnTrackCore
import SwiftUI
import WebKit

// MARK: model

@MainActor
final class NotesBrowserModel: ObservableObject {
    let notesDir: URL
    @Published private(set) var sessions: [NoteSession] = []
    @Published var selection: NoteSession.ID?
    @Published var kind: NoteSession.Kind = .summary
    @Published var query = ""

    // asking about a saved session
    struct Pending: Equatable { let id: String; let sessionId: String; let question: String; var answer: String }
    @Published var question = ""
    @Published private(set) var threads: [String: [QAExchange]] = [:]  // per session, this window's questions
    @Published private(set) var pending: Pending?
    var ask: ((_ id: String, _ question: String, _ sessionId: String) -> Void)?
    var onImprove: ((String) -> Void)?
    var audioDir: URL?
    var sessionsDir: URL?
    var onContinue: ((String) -> Void)?
    var onFinishLater: ((String) -> Void)?
    @Published var processingId: String?
    @Published var stopping = false
    @Published private var recordingId: String?
    var isRecording: Bool { recordingId != nil }

    /// The recording's audio is still kept (saved without the heavy pass) → it can be improved.
    func canImprove(_ s: NoteSession) -> Bool {
        guard let dir = audioDir, onImprove != nil else { return false }
        return FileManager.default.fileExists(atPath: dir.appendingPathComponent(s.sessionId).path)
    }

    init(notesDir: URL) { self.notesDir = notesDir }

    func continueTranscription(_ session: NoteSession) {
        guard processingId == nil, !isRecording else { return }
        processingId = session.sessionId
        onContinue?(session.sessionId)
    }

    func finishLater(_ session: NoteSession) {
        stopping = true
        onFinishLater?(session.sessionId)
    }

    func submit() {
        let q = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let s = selected, !q.isEmpty, pending == nil, let ask else { return }
        let id = "notes-" + UUID().uuidString
        pending = Pending(id: id, sessionId: s.id, question: q, answer: "")
        question = ""
        ask(id, q, s.id)
    }

    /// Engine answers for questions asked here (ids start with "notes-").
    func receive(_ msg: Inbound) {
        switch msg {
        case let .status(status):
            recordingId = status.state == "recording" ? status.sessionId : nil
        case let .finalizeProgress(sid, stage, _):
            let changed = processingId != sid
            processingId = sid
            stopping = stage == "deferring"
            if changed { reload() }
        case .processingDeferred, .finalizeDone:
            processingId = nil
            stopping = false
            reload()
        case .error:
            processingId = nil
            stopping = false
        case let .answerDelta(id, text) where id == pending?.id:
            pending?.answer += text
        case let .answerDone(id) where id == pending?.id:
            guard let p = pending else { return }
            let answer = p.answer.trimmingCharacters(in: .whitespacesAndNewlines)
            threads[p.sessionId, default: []].append(
                QAExchange(question: p.question, answer: answer.isEmpty ? "_No answer. See menu → Open Logs._" : answer))
            pending = nil
            reload()  // qa.md changed: the Q&A tab now has this question too
        default:
            break
        }
    }

    var visible: [NoteSession] { NotesLibrary.filter(sessions, query: query) }
    var selected: NoteSession? { sessions.first { $0.id == selection } }

    func reload() {
        let saved = NotesLibrary.scan(notesDir)
        let unfinished = sessionsDir.flatMap { dir in
            audioDir.map { NotesLibrary.pending(sessionsDir: dir, audioDir: $0, saved: saved, excluding: recordingId) }
        } ?? []
        let ids = Set(unfinished.map(\.sessionId))
        sessions = unfinished + saved.filter { !ids.contains($0.sessionId) }
        if selected == nil { selection = sessions.first?.id }
        fixKind()
    }

    func select(_ id: String) {
        reload()
        selection = id
        fixKind()
    }

    /// Keep the chosen tab when the session has it; otherwise Summary → Transcript → whatever exists.
    func fixKind() {
        guard let s = selected, !s.kinds.contains(kind) else { return }
        kind = s.kinds.first ?? .summary
    }

    func markdown(_ s: NoteSession, _ k: NoteSession.Kind) -> String {
        (try? String(contentsOf: s.file(k), encoding: .utf8)) ?? ""
    }
}

// MARK: window

@MainActor
final class NotesWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let model: NotesBrowserModel

    init(notesDir: URL, ask: @escaping (_ id: String, _ question: String, _ sessionId: String) -> Void) {
        model = NotesBrowserModel(notesDir: notesDir)
        model.ask = ask
    }

    func receive(_ msg: Inbound) { model.receive(msg) }

    var onImprove: ((String) -> Void)? {
        get { model.onImprove }
        set { model.onImprove = newValue }
    }

    var audioDir: URL? {
        get { model.audioDir }
        set { model.audioDir = newValue }
    }

    var sessionsDir: URL? {
        get { model.sessionsDir }
        set { model.sessionsDir = newValue }
    }
    var onContinue: ((String) -> Void)? {
        get { model.onContinue }
        set { model.onContinue = newValue }
    }
    var onFinishLater: ((String) -> Void)? {
        get { model.onFinishLater }
        set { model.onFinishLater = newValue }
    }

    func show(sessionId: String? = nil) {
        if let id = sessionId { model.select(id) } else { model.reload() }
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1040, height: 700),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                             backing: .buffered, defer: false)
            w.title = "OnTrack Notes"
            w.titlebarAppearsTransparent = true
            w.toolbarStyle = .unified
            w.isReleasedWhenClosed = false
            w.contentViewController = NSHostingController(rootView: NotesBrowserView(model: model))
            w.setFrameAutosaveName("OnTrackNotes")
            if !w.setFrameUsingName("OnTrackNotes") { w.center() }
            w.delegate = self
            window = w
        }
        NSApp.setActivationPolicy(.regular)  // Dock icon + ⌘Tab while the window is open
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    /// A session finished saving: refresh the list if the window is open.
    func refresh() {
        if window?.isVisible == true { model.reload() }
    }

    func windowDidBecomeKey(_ notification: Notification) { model.reload() }

    func windowWillClose(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)  // back to a menu-bar-only app
    }
}

// MARK: views

struct NotesBrowserView: View {
    @ObservedObject var model: NotesBrowserModel

    var body: some View {
        NavigationSplitView {
            sidebar.navigationSplitViewColumnWidth(min: 240, ideal: 280, max: 380)
        } detail: {
            if let s = model.selected {
                NoteDetailView(model: model, session: s)
            } else {
                emptyState
            }
        }
        .searchable(text: $model.query, placement: .sidebar, prompt: "Search notes")
        .onChange(of: model.selection) { _, _ in model.fixKind() }
    }

    private var sidebar: some View {
        List(selection: $model.selection) {
            ForEach(Self.groups(model.visible), id: \.title) { group in
                Section(group.title) {
                    ForEach(group.items) { s in
                        SessionRow(session: s)
                            .tag(s.id)
                            .contextMenu {
                                Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([s.folder]) }
                                Button("Copy Folder Path") { copy(s.folder.path) }
                            }
                    }
                }
            }
        }
        .listStyle(.sidebar)
        .overlay {
            if model.visible.isEmpty {
                Text(model.query.isEmpty ? "No saved sessions yet" : "No matches")
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "text.alignleft").font(.system(size: 34, weight: .light)).foregroundStyle(.tertiary)
            Text("Saved sessions appear here").foregroundStyle(.secondary)
            Button("Open Notes Folder") { NSWorkspace.shared.open(model.notesDir) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    struct Group { let title: String; let items: [NoteSession] }

    static func groups(_ sessions: [NoteSession]) -> [Group] {
        let cal = Calendar.current
        var order: [String] = []
        var map: [String: [NoteSession]] = [:]
        for s in sessions {
            let title: String
            if s.pendingTranscription { title = "Pending transcription" }
            else if let d = s.date {
                if cal.isDateInToday(d) { title = "Today" }
                else if cal.isDateInYesterday(d) { title = "Yesterday" }
                else { title = d.formatted(.dateTime.weekday(.wide).day().month(.wide).year()) }
            } else {
                title = "Other"
            }
            if map[title] == nil { order.append(title) }
            map[title, default: []].append(s)
        }
        return order.map { Group(title: $0, items: map[$0]!) }
    }
}

private struct SessionRow: View {
    let session: NoteSession

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(session.title).font(.system(size: 13, weight: .medium)).lineLimit(1)
            HStack(spacing: 6) {
                if let d = session.date {
                    Text(d.formatted(date: .omitted, time: .shortened))
                }
                Text(session.pendingTranscription ? "Recording kept · finish later" : session.kinds.map(\.title).joined(separator: " · "))
            }
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .lineLimit(1)
        }
        .padding(.vertical, 3)
    }
}

private struct NoteDetailView: View {
    @ObservedObject var model: NotesBrowserModel
    let session: NoteSession

    var body: some View {
        let kind = session.kinds.contains(model.kind) ? model.kind : (session.kinds.first ?? .summary)
        let md = model.markdown(session, kind)
        MarkdownWebView(html: NotesPage.html(body: Markdown.html(md), kind: kind), baseURL: session.folder)
            .safeAreaInset(edge: .top, spacing: 0) {
                if session.pendingTranscription {
                    HStack {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(model.processingId == session.sessionId ? (model.stopping ? "Stopping after the current step…" : "Transcribing…") : "Pending transcription")
                                .font(.headline)
                            Text("Your recording is kept. You can quit OnTrack and finish it later.")
                                .font(.callout).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if model.processingId == session.sessionId {
                            Button("Finish later") { model.finishLater(session) }.disabled(model.stopping)
                        } else {
                            Button("Continue transcription") { model.continueTranscription(session) }
                                .disabled(model.processingId != nil || model.isRecording)
                                .help("Process the saved audio again. Some work may repeat.")
                        }
                    }.padding().background(.regularMaterial)
                }
            }
            .safeAreaInset(edge: .bottom, spacing: 0) {
                if !session.pendingTranscription { NotesAskBar(model: model, session: session) }
            }
            .navigationTitle(session.title)
            .navigationSubtitle(session.date?.formatted(date: .abbreviated, time: .shortened) ?? session.id)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Picker("View", selection: $model.kind) {
                        ForEach(session.kinds, id: \.self) { Text($0.title).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .fixedSize()
                }
                ToolbarItemGroup(placement: .primaryAction) {
                    if !session.pendingTranscription && model.canImprove(session) {
                        Button { model.onImprove?(session.sessionId) } label: {
                            Label("Improve Transcript", systemImage: "wand.and.stars")
                        }
                        .help("Re-transcribe with the accurate model and separate speakers (takes a few minutes)")
                    }
                    Button { copy(md) } label: { Label("Copy", systemImage: "doc.on.doc") }
                        .help("Copy this page as Markdown")
                    Button { NSWorkspace.shared.open(session.file(kind)) } label: {
                        Label("Open in Editor", systemImage: "square.and.pencil")
                    }
                    .help("Open \(kind.fileName) in your default editor")
                    Button { NSWorkspace.shared.activateFileViewerSelecting([session.file(kind)]) } label: {
                        Label("Show in Finder", systemImage: "folder")
                    }
                    .help("Show in Finder")
                }
            }
    }
}

/// "Ask about this session" under a saved session: short thread + input (answers via the engine, fast model).
private struct NotesAskBar: View {
    @ObservedObject var model: NotesBrowserModel
    let session: NoteSession
    @FocusState private var focused: Bool

    var body: some View {
        let thread = Array((model.threads[session.id] ?? []).suffix(3))
        let pending = model.pending?.sessionId == session.id ? model.pending : nil
        VStack(alignment: .leading, spacing: 8) {
            if !thread.isEmpty || pending != nil {
                ScrollView {
                    VStack(alignment: .leading, spacing: 10) {
                        ForEach(Array(thread.enumerated()), id: \.offset) { _, ex in
                            exchange(ex.question, ex.answer, dim: false)
                        }
                        if let p = pending {
                            exchange(p.question, p.answer.isEmpty ? "…" : p.answer, dim: false)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 220)
            }
            HStack(spacing: 8) {
                Image(systemName: "sparkle.magnifyingglass").foregroundStyle(.secondary)
                TextField(thread.isEmpty ? "Ask about this session…" : "Ask a follow-up…", text: $model.question)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .focused($focused)
                    .onSubmit { model.submit() }
                    .disabled(model.pending != nil)
                if model.pending != nil { ProgressView().controlSize(.small) }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }

    private func exchange(_ q: String, _ a: String, dim: Bool) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(q).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            Text(LocalizedStringKey(a)).font(.system(size: 13)).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

private func copy(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}

// MARK: rendering

enum NotesPage {
    static func html(body: String, kind: NoteSession.Kind) -> String {
        """
        <!doctype html><html><head><meta charset="utf-8">
        <meta name="color-scheme" content="light dark">
        <style>\(css)</style></head>
        <body class="\(kind.rawValue)"><main>\(body)</main></body></html>
        """
    }

    static let css = """
    :root { --fg:#1d1d1f; --muted:#6e6e73; --accent:#2f8de4; --rule:rgba(0,0,0,.08); --code:rgba(47,141,228,.08);
            --quote:#e9f4fe; }
    @media (prefers-color-scheme: dark) {
      :root { --fg:#ececf0; --muted:#9a9aa2; --accent:#6cb6ff; --rule:rgba(255,255,255,.1);
              --code:rgba(108,182,255,.12); --quote:rgba(108,182,255,.08); }
    }
    html { background: transparent; }
    body { margin:0; font: 15px/1.62 -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif; color: var(--fg);
           -webkit-font-smoothing: antialiased; }
    main { max-width: 760px; margin: 0 auto; padding: 28px 40px 80px; }
    h1 { font-size: 26px; line-height: 1.25; margin: 6px 0 4px; letter-spacing: -.01em; }
    h2 { font-size: 18px; margin: 30px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--rule); }
    h3 { font-size: 15px; margin: 22px 0 6px; }
    h1 + p em, h1 + p { color: var(--muted); font-size: 13px; }
    p { margin: 8px 0; }
    ul, ol { padding-left: 22px; margin: 6px 0; }
    li { margin: 4px 0; }
    li::marker { color: var(--accent); }
    a { color: var(--accent); text-decoration: none; }
    a:hover { text-decoration: underline; }
    strong { font-weight: 600; }
    code { font: 12.5px ui-monospace, "SF Mono", Menlo, monospace; background: var(--code); padding: 1px 5px;
           border-radius: 5px; }
    pre { background: var(--code); padding: 12px 14px; border-radius: 10px; overflow-x: auto; }
    pre code { background: none; padding: 0; }
    blockquote { margin: 12px 0; padding: 4px 14px; border-left: 3px solid var(--accent); background: var(--quote);
                 border-radius: 0 8px 8px 0; }
    hr { border: 0; border-top: 1px solid var(--rule); margin: 24px 0; }
    .ts { color: var(--muted); font: 11.5px ui-monospace, "SF Mono", Menlo, monospace; background: none; padding: 0; }
    /* transcript: one line per speaker turn, timestamps in a quiet gutter */
    body.transcript main p { margin: 0; padding: 5px 0; }
    body.transcript main p + p { border-top: 1px solid var(--rule); }
    body.transcript code.ts { display: inline-block; width: 64px; }
    body.transcript strong { color: var(--accent); }
    """
}

struct MarkdownWebView: NSViewRepresentable {
    let html: String
    let baseURL: URL

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> WKWebView {
        let v = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        v.setValue(false, forKey: "drawsBackground")  // let the window material show through
        v.navigationDelegate = context.coordinator
        return v
    }

    func updateNSView(_ v: WKWebView, context: Context) {
        guard context.coordinator.lastHTML != html else { return }
        context.coordinator.lastHTML = html
        v.loadHTMLString(html, baseURL: baseURL)
    }

    @MainActor
    final class Coordinator: NSObject, WKNavigationDelegate {
        var lastHTML: String?

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
            if action.navigationType == .linkActivated, let url = action.request.url {
                NSWorkspace.shared.open(url)  // links open in the browser / Finder, never inside the viewer
                return .cancel
            }
            return .allow
        }
    }
}
