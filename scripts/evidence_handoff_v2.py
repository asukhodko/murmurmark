#!/usr/bin/env python3
"""Build and verify the deterministic Evidence Notes and Export v2 handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterable


SCRIPT_VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parents[1]
SPEAKER_SELECTION_SCHEMA = "murmurmark.speaker_resolved_transcript_selection/v1"
SPEAKER_SELECTOR = ROOT / "scripts/select-speaker-resolved-transcript.py"
MANIFEST_SCHEMA = "murmurmark.handoff_manifest/v2"
EVIDENCE_SCHEMA = "murmurmark.handoff_evidence/v2"
READINESS_SCHEMA = "murmurmark.session_readiness/v1"
QUALITY_SCHEMA = "murmurmark.quality_verdict/v1"
NOTES_SCHEMA = "murmurmark.evidence_notes/v2"
DIALOGUE_SCHEMA = "murmurmark.clean_dialogue/v1"
VALID_STATES = {"ready", "review_required", "blocked", "no_speech"}
EXPORTABLE_STATES = {"ready", "no_speech"}
LOCAL_PATH_RE = re.compile(r"/Users/[^\s`\"'<>]+")


class HandoffError(RuntimeError):
    pass


class SimulatedInterruption(HandoffError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify a deterministic MurmurMark handoff bundle."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    parser.add_argument(
        "--simulate-interruption-before-publish",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError) as error:
        return None, f"invalid:{type(error).__name__}"
    if not isinstance(value, dict):
        return None, "invalid:not_object"
    return value, None


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], "missing"
    except OSError as error:
        return [], f"invalid:{type(error).__name__}"
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return [], f"invalid_json_line:{number}"
        if not isinstance(value, dict):
            return [], f"invalid_row:{number}"
        rows.append(value)
    return rows, None


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_durable(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    write_durable(temporary, data)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def session_relative(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def resolve_session_path(session: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else session / candidate
    resolved = candidate.resolve()
    if not within(resolved, session.resolve()):
        return None
    return resolved


def readiness_output_path(
    readiness: dict[str, Any], key: str, session: Path
) -> Path | None:
    outputs = readiness.get("outputs")
    if not isinstance(outputs, dict):
        return None
    row = outputs.get(key)
    if not isinstance(row, dict):
        return None
    return resolve_session_path(session, row.get("path"))


def profile_suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def quality_json_path(session: Path, profile: str) -> Path:
    return (
        session
        / "derived/synthesis-simple/extractive"
        / f"quality_verdict{profile_suffix(profile)}.json"
    )


def source_identity(path: Path, session: Path, schema: str | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    row: dict[str, Any] = {
        "path": session_relative(path, session),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }
    if schema is not None:
        row["schema"] = schema
    return row


def output_identity(path: str, data: bytes) -> dict[str, Any]:
    return {"path": path, "bytes": len(data), "sha256": sha256_bytes(data)}


def dedupe_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        value = value.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sanitize_text(value: Any, session: Path) -> str:
    text = str(value or "").replace(str(session.resolve()), "$SESSION")
    text = LOCAL_PATH_RE.sub("[local-path]", text)
    return re.sub(r"\s+", " ", text).strip()


def time_label(seconds: Any) -> str:
    total = max(0, int(safe_float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def evidence_ids(row: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in (
        "evidence_utterance_ids",
        "representative_utterance_ids",
        "utterance_ids",
        "context_utterance_ids",
    ):
        value = row.get(key)
        if isinstance(value, list):
            values.extend(value)
    if isinstance(row.get("utterance_id"), str):
        values.append(row["utterance_id"])
    representatives = row.get("representatives")
    if isinstance(representatives, list):
        for representative in representatives:
            if isinstance(representative, dict):
                values.append(representative.get("utterance_id"))
    return dedupe_strings(values)


def compact_claim(
    row: dict[str, Any], category: str, session: Path
) -> dict[str, Any]:
    interval = row.get("time") if isinstance(row.get("time"), dict) else {}
    return {
        "id": sanitize_text(row.get("id") or f"{category}_item", session),
        "category": category,
        "text": sanitize_text(row.get("display_text") or row.get("text"), session),
        "evidence_utterance_ids": dedupe_strings(
            row.get("evidence_utterance_ids")
            if isinstance(row.get("evidence_utterance_ids"), list)
            else row.get("utterance_ids") if isinstance(row.get("utterance_ids"), list) else []
        ),
        "context_utterance_ids": dedupe_strings(
            row.get("context_utterance_ids")
            if isinstance(row.get("context_utterance_ids"), list)
            else []
        ),
        "start": safe_float(interval.get("start", row.get("start"))),
        "end": safe_float(interval.get("end", row.get("end"))),
        "needs_review": bool(row.get("needs_review", True)),
    }


def compact_outline(row: dict[str, Any], session: Path) -> dict[str, Any]:
    representatives: list[dict[str, Any]] = []
    raw_representatives = row.get("representatives")
    if isinstance(raw_representatives, list):
        for representative in raw_representatives:
            if not isinstance(representative, dict):
                continue
            representatives.append(
                {
                    "utterance_id": sanitize_text(representative.get("utterance_id"), session),
                    "role": sanitize_text(representative.get("role"), session),
                    "start": safe_float(representative.get("start")),
                    "end": safe_float(representative.get("end")),
                    "text": sanitize_text(representative.get("text"), session),
                }
            )
    representative_ids = dedupe_strings(
        row.get("representative_utterance_ids")
        if isinstance(row.get("representative_utterance_ids"), list)
        else []
    )
    representative_ids = dedupe_strings(
        representative_ids + [item.get("utterance_id") for item in representatives]
    )
    return {
        "id": sanitize_text(row.get("id") or "topic", session),
        "start": safe_float(row.get("start")),
        "end": safe_float(row.get("end")),
        "keywords": dedupe_strings(row.get("keywords") if isinstance(row.get("keywords"), list) else []),
        "representative_utterance_ids": representative_ids,
        "representatives": representatives,
    }


def compact_review_item(row: dict[str, Any], session: Path) -> dict[str, Any]:
    return {
        "type": sanitize_text(row.get("type") or "review", session),
        "severity": sanitize_text(row.get("severity") or "unknown", session),
        "start": safe_float(row.get("start")),
        "end": safe_float(row.get("end")),
        "utterance_ids": dedupe_strings(
            row.get("utterance_ids") if isinstance(row.get("utterance_ids"), list) else []
        ),
        "reason": sanitize_text(row.get("reason"), session),
        "text": sanitize_text(row.get("text"), session),
    }


def dialogue_utterances(dialogue: dict[str, Any], session: Path) -> list[dict[str, Any]]:
    rows = dialogue.get("utterances")
    if not isinstance(rows, list):
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "id": sanitize_text(row.get("id"), session),
                "start": safe_float(row.get("start")),
                "end": safe_float(row.get("end")),
                "role": sanitize_text(row.get("speaker_label") or row.get("role"), session),
                "text": sanitize_text(row.get("text"), session),
            }
        )
    return result


def render_transcript(
    session_id: str,
    profile: str,
    state: str,
    utterances: list[dict[str, Any]],
) -> str:
    lines = [
        "# Transcript",
        "",
        f"Session: `{session_id}`  ",
        f"Profile: `{profile or 'unknown'}`  ",
        f"Handoff state: `{state}`",
        "",
    ]
    if not utterances:
        lines.extend(["No speech was verified in this session.", ""])
        return "\n".join(lines)
    for row in utterances:
        lines.extend(
            [
                f"## {time_label(row['start'])} {row['role']} [{row['id']}]",
                "",
                row["text"],
                "",
            ]
        )
    return "\n".join(lines)


def claim_line(row: dict[str, Any]) -> str:
    ids = ", ".join(f"`{item}`" for item in row["evidence_utterance_ids"])
    review = " `needs_review`" if row.get("needs_review") else ""
    return f"- {row['text']} ({ids}){review}"


def render_notes(
    session_id: str,
    profile: str,
    state: str,
    outline: list[dict[str, Any]],
    claims: dict[str, list[dict[str, Any]]],
) -> str:
    lines = [
        "# Evidence Notes",
        "",
        f"Session: `{session_id}`  ",
        f"Profile: `{profile or 'unknown'}`  ",
        f"Handoff state: `{state}`",
        "",
        "## Conversation Outline",
        "",
    ]
    if not outline:
        lines.append("- No evidence-backed outline items.")
    for block in outline:
        title = ", ".join(block["keywords"][:4]) or "discussion block"
        lines.append(f"### {time_label(block['start'])}-{time_label(block['end'])}: {title}")
        lines.append("")
        for representative in block["representatives"]:
            lines.append(
                f"- {representative['text']} (`{representative['utterance_id']}`)"
            )
        if not block["representatives"]:
            ids = ", ".join(f"`{item}`" for item in block["representative_utterance_ids"])
            lines.append(f"- Evidence: {ids}")
        lines.append("")
    headings = (
        ("decisions", "Potential Decisions"),
        ("actions", "Potential Actions"),
        ("risks", "Risks"),
        ("open_questions", "Open Questions"),
    )
    for key, heading in headings:
        lines.extend([f"## {heading}", ""])
        rows = claims.get(key, [])
        lines.extend(claim_line(row) for row in rows)
        if not rows:
            lines.append("- None selected.")
        lines.append("")
    return "\n".join(lines)


def render_verdict(
    session_id: str,
    profile: str,
    state: str,
    verdict: str,
    review: dict[str, Any],
    blockers: list[str],
    warnings: list[str],
) -> str:
    lines = [
        "# Quality Verdict",
        "",
        f"- Session: `{session_id}`",
        f"- Profile: `{profile or 'unknown'}`",
        f"- Handoff state: `{state}`",
        f"- Transcript verdict: `{verdict}`",
        f"- Mandatory review: `{review['mandatory_count']}` items / `{review['mandatory_seconds']:.2f}s`",
        "",
    ]
    if blockers:
        lines.extend(["## Blockers", ""] + [f"- `{item}`" for item in blockers] + [""])
    if warnings:
        lines.extend(["## Warnings", ""] + [f"- `{item}`" for item in warnings] + [""])
    return "\n".join(lines)


def render_meeting(
    session_id: str,
    profile: str,
    state: str,
    verdict: str,
    review: dict[str, Any],
    outline: list[dict[str, Any]],
    claims: dict[str, list[dict[str, Any]]],
    speaker_resolution: dict[str, Any] | None = None,
) -> str:
    speaker_resolution = speaker_resolution or {}
    lines = [
        f"# {session_id}",
        "",
        f"- Handoff state: `{state}`",
        f"- Transcript profile: `{profile or 'unknown'}`",
        f"- Speaker profile: `{speaker_resolution.get('selected_speaker_profile') or 'aggregate_colleagues'}`",
        f"- Quality verdict: `{verdict}`",
        f"- Mandatory review: `{review['mandatory_count']}` items / `{review['mandatory_seconds']:.2f}s`",
        "",
        "Artifacts: [Transcript](transcript.md) | [Evidence Notes](notes.md) | "
        "[Quality Verdict](quality_verdict.md) | [Evidence JSON](handoff_evidence.json)",
        "",
        "## Evidence Summary",
        "",
        f"- Outline blocks: `{len(outline)}`",
        f"- Potential decisions: `{len(claims.get('decisions', []))}`",
        f"- Potential actions: `{len(claims.get('actions', []))}`",
        f"- Risks: `{len(claims.get('risks', []))}`",
        f"- Open questions: `{len(claims.get('open_questions', []))}`",
        "",
    ]
    return "\n".join(lines)


def materialize_speaker_selection(session: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not SPEAKER_SELECTOR.is_file():
        return None, "speaker_selector_missing"
    completed = subprocess.run(
        [os.sys.executable, str(SPEAKER_SELECTOR), str(session)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        return None, "speaker_selector_failed"
    path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    payload, error = read_json(path)
    if error or payload is None or payload.get("schema") != SPEAKER_SELECTION_SCHEMA:
        return None, f"speaker_selection:{error or 'unsupported_schema'}"
    return payload, None


def selected_speaker_transcript(
    session: Path,
    profile: str,
    aggregate: Path,
    selection: dict[str, Any] | None,
) -> tuple[dict[str, Any], Path | None, str | None]:
    fallback = {
        "state": "fallback",
        "selected_speaker_profile": "aggregate_colleagues",
        "fallback_reason": "speaker_selection_unavailable",
        "identity_scope": "session_local_anonymous",
    }
    if selection is None:
        return fallback, None, "speaker_selection_unavailable"
    if str(selection.get("selected_profile") or "") != profile:
        fallback["fallback_reason"] = "speaker_selection_profile_mismatch"
        return fallback, None, "speaker_selection_profile_mismatch"
    state = str(selection.get("state") or "")
    row = selection.get("selected_transcript")
    path = resolve_session_path(session, row.get("path") if isinstance(row, dict) else None)
    if path is None or not path.is_file() or not isinstance(row, dict):
        fallback["fallback_reason"] = "speaker_selection_output_missing"
        return fallback, None, "speaker_selection_output_missing"
    data = path.read_bytes()
    if safe_int(row.get("bytes"), -1) != len(data) or row.get("sha256") != sha256_bytes(data):
        fallback["fallback_reason"] = "speaker_selection_output_stale"
        return fallback, None, "speaker_selection_output_stale"
    if state == "selected" and selection.get("selected_speaker_profile") == "remote_speaker_coverage_v3":
        completed = subprocess.run(
            [
                os.sys.executable,
                str(SPEAKER_SELECTOR),
                str(session),
                "--verify-only",
                "--require-speaker-resolved",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            return {
                "state": "selected",
                "selected_speaker_profile": "remote_speaker_coverage_v3",
                "fallback_reason": None,
                "identity_scope": "session_local_anonymous",
                "selection_fingerprint": selection.get("semantic_fingerprint"),
            }, path, None
        fallback["fallback_reason"] = "speaker_selection_verification_failed"
        return fallback, None, "speaker_selection_verification_failed"
    if state == "fallback" and path.resolve() == aggregate.resolve():
        fallback["fallback_reason"] = str(selection.get("fallback_reason") or "speaker_evidence_unavailable")
        fallback["selection_fingerprint"] = selection.get("semantic_fingerprint")
        return fallback, aggregate, None
    fallback["fallback_reason"] = "speaker_selection_fallback_not_exact"
    return fallback, None, "speaker_selection_fallback_not_exact"


def collect_inputs(session: Path) -> tuple[dict[str, Any], dict[str, Path], list[str]]:
    blockers: list[str] = []
    readiness_path = session / "derived/readiness/session_readiness.json"
    readiness, error = read_json(readiness_path)
    if error:
        blockers.append(f"readiness:{error}")
        readiness = {}
    elif readiness.get("schema") != READINESS_SCHEMA:
        blockers.append("readiness:unsupported_schema")

    profile = str(readiness.get("selected_profile") or "").strip()
    if not profile:
        blockers.append("selected_profile_missing")

    paths: dict[str, Path] = {"readiness": readiness_path}
    for key in ("transcript", "clean_dialogue", "notes", "evidence_notes", "review_items"):
        path = readiness_output_path(readiness, key, session)
        if path is None:
            blockers.append(f"readiness_output_missing:{key}")
        else:
            paths[key] = path
    verdict_md = readiness_output_path(readiness, "quality_verdict", session)
    if verdict_md is None:
        blockers.append("readiness_output_missing:quality_verdict")
    else:
        paths["quality_verdict_md"] = verdict_md
    if profile:
        paths["quality_verdict_json"] = quality_json_path(session, profile)
    session_json = session / "session.json"
    paths["session"] = session_json
    selection, _ = materialize_speaker_selection(session)
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    if selection is not None and selection_path.is_file():
        paths["speaker_selection"] = selection_path
    for key, path in paths.items():
        if not path.is_file():
            blockers.append(f"input_missing:{key}")
    return readiness, paths, dedupe_strings(blockers)


def classify_state(
    readiness: dict[str, Any],
    verdict: str,
    utterances: list[dict[str, Any]],
    blockers: list[str],
) -> tuple[str, dict[str, Any]]:
    metrics = readiness.get("metrics") if isinstance(readiness.get("metrics"), dict) else {}
    required = safe_int(metrics.get("review_scope_required_rows"))
    closed = safe_int(metrics.get("review_scope_closed_rows"))
    mandatory_seconds = max(
        safe_float(metrics.get("review_burden_sec")),
        safe_float(metrics.get("transcript_review_burden_sec")),
    )
    mandatory_count = max(0, required - closed)
    if mandatory_seconds > 0.001 and mandatory_count == 0:
        mandatory_count = 1
    export_blockers = dedupe_strings(readiness.get("export_blockers") or [])
    review_blockers = dedupe_strings(readiness.get("review_blockers") or [])
    review = {
        "mandatory_count": mandatory_count,
        "mandatory_seconds": round(mandatory_seconds, 3),
        "export_blockers": export_blockers,
        "review_blockers": review_blockers,
    }
    if blockers or verdict in {"failed", "risky", "unknown", ""}:
        return "blocked", review
    classification = str(readiness.get("session_classification") or "conversation")
    if classification == "verified_no_speech":
        if utterances:
            return "blocked", review
        if verdict == "good" and not export_blockers and not review_blockers:
            return "no_speech", review
        return "review_required", review
    use_gate = str(readiness.get("use_gate") or "pipeline_incomplete")
    if (
        verdict != "good"
        or use_gate != "ready_for_notes"
        or mandatory_count > 0
        or mandatory_seconds > 0.001
        or export_blockers
        or review_blockers
    ):
        return "review_required", review
    return "ready", review


def build_handoff(
    session: Path,
    *,
    simulate_interruption_before_publish: bool = False,
) -> dict[str, Any]:
    session = session.expanduser().resolve()
    if not (session / "session.json").is_file():
        raise HandoffError(f"session.json not found under {session}")

    readiness, paths, blockers = collect_inputs(session)
    warnings: list[str] = []
    profile = str(readiness.get("selected_profile") or "").strip()
    session_payload, session_error = read_json(paths["session"])
    session_payload = session_payload or {}
    if session_error:
        blockers.append(f"session:{session_error}")
    session_id = sanitize_text(session_payload.get("session_id") or session.name, session)

    parsed: dict[str, dict[str, Any]] = {}
    expected_schemas = {
        "readiness": READINESS_SCHEMA,
        "quality_verdict_json": QUALITY_SCHEMA,
        "evidence_notes": NOTES_SCHEMA,
        "clean_dialogue": DIALOGUE_SCHEMA,
        "speaker_selection": SPEAKER_SELECTION_SCHEMA,
    }
    for key, expected_schema in expected_schemas.items():
        path = paths.get(key)
        if path is None:
            continue
        payload, error = read_json(path)
        if error:
            blockers.append(f"{key}:{error}")
            continue
        assert payload is not None
        parsed[key] = payload
        if payload.get("schema") != expected_schema:
            blockers.append(f"{key}:unsupported_schema")

    quality = parsed.get("quality_verdict_json", {})
    evidence = parsed.get("evidence_notes", {})
    dialogue = parsed.get("clean_dialogue", {})
    speaker_selection, speaker_selection_error = materialize_speaker_selection(session)
    speaker_resolution, speaker_transcript_path, speaker_resolution_warning = selected_speaker_transcript(
        session,
        profile,
        paths.get("transcript", session / "missing-transcript"),
        speaker_selection,
    )
    if speaker_selection_error:
        warnings.append(speaker_selection_error)
    if speaker_resolution_warning:
        warnings.append(speaker_resolution_warning)
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    if selection_path.is_file():
        paths["speaker_selection"] = selection_path
    if speaker_resolution.get("state") == "selected" and speaker_transcript_path is not None:
        paths["speaker_transcript"] = speaker_transcript_path
    verdict = str(readiness.get("verdict") or quality.get("verdict") or "unknown")
    if profile and str(quality.get("selected_transcript_profile") or "") != profile:
        blockers.append("quality_profile_mismatch")
    source = evidence.get("source") if isinstance(evidence.get("source"), dict) else {}
    if profile and str(source.get("transcript_profile") or "") != profile:
        blockers.append("evidence_profile_mismatch")

    review_rows: list[dict[str, Any]] = []
    review_path = paths.get("review_items")
    if review_path is not None:
        review_rows, review_error = read_jsonl(review_path)
        if review_error:
            blockers.append(f"review_items:{review_error}")

    utterances = dialogue_utterances(dialogue, session)
    utterance_ids = [row["id"] for row in utterances if row["id"]]
    if len(utterance_ids) != len(set(utterance_ids)):
        blockers.append("duplicate_utterance_ids")
    if any(not row["id"] for row in utterances):
        blockers.append("empty_utterance_id")
    known_ids = set(utterance_ids)

    selected = evidence.get("selected") if isinstance(evidence.get("selected"), dict) else {}
    outline = [
        compact_outline(row, session)
        for row in selected.get("outline_blocks", [])
        if isinstance(row, dict)
    ]
    claims: dict[str, list[dict[str, Any]]] = {}
    for category in ("decisions", "actions", "risks", "open_questions"):
        claims[category] = [
            compact_claim(row, category, session)
            for row in selected.get(category, [])
            if isinstance(row, dict)
        ]

    referenced_ids: set[str] = set()
    missing_references: set[str] = set()
    empty_reference_items: list[str] = []
    for block in outline:
        ids = block["representative_utterance_ids"]
        if not ids:
            empty_reference_items.append(block["id"])
        referenced_ids.update(ids)
        missing_references.update(set(ids) - known_ids)
    for category, rows in claims.items():
        for row in rows:
            ids = row["evidence_utterance_ids"]
            if not ids:
                empty_reference_items.append(f"{category}:{row['id']}")
            referenced_ids.update(ids)
            missing_references.update(set(ids) - known_ids)
            context_ids = set(row["context_utterance_ids"])
            referenced_ids.update(context_ids)
            missing_references.update(context_ids - known_ids)
    compact_review = [compact_review_item(row, session) for row in review_rows]
    for row in compact_review:
        ids = set(row["utterance_ids"])
        referenced_ids.update(ids)
        missing_references.update(ids - known_ids)
    if empty_reference_items:
        blockers.append("selected_item_without_evidence")
    if missing_references:
        blockers.append("unknown_evidence_utterance_id")

    blockers = dedupe_strings(blockers)
    state, review = classify_state(readiness, verdict, utterances, blockers)
    if (
        str(readiness.get("session_classification") or "") == "verified_no_speech"
        and utterances
    ):
        blockers.append("verified_no_speech_has_utterances")
        state = "blocked"
    blockers = dedupe_strings(blockers)

    source_files: dict[str, dict[str, Any]] = {}
    for key, path in sorted(paths.items()):
        if not path.is_file():
            continue
        schema = expected_schemas.get(key)
        source_files[key] = source_identity(path, session, schema)

    evidence_utterances = [row for row in utterances if row["id"] in referenced_ids]
    handoff_evidence = {
        "schema": EVIDENCE_SCHEMA,
        "version": 2,
        "generator": {
            "name": "evidence_handoff_v2",
            "version": SCRIPT_VERSION,
            "mode": "deterministic",
        },
        "session_id": session_id,
        "state": state,
        "selected_profile": profile or None,
        "verdict": verdict,
        "outline": outline,
        "claims": claims,
        "evidence_utterances": evidence_utterances,
        "review": {**review, "items": compact_review},
        "referential_integrity": {
            "passed": not missing_references and not empty_reference_items,
            "known_utterance_count": len(known_ids),
            "referenced_utterance_count": len(referenced_ids),
            "missing_utterance_ids": sorted(missing_references),
            "items_without_evidence": sorted(empty_reference_items),
        },
        "speaker_resolution": speaker_resolution,
    }

    aggregate_transcript = render_transcript(session_id, profile, state, utterances).encode("utf-8")
    transcript_output = (
        speaker_transcript_path.read_bytes()
        if speaker_transcript_path is not None
        else aggregate_transcript
    )

    output_payloads = {
        "handoff_evidence": canonical_json_bytes(handoff_evidence),
        "meeting": render_meeting(
            session_id, profile, state, verdict, review, outline, claims, speaker_resolution
        ).encode("utf-8"),
        "transcript": transcript_output,
        "notes": render_notes(
            session_id, profile, state, outline, claims
        ).encode("utf-8"),
        "quality_verdict": render_verdict(
            session_id, profile, state, verdict, review, blockers, warnings
        ).encode("utf-8"),
    }
    filenames = {
        "handoff_evidence": "handoff_evidence.json",
        "meeting": "meeting.md",
        "transcript": "transcript.md",
        "notes": "notes.md",
        "quality_verdict": "quality_verdict.md",
    }
    for name, data in output_payloads.items():
        decoded = data.decode("utf-8")
        if str(session) in decoded or LOCAL_PATH_RE.search(decoded):
            blockers.append(f"private_path_in_output:{name}")
            state = "blocked"
    blockers = dedupe_strings(blockers)
    handoff_evidence["state"] = state
    handoff_evidence["review"] = {**review, "items": compact_review}
    output_payloads["handoff_evidence"] = canonical_json_bytes(handoff_evidence)
    output_payloads["meeting"] = render_meeting(
        session_id, profile, state, verdict, review, outline, claims, speaker_resolution
    ).encode("utf-8")
    output_payloads["transcript"] = transcript_output
    output_payloads["notes"] = render_notes(
        session_id, profile, state, outline, claims
    ).encode("utf-8")
    output_payloads["quality_verdict"] = render_verdict(
        session_id, profile, state, verdict, review, blockers, warnings
    ).encode("utf-8")

    provisional_outputs = {
        name: {
            "filename": filenames[name],
            "bytes": len(data),
            "sha256": sha256_bytes(data),
        }
        for name, data in sorted(output_payloads.items())
    }
    session_ref = f"sessions/{session.name}"
    recommended_next = {
        "ready": f'murmurmark export "{session_ref}" --format markdown --include-json',
        "no_speech": f'murmurmark export "{session_ref}" --format markdown --include-json',
        "review_required": f'murmurmark review workspace --session "{session_ref}"',
        "blocked": f'murmurmark process "{session_ref}"',
    }[state]
    basis = {
        "schema": MANIFEST_SCHEMA,
        "version": 2,
        "session_id": session_id,
        "state": state,
        "selected_profile": profile or None,
        "verdict": verdict,
        "use_gate": readiness.get("use_gate"),
        "session_classification": readiness.get("session_classification") or "conversation",
        "inputs": source_files,
        "outputs": provisional_outputs,
        "review": review,
        "referential_integrity": handoff_evidence["referential_integrity"],
        "speaker_resolution": speaker_resolution,
        "blockers": blockers,
        "warnings": warnings,
        "recommended_next": recommended_next,
    }
    fingerprint = sha256_bytes(compact_json_bytes(basis))
    root = session / "derived/handoff-v2"
    bundle_relative = f"derived/handoff-v2/bundles/{fingerprint}"
    bundle = session / bundle_relative
    files = {
        name: output_identity(f"{bundle_relative}/{filenames[name]}", data)
        for name, data in sorted(output_payloads.items())
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": 2,
        "generator": {
            "name": "evidence_handoff_v2",
            "version": SCRIPT_VERSION,
            "mode": "deterministic",
        },
        "session_id": session_id,
        "state": state,
        "selected_profile": profile or None,
        "verdict": verdict,
        "use_gate": readiness.get("use_gate"),
        "session_classification": readiness.get("session_classification") or "conversation",
        "semantic_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "inputs": source_files,
        "bundle": {"path": bundle_relative, "files": files},
        "review": review,
        "referential_integrity": handoff_evidence["referential_integrity"],
        "speaker_resolution": speaker_resolution,
        "gates": {
            "export_allowed": state in EXPORTABLE_STATES,
            "profile_consistent": not any("profile_mismatch" in item for item in blockers),
            "inputs_complete": not any(
                item.startswith(("input_missing:", "readiness_output_missing:"))
                for item in blockers
            ),
            "evidence_references_valid": handoff_evidence["referential_integrity"]["passed"],
            "mandatory_review_closed": review["mandatory_count"] == 0
            and review["mandatory_seconds"] <= 0.001
            and not review["export_blockers"]
            and not review["review_blockers"],
        },
        "blockers": blockers,
        "warnings": warnings,
        "export": {
            "allowed": state in EXPORTABLE_STATES,
            "formats": ["markdown", "obsidian"] if state in EXPORTABLE_STATES else [],
            "source": "immutable_handoff_bundle",
        },
        "recommended_next": recommended_next,
    }

    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging.", dir=root))
    try:
        for name, data in output_payloads.items():
            write_durable(staging / filenames[name], data)
        write_durable(staging / "handoff_manifest.json", canonical_json_bytes(manifest))
        fsync_directory(staging)
        bundles = root / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            valid, reasons = verify_manifest(manifest, session, verify_current_inputs=False)
            if not valid:
                raise HandoffError(f"existing immutable bundle is invalid: {', '.join(reasons)}")
            shutil.rmtree(staging)
        else:
            os.replace(staging, bundle)
            fsync_directory(bundles)
        if simulate_interruption_before_publish:
            raise SimulatedInterruption("simulated interruption before current manifest publish")
        atomic_write(root / "handoff_manifest.json", canonical_json_bytes(manifest))
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return manifest


def verify_manifest(
    manifest: dict[str, Any],
    session: Path,
    *,
    verify_current_inputs: bool = True,
) -> tuple[bool, list[str]]:
    session = session.expanduser().resolve()
    reasons: list[str] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        reasons.append("unsupported_schema")
    if manifest.get("state") not in VALID_STATES:
        reasons.append("invalid_state")
    basis = manifest.get("fingerprint_basis")
    fingerprint = manifest.get("semantic_fingerprint")
    if not isinstance(basis, dict) or not isinstance(fingerprint, str):
        reasons.append("fingerprint_missing")
    elif sha256_bytes(compact_json_bytes(basis)) != fingerprint:
        reasons.append("fingerprint_mismatch")
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    bundle_path = resolve_session_path(session, bundle.get("path"))
    if bundle_path is None or not bundle_path.is_dir():
        reasons.append("bundle_missing")
    elif fingerprint and bundle_path.name != fingerprint:
        reasons.append("bundle_fingerprint_path_mismatch")
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    required_outputs = {
        "handoff_evidence",
        "meeting",
        "notes",
        "quality_verdict",
        "transcript",
    }
    if set(files) != required_outputs:
        reasons.append("bundle_files_incomplete")
    for name, row in files.items():
        if not isinstance(row, dict):
            reasons.append(f"invalid_output:{name}")
            continue
        path = resolve_session_path(session, row.get("path"))
        if path is None or not path.is_file():
            reasons.append(f"missing_output:{name}")
            continue
        data = path.read_bytes()
        if safe_int(row.get("bytes"), -1) != len(data):
            reasons.append(f"output_size_changed:{name}")
        if row.get("sha256") != sha256_bytes(data):
            reasons.append(f"output_hash_changed:{name}")
    if verify_current_inputs:
        inputs = manifest.get("inputs") if isinstance(manifest.get("inputs"), dict) else {}
        declared_speaker_resolution = manifest.get("speaker_resolution")
        legacy_aggregate_handoff = (
            not isinstance(declared_speaker_resolution, dict)
            and "speaker_selection" not in inputs
        )
        required_inputs = {
            "clean_dialogue",
            "evidence_notes",
            "notes",
            "quality_verdict_json",
            "quality_verdict_md",
            "readiness",
            "review_items",
            "session",
            "transcript",
        }
        if not legacy_aggregate_handoff:
            required_inputs.add("speaker_selection")
        allowed_inputs = required_inputs | {"speaker_transcript"}
        speaker_resolution = (
            declared_speaker_resolution
            if isinstance(declared_speaker_resolution, dict)
            else {
                "state": "fallback",
                "selected_speaker_profile": "aggregate_colleagues",
                "fallback_reason": "legacy_handoff_v2",
            }
        )
        if speaker_resolution.get("state") == "selected":
            required_inputs.add("speaker_transcript")
        if not set(inputs).issubset(allowed_inputs):
            reasons.append("manifest_inputs_unexpected")
        if manifest.get("state") != "blocked" and set(inputs) != required_inputs:
            reasons.append("manifest_inputs_incomplete")
        for name, row in inputs.items():
            if not isinstance(row, dict):
                reasons.append(f"invalid_input:{name}")
                continue
            path = resolve_session_path(session, row.get("path"))
            if path is None or not path.is_file():
                reasons.append(f"stale_input_missing:{name}")
                continue
            data = path.read_bytes()
            if safe_int(row.get("bytes"), -1) != len(data):
                reasons.append(f"stale_input_size:{name}")
            if row.get("sha256") != sha256_bytes(data):
                reasons.append(f"stale_input_hash:{name}")
        if speaker_resolution.get("state") not in {"selected", "fallback"}:
            reasons.append("speaker_resolution_state_invalid")
        if speaker_resolution.get("state") == "selected":
            selected_input = inputs.get("speaker_transcript")
            transcript_output = files.get("transcript")
            if not isinstance(selected_input, dict) or not isinstance(transcript_output, dict):
                reasons.append("speaker_resolved_transcript_missing")
            elif selected_input.get("sha256") != transcript_output.get("sha256"):
                reasons.append("speaker_resolved_handoff_mismatch")
        elif speaker_resolution.get("state") == "fallback":
            aggregate_input = inputs.get("transcript")
            transcript_output = files.get("transcript")
            if not isinstance(aggregate_input, dict) or not isinstance(transcript_output, dict):
                reasons.append("aggregate_fallback_transcript_missing")
            elif legacy_aggregate_handoff:
                dialogue_input = inputs.get("clean_dialogue")
                dialogue_path = (
                    resolve_session_path(session, dialogue_input.get("path"))
                    if isinstance(dialogue_input, dict)
                    else None
                )
                output_path = resolve_session_path(session, transcript_output.get("path"))
                dialogue, dialogue_error = (
                    read_json(dialogue_path)
                    if dialogue_path is not None
                    else (None, "missing")
                )
                expected = (
                    render_transcript(
                        str(manifest.get("session_id") or session.name),
                        str(manifest.get("selected_profile") or ""),
                        str(manifest.get("state") or "blocked"),
                        dialogue_utterances(dialogue, session),
                    ).encode("utf-8")
                    if dialogue is not None and dialogue_error is None
                    else None
                )
                if (
                    output_path is None
                    or not output_path.is_file()
                    or output_path.read_bytes() != expected
                ):
                    reasons.append("legacy_aggregate_handoff_mismatch")
            elif aggregate_input.get("sha256") != transcript_output.get("sha256"):
                reasons.append("aggregate_fallback_handoff_mismatch")
    return not reasons, dedupe_strings(reasons)


def load_valid_handoff(
    session: Path,
    *,
    verify_current_inputs: bool = True,
) -> tuple[dict[str, Any] | None, list[str]]:
    session = session.expanduser().resolve()
    manifest, error = read_json(session / "derived/handoff-v2/handoff_manifest.json")
    if error or manifest is None:
        return None, [f"manifest:{error or 'missing'}"]
    passed, reasons = verify_manifest(
        manifest, session, verify_current_inputs=verify_current_inputs
    )
    return (manifest if passed else None), reasons


def artifact_path(manifest: dict[str, Any], session: Path, key: str) -> Path | None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    row = files.get(key)
    if not isinstance(row, dict):
        return None
    return resolve_session_path(session.expanduser().resolve(), row.get("path"))


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    try:
        if args.verify_only:
            manifest, reasons = load_valid_handoff(session)
            if manifest is None:
                print("handoff_v2: invalid")
                for reason in reasons:
                    print(f"  {reason}")
                return 2
        else:
            manifest = build_handoff(
                session,
                simulate_interruption_before_publish=args.simulate_interruption_before_publish,
            )
    except SimulatedInterruption as error:
        print(str(error))
        return 3
    except HandoffError as error:
        print(f"error: {error}", file=os.sys.stderr)
        return 2
    path = session / "derived/handoff-v2/handoff_manifest.json"
    if args.print_path:
        print(path)
    else:
        print("handoff_v2:")
        print(f"  state: {manifest.get('state')}")
        print(f"  profile: {manifest.get('selected_profile')}")
        print(f"  fingerprint: {manifest.get('semantic_fingerprint')}")
        print(f"  export_allowed: {bool((manifest.get('export') or {}).get('allowed'))}")
        print(f"  manifest: {path}")
        print(f"  next: {manifest.get('recommended_next')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
