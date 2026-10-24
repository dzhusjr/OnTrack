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
        var toggleBatteryRule: () -> Void
        var batteryRule: () -> Bool
        var quit: () -> Void
    }

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
        let battery = NSMenuItem(title: "On Battery: Record Only", action: #selector(toggleBatteryRule), keyEquivalent: "")
        battery.target = self
        battery.state = actions.batteryRule() ? .on : .off
        menu.addItem(battery)
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
    @objc private func toggleBatteryRule() { actions.toggleBatteryRule() }
    @objc private func chooseExportFolder() { actions.chooseExportFolder() }
    @objc private func clearExportFolder() { actions.clearExportFolder() }
    @objc private func quit() { actions.quit() }
}
