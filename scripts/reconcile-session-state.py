#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from murmurmark_deadline import BUDGET_EXIT, run_bounded


SCHEMA = "murmurmark.session_state_reconciliation/v1"
SCRIPT_VERSION = "0.1.0"


class ReconciliationError(RuntimeError):
    pass


class ReconciliationBudgetExhausted(ReconciliationError):
    pass


class StageJournal(list):
    checkpoint = staticmethod(lambda: None)

    def append(self, stage):
        super().append(stage)
        self.checkpoint()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile review, speaker attribution, readiness and outcome for one session."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--reason", default="explicit_refresh")
    parser.add_argument("--cached-speakers-only", action="store_true")
    parser.add_argument(
        "--skip-review-rebase",
        action="store_true",
        help="Refresh reports and speaker evidence without rebasing existing review decisions.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while block := file.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path, session: Path) -> dict[str, Any]:
    return {
        "path": str(path.relative_to(session)) if path.is_relative_to(session) else str(path),
        "exists": path.is_file(),
        "bytes": path.stat().st_size if path.is_file() else None,
        "sha256": sha256_file(path),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def run_stage(
    stages: list[dict[str, Any]],
    name: str,
    command: list[str],
    *,
    allowed: set[int] | None = None,
) -> int:
    allowed = allowed or {0}
    started = datetime.now(timezone.utc).isoformat()
    stage = {
        "name": name,
        "command": command,
        "started_at": started,
        "status": "running",
    }
    stages.append(stage)
    result = run_bounded(command, capture_output=False)
    stage.update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "returncode": result["returncode"],
        "status": "passed" if result["returncode"] == 0 else "warning" if result["returncode"] in allowed
        else "failed" if result["status"] == "completed" else result["status"],
    })
    if result["stderr"]:
        stage["stderr_tail"] = result["stderr"][-12000:]
    if isinstance(stages, StageJournal):
        stages.checkpoint()
    if result["status"] == "budget_exhausted":
        raise ReconciliationBudgetExhausted(name)
    if result["status"] == "interrupted":
        raise KeyboardInterrupt
    if result["returncode"] not in allowed:
        raise ReconciliationError(f"{name} exited with {result['returncode']}")
    return result["returncode"]


def reviewed_rows(progress: dict[str, Any] | None) -> int:
    summary = progress.get("summary") if isinstance(progress, dict) else None
    return int(summary.get("reviewed") or 0) if isinstance(summary, dict) else 0


