import CoreGraphics
import XCTest
@testable import OnTrackCore

final class OverlayPlacementTests: XCTestCase {
    private let screen = CGRect(x: 0, y: 0, width: 1512, height: 982)
    private let visible = CGRect(x: 0, y: 40, width: 1512, height: 918)

    func testReleaseAboveDefaultPositionSnapsToCenter() {
        let overlay = CGRect(x: 600, y: 898, width: 560, height: 60)
        XCTAssertEqual(OverlayPlacement.snapTopLeft(for: overlay, screenFrame: screen, visibleFrame: visible),
                       CGPoint(x: 476, y: 934))
    }

    func testDropAwayFromTopCenterDoesNotSnap() {
        for overlay in [CGRect(x: 600, y: 700, width: 560, height: 60),
                        CGRect(x: 0, y: 898, width: 560, height: 60),
                        CGRect(x: 952, y: 898, width: 560, height: 60),
                        CGRect(x: 600, y: 1000, width: 560, height: 60)] {
            XCTAssertNil(OverlayPlacement.snapTopLeft(for: overlay, screenFrame: screen, visibleFrame: visible))
        }
    }

    func testSnapThresholdIncludesNearMissBelowDefault() {
        XCTAssertNotNil(OverlayPlacement.snapTopLeft(for: CGRect(x: 476, y: 842, width: 560, height: 60),
                                                    screenFrame: screen, visibleFrame: visible))
        XCTAssertNil(OverlayPlacement.snapTopLeft(for: CGRect(x: 476, y: 841, width: 560, height: 60),
                                                 screenFrame: screen, visibleFrame: visible))
    }

    func testDraggingHandleToVeryTopEdgeStillSnaps() {
        XCTAssertEqual(OverlayPlacement.snapTopLeft(for: CGRect(x: 476, y: 930, width: 560, height: 60),
                                                   screenFrame: screen, visibleFrame: visible),
                       CGPoint(x: 476, y: 934))
    }

    func testSecondaryDisplayAndExpandedOverlayUseActualGeometry() {
        let external = CGRect(x: -1920, y: 400, width: 1920, height: 1080)
        let usable = CGRect(x: -1920, y: 400, width: 1920, height: 1055)
        let overlay = CGRect(x: -1250, y: 1155, width: 640, height: 300)
        XCTAssertEqual(OverlayPlacement.snapTopLeft(for: overlay, screenFrame: external, visibleFrame: usable),
                       CGPoint(x: -1280, y: 1431))
        XCTAssertNil(OverlayPlacement.snapTopLeft(for: overlay, screenFrame: screen, visibleFrame: visible))
    }
}
