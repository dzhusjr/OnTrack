import Foundation
import ServiceManagement

/// Start OnTrack when you log in. The system (SMAppService) is the source of truth, so a switch flipped in
/// System Settings → Login Items shows up here too. On by default: registered once on the first launch,
/// after that only the person's own choice counts.
enum LoginItem {
    private static let appliedKey = "startAtLoginDefaultApplied"

    static var status: SMAppService.Status { SMAppService.mainApp.status }
    /// On, or waiting for the person to approve it in System Settings.
    static var isOn: Bool { status == .enabled || status == .requiresApproval }
    static var needsApproval: Bool { status == .requiresApproval }

    static func set(_ on: Bool) throws {
        if on { if status != .enabled { try SMAppService.mainApp.register() } }
        else if isOn { try SMAppService.mainApp.unregister() }
    }

    /// First launch only: turn it on. Never again, so turning it off sticks.
    static func applyDefaultOnce(_ defaults: UserDefaults = .standard) {
        guard defaults.object(forKey: appliedKey) == nil else { return }
        defaults.set(true, forKey: appliedKey)
        try? set(true)  // fails harmlessly when not running from an installed app (swift run)
    }
}
