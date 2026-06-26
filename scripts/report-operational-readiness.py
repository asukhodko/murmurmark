#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.operational_readiness_report/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report whether MurmurMark is ready for medium-risk working meetings.")
    parser.add_argument(
        "--session-quality",
        type=Path,
        default=Path("sessions/_reports/session-quality/session_quality_report.json"),
    )
    parser.add_argument(
        "--corpus-evaluation",
        type=Path,
        default=Path("sessions/_reports/regression-corpus/regression_corpus_evaluation.json"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/operational-readiness"),
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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def session_review_burden(session: dict[str, Any]) -> dict[str, Any]:
    duration = safe_float(session.get("meeting_duration_sec"))
    probable_error = safe_float(session.get("audio_review_probable_error_seconds"))
    stronger_judge = safe_float(session.get("audio_review_stronger_judge_seconds"))
    harmful = safe_float(session.get("audit_harmful_seconds_after"))
    burden = probable_error + stronger_judge
    ratio = burden / duration if duration > 0 else 0.0
    return {
        "session_id": session.get("session_id"),
        "label": session.get("label"),
        "duration_sec": round(duration, 3),
        "selected_profile": session.get("selected_profile"),
        "verdict": session.get("verdict"),
        "review_burden_sec": round(burden, 3),
        "review_burden_ratio": round(ratio, 6),
        "audio_review_probable_error_seconds": round(probable_error, 3),
        "audio_review_stronger_judge_seconds": round(stronger_judge, 3),
        "audit_harmful_seconds_after": round(harmful, 3),
        "risk_flags": session.get("risk_flags") or [],
    }


def operational_verdict(session_quality: dict[str, Any], corpus: dict[str, Any] | None) -> tuple[str, list[str], list[str]]:
    sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    summary = session_quality.get("summary") if isinstance(session_quality.get("summary"), dict) else {}
    blockers: list[str] = []
    warnings: list[str] = []

    session_count = safe_int(summary.get("session_count"))
    complete = safe_int(summary.get("complete_pipeline_count"))
    complete_ratio = complete / session_count if session_count > 0 else 0.0
    verdicts = summary.get("by_verdict") if isinstance(summary.get("by_verdict"), dict) else {}
    risky_or_failed = safe_int(verdicts.get("risky")) + safe_int(verdicts.get("failed"))
    selected_profiles = summary.get("by_selected_profile") if isinstance(summary.get("by_selected_profile"), dict) else {}
    cleanup_profiles = safe_int(selected_profiles.get("audit_cleanup_v1")) + safe_int(selected_profiles.get("audit_cleanup_v2"))
    cleanup_ratio = cleanup_profiles / session_count if session_count > 0 else 0.0

    burdens = [session_review_burden(row) for row in sessions]
    total_duration = sum(item["duration_sec"] for item in burdens)
    total_burden = sum(item["review_burden_sec"] for item in burdens)
    burden_ratio = total_burden / total_duration if total_duration > 0 else 0.0
    max_session_burden = max((item["review_burden_ratio"] for item in burdens), default=0.0)
    high_risk_sessions = [item for item in burdens if item["review_burden_ratio"] > 0.08 or len(item["risk_flags"]) >= 4]

    corpus_readiness = corpus.get("readiness") if isinstance(corpus, dict) else None
    missing_labels = corpus.get("missing_labels") if isinstance(corpus, dict) and isinstance(corpus.get("missing_labels"), list) else []

    if session_count < 5:
        blockers.append("too_few_regression_sessions")
    if complete_ratio < 0.80:
        blockers.append("not_enough_complete_pipelines")
    if risky_or_failed > 0:
        blockers.append("risky_or_failed_session_verdicts_present")
    if cleanup_ratio < 0.70:
        warnings.append("many_sessions_without_audit_cleanup_profile")
    if burden_ratio > 0.08:
        blockers.append("total_review_burden_too_high")
    elif burden_ratio > 0.04:
        warnings.append("total_review_burden_noticeable")
    if max_session_burden > 0.12:
        blockers.append("single_session_review_burden_too_high")
    elif high_risk_sessions:
        warnings.append("some_sessions_need_manual_review_before_use")
    if corpus_readiness not in {"useful_for_audio_judge_v0", "broad_regression_ready"}:
        warnings.append("regression_corpus_not_ready_for_audio_judge")
    if missing_labels:
        warnings.append("regression_corpus_missing_labels:" + ",".join(str(label) for label in missing_labels))

    if blockers:
        verdict = "not_ready"
    elif warnings:
        verdict = "pilot_ready_with_review"
    else:
        verdict = "medium_risk_ready"
    return verdict, blockers, warnings


def build_report(session_quality: dict[str, Any], corpus: dict[str, Any] | None, inputs: dict[str, str]) -> dict[str, Any]:
    sessions = session_quality.get("sessions") if isinstance(session_quality.get("sessions"), list) else []
    burdens = [session_review_burden(row) for row in sessions]
    total_duration = sum(item["duration_sec"] for item in burdens)
    total_burden = sum(item["review_burden_sec"] for item in burdens)
    verdict, blockers, warnings = operational_verdict(session_quality, corpus)
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "report-operational-readiness", "version": SCRIPT_VERSION},
        "inputs": inputs,
        "operational_verdict": verdict,
        "scope": "local tool for medium-risk working meetings",
        "blockers": blockers,
        "warnings": warnings,
        "summary": {
            "session_count": len(sessions),
            "complete_pipeline_count": safe_int((session_quality.get("summary") or {}).get("complete_pipeline_count")),
            "selected_profiles": (session_quality.get("summary") or {}).get("by_selected_profile", {}),
            "session_verdicts": (session_quality.get("summary") or {}).get("by_verdict", {}),
            "total_duration_sec": round(total_duration, 3),
            "total_review_burden_sec": round(total_burden, 3),
            "total_review_burden_ratio": round(total_burden / total_duration, 6) if total_duration > 0 else 0.0,
            "corpus_readiness": corpus.get("readiness") if isinstance(corpus, dict) else None,
            "corpus_item_count": safe_int(corpus.get("item_count")) if isinstance(corpus, dict) else 0,
            "corpus_missing_labels": corpus.get("missing_labels") if isinstance(corpus, dict) else None,
        },
        "session_review_burden": burdens,
        "recommendations": recommendations(verdict, blockers, warnings),
    }


