import AVFoundation
import AppKit
import OnTrackCore
import SwiftUI

/// What the Settings window can ask the app to do (it owns the engine connection and the overlay).
struct SettingsActions {
    var sessionOptionsChanged: () -> Void  // names, language, replies, connectors, project
    var autoShowChanged: (Bool) -> Void
    var projectChanged: (String?) -> Void
    var saveToProjectChanged: () -> Void
    var waitForPowerChanged: () -> Void  // turning it off starts sessions that were waiting
    var chooseFolder: (_ title: String, _ start: URL?) -> URL?
    var enroll: () -> Void
    var showPermissions: () -> Void
    var showNotes: () -> Void
    var openConfig: () -> Void
    var restartEngine: () -> Void
    var open: (URL) -> Void
}

/// Values shown in the Settings window. Every change is written to UserDefaults right away.
@MainActor
final class SettingsModel: ObservableObject {
    private let settings = Settings()
    let actions: SettingsActions

    @Published var autoShow = true { didSet { guard loaded else { return }; settings.autoShowOverlay = autoShow; actions.autoShowChanged(autoShow) } }
    @Published var liveByDefault = true { didSet { guard loaded else { return }; settings.liveByDefault = liveByDefault } }
    @Published var language = "auto" { didSet { guard loaded else { return }; settings.sessionLanguage = language; actions.sessionOptionsChanged() } }
    @Published var names = "" // committed on Return / leaving the field
    @Published var autoSuggest = true { didSet { guard loaded else { return }; settings.suggestReplies = autoSuggest; actions.sessionOptionsChanged() } }
    @Published var interview = false { didSet { guard loaded else { return }; settings.interviewMode = interview; actions.sessionOptionsChanged() } }
    @Published var connectors = false { didSet { guard loaded else { return }; settings.useConnectors = connectors; actions.sessionOptionsChanged() } }
    @Published var project: String?
    @Published var recentProjects: [String] = []
    @Published var saveToProject = true { didSet { guard loaded else { return }; settings.saveNotesToProject = saveToProject; actions.saveToProjectChanged() } }
    @Published var improveAfterRecording = false { didSet { guard loaded else { return }; settings.improveAfterRecording = improveAfterRecording } }
    @Published var waitForPower = false { didSet { guard loaded else { return }; settings.waitForPowerToFinalize = waitForPower; actions.waitForPowerChanged() } }
    @Published var exportFolder: URL?
    @Published var hasVoiceprint = false
    @Published var micGranted = false
    @Published var systemAudioGranted = false
    private var loaded = false

    var languageTitle: String { MenuBarController.languages.first { $0.code == language }?.title ?? language }
    var projectName: String { project.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "" }
    var permissionsOK: Bool { micGranted && systemAudioGranted }
    var permissionsSummary: String {
        switch (micGranted, systemAudioGranted) {
        case (true, true): return "Both granted."
        case (false, true): return "Microphone is missing."
        case (true, false): return "System audio is missing, so the other side of calls is not heard."
        case (false, false): return "Both are missing."
        }
    }

    init(actions: SettingsActions) {
        self.actions = actions
        reload()
    }

    /// Re-read everything (the menu can change the project or language while the window is closed).
    func reload() {
        loaded = false
        autoShow = settings.autoShowOverlay
        liveByDefault = settings.liveByDefault
        language = settings.sessionLanguage
        names = settings.triggerWords.joined(separator: ", ")
        autoSuggest = settings.suggestReplies
        interview = settings.interviewMode
        connectors = settings.useConnectors
        project = settings.projectDir
        recentProjects = settings.recentProjects
        saveToProject = settings.saveNotesToProject
        improveAfterRecording = settings.improveAfterRecording
        waitForPower = settings.waitForPowerToFinalize
        exportFolder = settings.exportFolder
        loadEngine()
        hasVoiceprint = FileManager.default.fileExists(atPath: settings.appSupportDir.appendingPathComponent("voiceprint.npy").path)
        micGranted = AVCaptureDevice.authorizationStatus(for: .audio) == .authorized
        systemAudioGranted = Permissions.screenCaptureAllowed
        loaded = true
    }


