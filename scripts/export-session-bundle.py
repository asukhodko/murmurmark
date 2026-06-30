#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.3.0"
SCHEMA_MANIFEST = "murmurmark.export_manifest/v1"
EXPORT_BUNDLE_QUALITY = "v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a reviewed MurmurMark session into a local user-facing bundle.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--format", choices=["markdown", "obsidian"], default="markdown")
    parser.add_argument("--profile", default="auto", help="auto, current, or an explicit transcript profile.")
    parser.add_argument("--out-dir", type=Path, default=Path("exports/private"))
    parser.add_argument("--force", action="store_true", help="Export even when readiness has export blockers.")
    parser.add_argument("--include-json", action="store_true", help="Copy evidence JSON files into the export bundle.")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def command_path(path: Path) -> str:
    if not path.is_absolute():
        return shlex.quote(str(path))
    try:
        display = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        display = path
    return shlex.quote(str(display))


def session_id(session: Path) -> str:
    payload = read_json(session / "session.json") or {}
    return str(payload.get("session_id") or session.name)


def single_session_quality(session: Path) -> dict[str, Any] | None:
    report = read_json(session / "derived/readiness/session-quality/session_quality_report.json")
    if not report:
        return None
    rows = report.get("sessions")
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    return None


def quality_verdict(session: Path, profile: str) -> dict[str, Any] | None:
    synthesis = session / "derived/synthesis-simple/extractive"
    candidates = []
    if profile != "auto":
        candidates.append(synthesis / f"quality_verdict{suffix(profile)}.json")
    candidates.append(synthesis / "quality_verdict.json")
    for path in candidates:
        payload = read_json(path)
        if payload:
            return payload
    return None


def resolve_profile(session: Path, requested: str) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    quality = single_session_quality(session)
    if requested != "auto":
        return requested, quality, quality_verdict(session, requested)
    if quality and quality.get("selected_profile"):
        profile = str(quality["selected_profile"])
        return profile, quality, quality_verdict(session, profile)
    verdict = quality_verdict(session, "auto")
    if verdict and verdict.get("selected_transcript_profile"):
        profile = str(verdict["selected_transcript_profile"])
        return profile, quality, verdict
    return "current", quality, verdict


def source_paths(session: Path, profile: str) -> dict[str, Path]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    paths = {
        "transcript_md": resolved / f"transcript{suffix(profile)}.md",
        "transcript_json": resolved / f"transcript.simple{suffix(profile)}.json",
        "clean_dialogue_json": resolved / f"clean_dialogue{suffix(profile)}.json",
        "quality_report_json": resolved / f"quality_report{suffix(profile)}.json",
        "notes_md": synthesis / f"notes{suffix(profile)}.md",
        "quality_verdict_md": synthesis / f"quality_verdict{suffix(profile)}.md",
        "quality_verdict_json": synthesis / f"quality_verdict{suffix(profile)}.json",
        "evidence_notes_json": synthesis / f"evidence_notes{suffix(profile)}.json",
        "review_items_jsonl": synthesis / f"review_items{suffix(profile)}.jsonl",
        "session_readiness_json": session / "derived/readiness/session_readiness.json",
        "session_readiness_md": session / "derived/readiness/session_readiness.md",
        "session_quality_json": session / "derived/readiness/session-quality/session_quality_report.json",
    }
    if profile == "current":
        paths.update(
            {
                "transcript_md": resolved / "transcript.md",
                "transcript_json": resolved / "transcript.simple.json",
                "clean_dialogue_json": resolved / "clean_dialogue.json",
                "quality_report_json": resolved / "quality_report.json",
                "notes_md": synthesis / "notes.md",
                "quality_verdict_md": synthesis / "quality_verdict.md",
                "quality_verdict_json": synthesis / "quality_verdict.json",
                "evidence_notes_json": synthesis / "evidence_notes.json",
                "review_items_jsonl": synthesis / "review_items.jsonl",
            }
        )
    return paths


