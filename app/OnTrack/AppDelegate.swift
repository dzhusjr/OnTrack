import AppKit
import Combine
import OnTrackCore

/// Wires capture → engine client → overlay/menu, and supervises the engine process.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let settings = Settings()
    private lazy var store = OverlayStore(maxLines: settings.overlayLines)
    private lazy var client = EngineClient(directory: settings.appSupportDir)
    private lazy var supervisor = EngineSupervisor(engineDir: settings.engineDir, logDir: settings.logsDir)
    private let capture = CaptureService()
    private let hotkeys = HotkeyService()
    private var menu: MenuBarController!
    private var overlay: OverlayController!
    private var cancellables: Set<AnyCancellable> = []
    private var enrolling = false
    private var resumeSessionId: String?  // set while waiting for a restarted engine to resume our session
    private var lastFinalizeSessionId: String?
    private var pendingSummarySessionId: String?

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: settings.appSupportDir, withIntermediateDirectories: true)

        overlay = OverlayController(store: store,
                                    onOpenNotes: { NSWorkspace.shared.open(URL(fileURLWithPath: $0)) },
                                    onOpenSettings: { $0 == "mic" ? Permissions.openMicrophoneSettings() : Permissions.openScreenCaptureSettings() })
        menu = MenuBarController(actions: .init(
            startStop: { [weak self] in self?.toggleRecording(askName: true) },
            toggleOverlay: { [weak self] in self?.store.send(.toggle) },
            enroll: { [weak self] in self?.startEnrollment() },
            retrySummary: { [weak self] in self?.retrySummary() },
            openSessions: { [weak self] in self?.open(self?.settings.sessionsDir) },
            openNotes: { [weak self] in self?.open(self?.settings.notesDir) },
            openLogs: { [weak self] in self?.open(self?.settings.logsDir) },
            openConfig: { [weak self] in self?.openConfig() },
            quit: { NSApp.terminate(nil) }
        ))

        store.sendToEngine = { [weak self] msg in
            if case let .finalize(sid, _) = msg { self?.lastFinalizeSessionId = sid }
            self?.client.send(msg)
        }
        store.$state
            .receive(on: RunLoop.main)
            .sink { [weak self] state in
                guard let self else { return }
                self.menu.update(state: state, sessionName: self.store.sessionName,
                                 canRetrySummary: self.pendingSummarySessionId != nil)
            }
            .store(in: &cancellables)

        client.onMessage = { [weak self] msg in self?.handle(msg) }
        client.onConnectionChange = { [weak self] up in self?.connectionChanged(up) }
        capture.echoCancellation = settings.echoCancellation
        let client = self.client  // called on audio threads: don't touch main-actor state
        capture.onFrame = { channel, t, samples in
            client.sendAudio(channel: channel, tNs: t, samples: samples)
        }
        capture.onIssue = { [weak self] text in
            if text == "[audio source changed]" { return }
            self?.store.showIssue(text)
        }

        hotkeys.register(id: 1, keyCode: HotkeyService.optionSpace.keyCode, modifiers: HotkeyService.optionSpace.modifiers) {
            [weak self] in self?.store.send(.toggle)
        }
        hotkeys.register(id: 2, keyCode: HotkeyService.optionShiftS.keyCode, modifiers: HotkeyService.optionShiftS.modifiers) {
            [weak self] in self?.toggleRecording(askName: false)
        }

        supervisor.onFatal = { [weak self] text in self?.store.showIssue(text) }
        supervisor.start()
        client.start()
        supervisor.startHealthChecks(client: client)

        Task {
            let micOK = await Permissions.requestMicrophone()
            if !micOK { store.showIssue("Microphone access is off — OnTrack can't hear the room. Allow it in System Settings → Privacy & Security → Microphone.") }
            if !Permissions.screenCaptureAllowed { Permissions.requestScreenCapture() }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        capture.stop()
        if store.state.recording { client.send(.stopSession) }
        // SIGTERM lets the engine finish the open utterance and close the session cleanly.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.client.stop()
            self?.supervisor.stop()
            NSApp.reply(toApplicationShouldTerminate: true)
        }
        return .terminateLater
    }

    // MARK: engine events

    private func handle(_ msg: Inbound) {
        switch msg {
        case .pong:
            supervisor.noteAlive()
        case let .status(st):
            supervisor.noteAlive()
            if let sid = resumeSessionId {
                if st.state == "recording", st.sessionId == sid {
                    resumeSessionId = nil
                    client.releaseAudio()
                } else {
                    return  // fresh engine still idle: don't let it end our session in the UI
                }
            } else if st.state == "recording" || st.state == "enrolling" {
                client.releaseAudio()
            }
        case .enrollDone:
            finishEnrollment()
        case let .error(err):
            if enrolling && err.code == "enroll_failed" { finishEnrollment() }
            if err.code == "summary_failed" { pendingSummarySessionId = lastFinalizeSessionId }
        case let .finalizeDone(sid, _):
            if sid == pendingSummarySessionId { pendingSummarySessionId = nil }
        default:
            break
        }
        store.send(.engine(msg))
    }

    private func connectionChanged(_ up: Bool) {
        if up {
            // Engine restarted mid-session → resume the same session (spec §8). Audio stays buffered until confirmed.
            if store.state.recording, let sid = store.state.sessionId {
                resumeSessionId = sid
                client.send(.startSession(name: store.sessionName ?? "Session", sessionId: sid))
            } else if enrolling {
                client.send(.enrollStart)
            }
        } else {
            store.send(.disconnected)
        }
    }

    // MARK: actions

    private func toggleRecording(askName: Bool) {
        if store.state.recording {
            client.send(.stopSession)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in self?.capture.stop() }
            return
        }
        guard ["idle", "postprocessing"].contains(store.state.engineState) else {
            store.showIssue("The engine is \(store.state.engineState) — try again in a moment.")
            return
        }
        // Starting the next lecture while the previous end card is open: save the previous one (the default action).
        if let card = store.state.endCard, store.state.mode == .endCard || store.state.mode == .done {
            if store.state.mode == .endCard { client.send(.finalize(sessionId: card.sessionId, action: .saveAll)) }
            lastFinalizeSessionId = card.sessionId
            store.send(.dismiss)
        }
        let fallback = "Session \(Self.timeFormatter.string(from: Date()))"
        let name: String
        if askName {
            guard let n = Self.promptForName(defaultName: fallback) else { return }
            name = n.isEmpty ? fallback : n
        } else {
            name = fallback
        }
        store.sessionName = name
        client.holdAudio()  // released when the engine reports status=recording
        client.send(.startSession(name: name, sessionId: nil))
        capture.start()
    }

    private func startEnrollment() {
        let alert = NSAlert()
        alert.messageText = "Enroll your voice"
        alert.informativeText = "After you click Start, talk normally for 30 seconds (read something aloud, any language). OnTrack uses this to tell you apart from other people in the room."
        alert.addButton(withTitle: "Start")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        enrolling = true
        store.showToast("Listening — keep talking for 30 seconds…")
        client.holdAudio()
        client.send(.enrollStart)
        capture.start(micOnly: true)
        DispatchQueue.main.asyncAfter(deadline: .now() + 90) { [weak self] in  // engine never answered
            guard let self, self.enrolling else { return }
            self.finishEnrollment()
            self.store.showIssue("Enrollment timed out — is the engine running? (menu → Open Logs)")
        }
    }

    private func finishEnrollment() {
        enrolling = false
        capture.stop()
    }

    private func retrySummary() {
        guard let sid = pendingSummarySessionId else { return }
        client.send(.retrySummary(sessionId: sid))
        store.showToast("Retrying summary…")
    }

    private func open(_ url: URL?) {
        guard let url else { return }
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    private func openConfig() {
        let url = settings.configFile
        if !FileManager.default.fileExists(atPath: url.path) {
            let template = """
            # OnTrack engine settings (restart OnTrack after editing). All keys optional.
            # tau = 0.55                  # voice match threshold: higher = stricter "Me"
            # llm_model = "sonnet"        # model used for questions and summaries
            # claude_path = ""            # full path to `claude` if OnTrack can't find it
            # allowed_langs = ["en", "ru", "uk"]
            # retranscribe = true         # re-run Whisper after the session for a cleaner final transcript

            """
            try? template.write(to: url, atomically: true, encoding: .utf8)
        }
        NSWorkspace.shared.open(url)
    }

    // MARK: helpers

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        return f
    }()

    private static func promptForName(defaultName: String) -> String? {
        let alert = NSAlert()
        alert.messageText = "Start session"
        alert.informativeText = "Name this lecture, meeting or conversation:"
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 280, height: 24))
        field.placeholderString = defaultName
        alert.accessoryView = field
        alert.addButton(withTitle: "Start")
        alert.addButton(withTitle: "Cancel")
        NSApp.activate(ignoringOtherApps: true)
        alert.window.initialFirstResponder = field
        return alert.runModal() == .alertFirstButtonReturn ? field.stringValue.trimmingCharacters(in: .whitespaces) : nil
    }
}
