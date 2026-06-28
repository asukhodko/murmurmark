import AppKit
import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

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
            case "list-apps":
                Commands.listApps()
            case "list-audio-devices":
                Commands.listAudioDevices()
            case "record":
                try await Commands.record(args)
            case "latest":
                try PipelineCommands.latest(args)
            case "sessions":
                try PipelineCommands.sessions(args)
            case "process":
                try PipelineCommands.process(args)
            case "status":
                try PipelineCommands.status(args)
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
          murmurmark record --target-bundle system
          murmurmark process latest
          murmurmark next latest
          murmurmark status latest
          # follow printed review commands when the gate is review_first
          murmurmark export latest --format markdown --include-json
          murmurmark retention plan latest

        Usage:
          murmurmark doctor [--strict]
          murmurmark list-apps
          murmurmark list-audio-devices
          murmurmark record [--out ./session] [--duration 60] [--target-bundle com.example.App]
                            [--mic default] [--mic-backend screencapturekit|voice-processing]
                            [--remote-backend screencapturekit|audio-input] [--remote-device Device_UID]
          murmurmark sessions [--limit 10] [--status exportable|review_required|incomplete] [--path-only|--next-only|--json]
                              [--sessions-root ./sessions]
          murmurmark latest [--sessions-root ./sessions]
          murmurmark process ./session|latest [--model ./model.bin] [--language ru] [--prompt-file ./prompt.txt]
                                [--force-asr] [--reuse-asr-cache] [--plan-only] [--skip-build]
                                [--skip-preprocess] [--skip-transcription] [--skip-audits] [--skip-cleanup]
                                [--progress-interval-sec 60] [--config murmurmark.config.json] [--sessions-root ./sessions]
          murmurmark status [./session|latest] [--sessions-root ./sessions]
          murmurmark next [./session|latest] [--refresh] [--export-manifest ./export_manifest.json] [--sessions-root ./sessions]
          murmurmark open [./session|latest] [--kind notes|transcript|verdict|readiness|audio-review] [--path-only|--command-only|--cat]
          murmurmark report ./session|latest [--sessions-root ./sessions]
          murmurmark report corpus [--sessions-root ./sessions]
          murmurmark review plan|progress|apply|first-lane|lane|next
          murmurmark review lane apply LANE|first [--session latest|SESSION] [--answers-source manual|suggested]
          murmurmark review agent [--session-quality sessions/_reports/session-quality/session_quality_report.json]
          murmurmark review workspace [build|apply] [--session latest|./session]
          murmurmark review ./session|latest [--lane fast_confirm_drop] [--no-play]
          murmurmark audit local-recall ./session|latest [--profile shadow_v2] [--sessions-root ./sessions]
          murmurmark audit order ./session|latest [--profile auto] [--sessions-root ./sessions]
          murmurmark audit group-overlaps ./session|latest [--profile shadow_v2] [--write-clips] [--sessions-root ./sessions]
          murmurmark audit audio-review ./session|latest [--profile audit_cleanup_v2] [--write-clips] [--sessions-root ./sessions]
          murmurmark cleanup ./session|latest [--input-profile shadow_v2] [--output-profile audit_cleanup_v1]
                             [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair order ./session|latest [--input-profile auto] [--output-profile order_repair_v1]
                                [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair local-recall ./session|latest [--input-profile auto] [--output-profile local_recall_repair_v1]
                                       [--mode conservative] [--sessions-root ./sessions]
          murmurmark repair remote-leak ./session|latest [--sessions-root ./sessions]
          murmurmark synthesize ./session|latest [--transcript-profile auto] [--sessions-root ./sessions]
          murmurmark notes ./session|latest [--kind notes|verdict|review-items|evidence] [--profile auto|current|NAME] [--path-only|--cat]
          murmurmark transcript ./session|latest [--profile auto] [--path-only|--cat] [--sessions-root ./sessions]
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
          murmurmark corpus report
          murmurmark export ./session|latest [--format markdown|obsidian] [--profile auto] [--out-dir exports/private]
                             [--include-json] [--force] [--sessions-root ./sessions]
          murmurmark retention plan|apply ./session|latest [--policy examples/retention-policy.local-first.json]
                             [--export-manifest ./exports/private/session/export_manifest.json]
                             [--confirm-delete-raw] [--sessions-root ./sessions]
          murmurmark retention payload ./session|latest [--policy examples/retention-policy.local-first.json]
                             [--export-manifest ./exports/private/session/export_manifest.json] [--provider name]
          murmurmark config print [--config murmurmark.config.json]
          murmurmark preprocess ./session [--echo diagnostic|clean] [--echo-engine linear_baseline|local_fir|speexdsp|webrtc-apm]
                              [--echo-policy preserve_local|role_safe|strict_silence]
          murmurmark reconcile-transcript ./session [--in ./transcript.rich.json] [--out ./transcript.rich.json]
          murmurmark inspect ./session
          murmurmark export-audio ./session [--sample-rate 16000]

        Notes:
          record defaults to ScreenCaptureKit for separate mic and remote tracks.
          audio-input remote capture and voice-processing mic capture are experimental comparison modes.
          It writes mic.caf, remote.caf, session.json, events.jsonl and pipeline_job.json.
          Without --duration, recording runs until Ctrl-C or SIGTERM and finalizes the session.
          Without --out, recording creates a unique directory under ./sessions.
          sessions lists recent session packages and their readiness state.
          process runs the current post-recording pipeline and prints the readiness summary.
          status prints the current readiness dashboard without recomputing reports.
          next prints the single recommended next command from readiness.
          open prints or streams the selected local output artifact from readiness.
          report refreshes and prints the readiness summary without rerunning ASR/audio processing.
          review wraps the current review-plan, agent-review, review CLI, progress and apply scripts.
          audit wraps the transcript order, local recall, group overlap and audio-review audit scripts through the project Python runtime.
          cleanup wraps conservative audit cleanup profiles.
          repair wraps explicit structural transcript repairs into separate profiles.
          synthesize refreshes deterministic extractive notes and quality verdict.
          notes prints or streams the selected notes/verdict artifacts.
          transcript prints or streams the selected transcript path.
          corpus wraps regression-corpus, audio-judge, corpus gates and operational-readiness scripts.
          export creates a local user-facing Markdown or Obsidian bundle and blocks readiness export blockers by default.
          retention plans or applies local retention policy; raw deletion requires apply plus --confirm-delete-raw.
          config shows local defaults loaded by process/export.
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

        do {
            let content = try await SCShareableContent.current
            report.check(.passed, "screen/system audio permission", "ok")
            print("shareable displays: \(content.displays.count)")
            print("shareable applications: \(content.applications.count)")
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
        let options = try Options(args)
        let out = try SessionPaths.outputDirectory(from: options)
        let duration = try options.optionalPositiveDouble("duration")
        let targetBundle = options.string("target-bundle")
        let microphone = options.string("mic") ?? "default"
        let microphoneBackend = try MicrophoneCaptureBackend.parse(options.string("mic-backend") ?? "screencapturekit")
        let remoteBackend = try RemoteCaptureBackend.parse(options.string("remote-backend") ?? "screencapturekit")
        let remoteDevice = options.string("remote-device")
        let sampleRate = options.int("sample-rate") ?? 48000
        let channelCount = options.int("channels") ?? 2

        let recorder = SessionRecorder(
            outputDirectory: out,
            targetBundleID: targetBundle,
            microphoneID: microphone,
            microphoneBackend: microphoneBackend,
            remoteBackend: remoteBackend,
            remoteDeviceID: remoteDevice,
            duration: duration,
            sampleRate: sampleRate,
            channelCount: channelCount
        )
        try await recorder.run()
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
        print("  murmurmark record --target-bundle system")
        print("  murmurmark process latest")
        print("  murmurmark status latest")
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
                    hint: "copy murmurmark.config.example.json when you want local defaults"
                )
            }
        } catch {
            report.check(.fail, "config", error.localizedDescription, hint: "fix or remove murmurmark.config.json")
        }
    }

    static func checkScripts(_ report: inout DoctorReport) {
        for path in [
            "scripts/run-session-pipeline.py",
            "scripts/transcribe-simple-whispercpp.py",
            "scripts/synthesize-simple-extractive.py",
            "scripts/audit-local-recall.py",
            "scripts/audit-transcript-order.py",
            "scripts/audit-group-overlaps.py",
            "scripts/build-audio-review-pack.py",
            "scripts/audit-audio-review-pack.py",
            "scripts/report-session-quality.py",
            "scripts/apply-retention-policy.py",
            "scripts/build-provider-payload-manifest.py",
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
        fflush(stdout)
        try Tooling.runPath(python, command)
        let planOnly = ArgumentEditing.hasOption("plan-only", in: forwarded)
        try ReadinessPrinter.printSession(session, label: planOnly ? "existing_readiness" : "readiness")
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
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/report-session-quality.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("session quality reporter not found: \(script.path)")
        }

        if target == "corpus" {
            guard remaining.isEmpty else { throw CLIError("report corpus only supports --sessions-root") }
            let sessions = try SessionResolver.all(in: sessionsRoot)
            guard !sessions.isEmpty else { throw CLIError("no sessions with session.json found under \(sessionsRoot.path)") }
            let reportsRoot = sessionsRoot.appendingPathComponent("_reports")
            let sessionQualityOut = reportsRoot.appendingPathComponent("session-quality")
            let operationalReadinessOut = reportsRoot.appendingPathComponent("operational-readiness")
            let command = [script.path] + sessions.map(\.path) + [
                "--out-dir", sessionQualityOut.path,
                "--write-session-readiness",
            ]
            try Tooling.runPathQuiet(python, command)
            try Tooling.runPathQuiet(python, [
                PathURLs.fileURL("scripts/report-operational-readiness.py").path,
                "--session-quality", sessionQualityOut.appendingPathComponent("session_quality_report.json").path,
                "--corpus-evaluation", reportsRoot.appendingPathComponent("regression-corpus/regression_corpus_evaluation.json").path,
                "--audio-judge", reportsRoot.appendingPathComponent("audio-judge-v0/audio_judge_v0_report.json").path,
                "--audio-judge-queue", reportsRoot.appendingPathComponent("audio-judge-v0/audio_judge_v0_queue_predictions.jsonl").path,
                "--out-dir", operationalReadinessOut.path,
            ])
            try ReadinessPrinter.printCorpus(report: sessionQualityOut.appendingPathComponent("session_quality_report.json"))
            try CorpusPrinter.printOperationalReadiness(outDir: operationalReadinessOut)
            return
        }

        guard remaining.isEmpty else { throw CLIError("unexpected report arguments: \(remaining.joined(separator: " "))") }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        try Tooling.runPathQuiet(python, [
            script.path,
            session.path,
            "--out-dir", session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
        try ReadinessPrinter.printSession(session)
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
        usage: murmurmark sessions [--limit 10] [--all] [--status exportable|review_required|incomplete|blocked|missing_readiness]
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
        """)
    }

    static func printProcess() {
        Swift.print("""
        usage: murmurmark process ./session|latest [--model ./model.bin] [--language ru] [--prompt-file ./prompt.txt]
                                [--force-asr] [--reuse-asr-cache] [--plan-only] [--skip-build]
                                [--skip-preprocess] [--skip-transcription] [--skip-audits] [--skip-cleanup]
                                [--progress-interval-sec 60] [--config murmurmark.config.json] [--sessions-root ./sessions]

        Runs scripts/run-session-pipeline.py for one recorded session, then prints readiness.
        Defaults come from murmurmark.config.json when present; explicit CLI flags win.
        The --skip-* flags are for debugging or refreshing only selected derived layers.

        Common:
          murmurmark process latest
          murmurmark process ./sessions/<id> --plan-only --skip-build
          murmurmark process ./sessions/<id> --progress-interval-sec 30
        """)
    }

    static func printStatus() {
        Swift.print("""
        usage: murmurmark status [./session|latest] [--sessions-root ./sessions]

        Prints the current session readiness dashboard without recomputing reports.
        Defaults to latest when no session is provided.

        Common:
          murmurmark status
          murmurmark status latest
          murmurmark status ./sessions/<id>
        """)
    }

    static func printNext() {
        Swift.print("""
        usage: murmurmark next [./session|latest] [--refresh] [--export-manifest ./export_manifest.json] [--sessions-root ./sessions]

        Prints the single recommended next command from session_readiness.json.
        Defaults to latest when no session is provided. Use --refresh to update readiness first
        without rerunning ASR, Echo Guard or audits. If a successful export manifest exists, the
        command follows its post-export handoff, usually retention planning.

        Common:
          murmurmark next
          murmurmark next latest
          murmurmark next ./sessions/<id> --export-manifest ./exports/private/<id>/export_manifest.json
          murmurmark next ./sessions/<id> --refresh
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
            "items": shown.map(jsonItem),
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
        let readiness = readReadiness(session)
        let sessionPath = PathDisplay.display(session)
        Swift.print("    - session: \(sessionPath)")
        Swift.print("      status: \(status(for: session, readiness: readiness))")
        Swift.print("      gate: \(string(readiness?["use_gate"]) ?? "missing")")
        Swift.print("      profile: \(string(readiness?["selected_profile"]) ?? "unknown")")
        Swift.print("      verdict: \(string(readiness?["verdict"]) ?? "unknown")")
        Swift.print("      next: \(nextCommand(for: session, readiness: readiness))")
    }

    private static func jsonItem(_ session: URL) -> [String: Any] {
        let readiness = readReadiness(session)
        let readinessURL = session.appendingPathComponent("derived/readiness/session_readiness.json")
        return [
            "session": PathDisplay.display(session),
            "session_id": session.lastPathComponent,
            "readiness_exists": readiness != nil,
            "readiness_path": PathDisplay.display(readinessURL),
            "status": status(for: session, readiness: readiness),
            "gate": string(readiness?["use_gate"]) ?? "missing",
            "profile": string(readiness?["selected_profile"]) ?? "unknown",
            "verdict": string(readiness?["verdict"]) ?? "unknown",
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

    private static func status(for _: URL, readiness: [String: Any]?) -> String {
        guard let readiness else { return "missing_readiness" }
        let gate = string(readiness["use_gate"]) ?? "unknown"
        let exportBlockers = (readiness["export_blockers"] as? [Any] ?? []).map { String(describing: $0) }
        let reviewBlockers = (readiness["review_blockers"] as? [Any] ?? []).map { String(describing: $0) }
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains("pipeline_incomplete") {
            return "incomplete"
        }
        if gate == "ready_for_notes" && exportBlockers.isEmpty {
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
        if let readiness {
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

    private static func string(_ value: Any?) -> String? {
        value as? String
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
            return
        }
        let selectedTarget = targets[0]
        print("  selected: \(selectedTarget.id)")
        print("  path: \(PathDisplay.display(selectedTarget.url))")
        print("  command: \(selectedTarget.command)")
        print("  recommended_next: \(selectedTarget.command)")
        print("  next:")
        print("    \(selectedTarget.command)")
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
                if let session {
                    print("SESSION=\"\(PathDisplay.display(session))\"")
                }
                try ReviewPrinter.printApplyNotReady(session: session, decisions: decisions, progress: progress)
                return
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
        if hasPreparedReviewPlan(planOut) {
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
        let exportBlockers = readiness["export_blockers"] as? [Any] ?? []
        return gate == "review_first" || !reviewBlockers.isEmpty || !exportBlockers.isEmpty
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
               murmurmark review lane LANE [--session latest|SESSION]
               murmurmark review lane apply LANE|first [--session latest|SESSION] [--answers-file PATH|--answers TEXT]
                                    [--answers-source manual|suggested]
               murmurmark review workspace [build|apply] [--session latest|SESSION] [--answers-source review|suggested]
               murmurmark review SESSION|latest [--lane LANE] [--no-play]
               murmurmark review progress [--session latest|SESSION]
               murmurmark review apply [--session latest|SESSION]
               murmurmark review agent

        Review turns audit evidence into explicit decisions, applies those decisions into a
        separate reviewed transcript profile, and refreshes readiness reports.

        Common flow:
          murmurmark review next latest
          murmurmark review first-lane --session latest
          # listen/edit the generated answer sheet
          murmurmark review lane apply first --session latest
          murmurmark review apply --session latest
        """)
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
        } else if context.progress.map(isReadyForApply) == true {
            print("  recommended_next: murmurmark review apply\(sessionArgument)")
            print("  next:")
            print("    murmurmark review apply\(sessionArgument)")
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

        Audit commands are local-only wrappers over existing Python scripts:
          local-recall    runs audit-local-recall.py
          order           runs audit-transcript-order.py
          group-overlaps  runs audit-group-overlaps.py
          audio-review    runs build-audio-review-pack.py, then audit-audio-review-pack.py

        Use --sessions-root when resolving latest from a non-default sessions directory.
        Extra options are forwarded to the underlying audit script; for audio-review they are
        forwarded to the pack builder, then the pack audit runs over the resulting directory.
        """)
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
        print("  missing_islands: \(int(summary["audited_missing_island_count"]))")
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
          murmurmark repair remote-leak ./session|latest [--sessions-root ./sessions]

        order writes a separate transcript profile with conservative transcript-order repairs.
        local-recall writes a separate transcript profile with conservative inserted Me islands.
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
        let paths = artifactPaths(session: session, profile: resolvedProfile)
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
        let reviewNext = shouldReview(verdictPayload) ? "murmurmark review next \(PathDisplay.display(session))" : nil
        let openCommand = "less \(PathDisplay.display(url))"
        print("  selected: \(kind)")
        print("  recommended_next: \(reviewNext ?? openCommand)")
        print("  next:")
        if let reviewNext {
            print("    \(reviewNext)")
        }
        print("    \(openCommand)")
    }

    private static func selectedProfile(_ requested: String, session: URL) throws -> String {
        if requested != "auto" {
            return requested
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

    private static func shouldReview(_ verdictPayload: [String: Any]?) -> Bool {
        guard let verdictPayload else { return false }
        let reviewSummary = verdictPayload["review_summary"] as? [String: Any] ?? [:]
        let reviewCount = int(reviewSummary["review_item_count"]) ?? 0
        let riskItems = verdictPayload["risk_items"] as? [Any] ?? []
        let verdict = verdictPayload["verdict"] as? String ?? ""
        return reviewCount > 0 || !riskItems.isEmpty || verdict == "usable_with_review"
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
        let url = transcriptURL(profile: profile, session: session)
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
        let reviewNext = shouldReview(verdictPayload) ? "murmurmark review next \(PathDisplay.display(session))" : nil
        let openCommand = "less \(PathDisplay.display(url))"
        print("  recommended_next: \(reviewNext ?? openCommand)")
        print("  next:")
        if let reviewNext {
            print("    \(reviewNext)")
        }
        print("    \(openCommand)")
    }

    private static func selectedProfile(_ requested: String, session: URL) throws -> String {
        if requested != "auto" {
            return requested
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

    private static func shouldReview(_ verdictPayload: [String: Any]?) -> Bool {
        guard let verdictPayload else { return false }
        let reviewSummary = verdictPayload["review_summary"] as? [String: Any] ?? [:]
        let reviewCount = int(reviewSummary["review_item_count"]) ?? 0
        let riskItems = verdictPayload["risk_items"] as? [Any] ?? []
        let verdict = verdictPayload["verdict"] as? String ?? ""
        return reviewCount > 0 || !riskItems.isEmpty || verdict == "usable_with_review"
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
        print("  recommended_next: murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)")
        print("  next:")
        print("    murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)")
        print("    murmurmark report \(PathDisplay.display(session))")
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
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
        print("  recommended_next: murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)")
        print("  next:")
        print("    murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)")
        print("    murmurmark transcript \(PathDisplay.display(session)) --profile \(profile)")
        print("    murmurmark report \(PathDisplay.display(session))")
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
        print("  recommended_next: murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)")
        print("  next:")
        print("    murmurmark synthesize \(PathDisplay.display(session)) --transcript-profile \(profile)")
        print("    murmurmark transcript \(PathDisplay.display(session)) --profile \(profile)")
        print("    murmurmark report \(PathDisplay.display(session))")
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
        print("  recommended_next: less \(PathDisplay.display(reportURL))")
        print("  next:")
        print("    less \(PathDisplay.display(reportURL))")
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
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

        print("")
        print("synthesis:")
        print("  quality_verdict: \(PathDisplay.display(verdictURL))")
        print("  notes: \(PathDisplay.display(outDir.appendingPathComponent("notes.md")))")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        print("  risk_items: \(riskItems.count)")
        ReviewSummaryPrinter.printReviewSummary(payload["review_summary"], indent: "  ")
        if let needsReview = intOptional(metrics["needs_review_count"]) {
            print("  needs_review_count: \(needsReview)")
        }
        if let overlapSeconds = doubleOptional(metrics["cross_role_overlap_gt2_seconds"]) {
            print(String(format: "  cross_role_overlap_gt2_seconds: %.2f", overlapSeconds))
        }
        let recommendedNext = needsReview
            ? "murmurmark review next \(sessionPath)"
            : "murmurmark notes \(sessionPath)"
        print("  recommended_next: \(recommendedNext)")
        print("  next:")
        if needsReview {
            print("    murmurmark review next \(sessionPath)")
        }
        print("    murmurmark notes \(sessionPath)")
        print("    murmurmark transcript \(sessionPath)")
        print("    murmurmark report \(sessionPath)")
        if canSuggestExport {
            print("    murmurmark export \(sessionPath) --format markdown --include-json")
        }
    }

    private static func dict(_ value: Any?) -> [String: Any] {
        value as? [String: Any] ?? [:]
    }

    private static func string(_ value: Any?) -> String? {
        value as? String
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

enum CorpusCommands {
    static func corpus(_ args: [String]) throws {
        if args.isEmpty || ArgumentEditing.hasHelpFlag([args.first ?? ""]) {
            CorpusHelp.print()
            return
        }
        guard let subcommand = args.first else {
            throw CLIError(
                "corpus requires process, build, evaluate, train-audio-judge, taxonomy, gate, order, " +
                    "local-recall, local-recall-repair, remote-leak, or report"
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
            try gates(extraArgs: [], allowedExitCodes: [0, 1])
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
            try gates(extraArgs: forwarded)
            try CorpusPrinter.printGates(outDir: outDir)
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
        case "local-recall":
            try CorpusLocalRecallCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
        case "local-recall-repair":
            try CorpusLocalRecallRepairCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
        case "remote-leak":
            try CorpusRemoteLeakCommands.run(args: forwarded, sessionsRoot: sessionsRoot)
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
        try gates(extraArgs: extraArgs, allowedExitCodes: [0])
    }

    private static func gates(extraArgs: [String], allowedExitCodes: Set<Int32>) throws {
        let python = try PythonRuntime.resolve()
        _ = try Tooling.runPathQuietAllowingExitCodes(python, [
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
          murmurmark corpus remote-leak [all|latest|./session...] [--plan] [--sessions-root ./sessions]
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
        """)
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

        print("")
        print("retention:")
        print("  plan: \(PathDisplay.display(planURL))")
        print("  mode: \(string(payload["mode"]) ?? "unknown")")
        print("  raw_audio_files: \(actions.count)")
        print("  actions: \(compactJSON(actionCounts))")
        if !appliedCounts.isEmpty {
            print("  applied_actions: \(compactJSON(appliedCounts))")
        }
        print("  can_apply: \(bool(payload["can_apply"]))")
        print("  applied: \(bool(payload["applied"]))")
        if let exportManifest {
            print("  export_manifest: \(PathDisplay.display(exportManifest))")
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
        usage: murmurmark config print [--config murmurmark.config.json]

        Config lookup order:
          1. --config PATH
          2. MURMURMARK_CONFIG
          3. ./murmurmark.config.json when it exists

        Local config is ignored by git. Start from:
          cp murmurmark.config.example.json murmurmark.config.json
        """)
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
            Swift.print("  next: cp murmurmark.config.example.json murmurmark.config.json")
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
        guard let first = args.first(where: { !$0.hasPrefix("--") }) else { throw CLIError("inspect requires a session path") }
        let showEcho = args.contains("--echo")
        let session = PathURLs.fileURL(first)
        let data = try Data(contentsOf: session.appendingPathComponent("session.json"))
        let manifest = try JSONDecoder().decode(SessionManifest.self, from: data)

        print("session_id: \(manifest.sessionID)")
        print("status: \(manifest.status)")
        print("capture_mode: \(manifest.captureMode)")
        print("created_at: \(manifest.createdAt)")
        print("ended_at: \(manifest.endedAt ?? "-")")
        print("health: \(manifest.health.summary)")

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

final class SessionRecorder: NSObject, SCStreamOutput, SCStreamDelegate {
    let outputDirectory: URL
    let targetBundleID: String?
    let microphoneID: String
    let microphoneBackend: MicrophoneCaptureBackend
    let remoteBackend: RemoteCaptureBackend
    let remoteDeviceID: String?
    let duration: TimeInterval?
    let sampleRate: Int
    let channelCount: Int

    private let fileManager = FileManager.default
    private let queue = DispatchQueue(label: "murmurmark.capture.samples")
    private let stateQueue = DispatchQueue(label: "murmurmark.capture.state")
    private var stream: SCStream?
    private var micWriter: AudioFileWriter?
    private var voiceProcessingMic: VoiceProcessingMicCapture?
    private var remoteWriter: AudioFileWriter?
    private var remoteInputCapture: AudioInputDeviceCapture?
    private var events: EventLog?
    private var warnings: [String] = []
    private var targetDisplayName = "System Audio"
    private var targetPIDStrategy = "screen_capture_filter"
    private var startDate = Date()
    private var stopDate: Date?

    init(
        outputDirectory: URL,
        targetBundleID: String?,
        microphoneID: String,
        microphoneBackend: MicrophoneCaptureBackend,
        remoteBackend: RemoteCaptureBackend,
        remoteDeviceID: String?,
        duration: TimeInterval?,
        sampleRate: Int,
        channelCount: Int
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
    }

    func run() async throws {
        try prepareDirectories()
        let eventLog = try EventLog(url: outputDirectory.appendingPathComponent("events.jsonl"))
        events = eventLog
        do {
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
                ]
            )

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
                let content = try await SCShareableContent.current
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
                config.excludesCurrentProcessAudio = true
                config.sampleRate = sampleRate
                config.channelCount = channelCount
                config.captureMicrophone = microphoneBackend == .screenCaptureKit
                if microphoneBackend == .screenCaptureKit, microphoneID != "default" {
                    config.microphoneCaptureDeviceID = microphoneID
                }

                let stream = SCStream(filter: filter, configuration: config, delegate: self)
                self.stream = stream
                if remoteBackend == .screenCaptureKit {
                    try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
                }
                if microphoneBackend == .screenCaptureKit {
                    try stream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: queue)
                }
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
            try await stream?.startCapture()
            if let duration {
                print("recording \(String(format: "%.1f", duration))s -> \(outputDirectory.path)")
            } else {
                print("recording until Ctrl-C -> \(outputDirectory.path)")
            }
            let stopReason = try await RecordingStopper.wait(duration: duration)
            if stopReason.isSignal {
                print("\nstopping...")
            }
            try await stream?.stopCapture()
            try remoteInputCapture?.stop()
            try voiceProcessingMic?.stop()
            stopDate = Date()
            try eventLog.write(type: "capture.stopped", fields: ["reason": stopReason.rawValue])
            try finish()
            print("done")
            printHandoff()
        } catch {
            try? remoteInputCapture?.stop()
            try? voiceProcessingMic?.stop()
            try? eventLog.write(type: "capture.failed", fields: ["error": error.localizedDescription])
            try? fileManager.removeItem(at: outputDirectory.appendingPathComponent("session.lock"))
            throw CaptureErrors.enrich(error)
        }
    }
}

extension SessionRecorder {
    func stream(_: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard CMSampleBufferDataIsReady(sampleBuffer), CMSampleBufferGetNumSamples(sampleBuffer) > 0 else {
            return
        }

        do {
            switch type {
            case .audio:
                if remoteWriter == nil {
                    remoteWriter = try AudioFileWriter(url: outputDirectory.appendingPathComponent("audio/remote/000001.caf"), source: "remote")
                }
                try remoteWriter?.write(sampleBuffer)
            case .microphone:
                if micWriter == nil {
                    micWriter = try AudioFileWriter(url: outputDirectory.appendingPathComponent("audio/mic/000001.caf"), source: "mic")
                }
                try micWriter?.write(sampleBuffer)
            default:
                break
            }
        } catch {
            stateQueue.sync {
                warnings.append("write failed for \(type): \(error.localizedDescription)")
            }
        }
    }

    func stream(_: SCStream, didStopWithError error: Error) {
        stateQueue.sync {
            warnings.append("stream stopped with error: \(error.localizedDescription)")
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
        addSilenceWarning(
            source: "mic",
            file: outputDirectory.appendingPathComponent(micInfo.path),
            to: &finalWarnings
        )
        addSilenceWarning(
            source: "remote",
            file: outputDirectory.appendingPathComponent(remoteInfo.path),
            to: &finalWarnings
        )

        let endedAt = stopDate ?? Date()
        let manifest = SessionManifest(
            schema: "murmurmark.session/v1",
            sessionID: SessionIDs.make(from: startDate),
            createdAt: DateStrings.iso8601(startDate),
            endedAt: DateStrings.iso8601(endedAt),
            appVersion: MurmurMark.version,
            captureMode: captureMode,
            status: finalWarnings.isEmpty ? "completed" : "completed_with_warnings",
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
                summary: finalWarnings.isEmpty ? "ok" : "warning",
                warnings: finalWarnings
            )
        )

        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(manifest).write(to: outputDirectory.appendingPathComponent("session.json"), options: .atomic)
        try PipelineJob.default(for: manifest).write(to: outputDirectory.appendingPathComponent("pipeline_job.json"))
        try? fileManager.removeItem(at: outputDirectory.appendingPathComponent("session.lock"))
        try events?.write(type: "manifest.written", fields: ["health": manifest.health.summary])
    }

    private func printHandoff() {
        let session = PathDisplay.display(outputDirectory)
        print("SESSION=\"\(session)\"")
        print("recommended_next: murmurmark process \(session)")
        print("next:")
        print("  murmurmark process \(session)")
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

    private func addSilenceWarning(source: String, file: URL, to warnings: inout [String]) {
        guard FileManager.default.fileExists(atPath: file.path),
              let rmsDb = try? AudioLevelProbe.rmsDb(url: file),
              rmsDb < -65
        else {
            return
        }

        warnings.append("\(source) track appears silent or almost silent (RMS \(AudioLevelProbe.formatDb(rmsDb)))")
    }

    private func microphoneName(for id: String) -> String {
        if id == "default" { return "System Default Microphone" }
        let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone], mediaType: .audio, position: .unspecified).devices
        return devices.first { $0.uniqueID == id }?.localizedName ?? id
    }
}

final class AudioFileWriter {
    let url: URL
    let source: String
    private var file: AVAudioFile?
    private(set) var framesWritten: AVAudioFramePosition = 0

    init(url: URL, source: String) throws {
        self.url = url
        self.source = source
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

    func write(_ sampleBuffer: CMSampleBuffer) throws {
        guard let format = Self.audioFormat(from: sampleBuffer) else {
            throw CLIError("cannot read audio format for \(source)")
        }
        try write(sampleBuffer, format: format)
    }

    func write(_ sampleBuffer: CMSampleBuffer, format: AVAudioFormat) throws {
        let frameCount = AVAudioFrameCount(CMSampleBufferGetNumSamples(sampleBuffer))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else {
            throw CLIError("cannot allocate PCM buffer for \(source)")
        }
        buffer.frameLength = frameCount

        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sampleBuffer,
            at: 0,
            frameCount: Int32(frameCount),
            into: buffer.mutableAudioBufferList
        )
        guard status == noErr else {
            throw CLIError("cannot copy PCM data for \(source): OSStatus \(status)")
        }

        if file == nil {
            do {
                file = try AVAudioFile(forWriting: url, settings: format.settings)
            } catch {
                throw CLIError("cannot create audio file for \(source): \(error.localizedDescription)")
            }
        }
        do {
            try file?.write(from: buffer)
        } catch {
            throw CLIError("cannot write audio buffer for \(source): \(error.localizedDescription)")
        }
        framesWritten += AVAudioFramePosition(frameCount)
    }

    func close() {
        file = nil
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
    let decision: LocalFIRHelperDecision
    let metrics: EchoSuppressionMetrics
    let warnings: [EchoSuppressionWarning]
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
        guard FileManager.default.fileExists(atPath: url.path) else {
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
        let command = exportHandoff?.command ?? readinessCommand

        print("")
        print("next:")
        print("  status: \(status)")
        print("  command: \(command)")
        print("  source: \(exportHandoff == nil ? "readiness" : "export_manifest")")
        print("  gate: \(gate)")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        if let manifest = exportHandoff?.manifest {
            print("  export_manifest: \(PathDisplay.display(manifest))")
        }
        if let firstOpen = openCommands.compactMap({ string($0["command"]) }).first {
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

    private static func successfulExportHandoff(session: URL, explicitManifest: URL?) -> (command: String, manifest: URL)? {
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
        guard FileManager.default.fileExists(atPath: url.path) else {
            print("\(label): missing")
            let sessionPath = PathDisplay.display(session)
            print("  session: \(sessionPath)")
            print("  expected: \(PathDisplay.display(url))")
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
        let status = readinessStatus(gate: gate, payload: payload)
        let recommendedNext = string(payload["recommended_next"]) ?? preferredNextCommand(nextCommands)
        let reviewSeconds = double(metrics["review_burden_sec"]) ?? 0
        let reviewRatio = (double(metrics["review_burden_ratio"]) ?? 0) * 100

        print("")
        print("\(label):")
        print("  session: \(PathDisplay.display(session))")
        print("  status: \(status)")
        if let recommendedNext {
            print("  recommended_next: \(recommendedNext)")
        }
        printHandoff(status: status, session: session, outputs: outputs)
        print("  gate: \(gate)")
        print("  recommendation: \(recommendation)")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        print(String(format: "  review_burden: %.2f min / %.2f%%", reviewSeconds / 60, reviewRatio))
        ReviewSummaryPrinter.printSynthesisReviewMetrics(metrics, indent: "  ")
        print("  open:")
        if openCommands.isEmpty {
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
        if nextCommands.isEmpty {
            print("    none")
        } else {
            for item in nextCommands {
                guard let command = string(item["command"]), !command.isEmpty else { continue }
                let label = string(item["label"]) ?? string(item["id"]) ?? "next"
                print("    \(command) — \(label)")
            }
        }
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
    }

    static func preferredNextCommand(_ nextCommands: [[String: Any]]) -> String? {
        let commands = nextCommands.compactMap { string($0["command"]) }.filter { !$0.isEmpty }
        let actionPrefixes = [
            "murmurmark process",
            "murmurmark review",
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

    private static func outputPath(_ key: String, outputs: [String: Any]) -> String? {
        guard let item = outputs[key] as? [String: Any] else { return nil }
        guard (item["exists"] as? Bool) == true else { return nil }
        return item["path"] as? String
    }

    private static func printHandoff(status: String, session: URL, outputs: [String: Any]) {
        var commands: [(String, String)] = []
        appendOpenCommand("open_notes", outputKey: "notes", session: session, outputs: outputs, to: &commands)
        appendOpenCommand("open_transcript", outputKey: "transcript", session: session, outputs: outputs, to: &commands)
        appendOpenCommand("open_verdict", outputKey: "quality_verdict", session: session, outputs: outputs, to: &commands)
        if status == "exportable" {
            let sessionPath = PathDisplay.display(session)
            commands.append(("export", "murmurmark export \(sessionPath) --format markdown --include-json"))
            commands.append(("retention", "murmurmark retention plan \(sessionPath)"))
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
                    "label": "Export a local Markdown handoff bundle.",
                    "command": "murmurmark export \(sessionPath) --format markdown --include-json",
                ],
                [
                    "label": "Inspect local retention/privacy actions.",
                    "command": "murmurmark retention plan \(sessionPath)",
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
        if gate.hasPrefix("pipeline_incomplete") || exportBlockers.contains("pipeline_incomplete") {
            return "incomplete"
        }
        if gate == "ready_for_notes" && exportBlockers.isEmpty {
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
        let synthesisReviewCount = int(metrics["synthesis_review_item_count"]) ?? 0
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
        Swift.print(String(format: "  review_burden: %.2f min / %.2f%%", reviewSeconds / 60.0, reviewRatio))
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
        if let recommended = focusedCommands.first {
            Swift.print("  recommended_next: \(recommended)")
        }
        printReviewFlowsIfPlanExists(sessionArg: sessionPath, planOutDir: planOutDir)
        Swift.print("  next:")
        for command in focusedCommands {
            Swift.print("    \(command)")
        }
    }

    private static func printReviewFlowsIfPlanExists(sessionArg: String, planOutDir: URL) {
        guard FileManager.default.fileExists(atPath: planOutDir.appendingPathComponent("review_plan.json").path) else {
            return
        }
        let firstLane = firstRecommendedLane(planOutDir: planOutDir) ?? "first"
        Swift.print("  first_lane_flow:")
        Swift.print("    build_and_listen: murmurmark review first-lane --session \(sessionArg)")
        Swift.print("    apply_answers: murmurmark review lane apply \(firstLane) --session \(sessionArg)")
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
            return sessionLocalReviewCommands(sessionArg: sessionPath, planOutDir: planOutDir)
        }
        let commands = rows.compactMap { string($0["command"]) }.filter { !$0.isEmpty }
        let reviewCommands = commands.filter { $0.contains("murmurmark review") }
        if !reviewCommands.isEmpty {
            return reviewCommands
        }
        if gate == "ready_for_notes" {
            return [
                "murmurmark export \(sessionPath) --format markdown --include-json",
                "murmurmark retention plan \(sessionPath)",
            ]
        }
        if gate.hasPrefix("pipeline_incomplete") {
            return ["murmurmark process \(sessionPath)"]
        }
        return commands.isEmpty ? ["less \(sessionPath)/derived/readiness/session_readiness.md"] : commands
    }

    private static func sessionLocalReviewCommands(sessionArg: String, planOutDir: URL) -> [String] {
        let firstLane = firstRecommendedLane(planOutDir: planOutDir) ?? "first"
        var commands = [
            "murmurmark review first-lane --session \(sessionArg)",
            "murmurmark review lane apply \(firstLane) --session \(sessionArg)",
        ]
        if let quickLane = quickRecommendedLane(planOutDir: planOutDir), quickLane != firstLane {
            commands += [
                "murmurmark review lane \(quickLane) --session \(sessionArg)",
                "murmurmark review lane apply \(quickLane) --session \(sessionArg)",
            ]
        }
        commands += [
            "murmurmark review workspace --session \(sessionArg)",
            "murmurmark review workspace apply --session \(sessionArg)",
            "murmurmark review progress --session \(sessionArg)",
            "murmurmark review apply --session \(sessionArg)",
        ]
        return commands
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
        print("")
        print("regression_corpus:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("regression_corpus.md")))")
        print("  sessions: \(int(payload["session_count"]) ?? 0)")
        print("  items: \(int(payload["item_count"]) ?? 0)")
        if let labels = payload["by_label"] as? [String: Any] {
            print("  by_label: \(corpusPrinterCompactJSON(labels))")
        }
        if let skipped = payload["skipped_sessions"] as? [[String: Any]], !skipped.isEmpty {
            print("  skipped_sessions: \(skipped.count)")
        }
    }

    static func printEvaluation(outDir: URL = PathURLs.fileURL("sessions/_reports/regression-corpus")) throws {
        let url = outDir.appendingPathComponent("regression_corpus_evaluation.json")
        let payload = try JSONFiles.object(url)
        print("")
        print("regression_evaluation:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("regression_corpus_evaluation.md")))")
        print("  readiness: \(string(payload["readiness"]) ?? "unknown")")
        print("  sessions: \(int(payload["session_count"]) ?? 0)")
        print("  items: \(int(payload["item_count"]) ?? 0)")
        if let missing = payload["missing_labels"] as? [Any], !missing.isEmpty {
            print("  missing_labels: \(corpusPrinterCompactJSON(missing))")
        }
    }

    static func printAudioJudge(outDir: URL = PathURLs.fileURL("sessions/_reports/audio-judge-v0")) throws {
        let url = outDir.appendingPathComponent("audio_judge_v0_report.json")
        let payload = try JSONFiles.object(url)
        let training = payload["training"] as? [String: Any] ?? [:]
        let evaluation = payload["evaluation"] as? [String: Any] ?? [:]
        let queue = payload["review_queue"] as? [String: Any] ?? [:]
        print("")
        print("audio_judge:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("audio_judge_v0_report.md")))")
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
    }

    static func printTaxonomy(outDir: URL = PathURLs.fileURL("sessions/_reports/audio-error-taxonomy")) throws {
        let url = outDir.appendingPathComponent("audio_error_taxonomy_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("audio_error_taxonomy:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("audio_error_taxonomy_report.md")))")
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
    }

    static func printGates(outDir: URL = PathURLs.fileURL("sessions/_reports/corpus-gates")) throws {
        let url = outDir.appendingPathComponent("corpus_gates_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("corpus_gates:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("corpus_gates_report.md")))")
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
    }

    static func printTranscriptOrder(outDir: URL = PathURLs.fileURL("sessions/_reports/transcript-order")) throws {
        let url = outDir.appendingPathComponent("transcript_order_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("transcript_order_corpus:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("transcript_order_corpus_report.md")))")
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
        print("  next: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printFirstNextCommand(payload)
    }

    static func printLocalRecallCorpus(outDir: URL = PathURLs.fileURL("sessions/_reports/local-recall")) throws {
        let url = outDir.appendingPathComponent("local_recall_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("local_recall_corpus:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("local_recall_corpus_report.md")))")
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
        print("  next: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printFirstNextCommand(payload)
    }

    static func printLocalRecallRepairCorpus(outDir: URL = PathURLs.fileURL("sessions/_reports/local-recall-repair")) throws {
        let url = outDir.appendingPathComponent("local_recall_repair_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("local_recall_repair_corpus:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("local_recall_repair_corpus_report.md")))")
        print("  repaired_sessions: \(int(summary["repaired_session_count"]) ?? 0) / \(int(summary["session_count"]) ?? 0)")
        print("  sessions_with_repairs: \(int(summary["sessions_with_repairs"]) ?? 0)")
        print("  applied_repairs: \(int(summary["applied_repairs"]) ?? 0)")
        print("  reviewable_applied_repairs: \(int(summary["reviewable_applied_repairs"]) ?? 0)")
        print("  incomplete_applied_repairs: \(int(summary["incomplete_applied_repairs"]) ?? 0)")
        if let seconds = double(summary["inserted_me_seconds"]) {
            print(String(format: "  inserted_me_seconds: %.2f", seconds))
        }
        print("  rejected_items: \(int(summary["rejected_items"]) ?? 0)")
        print("  next: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printFirstNextCommand(payload)
    }

    static func printRemoteLeakSegment(outDir: URL = PathURLs.fileURL("sessions/_reports/remote-leak-segment")) throws {
        let url = outDir.appendingPathComponent("remote_leak_segment_corpus_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("remote_leak_segment_corpus:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("remote_leak_segment_corpus_report.md")))")
        print("  planned_sessions: \(int(summary["planned_session_count"]) ?? 0) / \(int(summary["session_count"]) ?? 0)")
        print("  missing_plans: \(int(summary["missing_plan_count"]) ?? 0)")
        print("  items: \(int(summary["item_count"]) ?? 0)")
        print("  protect_local_content_items: \(int(summary["protect_local_content_items"]) ?? 0)")
        print("  reviewable_protect_local_content_items: \(int(summary["reviewable_protect_local_content_items"]) ?? 0)")
        print("  incomplete_protect_local_content_items: \(int(summary["incomplete_protect_local_content_items"]) ?? 0)")
        if let seconds = double(summary["protect_local_content_seconds"]) {
            print(String(format: "  protect_local_content_seconds: %.2f", seconds))
        }
        print("  next: \(string(summary["recommended_next_step"]) ?? "unknown")")
        printFirstNextCommand(payload)
    }

    static func printOperationalReadiness(
        outDir: URL = PathURLs.fileURL("sessions/_reports/operational-readiness")
    ) throws {
        let url = outDir.appendingPathComponent("operational_readiness_report.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let useGates = summary["use_gates"] as? [String: Any] ?? [:]
        let reviewSeconds = double(summary["total_review_burden_sec"]) ?? 0
        print("")
        print("operational_readiness:")
        print("  report: \(PathDisplay.display(outDir.appendingPathComponent("operational_readiness_report.md")))")
        print("  verdict: \(string(payload["operational_verdict"]) ?? "unknown")")
        print("  sessions_in_scope: \(int(summary["session_count"]) ?? 0)")
        print("  sessions_excluded: \(int(summary["excluded_diagnostic_session_count"]) ?? 0)")
        print("  sessions_ready_for_notes: \(int(useGates["ready_for_notes"]) ?? 0)")
        print("  sessions_review_first: \(int(useGates["review_first"]) ?? 0)")
        print(String(format: "  review_minutes: %.2f", reviewSeconds / 60))
        print("  review_actions: \(int(summary["review_action_count"]) ?? int(summary["review_queue_items"]) ?? 0)")
        print("  grouped_review_rows: \(int(summary["grouped_review_row_count"]) ?? 0)")
        printFirstNextCommand(payload)
        printOperationalFocus(payload)
    }

    private static func printOperationalFocus(_ payload: [String: Any]) {
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
        print("    murmurmark review first-lane --session \(sessionArg)")
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
        if let queue = payload["review_queue"] as? [[String: Any]], let first = queue.first {
            return first
        }
        let rows = payload["session_review_burden"] as? [[String: Any]] ?? []
        return rows.first { string($0["use_gate"]) == "review_first" }
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
    let nextCommands = payload["next_commands"] as? [[String: Any]] ?? []
    if let command = nextCommands.compactMap({ $0["command"] as? String }).first {
        print("  next_command: \(command)")
    }
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

    var isSignal: Bool {
        self == .interrupt || self == .terminated
    }
}

enum RecordingStopper {
    static func wait(duration: TimeInterval?) async throws -> StopReason {
        if let duration {
            try await Task.sleep(nanoseconds: UInt64(max(duration, 0.1) * 1_000_000_000))
            return .durationElapsed
        }

        return await withCheckedContinuation { continuation in
            let bridge = SignalBridge(continuation: continuation)
            bridge.start()
        }
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
        addSource(signal: SIGINT, reason: .interrupt)
        addSource(signal: SIGTERM, reason: .terminated)
    }

    private func addSource(signal: Int32, reason: StopReason) {
        let source = DispatchSource.makeSignalSource(signal: signal, queue: queue)
        source.setEventHandler { [self] in
            resume(reason)
        }
        sources.append(source)
        source.resume()
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
        continuation.resume(returning: reason)
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

    static func runPathQuiet(_ executable: URL, _ arguments: [String]) throws {
        let status = try runPathQuietAllowingExitCodes(executable, arguments, allowedExitCodes: [0])
        guard status == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(status)")
        }
    }

    static func runPathAllowingExitCodes(_ executable: URL, _ arguments: [String], allowedExitCodes: Set<Int32>) throws -> Int32 {
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw CLIError("executable not found: \(executable.path)")
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
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