def readiness_blockers(
    paths: dict[str, Path],
    quality: dict[str, Any] | None,
    verdict: dict[str, Any] | None,
    readiness: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    required_outputs = (
        "transcript_md",
        "notes_md",
        "quality_verdict_md",
        "quality_verdict_json",
        "session_readiness_md",
    )
    for key in required_outputs:
        if not paths[key].exists():
            blockers.append(f"missing:{key}")
    if readiness:
        for item in readiness.get("export_blockers") or []:
            item = str(item)
            if item and item not in blockers:
                blockers.append(item)
        for item in readiness.get("warnings") or []:
            item = str(item)
            if item:
                warnings.append(f"readiness:{item}")
    else:
        blockers.append("missing_session_readiness_json")
    if quality:
        if quality.get("pipeline_status") != "complete":
            blockers.append("pipeline_not_complete")
        use_gate = quality.get("use_gate")
        if use_gate == "review_first" and not readiness:
            blockers.append("session_requires_review")
        elif use_gate and use_gate != "ready_for_notes":
            warnings.append(f"use_gate:{use_gate}")
        for flag in quality.get("risk_flags") or []:
            warnings.append(f"risk:{flag}")
    else:
        warnings.append("missing_session_quality_report")
    if verdict:
        value = verdict.get("verdict")
        if value in {"failed", "risky"}:
            blockers.append(f"quality_verdict:{value}")
        elif value and value != "good":
            warnings.append(f"quality_verdict:{value}")
    else:
        blockers.append("missing_quality_verdict_json")
    return blockers, warnings


def blocked_export_next_commands(
    session: Path,
    readiness: dict[str, Any] | None,
    blockers: list[str],
) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    if readiness:
        for item in readiness.get("next_commands") or []:
            if isinstance(item, dict) and item.get("command"):
                command_item = {
                    "id": str(item.get("id") or f"step_{len(commands) + 1}"),
                    "label": str(item.get("label") or "Run the next readiness step."),
                    "command": str(item["command"]),
                }
                commands.append(command_item)
    if commands:
        return commands

    session_arg = command_path(session)
    if "pipeline_incomplete" in blockers or any(str(item).startswith("missing:") for item in blockers):
        return [
            {
                "id": "process",
                "label": "Run the pipeline until readiness is refreshed.",
                "command": f"murmurmark process {session_arg}",
            }
        ]
    return [
        {
            "id": "review_next",
            "label": "Refresh this session's review handoff.",
            "command": f"murmurmark review next {session_arg}",
        }
    ]


def export_commands(args: argparse.Namespace, session: Path) -> dict[str, str]:
    base = [
        "murmurmark",
        "export",
        command_path(session),
        "--format",
        shlex.quote(args.format),
        "--profile",
        shlex.quote(args.profile),
        "--out-dir",
        command_path(args.out_dir),
    ]
    if args.include_json:
        base.append("--include-json")
    rerun = " ".join(base)
    return {
        "rerun": rerun,
        "debug_force": f"{rerun} --force",
    }


def command_item(id_: str, label: str, command: str) -> dict[str, str]:
    return {"id": id_, "label": label, "command": command}


def ready_export_next_commands(session: Path, manifest_path: Path) -> list[dict[str, str]]:
    session_arg = command_path(session)
    manifest_arg = command_path(manifest_path)
    return [
        command_item(
            "retention_plan",
            "Plan local retention actions after this export.",
            f"murmurmark retention plan {session_arg} --export-manifest {manifest_arg}",
        ),
        command_item(
            "retention_payload",
            "Inventory any external-provider payload before handoff.",
            f"murmurmark retention payload {session_arg} --export-manifest {manifest_arg}",
        ),
    ]


def open_commands(files: dict[str, Any], manifest_path: Path) -> list[dict[str, str]]:
    labels = {
        "index": "Open exported bundle index.",
        "obsidian_note": "Open exported Obsidian note.",
        "quality_verdict_md": "Read exported quality verdict.",
        "notes_md": "Read exported notes.",
        "transcript_md": "Read exported transcript.",
    }
    commands: list[dict[str, str]] = []
    for key in ("index", "obsidian_note", "quality_verdict_md", "notes_md", "transcript_md"):
        item = files.get(key)
        if not isinstance(item, dict) or not item.get("path"):
            continue
        commands.append(command_item(f"open_{key}", labels[key], f"less {command_path(Path(str(item['path'])))}"))
    commands.append(command_item("open_manifest", "Inspect export manifest.", f"less {command_path(manifest_path)}"))
    return commands


def blocked_export_next(next_commands: list[dict[str, str]]) -> str:
    rendered = "; ".join(f"`{item['command']}`" for item in next_commands if item.get("command"))
    if not rendered:
        return "rerun export after blockers are closed, or use --force only for debugging"
    return f"{rendered}; rerun export after blockers are closed, or use --force only for debugging"


def first_next(next_commands: list[dict[str, str]], fallback: str) -> str:
    for item in next_commands:
        command = item.get("command")
        if command:
            return command
    return fallback


def print_blocked_export_handoff(manifest: dict[str, Any], blocked_path: Path) -> None:
    print(f"export blocked: {blocked_path}")
    print("blockers: " + ", ".join(str(item) for item in manifest["blockers"]))
    print("next:")
    for item in manifest.get("next_commands") or []:
        command = item.get("command") if isinstance(item, dict) else None
        if command:
            print(f"  {command}")
    export = manifest.get("export_commands") if isinstance(manifest.get("export_commands"), dict) else {}
    if export.get("rerun"):
        print(f"  rerun_export: {export['rerun']}")
    if export.get("debug_force"):
        print(f"  debug_force: {export['debug_force']}")


def print_success_export_handoff(manifest: dict[str, Any], manifest_path: Path) -> None:
    print(f"exported: {manifest_path.parent}")
    print(f"status: {manifest['status']}")
    print(f"profile: {manifest['selected_profile']}")
    print(f"manifest: {manifest_path}")
    print(f"recommended_next: {manifest.get('next', '')}")
    open_items = manifest.get("open_commands") if isinstance(manifest.get("open_commands"), list) else []
    if open_items:
        print("open:")
        for item in open_items:
            if isinstance(item, dict) and item.get("command"):
                print(f"  {item['command']}")
    next_items = manifest.get("next_commands") if isinstance(manifest.get("next_commands"), list) else []
    if next_items:
        print("next:")
        for item in next_items:
            if isinstance(item, dict) and item.get("command"):
                print(f"  {item['command']}")


def copy_file(src: Path, dst: Path) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"source": str(src), "path": str(dst), "bytes": dst.stat().st_size}


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def strip_heading(text: str) -> str:
    lines = text.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    return "\n".join(lines).strip() + "\n"


