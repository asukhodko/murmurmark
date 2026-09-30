import CryptoKit
import Foundation
import XCTest
@testable import MurmurMarkCLI

final class AuthoritativeHandoffStateTests: XCTestCase {
    func testSnapshotIsIndependentButReuseRequiresCurrentSource() throws {
        let session = FileManager.default.temporaryDirectory
            .appendingPathComponent("murmurmark-authoritative-handoff-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: session) }

        let sourcePath = "derived/transcript-simple/resolved/transcript.profile_v1.md"
        let snapshotPath = "derived/pipeline-run/authoritative-handoff/transcript.fixture.md"
        let source = session.appendingPathComponent(sourcePath)
        let snapshot = session.appendingPathComponent(snapshotPath)
        let readiness = session.appendingPathComponent("derived/readiness/session_readiness.json")
        let handoff = session.appendingPathComponent("derived/pipeline-run/authoritative_handoff.json")
        try FileManager.default.createDirectory(
            at: source.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: snapshot.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(
            at: readiness.deletingLastPathComponent(), withIntermediateDirectories: true
        )
        let transcript = Data("# authoritative\n".utf8)
        try transcript.write(to: source)
        try transcript.write(to: snapshot)
        let digest = SHA256.hash(data: transcript).map { String(format: "%02x", $0) }.joined()

        try writeJSON(
            [
                "selected_profile": "profile_v1",
                "outputs": ["transcript": ["path": sourcePath]],
            ],
            to: readiness
        )
        try writeJSON(
            [
                "schema": "murmurmark.authoritative_handoff/v1",
                "status": "ready",
                "selected_transcript_profile": "profile_v1",
                "paths": ["transcript": snapshotPath],
                "transcript_fingerprint": [
                    "path": snapshotPath,
                    "size": transcript.count,
                    "sha256": digest,
                ],
                "source_transcript_fingerprint": [
                    "path": sourcePath,
                    "size": transcript.count,
                    "sha256": digest,
                ],
            ],
            to: handoff
        )

        XCTAssertNotNil(AuthoritativeHandoffState.payload(session))
        try Data("mutated\n".utf8).write(to: source)
        XCTAssertNil(AuthoritativeHandoffState.payload(session))
        try transcript.write(to: source)
        var readinessPayload = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(contentsOf: readiness)) as? [String: Any])
        readinessPayload["selected_profile"] = "reviewed_v1"
        try writeJSON(readinessPayload, to: readiness)
        XCTAssertNil(AuthoritativeHandoffState.payload(session))
    }

    private func writeJSON(_ payload: [String: Any], to path: URL) throws {
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: path)
    }
}
