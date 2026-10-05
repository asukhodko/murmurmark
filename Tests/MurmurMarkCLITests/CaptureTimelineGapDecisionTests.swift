import XCTest
@testable import MurmurMarkCLI

final class CaptureTimelineGapDecisionTests: XCTestCase {
    func testDelayedCallbackWithContinuousPresentationTimeDoesNotInsertSilence() {
        let first = AudioTimelineGapDecision.evaluate(
            framesWritten: 0,
            sampleRate: 48_000,
            presentationTimeSec: 100,
            firstPresentationTimeSec: nil,
            wallElapsedSec: 0.2
        )
        XCTAssertEqual(first.gapFrames, 9_600)
        XCTAssertEqual(first.timestampSupportedFrames, 0)
        XCTAssertEqual(first.callbackOnlyFrames, 9_600)

        let delayed = AudioTimelineGapDecision.evaluate(
            framesWritten: 9_600 + 48_000,
            sampleRate: 48_000,
            presentationTimeSec: 101,
            firstPresentationTimeSec: first.firstPresentationTimeSec,
            wallElapsedSec: 4.2
        )
        XCTAssertEqual(delayed.gapFrames, 0)
        XCTAssertFalse(delayed.timelineReset)
    }

    func testPresentationTimestampGapIsPreserved() {
        let gap = AudioTimelineGapDecision.evaluate(
            framesWritten: 48_000,
            sampleRate: 48_000,
            presentationTimeSec: 103,
            firstPresentationTimeSec: 100,
            wallElapsedSec: 3
        )
        XCTAssertEqual(gap.gapFrames, 96_000)
        XCTAssertEqual(gap.timestampSupportedFrames, 96_000)
        XCTAssertEqual(gap.callbackOnlyFrames, 0)
    }

    func testMissingTimestampUsesExplicitWallClockFallback() {
        let gap = AudioTimelineGapDecision.evaluate(
            framesWritten: 48_000,
            sampleRate: 48_000,
            presentationTimeSec: nil,
            firstPresentationTimeSec: nil,
            wallElapsedSec: 3
        )
        XCTAssertEqual(gap.gapFrames, 96_000)
        XCTAssertEqual(gap.timestampSupportedFrames, 0)
        XCTAssertEqual(gap.callbackOnlyFrames, 96_000)
    }

    func testBackwardTimestampResetsAnchorWithoutAddingSilence() {
        let reset = AudioTimelineGapDecision.evaluate(
            framesWritten: 144_000,
            sampleRate: 48_000,
            presentationTimeSec: 100,
            firstPresentationTimeSec: 100,
            wallElapsedSec: 3
        )
        XCTAssertTrue(reset.timelineReset)
        XCTAssertEqual(reset.gapFrames, 0)

        let next = AudioTimelineGapDecision.evaluate(
            framesWritten: 192_000,
            sampleRate: 48_000,
            presentationTimeSec: 101,
            firstPresentationTimeSec: reset.firstPresentationTimeSec,
            wallElapsedSec: 4
        )
        XCTAssertEqual(next.gapFrames, 0)
        XCTAssertFalse(next.timelineReset)
    }

    func testFirstValidTimestampAfterMissingTimestampKeepsWallClockProvenance() {
        let anchored = AudioTimelineGapDecision.evaluate(
            framesWritten: 48_000,
            sampleRate: 48_000,
            presentationTimeSec: 103,
            firstPresentationTimeSec: nil,
            wallElapsedSec: 3
        )
        XCTAssertEqual(anchored.gapFrames, 96_000)
        XCTAssertEqual(anchored.timestampSupportedFrames, 0)
        XCTAssertEqual(anchored.callbackOnlyFrames, 96_000)

        let next = AudioTimelineGapDecision.evaluate(
            framesWritten: 192_000,
            sampleRate: 48_000,
            presentationTimeSec: 104,
            firstPresentationTimeSec: anchored.firstPresentationTimeSec,
            wallElapsedSec: 7
        )
        XCTAssertEqual(next.gapFrames, 0)
        XCTAssertFalse(next.timelineReset)
    }

    func testInvalidPresentationTimestampUsesWallClockFallback() {
        for timestamp in [Double.nan, .infinity, -.infinity] {
            let gap = AudioTimelineGapDecision.evaluate(
                framesWritten: 48_000,
                sampleRate: 48_000,
                presentationTimeSec: timestamp,
                firstPresentationTimeSec: 100,
                wallElapsedSec: 3
            )
            XCTAssertEqual(gap.gapFrames, 96_000)
            XCTAssertEqual(gap.timestampSupportedFrames, 0)
            XCTAssertEqual(gap.callbackOnlyFrames, 96_000)
            XCTAssertEqual(gap.firstPresentationTimeSec, 100)
        }
    }

    func testFiftyMillisecondToleranceBoundary() {
        for gapFrames in [2_399, 2_400, 2_401] {
            let gap = AudioTimelineGapDecision.evaluate(
                framesWritten: 48_000,
                sampleRate: 48_000,
                presentationTimeSec: 101 + Double(gapFrames) / 48_000,
                firstPresentationTimeSec: 100,
                wallElapsedSec: 4
            )
            XCTAssertEqual(gap.gapFrames, gapFrames > 2_400 ? Int64(gapFrames) : 0)
            XCTAssertFalse(gap.timelineReset)
        }
    }

    func testGapManifestKeepsTimingProvenanceAndReadsLegacyRows() throws {
        let gap = CaptureGapManifest(
            startSec: 3,
            endSec: 5,
            durationSec: 2,
            sources: ["mic"],
            evidence: "writer_inserted_timeline_silence",
            capturedAudio: false,
            timingEvidence: [
                CaptureGapTimingEvidence(
                    source: "mic",
                    startSec: 3,
                    endSec: 5,
                    basis: "presentation_timestamp",
                    timestampSupportedSec: 2,
                    callbackOnlySec: 0
                ),
            ]
        )
        let encoded = try JSONEncoder().encode(gap)
        let decoded = try JSONDecoder().decode(CaptureGapManifest.self, from: encoded)
        XCTAssertEqual(decoded.timingEvidence?.first?.timestampSupportedSec, 2)

        let legacy = Data(
            """
            {"start_sec":3,"end_sec":5,"duration_sec":2,"sources":["mic"],
             "evidence":"writer_inserted_timeline_silence","captured_audio":false}
            """.utf8
        )
        XCTAssertNil(try JSONDecoder().decode(CaptureGapManifest.self, from: legacy).timingEvidence)
    }
}
