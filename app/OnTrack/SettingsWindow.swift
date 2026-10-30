import AppKit
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
    private var loaded = false

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
        loaded = true
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
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 460),
                             styleMask: [.titled, .closable], backing: .buffered, defer: false)
            w.title = "OnTrack Settings"
            w.isReleasedWhenClosed = false
            w.contentViewController = NSHostingController(rootView: SettingsView(model: model))
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

struct SettingsView: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        TabView {
            GeneralTab(model: model).tabItem { Label("General", systemImage: "gearshape") }
            AssistantTab(model: model).tabItem { Label("Assistant", systemImage: "sparkles") }
            SavingTab(model: model).tabItem { Label("Saving", systemImage: "tray.and.arrow.down") }
            AdvancedTab(model: model).tabItem { Label("Advanced", systemImage: "wrench.and.screwdriver") }
        }
        .frame(width: 560, height: 460)
    }
}

private struct GeneralTab: View {
    @ObservedObject var model: SettingsModel

    var body: some View {
        Form {
            Section("Overlay") {
                Toggle("Show the live bar when recording starts", isOn: $model.autoShow)
                Toggle("Live transcription when a session starts", isOn: $model.liveByDefault)
                Note("Off: the session is only recorded and transcribed when you save. Toggle anytime with ⌥⇧L.")
            }
            Section("Language") {
                Picker("Transcribe in", selection: $model.language) {
                    ForEach(MenuBarController.languages, id: \.code) { Text($0.title).tag($0.code) }
                }
                Note("Pick one language when the whole conversation is in it. That is the most accurate.")
            }
            Section("Permissions") {
                HStack {
                    Text("Microphone and system audio")
                    Spacer()
                    Button("Check…") { model.actions.showPermissions() }
                }
            }
            Section("Your voice") {
                HStack {
                    Text("Voiceprint")
                    Spacer()
                    Button("Enroll My Voice…") { model.actions.enroll() }
                }
                Note("30 seconds of you talking, so your lines are labelled Me.")
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
                TextField("Names and trigger words", text: $model.names, prompt: Text("e.g. Alex, Sasha, Олекса"))
                    .focused($namesFocused)
                    .onSubmit { model.commitNames() }
                    .onChange(of: namesFocused) { _, focused in if !focused { model.commitNames() } }
                Note("When someone says one of these, the line is highlighted. Separate with commas.")
            }
            Section("Replies") {
                Toggle("Suggest a reply when someone says my name", isOn: $model.autoSuggest)
                Toggle("Interview mode: suggest for every question", isOn: $model.interview)
                Toggle("Use connectors (Jira, mail, calendar)", isOn: $model.connectors)
                Note("Connectors come from Claude Code (claude mcp list) and are used only when the question needs them. "
                     + "They add a few seconds. Reply anytime with ⌥⇧A.")
            }
            Section("Project folder") {
                HStack {
                    Text(model.project.map { URL(fileURLWithPath: $0).lastPathComponent } ?? "None")
                        .foregroundStyle(model.project == nil ? .secondary : .primary)
                        .help(model.project ?? "")
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
                Note("Answers and replies use this folder, its git history and CLAUDE.md as context (read only).")
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
                Toggle("Improve the transcript (accurate model and speakers)", isOn: $model.improveAfterRecording)
                Toggle("Only when plugged in", isOn: $model.waitForPower)
                    .disabled(!model.improveAfterRecording)
                Note("Off: the live transcript is saved right away and uses almost no battery. "
                     + "You can improve any session later in Notes.")
            }
            Section("Where notes go") {
                PathRow(title: "Notes", url: model.notesDir) {
                    Button("Open") { model.actions.showNotes() }
                }
                Toggle("Also save into the project's Meetings/<date> folder", isOn: $model.saveToProject)
                    .disabled(model.project == nil)
                HStack {
                    Text("Also copy to")
                    Spacer()
                    Text(model.exportFolder?.lastPathComponent ?? "Off").foregroundStyle(.secondary)
                        .help(model.exportFolder?.path ?? "")
                    Button("Choose…") { model.chooseExportFolder() }
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
            Section("Engine") {
                HStack {
                    Text("Engine config")
                    Spacer()
                    Button("Edit…") { model.actions.openConfig() }
                }
                Note("Models, about_me for interview answers, vocabulary. Restart OnTrack after editing.")
            }
            Section("Folders") {
                PathRow(title: "Sessions", url: model.sessionsDir) { Button("Show") { model.actions.open(model.sessionsDir) } }
                PathRow(title: "Logs", url: model.logsDir) { Button("Show") { model.actions.open(model.logsDir) } }
            }
        }
        .formStyle(.grouped)
    }
}

// MARK: small pieces

private struct Note: View {
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
