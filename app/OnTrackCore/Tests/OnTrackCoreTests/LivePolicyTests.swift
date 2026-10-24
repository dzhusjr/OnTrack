import XCTest
@testable import OnTrackCore

final class LivePolicyTests: XCTestCase {
    func testFollowsUserPreferenceWhenPluggedIn() {
        XCTAssertTrue(LivePolicy.live(preferLive: true, onBattery: false, recordOnlyOnBattery: true))
        XCTAssertFalse(LivePolicy.live(preferLive: false, onBattery: false, recordOnlyOnBattery: true))
    }

    func testBatteryRuleTurnsLiveOffOnlyWhenEnabled() {
        XCTAssertFalse(LivePolicy.live(preferLive: true, onBattery: true, recordOnlyOnBattery: true))
        XCTAssertTrue(LivePolicy.live(preferLive: true, onBattery: true, recordOnlyOnBattery: false))
    }
}