def format_seconds(value: Any) -> str:
    try:
        seconds = max(0.0, float(value))
    except (TypeError, ValueError):
        return "unknown"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_range(start: Any, end: Any) -> str:
    return f"{format_seconds(start)}-{format_seconds(end)}"


def metric(verdict: dict[str, Any] | None, key: str, default: Any = 0) -> Any:
    metrics = verdict.get("metrics") if isinstance(verdict, dict) else None
    if isinstance(metrics, dict):
        return metrics.get(key, default)
    return default


def review_summary(verdict: dict[str, Any] | None, evidence: dict[str, Any] | None, review_items: list[dict[str, Any]]) -> dict[str, Any]:
    computed: dict[str, Any] | None = None

    def compute() -> dict[str, Any]:
        nonlocal computed
        if computed is not None:
            return computed
        seconds = 0.0
        by_type: dict[str, dict[str, Any]] = {}
        for item in review_items:
            try:
                start = float(item.get("start", 0))
                end = float(item.get("end", start))
            except (TypeError, ValueError):
                start = end = 0.0
            item_type = str(item.get("type") or "review")
            bucket = by_type.setdefault(item_type, {"count": 0, "seconds": 0.0})
            duration = max(0.0, end - start)
            seconds += duration
            bucket["count"] += 1
            bucket["seconds"] = round(float(bucket["seconds"]) + duration, 3)
        computed = {"review_item_count": len(review_items), "review_item_seconds": round(seconds, 3), "by_type": by_type}
        return computed

    def normalize(summary: dict[str, Any]) -> dict[str, Any]:
        if not review_items:
            return summary
        count = summary.get("review_item_count")
        seconds = summary.get("review_item_seconds")
        try:
            seconds_value = float(seconds)
        except (TypeError, ValueError):
            seconds_value = 0.0
        if count is None or seconds is None or seconds_value <= 0.0:
            fixed = {**summary}
            calculated = compute()
            fixed.setdefault("review_item_count", calculated["review_item_count"])
            fixed["review_item_seconds"] = calculated["review_item_seconds"]
            fixed.setdefault("by_type", calculated["by_type"])
            return fixed
        return summary

    if isinstance(verdict, dict) and isinstance(verdict.get("review_summary"), dict):
        return normalize(verdict["review_summary"])
    if isinstance(evidence, dict):
        review = evidence.get("review")
        if isinstance(review, dict) and isinstance(review.get("summary"), dict):
            return normalize(review["summary"])
    return compute()