    // MARK: engine config (config.toml); empty = the engine's default
    static let engineDefaults: [String: String] = [
        "whisper_model": "mlx-community/whisper-large-v3-turbo",
        "final_whisper_model": "mlx-community/whisper-large-v3-mlx",
        "llm_model": "sonnet",
        "fast_llm_model": "haiku",
        "allowed_langs": "en, nl, uk",
    ]
    @Published var liveModel = ""
    @Published var finalModel = ""
    @Published var summaryModel = ""
    @Published var fastModel = ""
    @Published var aboutMe = ""
    @Published var vocabulary = ""
    @Published var languages = ""
    @Published var livePreview = true { didSet { guard loaded else { return }; commitEngine() } }
    @Published var collectSpeech = true { didSet { guard loaded else { return }; commitEngine() } }
    @Published var engineChanged = false  // saved, but the engine only reads it at launch

    private var configFile: URL { settings.configFile }

    private func loadEngine() {
        let c = EngineConfigFile(url: configFile)
        liveModel = c.string("whisper_model") ?? ""
        finalModel = c.string("final_whisper_model") ?? ""
        summaryModel = c.string("llm_model") ?? ""
        fastModel = c.string("fast_llm_model") ?? ""
        aboutMe = c.string("about_me") ?? ""
        vocabulary = (c.strings("vocabulary") ?? []).joined(separator: ", ")
        languages = (c.strings("allowed_langs") ?? []).joined(separator: ", ")
        livePreview = c.bool("live_preview") ?? true
        collectSpeech = c.bool("collect_my_speech") ?? true
    }

    private static func list(_ s: String) -> [String] {
        s.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }

    /// Writes the fields to config.toml. A field equal to the default (or empty) removes its key.
    func commitEngine() {
        var c = EngineConfigFile(url: configFile)
        let before = c.text
        func text(_ key: String, _ value: String) {
            let v = value.trimmingCharacters(in: .whitespaces)
            c.set(key, string: v.isEmpty || v == Self.engineDefaults[key] ? nil : v)
        }
        text("whisper_model", liveModel)
        text("final_whisper_model", finalModel)
        text("llm_model", summaryModel)
        text("fast_llm_model", fastModel)
        text("about_me", aboutMe)
        let vocab = Self.list(vocabulary)
        c.set("vocabulary", strings: vocab.isEmpty ? nil : vocab)
        let langs = Self.list(languages).map { $0.lowercased() }
        c.set("allowed_langs", strings: langs.isEmpty || langs == ["en", "nl", "uk"] ? nil : langs)
        c.set("live_preview", bool: livePreview ? nil : false)
        c.set("collect_my_speech", bool: collectSpeech ? nil : false)
        guard c.text != before else { return }
        do {
            try c.write(to: configFile)
            engineChanged = true
        } catch {
            NSSound.beep()
        }
    }

    func restartEngine() {
        commitEngine()
        engineChanged = false
        actions.restartEngine()
    }

    var notesDir: URL { settings.notesDir }
    var sessionsDir: URL { settings.sessionsDir }
    var logsDir: URL { settings.logsDir }

    func commitNames() {
        let words = names.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        guard words != settings.triggerWords else { return }
        settings.triggerWords = words
        names = words.joined(separator: ", ")
        actions.sessionOptionsChanged()
    }

    func setProject(_ path: String?) {
        actions.projectChanged(path)  // saves it, tells the engine, refreshes the save card
        reload()
    }

    func chooseProject() {
        let start = project.map { URL(fileURLWithPath: $0) }
        guard let url = actions.chooseFolder("Folder Claude uses as context (e.g. your work repos)", start) else { return }
        setProject(url.path)
    }

    func chooseExportFolder() {
        guard let url = actions.chooseFolder("Also save every session's notes to", exportFolder) else { return }
        settings.exportFolder = url
        exportFolder = url
    }

