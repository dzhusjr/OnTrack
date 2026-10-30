import AppKit
import OnTrackCore
import SwiftUI

/// Voice enrollment: pick a language, read the passage aloud, watch the 30 seconds run out.
@MainActor
final class EnrollmentModel: ObservableObject {
    enum Phase { case choose, recording }
    static let seconds = 30.0  // the engine collects this much speech

    let languages: [EnrollmentText.Language]
    @Published var code: String { didSet { UserDefaults.standard.set(code, forKey: "enrollLanguage") } }
    @Published var phase = Phase.choose
    @Published var elapsed = 0.0
    var onStart: () -> Void = {}
    var onCancel: () -> Void = {}

    init(languages: [EnrollmentText.Language]) {
        self.languages = languages
        code = EnrollmentText.pick(UserDefaults.standard.string(forKey: "enrollLanguage"), from: languages).code
    }

    var passage: String { languages.first { $0.code == code }?.passage ?? "" }
}

private struct EnrollmentView: View {
    @ObservedObject var model: EnrollmentModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Enroll your voice").font(.title2.weight(.semibold))
            if model.phase == .choose {
                Text("OnTrack uses this to tell you apart from other people in the room. Pick the language you will read in, press Start, then read the text aloud at a normal pace.")
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if model.languages.count > 1 {
                    Picker("Language", selection: $model.code) {
                        ForEach(model.languages, id: \.code) { Text($0.name).tag($0.code) }
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }
            } else {
                Text("Read this aloud. If you reach the end, start again from the top.")
                    .foregroundStyle(.secondary)
            }
            ScrollView {
                Text(model.passage)
                    .font(.system(size: 17))
                    .lineSpacing(5)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(14)
                    .textSelection(.enabled)
            }
            .frame(height: 190)
            .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .textBackgroundColor)))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.12)))
            if model.phase == .recording {
                VStack(alignment: .leading, spacing: 6) {
                    ProgressView(value: min(model.elapsed / EnrollmentModel.seconds, 1))
                    Text("Listening… \(Int(max(EnrollmentModel.seconds - model.elapsed, 0).rounded(.up))) s left")
                        .font(.callout).foregroundStyle(.secondary)
                }
            } else {
                HStack {
                    Spacer()
                    Button("Cancel") { model.onCancel() }.keyboardShortcut(.cancelAction)
                    Button("Start") { model.onStart() }.keyboardShortcut(.defaultAction)
                }
            }
        }
        .padding(24)
        .frame(width: 520)
    }
}

@MainActor
final class EnrollmentWindowController: NSObject {
    private var window: NSWindow?
    private var model: EnrollmentModel?
    private var timer: Timer?

    var isOpen: Bool { window != nil }

    func show(enabledLanguages: [String], onStart: @escaping () -> Void) {
        if let window {
            NSApp.activate(ignoringOtherApps: true)
            window.makeKeyAndOrderFront(nil)
            return
        }
        let m = EnrollmentModel(languages: EnrollmentText.offered(enabled: enabledLanguages))
        m.onStart = { [weak self, weak m] in
            guard let self, let m else { return }
            m.phase = .recording
            m.elapsed = 0
            self.timer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.model?.elapsed += 0.25 }
            }
            onStart()
        }
        m.onCancel = { [weak self] in self?.close() }
        let w = NSWindow(contentViewController: NSHostingController(rootView: EnrollmentView(model: m)))
        w.styleMask = [.titled]  // no close button: leaving mid-recording would strand the engine
        w.isReleasedWhenClosed = false
        w.level = .floating
        w.center()
        model = m
        window = w
        NSApp.activate(ignoringOtherApps: true)
        w.makeKeyAndOrderFront(nil)
    }

    func close() {
        timer?.invalidate()
        timer = nil
        window?.orderOut(nil)
        window = nil
        model = nil
    }
}
