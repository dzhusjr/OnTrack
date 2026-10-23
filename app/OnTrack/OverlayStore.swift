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

    init(maxLines: Int) {
        state = OverlayState(maxLines: maxLines)
    }

    func send(_ event: OverlayEvent) {
        let wasRecording = state.recording
        let commands = reduce(&state, event)
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
    }
}
