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
            print("  audio: \(displayPath(audio))")
        }
        if let markdown = string(outputs["markdown"]) {
            print("  markdown: \(displayPath(markdown))")
        }
        if let answerSheet = string(outputs["answer_sheet"]) {
            print("  answer_sheet: \(displayPath(answerSheet))")
            if let state = answerSheetState(url: PathURLs.fileURL(answerSheet)) {
                print("  answer_sheet_status: \(state.description)")
            }
        }
        if let suggested = string(outputs["suggested_answer_sheet"]) {
            print("  suggested_answer_sheet: \(displayPath(suggested))")
            if let suggestedLine = firstAnswersLine(url: PathURLs.fileURL(suggested)) {
                print("  suggested_answers: \(suggestedLine)")
            }
        }
        let selectedRows = int(summary["selected_rows"]) ?? int(summary["item_count"]) ?? 0
        let items = int(summary["item_count"]) ?? 0
        let groupedItems = int(summary["grouped_item_count"]) ?? 0
        let groupedRows = int(summary["grouped_row_count"]) ?? 0
        print("  rows: \(selectedRows)")
        print("  items: \(items)")
        if groupedItems > 0 || groupedRows > 0 {
            print("  grouped_items: \(groupedItems)")
            print("  grouped_rows_saved: \(groupedRows)")
        }
        print("  skipped: \(int(summary["skipped_count"]) ?? 0)")
        let applyCommand = laneApplyCommand(lane: lane, session: session, planOutDir: planOutDir, outDir: outDir)
        let listenCommand = string(outputs["audio"]).map { "afplay \(shellQuote(displayPath($0)))" }
        let readCommand = string(outputs["markdown"]).map { "less \(shellQuote(displayPath($0)))" }
        let editCommand = string(outputs["answer_sheet"]).map { "$EDITOR \(shellQuote(displayPath($0)))" }
        let answerState = string(outputs["answer_sheet"]).flatMap { answerSheetState(url: PathURLs.fileURL($0)) }
        let manifestRecommendedNext = string(payload["recommended_next"])
        let recommendedNext = answerState?.hasReviewedAnswers == true
            ? "\(applyCommand) --dry-run"
            : manifestRecommendedNext ?? listenCommand ?? readCommand ?? editCommand ?? "\(applyCommand) --dry-run"
        print("  recommended_next: \(recommendedNext)")
        if let listenCommand {
            print("  listen: \(listenCommand)")
        }
        if let readCommand {
            print("  read: \(readCommand)")
        }
        if let editCommand {
            print("  edit: \(editCommand)")
        }
        print("  dry_run: \(applyCommand) --dry-run")
        print("  apply: \(applyCommand)")
        let hasSuggestedAnswerSheet = string(outputs["suggested_answer_sheet"]) != nil
        if hasSuggestedAnswerSheet {
            print("  suggested_dry_run: \(applyCommand) --answers-source suggested --dry-run")
            print("  suggested_apply: \(applyCommand) --answers-source suggested")
        }
        printLanePackHandoff(applyCommand: applyCommand, session: session, hasSuggestedAnswerSheet: hasSuggestedAnswerSheet)
    }

    private static func printLanePackHandoff(applyCommand: String, session: URL?, hasSuggestedAnswerSheet: Bool) {
        let sessionArgument = session.map { " --session \(PathDisplay.display($0))" } ?? ""
        print("  manual_flow:")
        print("    dry_run: \(applyCommand) --dry-run")
        print("    apply: \(applyCommand)")
        if hasSuggestedAnswerSheet {
            print("  suggested_flow:")
            print("    dry_run: \(applyCommand) --answers-source suggested --dry-run")
            print("    apply: \(applyCommand) --answers-source suggested")
        }
        print("  after_apply:")
        print("    murmurmark review progress\(sessionArgument)")
        print("    murmurmark review apply\(sessionArgument)")
        print("  next: listen, read markdown, edit answer_sheet, dry-run, apply, then progress")
    }

    private static func laneApplyCommand(lane: String, session: URL?, planOutDir: URL?, outDir: URL) -> String {
        var parts = ["murmurmark", "review", "lane", "apply", lane]
        if let session {
            parts += ["--session", PathDisplay.display(session)]
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
        guard let line = firstAnswersLine(url: url),
              line.hasPrefix("answers=")
        else {
            return nil
        }
        let answers = String(line.dropFirst("answers=".count))
            .filter { !$0.isWhitespace }
            .map { Character(String($0).lowercased()) }
        let reviewed = answers.filter { ![".", "n", "t"].contains($0) }.count
        return AnswerSheetState(total: answers.count, reviewed: reviewed)
    }

    private static func shellQuote(_ value: String) -> String {
        if value.range(of: #"^[A-Za-z0-9_./:@%+=,-]+$"#, options: .regularExpression) != nil {
            return value
        }
        return "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    private static func displayPath(_ value: String) -> String {
        PathDisplay.display(PathURLs.fileURL(value))
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
        print("  review_actions: \(int(summary["review_action_count"]) ?? int(summary["raw_item_count"]) ?? 0)")
        print("  grouped_review_rows: \(int(summary["grouped_review_row_count"]) ?? 0)")
        print("  sessions_with_review: \(int(summary["sessions_with_review"]) ?? 0)")
        print("  estimated_listen_minutes: \(double(summary["estimated_listen_minutes"]) ?? 0)")
        if let lanes = summary["by_review_lane"] as? [String: Any] {
            print("  by_lane: \(compactJSON(lanes))")
        }
        print("  first_lane: \(firstLane)")
        if let reason = string(strategy["first_recommended_reason"]) {
            print("  first_lane_reason: \(reason)")
        }
        if let quickLane = string(strategy["quick_recommended_lane"]), quickLane != firstLane {
            print("  quick_lane: \(quickLane)")
        }
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
        if let actionCount = int(summary["action_count"]) {
            print("  review_actions: \(int(summary["reviewed_actions"]) ?? 0)/\(actionCount)")
            print("  remaining_actions: \(int(summary["remaining_actions"]) ?? 0)")
        }
        if let groupedRows = int(summary["grouped_review_row_count"]), groupedRows > 0 {
            print("  grouped_review_rows: \(groupedRows)")
        }
        print("  remaining_minutes: \(double(summary["remaining_minutes"]) ?? 0)")
        print("  invalid_rows: \(int(summary["invalid_rows"]) ?? 0)")
        print("  ready_for_apply: \(bool(summary["ready_for_batch_apply"]) ?? false)")
        if let decisions = summary["decisions"] as? [String: Any] {
            print("  decisions: \(compactJSON(decisions))")
        }
        printProgressLanes(payload)
        let planOutDir = report.deletingLastPathComponent()
        let sessionID = sessionIDForSessionLocalPlan(planOutDir)
        printProgressNext(summary: summary, nextLane: firstRemainingLane(payload), sessionID: sessionID, planOutDir: planOutDir)
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
            let remainingActions = int(lane["remaining_actions"])
            let minutes = (double(lane["remaining_seconds"]) ?? 0) / 60
            if let remainingActions {
                print(
                    String(
                        format: "    %@: reviewed=%d/%d remaining=%d actions=%d minutes=%.2f",
                        name,
                        reviewed,
                        total,
                        remaining,
                        remainingActions,
                        minutes
                    )
                )
            } else {
                print(String(format: "    %@: reviewed=%d/%d remaining=%d minutes=%.2f", name, reviewed, total, remaining, minutes))
            }
        }
    }

    private static func firstRemainingLane(_ payload: [String: Any]) -> String? {
        let lanes = payload["by_lane"] as? [[String: Any]] ?? []
        return lanes.first { (int($0["remaining"]) ?? 0) > 0 }.flatMap { string($0["review_lane"]) }
    }

    private static func printProgressNext(summary: [String: Any], nextLane: String?, sessionID: String?, planOutDir: URL) {
        let sessionArgument = sessionID.map { " --session \($0)" } ?? ""
        if let nextLane {
            print("  next_lane: \(nextLane)")
        }
        let commands: [String]
        let afterReady: [String]
        if bool(summary["ready_for_batch_apply"]) == true {
            commands = ["murmurmark review apply\(sessionArgument)"]
            afterReady = []
        } else if let nextLane,
                  let handoff = preparedLanePackHandoff(lane: nextLane, planOutDir: planOutDir, sessionArgument: sessionArgument) {
            print("  prepared_lane_pack: \(PathDisplay.display(handoff.manifest))")
            if let read = handoff.readCommand {
                print("  read: \(read)")
            }
            if let edit = handoff.editCommand {
                print("  edit: \(edit)")
            }
            if let answerSheetStatus = handoff.answerSheetStatus {
                print("  answer_sheet_status: \(answerSheetStatus)")
            }
            print("  apply: \(handoff.applyCommand)")
            commands = handoff.commands
            afterReady = ["murmurmark review apply\(sessionArgument)"]
        } else {
            var nextCommands: [String] = []
            if let nextLane {
                nextCommands.append("murmurmark review lane \(nextLane)\(sessionArgument)")
                nextCommands.append("murmurmark review lane apply \(nextLane)\(sessionArgument)")
            }
            nextCommands.append("murmurmark review workspace\(sessionArgument)")
            nextCommands.append("murmurmark review workspace apply\(sessionArgument)")
            nextCommands.append("murmurmark review progress\(sessionArgument)")
            commands = nextCommands
            afterReady = ["murmurmark review apply\(sessionArgument)"]
        }
        if let first = commands.first {
            print("  recommended_next: \(first)")
        }
        print("  next:")
        for command in commands {
            print("    \(command)")
        }
        if !afterReady.isEmpty {
            print("  after_ready:")
            for command in afterReady {
                print("    \(command)")
            }
        }
        if let first = commands.first {
            print("")
            print("next: \(first)")
        }
    }

    private struct PreparedLanePackHandoff {
        let manifest: URL
        let commands: [String]
        let readCommand: String?
        let editCommand: String?
        let answerSheetStatus: String?
        let applyCommand: String
    }

    private static func preparedLanePackHandoff(
        lane: String,
        planOutDir: URL,
        sessionArgument: String
    ) -> PreparedLanePackHandoff? {
        let outDir = planOutDir.appendingPathComponent("lane-packs")
        let manifest = outDir.appendingPathComponent("review_lane_pack.\(lane).json")
        guard FileManager.default.fileExists(atPath: manifest.path),
              let payload = try? JSONFiles.object(manifest)
        else {
            return nil
        }
        let outputs = payload["outputs"] as? [String: Any] ?? [:]
        let audioCommand = existingOutputCommand(outputs["audio"], prefix: "afplay")
        let readCommand = existingOutputCommand(outputs["markdown"], prefix: "less")
        let editCommand = existingOutputCommand(outputs["answer_sheet"], prefix: "$EDITOR")
        let answerState = string(outputs["answer_sheet"]).flatMap { answerSheetState(url: PathURLs.fileURL($0)) }
        let applyCommand = "murmurmark review lane apply \(lane)\(sessionArgument)"
        let dryRunCommand = "\(applyCommand) --dry-run"
        var commands: [String] = []
        if answerState?.hasReviewedAnswers == true {
            commands.append(dryRunCommand)
            commands.append(applyCommand)
        } else {
            if let audioCommand {
                commands.append(audioCommand)
            }
            if let readCommand {
                commands.append(readCommand)
            }
            if let editCommand {
                commands.append(editCommand)
            }
            commands.append(dryRunCommand)
            commands.append(applyCommand)
        }
        commands.append("murmurmark review progress\(sessionArgument)")
        guard !commands.isEmpty else { return nil }
        return PreparedLanePackHandoff(
            manifest: manifest,
            commands: commands,
            readCommand: readCommand,
            editCommand: editCommand,
            answerSheetStatus: answerState?.description,
            applyCommand: applyCommand
        )
    }

    private static func existingOutputCommand(_ value: Any?, prefix: String) -> String? {
        guard let path = string(value), !path.isEmpty else { return nil }
        let url = PathURLs.fileURL(path)
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        return "\(prefix) \(shellQuote(displayPath(path)))"
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
        let reportNextCommands = payload["next_commands"] as? [[String: Any]] ?? []
        let hasReportNextCommands = !reportNextCommands.isEmpty
        if let recommended = ReadinessPrinter.preferredNextCommand(reportNextCommands) {
            print("  recommended_next: \(recommended)")
        }
        if hasReportNextCommands {
            print("  next:")
            for item in reportNextCommands {
                guard let command = string(item["command"]), !command.isEmpty else { continue }
                let label = string(item["label"]) ?? string(item["id"]) ?? "next"
                print("    \(command) — \(label)")
            }
        }
        var finalNext = ReadinessPrinter.preferredNextCommand(reportNextCommands)
        if failedSessions > 0 || failedRefreshSteps > 0 {
            if !hasReportNextCommands {
                let next = "less \(PathDisplay.display(report))"
                print("  next: \(next)")
                finalNext = next
            }
        } else if let session = singleAppliedSession(sessions) {
            let sessionURL = PathURLs.fileURL(session)
            if !hasReportNextCommands {
                if let next = readinessRecommendedNext(sessionURL) {
                    print("  next: \(next)")
                    print("  report_next: murmurmark report \(PathDisplay.display(sessionURL))")
                    finalNext = next
                } else {
                    let next = "murmurmark report \(PathDisplay.display(sessionURL))"
                    print("  next: \(next)")
                    finalNext = next
                }
            } else {
                print("  report_next: murmurmark report \(PathDisplay.display(sessionURL))")
            }
            try printAppliedSessionReadiness(session)
        } else if !sessions.isEmpty {
            if !hasReportNextCommands {
                let next = "murmurmark report corpus"
                print("  next: \(next)")
                finalNext = next
            }
        }
        if let finalNext {
            print("")
            print("next: \(finalNext)")
        }
    }

    static func printApplyNotReady(session: URL?, decisions: URL, template: URL) {
        let sessionArgument = session.map { " --session \(PathDisplay.display($0))" } ?? ""
        print("")
        print("review_apply:")
        print("  status: not_ready")
        print("  decisions: \(PathDisplay.display(decisions))")
        print("  review_template: \(PathDisplay.display(template))")
        print("  missing:")
        if !FileManager.default.fileExists(atPath: decisions.path) {
            print("    decisions")
        }
        if !FileManager.default.fileExists(atPath: template.path) {
            print("    review_template")
        }
        printApplyNotReadyNext(sessionArgument: sessionArgument, nextLane: nil)
    }

    static func printApplyNotReady(session: URL?, decisions: URL, progress: URL) throws {
        let payload = try JSONFiles.object(progress)
        let summary = payload["summary"] as? [String: Any] ?? [:]
        let sessionArgument = session.map { " --session \(PathDisplay.display($0))" } ?? ""
        print("")
        print("review_apply:")
        print("  status: not_ready")
        print("  decisions: \(PathDisplay.display(decisions))")
        print("  progress: \(PathDisplay.display(progress))")
        print("  reviewed: \(int(summary["reviewed"]) ?? 0)/\(int(summary["total"]) ?? 0)")
        print("  remaining: \(int(summary["remaining"]) ?? 0)")
        if let actionCount = int(summary["action_count"]) {
            print("  review_actions: \(int(summary["reviewed_actions"]) ?? 0)/\(actionCount)")
            print("  remaining_actions: \(int(summary["remaining_actions"]) ?? 0)")
        }
        print("  ready_for_apply: \(bool(summary["ready_for_batch_apply"]) ?? false)")
        printProgressLanes(payload)
        printApplyNotReadyNext(sessionArgument: sessionArgument, nextLane: firstRemainingLane(payload))
    }

    private static func printApplyNotReadyNext(sessionArgument: String, nextLane: String?) {
        if let nextLane {
            print("  next_lane: \(nextLane)")
        }
        print("  next:")
        if let nextLane {
            print("    murmurmark review lane \(nextLane)\(sessionArgument)")
            print("    murmurmark review lane apply \(nextLane)\(sessionArgument)")
        } else {
            print("    murmurmark review first-lane\(sessionArgument)")
            print("    murmurmark review lane apply first\(sessionArgument)")
        }
        print("    murmurmark review workspace\(sessionArgument)")
        print("    murmurmark review workspace apply\(sessionArgument)")
        print("    murmurmark review progress\(sessionArgument)")
        let finalNext = nextLane
            .map { "murmurmark review lane \($0)\(sessionArgument)" }
            ?? "murmurmark review first-lane\(sessionArgument)"
        print("")
        print("next: \(finalNext)")
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
        let rowCount = okLanes.reduce(0) { $0 + (int($1["selected_rows"]) ?? int($1["items"]) ?? 0) }
        let groupedRows = okLanes.reduce(0) { $0 + (int($1["grouped_row_count"]) ?? 0) }
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
        print("  rows: \(rowCount)")
        print("  items: \(itemCount)")
        if groupedRows > 0 {
            print("  grouped_rows_saved: \(groupedRows)")
        }
        print(String(format: "  listen_minutes: %.2f", durationSeconds / 60))
        print("  by_lane: \(compactJSON(byLane))")
        if let recommended = firstWorkspaceReviewCommand(okLanes) {
            print("  recommended_next: \(recommended)")
        }
        if !okLanes.isEmpty {
            print("  lane_packs:")
            for lane in okLanes {
                printWorkspaceLane(lane)
            }
        }
        let sessionID = sessionIDForSessionLocalPlan(outDir)
        let applyCommand = workspaceApplyCommand(payload: payload, outDir: outDir, sessionID: sessionID)
        let hasSuggestedAnswerSheet = okLanes.contains { string($0["suggested_answer_sheet"]) != nil }
        if hasSuggestedAnswerSheet {
            print("  suggested_dry_run: \(applyCommand) --answers-source suggested --dry-run")
            print("  suggested_apply: \(applyCommand) --answers-source suggested")
        }
        printWorkspaceHandoff(
            applyCommand: applyCommand,
            sessionID: sessionID,
            hasSuggestedAnswerSheet: hasSuggestedAnswerSheet
        )
    }

    private static func printWorkspaceHandoff(
        applyCommand: String,
        sessionID: String?,
        hasSuggestedAnswerSheet: Bool
    ) {
        let sessionArgument = sessionID.map { " --session \($0)" } ?? ""
        print("  manual_flow:")
        print("    dry_run: \(applyCommand) --dry-run")
        print("    apply: \(applyCommand)")
        if hasSuggestedAnswerSheet {
            print("  suggested_flow:")
            print("    dry_run: \(applyCommand) --answers-source suggested --dry-run")
            print("    apply: \(applyCommand) --answers-source suggested")
        }
        print("  after_apply:")
        print("    murmurmark review progress\(sessionArgument)")
        print("    murmurmark review apply\(sessionArgument)")
        print("  next: listen, read lane markdown, edit answer sheets, dry-run, apply, then progress")
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
        let sessionArgument = sessionID.map { " --session \($0)" } ?? ""
        if bool(summary["ready_for_batch_apply"]) == true {
            print("  recommended_next: murmurmark review apply\(sessionArgument)")
            print("  next:")
            print("    murmurmark review apply\(sessionArgument)")
        } else {
            if let nextLane = firstTodoWorkspaceLane(payload) {
                print("  next_lane: \(nextLane)")
            }
            let recommended = firstTodoWorkspaceLane(payload)
                .map { "murmurmark review lane \($0)\(sessionArgument)" }
                ?? "murmurmark review workspace apply\(sessionArgument)"
            print("  recommended_next: \(recommended)")
            print("  next:")
            if let nextLane = firstTodoWorkspaceLane(payload) {
                print("    murmurmark review lane \(nextLane)\(sessionArgument)")
                print("    murmurmark review lane apply \(nextLane)\(sessionArgument)")
            }
            print("    read/edit remaining lane answer sheets above")
            print("    murmurmark review workspace apply\(sessionArgument)")
            print("    murmurmark review progress\(sessionArgument)")
            print("  after_ready:")
            print("    murmurmark review apply\(sessionArgument)")
        }
    }

    private static func firstTodoWorkspaceLane(_ payload: [String: Any]) -> String? {
        let lanes = payload["lanes"] as? [[String: Any]] ?? []
        return lanes.first {
            let summary = $0["summary"] as? [String: Any] ?? [:]
            return (int(summary["todo_count"]) ?? 0) > 0
        }.flatMap { string($0["lane"]) }
    }

    private static func firstWorkspaceReviewCommand(_ lanes: [[String: Any]]) -> String? {
        for lane in lanes {
            if let audio = string(lane["audio"]) {
                return "afplay \(shellQuote(displayPath(audio)))"
            }
            if let markdown = string(lane["markdown"]) {
                return "less \(shellQuote(displayPath(markdown)))"
            }
            if let answerSheet = string(lane["answer_sheet"]) {
                return "$EDITOR \(shellQuote(displayPath(answerSheet)))"
            }
        }
        return nil
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
            let markdown = string(lane["markdown"])
            let answerSheet = string(lane["answer_sheet"])
            print("    \(name): status=\(status) reviewed=\(reviewed) todo=\(todo) rejected=\(rejected)")
            if todo > 0, let markdown {
                print("      read: less \(shellQuote(markdown))")
            }
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
        return PathDisplay.display(session)
    }

    private static func printWorkspaceLane(_ lane: [String: Any]) {
        let name = string(lane["lane"]) ?? "unknown"
        let items = int(lane["items"]) ?? 0
        let rows = int(lane["selected_rows"]) ?? items
        let groupedRows = int(lane["grouped_row_count"]) ?? 0
        let minutes = (double(lane["duration_sec"]) ?? 0) / 60
        let suggested = string(lane["suggested_answer_sheet"]).flatMap { firstAnswersLine(url: PathURLs.fileURL($0)) }
        let suffix = suggested.map { " suggested=\($0)" } ?? ""
        let grouping = groupedRows > 0 ? " rows=\(rows) grouped_saved=\(groupedRows)" : " rows=\(rows)"
        print(String(format: "    %@: items=%d%@ minutes=%.2f%@", name, items, grouping, minutes, suffix))
        if let audio = string(lane["audio"]) {
            print("      listen: afplay \(shellQuote(displayPath(audio)))")
        }
        if let markdown = string(lane["markdown"]) {
            print("      read: less \(shellQuote(displayPath(markdown)))")
        }
        if let answerSheet = string(lane["answer_sheet"]) {
            print("      edit: $EDITOR \(shellQuote(displayPath(answerSheet)))")
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

    private static func readinessRecommendedNext(_ session: URL) -> String? {
        let readiness = session.appendingPathComponent("derived/readiness/session_readiness.json")
        guard FileManager.default.fileExists(atPath: readiness.path),
              let payload = try? JSONFiles.object(readiness),
              let nextCommands = payload["next_commands"] as? [[String: Any]]
        else {
            return nil
        }
        return ReadinessPrinter.preferredNextCommand(nextCommands)
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
