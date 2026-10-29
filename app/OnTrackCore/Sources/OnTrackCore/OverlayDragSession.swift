import CoreGraphics

/// Magnetic dragging preserves the original grab offset, even after snapping.
public struct OverlayDragSession {
    private let startPointer: CGPoint
    private let startTopLeft: CGPoint
    private let width: CGFloat
    private var snappedTopLeft: CGPoint?
    private var snapPointer: CGPoint?
    private var mustLeaveZone = false

    public init(pointer: CGPoint, frame: CGRect) {
        startPointer = pointer
        startTopLeft = CGPoint(x: frame.minX, y: frame.maxY)
        width = frame.width
    }

    public mutating func topLeft(at pointer: CGPoint, height: CGFloat,
                                 snapTarget: (CGRect) -> CGPoint?) -> CGPoint {
        let free = CGPoint(x: startTopLeft.x + pointer.x - startPointer.x,
                           y: startTopLeft.y + pointer.y - startPointer.y)
        let proposed = CGRect(x: free.x, y: free.y - height, width: width, height: height)
        let target = snapTarget(proposed)

        if let anchor = snapPointer, let snapped = snappedTopLeft {
            let dx = pointer.x - anchor.x
            let dy = pointer.y - anchor.y
            if dx * dx + dy * dy <= 24 * 24 { return snapped }
            snapPointer = nil
            snappedTopLeft = nil
            // Don't immediately snap back while the same continuous drag crosses the zone.
            mustLeaveZone = true
        }

        if mustLeaveZone {
            if target == nil { mustLeaveZone = false }
            return free
        }

        if let target {
            snappedTopLeft = target
            snapPointer = pointer
            return target
        }
        return free
    }
}
