import AppKit
import AVFoundation
import CoreMedia
import CryptoKit
import Darwin
import Foundation
import ScreenCaptureKit

final class SingleResumeBox<Value>: @unchecked Sendable {
    private let lock = NSLock()
    private var didResume = false
    private let continuation: CheckedContinuation<Value, Error>

    init(_ continuation: CheckedContinuation<Value, Error>) {
        self.continuation = continuation
    }

    func resume(_ result: sending Result<Value, Error>) {
        lock.lock()
        defer { lock.unlock() }
        guard !didResume else {
            return
        }
        didResume = true
        continuation.resume(with: result)
    }
}

enum ScreenCaptureContent {
    static func current(timeoutSeconds: TimeInterval = 5) async throws -> SCShareableContent {
        try await withCheckedThrowingContinuation { continuation in
            let box = SingleResumeBox(continuation)
            let contentTask = Task {
                do {
                    box.resume(.success(try await SCShareableContent.current))
                } catch {
                    box.resume(.failure(error))
                }
            }
            Task {
                let nanoseconds = UInt64(max(timeoutSeconds, 0.1) * 1_000_000_000)
                try? await Task.sleep(nanoseconds: nanoseconds)
                contentTask.cancel()
                box.resume(
                    .failure(
                        CLIError(
                            "ScreenCaptureKit shareable content timed out after \(Int(timeoutSeconds))s"
                        )
                    )
                )
            }
        }
    }
}

@main
struct MurmurMark {
    static let version = "0.1.0"

    static func main() async {
        do {
            try RuntimeHome.apply()
            var args = Array(CommandLine.arguments.dropFirst())
            guard let command = args.first else {
                printHelp()
                return
            }
            args.removeFirst()

            switch command {
            case "doctor":
                try await Commands.doctor(args)
            case "self-test":
                try Commands.selfTest(args)
            case "acceptance":
                try Commands.acceptance(args)
            case "list-apps":
                Commands.listApps()
            case "list-audio-devices":
                Commands.listAudioDevices()
            case "record":
                try await Commands.record(args)
            case "meeting":
                try await MeetingCommands.meeting(args)
            case "latest":
                try PipelineCommands.latest(args)
            case "sessions":
                try PipelineCommands.sessions(args)
            case "process":
                try PipelineCommands.process(args)
            case "enrich":
                try PipelineCommands.enrich(args)
            case "status":
                try PipelineCommands.status(args)
            case "outcome":
                try PipelineCommands.outcome(args)
            case "next":
                try PipelineCommands.next(args)
            case "open":
                try OpenCommands.open(args)
            case "report":
                try PipelineCommands.report(args)
            case "review":
                try ReviewCommands.review(args)
            case "audit":
                try AuditCommands.audit(args)
            case "cleanup":
                try CleanupCommands.cleanup(args)
            case "repair":
                try RepairCommands.repair(args)
            case "synthesize":
                try SynthesisCommands.synthesize(args)
            case "notes":
                try NotesCommands.notes(args)
            case "transcript":
                try TranscriptCommands.transcript(args)
            case "corpus":
                try CorpusCommands.corpus(args)
            case "live":
                try LiveCommands.live(args)
            case "experiment":
                try ExperimentCommands.experiment(args)
            case "finish":
                try FinishCommands.finish(args)
            case "export":
                try ExportCommands.export(args)
            case "retention":
                try RetentionCommands.retention(args)
            case "config":
                try ConfigCommands.config(args)
            case "preprocess":
                try Commands.preprocess(args)
            case "reconcile-transcript":
                try Commands.reconcileTranscript(args)
            case "inspect":
                try Commands.inspect(args)
            case "export-audio":
                try Commands.exportAudio(args)
            case "version", "--version", "-v":
                print("murmurmark \(MurmurMark.version)")
            case "help", "--help", "-h":
                printHelp()
            default:
                throw CLIError("unknown command: \(command)")
            }
        } catch {
            fputs("error: \(error.localizedDescription)\n", stderr)
            Foundation.exit(1)
        }
    }

    static func printHelp() {
        print("""
        MurmurMark \(MurmurMark.version)

        Normal flow:
          murmurmark doctor
          murmurmark self-test
          murmurmark config init
          murmurmark acceptance --skip-release
          murmurmark meeting --target-bundle system

        Low-level flow:
          SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
          murmurmark record --out "$SESSION" --target-bundle system
          murmurmark process "$SESSION"

        Handoff rule:
          When a command ends with `next: ...`, that final line is the primary command to run next.
          Avoid `latest` after recording if another terminal can start a newer session.

        Everyday usage:
          murmurmark doctor [--strict]
          murmurmark self-test
          murmurmark acceptance [--skip-release] [--python PATH] [--live-checklist] [--report PATH]
                              [--live-session SESSION|latest] [--require-meeting-lifecycle]
                              [--sessions-root ./sessions]
          murmurmark record [--out ./session] [--duration 60] [--target-bundle com.example.App]
                            [--mic default] [--mic-backend screencapturekit|voice-processing]
                            [--remote-backend screencapturekit|audio-input] [--remote-device Device_UID]
                            [--live-pipeline] [--live-segment-sec 60] [--live-overlap-sec 5]
                            [--live-no-worker] [--live-no-finalize]
          murmurmark meeting [record options]
          murmurmark meeting --resume ./session
          murmurmark sessions [--limit 10] [--status exported|exportable|review_required|incomplete] [--path-only|--next-only|--json]
                              [--sessions-root ./sessions]
          murmurmark latest [--sessions-root ./sessions]
          murmurmark process ./session|latest [--model ./model.bin] [--language ru] [--prompt-file ./prompt.txt]
                                [--full]
                                [--force-asr] [--reuse-asr-cache] [--plan-only] [--skip-build]
                                [--asr-track-workers 1|2] [--asr-threads N] [--micro-asr-workers 1|2|4]
                                [--skip-preprocess] [--skip-transcription] [--skip-audits] [--skip-cleanup]
                                [--skip-stronger-audio-judge] [--stronger-audio-judge-exhaustive]
                                [--progress-interval-sec 60] [--deferred-step-timeout-sec 3600] [--allow-partial]
                                [--config murmurmark.config.json] [--sessions-root ./sessions]
          murmurmark enrich ./session|latest [--skip-stronger-audio-judge] [--config murmurmark.config.json]
          murmurmark status [./session|latest] [--sessions-root ./sessions]
          murmurmark outcome [./session|latest] [--refresh] [--sessions-root ./sessions]
          murmurmark next [./session|latest|corpus] [--refresh] [--export-manifest ./export_manifest.json] [--sessions-root ./sessions]
          murmurmark open [./session|latest] [--kind notes|transcript|verdict|readiness|audio-review] [--path-only|--command-only|--cat]
          murmurmark report ./session|latest [--sessions-root ./sessions]
          murmurmark report corpus [--sessions-root ./sessions]
          murmurmark review next [SESSION|latest]
          murmurmark review --help
          murmurmark synthesize ./session|latest [--transcript-profile auto] [--sessions-root ./sessions]
          murmurmark notes ./session|latest [--kind notes|verdict|review-items|evidence] [--profile auto|current|NAME] [--path-only|--cat]
          murmurmark transcript ./session|latest [--profile auto] [--path-only|--cat] [--sessions-root ./sessions]
          murmurmark finish [./session|latest] [--format markdown|obsidian] [--profile auto] [--out-dir exports/private]
                             [--force-export] [--skip-retention] [--sessions-root ./sessions]
          murmurmark export ./session|latest [--format markdown|obsidian] [--profile auto] [--out-dir exports/private]
                             [--include-json] [--force] [--sessions-root ./sessions]
          murmurmark retention plan|apply ./session|latest [--policy examples/retention-policy.local-first.json]
                             [--export-manifest ./exports/private/session/export_manifest.json]
                             [--confirm-delete-raw] [--sessions-root ./sessions]
          murmurmark retention payload ./session|latest [--policy examples/retention-policy.local-first.json]
                             [--export-manifest ./exports/private/session/export_manifest.json] [--provider name]
          murmurmark config init [--config murmurmark.config.json] [--force]
          murmurmark config print [--config murmurmark.config.json]

        Quality and corpus maintenance:
          murmurmark audit local-recall ./session|latest [--profile shadow_v2] [--sessions-root ./sessions]
          murmurmark audit order ./session|latest [--profile auto] [--sessions-root ./sessions]
          murmurmark audit group-overlaps ./session|latest [--profile shadow_v2] [--write-clips] [--sessions-root ./sessions]
          murmurmark audit audio-review ./session|latest [--profile audit_cleanup_v2] [--write-clips] [--sessions-root ./sessions]
          murmurmark audit stronger-audio-judge ./session|latest [--profile audit_cleanup_v2] [--max-items 80]
                                                        [--quick] [--max-computed-items N]
                                                        [--review-lane-pack PATH] [--sessions-root ./sessions]
          murmurmark audit target-me ./session|latest [--profile auto] [--max-items 80] [--sessions-root ./sessions]
          murmurmark cleanup ./session|latest [--input-profile shadow_v2] [--output-profile audit_cleanup_v1]
                             [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair order ./session|latest [--input-profile auto] [--output-profile order_repair_v1]
                                [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair local-recall ./session|latest [--input-profile auto] [--output-profile local_recall_repair_v1]
                               [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair boundary ./session|latest [--sessions-root ./sessions]
          murmurmark repair remote-leak ./session|latest [--sessions-root ./sessions]
          murmurmark corpus process all|latest|./session... [--per-label 16] [--max-items 160] [--sessions-root ./sessions]
          murmurmark corpus build all|latest|./session... [--per-label 16] [--max-items 160] [--sessions-root ./sessions]
          murmurmark corpus evaluate
          murmurmark corpus train-audio-judge
          murmurmark corpus taxonomy
          murmurmark corpus gate
          murmurmark corpus order [all|latest|./session...] [--repair] [--sessions-root ./sessions]
          murmurmark corpus local-recall [all|latest|./session...] [--audit] [--sessions-root ./sessions]
          murmurmark corpus local-recall-repair [all|latest|./session...] [--repair] [--sessions-root ./sessions]
          murmurmark corpus remote-leak [all|latest|./session...] [--plan] [--sessions-root ./sessions]
          murmurmark corpus live [all|latest|./session...] [--refresh] [--target-live-sessions 3] [--sessions-root ./sessions]
          murmurmark live status [all|latest|./session...] [--refresh] [--sessions-root ./sessions]
          murmurmark live watch SESSION|latest [--poll-sec SEC] [--diagnostic-draft] [--sessions-root ./sessions]
          murmurmark live evidence SESSION|latest [--refresh] [--strict] [--require-causal-recovery]
                                                   [--sessions-root ./sessions]
          murmurmark live recovery-evidence [all|latest|SESSION...] [--refresh] [--strict]
                                            [--min-sessions 3] [--sessions-root ./sessions]
          murmurmark live replay SESSION|latest [--refresh] [--with-labs] [--lab-policy POLICY]
          murmurmark live gate [--sessions-root ./sessions]
          murmurmark live pilot [SESSION] [--controlled-real] [--preflight-only] [--skip-safety-gate]
          murmurmark experiment status|report|compare|recover-draft SESSION|latest [--experiment live-shadow-v1]
          murmurmark corpus report

        Setup and diagnostics:
          murmurmark self-test
          murmurmark list-apps
          murmurmark list-audio-devices
          murmurmark inspect ./session|latest [--echo] [--sessions-root ./sessions]

        Advanced/debugging:
          murmurmark preprocess ./session [--echo diagnostic|clean] [--echo-engine linear_baseline|local_fir|speexdsp|webrtc-apm]
                              [--echo-policy preserve_local|role_safe|strict_silence]
          murmurmark reconcile-transcript ./session [--in ./transcript.rich.json] [--out ./transcript.rich.json]
          murmurmark export-audio ./session [--sample-rate 16000]

        Notes:
          record defaults to ScreenCaptureKit for separate mic and remote tracks.
          self-test runs the quick local CLI smoke fixture through this command surface.
          acceptance runs the CLI MVP gate for this checkout or release bundle.
          audio-input remote capture and voice-processing mic capture are experimental comparison modes.
          It writes mic.caf, remote.caf, session.json, events.jsonl and pipeline_job.json.
          Without --duration, recording runs until Ctrl-C and finalizes the session.
          --live-pipeline is disabled by default; use `murmurmark live pilot` only for shadow evidence collection.
          Use plain `record --target-bundle system` for real meetings.
          Use --live-no-finalize only for lab diagnostics when unsafe live mode is explicitly enabled.
          SIGTERM/SIGHUP are treated as unexpected stops and leave a partial session.
          Unexpected ScreenCaptureKit stops are restarted when possible; unrecovered stops finalize a partial session and exit with an error.
          Without --out, recording creates a unique directory under ./sessions.
          sessions lists recent session packages and their readiness state.
          process runs the current post-recording pipeline and prints the readiness summary.
          status prints the current readiness dashboard without recomputing reports.
          next prints the single recommended next command from readiness.
          Summary handoffs end with a final `next: ...` command.
          open prints or streams the selected local output artifact from readiness.
          report refreshes and prints the readiness summary without rerunning ASR/audio processing.
          review next prints the next review command; review --help shows lane/workspace/apply commands.
          audit wraps order, local recall, group overlap, audio-review, stronger-audio-judge and target-me scripts through the project Python runtime.
          cleanup wraps conservative audit cleanup profiles.
          repair wraps explicit structural transcript repairs into separate profiles.
          synthesize refreshes deterministic extractive notes and quality verdict.
          notes prints or streams the selected notes/verdict artifacts.
          transcript prints or streams the selected transcript path.
          corpus wraps regression-corpus, audio-judge, corpus gates and operational-readiness scripts.
          finish refreshes readiness, creates the export bundle when allowed, and writes retention/payload recommendations.
          export creates a local user-facing Markdown or Obsidian bundle and blocks readiness export blockers by default.
          retention plans or applies local retention policy; raw deletion requires apply plus --confirm-delete-raw.
          config shows local defaults loaded by process/export.
          advanced/debugging commands are normally called by process; use them directly only to inspect one pipeline layer.
        """)
    }
}

enum Commands {
    static func doctor(_ args: [String]) async throws {
        if ArgumentEditing.hasHelpFlag(args) {
            DoctorChecks.printHelp()
            return
        }
        let strict = args.contains("--strict")
        let unknown = args.filter { $0 != "--strict" }
        guard unknown.isEmpty else {
            throw CLIError("doctor only supports --strict")
        }

        var report = DoctorReport()
        print("murmurmark: \(MurmurMark.version)")
        print("home: \(FileManager.default.currentDirectoryPath)")
        if let runtimeHome = ProcessInfo.processInfo.environment["MURMURMARK_HOME"], !runtimeHome.isEmpty {
            print("MURMURMARK_HOME: \(runtimeHome)")
        }
        print("executable: \(ExecutablePath.current())")
        print("macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
        print("swift capture backend: screencapturekit_system")
        DoctorChecks.checkLocalConfig(&report)
        DoctorChecks.checkExecutable("ffmpeg", required: true, report: &report)
        DoctorChecks.checkExecutable("ffprobe", required: true, report: &report)
        DoctorChecks.checkExecutable("whisper-cli", required: true, report: &report)
        DoctorChecks.checkExecutable("jq", required: false, report: &report)
        DoctorChecks.checkExecutable("swiftlint", required: false, report: &report)
        DoctorChecks.checkScripts(&report)
        DoctorChecks.checkPython(&report)
        DoctorChecks.checkWhisperModel(&report)
        DoctorChecks.checkStrongerAudioJudge(&report)
        DoctorChecks.checkTargetMeSpeakerBackend(&report)

        do {
            let content = try await ScreenCaptureContent.current()
            report.check(.passed, "screen/system audio permission", "ok")
            print("shareable displays: \(content.displays.count)")
            print("shareable applications: \(content.applications.count)")
            if content.displays.isEmpty {
                report.check(
                    .fail,
                    "shareable displays",
                    "none visible to ScreenCaptureKit",
                    hint: "run MurmurMark from a logged-in desktop session and re-check before recording"
                )
            }
        } catch {
            report.check(
                .fail,
                "screen/system audio permission",
                "not granted or blocked",
                hint: "grant Screen & System Audio Recording to the terminal or Codex app, then run record again"
            )
            print("screen/system audio detail: \(error.localizedDescription)")
        }

        let microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        let microphoneText = PermissionTexts.microphone(microphoneStatus)
        switch microphoneStatus {
        case .authorized:
            report.check(.passed, "microphone permission", microphoneText)
        case .notDetermined:
            report.check(
                .warn,
                "microphone permission",
                microphoneText,
                hint: "run a recording once or grant microphone access in macOS privacy settings"
            )
        default:
            report.check(.fail, "microphone permission", microphoneText, hint: "grant microphone access to the terminal or Codex app")
        }
        print("microphones: \(AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone], mediaType: .audio, position: .unspecified).devices.count)")
        print("readiness: \(report.readiness)")
        print("checks: ok=\(report.passed) warnings=\(report.warnings) failures=\(report.failures)")
        report.printNext()
        print("status: doctor completed")
        if strict && report.failures > 0 {
            throw CLIError("doctor strict failed: \(report.failures) required checks failed")
        }
    }

    static func selfTest(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            printSelfTestHelp()
            return
        }
        guard args.isEmpty else {
            throw CLIError("self-test does not accept arguments")
        }

        let script = PathURLs.fileURL("scripts/smoke-cli-handoff.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("self-test script not found: \(PathDisplay.display(script))")
        }

        print("self_test:")
        print("  mode: quick")
        print("  command: \(PathDisplay.display(script))")
        fflush(stdout)
        try runSelfTestScript(script)
        print("status: self-test completed")
    }

    static func printSelfTestHelp() {
        print("""
        usage: murmurmark self-test

        Runs the quick local CLI handoff smoke fixture through the current CLI.

        The check builds a tiny processed fixture and exercises the user-facing
        command chain: process --plan-only, review handoffs, status, report,
        next/open, export and retention.
        """)
    }

    static func acceptance(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            printAcceptanceHelp()
            return
        }

        let script = PathURLs.fileURL("scripts/acceptance-cli-mvp.sh")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("acceptance script not found: \(PathDisplay.display(script))")
        }

        print("acceptance:")
        let suffix = args.isEmpty ? "" : " \(args.joined(separator: " "))"
        print("  command: \(PathDisplay.display(script))\(suffix)")
        fflush(stdout)
        try runScript(script, arguments: args, failureName: "acceptance")
    }

    static func printAcceptanceHelp() {
        print("""
        usage: murmurmark acceptance [--skip-release] [--python PATH] [--live-checklist]
                                     [--live-session SESSION|latest] [--sessions-root ./sessions]
                                     [--require-meeting-lifecycle] [--report PATH]

        Runs the CLI MVP acceptance gate.

        In a developer checkout, the gate verifies local install, doctor,
        self-test, config init, open-source readiness and release bundle
        verification. In a release bundle, it verifies the bundle with
        doctor --strict, self-test and config init.

        Use --live-checklist to print the manual live recording gate without
        recording audio.

        Use --live-session SESSION|latest after recording and processing a real
        session to verify the manual live gate from session artifacts.

        Add --require-meeting-lifecycle for the one-command meeting soak. It rejects
        sessions that were produced only through the legacy manual command chain.

        Use --report PATH to write a machine-readable acceptance report.
        """)
    }

    private static func runSelfTestScript(_ script: URL) throws {
        try runScript(script, arguments: [], failureName: "self-test")
    }

    private static func runScript(_ script: URL, arguments: [String], failureName: String) throws {
        let bash = URL(fileURLWithPath: "/bin/bash")
        guard FileManager.default.isExecutableFile(atPath: bash.path) else {
            throw CLIError("executable not found: \(bash.path)")
        }

        let process = Process()
        process.executableURL = bash
        process.arguments = [script.path] + arguments
        process.standardInput = FileHandle.nullDevice
        var environment = ProcessInfo.processInfo.environment
        environment["MURMURMARK_BIN"] = ExecutablePath.current()
        process.environment = environment
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw CLIError("\(failureName) exited with \(process.terminationStatus)")
        }
    }

    static func listApps() {
        let apps = NSWorkspace.shared.runningApplications
            .filter { $0.bundleIdentifier != nil }
            .sorted { ($0.localizedName ?? "") < ($1.localizedName ?? "") }

        for app in apps {
            print("\(app.processIdentifier)\t\(app.bundleIdentifier ?? "-")\t\(app.localizedName ?? "-")")
        }
    }

    static func listAudioDevices() {
        let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone], mediaType: .audio, position: .unspecified).devices
        print("default\tSystem Default Microphone")
        for device in devices {
            print("\(device.uniqueID)\t\(device.localizedName)")
        }
    }

    static func record(_ args: [String]) async throws {
        if ArgumentEditing.hasHelpFlag(args) {
            printRecordHelp()
            return
        }
        let invocation = try prepareRecording(args, handoffEnabled: true)
        let stopReason = try await invocation.recorder.run()
        if stopReason.isUnexpectedCaptureStop {
            let session = PathDisplay.display(invocation.outputDirectory)
            throw CLIError(
                "recording interrupted before requested end; partial session saved: \(session). " +
                    "Inspect it with `murmurmark inspect \(session)`, then re-record if this was a live meeting. " +
                    "Use `murmurmark process \(session) --allow-partial` only for debugging."
            )
        }
    }

    static func prepareRecording(_ args: [String], handoffEnabled: Bool) throws -> RecordingInvocation {
        let options = try Options(args)
        let livePipelineEnabled = options.flag("live-pipeline")
        let experimentID = options.string("experiment")
        if livePipelineEnabled && ProcessInfo.processInfo.environment["MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE"] != "1" {
            throw CLIError(
                "--live-pipeline is disabled for real recordings until the async live path passes parity gates. " +
                    "Use `murmurmark record --target-bundle system`. " +
                    "For lab-only diagnostics set MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1."
            )
        }
        if let experimentID, experimentID != "live-shadow-v1" {
            throw CLIError("unsupported record experiment: \(experimentID)")
        }
        if livePipelineEnabled && experimentID != nil {
            throw CLIError("--experiment cannot be combined with --live-pipeline")
        }
        let out = try SessionPaths.outputDirectory(from: options)
        let duration = try options.optionalPositiveDouble("duration")
        let targetBundle = options.string("target-bundle")
        let microphone = options.string("mic") ?? "default"
        let microphoneBackend = try MicrophoneCaptureBackend.parse(options.string("mic-backend") ?? "screencapturekit")
        let remoteBackend = try RemoteCaptureBackend.parse(options.string("remote-backend") ?? "screencapturekit")
        let remoteDevice = options.string("remote-device")
        let sampleRate = options.int("sample-rate") ?? 48000
        let channelCount = options.int("channels") ?? 2
        let requestedLiveSegmentSeconds = try options.optionalPositiveDouble("live-segment-sec")
        let liveSegmentSeconds = requestedLiveSegmentSeconds ?? (experimentID == nil ? 60 : 30)
        let liveOverlapSeconds = try options.optionalNonNegativeDouble("live-overlap-sec") ?? 5
        let liveWorkerEnabled = (livePipelineEnabled || experimentID != nil) && !options.flag("live-no-worker")
        let liveFinalizeEnabled = livePipelineEnabled && !options.flag("live-no-finalize")
        let liveConsoleEnabled = experimentID != nil && liveWorkerEnabled && !options.flag("live-no-console")
        if liveOverlapSeconds >= liveSegmentSeconds / 2 {
            throw CLIError("--live-overlap-sec must be less than half of --live-segment-sec")
        }
        if livePipelineEnabled && (microphoneBackend != .screenCaptureKit || remoteBackend != .screenCaptureKit) {
            throw CLIError("--live-pipeline currently requires screencapturekit mic and remote backends")
        }
        if experimentID != nil && (microphoneBackend != .screenCaptureKit || remoteBackend != .screenCaptureKit) {
            throw CLIError("--experiment live-shadow-v1 currently requires screencapturekit mic and remote backends")
        }

        let recorder = SessionRecorder(
            outputDirectory: out,
            targetBundleID: targetBundle,
            microphoneID: microphone,
            microphoneBackend: microphoneBackend,
            remoteBackend: remoteBackend,
            remoteDeviceID: remoteDevice,
            duration: duration,
            sampleRate: sampleRate,
            channelCount: channelCount,
            livePipelineEnabled: livePipelineEnabled,
            liveSegmentSeconds: liveSegmentSeconds,
            liveOverlapSeconds: liveOverlapSeconds,
            liveWorkerEnabled: liveWorkerEnabled,
            liveFinalizeEnabled: liveFinalizeEnabled,
            liveConsoleEnabled: liveConsoleEnabled,
            experimentID: experimentID,
            handoffEnabled: handoffEnabled
        )
        return RecordingInvocation(outputDirectory: out, recorder: recorder)
    }

    static func printRecordHelp() {
        print("""
        usage: murmurmark record [--out ./session] [--duration 60] [--target-bundle system|com.example.App]
                                 [--mic default] [--mic-backend screencapturekit|voice-processing]
                                 [--remote-backend screencapturekit|audio-input] [--remote-device Device_UID]
                                 [--experiment live-shadow-v1]
                                 [--live-pipeline] [--live-segment-sec 30|60] [--live-overlap-sec 5]
                                 [--live-no-worker] [--live-no-console] [--live-no-finalize]

        Records separate mic and remote CAF tracks into a session package.
        Without --duration, recording continues until Ctrl-C.

        Normal meeting path:
          murmurmark doctor --strict
          SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
          murmurmark record --out "$SESSION" --target-bundle system
          murmurmark process "$SESSION"

        The record command keeps macOS display/system idle sleep disabled while capture is active,
        because ScreenCaptureKit needs an awake desktop capture source.

        --live-pipeline is disabled by default. Use it only in lab diagnostics with
        MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 until the async live path passes parity gates.

        --experiment live-shadow-v1 keeps raw CAF authoritative and starts a best-effort sidecar
        from committed PCM after each raw write. If the sidecar fails, process the session normally.
        New live turns are shown in the recording terminal by default; use --live-no-console to
        disable that view or `murmurmark live watch SESSION` to watch from another terminal.
        The default segment size is 30s for --experiment and 60s for legacy --live-pipeline.

        `latest` is a mutable pointer to the newest session. For real meetings, especially with
        multiple terminals, set SESSION before recording and pass --out "$SESSION".
        """)
    }
}

struct RecordingInvocation {
    let outputDirectory: URL
    let recorder: SessionRecorder
}

enum MeetingCommands {
    static func meeting(_ args: [String]) async throws {
        if ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        if let resumeTarget = ArgumentEditing.takeOption("resume", from: &remaining) {
            let sessionsRoot = PathURLs.fileURL(
                ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions"
            )
            guard remaining.isEmpty else {
                throw CLIError("meeting --resume accepts only SESSION and --sessions-root")
            }
            let session = try SessionResolver.resolve(resumeTarget, sessionsRoot: sessionsRoot)
            try runSupervisor(session: session, captureElapsed: nil, resume: true)
            return
        }

        try validateRecordingArguments(args)
        let invocation = try Commands.prepareRecording(args, handoffEnabled: false)
        print("SESSION=\"\(PathDisplay.display(invocation.outputDirectory))\"")
        fflush(stdout)
        let startedAt = Date()
        let stopReason = try await invocation.recorder.run()
        let captureElapsed = Date().timeIntervalSince(startedAt)
        try runSupervisor(
            session: invocation.outputDirectory,
            captureElapsed: captureElapsed,
            resume: false
        )
        if stopReason.isUnexpectedCaptureStop {
            throw CLIError(
                "meeting capture ended unexpectedly; raw partial session was preserved and processing was blocked"
            )
        }
    }

    private static func runSupervisor(session: URL, captureElapsed: TimeInterval?, resume: Bool) throws {
        let script = PathURLs.fileURL("scripts/run-meeting-lifecycle.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("meeting lifecycle supervisor not found: \(script.path)")
        }
        var command = [
            script.path,
            session.path,
            "--murmurmark-bin", ExecutablePath.current(),
        ]
        if let captureElapsed {
            command += ["--record-elapsed-sec", String(format: "%.3f", captureElapsed)]
        }
        if resume {
            command.append("--resume")
        }
        fflush(stdout)
        let status = try Tooling.runPathForwardingInterruptsAllowingExitCodes(
            try PythonRuntime.resolve(),
            command,
            allowedExitCodes: [0, 2, 3, 130]
        )
        switch status {
        case 0:
            return
        case 130:
            throw CLIError("meeting processing interrupted; use the resume command printed above")
        case 3:
            throw CLIError("another meeting lifecycle supervisor already owns this session")
        default:
            throw CLIError("meeting lifecycle failed; inspect derived/meeting-lifecycle/report.json")
        }
    }

    private static func validateRecordingArguments(_ args: [String]) throws {
        let valueOptions: Set<String> = [
            "out", "duration", "target-bundle", "mic", "mic-backend", "remote-backend",
            "remote-device", "experiment", "live-segment-sec", "live-overlap-sec", "sample-rate",
            "channels",
        ]
        let flagOptions: Set<String> = [
            "live-no-worker", "live-no-console", "live-no-finalize",
        ]
        var index = 0
        while index < args.count {
            let argument = args[index]
            guard argument.hasPrefix("--") else {
                throw CLIError("unexpected meeting argument: \(argument)")
            }
            let name = String(argument.dropFirst(2))
            if valueOptions.contains(name) {
                guard index + 1 < args.count, !args[index + 1].hasPrefix("--") else {
                    throw CLIError("--\(name) requires a value")
                }
                index += 2
            } else if flagOptions.contains(name) {
                index += 1
            } else {
                throw CLIError(
                    "unsupported meeting option --\(name); use low-level record/process commands for diagnostics"
                )
            }
        }
    }

    private static func printHelp() {
        print("""
        usage: murmurmark meeting [--out ./session] [--duration 60] [--target-bundle system|com.example.App]
                                  [--mic default] [--mic-backend screencapturekit|voice-processing]
                                  [--remote-backend screencapturekit|audio-input] [--remote-device Device_UID]
                                  [--experiment live-shadow-v1]
               murmurmark meeting --resume ./session [--sessions-root ./sessions]

        Records a durable two-track session and, after the first Ctrl-C, runs the bounded
        authoritative lifecycle through transcript, notes, verdict, safe suggested review and
        guarded export. A second Ctrl-C checkpoints processing and prints the exact resume command.

        Common:
          murmurmark meeting --target-bundle system
          murmurmark meeting --target-bundle system --experiment live-shadow-v1
          murmurmark meeting --resume sessions/<id>

        The high-level command never adds --full, --force-asr or --allow-partial. Use record and
        process directly for low-level diagnostics.
        """)
    }
}

enum DoctorSeverity {
    case passed
    case warn
    case fail

    var label: String {
        switch self {
        case .passed:
            "ok"
        case .warn:
            "warn"
        case .fail:
            "fail"
        }
    }
}

struct DoctorReport {
    var passed = 0
    var warnings = 0
    var failures = 0

    var readiness: String {
        if failures > 0 {
            return "blocked"
        }
        if warnings > 0 {
            return "usable_with_warnings"
        }
        return "ok"
    }

    mutating func check(_ severity: DoctorSeverity, _ name: String, _ detail: String, hint: String? = nil) {
        switch severity {
        case .passed:
            passed += 1
        case .warn:
            warnings += 1
        case .fail:
            failures += 1
        }

        print("[\(severity.label)] \(name): \(detail)")
        if let hint, !hint.isEmpty {
            print("      hint: \(hint)")
        }
    }

    func printNext() {
        print("next:")
        if failures > 0 {
            print("  fix failed checks above")
            print("  murmurmark doctor --strict")
            return
        }
        if warnings > 0 {
            print("  optional: resolve warnings above")
        }
        print("  murmurmark self-test")
        print("  murmurmark config init")
        print("  murmurmark acceptance --skip-release")
        print("  SESSION=\"sessions/$(date +%Y-%m-%d_%H-%M-%S)\"")
        print("  murmurmark record --out \"$SESSION\" --target-bundle system")
        print("  murmurmark inspect \"$SESSION\"")
        print("  murmurmark process \"$SESSION\"")
        print("  murmurmark status \"$SESSION\"")
        print("  murmurmark acceptance --live-session \"$SESSION\" --report /tmp/murmurmark-live-session.json")
    }
}

enum DoctorChecks {
    static let defaultModel = "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
    static let requiredPythonModules = ["numpy", "scipy", "soundfile", "librosa", "sklearn"]

    static func printHelp() {
        print("""
        usage: murmurmark doctor [--strict]

        Checks local readiness for recording and the current CLI pipeline:
          macOS permissions, ffmpeg/ffprobe, whisper.cpp, Python runtime,
          required Python modules, local model, config and core scripts.

        By default doctor reports failures but exits 0.
        With --strict it exits non-zero when required checks fail.
        """)
    }

    static func checkExecutable(_ name: String, required: Bool, report: inout DoctorReport) {
        if let path = Tooling.which(name) {
            report.check(.passed, name, path)
        } else if required {
            report.check(.fail, name, "not found in PATH", hint: installHint(for: name))
        } else {
            report.check(.warn, name, "not found in PATH", hint: installHint(for: name))
        }
    }

    static func checkLocalConfig(_ report: inout DoctorReport) {
        do {
            let config = try MurmurMarkConfig.load(from: nil)
            if let url = config.url {
                report.check(.passed, "config", PathDisplay.display(url))
            } else {
                report.check(
                    .warn,
                    "config",
                    "murmurmark.config.json not found",
                    hint: "run murmurmark config init when you want local defaults"
                )
            }
        } catch {
            report.check(.fail, "config", error.localizedDescription, hint: "fix or remove murmurmark.config.json")
        }
    }

    static func checkScripts(_ report: inout DoctorReport) {
        for path in [
            "scripts/run-session-pipeline.py",
            "scripts/run-meeting-lifecycle.py",
            "scripts/evaluate-outcome.py",
            "scripts/live-pipeline-shadow.py",
            "scripts/watch-live-draft.py",
            "scripts/report-live-session-evidence.py",
            "scripts/report-live-recovery-real-evidence.py",
            "scripts/live-progressive-target-me.py",
            "scripts/raw-sidecar-worker.py",
            "scripts/compare-live-batch.py",
            "scripts/report-live-replay-lab.py",
            "scripts/materialize-live-asr-cache.py",
            "scripts/report-live-corpus-gates.py",
            "scripts/report-live-local-recall-hardening.py",
            "scripts/report-live-boundary-island-micro-asr-lab.py",
            "scripts/live-causal-me-recovery-manager.py",
            "scripts/live-causal-me-recovery-runtime.py",
            "scripts/replay-live-causal-me-recovery-runtime.py",
            "scripts/report-recording-time-causal-me-recovery-runtime-v1.py",
            "scripts/run-live-parity-pilot.sh",
            "scripts/experiment-sidecar-contract.py",
            "scripts/report-asr-chunk-cache-corpus.py",
            "scripts/transcribe-simple-whispercpp.py",
            "scripts/check-asr-chunk-cache.py",
            "scripts/check-capture-regressions.sh",
            "scripts/synthesize-simple-extractive.py",
            "scripts/audit-local-recall.py",
            "scripts/audit-transcript-order.py",
            "scripts/audit-group-overlaps.py",
            "scripts/build-audio-review-pack.py",
            "scripts/audit-audio-review-pack.py",
            "scripts/audit-stronger-audio-judge.py",
            "scripts/audit-target-me.py",
            "scripts/authoritative-boundary.py",
            "scripts/mixed-utterance-span-separation.py",
            "scripts/run-asr-positive-echo-candidate.py",
            "scripts/report-asr-positive-echo-candidate-corpus.py",
            "scripts/probe-review-lane-pack-audio.py",
            "scripts/report-session-quality.py",
            "scripts/apply-retention-policy.py",
            "scripts/build-provider-payload-manifest.py",
            "scripts/smoke-cli-handoff.sh",
            "scripts/smoke-experimental-sidecar-contract.sh",
            "scripts/smoke-committed-pcm-sidecar.sh",
            "scripts/smoke-process-chunk-resume.sh",
        ] {
            let url = PathURLs.fileURL(path)
            if FileManager.default.fileExists(atPath: url.path) {
                report.check(.passed, path, "found")
            } else {
                report.check(.fail, path, "missing", hint: "run doctor from the repository or install with scripts/install-local.sh")
            }
        }
    }

    static func checkPython(_ report: inout DoctorReport) {
        do {
            let python = try PythonRuntime.resolve()
            let version = (try? Tooling.runPathCapturing(python, ["--version"]).trimmedSingleLine()) ?? "version unknown"
            report.check(.passed, "python", "\(python.path) (\(version))")

            let moduleCode = """
            import importlib.util
            mods = \(pythonList(requiredPythonModules))
            missing = [m for m in mods if importlib.util.find_spec(m) is None]
            print(",".join(missing))
            """
            let missing = (try Tooling.runPathCapturing(python, ["-c", moduleCode])).trimmedSingleLine()
            if missing.isEmpty {
                report.check(.passed, "python modules", requiredPythonModules.joined(separator: ", "))
            } else {
                report.check(.fail, "python modules", "missing: \(missing)", hint: "install project Python dependencies into .venv")
            }
        } catch {
            report.check(.fail, "python", error.localizedDescription, hint: "create .venv or set MURMURMARK_PYTHON")
        }
    }

    static func checkWhisperModel(_ report: inout DoctorReport) {
        let model = configuredModelPath()
        let url = PathURLs.fileURL(model)
        if FileManager.default.fileExists(atPath: url.path) {
            report.check(.passed, "whisper model", PathDisplay.display(url))
        } else {
            report.check(
                .fail,
                "whisper model",
                "not found: \(url.path)",
                hint: "download a multilingual whisper.cpp model or set transcription.model in murmurmark.config.json"
            )
        }
    }

    static func checkStrongerAudioJudge(_ report: inout DoctorReport) {
        do {
            let python = try PythonRuntime.resolve()
            let moduleCode = """
            import importlib.util
            print("ok" if importlib.util.find_spec("faster_whisper") else "missing")
            """
            let moduleStatus = (try Tooling.runPathCapturing(python, ["-c", moduleCode])).trimmedSingleLine()
            if moduleStatus == "ok" {
                report.check(.passed, "faster-whisper", "python module found")
            } else {
                report.check(
                    .warn,
                    "faster-whisper",
                    "python module not found",
                    hint: "install optional stronger audio judge dependencies: .venv/bin/pip install faster-whisper ctranslate2"
                )
            }
        } catch {
            report.check(.warn, "faster-whisper", "not checked: \(error.localizedDescription)")
        }

        let model = ProcessInfo.processInfo.environment["MURMURMARK_FASTER_WHISPER_MODEL"]
            ?? "~/.local/share/murmurmark/models/faster-whisper/large-v3"
        let url = PathURLs.fileURL(model)
        let modelBin = url.appendingPathComponent("model.bin")
        let exists = FileManager.default.fileExists(atPath: modelBin.path)
            || (url.lastPathComponent == "model.bin" && FileManager.default.fileExists(atPath: url.path))
        if exists {
            report.check(.passed, "faster-whisper model", PathDisplay.display(url))
        } else {
            report.check(
                .warn,
                "faster-whisper model",
                "not found: \(url.path)",
                hint: "download Systran/faster-whisper-large-v3 into ~/.local/share/murmurmark/models/faster-whisper/large-v3"
            )
        }
    }

    static func checkTargetMeSpeakerBackend(_ report: inout DoctorReport) {
        do {
            let python = try PythonRuntime.resolve()
            let moduleCode = """
            import importlib.util
            print("ok" if importlib.util.find_spec("resemblyzer") else "missing")
            """
            let moduleStatus = (try Tooling.runPathCapturing(python, ["-c", moduleCode])).trimmedSingleLine()
            if moduleStatus == "ok" {
                report.check(.passed, "resemblyzer", "python module found")
            } else {
                report.check(
                    .warn,
                    "resemblyzer",
                    "python module not found",
                    hint: "install optional Target-Me speaker backend: .venv/bin/pip install resemblyzer"
                )
            }
        } catch {
            report.check(.warn, "resemblyzer", "not checked: \(error.localizedDescription)")
        }
    }

    private static func configuredModelPath() -> String {
        guard let config = try? MurmurMarkConfig.load(from: nil),
              let value = config.section("transcription")["model"] as? String,
              !value.isEmpty
        else {
            return defaultModel
        }
        return value
    }

    private static func pythonList(_ values: [String]) -> String {
        "[" + values.map { "'\($0)'" }.joined(separator: ",") + "]"
    }

    private static func installHint(for executable: String) -> String {
        switch executable {
        case "ffmpeg", "ffprobe":
            "brew install ffmpeg"
        case "whisper-cli":
            "brew install whisper-cpp"
        case "jq":
            "brew install jq"
        case "swiftlint":
            "brew install swiftlint"
        default:
            "install \(executable) and make sure it is in PATH"
        }
    }
}

enum PipelineCommands {
    private struct CorpusReadinessOutputs {
        let sessionQualityOut: URL
        let operationalReadinessOut: URL
        let reviewPlanOut: URL
    }

    static func sessions(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printSessions()
            return
        }
        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let pathOnly = ArgumentEditing.takeFlag("path-only", from: &remaining)
        let nextOnly = ArgumentEditing.takeFlag("next-only", from: &remaining)
        let json = ArgumentEditing.takeFlag("json", from: &remaining)
        let all = ArgumentEditing.takeFlag("all", from: &remaining)
        let statusFilter = ArgumentEditing.takeOption("status", from: &remaining)
        let limitText = ArgumentEditing.takeOption("limit", from: &remaining)
        let limit = try parsePositiveLimit(limitText, defaultValue: 10)
        guard remaining.isEmpty else {
            throw CLIError("unexpected sessions arguments: \(remaining.joined(separator: " "))")
        }
        if pathOnly && nextOnly {
            throw CLIError("sessions accepts either --path-only or --next-only, not both")
        }
        if json && (pathOnly || nextOnly) {
            throw CLIError("sessions --json cannot be combined with --path-only or --next-only")
        }

        let allSessions = try SessionResolver.all(in: sessionsRoot)
        let filtered = statusFilter.map { expected in
            allSessions.filter { SessionListPrinter.status(for: $0) == expected }
        } ?? allSessions
        let selected = all ? filtered : Array(filtered.prefix(limit))
        if pathOnly {
            for session in selected {
                print(PathDisplay.display(session))
            }
            return
        }
        if nextOnly {
            for session in selected {
                print(SessionListPrinter.nextCommand(for: session))
            }
            return
        }
        if json {
            try SessionListPrinter.printJSON(sessions: filtered, shown: selected, root: sessionsRoot, statusFilter: statusFilter)
            return
        }
        SessionListPrinter.print(sessions: filtered, shown: selected, root: sessionsRoot, limit: all ? nil : limit, statusFilter: statusFilter)
    }

    static func latest(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printLatest()
            return
        }
        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        guard remaining.isEmpty else {
            throw CLIError("latest only supports --sessions-root")
        }
        let session = try SessionResolver.latest(in: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
    }

    static func process(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printProcess()
            return
        }
        guard let target = args.first else {
            PipelineHelp.printProcess()
            return
        }
        var forwarded = Array(args.dropFirst())
        let config = try MurmurMarkConfig.load(from: ArgumentEditing.takeOption("config", from: &forwarded))
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &forwarded) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/run-session-pipeline.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("pipeline runner not found: \(script.path)")
        }

        var command = [script.path, session.path]
        command += config.processDefaults(unless: forwarded)
        if !ArgumentEditing.hasOption("murmurmark-bin", in: forwarded) {
            command += ["--murmurmark-bin", ExecutablePath.current()]
        }
        command += forwarded

        print("SESSION=\"\(PathDisplay.display(session))\"")
        if ArgumentEditing.hasOption("full", in: forwarded) {
            print("note: --full waits for optional heavy enrichment after the authoritative transcript is ready")
            print("      use plain `murmurmark process SESSION` for the fastest final transcript handoff")
        }
        fflush(stdout)
        try Tooling.runPathForwardingInterrupts(python, command)
        let planOnly = ArgumentEditing.hasOption("plan-only", in: forwarded)
        try ReadinessPrinter.printSession(session, label: planOnly ? "existing_readiness" : "readiness")
        if planOnly {
            let report = PathURLs.fileURL(
                ArgumentEditing.peekOption("report", in: forwarded)
                    ?? session.appendingPathComponent("derived/pipeline-run/pipeline_run_report.json").path
            )
            let payload = try? JSONFiles.object(report)
            let next = payload?["recommended_next"] as? String ?? "murmurmark process \(PathDisplay.display(session))"
            print("")
            print("next: \(next)")
        } else {
            try ReadinessPrinter.printFinalNext(session)
        }
    }

    static func enrich(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printEnrich()
            return
        }
        guard let target = args.first else {
            PipelineHelp.printEnrich()
            return
        }
        var forwarded = Array(args.dropFirst())
        let config = try MurmurMarkConfig.load(from: ArgumentEditing.takeOption("config", from: &forwarded))
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &forwarded) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/run-session-pipeline.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("pipeline runner not found: \(script.path)")
        }

        var command = [script.path, session.path, "--phase", "deferred"]
        command += config.processDefaults(unless: forwarded)
        if !ArgumentEditing.hasOption("murmurmark-bin", in: forwarded) {
            command += ["--murmurmark-bin", ExecutablePath.current()]
        }
        command += forwarded

        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)
        try Tooling.runPathForwardingInterrupts(python, command)
        try ReadinessPrinter.printSession(session, label: "readiness")
        try ReadinessPrinter.printFinalNext(session)
    }

    static func status(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printStatus()
            return
        }
        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let target = remaining.isEmpty ? "latest" : remaining.removeFirst()
        guard remaining.isEmpty else {
            throw CLIError("unexpected status arguments: \(remaining.joined(separator: " "))")
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        try ReadinessPrinter.printSession(session)
        try ReadinessPrinter.printFinalNext(session)
    }

    static func outcome(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printOutcome()
            return
        }
        var remaining = args
        let refresh = ArgumentEditing.takeFlag("refresh", from: &remaining)
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let target = remaining.isEmpty ? "latest" : remaining.removeFirst()
        guard remaining.isEmpty else {
            throw CLIError("unexpected outcome arguments: \(remaining.joined(separator: " "))")
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        if refresh {
            try refreshReadiness(session)
        } else if !FileManager.default.fileExists(atPath: session.appendingPathComponent("derived/outcome/outcome.json").path) {
            try refreshOutcome(session)
        }
        print("SESSION=\"\(PathDisplay.display(session))\"")
        try ReadinessPrinter.printOutcome(session)
    }

    static func next(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printNext()
            return
        }
        var remaining = args
        let refresh = ArgumentEditing.takeFlag("refresh", from: &remaining)
        let exportManifest = ArgumentEditing.takeOption("export-manifest", from: &remaining).map(PathURLs.fileURL)
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let target = remaining.isEmpty ? "latest" : remaining.removeFirst()
        guard remaining.isEmpty else {
            throw CLIError("unexpected next arguments: \(remaining.joined(separator: " "))")
        }
        if target == "corpus" {
            guard exportManifest == nil else {
                throw CLIError("next corpus does not accept --export-manifest")
            }
            let outDir: URL
            if refresh {
                let outputs = try refreshCorpusReadiness(sessionsRoot: sessionsRoot)
                outDir = outputs.operationalReadinessOut
                try refreshCorpusFirstLanePack(
                    operationalReadiness: outputs.operationalReadinessOut.appendingPathComponent("operational_readiness_report.json"),
                    sessionsRoot: sessionsRoot
                )
            } else {
                outDir = sessionsRoot
                    .appendingPathComponent("_reports")
                    .appendingPathComponent("operational-readiness")
            }
            print("CORPUS=\"\(PathDisplay.display(sessionsRoot))\"")
            try CorpusPrinter.printOperationalNext(outDir: outDir)
            return
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        if refresh {
            try refreshReadiness(session)
        }
        print("SESSION=\"\(PathDisplay.display(session))\"")
        try ReadinessPrinter.printNext(session, exportManifest: exportManifest)
    }

    static func report(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            PipelineHelp.printReport()
            return
        }
        guard let target = args.first else {
            PipelineHelp.printReport()
            return
        }
        var remaining = Array(args.dropFirst())
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")

        if target == "corpus" {
            guard remaining.isEmpty else { throw CLIError("report corpus only supports --sessions-root") }
            let outputs = try refreshCorpusReadiness(sessionsRoot: sessionsRoot)
            try ReadinessPrinter.printCorpus(report: outputs.sessionQualityOut.appendingPathComponent("session_quality_report.json"))
            try CorpusPrinter.printOperationalReadiness(outDir: outputs.operationalReadinessOut)
            return
        }

        guard remaining.isEmpty else { throw CLIError("unexpected report arguments: \(remaining.joined(separator: " "))") }
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/report-session-quality.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("session quality reporter not found: \(script.path)")
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        try Tooling.runPathQuiet(python, [
            script.path,
            session.path,
            "--out-dir", session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
        try refreshOutcome(session)
        try ReadinessPrinter.printSession(session)
        try ReadinessPrinter.printFinalNext(session)
    }

    private static func refreshReadiness(_ session: URL) throws {
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/report-session-quality.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("session quality reporter not found: \(script.path)")
        }
        try Tooling.runPathQuiet(python, [
            script.path,
            session.path,
            "--out-dir", session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
        try refreshOutcome(session)
    }

    private static func refreshOutcome(_ session: URL) throws {
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/evaluate-outcome.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("outcome evaluator not found: \(script.path)")
        }
        try Tooling.runPathQuiet(python, [
            script.path,
            session.path,
        ])
    }

    private static func refreshCorpusReadiness(sessionsRoot: URL) throws -> CorpusReadinessOutputs {
        let python = try PythonRuntime.resolve()
        let sessionQualityScript = PathURLs.fileURL("scripts/report-session-quality.py")
        let operationalReadinessScript = PathURLs.fileURL("scripts/report-operational-readiness.py")
        let reviewPlanScript = PathURLs.fileURL("scripts/build-review-plan.py")
        guard FileManager.default.fileExists(atPath: sessionQualityScript.path) else {
            throw CLIError("session quality reporter not found: \(sessionQualityScript.path)")
        }
        guard FileManager.default.fileExists(atPath: operationalReadinessScript.path) else {
            throw CLIError("operational readiness reporter not found: \(operationalReadinessScript.path)")
        }
        guard FileManager.default.fileExists(atPath: reviewPlanScript.path) else {
            throw CLIError("review plan builder not found: \(reviewPlanScript.path)")
        }

        let sessions = try SessionResolver.all(in: sessionsRoot)
        guard !sessions.isEmpty else {
            throw CLIError("no sessions with session.json found under \(sessionsRoot.path)")
        }
        let reportsRoot = sessionsRoot.appendingPathComponent("_reports")
        let sessionQualityOut = reportsRoot.appendingPathComponent("session-quality")
        let operationalReadinessOut = reportsRoot.appendingPathComponent("operational-readiness")
        let reviewPlanOut = reportsRoot.appendingPathComponent("review-plan")
        let sessionQualityCommand = [sessionQualityScript.path] + sessions.map(\.path) + [
            "--out-dir", sessionQualityOut.path,
            "--write-session-readiness",
        ]
        try Tooling.runPathQuiet(python, sessionQualityCommand)
        let outcomeScript = PathURLs.fileURL("scripts/evaluate-outcome.py")
        if FileManager.default.fileExists(atPath: outcomeScript.path) {
            for session in sessions {
                try Tooling.runPathQuiet(python, [
                    outcomeScript.path,
                    session.path,
                ])
            }
        }
        try Tooling.runPathQuiet(python, [
            operationalReadinessScript.path,
            "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
            "--corpus-evaluation", reportsRoot.appendingPathComponent("regression-corpus/regression_corpus_evaluation.json").path,
            "--audio-judge", reportsRoot.appendingPathComponent("audio-judge-v0/audio_judge_v0_report.json").path,
            "--audio-judge-queue", reportsRoot.appendingPathComponent("audio-judge-v0/audio_judge_v0_queue_predictions.jsonl").path,
            "--out-dir", operationalReadinessOut.path,
        ])
        try Tooling.runPathQuiet(python, [
            reviewPlanScript.path,
            "--operational-readiness", operationalReadinessOut.appendingPathComponent("operational_readiness_report.json").path,
            "--out-dir", reviewPlanOut.path,
        ])
        return CorpusReadinessOutputs(
            sessionQualityOut: sessionQualityOut,
            operationalReadinessOut: operationalReadinessOut,
            reviewPlanOut: reviewPlanOut
        )
    }

    private static func refreshCorpusFirstLanePack(operationalReadiness: URL, sessionsRoot: URL) throws {
        guard FileManager.default.fileExists(atPath: operationalReadiness.path),
              let payload = try? JSONFiles.object(operationalReadiness),
              let focus = corpusReviewFocus(payload)
        else {
            return
        }
        let rawSessionID = (focus["session_id"] as? String)
            ?? (focus["session_arg"] as? String)
            ?? (focus["session"] as? String)
            ?? ""
        guard !rawSessionID.isEmpty else { return }
        let lane = (focus["review_lane"] as? String)
            ?? (((payload["promotion_plan"] as? [String: Any])?["review_focus"] as? [String: Any])?["review_lane"] as? String)
            ?? "fast_confirm_drop"
        let session = corpusFocusSessionURL(rawSessionID, sessionsRoot: sessionsRoot)
        guard FileManager.default.fileExists(atPath: session.appendingPathComponent("session.json").path) else {
            return
        }

        let python = try PythonRuntime.resolve()
        let sessionQualityScript = try requiredScript("report-session-quality.py")
        let operationalReadinessScript = try requiredScript("report-operational-readiness.py")
        let reviewPlanScript = try requiredScript("build-review-plan.py")
        let lanePackScript = try requiredScript("build-review-lane-pack.py")
        let readinessRoot = session.appendingPathComponent("derived/readiness")
        let sessionQualityOut = readinessRoot.appendingPathComponent("session-quality")
        let operationalOut = readinessRoot.appendingPathComponent("operational-readiness")
        let planOut = readinessRoot.appendingPathComponent("review-plan")
        let lanePackOut = planOut.appendingPathComponent("lane-packs")

        try Tooling.runPathQuiet(python, [
            sessionQualityScript.path,
            session.path,
            "--out-dir", sessionQualityOut.path,
            "--write-session-readiness",
        ])
        try Tooling.runPathQuiet(python, [
            operationalReadinessScript.path,
            "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
            "--out-dir", operationalOut.path,
        ])
        try Tooling.runPathQuiet(python, [
            reviewPlanScript.path,
            "--operational-readiness", operationalOut.appendingPathComponent("operational_readiness_report.json").path,
            "--out-dir", planOut.path,
        ])
        try Tooling.runPathQuiet(python, [
            lanePackScript.path,
            "--template", planOut.appendingPathComponent("review_decisions.template.jsonl").path,
            "--decisions", planOut.appendingPathComponent("review_decisions.jsonl").path,
            "--lane", lane,
            "--out-dir", lanePackOut.path,
            "--include-related-lanes",
            "--session", session.lastPathComponent,
        ])
    }

    private static func corpusReviewFocus(_ payload: [String: Any]) -> [String: Any]? {
        let plan = payload["promotion_plan"] as? [String: Any] ?? [:]
        if let focus = plan["review_focus"] as? [String: Any],
           focus["session_id"] is String || focus["session_arg"] is String {
            return focus
        }
        if let queue = payload["review_queue"] as? [[String: Any]], let first = queue.first {
            return first
        }
        return nil
    }

    private static func corpusFocusSessionURL(_ rawSessionID: String, sessionsRoot: URL) -> URL {
        let url = PathURLs.fileURL(rawSessionID)
        if rawSessionID.contains("/") || rawSessionID.hasPrefix(".") || rawSessionID.hasPrefix("/") {
            return url
        }
        return sessionsRoot.appendingPathComponent(rawSessionID)
    }

    private static func requiredScript(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("script not found: \(url.path)")
        }
        return url
    }

    private static func parsePositiveLimit(_ value: String?, defaultValue: Int) throws -> Int {
        guard let value else { return defaultValue }
        guard let parsed = Int(value), parsed > 0 else {
            throw CLIError("--limit must be a positive integer")
        }
        return parsed
    }
}

enum PipelineHelp {
    static func printSessions() {
        Swift.print("""
        usage: murmurmark sessions [--limit 10] [--all]
                                  [--status exported|exportable|review_required|incomplete|blocked|partial_capture|missing_readiness]
                                  [--path-only|--next-only|--json] [--sessions-root ./sessions]

        Lists recent session packages and their current readiness state.
        Use --path-only when another command or script only needs session paths.
        Use --next-only to print only the next safe command for matching sessions.
        Use --json when an agent needs a machine-readable queue snapshot.

        Common:
          murmurmark sessions
          murmurmark sessions --limit 5
          murmurmark sessions --status review_required
          murmurmark sessions --status review_required --next-only
          murmurmark sessions --status exported --next-only
          murmurmark sessions --status exportable --json
          murmurmark sessions --path-only --limit 3
        """)
    }

    static func printLatest() {
        Swift.print("""
        usage: murmurmark latest [--sessions-root ./sessions]

        Prints the newest session as:
          SESSION="./sessions/<id>"

        The command ignores internal report directories whose names start with `_`.
        `latest` is convenient for recovery, but it is not stable if another terminal starts a
        newer recording. Prefer `record --out "$SESSION"` for real meetings.
        """)
    }

    static func printProcess() {
        Swift.print("""
        usage: murmurmark process ./session|latest [--model ./model.bin] [--language ru] [--prompt-file ./prompt.txt]
                                [--full]
                                [--force-asr] [--reuse-asr-cache] [--plan-only] [--skip-build]
                                [--asr-track-workers 1|2] [--asr-threads N] [--micro-asr-workers 1|2|4]
                                [--skip-preprocess] [--skip-transcription] [--skip-audits] [--skip-cleanup]
                                [--skip-stronger-audio-judge] [--stronger-audio-judge-exhaustive]
                                [--progress-interval-sec 60] [--allow-partial]
                                [--config murmurmark.config.json] [--sessions-root ./sessions]

        Produces the authoritative transcript/verdict handoff and then returns. Deferred local
        audio judges and live-vs-batch diagnostics are intentionally left for `murmurmark enrich`.
        Use --full to run both phases in one foreground command.
        Defaults come from murmurmark.config.json when present; explicit CLI flags win.
        The --skip-* flags are for debugging or refreshing only selected derived layers.
        The normal stronger-audio-judge pass audits the residual queue with mic_clean+remote.
        Use --stronger-audio-judge-exhaustive only for deliberate four-source diagnostics.
        Interrupted partial captures are blocked by default; use --allow-partial only for debugging.

        Common:
          murmurmark process ./sessions/<id> --plan-only --skip-build
          murmurmark process ./sessions/<id> --progress-interval-sec 30
          murmurmark process latest  # only when no newer session can appear
        """)
    }

    static func printEnrich() {
        Swift.print("""
        usage: murmurmark enrich ./session|latest [--skip-stronger-audio-judge]
                                [--stronger-audio-judge-exhaustive]
                                [--progress-interval-sec 60] [--deferred-step-timeout-sec 3600]
                                [--config murmurmark.config.json] [--sessions-root ./sessions]

        Idempotently runs deferred enrichment after a valid authoritative handoff. Enrichment may
        add review evidence and candidate profiles, but it cannot replace the published transcript.
        """)
    }

    static func printStatus() {
        Swift.print("""
        usage: murmurmark status [./session|latest] [--sessions-root ./sessions]

        Prints the current session readiness dashboard without recomputing reports.
        Defaults to latest when no session is provided.

        Common:
          murmurmark status
          murmurmark status ./sessions/<id>
          murmurmark status latest  # only when no newer session can appear
        """)
    }

    static func printOutcome() {
        Swift.print("""
        usage: murmurmark outcome [./session|latest] [--refresh] [--sessions-root ./sessions]

        Prints the stable user-facing outcome contract for a processed session.
        Use --refresh to recompute session readiness and outcome without rerunning ASR or audio processing.

        Common:
          murmurmark outcome latest
          murmurmark outcome ./sessions/<id> --refresh
        """)
    }

    static func printNext() {
        Swift.print("""
        usage: murmurmark next [./session|latest|corpus] [--refresh] [--export-manifest ./export_manifest.json] [--sessions-root ./sessions]

        Prints the single recommended next command from session_readiness.json or partial-capture health.
        Defaults to latest when no session is provided. Pass corpus to print the current
        operational-readiness handoff for all sessions under --sessions-root.
        Use --refresh to update readiness first without rerunning ASR, Echo Guard or audits.
        For corpus, --refresh updates session-quality, operational-readiness and the first
        recommended review lane pack.
        If a successful export manifest exists for a session, the command follows its post-export
        handoff, usually retention planning.

        Common:
          murmurmark next
          murmurmark next corpus
          murmurmark next corpus --refresh
          murmurmark next ./sessions/<id> --export-manifest ./exports/private/<id>/export_manifest.json
          murmurmark next ./sessions/<id> --refresh
          murmurmark next latest  # only when no newer session can appear
        """)
    }

    static func printReport() {
        Swift.print("""
        usage: murmurmark report ./session|latest [--sessions-root ./sessions]
               murmurmark report corpus [--sessions-root ./sessions]

        Refreshes session-quality/readiness reports without rerunning ASR, Echo Guard or audits.
        Use `murmurmark status` when you only need to inspect already generated readiness.
        Use `report corpus` for a summary over all sessions under --sessions-root.

        Common:
          murmurmark report latest
          murmurmark report ./sessions/<id>
          murmurmark report corpus
        """)
    }
}

enum SessionListPrinter {
    static func printJSON(sessions: [URL], shown: [URL], root: URL, statusFilter: String?) throws {
        let payload: [String: Any] = [
            "schema": "murmurmark.sessions_queue/v1",
            "root": PathDisplay.display(root),
            "status_filter": statusFilter.map { $0 as Any } ?? NSNull(),
            "count": sessions.count,
            "shown": shown.count,
            "latest": sessions.first.map { PathDisplay.display($0) as Any } ?? NSNull(),
            "items": shown.map(itemPayload),
        ]
        guard JSONSerialization.isValidJSONObject(payload) else {
            throw CLIError("cannot render sessions queue JSON")
        }
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys])
        FileHandle.standardOutput.write(data)
        Swift.print("")
    }

    static func print(sessions: [URL], shown: [URL], root: URL, limit: Int?, statusFilter: String?) {
        Swift.print("")
        Swift.print("sessions:")
        Swift.print("  root: \(PathDisplay.display(root))")
        if let statusFilter {
            Swift.print("  status_filter: \(statusFilter)")
        }
        Swift.print("  count: \(sessions.count)")
        if let latest = sessions.first {
            Swift.print("  latest: \(PathDisplay.display(latest))")
        }
        if limit != nil, sessions.count > shown.count {
            Swift.print("  shown: \(shown.count)")
            Swift.print("  more: \(sessions.count - shown.count)")
            Swift.print("  hint: murmurmark sessions --all")
        } else {
            Swift.print("  shown: \(shown.count)")
        }
        Swift.print("  items:")
        if shown.isEmpty {
            Swift.print("    []")
            return
        }
        for session in shown {
            printItem(session)
        }
    }

    private static func printItem(_ session: URL) {
        let item = itemPayload(session)
        Swift.print("    - session: \(string(item["session"]) ?? PathDisplay.display(session))")
        Swift.print("      label: \(string(item["label"]) ?? session.lastPathComponent)")
        if let createdAt = string(item["created_at"]) {
            Swift.print("      created_at: \(createdAt)")
        }
        if let durationSec = double(item["duration_sec"]) {
            Swift.print("      duration: \(formatMinutes(durationSec))")
        }
        Swift.print("      status: \(string(item["status"]) ?? "unknown")")
        Swift.print("      gate: \(string(item["gate"]) ?? "missing")")
        Swift.print("      profile: \(string(item["profile"]) ?? "unknown")")
        Swift.print("      verdict: \(string(item["verdict"]) ?? "unknown")")
        if let reviewSec = double(item["review_burden_sec"]) {
            let ratio = double(item["review_burden_ratio"]) ?? 0
            Swift.print("      notes_review_burden: \(formatMinutes(reviewSec)) / \(formatPercent(ratio))")
        }
        if let transcriptReviewSec = double(item["transcript_review_burden_sec"]),
           let reviewSec = double(item["review_burden_sec"]),
           abs(transcriptReviewSec - reviewSec) > 0.05 {
            let ratio = double(item["transcript_review_burden_ratio"]) ?? 0
            Swift.print("      transcript_review_burden: \(formatMinutes(transcriptReviewSec)) / \(formatPercent(ratio))")
        }
        if let exportManifest = string(item["export_manifest"]) {
            Swift.print("      export_manifest: \(exportManifest)")
        }
        Swift.print("      next: \(string(item["next"]) ?? "murmurmark status \(PathDisplay.display(session))")")
    }

    private static func itemPayload(_ session: URL) -> [String: Any] {
        let readiness = readReadiness(session)
        let sessionJSON = readSessionJSON(session)
        let blocked = readPipelineBlocked(session)
        let metrics = blocked == nil ? readiness?["metrics"] as? [String: Any] ?? [:] : [:]
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        let createdAt = string(sessionJSON?["created_at"]) ?? string(readiness?["created_at"])
        let endedAt = string(sessionJSON?["ended_at"]) ?? string(readiness?["ended_at"])
        let exportHandoff = blocked == nil ? successfulExportHandoff(session: session, readiness: readiness) : nil
        let gate = blocked.flatMap { string($0["blocker"]) } ?? string(readiness?["use_gate"]) ?? "missing"
        let durationSec = double(metrics["meeting_duration_sec"]) ?? sessionDurationSec(sessionJSON)
        return [
            "session": PathDisplay.display(session),
            "session_id": session.lastPathComponent,
            "label": string(readiness?["label"]) ?? string(sessionJSON?["label"]) ?? session.lastPathComponent,
            "created_at": createdAt as Any? ?? NSNull(),
            "ended_at": endedAt as Any? ?? NSNull(),
            "duration_sec": durationSec as Any? ?? NSNull(),
            "review_burden_sec": double(metrics["review_burden_sec"]) as Any? ?? NSNull(),
            "review_burden_ratio": double(metrics["review_burden_ratio"]) as Any? ?? NSNull(),
            "transcript_review_burden_sec": double(metrics["transcript_review_burden_sec"]) as Any? ?? NSNull(),
            "transcript_review_burden_ratio": double(metrics["transcript_review_burden_ratio"]) as Any? ?? NSNull(),
            "readiness_exists": readiness != nil,
            "readiness_path": PathDisplay.display(readinessURL),
            "status": status(for: session, readiness: readiness),
            "gate": gate,
            "profile": blocked == nil ? string(readiness?["selected_profile"]) ?? "unknown" : "unknown",
            "verdict": blocked == nil ? string(readiness?["verdict"]) ?? "unknown" : "capture_failed",
            "export_manifest": exportHandoff.map { PathDisplay.display($0.manifest) as Any } ?? NSNull(),
            "next": nextCommand(for: session, readiness: readiness),
        ]
    }

    static func status(for session: URL) -> String {
        status(for: session, readiness: readReadiness(session))
    }

    static func nextCommand(for session: URL) -> String {
        nextCommand(for: session, readiness: readReadiness(session))
    }

    private static func readReadiness(_ session: URL) -> [String: Any]? {
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        return try? JSONFiles.object(url)
    }

    private static func readSessionJSON(_ session: URL) -> [String: Any]? {
        let url = session.appendingPathComponent("session.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        return try? JSONFiles.object(url)
    }

    private static func readPipelineBlocked(_ session: URL) -> [String: Any]? {
        let url = session.appendingPathComponent("derived/pipeline-run/pipeline_run_report.json")
        guard FileManager.default.fileExists(atPath: url.path),
              let payload = try? JSONFiles.object(url),
              string(payload["status"]) == "blocked"
        else {
            return nil
        }
        let blocker = string(payload["blocker"]) ?? ""
        guard blocker == "silent_capture" || blocker == "interrupted_capture" || blocker == "sparse_capture" else {
            return nil
        }
        return payload
    }

    private static func sessionDurationSec(_ sessionJSON: [String: Any]?) -> Double? {
        guard let health = sessionJSON?["health"] as? [String: Any] else { return nil }
        return double(health["actual_duration_sec"])
    }

    private static func status(for session: URL, readiness: [String: Any]?) -> String {
        if readPipelineBlocked(session) != nil {
            return "blocked"
        }
        if CaptureHealthState.partialInfo(session: session) != nil {
            return "partial_capture"
        }
        guard let readiness else { return "missing_readiness" }
        let gate = string(readiness["use_gate"]) ?? "unknown"
        let exportBlockers = (readiness["export_blockers"] as? [Any] ?? []).map { String(describing: $0) }
        let reviewBlockers = (readiness["review_blockers"] as? [Any] ?? []).map { String(describing: $0) }
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains("pipeline_incomplete") {
            return "incomplete"
        }
        if gate == "ready_for_notes" && exportBlockers.isEmpty {
            if successfulExportHandoff(session: session, readiness: readiness) != nil {
                return "exported"
            }
            return "exportable"
        }
        if gate == "review_first" || !reviewBlockers.isEmpty {
            return "review_required"
        }
        if gate == "do_not_use_without_manual_review" || !exportBlockers.isEmpty {
            return "blocked"
        }
        return "check_required"
    }

    private static func nextCommand(for session: URL, readiness: [String: Any]?) -> String {
        if let blocked = readPipelineBlocked(session) {
            if let next = string(blocked["recommended_next"]), !next.isEmpty {
                return next
            }
            if let commands = blocked["next_commands"] as? [[String: Any]],
               let command = commands.compactMap({ string($0["command"]) }).first {
                return command
            }
            return "murmurmark inspect \(PathDisplay.display(session))"
        }
        if CaptureHealthState.partialInfo(session: session) != nil {
            return CaptureHealthState.preferredPartialNext(session: session)
        }
        if let readiness {
            if let exportHandoff = successfulExportHandoff(session: session, readiness: readiness) {
                return exportHandoff.command
            }
            if let handoff = AuthoritativeHandoffState.payload(session),
               let next = string(handoff["recommended_next"]),
               !next.isEmpty {
                return next
            }
            if let next = string(readiness["recommended_next"]), !next.isEmpty {
                return next
            }
            if let commands = readiness["next_commands"] as? [[String: Any]],
               let command = ReadinessPrinter.preferredNextCommand(commands) {
                return command
            }
            return "murmurmark status \(PathDisplay.display(session))"
        }
        return "murmurmark process \(PathDisplay.display(session))"
    }

    private static func successfulExportHandoff(
        session: URL,
        readiness: [String: Any]?
    ) -> (command: String, manifest: URL)? {
        guard isExportable(readiness),
              outcomeAllowsExport(session)
        else {
            return nil
        }
        let manifestURL = PathURLs.fileURL("exports/private")
            .appendingPathComponent(session.lastPathComponent)
            .appendingPathComponent("export_manifest.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path),
              let payload = try? JSONFiles.object(manifestURL)
        else {
            return nil
        }
        guard string(payload["schema"]) == "murmurmark.export_manifest/v1" else { return nil }
        let status = string(payload["status"]) ?? ""
        guard status == "exported" || status == "exported_with_warnings" else { return nil }
        let blockers = payload["blockers"] as? [Any] ?? []
        guard blockers.isEmpty else { return nil }
        if let nextCommands = payload["next_commands"] as? [[String: Any]],
           let command = ReadinessPrinter.preferredNextCommand(nextCommands) {
            return (command, manifestURL)
        }
        if let next = string(payload["next"]), !next.isEmpty {
            return (next, manifestURL)
        }
        return nil
    }

    private static func outcomeAllowsExport(_ session: URL) -> Bool {
        let url = session.appendingPathComponent("derived/outcome/outcome.json")
        guard FileManager.default.fileExists(atPath: url.path),
              let payload = try? JSONFiles.object(url)
        else {
            return false
        }
        return string(payload["outcome"]) == "ready_for_notes"
            && string(payload["export_status"]) == "allowed"
    }

    private static func isExportable(_ readiness: [String: Any]?) -> Bool {
        guard let readiness else { return false }
        let gate = string(readiness["use_gate"]) ?? "unknown"
        let exportBlockers = readiness["export_blockers"] as? [Any] ?? []
        return gate == "ready_for_notes" && exportBlockers.isEmpty
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func double(_ value: Any?) -> Double? {
        if let value = value as? Double { return value }
        if let value = value as? NSNumber { return value.doubleValue }
        if let value = value as? String { return Double(value) }
        return nil
    }

    private static func formatMinutes(_ seconds: Double) -> String {
        String(format: "%.2f min", seconds / 60.0)
    }

    private static func formatPercent(_ ratio: Double) -> String {
        String(format: "%.2f%%", ratio * 100.0)
    }
}

enum OpenCommands {
    static func open(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }
        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let kind = canonicalKind(ArgumentEditing.takeOption("kind", from: &remaining) ?? "notes")
        let pathOnly = ArgumentEditing.takeFlag("path-only", from: &remaining)
        let commandOnly = ArgumentEditing.takeFlag("command-only", from: &remaining)
        let cat = ArgumentEditing.takeFlag("cat", from: &remaining)
        let target = remaining.isEmpty ? "latest" : remaining.removeFirst()
        guard remaining.isEmpty else {
            throw CLIError("unexpected open arguments: \(remaining.joined(separator: " "))")
        }
        if cat && kind == "all" {
            throw CLIError("open --cat requires a single --kind")
        }

        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let payload = try readinessPayload(session)
        let profile = string(payload["selected_profile"]) ?? "current"
        let targets = resolvedTargets(kind: kind, session: session, payload: payload)
        guard !targets.isEmpty else {
            let sessionPath = PathDisplay.display(session)
            throw CLIError("no openable artifact for kind '\(kind)' in \(sessionPath); run `murmurmark report \(sessionPath)`")
        }

        if cat {
            let data = try Data(contentsOf: targets[0].url)
            FileHandle.standardOutput.write(data)
            return
        }
        if pathOnly {
            for target in targets {
                print(PathDisplay.display(target.url))
            }
            return
        }
        if commandOnly {
            for target in targets {
                print(target.command)
            }
            return
        }

        print("SESSION=\"\(PathDisplay.display(session))\"")
        print("")
        print("open:")
        print("  profile: \(profile)")
        if kind == "all" {
            print("  selected: all")
            print("  commands:")
            for target in targets {
                print("    \(target.command) — \(target.label)")
            }
            if let first = targets.first {
                print("  recommended_next: \(first.command)")
                FinalNextPrinter.print(first.command)
            }
            return
        }
        let selectedTarget = targets[0]
        print("  selected: \(selectedTarget.id)")
        print("  path: \(PathDisplay.display(selectedTarget.url))")
        print("  command: \(selectedTarget.command)")
        print("  recommended_next: \(selectedTarget.command)")
        print("  next:")
        print("    \(selectedTarget.command)")
        FinalNextPrinter.print(selectedTarget.command)
    }

    private struct OpenTarget {
        let id: String
        let label: String
        let url: URL

        var command: String {
            "less \(PathDisplay.display(url))"
        }
    }

    private static let orderedKinds = [
        "notes",
        "transcript",
        "verdict",
        "readiness",
        "audio_review",
        "local_recall",
        "transcript_order",
        "review_items",
        "evidence",
        "pipeline_run",
    ]

    private static func readinessPayload(_ session: URL) throws -> [String: Any] {
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            let sessionPath = PathDisplay.display(session)
            throw CLIError("session_readiness.json not found for \(sessionPath); run `murmurmark process \(sessionPath)`")
        }
        return try JSONFiles.object(url)
    }

    private static func resolvedTargets(kind: String, session: URL, payload: [String: Any]) -> [OpenTarget] {
        let kinds = kind == "all" ? orderedKinds : [kind]
        return kinds.compactMap { itemKind in
            guard let url = url(for: itemKind, session: session, payload: payload) else { return nil }
            return OpenTarget(id: itemKind, label: label(for: itemKind), url: url)
        }
    }

    private static func url(for kind: String, session: URL, payload: [String: Any]) -> URL? {
        if kind == "readiness" {
            return existing(session.appendingPathComponent("derived/readiness/session_readiness.md"))
        }
        let outputKey = outputKey(for: kind)
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        if let outputKey,
           let url = outputURL(outputKey, outputs: outputs, session: session) {
            return url
        }
        return fallbackURL(for: kind, session: session, profile: string(payload["selected_profile"]) ?? "current")
    }

    private static func outputURL(_ key: String, outputs: [String: Any], session: URL) -> URL? {
        guard let item = outputs[key] as? [String: Any],
              let path = item["path"] as? String,
              !path.isEmpty
        else {
            return nil
        }
        let url = path.hasPrefix("/") ? PathURLs.fileURL(path) : session.appendingPathComponent(path)
        return existing(url)
    }

    private static func fallbackURL(for kind: String, session: URL, profile: String) -> URL? {
        let suffix = profile == "current" ? "" : ".\(profile)"
        let synthesis = session.appendingPathComponent("derived/synthesis-simple/extractive")
        let resolved = session.appendingPathComponent("derived/transcript-simple/whisper-cpp/resolved")
        let direct: URL
        switch kind {
        case "notes":
            direct = synthesis.appendingPathComponent("notes\(suffix).md")
        case "transcript":
            direct = resolved.appendingPathComponent(profile == "current" ? "transcript.md" : "transcript\(suffix).md")
        case "verdict":
            direct = synthesis.appendingPathComponent("quality_verdict\(suffix).md")
        case "review_items":
            direct = synthesis.appendingPathComponent("review_items\(suffix).jsonl")
        case "evidence":
            direct = synthesis.appendingPathComponent("evidence_notes\(suffix).json")
        case "audio_review":
            direct = session.appendingPathComponent("derived/audit/audio-review-pack/audio_review_report.md")
        case "local_recall":
            direct = session.appendingPathComponent("derived/audit/local-recall/local_recall_review.md")
        case "transcript_order":
            direct = session.appendingPathComponent("derived/audit/order/transcript_order_review.md")
        case "pipeline_run":
            direct = session.appendingPathComponent("derived/pipeline-run/pipeline_run_report.json")
        default:
            return nil
        }
        return existing(direct)
    }

    private static func existing(_ url: URL) -> URL? {
        FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private static func outputKey(for kind: String) -> String? {
        switch kind {
        case "notes":
            return "notes"
        case "transcript":
            return "transcript"
        case "verdict":
            return "quality_verdict"
        case "review_items":
            return "review_items"
        case "evidence":
            return "evidence_notes"
        case "audio_review":
            return "audio_review_report"
        case "local_recall":
            return "local_recall_review"
        case "transcript_order":
            return "transcript_order_review"
        case "pipeline_run":
            return "pipeline_run_report"
        default:
            return nil
        }
    }

    private static func canonicalKind(_ raw: String) -> String {
        switch raw {
        case "quality-verdict", "quality_verdict":
            return "verdict"
        case "review-items", "review_items":
            return "review_items"
        case "audio-review", "audio_review":
            return "audio_review"
        case "local-recall", "local_recall":
            return "local_recall"
        case "order", "transcript-order", "transcript_order":
            return "transcript_order"
        case "pipeline-run", "pipeline_run":
            return "pipeline_run"
        default:
            return raw
        }
    }

    private static func label(for kind: String) -> String {
        switch kind {
        case "notes":
            return "Notes"
        case "transcript":
            return "Transcript"
        case "verdict":
            return "Quality verdict"
        case "readiness":
            return "Session readiness"
        case "audio_review":
            return "Audio review report"
        case "local_recall":
            return "Local recall review"
        case "transcript_order":
            return "Transcript order review"
        case "review_items":
            return "Review items"
        case "evidence":
            return "Evidence notes JSON"
        case "pipeline_run":
            return "Pipeline run report"
        default:
            return kind
        }
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func printHelp() {
        print("""
        usage: murmurmark open [SESSION|latest] [--kind notes|transcript|verdict|readiness|audio-review|local-recall|order|all]
                             [--path-only|--command-only|--cat] [--sessions-root ./sessions]

        Resolves local output artifacts from session_readiness.json and prints the safest way to
        inspect them. Default kind is notes. Use --cat to stream one artifact to stdout.

        Common:
          murmurmark open latest
          murmurmark open latest --kind transcript --command-only
          murmurmark open latest --kind verdict --cat
          murmurmark open latest --kind all
        """)
    }
}

enum ReviewCommands {
    static func review(_ args: [String]) throws {
        guard let target = args.first else {
            ReviewHelp.print()
            return
        }
        if ArgumentEditing.hasHelpFlag([target]) {
            ReviewHelp.print()
            return
        }
        var forwarded = Array(args.dropFirst())
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &forwarded) ?? "sessions")

        switch target {
        case "plan":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("build-review-plan.py").path] + forwarded)
                return
            }
            try buildPlan(extraArgs: forwarded)
            try ReviewPrinter.printPlan()
        case "progress":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-review-decisions-progress.py").path] + forwarded)
                return
            }
            let session = ReviewSessionLocalPlan.takeSessionOption(from: &forwarded, sessionsRoot: sessionsRoot)
            if let session {
                ReviewSessionLocalPlan.addProgressDefaults(for: session, to: &forwarded)
            }
            try ReviewProgressRunner.run(args: forwarded)
            if let session {
                print("SESSION=\"\(PathDisplay.display(session))\"")
            }
            try ReviewPrinter.printProgress(report: ReviewPaths.progressReport(from: forwarded))
        case "next":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                ReviewNextCommand.printHelp()
                return
            }
            try ReviewNextCommand.run(forwarded, sessionsRoot: sessionsRoot)
        case "apply":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("apply-review-decisions-batch.py").path] + forwarded)
                return
            }
            try rewriteLatestSessionFilters(in: &forwarded, sessionsRoot: sessionsRoot)
            let session = ReviewSessionLocalPlan.sessionOption(from: forwarded, sessionsRoot: sessionsRoot)
            var preflightArgs = forwarded
            if let session {
                ReviewSessionLocalPlan.addReviewApplyDefaults(for: session, to: &preflightArgs)
            }
            let allowPartialReview = ArgumentEditing.hasOption("allow-partial-review", in: forwarded)
            let decisions = ReviewPaths.reviewApplyDecisions(from: preflightArgs)
            let template = ReviewPaths.reviewApplyTemplate(from: preflightArgs)
            let progress = ReviewPaths.reviewApplyProgress(decisions: decisions)
            if !FileManager.default.fileExists(atPath: decisions.path)
                || !FileManager.default.fileExists(atPath: template.path) {
                if let session {
                    print("SESSION=\"\(PathDisplay.display(session))\"")
                }
                ReviewPrinter.printApplyNotReady(session: session, decisions: decisions, template: template)
                return
            }
            try ReviewProgressRunner.run(args: [
                "--template", template.path,
                "--decisions", decisions.path,
                "--out", progress.path,
                "--markdown", progress.deletingPathExtension().appendingPathExtension("md").path,
            ])
            if !ReviewPaths.isProgressReadyForApply(progress) {
                if !allowPartialReview {
                    if let session {
                        print("SESSION=\"\(PathDisplay.display(session))\"")
                    }
                    try ReviewPrinter.printApplyNotReady(session: session, decisions: decisions, progress: progress)
                    return
                }
                print("partial_review_scope_allowed: true")
            }
            let report = try apply(forwarded, sessionsRoot: sessionsRoot)
            if let session {
                print("SESSION=\"\(PathDisplay.display(session))\"")
            }
            try ReviewPrinter.printApply(report: report)
        case "first-lane":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                ReviewFirstLaneCommand.printHelp()
                return
            }
            try ReviewFirstLaneCommand.run(forwarded, sessionsRoot: sessionsRoot)
        case "suggested":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                ReviewSuggestedCommand.printHelp()
                return
            }
            try ReviewSuggestedCommand.run(forwarded, sessionsRoot: sessionsRoot)
        case "lane":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                ReviewLaneCommand.printHelp()
                return
            }
            try ReviewLaneCommand.run(forwarded, sessionsRoot: sessionsRoot)
        case "agent":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                ReviewAgentHelp.print()
                return
            }
            try agent(forwarded)
        case "workspace":
            try workspace(forwarded, sessionsRoot: sessionsRoot)
        default:
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("review-decisions-cli.py").path] + forwarded)
                return
            }
            let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
            try ensurePlanExists()
            let python = try PythonRuntime.resolve()
            let script = try script("review-decisions-cli.py")
            let command = [
                script.path,
                "--template", "sessions/_reports/review-plan/review_decisions.template.jsonl",
                "--out", "sessions/_reports/review-plan/review_decisions.jsonl",
                "--session", session.lastPathComponent,
            ] + forwarded
            print("SESSION=\"\(PathDisplay.display(session))\"")
            fflush(stdout)
            try Tooling.runPath(python, command)
            try ReviewProgressRunner.run()
            try ReviewPrinter.printProgress()
        }
    }

    private static func buildPlan(extraArgs: [String], refreshOperational: Bool = true) throws {
        let python = try PythonRuntime.resolve()
        if refreshOperational {
            try Tooling.runPathQuiet(python, [try script("report-operational-readiness.py").path])
        }
        try Tooling.runPathQuiet(python, [try script("build-review-plan.py").path] + extraArgs)
    }

    private static func workspace(_ args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        let mode: String
        if let first = forwarded.first, ["build", "apply"].contains(first) {
            mode = first
            forwarded.removeFirst()
        } else {
            mode = "build"
        }

        switch mode {
        case "build":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("build-review-workspace.py").path] + forwarded)
                return
            }
            let session = ReviewSessionLocalPlan.sessionOption(from: forwarded, sessionsRoot: sessionsRoot)
            if let session {
                try ReviewSessionLocalPlan.prepareIfNeeded(session: session)
                ReviewSessionLocalPlan.addBuildDefaults(for: session, to: &forwarded)
                ReviewSessionLocalPlan.replaceSessionFilter(in: &forwarded, with: session.lastPathComponent)
            } else if !ArgumentEditing.hasOption("template", in: forwarded) {
                try ensurePlanExists()
                try rewriteLatestSessionFilters(in: &forwarded, sessionsRoot: sessionsRoot)
            } else {
                try rewriteLatestSessionFilters(in: &forwarded, sessionsRoot: sessionsRoot)
            }
            try Tooling.runPathQuiet(try PythonRuntime.resolve(), [try script("build-review-workspace.py").path] + forwarded)
            if let session {
                print("SESSION=\"\(PathDisplay.display(session))\"")
            }
            try ReviewPrinter.printWorkspace(outDir: ReviewPaths.workspaceOutDir(from: forwarded))
        case "apply":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("apply-review-workspace-decisions.py").path] + forwarded)
                return
            }
            let session = ReviewSessionLocalPlan.takeSessionOption(from: &forwarded, sessionsRoot: sessionsRoot)
            if let session {
                ReviewSessionLocalPlan.addWorkspaceApplyDefaults(for: session, to: &forwarded)
            }
            if !ArgumentEditing.hasOption("quiet", in: forwarded) {
                forwarded.append("--quiet")
            }
            try Tooling.runPathQuiet(try PythonRuntime.resolve(), [try script("apply-review-workspace-decisions.py").path] + forwarded)
            if let session {
                print("SESSION=\"\(PathDisplay.display(session))\"")
            }
            try ReviewPrinter.printWorkspaceApply(report: ReviewPaths.workspaceApplyReport(from: forwarded))
            if !ArgumentEditing.hasOption("dry-run", in: forwarded) {
                let defaultDecisions = PathURLs.fileURL("sessions/_reports/review-plan/review_decisions.jsonl")
                if ReviewPaths.workspaceDecisionsOut(from: forwarded).standardizedFileURL.path == defaultDecisions.standardizedFileURL.path {
                    try ReviewProgressRunner.run()
                    try ReviewPrinter.printProgress()
                }
            }
        default:
            throw CLIError("unknown review workspace mode: \(mode)")
        }
    }

    private static func rewriteLatestSessionFilters(in args: inout [String], sessionsRoot: URL) throws {
        var index = 0
        while index < args.count {
            if args[index] == "--session", index + 1 < args.count, args[index + 1] == "latest" {
                args[index + 1] = try SessionResolver.latest(in: sessionsRoot).lastPathComponent
                index += 2
            } else {
                index += 1
            }
        }
    }

    private static func ensurePlanExists() throws {
        let template = PathURLs.fileURL("sessions/_reports/review-plan/review_decisions.template.jsonl")
        if !FileManager.default.fileExists(atPath: template.path) {
            try buildPlan(extraArgs: [])
        }
    }

    private static func agent(_ args: [String]) throws {
        var forwarded = args
        let sessionQuality = ArgumentEditing.takeOption("session-quality", from: &forwarded)
            ?? "sessions/_reports/session-quality/session_quality_report.json"
        let audioJudgeQueue = ArgumentEditing.takeOption("audio-judge-queue", from: &forwarded)
            ?? "sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl"
        let corpusEvaluation = ArgumentEditing.takeOption("corpus-evaluation", from: &forwarded)
            ?? "sessions/_reports/regression-corpus/regression_corpus_evaluation.json"
        let audioJudge = ArgumentEditing.takeOption("audio-judge", from: &forwarded)
            ?? "sessions/_reports/audio-judge-v0/audio_judge_v0_report.json"
        let decisions = ArgumentEditing.takeOption("out", from: &forwarded)
            ?? "sessions/_reports/review-plan/review_decisions.agent_reviewed_v1.jsonl"
        let template = ArgumentEditing.takeOption("template-out", from: &forwarded)
            ?? "sessions/_reports/review-plan/review_decisions.agent_reviewed_v1.template.jsonl"
        let report = ArgumentEditing.takeOption("report", from: &forwarded)
            ?? "sessions/_reports/review-plan/agent_review_report.agent_reviewed_v1.json"
        let applyReport = ArgumentEditing.takeOption("apply-report", from: &forwarded)
            ?? "sessions/_reports/review-plan/review_decisions_apply.agent_reviewed_v1.json"
        let outputProfile = ArgumentEditing.takeOption("output-profile", from: &forwarded)
            ?? "agent_reviewed_v1"
        let sessionQualityOutDir = ArgumentEditing.takeOption("session-quality-out-dir", from: &forwarded)
            ?? "sessions/_reports/session-quality"
        let operationalReadinessOutDir = ArgumentEditing.takeOption("operational-readiness-out-dir", from: &forwarded)
            ?? "sessions/_reports/operational-readiness"
        let reviewPlanOutDir = ArgumentEditing.takeOption("review-plan-out-dir", from: &forwarded)
            ?? "sessions/_reports/review-plan"
        let noApply = ArgumentEditing.takeFlag("no-apply", from: &forwarded)
        guard forwarded.isEmpty else {
            throw CLIError("unknown review agent option(s): \(forwarded.joined(separator: " "))")
        }

        let python = try PythonRuntime.resolve()
        try Tooling.runPath(python, [
            try script("build-agent-review-decisions.py").path,
            "--session-quality", sessionQuality,
            "--audio-judge-queue", audioJudgeQueue,
            "--out", decisions,
            "--template-out", template,
            "--report", report,
        ])
        try ReviewPrinter.printAgentBuild(report: PathURLs.fileURL(report))
        guard !noApply else {
            return
        }

        let status = try Tooling.runPathAllowingExitCodes(
            python,
            [
                try script("apply-review-decisions-batch.py").path,
                "--decisions", decisions,
                "--review-template", template,
                "--output-profile", outputProfile,
                "--synthesize",
                "--refresh-reports",
                "--out", applyReport,
                "--session-quality-out-dir", sessionQualityOutDir,
                "--operational-readiness-out-dir", operationalReadinessOutDir,
                "--review-plan-out-dir", reviewPlanOutDir,
                "--corpus-evaluation", corpusEvaluation,
                "--audio-judge", audioJudge,
                "--audio-judge-queue", audioJudgeQueue,
            ],
            allowedExitCodes: [0, 2]
        )
        try ReviewPrinter.printApply(report: PathURLs.fileURL(applyReport))
        if status != 0 {
            throw CLIError("agent review apply did not pass; inspect \(PathDisplay.display(PathURLs.fileURL(applyReport)))")
        }
    }

    private static func apply(_ args: [String], sessionsRoot: URL) throws -> URL {
        var forwarded = args
        if let session = ReviewSessionLocalPlan.takeSessionOption(from: &forwarded, sessionsRoot: sessionsRoot) {
            ReviewSessionLocalPlan.addReviewApplyDefaults(for: session, to: &forwarded)
            forwarded += ["--session", session.lastPathComponent]
        }
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("apply-review-decisions-batch.py").path,
            "--decisions", "sessions/_reports/review-plan/review_decisions.jsonl",
            "--review-template", "sessions/_reports/review-plan/review_decisions.template.jsonl",
            "--synthesize",
            "--refresh-reports",
        ] + forwarded)
        return ReviewPaths.applyReport(from: forwarded)
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }

}

enum ReviewSessionLocalPlan {
    static func sessionOption(from args: [String], sessionsRoot: URL) -> URL? {
        guard let value = ArgumentEditing.peekOption("session", in: args) else { return nil }
        return try? SessionResolver.resolve(value, sessionsRoot: sessionsRoot)
    }

    static func takeSessionOption(from args: inout [String], sessionsRoot: URL) -> URL? {
        guard let value = ArgumentEditing.peekOption("session", in: args),
              let session = try? SessionResolver.resolve(value, sessionsRoot: sessionsRoot)
        else {
            return nil
        }
        _ = ArgumentEditing.takeOption("session", from: &args)
        return session
    }

    static func addBuildDefaults(for session: URL, to args: inout [String]) {
        let plan = planDir(session)
        addOption("template", plan.appendingPathComponent("review_decisions.template.jsonl").path, to: &args)
        addOption("decisions", plan.appendingPathComponent("review_decisions.jsonl").path, to: &args)
        addOption("out-dir", plan.path, to: &args)
    }

    static func addWorkspaceApplyDefaults(for session: URL, to args: inout [String]) {
        let plan = planDir(session)
        addOption("workspace", plan.appendingPathComponent("review_workspace.json").path, to: &args)
        addOption("template", plan.appendingPathComponent("review_decisions.template.jsonl").path, to: &args)
        addOption("out", plan.appendingPathComponent("review_decisions.jsonl").path, to: &args)
        addOption("report", plan.appendingPathComponent("review_workspace_apply_report.json").path, to: &args)
    }

    static func addProgressDefaults(for session: URL, to args: inout [String]) {
        let plan = planDir(session)
        addOption("template", plan.appendingPathComponent("review_decisions.template.jsonl").path, to: &args)
        addOption("decisions", plan.appendingPathComponent("review_decisions.jsonl").path, to: &args)
        addOption("out", plan.appendingPathComponent("review_decisions_progress.json").path, to: &args)
        addOption("markdown", plan.appendingPathComponent("review_decisions_progress.md").path, to: &args)
    }

    static func addReviewApplyDefaults(for session: URL, to args: inout [String]) {
        let plan = planDir(session)
        addOption("decisions", plan.appendingPathComponent("review_decisions.jsonl").path, to: &args)
        addOption("review-template", plan.appendingPathComponent("review_decisions.template.jsonl").path, to: &args)
        addOption("out", plan.appendingPathComponent("review_decisions_apply_report.json").path, to: &args)
        addOption("session-quality-out-dir", session.appendingPathComponent("derived/readiness/session-quality").path, to: &args)
        addOption("operational-readiness-out-dir", session.appendingPathComponent("derived/readiness/operational-readiness").path, to: &args)
        addOption("review-plan-out-dir", plan.path, to: &args)
    }

    static func replaceSessionFilter(in args: inout [String], with value: String) {
        var index = 0
        while index < args.count {
            if args[index] == "--session", index + 1 < args.count {
                args[index + 1] = value
                index += 2
            } else {
                index += 1
            }
        }
    }

    static func prepareIfNeeded(session: URL) throws {
        let readinessRoot = session.appendingPathComponent("derived/readiness")
        let sessionQualityOut = readinessRoot.appendingPathComponent("session-quality")
        let operationalOut = readinessRoot.appendingPathComponent("operational-readiness")
        let planOut = readinessRoot.appendingPathComponent("review-plan")
        if hasPreparedReviewPlan(planOut), !hasNewerReviewEvidence(session: session, planOut: planOut) {
            return
        }
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("report-session-quality.py").path,
            session.path,
            "--out-dir", sessionQualityOut.path,
            "--write-session-readiness",
        ])
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("report-operational-readiness.py").path,
            "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
            "--out-dir", operationalOut.path,
        ])
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("build-review-plan.py").path,
            "--operational-readiness", operationalOut.appendingPathComponent("operational_readiness_report.json").path,
            "--out-dir", planOut.path,
        ])
    }

    private static func hasPreparedReviewPlan(_ planOut: URL) -> Bool {
        let plan = planOut.appendingPathComponent("review_plan.json")
        let template = planOut.appendingPathComponent("review_decisions.template.jsonl")
        guard FileManager.default.fileExists(atPath: plan.path) else {
            return false
        }
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: template.path),
              let size = attributes[.size] as? NSNumber else {
            return false
        }
        return size.intValue > 0
    }

    private static func hasNewerReviewEvidence(session: URL, planOut: URL) -> Bool {
        let template = planOut.appendingPathComponent("review_decisions.template.jsonl")
        guard let templateDate = modificationDate(template) else {
            return true
        }
        let evidence = [
            session.appendingPathComponent("derived/audit/local-recall/local_recall_audit.json"),
            session.appendingPathComponent("derived/audit/order/transcript_order_audit.json"),
        ]
        return evidence.contains { url in
            guard let evidenceDate = modificationDate(url) else { return false }
            return evidenceDate > templateDate
        }
    }

    private static func modificationDate(_ url: URL) -> Date? {
        guard let attributes = try? FileManager.default.attributesOfItem(atPath: url.path) else {
            return nil
        }
        return attributes[.modificationDate] as? Date
    }

    private static func planDir(_ session: URL) -> URL {
        session.appendingPathComponent("derived/readiness/review-plan")
    }

    private static func addOption(_ key: String, _ value: String, to args: inout [String]) {
        guard !ArgumentEditing.hasOption(key, in: args) else { return }
        args += ["--\(key)", value]
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }
}

enum ReviewPaths {
    static func workspaceOutDir(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: args) ?? "sessions/_reports/review-plan")
    }

    static func workspaceApplyReport(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("report", in: args) ?? "sessions/_reports/review-plan/review_workspace_apply_report.json")
    }

    static func applyReport(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("out", in: args) ?? "sessions/_reports/review-plan/review_decisions_apply_report.json")
    }

    static func reviewApplyDecisions(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("decisions", in: args) ?? "sessions/_reports/review-plan/review_decisions.jsonl")
    }

    static func reviewApplyTemplate(from args: [String]) -> URL {
        PathURLs.fileURL(
            ArgumentEditing.peekOption("review-template", in: args)
                ?? "sessions/_reports/review-plan/review_decisions.template.jsonl"
        )
    }

    static func reviewApplyProgress(decisions: URL) -> URL {
        decisions.deletingLastPathComponent().appendingPathComponent("review_decisions_progress.json")
    }

    static func isProgressReadyForApply(_ progress: URL) -> Bool {
        guard let payload = try? JSONFiles.object(progress),
              let summary = payload["summary"] as? [String: Any]
        else {
            return false
        }
        return (summary["ready_for_batch_apply"] as? Bool) == true
    }

    static func workspaceDecisionsOut(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("out", in: args) ?? "sessions/_reports/review-plan/review_decisions.jsonl")
    }

    static func progressReport(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("out", in: args) ?? "sessions/_reports/review-plan/review_decisions_progress.json")
    }
}

enum ReviewAgentHelp {
    static func print() {
        Swift.print("""
        usage: murmurmark review agent [options]

        Builds conservative agent_reviewed_v1 decisions from existing session-quality and audio-judge
        reports, applies them as a separate reviewed profile, synthesizes notes and refreshes reports.

        Options:
          --session-quality PATH   Default: sessions/_reports/session-quality/session_quality_report.json
          --audio-judge-queue PATH Default: sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl
          --corpus-evaluation PATH Default: sessions/_reports/regression-corpus/regression_corpus_evaluation.json
          --audio-judge PATH       Default: sessions/_reports/audio-judge-v0/audio_judge_v0_report.json
          --out PATH               Decisions JSONL. Default: sessions/_reports/review-plan/review_decisions.agent_reviewed_v1.jsonl
          --template-out PATH      Template JSONL. Default: sessions/_reports/review-plan/review_decisions.agent_reviewed_v1.template.jsonl
          --report PATH            Build report JSON. Default: sessions/_reports/review-plan/agent_review_report.agent_reviewed_v1.json
          --apply-report PATH      Apply report JSON. Default: sessions/_reports/review-plan/review_decisions_apply.agent_reviewed_v1.json
          --output-profile NAME    Default: agent_reviewed_v1
          --session-quality-out-dir PATH
          --operational-readiness-out-dir PATH
          --review-plan-out-dir PATH
          --no-apply               Only build decisions and template.
        """)
    }
}

enum ReviewNextCommand {
    static func run(_ args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        let noRefresh = ArgumentEditing.takeFlag("no-refresh", from: &forwarded)
        let noPlan = ArgumentEditing.takeFlag("no-plan", from: &forwarded)
        let target = forwarded.first ?? "latest"
        if !forwarded.isEmpty {
            forwarded.removeFirst()
        }
        guard forwarded.isEmpty else {
            throw CLIError("review next accepts only SESSION|latest, --no-refresh and --no-plan")
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let readinessRoot = session.appendingPathComponent("derived/readiness")
        let sessionQualityOut = readinessRoot.appendingPathComponent("session-quality")
        let operationalOut = readinessRoot.appendingPathComponent("operational-readiness")
        let reviewPlanOut = readinessRoot.appendingPathComponent("review-plan")
        if !noRefresh {
            try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                try script("report-session-quality.py").path,
                session.path,
                "--out-dir", sessionQualityOut.path,
                "--write-session-readiness",
            ])
            if !noPlan, try needsReview(session: session) {
                try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                    try script("report-operational-readiness.py").path,
                    "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
                    "--out-dir", operationalOut.path,
                ])
                try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                    try script("build-review-plan.py").path,
                    "--operational-readiness", operationalOut.appendingPathComponent("operational_readiness_report.json").path,
                    "--out-dir", reviewPlanOut.path,
                ])
            }
        }
        try ReviewNextPrinter.print(
            session: session,
            planOutDir: reviewPlanOut
        )
    }

    static func printHelp() {
        print("""
        usage: murmurmark review next [SESSION|latest] [--no-refresh] [--no-plan]

        Refreshes session readiness, then prints the next review-oriented command chain for that
        session. When review is needed, it also builds a session-local review plan under
        SESSION/derived/readiness/review-plan.

        Use --no-refresh when session_readiness.json is already current.
        Use --no-plan to refresh readiness without rebuilding the review plan.
        """)
    }

    private static func needsReview(session: URL) throws -> Bool {
        let readiness = try JSONFiles.object(session.appendingPathComponent("derived/readiness/session_readiness.json"))
        let gate = readiness["use_gate"] as? String ?? ""
        let reviewBlockers = readiness["review_blockers"] as? [Any] ?? []
        return gateRequiresReview(gate) || !reviewBlockers.isEmpty
    }

    private static func gateRequiresReview(_ gate: String) -> Bool {
        gate == "review_first"
            || gate == "do_not_use_without_manual_review"
            || gate.hasSuffix("_review_first")
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }
}

enum ReviewHelp {
    static func print() {
        Swift.print("""
        usage: murmurmark review plan
               murmurmark review next [SESSION|latest]
               murmurmark review first-lane [--session latest|SESSION]
               murmurmark review suggested [preview|apply] [SESSION|latest|--session SESSION]
               murmurmark review lane LANE [--session latest|SESSION]
               murmurmark review lane apply LANE|first [--session latest|SESSION] [--answers-file PATH|--answers TEXT]
                                    [--answers-source manual|suggested]
               murmurmark review workspace [build|apply] [--session latest|SESSION] [--answers-source review|suggested]
               murmurmark review SESSION|latest [--lane LANE] [--no-play]
               murmurmark review progress [--session latest|SESSION]
               murmurmark review apply [--session latest|SESSION] [--allow-partial-review]
               murmurmark review agent

        Review turns audit evidence into explicit decisions, applies those decisions into a
        separate reviewed transcript profile, and refreshes readiness reports.

        Common flow:
          murmurmark review next "$SESSION"
          murmurmark review suggested "$SESSION"
          murmurmark review suggested apply "$SESSION"
          murmurmark review first-lane --session "$SESSION"
          # listen/edit the generated answer sheet
          murmurmark review lane apply first --session "$SESSION"
          murmurmark review apply --session latest

        Use --allow-partial-review to materialize already closed rows while keeping the
        remaining review scope visible in readiness reports.
        """)
    }
}

enum ReviewSuggestedCommand {
    static func run(_ args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        let mode: String
        if let first = forwarded.first, ["preview", "apply"].contains(first) {
            mode = first
            forwarded.removeFirst()
        } else {
            mode = "preview"
        }
        let noMaterialize = ArgumentEditing.takeFlag("no-materialize", from: &forwarded)
        let skipTargetedJudge = ArgumentEditing.takeFlag("skip-targeted-judge", from: &forwarded)
        let skipTargetMe = ArgumentEditing.takeFlag("skip-target-me", from: &forwarded)
        let explicitSession = ReviewSessionLocalPlan.takeSessionOption(from: &forwarded, sessionsRoot: sessionsRoot)
        let target: String
        if let first = forwarded.first, !first.hasPrefix("-") {
            target = first
            forwarded.removeFirst()
        } else {
            target = "latest"
        }
        guard forwarded.isEmpty else {
            throw CLIError(
                "review suggested accepts only preview|apply, SESSION|latest, --session, "
                + "--no-materialize, --skip-targeted-judge and --skip-target-me"
            )
        }
        let session: URL
        if let explicitSession {
            session = explicitSession
        } else {
            session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        }
        try ReviewSessionLocalPlan.prepareIfNeeded(session: session)

        let python = try PythonRuntime.resolve()
        try buildReviewWorkspace(session: session, python: python)
        if !skipTargetedJudge || !skipTargetMe {
            try refreshTargetedEvidence(
                session: session,
                python: python,
                runStrongerAudioJudge: !skipTargetedJudge,
                runTargetMe: !skipTargetMe
            )
            try buildReviewWorkspace(session: session, python: python)
        }

        var workspaceApplyArgs = ["--answers-source", "suggested", "--allow-partial", "--quiet"]
        ReviewSessionLocalPlan.addWorkspaceApplyDefaults(for: session, to: &workspaceApplyArgs)
        if mode == "preview" {
            workspaceApplyArgs.append("--dry-run")
        }
        try Tooling.runPathQuiet(python, [try script("apply-review-workspace-decisions.py").path] + workspaceApplyArgs)
        var progressArgs: [String] = []
        ReviewSessionLocalPlan.addProgressDefaults(for: session, to: &progressArgs)
        try ReviewProgressRunner.run(args: progressArgs)
        try refreshSessionReadiness(session: session, python: python)
        try refreshOutcome(session: session, python: python)

        print("SESSION=\"\(PathDisplay.display(session))\"")
        let workspaceReport = ReviewPaths.workspaceApplyReport(from: workspaceApplyArgs)
        try ReviewPrinter.printWorkspaceApply(report: workspaceReport)

        guard mode == "apply", !noMaterialize else {
            return
        }
        let payload = try JSONFiles.object(workspaceReport)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let readyForProfile = (summary["ready_for_batch_apply"] as? Bool) == true
            || (summary["ready_for_partial_apply"] as? Bool) == true
        guard readyForProfile else {
            return
        }

        var batchArgs = ["--allow-partial-review", "--session", session.lastPathComponent, "--synthesize", "--refresh-reports"]
        ReviewSessionLocalPlan.addReviewApplyDefaults(for: session, to: &batchArgs)
        let status = try Tooling.runPathAllowingExitCodes(
            python,
            [try script("apply-review-decisions-batch.py").path] + batchArgs,
            allowedExitCodes: [0, 2]
        )
        try ReviewPrinter.printApply(report: ReviewPaths.applyReport(from: batchArgs))
        try refreshOutcome(session: session, python: python)
        print("")
        print("corpus_delta:")
        print("  refresh: murmurmark report corpus")
        if status != 0 {
            throw CLIError("suggested review apply did not pass; inspect \(PathDisplay.display(ReviewPaths.applyReport(from: batchArgs)))")
        }
    }

    static func printHelp() {
        print("""
        usage: murmurmark review suggested [preview|apply] [SESSION|latest]
                                         [--session SESSION] [--no-materialize]
                                         [--skip-targeted-judge] [--skip-target-me]

        Builds all session-local review lanes, applies generated suggested answers in preview
        mode and shows the exact manual remainder. It refreshes lane suggestions from cached
        stronger-audio-judge and Target-Me evidence. New faster-whisper decode is opt-in via
        MURMURMARK_TARGETED_JUDGE_COMPUTE=1. `apply` writes only reviewed suggested rows, keeps
        dots/todo as manual review, then refreshes reviewed_v1 readiness unless --no-materialize is passed.

        Common:
          murmurmark review suggested latest
          murmurmark review suggested apply latest
          murmurmark review suggested apply --session latest --no-materialize
        """)
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }

    private static func refreshOutcome(session: URL, python: URL) throws {
        try Tooling.runPathQuiet(python, [
            try script("evaluate-outcome.py").path,
            session.path,
        ])
    }

    private static func refreshSessionReadiness(session: URL, python: URL) throws {
        try Tooling.runPathQuiet(python, [
            try script("report-session-quality.py").path,
            session.path,
            "--out-dir",
            session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
    }

    private static func buildReviewWorkspace(session: URL, python: URL) throws {
        var workspaceArgs: [String] = []
        ReviewSessionLocalPlan.addBuildDefaults(for: session, to: &workspaceArgs)
        workspaceArgs += ["--session", session.lastPathComponent]
        try Tooling.runPathQuiet(python, [try script("build-review-workspace.py").path] + workspaceArgs)
    }

    private static func refreshTargetedEvidence(
        session: URL,
        python: URL,
        runStrongerAudioJudge: Bool,
        runTargetMe: Bool
    ) throws {
        let lanePacks = reviewLanePacks(for: session)
        guard !lanePacks.isEmpty else {
            return
        }
        if runStrongerAudioJudge {
            let needsFullMicSources = lanePacks.contains { lanePack in
                lanePack.lastPathComponent.contains("check_local_recall")
            }
            var judgeArgs = [
                try script("audit-stronger-audio-judge.py").path,
                session.path,
                "--profile", "auto",
                "--max-items", envValue("MURMURMARK_TARGETED_JUDGE_MAX_ITEMS", defaultValue: "80"),
                "--no-progress",
            ]
            if !needsFullMicSources {
                judgeArgs.append("--quick")
            }
            if envBool("MURMURMARK_TARGETED_JUDGE_COMPUTE", defaultValue: false) {
                judgeArgs += [
                    "--max-computed-items",
                    envValue("MURMURMARK_TARGETED_JUDGE_MAX_COMPUTED", defaultValue: "4"),
                ]
            } else {
                judgeArgs.append("--cached-only")
            }
            for lanePack in lanePacks {
                judgeArgs += ["--review-lane-pack", lanePack.path]
            }
            do {
                try Tooling.runPathQuiet(python, judgeArgs)
            } catch {
                print("targeted_stronger_audio_judge: skipped (\(error.localizedDescription))")
            }
        }
        if runTargetMe && targetMeNeedsRefresh(session: session) {
            do {
                try Tooling.runPathQuiet(python, [
                    try script("audit-target-me.py").path,
                    session.path,
                    "--profile", "auto",
                    "--max-items", envValue("MURMURMARK_TARGET_ME_REVIEW_MAX_ITEMS", defaultValue: "20"),
                    "--no-progress",
                ])
            } catch {
                print("target_me: skipped (\(error.localizedDescription))")
            }
        }
    }

    private static func reviewLanePacks(for session: URL) -> [URL] {
        let directory = session.appendingPathComponent("derived/readiness/review-plan/lane-packs")
        let urls = (try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil,
            options: [.skipsHiddenFiles]
        )) ?? []
        return urls
            .filter { $0.lastPathComponent.hasPrefix("review_lane_pack.") && $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    private static func targetMeNeedsRefresh(session: URL) -> Bool {
        let targetSummary = session.appendingPathComponent("derived/audit/target-me/target_me_summary.json")
        let strongerSummary = session.appendingPathComponent("derived/audit/audio-review-pack/faster_whisper_judge_summary.json")
        if !envBool("MURMURMARK_REVIEW_TARGET_ME_REFRESH", defaultValue: false) {
            return false
        }
        guard FileManager.default.fileExists(atPath: strongerSummary.path) else {
            return !FileManager.default.fileExists(atPath: targetSummary.path)
        }
        guard let targetDate = modificationDate(targetSummary) else {
            return true
        }
        guard let strongerDate = modificationDate(strongerSummary) else {
            return false
        }
        return targetDate < strongerDate
    }

    private static func envValue(_ key: String, defaultValue: String) -> String {
        let value = ProcessInfo.processInfo.environment[key]?.trimmingCharacters(in: .whitespacesAndNewlines)
        return value?.isEmpty == false ? value! : defaultValue
    }

    private static func envBool(_ key: String, defaultValue: Bool) -> Bool {
        guard let value = ProcessInfo.processInfo.environment[key]?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
              !value.isEmpty
        else {
            return defaultValue
        }
        return ["1", "true", "yes", "on"].contains(value)
    }

    private static func modificationDate(_ url: URL) -> Date? {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path) else {
            return nil
        }
        return attrs[.modificationDate] as? Date
    }
}

enum ReviewProgressRunner {
    static func run(args: [String] = []) throws {
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("report-review-decisions-progress.py").path,
        ] + args)
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }
}

enum ReviewLaneCommand {
    static func run(_ args: [String], sessionsRoot: URL) throws {
        var args = args
        if args.first == "apply" {
            args.removeFirst()
            try ReviewLaneApplyCommand.run(args, sessionsRoot: sessionsRoot)
            return
        }
        try build(args, sessionsRoot: sessionsRoot)
    }

    private static func build(_ args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        let explicitLane = ArgumentEditing.takeOption("lane", from: &forwarded)
        let positionalLane: String?
        if let first = forwarded.first, !first.hasPrefix("-") {
            positionalLane = first
            forwarded.removeFirst()
        } else {
            positionalLane = nil
        }
        guard let lane = explicitLane ?? positionalLane, !lane.isEmpty else {
            throw CLIError("review lane requires a lane name, for example `murmurmark review lane check_local_recall`")
        }

        let explicitOperationalReadiness = ArgumentEditing.takeOption("operational-readiness", from: &forwarded)
        let explicitPlanOutDir = ArgumentEditing.takeOption("plan-out-dir", from: &forwarded)
        let explicitOutDir = ArgumentEditing.takeOption("out-dir", from: &forwarded)
        let sessionFilter = ArgumentEditing.takeOption("session", from: &forwarded)
        let session = try sessionFilter.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }

        var operationalReadiness = explicitOperationalReadiness.map(PathURLs.fileURL)
        let planURL: URL
        let lanePackOutURL: URL

        if let session {
            let readinessRoot = session.appendingPathComponent("derived/readiness")
            let sessionQualityOut = readinessRoot.appendingPathComponent("session-quality")
            let operationalOut = readinessRoot.appendingPathComponent("operational-readiness")
            planURL = explicitPlanOutDir.map(PathURLs.fileURL) ?? readinessRoot.appendingPathComponent("review-plan")
            lanePackOutURL = explicitOutDir.map(PathURLs.fileURL) ?? planURL.appendingPathComponent("lane-packs")
            if operationalReadiness == nil {
                try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                    try script("report-session-quality.py").path,
                    session.path,
                    "--out-dir", sessionQualityOut.path,
                    "--write-session-readiness",
                ])
                try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                    try script("report-operational-readiness.py").path,
                    "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
                    "--out-dir", operationalOut.path,
                ])
                operationalReadiness = operationalOut.appendingPathComponent("operational_readiness_report.json")
            }
        } else {
            planURL = explicitPlanOutDir.map(PathURLs.fileURL) ?? PathURLs.fileURL("sessions/_reports/review-plan")
            lanePackOutURL = explicitOutDir.map(PathURLs.fileURL) ?? PathURLs.fileURL("sessions/_reports/review-plan/lane-packs")
        }

        var planArgs = ["--out-dir", planURL.path]
        if let operationalReadiness {
            planArgs += ["--operational-readiness", operationalReadiness.path]
        }
        try buildPlan(extraArgs: planArgs, refreshOperational: operationalReadiness == nil)

        var laneArgs = [
            try script("build-review-lane-pack.py").path,
            "--template", planURL.appendingPathComponent("review_decisions.template.jsonl").path,
            "--decisions", planURL.appendingPathComponent("review_decisions.jsonl").path,
            "--lane", lane,
            "--out-dir", lanePackOutURL.path,
            "--include-related-lanes",
        ]
        if let session {
            laneArgs += ["--session", session.lastPathComponent]
        }
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), laneArgs + forwarded)
        if let session {
            print("SESSION=\"\(PathDisplay.display(session))\"")
        }
        try ReviewPrinter.printLanePack(lane: lane, outDir: lanePackOutURL, session: session, planOutDir: planURL)
    }

    static func printHelp() {
        print("""
        usage: murmurmark review lane LANE [--session latest|SESSION] [--out-dir PATH]
               murmurmark review lane --lane LANE [--session latest|SESSION] [--out-dir PATH]
               murmurmark review lane apply LANE|first [--session latest|SESSION] [--answers-file PATH|--answers TEXT]
                                    [--answers-source manual|suggested]

        Refreshes the review plan, then builds one explicit review lane pack.
        Use this when you want a specific lane such as check_local_recall instead of the
        automatically recommended first lane.

        Common lanes:
          fast_confirm_drop
          check_unique_me_content
          check_local_recall
          check_transcript_order
          confirm_benign
          classify_audio

        With --session, defaults are session-local under SESSION/derived/readiness/.
        Without --session, defaults are the global corpus review plan under sessions/_reports/.

        Options:
          --operational-readiness PATH  Default: sessions/_reports/operational-readiness/operational_readiness_report.json
          --plan-out-dir PATH           Default: sessions/_reports/review-plan
          --out-dir PATH                Lane pack directory. Default: sessions/_reports/review-plan/lane-packs
          --command-key KEY             Forwarded to build-review-lane-pack.py
          --include-reviewed            Forwarded to build-review-lane-pack.py

        Apply options:
          --answers-file PATH           Default: review_lane_answers.LANE.txt from the lane pack directory
          --answers TEXT                Compact answers: d=drop_me, c=drop_remote, k=keep_me, r=needs_review, s=skip, .=todo
          --answers-source manual|suggested
                                         Use the manual answer sheet or generated suggested sheet. Default: manual
          --decisions-out PATH          Default: review_decisions.jsonl in the review plan directory
          --dry-run                     Validate answers without writing decisions

        `first` resolves to review_queue_strategy.first_recommended_lane from review_plan.json.
        """)
    }

    private static func buildPlan(extraArgs: [String], refreshOperational: Bool) throws {
        let python = try PythonRuntime.resolve()
        if refreshOperational {
            try Tooling.runPathQuiet(python, [try script("report-operational-readiness.py").path])
        }
        try Tooling.runPathQuiet(python, [try script("build-review-plan.py").path] + extraArgs)
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }

}

struct ReviewLaneApplyPrintContext {
    let lane: String
    let session: URL?
    let manifest: URL
    let template: URL
    let planURL: URL
    let lanePackOutURL: URL
    let answers: String?
    let answersFile: URL?
    let answersSource: String
    let decisions: URL
    let applyReport: URL
    let reviewer: String?
    let progress: URL?
    let dryRun: Bool
}

enum ReviewLaneApplyCommand {
    static func run(_ args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        let explicitLane = ArgumentEditing.takeOption("lane", from: &forwarded)
        let positionalLane: String?
        if let first = forwarded.first, !first.hasPrefix("-") {
            positionalLane = first
            forwarded.removeFirst()
        } else {
            positionalLane = nil
        }
        guard let requestedLane = explicitLane ?? positionalLane, !requestedLane.isEmpty else {
            throw CLIError("review lane apply requires a lane name, for example `murmurmark review lane apply check_local_recall`")
        }

        let explicitPlanOutDir = ArgumentEditing.takeOption("plan-out-dir", from: &forwarded)
        let explicitOutDir = ArgumentEditing.takeOption("out-dir", from: &forwarded)
        let explicitManifest = ArgumentEditing.takeOption("manifest", from: &forwarded)
        let explicitTemplate = ArgumentEditing.takeOption("template", from: &forwarded)
        let explicitDecisionsOut = ArgumentEditing.takeOption("decisions-out", from: &forwarded)
        let explicitAnswersFile = ArgumentEditing.takeOption("answers-file", from: &forwarded)
        let explicitAnswers = ArgumentEditing.takeOption("answers", from: &forwarded)
        let answersSource = ArgumentEditing.takeOption("answers-source", from: &forwarded) ?? "manual"
        let reviewer = ArgumentEditing.takeOption("reviewer", from: &forwarded)
        let sessionFilter = ArgumentEditing.takeOption("session", from: &forwarded)
        let dryRun = ArgumentEditing.takeFlag("dry-run", from: &forwarded)
        guard explicitAnswers == nil || explicitAnswersFile == nil else {
            throw CLIError("pass either --answers or --answers-file, not both")
        }
        guard ["manual", "suggested"].contains(answersSource) else {
            throw CLIError("review lane apply --answers-source must be manual or suggested")
        }
        guard answersSource == "manual" || (explicitAnswers == nil && explicitAnswersFile == nil) else {
            throw CLIError("review lane apply --answers-source suggested cannot be combined with --answers or --answers-file")
        }
        guard forwarded.isEmpty else {
            throw CLIError("unexpected review lane apply arguments: \(forwarded.joined(separator: " "))")
        }

        let session = try sessionFilter.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
        let planURL: URL
        let lanePackOutURL: URL
        if let session {
            planURL = explicitPlanOutDir.map(PathURLs.fileURL)
                ?? session.appendingPathComponent("derived/readiness/review-plan")
            lanePackOutURL = explicitOutDir.map(PathURLs.fileURL)
                ?? planURL.appendingPathComponent("lane-packs")
        } else {
            planURL = explicitPlanOutDir.map(PathURLs.fileURL)
                ?? PathURLs.fileURL("sessions/_reports/review-plan")
            lanePackOutURL = explicitOutDir.map(PathURLs.fileURL)
                ?? planURL.appendingPathComponent("lane-packs")
        }

        let lane = try resolveLane(requestedLane, planURL: planURL)
        let manifest = explicitManifest.map(PathURLs.fileURL)
            ?? lanePackOutURL.appendingPathComponent("review_lane_pack.\(lane).json")
        let template = explicitTemplate.map(PathURLs.fileURL)
            ?? planURL.appendingPathComponent("review_decisions.template.jsonl")
        let decisions = explicitDecisionsOut.map(PathURLs.fileURL)
            ?? planURL.appendingPathComponent("review_decisions.jsonl")
        let defaultAnswersFile = answersSource == "suggested"
            ? lanePackOutURL.appendingPathComponent("review_lane_answers.\(lane).suggested.txt")
            : lanePackOutURL.appendingPathComponent("review_lane_answers.\(lane).txt")
        let answersFile = explicitAnswers == nil
            ? (explicitAnswersFile.map(PathURLs.fileURL) ?? defaultAnswersFile)
            : nil
        let applyReport = decisions.deletingLastPathComponent().appendingPathComponent("review_lane_pack_apply_report.json")

        try validateInputs(manifest: manifest, template: template, answersFile: answersFile, lane: lane, session: session)
        try runApplyScript(LaneApplyScriptContext(
            manifest: manifest,
            template: template,
            decisions: decisions,
            answers: explicitAnswers,
            answersFile: answersFile,
            reviewer: reviewer,
            dryRun: dryRun
        ))
        let progress = dryRun ? nil : try runProgress(template: template, decisions: decisions, planURL: planURL)
        if let session {
            print("SESSION=\"\(PathDisplay.display(session))\"")
        }
        printLaneApply(ReviewLaneApplyPrintContext(
            lane: lane,
            session: session,
            manifest: manifest,
            template: template,
            planURL: planURL,
            lanePackOutURL: lanePackOutURL,
            answers: explicitAnswers,
            answersFile: answersFile,
            answersSource: answersSource,
            decisions: decisions,
            applyReport: applyReport,
            reviewer: reviewer,
            progress: progress,
            dryRun: dryRun
        ))
    }

    private static func validateInputs(
        manifest: URL,
        template: URL,
        answersFile: URL?,
        lane: String,
        session: URL?
    ) throws {
        let fileManager = FileManager.default
        guard fileManager.fileExists(atPath: manifest.path) else {
            let sessionArgument = session.map { " --session \(PathDisplay.display($0))" } ?? ""
            throw CLIError(
                "lane pack manifest not found: \(PathDisplay.display(manifest)); " +
                "build it first with `murmurmark review lane \(lane)\(sessionArgument)`"
            )
        }
        guard fileManager.fileExists(atPath: template.path) else {
            throw CLIError("review template not found: \(PathDisplay.display(template)); run `murmurmark review lane \(lane)` first")
        }
        if let answersFile {
            guard fileManager.fileExists(atPath: answersFile.path) else {
                throw CLIError("lane answers file not found: \(PathDisplay.display(answersFile)); edit the generated answer sheet or pass --answers")
            }
        }
    }

    private struct LaneApplyScriptContext {
        let manifest: URL
        let template: URL
        let decisions: URL
        let answers: String?
        let answersFile: URL?
        let reviewer: String?
        let dryRun: Bool
    }

    private static func runApplyScript(_ context: LaneApplyScriptContext) throws {
        var command = [
            try script("apply-review-lane-pack-decisions.py").path,
            context.manifest.path,
            "--template", context.template.path,
            "--out", context.decisions.path,
        ]
        if let answers = context.answers {
            command += ["--answers", answers]
        } else if let answersFile = context.answersFile {
            command += ["--answers-file", answersFile.path]
        }
        if let reviewer = context.reviewer {
            command += ["--reviewer", reviewer]
        }
        if context.dryRun {
            command.append("--dry-run")
        }
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), command)
    }

    private static func runProgress(template: URL, decisions: URL, planURL: URL) throws -> URL {
        let progress = planURL.appendingPathComponent("review_decisions_progress.json")
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("report-review-decisions-progress.py").path,
            "--template", template.path,
            "--decisions", decisions.path,
            "--out", progress.path,
            "--markdown", planURL.appendingPathComponent("review_decisions_progress.md").path,
        ])
        return progress
    }

    private static func printLaneApply(_ context: ReviewLaneApplyPrintContext) {
        print("")
        print("review_lane_apply:")
        print("  lane: \(context.lane)")
        if let session = context.session {
            print("  session: \(PathDisplay.display(session))")
        }
        print("  manifest: \(PathDisplay.display(context.manifest))")
        if let answersFile = context.answersFile {
            print("  answers: \(PathDisplay.display(answersFile))")
        }
        print("  answers_source: \(context.answersSource)")
        print("  decisions: \(PathDisplay.display(context.decisions))")
        printLaneApplyReport(context.applyReport)
        if let progress = context.progress {
            print("  progress: \(PathDisplay.display(progress))")
            printProgressSummary(progress)
        }
        print("  dry_run: \(context.dryRun)")
        let sessionArgument = context.session.map { " --session \(PathDisplay.display($0))" } ?? ""
        if context.dryRun {
            let summary = laneApplySummary(context.applyReport)
            if summary.todo > 0 || summary.reviewed == 0 {
                printIncompleteDryRunNext(context, sessionArgument: sessionArgument)
                return
            }
            let nextCommand = ReviewLaneApplyNextCommand.command(context)
            print("  recommended_next: \(nextCommand)")
            print("  next:")
            print("    \(nextCommand)")
            print("")
            print("next: \(nextCommand)")
        } else if context.progress.map(isReadyForApply) == true {
            print("  recommended_next: murmurmark review apply\(sessionArgument)")
            print("  next:")
            print("    murmurmark review apply\(sessionArgument)")
            print("")
            print("next: murmurmark review apply\(sessionArgument)")
        } else {
            let nextLane = context.progress.flatMap(firstRemainingLane)
            let recommendedNext = nextLane
                .map { "murmurmark review lane \($0)\(sessionArgument)" }
                ?? "murmurmark review workspace\(sessionArgument)"
            print("  recommended_next: \(recommendedNext)")
            if let nextLane {
                print("  next_lane: \(nextLane)")
                print("  next:")
                print("    murmurmark review lane \(nextLane)\(sessionArgument)")
                print("    murmurmark review lane apply \(nextLane)\(sessionArgument)")
            } else {
                print("  next:")
            }
            print("    murmurmark review workspace\(sessionArgument)")
            print("    murmurmark review workspace apply\(sessionArgument)")
            print("    murmurmark review progress\(sessionArgument)")
            print("  after_ready:")
            print("    murmurmark review apply\(sessionArgument)")
            print("")
            print("next: \(recommendedNext)")
        }
    }

    private static func printIncompleteDryRunNext(_ context: ReviewLaneApplyPrintContext, sessionArgument: String) {
        let nextCommand = context.answersFile
            .map { "$EDITOR \(PathDisplay.display($0))" }
            ?? "murmurmark review lane \(context.lane)\(sessionArgument)"
        let markdown = context.lanePackOutURL.appendingPathComponent("review_lane_pack.\(context.lane).md")
        let retry = ReviewLaneApplyNextCommand.command(context) + " --dry-run"
        print("  recommended_next: \(nextCommand)")
        print("  next:")
        if FileManager.default.fileExists(atPath: markdown.path) {
            print("    less \(PathDisplay.display(markdown))")
        }
        if let answersFile = context.answersFile {
            print("    $EDITOR \(PathDisplay.display(answersFile))")
        } else {
            print("    murmurmark review lane \(context.lane)\(sessionArgument)")
        }
        print("    \(retry)")
        print("")
        print("next: \(nextCommand)")
    }

    private static func printLaneApplyReport(_ report: URL) {
        let summary = laneApplySummary(report)
        guard summary.exists else { return }
        print("  report: \(PathDisplay.display(report))")
        print("  lane_items: \(summary.items)")
        print("  lane_result: reviewed=\(summary.reviewed) todo=\(summary.todo) rejected=\(summary.rejected)")
    }

    private struct LaneApplySummary {
        let exists: Bool
        let items: Int
        let reviewed: Int
        let todo: Int
        let rejected: Int
    }

    private static func laneApplySummary(_ report: URL) -> LaneApplySummary {
        guard let payload = try? JSONFiles.object(report),
              let summary = payload["summary"] as? [String: Any]
        else {
            return LaneApplySummary(exists: false, items: 0, reviewed: 0, todo: 0, rejected: 0)
        }
        return LaneApplySummary(
            exists: true,
            items: int(summary["manifest_items"]) ?? 0,
            reviewed: int(summary["reviewed_count"]) ?? 0,
            todo: int(summary["todo_count"]) ?? 0,
            rejected: int(summary["rejected_count"]) ?? 0
        )
    }

    private static func printProgressSummary(_ progress: URL) {
        guard let payload = try? JSONFiles.object(progress),
              let summary = payload["summary"] as? [String: Any]
        else {
            return
        }
        let reviewed = int(summary["reviewed"]) ?? 0
        let total = int(summary["total"]) ?? 0
        let remaining = int(summary["remaining"]) ?? 0
        let ready = bool(summary["ready_for_batch_apply"]) ?? false
        print("  reviewed: \(reviewed)/\(total)")
        print("  remaining: \(remaining)")
        if let actionCount = int(summary["action_count"]) {
            print("  review_actions: \(int(summary["reviewed_actions"]) ?? 0)/\(actionCount)")
            print("  remaining_actions: \(int(summary["remaining_actions"]) ?? 0)")
        }
        if let groupedRows = int(summary["grouped_review_row_count"]), groupedRows > 0 {
            print("  grouped_review_rows: \(groupedRows)")
        }
        print("  ready_for_apply: \(ready)")
    }

    private static func isReadyForApply(_ progress: URL) -> Bool {
        guard let payload = try? JSONFiles.object(progress),
              let summary = payload["summary"] as? [String: Any]
        else {
            return false
        }
        return bool(summary["ready_for_batch_apply"]) == true
    }

    private static func firstRemainingLane(_ progress: URL) -> String? {
        guard let payload = try? JSONFiles.object(progress),
              let lanes = payload["by_lane"] as? [[String: Any]]
        else {
            return nil
        }
        return lanes.first { (int($0["remaining"]) ?? 0) > 0 }.flatMap { $0["review_lane"] as? String }
    }

    private static func bool(_ value: Any?) -> Bool? {
        if let value = value as? Bool { return value }
        if let text = value as? String { return ["true", "yes", "1"].contains(text.lowercased()) }
        return nil
    }

    private static func int(_ value: Any?) -> Int? {
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String { return Int(text) }
        return nil
    }

    private static func resolveLane(_ value: String, planURL: URL) throws -> String {
        guard value == "first" else { return value }
        let plan = planURL.appendingPathComponent("review_plan.json")
        guard FileManager.default.fileExists(atPath: plan.path) else {
            throw CLIError(
                "review plan not found: \(PathDisplay.display(plan)); " +
                "build it first with `murmurmark review first-lane`"
            )
        }
        let payload = try JSONFiles.object(plan)
        let strategy = payload["review_queue_strategy"] as? [String: Any] ?? [:]
        guard let lane = strategy["first_recommended_lane"] as? String, !lane.isEmpty else {
            throw CLIError(
                "review plan does not contain review_queue_strategy.first_recommended_lane: " +
                "\(PathDisplay.display(plan))"
            )
        }
        return lane
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }
}

enum ReviewLaneApplyNextCommand {
    static func command(_ context: ReviewLaneApplyPrintContext) -> String {
        var parts = ["murmurmark", "review", "lane", "apply", context.lane]
        if let session = context.session {
            parts += ["--session", PathDisplay.display(session)]
        }

        let defaultPlan = context.session?.appendingPathComponent("derived/readiness/review-plan")
            ?? PathURLs.fileURL("sessions/_reports/review-plan")
        appendPathOption("plan-out-dir", context.planURL, default: defaultPlan, to: &parts)
        appendPathOption("out-dir", context.lanePackOutURL, default: context.planURL.appendingPathComponent("lane-packs"), to: &parts)
        appendPathOption(
            "manifest",
            context.manifest,
            default: context.lanePackOutURL.appendingPathComponent("review_lane_pack.\(context.lane).json"),
            to: &parts
        )
        appendPathOption(
            "template",
            context.template,
            default: context.planURL.appendingPathComponent("review_decisions.template.jsonl"),
            to: &parts
        )
        appendPathOption(
            "decisions-out",
            context.decisions,
            default: context.planURL.appendingPathComponent("review_decisions.jsonl"),
            to: &parts
        )
        if let answers = context.answers {
            parts += ["--answers", answers]
        } else if context.answersSource == "suggested" {
            parts += ["--answers-source", "suggested"]
        } else if let answersFile = context.answersFile {
            appendPathOption(
                "answers-file",
                answersFile,
                default: context.lanePackOutURL.appendingPathComponent("review_lane_answers.\(context.lane).txt"),
                to: &parts
            )
        }
        if let reviewer = context.reviewer {
            parts += ["--reviewer", reviewer]
        }
        return parts.joined(separator: " ")
    }

    private static func appendPathOption(_ name: String, _ value: URL, default defaultValue: URL, to parts: inout [String]) {
        guard !samePath(value, defaultValue) else { return }
        parts += ["--\(name)", PathDisplay.display(value)]
    }

    private static func samePath(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.standardizedFileURL.path == rhs.standardizedFileURL.path
    }
}

enum ReviewFirstLaneCommand {
    static func run(_ args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        guard !ArgumentEditing.hasOption("lane", in: forwarded) else {
            throw CLIError("review first-lane chooses --lane from review_plan.json; pass --session/--out-dir only")
        }
        let explicitOperationalReadiness = ArgumentEditing.takeOption("operational-readiness", from: &forwarded)
        let explicitPlanOutDir = ArgumentEditing.takeOption("plan-out-dir", from: &forwarded)
        let explicitOutDir = ArgumentEditing.takeOption("out-dir", from: &forwarded)
        let sessionFilter = ArgumentEditing.peekOption("session", in: forwarded)
        let session = try sessionFilter.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }

        var operationalReadiness = explicitOperationalReadiness.map(PathURLs.fileURL)
        let planURL: URL
        let lanePackOutURL: URL

        if let session {
            let readinessRoot = session.appendingPathComponent("derived/readiness")
            let sessionQualityOut = readinessRoot.appendingPathComponent("session-quality")
            let operationalOut = readinessRoot.appendingPathComponent("operational-readiness")
            planURL = explicitPlanOutDir.map(PathURLs.fileURL) ?? readinessRoot.appendingPathComponent("review-plan")
            lanePackOutURL = explicitOutDir.map(PathURLs.fileURL) ?? planURL.appendingPathComponent("lane-packs")
            if operationalReadiness == nil {
                try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                    try script("report-session-quality.py").path,
                    session.path,
                    "--out-dir", sessionQualityOut.path,
                    "--write-session-readiness",
                ])
                try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
                    try script("report-operational-readiness.py").path,
                    "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
                    "--out-dir", operationalOut.path,
                ])
                operationalReadiness = operationalOut.appendingPathComponent("operational_readiness_report.json")
            }
        } else {
            planURL = explicitPlanOutDir.map(PathURLs.fileURL) ?? PathURLs.fileURL("sessions/_reports/review-plan")
            lanePackOutURL = explicitOutDir.map(PathURLs.fileURL) ?? PathURLs.fileURL("sessions/_reports/review-plan/lane-packs")
        }

        var planArgs = ["--out-dir", planURL.path]
        if let operationalReadiness {
            planArgs += ["--operational-readiness", operationalReadiness.path]
        }
        try buildPlan(extraArgs: planArgs, refreshOperational: operationalReadiness == nil)
        let lane = try firstRecommendedLane(planOutDir: planURL)
        if let session {
            replaceSessionFilter(in: &forwarded, with: session.lastPathComponent)
        } else {
            try rewriteLatestSessionFilters(in: &forwarded, sessionsRoot: sessionsRoot)
        }
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("build-review-lane-pack.py").path,
            "--template", planURL.appendingPathComponent("review_decisions.template.jsonl").path,
            "--decisions", planURL.appendingPathComponent("review_decisions.jsonl").path,
            "--lane", lane,
            "--out-dir", lanePackOutURL.path,
            "--include-related-lanes",
        ] + forwarded)
        if let session {
            print("SESSION=\"\(PathDisplay.display(session))\"")
        }
        try ReviewPrinter.printLanePack(lane: lane, outDir: lanePackOutURL, session: session, planOutDir: planURL)
    }

    static func printHelp() {
        print("""
        usage: murmurmark review first-lane [--session latest|SESSION] [--out-dir PATH]

        Refreshes the review plan, reads review_queue_strategy.first_recommended_lane,
        then builds one review lane pack for that lane.

        With --session, defaults are session-local under SESSION/derived/readiness/.
        Without --session, defaults are the global corpus review plan under sessions/_reports/.

        Options:
          --operational-readiness PATH  Default: sessions/_reports/operational-readiness/operational_readiness_report.json
          --plan-out-dir PATH           Default: sessions/_reports/review-plan
          --out-dir PATH                Lane pack directory. Default: sessions/_reports/review-plan/lane-packs

        Useful next step after:
          murmurmark corpus report
          murmurmark review next SESSION

        Use `murmurmark review lane check_local_recall --session SESSION` to build a specific lane.
        """)
    }

    private static func buildPlan(extraArgs: [String], refreshOperational: Bool) throws {
        let python = try PythonRuntime.resolve()
        if refreshOperational {
            try Tooling.runPathQuiet(python, [try script("report-operational-readiness.py").path])
        }
        try Tooling.runPathQuiet(python, [try script("build-review-plan.py").path] + extraArgs)
    }

    private static func rewriteLatestSessionFilters(in args: inout [String], sessionsRoot: URL) throws {
        var index = 0
        while index < args.count {
            if args[index] == "--session", index + 1 < args.count, args[index + 1] == "latest" {
                args[index + 1] = try SessionResolver.latest(in: sessionsRoot).lastPathComponent
                index += 2
            } else {
                index += 1
            }
        }
    }

    private static func replaceSessionFilter(in args: inout [String], with value: String) {
        var index = 0
        while index < args.count {
            if args[index] == "--session", index + 1 < args.count {
                args[index + 1] = value
                index += 2
            } else {
                index += 1
            }
        }
    }

    private static func firstRecommendedLane(planOutDir: URL) throws -> String {
        let plan = try JSONFiles.object(planOutDir.appendingPathComponent("review_plan.json"))
        let strategy = plan["review_queue_strategy"] as? [String: Any] ?? [:]
        if let lane = strategy["first_recommended_lane"] as? String, !lane.isEmpty {
            return lane
        }
        let summary = plan["summary"] as? [String: Any] ?? [:]
        let lanes = summary["by_review_lane"] as? [String: Any] ?? [:]
        let fallbackOrder = [
            "fast_confirm_drop",
            "check_unique_me_content",
            "check_local_recall",
            "check_transcript_order",
            "confirm_benign",
            "classify_audio",
        ]
        for lane in fallbackOrder {
            if let count = lanes[lane] as? NSNumber, count.intValue > 0 {
                return lane
            }
        }
        return "fast_confirm_drop"
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("review script not found: \(url.path)")
        }
        return url
    }
}

enum AuditCommands {
    static func audit(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let subcommand = remaining.removeFirst()
        guard let target = remaining.first else {
            throw CLIError("audit \(subcommand) requires a session path or latest")
        }
        remaining.removeFirst()

        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let python = try PythonRuntime.resolve()

        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)

        switch subcommand {
        case "local-recall":
            try Tooling.runPathQuiet(python, [try script("audit-local-recall.py").path, session.path] + remaining)
            try AuditPrinter.printLocalRecall(session: session, args: remaining)
        case "order":
            try Tooling.runPathQuiet(python, [try script("audit-transcript-order.py").path, session.path] + remaining)
            try AuditPrinter.printOrder(session: session, args: remaining)
        case "group-overlaps":
            try Tooling.runPathQuiet(python, [try script("audit-group-overlaps.py").path, session.path] + remaining)
            try AuditPrinter.printGroupOverlaps(session: session)
        case "audio-review":
            let packArgs = defaulted(remaining, option: "profile", value: "audit_cleanup_v2")
            let packDir = ArgumentEditing.peekOption("out-dir", in: packArgs)
            try Tooling.runPathQuiet(python, [try script("build-audio-review-pack.py").path, session.path] + packArgs)
            var auditArgs = [try script("audit-audio-review-pack.py").path, session.path]
            if let packDir {
                auditArgs += ["--pack-dir", packDir, "--out-dir", packDir]
            }
            try Tooling.runPathQuiet(python, auditArgs)
            try AuditPrinter.printAudioReview(session: session, args: packArgs)
        case "stronger-audio-judge", "stronger_audio_judge":
            let targetsExistingPack = ArgumentEditing.hasOption("pack-dir", in: remaining)
                || ArgumentEditing.hasOption("review-lane-pack", in: remaining)
                || ArgumentEditing.hasOption("pack-item-id", in: remaining)
            let packArgs = defaulted(packBuilderArgs(from: remaining), option: "profile", value: "audit_cleanup_v2")
            let packDir = ArgumentEditing.peekOption("out-dir", in: packArgs)
            if !targetsExistingPack {
                try Tooling.runPathQuiet(python, [try script("build-audio-review-pack.py").path, session.path] + packArgs)
            }
            var auditArgs = [try script("audit-stronger-audio-judge.py").path, session.path] + remaining
            if let packDir, !ArgumentEditing.hasOption("pack-dir", in: auditArgs) {
                auditArgs += ["--pack-dir", packDir]
            }
            try Tooling.runPath(python, auditArgs)
            try AuditPrinter.printStrongerAudioJudge(session: session, args: auditArgs)
        case "target-me", "target_me":
            var auditArgs = [try script("audit-target-me.py").path, session.path] + remaining
            if !ArgumentEditing.hasOption("profile", in: auditArgs) {
                auditArgs += ["--profile", "auto"]
            }
            try Tooling.runPath(python, auditArgs)
            try AuditPrinter.printTargetMe(session: session, args: auditArgs)
        case "asr-positive-echo-candidate", "asr_positive_echo_candidate", "echo-candidate", "echo_candidate":
            if !ArgumentEditing.hasOption("candidate", in: remaining) {
                remaining += ["--candidate", "coverage_v2_remote_gate_local_fir"]
            }
            try Tooling.runPath(python, [try script("run-asr-positive-echo-candidate.py").path, session.path] + remaining)
            try AuditPrinter.printASRPositiveEchoCandidate(session: session)
        case "remote-forbidden", "remote_forbidden":
            var forwarded = remaining
            let skipLab = ArgumentEditing.takeFlag("skip-lab", from: &forwarded)
            let profile = ArgumentEditing.takeOption("profile", from: &forwarded) ?? "auto"
            let outDir = ArgumentEditing.takeOption("out-dir", from: &forwarded)
            if !skipLab {
                var labArgs = [try script("echo-guard-offline-aec-v2-lab.py").path, session.path] + forwarded
                if !ArgumentEditing.hasOption("asr-audit", in: labArgs) {
                    labArgs.append("--asr-audit")
                }
                if !ArgumentEditing.hasOption("asr-max-clips", in: labArgs) {
                    labArgs += ["--asr-max-clips", "2"]
                }
                if !ArgumentEditing.hasOption("asr-max-local-clips", in: labArgs) {
                    labArgs += ["--asr-max-local-clips", "1"]
                }
                if !ArgumentEditing.hasOption("asr-max-risk-clips", in: labArgs) {
                    labArgs += ["--asr-max-risk-clips", "2"]
                }
                if !ArgumentEditing.hasOption("asr-candidate-keys", in: labArgs) {
                    labArgs += [
                        "--asr-candidate-keys",
                        "segment_switch_remote_floor_local_fir",
                        "coverage_v2_remote_gate_local_fir"
                    ]
                }
                try Tooling.runPath(python, labArgs)
            }
            var evidenceArgs = [try script("harden-remote-forbidden-evidence.py").path, session.path, "--profile", profile]
            if let outDir {
                evidenceArgs += ["--out-dir", outDir]
            }
            try Tooling.runPath(python, evidenceArgs)
            try AuditPrinter.printRemoteForbidden(session: session, args: evidenceArgs)
        default:
            throw CLIError("unknown audit command: \(subcommand)")
        }
    }

    private static func defaulted(_ args: [String], option: String, value: String) -> [String] {
        if ArgumentEditing.hasOption(option, in: args) {
            return args
        }
        return ["--\(option)", value] + args
    }

    private static func packBuilderArgs(from args: [String]) -> [String] {
        let valueOptions: Set<String> = ["profile", "out-dir", "min-overlap-sec", "padding-sec", "max-items"]
        let flags: Set<String> = ["write-clips", "no-write-clips"]
        let skipValueOptions: Set<String> = [
            "model",
            "device",
            "compute-type",
            "language",
            "beam-size",
            "max-computed-items",
            "pack-dir",
            "source",
            "pack-item-id",
            "review-lane-pack",
        ]
        let skipFlags: Set<String> = ["allow-download", "quick", "progress", "no-progress", "no-cache"]
        var filtered: [String] = []
        var index = 0
        while index < args.count {
            let arg = args[index]
            guard arg.hasPrefix("--") else {
                index += 1
                continue
            }
            let name = String(arg.dropFirst(2))
            if valueOptions.contains(name) {
                filtered.append(arg)
                if index + 1 < args.count, !args[index + 1].hasPrefix("--") {
                    filtered.append(args[index + 1])
                    index += 2
                } else {
                    index += 1
                }
            } else if flags.contains(name) {
                filtered.append(arg)
                index += 1
            } else if skipValueOptions.contains(name) {
                index += 1
                if index < args.count, !args[index].hasPrefix("--") {
                    index += 1
                }
            } else if skipFlags.contains(name) {
                index += 1
            } else {
                index += 1
            }
        }
        return filtered
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("audit script not found: \(url.path)")
        }
        return url
    }

    private static func printHelp() {
        print("""
        usage:
          murmurmark audit local-recall ./session|latest [--profile shadow_v2] [--sessions-root ./sessions]
          murmurmark audit order ./session|latest [--profile auto] [--sessions-root ./sessions]
          murmurmark audit group-overlaps ./session|latest [--profile shadow_v2] [--write-clips] [--sessions-root ./sessions]
          murmurmark audit audio-review ./session|latest [--profile audit_cleanup_v2] [--write-clips] [--sessions-root ./sessions]
          murmurmark audit stronger-audio-judge ./session|latest [--profile audit_cleanup_v2] [--max-items 80]
                                             [--quick] [--max-computed-items N] [--sessions-root ./sessions]
          murmurmark audit target-me ./session|latest [--profile auto] [--max-items 80] [--sessions-root ./sessions]
          murmurmark audit asr-positive-echo-candidate ./session|latest
                                             [--candidate coverage_v2_remote_gate_local_fir]
                                             [--skip-lab] [--sessions-root ./sessions]
          murmurmark audit remote-forbidden ./session|latest [--profile auto]
                                             [--asr-window-profile coverage_v2] [--asr-max-clips 2]
                                             [--asr-max-risk-clips 2] [--asr-max-local-clips 1]
                                             [--asr-candidate-keys segment_switch_remote_floor_local_fir
                                              coverage_v2_remote_gate_local_fir]
                                             [--skip-lab]

        Audit commands are local-only wrappers over existing Python scripts:
          local-recall    runs audit-local-recall.py
          order           runs audit-transcript-order.py
          group-overlaps  runs audit-group-overlaps.py
          audio-review    runs build-audio-review-pack.py, then audit-audio-review-pack.py
          stronger-audio-judge
                          runs build-audio-review-pack.py, then audit-stronger-audio-judge.py
                          with --review-lane-pack or --pack-item-id, reuses the existing audio-review pack
          target-me       runs audit-target-me.py as a shadow voice-evidence layer
          asr-positive-echo-candidate
                          runs/reuses offline_aec_v2 and writes an explicit shadow audio-candidate report
          remote-forbidden
                          runs offline_aec_v2 ASR audit, then materializes remote-forbidden
                          evidence rows and a review report

        Use --sessions-root when resolving latest from a non-default sessions directory.
        Extra options are forwarded to the underlying audit script; for audio-review they are
        forwarded to the pack builder, then the pack audit runs over the resulting directory.
        stronger-audio-judge forwards pack-compatible options to the pack builder and faster-whisper
        options to the stronger judge.
        """)
    }
}

enum FinalNextPrinter {
    static func print(_ command: String) {
        Swift.print("")
        Swift.print("next: \(command)")
    }
}

enum AuditPrinter {
    static func printLocalRecall(session: URL, args: [String]) throws {
        let outDir = outputDir(args: args, defaultURL: session.appendingPathComponent("derived/audit/local-recall"))
        let auditURL = outDir.appendingPathComponent("local_recall_audit.json")
        guard FileManager.default.fileExists(atPath: auditURL.path) else {
            printMissing(kind: "local_recall", expected: auditURL)
            return
        }
        let payload = try JSONFiles.object(auditURL)
        let summary = dict(payload["summary"])

        print("")
        print("audit:")
        print("  kind: local_recall")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("local_recall_review.md")))")
        print("  profile: \(string(payload["profile"]) ?? "unknown")")
        if let dialogueProfile = payload["dialogue_profile"] as? String {
            print("  dialogue_profile: \(dialogueProfile)")
        }
        print("  missing_islands: \(int(summary["audited_missing_island_count"]))")
        print(
            String(
                format: "  independent_live_me_evidence: %d / %.2fs",
                int(summary["independent_live_me_evidence_count"]),
                double(summary["independent_live_me_evidence_seconds"])
            )
        )
        print(
            String(
                format: "  possible_lost_me: %d / %.2fs",
                int(summary["possible_lost_me_count"]),
                double(summary["possible_lost_me_seconds"])
            )
        )
        print(
            String(
                format: "  needs_review: %d / %.2fs",
                int(summary["needs_review_count"]),
                double(summary["needs_review_seconds"])
            )
        )
        print("  recommendation: \(string(summary["recommended_next_step"]) ?? "unknown")")
        let needsReview = int(summary["possible_lost_me_count"]) > 0 || int(summary["needs_review_count"]) > 0
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("local_recall_review.md"),
            needsReview: needsReview
        )
    }

    static func printGroupOverlaps(session: URL) throws {
        let outDir = session.appendingPathComponent("derived/audit/group-overlaps")
        let summaryURL = outDir.appendingPathComponent("group_overlap_summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path) else {
            printMissing(kind: "group_overlaps", expected: summaryURL)
            return
        }
        let payload = try JSONFiles.object(summaryURL)
        let classified = dict(payload["classified"])
        let harmful = dict(payload["harmful"])
        let benign = dict(payload["benign_or_expected"])
        let review = dict(payload["review"])
        let adjustment = dict(payload["recommended_verdict_adjustment"])

        print("")
        print("audit:")
        print("  kind: group_overlaps")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("group_overlap_review.md")))")
        print("  profile: \(string(payload["profile"]) ?? "unknown")")
        print(
            String(
                format: "  overlaps: %d / %.2fs",
                int(classified["total_overlap_count"]),
                double(classified["total_overlap_seconds"])
            )
        )
        print(String(format: "  harmful: %.2fs", double(harmful["seconds"])))
        print(String(format: "  benign_or_expected: %.2fs", double(benign["seconds"])))
        print(String(format: "  needs_review: %d / %.2fs", int(review["count"]), double(review["seconds"])))
        if let verdict = string(adjustment["new"]) {
            print("  recommended_verdict: \(verdict) (informational)")
        }
        let needsReview = int(review["count"]) > 0 || double(harmful["seconds"]) > 0
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("group_overlap_review.md"),
            needsReview: needsReview
        )
    }

    static func printOrder(session: URL, args: [String]) throws {
        let outDir = outputDir(args: args, defaultURL: session.appendingPathComponent("derived/audit/order"))
        let auditURL = outDir.appendingPathComponent("transcript_order_audit.json")
        guard FileManager.default.fileExists(atPath: auditURL.path) else {
            printMissing(kind: "transcript_order", expected: auditURL)
            return
        }
        let payload = try JSONFiles.object(auditURL)
        let summary = dict(payload["summary"])

        print("")
        print("audit:")
        print("  kind: transcript_order")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("transcript_order_review.md")))")
        print("  profile: \(string(payload["profile"]) ?? "unknown")")
        print("  overlaps: \(int(summary["audited_overlap_count"]))")
        print(
            String(
                format: "  probable_order_risk: %d / %.2fs",
                int(summary["probable_order_risk_count"]),
                double(summary["probable_order_risk_seconds"])
            )
        )
        print(
            String(
                format: "  needs_review: %d / %.2fs",
                int(summary["needs_review_count"]),
                double(summary["needs_review_seconds"])
            )
        )
        print("  recommendation: \(string(summary["recommended_next_step"]) ?? "unknown")")
        let needsReview = int(summary["probable_order_risk_count"]) > 0 || int(summary["needs_review_count"]) > 0
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("transcript_order_review.md"),
            needsReview: needsReview
        )
    }

    static func printAudioReview(session: URL, args: [String]) throws {
        let outDir = outputDir(args: args, defaultURL: session.appendingPathComponent("derived/audit/audio-review-pack"))
        let summaryURL = outDir.appendingPathComponent("audio_review_summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path) else {
            printMissing(kind: "audio_review", expected: summaryURL)
            return
        }
        let payload = try JSONFiles.object(summaryURL)
        let pack = dict(payload["input_pack"])
        let probable = dict(payload["probable_error"])
        let stronger = dict(payload["needs_stronger_audio_judge"])
        let reliable = dict(payload["likely_reliable"])

        print("")
        print("audit:")
        print("  kind: audio_review")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("audio_review_report.md")))")
        print("  profile: \(string(pack["profile"]) ?? "unknown")")
        print("  items: \(int(payload["items"]))")
        print(String(format: "  probable_error: %d / %.2fs", int(probable["count"]), double(probable["seconds"])))
        print(String(format: "  likely_reliable: %d / %.2fs", int(reliable["count"]), double(reliable["seconds"])))
        print(String(format: "  needs_stronger_audio_judge: %d / %.2fs", int(stronger["count"]), double(stronger["seconds"])))
        print("  recommendation: \(string(payload["recommended_next_step"]) ?? "unknown")")
        let needsReview = int(probable["count"]) > 0 || int(stronger["count"]) > 0
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("audio_review_report.md"),
            needsReview: needsReview
        )
    }

    static func printStrongerAudioJudge(session: URL, args: [String]) throws {
        let defaultURL = session.appendingPathComponent("derived/audit/audio-review-pack")
        let outDir = PathURLs.fileURL(
            ArgumentEditing.peekOption("out-dir", in: args)
                ?? ArgumentEditing.peekOption("pack-dir", in: args)
                ?? defaultURL.path
        )
        let summaryURL = outDir.appendingPathComponent("faster_whisper_judge_summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path) else {
            printMissing(kind: "stronger_audio_judge", expected: summaryURL)
            return
        }
        let payload = try JSONFiles.object(summaryURL)
        let keepSeconds = double(payload["suggested_keep_me_seconds"])
        let dropSeconds = double(payload["suggested_drop_me_seconds"])

        print("")
        print("audit:")
        print("  kind: stronger_audio_judge")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("faster_whisper_judge_report.md")))")
        print("  items: \(int(payload["items"]))")
        print(String(format: "  suggested_keep_me: %.2fs", keepSeconds))
        print(String(format: "  suggested_drop_me: %.2fs", dropSeconds))
        if let skipped = string(payload["skipped_reason"]), !skipped.isEmpty {
            print("  skipped: \(skipped)")
        }
        print("  recommendation: \(string(payload["recommended_next_step"]) ?? "unknown")")
        let needsReview = int(payload["items"]) > 0
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("faster_whisper_judge_report.md"),
            needsReview: needsReview
        )
    }

    static func printTargetMe(session: URL, args: [String]) throws {
        let outDirName = ArgumentEditing.peekOption("out-dir-name", in: args) ?? "target-me"
        let outDir = session.appendingPathComponent("derived/audit/\(outDirName)")
        let summaryURL = outDir.appendingPathComponent("target_me_summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path) else {
            printMissing(kind: "target_me", expected: summaryURL)
            return
        }
        let payload = try JSONFiles.object(summaryURL)
        let enrollment = dict(payload["enrollment"])

        print("")
        print("audit:")
        print("  kind: target_me")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("target_me_report.md")))")
        print("  status: \(string(payload["status"]) ?? "unknown")")
        print("  profile: \(string(payload["profile"]) ?? "unknown")")
        print("  method: \(string(payload["method"]) ?? "unknown")")
        print("  enrollment_segments: \(int(enrollment["accepted_count"]))")
        print(String(format: "  enrollment_seconds: %.2fs", double(enrollment["accepted_total_sec"])))
        print("  items: \(int(payload["items"]))")
        print(String(format: "  helpful_seconds: %.2fs", double(payload["target_me_helpful_seconds"])))
        print(String(format: "  corroborating_seconds: %.2fs", double(payload["target_me_corroborating_seconds"])))
        print("  promotion: \(string(payload["promotion_decision"]) ?? "shadow_only_do_not_promote")")
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("target_me_report.md"),
            needsReview: false
        )
    }

    static func printASRPositiveEchoCandidate(session: URL) throws {
        let outDir = session.appendingPathComponent("derived/preprocess/echo")
        let reportURL = outDir.appendingPathComponent("asr_positive_echo_candidate_report.json")
        guard FileManager.default.fileExists(atPath: reportURL.path) else {
            printMissing(kind: "asr_positive_echo_candidate", expected: reportURL)
            return
        }
        let payload = try JSONFiles.object(reportURL)
        let assessment = dict(payload["assessment"])
        let metrics = dict(payload["metrics"])
        let coverage = dict(metrics["coverage_gate"])

        print("")
        print("audit:")
        print("  kind: asr_positive_echo_candidate")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("asr_positive_echo_candidate_report.md")))")
        print("  profile: \(string(payload["profile"]) ?? "unknown")")
        print("  mode: \(string(payload["mode"]) ?? "unknown")")
        print("  assessment: \(string(assessment["status"]) ?? "unknown")")
        print("  reason: \(string(assessment["reason"]) ?? "unknown")")
        print("  promotion: \(string(payload["promotion_decision"]) ?? "shadow_only_do_not_promote")")
        print(String(format: "  remote_token_leak_delta: %.6f", double(metrics["remote_token_leak_delta"])))
        print(String(format: "  local_word_recall_delta: %.6f", double(metrics["local_word_recall_delta"])))
        print("  coverage_windows: \(int(coverage["windows"]))")
        print("  coverage_applied_windows: \(int(coverage["applied_windows"]))")
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("asr_positive_echo_candidate_report.md"),
            needsReview: false
        )
    }

    static func printRemoteForbidden(session: URL, args: [String]) throws {
        let defaultURL = session.appendingPathComponent("derived/audit/remote-forbidden")
        let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: args) ?? defaultURL.path)
        let summaryURL = outDir.appendingPathComponent("remote_forbidden_summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path) else {
            printMissing(kind: "remote_forbidden", expected: summaryURL)
            return
        }
        let payload = try JSONFiles.object(summaryURL)
        let metrics = dict(payload["metrics"])
        let gates = dict(payload["gates"])

        print("")
        print("audit:")
        print("  kind: remote_forbidden")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("remote_forbidden_review.md")))")
        print("  status: \(string(payload["status"]) ?? "unknown")")
        print("  gate: \((gates["passed"] as? Bool) == true ? "passed" : "not_passed")")
        print("  gate_reason: \(string(gates["reason"]) ?? "unknown")")
        print("  remote_rows: \(int(metrics["remote_forbidden_rows"]))")
        print("  local_gate_rows: \(int(metrics["local_speech_gate_rows"]))")
        print(
            "  asr_windows: selected=\(int(metrics["asr_windows_selected"])) " +
                "evaluable=\(int(metrics["asr_windows_evaluable"])) " +
                "skipped=\(int(metrics["asr_windows_skipped"]))"
        )
        print(String(format: "  remote_token_leak_delta: %.6f", double(metrics["remote_token_leak_delta"])))
        print(String(format: "  local_word_recall_delta: %.6f", double(metrics["local_word_recall_delta"])))
        if let candidate = string(metrics["asr_selected_audio_candidate"]) {
            print("  asr_audio_candidate: \(candidate)")
            print("  asr_audio_candidate_gate: \(string(metrics["asr_audio_candidate_gate_reason"]) ?? "unknown")")
            print(
                String(
                    format: "  asr_audio_candidate_remote_token_leak_delta: %.6f",
                    double(metrics["audio_candidate_remote_token_leak_delta"])
                )
            )
            print(
                String(
                    format: "  asr_audio_candidate_local_word_recall_delta: %.6f",
                    double(metrics["audio_candidate_local_word_recall_delta"])
                )
            )
        }
        print(String(format: "  suggest_drop_seconds: %.2fs", double(metrics["suggest_drop_seconds"])))
        print(String(format: "  quarantine_seconds: %.2fs", double(metrics["quarantine_seconds"])))
        print(String(format: "  needs_review_seconds: %.2fs", double(metrics["needs_review_seconds"])))
        printAuditHandoff(
            session: session,
            report: outDir.appendingPathComponent("remote_forbidden_review.md"),
            needsReview: false
        )
    }

    private static func outputDir(args: [String], defaultURL: URL) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: args) ?? defaultURL.path)
    }

    private static func printMissing(kind: String, expected: URL) {
        print("")
        print("audit:")
        print("  kind: \(kind)")
        print("  summary: missing")
        print("  expected: \(PathDisplay.display(expected))")
    }

    private static func printAuditHandoff(session: URL, report: URL, needsReview: Bool) {
        let sessionPath = PathDisplay.display(session)
        let readCommand = "less \(PathDisplay.display(report))"
        let actionCommand = needsReview
            ? "murmurmark review next \(sessionPath)"
            : "murmurmark report \(sessionPath)"
        print("  read: \(readCommand)")
        print("  recommended_next: \(actionCommand)")
        print("  next:")
        print("    \(readCommand)")
        print("    \(actionCommand)")
        FinalNextPrinter.print(actionCommand)
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func int(_ value: Any?) -> Int {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String, let parsed = Int(value) {
            return parsed
        }
        return 0
    }

    private static func double(_ value: Any?) -> Double {
        if let value = value as? Double {
            return value
        }
        if let value = value as? NSNumber {
            return value.doubleValue
        }
        if let value = value as? String, let parsed = Double(value) {
            return parsed
        }
        return 0
    }
}

enum CleanupCommands {
    static func cleanup(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let target = remaining.removeFirst()
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)

        let status = try Tooling.runPathQuietAllowingExitCodes(
            try PythonRuntime.resolve(),
            [try script().path, session.path] + remaining,
            allowedExitCodes: [0, 2]
        )
        try CleanupPrinter.printSummary(session: session, args: remaining)
        if status != 0 {
            throw CLIError("cleanup gates did not pass; inspect audit_cleanup_report before promoting the profile")
        }
    }

    private static func script() throws -> URL {
        let url = PathURLs.fileURL("scripts/apply-audit-cleanup.py")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("cleanup script not found: \(url.path)")
        }
        return url
    }

    private static func printHelp() {
        print("""
        usage: murmurmark cleanup ./session|latest [--input-profile shadow_v2] [--output-profile audit_cleanup_v1]
                                     [--mode conservative] [--sessions-root ./sessions]

        Runs scripts/apply-audit-cleanup.py and writes a separate transcript profile.
        If cleanup gates fail, artifacts are still written and summarized, but the command exits non-zero.
        """)
    }
}

enum RepairCommands {
    static func repair(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let subcommand = remaining.removeFirst()
        guard !remaining.isEmpty else {
            throw CLIError("repair \(subcommand) requires a session path or latest")
        }
        let target = remaining.removeFirst()
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)

        switch subcommand {
        case "order":
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [try script("apply-transcript-order-repair.py").path, session.path] + remaining,
                allowedExitCodes: [0, 2]
            )
            try RepairPrinter.printOrderSummary(session: session, args: remaining)
            if status != 0 {
                throw CLIError("order repair gates did not pass; inspect transcript_order_repair_report before promoting the profile")
            }
        case "local-recall":
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [try script("apply-local-recall-repair.py").path, session.path] + remaining,
                allowedExitCodes: [0, 2]
            )
            try RepairPrinter.printLocalRecallSummary(session: session, args: remaining)
            if status != 0 {
                throw CLIError("local-recall repair gates did not pass; inspect local_recall_repair_report before promoting the profile")
            }
        case "boundary":
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [
                    try script("authoritative-boundary.py").path,
                    "apply",
                    session.path,
                    "--sessions-root",
                    sessionsRoot.path,
                ] + remaining,
                allowedExitCodes: [0, 2]
            )
            try RepairPrinter.printBoundarySummary(session: session)
            if status != 0 {
                throw CLIError("boundary repair gates did not pass; the frozen input remains authoritative")
            }
        case "remote-leak":
            try Tooling.runPath(
                try PythonRuntime.resolve(),
                [try script("plan-remote-leak-segment-repair.py").path, session.path] + remaining
            )
            try RepairPrinter.printRemoteLeakSummary(session: session, args: remaining)
        default:
            throw CLIError("unknown repair command: \(subcommand)")
        }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("repair script not found: \(url.path)")
        }
        return url
    }

    private static func printHelp() {
        print("""
        usage:
          murmurmark repair order ./session|latest [--input-profile auto] [--output-profile order_repair_v1]
                                [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair local-recall ./session|latest [--input-profile auto] [--output-profile local_recall_repair_v1]
                                       [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair boundary ./session|latest [--sessions-root ./sessions]
          murmurmark repair remote-leak ./session|latest [--sessions-root ./sessions]

        order writes a separate transcript profile with conservative transcript-order repairs.
        local-recall writes a separate transcript profile with conservative inserted Me islands.
        boundary applies frozen evidence dispositions and writes authoritative_boundary_v1.
        remote-leak writes an audit-only leak/duplicate segment repair plan and never edits transcript profiles.
        """)
    }
}

enum SynthesisCommands {
    static func synthesize(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let target = remaining.removeFirst()
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [try script().path, session.path] + remaining)
        try SynthesisPrinter.printSummary(session: session)
    }

    private static func script() throws -> URL {
        let url = PathURLs.fileURL("scripts/synthesize-simple-extractive.py")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("synthesis script not found: \(url.path)")
        }
        return url
    }

    private static func printHelp() {
        print("""
        usage: murmurmark synthesize ./session|latest [--transcript-profile auto] [--sessions-root ./sessions]

        Runs scripts/synthesize-simple-extractive.py and refreshes deterministic notes,
        evidence_notes and quality_verdict under derived/synthesis-simple/extractive/.
        """)
    }
}

enum AuthoritativeHandoffState {
    static func payload(_ session: URL) -> [String: Any]? {
        let url = session.appendingPathComponent("derived/pipeline-run/authoritative_handoff.json")
        guard FileManager.default.fileExists(atPath: url.path),
              let payload = try? JSONFiles.object(url),
              payload["schema"] as? String == "murmurmark.authoritative_handoff/v1",
              let status = payload["status"] as? String,
              ["ready", "review_required"].contains(status),
              fingerprintMatches(payload, session: session),
              readinessProfileMatches(payload, session: session)
        else {
            return nil
        }
        return payload
    }

    static func selectedProfile(_ session: URL) -> String? {
        payload(session)?["selected_transcript_profile"] as? String
    }

    static func artifact(_ key: String, session: URL) -> URL? {
        guard let paths = payload(session)?["paths"] as? [String: Any],
              let raw = paths[key] as? String,
              !raw.isEmpty
        else {
            return nil
        }
        let url = raw.hasPrefix("/") ? URL(fileURLWithPath: raw) : session.appendingPathComponent(raw)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private static func fingerprintMatches(_ payload: [String: Any], session: URL) -> Bool {
        guard let fingerprint = payload["transcript_fingerprint"] as? [String: Any],
              let rawPath = fingerprint["path"] as? String,
              let expectedSHA = fingerprint["sha256"] as? String,
              !rawPath.isEmpty,
              !expectedSHA.isEmpty,
              let paths = payload["paths"] as? [String: Any],
              paths["transcript"] as? String == rawPath
        else {
            return false
        }
        let url = rawPath.hasPrefix("/")
            ? URL(fileURLWithPath: rawPath)
            : session.appendingPathComponent(rawPath)
        guard FileManager.default.fileExists(atPath: url.path),
              let data = try? Data(contentsOf: url)
        else {
            return false
        }
        if let expectedSize = fingerprint["size"] as? Int, expectedSize != data.count {
            return false
        }
        let actualSHA = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        return actualSHA == expectedSHA
    }

    private static func readinessProfileMatches(_ payload: [String: Any], session: URL) -> Bool {
        guard let expectedProfile = payload["selected_transcript_profile"] as? String,
              !expectedProfile.isEmpty,
              let paths = payload["paths"] as? [String: Any],
              let transcriptPath = paths["transcript"] as? String
        else {
            return false
        }
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard let readiness = try? JSONFiles.object(readinessURL),
              readiness["selected_profile"] as? String == expectedProfile,
              let outputs = readiness["outputs"] as? [String: Any],
              let transcript = outputs["transcript"] as? [String: Any],
              transcript["path"] as? String == transcriptPath
        else {
            return false
        }
        return true
    }
}

enum NotesCommands {
    static func notes(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let target = remaining.removeFirst()
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let kind = ArgumentEditing.takeOption("kind", from: &remaining) ?? "notes"
        let profile = ArgumentEditing.takeOption("profile", from: &remaining) ?? "auto"
        let pathOnly = ArgumentEditing.takeFlag("path-only", from: &remaining)
        let cat = ArgumentEditing.takeFlag("cat", from: &remaining)
        guard remaining.isEmpty else {
            throw CLIError("unknown notes option(s): \(remaining.joined(separator: " "))")
        }

        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let resolvedProfile = try selectedProfile(profile, session: session)
        var paths = artifactPaths(session: session, profile: resolvedProfile)
        if profile == "auto" {
            if let notes = AuthoritativeHandoffState.artifact("notes", session: session) {
                paths["notes"] = notes
            }
            if let verdict = AuthoritativeHandoffState.artifact("verdict", session: session) {
                paths["verdict"] = verdict
            }
        }
        guard let url = paths[kind] else {
            throw CLIError("unknown notes kind: \(kind)")
        }
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("notes artifact not found: \(PathDisplay.display(url)); run `murmurmark synthesize \(PathDisplay.display(session))`")
        }

        if cat {
            let data = try Data(contentsOf: url)
            FileHandle.standardOutput.write(data)
            return
        }
        if pathOnly {
            print(PathDisplay.display(url))
            return
        }

        print("SESSION=\"\(PathDisplay.display(session))\"")
        print("")
        print("notes:")
        print("  profile: \(resolvedProfile)")
        for key in ["verdict", "notes", "review-items", "evidence"] {
            if let item = paths[key], FileManager.default.fileExists(atPath: item.path) {
                print("  \(key): \(PathDisplay.display(item))")
            }
        }
        let verdictPayload = try? JSONFiles.object(verdictJSONPath(session: session, profile: resolvedProfile))
        if let verdictPayload {
            ReviewSummaryPrinter.printReviewSummary(verdictPayload["review_summary"], indent: "  ")
        }
        let openCommand = "less \(PathDisplay.display(url))"
        let handoff = SynthesisArtifactHandoff.build(payload: verdictPayload, openCommand: openCommand)
        print("  selected: \(kind)")
        print("  recommended_next: \(handoff.recommendedNext)")
        print("  next:")
        for command in handoff.nextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(handoff.recommendedNext)
    }

    private static func selectedProfile(_ requested: String, session: URL) throws -> String {
        if requested != "auto" {
            return requested
        }
        if let profile = AuthoritativeHandoffState.selectedProfile(session), !profile.isEmpty {
            return profile
        }
        let verdict = session.appendingPathComponent("derived/synthesis-simple/extractive/quality_verdict.json")
        if FileManager.default.fileExists(atPath: verdict.path) {
            let payload = try JSONFiles.object(verdict)
            if let profile = payload["selected_transcript_profile"] as? String, !profile.isEmpty {
                return profile
            }
        }
        return "current"
    }

    private static func artifactPaths(session: URL, profile: String) -> [String: URL] {
        let outDir = session.appendingPathComponent("derived/synthesis-simple/extractive")
        let suffix = profile == "current" ? "" : ".\(profile)"
        return [
            "notes": outDir.appendingPathComponent("notes\(suffix).md"),
            "verdict": outDir.appendingPathComponent("quality_verdict\(suffix).md"),
            "review-items": outDir.appendingPathComponent("review_items\(suffix).jsonl"),
            "evidence": outDir.appendingPathComponent("evidence_notes\(suffix).json"),
        ]
    }

    private static func verdictJSONPath(session: URL, profile: String) -> URL {
        let outDir = session.appendingPathComponent("derived/synthesis-simple/extractive")
        let suffix = profile == "current" ? "" : ".\(profile)"
        return outDir.appendingPathComponent("quality_verdict\(suffix).json")
    }

    private static func int(_ value: Any?) -> Int? {
        if let value = value as? Int { return value }
        if let value = value as? NSNumber { return value.intValue }
        if let value = value as? String { return Int(value) }
        return nil
    }

    private static func printHelp() {
        print("""
        usage: murmurmark notes ./session|latest [--kind notes|verdict|review-items|evidence]
                                [--profile auto|current|NAME] [--path-only|--cat] [--sessions-root ./sessions]

        Prints paths to local synthesis artifacts. Use --cat to stream one artifact to stdout.
        """)
    }
}

enum ReviewSummaryPrinter {
    static func printReviewSummary(_ value: Any?, indent: String) {
        let summary = value as? [String: Any] ?? [:]
        guard !summary.isEmpty else {
            return
        }
        let count = int(summary["review_item_count"]) ?? 0
        let seconds = double(summary["review_item_seconds"]) ?? 0.0
        print("\(indent)review_items: \(count) / \(String(format: "%.2f", seconds))s")
        let byType = summary["by_type"] as? [String: Any] ?? [:]
        let topTypes = sortedTypeBuckets(byType).prefix(5)
        if !topTypes.isEmpty {
            let rendered = topTypes.map { "\($0.name)=\($0.count)" }.joined(separator: ", ")
            print("\(indent)review_item_types: \(rendered)")
        }
    }

    static func printSynthesisReviewMetrics(_ metrics: [String: Any], indent: String) {
        guard let count = int(metrics["synthesis_review_item_count"]) else {
            return
        }
        let seconds = double(metrics["synthesis_review_item_seconds"]) ?? 0.0
        print("\(indent)synthesis_review_items: \(count) / \(String(format: "%.2f", seconds))s")

        let rows = metrics["synthesis_review_top_types"] as? [Any] ?? []
        let rendered = rows.prefix(5).compactMap { item -> String? in
            guard let row = item as? [String: Any],
                  let type = row["type"] as? String,
                  !type.isEmpty
            else {
                return nil
            }
            let count = int(row["count"]) ?? 0
            return "\(type)=\(count)"
        }.joined(separator: ", ")
        if !rendered.isEmpty {
            print("\(indent)synthesis_review_types: \(rendered)")
        }
    }

    private static func sortedTypeBuckets(_ byType: [String: Any]) -> [(name: String, count: Int)] {
        byType.compactMap { key, value in
            let bucket = value as? [String: Any] ?? [:]
            guard let count = int(bucket["count"]) else {
                return nil
            }
            return (name: key, count: count)
        }.sorted { left, right in
            if left.count != right.count {
                return left.count > right.count
            }
            return left.name < right.name
        }
    }

    private static func int(_ value: Any?) -> Int? {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String {
            return Int(value)
        }
        return nil
    }

    private static func double(_ value: Any?) -> Double? {
        if let value = value as? Double {
            return value
        }
        if let value = value as? NSNumber {
            return value.doubleValue
        }
        if let value = value as? String {
            return Double(value)
        }
        return nil
    }
}

struct SynthesisArtifactHandoff {
    let recommendedNext: String
    let nextCommands: [String]

    static func build(payload: [String: Any]?, openCommand: String) -> SynthesisArtifactHandoff {
        let jsonRecommended = string(payload?["recommended_next"])
        let jsonCommands = commandList(payload?["next_commands"])
        let shouldPreferJSONAction = jsonRecommended?.hasPrefix("murmurmark review ") == true
        let recommended = shouldPreferJSONAction ? (jsonRecommended ?? openCommand) : openCommand

        var commands: [String] = []
        commands.append(recommended)
        if shouldPreferJSONAction {
            commands.append(contentsOf: jsonCommands)
            commands.append(openCommand)
        } else {
            commands.append(openCommand)
            commands.append(contentsOf: jsonCommands)
        }
        return SynthesisArtifactHandoff(recommendedNext: recommended, nextCommands: dedupe(commands))
    }

    private static func commandList(_ value: Any?) -> [String] {
        guard let rows = value as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let command = row["command"] as? String else { return nil }
            let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    private static func dedupe(_ commands: [String]) -> [String] {
        var seen = Set<String>()
        var result: [String] = []
        for command in commands {
            let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !trimmed.isEmpty, seen.insert(trimmed).inserted else {
                continue
            }
            result.append(trimmed)
        }
        return result
    }

    private static func string(_ value: Any?) -> String? {
        guard let value = value as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

enum TranscriptCommands {
    static func transcript(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let target = remaining.removeFirst()
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let requestedProfile = ArgumentEditing.takeOption("profile", from: &remaining) ?? "auto"
        let pathOnly = ArgumentEditing.takeFlag("path-only", from: &remaining)
        let cat = ArgumentEditing.takeFlag("cat", from: &remaining)
        guard remaining.isEmpty else {
            throw CLIError("unknown transcript option(s): \(remaining.joined(separator: " "))")
        }

        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let profile = try selectedProfile(requestedProfile, session: session)
        let url = requestedProfile == "auto"
            ? (AuthoritativeHandoffState.artifact("transcript", session: session) ?? transcriptURL(profile: profile, session: session))
            : transcriptURL(profile: profile, session: session)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("transcript not found: \(PathDisplay.display(url)); run `murmurmark process \(PathDisplay.display(session))`")
        }

        if cat {
            let data = try Data(contentsOf: url)
            FileHandle.standardOutput.write(data)
            return
        }
        if pathOnly {
            print(PathDisplay.display(url))
            return
        }

        print("SESSION=\"\(PathDisplay.display(session))\"")
        print("")
        print("transcript:")
        print("  profile: \(profile)")
        print("  path: \(PathDisplay.display(url))")
        let verdictPayload = try? JSONFiles.object(verdictJSONPath(session: session, profile: profile))
        if let verdictPayload {
            ReviewSummaryPrinter.printReviewSummary(verdictPayload["review_summary"], indent: "  ")
        }
        let openCommand = "less \(PathDisplay.display(url))"
        let handoff = SynthesisArtifactHandoff.build(payload: verdictPayload, openCommand: openCommand)
        print("  recommended_next: \(handoff.recommendedNext)")
        print("  next:")
        for command in handoff.nextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(handoff.recommendedNext)
    }

    private static func selectedProfile(_ requested: String, session: URL) throws -> String {
        if requested != "auto" {
            return requested
        }
        if let profile = AuthoritativeHandoffState.selectedProfile(session), !profile.isEmpty {
            return profile
        }
        let verdict = session.appendingPathComponent("derived/synthesis-simple/extractive/quality_verdict.json")
        if FileManager.default.fileExists(atPath: verdict.path) {
            let payload = try JSONFiles.object(verdict)
            if let profile = payload["selected_transcript_profile"] as? String, !profile.isEmpty {
                return profile
            }
        }
        let current = transcriptURL(profile: "current", session: session)
        if FileManager.default.fileExists(atPath: current.path) {
            return "current"
        }
        throw CLIError("selected transcript profile is unknown; run `murmurmark synthesize \(PathDisplay.display(session))`")
    }

    private static func transcriptURL(profile: String, session: URL) -> URL {
        let resolved = session.appendingPathComponent("derived/transcript-simple/whisper-cpp/resolved")
        if profile == "current" {
            return resolved.appendingPathComponent("transcript.md")
        }
        return resolved.appendingPathComponent("transcript.\(profile).md")
    }

    private static func verdictJSONPath(session: URL, profile: String) -> URL {
        let outDir = session.appendingPathComponent("derived/synthesis-simple/extractive")
        let suffix = profile == "current" ? "" : ".\(profile)"
        return outDir.appendingPathComponent("quality_verdict\(suffix).json")
    }

    private static func int(_ value: Any?) -> Int? {
        if let value = value as? Int { return value }
        if let value = value as? NSNumber { return value.intValue }
        if let value = value as? String { return Int(value) }
        return nil
    }

    private static func printHelp() {
        print("""
        usage: murmurmark transcript ./session|latest [--profile auto|current|NAME] [--path-only|--cat] [--sessions-root ./sessions]

        Resolves the selected transcript profile from quality_verdict.json and prints the transcript path.
        Use --cat to stream Markdown to stdout.
        """)
    }
}

enum CleanupPrinter {
    static func printSummary(session: URL, args: [String]) throws {
        let profile = ArgumentEditing.peekOption("output-profile", in: args) ?? "audit_cleanup_v1"
        let suffix = profile == "current" ? "" : ".\(profile)"
        let cleanupDir = session.appendingPathComponent("derived/transcript-simple/whisper-cpp/audit-cleanup")
        let reportURL = cleanupDir.appendingPathComponent("audit_cleanup_report\(suffix).json")
        guard FileManager.default.fileExists(atPath: reportURL.path) else {
            print("")
            print("cleanup:")
            print("  report: missing")
            print("  expected: \(PathDisplay.display(reportURL))")
            return
        }

        let payload = try JSONFiles.object(reportURL)
        let summary = dict(payload["summary"])
        let gates = dict(payload["gates"])

        print("")
        print("cleanup:")
        print("  report: \(PathDisplay.display(reportURL))")
        print("  input_profile: \(string(payload["input_profile"]) ?? "unknown")")
        print("  output_profile: \(string(payload["output_profile"]) ?? profile)")
        print("  applied_patches: \(int(summary["applied_patches"]))")
        print("  rejected_patches: \(int(summary["rejected_patches"]))")
        print(String(format: "  dropped_me_duplicate: %.2fs", double(summary["dropped_me_duplicate_seconds"])))
        print(String(format: "  dropped_me_noise: %.2fs", double(summary["dropped_me_noise_seconds"])))
        print(String(format: "  harmful_after: %.2fs", double(summary["audit_harmful_seconds_after"])))
        print("  gates_passed: \(bool(gates["passed"]))")
        if let warnings = gates["warnings"] as? [Any], !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let fallbackCommands = [
            "murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)",
            "murmurmark report \(PathDisplay.display(session))",
        ]
        let nextCommands = commandList(payload["next_commands"])
        let displayedNextCommands = nextCommands.isEmpty ? fallbackCommands : nextCommands
        let recommendedNext = string(payload["recommended_next"]) ?? displayedNextCommands[0]
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        for command in displayedNextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(recommendedNext)
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        guard let value = value as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func commandList(_ value: Any?) -> [String] {
        guard let rows = value as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let command = row["command"] as? String else { return nil }
            let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    private static func bool(_ value: Any?) -> Bool {
        if let value = value as? Bool {
            return value
        }
        if let value = value as? String {
            return ["true", "yes", "1"].contains(value.lowercased())
        }
        return false
    }

    private static func int(_ value: Any?) -> Int {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String, let parsed = Int(value) {
            return parsed
        }
        return 0
    }

    private static func double(_ value: Any?) -> Double {
        if let value = value as? Double {
            return value
        }
        if let value = value as? NSNumber {
            return value.doubleValue
        }
        if let value = value as? String, let parsed = Double(value) {
            return parsed
        }
        return 0
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }
}

enum RepairPrinter {
    static func printBoundarySummary(session: URL) throws {
        let reportURL = session.appendingPathComponent(
            "derived/transcript-simple/whisper-cpp/authoritative-boundary-v1/boundary_repair_report.json"
        )
        guard FileManager.default.fileExists(atPath: reportURL.path) else {
            print("")
            print("repair:")
            print("  kind: authoritative_boundary")
            print("  report: missing")
            print("  expected: \(PathDisplay.display(reportURL))")
            return
        }
        let payload = try JSONFiles.object(reportURL)
        let summary = dict(payload["summary"])
        let gates = dict(payload["gates"])
        print("")
        print("repair:")
        print("  kind: authoritative_boundary")
        print("  report: \(PathDisplay.display(reportURL))")
        print("  input_profile: \(string(payload["input_profile"]) ?? "unknown")")
        print("  output_profile: \(string(payload["output_profile"]) ?? "authoritative_boundary_v1")")
        print("  closed_items: \(int(summary["closed_items"]))")
        print("  remaining_items: \(int(summary["remaining_items"]))")
        print("  gates_passed: \(bool(gates["passed"]))")
        let next = "murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile authoritative_boundary_v1"
        FinalNextPrinter.print(next)
    }

    static func printLocalRecallSummary(session: URL, args: [String]) throws {
        let profile = ArgumentEditing.peekOption("output-profile", in: args) ?? "local_recall_repair_v1"
        let suffix = profile == "current" ? "" : ".\(profile)"
        let repairDir = session.appendingPathComponent("derived/transcript-simple/whisper-cpp/local-recall-repair")
        let reportURL = repairDir.appendingPathComponent("local_recall_repair_report\(suffix).json")
        guard FileManager.default.fileExists(atPath: reportURL.path) else {
            print("")
            print("repair:")
            print("  kind: local_recall")
            print("  report: missing")
            print("  expected: \(PathDisplay.display(reportURL))")
            return
        }

        let payload = try JSONFiles.object(reportURL)
        let summary = dict(payload["summary"])
        let gates = dict(payload["gates"])
        print("")
        print("repair:")
        print("  kind: local_recall")
        print("  report: \(PathDisplay.display(reportURL))")
        print("  input_profile: \(string(payload["input_profile"]) ?? "unknown")")
        print("  output_profile: \(string(payload["output_profile"]) ?? profile)")
        print("  eligible_items: \(int(summary["eligible_items"]))")
        print("  applied_repairs: \(int(summary["applied_repairs"]))")
        print("  inserted_me_seconds: \(summary["inserted_me_seconds"] ?? 0)")
        print("  rejected_items: \(int(summary["rejected_items"]))")
        print("  gates_passed: \(bool(gates["passed"]))")
        if let warnings = gates["warnings"] as? [Any], !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let fallbackCommands = repairFallbackCommands(session: session, profile: profile)
        let displayedNextCommands = commandList(payload["next_commands"])
        let nextCommands = displayedNextCommands.isEmpty ? fallbackCommands : displayedNextCommands
        let recommendedNext = string(payload["recommended_next"]) ?? nextCommands[0]
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        for command in nextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(recommendedNext)
    }

    static func printOrderSummary(session: URL, args: [String]) throws {
        let profile = ArgumentEditing.peekOption("output-profile", in: args) ?? "order_repair_v1"
        let suffix = profile == "current" ? "" : ".\(profile)"
        let repairDir = session.appendingPathComponent("derived/transcript-simple/whisper-cpp/order-repair")
        let reportURL = repairDir.appendingPathComponent("transcript_order_repair_report\(suffix).json")
        guard FileManager.default.fileExists(atPath: reportURL.path) else {
            print("")
            print("repair:")
            print("  kind: transcript_order")
            print("  report: missing")
            print("  expected: \(PathDisplay.display(reportURL))")
            return
        }

        let payload = try JSONFiles.object(reportURL)
        let summary = dict(payload["summary"])
        let gates = dict(payload["gates"])
        print("")
        print("repair:")
        print("  kind: transcript_order")
        print("  report: \(PathDisplay.display(reportURL))")
        print("  input_profile: \(string(payload["input_profile"]) ?? "unknown")")
        print("  output_profile: \(string(payload["output_profile"]) ?? profile)")
        print("  applied_repairs: \(int(summary["applied_repairs"]))")
        print("  split_utterances_created: \(int(summary["split_utterances_created"]))")
        print("  unrepaired_order_risks: \(int(summary["unrepaired_order_risks"]))")
        print("  gates_passed: \(bool(gates["passed"]))")
        if let warnings = gates["warnings"] as? [Any], !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let fallbackCommands = repairFallbackCommands(session: session, profile: profile)
        let displayedNextCommands = commandList(payload["next_commands"])
        let nextCommands = displayedNextCommands.isEmpty ? fallbackCommands : displayedNextCommands
        let recommendedNext = string(payload["recommended_next"]) ?? nextCommands[0]
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        for command in nextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(recommendedNext)
    }

    static func printRemoteLeakSummary(session: URL, args: [String]) throws {
        let outDir = ArgumentEditing.peekOption("out-dir", in: args)
            .map(PathURLs.fileURL(_:))
            ?? session.appendingPathComponent("derived/transcript-simple/whisper-cpp/remote-leak-repair")
        let planURL = outDir.appendingPathComponent("remote_leak_segment_repair_plan.json")
        let reportURL = outDir.appendingPathComponent("remote_leak_segment_repair.md")
        guard FileManager.default.fileExists(atPath: planURL.path) else {
            print("")
            print("remote_leak_segment_repair:")
            print("  plan: missing")
            print("  expected: \(PathDisplay.display(planURL))")
            return
        }

        let payload = try JSONFiles.object(planURL)
        let summary = dict(payload["summary"])
        let actionPlan = payload["action_plan"] as? [[String: Any]] ?? []
        print("")
        print("remote_leak_segment_repair:")
        print("  plan: \(PathDisplay.display(planURL))")
        print("  report: \(PathDisplay.display(reportURL))")
        print("  mode: audit_only")
        print("  items: \(int(summary["items"]))")
        print("  seconds: \(summary["seconds"] ?? 0)")
        print("  protect_local_content_items: \(int(summary["protect_local_content_items"]))")
        if let firstAction = actionPlan.first {
            print("  next_work: \(string(firstAction["next_work"]) ?? "none")")
        }
        let fallbackCommands = ["less \(PathDisplay.display(reportURL))"]
        let displayedNextCommands = commandList(payload["next_commands"])
        let nextCommands = displayedNextCommands.isEmpty ? fallbackCommands : displayedNextCommands
        let recommendedNext = string(payload["recommended_next"]) ?? nextCommands[0]
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        for command in nextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(recommendedNext)
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        guard let value = value as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func commandList(_ value: Any?) -> [String] {
        guard let rows = value as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let command = row["command"] as? String else { return nil }
            let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    private static func repairFallbackCommands(session: URL, profile: String) -> [String] {
        [
            "murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)",
            "murmurmark transcript \(PathDisplay.display(session)) --profile \(profile)",
            "murmurmark report \(PathDisplay.display(session))",
        ]
    }

    private static func bool(_ value: Any?) -> Bool {
        if let value = value as? Bool {
            return value
        }
        if let value = value as? String {
            return ["true", "yes", "1"].contains(value.lowercased())
        }
        return false
    }

    private static func int(_ value: Any?) -> Int {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String, let parsed = Int(value) {
            return parsed
        }
        return 0
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }
}

enum SynthesisPrinter {
    static func printSummary(session: URL) throws {
        let outDir = session.appendingPathComponent("derived/synthesis-simple/extractive")
        let verdictURL = outDir.appendingPathComponent("quality_verdict.json")
        guard FileManager.default.fileExists(atPath: verdictURL.path) else {
            print("")
            print("synthesis:")
            print("  quality_verdict: missing")
            print("  expected: \(PathDisplay.display(verdictURL))")
            return
        }

        let payload = try JSONFiles.object(verdictURL)
        let metrics = dict(payload["metrics"])
        let reviewSummary = dict(payload["review_summary"])
        let riskItems = payload["risk_items"] as? [Any] ?? []
        let profile = string(payload["selected_transcript_profile"]) ?? "unknown"
        let verdict = string(payload["verdict"]) ?? "unknown"
        let sessionPath = PathDisplay.display(session)
        let reviewItemCount = intOptional(reviewSummary["review_item_count"]) ?? 0
        let needsReview = reviewItemCount > 0 || !riskItems.isEmpty || verdict == "usable_with_review"
        let canSuggestExport = verdict == "good" && !needsReview
        let nextCommands = commandList(payload["next_commands"])
        let recommendedNext = string(payload["recommended_next"])
            ?? nextCommands.first
            ?? fallbackRecommendedNext(needsReview: needsReview, sessionPath: sessionPath)
        let displayedNextCommands = nextCommands.isEmpty
            ? fallbackNextCommands(needsReview: needsReview, canSuggestExport: canSuggestExport, sessionPath: sessionPath)
            : nextCommands

        print("")
        print("synthesis:")
        print("  quality_verdict: \(PathDisplay.display(verdictURL))")
        print("  notes: \(PathDisplay.display(outDir.appendingPathComponent("notes.md")))")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        if let classification = string(payload["session_classification"]), classification != "conversation" {
            print("  session_classification: \(classification)")
        }
        print("  risk_items: \(riskItems.count)")
        ReviewSummaryPrinter.printReviewSummary(payload["review_summary"], indent: "  ")
        if let needsReview = intOptional(metrics["needs_review_count"]) {
            print("  needs_review_count: \(needsReview)")
        }
        if let overlapSeconds = doubleOptional(metrics["cross_role_overlap_gt2_seconds"]) {
            print(String(format: "  cross_role_overlap_gt2_seconds: %.2f", overlapSeconds))
        }
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        for command in displayedNextCommands {
            print("    \(command)")
        }
        FinalNextPrinter.print(recommendedNext)
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func commandList(_ value: Any?) -> [String] {
        guard let rows = value as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let command = row["command"] as? String else { return nil }
            let trimmed = command.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? nil : trimmed
        }
    }

    private static func fallbackRecommendedNext(needsReview: Bool, sessionPath: String) -> String {
        needsReview ? "murmurmark review next \(sessionPath)" : "murmurmark notes \(sessionPath)"
    }

    private static func fallbackNextCommands(needsReview: Bool, canSuggestExport: Bool, sessionPath: String) -> [String] {
        var commands: [String] = []
        if needsReview {
            commands.append("murmurmark review next \(sessionPath)")
        }
        commands.append("murmurmark notes \(sessionPath)")
        commands.append("murmurmark transcript \(sessionPath)")
        commands.append("murmurmark report \(sessionPath)")
        if canSuggestExport {
            commands.append("murmurmark finish \(sessionPath)")
        }
        return commands
    }

    private static func intOptional(_ value: Any?) -> Int? {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String {
            return Int(value)
        }
        return nil
    }

    private static func doubleOptional(_ value: Any?) -> Double? {
        if let value = value as? Double {
            return value
        }
        if let value = value as? NSNumber {
            return value.doubleValue
        }
        if let value = value as? String {
            return Double(value)
        }
        return nil
    }
}

enum LiveCommands {
    static func live(_ args: [String]) throws {
        guard let subcommand = args.first else {
            printHelp()
            return
        }
        let forwarded = Array(args.dropFirst())
        switch subcommand {
        case "gate":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printGateHelp()
                return
            }
            var gateArgs = forwarded
            let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &gateArgs) ?? "sessions")
            guard gateArgs.isEmpty else {
                throw CLIError("live gate only accepts --sessions-root")
            }
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [
                    try script("report-live-corpus-gates.py").path,
                    "all",
                    "--refresh",
                    "--min-live-sessions", "3",
                    "--min-compared-sessions", "3",
                    "--min-meaningful-compared-sessions", "3",
                    "--min-passing-compared-sessions", "3",
                    "--max-order-mismatches", "0",
                    "--max-missing-me-sec", "0",
                    "--max-remote-in-me-sec", "0",
                    "--max-boundary-duplicates", "0",
                    "--require-passing-gates",
                    "--fail-on-promotion",
                    "--sessions-root", sessionsRoot.path,
                    "--out-dir", liveCorpusOutDir(sessionsRoot: sessionsRoot).path,
                ],
                allowedExitCodes: [0, 1]
            )
            if status != 0 {
                Foundation.exit(status)
            }
        case "recovery-evidence":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printRecoveryEvidenceHelp()
                return
            }
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [try script("report-live-recovery-real-evidence.py").path] + forwarded,
                allowedExitCodes: [0, 2]
            )
            if status != 0 {
                Foundation.exit(status)
            }
        case "status", "next":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printStatusHelp()
                return
            }
            var reportArgs = forwarded
            let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &reportArgs) ?? "sessions")
            if reportArgs.isEmpty {
                reportArgs = ["all", "--refresh"]
            }
            reportArgs = addDefaultLiveCorpusOutDir(reportArgs, sessionsRoot: sessionsRoot)
            try Tooling.runPath(
                try PythonRuntime.resolve(),
                [try script("report-live-corpus-gates.py").path] + reportArgs + ["--sessions-root", sessionsRoot.path]
            )
        case "watch":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printWatchHelp()
                return
            }
            var watchArgs = forwarded
            let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &watchArgs) ?? "sessions")
            let pollSec = ArgumentEditing.takeOption("poll-sec", from: &watchArgs) ?? "1"
            let diagnosticDraft = ArgumentEditing.takeFlag("diagnostic-draft", from: &watchArgs)
            guard let target = watchArgs.first else {
                printWatchHelp()
                return
            }
            guard watchArgs.count == 1 else {
                throw CLIError("live watch accepts one session and optional --poll-sec/--diagnostic-draft/--sessions-root")
            }
            // A recorder creates session.json only during finalization. Live watch must be able
            // to attach to the explicit in-progress directory while the session lock is present.
            let session = try SessionResolver.resolveLiveWatch(target, sessionsRoot: sessionsRoot)
            try Tooling.runPathForwardingInterrupts(
                try PythonRuntime.resolve(),
                [try script("watch-live-draft.py").path, session.path, "--poll-sec", pollSec]
                    + (diagnosticDraft ? ["--diagnostic-draft"] : [])
            )
        case "evidence":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printEvidenceHelp()
                return
            }
            var evidenceArgs = forwarded
            let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &evidenceArgs) ?? "sessions")
            guard let target = evidenceArgs.first else {
                printEvidenceHelp()
                return
            }
            evidenceArgs.removeFirst()
            let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [try script("report-live-session-evidence.py").path, session.path] + evidenceArgs,
                allowedExitCodes: [0, 2]
            )
            if status != 0 {
                Foundation.exit(status)
            }
        case "replay":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printReplayHelp()
                return
            }
            var replayArgs = forwarded
            let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &replayArgs) ?? "sessions")
            guard let target = replayArgs.first else {
                printReplayHelp()
                return
            }
            replayArgs.removeFirst()
            let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
            try Tooling.runPath(
                try PythonRuntime.resolve(),
                [try script("report-live-replay-lab.py").path, session.path] + replayArgs
            )
        case "pilot":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printPilotHelp()
                return
            }
            let script = try script("run-live-parity-pilot.sh")
            print("live_pilot:")
            let suffix = forwarded.isEmpty ? "" : " \(forwarded.joined(separator: " "))"
            print("  command: \(PathDisplay.display(script))\(suffix)")
            fflush(stdout)
            try Tooling.runPathForwardingInterrupts(
                URL(fileURLWithPath: "/bin/bash"),
                [script.path] + forwarded,
                environmentOverrides: ["MURMURMARK_BIN": ExecutablePath.current()]
            )
        case "help", "--help", "-h":
            printHelp()
        default:
            throw CLIError("unknown live command: \(subcommand)")
        }
    }

    static func liveCorpusOutDir(sessionsRoot: URL) -> URL {
        sessionsRoot.appendingPathComponent("_reports/live-pipeline")
    }

    static func addDefaultLiveCorpusOutDir(_ args: [String], sessionsRoot: URL) -> [String] {
        if ArgumentEditing.hasOption("out-dir", in: args) {
            return args
        }
        return args + ["--out-dir", liveCorpusOutDir(sessionsRoot: sessionsRoot).path]
    }

    static func printHelp() {
        Swift.print("""
        usage:
          murmurmark live status [all|latest|./session...] [--refresh] [--sessions-root ./sessions]
          murmurmark live next [all|latest|./session...] [--refresh] [--sessions-root ./sessions]
          murmurmark live gate [--sessions-root ./sessions]
          murmurmark live watch SESSION|latest [--poll-sec SEC] [--diagnostic-draft]
                                              [--sessions-root ./sessions]
          murmurmark live evidence SESSION|latest [--refresh] [--strict] [--require-causal-recovery]
                                                   [--max-recovery-final-lag-sec SEC]
                                                   [--sessions-root ./sessions]
          murmurmark live recovery-evidence [all|latest|SESSION...] [--refresh] [--strict]
                                            [--min-sessions 3] [--sessions-root ./sessions]
          murmurmark live replay SESSION|latest [--refresh] [--with-labs] [--lab-policy POLICY]
                                            [--sessions-root ./sessions]
          murmurmark live pilot [SESSION] [--duration SEC] [--segment-sec SEC] [--overlap-sec SEC]
                               [--controlled-real] [--preflight-only] [--skip-safety-gate]

        Live commands collect near-realtime shadow evidence. They do not promote live output.
        Batch transcript remains authoritative.
        """)
    }

    static func printRecoveryEvidenceHelp() {
        Swift.print("""
        usage:
          murmurmark live recovery-evidence [all|latest|SESSION...] [options]

        Aggregates fresh real-session proof for the incremental recording-time causal recovery.
        A session counts only when manager version 1.1.0 or newer actually completed recovery while
        recording, authoritative batch and meaningful comparison exist, required recovery checks
        pass, and final recovery lag is within the requested bound. Live promotion remains blocked.

        Options:
          --refresh                         Refresh per-session comparison and evidence first.
          --strict                          Exit 2 until the minimum number of sessions passes.
          --min-sessions N                  Required passing sessions. Default: 3.
          --max-recovery-final-lag-sec SEC  Default: 0.
          --sessions-root PATH              Default: ./sessions.
          --out-dir PATH                    Default: sessions/_reports/live-pipeline.
        """)
    }

    static func printPilotHelp() {
        Swift.print("""
        usage:
          murmurmark live pilot [SESSION] [options]

        Legacy wrapper around scripts/run-live-parity-pilot.sh.

        New live evidence should normally use:
          murmurmark record --target-bundle system --experiment live-shadow-v1

        Without SESSION, this command records an old lab live-pipeline pilot, then runs batch
        processing, live-vs-batch comparison and refreshed live corpus gates. With SESSION, it
        processes and compares an existing live-pipeline session. Use --controlled-real with SESSION
        to mark an existing date-named Live Evidence run without starting a new recording.

        Options:
          --duration SEC       Recording duration for a new lab pilot. Default: 45.
          --segment-sec SEC    Live segment length. Default: 15, or 60 for --controlled-real.
          --overlap-sec SEC    Live overlap length. Default: 3, or 5 for --controlled-real.
          --out SESSION        Output session path for a new pilot.
          --controlled-real    Record a date-named Live Evidence run until Ctrl-C, or process
                               existing controlled Live Evidence when SESSION is provided.
          --preflight-only     Check proof/corpus gates and exit before recording or processing.
          --skip-safety-gate   Reuse an existing full fail-open proof.
          --allow-unsafe-controlled-real-recording
                               Temporarily allow a new real live recording while the sidecar is
                               unsafe for valuable meetings.
          --force-asr          Force batch ASR during post-stop processing.

        Safe current use:
          murmurmark record --target-bundle system --experiment live-shadow-v1
          murmurmark live pilot sessions/<session-id> --controlled-real
          murmurmark record --target-bundle system
        """)
    }

    static func printWatchHelp() {
        Swift.print("""
        usage:
          murmurmark live watch SESSION|latest [--poll-sec SEC] [--diagnostic-draft]
                                              [--sessions-root ./sessions]

        Prints the conservative remote-energy preview plus worker heartbeat/lag until recording-time
        preview reaches a terminal state. An explicit SESSION path works while recording is in
        progress, before session.json exists; latest becomes available after finalization. Use
        --diagnostic-draft to include all candidate-only evidence. Batch transcript remains
        authoritative.
        """)
    }

    static func printEvidenceHelp() {
        Swift.print("""
        usage:
          murmurmark live evidence SESSION|latest [--refresh] [--strict]
                                           [--max-final-lag-sec 60]
                                           [--max-first-chunk-latency-sec 120]
                                           [--sessions-root ./sessions]

        Writes a compact per-session verdict for capture health, pre-stop committed-PCM provenance,
        worker termination/lag, fallback isolation and live-vs-batch parity. --strict exits 2 until
        all parity gates pass. Batch remains authoritative and promotion remains disabled.
        """)
    }

    static func printReplayHelp() {
        Swift.print("""
        usage:
          murmurmark live replay SESSION|latest [--refresh] [--with-labs]
                                            [--lab-policy POLICY]
                                            [--sessions-root ./sessions]

        Builds an offline matrix for live role policies from existing live/batch evidence. The
        report selects only candidates that reduce missing Me speech without increasing remote leak
        or blocking order errors. It also reports live/batch ASR-window compatibility. Raw audio,
        batch transcript and production defaults are never modified.
        """)
    }

    static func printStatusHelp() {
        Swift.print("""
        usage:
          murmurmark live status [all|latest|./session...] [--refresh] [--sessions-root ./sessions]
          murmurmark live next [all|latest|./session...] [--refresh] [--sessions-root ./sessions]

        Prints the live parity corpus gates and the recommended next command.
        Defaults to `all --refresh`.
        """)
    }

    static func printGateHelp() {
        Swift.print("""
        usage:
          murmurmark live gate [--sessions-root ./sessions]

        Runs the strict Near-Realtime Live Parity Coverage v1 gate:
        at least 3 live sessions, 3 compared sessions, 3 meaningful comparisons,
        3 all-gates-passed comparisons, zero order mismatches, zero missing-Me seconds,
        zero remote-in-Me seconds, zero boundary duplicates, all live parity gates passed,
        and no live promotion while v1 remains quarantined.

        This command exits non-zero until live promotion evidence is complete.
        """)
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("live script not found: \(url.path)")
        }
        return url
    }
}

enum ExperimentCommands {
    static func experiment(_ args: [String]) throws {
        guard let subcommand = args.first else {
            printHelp()
            return
        }
        let forwarded = Array(args.dropFirst())
        switch subcommand {
        case "status", "report", "compare", "refresh", "recover-draft":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printSubcommandHelp(subcommand)
                return
            }
            guard let target = forwarded.first else {
                printSubcommandHelp(subcommand)
                return
            }
            try runContract(command: subcommand, target: target, args: Array(forwarded.dropFirst()))
        case "help", "--help", "-h":
            printHelp()
        default:
            throw CLIError("unknown experiment command: \(subcommand)")
        }
    }

    private static func runContract(command: String, target: String, args: [String]) throws {
        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let experiment = ArgumentEditing.takeOption("experiment", from: &remaining) ?? "live-shadow-v1"
        guard remaining.isEmpty else {
            throw CLIError("unexpected experiment arguments: \(remaining.joined(separator: " "))")
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let script = PathURLs.fileURL("scripts/experiment-sidecar-contract.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("experiment contract script not found: \(script.path)")
        }
        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)
        try Tooling.runPath(
            try PythonRuntime.resolve(),
            [
                script.path,
                command,
                session.path,
                "--experiment",
                experiment,
                "--sessions-root",
                sessionsRoot.path,
            ]
        )
    }

    private static func printHelp() {
        Swift.print("""
        usage:
          murmurmark experiment status SESSION|latest [--experiment live-shadow-v1] [--sessions-root ./sessions]
          murmurmark experiment report SESSION|latest [--experiment live-shadow-v1] [--sessions-root ./sessions]
          murmurmark experiment compare SESSION|latest [--experiment live-shadow-v1] [--sessions-root ./sessions]
          murmurmark experiment recover-draft SESSION|latest [--experiment live-shadow-v1] [--sessions-root ./sessions]

        Writes and reads the experimental sidecar contract under
        derived/experiments/<experiment-id>/. Batch transcript remains authoritative.
        """)
    }

    private static func printSubcommandHelp(_ subcommand: String) {
        Swift.print("""
        usage: murmurmark experiment \(subcommand) SESSION|latest [--experiment live-shadow-v1] [--sessions-root ./sessions]

        status/report refresh the sidecar contract. compare reads existing realtime artifacts and
        never starts ASR. recover-draft explicitly builds an isolated post-stop fallback draft.
        """)
    }
}

enum CorpusCommands {
    static func corpus(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag([args.first ?? ""]) {
            CorpusHelp.print()
            return
        }
        guard let subcommand = args.first else {
            throw CLIError(
                "corpus requires process, build, evaluate, train-audio-judge, taxonomy, gate, order, " +
                    "local-recall, local-recall-repair, boundary, remote-leak, echo-candidate, or report"
            )
        }
        var forwarded = Array(args.dropFirst())
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &forwarded) ?? "sessions")

        switch subcommand {
        case "process":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                CorpusProcessHelp.print()
                return
            }
            guard !ArgumentEditing.hasOption("out-dir", in: forwarded),
                  !ArgumentEditing.hasOption("corpus-dir", in: forwarded)
            else {
                throw CLIError("corpus process uses default report directories; run build/evaluate/train-audio-judge separately for custom paths")
            }
            let sessions = try takeSessions(from: &forwarded, sessionsRoot: sessionsRoot)
            try CorpusRemoteLeakPlan.run(sessions: sessions)
            try reportSessionQuality(sessions: sessions)
            try build(sessions: sessions, extraArgs: forwarded)
            try evaluate(extraArgs: [])
            try trainAudioJudge(extraArgs: [])
            try taxonomy(extraArgs: [])
            try operationalReadiness()
            try transcriptOrder(sessions: [], extraArgs: [])
            try CorpusLocalRecallCommands.report(sessions: [], extraArgs: [])
            try CorpusLocalRecallRepairCommands.report(sessions: [], extraArgs: [])
            try CorpusRemoteLeakCommands.report(sessions: [], extraArgs: [])
            _ = try gates(extraArgs: [], allowedExitCodes: [0, 1])
            try CorpusPrinter.printSessionQuality()
            try CorpusPrinter.printBuild()
            try CorpusPrinter.printEvaluation()
            try CorpusPrinter.printAudioJudge()
            try CorpusPrinter.printTaxonomy()
            try CorpusPrinter.printTranscriptOrder()
            try CorpusPrinter.printLocalRecallCorpus()
            try CorpusPrinter.printLocalRecallRepairCorpus()
            try CorpusPrinter.printRemoteLeakSegment()
            try CorpusPrinter.printGates()
            try CorpusPrinter.printOperationalReadiness()
        case "build":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("build-regression-corpus.py").path] + forwarded)
                return
            }
            let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/regression-corpus")
            let sessions = try takeSessions(from: &forwarded, sessionsRoot: sessionsRoot)
            try build(sessions: sessions, extraArgs: forwarded)
            try CorpusPrinter.printBuild(outDir: outDir)
        case "evaluate":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("evaluate-regression-corpus.py").path] + forwarded)
                return
            }
            let outDir = PathURLs.fileURL(
                ArgumentEditing.peekOption("out-dir", in: forwarded)
                    ?? ArgumentEditing.peekOption("corpus-dir", in: forwarded)
                    ?? "sessions/_reports/regression-corpus"
            )
            try evaluate(extraArgs: forwarded)
            try CorpusPrinter.printEvaluation(outDir: outDir)
        case "train-audio-judge":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("train-audio-judge-v0.py").path] + forwarded)
                return
            }
            let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/audio-judge-v0")
            try trainAudioJudge(extraArgs: forwarded)
            try CorpusPrinter.printAudioJudge(outDir: outDir)
        case "taxonomy":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-audio-error-taxonomy.py").path] + forwarded)
                return
            }
            let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/audio-error-taxonomy")
            try taxonomy(extraArgs: forwarded)
            try CorpusPrinter.printTaxonomy(outDir: outDir)
        case "gate":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("check-corpus-gates.py").path] + forwarded)
                return
            }
            let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/corpus-gates")
            let status = try gates(extraArgs: forwarded, allowedExitCodes: [0, 1])
            try CorpusPrinter.printGates(outDir: outDir)
            if status != 0 {
                throw CLIError("corpus gate failed; see \(PathDisplay.display(outDir.appendingPathComponent("corpus_gates_report.md")))")
            }
        case "order":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                CorpusOrderHelp.print()
                return
            }
            let repair = ArgumentEditing.takeFlag("repair", from: &forwarded)
            let noSynthesize = ArgumentEditing.takeFlag("no-synthesize", from: &forwarded)
            let repairInputProfile = ArgumentEditing.takeOption("repair-input-profile", from: &forwarded) ?? "auto"
            let repairOutputProfile = ArgumentEditing.takeOption("repair-output-profile", from: &forwarded) ?? "order_repair_v1"
            let sessionQualityURL = PathURLs.fileURL(
                ArgumentEditing.peekOption("session-quality", in: forwarded)
                    ?? "sessions/_reports/session-quality/session_quality_report.json"
            )
            let sessionQualityOutDir = sessionQualityURL.deletingLastPathComponent().path
            let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/transcript-order")
            let sessions = try takeOptionalSessions(from: &forwarded, sessionsRoot: sessionsRoot)
            if repair {
                let repairSessions = sessions.isEmpty
                    ? try CorpusOrderRepair.sessions(sessionQuality: sessionQualityURL, sessionsRoot: sessionsRoot)
                    : sessions
                try CorpusOrderRepair.run(
                    sessions: repairSessions,
                    inputProfile: repairInputProfile,
                    outputProfile: repairOutputProfile,
                    synthesize: !noSynthesize
                )
                try reportSessionQuality(sessions: repairSessions, outDir: sessionQualityOutDir)
            }
            try transcriptOrder(sessions: sessions, extraArgs: forwarded)
            try CorpusPrinter.printTranscriptOrder(outDir: outDir)
        case "boundary":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(
                    try PythonRuntime.resolve(),
                    [try script("authoritative-boundary.py").path, "--help"]
                )
                return
            }
            let action = forwarded.first.map { value -> String in
                if ["freeze", "evaluate", "run"].contains(value) {
                    forwarded.removeFirst()
                    return value
                }
                return "run"
            } ?? "run"
            let status = try Tooling.runPathAllowingExitCodes(
                try PythonRuntime.resolve(),
                [
                    try script("authoritative-boundary.py").path,
                    action,
                    "--sessions-root",
                    sessionsRoot.path,
                ] + forwarded,
                allowedExitCodes: [0, 2]
            )
            if status != 0 {
                throw CLIError("authoritative boundary corpus gates did not pass; inspect sessions/_reports/authoritative-boundary-v1")
            }
        case "local-recall":
            try CorpusLocalRecallCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
        case "local-recall-repair":
            try CorpusLocalRecallRepairCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
        case "remote-leak":
            try CorpusRemoteLeakCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
        case "echo-candidate", "asr-positive-echo-candidate", "asr_positive_echo_candidate":
            try CorpusEchoCandidateCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
        case "live":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-live-corpus-gates.py").path] + forwarded)
                return
            }
            forwarded = LiveCommands.addDefaultLiveCorpusOutDir(forwarded, sessionsRoot: sessionsRoot)
            let liveReportOutDir = PathURLs.fileURL(
                ArgumentEditing.peekOption("out-dir", in: forwarded)
                    ?? "sessions/_reports/live-pipeline"
            )
            try Tooling.runPath(
                try PythonRuntime.resolve(),
                [try script("report-live-corpus-gates.py").path] + forwarded + ["--sessions-root", sessionsRoot.path]
            )
            try Tooling.runPath(
                try PythonRuntime.resolve(),
                [
                    try script("report-live-local-recall-hardening.py").path,
                    "--sessions-root", sessionsRoot.path,
                    "--scope-report", liveReportOutDir.appendingPathComponent(
                        "live_order_role_reconciliation_v1.json"
                    ).path,
                    "--out-dir", liveReportOutDir.path,
                    "--allow-missing-scope",
                ]
            )
        case "report":
            guard forwarded.isEmpty else { throw CLIError("corpus report does not support extra arguments") }
            try CorpusPrinter.printSessionQuality()
            try CorpusPrinter.printAvailableStatusReports()
        default:
            throw CLIError("unknown corpus command: \(subcommand)")
        }
    }

    private static func reportSessionQuality(sessions: [URL], outDir: String = "sessions/_reports/session-quality") throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-session-quality.py").path,
        ] + sessions.map(\.path) + [
            "--out-dir", outDir,
            "--write-session-readiness",
        ])
    }

    private static func build(sessions: [URL], extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("build-regression-corpus.py").path,
        ] + sessions.map(\.path) + extraArgs)
    }

    private static func evaluate(extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("evaluate-regression-corpus.py").path,
        ] + extraArgs)
    }

    private static func trainAudioJudge(extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("train-audio-judge-v0.py").path,
        ] + extraArgs)
    }

    private static func taxonomy(extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-audio-error-taxonomy.py").path,
        ] + extraArgs)
    }

    private static func gates(extraArgs: [String]) throws {
        _ = try gates(extraArgs: extraArgs, allowedExitCodes: [0])
    }

    @discardableResult
    private static func gates(extraArgs: [String], allowedExitCodes: Set<Int32>) throws -> Int32 {
        let python = try PythonRuntime.resolve()
        return try Tooling.runPathQuietAllowingExitCodes(python, [
            try script("check-corpus-gates.py").path,
        ] + extraArgs, allowedExitCodes: allowedExitCodes)
    }

    private static func transcriptOrder(sessions: [URL], extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-transcript-order-corpus.py").path,
        ] + sessions.map(\.path) + extraArgs)
    }

    private static func operationalReadiness() throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-operational-readiness.py").path,
        ])
    }

    private static func takeSessions(from args: inout [String], sessionsRoot: URL) throws -> [URL] {
        var targets: [String] = []
        while let first = args.first, !first.hasPrefix("--") {
            targets.append(first)
            args.removeFirst()
        }
        guard !targets.isEmpty else { throw CLIError("corpus command requires all, latest, or at least one session path") }
        if targets == ["all"] {
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            return sessions
        }
        return try targets.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
    }

    private static func takeOptionalSessions(from args: inout [String], sessionsRoot: URL) throws -> [URL] {
        var targets: [String] = []
        while let first = args.first, !first.hasPrefix("--") {
            targets.append(first)
            args.removeFirst()
        }
        if targets.isEmpty {
            return []
        }
        if targets == ["all"] {
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            return sessions
        }
        return try targets.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus script not found: \(url.path)")
        }
        return url
    }

}

enum CorpusHelp {
    static func print() {
        Swift.print("""
        usage:
          murmurmark corpus process all|latest|./session... [--per-label 16] [--max-items 160] [--sessions-root ./sessions]
          murmurmark corpus build all|latest|./session... [--per-label 16] [--max-items 160] [--sessions-root ./sessions]
          murmurmark corpus evaluate
          murmurmark corpus train-audio-judge
          murmurmark corpus taxonomy
          murmurmark corpus gate
          murmurmark corpus order [all|latest|./session...] [--repair] [--sessions-root ./sessions]
          murmurmark corpus local-recall [all|latest|./session...] [--audit] [--sessions-root ./sessions]
          murmurmark corpus local-recall-repair [all|latest|./session...] [--repair] [--sessions-root ./sessions]
          murmurmark corpus boundary [freeze|evaluate|run] [--sessions-root ./sessions]
          murmurmark corpus remote-leak [all|latest|./session...] [--plan] [--sessions-root ./sessions]
          murmurmark corpus echo-candidate [all|latest|./session...] [--run] [--sessions-root ./sessions]
          murmurmark corpus live [all|latest|./session...] [--refresh] [--target-live-sessions 3] [--sessions-root ./sessions]
          murmurmark corpus report

        Corpus commands operate on a local regression set and write reports under sessions/_reports/.
        Use `murmurmark corpus process all` for the normal full quality loop.
        Use `murmurmark corpus process --help` for the detailed process pipeline.
        """)
    }
}

enum CorpusProcessHelp {
    static func print() {
        Swift.print("""
        usage: murmurmark corpus process all|latest|./session... [options]

        Runs the local corpus quality loop:
          1. plan-remote-leak-segment-repair.py
          2. report-session-quality.py
          3. build-regression-corpus.py
          4. evaluate-regression-corpus.py
          5. train-audio-judge-v0.py
          6. report-audio-error-taxonomy.py
          7. report-operational-readiness.py
          8. report-transcript-order-corpus.py
          9. report-local-recall-corpus.py
          10. report-local-recall-repair-corpus.py
          11. report-remote-leak-segment-corpus.py
          12. check-corpus-gates.py

        Options:
          --sessions-root PATH  Sessions directory for all/latest. Default: sessions
          --per-label N         Forwarded to build-regression-corpus.py
          --max-items N         Forwarded to build-regression-corpus.py
          --copy-clips          Forwarded to build-regression-corpus.py
          --no-copy-clips       Forwarded to build-regression-corpus.py

        Order repair:
          murmurmark corpus order [all|latest|./session...] --repair
              [--repair-input-profile auto] [--repair-output-profile order_repair_v1]
              [--no-synthesize] [--sessions-root sessions]
          murmurmark corpus local-recall [all|latest|./session...] --audit
              [--audit-profile auto]
          murmurmark corpus local-recall-repair [all|latest|./session...] --repair
              [--repair-input-profile auto] [--repair-output-profile local_recall_repair_v1]
              [--no-synthesize]
          murmurmark corpus remote-leak [all|latest|./session...] --plan
              [--session-quality sessions/_reports/session-quality/session_quality_report.json]
          murmurmark corpus echo-candidate [all|latest|./session...] --run
              [--candidate coverage_v2_remote_gate_local_fir]
        """)
    }
}

enum CorpusEchoCandidateCommands {
    static func run(args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        if ArgumentEditing.hasHelpFlag(forwarded) {
            try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-asr-positive-echo-candidate-corpus.py").path] + forwarded)
            return
        }
        let run = ArgumentEditing.takeFlag("run", from: &forwarded)
        let candidate = ArgumentEditing.takeOption("candidate", from: &forwarded) ?? "coverage_v2_remote_gate_local_fir"
        let outDir = PathURLs.fileURL(
            ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/asr-positive-echo-candidate"
        )
        let sessions = try takeSessions(from: &forwarded, sessionsRoot: sessionsRoot)
        if run {
            try runCandidate(sessions: sessions, candidate: candidate)
        }
        var reportArgs = sessions.map(\.path) + ["--candidate", candidate]
        reportArgs += forwarded
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("report-asr-positive-echo-candidate-corpus.py").path,
        ] + reportArgs)
        try CorpusPrinter.printASRPositiveEchoCandidate(outDir: outDir)
    }

    private static func runCandidate(sessions: [URL], candidate: String) throws {
        let python = try PythonRuntime.resolve()
        for session in sessions {
            try Tooling.runPathQuiet(python, [
                try script("run-asr-positive-echo-candidate.py").path,
                session.path,
                "--candidate", candidate,
            ])
        }
    }

    private static func takeSessions(from args: inout [String], sessionsRoot: URL) throws -> [URL] {
        var targets: [String] = []
        while let first = args.first, !first.hasPrefix("--") {
            targets.append(first)
            args.removeFirst()
        }
        guard !targets.isEmpty else { throw CLIError("corpus echo-candidate requires all, latest, or at least one session path") }
        if targets == ["all"] {
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            return sessions
        }
        return try targets.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus echo-candidate script not found: \(url.path)")
        }
        return url
    }
}

enum CorpusRemoteLeakPlan {
    static func run(sessions: [URL]) throws {
        let python = try PythonRuntime.resolve()
        for session in sessions {
            try Tooling.runPathQuiet(python, [
                try script("plan-remote-leak-segment-repair.py").path,
                session.path,
            ])
        }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus remote-leak script not found: \(url.path)")
        }
        return url
    }
}

enum CorpusRemoteLeakCommands {
    static func run(args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        if ArgumentEditing.hasHelpFlag(forwarded) {
            try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-remote-leak-segment-corpus.py").path] + forwarded)
            return
        }
        let plan = ArgumentEditing.takeFlag("plan", from: &forwarded)
        let sessionQualityURL = PathURLs.fileURL(
            ArgumentEditing.peekOption("session-quality", in: forwarded)
                ?? "sessions/_reports/session-quality/session_quality_report.json"
        )
        let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/remote-leak-segment")
        let sessions = try takeOptionalSessions(from: &forwarded, sessionsRoot: sessionsRoot)
        if plan {
            let planSessions = sessions.isEmpty
                ? try CorpusOrderRepair.sessions(sessionQuality: sessionQualityURL, sessionsRoot: sessionsRoot)
                : sessions
            try CorpusRemoteLeakPlan.run(sessions: planSessions)
            try reportSessionQuality(sessions: planSessions, outDir: sessionQualityURL.deletingLastPathComponent().path)
        }
        try report(sessions: sessions, extraArgs: forwarded)
        try CorpusPrinter.printRemoteLeakSegment(outDir: outDir)
    }

    static func report(sessions: [URL], extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-remote-leak-segment-corpus.py").path,
        ] + sessions.map(\.path) + extraArgs)
    }

    private static func reportSessionQuality(sessions: [URL], outDir: String) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-session-quality.py").path,
        ] + sessions.map(\.path) + [
            "--out-dir", outDir,
            "--write-session-readiness",
        ])
    }

    private static func takeOptionalSessions(from args: inout [String], sessionsRoot: URL) throws -> [URL] {
        var targets: [String] = []
        while let first = args.first, !first.hasPrefix("--") {
            targets.append(first)
            args.removeFirst()
        }
        if targets.isEmpty {
            return []
        }
        if targets == ["all"] {
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            return sessions
        }
        return try targets.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus remote-leak script not found: \(url.path)")
        }
        return url
    }
}

enum CorpusLocalRecallCommands {
    static func run(args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        if ArgumentEditing.hasHelpFlag(forwarded) {
            try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-local-recall-corpus.py").path] + forwarded)
            return
        }
        let audit = ArgumentEditing.takeFlag("audit", from: &forwarded)
        let auditProfile = ArgumentEditing.takeOption("audit-profile", from: &forwarded) ?? "auto"
        let sessionQualityURL = PathURLs.fileURL(
            ArgumentEditing.peekOption("session-quality", in: forwarded)
                ?? "sessions/_reports/session-quality/session_quality_report.json"
        )
        let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/local-recall")
        let sessions = try takeOptionalSessions(from: &forwarded, sessionsRoot: sessionsRoot)
        if audit {
            let auditSessions = sessions.isEmpty
                ? try CorpusOrderRepair.sessions(sessionQuality: sessionQualityURL, sessionsRoot: sessionsRoot)
                : sessions
            try runAudits(sessions: auditSessions, profile: auditProfile)
            try reportSessionQuality(sessions: auditSessions, outDir: sessionQualityURL.deletingLastPathComponent().path)
        }
        try report(sessions: sessions, extraArgs: forwarded)
        try CorpusPrinter.printLocalRecallCorpus(outDir: outDir)
    }

    static func report(sessions: [URL], extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-local-recall-corpus.py").path,
        ] + sessions.map(\.path) + extraArgs)
    }

    private static func runAudits(sessions: [URL], profile: String) throws {
        let python = try PythonRuntime.resolve()
        for session in sessions {
            _ = try Tooling.runPathQuietAllowingExitCodes(python, [
                try script("audit-local-recall.py").path,
                session.path,
                "--profile", profile,
            ], allowedExitCodes: [0, 1])
        }
    }

    private static func reportSessionQuality(sessions: [URL], outDir: String) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-session-quality.py").path,
        ] + sessions.map(\.path) + [
            "--out-dir", outDir,
            "--write-session-readiness",
        ])
    }

    private static func takeOptionalSessions(from args: inout [String], sessionsRoot: URL) throws -> [URL] {
        var targets: [String] = []
        while let first = args.first, !first.hasPrefix("--") {
            targets.append(first)
            args.removeFirst()
        }
        if targets.isEmpty {
            return []
        }
        if targets == ["all"] {
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            return sessions
        }
        return try targets.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus local-recall script not found: \(url.path)")
        }
        return url
    }
}

enum CorpusLocalRecallRepairCommands {
    static func run(args: [String], sessionsRoot: URL) throws {
        var forwarded = args
        if ArgumentEditing.hasHelpFlag(forwarded) {
            try Tooling.runPath(try PythonRuntime.resolve(), [try script("report-local-recall-repair-corpus.py").path] + forwarded)
            return
        }
        let repair = ArgumentEditing.takeFlag("repair", from: &forwarded)
        let noSynthesize = ArgumentEditing.takeFlag("no-synthesize", from: &forwarded)
        let repairInputProfile = ArgumentEditing.takeOption("repair-input-profile", from: &forwarded) ?? "auto"
        let repairOutputProfile = ArgumentEditing.takeOption("repair-output-profile", from: &forwarded) ?? "local_recall_repair_v1"
        let sessionQualityURL = PathURLs.fileURL(
            ArgumentEditing.peekOption("session-quality", in: forwarded)
                ?? "sessions/_reports/session-quality/session_quality_report.json"
        )
        let outDir = PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: forwarded) ?? "sessions/_reports/local-recall-repair")
        let sessions = try takeOptionalSessions(from: &forwarded, sessionsRoot: sessionsRoot)
        if repair {
            let repairSessions = sessions.isEmpty
                ? try CorpusOrderRepair.sessions(sessionQuality: sessionQualityURL, sessionsRoot: sessionsRoot)
                : sessions
            try runRepairs(
                sessions: repairSessions,
                inputProfile: repairInputProfile,
                outputProfile: repairOutputProfile,
                synthesize: !noSynthesize
            )
            try reportSessionQuality(sessions: repairSessions, outDir: sessionQualityURL.deletingLastPathComponent().path)
        }
        try report(sessions: sessions, extraArgs: forwarded)
        try CorpusPrinter.printLocalRecallRepairCorpus(outDir: outDir)
    }

    static func report(sessions: [URL], extraArgs: [String]) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-local-recall-repair-corpus.py").path,
        ] + sessions.map(\.path) + extraArgs)
    }

    private static func runRepairs(sessions: [URL], inputProfile: String, outputProfile: String, synthesize: Bool) throws {
        let python = try PythonRuntime.resolve()
        for session in sessions {
            _ = try Tooling.runPathQuietAllowingExitCodes(python, [
                try script("audit-local-recall.py").path,
                session.path,
                "--profile", inputProfile,
            ], allowedExitCodes: [0, 1])
            let repairStatus = try Tooling.runPathQuietAllowingExitCodes(python, [
                try script("apply-local-recall-repair.py").path,
                session.path,
                "--input-profile", inputProfile,
                "--output-profile", outputProfile,
            ], allowedExitCodes: [0, 2])
            if repairStatus == 0, synthesize {
                _ = try Tooling.runPathQuietAllowingExitCodes(python, [
                    try script("synthesize-simple-extractive.py").path,
                    session.path,
                    "--transcript-profile", outputProfile,
                ], allowedExitCodes: [0, 2])
            }
        }
    }

    private static func reportSessionQuality(sessions: [URL], outDir: String) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-session-quality.py").path,
        ] + sessions.map(\.path) + [
            "--out-dir", outDir,
            "--write-session-readiness",
        ])
    }

    private static func takeOptionalSessions(from args: inout [String], sessionsRoot: URL) throws -> [URL] {
        var targets: [String] = []
        while let first = args.first, !first.hasPrefix("--") {
            targets.append(first)
            args.removeFirst()
        }
        if targets.isEmpty {
            return []
        }
        if targets == ["all"] {
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            return sessions
        }
        return try targets.map { try SessionResolver.resolve($0, sessionsRoot: sessionsRoot) }
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus local-recall-repair script not found: \(url.path)")
        }
        return url
    }
}

enum CorpusOrderRepair {
    static func run(sessions: [URL], inputProfile: String, outputProfile: String, synthesize: Bool) throws {
        let python = try PythonRuntime.resolve()
        for session in sessions {
            let auditStatus = try Tooling.runPathQuietAllowingExitCodes(python, [
                try script("audit-transcript-order.py").path,
                session.path,
                "--profile", inputProfile,
            ], allowedExitCodes: [0, 2])
            guard auditStatus == 0 else {
                continue
            }

            let repairStatus = try Tooling.runPathQuietAllowingExitCodes(python, [
                try script("apply-transcript-order-repair.py").path,
                session.path,
                "--input-profile", inputProfile,
                "--output-profile", outputProfile,
            ], allowedExitCodes: [0, 2])
            if repairStatus == 0, synthesize {
                _ = try Tooling.runPathQuietAllowingExitCodes(python, [
                    try script("synthesize-simple-extractive.py").path,
                    session.path,
                    "--transcript-profile", outputProfile,
                ], allowedExitCodes: [0, 2])
            }
        }
    }

    static func sessions(sessionQuality: URL, sessionsRoot: URL) throws -> [URL] {
        if FileManager.default.fileExists(atPath: sessionQuality.path),
           let rows = try JSONFiles.object(sessionQuality)["sessions"] as? [Any] {
            let sessions = rows.compactMap { row -> URL? in
                guard let dict = row as? [String: Any],
                      let session = dict["session"] as? String,
                      !session.isEmpty
                else {
                    return nil
                }
                return try? SessionResolver.resolve(session, sessionsRoot: sessionsRoot)
            }
            if !sessions.isEmpty {
                return sessions
            }
        }
        return try SessionResolver.all(in: sessionsRoot)
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts").appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("corpus script not found: \(url.path)")
        }
        return url
    }
}

enum CorpusOrderHelp {
    static func print() {
        Swift.print("""
        usage: murmurmark corpus order [all|latest|./session...] [--repair] [options]

        Aggregates transcript-order audits into a corpus report. With --repair, first refreshes
        the order audit, writes a conservative order_repair_v1 profile for each target session,
        refreshes session quality, then rebuilds the corpus order report.

        Options:
          --sessions-root PATH             Sessions directory for all/latest. Default: sessions
          --session-quality PATH           Input session quality report for aggregation.
                                           With --repair, its parent directory is refreshed first.
          --out-dir PATH                   Output directory. Default: sessions/_reports/transcript-order
          --max-review-items N             Max review rows in Markdown.
          --repair-input-profile NAME      Default: auto
          --repair-output-profile NAME     Default: order_repair_v1
          --no-synthesize                  Skip refreshing notes/verdict for the repair profile.
        """)
    }
}

enum ExportCommands {
    static func export(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }
        guard let target = args.first else {
            printHelp()
            return
        }
        var forwarded = Array(args.dropFirst())
        let config = try MurmurMarkConfig.load(from: ArgumentEditing.takeOption("config", from: &forwarded))
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &forwarded) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)
        try refreshOutcome(session)
        let effectiveArgs = config.exportDefaults(unless: forwarded) + forwarded
        let outDir = exportOutDir(from: effectiveArgs)
        let status = try Tooling.runPathQuietAllowingExitCodes(try PythonRuntime.resolve(), [
            try script().path,
            session.path,
        ] + effectiveArgs, allowedExitCodes: [0, 2])
        if status == 2 {
            try ExportPrinter.printBlocked(session: session, outDir: outDir)
            fflush(stdout)
            throw CLIError("export blocked; follow the printed next steps or pass --force for debugging")
        }
        try ExportPrinter.printManifest(session: session, outDir: outDir)
    }

    private static func script() throws -> URL {
        let url = PathURLs.fileURL("scripts/export-session-bundle.py")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("export script not found: \(url.path)")
        }
        return url
    }

    private static func refreshOutcome(_ session: URL) throws {
        let python = try PythonRuntime.resolve()
        try Tooling.runPathQuiet(python, [
            try script("report-session-quality.py").path,
            session.path,
            "--out-dir", session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
        try Tooling.runPathQuiet(python, [
            try script("evaluate-outcome.py").path,
            session.path,
        ])
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts/\(name)")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("script not found: \(url.path)")
        }
        return url
    }

    private static func exportOutDir(from args: [String]) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption("out-dir", in: args) ?? "exports/private")
    }

    private static func printHelp() {
        print("""
        usage: murmurmark export ./session|latest [--format markdown|obsidian] [--profile auto]
                               [--out-dir exports/private] [--include-json] [--force]
                               [--sessions-root ./sessions]

        Creates a local user-facing Markdown or Obsidian bundle. By default export refuses sessions
        with readiness export blockers; pass --force only after consciously accepting that risk.
        """)
    }
}

enum FinishCommands {
    private struct ExportRun {
        let format: String
        let status: Int32
        let manifestURL: URL
        let blockedURL: URL
        let manifest: [String: Any]?
        let blocked: [String: Any]?

        var succeeded: Bool {
            status == 0 && manifest != nil
        }
    }

    private struct ExportOptions {
        let format: String
        let profile: String
        let outDir: URL
        let includeJSON: Bool
        let force: Bool
    }

    static func finish(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let format = ArgumentEditing.takeOption("format", from: &remaining) ?? "markdown"
        let requestedProfile = ArgumentEditing.takeOption("profile", from: &remaining) ?? "auto"
        let outDir = PathURLs.fileURL(ArgumentEditing.takeOption("out-dir", from: &remaining) ?? "exports/private")
        let forceExport = ArgumentEditing.takeFlag("force-export", from: &remaining)
        let noJSON = ArgumentEditing.takeFlag("no-json", from: &remaining)
        let skipRetention = ArgumentEditing.takeFlag("skip-retention", from: &remaining)
        let policy = ArgumentEditing.takeOption("policy", from: &remaining)
        let provider = ArgumentEditing.takeOption("provider", from: &remaining)
        let target = remaining.isEmpty ? "latest" : remaining.removeFirst()
        guard remaining.isEmpty else {
            throw CLIError("unexpected finish arguments: \(remaining.joined(separator: " "))")
        }
        guard ["markdown", "obsidian"].contains(format) else {
            throw CLIError("finish --format must be markdown or obsidian")
        }

        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)

        let handoffProfile = requestedProfile == "auto"
            ? AuthoritativeHandoffState.selectedProfile(session)
            : nil
        if handoffProfile == nil {
            try refreshReadiness(session)
        }
        try ReadinessPrinter.printSession(session, label: "readiness")
        try ReadinessPrinter.printOutcome(session)

        let exportRun = try runExport(
            session: session,
            options: ExportOptions(
                format: format,
                profile: handoffProfile ?? requestedProfile,
                outDir: outDir,
                includeJSON: !noJSON,
                force: forceExport
            )
        )
        printExport(exportRun)

        if exportRun.succeeded {
            if skipRetention {
                print("")
                print("retention:")
                print("  status: skipped")
                print("  reason: --skip-retention")
            } else {
                try runRetention(session: session, manifestURL: exportRun.manifestURL, policy: policy, provider: provider)
            }
            printFinishReady(session: session, exportRun: exportRun)
        } else {
            printFinishBlocked(session: session, exportRun: exportRun)
        }
    }

    private static func refreshReadiness(_ session: URL) throws {
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("report-session-quality.py").path,
            session.path,
            "--out-dir", session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
        try refreshOutcome(session)
    }

    private static func refreshOutcome(_ session: URL) throws {
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), [
            try script("evaluate-outcome.py").path,
            session.path,
        ])
    }

    private static func runExport(session: URL, options: ExportOptions) throws -> ExportRun {
        var command = [
            try script("export-session-bundle.py").path,
            session.path,
            "--format", options.format,
            "--profile", options.profile,
            "--out-dir", options.outDir.path,
        ]
        if options.includeJSON {
            command.append("--include-json")
        }
        if options.force {
            command.append("--force")
        }

        let status = try Tooling.runPathQuietAllowingExitCodes(
            try PythonRuntime.resolve(),
            command,
            allowedExitCodes: [0, 2]
        )
        let manifestURL = options.outDir
            .appendingPathComponent(session.lastPathComponent)
            .appendingPathComponent("export_manifest.json")
        let blockedURL = options.outDir.appendingPathComponent("\(session.lastPathComponent).export_blocked.json")
        return ExportRun(
            format: options.format,
            status: status,
            manifestURL: manifestURL,
            blockedURL: blockedURL,
            manifest: try? JSONFiles.object(manifestURL),
            blocked: try? JSONFiles.object(blockedURL)
        )
    }

    private static func runRetention(session: URL, manifestURL: URL, policy: String?, provider: String?) throws {
        var planCommand = [
            try script("apply-retention-policy.py").path,
            session.path,
            "--export-manifest", manifestURL.path,
        ]
        if let policy {
            planCommand += ["--policy", policy]
        }
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), planCommand)

        var payloadCommand = [
            try script("build-provider-payload-manifest.py").path,
            session.path,
            "--export-manifest", manifestURL.path,
        ]
        if let policy {
            payloadCommand += ["--policy", policy]
        }
        if let provider {
            payloadCommand += ["--provider", provider]
        }
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), payloadCommand)
        try printRetention(session: session)
    }

    private static func printExport(_ exportRun: ExportRun) {
        print("")
        print("export:")
        print("  format: \(exportRun.format)")
        if let manifest = exportRun.manifest, exportRun.status == 0 {
            let files = dict(manifest["files"])
            print("  status: \(string(manifest["status"]) ?? "ok")")
            print("  manifest: \(PathDisplay.display(exportRun.manifestURL))")
            print("  profile: \(string(manifest["selected_profile"]) ?? "unknown")")
            print("  verdict: \(string(manifest["verdict"]) ?? "unknown")")
            for key in ["index", "obsidian_note", "quality_verdict_md", "notes_md", "transcript_md"] {
                if let path = exportedPath(key, files: files) {
                    print("  \(key): \(PathDisplay.display(path))")
                }
            }
            return
        }

        let blocked = exportRun.blocked ?? [:]
        let blockers = blocked["blockers"] as? [Any] ?? []
        let warnings = blocked["warnings"] as? [Any] ?? []
        print("  status: blocked")
        print("  report: \(PathDisplay.display(exportRun.blockedURL))")
        print("  profile: \(string(blocked["selected_profile"]) ?? string(blocked["requested_profile"]) ?? "unknown")")
        print("  blockers: \(compactJSON(blockers))")
        if !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        if let next = recommendedNext(from: blocked) {
            print("  recommended_next: \(next)")
        }
    }

    private static func printRetention(session: URL) throws {
        let planURL = session.appendingPathComponent("derived/retention/retention_plan.json")
        let payloadURL = session.appendingPathComponent("derived/retention/provider_payload_manifest.json")
        let plan = (try? JSONFiles.object(planURL)) ?? [:]
        let payload = (try? JSONFiles.object(payloadURL)) ?? [:]
        let planActions = plan["actions"] as? [[String: Any]] ?? []
        let payloadBlockers = payload["blockers"] as? [Any] ?? []

        print("")
        print("retention:")
        print("  plan: \(PathDisplay.display(planURL))")
        print("  payload: \(PathDisplay.display(payloadURL))")
        print("  raw_audio_files: \(planActions.count)")
        print("  can_apply: \(bool(plan["can_apply"]))")
        print("  applied: \(bool(plan["applied"]))")
        print("  payload_status: \(string(payload["status"]) ?? "unknown")")
        print("  sends_data: \(bool(payload["sends_data"]))")
        print("  raw_audio_included: \(bool(payload["raw_audio_included"]))")
        if !payloadBlockers.isEmpty {
            print("  payload_blockers: \(compactJSON(payloadBlockers))")
        }
    }

    private static func printFinishReady(session: URL, exportRun: ExportRun) {
        let files = dict(exportRun.manifest?["files"])
        let readPath = exportedPath("index", files: files)
            ?? exportedPath("obsidian_note", files: files)
            ?? exportedPath("notes_md", files: files)
            ?? exportedPath("transcript_md", files: files)
        let readCommand = readPath.map { "less \(PathDisplay.display($0))" }
            ?? "murmurmark export \(PathDisplay.display(session)) --format \(exportRun.format) --include-json"
        print("")
        print("finish:")
        print("  status: ready")
        print("  bundle_manifest: \(PathDisplay.display(exportRun.manifestURL))")
        print("  recommended_next: \(readCommand)")
        FinalNextPrinter.print(readCommand)
    }

    private static func printFinishBlocked(session: URL, exportRun: ExportRun) {
        let next = recommendedNext(from: exportRun.blocked ?? [:])
            ?? readinessNext(session: session)
            ?? "murmurmark status \(PathDisplay.display(session))"
        print("")
        print("finish:")
        print("  status: blocked")
        print("  bundle_manifest: not_created")
        print("  recommended_next: \(next)")
        FinalNextPrinter.print(next)
    }

    private static func readinessNext(session: URL) -> String? {
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: url.path),
              let payload = try? JSONFiles.object(url)
        else {
            return nil
        }
        if let next = string(payload["recommended_next"]) {
            return next
        }
        let commands = payload["next_commands"] as? [[String: Any]] ?? []
        return ReadinessPrinter.preferredNextCommand(commands)
    }

    private static func recommendedNext(from payload: [String: Any]) -> String? {
        if let nextCommands = payload["next_commands"] as? [[String: Any]],
           let command = ReadinessPrinter.preferredNextCommand(nextCommands) {
            return command
        }
        return string(payload["next"])
    }

    private static func script(_ name: String) throws -> URL {
        let url = PathURLs.fileURL("scripts/\(name)")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("script not found: \(url.path)")
        }
        return url
    }

    private static func exportedPath(_ key: String, files: [String: Any]) -> URL? {
        guard let item = files[key] as? [String: Any],
              let path = string(item["path"])
        else {
            return nil
        }
        return PathURLs.fileURL(path)
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        guard let value = value as? String else { return nil }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private static func bool(_ value: Any?) -> Bool {
        if let value = value as? Bool {
            return value
        }
        if let value = value as? String {
            return ["true", "yes", "1"].contains(value.lowercased())
        }
        return false
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }

    private static func printHelp() {
        print("""
        usage: murmurmark finish [./session|latest] [--format markdown|obsidian] [--profile auto]
                                [--out-dir exports/private] [--force-export] [--no-json]
                                [--skip-retention] [--policy ./policy.json] [--provider name]
                                [--sessions-root ./sessions]

        Refreshes readiness, attempts a normal guarded export with JSON evidence included by default,
        then writes retention plan and provider payload manifest when the export succeeds. It never
        deletes raw audio; use `murmurmark retention apply` explicitly for that.
        """)
    }
}

enum RetentionCommands {
    static func retention(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }

        var remaining = args
        let mode = remaining.removeFirst()
        guard ["plan", "apply", "payload"].contains(mode) else {
            throw CLIError("retention requires plan, apply, or payload")
        }
        guard let target = remaining.first else {
            throw CLIError("retention \(mode) requires a session path or latest")
        }
        remaining.removeFirst()

        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        var command = [try script(mode).path, session.path]
        if mode == "apply" {
            command.append("--apply")
        }
        command += remaining

        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)
        try Tooling.runPathQuiet(try PythonRuntime.resolve(), command)
        try RetentionPrinter.printSummary(mode: mode, session: session, args: remaining)
    }

    private static func script(_ mode: String) throws -> URL {
        let scriptName = mode == "payload" ? "build-provider-payload-manifest.py" : "apply-retention-policy.py"
        let url = PathURLs.fileURL("scripts/\(scriptName)")
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("retention script not found: \(url.path)")
        }
        return url
    }

    private static func printHelp() {
        print("""
        usage:
          murmurmark retention plan ./session|latest [--policy ./policy.json] [--export-manifest ./export_manifest.json]
          murmurmark retention apply ./session|latest --confirm-delete-raw [--policy ./policy.json] [--export-manifest ./export_manifest.json]
          murmurmark retention payload ./session|latest [--policy ./policy.json] [--export-manifest ./export_manifest.json] [--provider name]

        Plan mode writes SESSION/derived/retention/retention_plan.json and does not delete files.
        Apply mode can delete raw CAF only when the policy requests it, export manifest is successful,
        and --confirm-delete-raw is present.
        Payload mode writes SESSION/derived/retention/provider_payload_manifest.json and sends nothing.
        """)
    }
}

enum RetentionPrinter {
    static func printSummary(mode: String, session: URL, args: [String]) throws {
        if mode == "payload" {
            try printPayload(session: session, args: args)
        } else {
            try printPlan(session: session, args: args)
        }
    }

    private static func printPlan(session: URL, args: [String]) throws {
        let planURL = outputURL(
            option: "out",
            in: args,
            defaultURL: session.appendingPathComponent("derived/retention/retention_plan.json")
        )
        guard FileManager.default.fileExists(atPath: planURL.path) else {
            print("")
            print("retention:")
            print("  plan: missing")
            print("  expected: \(PathDisplay.display(planURL))")
            return
        }

        let payload = try JSONFiles.object(planURL)
        let actions = payload["actions"] as? [[String: Any]] ?? []
        let warnings = payload["warnings"] as? [Any] ?? []
        let export = payload["export_manifest"] as? [String: Any] ?? [:]
        let auditLog = string(payload["audit_log"]).map(PathURLs.fileURL)
        let actionCounts = count(actions, by: "planned_action")
        let appliedCounts = count(actions, by: "applied_action")
        let exportManifest = exportPath(from: export)
        let exportManifestReady = exportManifest.map { FileManager.default.fileExists(atPath: $0.path) } ?? false
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let openCommands = payload["open_commands"] as? [[String: Any]] ?? []
        let status = retentionStatus(payload: payload, export: export, actionCounts: actionCounts)

        print("")
        print("retention:")
        print("  plan: \(PathDisplay.display(planURL))")
        print("  mode: \(string(payload["mode"]) ?? "unknown")")
        print("  status: \(status)")
        print("  raw_audio_files: \(actions.count)")
        print("  actions: \(compactJSON(actionCounts))")
        if !appliedCounts.isEmpty {
            print("  applied_actions: \(compactJSON(appliedCounts))")
        }
        print("  can_apply: \(bool(payload["can_apply"]))")
        print("  applied: \(bool(payload["applied"]))")
        if let exportManifest {
            print("  export_manifest: \(PathDisplay.display(exportManifest))")
            print("  export_successful: \(bool(export["successful"]))")
            if let exportStatus = string(export["status"]) {
                print("  export_status: \(exportStatus)")
            }
            if let exportReason = string(export["reason"]) {
                print("  export_reason: \(exportReason)")
            }
        }
        if let auditLog {
            print("  audit_log: \(PathDisplay.display(auditLog))")
        }
        if !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let recommendedNext: String
        if let command = ReadinessPrinter.preferredNextCommand(nextCommands) {
            recommendedNext = command
        } else if let exportManifest, exportManifestReady {
            recommendedNext = "murmurmark retention payload \(PathDisplay.display(session)) --export-manifest \(PathDisplay.display(exportManifest))"
        } else if let readinessNext = readinessNextCommand(session: session) {
            recommendedNext = readinessNext
        } else {
            recommendedNext = "murmurmark export \(PathDisplay.display(session)) --format markdown --include-json"
        }
        print("  recommended_next: \(recommendedNext)")
        if !openCommands.isEmpty {
            print("  open:")
            for item in openCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        }
        print("  next:")
        if nextCommands.isEmpty {
            print("    \(recommendedNext)")
        } else {
            for item in nextCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        }
        print("")
        print("next: \(recommendedNext)")
    }

    private static func retentionStatus(
        payload: [String: Any],
        export: [String: Any],
        actionCounts: [String: Int]
    ) -> String {
        if bool(payload["applied"]) {
            return "applied"
        }
        if !bool(export["found"]) {
            return "waiting_for_export"
        }
        if !bool(export["valid"]) {
            return "blocked_invalid_export_manifest"
        }
        if !bool(export["successful"]) {
            return "waiting_for_successful_export"
        }
        if bool(payload["can_apply"]) {
            return "ready_to_apply"
        }
        if actionCounts.keys.allSatisfy({ $0 == "keep_raw_audio" }) {
            return "ready_no_raw_deletion"
        }
        return "ready"
    }

    private static func readinessNextCommand(session: URL) -> String? {
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: readinessURL.path),
              let payload = try? JSONFiles.object(readinessURL)
        else {
            return nil
        }
        let gate = string(payload["use_gate"]) ?? ""
        let exportBlockers = strings(payload["export_blockers"])
        let reviewBlockers = strings(payload["review_blockers"])
        let needsEarlierStep = gate != "ready_for_notes" || !exportBlockers.isEmpty || !reviewBlockers.isEmpty
        guard needsEarlierStep else { return nil }
        if let nextCommands = payload["next_commands"] as? [[String: Any]],
           let command = ReadinessPrinter.preferredNextCommand(nextCommands) {
            return command
        }
        let sessionPath = PathDisplay.display(session)
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains("pipeline_incomplete") {
            return "murmurmark process \(sessionPath)"
        }
        if gate == "review_first" || !reviewBlockers.isEmpty || !exportBlockers.isEmpty {
            return "murmurmark review next \(sessionPath)"
        }
        return nil
    }

    private static func printPayload(session: URL, args: [String]) throws {
        let manifestURL = outputURL(
            option: "out",
            in: args,
            defaultURL: session.appendingPathComponent("derived/retention/provider_payload_manifest.json")
        )
        guard FileManager.default.fileExists(atPath: manifestURL.path) else {
            print("")
            print("retention_payload:")
            print("  manifest: missing")
            print("  expected: \(PathDisplay.display(manifestURL))")
            return
        }

        let payload = try JSONFiles.object(manifestURL)
        let blockers = payload["blockers"] as? [Any] ?? []
        let warnings = payload["warnings"] as? [Any] ?? []
        let export = payload["export_manifest"] as? [String: Any] ?? [:]
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let openCommands = payload["open_commands"] as? [[String: Any]] ?? []

        print("")
        print("retention_payload:")
        print("  manifest: \(PathDisplay.display(manifestURL))")
        print("  status: \(string(payload["status"]) ?? "unknown")")
        print("  provider: \(string(payload["provider"]) ?? "unknown")")
        print("  payload_files: \(int(payload["payload_file_count"]))")
        print("  payload_bytes: \(int(payload["payload_bytes"]))")
        print("  sends_data: \(bool(payload["sends_data"]))")
        print("  raw_audio_included: \(bool(payload["raw_audio_included"]))")
        if let exportManifest = exportPath(from: export) {
            print("  export_manifest: \(PathDisplay.display(exportManifest))")
        }
        if !blockers.isEmpty {
            print("  blockers: \(compactJSON(blockers))")
        }
        if !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let recommendedNext = ReadinessPrinter.preferredNextCommand(nextCommands)
            ?? "less \(PathDisplay.display(manifestURL))"
        print("  recommended_next: \(recommendedNext)")
        if !openCommands.isEmpty {
            print("  open:")
            for item in openCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        }
        print("  next:")
        if nextCommands.isEmpty {
            print("    \(recommendedNext)")
        } else {
            for item in nextCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        }
        print("")
        print("next: \(recommendedNext)")
    }

    private static func outputURL(option: String, in args: [String], defaultURL: URL) -> URL {
        PathURLs.fileURL(ArgumentEditing.peekOption(option, in: args) ?? defaultURL.path)
    }

    private static func exportPath(from payload: [String: Any]) -> URL? {
        guard let path = string(payload["path"]), !path.isEmpty else { return nil }
        return PathURLs.fileURL(path)
    }

    private static func count(_ items: [[String: Any]], by key: String) -> [String: Int] {
        var result: [String: Int] = [:]
        for item in items {
            guard let value = string(item[key]), !value.isEmpty else { continue }
            result[value, default: 0] += 1
        }
        return result
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func strings(_ value: Any?) -> [String] {
        (value as? [Any] ?? []).map { String(describing: $0) }
    }

    private static func bool(_ value: Any?) -> Bool {
        if let value = value as? Bool {
            return value
        }
        if let value = value as? String {
            return ["true", "yes", "1"].contains(value.lowercased())
        }
        return false
    }

    private static func int(_ value: Any?) -> Int {
        if let value = value as? Int {
            return value
        }
        if let value = value as? NSNumber {
            return value.intValue
        }
        if let value = value as? String, let parsed = Int(value) {
            return parsed
        }
        return 0
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }
}

enum ConfigCommands {
    static func config(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag(args) {
            printHelp()
            return
        }
        let subcommand = args.first ?? "print"
        var forwarded = Array(args.dropFirst())
        switch subcommand {
        case "init":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printHelp()
                return
            }
            try initConfig(&forwarded)
        case "print":
            if ArgumentEditing.hasHelpFlag(forwarded) {
                printHelp()
                return
            }
            let config = try MurmurMarkConfig.load(from: ArgumentEditing.takeOption("config", from: &forwarded))
            guard forwarded.isEmpty else { throw CLIError("config print only supports --config") }
            ConfigPrinter.print(config)
        default:
            throw CLIError("unknown config command: \(subcommand)")
        }
    }

    private static func printHelp() {
        print("""
        usage:
          murmurmark config init [--config murmurmark.config.json] [--force]
          murmurmark config print [--config murmurmark.config.json]

        Config lookup order:
          1. --config PATH
          2. MURMURMARK_CONFIG
          3. ./murmurmark.config.json when it exists

        Local config is ignored by git. Start from:
          murmurmark config init
        """)
    }

    private static func initConfig(_ args: inout [String]) throws {
        let force = ArgumentEditing.takeFlag("force", from: &args)
        let destinationValue = ArgumentEditing.takeOption("config", from: &args) ?? "murmurmark.config.json"
        guard args.isEmpty else {
            throw CLIError("config init only supports --config and --force")
        }

        let source = PathURLs.fileURL("murmurmark.config.example.json")
        guard FileManager.default.fileExists(atPath: source.path) else {
            throw CLIError("config example not found: \(PathDisplay.display(source))")
        }

        let destination = PathURLs.fileURL(destinationValue)
        if FileManager.default.fileExists(atPath: destination.path) {
            if !force {
                Swift.print("config:")
                Swift.print("  path: \(PathDisplay.display(destination))")
                Swift.print("  loaded: true")
                Swift.print("  changed: false")
                Swift.print("  reason: already_exists")
                Swift.print("next:")
                Swift.print("  murmurmark config print --config \(PathDisplay.display(destination))")
                return
            }
            try FileManager.default.removeItem(at: destination)
        }

        try FileManager.default.createDirectory(
            at: destination.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try FileManager.default.copyItem(at: source, to: destination)

        Swift.print("config:")
        Swift.print("  created: \(PathDisplay.display(destination))")
        Swift.print("  source: \(PathDisplay.display(source))")
        Swift.print("next:")
        Swift.print("  murmurmark config print --config \(PathDisplay.display(destination))")
    }
}

struct MurmurMarkConfig {
    let url: URL?
    let raw: [String: Any]

    var loaded: Bool {
        url != nil
    }

    static func load(from explicitPath: String?) throws -> MurmurMarkConfig {
        if let explicitPath {
            let url = PathURLs.fileURL(explicitPath)
            return try loadRequired(url)
        }
        if let env = ProcessInfo.processInfo.environment["MURMURMARK_CONFIG"], !env.isEmpty {
            return try loadRequired(PathURLs.fileURL(env))
        }
        let local = PathURLs.fileURL("murmurmark.config.json")
        if FileManager.default.fileExists(atPath: local.path) {
            return try loadRequired(local)
        }
        return MurmurMarkConfig(url: nil, raw: [:])
    }

    private static func loadRequired(_ url: URL) throws -> MurmurMarkConfig {
        let raw = try JSONFiles.object(url)
        if let schema = raw["schema"] as? String, schema != "murmurmark.config/v1" {
            throw CLIError("unsupported config schema: \(schema)")
        }
        return MurmurMarkConfig(url: url, raw: raw)
    }

    func processDefaults(unless args: [String]) -> [String] {
        var defaults: [String] = []
        appendString(section: "transcription", key: "model", option: "model", unless: args, to: &defaults, expandHomePath: true)
        appendString(section: "transcription", key: "language", option: "language", unless: args, to: &defaults)
        appendString(section: "transcription", key: "prompt_file", option: "prompt-file", unless: args, to: &defaults, expandHomePath: true)
        return defaults
    }

    func exportDefaults(unless args: [String]) -> [String] {
        var defaults: [String] = []
        appendString(section: "export", key: "format", option: "format", unless: args, to: &defaults)
        appendString(section: "export", key: "profile", option: "profile", unless: args, to: &defaults)
        appendString(section: "export", key: "out_dir", option: "out-dir", unless: args, to: &defaults, expandHomePath: true)
        appendFlag(section: "export", key: "include_json", option: "include-json", unless: args, to: &defaults)
        appendFlag(section: "export", key: "force", option: "force", unless: args, to: &defaults)
        return defaults
    }

    func section(_ name: String) -> [String: Any] {
        raw[name] as? [String: Any] ?? [:]
    }

    private func string(section sectionName: String, key: String) -> String? {
        guard let value = section(sectionName)[key] as? String else { return nil }
        return value.isEmpty ? nil : value
    }

    private func bool(section sectionName: String, key: String) -> Bool {
        if let value = section(sectionName)[key] as? Bool {
            return value
        }
        if let value = section(sectionName)[key] as? String {
            return ["true", "yes", "1"].contains(value.lowercased())
        }
        return false
    }

    private func appendString(
        section sectionName: String,
        key: String,
        option: String,
        unless args: [String],
        to defaults: inout [String],
        expandHomePath: Bool = false
    ) {
        guard !ArgumentEditing.hasOption(option, in: args), let value = string(section: sectionName, key: key) else { return }
        defaults += ["--\(option)", expandHomePath ? expandHome(value) : value]
    }

    private func appendFlag(section sectionName: String, key: String, option: String, unless args: [String], to defaults: inout [String]) {
        guard !ArgumentEditing.hasOption(option, in: args), bool(section: sectionName, key: key) else { return }
        defaults.append("--\(option)")
    }

    private func expandHome(_ value: String) -> String {
        value.hasPrefix("~") ? PathURLs.fileURL(value).path : value
    }
}

enum ConfigPrinter {
    static func print(_ config: MurmurMarkConfig) {
        Swift.print("config:")
        if let url = config.url {
            Swift.print("  path: \(PathDisplay.display(url))")
            Swift.print("  loaded: true")
        } else {
            Swift.print("  path: murmurmark.config.json")
            Swift.print("  loaded: false")
            Swift.print("  next: murmurmark config init")
        }
        Swift.print("  transcription: \(compactJSON(config.section("transcription")))")
        Swift.print("  export: \(compactJSON(config.section("export")))")
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "{}"
        }
        return text
    }
}

extension Commands {
    static func inspect(_ args: [String]) throws {
        if ArgumentEditing.hasHelpFlag(args) {
            print("""
            usage: murmurmark inspect ./session|latest [--echo] [--sessions-root ./sessions]

            Prints raw session package health: manifest status, capture mode, mic and
            remote file counts, bytes, frames and durations. Pass --echo to include
            Echo Guard diagnostics when they exist.
            """)
            return
        }

        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        let showEcho = ArgumentEditing.takeFlag("echo", from: &remaining)
        guard remaining.count == 1, let target = remaining.first else {
            throw CLIError("usage: murmurmark inspect ./session|latest [--echo] [--sessions-root ./sessions]")
        }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let data = try Data(contentsOf: session.appendingPathComponent("session.json"))
        let manifest = try JSONDecoder().decode(SessionManifest.self, from: data)

        print("session_id: \(manifest.sessionID)")
        print("status: \(manifest.status)")
        print("capture_mode: \(manifest.captureMode)")
        print("created_at: \(manifest.createdAt)")
        print("ended_at: \(manifest.endedAt ?? "-")")
        print("health: \(manifest.health.summary)")
        if manifest.health.partial == true || manifest.status == "partial" {
            print("partial: true")
        }
        if let stopReason = manifest.health.stopReason {
            print("stop_reason: \(stopReason)")
        }
        if let explicitStop = manifest.health.explicitStop {
            print("explicit_stop: \(explicitStop)")
        }
        if let actualDuration = manifest.health.actualDurationSec {
            print(String(format: "actual_duration: %.2fs", actualDuration))
        }
        if let requestedDuration = manifest.health.requestedDurationSec {
            print(String(format: "requested_duration: %.2fs", requestedDuration))
        }
        if let restartCount = manifest.health.screenCaptureRestartCount {
            print("screen_capture_restarts: \(restartCount)")
        }

        for source in ["mic", "remote"] {
            let entries = manifest.files[source] ?? []
            let bytes = entries.reduce(Int64(0)) { total, entry in
                let url = session.appendingPathComponent(entry.path)
                let size = ((try? FileManager.default.attributesOfItem(atPath: url.path)[.size]) as? NSNumber)?.int64Value ?? 0
                return total + size
            }
            let frames = entries.reduce(Int64(0)) { $0 + $1.frames }
            let duration = entries.first.map { Double(frames) / Double(max($0.sampleRate, 1)) } ?? 0
            print("\(source): files=\(entries.count) bytes=\(bytes) frames=\(frames) duration=\(String(format: "%.2f", duration))s")
        }

        if !manifest.health.warnings.isEmpty {
            print("warnings:")
            for warning in manifest.health.warnings {
                print("- \(warning)")
            }
        }

        if let partial = CaptureHealthState.partialInfo(session: session) {
            print("partial_handoff:")
            print("  reason: \(partial.reason)")
            print("  recommended_next: \(CaptureHealthState.preferredPartialNext(session: session))")
            print("  next:")
            for item in CaptureHealthState.partialNextCommands(session: session) {
                if let command = item["command"] {
                    print("    \(command)")
                }
            }
        }

        if showEcho {
            try EchoGuard.inspect(session: session)
        }
    }

    static func preprocess(_ args: [String]) throws {
        guard let first = args.first else { throw CLIError("preprocess requires a session path") }
        let session = PathURLs.fileURL(first)
        let options = try Options(Array(args.dropFirst()))
        let echoMode = options.string("echo") ?? "diagnostic"

        switch echoMode {
        case "off":
            print("echo: off")
        case "diagnostic":
            let report = try EchoGuard.runDiagnostic(session: session)
            print("echo diagnostics: \(report.summary.bleedDetected ? "bleed detected" : "no probable bleed")")
            if let medianDelay = report.summary.medianDelayMs {
                print("median_delay_ms: \(medianDelay)")
            }
            print("segments_with_probable_bleed: \(report.summary.segmentsWithProbableBleed)")
            print("diagnostics: \(session.appendingPathComponent(EchoGuard.Paths.diagnostics).path)")
            print("segments: \(session.appendingPathComponent(EchoGuard.Paths.segments).path)")
        case "clean", "conservative", "experimental_aggressive":
            let engine = options.string("echo-engine") ?? "linear_baseline"
            let profile = options.string("echo-profile") ?? (echoMode == "experimental_aggressive" ? "experimental_aggressive" : "conservative")
            let policy = options.string("echo-policy") ?? "preserve_local"
            let report = try EchoGuard.runClean(session: session, engine: engine, profile: profile, policy: policy)
            print("echo cleanup: \(report.decision.acceptedForASR ? "accepted for ASR" : "rejected; using raw mic")")
            print("engine: \(report.engine.name)")
            print("mic_for_asr: \(report.decision.micForASR)")
            print("clean_mic: \(report.outputs.cleanMic)")
            if let roleMaskedMic = report.outputs.roleMaskedMic {
                print("role_masked_mic: \(roleMaskedMic)")
            }
            print("suppression_report: \(session.appendingPathComponent(EchoGuard.Paths.suppressionReport).path)")
        default:
            throw CLIError("unsupported echo mode: \(echoMode)")
        }
    }

    static func reconcileTranscript(_ args: [String]) throws {
        guard let first = args.first else { throw CLIError("reconcile-transcript requires a session path") }
        let session = PathURLs.fileURL(first)
        let options = try Options(Array(args.dropFirst()))
        let transcript = options.url("in") ?? session.appendingPathComponent(TranscriptEchoGuard.Paths.transcript)
        let output = options.url("out") ?? transcript
        let qualityReport = options.url("quality-report") ?? session.appendingPathComponent(TranscriptEchoGuard.Paths.qualityReport)
        let textThreshold = options.double("text-threshold") ?? 0.55
        let timeTolerance = options.double("time-tolerance") ?? 1.0

        let report = try TranscriptEchoGuard.reconcile(
            session: session,
            options: TranscriptEchoGuardOptions(
                transcriptURL: transcript,
                outputURL: output,
                qualityReportURL: qualityReport,
                textThreshold: textThreshold,
                timeTolerance: timeTolerance
            )
        )

        print("transcript echo guard: \(report.summary.matchedMicUtterances) mic utterance(s) excluded from me role")
        print("transcript: \(output.path)")
        print("quality_report: \(qualityReport.path)")
        print("reconciliation_report: \(session.appendingPathComponent(TranscriptEchoGuard.Paths.reconciliationReport).path)")
    }

    static func exportAudio(_ args: [String]) throws {
        guard let first = args.first else { throw CLIError("export-audio requires a session path") }
        let options = try Options(Array(args.dropFirst()))
        let session = PathURLs.fileURL(first)
        let sampleRate = options.int("sample-rate") ?? 16000
        let output = options.url("out") ?? session.appendingPathComponent("derived/asr")
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)

        let pairs = [
            AudioExportSource(name: "mic", relativePath: selectedMicExportPath(session: session)),
            AudioExportSource(name: "remote", relativePath: "audio/remote/000001.caf"),
        ]

        for source in pairs {
            let input = session.appendingPathComponent(source.relativePath)
            guard FileManager.default.fileExists(atPath: input.path) else {
                print("skip \(source.name): missing \(source.relativePath)")
                continue
            }
            let target = output.appendingPathComponent("\(source.name).wav")
            print("exporting \(source.name) from \(source.relativePath): \(target.path)")
            try Tooling.run("ffmpeg", [
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel", "error",
                "-i", input.path,
                "-map", "0:a:0",
                "-vn",
                "-ac", "1",
                "-ar", "\(sampleRate)",
                target.path,
            ])
            print("done \(source.name): \(target.path)")
        }

        let job = session.appendingPathComponent("pipeline_job.json")
        if FileManager.default.fileExists(atPath: job.path) {
            print("pipeline_job: \(job.path)")
        }
    }

    private static func selectedMicExportPath(session: URL) -> String {
        let selected = EchoGuard.Paths.micForASR
        if FileManager.default.fileExists(atPath: session.appendingPathComponent(selected).path) {
            return selected
        }
        return "audio/mic/000001.caf"
    }
}

struct AudioExportSource {
    let name: String
    let relativePath: String
}

enum MicrophoneCaptureBackend: String {
    case screenCaptureKit = "screencapturekit"
    case voiceProcessing = "voice-processing"

    static func parse(_ value: String) throws -> MicrophoneCaptureBackend {
        guard let backend = MicrophoneCaptureBackend(rawValue: value) else {
            throw CLIError("unsupported mic backend: \(value)")
        }
        return backend
    }

    var captureBackendName: String {
        switch self {
        case .screenCaptureKit:
            return "screencapturekit_microphone"
        case .voiceProcessing:
            return "av_audio_engine_voice_processing"
        }
    }
}

enum RemoteCaptureBackend: String {
    case screenCaptureKit = "screencapturekit"
    case audioInput = "audio-input"

    static func parse(_ value: String) throws -> RemoteCaptureBackend {
        guard let backend = RemoteCaptureBackend(rawValue: value) else {
            throw CLIError("unsupported remote backend: \(value)")
        }
        return backend
    }

    var captureBackendName: String {
        switch self {
        case .screenCaptureKit:
            return "screencapturekit_audio"
        case .audioInput:
            return "avcapture_audio_input"
        }
    }
}

final class SessionRecorder: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    let outputDirectory: URL
    let targetBundleID: String?
    let microphoneID: String
    let microphoneBackend: MicrophoneCaptureBackend
    let remoteBackend: RemoteCaptureBackend
    let remoteDeviceID: String?
    let duration: TimeInterval?
    let sampleRate: Int
    let channelCount: Int
    let livePipelineEnabled: Bool
    let liveSegmentSeconds: TimeInterval
    let liveOverlapSeconds: TimeInterval
    let liveWorkerEnabled: Bool
    let liveFinalizeEnabled: Bool
    let liveConsoleEnabled: Bool
    let experimentID: String?
    let handoffEnabled: Bool

    private let fileManager = FileManager.default
    private let queue = DispatchQueue(label: "murmurmark.capture.samples")
    private let stateQueue = DispatchQueue(label: "murmurmark.capture.state")
    private var stream: SCStream?
    private var screenCaptureFilter: SCContentFilter?
    private var screenCaptureConfiguration: SCStreamConfiguration?
    private var micWriter: AudioFileWriter?
    private var voiceProcessingMic: VoiceProcessingMicCapture?
    private var remoteWriter: AudioFileWriter?
    private var remoteInputCapture: AudioInputDeviceCapture?
    private var liveSegments: AsyncLiveSegmentCapture?
    private var experimentLivePreview: AsyncCommittedLiveSegmentCapture?
    private var liveWorker: LivePipelineWorker?
    private var liveConsole: LivePreviewConsole?
    private var rawSidecarCommits: RawSegmentCommitTracker?
    private var events: EventLog?
    private var warnings: [String] = []
    private var targetDisplayName = "System Audio"
    private var targetPIDStrategy = "screen_capture_filter"
    private var startDate = Date()
    private var stopDate: Date?
    private var finalStopReason: StopReason?
    private var stoppingRequested = false
    private var streamStoppedUnexpectedly = false
    private var restartingScreenCapture = false
    private var screenCaptureRestartCount = 0
    private var lastSampleDate: Date?
    private var screenCaptureSampleBufferCount = 0
    private var captureSilenceWarningCount = 0
    private var captureSilenceRestartCount = 0
    private var recordingActivity: NSObjectProtocol?

    init(
        outputDirectory: URL,
        targetBundleID: String?,
        microphoneID: String,
        microphoneBackend: MicrophoneCaptureBackend,
        remoteBackend: RemoteCaptureBackend,
        remoteDeviceID: String?,
        duration: TimeInterval?,
        sampleRate: Int,
        channelCount: Int,
        livePipelineEnabled: Bool = false,
        liveSegmentSeconds: TimeInterval = 60,
        liveOverlapSeconds: TimeInterval = 5,
        liveWorkerEnabled: Bool = false,
        liveFinalizeEnabled: Bool = false,
        liveConsoleEnabled: Bool = false,
        experimentID: String? = nil,
        handoffEnabled: Bool = true
    ) {
        self.outputDirectory = outputDirectory
        self.targetBundleID = targetBundleID
        self.microphoneID = microphoneID
        self.microphoneBackend = microphoneBackend
        self.remoteBackend = remoteBackend
        self.remoteDeviceID = remoteDeviceID
        self.duration = duration
        self.sampleRate = sampleRate
        self.channelCount = channelCount
        self.livePipelineEnabled = livePipelineEnabled
        self.liveSegmentSeconds = liveSegmentSeconds
        self.liveOverlapSeconds = liveOverlapSeconds
        self.liveWorkerEnabled = liveWorkerEnabled
        self.liveFinalizeEnabled = liveFinalizeEnabled
        self.liveConsoleEnabled = liveConsoleEnabled
        self.experimentID = experimentID
        self.handoffEnabled = handoffEnabled
    }

    func run() async throws -> StopReason {
        let recordingLock = try RecordingProcessLock.acquire(for: outputDirectory)
        defer {
            recordingLock.release()
        }
        try prepareDirectories()
        let eventLog = try EventLog(url: outputDirectory.appendingPathComponent("events.jsonl"))
        events = eventLog
        do {
            beginRecordingActivity()
            defer {
                endRecordingActivity()
            }
            if microphoneBackend == .voiceProcessing, microphoneID != "default" {
                throw CLIError("voice-processing mic backend currently supports only --mic default")
            }
            if remoteBackend == .audioInput, remoteDeviceID == nil {
                throw CLIError("audio-input remote backend requires --remote-device from list-audio-devices")
            }

            try eventLog.write(
                type: "capture.prepare",
                fields: [
                    "backend": "screencapturekit_system",
                    "mic_backend": microphoneBackend.rawValue,
                    "remote_backend": remoteBackend.rawValue,
                    "remote_device": remoteDeviceID ?? "",
                    "live_pipeline": livePipelineEnabled,
                    "experiment": experimentID ?? "",
                ]
            )
            try eventLog.write(
                type: "power.activity_started",
                fields: [
                    "display_sleep_disabled": true,
                    "idle_system_sleep_disabled": true,
                    "reason": "keep ScreenCaptureKit capture source available during recording",
                ]
            )
            if livePipelineEnabled {
                let liveSegmentMaxPendingSamples = AsyncLiveSegmentCapture.resolveMaxPendingSamples()
                let liveSegmentWriteDelayMilliseconds = AsyncLiveSegmentCapture.resolveWriteDelayMilliseconds()
                let capture = try AsyncLiveSegmentCapture(
                    sessionDirectory: outputDirectory,
                    segmentSeconds: liveSegmentSeconds,
                    overlapSeconds: liveOverlapSeconds,
                    maxPendingSamples: liveSegmentMaxPendingSamples,
                    artificialWriteDelayMilliseconds: liveSegmentWriteDelayMilliseconds,
                    warningHandler: { [weak self] warning in
                        self?.appendWarning(warning)
                    }
                )
                liveSegments = capture
                try eventLog.write(
                    type: "live_pipeline.prepare",
                    fields: [
                        "segment_sec": liveSegmentSeconds,
                        "overlap_sec": liveOverlapSeconds,
                        "worker_enabled": liveWorkerEnabled,
                        "finalize_enabled": liveFinalizeEnabled,
                        "writer_mode": "async_bounded_queue",
                        "callback_policy": "raw_write_then_nonblocking_live_enqueue",
                        "max_pending_samples": liveSegmentMaxPendingSamples,
                        "artificial_write_delay_ms": liveSegmentWriteDelayMilliseconds,
                        "segments": "derived/live/segments.jsonl",
                    ]
                )
            }
            if let experimentID {
                let maxPendingCommits = RawSegmentCommitTracker.resolveMaxPendingCommits()
                let tracker = try RawSegmentCommitTracker(
                    sessionDirectory: outputDirectory,
                    experimentID: experimentID,
                    segmentSeconds: liveSegmentSeconds,
                    maxPendingCommits: maxPendingCommits,
                    warningHandler: { [weak self] warning in
                        self?.appendWarning(warning)
                    }
                )
                rawSidecarCommits = tracker
                let maxPendingPackets = AsyncCommittedLiveSegmentCapture.resolveMaxPendingPackets()
                let maxPendingSeconds = AsyncCommittedLiveSegmentCapture.resolveMaxPendingSeconds()
                let livePreviewWriteDelayMilliseconds = AsyncCommittedLiveSegmentCapture.resolveWriteDelayMilliseconds()
                let livePreview = try AsyncCommittedLiveSegmentCapture(
                    sessionDirectory: outputDirectory,
                    experimentID: experimentID,
                    segmentSeconds: liveSegmentSeconds,
                    overlapSeconds: liveOverlapSeconds,
                    maxPendingPackets: maxPendingPackets,
                    maxPendingSeconds: maxPendingSeconds,
                    artificialWriteDelayMilliseconds: livePreviewWriteDelayMilliseconds,
                    warningHandler: { [weak self] warning in
                        self?.appendWarning(warning)
                    }
                )
                experimentLivePreview = livePreview
                try eventLog.write(
                    type: "experiment_sidecar.prepare",
                    fields: [
                        "experiment_id": experimentID,
                        "segment_sec": liveSegmentSeconds,
                        "overlap_sec": liveOverlapSeconds,
                        "worker_enabled": liveWorkerEnabled,
                        "console_enabled": liveConsoleEnabled,
                        "writer_mode": "committed_pcm_queue_v1",
                        "fallback_writer_mode": "raw_segment_commit_log",
                        "callback_policy": "raw_write_then_nonblocking_committed_pcm_enqueue",
                        "max_pending_commits": maxPendingCommits,
                        "max_pending_pcm_packets": maxPendingPackets,
                        "artificial_write_delay_ms": livePreviewWriteDelayMilliseconds,
                        "commits": "derived/experiments/\(experimentID)/raw_segment_commits.jsonl",
                        "segments": "derived/live/segments.jsonl",
                    ]
                )
            }

            if remoteBackend == .audioInput, let remoteDeviceID {
                let capture = try AudioInputDeviceCapture(
                    deviceID: remoteDeviceID,
                    outputURL: outputDirectory.appendingPathComponent("audio/remote/000001.caf"),
                    source: "remote"
                )
                remoteInputCapture = capture
                targetDisplayName = capture.deviceName
                targetPIDStrategy = "audio_input_device"
            }

            if needsScreenCaptureKit {
                let content = try await ScreenCaptureContent.current()
                guard let display = content.displays.first else {
                    throw CLIError("no shareable display found")
                }

                let applications = try selectApplications(from: content)
                let filter: SCContentFilter
                if let applications, !applications.isEmpty {
                    filter = SCContentFilter(display: display, including: applications, exceptingWindows: [])
                    if remoteBackend == .screenCaptureKit {
                        targetDisplayName = applications.map(\.applicationName).joined(separator: ", ")
                        targetPIDStrategy = "screen_capture_including_applications"
                    }
                } else {
                    let currentPID = getpid()
                    let current = content.applications.filter { $0.processID == currentPID }
                    filter = SCContentFilter(display: display, excludingApplications: current, exceptingWindows: [])
                    if remoteBackend == .screenCaptureKit {
                        targetPIDStrategy = "screen_capture_system_excluding_self"
                    }
                }

                let config = SCStreamConfiguration()
                config.width = 2
                config.height = 2
                config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
                config.queueDepth = 3
                config.showsCursor = false
                config.capturesAudio = remoteBackend == .screenCaptureKit
                // Keep this false: on macOS 26.5.1, excluding current-process audio
                // can also silence terminal-launched or otherwise related system audio.
                config.excludesCurrentProcessAudio = false
                config.sampleRate = sampleRate
                config.channelCount = channelCount
                config.captureMicrophone = microphoneBackend == .screenCaptureKit
                if microphoneBackend == .screenCaptureKit, microphoneID != "default" {
                    config.microphoneCaptureDeviceID = microphoneID
                }

                screenCaptureFilter = filter
                screenCaptureConfiguration = config
            }

            if microphoneBackend == .voiceProcessing {
                let capture = VoiceProcessingMicCapture(
                    outputURL: outputDirectory.appendingPathComponent("audio/mic/000001.caf")
                )
                voiceProcessingMic = capture
            }

            startDate = Date()
            var startedFields: [String: Any] = [
                "target": targetBundleID ?? "system",
                "mic": microphoneID,
                "mic_backend": microphoneBackend.rawValue,
                "remote_backend": remoteBackend.rawValue,
                "remote_device": remoteDeviceID ?? "",
            ]
            if let duration {
                startedFields["duration_sec"] = duration
            }
            try eventLog.write(type: "capture.started", fields: startedFields)
            try voiceProcessingMic?.start()
            try remoteInputCapture?.start()
            if needsScreenCaptureKit {
                try await startScreenCaptureStream()
            }
            if liveWorkerEnabled {
                try startLiveWorker(eventLog: eventLog)
            }
            if let duration {
                print("recording \(String(format: "%.1f", duration))s -> \(outputDirectory.path)")
            } else {
                print("recording until Ctrl-C -> \(outputDirectory.path)")
            }
            if experimentID != nil {
                if liveConsoleEnabled {
                    print("live preview: inline; batch remains authoritative (disable with --live-no-console)")
                    startLiveConsole(eventLog: eventLog)
                } else {
                    print("live preview: murmurmark live watch \(PathDisplay.display(outputDirectory))")
                }
            }
            let stopReason = try await waitForRecordingStop()
            let finalizationSignalGuard = CaptureFinalizationSignalGuard()
            finalizationSignalGuard.start()
            defer { finalizationSignalGuard.cancel() }
            finalStopReason = stopReason
            if stopReason == .interrupt {
                print("\nstopping...")
            } else if stopReason.isUnexpectedCaptureStop {
                print("\ncapture stopped before explicit user stop; finalizing partial session...")
            }
            await stopScreenCaptureStream()
            stopRemoteInputCapture()
            stopVoiceProcessingMic()
            stopDate = Date()
            let actualDuration = max(0.0, (stopDate ?? Date()).timeIntervalSince(startDate))
            try padScreenCaptureWritersToDuration(actualDuration)
            liveSegments?.closeAll(finalDurationSeconds: actualDuration)
            experimentLivePreview?.closeAll(finalDurationSeconds: actualDuration)
            rawSidecarCommits?.closeAll(finalFramesBySource: [
                "mic": micFramesWritten() ?? 0,
                "remote": remoteFramesWritten() ?? 0,
            ])
            let preFinishAudioCoverage = min(
                writerCoverage(
                    frames: micFramesWritten(),
                    sampleRate: micSampleRateWritten() ?? Double(sampleRate),
                    actualDuration: actualDuration
                ),
                writerCoverage(
                    frames: remoteFramesWritten(),
                    sampleRate: remoteSampleRateWritten() ?? Double(sampleRate),
                    actualDuration: actualDuration
                )
            )
            let severePreFinishAudioCoverageGap = actualDuration >= 60 && preFinishAudioCoverage < 0.80
            let finalizedAsPartial = stopReason.isUnexpectedCaptureStop || severePreFinishAudioCoverageGap
            var stoppedFields: [String: Any] = [
                "reason": stopReason.rawValue,
                "partial": finalizedAsPartial,
                "explicit_stop": stopReason.isExplicitStop,
                "actual_duration_sec": Double(round(actualDuration * 1000) / 1000),
                "screen_capture_restart_count": screenCaptureRestartCount,
            ]
            if severePreFinishAudioCoverageGap {
                stoppedFields["audio_coverage_ratio"] = Double((preFinishAudioCoverage * 1000).rounded() / 1000)
            }
            if let duration {
                stoppedFields["requested_duration_sec"] = duration
            }
            try eventLog.write(type: "capture.stopped", fields: stoppedFields)
            try finish()
            if let worker = liveWorker {
                let workerWaitSeconds = livePipelineWorkerFinalizationWaitSeconds(
                    capturedDuration: max(
                        actualDuration,
                        liveSegments?.capturedDurationSeconds() ?? 0,
                        experimentLivePreview?.capturedDurationSeconds() ?? 0
                    )
                )
                let exited = worker.wait(seconds: workerWaitSeconds)
                try? eventLog.write(
                    type: "live_pipeline.worker_waited",
                    fields: [
                        "exited": exited,
                        "status": worker.terminationStatus.map { Int($0) } as Any? ?? NSNull(),
                        "report": "derived/live/live_pipeline_report.json",
                        "timeout_sec": Double((workerWaitSeconds * 1000).rounded() / 1000),
                    ]
                )
                if !exited {
                    appendWarning("live pipeline worker still running after \(Int(workerWaitSeconds.rounded()))s finalization wait")
                    worker.terminate()
                    markLiveWorkerTerminatedReport(
                        reason: "finalization_wait_timeout",
                        waitSeconds: workerWaitSeconds
                    )
                    try? eventLog.write(
                        type: "live_pipeline.worker_terminated",
                        fields: [
                            "reason": "finalization_wait_timeout",
                            "report": "derived/live/live_pipeline_report.json",
                        ]
                    )
                }
            }
            finishLiveConsole(eventLog: eventLog)
            if liveFinalizeEnabled, !finalizedAsPartial {
                runLiveFinalReconcile(eventLog: eventLog)
            }
            refreshExperimentContract(eventLog: eventLog)
            if finalizedAsPartial {
                print("partial session finalized")
                if handoffEnabled {
                    printInterruptedHandoff()
                }
            } else {
                print("done")
                if handoffEnabled {
                    printHandoff()
                }
            }
            return stopReason
        } catch {
            liveConsole?.terminate()
            liveWorker?.terminate()
            await stopScreenCaptureStream()
            try? remoteInputCapture?.stop()
            try? voiceProcessingMic?.stop()
            liveSegments?.closeAll()
            experimentLivePreview?.closeAll()
            rawSidecarCommits?.closeAll(finalFramesBySource: [:])
            try? eventLog.write(type: "capture.failed", fields: ["error": error.localizedDescription])
            try? fileManager.removeItem(at: outputDirectory.appendingPathComponent("session.lock"))
            throw CaptureErrors.enrich(error)
        }
    }

    private func startLiveWorker(eventLog: EventLog) throws {
        let worker = try LivePipelineWorker(sessionDirectory: outputDirectory)
        liveWorker = worker
        try worker.start()
        try eventLog.write(
            type: "live_pipeline.worker_started",
            fields: [
                "log": "derived/live/live_worker.log",
                "report": "derived/live/live_pipeline_report.json",
            ]
        )
    }

    private func startLiveConsole(eventLog: EventLog) {
        do {
            let console = try LivePreviewConsole(sessionDirectory: outputDirectory)
            liveConsole = console
            try console.start()
            try? eventLog.write(
                type: "live_preview.console_started",
                fields: [
                    "mode": "inline_delta",
                    "source": "derived/live/transcript.preview.md",
                ]
            )
        } catch {
            liveConsole = nil
            print("[warn] inline live preview unavailable: \(error.localizedDescription)")
            print("       recording continues; use `murmurmark live watch \(PathDisplay.display(outputDirectory))` if needed")
            try? eventLog.write(
                type: "live_preview.console_failed",
                fields: ["error": error.localizedDescription]
            )
        }
    }

    private func finishLiveConsole(eventLog: EventLog) {
        guard let console = liveConsole else { return }
        let exited = console.wait(seconds: 2.5)
        if !exited {
            console.terminate()
        }
        try? eventLog.write(
            type: "live_preview.console_finished",
            fields: [
                "exited": exited,
                "status": console.terminationStatus.map { Int($0) } as Any? ?? NSNull(),
            ]
        )
    }

    private func refreshExperimentContract(eventLog: EventLog) {
        guard let experimentID else { return }
        let script = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("scripts/experiment-sidecar-contract.py")
        guard fileManager.fileExists(atPath: script.path) else {
            appendWarning("experiment sidecar contract refresh skipped: script missing")
            return
        }
        do {
            try Tooling.runPathQuiet(
                try PythonRuntime.resolve(),
                [
                    script.path,
                    "refresh",
                    outputDirectory.path,
                    "--experiment",
                    experimentID,
                    "--sessions-root",
                    outputDirectory.deletingLastPathComponent().path,
                ]
            )
            try eventLog.write(
                type: "experiment_sidecar.contract_refreshed",
                fields: [
                    "experiment_id": experimentID,
                    "state": "derived/experiments/\(experimentID)/state.json",
                    "report": "derived/experiments/\(experimentID)/report.json",
                ]
            )
        } catch {
            appendWarning("experiment sidecar contract refresh failed: \(error.localizedDescription)")
            try? eventLog.write(
                type: "experiment_sidecar.contract_refresh_failed",
                fields: [
                    "experiment_id": experimentID,
                    "error": error.localizedDescription,
                ]
            )
        }
    }

    private func markLiveWorkerTerminatedReport(reason: String, waitSeconds: TimeInterval) {
        let reportURL = outputDirectory.appendingPathComponent("derived/live/live_pipeline_report.json")
        var payload = (try? JSONObject.readDictionary(from: reportURL)) ?? [:]
        if payload["schema"] == nil {
            payload["schema"] = "murmurmark.live_pipeline_report/v1"
        }
        if payload["mode"] == nil {
            payload["mode"] = "near_realtime_shadow"
        }
        if payload["generator"] == nil {
            payload["generator"] = ["name": "murmurmark-recorder-sidecar", "version": "0.1.0"]
        }
        var progress = payload["progress"] as? [String: Any] ?? [:]
        let recorderCaptured = max(
            liveSegments?.capturedDurationSeconds() ?? 0,
            experimentLivePreview?.capturedDurationSeconds() ?? 0
        )
        let reportCaptured = secondsValue(progress["captured_sec"]) ?? 0
        let captured = max(recorderCaptured, reportCaptured)
        if captured > 0 {
            progress["captured_sec"] = roundedSeconds(captured)
        }
        let processed = max(
            secondsValue(progress["processed_sec"]) ?? 0,
            secondsValue(progress["asr_sec"]) ?? 0,
            secondsValue(progress["draft_sec"]) ?? 0
        )
        let caughtUp = captured > 0 && processed + 0.5 >= captured
        let status = caughtUp ? "completed" : "completed_partial_draft"

        payload["status"] = status
        payload["current_stage"] = "terminated"
        payload["termination_reason"] = reason
        payload["terminated_at"] = DateStrings.iso8601(Date())
        payload["finalization_wait_timeout_sec"] = roundedSeconds(waitSeconds)
        payload["batch_authoritative"] = true
        payload["promotion_allowed"] = false
        payload["recommended_next"] = "murmurmark process \(PathDisplay.display(outputDirectory))"
        payload["progress"] = progress

        try? JSONObject.write(payload, to: reportURL)

        let stateURL = outputDirectory.appendingPathComponent("derived/live/live_pipeline_state.json")
        var state = (try? JSONObject.readDictionary(from: stateURL)) ?? [:]
        if state["schema"] == nil {
            state["schema"] = "murmurmark.live_pipeline_state/v1"
        }
        state["status"] = status
        state["current_stage"] = "terminated"
        state["termination_reason"] = reason
        state["updated_at"] = DateStrings.iso8601(Date())
        state["heartbeat_at"] = DateStrings.iso8601(Date())
        state["report"] = "derived/live/live_pipeline_report.json"
        state["progress"] = progress
        try? JSONObject.write(state, to: stateURL)
    }

    private func secondsValue(_ value: Any?) -> Double? {
        if let value = value as? Double {
            return value
        }
        if let value = value as? Int {
            return Double(value)
        }
        if let value = value as? NSNumber {
            return value.doubleValue
        }
        if let value = value as? String {
            return Double(value)
        }
        return nil
    }

    private func beginRecordingActivity() {
        guard recordingActivity == nil else { return }
        recordingActivity = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated, .idleSystemSleepDisabled, .idleDisplaySleepDisabled],
            reason: "MurmurMark recording requires an awake desktop capture session"
        )
    }

    private func endRecordingActivity() {
        guard let activity = recordingActivity else { return }
        ProcessInfo.processInfo.endActivity(activity)
        recordingActivity = nil
    }
}

struct LiveFinalReconcileSnapshot {
    let status: String
    let startedAt: Date
    let finishedAt: Date?
    let elapsed: TimeInterval?
    let command: [String]
    let error: String?
}

extension SessionRecorder {
    private struct CaptureSilenceSnapshot {
        let silentFor: TimeInterval
        let sinceStart: TimeInterval
        let sampleCount: Int
        let restartCount: Int
    }

    func stream(_: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard CMSampleBufferDataIsReady(sampleBuffer), CMSampleBufferGetNumSamples(sampleBuffer) > 0 else {
            return
        }
        stateQueue.sync {
            lastSampleDate = Date()
            screenCaptureSampleBufferCount += 1
        }

        do {
            switch type {
            case .audio:
                guard let format = AudioFileWriter.audioFormat(from: sampleBuffer) else {
                    throw CLIError("cannot read audio format for remote")
                }
                if remoteWriter == nil {
                    remoteWriter = try AudioFileWriter(
                        url: outputDirectory.appendingPathComponent("audio/remote/000001.caf"),
                        source: "remote",
                        timelineStartDate: startDate
                    )
                }
                let committedWrite = try remoteWriter?.writeReturningCommittedPCM(sampleBuffer, format: format)
                if let remoteWriter {
                    recordRawSidecarCommit(source: "remote", framesWritten: remoteWriter.framesWritten, sampleRate: format.sampleRate)
                }
                if let committedWrite {
                    enqueueExperimentLivePreview(committedWrite, source: "remote")
                }
                writeLiveSegmentSafely(sampleBuffer, source: "remote")
            case .microphone:
                guard let format = AudioFileWriter.audioFormat(from: sampleBuffer) else {
                    throw CLIError("cannot read audio format for mic")
                }
                if micWriter == nil {
                    micWriter = try AudioFileWriter(
                        url: outputDirectory.appendingPathComponent("audio/mic/000001.caf"),
                        source: "mic",
                        timelineStartDate: startDate
                    )
                }
                let committedWrite = try micWriter?.writeReturningCommittedPCM(sampleBuffer, format: format)
                if let micWriter {
                    recordRawSidecarCommit(source: "mic", framesWritten: micWriter.framesWritten, sampleRate: format.sampleRate)
                }
                if let committedWrite {
                    enqueueExperimentLivePreview(committedWrite, source: "mic")
                }
                writeLiveSegmentSafely(sampleBuffer, source: "mic")
            default:
                break
            }
        } catch {
            stateQueue.sync {
                warnings.append("write failed for \(type): \(error.localizedDescription)")
            }
        }
    }

    private func writeLiveSegmentSafely(_ sampleBuffer: CMSampleBuffer, source: String) {
        guard let liveSegments else { return }
        liveSegments.enqueue(sampleBuffer, source: source)
    }

    private func enqueueExperimentLivePreview(_ write: CommittedAudioWrite, source: String) {
        guard let experimentLivePreview else { return }
        experimentLivePreview.enqueue(CommittedAudioPacket(source: source, write: write))
    }

    private func recordRawSidecarCommit(source: String, framesWritten: AVAudioFramePosition, sampleRate: Double) {
        rawSidecarCommits?.recordWrite(source: source, framesWritten: framesWritten, sampleRate: sampleRate)
    }

    func stream(_: SCStream, didStopWithError error: Error) {
        stateQueue.sync {
            if !stoppingRequested && !restartingScreenCapture {
                streamStoppedUnexpectedly = true
                warnings.append("stream stopped with error: \(error.localizedDescription)")
            }
        }
    }

    private func waitForRecordingStop() async throws -> StopReason {
        try await withThrowingTaskGroup(of: StopReason.self) { group in
            group.addTask { [duration] in
                try await RecordingStopper.wait(duration: duration)
            }
            if needsScreenCaptureKit {
                group.addTask { [weak self] in
                    await self?.waitForUnexpectedStreamStop() ?? .streamStopped
                }
                group.addTask { [weak self] in
                    await self?.monitorCaptureSilence() ?? .terminated
                }
            }

            guard let reason = try await group.next() else {
                return .durationElapsed
            }
            group.cancelAll()
            return reason
        }
    }

    private func waitForUnexpectedStreamStop() async -> StopReason {
        while !Task.isCancelled {
            if consumeUnexpectedStreamStop() {
                if await restartScreenCaptureStream(reason: .streamStopped) {
                    continue
                }
                return .streamStopped
            }
            do {
                try await Task.sleep(nanoseconds: 250_000_000)
            } catch {
                break
            }
        }
        return .terminated
    }

    private func monitorCaptureSilence() async -> StopReason {
        let warningThreshold: TimeInterval = 60
        let initialRestartThreshold: TimeInterval = 10
        let initialFailureThreshold: TimeInterval = 45
        let maxInitialRestartCount = 3
        while !Task.isCancelled {
            let snapshot = stateQueue.sync { () -> CaptureSilenceSnapshot in
                let now = Date()
                let reference = lastSampleDate ?? startDate
                return CaptureSilenceSnapshot(
                    silentFor: now.timeIntervalSince(reference),
                    sinceStart: now.timeIntervalSince(startDate),
                    sampleCount: screenCaptureSampleBufferCount,
                    restartCount: captureSilenceRestartCount
                )
            }

            if snapshot.sampleCount == 0 {
                if snapshot.sinceStart >= initialFailureThreshold, snapshot.restartCount >= maxInitialRestartCount {
                    let warning = "capture produced no ScreenCaptureKit audio samples for "
                        + "\(Int(snapshot.sinceStart.rounded()))s after \(snapshot.restartCount) restart attempts"
                    appendWarning(warning)
                    print("\n\(warning); finalizing partial session...")
                    return .captureStalled
                }

                if snapshot.silentFor >= initialRestartThreshold, snapshot.restartCount < maxInitialRestartCount {
                    appendWarning("capture produced no ScreenCaptureKit audio samples for \(Int(snapshot.silentFor.rounded()))s")
                    if await restartScreenCaptureStream(reason: .captureStalled) {
                        stateQueue.sync {
                            captureSilenceRestartCount += 1
                        }
                        continue
                    }
                    return .captureStalled
                }
            }

            let warningBucket = Int(snapshot.silentFor / warningThreshold)
            if warningBucket > 0 {
                let shouldWarn = stateQueue.sync { () -> Bool in
                    if warningBucket > captureSilenceWarningCount {
                        captureSilenceWarningCount = warningBucket
                        return true
                    }
                    return false
                }
                if shouldWarn {
                    appendWarning(
                        "no ScreenCaptureKit audio samples for \(Int(snapshot.silentFor.rounded()))s; "
                            + "recording continues and timestamp gaps will be preserved"
                    )
                    if await restartScreenCaptureStream(reason: .captureStalled) {
                        continue
                    }
                    return .captureStalled
                }
            }
            do {
                try await Task.sleep(nanoseconds: 1_000_000_000)
            } catch {
                break
            }
        }
        return .terminated
    }

    private func startScreenCaptureStream() async throws {
        guard let filter = screenCaptureFilter, let config = screenCaptureConfiguration else {
            throw CLIError("ScreenCaptureKit stream is not configured")
        }
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        if remoteBackend == .screenCaptureKit {
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
        }
        if microphoneBackend == .screenCaptureKit {
            try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
        }
        self.stream = stream
        try await stream.startCapture()
        stateQueue.sync {
            lastSampleDate = Date()
        }
    }

    private func consumeUnexpectedStreamStop() -> Bool {
        stateQueue.sync {
            if streamStoppedUnexpectedly {
                streamStoppedUnexpectedly = false
                return true
            }
            return false
        }
    }

    private func restartScreenCaptureStream(reason: StopReason) async -> Bool {
        let shouldRestart = stateQueue.sync {
            if stoppingRequested {
                return false
            }
            if restartingScreenCapture {
                return true
            }
            restartingScreenCapture = true
            return true
        }
        guard shouldRestart else { return false }
        defer {
            stateQueue.sync {
                restartingScreenCapture = false
            }
        }

        let oldStream = stream
        stream = nil
        _ = try? await oldStream?.stopCapture()
        do {
            try await Task.sleep(nanoseconds: 500_000_000)
            try await startScreenCaptureStream()
            let restartCount = stateQueue.sync {
                screenCaptureRestartCount += 1
                return screenCaptureRestartCount
            }
            appendWarning("ScreenCaptureKit stream restarted after \(reason.rawValue)")
            try? events?.write(
                type: "capture.restarted",
                fields: [
                    "reason": reason.rawValue,
                    "restart_count": restartCount,
                ]
            )
            print("\ncapture stream restarted after \(reason.rawValue); recording continues...")
            return true
        } catch {
            appendWarning("ScreenCaptureKit restart failed after \(reason.rawValue): \(error.localizedDescription)")
            return false
        }
    }

    private func stopScreenCaptureStream() async {
        guard let stream else { return }
        stateQueue.sync {
            stoppingRequested = true
        }
        do {
            try await stream.stopCapture()
        } catch {
            appendWarning("stopCapture failed during finalization: \(error.localizedDescription)")
        }
    }

    private func stopRemoteInputCapture() {
        do {
            try remoteInputCapture?.stop()
        } catch {
            appendWarning("remote input stop failed during finalization: \(error.localizedDescription)")
        }
    }

    private func stopVoiceProcessingMic() {
        do {
            try voiceProcessingMic?.stop()
        } catch {
            appendWarning("voice-processing mic stop failed during finalization: \(error.localizedDescription)")
        }
    }

    private func appendWarning(_ warning: String) {
        stateQueue.sync {
            warnings.append(warning)
        }
    }

    private func padScreenCaptureWritersToDuration(_ duration: TimeInterval) throws {
        if microphoneBackend == .screenCaptureKit {
            try micWriter?.padToDuration(duration)
        }
        if remoteBackend == .screenCaptureKit {
            try remoteWriter?.padToDuration(duration)
        }
    }

    private func prepareDirectories() throws {
        if fileManager.fileExists(atPath: outputDirectory.path) {
            let contents = try fileManager.contentsOfDirectory(atPath: outputDirectory.path)
            guard contents.isEmpty else {
                throw CLIError("output directory already exists and is not empty: \(outputDirectory.path)")
            }
        }
        try fileManager.createDirectory(at: outputDirectory.appendingPathComponent("audio/mic"), withIntermediateDirectories: true)
        try fileManager.createDirectory(at: outputDirectory.appendingPathComponent("audio/remote"), withIntermediateDirectories: true)
        try fileManager.createDirectory(at: outputDirectory.appendingPathComponent("derived"), withIntermediateDirectories: true)
        fileManager.createFile(atPath: outputDirectory.appendingPathComponent("session.lock").path, contents: Data())
    }

    private func selectApplications(from content: SCShareableContent) throws -> [SCRunningApplication]? {
        guard let targetBundleID else { return nil }
        if targetBundleID == "system" || targetBundleID == "all" {
            return nil
        }
        let apps = content.applications.filter { $0.bundleIdentifier == targetBundleID }
        guard !apps.isEmpty else {
            throw CLIError("target bundle is not shareable/running: \(targetBundleID)")
        }
        return apps
    }

    private func finish() throws {
        micWriter?.close()
        remoteWriter?.close()

        let micFrames = microphoneBackend == .voiceProcessing ? voiceProcessingMic?.framesWritten : micWriter?.framesWritten
        let remoteFrames = remoteBackend == .audioInput ? remoteInputCapture?.framesWritten : remoteWriter?.framesWritten
        let micInfo = try fileInfo(path: "audio/mic/000001.caf", framesWritten: micFrames)
        let remoteInfo = try fileInfo(path: "audio/remote/000001.caf", framesWritten: remoteFrames)

        var finalWarnings = stateQueue.sync { warnings }
        if micInfo.frames == 0 {
            finalWarnings.append("mic track is empty")
        }
        if remoteInfo.frames == 0 {
            finalWarnings.append("remote track is empty")
        }
        let micSilent = addSilenceWarning(
            source: "mic",
            file: outputDirectory.appendingPathComponent(micInfo.path),
            to: &finalWarnings
        )
        let remoteSilent = addSilenceWarning(
            source: "remote",
            file: outputDirectory.appendingPathComponent(remoteInfo.path),
            to: &finalWarnings
        )

        let endedAt = stopDate ?? Date()
        let actualDuration = max(0.0, endedAt.timeIntervalSince(startDate))
        let stopReason = finalStopReason
        let trackCoverage = min(audioCoverage(micInfo, actualDuration: actualDuration), audioCoverage(remoteInfo, actualDuration: actualDuration))
        let severeAudioCoverageGap = actualDuration >= 60 && trackCoverage < 0.80
        let severeSilentCapture = actualDuration >= 30 && micSilent && remoteSilent
        if severeAudioCoverageGap {
            finalWarnings.append(
                String(
                    format: "captured audio covers only %.1f%% of wall-clock recording duration",
                    trackCoverage * 100.0
                )
            )
        }
        if severeSilentCapture {
            finalWarnings.append("capture finalized as partial because both mic and remote tracks are silent")
        }
        let partial = (stopReason?.isUnexpectedCaptureStop ?? false) || severeAudioCoverageGap || severeSilentCapture
        if stopReason?.isUnexpectedCaptureStop == true {
            finalWarnings.append("capture ended unexpectedly: \(stopReason?.rawValue ?? "unknown")")
        } else if severeAudioCoverageGap {
            finalWarnings.append("capture finalized as partial because audio coverage is incomplete")
        }
        let healthSummary: String
        if partial {
            healthSummary = "partial"
        } else if finalWarnings.isEmpty {
            healthSummary = "ok"
        } else {
            healthSummary = "warning"
        }
        let manifest = SessionManifest(
            schema: "murmurmark.session/v1",
            sessionID: SessionIDs.make(from: startDate),
            createdAt: DateStrings.iso8601(startDate),
            endedAt: DateStrings.iso8601(endedAt),
            appVersion: MurmurMark.version,
            captureMode: captureMode,
            status: partial ? "partial" : (finalWarnings.isEmpty ? "completed" : "completed_with_warnings"),
            target: TargetManifest(
                kind: targetKind,
                bundleID: targetKind == "bundle_id" ? targetBundleID : nil,
                displayName: targetDisplayName,
                pidStrategy: targetPIDStrategy
            ),
            microphone: MicrophoneManifest(
                deviceUID: microphoneID,
                displayName: microphoneName(for: microphoneID),
                captureBackend: microphoneBackend.captureBackendName
            ),
            remoteAudio: AudioManifest(
                backend: remoteBackend.captureBackendName,
                sampleRate: remoteInfo.sampleRate,
                channels: remoteInfo.channels,
                format: remoteBackend == .audioInput ? "caf:aac" : "caf:lpcm"
            ),
            micAudio: AudioManifest(
                backend: microphoneBackend.captureBackendName,
                sampleRate: micInfo.sampleRate,
                channels: micInfo.channels,
                format: "caf:lpcm"
            ),
            privacy: PrivacyManifest(
                networkAllowedDuringCapture: false,
                telemetry: false,
                rawAudioRetention: "keep_until_manual_delete"
            ),
            files: [
                "mic": [micInfo],
                "remote": [remoteInfo],
            ],
            health: HealthManifest(
                summary: healthSummary,
                warnings: finalWarnings,
                stopReason: stopReason?.rawValue,
                partial: partial,
                explicitStop: stopReason?.isExplicitStop,
                actualDurationSec: roundedSeconds(actualDuration),
                requestedDurationSec: duration.map(roundedSeconds),
                screenCaptureRestartCount: screenCaptureRestartCount,
                tracks: [
                    "mic": TrackHealthManifest(from: micInfo),
                    "remote": TrackHealthManifest(from: remoteInfo),
                ]
            )
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(manifest).write(to: outputDirectory.appendingPathComponent("session.json"), options: .atomic)
        try PipelineJob.default(for: manifest).write(to: outputDirectory.appendingPathComponent("pipeline_job.json"))
        try? fileManager.removeItem(at: outputDirectory.appendingPathComponent("session.lock"))
        try events?.write(
            type: "manifest.written",
            fields: [
                "health": manifest.health.summary,
                "partial": partial,
                "stop_reason": stopReason?.rawValue ?? "",
            ]
        )
    }

    private func roundedSeconds(_ value: Double) -> Double {
        Double(round(value * 1000) / 1000)
    }

    private func runLiveFinalReconcile(eventLog: EventLog) {
        let startedAt = Date()
        let command = [ExecutablePath.current(), "process", outputDirectory.path, "--skip-build"]
        print("live final reconcile -> batch-grade pipeline")
        writeLiveFinalReconcileReport(
            LiveFinalReconcileSnapshot(
                status: "running",
                startedAt: startedAt,
                finishedAt: nil,
                elapsed: nil,
                command: command,
                error: nil
            )
        )
        try? eventLog.write(
            type: "live_pipeline.final_reconcile_started",
            fields: [
                "report": "derived/live/final_reconcile_report.json",
                "command": command.joined(separator: " "),
            ]
        )

        let start = Date()
        let executable = URL(fileURLWithPath: command[0])
        let arguments = Array(command.dropFirst())
        do {
            try Tooling.runPath(executable, arguments)
            let elapsed = Date().timeIntervalSince(start)
            writeLiveFinalReconcileReport(
                LiveFinalReconcileSnapshot(
                    status: "passed",
                    startedAt: startedAt,
                    finishedAt: Date(),
                    elapsed: elapsed,
                    command: command,
                    error: nil
                )
            )
            try? eventLog.write(
                type: "live_pipeline.final_reconcile_completed",
                fields: [
                    "status": "passed",
                    "elapsed_sec": roundedSeconds(elapsed),
                    "report": "derived/live/final_reconcile_report.json",
                ]
            )
        } catch {
            let elapsed = Date().timeIntervalSince(start)
            let message = error.localizedDescription
            writeLiveFinalReconcileReport(
                LiveFinalReconcileSnapshot(
                    status: "failed",
                    startedAt: startedAt,
                    finishedAt: Date(),
                    elapsed: elapsed,
                    command: command,
                    error: message
                )
            )
            try? eventLog.write(
                type: "live_pipeline.final_reconcile_failed",
                fields: [
                    "status": "failed",
                    "elapsed_sec": roundedSeconds(elapsed),
                    "error": message,
                    "report": "derived/live/final_reconcile_report.json",
                ]
            )
            print("warning: live final reconcile failed: \(message)")
        }
    }

    private func writeLiveFinalReconcileReport(_ snapshot: LiveFinalReconcileSnapshot) {
        let pipelineReport = outputDirectory.appendingPathComponent("derived/pipeline-run/pipeline_run_report.json")
        let readiness = outputDirectory.appendingPathComponent("derived/readiness/session_readiness.json")
        let liveComparison = outputDirectory.appendingPathComponent("derived/live/live_batch_comparison.json")
        let liveASRCache = outputDirectory.appendingPathComponent("derived/live/live_asr_cache_report.json")
        let cacheReport = try? JSONObject.readDictionary(from: liveASRCache)
        let cacheStatus = cacheReport?["status"] as? String
        let cacheMaterialized = cacheReport?["materialized"] as? Bool ?? false
        let speedupStatus = cacheMaterialized ? "live_asr_cache_reused" : "fallback_batch_asr"
        let fallbackReason = cacheMaterialized ? [] : (cacheReport?["reasons"] ?? ["live_asr_cache_report_missing"])
        func relIfExists(_ url: URL) -> Any {
            fileManager.fileExists(atPath: url.path)
                ? RelativePaths.path(outputURL: url, relativeTo: outputDirectory)
                : NSNull()
        }
        var payload: [String: Any] = [
            "schema": "murmurmark.live_final_reconcile_report/v1",
            "mode": "near_realtime_shadow",
            "status": snapshot.status,
            "batch_authoritative": true,
            "promotion_allowed": false,
            "started_at": DateStrings.iso8601(snapshot.startedAt),
            "command": snapshot.command,
            "source_of_truth": "batch_pipeline",
            "live_cache_reuse": cacheMaterialized ? "materialized_raw_whisper_cache" : "not_used",
            "live_asr_cache_status": cacheStatus ?? "missing",
            "speedup_status": speedupStatus,
            "fallback_reason": fallbackReason,
            "outputs": [
                "pipeline_run_report": relIfExists(pipelineReport),
                "session_readiness": relIfExists(readiness),
                "live_asr_cache_report": relIfExists(liveASRCache),
                "live_batch_comparison": relIfExists(liveComparison),
            ],
        ]
        if let finishedAt = snapshot.finishedAt {
            payload["finished_at"] = DateStrings.iso8601(finishedAt)
        }
        if let elapsed = snapshot.elapsed {
            payload["elapsed_sec"] = roundedSeconds(elapsed)
        }
        if let error = snapshot.error {
            payload["error"] = error
            payload["recommended_next"] = "murmurmark process \(PathDisplay.display(outputDirectory))"
        } else {
            payload["recommended_next"] = snapshot.status == "passed"
                ? "murmurmark next \(PathDisplay.display(outputDirectory))"
                : "murmurmark status \(PathDisplay.display(outputDirectory))"
        }
        try? JSONObject.write(payload, to: outputDirectory.appendingPathComponent("derived/live/final_reconcile_report.json"))
    }

    private func livePipelineWorkerFinalizationWaitSeconds(capturedDuration: TimeInterval) -> TimeInterval {
        guard liveWorkerEnabled else { return 0 }
        let segmentCount = max(1.0, ceil(max(capturedDuration, liveSegmentSeconds) / max(liveSegmentSeconds, 1.0)))
        let estimatedTailWork = segmentCount
        // The optional recovery worker gets up to 30s for its newest-cutoff drain.
        // Leave enough outer-process headroom to persist its final state and report.
        return min(45.0, max(35.0, estimatedTailWork))
    }

    private func printHandoff() {
        let session = PathDisplay.display(outputDirectory)
        let readinessExists = fileManager.fileExists(
            atPath: outputDirectory.appendingPathComponent("derived/readiness/session_readiness.json").path
        )
        let finalReportExists = fileManager.fileExists(
            atPath: outputDirectory.appendingPathComponent("derived/live/final_reconcile_report.json").path
        )
        print("SESSION=\"\(session)\"")
        if livePipelineEnabled || experimentID != nil {
            if let experimentID {
                print("experiment: \(experimentID)")
                print("experiment_transport: committed_pcm_queue_v1")
            } else {
                print("live_pipeline: shadow")
            }
            print("live_preview: \(session)/derived/live/transcript.preview.md")
            print("live_draft_diagnostic: \(session)/derived/live/transcript.draft.md")
            print("live_report: \(session)/derived/live/live_pipeline_report.json")
            print("live_watch: murmurmark live watch \(session)")
            if finalReportExists {
                print("live_final_reconcile: \(session)/derived/live/final_reconcile_report.json")
            }
        }
        if readinessExists {
            print("recommended_next: murmurmark next \(session)")
            print("next:")
            print("  murmurmark status \(session)")
            print("  murmurmark next \(session)")
            print("  murmurmark finish \(session)")
            return
        }
        print("recommended_next: murmurmark process \(session)")
        print("next:")
        print("  murmurmark process \(session)")
        if livePipelineEnabled || experimentID != nil {
            print("  less \(session)/derived/live/transcript.preview.md")
            print("  less \(session)/derived/live/transcript.draft.md  # diagnostic candidates")
            print("  less \(session)/derived/live/live_pipeline_report.json")
            print("  murmurmark live watch \(session)")
            if let experimentID {
                print("  murmurmark experiment status \(session) --experiment \(experimentID)")
            }
        }
    }

    private func printInterruptedHandoff() {
        let session = PathDisplay.display(outputDirectory)
        print("SESSION=\"\(session)\"")
        print("recording_status: interrupted")
        print("warning: capture stopped before Ctrl-C or requested duration; treat this as a partial recording")
        print("recommended_next: murmurmark inspect \(session)")
        print("next:")
        print("  murmurmark inspect \(session)")
        print("  murmurmark status \(session)")
        print("  murmurmark process \(session) --allow-partial  # debug only")
    }
}

final class RecordingProcessLock {
    private let fileDescriptor: Int32
    private var released = false

    private init(fileDescriptor: Int32) {
        self.fileDescriptor = fileDescriptor
    }

    static func acquire(for outputDirectory: URL) throws -> RecordingProcessLock {
        let root = outputDirectory.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let lockURL = root.appendingPathComponent(".murmurmark-recording.lock")
        let fileDescriptor = open(lockURL.path, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR | S_IRGRP | S_IROTH)
        guard fileDescriptor >= 0 else {
            throw CLIError("cannot open recording lock: \(lockURL.path)")
        }

        guard flock(fileDescriptor, LOCK_EX | LOCK_NB) == 0 else {
            let existing: String?
            if let text = try? String(contentsOf: lockURL, encoding: .utf8) {
                let trimmed = text.trimmingCharacters(in: CharacterSet.whitespacesAndNewlines)
                existing = trimmed.isEmpty ? nil : trimmed
            } else {
                existing = nil
            }
            close(fileDescriptor)
            var message = """
            another MurmurMark recording is already active in \(root.path)
            Run only one `murmurmark record` process at a time. Use the live pilot runner to collect
            live evidence and batch output from the same raw recording.
            """
            if let existing {
                message += "\ncurrent lock: \(existing)"
            }
            throw CLIError(message)
        }

        let lock = RecordingProcessLock(fileDescriptor: fileDescriptor)
        lock.writeMetadata(outputDirectory: outputDirectory)
        return lock
    }

    func release() {
        guard !released else { return }
        released = true
        flock(fileDescriptor, LOCK_UN)
        close(fileDescriptor)
    }

    deinit {
        release()
    }

    private func writeMetadata(outputDirectory: URL) {
        let payload = "pid=\(getpid()) session=\(outputDirectory.path) started_at=\(DateStrings.iso8601(Date()))\n"
        _ = ftruncate(fileDescriptor, 0)
        _ = lseek(fileDescriptor, 0, SEEK_SET)
        let data = Data(payload.utf8)
        data.withUnsafeBytes { buffer in
            guard let baseAddress = buffer.baseAddress else { return }
            _ = Darwin.write(fileDescriptor, baseAddress, data.count)
        }
    }
}

extension SessionRecorder {
    private var needsScreenCaptureKit: Bool {
        remoteBackend == .screenCaptureKit || microphoneBackend == .screenCaptureKit
    }

    private var captureMode: String {
        if remoteBackend == .screenCaptureKit, microphoneBackend == .screenCaptureKit {
            return "screencapturekit_system"
        }
        return "remote_\(remoteBackend.rawValue)_mic_\(microphoneBackend.rawValue)".replacingOccurrences(of: "-", with: "_")
    }

    private var targetKind: String {
        if remoteBackend == .audioInput {
            return "audio_input_device"
        }
        return isSystemTarget ? "system_audio" : "bundle_id"
    }

    private var isSystemTarget: Bool {
        guard let targetBundleID else { return true }
        return targetBundleID == "system" || targetBundleID == "all"
    }

    private func audioCoverage(_ file: FileEntry, actualDuration: TimeInterval) -> Double {
        guard actualDuration > 0, file.sampleRate > 0 else { return 1.0 }
        let duration = Double(file.frames) / Double(file.sampleRate)
        return min(1.0, max(0.0, duration / actualDuration))
    }

    private func writerCoverage(
        frames: AVAudioFramePosition?,
        sampleRate: Double,
        actualDuration: TimeInterval
    ) -> Double {
        guard actualDuration > 0, sampleRate > 0, let frames else { return 0.0 }
        let duration = Double(frames) / sampleRate
        return min(1.0, max(0.0, duration / actualDuration))
    }

    private func micFramesWritten() -> AVAudioFramePosition? {
        microphoneBackend == .voiceProcessing ? voiceProcessingMic?.framesWritten : micWriter?.framesWritten
    }

    private func remoteFramesWritten() -> AVAudioFramePosition? {
        remoteBackend == .audioInput ? remoteInputCapture?.framesWritten : remoteWriter?.framesWritten
    }

    private func micSampleRateWritten() -> Double? {
        microphoneBackend == .voiceProcessing ? voiceProcessingMic?.sampleRate : micWriter?.sampleRate
    }

    private func remoteSampleRateWritten() -> Double? {
        remoteBackend == .audioInput ? remoteInputCapture?.sampleRate : remoteWriter?.sampleRate
    }

    private func fileInfo(path: String, framesWritten: AVAudioFramePosition?) throws -> FileEntry {
        let url = outputDirectory.appendingPathComponent(path)
        let file = try? AVAudioFile(forReading: url)
        let attrs = try? fileManager.attributesOfItem(atPath: url.path)
        let size = (attrs?[.size] as? NSNumber)?.int64Value ?? 0
        let nominalSampleRate = Int(file?.processingFormat.sampleRate ?? Double(sampleRate))
        let channels = Int(file?.processingFormat.channelCount ?? 0)
        return FileEntry(
            path: path,
            startHostTimeNs: 0,
            startSessionSec: 0,
            sampleRate: nominalSampleRate,
            frames: framesWritten ?? AVAudioFramePosition(0),
            channels: channels,
            bytes: size,
            sha256: nil
        )
    }

    private func addSilenceWarning(source: String, file: URL, to warnings: inout [String]) -> Bool {
        guard FileManager.default.fileExists(atPath: file.path),
              let rmsDb = try? AudioLevelProbe.rmsDb(url: file),
              rmsDb < -65
        else {
            return false
        }

        warnings.append("\(source) track appears silent or almost silent (RMS \(AudioLevelProbe.formatDb(rmsDb)))")
        return true
    }

    private func microphoneName(for id: String) -> String {
        if id == "default" { return "System Default Microphone" }
        let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone], mediaType: .audio, position: .unspecified).devices
        return devices.first { $0.uniqueID == id }?.localizedName ?? id
    }
}

struct CommittedAudioWrite: @unchecked Sendable {
    let format: AVAudioFormat
    let buffer: AVAudioPCMBuffer
    let gapFrames: AVAudioFramePosition
    let sampleFrames: AVAudioFramePosition
    let totalFramesWritten: AVAudioFramePosition
}

struct CommittedAudioPacket: @unchecked Sendable {
    let source: String
    let format: AVAudioFormat
    let buffer: AVAudioPCMBuffer
    let gapFrames: AVAudioFramePosition
    let sampleFrames: AVAudioFramePosition
    let totalFramesWritten: AVAudioFramePosition

    init(source: String, write: CommittedAudioWrite) {
        self.source = source
        format = write.format
        buffer = write.buffer
        gapFrames = write.gapFrames
        sampleFrames = write.sampleFrames
        totalFramesWritten = write.totalFramesWritten
    }
}

final class AudioFileWriter {
    let url: URL
    let source: String
    private var file: AVAudioFile?
    private var currentFormat: AVAudioFormat?
    private(set) var framesWritten: AVAudioFramePosition = 0
    private var firstPresentationTimeSec: Double?
    private var firstWallDate: Date?
    private var timelineResetCount = 0
    private(set) var insertedSilenceFrames: AVAudioFramePosition = 0

    var sampleRate: Double? {
        currentFormat?.sampleRate
    }

    init(url: URL, source: String, timelineStartDate: Date? = nil) throws {
        self.url = url
        self.source = source
        self.firstWallDate = timelineStartDate
    }

    static func audioFormat(from sampleBuffer: CMSampleBuffer) -> AVAudioFormat? {
        guard let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer),
              var asbd = CMAudioFormatDescriptionGetStreamBasicDescription(formatDescription)?.pointee,
              let format = AVAudioFormat(streamDescription: &asbd)
        else {
            return nil
        }
        return format
    }

    static func hasReadableAudioFormat(_ sampleBuffer: CMSampleBuffer) -> Bool {
        audioFormat(from: sampleBuffer) != nil
    }

    @discardableResult
    func write(_ sampleBuffer: CMSampleBuffer) throws -> AVAudioFramePosition {
        guard let format = Self.audioFormat(from: sampleBuffer) else {
            throw CLIError("cannot read audio format for \(source)")
        }
        return try write(sampleBuffer, format: format)
    }

    @discardableResult
    func write(_ sampleBuffer: CMSampleBuffer, format: AVAudioFormat) throws -> AVAudioFramePosition {
        let write = try writeReturningCommittedPCM(sampleBuffer, format: format)
        return write.totalFramesWritten
    }

    func writeReturningCommittedPCM(_ sampleBuffer: CMSampleBuffer, format: AVAudioFormat) throws -> CommittedAudioWrite {
        let buffer = try Self.pcmBuffer(from: sampleBuffer, format: format)

        try ensureFile(format: format)
        let gapFrames = timelineGapFrames(sampleBuffer: sampleBuffer, format: format)
        if gapFrames > 0 {
            try writeSilence(format: format, frames: gapFrames)
        }
        try writePCMBuffer(buffer)
        let sampleFrames = AVAudioFramePosition(buffer.frameLength)
        framesWritten += sampleFrames
        return CommittedAudioWrite(
            format: format,
            buffer: buffer,
            gapFrames: gapFrames,
            sampleFrames: sampleFrames,
            totalFramesWritten: gapFrames + sampleFrames
        )
    }

    @discardableResult
    func writeCommittedPCM(_ buffer: AVAudioPCMBuffer, format: AVAudioFormat, gapFrames: AVAudioFramePosition = 0) throws -> AVAudioFramePosition {
        try ensureFile(format: format)
        if gapFrames > 0 {
            try writeSilence(format: format, frames: gapFrames)
        }
        try writePCMBuffer(buffer)
        let sampleFrames = AVAudioFramePosition(buffer.frameLength)
        framesWritten += sampleFrames
        return max(0, gapFrames) + sampleFrames
    }

    static func pcmBuffer(from sampleBuffer: CMSampleBuffer, format: AVAudioFormat) throws -> AVAudioPCMBuffer {
        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            throw CLIError("cannot allocate PCM buffer")
        }
        buffer.frameLength = frameCount

        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer,
            at: 0,
            frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList
        )
        guard status == noErr else {
            throw CLIError("cannot copy PCM data: OSStatus \(status)")
        }
        return buffer
    }

    private func ensureFile(format: AVAudioFormat) throws {
        currentFormat = format
        guard file == nil else { return }
        do {
            file = try Self.makeAudioFile(url: url, processingFormat: format)
        } catch {
            throw CLIError("cannot create audio file for \(source): \(error.localizedDescription)")
        }
    }

    private func writePCMBuffer(_ buffer: AVAudioPCMBuffer) throws {
        do {
            try file?.write(from: buffer)
        } catch {
            throw CLIError("cannot write audio buffer for \(source): \(error.localizedDescription)")
        }
    }

    @discardableResult
    func padToDuration(_ duration: TimeInterval) throws -> AVAudioFramePosition {
        guard duration > 0, let currentFormat else { return 0 }
        try ensureFile(format: currentFormat)
        let targetFrames = AVAudioFramePosition((duration * currentFormat.sampleRate).rounded())
        let missingFrames = targetFrames - framesWritten
        if missingFrames > 0 {
            try writeSilence(format: currentFormat, frames: missingFrames)
            return missingFrames
        }
        return 0
    }

    private func timelineGapFrames(sampleBuffer: CMSampleBuffer, format: AVAudioFormat) -> AVAudioFramePosition {
        let sampleRate = max(format.sampleRate, 1.0)
        let toleranceFrames = AVAudioFramePosition(max(256.0, sampleRate * 0.050))
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let ptsSeconds = CMTimeGetSeconds(pts)
        if firstPresentationTimeSec == nil, ptsSeconds.isFinite {
            firstPresentationTimeSec = ptsSeconds
        }
        if firstWallDate == nil {
            firstWallDate = Date()
        }

        var expectedStartFrame: AVAudioFramePosition?
        if let firstPresentationTimeSec, ptsSeconds.isFinite {
            expectedStartFrame = AVAudioFramePosition(((ptsSeconds - firstPresentationTimeSec) * sampleRate).rounded())
        }
        if let firstWallDate {
            let wallFrame = AVAudioFramePosition((Date().timeIntervalSince(firstWallDate) * sampleRate).rounded())
            if let currentExpected = expectedStartFrame {
                expectedStartFrame = max(currentExpected, wallFrame)
            } else {
                expectedStartFrame = wallFrame
            }
        }

        guard var expectedStartFrame else { return 0 }
        if expectedStartFrame + toleranceFrames < framesWritten {
            timelineResetCount += 1
            if ptsSeconds.isFinite {
                firstPresentationTimeSec = ptsSeconds - (Double(framesWritten) / sampleRate)
            }
            expectedStartFrame = framesWritten
        }
        let gapFrames = expectedStartFrame - framesWritten
        return gapFrames > toleranceFrames ? gapFrames : 0
    }

    private func writeSilence(format: AVAudioFormat, frames: AVAudioFramePosition) throws {
        guard frames > 0 else { return }
        let maxChunk = AVAudioFramePosition(max(1.0, format.sampleRate * 10.0))
        var remaining = frames
        while remaining > 0 {
            let chunkFrames = AVAudioFrameCount(min(remaining, maxChunk))
            guard let silence = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: chunkFrames) else {
                throw CLIError("cannot allocate silence buffer for \(source)")
            }
            silence.frameLength = chunkFrames
            zeroBuffer(silence)
            try writePCMBuffer(silence)
            framesWritten += AVAudioFramePosition(chunkFrames)
            insertedSilenceFrames += AVAudioFramePosition(chunkFrames)
            remaining -= AVAudioFramePosition(chunkFrames)
        }
    }

    private func zeroBuffer(_ buffer: AVAudioPCMBuffer) {
        let audioBuffers = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        for audioBuffer in audioBuffers {
            if let data = audioBuffer.mData {
                memset(data, 0, Int(audioBuffer.mDataByteSize))
            }
        }
    }

    private static func makeAudioFile(url: URL, processingFormat: AVAudioFormat) throws -> AVAudioFile {
        guard let fileFormat = AVAudioFormat(
            commonFormat: processingFormat.commonFormat,
            sampleRate: processingFormat.sampleRate,
            channels: processingFormat.channelCount,
            interleaved: true
        ) else {
            var settings = processingFormat.settings
            settings[AVLinearPCMIsNonInterleaved] = false
            return try AVAudioFile(forWriting: url, settings: settings)
        }

        return try AVAudioFile(
            forWriting: url,
            settings: fileFormat.settings,
            commonFormat: processingFormat.commonFormat,
            interleaved: processingFormat.isInterleaved
        )
    }

    func close() {
        file = nil
    }
}

final class RawSegmentCommitTracker: @unchecked Sendable {
    private struct SourceState {
        var index = 1
        var nextStartFrame: AVAudioFramePosition = 0
        var sampleRate: Double = 0
    }

    private struct CommitInterval {
        let source: String
        let index: Int
        let startFrame: AVAudioFramePosition
        let endFrame: AVAudioFramePosition
        let sampleRate: Double
        let final: Bool
    }

    private let sessionDirectory: URL
    private let experimentID: String
    private let segmentSeconds: TimeInterval
    private let maxPendingCommits: Int
    private let warningHandler: (String) -> Void
    private let stateQueue = DispatchQueue(label: "murmurmark.raw.sidecar.commit.state")
    private let writerQueue = DispatchQueue(label: "murmurmark.raw.sidecar.commit.writer")
    private let handle: FileHandle

    private var states: [String: SourceState] = [:]
    private var pendingCommits = 0
    private var disabled = false
    private var closed = false

    init(
        sessionDirectory: URL,
        experimentID: String,
        segmentSeconds: TimeInterval,
        maxPendingCommits: Int,
        warningHandler: @escaping (String) -> Void
    ) throws {
        self.sessionDirectory = sessionDirectory
        self.experimentID = experimentID
        self.segmentSeconds = max(5.0, segmentSeconds)
        self.maxPendingCommits = max(1, maxPendingCommits)
        self.warningHandler = warningHandler
        let experimentDirectory = sessionDirectory
            .appendingPathComponent("derived/experiments")
            .appendingPathComponent(experimentID)
        try FileManager.default.createDirectory(at: experimentDirectory, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: sessionDirectory.appendingPathComponent("derived/live"), withIntermediateDirectories: true)
        let commitsURL = experimentDirectory.appendingPathComponent("raw_segment_commits.jsonl")
        FileManager.default.createFile(atPath: commitsURL.path, contents: Data())
        handle = try FileHandle(forWritingTo: commitsURL)
        let startedAt = DateStrings.iso8601(Date())
        try JSONObject.write(
            [
                "schema": "murmurmark.experimental_sidecar_manifest/v1",
                "experiment_id": experimentID,
                "kind": "near_realtime_shadow",
                "status": "recording",
                "started_at": startedAt,
                "config": [
                    "segment_sec": rounded(segmentSeconds),
                    "handoff": "committed_pcm_queue_v1",
                    "fallback_handoff": "raw_segment_commits",
                    "compatibility_alias": "derived/live",
                ],
                "inputs": [
                    "raw_mic": "audio/mic/000001.caf",
                    "raw_remote": "audio/remote/000001.caf",
                ],
                "outputs": [
                    "raw_segment_commits": "derived/experiments/\(experimentID)/raw_segment_commits.jsonl",
                    "experiment_audio": "derived/experiments/\(experimentID)/audio",
                    "compat_live_dir": "derived/live",
                    "segments": "derived/live/segments.jsonl",
                    "preview_transcript": "derived/live/transcript.preview.md",
                    "preview_snapshots": "derived/live/preview_snapshots.jsonl",
                    "draft_transcript": "derived/live/transcript.draft.md",
                ],
                "raw_capture_affected": "unknown",
                "batch_authoritative": true,
                "promotion_allowed": false,
            ],
            to: experimentDirectory.appendingPathComponent("experiment_manifest.json")
        )
        try JSONObject.write(
            [
                "schema": "murmurmark.raw_sidecar_commit_state/v1",
                "experiment_id": experimentID,
                "status": "recording",
                "segment_sec": rounded(segmentSeconds),
                "raw_segment_commits": "derived/experiments/\(experimentID)/raw_segment_commits.jsonl",
                "updated_at": startedAt,
            ],
            to: experimentDirectory.appendingPathComponent("raw_commit_state.json")
        )
    }

    static func resolveMaxPendingCommits() -> Int {
        let env = ProcessInfo.processInfo.environment
        guard let value = env["MURMURMARK_RAW_SIDECAR_MAX_PENDING_COMMITS"],
              let parsed = Int(value) else {
            return 128
        }
        return max(1, min(parsed, 100_000))
    }

    func recordWrite(source: String, framesWritten: AVAudioFramePosition, sampleRate: Double) {
        let rows = stateQueue.sync {
            rowsForWrite(source: source, framesWritten: framesWritten, sampleRate: sampleRate, final: false)
        }
        enqueue(rows)
    }

    func closeAll(finalFramesBySource: [String: AVAudioFramePosition]) {
        let rows = stateQueue.sync { () -> [[String: Any]] in
            guard !closed else { return [] }
            var rows: [[String: Any]] = []
            for source in ["mic", "remote"] {
                guard let frames = finalFramesBySource[source] else { continue }
                guard let sampleRate = states[source]?.sampleRate, sampleRate > 0 else { continue }
                rows.append(contentsOf: rowsForWrite(source: source, framesWritten: frames, sampleRate: sampleRate, final: true))
            }
            closed = true
            return rows
        }
        enqueue(rows)
        writerQueue.sync {
            try? handle.close()
        }
    }

    private func rowsForWrite(
        source: String,
        framesWritten: AVAudioFramePosition,
        sampleRate: Double,
        final: Bool
    ) -> [[String: Any]] {
        guard !closed, !disabled, framesWritten > 0, sampleRate > 0 else { return [] }
        var state = states[source] ?? SourceState()
        if state.sampleRate <= 0 {
            state.sampleRate = sampleRate
        }
        let effectiveSampleRate = state.sampleRate > 0 ? state.sampleRate : sampleRate
        let segmentFrames = AVAudioFramePosition(max(1.0, effectiveSampleRate * segmentSeconds))
        var rows: [[String: Any]] = []
        while framesWritten - state.nextStartFrame >= segmentFrames {
            let endFrame = state.nextStartFrame + segmentFrames
            rows.append(row(CommitInterval(
                source: source,
                index: state.index,
                startFrame: state.nextStartFrame,
                endFrame: endFrame,
                sampleRate: effectiveSampleRate,
                final: false
            )))
            state.nextStartFrame = endFrame
            state.index += 1
        }
        if final, framesWritten > state.nextStartFrame {
            rows.append(row(CommitInterval(
                source: source,
                index: state.index,
                startFrame: state.nextStartFrame,
                endFrame: framesWritten,
                sampleRate: effectiveSampleRate,
                final: true
            )))
            state.nextStartFrame = framesWritten
            state.index += 1
        }
        states[source] = state
        return rows
    }

    private func row(_ interval: CommitInterval) -> [String: Any] {
        let startSec = Double(interval.startFrame) / max(interval.sampleRate, 1.0)
        let endSec = Double(interval.endFrame) / max(interval.sampleRate, 1.0)
        return [
            "schema": "murmurmark.raw_segment_commit/v1",
            "experiment_id": experimentID,
            "source": interval.source,
            "index": interval.index,
            "start_sec": rounded(startSec),
            "end_sec": rounded(endSec),
            "duration_sec": rounded(endSec - startSec),
            "raw_path": rawPath(source: interval.source),
            "frames_committed": Int64(interval.endFrame - interval.startFrame),
            "total_frames_committed": Int64(interval.endFrame),
            "sample_rate": Int(interval.sampleRate.rounded()),
            "status": "committed",
            "final": interval.final,
            "t": DateStrings.iso8601(Date()),
        ]
    }

    private func enqueue(_ rows: [[String: Any]]) {
        guard !rows.isEmpty else { return }
        let encodedRows: [Data] = rows.compactMap { try? JSONSerialization.data(withJSONObject: $0, options: [.sortedKeys]) }
        let rowCount = encodedRows.count
        guard rowCount > 0 else { return }
        let shouldWrite = stateQueue.sync { () -> Bool in
            guard !disabled else { return false }
            if pendingCommits + rowCount > maxPendingCommits {
                disabled = true
                return false
            }
            pendingCommits += rowCount
            return true
        }
        guard shouldWrite else {
            warningHandler("raw sidecar commit tracker disabled: backlog exceeded \(maxPendingCommits) commits")
            return
        }
        writerQueue.async { [weak self, encodedRows, rowCount] in
            guard let self else { return }
            defer {
                stateQueue.sync {
                    pendingCommits = max(0, pendingCommits - rowCount)
                }
            }
            for data in encodedRows {
                handle.write(data)
                handle.write(Data("\n".utf8))
            }
        }
    }

    private func rawPath(source: String) -> String {
        "audio/\(source)/000001.caf"
    }

    private func rounded(_ value: Double) -> Double {
        Double((value * 1000).rounded() / 1000)
    }
}

final class AsyncLiveSegmentCapture: @unchecked Sendable {
    private struct QueuedSample: @unchecked Sendable {
        let sampleBuffer: CMSampleBuffer
    }

    private let writer: LiveSegmentCapture
    private let writerQueue = DispatchQueue(label: "murmurmark.live.segment.writer")
    private let stateQueue = DispatchQueue(label: "murmurmark.live.segment.state")
    private let maxPendingSamples: Int
    private let artificialWriteDelayMilliseconds: Int
    private let warningHandler: (String) -> Void

    private var pendingSamples = 0
    private var disabled = false
    private var closed = false
    private var capturedDuration: TimeInterval = 0

    init(
        sessionDirectory: URL,
        segmentSeconds: TimeInterval,
        overlapSeconds: TimeInterval,
        maxPendingSamples: Int = 512,
        artificialWriteDelayMilliseconds: Int = 0,
        warningHandler: @escaping (String) -> Void
    ) throws {
        writer = try LiveSegmentCapture(
            sessionDirectory: sessionDirectory,
            segmentSeconds: segmentSeconds,
            overlapSeconds: overlapSeconds,
            provenance: "recording_time_unsafe_sample_buffer"
        )
        self.maxPendingSamples = max(1, maxPendingSamples)
        self.artificialWriteDelayMilliseconds = max(0, min(artificialWriteDelayMilliseconds, 5000))
        self.warningHandler = warningHandler
    }

    static func resolveMaxPendingSamples() -> Int {
        let env = ProcessInfo.processInfo.environment
        guard let value = env["MURMURMARK_LIVE_SEGMENT_MAX_PENDING_SAMPLES"],
              let parsed = Int(value) else {
            return 512
        }
        return max(1, min(parsed, 100_000))
    }

    static func resolveWriteDelayMilliseconds() -> Int {
        let env = ProcessInfo.processInfo.environment
        guard let value = env["MURMURMARK_LIVE_SEGMENT_WRITE_DELAY_MS"],
              let parsed = Int(value) else {
            return 0
        }
        return max(0, min(parsed, 5000))
    }

    func enqueue(_ sampleBuffer: CMSampleBuffer, source: String) {
        enum Decision {
            case enqueue
            case ignore
            case disable(String)
        }

        let decision = stateQueue.sync { () -> Decision in
            if closed || disabled {
                return .ignore
            }
            if pendingSamples >= maxPendingSamples {
                disabled = true
                return .disable("live segment writer disabled for \(source): backlog exceeded \(maxPendingSamples) samples")
            }
            pendingSamples += 1
            return .enqueue
        }

        switch decision {
        case .ignore:
            return
        case .disable(let warning):
            warningHandler(warning)
            writerQueue.async { [weak self] in
                self?.closeWriterFromWriterQueue()
            }
        case .enqueue:
            let queuedSample = QueuedSample(sampleBuffer: sampleBuffer)
            writerQueue.async { [weak self, queuedSample] in
                guard let self else { return }
                defer {
                    stateQueue.sync {
                        pendingSamples = max(0, pendingSamples - 1)
                    }
                }
                let shouldWrite = stateQueue.sync { !closed && !disabled }
                guard shouldWrite else { return }
                do {
                    if artificialWriteDelayMilliseconds > 0 {
                        Thread.sleep(forTimeInterval: Double(artificialWriteDelayMilliseconds) / 1000.0)
                    }
                    try writer.write(queuedSample.sampleBuffer, source: source)
                    let duration = writer.capturedDurationSeconds()
                    stateQueue.sync {
                        capturedDuration = max(capturedDuration, duration)
                    }
                } catch {
                    stateQueue.sync {
                        disabled = true
                    }
                    warningHandler("live segment writer disabled for \(source): \(error.localizedDescription)")
                    closeWriterFromWriterQueue()
                }
            }
        }
    }

    func closeAll(finalDurationSeconds: TimeInterval? = nil) {
        writerQueue.sync {
            let shouldClose = stateQueue.sync { !closed }
            guard shouldClose else { return }
            writer.closeAll(finalDurationSeconds: finalDurationSeconds)
            let duration = writer.capturedDurationSeconds()
            stateQueue.sync {
                capturedDuration = max(capturedDuration, duration)
                closed = true
                pendingSamples = 0
            }
        }
    }

    func capturedDurationSeconds() -> TimeInterval {
        stateQueue.sync { capturedDuration }
    }

    private func closeWriterFromWriterQueue() {
        let shouldClose = stateQueue.sync { !closed }
        guard shouldClose else { return }
        writer.closeAll()
        let duration = writer.capturedDurationSeconds()
        stateQueue.sync {
            capturedDuration = max(capturedDuration, duration)
            closed = true
            pendingSamples = 0
        }
    }
}

final class AsyncCommittedLiveSegmentCapture: @unchecked Sendable {
    private enum EnqueueDecision {
        case enqueue(scheduleDrain: Bool)
        case ignore
        case disable(String)
    }

    private let writer: LiveSegmentCapture
    private let writerQueue = DispatchQueue(label: "murmurmark.live.committed-pcm.writer")
    private let stateQueue = DispatchQueue(label: "murmurmark.live.committed-pcm.state")
    private let sessionDirectory: URL
    private let experimentID: String
    private let segmentSeconds: TimeInterval
    private let overlapSeconds: TimeInterval
    private let maxPendingPackets: Int
    private let maxPendingSeconds: TimeInterval
    private let artificialWriteDelayMilliseconds: Int
    private let warningHandler: (String) -> Void

    private var packetQueue: [CommittedAudioPacket] = []
    private var packetQueueHead = 0
    private var pendingPackets = 0
    private var pendingFramesBySource: [String: AVAudioFramePosition] = [:]
    private var sampleRateBySource: [String: Double] = [:]
    private var maxObservedPendingSeconds = 0.0
    private var drainScheduled = false
    private var disabled = false
    private var closed = false
    private var capturedDuration: TimeInterval = 0
    private var droppedPackets = 0
    private var lastStatus = "preview_running"
    private var disabledReason: String?
    private var lastStateWriteDate = Date.distantPast

    init(
        sessionDirectory: URL,
        experimentID: String,
        segmentSeconds: TimeInterval,
        overlapSeconds: TimeInterval,
        maxPendingPackets: Int = 100_000,
        maxPendingSeconds: TimeInterval = 30.0,
        artificialWriteDelayMilliseconds: Int = 0,
        warningHandler: @escaping (String) -> Void
    ) throws {
        self.sessionDirectory = sessionDirectory
        self.experimentID = experimentID
        self.segmentSeconds = max(5.0, segmentSeconds)
        self.overlapSeconds = max(0.0, overlapSeconds)
        self.maxPendingPackets = max(1, maxPendingPackets)
        self.maxPendingSeconds = max(0.1, min(maxPendingSeconds, 300.0))
        self.artificialWriteDelayMilliseconds = max(0, min(artificialWriteDelayMilliseconds, 5000))
        self.warningHandler = warningHandler
        writer = try LiveSegmentCapture(
            sessionDirectory: sessionDirectory,
            segmentSeconds: segmentSeconds,
            overlapSeconds: overlapSeconds,
            audioPathPrefix: "derived/experiments/\(experimentID)/audio",
            provenance: "recording_time_committed_pcm"
        )
        try writeExperimentState(status: "preview_running", reason: nil)
        lastStateWriteDate = Date()
    }

    static func resolveMaxPendingPackets() -> Int {
        let env = ProcessInfo.processInfo.environment
        guard let value = env["MURMURMARK_LIVE_PCM_MAX_PENDING_PACKETS"],
              let parsed = Int(value) else {
            return 100_000
        }
        return max(1, min(parsed, 100_000))
    }

    static func resolveMaxPendingSeconds() -> TimeInterval {
        let env = ProcessInfo.processInfo.environment
        guard let value = env["MURMURMARK_LIVE_PCM_MAX_PENDING_SECONDS"],
              let parsed = TimeInterval(value) else {
            return 30.0
        }
        return max(0.1, min(parsed, 300.0))
    }

    static func resolveWriteDelayMilliseconds() -> Int {
        let env = ProcessInfo.processInfo.environment
        guard let value = env["MURMURMARK_LIVE_PCM_WRITE_DELAY_MS"],
              let parsed = Int(value) else {
            return 0
        }
        return max(0, min(parsed, 5000))
    }

    func enqueue(_ packet: CommittedAudioPacket) {
        let decision = stateQueue.sync { () -> EnqueueDecision in
            if closed || disabled {
                return .ignore
            }
            let packetFrames = max(0, packet.gapFrames) + max(0, packet.sampleFrames)
            let sampleRate = max(packet.format.sampleRate, 1.0)
            let sourceFrames = (pendingFramesBySource[packet.source] ?? 0) + packetFrames
            let sourceSeconds = Double(sourceFrames) / sampleRate
            if pendingPackets >= maxPendingPackets {
                return disableForBackpressure(
                    reason: "committed PCM queue exceeded hard limit of \(maxPendingPackets) packets",
                    warning: "live-shadow-v1 committed PCM sidecar disabled: backlog exceeded hard limit of \(maxPendingPackets) packets"
                )
            }
            if sourceSeconds > maxPendingSeconds {
                let formatted = String(format: "%.1f", maxPendingSeconds)
                return disableForBackpressure(
                    reason: "committed PCM queue exceeded \(formatted)s for \(packet.source)",
                    warning: "live-shadow-v1 committed PCM sidecar disabled: \(packet.source) backlog exceeded \(formatted)s"
                )
            }
            packetQueue.append(packet)
            pendingPackets += 1
            pendingFramesBySource[packet.source] = sourceFrames
            sampleRateBySource[packet.source] = sampleRate
            maxObservedPendingSeconds = max(maxObservedPendingSeconds, sourceSeconds)
            let shouldSchedule = !drainScheduled
            drainScheduled = true
            return .enqueue(scheduleDrain: shouldSchedule)
        }

        switch decision {
        case .ignore:
            return
        case .disable(let warning):
            warningHandler(warning)
            writerQueue.async { [weak self] in
                self?.closeWriterFromWriterQueue(status: "disabled_backpressure", reason: warning)
            }
        case .enqueue(let scheduleDrain):
            guard scheduleDrain else { return }
            writerQueue.async { [weak self] in
                self?.drainPacketsFromWriterQueue()
            }
        }
    }

    func closeAll(finalDurationSeconds: TimeInterval? = nil) {
        writerQueue.sync {
            let shouldClose = stateQueue.sync { !closed }
            guard shouldClose else { return }
            writer.closeAll(finalDurationSeconds: finalDurationSeconds)
            let duration = writer.capturedDurationSeconds()
            let status = stateQueue.sync { () -> String in
                capturedDuration = max(capturedDuration, duration)
                closed = true
                clearPendingPacketsLocked()
                if disabled {
                    return lastStatus == "preview_running" ? "completed_partial_draft" : lastStatus
                }
                lastStatus = "completed"
                return "completed"
            }
            try? writeExperimentState(status: status, reason: disabledReason)
        }
    }

    func capturedDurationSeconds() -> TimeInterval {
        stateQueue.sync { capturedDuration }
    }

    private func closeWriterFromWriterQueue(status: String, reason: String?) {
        let shouldClose = stateQueue.sync { !closed }
        guard shouldClose else { return }
        writer.closeAll(stateStatus: status)
        let duration = writer.capturedDurationSeconds()
        stateQueue.sync {
            capturedDuration = max(capturedDuration, duration)
            closed = true
            clearPendingPacketsLocked()
            lastStatus = status
            disabledReason = reason
        }
        try? writeExperimentState(status: status, reason: reason)
    }

    private func drainPacketsFromWriterQueue() {
        while let packet = nextPacketForWriting() {
            let shouldWrite = stateQueue.sync { !closed && !disabled }
            guard shouldWrite else {
                completePacket(packet)
                continue
            }
            do {
                if artificialWriteDelayMilliseconds > 0 {
                    Thread.sleep(forTimeInterval: Double(artificialWriteDelayMilliseconds) / 1000.0)
                }
                try writer.write(packet)
                let duration = writer.capturedDurationSeconds()
                stateQueue.sync {
                    capturedDuration = max(capturedDuration, duration)
                }
                completePacket(packet)
                writeExperimentStateIfDue()
            } catch {
                let reason = error.localizedDescription
                completePacket(packet)
                stateQueue.sync {
                    disabled = true
                    lastStatus = "disabled_pcm_copy"
                    disabledReason = reason
                    clearPendingPacketsLocked()
                }
                warningHandler("live-shadow-v1 committed PCM sidecar disabled for \(packet.source): \(reason)")
                closeWriterFromWriterQueue(status: "disabled_pcm_copy", reason: reason)
                return
            }
        }
    }

    private func nextPacketForWriting() -> CommittedAudioPacket? {
        stateQueue.sync {
            guard !closed, !disabled, packetQueueHead < packetQueue.count else {
                if closed || disabled {
                    clearPendingPacketsLocked()
                }
                drainScheduled = false
                return nil
            }
            let packet = packetQueue[packetQueueHead]
            packetQueueHead += 1
            compactPacketQueueIfNeededLocked()
            return packet
        }
    }

    private func completePacket(_ packet: CommittedAudioPacket) {
        stateQueue.sync {
            pendingPackets = max(0, pendingPackets - 1)
            let packetFrames = max(0, packet.gapFrames) + max(0, packet.sampleFrames)
            pendingFramesBySource[packet.source] = max(
                0,
                (pendingFramesBySource[packet.source] ?? 0) - packetFrames
            )
        }
    }

    private func disableForBackpressure(reason: String, warning: String) -> EnqueueDecision {
        disabled = true
        droppedPackets += 1
        lastStatus = "disabled_backpressure"
        disabledReason = reason
        clearPendingPacketsLocked()
        return .disable(warning)
    }

    private func clearPendingPacketsLocked() {
        packetQueue.removeAll(keepingCapacity: false)
        packetQueueHead = 0
        pendingPackets = 0
        pendingFramesBySource.removeAll(keepingCapacity: false)
        sampleRateBySource.removeAll(keepingCapacity: false)
    }

    private func compactPacketQueueIfNeededLocked() {
        guard packetQueueHead >= 512, packetQueueHead * 2 >= packetQueue.count else { return }
        packetQueue.removeFirst(packetQueueHead)
        packetQueueHead = 0
    }

    private func writeExperimentStateIfDue() {
        let now = Date()
        guard now.timeIntervalSince(lastStateWriteDate) >= 1.0 else { return }
        lastStateWriteDate = now
        try? writeExperimentState(status: "preview_running", reason: nil)
    }

    private func writeExperimentState(status: String, reason: String?) throws {
        let snapshot = stateQueue.sync {
            (
                pending: pendingPackets,
                dropped: droppedPackets,
                disabled: disabled,
                duration: capturedDuration,
                reason: reason ?? disabledReason,
                pendingSeconds: pendingFramesBySource.reduce(into: [String: Double]()) { result, entry in
                    let sampleRate = max(sampleRateBySource[entry.key] ?? 1.0, 1.0)
                    result[entry.key] = rounded(Double(entry.value) / sampleRate)
                },
                maxObservedPendingSeconds: maxObservedPendingSeconds
            )
        }
        let experimentDirectory = sessionDirectory
            .appendingPathComponent("derived/experiments")
            .appendingPathComponent(experimentID)
        let now = DateStrings.iso8601(Date())
        let state: [String: Any] = [
            "schema": "murmurmark.experimental_sidecar_state/v1",
            "experiment_id": experimentID,
            "kind": "near_realtime_shadow",
            "status": status,
            "updated_at": now,
            "live_preview_mode": "committed_pcm_queue_v1",
            "segment_sec": rounded(segmentSeconds),
            "overlap_sec": rounded(overlapSeconds),
            "reason": snapshot.reason as Any? ?? NSNull(),
            "answers": [
                "experiment_started": true,
                "raw_seconds_recorded": rounded(snapshot.duration),
                "sidecar_seconds_captured": rounded(snapshot.duration),
                "sidecar_seconds_preprocessed": 0.0,
                "sidecar_seconds_asr": 0.0,
                "dropped_chunks": snapshot.dropped,
                "backpressure_detected": status == "disabled_backpressure",
                "sidecar_disabled": snapshot.disabled,
                "raw_capture_affected": false,
                "batch_reproducible_from_raw": FileManager.default.fileExists(atPath: sessionDirectory.appendingPathComponent("session.json").path),
            ],
            "counters": [
                "pending_pcm_packets": snapshot.pending,
                "pending_pcm_seconds_by_source": snapshot.pendingSeconds,
                "dropped_pcm_packets": snapshot.dropped,
                "max_pending_pcm_packets": maxPendingPackets,
                "max_pending_pcm_seconds": rounded(maxPendingSeconds),
                "max_observed_pending_pcm_seconds": rounded(snapshot.maxObservedPendingSeconds),
                "artificial_write_delay_ms": artificialWriteDelayMilliseconds,
            ],
            "inputs": [
                "raw_mic": "audio/mic/000001.caf",
                "raw_remote": "audio/remote/000001.caf",
            ],
            "outputs": [
                "segments": "derived/live/segments.jsonl",
                "compat_live_dir": "derived/live",
                "experiment_audio": "derived/experiments/\(experimentID)/audio",
                "preview_transcript": "derived/live/transcript.preview.md",
                "preview_snapshots": "derived/live/preview_snapshots.jsonl",
                "draft_transcript": "derived/live/transcript.draft.md",
                "live_pipeline_report": "derived/live/live_pipeline_report.json",
                "raw_segment_commits": "derived/experiments/\(experimentID)/raw_segment_commits.jsonl",
            ],
            "recovery_command": "murmurmark process \(PathDisplay.display(sessionDirectory))",
            "comparison_command": "murmurmark experiment compare \(PathDisplay.display(sessionDirectory)) --experiment \(experimentID)",
        ]
        try JSONObject.write(state, to: experimentDirectory.appendingPathComponent("state.json"))
        try JSONObject.write(
            [
                "schema": "murmurmark.experimental_sidecar_report/v1",
                "experiment_id": experimentID,
                "generated_at": now,
                "session": PathDisplay.display(sessionDirectory),
                "status": status,
                "raw_capture_affected": false,
                "batch_authoritative": true,
                "promotion_allowed": false,
                "summary": [
                    "sidecar_seconds_captured": rounded(snapshot.duration),
                    "sidecar_disabled": snapshot.disabled,
                    "backpressure_detected": status == "disabled_backpressure",
                    "live_preview_mode": "committed_pcm_queue_v1",
                    "reason": snapshot.reason as Any? ?? NSNull(),
                ],
                "recovery_command": "murmurmark process \(PathDisplay.display(sessionDirectory))",
                "comparison_command": "murmurmark experiment compare \(PathDisplay.display(sessionDirectory)) --experiment \(experimentID)",
            ],
            to: experimentDirectory.appendingPathComponent("report.json")
        )
    }

    private func rounded(_ value: Double) -> Double {
        Double((value * 1000).rounded() / 1000)
    }
}

final class LiveSegmentCapture {
    private struct BufferedSample {
        let format: AVAudioFormat
        let buffer: AVAudioPCMBuffer
        let gapFrames: AVAudioFramePosition
        let frames: AVAudioFramePosition
    }

    private struct ClosingSegment {
        let writer: AudioFileWriter
        let index: Int
        let path: String
        let hardStartFrame: AVAudioFramePosition
        let fileStartFrame: AVAudioFramePosition
        let sampleRate: Double
        var hardFrames: AVAudioFramePosition
        var fileFrames: AVAudioFramePosition
        var afterFramesTarget: AVAudioFramePosition
        var afterFramesWritten: AVAudioFramePosition = 0
    }

    private struct SegmentManifestRow {
        let source: String
        let index: Int
        let path: String
        let hardStartFrame: AVAudioFramePosition
        let hardFrames: AVAudioFramePosition
        let fileStartFrame: AVAudioFramePosition
        let fileFrames: AVAudioFramePosition
        let sampleRate: Double
        let final: Bool
        let afterOverlapComplete: Bool
    }

    private struct SourceState {
        var writer: AudioFileWriter?
        var index = 1
        var cumulativeFrames: AVAudioFramePosition = 0
        var hardStartFrame: AVAudioFramePosition = 0
        var fileStartFrame: AVAudioFramePosition = 0
        var hardFrames: AVAudioFramePosition = 0
        var fileFrames: AVAudioFramePosition = 0
        var sampleRate: Double = 0
        var path: String = ""
        var tail: [BufferedSample] = []
        var tailHead = 0
        var tailFrames: AVAudioFramePosition = 0
        var closing: [ClosingSegment] = []
    }

    let sessionDirectory: URL
    let segmentSeconds: TimeInterval
    let overlapSeconds: TimeInterval
    let audioPathPrefix: String
    let provenance: String

    private let manifestURL: URL
    private let manifestHandle: FileHandle
    private var states: [String: SourceState] = [:]

    init(
        sessionDirectory: URL,
        segmentSeconds: TimeInterval,
        overlapSeconds: TimeInterval,
        audioPathPrefix: String = "derived/live/audio",
        provenance: String
    ) throws {
        self.sessionDirectory = sessionDirectory
        self.segmentSeconds = max(5.0, segmentSeconds)
        self.overlapSeconds = max(0.0, overlapSeconds)
        self.audioPathPrefix = audioPathPrefix
        self.provenance = provenance
        let liveDirectory = sessionDirectory.appendingPathComponent("derived/live")
        let audioDirectory = sessionDirectory.appendingPathComponent(audioPathPrefix)
        try FileManager.default.createDirectory(at: audioDirectory.appendingPathComponent("mic"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: audioDirectory.appendingPathComponent("remote"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: liveDirectory.appendingPathComponent("chunks"), withIntermediateDirectories: true)
        manifestURL = liveDirectory.appendingPathComponent("segments.jsonl")
        FileManager.default.createFile(atPath: manifestURL.path, contents: Data())
        manifestHandle = try FileHandle(forWritingTo: manifestURL)
        try writeState(status: "recording")
    }

    func write(_ sampleBuffer: CMSampleBuffer, source: String) throws {
        guard let format = AudioFileWriter.audioFormat(from: sampleBuffer) else {
            throw CLIError("cannot read live audio format for \(source)")
        }
        let buffer = try AudioFileWriter.pcmBuffer(from: sampleBuffer, format: format)
        try writePCM(buffer: buffer, format: format, gapFrames: 0, source: source)
    }

    func write(_ packet: CommittedAudioPacket) throws {
        try writePCM(
            buffer: packet.buffer,
            format: packet.format,
            gapFrames: packet.gapFrames,
            source: packet.source
        )
    }

    private func writePCM(
        buffer: AVAudioPCMBuffer,
        format: AVAudioFormat,
        gapFrames: AVAudioFramePosition,
        source: String
    ) throws {
        var state = states.removeValue(forKey: source) ?? SourceState()
        do {
            try writePendingAfterOverlap(buffer: buffer, format: format, gapFrames: gapFrames, source: source, state: &state)
            if state.writer == nil {
                try startSegment(source: source, format: format, state: &state)
            }
            let writtenFrames = try state.writer?.writeCommittedPCM(buffer, format: format, gapFrames: gapFrames)
                ?? (max(0, gapFrames) + AVAudioFramePosition(buffer.frameLength))
            state.hardFrames += writtenFrames
            state.fileFrames += writtenFrames
            state.cumulativeFrames += writtenFrames
            appendTail(buffer: buffer, format: format, gapFrames: gapFrames, frames: writtenFrames, state: &state)

            let targetFrames = AVAudioFramePosition(max(1.0, state.sampleRate * segmentSeconds))
            if state.hardFrames >= targetFrames {
                try rotateSegment(source: source, state: &state)
            }
            states[source] = state
        } catch {
            states[source] = state
            throw error
        }
    }

    func closeAll(finalDurationSeconds: TimeInterval? = nil, stateStatus: String = "capture_finished") {
        for source in Array(states.keys) {
            var state = states.removeValue(forKey: source) ?? SourceState()
            if let finalDurationSeconds, finalDurationSeconds > 0 {
                try? padCurrentSegment(
                    toGlobalDuration: finalDurationSeconds,
                    source: source,
                    state: &state
                )
            }
            for index in state.closing.indices.reversed() {
                try? closePendingSegment(source: source, state: &state, at: index, final: false)
            }
            try? closeCurrentSegment(source: source, state: &state, final: true)
            states[source] = state
        }
        try? writeState(status: stateStatus)
        try? manifestHandle.close()
    }

    func capturedDurationSeconds() -> TimeInterval {
        states.values.map { state in
            Double(state.cumulativeFrames) / max(state.sampleRate, 1.0)
        }.max() ?? 0
    }

    private func startSegment(source: String, format: AVAudioFormat, state: inout SourceState) throws {
        state.sampleRate = format.sampleRate
        state.hardStartFrame = state.cumulativeFrames
        state.fileStartFrame = max(0, state.cumulativeFrames - state.tailFrames)
        state.hardFrames = 0
        state.fileFrames = 0
        state.path = "\(audioPathPrefix)/\(source)/\(String(format: "%06d", state.index)).caf"
        let writer = try AudioFileWriter(
            url: sessionDirectory.appendingPathComponent(state.path),
            source: "live-\(source)-\(state.index)"
        )
        state.writer = writer
        for sample in state.tail.dropFirst(state.tailHead) {
            do {
                try writer.writeCommittedPCM(sample.buffer, format: sample.format, gapFrames: sample.gapFrames)
                state.fileFrames += sample.frames
            } catch {
                state.fileStartFrame = state.cumulativeFrames
                state.fileFrames = 0
                state.tail.removeAll()
                state.tailHead = 0
                state.tailFrames = 0
                break
            }
        }
    }

    private func padCurrentSegment(
        toGlobalDuration duration: TimeInterval,
        source _: String,
        state: inout SourceState
    ) throws {
        guard let writer = state.writer, state.sampleRate > 0 else { return }
        let targetGlobalFrame = AVAudioFramePosition((duration * state.sampleRate).rounded())
        let missingGlobalFrames = targetGlobalFrame - state.cumulativeFrames
        guard missingGlobalFrames > 0 else { return }
        let targetFileDuration = Double(targetGlobalFrame - state.fileStartFrame) / state.sampleRate
        let writtenFrames = try writer.padToDuration(targetFileDuration)
        state.hardFrames += writtenFrames
        state.fileFrames += writtenFrames
        state.cumulativeFrames += writtenFrames
    }

    private func rotateSegment(source: String, state: inout SourceState) throws {
        guard let writer = state.writer else { return }
        let closing = ClosingSegment(
            writer: writer,
            index: state.index,
            path: state.path,
            hardStartFrame: state.hardStartFrame,
            fileStartFrame: state.fileStartFrame,
            sampleRate: state.sampleRate,
            hardFrames: state.hardFrames,
            fileFrames: state.fileFrames,
            afterFramesTarget: overlapFrames(sampleRate: state.sampleRate)
        )
        state.index += 1
        state.writer = nil
        state.path = ""
        state.hardFrames = 0
        state.fileFrames = 0
        if closing.afterFramesTarget <= 0 {
            state.closing.append(closing)
            try closePendingSegment(source: source, state: &state, at: state.closing.count - 1, final: false)
        } else {
            state.closing.append(closing)
        }
    }

    private func closeCurrentSegment(source: String, state: inout SourceState, final: Bool) throws {
        guard let writer = state.writer, state.hardFrames > 0 else { return }
        writer.close()
        try appendSegmentRow(SegmentManifestRow(
            source: source,
            index: state.index,
            path: state.path,
            hardStartFrame: state.hardStartFrame,
            hardFrames: state.hardFrames,
            fileStartFrame: state.fileStartFrame,
            fileFrames: state.fileFrames,
            sampleRate: state.sampleRate,
            final: final,
            afterOverlapComplete: true
        ))
        state.index += 1
        state.writer = nil
        state.path = ""
        state.hardFrames = 0
        state.fileFrames = 0
    }

    private func closePendingSegment(source: String, state: inout SourceState, at index: Int, final: Bool) throws {
        let pending = state.closing[index]
        pending.writer.close()
        try appendSegmentRow(SegmentManifestRow(
            source: source,
            index: pending.index,
            path: pending.path,
            hardStartFrame: pending.hardStartFrame,
            hardFrames: pending.hardFrames,
            fileStartFrame: pending.fileStartFrame,
            fileFrames: pending.fileFrames,
            sampleRate: pending.sampleRate,
            final: final,
            afterOverlapComplete: pending.afterFramesWritten >= pending.afterFramesTarget
        ))
        state.closing.remove(at: index)
    }

    private func appendSegmentRow(_ row: SegmentManifestRow) throws {
        let startSec = Double(row.hardStartFrame) / max(row.sampleRate, 1.0)
        let endSec = Double(row.hardStartFrame + row.hardFrames) / max(row.sampleRate, 1.0)
        let clipStartSec = Double(row.fileStartFrame) / max(row.sampleRate, 1.0)
        let clipEndSec = Double(row.fileStartFrame + row.fileFrames) / max(row.sampleRate, 1.0)
        try appendJSONLine(
            [
                "schema": "murmurmark.live_segment/v1",
                "created_at": DateStrings.iso8601(Date()),
                "provenance": provenance,
                "source": row.source,
                "index": row.index,
                "path": row.path,
                "start_sec": rounded(startSec),
                "end_sec": rounded(endSec),
                "duration_sec": rounded(endSec - startSec),
                "clip_start_sec": rounded(clipStartSec),
                "clip_end_sec": rounded(clipEndSec),
                "clip_duration_sec": rounded(clipEndSec - clipStartSec),
                "overlap_before_sec": rounded(startSec - clipStartSec),
                "overlap_after_sec": rounded(clipEndSec - endSec),
                "frames": Int64(row.hardFrames),
                "clip_frames": Int64(row.fileFrames),
                "sample_rate": Int(row.sampleRate.rounded()),
                "closed": true,
                "final": row.final,
                "after_overlap_complete": row.afterOverlapComplete,
            ],
            to: manifestHandle
        )
        try manifestHandle.synchronize()
    }

    private func writePendingAfterOverlap(
        buffer: AVAudioPCMBuffer,
        format: AVAudioFormat,
        gapFrames: AVAudioFramePosition,
        source: String,
        state: inout SourceState
    ) throws {
        for index in state.closing.indices.reversed() {
            do {
                let writtenFrames = try state.closing[index].writer.writeCommittedPCM(
                    buffer,
                    format: format,
                    gapFrames: gapFrames
                )
                state.closing[index].fileFrames += writtenFrames
                state.closing[index].afterFramesWritten += writtenFrames
            } catch {
                try closePendingSegment(source: source, state: &state, at: index, final: false)
                continue
            }
            if state.closing[index].afterFramesWritten >= state.closing[index].afterFramesTarget {
                try closePendingSegment(source: source, state: &state, at: index, final: false)
            }
        }
    }

    private func appendTail(
        buffer: AVAudioPCMBuffer,
        format: AVAudioFormat,
        gapFrames: AVAudioFramePosition,
        frames: AVAudioFramePosition,
        state: inout SourceState
    ) {
        guard overlapSeconds > 0 else {
            state.tail.removeAll()
            state.tailHead = 0
            state.tailFrames = 0
            return
        }
        state.tail.append(BufferedSample(format: format, buffer: buffer, gapFrames: gapFrames, frames: frames))
        state.tailFrames += frames
        let maxFrames = overlapFrames(sampleRate: format.sampleRate)
        while state.tailFrames > maxFrames, state.tailHead < state.tail.count {
            let removed = state.tail[state.tailHead]
            state.tailHead += 1
            state.tailFrames -= removed.frames
        }
        if state.tailHead >= 512, state.tailHead * 2 >= state.tail.count {
            state.tail.removeFirst(state.tailHead)
            state.tailHead = 0
        }
    }

    private func overlapFrames(sampleRate: Double) -> AVAudioFramePosition {
        AVAudioFramePosition(max(0.0, sampleRate * overlapSeconds))
    }

    private func writeState(status: String) throws {
        try JSONObject.write(
            [
                "schema": "murmurmark.live_pipeline_state/v1",
                "status": status,
                "provenance": provenance,
                "segment_sec": segmentSeconds,
                "overlap_sec": overlapSeconds,
                "segments": "derived/live/segments.jsonl",
                "preview_transcript": "derived/live/transcript.preview.md",
                "preview_snapshots": "derived/live/preview_snapshots.jsonl",
                "draft_transcript": "derived/live/transcript.draft.md",
                "report": "derived/live/live_pipeline_report.json",
                "updated_at": DateStrings.iso8601(Date()),
            ],
            to: sessionDirectory.appendingPathComponent("derived/live/live_pipeline_state.json")
        )
    }

    private func appendJSONLine(_ value: [String: Any], to handle: FileHandle) throws {
        let data = try JSONSerialization.data(withJSONObject: value, options: [.sortedKeys])
        handle.write(data)
        handle.write(Data("\n".utf8))
    }

    private func rounded(_ value: Double) -> Double {
        Double((value * 1000).rounded() / 1000)
    }
}

final class LivePipelineWorker {
    let sessionDirectory: URL
    private let process = Process()
    private var logHandle: FileHandle?

    init(sessionDirectory: URL) throws {
        self.sessionDirectory = sessionDirectory
        let script = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("scripts/live-pipeline-shadow.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("live pipeline worker script not found: \(script.path)")
        }
        let python = Self.resolvePython()
        let logURL = sessionDirectory.appendingPathComponent("derived/live/live_worker.log")
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: Data())
        let handle = try FileHandle(forWritingTo: logURL)
        logHandle = handle
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = [script.path, sessionDirectory.path]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = handle
        process.standardError = handle
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
    }

    func start() throws {
        try process.run()
    }

    func wait(seconds: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while process.isRunning, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.25)
        }
        if !process.isRunning {
            try? logHandle?.close()
            return true
        }
        return false
    }

    func terminate() {
        if process.isRunning {
            process.terminate()
            let deadline = Date().addingTimeInterval(5)
            while process.isRunning, Date() < deadline {
                Thread.sleep(forTimeInterval: 0.1)
            }
            if process.isRunning {
                process.interrupt()
            }
        }
        try? logHandle?.close()
    }

    var terminationStatus: Int32? {
        process.isRunning ? nil : process.terminationStatus
    }

    private static func resolvePython() -> String {
        let env = ProcessInfo.processInfo.environment
        if let value = env["MURMURMARK_PYTHON"], !value.isEmpty {
            return value
        }
        let venv = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent(".venv/bin/python").path
        if FileManager.default.isExecutableFile(atPath: venv) {
            return venv
        }
        return Tooling.which("python3") ?? "/usr/bin/python3"
    }
}

final class LivePreviewConsole {
    private let process = Process()

    init(sessionDirectory: URL) throws {
        let script = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("scripts/watch-live-draft.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("live preview watcher script not found: \(script.path)")
        }
        process.executableURL = try PythonRuntime.resolve()
        process.arguments = [script.path, sessionDirectory.path, "--poll-sec", "1", "--embedded"]
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.standardOutput
        process.standardError = FileHandle.standardError
        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
    }

    func start() throws {
        try process.run()
    }

    func wait(seconds: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(seconds)
        while process.isRunning, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        return !process.isRunning
    }

    func terminate() {
        guard process.isRunning else { return }
        process.terminate()
        let deadline = Date().addingTimeInterval(1)
        while process.isRunning, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.05)
        }
        if process.isRunning {
            process.interrupt()
        }
    }

    var terminationStatus: Int32? {
        process.isRunning ? nil : process.terminationStatus
    }
}

final class AudioInputDeviceCapture: NSObject, AVCaptureFileOutputRecordingDelegate {
    let deviceID: String
    let outputURL: URL
    let source: String
    let deviceName: String

    private let session = AVCaptureSession()
    private let fileOutput = AVCaptureAudioFileOutput()
    private let finishSemaphore = DispatchSemaphore(value: 0)
    private var recordingError: Error?

    init(deviceID: String, outputURL: URL, source: String) throws {
        self.deviceID = deviceID
        self.outputURL = outputURL
        self.source = source
        self.deviceName = try AudioInputDeviceCapture.device(for: deviceID).localizedName
        super.init()
    }

    var framesWritten: AVAudioFramePosition {
        (try? AVAudioFile(forReading: outputURL).length) ?? AVAudioFramePosition(0)
    }

    var sampleRate: Double? {
        try? AVAudioFile(forReading: outputURL).processingFormat.sampleRate
    }

    func start() throws {
        try FileManager.default.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)

        let device = try Self.device(for: deviceID)
        let input = try AVCaptureDeviceInput(device: device)

        session.beginConfiguration()
        guard session.canAddInput(input) else {
            session.commitConfiguration()
            throw CLIError("cannot add audio input device for \(source): \(device.localizedName)")
        }
        session.addInput(input)

        guard session.canAddOutput(fileOutput) else {
            session.commitConfiguration()
            throw CLIError("cannot add audio output delegate for \(source): \(device.localizedName)")
        }
        session.addOutput(fileOutput)
        session.commitConfiguration()
        session.startRunning()
        fileOutput.startRecording(to: outputURL, outputFileType: .caf, recordingDelegate: self)
    }

    func stop() throws {
        if fileOutput.isRecording {
            fileOutput.stopRecording()
            _ = finishSemaphore.wait(timeout: .now() + 10)
        }
        session.stopRunning()
        if let recordingError {
            throw CLIError("\(source) audio input recording failed: \(recordingError.localizedDescription)")
        }
    }

    func fileOutput(
        _: AVCaptureFileOutput,
        didFinishRecordingTo _: URL,
        from _: [AVCaptureConnection],
        error: Error?
    ) {
        recordingError = error
        finishSemaphore.signal()
    }

    private static func device(for id: String) throws -> AVCaptureDevice {
        if id == "default", let device = AVCaptureDevice.default(for: .audio) {
            return device
        }

        let devices = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.microphone],
            mediaType: .audio,
            position: .unspecified
        ).devices
        guard let device = devices.first(where: { $0.uniqueID == id }) else {
            throw CLIError("audio input device not found: \(id)")
        }
        return device
    }
}

final class VoiceProcessingMicCapture: @unchecked Sendable {
    let outputURL: URL
    private let engine = AVAudioEngine()
    private let writerQueue = DispatchQueue(label: "murmurmark.capture.voice-processing-mic")
    private var file: AVAudioFile?
    private var monoFormat: AVAudioFormat?
    private var firstError: Error?
    private var framesWrittenStorage = AVAudioFramePosition(0)

    init(outputURL: URL) {
        self.outputURL = outputURL
    }

    var framesWritten: AVAudioFramePosition {
        writerQueue.sync { framesWrittenStorage }
    }

    var sampleRate: Double? {
        writerQueue.sync { monoFormat?.sampleRate }
    }

    func start() throws {
        try FileManager.default.createDirectory(at: outputURL.deletingLastPathComponent(), withIntermediateDirectories: true)

        let input = engine.inputNode
        _ = engine.outputNode
        do {
            try input.setVoiceProcessingEnabled(true)
        } catch {
            throw CLIError("cannot enable voice-processing microphone capture: \(error.localizedDescription)")
        }

        let format = input.outputFormat(forBus: 0)
        guard format.channelCount > 0 else {
            throw CLIError("voice-processing microphone has no input channels")
        }
        guard let monoFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: format.sampleRate,
            channels: 1,
            interleaved: false
        ) else {
            throw CLIError("cannot create mono voice-processing microphone format")
        }

        self.monoFormat = monoFormat
        file = try AVAudioFile(forWriting: outputURL, settings: monoFormat.settings)
        input.installTap(onBus: 0, bufferSize: 4_800, format: format) { [weak self] buffer, _ in
            self?.enqueue(buffer)
        }

        engine.prepare()
        try engine.start()
    }

    func stop() throws {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()

        let error = writerQueue.sync { () -> Error? in
            let error = firstError
            file = nil
            return error
        }

        if let error {
            throw error
        }
    }

    private func enqueue(_ buffer: AVAudioPCMBuffer) {
        guard let samples = firstChannelSamples(from: buffer) else {
            writerQueue.async { [self] in
                firstError = CLIError("cannot convert voice-processing microphone buffer to mono")
            }
            return
        }

        writerQueue.async { [self] in
            guard firstError == nil else { return }
            do {
                guard let monoBuffer = monoBuffer(from: samples) else {
                    throw CLIError("cannot allocate voice-processing microphone mono buffer")
                }
                try file?.write(from: monoBuffer)
                framesWrittenStorage += AVAudioFramePosition(samples.count)
            } catch {
                firstError = CLIError("voice-processing microphone write failed: \(error.localizedDescription)")
            }
        }
    }

    private func firstChannelSamples(from buffer: AVAudioPCMBuffer) -> [Float]? {
        guard let source = buffer.floatChannelData?[0] else {
            return nil
        }

        return Array(UnsafeBufferPointer(start: source, count: Int(buffer.frameLength)))
    }

    private func monoBuffer(from samples: [Float]) -> AVAudioPCMBuffer? {
        guard let monoFormat,
              let output = AVAudioPCMBuffer(pcmFormat: monoFormat, frameCapacity: AVAudioFrameCount(samples.count)),
              let target = output.floatChannelData?[0]
        else {
            return nil
        }

        output.frameLength = AVAudioFrameCount(samples.count)
        for index in samples.indices {
            target[index] = samples[index]
        }
        return output
    }
}

enum EchoGuard {
    enum Paths {
        static let audioDirectory = "derived/preprocess/audio"
        static let echoDirectory = "derived/preprocess/echo"
        static let micRawForASR = "derived/preprocess/audio/mic_raw_for_asr.wav"
        static let remoteForAEC = "derived/preprocess/audio/remote_for_aec.wav"
        static let micForASR = "derived/preprocess/audio/mic_for_asr.wav"
        static let micCleanLinear = "derived/preprocess/audio/mic_clean_linear.wav"
        static let micCleanLocalFIR = "derived/preprocess/audio/mic_clean_local_fir.wav"
        static let micRoleMaskedForASR = "derived/preprocess/audio/mic_role_masked_for_asr.wav"
        static let micRolePreview = "derived/preprocess/audio/mic_role_preview.wav"
        static let micASRSegmentsDirectory = "derived/preprocess/mic_asr_segments"
        static let micCleanSpeex = "derived/preprocess/audio/mic_clean_speex.wav"
        static let micCleanWebRTC = "derived/preprocess/audio/mic_clean_webrtc.wav"
        static let echoHatLocalFIR = "derived/preprocess/audio/echo_hat_local_fir.wav"
        static let diagnostics = "derived/preprocess/echo/echo_diagnostics.json"
        static let segments = "derived/preprocess/echo/echo_segments.jsonl"
        static let suppressionReport = "derived/preprocess/echo/echo_suppression_report.json"
        static let localFIRReport = "derived/preprocess/echo/local_fir_report.json"
        static let localFIRSegments = "derived/preprocess/echo/local_fir_segments.jsonl"
        static let speakerState = "derived/preprocess/echo/speaker_state.jsonl"
    }

    static func runDiagnostic(session: URL) throws -> EchoDiagnostics {
        let manifest = try SessionFiles.manifest(at: session)
        let inputs = try materializeWorkingAudio(session: session, manifest: manifest)
        let analysis = try EchoAnalyzer.analyze(
            micURL: session.appendingPathComponent(Paths.micRawForASR),
            remoteURL: session.appendingPathComponent(Paths.remoteForAEC)
        )

        let report = EchoDiagnostics(
            schema: "murmurmark.echo_diagnostics/v1",
            sessionID: manifest.sessionID,
            mode: "diagnostic",
            analyzer: "energy_envelope_correlation",
            inputs: inputs,
            workingAudio: EchoWorkingAudio(
                mic: Paths.micRawForASR,
                remote: Paths.remoteForAEC,
                micForASR: Paths.micForASR
            ),
            parameters: analysis.parameters,
            summary: analysis.summary
        )

        let echoDirectory = session.appendingPathComponent(Paths.echoDirectory)
        try FileManager.default.createDirectory(at: echoDirectory, withIntermediateDirectories: true)
        try JSONFiles.write(report, to: session.appendingPathComponent(Paths.diagnostics))
        try JSONLines.write(analysis.segments, to: session.appendingPathComponent(Paths.segments))
        return report
    }

    static func runClean(session: URL, engine: String, profile: String, policy: String) throws -> EchoSuppressionReport {
        let diagnostics = try runDiagnostic(session: session)
        let micURL = session.appendingPathComponent(Paths.micRawForASR)
        let remoteURL = session.appendingPathComponent(Paths.remoteForAEC)
        let micForASRURL = session.appendingPathComponent(Paths.micForASR)
        let segments = try EchoSegments.read(from: session.appendingPathComponent(Paths.segments))
        let normalizedEngine = engine == "linear" ? "linear_baseline" : engine

        let cleanPath: String
        let acceptedASRPath: String
        let roleMaskedPath: String?
        let result: EchoCleanupResult
        switch normalizedEngine {
        case "linear_baseline":
            cleanPath = Paths.micCleanLinear
            acceptedASRPath = cleanPath
            roleMaskedPath = nil
            result = try LinearEchoSuppressor.suppress(
                micURL: micURL,
                remoteURL: remoteURL,
                segments: segments,
                outputURL: session.appendingPathComponent(cleanPath),
                profile: profile
            )
        case "local_fir":
            cleanPath = Paths.micCleanLocalFIR
            acceptedASRPath = Paths.micRoleMaskedForASR
            roleMaskedPath = Paths.micRoleMaskedForASR
            result = try LocalFIREchoSuppressor.suppress(
                options: LocalFIRRunOptions(
                    session: session,
                    outputURL: session.appendingPathComponent(cleanPath),
                    roleMaskedOutputURL: session.appendingPathComponent(Paths.micRoleMaskedForASR),
                    rolePreviewOutputURL: session.appendingPathComponent(Paths.micRolePreview),
                    asrSegmentsDirectoryURL: session.appendingPathComponent(Paths.micASRSegmentsDirectory),
                    echoOutputURL: session.appendingPathComponent(Paths.echoHatLocalFIR),
                    reportURL: session.appendingPathComponent(Paths.localFIRReport),
                    segmentsURL: session.appendingPathComponent(Paths.localFIRSegments),
                    speakerStateURL: session.appendingPathComponent(Paths.speakerState),
                    policy: policy,
                    profile: profile
                )
            )
        case "speexdsp":
            cleanPath = Paths.micCleanSpeex
            acceptedASRPath = cleanPath
            roleMaskedPath = nil
            result = try SpeexDSPEchoSuppressor.suppress(
                micURL: micURL,
                remoteURL: remoteURL,
                segments: segments,
                outputURL: session.appendingPathComponent(cleanPath),
                profile: profile
            )
        case "webrtc-apm":
            cleanPath = Paths.micCleanWebRTC
            acceptedASRPath = cleanPath
            roleMaskedPath = nil
            result = try WebRTCAPMEchoSuppressor.suppress(
                micURL: micURL,
                remoteURL: remoteURL,
                diagnostics: diagnostics,
                segments: segments,
                outputURL: session.appendingPathComponent(cleanPath)
            )
        default:
            throw CLIError("echo engine is not implemented yet: \(engine)")
        }

        let accepted = result.rejectionReason == "accepted"

        try? FileManager.default.removeItem(at: micForASRURL)
        if accepted {
            try FileManager.default.copyItem(at: session.appendingPathComponent(acceptedASRPath), to: micForASRURL)
        } else {
            try FileManager.default.copyItem(at: micURL, to: micForASRURL)
        }

        let decision = EchoSuppressionDecision(
            acceptedForASR: accepted,
            micForASR: accepted ? acceptedASRPath : Paths.micRawForASR,
            fallback: Paths.micRawForASR,
            reason: accepted ? nil : result.rejectionReason
        )
        let report = EchoSuppressionReport(
            schema: "murmurmark.echo_suppression_report/v1",
            sessionID: diagnostics.sessionID,
            engine: EchoSuppressionEngine(
                name: normalizedEngine,
                profile: profile,
                frameMs: diagnostics.parameters.frameMs,
                sampleRate: normalizedEngine == "local_fir" ? 16000 : 48000
            ),
            inputs: EchoSuppressionInputs(
                mic: Paths.micRawForASR,
                remote: Paths.remoteForAEC,
                diagnostics: Paths.diagnostics
            ),
            outputs: EchoSuppressionOutputs(cleanMic: cleanPath, roleMaskedMic: roleMaskedPath),
            decision: decision,
            metrics: result.metrics,
            warnings: result.warnings
        )
        try JSONFiles.write(report, to: session.appendingPathComponent(Paths.suppressionReport))
        return report
    }

    static func inspect(session: URL) throws {
        let diagnosticsURL = session.appendingPathComponent(Paths.diagnostics)
        guard FileManager.default.fileExists(atPath: diagnosticsURL.path) else {
            print("echo: diagnostics not found")
            return
        }

        let data = try Data(contentsOf: diagnosticsURL)
        let report = try JSONDecoder().decode(EchoDiagnostics.self, from: data)
        print("echo:")
        print("  mode: \(report.mode)")
        print("  analyzer: \(report.analyzer)")
        print("  bleed_detected: \(report.summary.bleedDetected)")
        print("  median_delay_ms: \(report.summary.medianDelayMs.map(String.init) ?? "-")")
        print("  delay_range_ms: \(report.summary.delayRangeMs?.map(String.init).joined(separator: "..") ?? "-")")
        print("  segments_with_probable_bleed: \(report.summary.segmentsWithProbableBleed)")
        print("  recommendation: \(report.summary.recommendation)")
        print("  mic_for_asr: \(report.workingAudio.micForASR)")

        let suppressionURL = session.appendingPathComponent(Paths.suppressionReport)
        if FileManager.default.fileExists(atPath: suppressionURL.path) {
            let data = try Data(contentsOf: suppressionURL)
            let suppression = try JSONDecoder().decode(EchoSuppressionReport.self, from: data)
            print("  suppression_engine: \(suppression.engine.name)")
            print("  clean_mic_accepted_for_asr: \(suppression.decision.acceptedForASR)")
            print("  remote_similarity_before: \(suppression.metrics.remoteSimilarityBefore)")
            print("  remote_similarity_after: \(suppression.metrics.remoteSimilarityAfter)")
            print("  estimated_echo_reduction_db: \(suppression.metrics.estimatedEchoReductionDb)")
            if suppression.engine.name == "local_fir",
               let localFIR = try? readLocalFIRReport(session: session) {
                let delay = localFIR.summary?.medianDelayMs.map { "\($0)" } ?? "-"
                let reliableWindows = localFIR.summary?.reliableDelayWindows.map { "\($0)" } ?? "-"
                let remoteOnlyWindows = localFIR.summary?.remoteOnlyWindows.map { "\($0)" } ?? "-"
                print("  local_fir_delay_ms: \(delay)")
                print("  local_fir_reliable_delay_windows: \(reliableWindows)")
                print("  local_fir_remote_only_windows: \(remoteOnlyWindows)")
                if let acousticMode = localFIR.acousticMode {
                    print("  acoustic_mode: \(acousticMode.mode)")
                    print("  acoustic_mode_confidence: \(acousticMode.confidence)")
                }
            }
        }
    }

    private static func readLocalFIRReport(session: URL) throws -> LocalFIRHelperReport? {
        let url = session.appendingPathComponent(Paths.localFIRReport)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try JSONDecoder().decode(LocalFIRHelperReport.self, from: Data(contentsOf: url))
    }

    private static func materializeWorkingAudio(session: URL, manifest: SessionManifest) throws -> EchoInputs {
        let audioDirectory = session.appendingPathComponent(Paths.audioDirectory)
        try FileManager.default.createDirectory(at: audioDirectory, withIntermediateDirectories: true)

        let micPath = try SessionFiles.firstAudioPath(in: manifest, source: "mic")
        let remotePath = try SessionFiles.firstAudioPath(in: manifest, source: "remote")
        let micInput = session.appendingPathComponent(micPath)
        let remoteInput = session.appendingPathComponent(remotePath)
        let micOutput = session.appendingPathComponent(Paths.micRawForASR)
        let remoteOutput = session.appendingPathComponent(Paths.remoteForAEC)
        let micForASR = session.appendingPathComponent(Paths.micForASR)

        try AudioMaterializer.materialize(input: micInput, output: micOutput, sampleRate: 48000)
        try AudioMaterializer.materialize(input: remoteInput, output: remoteOutput, sampleRate: 48000)
        try? FileManager.default.removeItem(at: micForASR)
        try FileManager.default.copyItem(at: micOutput, to: micForASR)

        return EchoInputs(mic: micPath, remote: remotePath)
    }
}

enum AudioMaterializer {
    static func materialize(input: URL, output: URL, sampleRate: Int) throws {
        try FileManager.default.createDirectory(at: output.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Tooling.run("ffmpeg", [
            "-nostdin",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", input.path,
            "-map", "0:a:0",
            "-vn",
            "-ac", "1",
            "-ar", "\(sampleRate)",
            "-c:a", "pcm_f32le",
            output.path,
        ])
    }
}

enum EchoAnalyzer {
    static func analyze(micURL: URL, remoteURL: URL) throws -> EchoAnalysis {
        let frameMs = 20
        let windowSeconds = 10.0
        let stepSeconds = 5.0
        let maxDelayMs = 1500
        let mic = try AudioEnvelope.read(url: micURL, frameMs: frameMs)
        let remote = try AudioEnvelope.read(url: remoteURL, frameMs: frameMs)
        let frameRate = 1000.0 / Double(frameMs)
        let windowFrames = max(4, Int(windowSeconds * frameRate))
        let stepFrames = max(1, Int(stepSeconds * frameRate))
        let maxDelayFrames = max(1, Int(Double(maxDelayMs) / Double(frameMs)))
        let count = min(mic.values.count, remote.values.count)
        let usableWindowFrames = min(windowFrames, count)

        guard usableWindowFrames >= 4 else {
            return EchoAnalysis(
                parameters: EchoParameters(frameMs: frameMs, windowSec: windowSeconds, stepSec: stepSeconds, maxDelayMs: maxDelayMs),
                summary: EchoSummary(
                    bleedDetected: false,
                    medianDelayMs: nil,
                    delayRangeMs: nil,
                    segmentsWithProbableBleed: 0,
                    recommendation: "not_enough_audio"
                ),
                segments: []
            )
        }

        var segments: [EchoSegment] = []
        var start = 0
        while start + 4 <= count {
            let frames = min(usableWindowFrames, count - start)
            if frames < 4 {
                break
            }

            let match = bestMatch(mic: mic.values, remote: remote.values, start: start, count: frames, maxDelayFrames: maxDelayFrames)
            let remoteMean = mean(remote.values, start: start, count: frames)
            let micStart = min(start + match.delayFrames, mic.values.count - frames)
            let micMean = mean(mic.values, start: micStart, count: frames)
            let probable = match.score >= 0.48 && remoteMean > 0.0005 && micMean > 0.0005

            if probable {
                let segment = EchoSegment(
                    start: rounded(Double(start) / frameRate),
                    end: rounded(Double(start + frames) / frameRate),
                    delayMs: match.delayFrames * frameMs,
                    bleedScore: rounded(match.score),
                    doubleTalk: micMean > remoteMean * 0.75,
                    confidence: rounded(min(1.0, max(0.0, (match.score - 0.35) / 0.5)))
                )
                segments.append(segment)
            }

            if start + frames >= count {
                break
            }
            start += stepFrames
        }

        let delays = segments.map(\.delayMs)
        let summary = EchoSummary(
            bleedDetected: !segments.isEmpty,
            medianDelayMs: median(delays),
            delayRangeMs: delayRange(delays),
            segmentsWithProbableBleed: segments.count,
            recommendation: segments.isEmpty ? "use_raw_mic" : "try_conservative_aec"
        )

        return EchoAnalysis(
            parameters: EchoParameters(frameMs: frameMs, windowSec: windowSeconds, stepSec: stepSeconds, maxDelayMs: maxDelayMs),
            summary: summary,
            segments: segments
        )
    }

    private static func bestMatch(mic: [Double], remote: [Double], start: Int, count: Int, maxDelayFrames: Int) -> (delayFrames: Int, score: Double) {
        var best = (delayFrames: 0, score: 0.0)
        let maxDelay = min(maxDelayFrames, max(0, mic.count - start - count), max(0, remote.count - start - count))
        for delay in 0 ... maxDelay {
            let score = cosineSimilarity(
                left: remote,
                leftStart: start,
                right: mic,
                rightStart: start + delay,
                count: count
            )
            if score > best.score {
                best = (delay, score)
            }
        }
        return best
    }

    private static func cosineSimilarity(left: [Double], leftStart: Int, right: [Double], rightStart: Int, count: Int) -> Double {
        var dot = 0.0
        var leftNorm = 0.0
        var rightNorm = 0.0
        for offset in 0 ..< count {
            let leftValue = left[leftStart + offset]
            let rightValue = right[rightStart + offset]
            dot += leftValue * rightValue
            leftNorm += leftValue * leftValue
            rightNorm += rightValue * rightValue
        }
        guard leftNorm > 1e-12, rightNorm > 1e-12 else {
            return 0
        }
        return dot / (sqrt(leftNorm) * sqrt(rightNorm))
    }

    private static func mean(_ values: [Double], start: Int, count: Int) -> Double {
        guard count > 0 else { return 0 }
        var total = 0.0
        for index in start ..< start + count {
            total += values[index]
        }
        return total / Double(count)
    }

    private static func median(_ values: [Int]) -> Int? {
        guard !values.isEmpty else { return nil }
        let sorted = values.sorted()
        return sorted[sorted.count / 2]
    }

    private static func delayRange(_ values: [Int]) -> [Int]? {
        guard let min = values.min(), let max = values.max() else { return nil }
        return [min, max]
    }

    private static func rounded(_ value: Double) -> Double {
        (value * 1000).rounded() / 1000
    }
}

enum LinearEchoSuppressor {
    static func suppress(micURL: URL, remoteURL: URL, segments: [EchoSegment], outputURL: URL, profile: String) throws -> EchoCleanupResult {
        let mic = try MonoAudio.read(url: micURL)
        let remote = try MonoAudio.read(url: remoteURL)
        guard abs(mic.sampleRate - remote.sampleRate) < 1 else {
            throw CLIError("echo cleanup requires matching sample rates")
        }

        let sampleRate = mic.sampleRate
        var clean = mic.samples
        let sortedSegments = segments.sorted { $0.start < $1.start }
        var warnings: [EchoSuppressionWarning] = []
        var processed = 0
        var rejected = 0

        for segment in sortedSegments {
            if profile == "conservative", segment.doubleTalk {
                rejected += 1
                warnings.append(EchoSuppressionWarning(type: "double_talk_risk", start: segment.start, end: segment.end))
                continue
            }

            let remoteStart = max(0, Int((segment.start * sampleRate).rounded()))
            let remoteEnd = min(remote.samples.count, Int((segment.end * sampleRate).rounded()))
            let delaySamples = max(0, Int((Double(segment.delayMs) * sampleRate / 1000.0).rounded()))
            let micStart = min(clean.count, remoteStart + delaySamples)
            let count = min(remoteEnd - remoteStart, clean.count - micStart)
            guard count > Int(sampleRate * 0.05) else { continue }

            let gain = leakageGain(mic: mic.samples, micStart: micStart, remote: remote.samples, remoteStart: remoteStart, count: count)
            guard abs(gain) > 0.001 else { continue }

            let clampedGain = signedClamp(gain, limit: profile == "experimental_aggressive" ? 4.0 : 1.5)
            for offset in 0 ..< count {
                clean[micStart + offset] -= Float(clampedGain) * remote.samples[remoteStart + offset]
            }
            processed += 1
        }

        try MonoAudio(samples: clean, sampleRate: sampleRate).write(to: outputURL)
        return try EchoCleanupEvaluation.evaluate(
            EchoCleanupEvaluationInput(
                rawMicURL: micURL,
                cleanMicURL: outputURL,
                remoteURL: remoteURL,
                segments: segments,
                processed: processed,
                rejected: rejected,
                warnings: warnings
            )
        )
    }

    private static func signedClamp(_ value: Double, limit: Double) -> Double {
        min(max(value, -limit), limit)
    }

    private static func leakageGain(mic: [Float], micStart: Int, remote: [Float], remoteStart: Int, count: Int) -> Double {
        var dot = 0.0
        var remoteEnergy = 0.0
        for offset in 0 ..< count {
            let micValue = Double(mic[micStart + offset])
            let remoteValue = Double(remote[remoteStart + offset])
            dot += micValue * remoteValue
            remoteEnergy += remoteValue * remoteValue
        }
        guard remoteEnergy > 1e-12 else { return 0 }
        return dot / remoteEnergy
    }
}

enum SpeexDSPEchoSuppressor {
    static func suppress(micURL: URL, remoteURL: URL, segments: [EchoSegment], outputURL: URL, profile: String) throws -> EchoCleanupResult {
        let helper = try SpeexDSPHelper.ensureBuilt()
        let frameMs = 20
        let tailMs = tailLengthMs(segments: segments, profile: profile)
        try Tooling.runPath(helper, [
            micURL.path,
            remoteURL.path,
            outputURL.path,
            "\(frameMs)",
            "\(tailMs)",
        ])

        return try EchoCleanupEvaluation.evaluate(
            EchoCleanupEvaluationInput(
                rawMicURL: micURL,
                cleanMicURL: outputURL,
                remoteURL: remoteURL,
                segments: segments,
                processed: segments.count,
                rejected: 0,
                warnings: []
            )
        )
    }

    private static func tailLengthMs(segments: [EchoSegment], profile: String) -> Int {
        let maxDelay = segments.map(\.delayMs).max() ?? 0
        let roomTail = profile == "experimental_aggressive" ? 800 : 400
        return min(max(maxDelay + roomTail, 300), 2400)
    }
}

enum LocalFIREchoSuppressor {
    static func suppress(options: LocalFIRRunOptions) throws -> EchoCleanupResult {
        let python = try PythonRuntime.resolve()
        let helper = PathURLs.fileURL("scripts/echo-guard-session-local-fir.py")
        guard FileManager.default.fileExists(atPath: helper.path) else {
            throw CLIError("local FIR helper not found: \(helper.path)")
        }

        try Tooling.runPath(python, [
            helper.path,
            options.session.path,
            "--profile", options.profile == "experimental_aggressive" ? "experimental_aggressive" : "conservative",
            "--role-policy", options.policy,
            "--output-clean", options.outputURL.path,
            "--output-role-mask", options.roleMaskedOutputURL.path,
            "--output-role-preview", options.rolePreviewOutputURL.path,
            "--asr-segments-dir", options.asrSegmentsDirectoryURL.path,
            "--output-echo", options.echoOutputURL.path,
            "--report", options.reportURL.path,
            "--segments", options.segmentsURL.path,
            "--speaker-state", options.speakerStateURL.path,
        ])

        let report = try JSONDecoder().decode(LocalFIRHelperReport.self, from: Data(contentsOf: options.reportURL))
        return EchoCleanupResult(
            metrics: report.metrics,
            warnings: report.warnings,
            rejectionReason: report.decision.acceptedForASR ? "accepted" : (report.decision.reason ?? "quality_gate_failed")
        )
    }
}

struct LocalFIRRunOptions {
    let session: URL
    let outputURL: URL
    let roleMaskedOutputURL: URL
    let rolePreviewOutputURL: URL
    let asrSegmentsDirectoryURL: URL
    let echoOutputURL: URL
    let reportURL: URL
    let segmentsURL: URL
    let speakerStateURL: URL
    let policy: String
    let profile: String
}

enum PythonRuntime {
    static func resolve() throws -> URL {
        if let explicit = ProcessInfo.processInfo.environment["MURMURMARK_PYTHON"] {
            let url = PathURLs.fileURL(explicit)
            guard FileManager.default.isExecutableFile(atPath: url.path) else {
                throw CLIError("MURMURMARK_PYTHON is not executable: \(url.path)")
            }
            return url
        }

        let venv = PathURLs.fileURL(".venv/bin/python")
        if FileManager.default.isExecutableFile(atPath: venv.path) {
            return venv
        }

        if let python3 = Tooling.which("python3") {
            return URL(fileURLWithPath: python3)
        }

        throw CLIError("python runtime not found; set MURMURMARK_PYTHON or create .venv")
    }
}

struct LocalFIRHelperReport: Decodable {
    let summary: LocalFIRHelperSummary?
    let acousticMode: LocalFIRAcousticMode?
    let decision: LocalFIRHelperDecision
    let metrics: EchoSuppressionMetrics
    let warnings: [EchoSuppressionWarning]

    enum CodingKeys: String, CodingKey {
        case summary
        case acousticMode = "acoustic_mode"
        case decision
        case metrics
        case warnings
    }
}

struct LocalFIRAcousticMode: Decodable {
    let mode: String
    let confidence: Double
}

struct LocalFIRHelperSummary: Decodable {
    let reliableDelayWindows: Int?
    let medianDelayMs: Double?
    let remoteOnlyWindows: Int?

    enum CodingKeys: String, CodingKey {
        case reliableDelayWindows = "reliable_delay_windows"
        case medianDelayMs = "median_delay_ms"
        case remoteOnlyWindows = "remote_only_windows"
    }
}

struct LocalFIRHelperDecision: Decodable {
    let acceptedForASR: Bool
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case acceptedForASR = "accepted_for_asr"
        case reason
    }
}

enum SpeexDSPHelper {
    static func ensureBuilt() throws -> URL {
        if let explicit = ProcessInfo.processInfo.environment["MURMURMARK_SPEEXDSP_HELPER"] {
            let url = PathURLs.fileURL(explicit)
            guard FileManager.default.isExecutableFile(atPath: url.path) else {
                throw CLIError("MURMURMARK_SPEEXDSP_HELPER is not executable: \(url.path)")
            }
            return url
        }

        let helper = PathURLs.fileURL(".build/tools/murmurmark-aec-speexdsp")
        if FileManager.default.isExecutableFile(atPath: helper.path) {
            return helper
        }

        let buildScript = PathURLs.fileURL("scripts/build-speexdsp-helper.sh")
        guard FileManager.default.isExecutableFile(atPath: buildScript.path) else {
            throw CLIError("SpeexDSP helper build script not found: \(buildScript.path)")
        }
        try Tooling.runPath(buildScript, [helper.path])
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw CLIError("SpeexDSP helper was not created: \(helper.path)")
        }
        return helper
    }
}

enum WebRTCAPMEchoSuppressor {
    static func suppress(
        micURL: URL,
        remoteURL: URL,
        diagnostics: EchoDiagnostics,
        segments: [EchoSegment],
        outputURL: URL
    ) throws -> EchoCleanupResult {
        let helper = try WebRTCAPMHelper.ensureBuilt()
        var arguments = [
            micURL.path,
            remoteURL.path,
            outputURL.path,
        ]
        if let delayMs = diagnostics.summary.medianDelayMs {
            arguments.append("\(delayMs)")
        }
        try Tooling.runPath(helper, arguments)

        return try EchoCleanupEvaluation.evaluate(
            EchoCleanupEvaluationInput(
                rawMicURL: micURL,
                cleanMicURL: outputURL,
                remoteURL: remoteURL,
                segments: segments,
                processed: segments.count,
                rejected: 0,
                warnings: []
            )
        )
    }
}

enum WebRTCAPMHelper {
    static func ensureBuilt() throws -> URL {
        if let explicit = ProcessInfo.processInfo.environment["MURMURMARK_WEBRTC_APM_HELPER"] {
            let url = PathURLs.fileURL(explicit)
            guard FileManager.default.isExecutableFile(atPath: url.path) else {
                throw CLIError("MURMURMARK_WEBRTC_APM_HELPER is not executable: \(url.path)")
            }
            return url
        }

        let helper = PathURLs.fileURL(".build/tools/murmurmark-aec-webrtc")
        if FileManager.default.isExecutableFile(atPath: helper.path) {
            return helper
        }

        let buildScript = PathURLs.fileURL("scripts/build-webrtc-apm-helper.sh")
        guard FileManager.default.isExecutableFile(atPath: buildScript.path) else {
            throw CLIError("WebRTC APM helper build script not found: \(buildScript.path)")
        }
        try Tooling.runPath(buildScript, [helper.path])
        guard FileManager.default.isExecutableFile(atPath: helper.path) else {
            throw CLIError("WebRTC APM helper was not created: \(helper.path)")
        }
        return helper
    }
}

struct EchoCleanupEvaluationInput {
    let rawMicURL: URL
    let cleanMicURL: URL
    let remoteURL: URL
    let segments: [EchoSegment]
    let processed: Int
    let rejected: Int
    let warnings: [EchoSuppressionWarning]
}

enum EchoCleanupEvaluation {
    static func evaluate(_ input: EchoCleanupEvaluationInput) throws -> EchoCleanupResult {
        let raw = try MonoAudio.read(url: input.rawMicURL)
        let clean = try MonoAudio.read(url: input.cleanMicURL)
        let before = averageScore(input.segments)
        let afterAnalysis = try EchoAnalyzer.analyze(micURL: input.cleanMicURL, remoteURL: input.remoteURL)
        let after = averageScore(afterAnalysis.segments)
        let lossRatio = max(0, 1 - rms(clean.samples) / max(rms(raw.samples), 1e-9))
        let reductionDb = before > 0 && after > 0 ? 20 * log10(before / after) : (before > 0 && after == 0 ? 60 : 0)
        let metrics = EchoSuppressionMetrics(
            remoteSimilarityBefore: rounded(before),
            remoteSimilarityAfter: rounded(after),
            estimatedEchoReductionDb: rounded(reductionDb),
            nearEndSpeechLossRatio: rounded(lossRatio),
            segmentsProcessed: input.processed,
            segmentsRejected: input.rejected
        )
        let reason = acceptanceReason(metrics)
        return EchoCleanupResult(metrics: metrics, warnings: input.warnings, rejectionReason: reason)
    }

    private static func rms(_ samples: [Float]) -> Double {
        guard !samples.isEmpty else { return 0 }
        let total = samples.reduce(0.0) { $0 + Double($1) * Double($1) }
        return sqrt(total / Double(samples.count))
    }

    private static func rounded(_ value: Double) -> Double {
        (value * 1000).rounded() / 1000
    }

    private static func averageScore(_ segments: [EchoSegment]) -> Double {
        guard !segments.isEmpty else { return 0 }
        return segments.reduce(0.0) { $0 + $1.bleedScore } / Double(segments.count)
    }

    private static func acceptanceReason(_ metrics: EchoSuppressionMetrics) -> String {
        guard metrics.segmentsProcessed > 0 else {
            return "no_safe_segments_to_process"
        }
        guard metrics.remoteSimilarityBefore > 0 else {
            return "no_remote_similarity_baseline"
        }
        guard metrics.remoteSimilarityAfter <= metrics.remoteSimilarityBefore * 0.65 else {
            return "remote_similarity_not_reduced_enough"
        }
        guard metrics.remoteSimilarityAfter <= 0.45 else {
            return "residual_remote_similarity_too_high"
        }
        guard metrics.nearEndSpeechLossRatio <= 0.30 else {
            return "near_end_speech_damage_detected"
        }
        return "accepted"
    }
}

struct EchoCleanupResult {
    let metrics: EchoSuppressionMetrics
    let warnings: [EchoSuppressionWarning]
    let rejectionReason: String
}

struct MonoAudio {
    let samples: [Float]
    let sampleRate: Double

    static func read(url: URL) throws -> MonoAudio {
        let file = try AVAudioFile(forReading: url)
        let format = file.processingFormat
        guard format.channelCount == 1 else {
            throw CLIError("expected mono audio: \(url.path)")
        }

        let frameCapacity = AVAudioFrameCount(min(file.length, 48000))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: max(1, frameCapacity)) else {
            throw CLIError("cannot allocate audio buffer for \(url.path)")
        }

        var samples: [Float] = []
        samples.reserveCapacity(Int(file.length))
        var remainingFrames = file.length
        while remainingFrames > 0 {
            let framesToRead = min(buffer.frameCapacity, AVAudioFrameCount(remainingFrames))
            try file.read(into: buffer, frameCount: framesToRead)
            guard buffer.frameLength > 0 else { break }
            guard let channel = buffer.floatChannelData?[0] else {
                throw CLIError("expected float PCM audio: \(url.path)")
            }
            samples.append(contentsOf: UnsafeBufferPointer(start: channel, count: Int(buffer.frameLength)))
            remainingFrames -= AVAudioFramePosition(buffer.frameLength)
        }
        return MonoAudio(samples: samples, sampleRate: format.sampleRate)
    }

    func write(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false) else {
            throw CLIError("cannot create float mono format")
        }
        let file = try AVAudioFile(forWriting: url, settings: format.settings)
        let chunkSize = 48000
        var offset = 0
        while offset < samples.count {
            let count = min(chunkSize, samples.count - offset)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)) else {
                throw CLIError("cannot allocate output audio buffer")
            }
            buffer.frameLength = AVAudioFrameCount(count)
            guard let channel = buffer.floatChannelData?[0] else {
                throw CLIError("cannot access output audio buffer")
            }
            for index in 0 ..< count {
                channel[index] = samples[offset + index]
            }
            try file.write(from: buffer)
            offset += count
        }
    }
}

enum AudioLevelProbe {
    static func rmsDb(url: URL) throws -> Double {
        if let ffmpegRMS = try? ffmpegRmsDb(url: url) {
            return ffmpegRMS
        }
        return try avFoundationRmsDb(url: url)
    }

    private static func ffmpegRmsDb(url: URL) throws -> Double {
        let output = try Tooling.runCapturing("ffmpeg", [
            "-hide_banner",
            "-nostdin",
            "-i", url.path,
            "-af", "astats=metadata=1:reset=0",
            "-f", "null",
            "-",
        ])
        let values = output
            .split(separator: "\n")
            .filter { $0.contains("RMS level dB:") }
            .compactMap { line -> Double? in
                guard let value = line.split(separator: ":").last?.trimmingCharacters(in: .whitespaces) else {
                    return nil
                }
                return value == "-inf" ? -.infinity : Double(value)
            }

        guard !values.isEmpty else {
            throw CLIError("cannot parse audio RMS from ffmpeg output for \(url.path)")
        }
        let finite = values.filter(\.isFinite)
        return finite.max() ?? -.infinity
    }

    private static func avFoundationRmsDb(url: URL) throws -> Double {
        let file = try AVAudioFile(forReading: url)
        let format = file.processingFormat
        let chunkFrames = AVAudioFrameCount(max(1, min(file.length, 48_000)))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: chunkFrames) else {
            throw CLIError("cannot allocate audio level buffer for \(url.path)")
        }

        var total = 0.0
        var count = 0
        var remainingFrames = file.length
        while remainingFrames > 0 {
            let framesToRead = min(buffer.frameCapacity, AVAudioFrameCount(remainingFrames))
            try file.read(into: buffer, frameCount: framesToRead)
            guard buffer.frameLength > 0 else { break }
            guard let channels = buffer.floatChannelData else {
                throw CLIError("expected float audio while probing \(url.path)")
            }

            for channelIndex in 0 ..< Int(format.channelCount) {
                let channel = channels[channelIndex]
                for frameIndex in 0 ..< Int(buffer.frameLength) {
                    let value = Double(channel[frameIndex])
                    total += value * value
                    count += 1
                }
            }
            remainingFrames -= AVAudioFramePosition(buffer.frameLength)
        }

        guard count > 0 else { return -.infinity }
        let rms = sqrt(total / Double(count))
        guard rms > 1e-12 else { return -.infinity }
        return 20 * log10(rms)
    }

    static func formatDb(_ value: Double) -> String {
        if value.isFinite {
            return "\(String(format: "%.1f", value)) dB"
        }
        return "-inf dB"
    }
}

struct AudioEnvelope {
    let values: [Double]

    static func read(url: URL, frameMs: Int) throws -> AudioEnvelope {
        let file = try AVAudioFile(forReading: url)
        let sampleRate = file.processingFormat.sampleRate
        let frameCount = max(1, AVAudioFrameCount((sampleRate * Double(frameMs) / 1000.0).rounded()))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: frameCount) else {
            throw CLIError("cannot allocate envelope buffer for \(url.path)")
        }

        var values: [Double] = []
        var remainingFrames = file.length
        while remainingFrames > 0 {
            let framesToRead = min(frameCount, AVAudioFrameCount(remainingFrames))
            try file.read(into: buffer, frameCount: framesToRead)
            guard buffer.frameLength > 0 else { break }
            guard let channel = buffer.floatChannelData?[0] else {
                throw CLIError("expected float mono WAV for echo analysis: \(url.path)")
            }
            var sumSquares = 0.0
            for index in 0 ..< Int(buffer.frameLength) {
                let value = Double(channel[index])
                sumSquares += value * value
            }
            values.append(sqrt(sumSquares / Double(max(1, Int(buffer.frameLength)))))
            remainingFrames -= AVAudioFramePosition(buffer.frameLength)
        }

        return AudioEnvelope(values: values)
    }
}

struct EchoAnalysis {
    let parameters: EchoParameters
    let summary: EchoSummary
    let segments: [EchoSegment]
}

struct EchoDiagnostics: Codable {
    let schema: String
    let sessionID: String
    let mode: String
    let analyzer: String
    let inputs: EchoInputs
    let workingAudio: EchoWorkingAudio
    let parameters: EchoParameters
    let summary: EchoSummary

    enum CodingKeys: String, CodingKey {
        case schema
        case sessionID = "session_id"
        case mode
        case analyzer
        case inputs
        case workingAudio = "working_audio"
        case parameters
        case summary
    }
}

struct EchoInputs: Codable {
    let mic: String
    let remote: String
}

struct EchoWorkingAudio: Codable {
    let mic: String
    let remote: String
    let micForASR: String

    enum CodingKeys: String, CodingKey {
        case mic
        case remote
        case micForASR = "mic_for_asr"
    }
}

struct EchoParameters: Codable {
    let frameMs: Int
    let windowSec: Double
    let stepSec: Double
    let maxDelayMs: Int

    enum CodingKeys: String, CodingKey {
        case frameMs = "frame_ms"
        case windowSec = "window_sec"
        case stepSec = "step_sec"
        case maxDelayMs = "max_delay_ms"
    }
}

struct EchoSummary: Codable {
    let bleedDetected: Bool
    let medianDelayMs: Int?
    let delayRangeMs: [Int]?
    let segmentsWithProbableBleed: Int
    let recommendation: String

    enum CodingKeys: String, CodingKey {
        case bleedDetected = "bleed_detected"
        case medianDelayMs = "median_delay_ms"
        case delayRangeMs = "delay_range_ms"
        case segmentsWithProbableBleed = "segments_with_probable_bleed"
        case recommendation
    }
}

struct EchoSegment: Codable {
    let start: Double
    let end: Double
    let delayMs: Int
    let bleedScore: Double
    let doubleTalk: Bool
    let confidence: Double

    enum CodingKeys: String, CodingKey {
        case start
        case end
        case delayMs = "delay_ms"
        case bleedScore = "bleed_score"
        case doubleTalk = "double_talk"
        case confidence
    }
}

struct EchoSuppressionReport: Codable {
    let schema: String
    let sessionID: String
    let engine: EchoSuppressionEngine
    let inputs: EchoSuppressionInputs
    let outputs: EchoSuppressionOutputs
    let decision: EchoSuppressionDecision
    let metrics: EchoSuppressionMetrics
    let warnings: [EchoSuppressionWarning]

    enum CodingKeys: String, CodingKey {
        case schema
        case sessionID = "session_id"
        case engine
        case inputs
        case outputs
        case decision
        case metrics
        case warnings
    }
}

struct EchoSuppressionEngine: Codable {
    let name: String
    let profile: String
    let frameMs: Int
    let sampleRate: Int

    enum CodingKeys: String, CodingKey {
        case name
        case profile
        case frameMs = "frame_ms"
        case sampleRate = "sample_rate"
    }
}

struct EchoSuppressionInputs: Codable {
    let mic: String
    let remote: String
    let diagnostics: String
}

struct EchoSuppressionOutputs: Codable {
    let cleanMic: String
    let roleMaskedMic: String?

    enum CodingKeys: String, CodingKey {
        case cleanMic = "clean_mic"
        case roleMaskedMic = "role_masked_mic"
    }
}

struct EchoSuppressionDecision: Codable {
    let acceptedForASR: Bool
    let micForASR: String
    let fallback: String
    let reason: String?

    enum CodingKeys: String, CodingKey {
        case acceptedForASR = "accepted_for_asr"
        case micForASR = "mic_for_asr"
        case fallback
        case reason
    }
}

struct EchoSuppressionMetrics: Codable {
    let remoteSimilarityBefore: Double
    let remoteSimilarityAfter: Double
    let estimatedEchoReductionDb: Double
    let nearEndSpeechLossRatio: Double
    let segmentsProcessed: Int
    let segmentsRejected: Int

    enum CodingKeys: String, CodingKey {
        case remoteSimilarityBefore = "remote_similarity_before"
        case remoteSimilarityAfter = "remote_similarity_after"
        case estimatedEchoReductionDb = "estimated_echo_reduction_db"
        case nearEndSpeechLossRatio = "near_end_speech_loss_ratio"
        case segmentsProcessed = "segments_processed"
        case segmentsRejected = "segments_rejected"
    }
}

struct EchoSuppressionWarning: Codable {
    let type: String
    let start: Double
    let end: Double
}

enum EchoSegments {
    static func read(from url: URL) throws -> [EchoSegment] {
        guard FileManager.default.fileExists(atPath: url.path) else { return [] }
        let text = try String(contentsOf: url, encoding: .utf8)
        let decoder = JSONDecoder()
        return try text
            .split(separator: "\n")
            .map { try decoder.decode(EchoSegment.self, from: Data($0.utf8)) }
    }
}

struct TranscriptEchoGuardOptions {
    let transcriptURL: URL
    let outputURL: URL
    let qualityReportURL: URL
    let textThreshold: Double
    let timeTolerance: Double
}

enum TranscriptEchoGuard {
    enum Paths {
        static let transcript = "derived/transcript/resolved/transcript.rich.json"
        static let qualityReport = "derived/transcript/resolved/quality_report.json"
        static let reconciliationReport = "derived/transcript/resolved/echo_reconciliation_report.json"
    }

    static func reconcile(
        session: URL,
        options: TranscriptEchoGuardOptions
    ) throws -> TranscriptEchoReconciliationReport {
        let transcriptURL = options.transcriptURL
        let outputURL = options.outputURL
        let qualityReportURL = options.qualityReportURL
        guard FileManager.default.fileExists(atPath: transcriptURL.path) else {
            throw CLIError("transcript not found: \(transcriptURL.path)")
        }

        let diagnosticsURL = session.appendingPathComponent(EchoGuard.Paths.diagnostics)
        guard FileManager.default.fileExists(atPath: diagnosticsURL.path) else {
            throw CLIError("echo diagnostics not found; run preprocess --echo diagnostic first")
        }

        let diagnostics = try JSONDecoder().decode(EchoDiagnostics.self, from: Data(contentsOf: diagnosticsURL))
        let segments = try EchoSegments.read(from: session.appendingPathComponent(EchoGuard.Paths.segments))
        guard !segments.isEmpty else {
            throw CLIError("echo segments not found or empty; run preprocess --echo diagnostic first")
        }

        var root = try JSONObject.readDictionary(from: transcriptURL)
        guard var utterances = root["utterances"] as? [[String: Any]] else {
            throw CLIError("transcript has no utterances array: \(transcriptURL.path)")
        }

        let remote = utterances.compactMap(TranscriptUtterance.init(json:))
            .filter { $0.sourceTrack == "remote" }
        let micIndexes = utterances.indices.filter { (utterances[$0]["source_track"] as? String) == "mic" }

        var matches: [TranscriptEchoMatch] = []
        for index in micIndexes {
            guard let mic = TranscriptUtterance(json: utterances[index]),
                  let match = bestMatch(
                      mic: mic,
                      remote: remote,
                      segments: segments,
                      textThreshold: options.textThreshold,
                      timeTolerance: options.timeTolerance
                  )
            else {
                continue
            }

            var utterance = utterances[index]
            var quality = utterance["quality"] as? [String: Any] ?? [:]
            quality["possible_mic_leakage"] = true
            quality["excluded_from_me_role"] = true
            quality["matched_remote_utterance_id"] = match.remoteID
            quality["needs_review"] = match.confidence < 0.70
            quality["echo_guard"] = [
                "reason": "matches_remote_utterance_with_echo_delay",
                "matched_remote_utterance_id": match.remoteID,
                "delay_ms": match.delayMs,
                "time_delta_ms": match.timeDeltaMs,
                "text_similarity": match.textSimilarity,
                "confidence": match.confidence,
                "segment_confidence": match.segmentConfidence,
            ]
            utterance["quality"] = quality
            utterances[index] = utterance
            matches.append(match)
        }

        root["utterances"] = utterances
        try JSONObject.write(root, to: outputURL)

        let suppression = try readSuppressionReportIfPresent(session: session)
        let report = TranscriptEchoReconciliationReport(
            schema: "murmurmark.echo_reconciliation_report/v1",
            sessionID: diagnostics.sessionID,
            inputs: TranscriptEchoReconciliationInputs(
                transcript: RelativePaths.path(outputURL: transcriptURL, relativeTo: session),
                diagnostics: EchoGuard.Paths.diagnostics,
                segments: EchoGuard.Paths.segments,
                suppressionReport: suppression == nil ? nil : EchoGuard.Paths.suppressionReport
            ),
            outputs: TranscriptEchoReconciliationOutputs(
                transcript: RelativePaths.path(outputURL: outputURL, relativeTo: session),
                qualityReport: RelativePaths.path(outputURL: qualityReportURL, relativeTo: session)
            ),
            parameters: TranscriptEchoReconciliationParameters(
                textThreshold: options.textThreshold,
                timeToleranceSec: options.timeTolerance
            ),
            summary: TranscriptEchoReconciliationSummary(
                utterancesTotal: utterances.count,
                micUtterances: micIndexes.count,
                remoteUtterances: remote.count,
                matchedMicUtterances: matches.count,
                probableBleedSegments: diagnostics.summary.segmentsWithProbableBleed
            ),
            matches: matches
        )
        try JSONFiles.write(report, to: session.appendingPathComponent(Paths.reconciliationReport))
        try updateQualityReport(
            url: qualityReportURL,
            sessionID: diagnostics.sessionID,
            diagnostics: diagnostics,
            suppression: suppression,
            reconciliation: report
        )
        return report
    }

    private static func bestMatch(
        mic: TranscriptUtterance,
        remote: [TranscriptUtterance],
        segments: [EchoSegment],
        textThreshold: Double,
        timeTolerance: Double
    ) -> TranscriptEchoMatch? {
        var best: TranscriptEchoMatch?
        var bestScore = 0.0

        for segment in segments where segment.confidence >= 0.50 {
            let delay = Double(segment.delayMs) / 1000.0
            for candidate in remote {
                let similarity = TextSimilarity.score(mic.text, candidate.text)
                guard similarity >= textThreshold else { continue }

                let shiftedStart = candidate.start + delay
                let shiftedEnd = candidate.end + delay
                let gap = Interval.gap(startA: mic.start, endA: mic.end, startB: shiftedStart, endB: shiftedEnd)
                let midDelta = abs(((mic.start + mic.end) / 2.0) - ((candidate.start + candidate.end) / 2.0) - delay)
                guard gap <= timeTolerance || midDelta <= timeTolerance else { continue }

                let timePenalty = min(gap, midDelta) / max(timeTolerance, 0.001)
                let timeScore = max(0.0, 1.0 - timePenalty)
                let score = similarity * 0.65 + timeScore * 0.25 + segment.confidence * 0.10
                guard score > bestScore else { continue }

                bestScore = score
                best = TranscriptEchoMatch(
                    micID: mic.id,
                    remoteID: candidate.id,
                    micStart: mic.start,
                    micEnd: mic.end,
                    remoteStart: candidate.start,
                    remoteEnd: candidate.end,
                    delayMs: segment.delayMs,
                    timeDeltaMs: Int(round(midDelta * 1000.0)),
                    textSimilarity: Numbers.round(similarity),
                    confidence: Numbers.round(score),
                    segmentConfidence: Numbers.round(segment.confidence)
                )
            }
        }

        return best
    }

    private static func readSuppressionReportIfPresent(session: URL) throws -> EchoSuppressionReport? {
        let url = session.appendingPathComponent(EchoGuard.Paths.suppressionReport)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return try JSONDecoder().decode(EchoSuppressionReport.self, from: Data(contentsOf: url))
    }

    private static func updateQualityReport(
        url: URL,
        sessionID: String,
        diagnostics: EchoDiagnostics,
        suppression: EchoSuppressionReport?,
        reconciliation: TranscriptEchoReconciliationReport
    ) throws {
        var root: [String: Any]
        if FileManager.default.fileExists(atPath: url.path) {
            root = try JSONObject.readDictionary(from: url)
        } else {
            root = [:]
            root["schema"] = "murmurmark.quality_report/v1"
            root["session_id"] = sessionID
            root["summary"] = [String: Any]()
            root["risks"] = [[String: Any]]()
        }

        var summary = root["summary"] as? [String: Any] ?? [:]
        var echo = summary["echo"] as? [String: Any] ?? [:]
        echo["mode"] = suppression?.engine.profile ?? diagnostics.mode
        echo["bleed_detected"] = diagnostics.summary.bleedDetected
        if let medianDelayMs = diagnostics.summary.medianDelayMs {
            echo["median_delay_ms"] = medianDelayMs
        } else {
            echo.removeValue(forKey: "median_delay_ms")
        }
        echo["suppression_attempted"] = suppression != nil
        echo["clean_mic_accepted_for_asr"] = suppression?.decision.acceptedForASR ?? false
        echo["segments_with_probable_bleed"] = diagnostics.summary.segmentsWithProbableBleed
        echo["segments_excluded_from_me_role"] = reconciliation.summary.matchedMicUtterances
        echo["transcript_guard_applied"] = true
        summary["echo"] = echo
        root["summary"] = summary

        var risks = root["risks"] as? [[String: Any]] ?? []
        risks.removeAll { ($0["source"] as? String) == "echo_guard_transcript_reconciliation" }
        for match in reconciliation.matches {
            risks.append([
                "type": "probable_remote_bleed_in_mic",
                "source": "echo_guard_transcript_reconciliation",
                "start": match.micStart,
                "end": match.micEnd,
                "delay_ms": match.delayMs,
                "confidence": match.confidence,
                "mic_utterance_id": match.micID,
                "matched_remote_utterance_id": match.remoteID,
            ])
        }
        if let suppression, !suppression.decision.acceptedForASR {
            risks.append([
                "type": "echo_suppression_rejected",
                "source": "echo_guard_transcript_reconciliation",
                "reason": suppression.decision.reason ?? "quality_gate_failed",
            ])
        }
        root["risks"] = risks

        try JSONObject.write(root, to: url)
    }
}

struct TranscriptUtterance {
    let id: String
    let sourceTrack: String
    let start: Double
    let end: Double
    let text: String

    init?(json: [String: Any]) {
        guard let id = json["id"] as? String,
              let sourceTrack = json["source_track"] as? String,
              let start = JSONScalars.double(json["start"]),
              let end = JSONScalars.double(json["end"])
        else {
            return nil
        }

        let text = (json["corrected_text"] as? String)
            ?? (json["raw_text"] as? String)
            ?? (json["text"] as? String)
            ?? ""
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }

        self.id = id
        self.sourceTrack = sourceTrack
        self.start = start
        self.end = end
        self.text = text
    }
}

struct TranscriptEchoReconciliationReport: Codable {
    let schema: String
    let sessionID: String
    let inputs: TranscriptEchoReconciliationInputs
    let outputs: TranscriptEchoReconciliationOutputs
    let parameters: TranscriptEchoReconciliationParameters
    let summary: TranscriptEchoReconciliationSummary
    let matches: [TranscriptEchoMatch]

    enum CodingKeys: String, CodingKey {
        case schema
        case sessionID = "session_id"
        case inputs
        case outputs
        case parameters
        case summary
        case matches
    }
}

struct TranscriptEchoReconciliationInputs: Codable {
    let transcript: String
    let diagnostics: String
    let segments: String
    let suppressionReport: String?

    enum CodingKeys: String, CodingKey {
        case transcript
        case diagnostics
        case segments
        case suppressionReport = "suppression_report"
    }
}

struct TranscriptEchoReconciliationOutputs: Codable {
    let transcript: String
    let qualityReport: String

    enum CodingKeys: String, CodingKey {
        case transcript
        case qualityReport = "quality_report"
    }
}

struct TranscriptEchoReconciliationParameters: Codable {
    let textThreshold: Double
    let timeToleranceSec: Double

    enum CodingKeys: String, CodingKey {
        case textThreshold = "text_threshold"
        case timeToleranceSec = "time_tolerance_sec"
    }
}

struct TranscriptEchoReconciliationSummary: Codable {
    let utterancesTotal: Int
    let micUtterances: Int
    let remoteUtterances: Int
    let matchedMicUtterances: Int
    let probableBleedSegments: Int

    enum CodingKeys: String, CodingKey {
        case utterancesTotal = "utterances_total"
        case micUtterances = "mic_utterances"
        case remoteUtterances = "remote_utterances"
        case matchedMicUtterances = "matched_mic_utterances"
        case probableBleedSegments = "probable_bleed_segments"
    }
}

struct TranscriptEchoMatch: Codable {
    let micID: String
    let remoteID: String
    let micStart: Double
    let micEnd: Double
    let remoteStart: Double
    let remoteEnd: Double
    let delayMs: Int
    let timeDeltaMs: Int
    let textSimilarity: Double
    let confidence: Double
    let segmentConfidence: Double

    enum CodingKeys: String, CodingKey {
        case micID = "mic_id"
        case remoteID = "remote_id"
        case micStart = "mic_start"
        case micEnd = "mic_end"
        case remoteStart = "remote_start"
        case remoteEnd = "remote_end"
        case delayMs = "delay_ms"
        case timeDeltaMs = "time_delta_ms"
        case textSimilarity = "text_similarity"
        case confidence
        case segmentConfidence = "segment_confidence"
    }
}

enum TextSimilarity {
    static func score(_ lhs: String, _ rhs: String) -> Double {
        let left = Set(tokens(lhs))
        let right = Set(tokens(rhs))
        guard !left.isEmpty, !right.isEmpty else { return 0.0 }
        let overlap = left.intersection(right).count
        return Double(2 * overlap) / Double(left.count + right.count)
    }

    private static func tokens(_ text: String) -> [String] {
        var result: [String] = []
        var current = ""
        let normalized = text
            .lowercased()
            .replacingOccurrences(of: "ё", with: "е")

        for scalar in normalized.unicodeScalars {
            if CharacterSet.alphanumerics.contains(scalar) {
                current.unicodeScalars.append(scalar)
            } else if !current.isEmpty {
                result.append(current)
                current = ""
            }
        }
        if !current.isEmpty {
            result.append(current)
        }
        return result
    }
}

enum Interval {
    static func overlap(startA: Double, endA: Double, startB: Double, endB: Double) -> Double {
        max(0.0, min(endA, endB) - max(startA, startB))
    }

    static func gap(startA: Double, endA: Double, startB: Double, endB: Double) -> Double {
        if overlap(startA: startA, endA: endA, startB: startB, endB: endB) > 0 {
            return 0.0
        }
        return max(max(startB - endA, startA - endB), 0.0)
    }
}

enum Numbers {
    static func round(_ value: Double) -> Double {
        (value * 1000.0).rounded() / 1000.0
    }
}

enum JSONScalars {
    static func double(_ value: Any?) -> Double? {
        switch value {
        case let value as Double:
            value
        case let value as Int:
            Double(value)
        case let value as NSNumber:
            value.doubleValue
        case let value as String:
            Double(value)
        default:
            nil
        }
    }
}

struct PartialCaptureInfo {
    let reason: String
    let summary: String
    let warnings: [String]
    let actualDurationSec: Double?
    let requestedDurationSec: Double?
    let restartCount: Int?
}

enum CaptureHealthState {
    private static let partialStopReasons: Set<String> = ["stream_stopped", "capture_stalled", "sigterm", "sighup"]

    static func partialInfo(session: URL) -> PartialCaptureInfo? {
        let manifestURL = session.appendingPathComponent("session.json")
        guard let manifest = try? JSONFiles.object(manifestURL) else { return nil }
        let health = manifest["health"] as? [String: Any] ?? [:]
        let status = manifest["status"] as? String ?? ""
        let reason = string(health["stop_reason"])
            ?? finalCaptureStopReason(session: session)
            ?? ""
        let partial = bool(health["partial"]) == true
            || status == "partial"
            || partialStopReasons.contains(reason)
        guard partial else { return nil }
        return PartialCaptureInfo(
            reason: reason.isEmpty ? "unknown" : reason,
            summary: string(health["summary"]) ?? status,
            warnings: strings(health["warnings"]),
            actualDurationSec: JSONScalars.double(health["actual_duration_sec"]),
            requestedDurationSec: JSONScalars.double(health["requested_duration_sec"]),
            restartCount: int(health["screen_capture_restart_count"])
        )
    }

    static func partialNextCommands(session: URL) -> [[String: String]] {
        let sessionPath = PathDisplay.display(session)
        return [
            [
                "id": "inspect_partial_session",
                "label": "Inspect partial recording health and raw track durations.",
                "command": "murmurmark inspect \(sessionPath)",
            ],
            [
                "id": "record_again",
                "label": "Start a fresh recording for a live meeting.",
                "command": "murmurmark record --target-bundle system",
            ],
            [
                "id": "debug_process_partial",
                "label": "Debug only: force processing of the partial recording.",
                "command": "murmurmark process \(sessionPath) --allow-partial",
            ],
        ]
    }

    static func preferredPartialNext(session: URL) -> String {
        partialNextCommands(session: session)[0]["command"] ?? "murmurmark inspect \(PathDisplay.display(session))"
    }

    private static func finalCaptureStopReason(session: URL) -> String? {
        let eventsURL = session.appendingPathComponent("events.jsonl")
        guard FileManager.default.fileExists(atPath: eventsURL.path),
              let text = try? String(contentsOf: eventsURL, encoding: .utf8)
        else {
            return nil
        }
        var reason: String?
        for line in text.split(separator: "\n") {
            guard let data = String(line).data(using: .utf8),
                  let row = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  row["type"] as? String == "capture.stopped"
            else {
                continue
            }
            reason = row["reason"] as? String
        }
        return reason
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func strings(_ value: Any?) -> [String] {
        (value as? [Any] ?? []).map { String(describing: $0) }
    }

    private static func bool(_ value: Any?) -> Bool? {
        switch value {
        case let value as Bool:
            value
        case let value as String:
            ["true", "yes", "1"].contains(value.lowercased())
        case let value as NSNumber:
            value.boolValue
        default:
            nil
        }
    }

    private static func int(_ value: Any?) -> Int? {
        switch value {
        case let value as Int:
            value
        case let value as NSNumber:
            value.intValue
        case let value as String:
            Int(value)
        default:
            nil
        }
    }
}

enum JSONObject {
    static func readDictionary(from url: URL) throws -> [String: Any] {
        let data = try Data(contentsOf: url)
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw CLIError("expected JSON object: \(url.path)")
        }
        return root
    }

    static func write(_ value: [String: Any], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        guard JSONSerialization.isValidJSONObject(value) else {
            throw CLIError("cannot write invalid JSON object: \(url.path)")
        }
        let data = try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
        try data.write(to: url, options: .atomic)
    }
}

enum RelativePaths {
    static func path(outputURL: URL, relativeTo root: URL) -> String {
        let rootPath = root.standardizedFileURL.path
        let outputPath = outputURL.standardizedFileURL.path
        if outputPath.hasPrefix(rootPath + "/") {
            return String(outputPath.dropFirst(rootPath.count + 1))
        }
        return outputPath
    }
}

enum SessionFiles {
    static func manifest(at session: URL) throws -> SessionManifest {
        let data = try Data(contentsOf: session.appendingPathComponent("session.json"))
        return try JSONDecoder().decode(SessionManifest.self, from: data)
    }

    static func firstAudioPath(in manifest: SessionManifest, source: String) throws -> String {
        guard let path = manifest.files[source]?.first?.path else {
            throw CLIError("session manifest has no \(source) audio file")
        }
        return path
    }
}

enum JSONFiles {
    static func write<T: Encodable>(_ value: T, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(value).write(to: url, options: .atomic)
    }

    static func object(_ url: URL) throws -> [String: Any] {
        guard FileManager.default.fileExists(atPath: url.path) else {
            throw CLIError("JSON file not found: \(url.path)")
        }
        let data = try Data(contentsOf: url)
        let value = try JSONSerialization.jsonObject(with: data)
        guard let object = value as? [String: Any] else {
            throw CLIError("expected JSON object: \(url.path)")
        }
        return object
    }
}

enum JSONLines {
    static func write<T: Encodable>(_ values: [T], to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: url.path, contents: Data())
        let handle = try FileHandle(forWritingTo: url)
        defer { try? handle.close() }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        for value in values {
            let data = try encoder.encode(value)
            handle.write(data)
            handle.write(Data("\n".utf8))
        }
    }
}

struct Options {
    private var values: [String: String] = [:]
    private var flags: Set<String> = []

    init(_ args: [String]) throws {
        var index = 0
        while index < args.count {
            let arg = args[index]
            guard arg.hasPrefix("--") else {
                throw CLIError("unexpected argument: \(arg)")
            }
            let key = String(arg.dropFirst(2))
            if index + 1 < args.count, !args[index + 1].hasPrefix("--") {
                values[key] = args[index + 1]
                index += 2
            } else {
                flags.insert(key)
                index += 1
            }
        }
    }

    func string(_ key: String) -> String? {
        values[key]
    }

    func flag(_ key: String) -> Bool {
        flags.contains(key)
    }

    func int(_ key: String) -> Int? {
        values[key].flatMap(Int.init)
    }

    func double(_ key: String) -> Double? {
        values[key].flatMap(Double.init)
    }

    func optionalPositiveDouble(_ key: String) throws -> Double? {
        guard let value = double(key) else { return nil }
        guard value > 0 else { throw CLIError("--\(key) must be greater than 0") }
        return value
    }

    func optionalNonNegativeDouble(_ key: String) throws -> Double? {
        guard let value = double(key) else { return nil }
        guard value >= 0 else { throw CLIError("--\(key) must be greater than or equal to 0") }
        return value
    }

    func url(_ key: String) -> URL? {
        values[key].map(PathURLs.fileURL)
    }

    func requiredURL(_ key: String) throws -> URL {
        guard let value = values[key] else { throw CLIError("--\(key) is required") }
        return PathURLs.fileURL(value)
    }
}

enum PathURLs {
    static func fileURL(_ path: String) -> URL {
        if path == "~" {
            return FileManager.default.homeDirectoryForCurrentUser
        }
        if path.hasPrefix("~/") {
            return FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent(String(path.dropFirst(2)))
        }
        if path.hasPrefix("/") {
            return URL(fileURLWithPath: path)
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent(path)
    }
}

enum RuntimeHome {
    static func apply() throws {
        guard let value = ProcessInfo.processInfo.environment["MURMURMARK_HOME"],
              !value.isEmpty
        else {
            return
        }

        let url = PathURLs.fileURL(value).standardizedFileURL
        var isDirectory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory), isDirectory.boolValue else {
            throw CLIError("MURMURMARK_HOME is not a directory: \(url.path)")
        }
        guard FileManager.default.changeCurrentDirectoryPath(url.path) else {
            throw CLIError("failed to change directory to MURMURMARK_HOME: \(url.path)")
        }
    }
}

enum PathDisplay {
    static func display(_ url: URL) -> String {
        let cwd = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
        let path = url.standardizedFileURL.path
        let root = cwd.standardizedFileURL.path
        if path == root {
            return "."
        }
        if path.hasPrefix(root + "/") {
            return String(path.dropFirst(root.count + 1))
        }
        return path
    }
}

extension String {
    func trimmedSingleLine() -> String {
        trimmingCharacters(in: .whitespacesAndNewlines)
            .split(whereSeparator: \.isNewline)
            .joined(separator: " ")
    }
}

enum ExecutablePath {
    static func current() -> String {
        let raw = CommandLine.arguments.first ?? ".build/debug/murmurmark"
        if raw.hasPrefix("/") {
            return raw
        }
        if raw.contains("/") {
            return PathURLs.fileURL(raw).path
        }
        if let resolved = Tooling.which(raw) {
            return resolved
        }
        return PathURLs.fileURL(raw).path
    }
}

enum ArgumentEditing {
    static func hasOption(_ key: String, in args: [String]) -> Bool {
        args.contains("--\(key)")
    }

    static func takeOption(_ key: String, from args: inout [String]) -> String? {
        let flag = "--\(key)"
        guard let index = args.firstIndex(of: flag) else { return nil }
        args.remove(at: index)
        guard index < args.count else { return nil }
        let value = args.remove(at: index)
        if value.hasPrefix("--") {
            args.insert(value, at: index)
            return nil
        }
        return value
    }

    static func takeFlag(_ key: String, from args: inout [String]) -> Bool {
        let flag = "--\(key)"
        guard let index = args.firstIndex(of: flag) else { return false }
        args.remove(at: index)
        return true
    }

    static func peekOption(_ key: String, in args: [String]) -> String? {
        let flag = "--\(key)"
        guard let index = args.firstIndex(of: flag), index + 1 < args.count else { return nil }
        let value = args[index + 1]
        return value.hasPrefix("--") ? nil : value
    }

    static func hasHelpFlag(_ args: [String]) -> Bool {
        args.contains("--help") || args.contains("-h")
    }
}

enum SessionResolver {
    static func resolve(_ value: String, sessionsRoot: URL) throws -> URL {
        if value == "latest" {
            return try latest(in: sessionsRoot)
        }
        let fileManager = FileManager.default
        let direct = PathURLs.fileURL(value)
        if fileManager.fileExists(atPath: direct.appendingPathComponent("session.json").path) {
            return direct
        }
        if !value.hasPrefix("/") {
            let rooted = sessionsRoot.appendingPathComponent(value)
            if fileManager.fileExists(atPath: rooted.appendingPathComponent("session.json").path) {
                return rooted
            }
        }
        throw CLIError("session.json not found for \(value) under \(direct.path) or \(sessionsRoot.path)")
    }

    static func resolveLiveWatch(_ value: String, sessionsRoot: URL) throws -> URL {
        if value == "latest" {
            return try latest(in: sessionsRoot)
        }

        let fileManager = FileManager.default
        let direct = PathURLs.fileURL(value)
        var candidates = [direct]
        if !value.hasPrefix("/") {
            candidates.append(sessionsRoot.appendingPathComponent(value))
        }

        for candidate in candidates {
            var isDirectory: ObjCBool = false
            guard fileManager.fileExists(atPath: candidate.path, isDirectory: &isDirectory), isDirectory.boolValue else {
                continue
            }

            let sessionMarkers = [
                "session.json",
                "session.lock",
                "audio",
                "derived/live",
                "derived/experiments/live-shadow-v1",
            ]
            if sessionMarkers.contains(where: {
                fileManager.fileExists(atPath: candidate.appendingPathComponent($0).path)
            }) {
                return candidate
            }
        }

        throw CLIError(
            "live session directory not found for \(value) under \(direct.path) or \(sessionsRoot.path); "
                + "start watching after recording creates the session directory"
        )
    }

    static func latest(in root: URL) throws -> URL {
        let sessions = try all(in: root)
        guard let session = sessions.first else {
            throw CLIError("no sessions with session.json found under \(root.path)")
        }
        return session
    }

    static func all(in root: URL) throws -> [URL] {
        let fileManager = FileManager.default
        guard fileManager.fileExists(atPath: root.path) else {
            throw CLIError("sessions root not found: \(root.path)")
        }
        let urls = try fileManager.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey, .contentModificationDateKey],
            options: [.skipsHiddenFiles]
        )
        var sessions: [(url: URL, modified: Date)] = []
        for url in urls {
            let values = try url.resourceValues(forKeys: [.isDirectoryKey, .contentModificationDateKey])
            guard values.isDirectory == true else { continue }
            guard !url.lastPathComponent.hasPrefix("_") else { continue }
            guard fileManager.fileExists(atPath: url.appendingPathComponent("session.json").path) else { continue }
            sessions.append((url, values.contentModificationDate ?? Date.distantPast))
        }
        return sessions.sorted { left, right in
            if left.modified == right.modified {
                return left.url.lastPathComponent > right.url.lastPathComponent
            }
            return left.modified > right.modified
        }.map(\.url)
    }
}

enum ReadinessPrinter {
    static func printNext(_ session: URL, exportManifest explicitExportManifest: URL? = nil) throws {
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        let sessionPath = PathDisplay.display(session)
        if let runState = pipelineRunStatePayload(session) {
            printPipelineRunStateNext(session: session, state: runState)
            return
        }
        if let blocked = pipelineBlockedPayload(session) {
            let blocker = string(blocked["blocker"]) ?? "pipeline_blocked"
            let nextCommands = blocked["next_commands"] as? [[String: Any]] ?? []
            let command = string(blocked["recommended_next"])
                ?? nextCommands.compactMap { string($0["command"]) }.first
                ?? "murmurmark inspect \(sessionPath)"
            print("")
            print("next:")
            print("  status: blocked")
            print("  command: \(command)")
            print("  source: pipeline_run")
            print("  gate: \(blocker)")
            print("  verdict: capture_failed")
            if nextCommands.count > 1 {
                print("  alternatives:")
                for item in nextCommands.dropFirst().prefix(4) {
                    guard let alternative = string(item["command"]), !alternative.isEmpty else { continue }
                    print("    \(alternative)")
                }
            }
            return
        }
        guard FileManager.default.fileExists(atPath: url.path) else {
            if let partial = CaptureHealthState.partialInfo(session: session) {
                print("")
                print("next:")
                print("  status: partial_capture")
                print("  command: \(CaptureHealthState.preferredPartialNext(session: session))")
                print("  reason: \(partial.reason)")
                print("  read: murmurmark status \(sessionPath)")
                return
            }
            print("")
            print("next:")
            print("  status: missing_readiness")
            print("  command: murmurmark process \(sessionPath)")
            print("  reason: session_readiness.json is missing")
            print("  read: murmurmark status \(sessionPath)")
            return
        }

        let payload = try JSONFiles.object(url)
        let gate = string(payload["use_gate"]) ?? "unknown"
        let profile = string(payload["selected_profile"]) ?? "unknown"
        let verdict = string(payload["verdict"]) ?? "unknown"
        let nextCommands = payload["next_commands"] as? [[String: Any]]
            ?? fallbackNextCommands(gate: gate, session: session, payload: payload)
        let openCommands = payload["open_commands"] as? [[String: Any]] ?? []
        let status = readinessStatus(gate: gate, payload: payload)
        let readinessCommand = string(payload["recommended_next"]) ?? preferredNextCommand(nextCommands) ?? "murmurmark status \(sessionPath)"
        let exportHandoff = status == "exportable"
            ? successfulExportHandoff(session: session, explicitManifest: explicitExportManifest)
            : nil
        let outcome = compatibleOutcomePayload(session, readinessProfile: profile)
        let outcomeSummary = outcome?["summary"] as? [String: Any]
        let outcomeCommand = outcome.flatMap { string($0["next_command"]) }
        let handoffCommand = AuthoritativeHandoffState.payload(session).flatMap {
            string($0["recommended_next"])
        }
        let command = exportHandoff?.command ?? handoffCommand ?? outcomeCommand ?? readinessCommand
        let source: String
        if exportHandoff != nil {
            source = "export_manifest"
        } else if handoffCommand != nil {
            source = "authoritative_handoff"
        } else if outcomeCommand != nil {
            source = "outcome"
        } else {
            source = "readiness"
        }
        let displayStatus = exportHandoff == nil
            ? effectiveStatus(readinessStatus: status, outcome: outcome)
            : "exported"
        let canReadOutputs = outcomeSummary.flatMap { bool($0["can_read_notes"]) }
            ?? canReadOutputsForStatus(displayStatus)

        print("")
        print("next:")
        print("  status: \(displayStatus)")
        print("  command: \(command)")
        print("  source: \(source)")
        print("  gate: \(gate)")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        if let classification = string(payload["session_classification"]), classification != "conversation" {
            print("  session_classification: \(classification)")
        }
        if exportHandoff != nil {
            print("  export_status: exported")
        } else if let exportStatus = outcome.flatMap({ string($0["export_status"]) }) {
            print("  export_status: \(exportStatus)")
        }
        if let manifest = exportHandoff?.manifest {
            print("  export_manifest: \(PathDisplay.display(manifest))")
        }
        if outcome != nil {
            print("  outcome: \(PathDisplay.display(session.appendingPathComponent("derived/outcome/outcome.json")))")
        }
        if canReadOutputs, let firstOpen = openCommands.compactMap({ string($0["command"]) }).first {
            print("  open_first: \(firstOpen)")
        }
        if nextCommands.count > 1 {
            print("  alternatives:")
            for item in nextCommands.dropFirst().prefix(4) {
                guard let alternative = string(item["command"]), !alternative.isEmpty else { continue }
                print("    \(alternative)")
            }
        }
    }

    private static func outcomePayload(_ session: URL) throws -> [String: Any]? {
        let url = session.appendingPathComponent("derived/outcome/outcome.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            return nil
        }
        return try JSONFiles.object(url)
    }

    private static func compatibleOutcomePayload(_ session: URL, readinessProfile: String) -> [String: Any]? {
        guard let outcome = try? outcomePayload(session) else { return nil }
        let fileManager = FileManager.default
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        let outcomeURL = session.appendingPathComponent("derived/outcome/outcome.json")
        let readinessDate = (try? fileManager.attributesOfItem(atPath: readinessURL.path)[.modificationDate]) as? Date
        let outcomeDate = (try? fileManager.attributesOfItem(atPath: outcomeURL.path)[.modificationDate]) as? Date
        if let readinessDate, let outcomeDate, outcomeDate < readinessDate {
            return nil
        }
        let summary = outcome["summary"] as? [String: Any] ?? [:]
        let outcomeProfile = string(summary["selected_profile"])
            ?? string(outcome["selected_profile"])
            ?? ""
        if !readinessProfile.isEmpty,
           readinessProfile != "unknown",
           !outcomeProfile.isEmpty,
           outcomeProfile != readinessProfile {
            return nil
        }
        return outcome
    }

    private static func successfulExportHandoff(session: URL, explicitManifest: URL?) -> (command: String, manifest: URL)? {
        guard let outcome = try? outcomePayload(session),
              string(outcome["outcome"]) == "ready_for_notes",
              string(outcome["export_status"]) == "allowed"
        else {
            return nil
        }
        let manifestURL = explicitManifest ?? PathURLs.fileURL("exports/private")
            .appendingPathComponent(session.lastPathComponent)
            .appendingPathComponent("export_manifest.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path),
              let payload = try? JSONFiles.object(manifestURL)
        else {
            return nil
        }
        guard string(payload["schema"]) == "murmurmark.export_manifest/v1" else { return nil }
        let status = string(payload["status"]) ?? ""
        guard status == "exported" || status == "exported_with_warnings" else { return nil }
        let blockers = payload["blockers"] as? [Any] ?? []
        guard blockers.isEmpty else { return nil }
        if let nextCommands = payload["next_commands"] as? [[String: Any]],
           let command = preferredNextCommand(nextCommands) {
            return (command, manifestURL)
        }
        if let next = string(payload["next"]), !next.isEmpty {
            return (next, manifestURL)
        }
        return nil
    }

    static func printSession(_ session: URL, label: String = "readiness") throws {
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        if let runState = pipelineRunStatePayload(session) {
            printPipelineRunStateReadiness(label: label, session: session, state: runState)
            return
        }
        if let blocked = pipelineBlockedPayload(session) {
            printPipelineBlockedReadiness(label: label, session: session, pipeline: blocked)
            return
        }
        guard FileManager.default.fileExists(atPath: url.path) else {
            if let partial = CaptureHealthState.partialInfo(session: session) {
                printPartialCaptureReadiness(label: label, session: session, partial: partial)
                return
            }
            print("\(label): missing")
            let sessionPath = PathDisplay.display(session)
            print("  session: \(sessionPath)")
            print("  expected: \(PathDisplay.display(url))")
            printCaptureSummary(session)
            printLivePipelineSummary(session)
            printExperimentSidecarSummary(session)
            print("  recommended_next: murmurmark process \(sessionPath)")
            print("  next:")
            print("    murmurmark process \(sessionPath)")
            print("    murmurmark report \(sessionPath)")
            return
        }
        let payload = try JSONFiles.object(url)
        let metrics = payload["metrics"] as? [String: Any] ?? [:]
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        let gate = string(payload["use_gate"]) ?? "unknown"
        let recommendation = string(payload["recommendation"]) ?? "unknown"
        let profile = string(payload["selected_profile"]) ?? "unknown"
        let verdict = string(payload["verdict"]) ?? "unknown"
        let nextCommands = payload["next_commands"] as? [[String: Any]]
            ?? fallbackNextCommands(gate: gate, session: session, payload: payload)
        let openCommands = payload["open_commands"] as? [[String: Any]] ?? []
        let exportBlockers = strings(payload["export_blockers"])
        let reviewBlockers = strings(payload["review_blockers"])
        let exportHandoff = successfulExportHandoff(session: session, explicitManifest: nil)
        let outcome = compatibleOutcomePayload(session, readinessProfile: profile)
        let status = exportHandoff == nil
            ? effectiveStatus(readinessStatus: readinessStatus(gate: gate, payload: payload), outcome: outcome)
            : "exported"
        let outcomeCommand = outcome.flatMap { string($0["next_command"]) }
        let recommendedNext = exportHandoff?.command ?? outcomeCommand ?? string(payload["recommended_next"]) ?? preferredNextCommand(nextCommands)
        let reviewSeconds = double(metrics["review_burden_sec"]) ?? 0
        let reviewRatio = (double(metrics["review_burden_ratio"]) ?? 0) * 100
        let transcriptReviewSeconds = double(metrics["transcript_review_burden_sec"]) ?? reviewSeconds
        let transcriptReviewRatio = (double(metrics["transcript_review_burden_ratio"]) ?? (reviewRatio / 100.0)) * 100
        var displayedNextCommands = nextCommands
        if let recommendedNext, !recommendedNext.isEmpty {
            let first = nextCommands.first.flatMap { string($0["command"]) }
            if first != recommendedNext {
                displayedNextCommands = [[
                    "id": "recommended_next",
                    "label": "Run the recommended next action.",
                    "command": recommendedNext,
                ]] + nextCommands.filter { string($0["command"]) != recommendedNext }
            }
        }

        print("")
        print("\(label):")
        print("  session: \(PathDisplay.display(session))")
        print("  status: \(status)")
        if let manifest = exportHandoff?.manifest {
            print("  export_manifest: \(PathDisplay.display(manifest))")
        }
        if let recommendedNext {
            print("  recommended_next: \(recommendedNext)")
        }
        printHandoff(status: status, session: session, outputs: outputs, exportHandoff: exportHandoff)
        printUseSummary(UseSummary(
            status: status,
            gate: gate,
            outputs: outputs,
            exportBlockers: exportBlockers,
            reviewBlockers: reviewBlockers,
            reviewSeconds: reviewSeconds,
            recommendedNext: recommendedNext,
            outcomeSummary: outcome?["summary"] as? [String: Any]
        ))
        print("  gate: \(gate)")
        print("  recommendation: \(recommendation)")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        if let classification = string(payload["session_classification"]), classification != "conversation" {
            print("  session_classification: \(classification)")
        }
        printAuthoritativeHandoffSummary(session)
        print(String(format: "  notes_review_burden: %.2f min / %.2f%%", reviewSeconds / 60, reviewRatio))
        if abs(transcriptReviewSeconds - reviewSeconds) > 0.05 {
            print(String(format: "  transcript_review_burden: %.2f min / %.2f%%", transcriptReviewSeconds / 60, transcriptReviewRatio))
        }
        ReviewSummaryPrinter.printSynthesisReviewMetrics(metrics, indent: "  ")
        printCaptureSummary(session)
        printLivePipelineSummary(session)
        printExperimentSidecarSummary(session)
        printStrongerAudioJudgeSummary(session)
        printSuggestedClosureSummary(session)
        printOutcomeSummary(session, readinessProfile: profile)
        let canReadOutputs = (outcome?["summary"] as? [String: Any]).flatMap { bool($0["can_read_notes"]) }
            ?? canReadOutputsForStatus(status)
        print("  open:")
        if !canReadOutputs {
            if outcome != nil {
                let outcomePath = PathDisplay.display(
                    session.appendingPathComponent("derived/outcome/outcome.json")
                )
                print("    less \(outcomePath) — Inspect the outcome blocker.")
            } else {
                let readinessPath = PathDisplay.display(
                    session.appendingPathComponent("derived/readiness/session_readiness.json")
                )
                print("    less \(readinessPath) — Inspect the readiness blocker.")
            }
        } else if openCommands.isEmpty {
            for key in ["transcript", "notes", "quality_verdict", "audio_review_report", "local_recall_review", "transcript_order_review"] {
                if let path = outputPath(key, outputs: outputs) {
                    let target = path.hasPrefix("/") ? URL(fileURLWithPath: path) : session.appendingPathComponent(path)
                    print("    \(key): \(PathDisplay.display(target))")
                }
            }
        } else {
            for item in openCommands {
                guard let command = string(item["command"]), !command.isEmpty else { continue }
                let label = string(item["label"]) ?? string(item["id"]) ?? "open"
                print("    \(command) — \(label)")
            }
        }
        print("  next:")
        if displayedNextCommands.isEmpty {
            print("    none")
        } else {
            for item in displayedNextCommands {
                guard let command = string(item["command"]), !command.isEmpty else { continue }
                let label = string(item["label"]) ?? string(item["id"]) ?? "next"
                print("    \(command) — \(label)")
            }
        }
    }

    private static func pipelineBlockedPayload(_ session: URL) -> [String: Any]? {
        let url = session.appendingPathComponent("derived/pipeline-run/pipeline_run_report.json")
        guard FileManager.default.fileExists(atPath: url.path),
              let payload = try? JSONFiles.object(url),
              string(payload["status"]) == "blocked"
        else {
            return nil
        }
        let blocker = string(payload["blocker"]) ?? ""
        guard blocker == "silent_capture" || blocker == "interrupted_capture" || blocker == "sparse_capture" else {
            return nil
        }
        return payload
    }

    private static func pipelineRunStatePayload(_ session: URL) -> [String: Any]? {
        let stateURL = session.appendingPathComponent("derived/pipeline-run/pipeline_run_state.json")
        guard FileManager.default.fileExists(atPath: stateURL.path),
              let payload = try? JSONFiles.object(stateURL)
        else {
            return nil
        }
        let status = string(payload["status"]) ?? ""
        guard status == "running" || status == "interrupted" else {
            return nil
        }
        if string(payload["phase"]) == "deferred_enrichment",
           AuthoritativeHandoffState.payload(session) != nil {
            return nil
        }
        let reportURL = session.appendingPathComponent("derived/pipeline-run/pipeline_run_report.json")
        if let stateDate = modificationDate(stateURL),
           let reportDate = modificationDate(reportURL),
           reportDate >= stateDate {
            return nil
        }
        return payload
    }

    private static func printAuthoritativeHandoffSummary(_ session: URL) {
        guard let payload = AuthoritativeHandoffState.payload(session) else {
            return
        }
        let deferred = payload["deferred_enrichment"] as? [String: Any] ?? [:]
        let fingerprint = payload["transcript_fingerprint"] as? [String: Any] ?? [:]
        print("  authoritative_handoff:")
        print("    status: \(string(payload["status"]) ?? "unknown")")
        if let readyAt = string(payload["ready_at"]) {
            print("    ready_at: \(readyAt)")
        }
        if let elapsed = double(payload["elapsed_sec"]) {
            print(String(format: "    elapsed: %.1fs", elapsed))
        }
        print("    deferred: \(string(deferred["status"]) ?? "pending")")
        if let sha = string(fingerprint["sha256"]) {
            print("    transcript_sha256: \(String(sha.prefix(16)))")
        }
        print("    report: \(PathDisplay.display(session.appendingPathComponent("derived/pipeline-run/authoritative_handoff.json")))")
    }

    private static func modificationDate(_ url: URL) -> Date? {
        guard FileManager.default.fileExists(atPath: url.path),
              let values = try? url.resourceValues(forKeys: [.contentModificationDateKey])
        else {
            return nil
        }
        return values.contentModificationDate
    }

    private static func printPipelineRunStateNext(session: URL, state: [String: Any]) {
        let sessionPath = PathDisplay.display(session)
        let status = string(state["status"]) ?? "running"
        let command = string(state["resume_command"]) ?? "murmurmark process \(sessionPath)"
        print("")
        print("next:")
        print("  status: process_\(status)")
        print("  command: \(command)")
        print("  source: pipeline_run_state")
        if let step = string(state["active_step"]) {
            print("  active_step: \(step)")
        }
        if let elapsed = double(state["active_step_elapsed_sec"]) {
            print(String(format: "  active_step_elapsed: %.1fs", elapsed))
        }
        print("  safe_interrupt: \(bool(state["safe_interrupt"]) ?? true)")
        if let hint = string(state["safe_interrupt_hint"]) {
            print("  hint: \(hint)")
        }
        if let progress = state["progress"] as? [String: Any] {
            printASRChunkProgress(progress, indent: "  ")
        }
        print("  read: murmurmark status \(sessionPath)")
    }

    private static func printPipelineRunStateReadiness(label: String, session: URL, state: [String: Any]) {
        let sessionPath = PathDisplay.display(session)
        let status = string(state["status"]) ?? "running"
        let command = string(state["resume_command"]) ?? "murmurmark process \(sessionPath)"
        print("")
        print("\(label):")
        print("  session: \(sessionPath)")
        print("  status: process_\(status)")
        print("  recommended_next: \(command)")
        if let step = string(state["active_step"]) {
            print("  active_step: \(step)")
        }
        if let elapsed = double(state["active_step_elapsed_sec"]) {
            print(String(format: "  active_step_elapsed: %.1fs", elapsed))
        }
        if let message = string(state["message"]) {
            print("  message: \(message)")
        }
        if let progress = state["progress"] as? [String: Any] {
            printASRChunkProgress(progress, indent: "  ")
        }
        print("  use:")
        print("    summary: processing is not finished yet")
        print("    can_read_notes: false")
        print("    can_export: false")
        print("    minimum_step: \(command)")
        print("  next:")
        print("    \(command) — resume or continue processing")
        print("    murmurmark next \(sessionPath) — print the current handoff")
    }

    private static func printASRChunkProgress(_ progress: [String: Any], indent: String) {
        guard let chunks = progress["asr_chunks"] as? [String: Any] else {
            return
        }
        let completed = int(chunks["chunks_completed"]) ?? 0
        let total = int(chunks["chunks_total"]) ?? 0
        guard total > 0 else {
            return
        }
        let completedSec = double(chunks["completed_sec"]) ?? 0.0
        let totalSec = double(chunks["total_sec"]) ?? 0.0
        let remainingSec = double(chunks["remaining_sec"]) ?? max(0.0, totalSec - completedSec)
        let reused = int(chunks["chunks_reused"]) ?? 0
        let transcribed = int(chunks["chunks_transcribed"]) ?? 0
        print("\(indent)asr_chunks:")
        print("\(indent)  chunks: \(completed)/\(total)")
        print(String(format: "\(indent)  audio: %.1fs/%.1fs", completedSec, totalSec))
        print(String(format: "\(indent)  remaining: %.1fs", remainingSec))
        print("\(indent)  reused: \(reused)")
        print("\(indent)  transcribed: \(transcribed)")
    }

    private static func printPipelineBlockedReadiness(label: String, session: URL, pipeline: [String: Any]) {
        let sessionPath = PathDisplay.display(session)
        let blocker = string(pipeline["blocker"]) ?? "pipeline_blocked"
        let nextCommands = pipeline["next_commands"] as? [[String: Any]] ?? []
        let recommendedNext = string(pipeline["recommended_next"])
            ?? nextCommands.compactMap { string($0["command"]) }.first
            ?? "murmurmark inspect \(sessionPath)"
        print("")
        print("\(label):")
        print("  session: \(sessionPath)")
        print("  status: blocked")
        print("  recommended_next: \(recommendedNext)")
        print("  use:")
        print("    summary: capture failed; do not use transcript")
        print("    can_read_notes: false")
        print("    can_export: false")
        print("    blocker: \(blocker)")
        print("    minimum_step: \(recommendedNext)")
        print("  gate: \(blocker)")
        print("  recommendation: re_record")
        if let warnings = pipeline["warnings"] as? [Any], !warnings.isEmpty {
            print("  warnings:")
            for warning in warnings.prefix(5) {
                print("    - \(String(describing: warning))")
            }
        }
        printCaptureSummary(session)
        printLivePipelineSummary(session)
        printExperimentSidecarSummary(session)
        print("  open:")
        print("    less \(sessionPath)/derived/pipeline-run/pipeline_run_report.json — Inspect the blocked pipeline report.")
        print("  next:")
        if nextCommands.isEmpty {
            print("    \(recommendedNext) — Run the recommended next action.")
        } else {
            for item in nextCommands {
                guard let command = string(item["command"]), !command.isEmpty else { continue }
                let label = string(item["reason"]) ?? string(item["id"]) ?? "next"
                print("    \(command) — \(label)")
            }
        }
    }

    private static func printCaptureSummary(_ session: URL) {
        let manifestURL = session.appendingPathComponent("session.json")
        guard let manifest = try? JSONFiles.object(manifestURL) else {
            return
        }
        let health = manifest["health"] as? [String: Any] ?? [:]
        let tracks = health["tracks"] as? [String: Any] ?? [:]
        let mic = tracks["mic"] as? [String: Any] ?? [:]
        let remote = tracks["remote"] as? [String: Any] ?? [:]
        print("  capture:")
        print("    status: \(string(health["summary"]) ?? string(manifest["status"]) ?? "unknown")")
        print("    partial: \(bool(health["partial"]) ?? false)")
        if let duration = double(health["actual_duration_sec"]) {
            print(String(format: "    duration: %.1fs", duration))
        }
        if let micDuration = double(mic["duration_sec"]) {
            print(String(format: "    mic: %.1fs", micDuration))
        }
        if let remoteDuration = double(remote["duration_sec"]) {
            print(String(format: "    remote: %.1fs", remoteDuration))
        }
    }

    private static func printLivePipelineSummary(_ session: URL) {
        let reportURL = session.appendingPathComponent("derived/live/live_pipeline_report.json")
        guard FileManager.default.fileExists(atPath: reportURL.path),
              let payload = try? JSONFiles.object(reportURL)
        else {
            return
        }
        let progress = payload["progress"] as? [String: Any] ?? [:]
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        let finalURL = session.appendingPathComponent("derived/live/final_reconcile_report.json")
        let liveStatus = string(payload["status"]) ?? "unknown"
        let terminationReason = liveStatus == "running"
            ? livePipelineTerminationReason(fromEventsIn: session)
            : string(payload["termination_reason"])
        let displayLiveStatus: String
        if let terminationReason, liveStatus == "running" {
            displayLiveStatus = "terminated_after_\(terminationReason)"
        } else if liveStatus == "running" && FileManager.default.fileExists(atPath: finalURL.path) {
            displayLiveStatus = "stale_running_after_finalize"
        } else {
            displayLiveStatus = liveStatus
        }
        print("  live_pipeline:")
        print("    mode: \(string(payload["mode"]) ?? "shadow")")
        print("    status: \(displayLiveStatus)")
        if let terminationReason {
            print("    termination_reason: \(terminationReason)")
        }
        print("    batch_authoritative: \(bool(payload["batch_authoritative"]) ?? true)")
        if let worker = string(payload["current_worker"]) {
            print("    worker: \(worker)")
        }
        if let stage = string(payload["current_stage"]) {
            print("    stage: \(terminationReason == nil ? stage : "terminated")")
        }
        print(String(format: "    captured: %.1fs", double(progress["captured_sec"]) ?? 0.0))
        if let preprocessed = double(progress["preprocessed_sec"]) {
            print(String(format: "    preprocessed: %.1fs", preprocessed))
        }
        if let asr = double(progress["asr_sec"]) {
            print(String(format: "    asr: %.1fs", asr))
        }
        print(String(format: "    processed: %.1fs", double(progress["processed_sec"]) ?? 0.0))
        print(String(format: "    lag: %.1fs", double(progress["live_lag_sec"]) ?? 0.0))
        print("    chunks: \(int(progress["chunks_processed"]) ?? 0)")
        if let targetMe = payload["causal_target_me_shadow"] as? [String: Any] {
            print("    target_me_candidates: \(int(targetMe["candidate_count"]) ?? 0)")
            print("    target_me_preview_kept: \(int(targetMe["preview_candidate_count"]) ?? 0)")
            print("    target_me_preview_rejected: \(int(targetMe["preview_rejected_count"]) ?? 0)")
            print("    target_me_lag_skips: \(int(targetMe["skipped_lag_budget_count"]) ?? 0)")
            print("    target_me_failures: \(int(targetMe["failed_open_count"]) ?? 0)")
        }
        if let runtime = payload["runtime_cost"] as? [String: Any],
           let base = runtime["base_chunk"] as? [String: Any] {
            if let median = double(base["median_sec"]) {
                print(String(format: "    base_chunk_median: %.1fs", median))
            }
            if let target = runtime["causal_target_me"] as? [String: Any],
               let median = double(target["median_sec"]) {
                print(String(format: "    target_me_median: %.1fs", median))
            }
        }
        if let preview = string(outputs["preview_transcript"]) {
            print("    preview: \(PathDisplay.display(session.appendingPathComponent(preview)))")
        }
        if let draft = string(outputs["draft_transcript"]) {
            print("    diagnostic_draft: \(PathDisplay.display(session.appendingPathComponent(draft)))")
        }
        print("    report: \(PathDisplay.display(reportURL))")
        if FileManager.default.fileExists(atPath: finalURL.path),
           let final = try? JSONFiles.object(finalURL) {
            print("    final_reconcile:")
            print("      status: \(string(final["status"]) ?? "unknown")")
            print("      source_of_truth: \(string(final["source_of_truth"]) ?? "batch_pipeline")")
            print("      speedup_status: \(string(final["speedup_status"]) ?? "unknown")")
            if let reason = string(final["fallback_reason"]) {
                print("      fallback_reason: \(reason)")
            } else if let reasons = final["fallback_reason"] as? [String], !reasons.isEmpty {
                print("      fallback_reason: \(reasons.joined(separator: ", "))")
            }
            if let elapsed = double(final["elapsed_sec"]) {
                print(String(format: "      elapsed: %.1fs", elapsed))
            }
            print("      report: \(PathDisplay.display(finalURL))")
        }
        let comparisonURL = session.appendingPathComponent("derived/live/live_batch_comparison.json")
        if FileManager.default.fileExists(atPath: comparisonURL.path),
           let comparison = try? JSONFiles.object(comparisonURL) {
            let gates = comparison["parity_gates"] as? [String: Any] ?? [:]
            let metrics = comparison["metrics"] as? [String: Any] ?? [:]
            print("    batch_comparison:")
            print("      status: \(string(comparison["status"]) ?? "unknown")")
            print("      parity: \(string(gates["status"]) ?? "unknown")")
            print("      promotion_allowed: \(bool(comparison["promotion_allowed"]) ?? false)")
            print("      meaningful: \(bool(metrics["meaningful_live_comparison"]) ?? false)")
            print("      all_gates_passed: \(bool(metrics["all_parity_gates_passed"]) ?? false)")
            print("      live_order_mismatches: \(int(metrics["live_order_mismatch_count"]) ?? 0)")
            print(String(format: "      live_missing_me: %.1fs", double(metrics["live_missing_me_seconds"]) ?? 0.0))
            print(String(format: "      suspicious_batch_me: %.1fs", double(metrics["live_suspicious_batch_me_missing_seconds"]) ?? 0.0))
            print(String(format: "      live_remote_in_me: %.1fs", double(metrics["live_suspected_remote_leak_in_me_seconds"]) ?? 0.0))
            print("      boundary_duplicates: \(int(metrics["adjacent_duplicate_chunk_count"]) ?? 0)")
            print("      report: \(PathDisplay.display(comparisonURL))")
        }
    }

    private static func printExperimentSidecarSummary(_ session: URL) {
        let root = session.appendingPathComponent("derived/experiments")
        guard let entries = try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil),
              !entries.isEmpty
        else {
            return
        }
        for experimentURL in entries.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
            let stateURL = experimentURL.appendingPathComponent("state.json")
            let rawCommitStateURL = experimentURL.appendingPathComponent("raw_commit_state.json")
            let payload = (try? JSONFiles.object(stateURL)) ?? (try? JSONFiles.object(rawCommitStateURL))
            guard let payload else { continue }
            let answers = payload["answers"] as? [String: Any] ?? [:]
            let outputs = payload["outputs"] as? [String: Any] ?? [:]
            let counters = payload["counters"] as? [String: Any] ?? [:]
            print("  experiment \(experimentURL.lastPathComponent):")
            print("    status: \(string(payload["status"]) ?? "unknown")")
            print("    batch_authoritative: true")
            if let raw = double(answers["raw_seconds_recorded"]) {
                print(String(format: "    raw: %.1fs", raw))
            }
            if let sidecar = double(answers["sidecar_seconds_captured"]) {
                print(String(format: "    sidecar: %.1fs", sidecar))
            }
            if let indexes = counters["processed_indexes"] as? [Any] {
                print("    processed_indexes: \(indexes.count)")
            }
            if let commits = string(outputs["raw_segment_commits"]) {
                print("    commits: \(PathDisplay.display(session.appendingPathComponent(commits)))")
            }
            print("    state: \(PathDisplay.display(stateURL))")
        }
    }

    private static func livePipelineTerminationReason(fromEventsIn session: URL) -> String? {
        let eventsURL = session.appendingPathComponent("events.jsonl")
        guard let text = try? String(contentsOf: eventsURL, encoding: .utf8) else {
            return nil
        }
        var latestReason: String?
        for line in text.split(separator: "\n") {
            guard let data = String(line).data(using: .utf8),
                  let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  string(object["type"]) == "live_pipeline.worker_terminated"
            else {
                continue
            }
            let fields = object["fields"] as? [String: Any] ?? [:]
            latestReason = string(fields["reason"]) ?? string(object["reason"]) ?? "terminated"
        }
        return latestReason
    }

    private static func printStrongerAudioJudgeSummary(_ session: URL) {
        let summaryURL = session.appendingPathComponent("derived/audit/audio-review-pack/faster_whisper_judge_summary.json")
        guard FileManager.default.fileExists(atPath: summaryURL.path),
              let payload = try? JSONFiles.object(summaryURL)
        else {
            return
        }
        let items = int(payload["items"]) ?? 0
        let keepSeconds = double(payload["suggested_keep_me_seconds"]) ?? 0.0
        let dropSeconds = double(payload["suggested_drop_me_seconds"]) ?? 0.0
        print("  stronger_audio_judge:")
        print("    items: \(items)")
        print(String(format: "    suggested_keep_me: %.2f min", keepSeconds / 60.0))
        print(String(format: "    suggested_drop_me: %.2f min", dropSeconds / 60.0))
        if let skipped = string(payload["skipped_reason"]), !skipped.isEmpty {
            print("    skipped: \(skipped)")
        }
    }

    private static func printSuggestedClosureSummary(_ session: URL) {
        let reportURL = session.appendingPathComponent("derived/readiness/review-plan/review_workspace_apply_report.json")
        guard FileManager.default.fileExists(atPath: reportURL.path),
              let payload = try? JSONFiles.object(reportURL),
              string(payload["answers_source"]) == "suggested",
              let closure = payload["suggested_closure"] as? [String: Any]
        else {
            return
        }
        let closed = closure["closed_by_suggestions"] as? [String: Any] ?? [:]
        let remaining = closure["remaining_manual_queue"] as? [String: Any] ?? [:]
        let projection = closure["readiness_projection"] as? [String: Any] ?? [:]
        let closedRows = int(closed["rows"]) ?? 0
        var remainingRows = int(remaining["rows"]) ?? 0
        var remainingSeconds = double(remaining["seconds"]) ?? 0.0
        if let current = currentReviewRemaining(session) {
            remainingRows = current.rows
            remainingSeconds = current.seconds
        }
        print("  suggested_closure:")
        print("    status: \(string(closure["status"]) ?? "unknown")")
        let closureLabel = suggestedClosureAlreadyPartiallyApplied(session) ? "safe_rows_applied" : "auto_closable"
        print(String(format: "    \(closureLabel): %d rows / %.2f min", closedRows, (double(closed["seconds"]) ?? 0.0) / 60.0))
        print(String(format: "    manual_remaining: %d rows / %.2f min", remainingRows, remainingSeconds / 60.0))
        if let beforeState = string(projection["before_state"]),
           let afterState = string(projection["after_state"]) {
            print("    readiness_projection: \(beforeState) -> \(afterState)")
        }
        if suggestedClosureAlreadyPartiallyApplied(session) {
            print("    status_detail: applied_safe_rows; continue with manual review progress")
        } else if closedRows > 0 {
            print("    apply: murmurmark review suggested apply \(PathDisplay.display(session))")
        } else if remainingRows > 0 {
            print("    manual: murmurmark review workspace --session \(PathDisplay.display(session))")
        }
    }

    private static func suggestedClosureAlreadyPartiallyApplied(_ session: URL) -> Bool {
        guard let current = currentReviewRemaining(session) else {
            return false
        }
        return current.rows > 0
    }

    private static func currentReviewRemaining(_ session: URL) -> (rows: Int, seconds: Double)? {
        let progressURL = session.appendingPathComponent("derived/readiness/review-plan/review_decisions_progress.json")
        guard let payload = try? JSONFiles.object(progressURL),
              let summary = payload["summary"] as? [String: Any]
        else {
            return nil
        }
        let reviewed = int(summary["reviewed"]) ?? 0
        let remaining = int(summary["remaining"])
        guard reviewed > 0, let remaining else {
            return nil
        }
        let seconds = (double(summary["remaining_minutes"]) ?? 0.0) * 60.0
        return (remaining, seconds)
    }

    private struct UseSummary {
        let status: String
        let gate: String
        let outputs: [String: Any]
        let exportBlockers: [String]
        let reviewBlockers: [String]
        let reviewSeconds: Double
        let recommendedNext: String?
        let outcomeSummary: [String: Any]?
    }

    private static func printUseSummary(_ summaryInput: UseSummary) {
        let fallbackCanRead = canReadOutputsForStatus(summaryInput.status)
            && outputPath("notes", outputs: summaryInput.outputs) != nil
            && outputPath("quality_verdict", outputs: summaryInput.outputs) != nil
        let canRead = summaryInput.outcomeSummary.flatMap { bool($0["can_read_notes"]) } ?? fallbackCanRead
        let canExport = summaryInput.outcomeSummary.flatMap { bool($0["can_export"]) } ?? (summaryInput.status == "exportable")
        var summary: String
        switch summaryInput.status {
        case "exported":
            summary = "exported; plan retention/privacy next"
        case "exportable":
            summary = "ready to read and export"
        case "ready_for_notes":
            summary = "ready_for_notes: notes can be read; export depends on outcome gate"
        case "review_first":
            summary = "review_first: read with review; close review before export"
        case "notes_ready_export_blocked":
            summary = "notes ready; full transcript export still blocked"
        case "review_required":
            summary = "read with review; close review before export"
        case "non_actionable_review_blocker":
            summary = "residual review risk is documented, but there is no actionable review lane"
        case "incomplete", "partial":
            summary = "pipeline incomplete; process before use"
        case "blocked", "pipeline_failed":
            summary = "blocked; inspect review/export blockers"
        default:
            summary = "check readiness before use"
        }
        if summaryInput.status == "blocked",
           !canRead,
           let headline = summaryInput.outcomeSummary.flatMap({ string($0["headline"]) }),
           !headline.isEmpty {
            summary = headline
        }
        let outcomeExportBlockers = summaryInput.outcomeSummary.map { strings($0["export_blockers"]) } ?? []
        let blocker = firstBlocker(
            gate: summaryInput.gate,
            exportBlockers: outcomeExportBlockers.isEmpty ? summaryInput.exportBlockers : outcomeExportBlockers,
            reviewBlockers: summaryInput.reviewBlockers
        )
        print("  use:")
        print("    summary: \(summary)")
        print("    can_read_notes: \(canRead)")
        print("    can_export: \(canExport)")
        if summaryInput.reviewSeconds > 0 {
            print(String(format: "    notes_review_burden_min: %.2f", summaryInput.reviewSeconds / 60.0))
        }
        if let blocker {
            print("    blocker: \(blocker)")
        }
        if let recommendedNext = summaryInput.recommendedNext, !recommendedNext.isEmpty {
            print("    minimum_step: \(recommendedNext)")
        }
    }

    private static func firstBlocker(gate: String, exportBlockers: [String], reviewBlockers: [String]) -> String? {
        if gate.hasPrefix("pipeline_incomplete") {
            return "pipeline_incomplete"
        }
        if let blocker = reviewBlockers.first {
            return blocker
        }
        if let blocker = exportBlockers.first {
            return blocker
        }
        return gate == "ready_for_notes" ? nil : gate
    }

    static func printCorpus(report: URL) throws {
        let payload = try JSONFiles.object(report)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("corpus:")
        print("  report: \(report.path)")
        print("  sessions: \(int(summary["session_count"]) ?? 0)")
        print("  complete_pipeline_count: \(int(summary["complete_pipeline_count"]) ?? 0)")
        print("  total_duration_min: \(double(summary["total_duration_min"]) ?? 0)")
        if let profiles = summary["by_selected_profile"] as? [String: Any] {
            print("  by_selected_profile: \(compactJSON(profiles))")
        }
        if let verdicts = summary["by_verdict"] as? [String: Any] {
            print("  by_verdict: \(compactJSON(verdicts))")
        }
        if summary["sessions_with_suggested_closure"] != nil {
            let generatedRows = int(summary["suggested_closure_generated_rows"]) ?? 0
            let actionableRows = int(summary["suggested_closure_actionable_rows"]) ?? 0
            let needsReviewRows = int(summary["suggested_closure_needs_review_rows"]) ?? 0
            let todoRows = int(summary["suggested_closure_todo_rows"]) ?? 0
            let autoRows = int(summary["suggested_closure_auto_rows"]) ?? 0
            let autoSeconds = double(summary["suggested_closure_auto_seconds"]) ?? 0.0
            let keepRows = int(summary["suggested_closure_auto_keep_rows"]) ?? 0
            let dropRows = int(summary["suggested_closure_auto_drop_rows"]) ?? 0
            let reviewRows = int(summary["suggested_closure_auto_review_rows"]) ?? 0
            let manualRows = int(summary["suggested_closure_manual_remaining_rows"]) ?? 0
            let manualSeconds = double(summary["suggested_closure_manual_remaining_seconds"]) ?? 0.0
            print("  suggested_closure:")
            print("    sessions: \(int(summary["sessions_with_suggested_closure"]) ?? 0)")
            print(
                "    generated: \(generatedRows) rows "
                    + "(actionable=\(actionableRows), needs_review=\(needsReviewRows), todo=\(todoRows))"
            )
            print(String(format: "    safe_rows: %d rows / %.2fs", autoRows, autoSeconds))
            print("    safe_decisions: keep=\(keepRows), drop=\(dropRows), review=\(reviewRows)")
            print(String(format: "    manual_remaining: %d rows / %.2fs", manualRows, manualSeconds))
        }
    }

    static func preferredNextCommand(_ nextCommands: [[String: Any]]) -> String? {
        let commands = nextCommands.compactMap { string($0["command"]) }.filter { !$0.isEmpty }
        let actionPrefixes = [
            "murmurmark process",
            "murmurmark review",
            "murmurmark finish",
            "murmurmark export",
            "murmurmark retention",
            "murmurmark report",
        ]
        for prefix in actionPrefixes {
            if let command = commands.first(where: { $0.hasPrefix(prefix) }) {
                return command
            }
        }
        return commands.first
    }

    static func printFinalNext(_ session: URL) throws {
        let sessionPath = PathDisplay.display(session)
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        let command: String
        if let runState = pipelineRunStatePayload(session) {
            command = string(runState["resume_command"]) ?? "murmurmark process \(sessionPath)"
        } else if let blocked = pipelineBlockedPayload(session) {
            let nextCommands = blocked["next_commands"] as? [[String: Any]] ?? []
            command = string(blocked["recommended_next"])
                ?? nextCommands.compactMap { string($0["command"]) }.first
                ?? "murmurmark inspect \(sessionPath)"
        } else if let exportHandoff = successfulExportHandoff(session: session, explicitManifest: nil) {
            command = exportHandoff.command
        } else if FileManager.default.fileExists(atPath: url.path) {
            let payload = try JSONFiles.object(url)
            let gate = string(payload["use_gate"]) ?? "unknown"
            let profile = string(payload["selected_profile"]) ?? "unknown"
            let nextCommands = payload["next_commands"] as? [[String: Any]]
                ?? fallbackNextCommands(gate: gate, session: session, payload: payload)
            let exportHandoff = successfulExportHandoff(session: session, explicitManifest: nil)
            let outcomeCommand = compatibleOutcomePayload(session, readinessProfile: profile)
                .flatMap { string($0["next_command"]) }
            command = exportHandoff?.command
                ?? outcomeCommand
                ?? string(payload["recommended_next"])
                ?? preferredNextCommand(nextCommands)
                ?? "murmurmark status \(sessionPath)"
        } else if CaptureHealthState.partialInfo(session: session) != nil {
            command = CaptureHealthState.preferredPartialNext(session: session)
        } else {
            command = "murmurmark process \(sessionPath)"
        }
        print("")
        print("next: \(command)")
    }

    static func printOutcome(_ session: URL) throws {
        let url = session.appendingPathComponent("derived/outcome/outcome.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            print("")
            print("outcome: missing")
            print("  expected: \(PathDisplay.display(url))")
            print("  next: murmurmark outcome \(PathDisplay.display(session)) --refresh")
            return
        }
        let payload = try JSONFiles.object(url)
        print("")
        print("outcome:")
        print("  status: \(string(payload["outcome"]) ?? "unknown")")
        print("  selected_profile: \(string(payload["selected_profile"]) ?? "unknown")")
        print("  verdict: \(string(payload["verdict"]) ?? "unknown")")
        if let classification = string(payload["session_classification"]), classification != "conversation" {
            print("  session_classification: \(classification)")
        }
        print("  use_gate: \(string(payload["use_gate"]) ?? "unknown")")
        print("  export_status: \(string(payload["export_status"]) ?? "unknown")")
        if let next = string(payload["next_command"]), !next.isEmpty {
            print("  next: \(next)")
        }
        let hasSummary = payload["summary"] is [String: Any]
        if let summary = payload["summary"] as? [String: Any] {
            print("  summary:")
            if let headline = string(summary["headline"]), !headline.isEmpty {
                print("    \(headline)")
            }
            if let canRead = bool(summary["can_read_notes"]) {
                print("    can_read_notes: \(canRead)")
            }
            if let canExport = bool(summary["can_export"]) {
                print("    can_export: \(canExport)")
            }
            let exportBlockers = strings(summary["export_blockers"])
            if !exportBlockers.isEmpty {
                let blockerText = exportBlockers.joined(separator: ", ")
                print("    export_blockers: \(blockerText)")
            }
            if let review = double(summary["review_burden_minutes"]) {
                print(String(format: "    review_burden: %.2f min", review))
            }
            if let transcriptReview = double(summary["transcript_review_burden_minutes"]) {
                print(String(format: "    transcript_review_burden: %.2f min", transcriptReview))
            }
            if let lanes = int(summary["open_review_lanes"]) {
                print("    open_review_lanes: \(lanes)")
            }
            if let firstLane = string(summary["first_review_lane"]), !firstLane.isEmpty {
                print("    first_review_lane: \(firstLane)")
            }
            if let notesPath = string(summary["notes_path"]), !notesPath.isEmpty {
                print("    notes: \(PathDisplay.display(session.appendingPathComponent(notesPath)))")
            }
            if let transcriptPath = string(summary["transcript_path"]), !transcriptPath.isEmpty {
                print("    transcript: \(PathDisplay.display(session.appendingPathComponent(transcriptPath)))")
            }
            if let verdictPath = string(summary["quality_verdict_path"]), !verdictPath.isEmpty {
                print("    verdict: \(PathDisplay.display(session.appendingPathComponent(verdictPath)))")
            }
        }
        if let metrics = payload["metrics"] as? [String: Any] {
            if !hasSummary, let review = double(metrics["review_burden_sec"]) {
                print(String(format: "  review_burden: %.2f min", review / 60.0))
            }
            if let harmful = double(metrics["audit_harmful_seconds_after"]) {
                print(String(format: "  harmful_remote_in_me: %.2fs", harmful))
            }
            if let localRecall = double(metrics["local_only_island_recall"]) {
                print(String(format: "  local_recall: %.3f", localRecall))
            }
        }
        if let gates = payload["gates"] as? [[String: Any]] {
            let failed = gates.filter { (string($0["status"]) ?? "") == "fail" }.count
            let review = gates.filter { (string($0["status"]) ?? "") == "review" }.count
            let unknown = gates.filter { (string($0["status"]) ?? "") == "unknown" }.count
            print("  gates: fail=\(failed), review=\(review), unknown=\(unknown)")
        }
        print("  files:")
        print("    outcome: \(PathDisplay.display(url))")
        print("    review_plan: \(PathDisplay.display(session.appendingPathComponent("derived/outcome/review_plan.json")))")
        print("    next_command: \(PathDisplay.display(session.appendingPathComponent("derived/outcome/next_command.txt")))")
        print("    run_manifest: \(PathDisplay.display(session.appendingPathComponent("derived/run/pipeline_run.json")))")
    }

    private static func printOutcomeSummary(_ session: URL, readinessProfile: String) {
        let url = session.appendingPathComponent("derived/outcome/outcome.json")
        guard let payload = compatibleOutcomePayload(session, readinessProfile: readinessProfile) else { return }
        print("  outcome:")
        print("    status: \(string(payload["outcome"]) ?? "unknown")")
        print("    export_status: \(string(payload["export_status"]) ?? "unknown")")
        if let next = string(payload["next_command"]), !next.isEmpty {
            print("    next: \(next)")
        }
        print("    report: \(PathDisplay.display(url))")
    }

    private static func printPartialCaptureReadiness(label: String, session: URL, partial: PartialCaptureInfo) {
        let commands = CaptureHealthState.partialNextCommands(session: session)
        print("\(label):")
        print("  session: \(PathDisplay.display(session))")
        print("  status: partial_capture")
        print("  reason: \(partial.reason)")
        if let actual = partial.actualDurationSec {
            print(String(format: "  actual_duration: %.2fs", actual))
        }
        if let requested = partial.requestedDurationSec {
            print(String(format: "  requested_duration: %.2fs", requested))
        }
        if let restartCount = partial.restartCount {
            print("  screen_capture_restarts: \(restartCount)")
        }
        print("  recommended_next: \(CaptureHealthState.preferredPartialNext(session: session))")
        print("  use:")
        print("    summary: partial recording; inspect before processing")
        print("    can_read_notes: false")
        print("    can_export: false")
        print("    blocker: partial_capture")
        if !partial.warnings.isEmpty {
            print("  warnings:")
            for warning in partial.warnings.prefix(5) {
                print("    - \(warning)")
            }
        }
        print("  next:")
        for item in commands {
            let label = item["label"] ?? item["id"] ?? "next"
            let command = item["command"] ?? ""
            print("    \(command) — \(label)")
        }
    }

    private static func outputPath(_ key: String, outputs: [String: Any]) -> String? {
        guard let item = outputs[key] as? [String: Any] else { return nil }
        guard (item["exists"] as? Bool) == true else { return nil }
        return item["path"] as? String
    }

    private static func canReadOutputsForStatus(_ status: String) -> Bool {
        [
            "exported",
            "exportable",
            "ready_for_notes",
            "review_first",
            "notes_ready_export_blocked",
            "review_required",
            "non_actionable_review_blocker",
        ].contains(status)
    }

    private static func effectiveStatus(readinessStatus: String, outcome: [String: Any]?) -> String {
        guard let outcomeValue = outcome.flatMap({ string($0["outcome"]) }) else {
            return readinessStatus
        }
        switch outcomeValue {
        case "blocked", "pipeline_failed":
            return "blocked"
        case "partial":
            return "incomplete"
        default:
            return readinessStatus
        }
    }

    private static func printHandoff(
        status: String,
        session: URL,
        outputs: [String: Any],
        exportHandoff: (command: String, manifest: URL)? = nil
    ) {
        var commands: [(String, String)] = []
        let canOpenReadOutputs = canReadOutputsForStatus(status)
        if canOpenReadOutputs {
            appendOpenCommand("open_notes", outputKey: "notes", session: session, outputs: outputs, to: &commands)
            appendOpenCommand("open_transcript", outputKey: "transcript", session: session, outputs: outputs, to: &commands)
            appendOpenCommand("open_verdict", outputKey: "quality_verdict", session: session, outputs: outputs, to: &commands)
        }
        if let exportHandoff {
            commands.append(("retention", exportHandoff.command))
        } else if status == "exportable" {
            let sessionPath = PathDisplay.display(session)
            commands.append(("finish", "murmurmark finish \(sessionPath)"))
        }
        guard !commands.isEmpty else { return }
        print("  handoff:")
        for (name, command) in commands {
            print("    \(name): \(command)")
        }
    }

    private static func appendOpenCommand(
        _ name: String,
        outputKey: String,
        session: URL,
        outputs: [String: Any],
        to commands: inout [(String, String)]
    ) {
        guard let path = outputPath(outputKey, outputs: outputs) else { return }
        let target = path.hasPrefix("/") ? PathURLs.fileURL(path) : session.appendingPathComponent(path)
        commands.append((name, "less \(PathDisplay.display(target))"))
    }

    private static func fallbackNextCommands(gate: String, session: URL, payload: [String: Any]) -> [[String: Any]] {
        let exportBlockers = payload["export_blockers"] as? [Any] ?? []
        let reviewBlockers = payload["review_blockers"] as? [Any] ?? []
        let sessionPath = PathDisplay.display(session)
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains(where: { String(describing: $0) == "pipeline_incomplete" }) {
            return [
                [
                    "label": "Run or refresh the full post-recording pipeline.",
                    "command": "murmurmark process \(sessionPath)",
                ],
            ]
        }
        if gate == "ready_for_notes" && !exportBlockers.isEmpty && reviewBlockers.isEmpty {
            return [
                [
                    "label": "Read selected evidence-backed notes; full transcript export is still blocked.",
                    "command": "murmurmark notes \(sessionPath)",
                ],
                [
                    "label": "Inspect notes readiness and export blockers.",
                    "command": "murmurmark status \(sessionPath)",
                ],
                [
                    "label": "Read detailed readiness before forcing any export.",
                    "command": "less \(sessionPath)/derived/readiness/session_readiness.md",
                ],
            ]
        }
        if !reviewBlockers.isEmpty || !exportBlockers.isEmpty || gate == "review_first" {
            return [
                [
                    "label": "Refresh this session's review handoff and recommended first lane.",
                    "command": "murmurmark review next \(sessionPath)",
                ],
                [
                    "label": "Build the recommended first review lane pack.",
                    "command": "murmurmark review first-lane --session \(sessionPath)",
                ],
                [
                    "label": "Build lane packs and answer sheets for this session.",
                    "command": "murmurmark review workspace --session \(sessionPath)",
                ],
                [
                    "label": "Apply edited review workspace answers.",
                    "command": "murmurmark review workspace apply --session \(sessionPath)",
                ],
                [
                    "label": "Check whether enough review decisions are closed for batch apply.",
                    "command": "murmurmark review progress --session \(sessionPath)",
                ],
                [
                    "label": "Apply closed review decisions and refresh reports when progress is ready.",
                    "command": "murmurmark review apply --session \(sessionPath)",
                ],
            ]
        }
        if gate == "ready_for_notes" {
            return [
                [
                    "label": "Create the final local handoff bundle and retention manifests.",
                    "command": "murmurmark finish \(sessionPath)",
                ],
                [
                    "label": "Low-level export command for debugging the handoff bundle.",
                    "command": "murmurmark export \(sessionPath) --format markdown --include-json",
                ],
            ]
        }
        return [
            [
                "label": "Inspect readiness details before using this session.",
                "command": "less \(sessionPath)/derived/readiness/session_readiness.md",
            ],
        ]
    }

    private static func readinessStatus(gate: String, payload: [String: Any]) -> String {
        let exportBlockers = (payload["export_blockers"] as? [Any] ?? []).map { String(describing: $0) }
        let reviewBlockers = (payload["review_blockers"] as? [Any] ?? []).map { String(describing: $0) }
        let nonActionable = payload["non_actionable_blockers"] as? [Any] ?? []
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains("pipeline_incomplete") {
            return "incomplete"
        }
        if gate == "ready_for_notes" && exportBlockers.isEmpty {
            return "exportable"
        }
        if gate == "ready_for_notes" && !exportBlockers.isEmpty && reviewBlockers.isEmpty {
            return "notes_ready_export_blocked"
        }
        if !nonActionable.isEmpty {
            return "non_actionable_review_blocker"
        }
        if gate == "review_first" || !reviewBlockers.isEmpty {
            return "review_required"
        }
        if gate == "do_not_use_without_manual_review" || !exportBlockers.isEmpty {
            return "blocked"
        }
        return "check_required"
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func strings(_ value: Any?) -> [String] {
        (value as? [Any] ?? []).map { String(describing: $0) }
    }

    private static func double(_ value: Any?) -> Double? {
        if let number = value as? NSNumber { return number.doubleValue }
        if let text = value as? String { return Double(text) }
        return nil
    }

    private static func int(_ value: Any?) -> Int? {
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String { return Int(text) }
        return nil
    }

    private static func bool(_ value: Any?) -> Bool? {
        if let value = value as? Bool { return value }
        if let number = value as? NSNumber { return number.boolValue }
        if let text = value as? String {
            return ["true", "yes", "1"].contains(text.lowercased())
        }
        return nil
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }
}

enum ReviewNextPrinter {
    static func print(session: URL, planOutDir: URL) throws {
        let sessionPath = PathDisplay.display(session)
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: readinessURL.path) else {
            Swift.print("SESSION=\"\(sessionPath)\"")
            Swift.print("")
            Swift.print("review_next:")
            Swift.print("  session: \(sessionPath)")
            Swift.print("  readiness: missing")
            Swift.print("  recommended_next: murmurmark report \(sessionPath)")
            Swift.print("  next: murmurmark report \(sessionPath)")
            Swift.print("")
            Swift.print("next: murmurmark report \(sessionPath)")
            return
        }

        let payload = try JSONFiles.object(readinessURL)
        let metrics = payload["metrics"] as? [String: Any] ?? [:]
        let gate = string(payload["use_gate"]) ?? "unknown"
        let profile = string(payload["selected_profile"]) ?? "unknown"
        let verdict = string(payload["verdict"]) ?? "unknown"
        let recommendation = string(payload["recommendation"]) ?? "unknown"
        let reviewBlockers = payload["review_blockers"] as? [Any] ?? []
        let exportBlockers = payload["export_blockers"] as? [Any] ?? []
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let reviewSeconds = double(metrics["review_burden_sec"]) ?? 0.0
        let reviewRatio = (double(metrics["review_burden_ratio"]) ?? 0.0) * 100.0
        let transcriptReviewSeconds = double(metrics["transcript_review_burden_sec"]) ?? reviewSeconds
        let transcriptReviewRatio = (double(metrics["transcript_review_burden_ratio"]) ?? (reviewRatio / 100.0)) * 100.0
        let synthesisReviewCount = int(metrics["synthesis_review_item_count"]) ?? 0
        if !requiresReview(gate: gate, reviewBlockers: reviewBlockers, exportBlockers: exportBlockers) {
            printNoReviewHandoff(
                NoReviewHandoff(
                    sessionPath: sessionPath,
                    gate: gate,
                    recommendation: recommendation,
                    profile: profile,
                    verdict: verdict,
                    reviewSeconds: reviewSeconds,
                    reviewRatio: reviewRatio,
                    transcriptReviewSeconds: transcriptReviewSeconds,
                    transcriptReviewRatio: transcriptReviewRatio,
                    synthesisReviewCount: synthesisReviewCount,
                    exportBlockers: exportBlockers
                )
            )
            return
        }
        let focusedCommands = focusedNextCommands(
            nextCommands,
            gate: gate,
            sessionPath: sessionPath,
            planOutDir: planOutDir
        )

        Swift.print("SESSION=\"\(sessionPath)\"")
        Swift.print("")
        Swift.print("review_next:")
        Swift.print("  session: \(sessionPath)")
        Swift.print("  gate: \(gate)")
        Swift.print("  recommendation: \(recommendation)")
        Swift.print("  selected_profile: \(profile)")
        Swift.print("  verdict: \(verdict)")
        Swift.print(String(format: "  notes_review_burden: %.2f min / %.2f%%", reviewSeconds / 60.0, reviewRatio))
        if abs(transcriptReviewSeconds - reviewSeconds) > 0.05 {
            Swift.print(String(format: "  transcript_review_burden: %.2f min / %.2f%%", transcriptReviewSeconds / 60.0, transcriptReviewRatio))
        }
        if synthesisReviewCount > 0 {
            Swift.print("  synthesis_review_items: \(synthesisReviewCount)")
        }
        if !reviewBlockers.isEmpty {
            Swift.print("  review_blockers: \(compactJSON(reviewBlockers))")
        }
        if !exportBlockers.isEmpty {
            Swift.print("  export_blockers: \(compactJSON(exportBlockers))")
        }
        printPlanHint(planOutDir: planOutDir)
        printOpenHandoff(session: session, planOutDir: planOutDir)
        if let recommended = focusedCommands.first {
            Swift.print("  recommended_next: \(recommended)")
        }
        printReviewFlowsIfPlanExists(sessionArg: sessionPath, planOutDir: planOutDir)
        Swift.print("  next:")
        for command in focusedCommands {
            Swift.print("    \(command)")
        }
        if let first = focusedCommands.first {
            Swift.print("")
            Swift.print("next: \(first)")
        }
    }

    private struct NoReviewHandoff {
        let sessionPath: String
        let gate: String
        let recommendation: String
        let profile: String
        let verdict: String
        let reviewSeconds: Double
        let reviewRatio: Double
        let transcriptReviewSeconds: Double
        let transcriptReviewRatio: Double
        let synthesisReviewCount: Int
        let exportBlockers: [Any]
    }

    private static func printNoReviewHandoff(_ handoff: NoReviewHandoff) {
        let sessionPath = handoff.sessionPath
        Swift.print("SESSION=\"\(sessionPath)\"")
        Swift.print("")
        Swift.print("review_next:")
        Swift.print("  session: \(sessionPath)")
        Swift.print("  status: \(statusWithoutReview(gate: handoff.gate, exportBlockers: handoff.exportBlockers))")
        Swift.print("  gate: \(handoff.gate)")
        Swift.print("  reason: no_review_required")
        Swift.print("  recommendation: \(handoff.recommendation)")
        Swift.print("  selected_profile: \(handoff.profile)")
        Swift.print("  verdict: \(handoff.verdict)")
        Swift.print(String(format: "  notes_review_burden: %.2f min / %.2f%%", handoff.reviewSeconds / 60.0, handoff.reviewRatio))
        if abs(handoff.transcriptReviewSeconds - handoff.reviewSeconds) > 0.05 {
            Swift.print(
                String(
                    format: "  transcript_review_burden: %.2f min / %.2f%%",
                    handoff.transcriptReviewSeconds / 60.0,
                    handoff.transcriptReviewRatio
                )
            )
        }
        if handoff.synthesisReviewCount > 0 {
            Swift.print("  synthesis_review_items: \(handoff.synthesisReviewCount)")
        }
        if !handoff.exportBlockers.isEmpty {
            Swift.print("  export_blockers: \(compactJSON(handoff.exportBlockers))")
        }
        Swift.print("  recommended_next: murmurmark next \(sessionPath)")
        Swift.print("  next:")
        Swift.print("    murmurmark next \(sessionPath)")
        Swift.print("    murmurmark status \(sessionPath)")
        Swift.print("")
        Swift.print("next: murmurmark next \(sessionPath)")
    }

    private static func printOpenHandoff(session: URL, planOutDir: URL) {
        var commands: [String] = []
        appendOpen(session.appendingPathComponent("derived/readiness/session_readiness.md"), to: &commands)
        appendOpen(planOutDir.appendingPathComponent("review_plan.md"), to: &commands)
        appendOpen(planOutDir.appendingPathComponent("review_decisions_progress.md"), to: &commands)
        appendOpen(session.appendingPathComponent("derived/readiness/operational-readiness/operational_readiness_report.md"), to: &commands)
        guard !commands.isEmpty else { return }
        Swift.print("  open:")
        for command in commands {
            Swift.print("    \(command)")
        }
    }

    private static func appendOpen(_ url: URL, to commands: inout [String]) {
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        commands.append("less \(PathDisplay.display(url))")
    }

    private static func statusWithoutReview(gate: String, exportBlockers: [Any]) -> String {
        let blockers = exportBlockers.map { String(describing: $0) }
        if gate.hasPrefix("pipeline_incomplete") || blockers.contains("pipeline_incomplete") {
            return "incomplete"
        }
        if gate == "ready_for_notes" && blockers.isEmpty {
            return "exportable"
        }
        if gate == "ready_for_notes" && !blockers.isEmpty {
            return "notes_ready_export_blocked"
        }
        if gate == "do_not_use_without_manual_review" || !blockers.isEmpty {
            return "blocked"
        }
        return "check_required"
    }

    private static func requiresReview(gate: String, reviewBlockers: [Any], exportBlockers: [Any]) -> Bool {
        gate == "review_first"
            || gate == "do_not_use_without_manual_review"
            || gate.hasSuffix("_review_first")
            || !reviewBlockers.isEmpty
            || (gate == "ready_for_notes" && !exportBlockers.isEmpty)
    }

    private static func printReviewFlowsIfPlanExists(sessionArg: String, planOutDir: URL) {
        guard FileManager.default.fileExists(atPath: planOutDir.appendingPathComponent("review_plan.json").path) else {
            return
        }
        guard planHasReviewActions(planOutDir: planOutDir) else {
            Swift.print("  review_handoff: no_actionable_review_rows")
            return
        }
        let firstLane = firstRecommendedLane(planOutDir: planOutDir) ?? "first"
        if hasCompletedReviewProgress(planOutDir: planOutDir) {
            Swift.print("  review_progress:")
            Swift.print("    status: completed")
            Swift.print("    inspect: murmurmark review progress --session \(sessionArg)")
            Swift.print("  workspace_flow:")
            Swift.print("    build_and_listen: murmurmark review workspace --session \(sessionArg)")
            Swift.print("    apply_answers: murmurmark review workspace apply --session \(sessionArg)")
            return
        }
        Swift.print("  first_lane_flow:")
        Swift.print("    build_and_listen: murmurmark review first-lane --session \(sessionArg)")
        Swift.print("    apply_answers: murmurmark review lane apply \(firstLane) --session \(sessionArg)")
        if hasPartialReviewProgress(planOutDir: planOutDir) {
            Swift.print("  review_progress:")
            Swift.print("    inspect_remaining: murmurmark review progress --session \(sessionArg)")
            Swift.print("    refresh_profile: murmurmark review apply --session \(sessionArg)")
        } else {
            Swift.print("  suggested_flow:")
            Swift.print("    preview: murmurmark review suggested \(sessionArg)")
            Swift.print("    apply_safe_suggestions: murmurmark review suggested apply \(sessionArg)")
        }
        if let quickLane = quickRecommendedLane(planOutDir: planOutDir), quickLane != firstLane {
            Swift.print("  quick_lane_flow:")
            Swift.print("    build_and_listen: murmurmark review lane \(quickLane) --session \(sessionArg)")
            Swift.print("    apply_answers: murmurmark review lane apply \(quickLane) --session \(sessionArg)")
        }
        Swift.print("  workspace_flow:")
        Swift.print("    build_and_listen: murmurmark review workspace --session \(sessionArg)")
        Swift.print("    apply_answers: murmurmark review workspace apply --session \(sessionArg)")
    }

    private static func printPlanHint(planOutDir: URL) {
        let planURL = planOutDir.appendingPathComponent("review_plan.json")
        guard FileManager.default.fileExists(atPath: planURL.path),
              let payload = try? JSONFiles.object(planURL)
        else {
            return
        }
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let strategy = payload["review_queue_strategy"] as? [String: Any] ?? [:]
        Swift.print("  plan: \(PathDisplay.display(planURL))")
        if let actions = int(summary["review_action_count"]) {
            Swift.print("  review_actions: \(actions)")
        }
        if let groupedRows = int(summary["grouped_review_row_count"]), groupedRows > 0 {
            Swift.print("  grouped_review_rows: \(groupedRows)")
        }
        if let firstLane = string(strategy["first_recommended_lane"]) {
            Swift.print("  first_lane: \(firstLane)")
        }
        if let quickLane = string(strategy["quick_recommended_lane"]) {
            Swift.print("  quick_lane: \(quickLane)")
        }
        if let reason = string(strategy["first_recommended_reason"]) {
            Swift.print("  first_lane_reason: \(reason)")
        }
        if let estimate = strategy["after_first_lane_estimate"] as? [String: Any] {
            let remainingItems = int(estimate["remaining_items"]) ?? 0
            let remainingActions = int(estimate["remaining_actions"])
            let remainingMinutes = double(estimate["remaining_minutes"]) ?? 0.0
            if let remainingActions {
                Swift.print(
                    String(
                        format: "  after_first_lane: remaining_items=%d remaining_actions=%d remaining_minutes=%.2f",
                        remainingItems,
                        remainingActions,
                        remainingMinutes
                    )
                )
            } else {
                Swift.print(String(format: "  after_first_lane: remaining_items=%d remaining_minutes=%.2f", remainingItems, remainingMinutes))
            }
        }
        if let lanes = summary["by_review_lane"] as? [String: Any], !lanes.isEmpty {
            Swift.print("  by_lane: \(compactJSON(lanes))")
        }
    }

    private static func focusedNextCommands(
        _ rows: [[String: Any]],
        gate: String,
        sessionPath: String,
        planOutDir: URL
    ) -> [String] {
        if FileManager.default.fileExists(atPath: planOutDir.appendingPathComponent("review_plan.json").path) {
            if !planHasReviewActions(planOutDir: planOutDir) {
                return nonActionableReviewCommands(sessionPath: sessionPath)
            }
            if hasCompletedReviewProgress(planOutDir: planOutDir) {
                let readinessCommands = rows.compactMap { string($0["command"]) }.filter { !$0.isEmpty }
                let readinessReviewCommands = readinessCommands.filter { $0.contains("murmurmark review") }
                if !readinessReviewCommands.isEmpty {
                    return readinessReviewCommands
                }
                return [
                    "murmurmark review workspace --session \(sessionPath)",
                    "murmurmark review progress --session \(sessionPath)",
                ]
            }
            return sessionLocalReviewCommands(sessionArg: sessionPath, planOutDir: planOutDir)
        }
        let commands = rows.compactMap { string($0["command"]) }.filter { !$0.isEmpty }
        let reviewCommands = commands.filter { $0.contains("murmurmark review") }
        if !reviewCommands.isEmpty {
            return reviewCommands
        }
        if gate == "ready_for_notes" {
            return [
                "murmurmark finish \(sessionPath)",
                "murmurmark export \(sessionPath) --format markdown --include-json",
            ]
        }
        if gate.hasPrefix("pipeline_incomplete") {
            return ["murmurmark process \(sessionPath)"]
        }
        return commands.isEmpty ? ["less \(sessionPath)/derived/readiness/session_readiness.md"] : commands
    }

    private static func nonActionableReviewCommands(sessionPath: String) -> [String] {
        [
            "murmurmark status \(sessionPath)",
            "murmurmark report \(sessionPath)",
            "less \(sessionPath)/derived/readiness/session_readiness.md",
        ]
    }

    private static func sessionLocalReviewCommands(sessionArg: String, planOutDir: URL) -> [String] {
        let firstLane = firstRecommendedLane(planOutDir: planOutDir) ?? "first"
        let manualLaneCommands = [
            "murmurmark review first-lane --session \(sessionArg)",
            "murmurmark review lane apply \(firstLane) --session \(sessionArg)",
        ]
        let suggestedCommands = [
            "murmurmark review suggested \(sessionArg)",
            "murmurmark review suggested apply \(sessionArg)",
        ]
        var commands: [String] = []
        if hasPartialReviewProgress(planOutDir: planOutDir) {
            commands.append("murmurmark review progress --session \(sessionArg)")
            commands += manualLaneCommands
            commands.append("murmurmark review apply --session \(sessionArg)")
            commands += suggestedCommands
        } else {
            commands += suggestedCommands
            commands += manualLaneCommands
        }
        if let quickLane = quickRecommendedLane(planOutDir: planOutDir), quickLane != firstLane {
            commands += [
                "murmurmark review lane \(quickLane) --session \(sessionArg)",
                "murmurmark review lane apply \(quickLane) --session \(sessionArg)",
            ]
        }
        commands += [
            "murmurmark review workspace --session \(sessionArg)",
            "murmurmark review workspace apply --session \(sessionArg)",
        ]
        if !hasPartialReviewProgress(planOutDir: planOutDir) {
            commands += [
                "murmurmark review progress --session \(sessionArg)",
                "murmurmark review apply --session \(sessionArg)",
            ]
        }
        return commands
    }

    private static func hasPartialReviewProgress(planOutDir: URL) -> Bool {
        let progressURL = planOutDir.appendingPathComponent("review_decisions_progress.json")
        guard let payload = try? JSONFiles.object(progressURL),
              let summary = payload["summary"] as? [String: Any]
        else {
            return false
        }
        let reviewed = int(summary["reviewed"]) ?? 0
        let remaining = int(summary["remaining"]) ?? 0
        return reviewed > 0 && remaining > 0
    }

    private static func hasCompletedReviewProgress(planOutDir: URL) -> Bool {
        let progressURL = planOutDir.appendingPathComponent("review_decisions_progress.json")
        guard let payload = try? JSONFiles.object(progressURL),
              let summary = payload["summary"] as? [String: Any]
        else {
            return false
        }
        let reviewed = int(summary["reviewed"]) ?? 0
        let remaining = int(summary["remaining"]) ?? 0
        return reviewed > 0 && remaining == 0
    }

    private static func firstRecommendedLane(planOutDir: URL) -> String? {
        recommendedLane("first_recommended_lane", planOutDir: planOutDir)
    }

    private static func quickRecommendedLane(planOutDir: URL) -> String? {
        recommendedLane("quick_recommended_lane", planOutDir: planOutDir)
    }

    private static func recommendedLane(_ key: String, planOutDir: URL) -> String? {
        let plan = planOutDir.appendingPathComponent("review_plan.json")
        guard FileManager.default.fileExists(atPath: plan.path),
              let payload = try? JSONFiles.object(plan),
              let strategy = payload["review_queue_strategy"] as? [String: Any]
        else {
            return nil
        }
        return string(strategy[key])
    }

    private static func planHasReviewActions(planOutDir: URL) -> Bool {
        let plan = planOutDir.appendingPathComponent("review_plan.json")
        guard FileManager.default.fileExists(atPath: plan.path),
              let payload = try? JSONFiles.object(plan),
              let summary = payload["summary"] as? [String: Any]
        else {
            return false
        }
        let actions = int(summary["review_action_count"]) ?? int(summary["raw_item_count"]) ?? 0
        return actions > 0
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func double(_ value: Any?) -> Double? {
        if let number = value as? NSNumber { return number.doubleValue }
        if let text = value as? String { return Double(text) }
        return nil
    }

    private static func int(_ value: Any?) -> Int? {
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String { return Int(text) }
        return nil
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }
}

enum ExportPrinter {
    static func printBlocked(session: URL, outDir: URL) throws {
        let blockedURL = outDir.appendingPathComponent("\(session.lastPathComponent).export_blocked.json")
        guard FileManager.default.fileExists(atPath: blockedURL.path) else {
            print("")
            print("export_blocked: missing")
            print("  expected: \(PathDisplay.display(blockedURL))")
            return
        }

        let payload = try JSONFiles.object(blockedURL)
        let blockers = payload["blockers"] as? [Any] ?? []
        let warnings = payload["warnings"] as? [Any] ?? []
        let next = string(payload["next"]) ?? "murmurmark report \(PathDisplay.display(session))"
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let exportCommands = payload["export_commands"] as? [String: Any] ?? [:]
        let profile = string(payload["selected_profile"]) ?? string(payload["requested_profile"]) ?? "unknown"
        let format = string(payload["format"]) ?? "unknown"

        print("")
        print("export_blocked:")
        print("  report: \(PathDisplay.display(blockedURL))")
        print("  profile: \(profile)")
        print("  format: \(format)")
        print("  blockers: \(compactJSON(blockers))")
        if !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let recommendedNext = nextCommands.compactMap { string($0["command"]) }.first ?? next
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        if nextCommands.isEmpty {
            print("    \(next)")
        } else {
            print("    commands:")
            for item in nextCommands {
                if let label = string(item["label"]) {
                    print("      # \(label)")
                }
                if let command = string(item["command"]) {
                    print("      \(command)")
                }
            }
        }
        if let rerun = string(exportCommands["rerun"]) {
            print("    rerun_export:")
            print("      \(rerun)")
        }
        if let debugForce = string(exportCommands["debug_force"]) {
            print("    debug_force:")
            print("      \(debugForce)")
        }
        print("")
        print("next: \(recommendedNext)")
    }

    static func printManifest(session: URL, outDir: URL) throws {
        let manifestURL = outDir
            .appendingPathComponent(session.lastPathComponent)
            .appendingPathComponent("export_manifest.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path) else {
            print("")
            print("export_manifest: missing")
            print("  expected: \(PathDisplay.display(manifestURL))")
            return
        }
        let payload = try JSONFiles.object(manifestURL)
        let files = payload["files"] as? [String: Any] ?? [:]
        let status = string(payload["status"]) ?? "unknown"
        let profile = string(payload["selected_profile"]) ?? "unknown"
        let format = string(payload["format"]) ?? "unknown"
        let verdict = string(payload["verdict"]) ?? "unknown"
        let useGate = string(payload["use_gate"]) ?? "unknown"
        let blockers = payload["blockers"] as? [Any] ?? []
        let warnings = payload["warnings"] as? [Any] ?? []
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let openCommands = payload["open_commands"] as? [[String: Any]] ?? []
        let debugRetentionCommands = payload["debug_retention_commands"] as? [[String: Any]] ?? []

        print("")
        print("export:")
        print("  manifest: \(PathDisplay.display(manifestURL))")
        print("  status: \(status)")
        print("  profile: \(profile)")
        print("  format: \(format)")
        print("  verdict: \(verdict)")
        print("  use_gate: \(useGate)")
        print("  files: \(files.count)")
        if !blockers.isEmpty {
            print("  blockers: \(compactJSON(blockers))")
        }
        if !warnings.isEmpty {
            print("  warnings: \(compactJSON(warnings))")
        }
        let retentionPlan = "murmurmark retention plan \(PathDisplay.display(session)) --export-manifest \(PathDisplay.display(manifestURL))"
        let retentionPayload = "murmurmark retention payload \(PathDisplay.display(session)) --export-manifest \(PathDisplay.display(manifestURL))"
        let isReadyExport = blockers.isEmpty && useGate == "ready_for_notes" && !status.contains("forced")
        let reviewNext = readinessNextCommand(session: session) ?? "murmurmark review next \(PathDisplay.display(session))"
        let manifestNext = string(payload["next"])
        let recommendedNext = manifestNext ?? nextCommands.compactMap { string($0["command"]) }.first ?? (isReadyExport ? retentionPlan : reviewNext)
        print("  recommended_next: \(recommendedNext)")
        print("  open:")
        if openCommands.isEmpty {
            for key in ["index", "obsidian_note", "quality_verdict_md", "notes_md", "transcript_md"] {
                if let path = exportedPath(key, files: files) {
                    print("    \(key): \(PathDisplay.display(path))")
                }
            }
        } else {
            for item in openCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        }
        print("  next:")
        if !nextCommands.isEmpty {
            for item in nextCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        } else if isReadyExport {
            print("    \(retentionPlan)")
            print("    \(retentionPayload)")
        } else {
            print("    \(reviewNext)")
            print("    murmurmark report \(PathDisplay.display(session))")
        }
        if !debugRetentionCommands.isEmpty {
            print("  debug_retention:")
            for item in debugRetentionCommands {
                if let command = string(item["command"]) {
                    print("    \(command)")
                }
            }
        } else if !isReadyExport {
            print("  debug_retention:")
            print("    \(retentionPlan)")
            print("    \(retentionPayload)")
        }
        print("")
        print("next: \(recommendedNext)")
    }

    private static func readinessNextCommand(session: URL) -> String? {
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: readinessURL.path),
              let payload = try? JSONFiles.object(readinessURL)
        else {
            return nil
        }
        if let nextCommands = payload["next_commands"] as? [[String: Any]],
           let command = ReadinessPrinter.preferredNextCommand(nextCommands) {
            return command
        }
        let gate = string(payload["use_gate"]) ?? ""
        let exportBlockers = strings(payload["export_blockers"])
        let reviewBlockers = strings(payload["review_blockers"])
        let sessionPath = PathDisplay.display(session)
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains("pipeline_incomplete") {
            return "murmurmark process \(sessionPath)"
        }
        if gate == "review_first" || !reviewBlockers.isEmpty || !exportBlockers.isEmpty {
            return "murmurmark review next \(sessionPath)"
        }
        return nil
    }

    private static func exportedPath(_ key: String, files: [String: Any]) -> URL? {
        guard let item = files[key] as? [String: Any],
              let path = item["path"] as? String
        else {
            return nil
        }
        return PathURLs.fileURL(path)
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func strings(_ value: Any?) -> [String] {
        (value as? [Any] ?? []).map { String(describing: $0) }
    }

    private static func compactJSON(_ value: Any) -> String {
        guard JSONSerialization.isValidJSONObject(value),
              let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
              let text = String(data: data, encoding: .utf8)
        else {
            return "\(value)"
        }
        return text
    }
}

enum CorpusPrinter {
    static func printAvailableStatusReports() throws {
        let transcriptOrder = PathURLs.fileURL("sessions/_reports/transcript-order/transcript_order_corpus_report.json")
        let localRecall = PathURLs.fileURL("sessions/_reports/local-recall/local_recall_corpus_report.json")
        let remoteLeak = PathURLs.fileURL("sessions/_reports/remote-leak-segment/remote_leak_segment_corpus_report.json")
        let corpusGates = PathURLs.fileURL("sessions/_reports/corpus-gates/corpus_gates_report.json")
        let operationalReadiness = PathURLs.fileURL("sessions/_reports/operational-readiness/operational_readiness_report.json")
        let taxonomy = PathURLs.fileURL("sessions/_reports/audio-error-taxonomy/audio_error_taxonomy_report.json")
        if FileManager.default.fileExists(atPath: taxonomy.path) {
            try printTaxonomy()
        }
        if FileManager.default.fileExists(atPath: transcriptOrder.path) {
            try printTranscriptOrder()
        }
        if FileManager.default.fileExists(atPath: localRecall.path) {
            try printLocalRecallCorpus()
        }
        if FileManager.default.fileExists(atPath: remoteLeak.path) {
            try printRemoteLeakSegment()
        }
        if FileManager.default.fileExists(atPath: corpusGates.path) {
            try printGates()
        }
        if FileManager.default.fileExists(atPath: operationalReadiness.path) {
            try printOperationalReadiness()
        }
    }

    static func printSessionQuality() throws {
        try ReadinessPrinter.printCorpus(report: PathURLs.fileURL("sessions/_reports/session-quality/session_quality_report.json"))
    }

    static func printBuild(outDir: URL = PathURLs.fileURL("sessions/_reports/regression-corpus")) throws {
        let url = outDir.appendingPathComponent("regression_corpus_summary.json")
        let payload = try JSONFiles.object(url)
        let report = outDir.appendingPathComponent("regression_corpus.md")
        let nextCommand = "murmurmark corpus evaluate --corpus-dir \(PathDisplay.display(outDir))"
        print("")
        print("regression_corpus:")
        print("  report: \(PathDisplay.display(report))")
        print("  sessions: \(int(payload["session_count"]) ?? 0)")
        print("  items: \(int(payload["item_count"]) ?? 0)")
        if let labels = payload["by_label"] as? [String: Any] {
            print("  by_label: \(corpusPrinterCompactJSON(labels))")
        }
        if let skipped = payload["skipped_sessions"] as? [[String: Any]], !skipped.isEmpty {
            print("  skipped_sessions: \(skipped.count)")
        }
        printCorpusStageHandoff(report: report, nextCommand: nextCommand)
    }

    static func printEvaluation(outDir: URL = PathURLs.fileURL("sessions/_reports/regression-corpus")) throws {
        let url = outDir.appendingPathComponent("regression_corpus_evaluation.json")
        let payload = try JSONFiles.object(url)
        let report = outDir.appendingPathComponent("regression_corpus_evaluation.md")
        let nextCommand = "murmurmark corpus train-audio-judge --corpus-dir \(PathDisplay.display(outDir))"
        print("")
        print("regression_evaluation:")
        print("  report: \(PathDisplay.display(report))")
        print("  readiness: \(string(payload["readiness"]) ?? "unknown")")
        print("  sessions: \(int(payload["session_count"]) ?? 0)")
        print("  items: \(int(payload["item_count"]) ?? 0)")
        if let missing = payload["missing_labels"] as? [Any], !missing.isEmpty {
            print("  missing_labels: \(corpusPrinterCompactJSON(missing))")
        }
        printCorpusStageHandoff(report: report, nextCommand: nextCommand)
    }

    static func printAudioJudge(outDir: URL = PathURLs.fileURL("sessions/_reports/audio-judge-v0")) throws {
        let url = outDir.appendingPathComponent("audio_judge_v0_report.json")
        let payload = try JSONFiles.object(url)
        let training = payload["training"] as? [String: Any] ?? [:]
        let evaluation = payload["evaluation"] as? [String: Any] ?? [:]
        let queue = payload["review_queue"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("audio_judge_v0_report.md")
        let inputs = payload["inputs"] as? [String: Any] ?? [:]
        let corpusDir = string(inputs["corpus_dir"]) ?? "sessions/_reports/regression-corpus"
        let nextCommand = "murmurmark corpus taxonomy --corpus-dir \(corpusDir) --audio-judge-dir \(PathDisplay.display(outDir))"
        print("")
        print("audio_judge:")
        print("  report: \(PathDisplay.display(report))")
        print("  cv_predictions: \(PathDisplay.display(outDir.appendingPathComponent("audio_judge_v0_cv_predictions.jsonl")))")
        print("  rows: \(int(training["rows"]) ?? 0)")
        print("  sessions: \(int(training["sessions"]) ?? 0)")
        if let accuracy = double(evaluation["cv_accuracy"]) {
            print(String(format: "  cv_accuracy: %.3f", accuracy))
        }
        if let accuracy = double(evaluation["policy_accuracy"]) {
            print(String(format: "  policy_accuracy: %.3f", accuracy))
        }
        print("  queue_items: \(int(queue["items"]) ?? 0)")
        print("  remaining_human_review_items: \(int(queue["remaining_human_review_items"]) ?? 0)")
        if let labels = queue["by_judge_label"] as? [String: Any] {
            print("  by_judge_label: \(corpusPrinterCompactJSON(labels))")
        }
        printCorpusStageHandoff(report: report, nextCommand: nextCommand)
    }

    private static func printCorpusStageHandoff(report: URL, nextCommand: String) {
        let readCommand = "less \(PathDisplay.display(report))"
        print("  read: \(readCommand)")
        print("  recommended_next: \(nextCommand)")
        print("  next:")
        print("    \(nextCommand)")
        FinalNextPrinter.print(nextCommand)
    }

    static func printTaxonomy(outDir: URL = PathURLs.fileURL("sessions/_reports/audio-error-taxonomy")) throws {
        let url = outDir.appendingPathComponent("audio_error_taxonomy_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("audio_error_taxonomy_report.md")
        let readCommand = "less \(PathDisplay.display(report))"
        print("")
        print("audio_error_taxonomy:")
        print("  report: \(PathDisplay.display(report))")
        print("  items: \(int(summary["items"]) ?? 0)")
        print("  sessions: \(int(summary["sessions"]) ?? 0)")
        if let seconds = double(summary["attention_seconds"]) {
            print(String(format: "  attention_seconds: %.2f", seconds))
        }
        if let missing = summary["missing_classes"] as? [Any], !missing.isEmpty {
            print("  missing_classes: \(corpusPrinterCompactJSON(missing))")
        }
        if let focus = payload["focus_areas"] as? [[String: Any]], let first = focus.first {
            print("  first_focus: \(string(first["area"]) ?? "unknown") -> \(string(first["next_action"]) ?? "unknown")")
        }
        if let plan = payload["action_plan"] as? [[String: Any]], let first = plan.first {
            let work = string(first["next_work"]) ?? "unknown"
            let diagnostic = string(first["diagnostic"]) ?? "unknown"
            print("  first_action: \(work) (\(diagnostic))")
        }
        print("  read: \(readCommand)")
        if let commands = payload["next_commands"] as? [String], !commands.isEmpty {
            print("  follow_up:")
            for command in commands.prefix(4) {
                print("    \(command)")
            }
        }
        print("  recommended_next: \(readCommand)")
        print("  next:")
        print("    \(readCommand)")
        FinalNextPrinter.print(readCommand)
    }

    static func printGates(outDir: URL = PathURLs.fileURL("sessions/_reports/corpus-gates")) throws {
        let url = outDir.appendingPathComponent("corpus_gates_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("corpus_gates_report.md")
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let readCommand = string(payload["recommended_next"])
            ?? nextCommands.compactMap { string($0["command"]) }.first
            ?? "less \(PathDisplay.display(report))"
        print("")
        print("corpus_gates:")
        print("  report: \(PathDisplay.display(report))")
        print("  status: \(string(payload["status"]) ?? "unknown")")
        print("  failed_gates: \(int(payload["failed_gate_count"]) ?? 0)")
        print("  warnings: \(int(payload["warning_count"]) ?? 0)")
        print("  complete_pipeline_count: \(int(summary["complete_pipeline_count"]) ?? 0)")
        print("  ready_for_notes: \(int(summary["ready_for_notes"]) ?? 0)")
        print("  review_first: \(int(summary["review_first"]) ?? 0)")
        if let blocking = int(summary["local_recall_complete_blocking_sessions"]) {
            print("  local_recall_complete_blocking_sessions: \(blocking)")
        }
        if let seconds = double(summary["local_recall_possible_lost_me_seconds"]) {
            print(String(format: "  local_recall_possible_lost_me_seconds: %.2f", seconds))
        }
        if let remoteLeakItems = int(summary["remote_leak_segment_item_count"]) {
            print("  remote_leak_segment_items: \(remoteLeakItems)")
        }
        if let protectedItems = int(summary["remote_leak_segment_protect_local_content_items"]) {
            print("  remote_leak_protect_local_content_items: \(protectedItems)")
        }
        let suggestedGeneratedRows = int(summary["suggested_closure_generated_rows"]) ?? 0
        let suggestedActionableRows = int(summary["suggested_closure_actionable_rows"]) ?? 0
        let suggestedNeedsReviewRows = int(summary["suggested_closure_needs_review_rows"]) ?? 0
        let suggestedAutoRows = int(summary["suggested_closure_auto_rows"]) ?? 0
        let suggestedManualRows = int(summary["suggested_closure_manual_remaining_rows"]) ?? 0
        if suggestedGeneratedRows > 0 || suggestedAutoRows > 0 || suggestedManualRows > 0 {
            print("  suggested_closure:")
            print(
                "    generated: \(suggestedGeneratedRows) rows "
                    + "(actionable=\(suggestedActionableRows), needs_review=\(suggestedNeedsReviewRows))"
            )
            print("    safe_rows: \(suggestedAutoRows) rows")
            print("    manual_remaining: \(suggestedManualRows) rows")
        }
        print("  read: \(readCommand)")
        print("  recommended_next: \(readCommand)")
        print("  next:")
        print("    \(readCommand)")
        FinalNextPrinter.print(readCommand)
    }

    static func printTranscriptOrder(outDir: URL = PathURLs.fileURL("sessions/_reports/transcript-order")) throws {
        let url = outDir.appendingPathComponent("transcript_order_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("transcript_order_corpus_report.md")
        print("")
        print("transcript_order_corpus:")
        print("  report: \(PathDisplay.display(report))")
        print("  audited_sessions: \(int(summary["audited_session_count"]) ?? 0) / \(int(summary["session_count"]) ?? 0)")
        print("  blocking_sessions: \(int(summary["blocking_session_count"]) ?? 0)")
        print("  complete_blocking_sessions: \(int(summary["complete_blocking_session_count"]) ?? 0)")
        if let seconds = double(summary["probable_order_risk_seconds"]) {
            print(String(format: "  probable_order_risk_seconds: %.2f", seconds))
        }
        if let seconds = double(summary["needs_review_seconds"]) {
            print(String(format: "  needs_review_seconds: %.2f", seconds))
        }
        if let repair = summary["order_repair"] as? [String: Any] {
            print("  order_repair_applied_repairs: \(int(repair["applied_repairs"]) ?? 0)")
            print("  order_repair_cleared_sessions: \(int(repair["cleared_session_count"]) ?? 0)")
            print("  order_repair_unrepaired_order_risks: \(int(repair["unrepaired_order_risks"]) ?? 0)")
        }
        print("  recommendation: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printCorpusDiagnosticHandoff(payload: payload, report: report)
    }

    static func printLocalRecallCorpus(outDir: URL = PathURLs.fileURL("sessions/_reports/local-recall")) throws {
        let url = outDir.appendingPathComponent("local_recall_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("local_recall_corpus_report.md")
        print("")
        print("local_recall_corpus:")
        print("  report: \(PathDisplay.display(report))")
        print("  audited_sessions: \(int(summary["audited_session_count"]) ?? 0) / \(int(summary["session_count"]) ?? 0)")
        print("  blocking_sessions: \(int(summary["blocking_session_count"]) ?? 0)")
        print("  complete_blocking_sessions: \(int(summary["complete_blocking_session_count"]) ?? 0)")
        if let seconds = double(summary["possible_lost_me_seconds"]) {
            print(String(format: "  possible_lost_me_seconds: %.2f", seconds))
        }
        if let seconds = double(summary["needs_review_seconds"]) {
            print(String(format: "  needs_review_seconds: %.2f", seconds))
        }
        if let seconds = double(summary["likely_harmless_seconds"]) {
            print(String(format: "  likely_harmless_seconds: %.2f", seconds))
        }
        print("  recommendation: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printCorpusDiagnosticHandoff(payload: payload, report: report)
    }

    static func printLocalRecallRepairCorpus(outDir: URL = PathURLs.fileURL("sessions/_reports/local-recall-repair")) throws {
        let url = outDir.appendingPathComponent("local_recall_repair_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("local_recall_repair_corpus_report.md")
        print("")
        print("local_recall_repair_corpus:")
        print("  report: \(PathDisplay.display(report))")
        print("  repaired_sessions: \(int(summary["repaired_session_count"]) ?? 0) / \(int(summary["session_count"]) ?? 0)")
        print("  sessions_with_repairs: \(int(summary["sessions_with_repairs"]) ?? 0)")
        print("  applied_repairs: \(int(summary["applied_repairs"]) ?? 0)")
        print("  reviewable_applied_repairs: \(int(summary["reviewable_applied_repairs"]) ?? 0)")
        print("  incomplete_applied_repairs: \(int(summary["incomplete_applied_repairs"]) ?? 0)")
        if let seconds = double(summary["inserted_me_seconds"]) {
            print(String(format: "  inserted_me_seconds: %.2f", seconds))
        }
        print("  rejected_items: \(int(summary["rejected_items"]) ?? 0)")
        print("  recommendation: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printCorpusDiagnosticHandoff(payload: payload, report: report)
    }

    static func printRemoteLeakSegment(outDir: URL = PathURLs.fileURL("sessions/_reports/remote-leak-segment")) throws {
        let url = outDir.appendingPathComponent("remote_leak_segment_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("remote_leak_segment_corpus_report.md")
        print("")
        print("remote_leak_segment_corpus:")
        print("  report: \(PathDisplay.display(report))")
        print("  planned_sessions: \(int(summary["planned_session_count"]) ?? 0) / \(int(summary["session_count"]) ?? 0)")
        print("  missing_plans: \(int(summary["missing_plan_count"]) ?? 0)")
        print("  items: \(int(summary["item_count"]) ?? 0)")
        print("  protect_local_content_items: \(int(summary["protect_local_content_items"]) ?? 0)")
        print("  reviewable_protect_local_content_items: \(int(summary["reviewable_protect_local_content_items"]) ?? 0)")
        print("  incomplete_protect_local_content_items: \(int(summary["incomplete_protect_local_content_items"]) ?? 0)")
        if let seconds = double(summary["protect_local_content_seconds"]) {
            print(String(format: "  protect_local_content_seconds: %.2f", seconds))
        }
        print("  recommendation: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printCorpusDiagnosticHandoff(payload: payload, report: report)
    }

    static func printASRPositiveEchoCandidate(
        outDir: URL = PathURLs.fileURL("sessions/_reports/asr-positive-echo-candidate")
    ) throws {
        let url = outDir.appendingPathComponent("asr_positive_echo_candidate_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let gate = payload["promotion_gate"] as? [String: Any] ?? [:]
        let report = outDir.appendingPathComponent("asr_positive_echo_candidate_corpus_report.md")
        print("")
        print("asr_positive_echo_candidate_corpus:")
        print("  report: \(PathDisplay.display(report))")
        print("  candidate: \(string(summary["candidate"]) ?? "unknown")")
        print("  reports_found: \(int(summary["reports_found"]) ?? 0) / \(int(summary["sessions"]) ?? 0)")
        print("  safe_improved_sessions: \(int(summary["safe_improved_sessions"]) ?? 0)")
        print("  not_applicable_sessions: \(int(summary["not_applicable_sessions"]) ?? 0)")
        print("  local_recall_regressions: \(int(summary["local_recall_regressions"]) ?? 0)")
        print("  promotion_gate: \((gate["passed"] as? Bool) == true ? "passed" : "not_passed")")
        print("  promotion_decision: \(string(summary["promotion_decision"]) ?? "shadow_only_do_not_promote")")
        printCorpusDiagnosticHandoff(payload: payload, report: report)
    }

    private static func printCorpusDiagnosticHandoff(payload: [String: Any], report: URL) {
        let readCommand = "less \(PathDisplay.display(report))"
        let command = firstNextCommand(payload) ?? readCommand
        print("  read: \(readCommand)")
        if let nextCommand = firstNextCommand(payload) {
            print("  next_command: \(nextCommand)")
        }
        print("  recommended_next: \(command)")
        print("  next:")
        print("    \(command)")
        FinalNextPrinter.print(command)
    }

    static func printOperationalReadiness(
        outDir: URL = PathURLs.fileURL("sessions/_reports/operational-readiness")
    ) throws {
        let url = outDir.appendingPathComponent("operational_readiness_report.json")
        let sessionsRoot = outDir.deletingLastPathComponent().deletingLastPathComponent()
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let useGates = summary["use_gates"] as? [String: Any] ?? [:]
        let reviewSeconds = double(summary["total_review_burden_sec"]) ?? 0
        let transcriptReviewSeconds = double(summary["total_transcript_review_burden_sec"]) ?? reviewSeconds
        let lanePack = preparedLanePackHandoff(payload: payload, sessionsRoot: sessionsRoot, freshnessReference: url)
        print("")
        print("operational_readiness:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("operational_readiness_report.md")))")
        print("  verdict: \(string(payload["operational_verdict"]) ?? "unknown")")
        print("  sessions_in_scope: \(int(summary["session_count"]) ?? 0)")
        print("  sessions_excluded: \(int(summary["excluded_diagnostic_session_count"]) ?? 0)")
        print("  sessions_ready_for_notes: \(int(useGates["ready_for_notes"]) ?? 0)")
        print("  sessions_review_first: \(int(useGates["review_first"]) ?? 0)")
        print(String(format: "  notes_review_minutes: %.2f", reviewSeconds / 60))
        if abs(transcriptReviewSeconds - reviewSeconds) > 0.05 {
            print(String(format: "  transcript_review_minutes: %.2f", transcriptReviewSeconds / 60))
        }
        print("  review_actions: \(int(summary["review_action_count"]) ?? int(summary["review_queue_items"]) ?? 0)")
        printLowMaterialityReviewRows(summary)
        print("  grouped_review_rows: \(int(summary["grouped_review_row_count"]) ?? 0)")
        printOperationalUse(payload)
        printFirstNextCommand(payload)
        if let lanePack {
            printOperationalFocus(lanePack)
        } else {
            printOperationalFocus(payload)
        }
        if let command = firstNextCommand(payload) {
            FinalNextPrinter.print(command)
        }
    }

    static func printOperationalNext(
        outDir: URL = PathURLs.fileURL("sessions/_reports/operational-readiness")
    ) throws {
        let url = outDir.appendingPathComponent("operational_readiness_report.json")
        let sessionsRoot = outDir.deletingLastPathComponent().deletingLastPathComponent()
        let reportCommand = corpusReportCommand(sessionsRoot: sessionsRoot)
        guard FileManager.default.fileExists(atPath: url.path) else {
            print("")
            print("corpus_next:")
            print("  status: missing_operational_readiness")
            print("  command: \(reportCommand)")
            print("  reason: operational_readiness_report.json is missing")
            print("  read: \(reportCommand)")
            FinalNextPrinter.print(reportCommand)
            return
        }

        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let reviewSeconds = double(summary["total_review_burden_sec"]) ?? 0
        let transcriptReviewSeconds = double(summary["total_transcript_review_burden_sec"]) ?? reviewSeconds
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let lanePack = preparedLanePackHandoff(payload: payload, sessionsRoot: sessionsRoot, freshnessReference: url)
        let readinessCommand = nextCommands.compactMap { string($0["command"]) }.first
        let exportHandoff = lanePack == nil ? successfulExportHandoff(fromNextCommand: readinessCommand) : nil
        let command = lanePack?.command ?? exportHandoff?.command ?? readinessCommand ?? reportCommand
        let source: String
        if lanePack != nil {
            source = "review_lane_pack"
        } else if exportHandoff != nil {
            source = "export_manifest"
        } else {
            source = "operational_readiness"
        }
        print("")
        print("corpus_next:")
        print("  status: \(string(payload["operational_verdict"]) ?? "unknown")")
        print("  command: \(command)")
        print("  source: \(source)")
        if let exportHandoff {
            print("  export_manifest: \(PathDisplay.display(exportHandoff.manifest))")
        }
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("operational_readiness_report.md")))")
        print("  sessions_in_scope: \(int(summary["session_count"]) ?? 0)")
        print("  sessions_excluded: \(int(summary["excluded_diagnostic_session_count"]) ?? 0)")
        print(String(format: "  notes_review_minutes: %.2f", reviewSeconds / 60))
        if abs(transcriptReviewSeconds - reviewSeconds) > 0.05 {
            print(String(format: "  transcript_review_minutes: %.2f", transcriptReviewSeconds / 60))
        }
        print("  review_actions: \(int(summary["review_action_count"]) ?? int(summary["review_queue_items"]) ?? 0)")
        printLowMaterialityReviewRows(summary)
        printOperationalUse(payload)
        if let lanePack {
            print("  lane_pack: \(PathDisplay.display(lanePack.manifest))")
            if let itemCount = lanePack.itemCount {
                print("  focus_pack_items: \(itemCount)")
            }
            if let selectedRows = lanePack.selectedRows {
                print("  focus_pack_rows: \(selectedRows)")
            }
            if let durationSec = lanePack.durationSec {
                print(String(format: "  focus_pack_minutes: %.2f", durationSec / 60.0))
            }
            if let itemCount = lanePack.itemCount {
                let totalActions = int(summary["review_action_count"]) ?? int(summary["review_queue_items"]) ?? 0
                print("  after_focus_pack_actions: \(max(0, totalActions - itemCount))")
            }
            if let selectedRows = lanePack.selectedRows {
                let totalRows = int(summary["review_queue_items"]) ?? 0
                print("  after_focus_pack_rows: \(max(0, totalRows - selectedRows))")
            }
            if let markdown = lanePack.markdown {
                print("  read: less \(shellQuote(PathDisplay.display(markdown)))")
            }
            if let answerSheet = lanePack.answerSheet {
                print("  edit: $EDITOR \(shellQuote(PathDisplay.display(answerSheet)))")
            }
            if let answerSheetStatus = lanePack.answerSheetStatus {
                print("  answer_sheet_status: \(answerSheetStatus)")
            }
            if let dryRun = lanePack.dryRunCommand {
                print("  dry_run: \(dryRun)")
            }
            if let apply = lanePack.applyCommand {
                print("  apply: \(apply)")
            }
        }
        if nextCommands.count > 1 {
            print("  alternatives:")
            for item in nextCommands.dropFirst().prefix(4) {
                guard let alternative = string(item["command"]), !alternative.isEmpty else { continue }
                print("    \(alternative)")
            }
        }
        if let lanePack {
            printOperationalFocus(lanePack)
        } else {
            printOperationalFocus(payload)
        }
        FinalNextPrinter.print(command)
    }

    private static func printOperationalUse(_ payload: [String: Any]) {
        let verdict = string(payload["operational_verdict"]) ?? "unknown"
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let useGates = summary["use_gates"] as? [String: Any] ?? [:]
        let sessionCount = int(summary["session_count"]) ?? 0
        let ready = int(useGates["ready_for_notes"]) ?? 0
        let reviewFirst = int(useGates["review_first"]) ?? 0
        let incomplete = useGates.reduce(0) { total, item in
            item.key.hasPrefix("pipeline_incomplete")
                ? total + (int(item.value) ?? 0)
                : total
        }
        let reviewActions = int(summary["review_action_count"]) ?? int(summary["review_queue_items"]) ?? 0
        let reviewSeconds = double(summary["total_review_burden_sec"]) ?? 0
        let blockers = (payload["blockers"] as? [Any] ?? []).map { String(describing: $0) }
        let warnings = (payload["warnings"] as? [Any] ?? []).map { String(describing: $0) }
        let summaryText: String
        switch verdict {
        case "medium_risk_ready":
            summaryText = "corpus ready for medium-risk meeting notes"
        case "pilot_ready_with_review":
            summaryText = "pilot-ready with review; close queued review before broader use"
        case "not_ready":
            summaryText = "not ready; fix blockers before relying on corpus output"
        default:
            summaryText = "check operational readiness before use"
        }

        print("  use:")
        print("    summary: \(summaryText)")
        print("    can_use_any_notes: \(ready > 0)")
        print("    can_use_medium_risk: \(verdict == "medium_risk_ready")")
        print("    ready_sessions: \(ready)/\(sessionCount)")
        print("    review_first_sessions: \(reviewFirst)")
        print("    incomplete_sessions: \(incomplete)")
        print("    review_actions: \(reviewActions)")
        if reviewSeconds > 0 {
            print(String(format: "    notes_review_burden_min: %.2f", reviewSeconds / 60))
        }
        if let blocker = blockers.first ?? warnings.first {
            print("    blocker: \(blocker)")
        }
        if let command = firstNextCommand(payload) {
            print("    minimum_step: \(command)")
        }
    }

    private static func printLowMaterialityReviewRows(_ summary: [String: Any]) {
        guard let lowMateriality = summary["review_queue_low_materiality_excluded"] as? [String: Any],
              let items = int(lowMateriality["items"]),
              items > 0
        else {
            return
        }
        let seconds = double(lowMateriality["seconds"]) ?? 0.0
        print(String(format: "  low_materiality_review_rows: %d / %.2f sec", items, seconds))
    }

    private struct PreparedLanePackHandoff {
        let command: String
        let session: URL
        let sessionID: String
        let lane: String
        let label: String?
        let action: String?
        let manifest: URL
        let markdown: URL?
        let answerSheet: URL?
        let answerSheetStatus: String?
        let dryRunCommand: String?
        let applyCommand: String?
        let itemCount: Int?
        let selectedRows: Int?
        let durationSec: Double?
    }

    private struct ReviewLaneTarget {
        let sessionID: String
        let session: URL
        let lane: String
    }

    private struct ExportHandoff {
        let command: String
        let manifest: URL
    }

    private static func successfulExportHandoff(fromNextCommand command: String?) -> ExportHandoff? {
        guard let session = exportSession(from: command) else { return nil }
        let manifestURL = PathURLs.fileURL("exports/private")
            .appendingPathComponent(session.lastPathComponent)
            .appendingPathComponent("export_manifest.json")
        guard FileManager.default.fileExists(atPath: manifestURL.path),
              let payload = try? JSONFiles.object(manifestURL)
        else {
            return nil
        }
        guard string(payload["schema"]) == "murmurmark.export_manifest/v1" else { return nil }
        let status = string(payload["status"]) ?? ""
        guard status == "exported" || status == "exported_with_warnings" else { return nil }
        let blockers = payload["blockers"] as? [Any] ?? []
        guard blockers.isEmpty else { return nil }
        if let nextCommands = payload["next_commands"] as? [[String: Any]],
           let command = ReadinessPrinter.preferredNextCommand(nextCommands) {
            return ExportHandoff(command: command, manifest: manifestURL)
        }
        if let next = string(payload["next"]), !next.isEmpty {
            return ExportHandoff(command: next, manifest: manifestURL)
        }
        return nil
    }

    private static func exportSession(from command: String?) -> URL? {
        guard let command, command.hasPrefix("murmurmark export ") else { return nil }
        let rest = command.dropFirst("murmurmark export ".count)
        guard let rawToken = rest.split(whereSeparator: \.isWhitespace).first else { return nil }
        let token = unquoteShellToken(String(rawToken))
        guard token != "latest", !token.hasPrefix("--") else { return nil }
        return PathURLs.fileURL(token)
    }

    private static func unquoteShellToken(_ token: String) -> String {
        if token.count >= 2,
           let first = token.first,
           let last = token.last,
           first == "'" && last == "'" || first == "\"" && last == "\"" {
            return String(token.dropFirst().dropLast())
        }
        return token
    }

    private static func preparedLanePackHandoff(
        payload: [String: Any],
        sessionsRoot: URL,
        freshnessReference: URL
    ) -> PreparedLanePackHandoff? {
        let expectedFocus = firstReviewFocus(payload)
        let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        if let command = nextCommands.compactMap({ string($0["command"]) }).first,
           let target = reviewLaneTarget(fromNextCommand: command, sessionsRoot: sessionsRoot) {
            return preparedLanePackHandoff(
                session: target.session,
                lane: target.lane,
                freshnessReference: freshnessReference,
                expectedFocus: expectedFocus
            )
        }

        guard let focus = expectedFocus else { return nil }
        let sessionID = string(focus["session_id"])
            ?? string(focus["session"]).map { URL(fileURLWithPath: $0).lastPathComponent }
            ?? ""
        guard !sessionID.isEmpty,
              let lane = focusLane(payload, focus: focus, sessionID: sessionID)
        else {
            return nil
        }

        let session = focusSessionURL(focus: focus, sessionID: sessionID, sessionsRoot: sessionsRoot)
        return preparedLanePackHandoff(
            session: session,
            lane: lane,
            freshnessReference: freshnessReference,
            expectedFocus: focus
        )
    }

    private static func preparedLanePackHandoff(
        session: URL,
        lane: String,
        freshnessReference: URL,
        expectedFocus: [String: Any]? = nil
    ) -> PreparedLanePackHandoff? {
        let lanePackDir = session.appendingPathComponent("derived/readiness/review-plan/lane-packs")
        let manifest = lanePackDir.appendingPathComponent("review_lane_pack.\(lane).json")
        guard FileManager.default.fileExists(atPath: manifest.path),
              let payload = try? JSONFiles.object(manifest),
              lanePackIsFresh(payload: payload, manifest: manifest, freshnessReference: freshnessReference)
        else {
            return nil
        }

        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        let audio = urlFromOutput(outputs["audio"])
        let markdown = urlFromOutput(outputs["markdown"])
        let answerSheet = urlFromOutput(outputs["answer_sheet"])
        let answerState = answerSheet.flatMap(answerSheetState)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let items = payload["items"] as? [[String: Any]] ?? []
        guard !items.isEmpty,
              lanePackItems(items, match: expectedFocus)
        else {
            return nil
        }
        let firstItem = items.first ?? [:]
        let applyCommand = "murmurmark review lane apply \(lane) --session \(PathDisplay.display(session))"
        let dryRunCommand = "\(applyCommand) --dry-run"
        let primary = answerState?.hasReviewedAnswers == true
            ? dryRunCommand
            : audio.map { "afplay \(shellQuote(PathDisplay.display($0)))" }
                ?? markdown.map { "less \(shellQuote(PathDisplay.display($0)))" }
                ?? answerSheet.map { "$EDITOR \(shellQuote(PathDisplay.display($0)))" }
        guard let command = primary else { return nil }

        return PreparedLanePackHandoff(
            command: command,
            session: session,
            sessionID: string(payload["session_id"]) ?? session.lastPathComponent,
            lane: lane,
            label: string(firstItem["label"]),
            action: string(firstItem["review_action"]) ?? lane,
            manifest: manifest,
            markdown: markdown,
            answerSheet: answerSheet,
            answerSheetStatus: answerState?.description,
            dryRunCommand: dryRunCommand,
            applyCommand: applyCommand,
            itemCount: int(summary["item_count"]),
            selectedRows: int(summary["selected_rows"]),
            durationSec: double(summary["duration_sec"])
        )
    }

    private static func lanePackItems(_ items: [[String: Any]], match focus: [String: Any]?) -> Bool {
        guard let focus,
              let expectedID = string(focus["source_audit_id"]),
              !expectedID.isEmpty
        else {
            return true
        }
        return items.contains { item in
            if string(item["source_audit_id"]) == expectedID {
                return true
            }
            if let ids = item["source_audit_ids"] as? [Any] {
                return ids.contains { string($0) == expectedID }
            }
            return false
        }
    }

    private static func reviewLaneTarget(fromNextCommand command: String, sessionsRoot: URL) -> ReviewLaneTarget? {
        let tokens = command
            .split(whereSeparator: \.isWhitespace)
            .map { unquoteShellToken(String($0)) }
        guard tokens.count >= 4,
              tokens[0] == "murmurmark",
              tokens[1] == "review",
              tokens[2] == "lane"
        else {
            return nil
        }
        let lane = tokens[3]
        guard !lane.hasPrefix("--"), lane != "apply" else {
            return nil
        }
        guard let sessionIndex = tokens.firstIndex(of: "--session"),
              sessionIndex + 1 < tokens.count
        else {
            return nil
        }
        let sessionArg = tokens[sessionIndex + 1]
        guard !sessionArg.isEmpty else {
            return nil
        }
        let session = sessionArg.contains("/")
            ? PathURLs.fileURL(sessionArg)
            : sessionsRoot.appendingPathComponent(sessionArg)
        return ReviewLaneTarget(sessionID: session.lastPathComponent, session: session, lane: lane)
    }

    private static func isStale(path: URL, comparedTo reference: URL) -> Bool {
        guard let pathModified = modificationDate(path),
              let referenceModified = modificationDate(reference)
        else {
            return false
        }
        return pathModified < referenceModified
    }

    private static func lanePackIsFresh(payload: [String: Any], manifest: URL, freshnessReference: URL) -> Bool {
        if let inputsCurrent = lanePackInputFingerprintsAreCurrent(payload) {
            return inputsCurrent
        }
        return !isStale(path: manifest, comparedTo: freshnessReference)
    }

    private static func lanePackInputFingerprintsAreCurrent(_ payload: [String: Any]) -> Bool? {
        guard let inputs = payload["inputs"] as? [String: Any],
              let fingerprints = inputs["fingerprints"] as? [String: Any]
        else {
            return nil
        }
        var checked = false
        for key in ["template", "decisions"] {
            guard let fingerprint = fingerprints[key] as? [String: Any] else {
                continue
            }
            checked = true
            if !fingerprintMatchesCurrentFile(fingerprint) {
                return false
            }
        }
        return checked ? true : nil
    }

    private static func fingerprintMatchesCurrentFile(_ fingerprint: [String: Any]) -> Bool {
        guard let path = string(fingerprint["path"]), !path.isEmpty else {
            return false
        }
        let expectedExists = bool(fingerprint["exists"]) ?? false
        let url = PathURLs.fileURL(path)
        let exists = FileManager.default.fileExists(atPath: url.path)
        guard exists == expectedExists else {
            return false
        }
        guard expectedExists else {
            return true
        }
        if let expectedSize = int(fingerprint["size"]),
           let actualSize = fileSize(url),
           expectedSize != actualSize {
            return false
        }
        if let expectedSHA = string(fingerprint["sha256"]), !expectedSHA.isEmpty {
            return sha256File(url) == expectedSHA
        }
        return true
    }

    private static func fileSize(_ url: URL) -> Int? {
        guard let values = try? url.resourceValues(forKeys: [.fileSizeKey]) else {
            return nil
        }
        return values.fileSize
    }

    private static func sha256File(_ url: URL) -> String? {
        guard let data = try? Data(contentsOf: url) else {
            return nil
        }
        let digest = SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }

    private static func modificationDate(_ path: URL) -> Date? {
        guard let values = try? path.resourceValues(forKeys: [.contentModificationDateKey]) else {
            return nil
        }
        return values.contentModificationDate
    }

    private static func focusSessionURL(focus: [String: Any], sessionID: String, sessionsRoot: URL) -> URL {
        if let sessionPath = string(focus["session"]), !sessionPath.isEmpty {
            return PathURLs.fileURL(sessionPath)
        }
        if sessionID.contains("/") {
            return PathURLs.fileURL(sessionID)
        }
        return sessionsRoot.appendingPathComponent(sessionID)
    }

    private static func urlFromOutput(_ value: Any?) -> URL? {
        guard let path = string(value), !path.isEmpty else { return nil }
        let url = PathURLs.fileURL(path)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    private struct AnswerSheetState {
        let total: Int
        let reviewed: Int

        var hasReviewedAnswers: Bool {
            reviewed > 0
        }

        var description: String {
            let status: String
            if total == 0 {
                status = "empty"
            } else if reviewed == 0 {
                status = "todo"
            } else if reviewed == total {
                status = "complete"
            } else {
                status = "partial"
            }
            return "\(status) reviewed=\(reviewed)/\(total)"
        }
    }

    private static func answerSheetState(url: URL) -> AnswerSheetState? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return nil
        }
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") {
                continue
            }
            guard line.hasPrefix("answers=") else {
                return nil
            }
            let answers = String(line.dropFirst("answers=".count))
                .filter { !$0.isWhitespace }
                .map { Character(String($0).lowercased()) }
            let reviewed = answers.filter { ![".", "n", "t"].contains($0) }.count
            return AnswerSheetState(total: answers.count, reviewed: reviewed)
        }
        return nil
    }

    private static func corpusReportCommand(sessionsRoot: URL) -> String {
        let root = PathDisplay.display(sessionsRoot)
        if root == "sessions" || root == "./sessions" {
            return "murmurmark report corpus"
        }
        return "murmurmark report corpus --sessions-root \(root)"
    }

    private static func printOperationalFocus(_ lanePack: PreparedLanePackHandoff) {
        let sessionArg = PathDisplay.display(lanePack.session)
        print("  focus_session: \(lanePack.sessionID)")
        if let label = lanePack.label {
            print("  focus_label: \(label)")
        }
        print("  focus_lane: \(lanePack.lane)")
        if let action = lanePack.action {
            print("  focus_action: \(action)")
        }
        print("  focus_next:")
        print("    murmurmark review next \(sessionArg)")
        print("    murmurmark review lane \(lanePack.lane) --session \(sessionArg)")
    }

    private static func printOperationalFocus(_ payload: [String: Any]) {
        if operationalReviewActionCount(payload) <= 0 {
            guard let first = firstNonActionableReviewTarget(payload) else { return }
            let sessionID = string(first["session_id"])
                ?? string(first["session"]).map { URL(fileURLWithPath: $0).lastPathComponent }
                ?? ""
            guard !sessionID.isEmpty else { return }
            let sessionArg = sessionID.hasPrefix("sessions/") || sessionID.hasPrefix("./sessions/")
                ? sessionID
                : "sessions/\(sessionID)"
            print("  focus_session: \(sessionID)")
            if let label = string(first["label"]) {
                print("  focus_label: \(label)")
            }
            print("  focus_reason: no_actionable_review_rows")
            if let action = string(first["recommended_action"]) {
                print("  focus_action: \(action)")
            }
            print("  focus_next:")
            print("    murmurmark status \(sessionArg)")
            print("    murmurmark report \(sessionArg)")
            return
        }
        guard let first = firstReviewFocus(payload) else { return }
        let sessionID = string(first["session_id"])
            ?? string(first["session"]).map { URL(fileURLWithPath: $0).lastPathComponent }
            ?? ""
        guard !sessionID.isEmpty else { return }
        print("  focus_session: \(sessionID)")
        if let label = string(first["label"]) {
            print("  focus_label: \(label)")
        }
        if let lane = focusLane(payload, focus: first, sessionID: sessionID) {
            print("  focus_lane: \(lane)")
        }
        if let action = focusAction(payload, focus: first, sessionID: sessionID) {
            print("  focus_action: \(action)")
        }
        if let reason = string(first["reason"]) {
            print("  focus_reason: \(reason)")
        }
        let sessionArg = sessionID.hasPrefix("sessions/") || sessionID.hasPrefix("./sessions/")
            ? sessionID
            : "sessions/\(sessionID)"
        print("  focus_next:")
        print("    murmurmark review next \(sessionArg)")
        if let lane = focusLane(payload, focus: first, sessionID: sessionID) {
            print("    murmurmark review lane \(lane) --session \(sessionArg)")
        } else {
            print("    murmurmark review first-lane --session \(sessionArg)")
        }
    }

    private static func operationalReviewActionCount(_ payload: [String: Any]) -> Int {
        let summary = payload["summary"] as? [String: Any] ?? [:]
        return int(summary["review_action_count"]) ?? int(summary["review_queue_items"]) ?? 0
    }

    private static func firstNonActionableReviewTarget(_ payload: [String: Any]) -> [String: Any]? {
        let plan = payload["promotion_plan"] as? [String: Any] ?? [:]
        let targets = plan["session_targets"] as? [[String: Any]] ?? []
        return targets.first { row in
            string(row["recommended_action"]) == "inspect_documented_non_actionable_blocker"
        }
    }

    private static func focusLane(_ payload: [String: Any], focus: [String: Any], sessionID: String) -> String? {
        if let lane = string(focus["review_lane"]) {
            return lane
        }
        let plan = payload["promotion_plan"] as? [String: Any] ?? [:]
        let reviewFocus = plan["review_focus"] as? [String: Any] ?? [:]
        if sameSession(reviewFocus["session_id"], sessionID) || sameSession(reviewFocus["session_arg"], sessionID),
           let lane = string(reviewFocus["review_lane"]) {
            return lane
        }
        let bySession = plan["review_queue_by_session"] as? [[String: Any]] ?? []
        for row in bySession where sameSession(row["session_id"], sessionID) || sameSession(row["session"], sessionID) {
            if let lane = string(row["first_review_lane"]) {
                return lane
            }
        }
        return nil
    }

    private static func focusAction(_ payload: [String: Any], focus: [String: Any], sessionID: String) -> String? {
        if let action = string(focus["review_action"]) {
            return action
        }
        guard let lane = focusLane(payload, focus: focus, sessionID: sessionID) else {
            return nil
        }
        return lane
    }

    private static func sameSession(_ value: Any?, _ sessionID: String) -> Bool {
        guard let text = string(value), !text.isEmpty else {
            return false
        }
        if text == sessionID {
            return true
        }
        return URL(fileURLWithPath: text).lastPathComponent == sessionID
    }

    private static func firstReviewFocus(_ payload: [String: Any]) -> [String: Any]? {
        let plan = payload["promotion_plan"] as? [String: Any] ?? [:]
        if let focus = plan["review_focus"] as? [String: Any],
           string(focus["session_id"]) != nil || string(focus["session_arg"]) != nil {
            return focus
        }
        if let queue = payload["review_queue"] as? [[String: Any]], let first = queue.first {
            return first
        }
        let rows = payload["session_review_burden"] as? [[String: Any]] ?? []
        return rows.first { string($0["use_gate"]) == "review_first" }
    }

    private static func shellQuote(_ value: String) -> String {
        if value.range(of: #"^[A-Za-z0-9_./:@%+=,-]+$"#, options: .regularExpression) != nil {
            return value
        }
        return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
    }

    private static func double(_ value: Any?) -> Double? {
        if let number = value as? NSNumber { return number.doubleValue }
        if let text = value as? String { return Double(text) }
        return nil
    }

    private static func int(_ value: Any?) -> Int? {
        if let number = value as? NSNumber { return number.intValue }
        if let text = value as? String { return Int(text) }
        return nil
    }

    private static func bool(_ value: Any?) -> Bool? {
        if let bool = value as? Bool { return bool }
        if let number = value as? NSNumber { return number.boolValue }
        if let text = value as? String {
            if ["true", "yes", "1"].contains(text.lowercased()) { return true }
            if ["false", "no", "0"].contains(text.lowercased()) { return false }
        }
        return nil
    }

}

private func corpusPrinterCompactJSON(_ value: Any) -> String {
    guard JSONSerialization.isValidJSONObject(value),
          let data = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]),
          let text = String(data: data, encoding: .utf8)
    else {
        return "\(value)"
    }
    return text
}

private func printFirstNextCommand(_ payload: [String: Any]) {
    if let command = firstNextCommand(payload) {
        print("  next_command: \(command)")
    }
}

private func firstNextCommand(_ payload: [String: Any]) -> String? {
    let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
    return nextCommands.compactMap { $0["command"] as? String }.first
}

enum SessionPaths {
    static func outputDirectory(from options: Options) throws -> URL {
        if let explicit = options.url("out") {
            return explicit
        }
        let root = options.url("sessions-root") ?? PathURLs.fileURL("sessions")
        return try uniqueDirectory(in: root)
    }

    private static func uniqueDirectory(in root: URL) throws -> URL {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone.current
        formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        let baseName = formatter.string(from: Date())

        for attempt in 0 ..< 1000 {
            let name = attempt == 0 ? baseName : "\(baseName)-\(attempt)"
            let candidate = root.appendingPathComponent(name)
            if !FileManager.default.fileExists(atPath: candidate.path) {
                return candidate
            }
        }

        throw CLIError("cannot create unique session directory under \(root.path)")
    }
}

enum StopReason: String {
    case durationElapsed = "duration_elapsed"
    case interrupt = "sigint"
    case terminated = "sigterm"
    case hangup = "sighup"
    case streamStopped = "stream_stopped"
    case captureStalled = "capture_stalled"

    var isSignal: Bool {
        self == .interrupt || self == .terminated || self == .hangup
    }

    var isExplicitStop: Bool {
        self == .interrupt || self == .durationElapsed
    }

    var isUnexpectedCaptureStop: Bool {
        self == .streamStopped || self == .captureStalled || self == .terminated || self == .hangup
    }
}

enum RecordingStopper {
    static func wait(duration: TimeInterval?) async throws -> StopReason {
        await withTaskGroup(of: StopReason.self) { group in
            group.addTask {
                await waitForSignal()
            }
            if let duration {
                group.addTask {
                    do {
                        try await Task.sleep(nanoseconds: UInt64(max(duration, 0.1) * 1_000_000_000))
                        return .durationElapsed
                    } catch {
                        return .terminated
                    }
                }
            }

            let reason = await group.next() ?? .durationElapsed
            group.cancelAll()
            return reason
        }
    }

    private static func waitForSignal() async -> StopReason {
        let bridgeBox = SignalBridgeBox()
        return await withTaskCancellationHandler {
            await withCheckedContinuation { continuation in
                let bridge = SignalBridge(continuation: continuation)
                bridgeBox.set(bridge)
                bridge.start()
            }
        } onCancel: {
            bridgeBox.cancel()
        }
    }
}

final class SignalBridgeBox: @unchecked Sendable {
    private let lock = NSLock()
    private var bridge: SignalBridge?
    private var cancelled = false

    func set(_ bridge: SignalBridge) {
        lock.lock()
        if cancelled {
            lock.unlock()
            bridge.cancel()
            return
        }
        self.bridge = bridge
        lock.unlock()
    }

    func cancel() {
        lock.lock()
        cancelled = true
        let active = bridge
        bridge = nil
        lock.unlock()
        active?.cancel()
    }
}

final class SignalBridge: @unchecked Sendable {
    private let continuation: CheckedContinuation<StopReason, Never>
    private let queue = DispatchQueue(label: "murmurmark.recording.signals")
    private let lock = NSLock()
    private var sources: [DispatchSourceSignal] = []
    private var didResume = false

    init(continuation: CheckedContinuation<StopReason, Never>) {
        self.continuation = continuation
    }

    func start() {
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        signal(SIGHUP, SIG_IGN)
        addSource(signal: SIGINT, reason: .interrupt)
        addSource(signal: SIGTERM, reason: .terminated)
        addSource(signal: SIGHUP, reason: .hangup)
    }

    private func addSource(signal: Int32, reason: StopReason) {
        let source = DispatchSource.makeSignalSource(signal: signal, queue: queue)
        source.setEventHandler { [self] in
            resume(reason)
        }
        sources.append(source)
        source.resume()
    }

    func cancel() {
        resume(.terminated)
    }

    private func resume(_ reason: StopReason) {
        lock.lock()
        guard !didResume else {
            lock.unlock()
            return
        }
        didResume = true
        let activeSources = sources
        sources.removeAll()
        lock.unlock()

        for source in activeSources {
            source.cancel()
        }
        restoreDefaults()
        continuation.resume(returning: reason)
    }

    private func restoreDefaults() {
        signal(SIGINT, SIG_DFL)
        signal(SIGTERM, SIG_DFL)
        signal(SIGHUP, SIG_DFL)
    }
}

final class CaptureFinalizationSignalGuard: @unchecked Sendable {
    private let queue = DispatchQueue(label: "murmurmark.capture.finalization.signals")
    private let lock = NSLock()
    private var sources: [DispatchSourceSignal] = []
    private var active = false

    func start() {
        lock.lock()
        guard !active else {
            lock.unlock()
            return
        }
        active = true
        lock.unlock()

        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        signal(SIGHUP, SIG_IGN)
        for signalNumber in [SIGINT, SIGTERM, SIGHUP] {
            let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: queue)
            source.setEventHandler {
                fputs("\ncapture finalization in progress; please wait\n", stderr)
                fflush(stderr)
            }
            lock.lock()
            sources.append(source)
            lock.unlock()
            source.resume()
        }
    }

    func cancel() {
        lock.lock()
        guard active else {
            lock.unlock()
            return
        }
        active = false
        let activeSources = sources
        sources.removeAll()
        lock.unlock()

        for source in activeSources {
            source.cancel()
        }
        signal(SIGINT, SIG_DFL)
        signal(SIGTERM, SIG_DFL)
        signal(SIGHUP, SIG_DFL)
    }
}

final class ChildProcessSignalForwarder: @unchecked Sendable {
    private let process: Process
    private let queue = DispatchQueue(label: "murmurmark.child-process.signals")
    private let lock = NSLock()
    private var sources: [DispatchSourceSignal] = []
    private var active = false

    init(process: Process) {
        self.process = process
    }

    func start() {
        lock.lock()
        guard !active else {
            lock.unlock()
            return
        }
        active = true
        lock.unlock()

        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        addSource(signal: SIGINT) { [process] in
            if process.isRunning {
                process.interrupt()
            }
        }
        addSource(signal: SIGTERM) { [process] in
            if process.isRunning {
                process.terminate()
            }
        }
    }

    private func addSource(signal: Int32, handler: @escaping @Sendable () -> Void) {
        let source = DispatchSource.makeSignalSource(signal: signal, queue: queue)
        source.setEventHandler(handler: handler)
        lock.lock()
        sources.append(source)
        lock.unlock()
        source.resume()
    }

    func cancel() {
        lock.lock()
        guard active else {
            lock.unlock()
            return
        }
        active = false
        let activeSources = sources
        sources.removeAll()
        lock.unlock()

        for source in activeSources {
            source.cancel()
        }
        signal(SIGINT, SIG_DFL)
        signal(SIGTERM, SIG_DFL)
    }
}

enum Tooling {
    static func which(_ name: String) -> String? {
        let paths = (ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").map(String.init)
        for path in paths {
            let candidate = URL(fileURLWithPath: path).appendingPathComponent(name).path
            if FileManager.default.isExecutableFile(atPath: candidate) {
                return candidate
            }
        }
        return nil
    }

    static func run(_ executable: String, _ arguments: [String]) throws {
        guard let path = which(executable) else {
            throw CLIError("\(executable) not found in PATH")
        }
        try runPath(URL(fileURLWithPath: path), arguments)
    }

    static func runCapturing(_ executable: String, _ arguments: [String]) throws -> String {
        guard let path = which(executable) else {
            throw CLIError("\(executable) not found in PATH")
        }
        return try runPathCapturing(URL(fileURLWithPath: path), arguments)
    }

    static func runPath(_ executable: URL, _ arguments: [String]) throws {
        let status = try runPathAllowingExitCodes(executable, arguments, allowedExitCodes: [0])
        guard status == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(status)")
        }
    }

    static func runPathForwardingInterrupts(_ executable: URL, _ arguments: [String]) throws {
        let status = try runPathForwardingInterruptsAllowingExitCodes(executable, arguments, allowedExitCodes: [0])
        guard status == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(status)")
        }
    }

    static func runPathForwardingInterrupts(
        _ executable: URL,
        _ arguments: [String],
        environmentOverrides: [String: String]
    ) throws {
        let status = try runPathForwardingInterruptsAllowingExitCodes(
            executable,
            arguments,
            allowedExitCodes: [0],
            environmentOverrides: environmentOverrides
        )
        guard status == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(status)")
        }
    }

    static func runPathQuiet(_ executable: URL, _ arguments: [String]) throws {
        let status = try runPathQuietAllowingExitCodes(executable, arguments, allowedExitCodes: [0])
        guard status == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(status)")
        }
    }

    static func runPathForwardingInterruptsAllowingExitCodes(
        _ executable: URL,
        _ arguments: [String],
        allowedExitCodes: Set<Int32>
    ) throws -> Int32 {
        try runPathForwardingInterruptsAllowingExitCodes(
            executable,
            arguments,
            allowedExitCodes: allowedExitCodes,
            environmentOverrides: [:]
        )
    }

    static func runPathForwardingInterruptsAllowingExitCodes(
        _ executable: URL,
        _ arguments: [String],
        allowedExitCodes: Set<Int32>,
        environmentOverrides: [String: String]
    ) throws -> Int32 {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw CLIError("executable not found: \(executable.path)")
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        if !environmentOverrides.isEmpty {
            var environment = ProcessInfo.processInfo.environment
            for (key, value) in environmentOverrides {
                environment[key] = value
            }
            process.environment = environment
        }
        try process.run()
        let signalForwarder = ChildProcessSignalForwarder(process: process)
        signalForwarder.start()
        process.waitUntilExit()
        signalForwarder.cancel()
        guard allowedExitCodes.contains(process.terminationStatus) else {
            throw CLIError("\(executable.lastPathComponent) exited with \(process.terminationStatus)")
        }
        return process.terminationStatus
    }

    static func runPathAllowingExitCodes(_ executable: URL, _ arguments: [String], allowedExitCodes: Set<Int32>) throws -> Int32 {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw CLIError("executable not found: \(executable.path)")
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        try process.run()
        process.waitUntilExit()
        guard allowedExitCodes.contains(process.terminationStatus) else {
            throw CLIError("\(executable.lastPathComponent) exited with \(process.terminationStatus)")
        }
        return process.terminationStatus
    }

    static func runPathQuietAllowingExitCodes(
        _ executable: URL,
        _ arguments: [String],
        allowedExitCodes: Set<Int32>
    ) throws -> Int32 {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw CLIError("executable not found: \(executable.path)")
        }
        let process = Process()
        let tempDir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let outputURL = tempDir.appendingPathComponent("murmurmark-\(UUID().uuidString).out")
        let errorURL = tempDir.appendingPathComponent("murmurmark-\(UUID().uuidString).err")
        FileManager.default.createFile(atPath: outputURL.path, contents: nil)
        FileManager.default.createFile(atPath: errorURL.path, contents: nil)
        let output = try FileHandle(forWritingTo: outputURL)
        let error = try FileHandle(forWritingTo: errorURL)
        defer {
            try? output.close()
            try? error.close()
            try? FileManager.default.removeItem(at: outputURL)
            try? FileManager.default.removeItem(at: errorURL)
        }
        process.executableURL = executable
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = output
        process.standardError = error
        try process.run()
        process.waitUntilExit()
        guard allowedExitCodes.contains(process.terminationStatus) else {
            try? output.close()
            try? error.close()
            let capturedOutput = quietProcessOutput(stdout: outputURL, stderr: errorURL)
            throw CLIError("\(executable.lastPathComponent) exited with \(process.terminationStatus)\(capturedOutput)")
        }
        return process.terminationStatus
    }

    static func runPathCapturing(_ executable: URL, _ arguments: [String]) throws -> String {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw CLIError("executable not found: \(executable.path)")
        }
        let process = Process()
        let stdout = Pipe()
        let stderr = Pipe()
        process.executableURL = executable
        process.arguments = arguments
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = stdout
        process.standardError = stderr
        try process.run()
        process.waitUntilExit()
        let output = stdout.fileHandleForReading.readDataToEndOfFile()
        let error = stderr.fileHandleForReading.readDataToEndOfFile()
        guard process.terminationStatus == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(process.terminationStatus)")
        }
        let combined = output + error
        return String(bytes: [UInt8](combined), encoding: .utf8) ?? ""
    }
}

private func quietProcessOutput(stdout: URL, stderr: URL) -> String {
    let output = [readQuietProcessFile(stderr), readQuietProcessFile(stdout)]
        .filter { !$0.isEmpty }
        .joined(separator: "\n")
    guard !output.isEmpty else { return "" }
    let limit = 4_000
    let tail = output.count > limit ? String(output.suffix(limit)) : output
    return "\n\(tail)"
}

private func readQuietProcessFile(_ url: URL) -> String {
    guard let data = try? Data(contentsOf: url),
          let text = String(data: data, encoding: .utf8)
    else {
        return ""
    }
    return text.trimmingCharacters(in: .whitespacesAndNewlines)
}

enum CaptureErrors {
    static func enrich(_ error: Error) -> Error {
        let detail = error.localizedDescription
        if looksLikePermissionError(detail) {
            return CLIError("""
            capture failed: \(detail)
            Grant Screen & System Audio Recording and Microphone permission to the terminal or Codex app, then run record again.
            """)
        }
        return error
    }

    private static func looksLikePermissionError(_ text: String) -> Bool {
        let lower = text.lowercased()
        return lower.contains("permission")
            || lower.contains("denied")
            || lower.contains("declined")
            || lower.contains("захват")
            || lower.contains("отклонил")
            || lower.contains("доступ")
    }
}

enum PermissionTexts {
    static func microphone(_ status: AVAuthorizationStatus) -> String {
        switch status {
        case .authorized:
            "ok"
        case .denied:
            "denied"
        case .restricted:
            "restricted"
        case .notDetermined:
            "not requested"
        @unknown default:
            "unknown"
        }
    }
}

final class EventLog {
    let url: URL
    private let handle: FileHandle
    private let encoder = JSONEncoder()

    init(url: URL) throws {
        self.url = url
        FileManager.default.createFile(atPath: url.path, contents: Data())
        handle = try FileHandle(forWritingTo: url)
        encoder.outputFormatting = [.sortedKeys]
    }

    func write(type: String, fields: [String: Any] = [:]) throws {
        var payload = fields
        payload["t"] = DateStrings.iso8601(Date())
        payload["type"] = type
        let data = try JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys])
        handle.write(data)
        handle.write(Data("\n".utf8))
    }

    deinit {
        try? handle.close()
    }
}

struct SessionManifest: Codable {
    let schema: String
    let sessionID: String
    let createdAt: String
    let endedAt: String?
    let appVersion: String
    let captureMode: String
    let status: String
    let target: TargetManifest
    let microphone: MicrophoneManifest
    let remoteAudio: AudioManifest
    let micAudio: AudioManifest
    let privacy: PrivacyManifest
    let files: [String: [FileEntry]]
    let health: HealthManifest

    enum CodingKeys: String, CodingKey {
        case schema
        case sessionID = "session_id"
        case createdAt = "created_at"
        case endedAt = "ended_at"
        case appVersion = "app_version"
        case captureMode = "capture_mode"
        case status
        case target
        case microphone
        case remoteAudio = "remote_audio"
        case micAudio = "mic_audio"
        case privacy
        case files
        case health
    }
}

struct TargetManifest: Codable {
    let kind: String
    let bundleID: String?
    let displayName: String
    let pidStrategy: String

    enum CodingKeys: String, CodingKey {
        case kind
        case bundleID = "bundle_id"
        case displayName = "display_name"
        case pidStrategy = "pid_strategy"
    }
}

struct MicrophoneManifest: Codable {
    let deviceUID: String
    let displayName: String
    let captureBackend: String

    enum CodingKeys: String, CodingKey {
        case deviceUID = "device_uid"
        case displayName = "display_name"
        case captureBackend = "capture_backend"
    }
}

struct AudioManifest: Codable {
    let backend: String
    let sampleRate: Int
    let channels: Int
    let format: String

    enum CodingKeys: String, CodingKey {
        case backend
        case sampleRate = "sample_rate"
        case channels
        case format
    }
}

struct PrivacyManifest: Codable {
    let networkAllowedDuringCapture: Bool
    let telemetry: Bool
    let rawAudioRetention: String

    enum CodingKeys: String, CodingKey {
        case networkAllowedDuringCapture = "network_allowed_during_capture"
        case telemetry
        case rawAudioRetention = "raw_audio_retention"
    }
}

struct FileEntry: Codable {
    let path: String
    let startHostTimeNs: UInt64
    let startSessionSec: Double
    let sampleRate: Int
    let frames: AVAudioFramePosition
    let channels: Int
    let bytes: Int64
    let sha256: String?

    enum CodingKeys: String, CodingKey {
        case path
        case startHostTimeNs = "start_host_time_ns"
        case startSessionSec = "start_session_sec"
        case sampleRate = "sample_rate"
        case frames
        case channels
        case bytes
        case sha256
    }
}

struct HealthManifest: Codable {
    let summary: String
    let warnings: [String]
    let stopReason: String?
    let partial: Bool?
    let explicitStop: Bool?
    let actualDurationSec: Double?
    let requestedDurationSec: Double?
    let screenCaptureRestartCount: Int?
    let tracks: [String: TrackHealthManifest]?

    enum CodingKeys: String, CodingKey {
        case summary
        case warnings
        case stopReason = "stop_reason"
        case partial
        case explicitStop = "explicit_stop"
        case actualDurationSec = "actual_duration_sec"
        case requestedDurationSec = "requested_duration_sec"
        case screenCaptureRestartCount = "screen_capture_restart_count"
        case tracks
    }
}

struct TrackHealthManifest: Codable {
    let frames: AVAudioFramePosition
    let sampleRate: Int
    let durationSec: Double
    let bytes: Int64
    let empty: Bool

    enum CodingKeys: String, CodingKey {
        case frames
        case sampleRate = "sample_rate"
        case durationSec = "duration_sec"
        case bytes
        case empty
    }

    init(frames: AVAudioFramePosition, sampleRate: Int, durationSec: Double, bytes: Int64, empty: Bool) {
        self.frames = frames
        self.sampleRate = sampleRate
        self.durationSec = durationSec
        self.bytes = bytes
        self.empty = empty
    }

    init(from file: FileEntry) {
        let duration = Double(file.frames) / Double(max(file.sampleRate, 1))
        self.init(
            frames: file.frames,
            sampleRate: file.sampleRate,
            durationSec: Double(round(duration * 1000) / 1000),
            bytes: file.bytes,
            empty: file.frames == 0 || file.bytes == 0
        )
    }
}

struct PipelineJob: Codable {
    let schema: String
    let sessionID: String
    let inputs: [String: String]
    let meetingContext: [String: [String]]
    let steps: [String]

    enum CodingKeys: String, CodingKey {
        case schema
        case sessionID = "session_id"
        case inputs
        case meetingContext = "meeting_context"
        case steps
    }

    static func `default`(for manifest: SessionManifest) -> PipelineJob {
        PipelineJob(
            schema: "murmurmark.pipeline_job/v1",
            sessionID: manifest.sessionID,
            inputs: [
                "mic": "audio/mic",
                "remote": "audio/remote",
                "manifest": "session.json",
            ],
            meetingContext: [
                "language": ["ru", "en"],
            ],
            steps: ["preprocess", "asr", "diarization", "speaker_resolution", "glossary_correction", "notes", "export", "retention"]
        )
    }

    func write(to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(to: url, options: .atomic)
    }
}

enum SessionIDs {
    static func make(from date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyy-MM-dd'T'HH-mm-ss'Z'"
        return formatter.string(from: date) + "_" + UUID().uuidString.prefix(6).lowercased()
    }
}

enum DateStrings {
    static func iso8601(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.string(from: date)
    }
}

struct CLIError: LocalizedError {
    let message: String
    init(_ message: String) {
        self.message = message
    }

    var errorDescription: String? {
        message
    }
}
