import Foundation
import XCTest
@testable import MurmurMarkCLI

final class ReviewSuggestedLanePackTests: XCTestCase {
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