def recommendations(verdict: str, blockers: list[str], warnings: list[str]) -> list[str]:
    rows: list[str] = []
    if verdict == "not_ready":
        rows.append("do_not_use_without_manual_audio_review")
    if "single_session_review_burden_too_high" in blockers or "some_sessions_need_manual_review_before_use" in warnings:
        rows.append("review_audio_review_report_for_high_burden_sessions")
    if any(item.startswith("regression_corpus") for item in warnings):
        rows.append("expand_or_rebuild_regression_corpus_before_audio_judge_v1")
    rows.append("use_quality_verdict_and_notes_for_medium_risk_meetings_with_review")
    rows.append("keep_raw_audio_private_and_derived_artifacts_ignored")
    return rows


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# MurmurMark Operational Readiness",
        "",
        f"Verdict: `{report['operational_verdict']}`",
        f"Scope: `{report['scope']}`",
        "",
        "## Summary",
        "",
        f"- Sessions: `{report['summary']['session_count']}`",
        f"- Complete pipelines: `{report['summary']['complete_pipeline_count']}`",
        f"- Total review burden: `{round(report['summary']['total_review_burden_sec'] / 60.0, 2)} min`",
        f"- Review burden ratio: `{round(report['summary']['total_review_burden_ratio'] * 100.0, 2)}%`",
        f"- Corpus readiness: `{report['summary']['corpus_readiness']}`",
        f"- Corpus items: `{report['summary']['corpus_item_count']}`",
        "",
        "## Blockers",
        "",
    ]
    if report["blockers"]:
        lines.extend(f"- `{item}`" for item in report["blockers"])
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if report["warnings"]:
        lines.extend(f"- `{item}`" for item in report["warnings"])
    else:
        lines.append("- none")
    lines.extend(["", "## Session Review Burden", ""])
    lines.append("| Session | Profile | Verdict | Review min | Review % | Flags |")
    lines.append("|---|---|---|---:|---:|---|")
    for row in sorted(report["session_review_burden"], key=lambda item: item["review_burden_ratio"], reverse=True):
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['session_id']}`",
                    str(row["selected_profile"]),
                    str(row["verdict"]),
                    f"{row['review_burden_sec'] / 60.0:.2f}",
                    f"{row['review_burden_ratio'] * 100.0:.2f}",
                    ", ".join(row["risk_flags"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Recommendations", ""])
    lines.extend(f"- `{item}`" for item in report["recommendations"])
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    session_quality = read_json(args.session_quality)
    if not session_quality:
        raise SystemExit(f"missing session quality report: {args.session_quality}")
    corpus = read_json(args.corpus_evaluation)
    inputs = {
        "session_quality": str(args.session_quality),
        "corpus_evaluation": str(args.corpus_evaluation),
    }
    report = build_report(session_quality, corpus, inputs)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "operational_readiness_report.json", report)
    write_markdown(out_dir / "operational_readiness_report.md", report)
    print(f"verdict: {report['operational_verdict']}")
    print(f"written: {out_dir / 'operational_readiness_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
