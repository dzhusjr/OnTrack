import CoreGraphics

public enum OverlayPlacement {
    public static func defaultTopLeft(in visibleFrame: CGRect, width: CGFloat) -> CGPoint {
        CGPoint(x: visibleFrame.midX - width / 2, y: visibleFrame.maxY - 24)
    }

    /// The overlay's top center must enter the default position's width and the strip
    /// extending from 32 points below it up through the menu bar.
    public static func snapTopLeft(for overlay: CGRect, screenFrame: CGRect,
                                   visibleFrame: CGRect) -> CGPoint? {
        let target = defaultTopLeft(in: visibleFrame, width: overlay.width)
        let halfZoneWidth = min(overlay.width / 2, visibleFrame.width / 2)
        guard abs(overlay.midX - visibleFrame.midX) <= halfZoneWidth,
              overlay.maxY >= target.y - 32,
              // The 14-point handle can extend above the display when grabbed at its lower edge.
              overlay.maxY <= screenFrame.maxY + 14,
              overlay.midX >= screenFrame.minX, overlay.midX <= screenFrame.maxX else { return nil }
        return target
    }
}