    func clearExportFolder() {
        settings.exportFolder = nil
        exportFolder = nil
    }
}

// MARK: window

@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let model: SettingsModel

    init(actions: SettingsActions) { model = SettingsModel(actions: actions) }

    func show() {
        model.reload()
        if window == nil {
            // Native preferences look: tabs as toolbar icons, window titled after the tab.
            let tabs = NSTabViewController()
            tabs.tabStyle = .toolbar
            tabs.canPropagateSelectedChildViewControllerTitle = true
            for (title, icon, view) in [
                ("General", "gearshape", AnyView(GeneralTab(model: model))),
                ("Assistant", "sparkles", AnyView(AssistantTab(model: model))),
                ("Saving", "tray.and.arrow.down", AnyView(SavingTab(model: model))),
                ("Advanced", "wrench.and.screwdriver", AnyView(AdvancedTab(model: model))),
            ] {
                let vc = NSHostingController(rootView: view.contentMargins(.top, 14, for: .scrollContent).contentMargins(.bottom, 12, for: .scrollContent).frame(width: 520, height: 560).background(Color(nsColor: .windowBackgroundColor)))
                vc.title = title
                let item = NSTabViewItem(viewController: vc)
                item.label = title
                item.image = NSImage(systemSymbolName: icon, accessibilityDescription: title)
                tabs.addTabViewItem(item)
            }
            let w = NSWindow(contentViewController: tabs)
            w.styleMask = [.titled, .closable]
            w.toolbarStyle = .preference
            w.isReleasedWhenClosed = false
            w.center()
            w.delegate = self
            window = w
        }
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowDidBecomeKey(_ notification: Notification) { model.reload() }

    func windowWillClose(_ notification: Notification) {
        model.commitNames()
        if !NSApp.windows.contains(where: { $0 !== window && $0.isVisible && $0.styleMask.contains(.titled) }) {
            NSApp.setActivationPolicy(.accessory)  // back to menu bar only (unless Notes is still open)
        }
    }
}

// MARK: views

