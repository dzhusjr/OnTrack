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

    /// Ping every 2 s while connected; 3 missed pongs → restart.
    func startHealthChecks(client: EngineClient) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self, weak client] _ in
            guard let self, let client, client.isConnected else { return }
            self.missedPings += 1
            if self.missedPings > 3 {
                NSLog("OnTrack: engine unresponsive — restarting")
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

    private func killStaleEngines() {
        let pk = Process()
        pk.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        pk.arguments = ["-f", "bin/ontrack-engine"]
        try? pk.run()
        pk.waitUntilExit()
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
        env["PATH"] = ([env["PATH"] ?? "/usr/bin:/bin"] + extra).joined(separator: ":")
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
        NSLog("OnTrack: engine exited (%d) — restarting", proc.terminationStatus)
        if Date().timeIntervalSince(lastLaunch) > 30 { restartBackoff.reset() }
        let delay = restartBackoff.next()  // ≤ 4 s, well inside the spec's 5 s restart budget
        DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in self?.launch() }
    }
}
