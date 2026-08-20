import XCTest
@testable import MurmurMarkCLI

private actor InvocationCounter {
    private var value = 0

    func increment() {
        value += 1
    }

    func count() -> Int {
        value
    }
}

final class CaptureRestartCoordinatorTests: XCTestCase {
    func testConcurrentRequestsJoinOneRestart() async throws {
        let coordinator = CaptureRestartCoordinator()
        let counter = InvocationCounter()
        let first = Task {
            await coordinator.run { _ in
                await counter.increment()
                try? await Task.sleep(nanoseconds: 50_000_000)
                return true
            }
        }
        try await Task.sleep(nanoseconds: 5_000_000)
        let second = Task {
            await coordinator.run { _ in
                await counter.increment()
                return false
            }
        }

        let results = [await first.value, await second.value]
        let invocationCount = await counter.count()
        XCTAssertEqual(invocationCount, 1)
        XCTAssertEqual(Set(results.map(\.participation)), [.owner, .joined])
        XCTAssertEqual(Set(results.compactMap(\.attemptID)), Set([1]))
        XCTAssertTrue(results.allSatisfy(\.succeeded))
    }

    func testStoppingCancelsActiveRestartAndRejectsNewWork() async throws {
        let coordinator = CaptureRestartCoordinator()
        let counter = InvocationCounter()
        let active = Task {
            await coordinator.run { _ in
                await counter.increment()
                do {
                    try await Task.sleep(nanoseconds: 10_000_000_000)
                    return true
                } catch {
                    return false
                }
            }
        }
        while await counter.count() == 0 {
            try await Task.sleep(nanoseconds: 1_000_000)
        }

        await coordinator.stopAndWait()
        let cancelled = await active.value
        XCTAssertFalse(cancelled.succeeded)
        let rejected = await coordinator.run { _ in true }
        XCTAssertEqual(rejected.participation, .rejectedStopping)
        XCTAssertFalse(rejected.succeeded)
    }

    func testProvenanceIsStrictlyMonotonicAndHasOneTerminalOutcome() {
        let ledger = CaptureRestartProvenanceLedger()
        var events: [CaptureRestartProvenanceEvent] = []
        events.append(ledger.begin(attemptID: 7, reason: "stream_stopped", at: 100)!)
        events.append(ledger.mark(attemptID: 7, phase: "old_stream_already_stopped", at: 90)!)
        events.append(ledger.mark(attemptID: 7, phase: "start_requested", at: 110)!)
        events.append(ledger.mark(attemptID: 7, phase: "start_completed", at: 200)!)
        events.append(ledger.terminal(attemptID: 7, status: "started", at: 201)!)
        events.append(
            ledger.mark(
                attemptID: 7,
                phase: "first_callback",
                source: "remote",
                at: 210
            )!
        )
        events.append(
            ledger.mark(
                attemptID: 7,
                phase: "first_committed_pcm",
                source: "remote",
                at: 220
            )!
        )

        XCTAssertTrue(zip(events, events.dropFirst()).allSatisfy { $0.monotonicNS < $1.monotonicNS })
        XCTAssertEqual(events.filter { $0.phase == "terminal" }.count, 1)
        XCTAssertNil(ledger.terminal(attemptID: 7, status: "failed", at: 230))
        XCTAssertNil(
            ledger.mark(
                attemptID: 7,
                phase: "first_committed_pcm",
                source: "remote",
                at: 240
            )
        )
        XCTAssertEqual(
            events.last?.elapsedFromFirstCallbackMS ?? -1,
            0.00001,
            accuracy: 0.0000001
        )
    }

    func testLateCallbackFromCompletedAttemptSurvivesNextAttempt() {
        let ledger = CaptureRestartProvenanceLedger()
        XCTAssertNotNil(ledger.begin(attemptID: 1, reason: "stream_stopped", at: 100))
        XCTAssertNotNil(ledger.mark(attemptID: 1, phase: "start_requested", at: 110))
        XCTAssertNotNil(ledger.mark(attemptID: 1, phase: "start_completed", at: 120))
        XCTAssertNotNil(ledger.terminal(attemptID: 1, status: "started", at: 130))
        XCTAssertNotNil(ledger.begin(attemptID: 2, reason: "capture_stalled", at: 140))

        let late = ledger.mark(
            attemptID: 1,
            phase: "first_callback",
            source: "remote",
            at: 150
        )
        XCTAssertEqual(late?.attemptID, 1)
        XCTAssertEqual(late?.elapsedFromRequestMS ?? -1, 0.00005, accuracy: 0.0000001)
        XCTAssertNotNil(ledger.terminal(attemptID: 2, status: "cancelled", at: 160))
    }
}
