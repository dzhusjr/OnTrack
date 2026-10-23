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

    var echoCancellation: Bool {
        defaults.object(forKey: "echoCancellation") as? Bool ?? true
    }
}
