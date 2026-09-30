import Foundation
import XCTest
@testable import MurmurMarkCLI

final class ReviewSuggestedLanePackTests: XCTestCase {
    func testMissingSuggestionRoutesToManualAnswersWithoutLosingSessionPaths() {
        let session = URL(fileURLWithPath: "/tmp/review-session")
        let plan = session.appendingPathComponent("custom-plan")
        let packs = session.appendingPathComponent("custom-packs")
        let context = ReviewLaneApplyPrintContext(
            lane: "classify_audio", session: session,
            manifest: packs.appendingPathComponent("custom-manifest.json"),
            template: plan.appendingPathComponent("custom-template.jsonl"),
            planURL: plan, lanePackOutURL: packs, answers: nil,
            answersFile: packs.appendingPathComponent("review_lane_answers.classify_audio.suggested.txt"),
            answersSource: "suggested", decisions: plan.appendingPathComponent("custom-decisions.jsonl"),
            applyReport: plan.appendingPathComponent("report.json"), reviewer: "tester",
            progress: nil, dryRun: true
        )
        XCTAssertTrue(ReviewLaneApplyNextCommand.command(context).contains("--answers-source suggested"))
        let retry = ReviewLaneApplyNextCommand.command(context, manualFallback: true)
        XCTAssertTrue(retry.contains("--answers-source manual"))
        XCTAssertFalse(retry.contains("suggested"))
        XCTAssertTrue(retry.contains("--session /tmp/review-session"))
        XCTAssertTrue(retry.contains("--out-dir /tmp/review-session/custom-packs"))
        XCTAssertTrue(retry.contains("--manifest /tmp/review-session/custom-packs/custom-manifest.json"))
        XCTAssertTrue(retry.contains("--reviewer tester"))
    }

    func testCurrentWorkspaceManifestsExcludeStaleLaneFiles() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmurmark-review-lanes-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let laneDirectory = root.appendingPathComponent("derived/readiness/review-plan/lane-packs")
        try FileManager.default.createDirectory(at: laneDirectory, withIntermediateDirectories: true)
        let current = laneDirectory.appendingPathComponent("review_lane_pack.check_transcript_text.json")
        let stale = laneDirectory.appendingPathComponent("review_lane_pack.check_transcript_order.json")
        try Data("{}\n".utf8).write(to: current)
        try Data("{}\n".utf8).write(to: stale)

        let workspace = root.appendingPathComponent("derived/readiness/review-plan/review_workspace.json")
        let payload: [String: Any] = [
            "schema": "murmurmark.review_workspace/v1",
            "lanes": [["lane": "check_transcript_text", "manifest": current.path]],
        ]
        try JSONSerialization.data(withJSONObject: payload).write(to: workspace)

        XCTAssertEqual(
            ReviewSuggestedCommand.reviewLanePacks(for: root).map(\.lastPathComponent),
            ["review_lane_pack.check_transcript_text.json"]
        )
    }

    func testDirectoryScanRemainsLegacyFallback() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmurmark-review-lanes-legacy-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }

        let laneDirectory = root.appendingPathComponent("derived/readiness/review-plan/lane-packs")
        try FileManager.default.createDirectory(at: laneDirectory, withIntermediateDirectories: true)
        let lane = laneDirectory.appendingPathComponent("review_lane_pack.classify_audio.json")
        try Data("{}\n".utf8).write(to: lane)

        XCTAssertEqual(
            ReviewSuggestedCommand.reviewLanePacks(for: root).map(\.lastPathComponent),
            ["review_lane_pack.classify_audio.json"]
        )
    }
}