def optional_int(mapping: dict[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    return int(value) if value is not None else None


def optional_float(mapping: dict[str, Any], key: str) -> float | None:
    value = mapping.get(key)
    return round(float(value), 3) if value is not None else None


def refresh_review_plan(
    session: Path,
    repo_root: Path,
    stages: list[dict[str, Any]],
    *,
    rebase: bool,
    metadata_only: bool = False,
) -> None:
    py = sys.executable
    readiness = session / "derived/readiness"
    quality = readiness / "session-quality"
    operational = readiness / "operational-readiness"
    plan = readiness / "review-plan"
    run_stage(
        stages,
        "session_quality",
        [
            py,
            str(repo_root / "scripts/report-session-quality.py"),
            str(session),
            "--out-dir",
            str(quality),
            "--write-session-readiness",
        ],
    )
    run_stage(
        stages,
        "operational_readiness",
        [
            py,
            str(repo_root / "scripts/report-operational-readiness.py"),
            "--session-quality",
            str(quality / "session_quality_report.json"),
            "--out-dir",
            str(operational),
        ],
    )
    run_stage(
        stages,
        "review_plan",
        [
            py,
            str(repo_root / "scripts/build-review-plan.py"),
            "--operational-readiness",
            str(operational / "operational_readiness_report.json"),
            "--out-dir",
            str(plan),
        ],
    )
    workspace_command = [
        py,
        str(repo_root / "scripts/build-review-workspace.py"),
        "--template",
        str(plan / "review_decisions.template.jsonl"),
        "--decisions",
        str(plan / "review_decisions.jsonl"),
        "--out-dir",
        str(plan),
        "--session",
        session.name,
    ]
    if metadata_only:
        workspace_command.append("--metadata-only")
    if rebase:
        workspace_command.append("--rebase-decisions")
        applied_dir = session / "derived/transcript-simple/whisper-cpp/review-decisions"
        for source in sorted(applied_dir.glob("review_decisions_applied.*.jsonl")):
            workspace_command.extend(["--history-source", str(source)])
    run_stage(stages, "review_workspace", workspace_command)
    run_stage(
        stages,
        "review_progress",
        [
            py,
            str(repo_root / "scripts/report-review-decisions-progress.py"),
            "--template",
            str(plan / "review_decisions.template.jsonl"),
            "--decisions",
            str(plan / "review_decisions.jsonl"),
            "--out",
            str(plan / "review_decisions_progress.json"),
            "--markdown",
            str(plan / "review_decisions_progress.md"),
        ],
    )
    run_stage(
        stages,
        "session_quality_after_review_progress",
        [
            py,
            str(repo_root / "scripts/report-session-quality.py"),
            str(session),
            "--out-dir",
            str(quality),
            "--write-session-readiness",
        ],
    )
    run_stage(
        stages,
        "operational_readiness_after_review_progress",
        [
            py,
            str(repo_root / "scripts/report-operational-readiness.py"),
            "--session-quality",
            str(quality / "session_quality_report.json"),
            "--out-dir",
            str(operational),
        ],
    )


def apply_rebased_review(session: Path, repo_root: Path, stages: list[dict[str, Any]]) -> None:
    plan = session / "derived/readiness/review-plan"
    progress = read_json(plan / "review_decisions_progress.json")
    if reviewed_rows(progress) <= 0:
        stages.append({"name": "materialize_rebased_review", "status": "skipped", "reason": "no_closed_review_rows"})
        return
    run_stage(
        stages,
        "materialize_rebased_review",
        [
            sys.executable,
            str(repo_root / "scripts/apply-review-decisions-batch.py"),
            "--decisions",
            str(plan / "review_decisions.jsonl"),
            "--review-template",
            str(plan / "review_decisions.template.jsonl"),
            "--allow-partial-review",
            "--session",
            session.name,
            "--synthesize",
            "--refresh-reports",
            "--out",
            str(plan / "review_decisions_apply_report.json"),
            "--session-quality-out-dir",
            str(session / "derived/readiness/session-quality"),
            "--operational-readiness-out-dir",
            str(session / "derived/readiness/operational-readiness"),
            "--review-plan-out-dir",
            str(plan),
        ],
    )


def verify_consistency(session: Path) -> dict[str, Any]:
    from review_audio_evidence import read_queue_snapshot

    readiness = read_json(session / "derived/readiness/session_readiness.json") or {}
    outcome = read_json(session / "derived/outcome/outcome.json") or {}
    selection = read_json(
        session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    ) or {}
    speaker = outcome.get("speaker_resolution") or {}
    if speaker.get("state") in {"provisional", "unavailable"}:
        selection = read_json(
            session / "derived/transcript-rich/speaker-resolved-default-v1/provisional/selection.json"
        ) or {}
    progress = read_json(
        session / "derived/readiness/review-plan/review_decisions_progress.json"
    ) or {}
    progress_summary = progress.get("summary") if isinstance(progress.get("summary"), dict) else {}
    readiness_metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
    outcome_metrics = outcome.get("metrics") if isinstance(outcome.get("metrics"), dict) else {}
    profiles = {
        "readiness": str(readiness.get("selected_profile") or ""),
        "outcome": str(outcome.get("selected_profile") or ""),
        "speaker_selection": str(selection.get("selected_profile") or ""),
    }
    nonempty_profiles = {value for value in profiles.values() if value}
    remaining_rows = int(progress_summary.get("remaining") or 0)
    remaining_seconds = round(float(progress_summary.get("remaining_seconds") or 0.0), 3)
    metric_rows = optional_int(readiness_metrics, "suggested_closure_manual_remaining_rows")
    metric_seconds = optional_float(readiness_metrics, "suggested_closure_manual_remaining_seconds")
    outcome_rows = optional_int(outcome_metrics, "suggested_closure_manual_remaining_rows")
    outcome_seconds = optional_float(outcome_metrics, "suggested_closure_manual_remaining_seconds")
    canonical_rows = int(readiness_metrics.get("manual_review_queue_rows") or 0)
    canonical_seconds = round(float(readiness_metrics.get("manual_review_queue_seconds") or 0.0), 3)
    outcome_canonical_rows = int(outcome_metrics.get("manual_review_queue_rows") or 0)
    outcome_canonical_seconds = round(float(outcome_metrics.get("manual_review_queue_seconds") or 0.0), 3)
    checks = {
        "selected_profile_agrees": len(nonempty_profiles) <= 1,
        "review_rows_agree": remaining_rows == canonical_rows == outcome_canonical_rows
        and (metric_rows is None or remaining_rows == metric_rows)
        and (outcome_rows is None or remaining_rows == outcome_rows),
        "review_seconds_agree": (
            metric_seconds is None or abs(remaining_seconds - metric_seconds) <= 0.001
        )
        and (outcome_seconds is None or abs(remaining_seconds - outcome_seconds) <= 0.001)
        and abs(remaining_seconds - canonical_seconds) <= 0.001
        and abs(remaining_seconds - outcome_canonical_seconds) <= 0.001,
        "speaker_selection_current": selection.get("selected_profile") == readiness.get("selected_profile")
        and selection.get("state") == speaker.get("state")
        and selection.get("selected_speaker_profile") == speaker.get("selected_speaker_profile")
        and (selection.get("selected_transcript") or {}).get("path") == speaker.get("transcript_path"),
    }
    snapshot = read_queue_snapshot(session)
    if (outcome.get("transcript_read_view") or {}).get("status") == "current":
        from transcript_read_view import verified_path

        path = verified_path(session, outcome)
        checks["transcript_read_view_current"] = path is not None
        checks["transcript_read_view_selected"] = path is not None and (
            session / ((outcome.get("outputs") or {}).get("transcript") or {}).get("path", "")
        ).resolve() == path
    if "queue_snapshot" in progress:
        checks["review_queue_snapshot_current"] = snapshot is not None
        checks["review_queue_snapshot_agrees"] = (
            snapshot is not None
            and readiness.get("review_queue_snapshot") == snapshot
            and outcome.get("review_queue_snapshot") == snapshot
        )
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "profiles": profiles,
        "review_queue_fingerprint": snapshot.get("fingerprint") if snapshot else None,
        "review_queue": {"rows": remaining_rows, "seconds": remaining_seconds},
        "readiness_review_queue": {"rows": metric_rows, "seconds": metric_seconds},
        "outcome_review_queue": {"rows": outcome_rows, "seconds": outcome_seconds},
        "canonical_readiness_review_queue": {"rows": canonical_rows, "seconds": canonical_seconds},
        "canonical_outcome_review_queue": {
            "rows": outcome_canonical_rows,
            "seconds": outcome_canonical_seconds,
        },
    }


def main() -> int:
    args = parse_args()
    finalize_only = os.environ.get("MURMURMARK_FINALIZE_ONLY") == "1"
    if finalize_only:
        args.skip_review_rebase = True
    cached_speakers = (finalize_only or args.cached_speakers_only
                       or os.environ.get("MURMURMARK_SPEAKER_REFRESH_MODE") == "cache_only")
    session = args.session.expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[1]
    if not (session / "session.json").is_file():
        raise SystemExit(f"error: session.json not found: {session}")

    report_dir = session / "derived/pipeline-run/state-reconciliation"
    report_path = report_dir / "state_reconciliation_report.json"
    previous_outcome = read_json(session / "derived/outcome/outcome.json") or {}
    previous_transcript_entry = (previous_outcome.get("outputs") or {}).get("transcript")
    previous_transcript_value = (
        previous_transcript_entry.get("path")
        if isinstance(previous_transcript_entry, dict)
        else previous_transcript_entry
    )
    previous_transcript = Path(previous_transcript_value) if isinstance(previous_transcript_value, str) else None
    if previous_transcript is not None and not previous_transcript.is_absolute():
        previous_transcript = session / previous_transcript
    fallback = artifact(previous_transcript, session) if previous_transcript is not None else None
    stages = StageJournal()
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "generator": {"name": "reconcile-session-state", "version": SCRIPT_VERSION},
        "session": str(session),
        "reason": args.reason,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "metadata_only": finalize_only,
        "previous_authoritative_fallback": fallback,
        "stages": stages,
    }
    stages.checkpoint = lambda: write_json(report_path, payload)
    write_json(report_path, payload)
    try:
        if args.skip_review_rebase:
            run_stage(stages, "transcript_order_current_profile", [
                sys.executable, str(repo_root / "scripts/audit-transcript-order.py"),
                str(session), "--profile", "auto",
            ])
        refresh_review_plan(
            session,
            repo_root,
            stages,
            rebase=not args.skip_review_rebase,
            metadata_only=finalize_only,
        )
        if not args.skip_review_rebase:
            apply_rebased_review(session, repo_root, stages)
            run_stage(
                stages,
                "transcript_order_after_review",
                [
                    sys.executable,
                    str(repo_root / "scripts/audit-transcript-order.py"),
                    str(session),
                    "--profile",
                    "auto",
                ],
            )
            refresh_review_plan(session, repo_root, stages, rebase=True)
        run_stage(
            stages,
            "speaker_selection",
            [
                sys.executable,
                str(repo_root / "scripts/select-speaker-resolved-transcript.py"),
                str(session),
                "--verify-only" if cached_speakers
                else "--refresh-evidence",
            ],
            allowed={0, 2},
        )
        run_stage(
            stages,
            "provisional_speaker_transcript",
            [
                sys.executable,
                str(repo_root / "scripts/materialize-provisional-speaker-transcript.py"),
                str(session),
                *(["--cached-only"] if cached_speakers else []),
            ],
            allowed={0, 2},
        )
        run_stage(
            stages,
            "outcome",
            [sys.executable, str(repo_root / "scripts/evaluate-outcome.py"), str(session)],
        )
        consistency = verify_consistency(session)
        if not consistency["passed"]:
            raise ReconciliationError(f"consistency checks failed: {consistency['checks']}")
        payload.update(
            {
                "status": "completed",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "consistency": consistency,
            }
        )
        write_json(report_path, payload)
        print(f"state_reconciliation: completed ({args.reason})")
        print(f"report: {report_path}")
        return 0
    except (KeyboardInterrupt, ReconciliationBudgetExhausted) as error:
        resume_command = (
            f"murmurmark enrich {session}"
            if args.reason == "deferred_enrichment"
            else f"murmurmark report {session}"
        )
        payload.update(
            {
                "status": "deferred_budget_exhausted" if isinstance(error, ReconciliationBudgetExhausted)
                else "interrupted_recoverable",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "resume_command": resume_command,
            }
        )
        write_json(report_path, payload)
        print(f"state_reconciliation: {payload['status']}", file=sys.stderr)
        print(f"report: {report_path}", file=sys.stderr)
        return BUDGET_EXIT if isinstance(error, ReconciliationBudgetExhausted) else 130
    except Exception as error:  # noqa: BLE001 - preserve a recoverable transaction report
        payload.update(
            {
                "status": "failed_recoverable",
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "error": str(error),
                "resume_command": f"murmurmark report {session}",
            }
        )
        write_json(report_path, payload)
        print(f"state_reconciliation: failed_recoverable: {error}", file=sys.stderr)
        print(f"fallback: {fallback}", file=sys.stderr)
        print(f"report: {report_path}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