private struct GeneralTab: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        Form {
            Section("Overlay") {
                Toggle(isOn: $model.autoShow) {
                    Text("Show the live bar when recording starts")
                    Text(model.autoShow ? "The overlay opens when you start a session."
                                        : "Sessions start quietly. Show the overlay with ⌥Space.")
                }
                Toggle(isOn: $model.liveByDefault) {
                    Text("Live transcription when a session starts")
                    Text(model.liveByDefault ? "Text appears as people speak. Pause it anytime with ⌥⇧L."
                                             : "Only audio is recorded and transcribed when you save. Turn it on anytime with ⌥⇧L.")
                }
            }
            Section("Language") {
                Picker(selection: $model.language) {
                    ForEach(MenuBarController.languages, id: \.code) { Text($0.title).tag($0.code) }
                } label: {
                    Text("Transcribe in")
                    Text(model.language == "auto"
                         ? "Detects English, Dutch and Ukrainian phrase by phrase."
                         : "Everything is transcribed as \(model.languageTitle). Use Automatic for mixed talks.")
                }
            }
            Section("Permissions") {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Microphone and system audio")
                        Caption(model.permissionsSummary)
                    }
                    Spacer()
                    Button(model.permissionsOK ? "Check…" : "Fix…") { model.actions.showPermissions() }
                }
            }
            Section("Your voice") {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Voiceprint")
                        Caption(model.hasVoiceprint ? "Saved. Your lines are labelled Me."
                                                    : "Not set. 30 seconds of you talking, so your lines are labelled Me.")
                    }
                    Spacer()
                    Button(model.hasVoiceprint ? "Enroll Again…" : "Enroll My Voice…") { model.actions.enroll() }
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct AssistantTab: View {
    @ObservedObject var model: SettingsModel
    @FocusState private var namesFocused: Bool

    var body: some View {
        Form {
            Section("You") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Names and trigger words")
                    TextField("Names and trigger words", text: $model.names, prompt: Text("e.g. Alex, Sasha, Олекса"))
                        .labelsHidden()
                        .textFieldStyle(.roundedBorder)
                        .focused($namesFocused)
                        .onSubmit { model.commitNames() }
                        .onChange(of: namesFocused) { _, focused in if !focused { model.commitNames() } }
                }
                Caption(model.names.trimmingCharacters(in: .whitespaces).isEmpty
                        ? "No names yet, so nothing is highlighted. Separate several with commas."
                        : "Lines that mention these are highlighted. Separate with commas.")
            }
            Section("Replies") {
                Toggle(isOn: $model.autoSuggest) {
                    Text("Suggest a reply when someone says my name")
                    Text(!model.autoSuggest ? "Lines are only highlighted. Ask for a reply anytime with ⌥⇧A."
                         : model.names.trimmingCharacters(in: .whitespaces).isEmpty ? "Add your names above first."
                         : "A short reply appears when someone says one of your names.")
                }
                Toggle(isOn: $model.interview) {
                    Text("Interview mode")
                    Text(model.interview ? "A reply appears for every question someone asks, from general knowledge, without your project."
                                         : "Off. Replies only when someone says your name.")
                }
                Toggle(isOn: $model.connectors) {
                    Text("Use connectors (Jira, mail, calendar)")
                    Text(model.connectors ? "Claude checks your Claude Code connectors when a question needs them. Adds a few seconds."
                                          : "Off. Fastest answers, from the talk, your project and git only.")
                }
            }
            Section("Project folder") {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(model.project.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "None")
                            .help(model.project ?? "")
                        Caption(model.project == nil ? "Answers use only the conversation."
                                                     : "Answers use this folder, its git history and CLAUDE.md (read only).")
                    }
                    Spacer()
                    if !model.recentProjects.isEmpty {
                        Menu("Recent") {
                            ForEach(model.recentProjects, id: \.self) { p in
                                Button(URL(fileURLWithPath: p).lastPathComponent) { model.setProject(p) }
                            }
                        }
                        .fixedSize()
                    }
                    Button("Choose…") { model.chooseProject() }
                    if model.project != nil { Button("Clear") { model.setProject(nil) } }
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct SavingTab: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        Form {
            Section("After recording") {
                Toggle(isOn: $model.improveAfterRecording) {
                    Text("Improve the transcript")
                    Text(model.improveAfterRecording
                         ? "The recording is transcribed again with the accurate model and speakers are separated. Takes minutes and uses battery."
                         : "The live transcript is saved right away and uses almost no battery. Improve any session later in Notes.")
                }
                if model.improveAfterRecording {
                    Toggle(isOn: $model.waitForPower) {
                        Text("Only when plugged in")
                        Text(model.waitForPower ? "On battery, sessions wait until your Mac is plugged in."
                                                : "Starts right after you save, also on battery.")
                    }
                }
            }
            Section("Where notes go") {
                PathRow(title: "Notes", url: model.notesDir) {
                    Button("Open") { model.actions.showNotes() }
                }
                Toggle(isOn: $model.saveToProject) {
                    Text("Also save into the project folder")
                    Text(model.project == nil ? "Choose a project folder first (Assistant tab)."
                         : model.saveToProject ? "Copies each session to \(model.projectName)/Meetings/<date>."
                         : "Off. Notes stay in the notes folder.")
                }
                .disabled(model.project == nil)
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Also copy to another folder")
                        Caption(model.exportFolder.map { "Every session is also copied to \($0.lastPathComponent)." }
                                ?? "Off. Useful for iCloud Drive or a course folder.")
                    }
                    Spacer()
                    Button(model.exportFolder == nil ? "Choose…" : "Change…") { model.chooseExportFolder() }
                    if model.exportFolder != nil { Button("Stop") { model.clearExportFolder() } }
                }
            }
        }
        .formStyle(.grouped)
    }
}

