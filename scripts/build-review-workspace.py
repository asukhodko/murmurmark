#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.5.1"
REVIEW_STATE_FIELDS = {
    "decision",
    "status",
    "reviewer",
    "notes",
    "reviewed_at",
    "review_source",
    "review_workspace_lane",
    "review_lane_pack",
    "review_lane_pack_index",
    "review_lane_pack_group_size",
    "review_reason",
    "review_evidence",
    "review_suggested_decision",
    "review_suggested_decision_confidence",
    "review_suggested_decision_reason",
}
SCHEMA = "murmurmark.review_workspace/v1"
LANE_ORDER = [
    "fast_confirm_drop",
    "check_unique_me_content",
    "check_local_recall",
    "check_transcript_order",
    "confirm_benign",
    "classify_audio",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build all MurmurMark review lane packs and a workspace index.")
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.template.jsonl"),
        help="Input review_decisions.template.jsonl.",
    )
    parser.add_argument(
        "--decisions",
        type=Path,
        default=Path("sessions/_reports/review-plan/review_decisions.jsonl"),
        help="Optional existing decisions JSONL. Reviewed rows are skipped.",
    )
    parser.add_argument("--session", action="append", default=[], help="Optional session id/path filter. Can be repeated.")
    parser.add_argument("--out-dir", type=Path, default=Path("sessions/_reports/review-plan"))
    parser.add_argument("--silence-sec", type=float, default=0.5)
    parser.add_argument(
        "--rebase-decisions",
        action="store_true",
        help="Rewrite decisions against the current template, carrying only evidence-identical closed rows.",
    )
    parser.add_argument(
        "--history-source",
        action="append",
        type=Path,
        default=[],
        help="Additional applied decision JSONL to preserve in review_decisions_history.jsonl.",
    )
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temp.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        file.flush()
        os.fsync(file.fileno())
    temp.replace(path)


def display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def shell_path(path: Path) -> str:
    return shlex.quote(display_path(path))


def command_item(item_id: str, command: str, reason: str) -> dict[str, str]:
    return {"id": item_id, "command": command, "reason": reason}


def command_strings(rows: Any) -> list[str]:
    if not isinstance(rows, list):
        return []
    commands: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        command = str(row.get("command") or "").strip()
        if command:
            commands.append(command)
    return commands


def workspace_handoff(
    *,
    workspace_path: Path,
    workspace_md_path: Path,
    template_path: Path,
    decisions_path: Path,
    report_path: Path,
    lanes: list[dict[str, Any]],
) -> dict[str, Any]:
    apply_base = (
        "murmurmark review workspace apply "
        f"--workspace {shell_path(workspace_path)} "
        f"--template {shell_path(template_path)} "
        f"--out {shell_path(decisions_path)} "
        f"--report {shell_path(report_path)}"
    )
    next_commands: list[dict[str, str]] = []
    first_lane = next((lane for lane in lanes if lane.get("status") == "ok"), None)
    if isinstance(first_lane, dict):
        for command in command_strings(first_lane.get("next_commands"))[:3]:
            next_commands.append(command_item(f"first_lane_{len(next_commands) + 1}", command, "continue with the first review lane"))
    next_commands.extend(
        [
            command_item("open_review_workspace", f"less {shell_path(workspace_md_path)}", "inspect the review workspace index"),
            command_item("dry_run_review_workspace", f"{apply_base} --dry-run", "validate all lane answer sheets before applying"),
            command_item("apply_review_workspace", apply_base, "apply all lane answer sheets to review_decisions.jsonl"),
            command_item(
                "dry_run_suggested_review_workspace",
                f"{apply_base} --answers-source suggested --dry-run",
                "validate generated suggested answer sheets before applying",
            ),
            command_item(
                "apply_suggested_review_workspace",
                f"{apply_base} --answers-source suggested",
                "apply generated suggested answer sheets",
            ),
            command_item(
                "refresh_review_progress",
                (
                    "murmurmark review progress "
                    f"--template {shell_path(template_path)} "
                    f"--decisions {shell_path(decisions_path)}"
                ),
                "refresh review progress after applying workspace answers",
            ),
        ]
    )
    open_commands = [
        command_item("open_review_workspace", f"less {shell_path(workspace_md_path)}", "inspect the review workspace index"),
        command_item("open_review_workspace_json", f"less {shell_path(workspace_path)}", "inspect review workspace JSON"),
    ]
    if isinstance(first_lane, dict) and first_lane.get("markdown"):
        open_commands.append(
            command_item(
                "open_first_lane_pack",
                f"less {shell_path(Path(str(first_lane.get('markdown'))))}",
                "inspect the first lane pack",
            )
        )
    return {
        "recommended_next": next_commands[0]["command"] if next_commands else f"less {shell_path(workspace_md_path)}",
        "next_commands": next_commands,
        "open_commands": open_commands,
        "manual_flow": {"dry_run": f"{apply_base} --dry-run", "apply": apply_base},
        "suggested_flow": {
            "dry_run": f"{apply_base} --answers-source suggested --dry-run",
            "apply": f"{apply_base} --answers-source suggested",
        },
        "after_apply": [
            (
                "murmurmark review progress "
                f"--template {shell_path(template_path)} "
                f"--decisions {shell_path(decisions_path)}"
            ),
            "murmurmark review apply",
        ],
    }


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("ё", "е").split())


