import Foundation

enum ReviewPrinter {
    static func printLanePack(lane: String, outDir: URL, session: URL? = nil, planOutDir: URL? = nil) throws {
        let manifestURL = outDir.appendingPathComponent("review_lane_pack.\(lane).json")
        let payload = try JSONFiles.object(manifestURL)
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("review_lane_pack:")
        print("  lane: \(lane)")
        print("  manifest: \(PathDisplay.display(manifestURL))")
        if let audio = string(outputs["audio"]) {
            print("  audio: \(audio)")
        }
        if let markdown = string(outputs["markdown"]) {
            print("  markdown: \(markdown)")
        }
        if let answerSheet = string(outputs["answer_sheet"]) {
            print("  answer_sheet: \(answerSheet)")
        }
        if let suggested = string(outputs["suggested_answer_sheet"]) {
            print("  suggested_answer_sheet: \(suggested)")
            if let suggestedLine = firstAnswersLine(url: PathURLs.fileURL(suggested)) {
                print("  suggested_answers: \(suggestedLine)")
            }
        }
        print("  items: \(int(summary["item_count"]) ?? 0)")
        print("  skipped: \(int(summary["skipped_count"]) ?? 0)")
        let applyCommand = laneApplyCommand(lane: lane, session: session, planOutDir: planOutDir, outDir: outDir)
        if let audio = string(outputs["audio"]) {
            print("  listen: afplay \(shellQuote(audio))")
        }
        if let answerSheet = string(outputs["answer_sheet"]) {
            print("  edit: $EDITOR \(shellQuote(answerSheet))")
        }
        print("  dry_run: \(applyCommand) --dry-run")
        print("  apply: \(applyCommand)")
        if string(outputs["suggested_answer_sheet"]) != nil {
            print("  suggested_dry_run: \(applyCommand) --answers-source suggested --dry-run")
            print("  suggested_apply: \(applyCommand) --answers-source suggested")
        }
        print("  next: listen, edit answer_sheet, then apply")
    }

    private static func laneApplyCommand(lane: String, session: URL?, planOutDir: URL?, outDir: URL) -> String {
        var parts = ["murmurmark", "review", "lane", "apply", lane]
        if let session {
            parts += ["--session", session.lastPathComponent]
        }
        if let planOutDir {
            let defaultPlan = session?.appendingPathComponent("derived/readiness/review-plan")
                ?? PathURLs.fileURL("sessions/_reports/review-plan")
            if !samePath(planOutDir, defaultPlan) {
                parts += ["--plan-out-dir", PathDisplay.display(planOutDir)]
            }
            let defaultOut = planOutDir.appendingPathComponent("lane-packs")
            if !samePath(outDir, defaultOut) {
                parts += ["--out-dir", PathDisplay.display(outDir)]
            }
        }
        return parts.joined(separator: " ")
    }

    private static func samePath(_ lhs: URL, _ rhs: URL) -> Bool {
        lhs.standardizedFileURL.path == rhs.standardizedFileURL.path
    }

    private static func firstAnswersLine(url: URL) -> String? {
        guard let text = try? String(contentsOf: url, encoding: .utf8) else {
            return nil
        }
        for rawLine in text.split(whereSeparator: \.isNewline) {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.isEmpty || line.hasPrefix("#") {
                continue
            }
            return line
        }
        return nil
    }