private struct AdvancedTab: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        Form {
            Section("Transcription") {
                EngineField(title: "Live model", caption: "Used while people speak. A Hugging Face MLX Whisper repo.",
                            prompt: SettingsModel.engineDefaults["whisper_model"]!, text: $model.liveModel, commit: model.commitEngine)
                EngineField(title: "Saved transcript model", caption: "Used when a session is improved. Slower and more accurate.",
                            prompt: SettingsModel.engineDefaults["final_whisper_model"]!, text: $model.finalModel, commit: model.commitEngine)
                EngineField(title: "Languages", caption: "Codes the engine may pick from, separated by commas. Add ru only if you want Russian output.",
                            prompt: SettingsModel.engineDefaults["allowed_langs"]!, text: $model.languages, commit: model.commitEngine)
                EngineField(title: "Vocabulary", caption: "Names and terms Whisper should spell right, separated by commas.",
                            prompt: "Kubernetes, eigenvalue", text: $model.vocabulary, commit: model.commitEngine)
                Toggle(isOn: $model.livePreview) {
                    Text("Word-by-word preview")
                    Text(model.livePreview ? "A grey preview line while someone speaks. Uses more battery."
                                           : "Lines appear once a phrase is finished. Easier on the battery.")
                }
            }
            Section("Claude") {
                EngineField(title: "Summary model", caption: "Writes the end-of-session notes.",
                            prompt: SettingsModel.engineDefaults["llm_model"]!, text: $model.summaryModel, commit: model.commitEngine)
                EngineField(title: "Fast model", caption: "Answers live questions and drafts replies.",
                            prompt: SettingsModel.engineDefaults["fast_llm_model"]!, text: $model.fastModel, commit: model.commitEngine)
                EngineField(title: "About me", caption: "One line for Interview Mode, e.g. a backend engineer (Python, Go).",
                            prompt: "a software engineer", text: $model.aboutMe, commit: model.commitEngine)
                Toggle(isOn: $model.collectSpeech) {
                    Text("Learn how I speak")
                    Text(model.collectSpeech ? "Your lines are kept on this Mac so replies sound like you."
                                             : "Replies use a neutral, polite style.")
                }
            }
            Section("Engine") {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(model.engineChanged ? "Restart to apply" : "Config file")
                        Caption(model.engineChanged ? "Changes are saved. The engine reads them when it starts."
                                                    : "Everything else (thresholds, timeouts) lives in config.toml.")
                    }
                    Spacer()
                    if model.engineChanged { Button("Restart Engine") { model.restartEngine() }.buttonStyle(.borderedProminent) }
                    Button("Open File…") { model.actions.openConfig() }
                }
            }
            Section("Folders") {
                PathRow(title: "Sessions", url: model.sessionsDir) { Button("Show") { model.actions.open(model.sessionsDir) } }
                PathRow(title: "Logs", url: model.logsDir) { Button("Show") { model.actions.open(model.logsDir) } }
            }
        }
        .formStyle(.grouped)
        .onDisappear { model.commitEngine() }
    }
}

/// A text setting with its explanation underneath; saved on Return or when the field loses focus.
private struct EngineField: View {
    let title: String
    let caption: String
    let prompt: String
    @Binding var text: String
    let commit: () -> Void
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
            TextField(title, text: $text, prompt: Text(prompt))
                .labelsHidden()
                .textFieldStyle(.roundedBorder)
                .focused($focused)
                .onSubmit(commit)
                .onChange(of: focused) { _, now in if !now { commit() } }
            Caption(caption)
        }
    }
}

// MARK: small pieces

private struct Caption: View {
    let text: String
    init(_ text: String) { self.text = text }

    var body: some View {
        Text(text).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
    }
}

private struct PathRow<Trailing: View>: View {
    let title: String
    let url: URL
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack {
            Text(title)
            Spacer()
            Text(url.path.replacingOccurrences(of: NSHomeDirectory(), with: "~"))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.middle)
                .help(url.path)
            trailing()
        }
    }
}
