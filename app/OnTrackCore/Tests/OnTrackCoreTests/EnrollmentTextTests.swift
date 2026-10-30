import XCTest
@testable import OnTrackCore

final class EnrollmentTextTests: XCTestCase {
    func testEveryLanguageHasAThirtySecondPassage() {
        XCTAssertEqual(EnrollmentText.all.map(\.code), ["en", "nl", "uk", "ru"])
        for lang in EnrollmentText.all {
            let words = lang.passage.split(whereSeparator: \.isWhitespace).count
            XCTAssertTrue((60...100).contains(words), "\(lang.name): \(words) words")
            XCTAssertFalse(lang.passage.contains("\\"), lang.name)
        }
    }

    func testOnlyTheSelectedLanguagesAreOffered() {
        XCTAssertEqual(EnrollmentText.offered(enabled: ["uk", "en"]).map(\.code), ["en", "uk"])
        XCTAssertEqual(EnrollmentText.offered(enabled: []).count, 4)
        XCTAssertEqual(EnrollmentText.offered(enabled: ["fr"]).count, 4)
    }

    func testRememberedChoiceIsUsedOnlyWhileStillOffered() {
        let two = EnrollmentText.offered(enabled: ["nl", "ru"])
        XCTAssertEqual(EnrollmentText.pick("ru", from: two).code, "ru")
        XCTAssertEqual(EnrollmentText.pick("en", from: two).code, "nl")
        XCTAssertEqual(EnrollmentText.pick(nil, from: two).code, "nl")
    }
}
