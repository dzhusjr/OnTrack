import AppKit
import OnTrackCore
import SwiftUI

/// Spotlight-style overlay (spec §6).
struct OverlayView: View {
    @ObservedObject var store: OverlayStore
    var onOpenNotes: (String) -> Void
    var onOpenSettings: (String) -> Void
    var onSaveTo: (String) -> Void
    @FocusState private var questionFocused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var s: OverlayState { store.state }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            switch s.mode {
            case .hidden:
                EmptyView()
            case .collapsed, .expanded:
                liveSection
                if let sug = s.suggestion { suggestionCard(sug) }
                if s.mode == .expanded { askSection }
            case .endCard:
                endCard
            case .finalizing:
                finalizing
            case .done:
                doneCard
            }
            if let err = s.error { banner(err, color: .red) }
            if let toast = s.toast { banner(toast, color: .green) }
        }
        .padding(14)
        .frame(width: 560, alignment: .leading)
        .background(OverlayBackground())
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(Color.white.opacity(0.14), lineWidth: 1))
        .onExitCommand { store.send(.escape) }
        .onChange(of: s.mode) { _, mode in questionFocused = (mode == .expanded) }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: s.lines)
        .animation(reduceMotion ? nil : .linear(duration: 0.08), value: s.partials)
    }

    // MARK: live lines

    private var liveSection: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 5) {
                if s.lines.isEmpty && s.partials.isEmpty {
                    Text(!s.modelReady ? "Loading speech model…"
                         : !s.recording ? "Not recording — ⌥⇧S to start"
                         : s.paused ? "Paused — nothing is recorded (⌥⇧P to resume)"
                         : s.liveTranscription ? "Listening…" : "Recording only — transcribed when you save (⌥⇧L for live)")
                        .font(.system(size: 13)).foregroundStyle(.secondary)
                }
                let shown = s.visibleLines
                ForEach(Array(shown.enumerated()), id: \.element.seq) { index, line in
                    let addressed = s.addressedSeqs.contains(line.seq)
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        if addressed {
                            Image(systemName: "person.wave.2.fill").font(.system(size: 9)).foregroundStyle(.yellow)
                                .help("You were addressed")
                        } else {
                            Circle().fill(Self.color(for: line.speaker)).frame(width: 7, height: 7)
                        }
                        Text(line.text)
                            .font(.system(size: 13, weight: addressed ? .semibold : .regular))
                            .foregroundStyle(line.speaker == "Note" ? Color.secondary : Color.primary)
                            .lineLimit(3)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .opacity(s.partials.isEmpty && index == shown.count - 1 ? 1 : 0.55 + 0.3 * Double(index) / Double(max(1, shown.count)))
                    .transition(.move(edge: .bottom).combined(with: .opacity))
                }
                ForEach(s.partials, id: \.channel) { p in  // live word-by-word preview (grey, replaced by the final line)
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        Circle().strokeBorder(p.channel == 1 ? Color.green : Color.secondary, lineWidth: 1.2)
                            .frame(width: 7, height: 7)
                        Text(p.text)
                            .font(.system(size: 13))
                            .italic()
                            .foregroundStyle(Color.secondary)
                            .lineLimit(2)
                            .truncationMode(.head)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            Spacer(minLength: 4)
            statusBadge
        }
    }

    private var statusBadge: some View {
        VStack(alignment: .trailing, spacing: 4) {
            HStack(spacing: 5) {
                if s.recording {
                    Button("Reply") { store.send(.requestSuggestion(id: UUID().uuidString)) }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.mini)
                        .tint(.yellow.opacity(0.85))
                        .disabled(s.suggestion != nil && s.suggestion?.done == false)
                        .help("Answer the latest question — or what's worth saying now (⌥⇧A)")
                }
                Button {
                    store.toggleLive()
                } label: {
                    Image(systemName: s.liveTranscription ? "text.bubble" : "text.bubble.fill").font(.system(size: 10))
                }
                .buttonStyle(.plain)
                .foregroundStyle(s.liveTranscription ? Color.secondary : Color.orange)
                .help(s.liveTranscription ? "Pause live transcription (⌥⇧L) — audio keeps recording"
                                          : "Resume live transcription (⌥⇧L)")
                Button {
                    store.send(.hide)
                } label: {
                    Image(systemName: "eye.slash").font(.system(size: 10))
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .help("Hide (Esc) — recording continues; ⌥Space shows it again")
                if s.recording && s.paused {
                    Image(systemName: "pause.fill").font(.system(size: 8)).foregroundStyle(.orange)
                        .help("Paused — ⌥⇧P to resume")
                } else {
                    Circle().fill(s.recording ? Color.red : Color.gray).frame(width: 7, height: 7)
                }
                if let start = store.recordingStarted {
                    TimelineView(.periodic(from: start, by: 1)) { ctx in
                        Text(Self.elapsed(from: start, to: ctx.date))
                            .font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary)
                    }
                }
            }
            if s.recording && s.paused {
                Text("paused").font(.system(size: 10)).foregroundStyle(.orange)
            } else if s.recording && !s.liveTranscription {
                Text("recording only").font(.system(size: 10)).foregroundStyle(.orange)
            }
            if s.degraded {
                Text("fast mode").font(.system(size: 10)).foregroundStyle(.orange)
            }
            if s.engineState == "disconnected" {
                Text("reconnecting…").font(.system(size: 10)).foregroundStyle(.orange)
            }
        }
    }

    // MARK: suggested reply

    private func suggestionCard(_ sug: Suggestion) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "lightbulb.fill").font(.system(size: 11)).foregroundStyle(.yellow)
                .padding(.top, 2)
            if sug.text.isEmpty {
                Text("Drafting a reply…").font(.system(size: 13)).foregroundStyle(.secondary)
            } else {
                Text(sug.text)
                    .font(.system(size: 13, weight: .medium))
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            Button { store.send(.dismissSuggestion) } label: { Image(systemName: "xmark").font(.system(size: 9)) }
                .buttonStyle(.plain).foregroundStyle(.secondary)
                .help("Dismiss — ⌥⇧A drafts a reply to the latest question")
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.yellow.opacity(0.12)))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).stroke(Color.yellow.opacity(0.35), lineWidth: 1))
    }

    // MARK: Q&A

    private var askSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().opacity(0.4)
            ForEach(Array(s.qaHistory.enumerated()), id: \.offset) { _, ex in  // earlier questions, dimmed
                VStack(alignment: .leading, spacing: 2) {
                    Text(ex.question).font(.system(size: 11, weight: .medium)).foregroundStyle(.tertiary)
                    Text(LocalizedStringKey(ex.answer)).font(.system(size: 12)).foregroundStyle(.secondary)
                        .lineLimit(2).textSelection(.enabled)
                }
            }
            if !s.lastQuestion.isEmpty {
                Text(s.lastQuestion).font(.system(size: 11, weight: .medium)).foregroundStyle(.secondary)
            }
            if !s.answer.isEmpty {
                Text(LocalizedStringKey(s.answer))  // renders Markdown
                    .font(.system(size: 13))
                    .textSelection(.enabled)
                    .lineLimit(12)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            HStack(spacing: 8) {
                Image(systemName: "sparkle.magnifyingglass").foregroundStyle(.secondary)
                TextField(s.lastQuestion.isEmpty ? "Ask about this session…" : "Ask a follow-up…", text: store.question)
                    .textFieldStyle(.plain)
                    .font(.system(size: 14))
                    .focused($questionFocused)
                    .onSubmit { store.submitQuestion() }
                    .disabled(s.asking)
                if s.asking { ProgressView().controlSize(.small) }
            }
        }
        .onAppear { questionFocused = true }
    }

    // MARK: end of session

    private var endCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let card = s.endCard {
                Text("Session ended · \(Self.minutes(card.durationS)) · \(card.lineCount) lines")
                    .font(.system(size: 14, weight: .semibold))
                HStack(spacing: 8) {
                    Button("Save summary + transcript") { store.send(.choose(.saveAll)) }
                        .keyboardShortcut(.defaultAction)
                    Button("Transcript only") { store.send(.choose(.transcriptOnly)) }
                    Spacer()
                    Button(card.confirmDiscard ? "Really discard?" : "Discard") { store.send(.choose(.discard)) }
                        .foregroundStyle(card.confirmDiscard ? .red : .secondary)
                }
                .controlSize(.regular)
            }
        }
    }

    private var finalizing: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(Self.stageTitle(s.progress?.stage)).font(.system(size: 13, weight: .medium))
            ProgressView(value: s.progress?.pct ?? 0).progressViewStyle(.linear)
        }
    }

    private var doneCard: some View {
        HStack {
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            Text("Saved to notes").font(.system(size: 13, weight: .medium))
            Spacer()
            if let path = s.notesPath {
                Button("Open") { onOpenNotes(path) }
                Button("Save to…") { onSaveTo(path) }
            }
            Button("Close") { store.send(.dismiss) }
        }
    }

    private func banner(_ text: String, color: Color) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: color == .red ? "exclamationmark.triangle.fill" : "checkmark.circle")
                .foregroundStyle(color)
            Text(text).font(.system(size: 12)).fixedSize(horizontal: false, vertical: true)
            Spacer()
            if text.contains("Screen & System Audio Recording") {
                Button("Open Settings") { onOpenSettings("screen") }.controlSize(.small)
            } else if text.localizedCaseInsensitiveContains("microphone") {
                Button("Open Settings") { onOpenSettings("mic") }.controlSize(.small)
            }
            Button {
                store.send(.clearError)
            } label: { Image(systemName: "xmark") }
                .buttonStyle(.plain).foregroundStyle(.secondary)
        }
    }

    // MARK: helpers

    static func color(for speaker: String) -> Color {
        switch speaker {
        case "Me": return .blue
        case "Other": return .orange
        case "Them": return .green
        case "Note": return .gray
        default: return .purple  // Speaker A/B/C after post-processing
        }
    }

    static func elapsed(from start: Date, to now: Date) -> String {
        let t = max(0, Int(now.timeIntervalSince(start)))
        return t >= 3600 ? String(format: "%d:%02d:%02d", t / 3600, t % 3600 / 60, t % 60)
                         : String(format: "%02d:%02d", t / 60, t % 60)
    }

    static func minutes(_ s: Double) -> String {
        s < 60 ? "\(Int(s)) s" : "\(Int((s / 60).rounded())) min"
    }

    static func stageTitle(_ stage: String?) -> String {
        switch stage {
        case "separating": return "Separating speakers…"
        case "retranscribing": return "Writing the accurate transcript…"
        case "summarizing": return "Summarizing…"
        case "saving", "done": return "Saving…"
        default: return "Preparing…"
        }
    }
}

/// Translucent HUD material, or a solid background when Reduce Transparency is on.
struct OverlayBackground: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .hudWindow
        v.blendingMode = .behindWindow
        v.state = .active
        return v
    }

    func updateNSView(_ v: NSVisualEffectView, context: Context) {
        if NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency {
            v.material = .windowBackground
        }
    }
}
