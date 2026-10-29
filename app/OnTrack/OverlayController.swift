import AppKit
import Combine
import OnTrackCore
import SwiftUI

/// Borderless non-activating floating panel: never steals focus unless you are typing a question.
final class OverlayPanel: NSPanel {
    var snapTarget: ((NSRect, NSPoint) -> NSPoint?)?
    var onDragEnded: (() -> Void)?
    private var dragSession: OverlayDragSession?

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

    func beginHandleDrag(with event: NSEvent) {
        dragSession = OverlayDragSession(pointer: NSEvent.mouseLocation, frame: frame)
    }

    func continueHandleDrag(with event: NSEvent) {
        guard var session = dragSession else { return }
        // Screen coordinates remain stable when snapping moves the view under the cursor.
        let pointer = NSEvent.mouseLocation
        let top = session.topLeft(at: pointer, height: frame.height) { [self] proposed in
            snapTarget?(proposed, pointer)
        }
        dragSession = session
        setFrameTopLeftPoint(top)
    }

    func endHandleDrag() {
        dragSession = nil
        onDragEnded?()
    }
}

@MainActor
final class OverlayController: NSObject, NSWindowDelegate {
    private let store: OverlayStore
    private let panel: OverlayPanel
    private let hosting: NSHostingController<OverlayView>
    private var cancellables: Set<AnyCancellable> = []
    private var lastMode: OverlayMode = .hidden
    private var positioned = false  // false until the panel has been placed at its saved/default spot
    private static let positionKey = "overlayTopLeft"

    init(store: OverlayStore, onOpenNotes: @escaping (String) -> Void, onOpenSettings: @escaping (String) -> Void,
         onSaveTo: @escaping (String) -> Void) {
        self.store = store
        hosting = NSHostingController(rootView: OverlayView(store: store, onOpenNotes: onOpenNotes,
                                                            onOpenSettings: onOpenSettings, onSaveTo: onSaveTo))
        hosting.sizingOptions = []  // the controller sizes the panel (top edge fixed); avoid fighting it
        panel = OverlayPanel(contentView: hosting.view)
        super.init()
        panel.delegate = self
        panel.snapTarget = { [weak self] frame, pointer in self?.snapTarget(for: frame, pointer: pointer) }
        panel.onDragEnded = { [weak self] in self?.savePosition() }
        store.$state
            .removeDuplicates()  // engine pings re-publish identical state every 2 s
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
        // Size and place BEFORE showing, so it never flashes at the screen origin.
        layout()
        DispatchQueue.main.async { [weak self] in self?.layout() }  // again once SwiftUI has applied the new state
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
        // Measure the SwiftUI content explicitly (fittingSize is 0×0 when the hosting view doesn't self-size).
        var size = hosting.sizeThatFits(in: NSSize(width: 560, height: 2000))
        if size.width < 100 || size.height < 20 { size = NSSize(width: 560, height: max(size.height, 64)) }
        let top: NSPoint
        if positioned {
            top = NSPoint(x: panel.frame.minX, y: panel.frame.maxY)  // keep the top edge; grow downward
        } else {
            top = savedTopLeft() ?? defaultTopLeft(width: size.width)
            positioned = true
        }
        panel.setFrame(NSRect(x: top.x, y: top.y - size.height, width: size.width, height: size.height), display: true)
    }

    private func defaultTopLeft(width: CGFloat) -> NSPoint {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        return OverlayPlacement.defaultTopLeft(in: screen, width: width)
    }

    private func snapTarget(for frame: NSRect, pointer: NSPoint) -> NSPoint? {
        // Match the destination display, including displays with nonzero/negative origins.
        let screen = NSScreen.screens.first { $0.frame.contains(pointer) } ?? panel.screen
        guard let screen else { return nil }
        return OverlayPlacement.snapTopLeft(for: frame, screenFrame: screen.frame, visibleFrame: screen.visibleFrame)
    }

    private func savedTopLeft() -> NSPoint? {
        guard let s = UserDefaults.standard.string(forKey: Self.positionKey) else { return nil }
        let p = NSPointFromString(s)
        let onScreen = NSScreen.screens.contains { $0.visibleFrame.insetBy(dx: -10, dy: -10).contains(p) }
        return onScreen ? p : nil
    }

    func windowDidMove(_ notification: Notification) {
        savePosition()
    }

    private func savePosition() {
        guard positioned else { return }
        UserDefaults.standard.set(NSStringFromPoint(NSPoint(x: panel.frame.minX, y: panel.frame.maxY)), forKey: Self.positionKey)
    }
}
