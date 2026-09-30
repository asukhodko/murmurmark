import XCTest
@testable import MurmurMarkCLI

final class TranscriptAttributionDisclaimerTests: XCTestCase {
    func testRichViewDoesNotClaimWordAccuracyOrIdentity() {
        let warning = TranscriptCommands.attributionDisclaimer(kind: "remote_speaker_coverage_v3")
        XCTAssertTrue(warning.contains("does not certify transcript words"))
        XCTAssertTrue(warning.contains("not verified people"))
        XCTAssertEqual(TranscriptCommands.attributionDisclaimer(kind: "aggregate_colleagues"), "")
    }
}