def verdict_explanation(verdict_value: str, blockers: list[str] | None = None) -> tuple[str, str]:
    if blockers:
        return (
            "Do not use yet.",
            "The session still has export blockers. Run the next review or processing command first.",
        )
    if verdict_value == "good":
        return (
            "Usable.",
            "No mandatory review regions were reported. Use the notes normally, keeping the transcript available for context.",
        )
    if verdict_value == "usable_with_review":
        return (
            "Usable after targeted review.",
            "The notes are useful, but flagged intervals should be checked before sharing or acting on sensitive details.",
        )
    if verdict_value == "risky":
        return (
            "Review first.",
            "The transcript has quality risks that can change meaning. Treat notes as a draft until the review queue is closed.",
        )
    if verdict_value == "failed":
        return (
            "Do not use.",
            "The pipeline could not produce a trustworthy working transcript.",
        )
    return (
        "Unknown.",
        "The export could not determine a clear quality verdict. Inspect the transcript and source reports before using it.",
    )


def evidence_ids(item: dict[str, Any]) -> str:
    ids = item.get("evidence_utterance_ids")
    if not isinstance(ids, list) or not ids:
        ids = item.get("utterance_ids")
    if not isinstance(ids, list) or not ids:
        return "`needs_review:no_evidence_id`"
    return ", ".join(f"`{str(value)}`" for value in ids[:8])


def item_text(item: dict[str, Any]) -> str:
    return str(item.get("display_text") or item.get("text") or "").strip()


def render_candidate_item(item: dict[str, Any]) -> str:
    score = item.get("score")
    score_text = f", score `{score}`" if score is not None else ""
    status = "needs_review" if item.get("needs_review", True) else "evidence"
    subtype = str(item.get("subtype") or item.get("type") or "item")
    time = item.get("time") if isinstance(item.get("time"), dict) else item
    roles = ", ".join(str(value) for value in item.get("roles", []) if value)
    role_text = f", {roles}" if roles else ""
    reasons = item.get("reasons") if isinstance(item.get("reasons"), list) else []
    reason_text = f"\n  - Why: {'; '.join(str(value) for value in reasons[:2])}" if reasons else ""
    return (
        f"- `{status}` `{subtype}`{score_text}, {format_range(time.get('start'), time.get('end'))}"
        f"{role_text}, evidence {evidence_ids(item)}: {item_text(item)}"
        + reason_text
    )


def render_review_item(item: dict[str, Any]) -> str:
    reason = str(item.get("reason") or item.get("type") or "needs review")
    severity = str(item.get("severity") or "medium")
    text = item_text(item)
    text_part = f": {text}" if text else ""
    return f"- `{severity}` {format_range(item.get('start'), item.get('end'))}, {evidence_ids(item)}: {reason}{text_part}"


