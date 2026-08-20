import Foundation

struct CaptureRestartRunResult: Equatable, Sendable {
    enum Participation: String, Hashable, Sendable {
        case owner
        case joined
        case rejectedStopping = "rejected_stopping"
    }

    let attemptID: Int?
    let succeeded: Bool
    let participation: Participation
}

actor CaptureRestartCoordinator {
    private struct ActiveRestart {
        let id: Int
        let task: Task<Bool, Never>
    }

    private var active: ActiveRestart?
    private var nextAttemptID = 1
    private var stopping = false

    func run(
        operation: @escaping @Sendable (Int) async -> Bool
    ) async -> CaptureRestartRunResult {
        if stopping {
            return CaptureRestartRunResult(
                attemptID: nil,
                succeeded: false,
                participation: .rejectedStopping
            )
        }
        if let active {
            let succeeded = await active.task.value
            return CaptureRestartRunResult(
                attemptID: active.id,
                succeeded: succeeded,
                participation: .joined
            )
        }

        let attemptID = nextAttemptID
        nextAttemptID += 1
        let task = Task { await operation(attemptID) }
        active = ActiveRestart(id: attemptID, task: task)
        let succeeded = await task.value
        if active?.id == attemptID {
            active = nil
        }
        return CaptureRestartRunResult(
            attemptID: attemptID,
            succeeded: succeeded,
            participation: .owner
        )
    }

    func stopAndWait() async {
        stopping = true
        guard let current = active else { return }
        current.task.cancel()
        _ = await current.task.value
        if active?.id == current.id {
            active = nil
        }
    }
}

struct CaptureRestartProvenanceEvent: Equatable, Sendable {
    let attemptID: Int
    let reason: String
    let phase: String
    let source: String?
    let terminalStatus: String?
    let monotonicNS: UInt64
    let elapsedFromRequestMS: Double
    let elapsedFromStartCompletedMS: Double?
    let elapsedFromFirstCallbackMS: Double?
}

final class CaptureRestartProvenanceLedger: @unchecked Sendable {
    private struct Attempt {
        let id: Int
        let reason: String
        let requestNS: UInt64
        var phases: [String: UInt64]
        var firstCallbacks: [String: UInt64]
        var terminalStatus: String?
    }

    private let lock = NSLock()
    private var attempts: [Int: Attempt] = [:]
    private var lastMonotonicNS: UInt64 = 0

    func begin(attemptID: Int, reason: String, at monotonicNS: UInt64) -> CaptureRestartProvenanceEvent? {
        lock.lock()
        defer { lock.unlock() }
        if attempts.values.contains(where: { $0.terminalStatus == nil }) {
            return nil
        }
        let timestamp = normalize(monotonicNS)
        attempts[attemptID] = Attempt(
            id: attemptID,
            reason: reason,
            requestNS: timestamp,
            phases: ["requested": timestamp],
            firstCallbacks: [:],
            terminalStatus: nil
        )
        return makeEvent(
            attemptID: attemptID,
            phase: "requested",
            source: nil,
            terminalStatus: nil,
            timestamp: timestamp
        )
    }

    func mark(
        attemptID: Int,
        phase: String,
        source: String? = nil,
        at monotonicNS: UInt64
    ) -> CaptureRestartProvenanceEvent? {
        lock.lock()
        defer { lock.unlock() }
        guard var attempt = attempts[attemptID] else { return nil }
        let key = source.map { "\(phase):\($0)" } ?? phase
        guard attempt.phases[key] == nil else { return nil }
        let timestamp = normalize(monotonicNS)
        attempt.phases[key] = timestamp
        if phase == "first_callback", let source {
            attempt.firstCallbacks[source] = timestamp
        }
        attempts[attemptID] = attempt
        return makeEvent(
            attemptID: attemptID,
            phase: phase,
            source: source,
            terminalStatus: nil,
            timestamp: timestamp
        )
    }

    func terminal(
        attemptID: Int,
        status: String,
        at monotonicNS: UInt64
    ) -> CaptureRestartProvenanceEvent? {
        lock.lock()
        defer { lock.unlock() }
        guard var attempt = attempts[attemptID],
              attempt.terminalStatus == nil
        else {
            return nil
        }
        let timestamp = normalize(monotonicNS)
        attempt.terminalStatus = status
        attempt.phases["terminal"] = timestamp
        attempts[attemptID] = attempt
        return makeEvent(
            attemptID: attemptID,
            phase: "terminal",
            source: nil,
            terminalStatus: status,
            timestamp: timestamp
        )
    }

    private func normalize(_ candidate: UInt64) -> UInt64 {
        let normalized = candidate > lastMonotonicNS ? candidate : lastMonotonicNS + 1
        lastMonotonicNS = normalized
        return normalized
    }

    private func makeEvent(
        attemptID: Int,
        phase: String,
        source: String?,
        terminalStatus: String?,
        timestamp: UInt64
    ) -> CaptureRestartProvenanceEvent? {
        guard let attempt = attempts[attemptID] else { return nil }
        let startCompleted = attempt.phases["start_completed"]
        let firstCallback = source.flatMap { attempt.firstCallbacks[$0] }
        return CaptureRestartProvenanceEvent(
            attemptID: attempt.id,
            reason: attempt.reason,
            phase: phase,
            source: source,
            terminalStatus: terminalStatus,
            monotonicNS: timestamp,
            elapsedFromRequestMS: milliseconds(timestamp - attempt.requestNS),
            elapsedFromStartCompletedMS: startCompleted.map { signedMilliseconds(timestamp, since: $0) },
            elapsedFromFirstCallbackMS: firstCallback.map { signedMilliseconds(timestamp, since: $0) }
        )
    }

    private func milliseconds(_ nanoseconds: UInt64) -> Double {
        Double(nanoseconds) / 1_000_000.0
    }

    private func signedMilliseconds(_ value: UInt64, since reference: UInt64) -> Double {
        if value >= reference {
            return milliseconds(value - reference)
        }
        return -milliseconds(reference - value)
    }
}

enum CaptureMonotonicClock {
    static func nowNanoseconds() -> UInt64 {
        DispatchTime.now().uptimeNanoseconds
    }
}
