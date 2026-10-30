import Foundation
import OnTrackCore

/// Launches ontrack-engine, restarts it if it exits or stops answering pings (spec §4.1, §8).
final class EngineSupervisor {
    var onFatal: ((String) -> Void)?
    var onLaunch: (() -> Void)?

    private let engineDir: URL
    private let logDir: URL
    private var process: Process?
    private var shouldRun = false
    private var restartBackoff = Backoff(initial: 0.5, cap: 4.0)
    private var lastLaunch = Date.distantPast
    private var missedPings = 0
    private var timer: Timer?
    private var unreachableSince: Date?
    private lazy var loginPath: String? = Self.loginShellPath()

    init(engineDir: URL, logDir: URL) {
        self.engineDir = engineDir
        self.logDir = logDir
    }

    var executable: URL { engineDir.appendingPathComponent(".venv/bin/ontrack-engine") }

    func start() {
        shouldRun = true
        killStaleEngines()
        launch()
    }

    func stop() {
        shouldRun = false
        timer?.invalidate()
        guard let p = process, p.isRunning else { return }
        p.terminate()  // SIGTERM → engine finishes the open utterance and closes the session
        let pid = p.processIdentifier
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) { if p.isRunning { kill(pid, SIGKILL) } }
        p.waitUntilExit()
    }

    /// Ping every 2 s while connected; ~20 s without any message → restart (heavy model loads can stall the engine for a few seconds).
    func startHealthChecks(client: EngineClient) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self, weak client] _ in
            guard let self, let client else { return }
            guard client.isConnected else {
                // Running but not accepting connections (e.g. its sockets were removed) → restart after 10 s.
                if let p = self.process, p.isRunning, Date().timeIntervalSince(self.lastLaunch) > 10 {
                    let since = self.unreachableSince ?? Date()
                    self.unreachableSince = since
                    if Date().timeIntervalSince(since) > 10 {
                        NSLog("OnTrack: engine unreachable. Restarting")
                        self.unreachableSince = nil
                        self.restart()
                    }
                }
                return
            }
            self.unreachableSince = nil
            self.missedPings += 1
            if self.missedPings > 10 {
                NSLog("OnTrack: engine unresponsive. Restarting")
                self.missedPings = 0
                self.restart()
                return
            }
            client.send(.ping(id: UUID().uuidString))
        }
    }

    func noteAlive() { missedPings = 0 }

    func restart() {
        guard let p = process, p.isRunning else { return launch() }
        let pid = p.processIdentifier
        p.terminate()
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { if p.isRunning { kill(pid, SIGKILL) } }
        // terminationHandler relaunches
    }

    /// Stop engines left over from a previous app run and wait until they are gone,
    /// so their shutdown can't interfere with the new engine.
    private func killStaleEngines() {
        func run(_ tool: String, _ args: [String]) -> Int32 {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: tool)
            p.arguments = args
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            guard (try? p.run()) != nil else { return -1 }
            p.waitUntilExit()
            return p.terminationStatus
        }
        guard run("/usr/bin/pgrep", ["-f", "bin/ontrack-engine"]) == 0 else { return }
        _ = run("/usr/bin/pkill", ["-f", "bin/ontrack-engine"])
        for _ in 0..<50 {  // up to 5 s for a graceful stop
            if run("/usr/bin/pgrep", ["-f", "bin/ontrack-engine"]) != 0 { return }
            Thread.sleep(forTimeInterval: 0.1)
        }
        _ = run("/usr/bin/pkill", ["-9", "-f", "bin/ontrack-engine"])
    }

    /// The PATH a Terminal would have (nvm/fnm/volta node for the `claude` CLI); apps launched from Finder get a minimal one.
    private static func loginShellPath() -> String? {
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        let p = Process()
        p.executableURL = URL(fileURLWithPath: shell)
        p.arguments = ["-lc", "printf %s \"$PATH\""]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return nil }
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning {
            p.terminate()
            return nil
        }
        let path = String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        return path.isEmpty ? nil : path
    }

    private func launch() {
        guard shouldRun else { return }
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            onFatal?("Engine not found at \(executable.path). Run `uv sync --extra models` in the engine folder.")
            return
        }
        try? FileManager.default.createDirectory(at: logDir, withIntermediateDirectories: true)
        let logURL = logDir.appendingPathComponent("engine-stdout.log")
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        let log = try? FileHandle(forWritingTo: logURL)
        _ = try? log?.seekToEnd()

        let p = Process()
        p.executableURL = executable
        p.currentDirectoryURL = engineDir
        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extra = ["\(home)/.local/bin", "/opt/homebrew/bin", "/usr/local/bin", "\(home)/.claude/local"]
        env["PATH"] = ([loginPath ?? env["PATH"] ?? "/usr/bin:/bin"] + extra).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        if let log {
            p.standardOutput = log
            p.standardError = log
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.handleExit(proc) }
        }
        do {
            try p.run()
            process = p
            lastLaunch = Date()
            missedPings = 0
            onLaunch?()
        } catch {
            onFatal?("Could not start engine: \(error.localizedDescription)")
        }
    }

    private func handleExit(_ proc: Process) {
        guard proc === process else { return }
        process = nil
        guard shouldRun else { return }
        NSLog("OnTrack: engine exited (%d). Restarting", proc.terminationStatus)
        if Date().timeIntervalSince(lastLaunch) > 30 { restartBackoff.reset() }
        let delay = restartBackoff.next()  // ≤ 4 s, well inside the spec's 5 s restart budget
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.launch() }
    }
}
