import AppKit
import OnTrackCore

/// Menu-bar icon (idle / recording / processing / disconnected) and menu (spec §4.1).
final class MenuBarController: NSObject, NSMenuDelegate {
    struct Actions {
        var startStop: () -> Void
        var toggleOverlay: () -> Void
        var enroll: () -> Void
        var retrySummary: () -> Void
        var openSessions: () -> Void
        var openNotes: () -> Void
        var openLogs: () -> Void
        var openConfig: () -> Void
        var toggleAutoShow: () -> Void
        var chooseExportFolder: () -> Void
        var clearExportFolder: () -> Void
        var exportFolderName: () -> String?
        var autoShow: () -> Bool
        var toggleLive: () -> Void
        var toggleWaitForPower: () -> Void
        var waitForPower: () -> Bool
        var queuedCount: () -> Int
        var processNow: () -> Void
        var quit: () -> Void
        var session: SessionMenu
    }

    /// Project folder, language lock and reply helper for the next / current session.
    struct SessionMenu {
        var project: () -> String?
        var recentProjects: () -> [String]
        var setProject: (String?) -> Void
        var chooseProject: () -> Void
        var language: () -> String
        var setLanguage: (String) -> Void
        var assist: () -> Bool
        var toggleAssist: () -> Void
        var interview: () -> Bool
        var toggleInterview: () -> Void
        var suggestNow: () -> Void
    }

    static let languages: [(code: String, title: String)] = [
        ("auto", "Automatic"), ("uk", "Українська"), ("en", "English"), ("nl", "Nederlands"),
    ]

    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let actions: Actions
    private var state = OverlayState()
    private var sessionName: String?
    private var canRetrySummary = false

    init(actions: Actions) {
        self.actions = actions
        super.init()
        let menu = NSMenu()
        menu.delegate = self
        item.menu = menu
        update(state: state, sessionName: nil, canRetrySummary: false)
    }

