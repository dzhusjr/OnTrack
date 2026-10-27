import Foundation
import OnTrackCore

/// App-side settings (UserDefaults). Engine settings live in ~/Library/Application Support/OnTrack/config.toml.
struct Settings {
    private let defaults = UserDefaults.standard

    var engineDir: URL {
        let path = defaults.string(forKey: "engineDir") ?? "~/Desktop/Work/OnTrack/engine"
        return URL(fileURLWithPath: (path as NSString).expandingTildeInPath)
    }

    var repoDir: URL { engineDir.deletingLastPathComponent() }

    var appSupportDir: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("OnTrack", isDirectory: true)
    }

    var sessionsDir: URL { repoDir.appendingPathComponent("sessions", isDirectory: true) }
    var notesDir: URL { repoDir.appendingPathComponent("notes", isDirectory: true) }
    var logsDir: URL { appSupportDir.appendingPathComponent("logs", isDirectory: true) }
    var configFile: URL { appSupportDir.appendingPathComponent("config.toml") }

    var overlayLines: Int {
        let v = defaults.integer(forKey: "overlayLines")
        return v == 0 ? 3 : max(1, min(5, v))
    }

    /// Extra folder every saved session is copied to (e.g. a course folder or iCloud Drive). nil = off.
    var exportFolder: URL? {
        get { defaults.string(forKey: "exportFolder").map { URL(fileURLWithPath: $0, isDirectory: true) } }
        nonmutating set { defaults.set(newValue?.path, forKey: "exportFolder") }
    }

    /// Live transcription when a session starts (off = record only, transcribed when saved).
    var liveByDefault: Bool {
        get { defaults.object(forKey: "liveByDefault") as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: "liveByDefault") }
    }

    /// Run the heavy after-recording work (speaker separation, large-v3, summary) only on mains power.
    var waitForPowerToFinalize: Bool {
        get { defaults.object(forKey: "waitForPowerToFinalize") as? Bool ?? false }
        nonmutating set { defaults.set(newValue, forKey: "waitForPowerToFinalize") }
    }

    /// Sessions saved on battery that still need processing (JSON).
    var finalizeQueue: Data? {
        get { defaults.data(forKey: "finalizeQueue") }
        nonmutating set { defaults.set(newValue, forKey: "finalizeQueue") }
    }

    var autoShowOverlay: Bool {
        get { defaults.object(forKey: "autoShowOverlay") as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: "autoShowOverlay") }
    }

    /// Folder Claude works in for this session's answers (e.g. a code project). nil = none.
    var projectDir: String? {
        get { defaults.string(forKey: "projectDir") }
        nonmutating set {
            defaults.set(newValue, forKey: "projectDir")
            if let p = newValue { recentProjects = Array(([p] + recentProjects.filter { $0 != p }).prefix(6)) }
        }
    }

    var recentProjects: [String] {
        get { (defaults.stringArray(forKey: "recentProjects") ?? []).filter { FileManager.default.fileExists(atPath: $0) } }
        nonmutating set { defaults.set(newValue, forKey: "recentProjects") }
    }

    /// "auto" or a language code ("uk", "en", "nl") to lock transcription to.
    var sessionLanguage: String {
        get { defaults.string(forKey: "sessionLanguage") ?? "auto" }
        nonmutating set { defaults.set(newValue, forKey: "sessionLanguage") }
    }

    /// Draft a reply when someone says your name.
    var suggestReplies: Bool {
        get { defaults.object(forKey: "suggestReplies") as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: "suggestReplies") }
    }

    /// Interview mode: also draft a reply to every question someone else asks.
    var interviewMode: Bool {
        get { defaults.object(forKey: "interviewMode") as? Bool ?? false }
        nonmutating set { defaults.set(newValue, forKey: "interviewMode") }
    }

    /// No names ship with the app: yours stay in this Mac's settings (menu → Names & Trigger Words…).
    static let defaultTriggerWords: [String] = []

    /// Your name, nicknames and anything else that should get your attention (case-insensitive, whole words).
    var triggerWords: [String] {
        get { defaults.stringArray(forKey: "triggerWords") ?? Self.defaultTriggerWords }
        nonmutating set { defaults.set(newValue, forKey: "triggerWords") }
    }

    /// Also copy each saved session's notes into "<project>/Meetings/<date>/". On by default (ticked on the save card).
    var saveNotesToProject: Bool {
        get { defaults.object(forKey: "saveNotesToProject") as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: "saveNotesToProject") }
    }

    /// Let answers use the connectors set up in Claude Code (`claude mcp list`), e.g. Jira. Slower.
    /// After recording: re-transcribe with the accurate model + separate speakers (heavy, uses battery).
    /// Off = keep the live transcript right away; "Improve Transcript" in Notes can do it later.
    var improveAfterRecording: Bool {
        get { defaults.object(forKey: "improveAfterRecording") as? Bool ?? false }
        nonmutating set { defaults.set(newValue, forKey: "improveAfterRecording") }
    }

    var useConnectors: Bool {
        get { defaults.object(forKey: "useConnectors") as? Bool ?? false }
        nonmutating set { defaults.set(newValue, forKey: "useConnectors") }
    }

    var sessionOptions: SessionOptions {
        SessionOptions(projectDir: projectDir, languages: sessionLanguage == "auto" ? [] : [sessionLanguage],
                       assist: suggestReplies, interview: interviewMode, names: triggerWords, connectors: useConnectors)
    }

    var echoCancellation: Bool {
        // Off by default: Apple voice processing fails or returns silence on some Macs (-10875).
        // Enable with: defaults write <bundle id> echoCancellation -bool true
        defaults.object(forKey: "echoCancellation") as? Bool ?? false
    }
}