    private static func shellQuote(_ value: String) -> String {
        if value.range(of: #"^[A-Za-z0-9_./:@%+=,-]+$"#, options: .regularExpression) != nil {
            return value
        }
        return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    static func printPlan() throws {
        let url = PathURLs.fileURL("sessions/_reports/review-plan/review_plan.json")
        let payload = try JSONFiles.object(url)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let strategy = payload["review_queue_strategy"] as? [String: Any] ?? [:]
        let commands = strategy["commands"] as? [String: Any] ?? [:]
        let firstLane = string(strategy["first_recommended_lane"]) ?? "fast_confirm_drop"
        print("")
        print("review_plan:")
        print("  report: sessions/_reports/review-plan/review_plan.md")
        print("  clusters: \(int(summary["cluster_count"]) ?? 0)")
        print("  raw_items: \(int(summary["raw_item_count"]) ?? 0)")
        print("  sessions_with_review: \(int(summary["sessions_with_review"]) ?? 0)")
        print("  estimated_listen_minutes: \(double(summary["estimated_listen_minutes"]) ?? 0)")
        if let lanes = summary["by_review_lane"] as? [String: Any] {
            print("  by_lane: \(compactJSON(lanes))")
        }
        print("  first_lane: \(firstLane)")
        print("  next:")
        print("    murmurmark review workspace")
        print("    murmurmark review latest --lane \(firstLane)")
        if let buildFirstLanePack = string(commands["build_first_lane_pack"]) {
            print("    \(buildFirstLanePack)")
        }
    }

    static func printProgress(report: URL = PathURLs.fileURL("sessions/_reports/review-plan/review_decisions_progress.json")) throws {
        let payload = try JSONFiles.object(report)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let markdown = report.deletingPathExtension().appendingPathExtension("md")
        print("")
        print("review_progress:")
        print("  report: \(PathDisplay.display(markdown))")
        print("  reviewed: \(int(summary["reviewed"]) ?? 0)/\(int(summary["total"]) ?? 0)")
        print("  remaining: \(int(summary["remaining"]) ?? 0)")
        print("  remaining_minutes: \(double(summary["remaining_minutes"]) ?? 0)")
        print("  invalid_rows: \(int(summary["invalid_rows"]) ?? 0)")
        print("  ready_for_apply: \(bool(summary["ready_for_batch_apply"]) ?? false)")
        if let decisions = summary["decisions"] as? [String: Any] {
            print("  decisions: \(compactJSON(decisions))")
        }
        printProgressLanes(payload)
        let sessionID = sessionIDForSessionLocalPlan(report.deletingLastPathComponent())
        if bool(summary["ready_for_batch_apply"]) == true {
            if let sessionID {
                print("  next: murmurmark review apply --session \(sessionID)")
            } else {
                print("  next: murmurmark review apply")
            }
        } else if let sessionID {
            print("  next: murmurmark review workspace --session \(sessionID)")
        } else {
            print("  next: murmurmark review workspace")
        }
    }

    private static func printProgressLanes(_ payload: [String: Any]) {
        let lanes = payload["by_lane"] as? [[String: Any]] ?? []
        guard !lanes.isEmpty else { return }
        print("  by_lane:")
        for lane in lanes {
            let name = string(lane["review_lane"]) ?? "unknown"
            let total = int(lane["total"]) ?? 0
            let reviewed = int(lane["reviewed"]) ?? 0
            let remaining = int(lane["remaining"]) ?? 0
            let minutes = (double(lane["remaining_seconds"]) ?? 0) / 60
            print(String(format: "    %@: reviewed=%d/%d remaining=%d minutes=%.2f", name, reviewed, total, remaining, minutes))
        }
    }

    static func printApply(report: URL) throws {
        let payload = try JSONFiles.object(report)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let sessions = payload["sessions"] as? [[String: Any]] ?? []
        let failedSessions = int(summary["failed_sessions"]) ?? 0
        let failedRefreshSteps = int(summary["failed_refresh_steps"]) ?? 0
        print("")
        print("review_apply:")
        print("  report: \(PathDisplay.display(report))")
        print("  sessions: \(int(summary["session_count"]) ?? 0)")
        print("  passed_sessions: \(int(summary["passed_sessions"]) ?? 0)")
        print("  failed_sessions: \(failedSessions)")
        print("  failed_refresh_steps: \(failedRefreshSteps)")
        if failedSessions > 0 || failedRefreshSteps > 0 {
            print("  next: less \(PathDisplay.display(report))")
        } else if let session = singleAppliedSession(sessions) {
            print("  next: murmurmark report \(session)")
            try printAppliedSessionReadiness(session)
        } else if !sessions.isEmpty {
            print("  next: murmurmark report corpus")
        }
    }

    static func printAgentBuild(report: URL) throws {
        let payload = try JSONFiles.object(report)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        print("")
        print("agent_review:")
        print("  report: \(PathDisplay.display(report))")
        print("  profile: \(string(payload["profile"]) ?? "agent_reviewed_v1")")
        print("  decision_rows: \(int(summary["decision_rows"]) ?? 0)")
        print("  rejected_candidate_rows: \(int(summary["rejected_candidate_rows"]) ?? 0)")
        if let byDecision = summary["by_decision"] {
            print("  by_decision: \(compactJSON(byDecision))")
        }
        if let decisions = string(outputs["decisions"]) {
            print("  decisions: \(PathDisplay.display(PathURLs.fileURL(decisions)))")
        }
        if let template = string(outputs["template"]) {
            print("  template: \(PathDisplay.display(PathURLs.fileURL(template)))")
        }
    }

    static func printWorkspace(outDir: URL = PathURLs.fileURL("sessions/_reports/review-plan")) throws {
        let url = outDir.appendingPathComponent("review_workspace.json")
        let payload = try JSONFiles.object(url)
        let lanes = payload["lanes"] as? [[String: Any]] ?? []
        let okLanes = lanes.filter { string($0["status"]) == "ok" }
        let itemCount = okLanes.reduce(0) { $0 + (int($1["items"]) ?? 0) }
        let durationSeconds = okLanes.reduce(0.0) { $0 + (double($1["duration_sec"]) ?? 0) }
        var byLane: [String: Int] = [:]
        for lane in okLanes {
            if let name = string(lane["lane"]) {
                byLane[name] = int(lane["items"]) ?? 0
            }
        }

        print("")
        print("review_workspace:")
        print("  index: \(PathDisplay.display(outDir.appendingPathComponent("review_workspace.md")))")
        print("  lanes: \(lanes.count)")
        print("  ok_lanes: \(okLanes.count)")
        print("  items: \(itemCount)")
        print(String(format: "  listen_minutes: %.2f", durationSeconds / 60))
        print("  by_lane: \(compactJSON(byLane))")
        if !okLanes.isEmpty {
            print("  lane_packs:")
            for lane in okLanes {
                printWorkspaceLane(lane)
            }
        }
        let sessionID = sessionIDForSessionLocalPlan(outDir)
        let applyCommand = workspaceApplyCommand(payload: payload, outDir: outDir, sessionID: sessionID)
        if okLanes.contains(where: { string($0["suggested_answer_sheet"]) != nil }) {
            print("  suggested_dry_run: \(applyCommand) --answers-source suggested --dry-run")
            print("  suggested_apply: \(applyCommand) --answers-source suggested")
        }
        print("  next: edit lane answer sheets, then `\(applyCommand)`")
    }

    static func printWorkspaceApply(report: URL) throws {
        let payload = try JSONFiles.object(report)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        print("")
        print("review_workspace_apply:")
        print("  report: \(PathDisplay.display(report))")
        print("  lanes: \(int(summary["lane_count"]) ?? 0)")
        print("  reviewed: \(int(summary["reviewed_count"]) ?? 0)")
        print("  remaining: \(int(summary["remaining_rows"]) ?? 0)")
        print("  rejected: \(int(summary["rejected_count"]) ?? 0)")
        print("  answers_source: \(string(payload["answers_source"]) ?? "review")")
        print("  dry_run: \(bool(payload["dry_run"]) ?? false)")
        print("  ready_for_apply: \(bool(summary["ready_for_batch_apply"]) ?? false)")
        printWorkspaceApplyLanes(payload)
        let sessionID = sessionIDForSessionLocalPlan(report.deletingLastPathComponent())
        if bool(summary["ready_for_batch_apply"]) == true {
            if let sessionID {
                print("  next: murmurmark review apply --session \(sessionID)")
            } else {
                print("  next: murmurmark review apply")
            }
        } else if let sessionID {
            print("  next: edit remaining lane answer sheets, then `murmurmark review workspace apply --session \(sessionID)`")
        } else {
            print("  next: edit remaining lane answer sheets, then `murmurmark review workspace apply`")
        }
    }

    private static func workspaceApplyCommand(payload: [String: Any], outDir: URL, sessionID: String?) -> String {
        var parts = ["murmurmark", "review", "workspace", "apply"]
        if let sessionID {
            parts += ["--session", sessionID]
            return parts.joined(separator: " ")
        }

        let workspace = outDir.appendingPathComponent("review_workspace.json")
        appendPathOption(
            "workspace",
            workspace,
            default: PathURLs.fileURL("sessions/_reports/review-plan/review_workspace.json"),
            to: &parts
        )

        let inputs = payload["inputs"] as? [String: Any] ?? [:]
        if let template = string(inputs["template"]) {
            appendPathOption(
                "template",
                PathURLs.fileURL(template),
                default: PathURLs.fileURL("sessions/_reports/review-plan/review_decisions.template.jsonl"),
                to: &parts
            )
        }

        appendPathOption(
            "report",
            outDir.appendingPathComponent("review_workspace_apply_report.json"),
            default: PathURLs.fileURL("sessions/_reports/review-plan/review_workspace_apply_report.json"),
            to: &parts
        )
        return parts.joined(separator: " ")
    }

    private static func appendPathOption(_ name: String, _ value: URL, default defaultValue: URL, to parts: inout [String]) {
        guard !samePath(value, defaultValue) else { return }
        parts += ["--\(name)", PathDisplay.display(value)]
    }

    private static func printWorkspaceApplyLanes(_ payload: [String: Any]) {
        let lanes = payload["lanes"] as? [[String: Any]] ?? []
        guard !lanes.isEmpty else { return }
        print("  lane_progress:")
        for lane in lanes {
            let summary = lane["summary"] as? [String: Any] ?? [:]
            let name = string(lane["lane"]) ?? "unknown"
            let status = string(lane["status"]) ?? "unknown"
            let reviewed = int(summary["reviewed_count"]) ?? 0
            let todo = int(summary["todo_count"]) ?? 0
            let rejected = int(summary["rejected_count"]) ?? 0
            let answerSheet = string(lane["answer_sheet"])
            print("    \(name): status=\(status) reviewed=\(reviewed) todo=\(todo) rejected=\(rejected)")
            if todo > 0, let answerSheet {
                print("      edit: $EDITOR \(shellQuote(answerSheet))")
            }
        }
    }

    private static func sessionIDForSessionLocalPlan(_ outDir: URL) -> String? {
        let plan = outDir.standardizedFileURL
        guard plan.lastPathComponent == "review-plan" else { return nil }
        let readiness = plan.deletingLastPathComponent()
        let derived = readiness.deletingLastPathComponent()
        let session = derived.deletingLastPathComponent()
        guard readiness.lastPathComponent == "readiness",
              derived.lastPathComponent == "derived",
              !session.lastPathComponent.isEmpty
        else {
            return nil
        }
        return session.lastPathComponent
    }

    private static func printWorkspaceLane(_ lane: [String: Any]) {
        let name = string(lane["lane"]) ?? "unknown"
        let items = int(lane["items"]) ?? 0
        let minutes = (double(lane["duration_sec"]) ?? 0) / 60
        let suggested = string(lane["suggested_answer_sheet"]).flatMap { firstAnswersLine(url: PathURLs.fileURL($0)) }
        let suffix = suggested.map { " suggested=\($0)" } ?? ""
        print(String(format: "    %@: items=%d minutes=%.2f%@", name, items, minutes, suffix))
        if let audio = string(lane["audio"]) {
            print("      listen: afplay \(shellQuote(audio))")
        }
        if let answerSheet = string(lane["answer_sheet"]) {
            print("      edit: $EDITOR \(shellQuote(answerSheet))")
        }
    }

    private static func bool(_ value: Any?) -> Bool? {
        if let value = value as? Bool { return value }
        if let text = value as? String { return ["true", "yes", "1"].contains(text.lowercased()) }
        return nil
    }

    private static func singleAppliedSession(_ sessions: [[String: Any]]) -> String? {
        let names = sessions.compactMap { string($0["session"]) }.filter { !$0.isEmpty }
        return names.count == 1 ? names[0] : nil
    }

    private static func printAppliedSessionReadiness(_ session: String) throws {
        let sessionURL = PathURLs.fileURL(session)
        let readiness = sessionURL.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: readiness.path) else {
            return
        }
        try ReadinessPrinter.printSession(sessionURL)
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