    func update(state: OverlayState, sessionName: String?, canRetrySummary: Bool) {
        self.state = state
        self.sessionName = sessionName
        self.canRetrySummary = canRetrySummary
        guard let button = item.button else { return }
        let (symbol, tint): (String, NSColor?) = {
            if state.recording { return ("record.circle.fill", .systemRed) }
            switch state.engineState {
            case "postprocessing", "stopping": return ("hourglass", nil)
            case "enrolling", "enroll_compute": return ("person.wave.2", nil)
            case "disconnected": return ("waveform.slash", nil)
            default: return ("waveform", nil)
            }
        }()
        var image = NSImage(systemSymbolName: symbol, accessibilityDescription: "OnTrack")
        if let tint {
            image = image?.withSymbolConfiguration(.init(paletteColors: [tint]))
            image?.isTemplate = false
        } else {
            image?.isTemplate = true
        }
        button.image = image
        button.toolTip = state.recording ? "OnTrack — recording \(sessionName ?? "")" : "OnTrack"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        if state.recording {
            let title = NSMenuItem(title: "● \(sessionName ?? "Recording")", action: nil, keyEquivalent: "")
            title.isEnabled = false
            menu.addItem(title)
        }
        let canStart = state.engineState == "idle" || state.recording
        add(menu, state.recording ? "Stop Session" : "Start Session…", #selector(startStop), key: "s", mods: [.option, .shift],
            enabled: canStart)
        add(menu, "Show / Hide Overlay", #selector(toggleOverlay), key: " ", mods: [.option])
        let live = NSMenuItem(title: "Live Transcription", action: #selector(toggleLive), keyEquivalent: "l")
        live.keyEquivalentModifierMask = [.option, .shift]
        live.target = self
        live.state = state.liveTranscription ? .on : .off
        menu.addItem(live)
        let power = NSMenuItem(title: "Transcribe After Recording Only When Plugged In",
                               action: #selector(toggleWaitForPower), keyEquivalent: "")
        power.target = self
        power.state = actions.waitForPower() ? .on : .off
        menu.addItem(power)
        let queued = actions.queuedCount()
        if queued > 0 {
            let waiting = NSMenuItem(title: "Waiting for power: \(queued) session\(queued == 1 ? "" : "s")",
                                     action: nil, keyEquivalent: "")
            waiting.isEnabled = false
            menu.addItem(waiting)
            add(menu, "Process Now", #selector(processNow))
        }
        addSessionItems(menu)
        let auto = NSMenuItem(title: "Show Live Bar When Recording", action: #selector(toggleAutoShow), keyEquivalent: "")
        auto.target = self
        auto.state = actions.autoShow() ? .on : .off
        menu.addItem(auto)
        menu.addItem(.separator())
        add(menu, "Enroll My Voice…", #selector(enroll), enabled: state.engineState == "idle")
        add(menu, "Retry Summary", #selector(retrySummary), enabled: canRetrySummary)
        menu.addItem(.separator())
        add(menu, "Open Sessions Folder", #selector(openSessions))
        add(menu, "Open Notes Folder", #selector(openNotes))
        if let name = actions.exportFolderName() {
            add(menu, "Also Saving Notes to “\(name)” — Change…", #selector(chooseExportFolder))
            add(menu, "Stop Saving Notes to “\(name)”", #selector(clearExportFolder))
        } else {
            add(menu, "Also Save Notes To…", #selector(chooseExportFolder))
        }
        add(menu, "Open Logs", #selector(openLogs))
        add(menu, "Edit Engine Config…", #selector(openConfig))
        menu.addItem(.separator())
        let status = NSMenuItem(title: "Engine: \(state.engineState)\(state.modelReady ? "" : " (loading model)")",
                                action: nil, keyEquivalent: "")
        status.isEnabled = false
        menu.addItem(status)
        add(menu, "Quit OnTrack", #selector(quit), key: "q")
    }

    private func addSessionItems(_ menu: NSMenu) {
        menu.addItem(.separator())
        let s = actions.session
        let current = s.project()
        let projectItem = NSMenuItem(title: "Project: \(current.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "None")",
                                     action: nil, keyEquivalent: "")
        let projects = NSMenu()
        let none = NSMenuItem(title: "None", action: #selector(pickProject(_:)), keyEquivalent: "")
        none.target = self
        none.state = current == nil ? .on : .off
        projects.addItem(none)
        for path in s.recentProjects() {
            let mi = NSMenuItem(title: URL(fileURLWithPath: path).lastPathComponent, action: #selector(pickProject(_:)),
                                keyEquivalent: "")
            mi.target = self
            mi.representedObject = path
            mi.toolTip = path
            mi.state = path == current ? .on : .off
            projects.addItem(mi)
        }
        projects.addItem(.separator())
        let choose = NSMenuItem(title: "Choose Folder…", action: #selector(chooseProject), keyEquivalent: "")
        choose.target = self
        projects.addItem(choose)
        projectItem.submenu = projects
        projectItem.toolTip = "Claude answers questions and suggests replies with this folder (and its CLAUDE.md) as context"
        menu.addItem(projectItem)

        let lang = s.language()
        let langItem = NSMenuItem(title: "Language: \(Self.languages.first { $0.code == lang }?.title ?? lang)",
                                  action: nil, keyEquivalent: "")
        let langs = NSMenu()
        for (code, title) in Self.languages {
            let mi = NSMenuItem(title: title, action: #selector(pickLanguage(_:)), keyEquivalent: "")
            mi.target = self
            mi.representedObject = code
            mi.state = code == lang ? .on : .off
            langs.addItem(mi)
        }
        langItem.submenu = langs
        langItem.toolTip = "Lock transcription to one language when the whole conversation is in it (most accurate)"
        menu.addItem(langItem)

        let assist = NSMenuItem(title: "Suggest a Reply When Someone Says My Name", action: #selector(toggleAssist),
                                keyEquivalent: "")
        assist.target = self
        assist.state = s.assist() ? .on : .off
        menu.addItem(assist)
        let interview = NSMenuItem(title: "Interview Mode — Suggest for Every Question", action: #selector(toggleInterview),
                                   keyEquivalent: "")
        interview.target = self
        interview.state = s.interview() ? .on : .off
        menu.addItem(interview)
        add(menu, "Suggest a Reply Now", #selector(suggestNow), key: "a", mods: [.option, .shift], enabled: state.recording)
        menu.addItem(.separator())
    }

    @objc private func pickProject(_ sender: NSMenuItem) { actions.session.setProject(sender.representedObject as? String) }
    @objc private func chooseProject() { actions.session.chooseProject() }
    @objc private func pickLanguage(_ sender: NSMenuItem) { actions.session.setLanguage(sender.representedObject as? String ?? "auto") }
    @objc private func toggleAssist() { actions.session.toggleAssist() }
    @objc private func toggleInterview() { actions.session.toggleInterview() }
    @objc private func suggestNow() { actions.session.suggestNow() }

    private func add(_ menu: NSMenu, _ title: String, _ sel: Selector, key: String = "",
                     mods: NSEvent.ModifierFlags = [.command], enabled: Bool = true) {
        let mi = NSMenuItem(title: title, action: enabled ? sel : nil, keyEquivalent: key)
        mi.keyEquivalentModifierMask = mods
        mi.target = self
        mi.isEnabled = enabled
        menu.addItem(mi)
    }

    @objc private func startStop() { actions.startStop() }
    @objc private func toggleOverlay() { actions.toggleOverlay() }
    @objc private func enroll() { actions.enroll() }
    @objc private func retrySummary() { actions.retrySummary() }
    @objc private func openSessions() { actions.openSessions() }
    @objc private func openNotes() { actions.openNotes() }
    @objc private func openLogs() { actions.openLogs() }
    @objc private func openConfig() { actions.openConfig() }
    @objc private func toggleAutoShow() { actions.toggleAutoShow() }
    @objc private func toggleLive() { actions.toggleLive() }
    @objc private func toggleWaitForPower() { actions.toggleWaitForPower() }
    @objc private func processNow() { actions.processNow() }
    @objc private func chooseExportFolder() { actions.chooseExportFolder() }
    @objc private func clearExportFolder() { actions.clearExportFolder() }
    @objc private func quit() { actions.quit() }
}
