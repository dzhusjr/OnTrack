import Foundation

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

    /// On battery, record only; live transcription resumes when plugged in.
    var recordOnlyOnBattery: Bool {
        get { defaults.object(forKey: "recordOnlyOnBattery") as? Bool ?? false }
        nonmutating set { defaults.set(newValue, forKey: "recordOnlyOnBattery") }
    }

    var autoShowOverlay: Bool {
        get { defaults.object(forKey: "autoShowOverlay") as? Bool ?? true }
        nonmutating set { defaults.set(newValue, forKey: "autoShowOverlay") }
    }

    var echoCancellation: Bool {
        // Off by default: Apple voice processing fails or returns silence on some Macs (-10875).
        // Enable with: defaults write com.romandzhus.ontrack echoCancellation -bool true
        defaults.object(forKey: "echoCancellation") as? Bool ?? false
    }
}