def top_review_items(review_items: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    def sort_key(item: dict[str, Any]) -> tuple[float, float]:
        severity_rank = {"high": 0.0, "medium": 1.0, "low": 2.0}.get(str(item.get("severity")), 1.5)
        try:
            duration = float(item.get("end", 0)) - float(item.get("start", 0))
        except (TypeError, ValueError):
            duration = 0.0
        return severity_rank, -duration

    return sorted(review_items, key=sort_key)[:limit]


def render_selected_notes_section(title: str, rows: list[Any]) -> list[str]:
    lines = ["", f"## {title}", ""]
    clean_rows = [row for row in rows if isinstance(row, dict)]
    if not clean_rows:
        lines.append("- none detected")
        return lines
    lines.extend(render_candidate_item(row) for row in clean_rows)
    return lines


def source_file_status(paths: dict[str, Path]) -> list[str]:
    rows = []
    for label, key in (
        ("quality verdict JSON", "quality_verdict_json"),
        ("evidence notes JSON", "evidence_notes_json"),
        ("clean dialogue JSON", "clean_dialogue_json"),
        ("review items JSONL", "review_items_jsonl"),
        ("readiness JSON", "session_readiness_json"),
    ):
        rows.append(f"- {label}: `{'present' if paths[key].exists() else 'missing'}`")
    return rows


def render_quality_verdict_markdown(
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    review_items: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    paths: dict[str, Path],
) -> str:
    verdict_value = str((verdict or {}).get("verdict") or "unknown")
    headline, recommendation = verdict_explanation(verdict_value, blockers)
    summary = review_summary(verdict, evidence, review_items)
    risk_items = verdict.get("risk_items") if isinstance(verdict, dict) and isinstance(verdict.get("risk_items"), list) else []
    metrics = verdict.get("metrics") if isinstance(verdict, dict) and isinstance(verdict.get("metrics"), dict) else {}
    lines = [
        "# Quality Verdict",
        "",
        f"Session: `{sid}`",
        f"Transcript profile: `{profile}`",
        f"Verdict: `{verdict_value}`",
        f"Use gate: `{(quality or {}).get('use_gate', 'unknown')}`",
        "",
        "## Can I Use This?",
        "",
        f"**{headline}** {recommendation}",
        "",
        "## Review Burden",
        "",
        f"- Review items: `{summary.get('review_item_count', 0)}`",
        f"- Review seconds: `{summary.get('review_item_seconds', 0)}`",
        f"- Utterances needing review: `{metrics.get('needs_review_count', metric(verdict, 'needs_review_count', 0))}`",
        f"- Cross-role overlap over 2s: `{metrics.get('cross_role_overlap_gt2_count', metric(verdict, 'cross_role_overlap_gt2_count', 0))}`",
        f"- Remote duplicate in Me seconds: `{metrics.get('remote_duplicate_in_me_seconds', metric(verdict, 'remote_duplicate_in_me_seconds', 0))}`",
        "",
        "## Main Reasons",
        "",
    ]
    if blockers:
        lines.extend(f"- blocker: `{item}`" for item in blockers)
    elif risk_items:
        for item in risk_items[:10]:
            lines.append(f"- `{item.get('severity', 'medium')}` `{item.get('type', 'risk')}`: {item.get('reason', '')}")
    else:
        lines.append("- No hard risk item was reported by the current verdict.")
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{item}`" for item in warnings[:20])
    if review_items:
        lines.extend(["", "## What Needs Review First", ""])
        lines.extend(render_review_item(item) for item in top_review_items(review_items, 8))
    lines.extend(
        [
            "",
            "## Source Checks",
            "",
            *source_file_status(paths),
            "",
            "This report is local and evidence-backed. It does not inspect raw audio directly and does not send data anywhere.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_notes_markdown(
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    review_items: list[dict[str, Any]],
    fallback_notes: str,
) -> str:
    if not evidence:
        fallback = strip_heading(fallback_notes).strip()
        lines = [
            "# Meeting Notes",
            "",
            f"Session: `{sid}`",
            f"Transcript profile: `{profile}`",
            f"Verdict: `{(verdict or {}).get('verdict', 'unknown')}`",
            "",
            "`needs_review` Evidence notes JSON is missing; this export is using the existing notes Markdown as a fallback.",
            "",
        ]
        lines.append(fallback if fallback else "- no notes available")
        return "\n".join(lines).rstrip() + "\n"

    selected = evidence.get("selected") if isinstance(evidence.get("selected"), dict) else {}
    summary = review_summary(verdict, evidence, review_items)
    lines = [
        "# Meeting Notes",
        "",
        f"Session: `{sid}`",
        f"Transcript profile: `{profile}`",
        f"Verdict: `{(verdict or {}).get('verdict', 'unknown')}`",
        f"Review items: `{summary.get('review_item_count', 0)}`",
        "",
        "These notes are extractive. Decisions, actions, risks and questions are shown only when they have utterance evidence IDs. Items marked `needs_review` should be checked before use.",
        "",
        "## Conversation Outline",
        "",
    ]
    blocks = selected.get("outline_blocks") if isinstance(selected.get("outline_blocks"), list) else []
    if not blocks:
        lines.append("- no outline blocks detected")
    for block in blocks:
        if not isinstance(block, dict):
            continue
        keywords = [str(value) for value in block.get("keywords", []) if value]
        title = ", ".join(keywords[:4]) or "discussion block"
        ids = [str(value) for value in block.get("utterance_ids", []) if value]
        id_span = f"`{ids[0]}`..`{ids[-1]}`" if ids else "`unknown`"
        lines.append(f"### {format_range(block.get('start'), block.get('end'))}: {title}")
        lines.append(f"- Utterances: {id_span}; turns: `{block.get('utterance_count', len(ids))}`")
        representatives = block.get("representatives") if isinstance(block.get("representatives"), list) else []
        for sample in representatives[:5]:
            if not isinstance(sample, dict):
                continue
            lines.append(
                f"  - `{sample.get('utterance_id', 'unknown')}` {sample.get('role', 'Unknown')}: "
                f"{str(sample.get('text') or '').strip()}"
            )
        lines.append("")

    lines.extend(render_selected_notes_section("Potential Decisions", selected.get("decisions", [])))
    lines.extend(render_selected_notes_section("Potential Actions", selected.get("actions", [])))
    lines.extend(render_selected_notes_section("Risks", selected.get("risks", [])))
    lines.extend(render_selected_notes_section("Open Questions", selected.get("open_questions", [])))
    lines.extend(["", "## Review Queue", ""])
    if not review_items:
        lines.append("- No mandatory review items were reported.")
    else:
        lines.append("Check these before using the notes for high-stakes decisions:")
        lines.extend(render_review_item(item) for item in top_review_items(review_items, 10))
    lines.extend(["", "## Evidence Audit", "", "Full scored candidates and hidden weak/process items are in `evidence_notes*.json` when JSON evidence is included in the export."])
    return "\n".join(lines).rstrip() + "\n"


def render_transcript_markdown(
    *,
    sid: str,
    profile: str,
    clean_dialogue: dict[str, Any] | None,
    fallback_transcript: str,
) -> str:
    utterances = clean_dialogue.get("utterances") if isinstance(clean_dialogue, dict) else None
    if not isinstance(utterances, list) or not utterances:
        fallback = strip_heading(fallback_transcript).strip()
        lines = [
            "# Transcript",
            "",
            f"Session: `{sid}`",
            f"Transcript profile: `{profile}`",
            "",
            "`needs_review` Clean dialogue JSON is missing; this export is using the existing transcript Markdown as a fallback.",
            "",
        ]
        lines.append(fallback if fallback else "- transcript is missing")
        return "\n".join(lines).rstrip() + "\n"

    lines = [
        "# Transcript",
        "",
        f"Session: `{sid}`",
        f"Transcript profile: `{profile}`",
        "",
        "Legend: `ok` means no mandatory review flag on the utterance; `needs_review` means the line should be checked before relying on wording or role.",
        "",
    ]
    for row in utterances:
        if not isinstance(row, dict):
            continue
        quality_row = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        flag = "needs_review" if quality_row.get("needs_review") else "ok"
        reason = str(quality_row.get("decision_reason") or quality_row.get("review_reason") or "").strip()
        reason_text = f" · {reason}" if reason else ""
        role = str(row.get("speaker_label") or row.get("role") or "Unknown")
        utterance_id = str(row.get("id") or "unknown")
        track = str(row.get("source_track") or "unknown")
        lines.extend(
            [
                f"## {format_range(row.get('start'), row.get('end'))} {role} · `{utterance_id}` · `{flag}`",
                "",
                f"- Source: `{track}`{reason_text}",
                "",
                str(row.get("text") or "").strip() or "_empty_",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_index_markdown(
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    review_items: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    include_json: bool,
    next_commands: list[dict[str, str]] | None = None,
    single_note: bool = False,
) -> str:
    verdict_value = str((verdict or {}).get("verdict") or "unknown")
    headline, recommendation = verdict_explanation(verdict_value, blockers)
    summary = review_summary(verdict, evidence, review_items)
    next_command = ""
    for item in next_commands or []:
        if item.get("command"):
            next_command = item["command"]
            break
    lines = [
        f"# MurmurMark Export: {sid}",
        "",
        "## Can I Use This?",
        "",
        f"**{headline}** {recommendation}",
        "",
        f"- Transcript profile: `{profile}`",
        f"- Verdict: `{verdict_value}`",
        f"- Use gate: `{(quality or {}).get('use_gate', 'unknown')}`",
        f"- Review items: `{summary.get('review_item_count', 0)}`",
        f"- Review seconds: `{summary.get('review_item_seconds', 0)}`",
        f"- Exported at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Start Here",
        "",
    ]
    if single_note:
        lines.extend(
            [
                "- Read `Quality Verdict` below to decide how much to trust the result.",
                "- Read `Notes` for the extractive outline, decisions, actions, risks and questions.",
                "- Use `Review Items` and `Transcript` below to check flagged places by utterance ID.",
            ]
        )
    else:
        lines.extend(
            [
                "- [Quality verdict](quality_verdict.md) tells whether the result is safe to use.",
                "- [Notes](notes.md) contains extractive outline, decisions, actions, risks and questions.",
                "- [Transcript](transcript.md) contains the full text with utterance IDs and review flags.",
                "- [Manifest](export_manifest.json) records exact files, profile, warnings and next commands.",
            ]
        )
    if include_json:
        lines.append("- JSON evidence files are included for audit and future tooling.")
    lines.extend(["", "## What Needs Review", ""])
    if blockers:
        lines.append("This export still has blockers:")
        lines.extend(f"- `{item}`" for item in blockers)
    elif not review_items:
        lines.append("- No mandatory review items were reported.")
    else:
        lines.extend(render_review_item(item) for item in top_review_items(review_items, 8))
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{item}`" for item in warnings[:20])
    lines.extend(
        [
            "",
            "## Retention And Privacy",
            "",
            "- Raw audio is not copied into this export bundle.",
            "- Retention planning is local and explicit; raw deletion requires a separate `retention apply` command.",
            "- Provider payload manifests are inventories only; MurmurMark does not upload anything from this step.",
        ]
    )
    if next_command:
        lines.extend(["", "## Next Action", "", f"```bash\n{next_command}\n```"])
    return "\n".join(lines).rstrip() + "\n"


def write_markdown_index(
    path: Path,
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    review_items: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    include_json: bool,
    next_commands: list[dict[str, str]] | None = None,
) -> None:
    path.write_text(
        render_index_markdown(
            sid=sid,
            profile=profile,
            verdict=verdict,
            quality=quality,
            evidence=evidence,
            review_items=review_items,
            blockers=blockers,
            warnings=warnings,
            include_json=include_json,
            next_commands=next_commands,
            single_note=False,
        ),
        encoding="utf-8",
    )


def write_obsidian_note(
    path: Path,
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    evidence: dict[str, Any] | None,
    review_items: list[dict[str, Any]],
    blockers: list[str],
    warnings: list[str],
    notes: str,
    transcript: str,
    quality_text: str,
) -> None:
    frontmatter = [
        "---",
        "type: murmurmark-session",
        f"session_id: {json.dumps(sid, ensure_ascii=False)}",
        f"transcript_profile: {json.dumps(profile, ensure_ascii=False)}",
        f"verdict: {json.dumps((verdict or {}).get('verdict', 'unknown'), ensure_ascii=False)}",
        f"use_gate: {json.dumps((quality or {}).get('use_gate', 'unknown'), ensure_ascii=False)}",
        f"exported_at: {json.dumps(datetime.now(timezone.utc).isoformat(), ensure_ascii=False)}",
        "---",
        "",
    ]
    body = [
        f"# {sid}",
        "",
        strip_heading(render_index_markdown(
            sid=sid,
            profile=profile,
            verdict=verdict,
            quality=quality,
            evidence=evidence,
            review_items=review_items,
            blockers=blockers,
            warnings=warnings,
            include_json=False,
            next_commands=None,
            single_note=True,
        )),
        "## Quality Verdict",
        "",
        strip_heading(quality_text),
        "## Notes",
        "",
        strip_heading(notes),
        "## Review Items",
        "",
    ]
    if review_items:
        body.extend(render_review_item(item) for item in top_review_items(review_items, 20))
    else:
        body.append("- No mandatory review items were reported.")
    body.extend(["", "## Transcript", "", strip_heading(transcript)])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(frontmatter + body), encoding="utf-8")


