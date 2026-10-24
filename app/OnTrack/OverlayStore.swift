import Combine
import Foundation
import OnTrackCore
import SwiftUI

/// Observable wrapper around the pure OverlayState reducer (OnTrackCore).
@MainActor
final class OverlayStore: ObservableObject {
    @Published private(set) var state: OverlayState
    @Published private(set) var recordingStarted: Date?
    @Published var sessionName: String?

    var sendToEngine: ((Outbound) -> Void)?

    var onLiveToggled: ((Bool) -> Void)?  // user flipped live transcription (overlay, menu, hotkey)

    init(maxLines: Int, autoShow: Bool = true, live: Bool = true) {
        var s = OverlayState(maxLines: maxLines)
        s.autoShow = autoShow
        s.liveTranscription = live
        state = s
    }

    func toggleLive() {
        send(.toggleLive)
        onLiveToggled?(state.liveTranscription)
    }

    func setAutoShow(_ on: Bool) {
        state.autoShow = on
    }

    private var toastClear: DispatchWorkItem?

    func send(_ event: OverlayEvent) {
        let wasRecording = state.recording
        let oldToast = state.toast
        let commands = reduce(&state, event)
        if state.toast != oldToast, state.toast != nil { scheduleToastClear() }
        if state.recording && !wasRecording && recordingStarted == nil { recordingStarted = Date() }
        if !state.recording && wasRecording { recordingStarted = nil }
        commands.forEach { sendToEngine?($0) }
    }

    var question: Binding<String> {
        Binding(get: { self.state.question }, set: { self.send(.questionChanged($0)) })
    }

    func submitQuestion() { send(.submitQuestion(id: UUID().uuidString)) }

    func showIssue(_ text: String) {
        send(.engine(.error(EngineError(code: "app", message: text, recoverable: true))))
    }

    func showToast(_ text: String) {
        state.toast = text
        scheduleToastClear()
    }

    private func scheduleToastClear() {
        toastClear?.cancel()
        let item = DispatchWorkItem { [weak self] in
            guard let self else { return }
            var s = self.state
            s.toast = nil
            self.state = s
        }
        toastClear = item
        DispatchQueue.main.asyncAfter(deadline: .now() + 5, execute: item)
    }
}
