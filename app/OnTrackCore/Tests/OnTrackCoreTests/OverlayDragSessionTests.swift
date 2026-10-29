import CoreGraphics
import XCTest
@testable import OnTrackCore

final class OverlayDragSessionTests: XCTestCase {
    private let initial = CGRect(x: 100, y: 500, width: 560, height: 60)
    private let pointer = CGPoint(x: 380, y: 553)
    private let target = CGPoint(x: 476, y: 934)

    private func snap(_ frame: CGRect) -> CGPoint? {
        OverlayPlacement.snapTopLeft(for: frame,
                                    screenFrame: CGRect(x: 0, y: 0, width: 1512, height: 982),
                                    visibleFrame: CGRect(x: 0, y: 40, width: 1512, height: 918))
    }

    func testEnteringZoneSnapsBeforeReleaseAndSmallMovementHolds() {
        var drag = OverlayDragSession(pointer: pointer, frame: initial)
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 720, y: 920), height: 60, snapTarget: snap), target)
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 732, y: 925), height: 60, snapTarget: snap), target)
    }

    func testContinuingSameDragPullsFreeWithoutImmediatelyResnapping() {
        var drag = OverlayDragSession(pointer: pointer, frame: initial)
        _ = drag.topLeft(at: CGPoint(x: 720, y: 920), height: 60, snapTarget: snap)
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 745, y: 920), height: 60, snapTarget: snap),
                       CGPoint(x: 465, y: 927))
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 756, y: 920), height: 60, snapTarget: snap),
                       CGPoint(x: 476, y: 927))
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 900, y: 920), height: 60, snapTarget: snap),
                       CGPoint(x: 620, y: 927))
    }

    func testLeavingAndReenteringZoneRearmsSnapping() {
        var drag = OverlayDragSession(pointer: pointer, frame: initial)
        _ = drag.topLeft(at: CGPoint(x: 720, y: 920), height: 60, snapTarget: snap)
        _ = drag.topLeft(at: CGPoint(x: 745, y: 920), height: 60, snapTarget: snap)
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 1200, y: 920), height: 60, snapTarget: snap),
                       CGPoint(x: 920, y: 927))
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 756, y: 927), height: 60, snapTarget: snap), target)
    }

    func testPullingAwayVerticallyAndResizingPreservesGrabOffset() {
        var drag = OverlayDragSession(pointer: pointer, frame: initial)
        _ = drag.topLeft(at: CGPoint(x: 720, y: 920), height: 60, snapTarget: snap)
        XCTAssertEqual(drag.topLeft(at: CGPoint(x: 720, y: 880), height: 240, snapTarget: snap),
                       CGPoint(x: 440, y: 887))
    }
}
