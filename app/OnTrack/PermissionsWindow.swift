import AVFoundation
import AppKit
import SwiftUI

/// First-run "Permissions" card: what OnTrack needs, what is granted, one click to grant the rest.
@MainActor
final class PermissionsModel: ObservableObject {
    @Published var mic: AVAuthorizationStatus = .notDetermined
    @Published var systemAudio = false
    @Published var voiceprint = false
    @Published var grantedSystemAudioThisRun = false  // macOS applies it only after a relaunch

    private let voiceprintFile: URL
    private var timer: Timer?
    let enroll: () -> Void

    init(voiceprintFile: URL, enroll: @escaping () -> Void) {
        self.voiceprintFile = voiceprintFile
        self.enroll = enroll
        refresh()
    }

    var allRequiredGranted: Bool { mic == .authorized && systemAudio }

    func refresh() {
        mic = AVCaptureDevice.authorizationStatus(for: .audio)
        systemAudio = Permissions.screenCaptureAllowed
        voiceprint = FileManager.default.fileExists(atPath: voiceprintFile.path)
    }

    /// Settings change outside the app, so keep looking while the window is open.
    func startPolling() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func grantMic() {
        if mic == .notDetermined {
            Task {
                _ = await Permissions.requestMicrophone()
                refresh()
            }
        } else {
            Permissions.openMicrophoneSettings()
        }
    }

    func grantSystemAudio() {
        grantedSystemAudioThisRun = true
        Permissions.requestScreenCapture()  // first time: the system prompt; after that it only lists the app
        Permissions.openScreenCaptureSettings()
    }

    func relaunch() {
        let path = Bundle.main.bundleURL.path
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", "sleep 1; open \"\(path)\""]
        try? task.run()
        NSApp.terminate(nil)
    }
}

@MainActor
final class PermissionsWindowController: NSObject, NSWindowDelegate {
    private var window: NSWindow?
    private let model: PermissionsModel

    init(voiceprintFile: URL, enroll: @escaping () -> Void) {
        model = PermissionsModel(voiceprintFile: voiceprintFile, enroll: enroll)
    }

    /// True when something required is missing (shown automatically at launch then).
    var needed: Bool {
        model.refresh()
        return !model.allRequiredGranted
    }

    func show() {
        model.refresh()
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 500, height: 560),
                             styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
            w.titleVisibility = .hidden
            w.titlebarAppearsTransparent = true
            w.isMovableByWindowBackground = true
            w.isReleasedWhenClosed = false
            w.contentViewController = NSHostingController(rootView: PermissionsView(model: model) { [weak w] in w?.close() })
            w.center()
            w.delegate = self
            window = w
        }
        model.startPolling()
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }

    func windowWillClose(_ notification: Notification) {
        model.stopPolling()
        if !NSApp.windows.contains(where: { $0 !== window && $0.isVisible && $0.styleMask.contains(.titled) }) {
            NSApp.setActivationPolicy(.accessory)
        }
    }
}

// MARK: views

private enum Brand {
    static let top = Color(red: 0.62, green: 0.85, blue: 1.0)  // the app icon's light blue
    static let bottom = Color(red: 0.31, green: 0.65, blue: 0.93)
    static let gradient = LinearGradient(colors: [top, bottom], startPoint: .top, endPoint: .bottom)
}

struct PermissionsView: View {
    @ObservedObject var model: PermissionsModel
    var close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            header
            VStack(spacing: 12) {
                PermissionRow(icon: "mic.fill", tint: Brand.bottom,
                              title: "Microphone",
                              detail: "Hears you and the people in the room.",
                              state: model.mic == .authorized ? .granted : .missing,
                              action: model.grantMic)
                PermissionRow(icon: "waveform", tint: Color(red: 0.36, green: 0.62, blue: 0.45),
                              title: "System audio",
                              detail: "Hears the other side of calls. macOS lists this under Screen & System Audio Recording. Your screen is never recorded.",
                              state: model.systemAudio ? .granted : (model.grantedSystemAudioThisRun ? .relaunch : .missing),
                              action: model.grantSystemAudio)
                PermissionRow(icon: "person.wave.2.fill", tint: Color(red: 0.85, green: 0.6, blue: 0.2),
                              title: "Your voice (optional)",
                              detail: "30 seconds of you talking, so your lines are labelled Me.",
                              state: model.voiceprint ? .done : .optional,
                              buttonTitle: "Enroll",
                              action: { close(); model.enroll() })
            }
            .padding(.horizontal, 28)
            footer
        }
        .frame(width: 500)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var header: some View {
        VStack(spacing: 10) {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(Brand.gradient)
                .frame(width: 72, height: 72)
                .overlay(Image(systemName: "lock.shield.fill").font(.system(size: 32, weight: .medium)).foregroundStyle(.white))
                .shadow(color: Brand.bottom.opacity(0.35), radius: 10, y: 4)
            Text("Permissions").font(.system(size: 26, weight: .bold))
            Text("OnTrack needs these to transcribe your meetings. Everything stays on this Mac.")
                .font(.system(size: 13)).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .padding(.top, 36)
        .padding(.horizontal, 40)
        .padding(.bottom, 24)
    }

    private var footer: some View {
        VStack(spacing: 10) {
            if model.grantedSystemAudioThisRun && !model.systemAudio {
                Button("Relaunch OnTrack") { model.relaunch() }
                    .buttonStyle(.borderedProminent)
                    .tint(Brand.bottom)
                Text("macOS applies system audio access after a relaunch.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            } else if model.allRequiredGranted {
                Button("Done") { close() }
                    .buttonStyle(.borderedProminent)
                    .tint(Brand.bottom)
                    .keyboardShortcut(.defaultAction)
            } else {
                Text("You can open this again from Settings.")
                    .font(.system(size: 11)).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 24)
    }
}

private enum RowState { case granted, missing, relaunch, done, optional }

private struct PermissionRow: View {
    let icon: String
    let tint: Color
    let title: String
    let detail: String
    let state: RowState
    var buttonTitle = "Grant access"
    let action: () -> Void

    var body: some View {
        HStack(spacing: 14) {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(tint.opacity(0.15))
                .frame(width: 48, height: 48)
                .overlay(Image(systemName: icon).font(.system(size: 20, weight: .medium)).foregroundStyle(tint))
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.system(size: 14, weight: .semibold))
                Text(detail).font(.system(size: 12)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.primary.opacity(0.04)))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Color.primary.opacity(0.07)))
    }

    @ViewBuilder private var trailing: some View {
        switch state {
        case .granted, .done:
            Label(state == .granted ? "Granted" : "Done", systemImage: "checkmark.circle.fill")
                .labelStyle(.titleAndIcon)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.secondary)
        case .relaunch:
            Text("Relaunch needed").font(.system(size: 12, weight: .medium)).foregroundStyle(.orange)
        case .missing, .optional:
            Button(buttonTitle, action: action)
                .buttonStyle(.bordered)
                .controlSize(.regular)
        }
    }
}
