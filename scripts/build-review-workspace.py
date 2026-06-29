#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.4.0"
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
    cluster_id = str(row.get("cluster_id") or "").strip()
    utterance_ids = row.get("utterance_ids")
    utterance_key = ",".join(str(item) for item in utterance_ids) if isinstance(utterance_ids, list) else ""
    interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = interval.get("start")
    end = interval.get("end")
    return (
        "review:"
        f"{row.get('session_id') or ''}:"
        f"{cluster_id}:"
        f"{utterance_key}:"
        f"{start}:{end}:"
        f"{row.get('label')}"
    )


def merge_existing(template_rows: list[dict[str, Any]], existing_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_key = {review_row_key(row): row for row in existing_rows}
    return [{**row, **existing_by_key.get(review_row_key(row), {})} for row in template_rows]


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
    rows = merge_existing(read_jsonl(template), read_jsonl(decisions))
    session_filters = {item.strip() for item in args.session if item.strip()}
    counts = lane_counts(rows, session_filters)
    out_dir = args.out_dir.expanduser()
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
