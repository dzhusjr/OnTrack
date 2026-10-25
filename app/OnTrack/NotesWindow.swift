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

    init(notesDir: URL) { self.notesDir = notesDir }

    var visible: [NoteSession] { NotesLibrary.filter(sessions, query: query) }
    var selected: NoteSession? { sessions.first { $0.id == selection } }

    func reload() {
        sessions = NotesLibrary.scan(notesDir)
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

    init(notesDir: URL) { model = NotesBrowserModel(notesDir: notesDir) }

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
            if let d = s.date {
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
                Text(session.kinds.map(\.title).joined(separator: " · "))
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