def export_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser()
    if not (session / "session.json").exists():
        raise SystemExit(f"session.json not found under {session}")
    sid = session_id(session)
    profile, quality, verdict = resolve_profile(session, args.profile)
    paths = source_paths(session, profile)
    readiness = read_json(paths["session_readiness_json"])
    evidence = read_json(paths["evidence_notes_json"])
    clean_dialogue = read_json(paths["clean_dialogue_json"])
    review_items = read_jsonl(paths["review_items_jsonl"])
    blockers, warnings = readiness_blockers(paths, quality, verdict, readiness)
    if blockers and not args.force:
        next_commands = blocked_export_next_commands(session, readiness, blockers)
        blocked_export_commands = export_commands(args, session)
        manifest = {
            "schema": SCHEMA_MANIFEST,
            "generator": {"name": "export-session-bundle", "version": SCRIPT_VERSION},
            "bundle_quality": EXPORT_BUNDLE_QUALITY,
            "status": "blocked",
            "session_id": sid,
            "requested_profile": args.profile,
            "selected_profile": profile,
            "format": args.format,
            "blockers": blockers,
            "warnings": warnings,
            "readiness": readiness,
            "next": blocked_export_next(next_commands),
            "next_commands": next_commands,
            "export_commands": blocked_export_commands,
        }
        args.out_dir.mkdir(parents=True, exist_ok=True)
        blocked_path = args.out_dir / f"{session.name}.export_blocked.json"
        write_json(blocked_path, manifest)
        print_blocked_export_handoff(manifest, blocked_path)
        raise SystemExit(2)

    out_dir = args.out_dir / session.name
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Any] = {}
    next_commands_for_index = ready_export_next_commands(session, out_dir / "export_manifest.json") if not blockers else blocked_export_next_commands(session, readiness, blockers)
    quality_text = render_quality_verdict_markdown(
        sid=sid,
        profile=profile,
        verdict=verdict,
        quality=quality,
        evidence=evidence,
        review_items=review_items,
        blockers=blockers,
        warnings=warnings,
        paths=paths,
    )
    notes_text = render_notes_markdown(
        sid=sid,
        profile=profile,
        verdict=verdict,
        evidence=evidence,
        review_items=review_items,
        fallback_notes=read_text(paths["notes_md"]),
    )
    transcript_text = render_transcript_markdown(
        sid=sid,
        profile=profile,
        clean_dialogue=clean_dialogue,
        fallback_transcript=read_text(paths["transcript_md"]),
    )
    if args.format == "markdown":
        index = out_dir / "index.md"
        write_markdown_index(
            index,
            sid=sid,
            profile=profile,
            verdict=verdict,
            quality=quality,
            evidence=evidence,
            review_items=review_items,
            blockers=blockers,
            warnings=warnings,
            include_json=args.include_json,
            next_commands=next_commands_for_index,
        )
        files["index"] = {"path": str(index), "bytes": index.stat().st_size}
        quality_out = out_dir / "quality_verdict.md"
        quality_out.write_text(quality_text, encoding="utf-8")
        files["quality_verdict_md"] = {
            "source": str(paths["quality_verdict_json"]),
            "path": str(quality_out),
            "bytes": quality_out.stat().st_size,
            "rendered": True,
        }
        notes_out = out_dir / "notes.md"
        notes_out.write_text(notes_text, encoding="utf-8")
        files["notes_md"] = {
            "source": str(paths["evidence_notes_json"]),
            "path": str(notes_out),
            "bytes": notes_out.stat().st_size,
            "rendered": True,
        }
        transcript_out = out_dir / "transcript.md"
        transcript_out.write_text(transcript_text, encoding="utf-8")
        files["transcript_md"] = {
            "source": str(paths["clean_dialogue_json"]),
            "path": str(transcript_out),
            "bytes": transcript_out.stat().st_size,
            "rendered": True,
        }
    else:
        obsidian = out_dir / f"{sid}.md"
        write_obsidian_note(
            obsidian,
            sid=sid,
            profile=profile,
            verdict=verdict,
            quality=quality,
            evidence=evidence,
            review_items=review_items,
            blockers=blockers,
            warnings=warnings,
            notes=notes_text,
            transcript=transcript_text,
            quality_text=quality_text,
        )
        files["obsidian_note"] = {"path": str(obsidian), "bytes": obsidian.stat().st_size}

    if args.include_json:
        for key in (
            "quality_verdict_json",
            "evidence_notes_json",
            "transcript_json",
            "clean_dialogue_json",
            "quality_report_json",
            "review_items_jsonl",
            "session_readiness_json",
            "session_quality_json",
        ):
            if paths[key].exists():
                files[key] = copy_file(paths[key], out_dir / paths[key].name)

    manifest_path = out_dir / "export_manifest.json"
    if blockers:
        next_commands = blocked_export_next_commands(session, readiness, blockers)
        debug_retention_commands = ready_export_next_commands(session, manifest_path)
        next_text = first_next(next_commands, blocked_export_next(next_commands))
    else:
        next_commands = ready_export_next_commands(session, manifest_path)
        debug_retention_commands = []
        next_text = first_next(next_commands, "inspect exported files")
    export_command_map = export_commands(args, session)
    manifest = {
        "schema": SCHEMA_MANIFEST,
        "generator": {"name": "export-session-bundle", "version": SCRIPT_VERSION},
        "bundle_quality": EXPORT_BUNDLE_QUALITY,
        "status": "exported_forced_with_blockers" if blockers else ("exported_with_warnings" if warnings else "exported"),
        "session_id": sid,
        "session": str(session),
        "requested_profile": args.profile,
        "selected_profile": profile,
        "format": args.format,
        "verdict": (verdict or {}).get("verdict"),
        "use_gate": (quality or {}).get("use_gate"),
        "blockers": blockers,
        "warnings": warnings,
        "readiness": readiness,
        "files": files,
        "next": next_text,
        "next_commands": next_commands,
        "open_commands": open_commands(files, manifest_path),
        "export_commands": export_command_map,
        "debug_retention_commands": debug_retention_commands,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(manifest_path, manifest)
    print_success_export_handoff(manifest, manifest_path)
    return manifest


def main() -> int:
    export_session(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
