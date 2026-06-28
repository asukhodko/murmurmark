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


SCRIPT_VERSION = "0.2.0"
SCHEMA_MANIFEST = "murmurmark.export_manifest/v1"


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


def blocked_export_next(session: Path, readiness: dict[str, Any] | None, blockers: list[str]) -> str:
    commands: list[str] = []
    if readiness:
        for item in readiness.get("next_commands") or []:
            if isinstance(item, dict) and item.get("command"):
                commands.append(str(item["command"]))
    if commands:
        rendered = "; ".join(f"`{command}`" for command in commands)
        return f"{rendered}; rerun export after blockers are closed, or use --force only for debugging"

    session_arg = command_path(session)
    if "pipeline_incomplete" in blockers or any(str(item).startswith("missing:") for item in blockers):
        return f"run `murmurmark process {session_arg}` and rerun export, or use --force only for debugging"
    return (
        "`murmurmark review plan`; "
        f"`murmurmark review workspace --session {session_arg}`; "
        "`murmurmark review workspace apply`; "
        "then rerun export, or use --force only for debugging"
    )


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


def write_markdown_index(
    path: Path,
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    quality: dict[str, Any] | None,
    warnings: list[str],
) -> None:
    lines = [
        f"# MurmurMark Export: {sid}",
        "",
        f"- Transcript profile: `{profile}`",
        f"- Verdict: `{(verdict or {}).get('verdict', 'unknown')}`",
        f"- Use gate: `{(quality or {}).get('use_gate', 'unknown')}`",
        f"- Exported at: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Files",
        "",
        "- [Quality verdict](quality_verdict.md)",
        "- [Notes](notes.md)",
        "- [Transcript](transcript.md)",
        "- [Manifest](export_manifest.json)",
    ]
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{item}`" for item in warnings)
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_obsidian_note(
    path: Path,
    *,
    sid: str,
    profile: str,
    verdict: dict[str, Any] | None,
    quality: dict[str, Any] | None,
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
    body = [f"# {sid}", ""]
    if warnings:
        body.extend(["## Export Warnings", ""])
        body.extend(f"- `{item}`" for item in warnings)
        body.append("")
    body.extend(["## Quality Verdict", "", strip_heading(quality_text), "## Notes", "", strip_heading(notes), "## Transcript", "", strip_heading(transcript)])
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
    blockers, warnings = readiness_blockers(paths, quality, verdict, readiness)
    if blockers and not args.force:
        manifest = {
            "schema": SCHEMA_MANIFEST,
            "generator": {"name": "export-session-bundle", "version": SCRIPT_VERSION},
            "status": "blocked",
            "session_id": sid,
            "requested_profile": args.profile,
            "selected_profile": profile,
            "format": args.format,
            "blockers": blockers,
            "warnings": warnings,
            "readiness": readiness,
            "next": blocked_export_next(session, readiness, blockers),
        }
        args.out_dir.mkdir(parents=True, exist_ok=True)
        blocked_path = args.out_dir / f"{session.name}.export_blocked.json"
        write_json(blocked_path, manifest)
        print(f"export blocked: {blocked_path}")
        print("blockers: " + ", ".join(blockers))
        print("next: " + str(manifest["next"]))
        raise SystemExit(2)

    out_dir = args.out_dir / session.name
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, Any] = {}
    if args.format == "markdown":
        index = out_dir / "index.md"
        write_markdown_index(index, sid=sid, profile=profile, verdict=verdict, quality=quality, warnings=warnings)
        files["index"] = {"path": str(index), "bytes": index.stat().st_size}
        files["quality_verdict_md"] = copy_file(paths["quality_verdict_md"], out_dir / "quality_verdict.md")
        files["notes_md"] = copy_file(paths["notes_md"], out_dir / "notes.md")
        files["transcript_md"] = copy_file(paths["transcript_md"], out_dir / "transcript.md")
    else:
        obsidian = out_dir / f"{sid}.md"
        write_obsidian_note(
            obsidian,
            sid=sid,
            profile=profile,
            verdict=verdict,
            quality=quality,
            warnings=warnings,
            notes=read_text(paths["notes_md"]),
            transcript=read_text(paths["transcript_md"]),
            quality_text=read_text(paths["quality_verdict_md"]),
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

    manifest = {
        "schema": SCHEMA_MANIFEST,
        "generator": {"name": "export-session-bundle", "version": SCRIPT_VERSION},
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
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(out_dir / "export_manifest.json", manifest)
    print(f"exported: {out_dir}")
    print(f"status: {manifest['status']}")
    print(f"profile: {profile}")
    return manifest


def main() -> int:
    export_session(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
