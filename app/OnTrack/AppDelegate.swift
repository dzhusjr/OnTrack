import AppKit
import Combine
import OnTrackCore

/// Wires capture → engine client → overlay/menu, and supervises the engine process.
@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let settings = Settings()
    private lazy var store = OverlayStore(maxLines: settings.overlayLines, autoShow: settings.autoShowOverlay,
                                          live: settings.liveByDefault)
    private let power = PowerMonitor()
    private var policyAppliedSession: String?
    private lazy var queue = FinalizeQueue(jsonOrEmpty: settings.finalizeQueue)
    private var processingSessionId: String?  // queued session currently being finalized
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
    private lazy var notesWindow = NotesWindowController(notesDir: settings.notesDir) { [weak self] id, question, sid in
        self?.client.send(.askAbout(id: id, question: question, sessionId: sid))
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        try? FileManager.default.createDirectory(at: settings.appSupportDir, withIntermediateDirectories: true)

        overlay = OverlayController(store: store,
                                    onOpenNotes: { [weak self] in
                                        self?.notesWindow.show(sessionId: URL(fileURLWithPath: $0).lastPathComponent)
                                    },
                                    onOpenSettings: { $0 == "mic" ? Permissions.openMicrophoneSettings() : Permissions.openScreenCaptureSettings() },
                                    onSaveTo: { [weak self] in self?.saveTo(notesPath: $0) })
        menu = MenuBarController(actions: .init(
            startStop: { [weak self] in self?.toggleRecording(askName: true) },
            toggleOverlay: { [weak self] in self?.store.send(.toggle) },
            enroll: { [weak self] in self?.startEnrollment() },
            retrySummary: { [weak self] in self?.retrySummary() },
            openSessions: { [weak self] in self?.open(self?.settings.sessionsDir) },
            openNotes: { [weak self] in self?.open(self?.settings.notesDir) },
            openLogs: { [weak self] in self?.open(self?.settings.logsDir) },
            openConfig: { [weak self] in self?.openConfig() },
            toggleAutoShow: { [weak self] in
                guard let self else { return }
                self.settings.autoShowOverlay.toggle()
                self.store.setAutoShow(self.settings.autoShowOverlay)
            },
            chooseExportFolder: { [weak self] in
                guard let self, let folder = Self.pickFolder(title: "Also save every session's notes to…",
                                                             start: self.settings.exportFolder) else { return }
                self.settings.exportFolder = folder
                self.store.showToast("New sessions will also be saved to “\(folder.lastPathComponent)”")
            },
            clearExportFolder: { [weak self] in self?.settings.exportFolder = nil },
            exportFolderName: { [weak self] in self?.settings.exportFolder?.lastPathComponent },
            autoShow: { [weak self] in self?.settings.autoShowOverlay ?? true },
            toggleLive: { [weak self] in self?.store.toggleLive() },
            toggleWaitForPower: { [weak self] in
                guard let self else { return }
                self.settings.waitForPowerToFinalize.toggle()
                if !self.settings.waitForPowerToFinalize, !self.queue.items.isEmpty {
                    self.store.showToast("Finishing \(self.queue.items.count) waiting session(s) now")
                }
                self.processQueue(force: false)
            },
            waitForPower: { [weak self] in self?.settings.waitForPowerToFinalize ?? false },
            queuedCount: { [weak self] in self?.queue.items.count ?? 0 },
            processNow: { [weak self] in self?.processQueue(force: true) },
            quit: { NSApp.terminate(nil) },
            session: .init(
                project: { [weak self] in self?.settings.projectDir },
                recentProjects: { [weak self] in self?.settings.recentProjects ?? [] },
                setProject: { [weak self] path in self?.setProject(path) },
                chooseProject: { [weak self] in
                    guard let self, let folder = Self.pickFolder(
                        title: "Folder Claude should use as context (e.g. a code project)",
                        start: self.settings.projectDir.map { URL(fileURLWithPath: $0) }, prompt: "Use Folder")
                    else { return }
                    self.setProject(folder.path)
                },
                language: { [weak self] in self?.settings.sessionLanguage ?? "auto" },
                setLanguage: { [weak self] code in
                    guard let self else { return }
                    self.settings.sessionLanguage = code
                    self.sendSessionOptions()
                    self.store.showToast(code == "auto" ? "Language: automatic"
                                         : "Transcribing only in \(MenuBarController.languages.first { $0.code == code }?.title ?? code)")
                },
                assist: { [weak self] in self?.settings.suggestReplies ?? true },
                toggleAssist: { [weak self] in
                    guard let self else { return }
                    self.settings.suggestReplies.toggle()
                    self.sendSessionOptions()
                },
                interview: { [weak self] in self?.settings.interviewMode ?? false },
                toggleInterview: { [weak self] in
                    guard let self else { return }
                    self.settings.interviewMode.toggle()
                    if self.settings.interviewMode { self.settings.suggestReplies = true }
                    self.sendSessionOptions()
                    self.store.showToast(self.settings.interviewMode ? "Interview mode: replies suggested for every question"
                                                                     : "Interview mode off")
                },
                suggestNow: { [weak self] in self?.suggestNow() },
                editTriggerWords: { [weak self] in self?.editTriggerWords() },
                saveNotesToProject: { [weak self] in self?.settings.saveNotesToProject ?? false },
                toggleSaveNotesToProject: { [weak self] in
                    guard let self else { return }
                    self.settings.saveNotesToProject.toggle()
                    self.store.showToast(self.settings.saveNotesToProject ? "Notes will also be saved into the project folder"
                                                                          : "Notes no longer saved into the project folder")
                }
            ),
            showNotes: { [weak self] in self?.notesWindow.show() }
        ))

        store.onLiveToggled = { [weak self] on in self?.settings.liveByDefault = on }
        power.onChange = { [weak self] onBattery in
            guard let self, !onBattery, !self.queue.items.isEmpty else { return }
            self.store.showToast("Plugged in — finishing \(self.queue.items.count) saved session(s)")
            self.processQueue(force: false)
        }
        power.start()
        store.sendToEngine = { [weak self] msg in
            guard let self else { return }
            if case let .finalize(sid, action) = msg {
                self.lastFinalizeSessionId = sid
                if PowerPolicy.deferFinalize(action, onBattery: PowerMonitor.onBattery,
                                             waitForPower: self.settings.waitForPowerToFinalize) {
                    self.queue.add(sessionId: sid, action: action)  // audio is kept until it's processed
                    self.saveQueue()
                    self.store.send(.deferredUntilPower)
                    return
                }
            }
            self.client.send(msg)
        }
        store.$state
            .removeDuplicates()
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
        hotkeys.register(id: 3, keyCode: HotkeyService.optionShiftL.keyCode, modifiers: HotkeyService.optionShiftL.modifiers) {
            [weak self] in self?.store.toggleLive()
        }
        hotkeys.register(id: 4, keyCode: HotkeyService.optionShiftP.keyCode, modifiers: HotkeyService.optionShiftP.modifiers) {
            [weak self] in self?.togglePause()
        }
        hotkeys.register(id: 5, keyCode: HotkeyService.optionShiftA.keyCode, modifiers: HotkeyService.optionShiftA.modifiers) {
            [weak self] in self?.suggestNow()
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
        supervisor.noteAlive()  // any message proves the engine is alive, not just pongs
        switch msg {
        case let .answerDelta(id, _) where id.hasPrefix("notes-"), let .answerDone(id) where id.hasPrefix("notes-"):
            notesWindow.receive(msg)
            return  // a question from the Notes window, not the overlay
        case .pong:
            supervisor.noteAlive()
        case let .status(st):
            supervisor.noteAlive()
            if let sid = resumeSessionId {
                if st.state == "recording", st.sessionId == sid {
                    resumeSessionId = nil
                    if store.state.paused { client.send(.setPaused(true)) }  // a restarted engine forgot the pause
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
            if let sid = processingSessionId, err.code == "finalize_failed" || err.code == "no_session" {
                queuedSessionFinished(sid)  // don't block the rest of the queue
            }
        case let .finalizeDone(sid, path):
            if sid == pendingSummarySessionId { pendingSummarySessionId = nil }
            if sid == processingSessionId { queuedSessionFinished(sid) }
            notesWindow.refresh()
            if !path.isEmpty, let folder = settings.exportFolder {
                export(notesPath: path, to: folder)
            }
            if !path.isEmpty, settings.saveNotesToProject, let project = settings.projectDir {
                export(notesPath: path, to: URL(fileURLWithPath: project).appendingPathComponent("OnTrack Notes"))
            }
        default:
            break
        }
        store.send(.engine(msg))
        if case let .status(st) = msg, st.state == "recording", let sid = st.sessionId, sid != policyAppliedSession {
            policyAppliedSession = sid  // new session: apply "live by default" and the battery rule once
            applyLivePolicy(announce: false)
        }
    }

    /// Live transcription on/off from the user's default and the battery rule (recording continues either way).
    private func applyLivePolicy(announce: Bool) {
        let desired = settings.liveByDefault
        guard store.state.recording, desired != store.state.liveTranscription else { return }
        store.send(.toggleLive)
        if announce || !desired {
            store.showToast(desired ? "Live transcription on" : "Recording only — transcribed when you save")
        }
    }

    // MARK: sessions saved on battery

    private func saveQueue() { settings.finalizeQueue = queue.json() }

    /// Finalize queued sessions one at a time (on mains power, or right away when forced / the switch is off).
    private func processQueue(force: Bool) {
        guard processingSessionId == nil, let next = queue.next, client.isConnected else { return }
        if !force && settings.waitForPowerToFinalize && PowerMonitor.onBattery { return }
        processingSessionId = next.sessionId
        lastFinalizeSessionId = next.sessionId
        client.send(.finalize(sessionId: next.sessionId, action: next.finalizeAction))
    }

    private func queuedSessionFinished(_ sid: String) {
        queue.remove(sessionId: sid)
        saveQueue()
        processingSessionId = nil
        processQueue(force: false)
    }

    private func connectionChanged(_ up: Bool) {
        if up {
            sendSessionOptions()  // a (re)started engine starts with defaults
            processingSessionId = nil  // a restarted engine forgot it: try again
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.processQueue(force: false) }
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

    private func sendSessionOptions() {
        guard client.isConnected else { return }
        client.send(.setSessionOptions(settings.sessionOptions))
    }

    private func setProject(_ path: String?) {
        settings.projectDir = path
        sendSessionOptions()
        store.showToast(path.map { "Claude now answers with “\(URL(fileURLWithPath: $0).lastPathComponent)” as context" }
                        ?? "No project folder")
    }

    private func editTriggerWords() {
        let alert = NSAlert()
        alert.messageText = "Names & Trigger Words"
        alert.informativeText = "When someone says one of these, the line is highlighted and (with Auto-Suggest on) "
            + "a short reply is drafted. Separate with commas; case doesn't matter."
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 360, height: 24))
        field.stringValue = settings.triggerWords.joined(separator: ", ")
        field.placeholderString = Settings.defaultTriggerWords.joined(separator: ", ")
        alert.accessoryView = field
        alert.addButton(withTitle: "Save")
        alert.addButton(withTitle: "Cancel")
        alert.addButton(withTitle: "Reset to Default")
        NSApp.activate(ignoringOtherApps: true)
        alert.window.initialFirstResponder = field
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            let words = field.stringValue.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
            settings.triggerWords = words.isEmpty ? Settings.defaultTriggerWords : words
        case .alertThirdButtonReturn:
            settings.triggerWords = Settings.defaultTriggerWords
        default:
            return
        }
        sendSessionOptions()
        store.showToast("Listening for: \(settings.triggerWords.prefix(4).joined(separator: ", "))\(settings.triggerWords.count > 4 ? "…" : "")")
    }

    private func suggestNow() {
        guard store.state.recording else { return }
        store.send(.requestSuggestion(id: UUID().uuidString))
    }

    private func togglePause() {
        guard store.state.recording else { return }
        store.send(.togglePause)
        store.showToast(store.state.paused ? "Paused — nothing is recorded (⌥⇧P to resume)" : "Recording resumed")
    }

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

    // MARK: save to…

    private func saveTo(notesPath: String) {
        let start = UserDefaults.standard.string(forKey: "lastSaveToFolder").map { URL(fileURLWithPath: $0) }
            ?? settings.exportFolder
        guard let folder = Self.pickFolder(title: "Save transcript and summary to…", start: start) else { return }
        UserDefaults.standard.set(folder.path, forKey: "lastSaveToFolder")
        export(notesPath: notesPath, to: folder)
    }

    private func export(notesPath: String, to folder: URL) {
        do {
            let dest = try NotesExporter.export(notesDir: URL(fileURLWithPath: notesPath, isDirectory: true), into: folder)
            store.showToast("Saved to \(dest.deletingLastPathComponent().lastPathComponent)/\(dest.lastPathComponent)")
        } catch {
            store.showIssue("Couldn't save to \(folder.path): \(error.localizedDescription)")
        }
    }

    private static func pickFolder(title: String, start: URL?, prompt: String = "Save Here") -> URL? {
        let panel = NSOpenPanel()
        panel.title = title
        panel.prompt = prompt
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        if let start { panel.directoryURL = start }
        NSApp.activate(ignoringOtherApps: true)
        return panel.runModal() == .OK ? panel.url : nil
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
            # allowed_langs = ["en", "nl", "uk"]   # add "ru" only if you want Russian output
            # vocabulary = ["KU Leuven", "Raccoon Gang", "eigenvalue"]   # names/terms Whisper should spell right
            # final_whisper_model = "mlx-community/whisper-large-v3-mlx" # model for the saved transcript
            # retranscribe = true         # re-run Whisper after the session for a cleaner final transcript
            # live_preview = true         # grey word-by-word preview while someone speaks (more GPU / battery)
            # preview_interval_s = 0.7    # how often the preview updates

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
