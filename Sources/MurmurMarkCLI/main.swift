import AppKit
import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

let murmurmarkVersion = "0.1.0"

@main
struct MurmurMark {
    static func main() async {
        do {
            var args = Array(CommandLine.arguments.dropFirst())
            guard let command = args.first else {
                printHelp()
                return
            }
            args.removeFirst()

            switch command {
            case "doctor":
                try await Commands.doctor()
            case "list-apps":
                Commands.listApps()
            case "list-audio-devices":
                Commands.listAudioDevices()
            case "record":
                try await Commands.record(args)
            case "latest":
                try PipelineCommands.latest(args)
            case "process":
                try PipelineCommands.process(args)
            case "report":
                try PipelineCommands.report(args)
            case "preprocess":
                try Commands.preprocess(args)
            case "reconcile-transcript":
                try Commands.reconcileTranscript(args)
            case "inspect":
                try Commands.inspect(args)
            case "export-audio":
                try Commands.exportAudio(args)
            case "version", "--version", "-v":
                print("murmurmark \(murmurmarkVersion)")
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
        MurmurMark \(murmurmarkVersion)

        Usage:
          murmurmark doctor
          murmurmark list-apps
          murmurmark list-audio-devices
          murmurmark record [--out ./session] [--duration 60] [--target-bundle com.example.App]
                            [--mic default] [--mic-backend screencapturekit|voice-processing]
                            [--remote-backend screencapturekit|audio-input] [--remote-device Device_UID]
          murmurmark latest [--sessions-root ./sessions]
          murmurmark process ./session|latest [--model ./model.bin] [--language ru] [--force-asr]
                                [--reuse-asr-cache] [--plan-only] [--sessions-root ./sessions]
          murmurmark report ./session|latest [--sessions-root ./sessions]
          murmurmark report corpus [--sessions-root ./sessions]
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
          process runs the current post-recording pipeline and prints the readiness summary.
          report refreshes and prints the readiness summary without rerunning ASR/audio processing.
        """)
    }
}

enum Commands {
    static func doctor() async throws {
        print("murmurmark: \(murmurmarkVersion)")
        print("macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
        print("swift capture backend: screencapturekit_system")
        print("ffmpeg: \(Tooling.which("ffmpeg") ?? "not found")")

        do {
            let content = try await SCShareableContent.current
            print("screen/system audio permission: ok")
            print("shareable displays: \(content.displays.count)")
            print("shareable applications: \(content.applications.count)")
        } catch {
            print("screen/system audio permission: not granted or blocked")
            print("screen/system audio detail: \(error.localizedDescription)")
            print("hint: grant Screen & System Audio Recording to the terminal or Codex app, then run record again")
        }

        let microphoneStatus = AVCaptureDevice.authorizationStatus(for: .audio)
        print("microphone permission: \(PermissionTexts.microphone(microphoneStatus))")
        print("microphones: \(AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone], mediaType: .audio, position: .unspecified).devices.count)")
        print("status: doctor completed")
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

enum PipelineCommands {
    static func latest(_ args: [String]) throws {
        var remaining = args
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &remaining) ?? "sessions")
        guard remaining.isEmpty else {
            throw CLIError("latest only supports --sessions-root")
        }
        let session = try SessionResolver.latest(in: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
    }

    static func process(_ args: [String]) throws {
        guard let target = args.first else { throw CLIError("process requires a session path or latest") }
        var forwarded = Array(args.dropFirst())
        let sessionsRoot = PathURLs.fileURL(ArgumentEditing.takeOption("sessions-root", from: &forwarded) ?? "sessions")
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        let python = try PythonRuntime.resolve()
        let script = PathURLs.fileURL("scripts/run-session-pipeline.py")
        guard FileManager.default.fileExists(atPath: script.path) else {
            throw CLIError("pipeline runner not found: \(script.path)")
        }

        var command = [script.path, session.path]
        if !ArgumentEditing.hasOption("murmurmark-bin", in: forwarded) {
            command += ["--murmurmark-bin", ExecutablePath.current()]
        }
        command += forwarded

        print("SESSION=\"\(PathDisplay.display(session))\"")
        fflush(stdout)
        try Tooling.runPath(python, command)
        try ReadinessPrinter.printSession(session)
    }

    static func report(_ args: [String]) throws {
        guard let target = args.first else { throw CLIError("report requires a session path, latest, or corpus") }
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
            let command = [script.path] + sessions.map(\.path) + [
                "--out-dir", "sessions/_reports/session-quality",
                "--write-session-readiness",
            ]
            try Tooling.runPath(python, command)
            try ReadinessPrinter.printCorpus(report: PathURLs.fileURL("sessions/_reports/session-quality/session_quality_report.json"))
            return
        }

        guard remaining.isEmpty else { throw CLIError("unexpected report arguments: \(remaining.joined(separator: " "))") }
        let session = try SessionResolver.resolve(target, sessionsRoot: sessionsRoot)
        print("SESSION=\"\(PathDisplay.display(session))\"")
        try Tooling.runPath(python, [
            script.path,
            session.path,
            "--out-dir", session.appendingPathComponent("derived/readiness/session-quality").path,
            "--write-session-readiness",
        ])
        try ReadinessPrinter.printSession(session)
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
            appVersion: murmurmarkVersion,
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
        if path.hasPrefix("/") {
            return URL(fileURLWithPath: path)
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent(path)
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
}

enum SessionResolver {
    static func resolve(_ value: String, sessionsRoot: URL) throws -> URL {
        if value == "latest" {
            return try latest(in: sessionsRoot)
        }
        let session = PathURLs.fileURL(value)
        guard FileManager.default.fileExists(atPath: session.appendingPathComponent("session.json").path) else {
            throw CLIError("session.json not found under \(session.path)")
        }
        return session
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
    static func printSession(_ session: URL) throws {
        let url = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            print("readiness: missing")
            print("hint: run `murmurmark report \(PathDisplay.display(session))`")
            return
        }
        let payload = try JSONFiles.object(url)
        let metrics = payload["metrics"] as? [String: Any] ?? [:]
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        let gate = string(payload["use_gate"]) ?? "unknown"
        let recommendation = string(payload["recommendation"]) ?? "unknown"
        let profile = string(payload["selected_profile"]) ?? "unknown"
        let verdict = string(payload["verdict"]) ?? "unknown"
        let reviewSeconds = double(metrics["review_burden_sec"]) ?? 0
        let reviewRatio = (double(metrics["review_burden_ratio"]) ?? 0) * 100

        print("")
        print("readiness:")
        print("  session: \(PathDisplay.display(session))")
        print("  gate: \(gate)")
        print("  recommendation: \(recommendation)")
        print("  selected_profile: \(profile)")
        print("  verdict: \(verdict)")
        print(String(format: "  review_burden: %.2f min / %.2f%%", reviewSeconds / 60, reviewRatio))
        print("  open:")
        for key in ["transcript", "notes", "quality_verdict", "audio_review_report", "local_recall_review"] {
            if let path = outputPath(key, outputs: outputs) {
                let target = path.hasPrefix("/") ? URL(fileURLWithPath: path) : session.appendingPathComponent(path)
                print("    \(key): \(PathDisplay.display(target))")
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

    private static func outputPath(_ key: String, outputs: [String: Any]) -> String? {
        guard let item = outputs[key] as? [String: Any] else { return nil }
        guard (item["exists"] as? Bool) == true else { return nil }
        return item["path"] as? String
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
        guard FileManager.default.isExecutableFile(atPath: executable.path) else {
            throw CLIError("executable not found: \(executable.path)")
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw CLIError("\(executable.lastPathComponent) exited with \(process.terminationStatus)")
        }
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