def review_row_key(row: dict[str, Any]) -> str:
    stable_id = str(row.get("source") or row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(item) for item in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = interval.get("start")
    end = interval.get("end")
    return (
        "review:"
        f"{row.get('session_id') or ''}:"
        f"{stable_id}:"
        f"{utterance_key}:"
        f"{start}:{end}:"
        f"{row.get('label')}"
    )


def semantic_review_row_key(row: dict[str, Any]) -> str:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    text_rows = row.get("text") if isinstance(row.get("text"), list) else []
    text_key = "|".join(
        f"{item.get('source_track') or item.get('role') or ''}:{normalize_text(item.get('text'))}"
        for item in text_rows
        if isinstance(item, dict)
    )
    return (
        "semantic-review:"
        f"{row.get('session_id') or ''}:"
        f"{row.get('source') or ''}:"
        f"{row.get('review_action') or ''}:"
        f"{row.get('label') or ''}:"
        f"{interval.get('start')}:{interval.get('end')}:"
        f"{text_key}"
    )


def review_evidence_identity(row: dict[str, Any]) -> str:
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    text_rows = row.get("text") if isinstance(row.get("text"), list) else []
    payload = {
        "session_id": str(row.get("session_id") or ""),
        "source": str(row.get("source") or ""),
        "cluster_id": str(row.get("cluster_id") or ""),
        "review_action": str(row.get("review_action") or ""),
        "label": str(row.get("label") or ""),
        "utterance_ids": [str(value) for value in row.get("utterance_ids") or []],
        "me_utterance_ids": [str(value) for value in row.get("me_utterance_ids") or []],
        "remote_utterance_ids": [str(value) for value in row.get("remote_utterance_ids") or []],
        "interval": {
            "start": round(float(interval.get("start") or 0.0), 3),
            "end": round(float(interval.get("end") or 0.0), 3),
        },
        "text": [
            {
                "id": str(item.get("id") or ""),
                "role": str(item.get("source_track") or item.get("role") or "").lower(),
                "text": normalize_text(item.get("text")),
            }
            for item in text_rows
            if isinstance(item, dict)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def direct_utterance_keys(row: dict[str, Any]) -> list[str]:
    if str(row.get("source") or "") not in {"audio_review", "transcript_order", "transcript_text"}:
        return []
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    interval_key = f"{interval.get('start')}:{interval.get('end')}"
    text_rows = row.get("text") if isinstance(row.get("text"), list) else []
    keys: list[str] = []
    for item in text_rows:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source_track") or item.get("role") or "").lower()
        role = "mic" if source in {"mic", "me"} else "remote" if source in {"remote", "colleagues"} else ""
        text = normalize_text(item.get("text"))
        if role and text:
            keys.append(f"utterance:{row.get('session_id') or ''}:{interval_key}:{role}:{text}")
    return keys


def obsolete_audit_only_local_recall_keep(row: dict[str, Any]) -> bool:
    return str(row.get("source") or "") == "local_recall" and str(row.get("decision") or "") == "keep_me"


def merge_review_state(template: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(template)
    if existing:
        merged.update({key: existing[key] for key in REVIEW_STATE_FIELDS if key in existing})
    return merged


def unambiguous_review_candidate(
    candidates: list[dict[str, Any]],
    *,
    allowed_decisions: set[str],
    carried_source_ids: set[int],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    available = [candidate for candidate in candidates if id(candidate) not in carried_source_ids]
    decisions = {
        str(candidate.get("decision") or "")
        for candidate in available
        if str(candidate.get("decision") or "") not in {"", "todo"}
    }
    if len(decisions) != 1:
        return None, []
    decision = next(iter(decisions))
    if allowed_decisions and decision not in allowed_decisions:
        return None, []
    matching = [candidate for candidate in available if str(candidate.get("decision") or "") == decision]
    return (matching[0], matching) if matching else (None, [])


def rebase_candidate_rows(
    existing_rows: list[dict[str, Any]],
    history_rows: list[dict[str, Any]],
    history_source_rows: list[dict[str, Any]],
    *,
    enabled: bool,
) -> list[dict[str, Any]]:
    if not enabled:
        return existing_rows
    return existing_rows + history_rows + history_source_rows


def merge_existing_with_report(
    template_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    existing_rows = [row for row in existing_rows if not obsolete_audit_only_local_recall_keep(row)]
    closed_rows = [row for row in existing_rows if str(row.get("decision") or "todo") not in {"", "todo"}]
    existing_by_identity: dict[str, list[dict[str, Any]]] = {}
    semantic_rows: dict[str, list[dict[str, Any]]] = {}
    direct_rows: dict[str, list[dict[str, Any]]] = {}
    for row in closed_rows:
        existing_by_identity.setdefault(review_evidence_identity(row), []).append(row)
        semantic_rows.setdefault(semantic_review_row_key(row), []).append(row)
        for key in direct_utterance_keys(row):
            direct_rows.setdefault(key, []).append(row)

    merged_rows: list[dict[str, Any]] = []
    carried_by_match: Counter[str] = Counter()
    carried_source_ids: set[int] = set()
    for row in template_rows:
        existing = None
        consumed_candidates: list[dict[str, Any]] = []
        match = ""
        allowed = {str(value) for value in row.get("allowed_decisions") or []}
        existing, consumed_candidates = unambiguous_review_candidate(
            existing_by_identity.get(review_evidence_identity(row), []),
            allowed_decisions=allowed,
            carried_source_ids=carried_source_ids,
        )
        if existing is not None:
            match = "evidence_identity"
        if existing is None:
            existing, consumed_candidates = unambiguous_review_candidate(
                semantic_rows.get(semantic_review_row_key(row), []),
                allowed_decisions=allowed,
                carried_source_ids=carried_source_ids,
            )
            if existing is not None:
                match = "semantic_interval_text"
        if existing is None and str(row.get("source") or "") == "transcript_text":
            candidates = list({
                id(candidate): candidate
                for key in direct_utterance_keys(row)
                for candidate in direct_rows.get(key, [])
                if id(candidate) not in carried_source_ids
            }.values())
            direct_allowed = {"keep_me", "drop_me"}
            if allowed:
                direct_allowed &= allowed
            if direct_allowed:
                existing, consumed_candidates = unambiguous_review_candidate(
                    candidates,
                    allowed_decisions=direct_allowed,
                    carried_source_ids=carried_source_ids,
                )
            if existing is not None:
                match = "direct_utterance_interval_text"
        merged = merge_review_state(row, existing)
        if existing is not None:
            carried_source_ids.update(id(candidate) for candidate in consumed_candidates)
            carried_by_match[match] += 1
            merged["review_rebase"] = {
                "schema": "murmurmark.review_decision_rebase_evidence/v1",
                "match": match,
                "source_input_profile": existing.get("input_profile"),
                "source_evidence_identity": review_evidence_identity(existing),
                "target_evidence_identity": review_evidence_identity(row),
            }
        merged_rows.append(merged)
    report = {
        "schema": "murmurmark.review_decisions_rebase/v1",
        "generator": {"name": "build-review-workspace", "version": SCRIPT_VERSION},
        "template_rows": len(template_rows),
        "existing_closed_rows": len(closed_rows),
        "carried_rows": sum(carried_by_match.values()),
        "carried_by_match": dict(sorted(carried_by_match.items())),
        "unmatched_closed_rows": len(closed_rows) - len(carried_source_ids),
    }
    return merged_rows, report


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows, _ = merge_existing_with_report(template_rows, existing_rows)
    return rows


def archive_closed_decisions(
    history_rows: list[dict[str, Any]],
    existing_rows: list[dict[str, Any]],
    *,
    archived_at: str,
) -> tuple[list[dict[str, Any]], int]:
    archived = list(history_rows)

    def history_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            review_evidence_identity(row),
            str(row.get("decision") or ""),
            str(row.get("reviewer") or ""),
            str(row.get("reviewed_at") or ""),
        )

    seen = {history_key(row) for row in archived}
    added = 0
    for row in existing_rows:
        if str(row.get("decision") or "todo") in {"", "todo"}:
            continue
        key = history_key(row)
        if key in seen:
            continue
        entry = dict(row)
        entry["review_history"] = {
            "schema": "murmurmark.review_decision_history/v1",
            "archived_at": archived_at,
            "evidence_identity": key[0],
            "reason": "review_template_rebase",
        }
        archived.append(entry)
        seen.add(key)
        added += 1
    return archived, added


def undecided(row: dict[str, Any]) -> bool:
    return str(row.get("decision") or "todo") in {"", "todo"}


def session_matches(row: dict[str, Any], filters: set[str]) -> bool:
    if not filters:
        return True
    session = str(row.get("session") or "")
    session_id = str(row.get("session_id") or "")
    return bool({session, session_id, f"./{session}"} & filters)


def lane_counts(rows: list[dict[str, Any]], session_filters: set[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not session_matches(row, session_filters) or not undecided(row):
            continue
        lane = str(row.get("review_lane") or "classify_audio")
        grouped.setdefault(lane, []).append(row)
    ordered = [lane for lane in LANE_ORDER if lane in grouped]
    ordered.extend(sorted(lane for lane in grouped if lane not in set(ordered)))
    result: list[dict[str, Any]] = []
    for lane in ordered:
        lane_rows = grouped[lane]
        labels = Counter(str(row.get("label") or "unknown") for row in lane_rows)
        result.append(
            {
                "lane": lane,
                "template_rows": len(lane_rows),
                "labels": dict(sorted(labels.items())),
            }
        )
    return result


def build_lane_pack(
    script: Path,
    template: Path,
    decisions: Path,
    lane: str,
    lane_pack_dir: Path,
    session_filters: set[str],
    silence_sec: float,
) -> dict[str, Any] | None:
    cmd = [
        str(script),
        "--template",
        str(template),
        "--decisions",
        str(decisions),
        "--lane",
        lane,
        "--out-dir",
        str(lane_pack_dir),
        "--silence-sec",
        f"{silence_sec:.3f}",
    ]
    for session in sorted(session_filters):
        cmd.extend(["--session", session])
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    if completed.returncode != 0:
        return {
            "lane": lane,
            "status": "failed",
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip(),
            "stdout": completed.stdout.strip(),
        }
    manifest_path = lane_pack_dir / f"review_lane_pack.{lane}.json"
    if not manifest_path.exists():
        return {"lane": lane, "status": "failed", "reason": "missing_manifest", "manifest": str(manifest_path)}
    manifest = read_json(manifest_path)
    summary = manifest.get("summary") if isinstance(manifest.get("summary"), dict) else {}
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), dict) else {}
    answer_sheet = outputs.get("answer_sheet") or str(write_answer_sheet(lane_pack_dir / f"review_lane_answers.{lane}.txt", manifest))
    suggested_answer_sheet = outputs.get("suggested_answer_sheet")
    return {
        "lane": lane,
        "status": "ok",
        "items": summary.get("item_count", 0),
        "selected_rows": summary.get("selected_rows", summary.get("item_count", 0)),
        "grouped_item_count": summary.get("grouped_item_count", 0),
        "grouped_row_count": summary.get("grouped_row_count", 0),
        "skipped": summary.get("skipped_count", 0),
        "duration_sec": summary.get("duration_sec", 0.0),
        "audio": outputs.get("audio"),
        "manifest": outputs.get("manifest"),
        "markdown": outputs.get("markdown"),
        "answer_sheet": answer_sheet,
        "suggested_answer_sheet": suggested_answer_sheet,
        "recommended_next": manifest.get("recommended_next"),
        "next_commands": manifest.get("next_commands") if isinstance(manifest.get("next_commands"), list) else [],
        "open_commands": manifest.get("open_commands") if isinstance(manifest.get("open_commands"), list) else [],
        "manual_flow": manifest.get("manual_flow") if isinstance(manifest.get("manual_flow"), dict) else {},
        "suggested_flow": manifest.get("suggested_flow") if isinstance(manifest.get("suggested_flow"), dict) else {},
        "after_apply": manifest.get("after_apply") if isinstance(manifest.get("after_apply"), list) else [],
    }


def write_answer_sheet(path: Path, manifest: dict[str, Any]) -> Path:
    items = [item for item in manifest.get("items") or [] if isinstance(item, dict)]
    placeholders = "." * len(items)
    lines = [
        f"# MurmurMark review answers for lane {manifest.get('lane')}",
        "# Listen to the lane WAV, then replace dots in answers=... with decisions.",
        "# d=drop_me, c=drop_remote, k=keep_me, r/?=needs_review, s=skip, ./n/t=todo",
        "# Keep dots for items you have not reviewed yet.",
        f"answers={placeholders}",
        "",
        "# Items",
    ]
    for item in items:
        text = " ".join(str(item.get("text") or "").split())
        lines.append(
            f"# {item.get('index')}: {item.get('pack_start_time')}-{item.get('pack_end_time')} "
            f"{item.get('source_audit_id')} suggested={item.get('suggested_decision')} {text}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def write_markdown(path: Path, workspace: dict[str, Any]) -> None:
    lines = [
        "# MurmurMark Review Workspace",
        "",
        f"Generated: `{workspace.get('generated_at')}`",
        f"Total lanes: `{len(workspace.get('lanes') or [])}`",
        "",
        "## Workflow",
        "",
        "1. Listen to a lane WAV.",
        "2. Use the lane Markdown to map clip numbers to decisions.",
        "3. Edit the lane answer sheet.",
        "4. Apply the answer sheet for that lane.",
        "5. Run the progress report.",
        "6. When all rows are closed, run the batch apply command.",
        "",
        "## Lanes",
        "",
        "| Lane | Rows | Items | Grouped | Duration sec | Skipped | Audio | Index | Answers | Suggested | Apply answers |",
        "|---|---:|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for lane in workspace.get("lanes") or []:
        audio = lane.get("audio") or ""
        markdown = lane.get("markdown") or ""
        manifest = lane.get("manifest") or ""
        answer_sheet = lane.get("answer_sheet") or ""
        suggested_answer_sheet = lane.get("suggested_answer_sheet") or ""
        apply_cmd = (
            f".venv/bin/python scripts/apply-review-lane-pack-decisions.py {shlex.quote(str(manifest))} "
            f"--answers-file {shlex.quote(str(answer_sheet))} --out sessions/_reports/review-plan/review_decisions.jsonl"
            if manifest and answer_sheet
            else ""
        )
        lines.append(
            f"| `{lane.get('lane')}` | {lane.get('selected_rows')} | {lane.get('items')} | "
            f"{lane.get('grouped_row_count')} | {lane.get('duration_sec')} | {lane.get('skipped')} | "
            f"`{audio}` | `{markdown}` | `{answer_sheet}` | `{suggested_answer_sheet}` | `{apply_cmd}` |"
        )
    lines.extend(
        [
            "",
            "## Commands",
            "",
            "```bash",
            ".venv/bin/python scripts/report-review-decisions-progress.py \\",
            "  --decisions sessions/_reports/review-plan/review_decisions.jsonl",
            "",
            ".venv/bin/python scripts/apply-review-decisions-batch.py \\",
            "  --decisions sessions/_reports/review-plan/review_decisions.jsonl \\",
            "  --review-template sessions/_reports/review-plan/review_decisions.template.jsonl \\",
            "  --synthesize \\",
            "  --refresh-reports",
            "```",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    template = args.template.expanduser()
    decisions = args.decisions.expanduser()
    out_dir = args.out_dir.expanduser()
    existing_rows = read_jsonl(decisions)
    history_path = out_dir / "review_decisions_history.jsonl"
    existing_history = read_jsonl(history_path) if args.rebase_decisions else []
    history_source_rows = (
        [
            row
            for source in args.history_source
            for row in read_jsonl(source.expanduser())
        ]
        if args.rebase_decisions
        else []
    )
    candidate_rows = rebase_candidate_rows(
        existing_rows,
        existing_history,
        history_source_rows,
        enabled=args.rebase_decisions,
    )
    rows, rebase_report = merge_existing_with_report(read_jsonl(template), candidate_rows)
    if args.rebase_decisions:
        generated_at = datetime.now(timezone.utc).isoformat()
        history_rows, archived_rows = archive_closed_decisions(
            existing_history,
            existing_rows + history_source_rows,
            archived_at=generated_at,
        )
        write_jsonl_atomic(history_path, history_rows)
        write_jsonl_atomic(decisions, rows)
        rebase_report["generated_at"] = generated_at
        rebase_report["template"] = str(template)
        rebase_report["decisions"] = str(decisions)
        rebase_report["history"] = str(history_path)
        rebase_report["history_rows"] = len(history_rows)
        rebase_report["history_candidate_rows"] = len(existing_history)
        rebase_report["history_source_rows"] = len(history_source_rows)
        rebase_report["archived_rows_this_run"] = archived_rows
        write_json(out_dir / "review_decisions_rebase.json", rebase_report)
    session_filters = {item.strip() for item in args.session if item.strip()}
    counts = lane_counts(rows, session_filters)
    lane_pack_dir = out_dir / "lane-packs"
    script = Path(__file__).resolve().parent / "build-review-lane-pack.py"
    lanes = [
        build_lane_pack(script, template, decisions, str(row["lane"]), lane_pack_dir, session_filters, args.silence_sec)
        for row in counts
    ]
    lanes = [lane for lane in lanes if isinstance(lane, dict)]
    workspace_path = out_dir / "review_workspace.json"
    workspace_md_path = out_dir / "review_workspace.md"
    workspace_apply_report = out_dir / "review_workspace_apply_report.json"
    workspace = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": {"name": "build-review-workspace", "version": SCRIPT_VERSION},
        "inputs": {
            "template": str(template),
            "decisions": str(decisions) if decisions.exists() else None,
        },
        "parameters": {
            "session_filters": sorted(session_filters),
            "silence_sec": args.silence_sec,
        },
        "lane_counts": counts,
        "lanes": lanes,
        "outputs": {
            "workspace_json": str(workspace_path),
            "workspace_markdown": str(workspace_md_path),
            "lane_pack_dir": str(lane_pack_dir),
        },
    }
    workspace.update(
        workspace_handoff(
            workspace_path=workspace_path,
            workspace_md_path=workspace_md_path,
            template_path=template,
            decisions_path=decisions,
            report_path=workspace_apply_report,
            lanes=lanes,
        )
    )
    write_json(workspace_path, workspace)
    write_markdown(workspace_md_path, workspace)
    print(f"workspace: {workspace_path}")
    print(f"lanes: {len(lanes)}")
    failed = [lane for lane in lanes if lane.get("status") != "ok"]
    if failed:
        print(f"failed_lanes: {len(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
