import AppKit
import Combine
import OnTrackCore
import SwiftUI

/// Borderless non-activating floating panel: never steals focus unless you are typing a question.
final class OverlayPanel: NSPanel {
    init(contentView: NSView) {
        super.init(contentRect: NSRect(x: 0, y: 0, width: 560, height: 80),
                   styleMask: [.nonactivatingPanel, .borderless, .fullSizeContentView],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        isMovableByWindowBackground = true
        hidesOnDeactivate = false
        becomesKeyOnlyIfNeeded = true
        self.contentView = contentView
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class OverlayController: NSObject, NSWindowDelegate {
    private let store: OverlayStore
    private let panel: OverlayPanel
    private let hosting: NSHostingView<OverlayView>
    private var cancellables: Set<AnyCancellable> = []
    private var lastMode: OverlayMode = .hidden
    private static let positionKey = "overlayTopLeft"

    init(store: OverlayStore, onOpenNotes: @escaping (String) -> Void, onOpenSettings: @escaping (String) -> Void) {
        self.store = store
        hosting = NSHostingView(rootView: OverlayView(store: store, onOpenNotes: onOpenNotes, onOpenSettings: onOpenSettings))
        panel = OverlayPanel(contentView: hosting)
        super.init()
        panel.delegate = self
        store.$state
            .receive(on: RunLoop.main)
            .sink { [weak self] state in self?.render(state) }
            .store(in: &cancellables)
    }

    private func render(_ state: OverlayState) {
        let visible = state.mode != .hidden || state.error != nil || state.toast != nil
        if !visible {
            panel.orderOut(nil)
            lastMode = state.mode
            return
        }
        // Resize to content, keeping the top edge fixed (grows downward like Spotlight).
        DispatchQueue.main.async { [weak self] in self?.layout() }
        if state.mode == .expanded {
            panel.makeKeyAndOrderFront(nil)
        } else {
            if lastMode == .expanded {
                panel.resignKey()
                panel.orderOut(nil)  // hand keyboard focus back to the app you were in
            }
            panel.orderFrontRegardless()
        }
        lastMode = state.mode
    }

    private func layout() {
        let size = hosting.fittingSize
        let top: NSPoint
        if panel.frame.height > 1, panel.isVisible {
            top = NSPoint(x: panel.frame.minX, y: panel.frame.maxY)
        } else {
            top = savedTopLeft() ?? defaultTopLeft()
        }
        panel.setFrame(NSRect(x: top.x, y: top.y - size.height, width: size.width, height: size.height), display: true)
    }

    private func defaultTopLeft() -> NSPoint {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        return NSPoint(x: screen.midX - 280, y: screen.maxY - 24)
    }

    private func savedTopLeft() -> NSPoint? {
        guard let s = UserDefaults.standard.string(forKey: Self.positionKey) else { return nil }
        let p = NSPointFromString(s)
        let onScreen = NSScreen.screens.contains { $0.visibleFrame.insetBy(dx: -10, dy: -10).contains(p) }
        return onScreen ? p : nil
    }

    func windowDidMove(_ notification: Notification) {
        UserDefaults.standard.set(NSStringFromPoint(NSPoint(x: panel.frame.minX, y: panel.frame.maxY)), forKey: Self.positionKey)
    }
}
