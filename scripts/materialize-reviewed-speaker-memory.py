#!/usr/bin/env python3
"""Publish reviewed session-local speaker labels into an opt-in meeting-memory bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

import evidence_handoff_v2 as evidence_handoff


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
HANDOFF_SCHEMA = "murmurmark.reviewed_speaker_memory_handoff/v1"
MEMORY_SCHEMA = "murmurmark.reviewed_speaker_memory/v1"
REPORT_SCHEMA = "murmurmark.reviewed_speaker_memory_report/v1"
POLICY_SCHEMA = "murmurmark.reviewed_speaker_memory_policy/v1"
DEFAULT_OUTPUT = Path("derived/meeting-memory/reviewed-speakers-v1")
DEFAULT_POLICY = Path("policies/reviewed-speaker-memory-v1.json")
OUTPUT_FILENAMES = {
    "memory_json": "speaker_aware_memory.json",
    "handoff_evidence": "handoff_evidence.json",
    "meeting": "meeting.md",
    "notes": "notes.md",
    "transcript": "transcript.md",
    "quality_verdict": "quality_verdict.md",
}


class MemoryError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise MemoryError(f"helper_cannot_be_loaded:{path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


naming = load_module(
    "review_remote_speaker_labels_for_memory",
    ROOT / "scripts/review-remote-speaker-labels.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-path", choices=sorted(OUTPUT_FILENAMES))
    parser.add_argument("--simulate-interruption-before-publish", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compact_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MemoryError(f"invalid_or_missing_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(payload, dict):
        raise MemoryError(f"invalid_json_object:{path.name}")
    return payload


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_inside(root: Path, raw: str | Path) -> Path:
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    result = candidate.resolve()
    if not within(result, root):
        raise MemoryError("path_outside_session")
    return result


def session_relative(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def identity(path: Path, session: Path) -> dict[str, Any]:
    if not path.is_file() or not within(path, session):
        raise MemoryError(f"input_missing_or_outside_session:{path.name}")
    return {
        "scope": "session",
        "path": session_relative(path, session),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def repository_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or not within(path, ROOT):
        raise MemoryError(f"implementation_missing:{path.name}")
    return {
        "scope": "repository",
        "path": str(path.resolve().relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def resolve_identity(row: Any, session: Path) -> Path | None:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return None
    try:
        if row.get("scope") == "session":
            return resolve_inside(session, row["path"])
        if row.get("scope") == "repository":
            path = (ROOT / row["path"]).resolve()
            return path if within(path, ROOT) else None
    except MemoryError:
        return None
    return None


def identity_matches(row: Any, session: Path) -> bool:
    path = resolve_identity(row, session)
    return bool(
        path is not None
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def implementation() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {"script": path.name, "version": SCRIPT_VERSION, "fingerprint": repository_identity(path)}


def decisions_path(args: argparse.Namespace, session: Path) -> Path:
    return resolve_inside(session, args.decisions or naming.DEFAULT_DECISIONS)


def output_root(args: argparse.Namespace, session: Path) -> Path:
    return resolve_inside(session, args.out_dir or DEFAULT_OUTPUT)


def policy_path(args: argparse.Namespace) -> Path:
    candidate = args.policy.expanduser() if args.policy else ROOT / DEFAULT_POLICY
    candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
    result = candidate.resolve()
    if not within(result, ROOT):
        raise MemoryError("policy_outside_repository")
    return result


def validate_policy(path: Path) -> tuple[dict[str, Any], Path]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise MemoryError("policy_schema_mismatch")
    if policy.get("decision") != "PROMOTE_OPTIONAL_REVIEWED_SPEAKER_MEMORY":
        raise MemoryError("policy_not_promoted")
    source = policy.get("source") if isinstance(policy.get("source"), dict) else {}
    if source.get("materializer") != implementation():
        raise MemoryError("policy_materializer_fingerprint_mismatch")
    raw_manifest = source.get("frozen_manifest_path")
    if not isinstance(raw_manifest, str):
        raise MemoryError("policy_frozen_manifest_path_missing")
    frozen_path = (ROOT / raw_manifest).resolve()
    if not within(frozen_path, ROOT) or not frozen_path.is_file():
        raise MemoryError("policy_frozen_manifest_missing")
    frozen_identity = source.get("frozen_manifest")
    if not isinstance(frozen_identity, dict):
        raise MemoryError("policy_frozen_manifest_fingerprint_missing")
    actual = repository_identity(frozen_path)
    if any(actual.get(key) != frozen_identity.get(key) for key in ("bytes", "sha256")):
        raise MemoryError("policy_frozen_manifest_fingerprint_mismatch")
    frozen = read_json(frozen_path)
    if frozen.get("schema") != "murmurmark.reviewed_speaker_memory_frozen_manifest/v1":
        raise MemoryError("policy_frozen_manifest_schema_mismatch")
    if frozen.get("decision") != "PROMOTE_OPTIONAL_REVIEWED_SPEAKER_MEMORY":
        raise MemoryError("policy_frozen_manifest_not_promoted")
    return policy, frozen_path


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_durable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    write_durable(temporary, payload)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def output_identity(path: str, payload: bytes) -> dict[str, Any]:
    return {"scope": "session", "path": path, "bytes": len(payload), "sha256": sha256_bytes(payload)}


def dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def seconds_label(value: Any) -> str:
    total = max(0, int(float(value or 0.0)))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def load_valid_evidence_handoff(session: Path) -> tuple[dict[str, Any], dict[str, Path]]:
    manifest, reasons = evidence_handoff.load_valid_handoff(session)
    if manifest is None:
        raise MemoryError("evidence_handoff_invalid:" + ",".join(reasons))
    paths: dict[str, Path] = {}
    for key in ("handoff_evidence", "meeting", "notes", "transcript", "quality_verdict"):
        path = evidence_handoff.artifact_path(manifest, session, key)
        if path is None or not path.is_file():
            raise MemoryError(f"evidence_handoff_artifact_missing:{key}")
        paths[key] = path
    return manifest, paths


def utterance_projection(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            row.get("id"),
            row.get("start"),
            row.get("end"),
            row.get("role"),
            row.get("speaker_label"),
            row.get("text"),
        )
        for row in rows
    ]


def collect_statement_rows(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for block in evidence.get("outline") or []:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("id") or f"outline_{len(result) + 1}")
        representatives = block.get("representatives") if isinstance(block.get("representatives"), list) else []
        for index, representative in enumerate(representatives):
            if not isinstance(representative, dict):
                continue
            utterance_id = str(representative.get("utterance_id") or "")
            result.append(
                {
                    "statement_id": f"outline:{block_id}:{index}",
                    "category": "outline",
                    "text": str(representative.get("text") or ""),
                    "evidence_utterance_ids": [utterance_id] if utterance_id else [],
                    "context_utterance_ids": [],
                }
            )
    claims = evidence.get("claims") if isinstance(evidence.get("claims"), dict) else {}
    for category in ("decisions", "actions", "risks", "open_questions"):
        for index, row in enumerate(claims.get(category) or []):
            if not isinstance(row, dict):
                continue
            result.append(
                {
                    "statement_id": f"claim:{category}:{row.get('id') or index}",
                    "category": category,
                    "text": str(row.get("text") or ""),
                    "evidence_utterance_ids": dedupe(row.get("evidence_utterance_ids") or []),
                    "context_utterance_ids": dedupe(row.get("context_utterance_ids") or []),
                    "needs_review": bool(row.get("needs_review")),
                }
            )
    review = evidence.get("review") if isinstance(evidence.get("review"), dict) else {}
    for index, row in enumerate(review.get("items") or []):
        if not isinstance(row, dict):
            continue
        result.append(
            {
                "statement_id": f"review:{index}",
                "category": "review",
                "text": str(row.get("text") or ""),
                "evidence_utterance_ids": dedupe(row.get("utterance_ids") or []),
                "context_utterance_ids": [],
            }
        )
    return result


def build_material(
    session: Path,
    decision_path: Path,
    reviewed_root: Path | None = None,
    policy: Path | None = None,
) -> dict[str, Any]:
    policy = policy or ROOT / DEFAULT_POLICY
    _, frozen_manifest = validate_policy(policy)
    evidence_manifest, evidence_paths = load_valid_evidence_handoff(session)
    evidence = read_json(evidence_paths["handoff_evidence"])
    if evidence.get("schema") != evidence_handoff.EVIDENCE_SCHEMA:
        raise MemoryError("handoff_evidence_schema_mismatch")

    anonymous_manifest, anonymous, anonymous_json, anonymous_markdown = naming.load_anonymous(session)
    reviewed_root = reviewed_root or session / naming.DEFAULT_OUTPUT
    reviewed_manifest, reviewed_reasons = naming.verify_handoff(session, decision_path, reviewed_root)
    if reviewed_manifest is None:
        raise MemoryError("reviewed_speaker_handoff_invalid:" + ",".join(reviewed_reasons))
    naming_material = naming.build_material(session, decision_path)
    reviewed_json = naming.reviewed_path(reviewed_manifest, session, "transcript_json")
    reviewed_markdown = naming.reviewed_path(reviewed_manifest, session, "transcript_markdown")
    if reviewed_json is None or reviewed_markdown is None:
        raise MemoryError("reviewed_speaker_outputs_missing")
    reviewed_payload = read_json(reviewed_json)
    if reviewed_payload.get("schema") != naming.TRANSCRIPT_SCHEMA:
        raise MemoryError("reviewed_speaker_transcript_schema_mismatch")

    utterances = anonymous.get("utterances")
    reviewed_utterances = reviewed_payload.get("utterances")
    if not isinstance(utterances, list) or not all(isinstance(row, dict) for row in utterances):
        raise MemoryError("anonymous_utterances_missing")
    if not isinstance(reviewed_utterances, list) or utterance_projection(utterances) != utterance_projection(reviewed_utterances):
        raise MemoryError("reviewed_utterance_projection_mismatch")
    utterances = list(utterances)
    utterance_ids = [str(row.get("id") or "") for row in utterances]
    if any(not item for item in utterance_ids) or len(utterance_ids) != len(set(utterance_ids)):
        raise MemoryError("selected_utterance_ids_invalid")
    known_ids = set(utterance_ids)

    attributions = anonymous.get("remote_speaker_attributions")
    if not isinstance(attributions, list) or not all(isinstance(row, dict) for row in attributions):
        raise MemoryError("anonymous_attributions_missing")
    remote_ids = [str(row["id"]) for row in utterances if row.get("role") == "remote"]
    if [str(row.get("utterance_id") or "") for row in attributions] != remote_ids:
        raise MemoryError("anonymous_attribution_set_mismatch")
    attribution_by_id = {str(row["utterance_id"]): (index, row) for index, row in enumerate(attributions)}

    decisions = naming_material["decisions"]
    decision_rows = decisions.get("labels")
    if not isinstance(decision_rows, list) or not all(isinstance(row, dict) for row in decision_rows):
        raise MemoryError("decision_rows_missing")
    reviewed_rows = naming_material["reviewed"]
    reviewed_by_id = {str(row["speaker_id"]): (index, row) for index, row in enumerate(reviewed_rows)}
    if len(reviewed_by_id) != len(reviewed_rows):
        raise MemoryError("reviewed_speaker_ids_duplicate")

    statements = collect_statement_rows(evidence)
    referenced_by: dict[str, list[str]] = {item: [] for item in utterance_ids}
    for row in statements:
        evidence_ids = dedupe(row["evidence_utterance_ids"])
        context_ids = dedupe(row["context_utterance_ids"])
        if row["category"] != "review" and not evidence_ids:
            raise MemoryError(f"statement_without_evidence:{row['statement_id']}")
        missing = (set(evidence_ids) | set(context_ids)) - known_ids
        if missing:
            raise MemoryError("unknown_statement_utterance_id:" + ",".join(sorted(missing)))
        row["evidence_utterance_ids"] = evidence_ids
        row["context_utterance_ids"] = context_ids
        row["text_sha256"] = sha256_bytes(row["text"].encode("utf-8"))
        for utterance_id in evidence_ids + context_ids:
            referenced_by[utterance_id].append(row["statement_id"])

    utterance_bindings: list[dict[str, Any]] = []
    for utterance in utterances:
        utterance_id = str(utterance["id"])
        if utterance.get("role") != "remote":
            binding = {
                "utterance_id": utterance_id,
                "display_label": "Me",
                "display_mode": "local_role",
                "anonymous_speaker_id": None,
                "attribution_row_index": None,
                "decision_row_index": None,
                "decision_action": None,
            }
        else:
            attribution_index, attribution = attribution_by_id[utterance_id]
            speaker_id = attribution.get("speaker_id")
            if speaker_id is None:
                binding = {
                    "utterance_id": utterance_id,
                    "display_label": "Colleagues",
                    "display_mode": "aggregate_remote",
                    "anonymous_speaker_id": None,
                    "attribution_row_index": attribution_index,
                    "decision_row_index": None,
                    "decision_action": None,
                }
            else:
                reviewed = reviewed_by_id.get(str(speaker_id))
                if reviewed is None:
                    raise MemoryError(f"unknown_reviewed_speaker_id:{speaker_id}")
                decision_index, decision = reviewed
                action = str(decision.get("action") or "")
                if action == "label":
                    display = str(decision.get("display_label") or "")
                    mode = "reviewed_label"
                elif action == "keep_anonymous":
                    display = str(speaker_id)
                    mode = "anonymous_id"
                else:
                    raise MemoryError(f"unsupported_review_action:{action}")
                binding = {
                    "utterance_id": utterance_id,
                    "display_label": display,
                    "display_mode": mode,
                    "anonymous_speaker_id": str(speaker_id),
                    "attribution_row_index": attribution_index,
                    "decision_row_index": decision_index,
                    "decision_action": action,
                }
        binding["referenced_by_statement_ids"] = dedupe(referenced_by[utterance_id])
        utterance_bindings.append(binding)
    binding_by_id = {row["utterance_id"]: row for row in utterance_bindings}

    statement_bindings: list[dict[str, Any]] = []
    for row in statements:
        ids = row["evidence_utterance_ids"]
        statement_bindings.append(
            {
                **row,
                "speaker_evidence": [
                    {
                        "utterance_id": utterance_id,
                        "display_label": binding_by_id[utterance_id]["display_label"],
                        "display_mode": binding_by_id[utterance_id]["display_mode"],
                        "anonymous_speaker_id": binding_by_id[utterance_id]["anonymous_speaker_id"],
                        "decision_row_index": binding_by_id[utterance_id]["decision_row_index"],
                    }
                    for utterance_id in ids
                ],
            }
        )

    speaker_groups: dict[tuple[str, str, str | None, int | None], list[str]] = {}
    for row in utterance_bindings:
        key = (
            row["display_label"],
            row["display_mode"],
            row["anonymous_speaker_id"],
            row["decision_row_index"],
        )
        speaker_groups.setdefault(key, []).append(row["utterance_id"])
    speaker_bindings = []
    for (display, mode, speaker_id, decision_index), ids in speaker_groups.items():
        evidence_ids = [item for item in ids if referenced_by[item]]
        speaker_bindings.append(
            {
                "display_label": display,
                "display_mode": mode,
                "anonymous_speaker_id": speaker_id,
                "decision_row_index": decision_index,
                "decision_action": (
                    reviewed_rows[decision_index]["action"] if decision_index is not None else None
                ),
                "utterance_ids": ids,
                "evidence_utterance_ids": evidence_ids,
            }
        )
    speaker_bindings.sort(key=lambda row: (str(row["display_mode"]), str(row["display_label"])))

    memory = {
        "schema": MEMORY_SCHEMA,
        "version": 1,
        "status": "optional_reviewed_session_memory",
        "session_id": session.name,
        "selected_profile": evidence_manifest.get("selected_profile"),
        "handoff_state": evidence_manifest.get("state"),
        "source": {
            "evidence_handoff_fingerprint": evidence_manifest.get("semantic_fingerprint"),
            "anonymous_rich_fingerprint": anonymous_manifest.get("semantic_fingerprint"),
            "reviewed_speaker_fingerprint": reviewed_manifest.get("semantic_fingerprint"),
            "decision_file": identity(decision_path, session),
        },
        "speaker_bindings": speaker_bindings,
        "utterance_bindings": utterance_bindings,
        "statement_bindings": statement_bindings,
        "referential_integrity": {
            "passed": True,
            "selected_utterance_count": len(utterances),
            "remote_utterance_count": len(attributions),
            "statement_count": len(statement_bindings),
            "referenced_utterance_count": sum(bool(referenced_by[item]) for item in utterance_ids),
            "unknown_utterance_ids": [],
            "text_role_order_timestamps_unchanged": True,
        },
        "constraints": {
            "authoritative": False,
            "explicit_session_review_only": True,
            "voice_identity_inference": False,
            "cross_session_identity": False,
            "generated_claims": False,
            "external_writes": False,
        },
    }

    statement_by_id = {row["statement_id"]: row for row in statement_bindings}
    outputs = {
        "memory_json": canonical_bytes(memory),
        "handoff_evidence": evidence_paths["handoff_evidence"].read_bytes(),
        "meeting": render_meeting(memory, evidence).encode("utf-8"),
        "notes": render_notes(evidence, statement_by_id).encode("utf-8"),
        "transcript": render_transcript(utterances, binding_by_id, evidence_manifest).encode("utf-8"),
        "quality_verdict": evidence_paths["quality_verdict"].read_bytes(),
    }
    inputs = {
        "policy": repository_identity(policy),
        "frozen_corpus_manifest": repository_identity(frozen_manifest),
        "evidence_handoff_manifest": identity(session / "derived/handoff-v2/handoff_manifest.json", session),
        "anonymous_handoff_manifest": identity(
            session / naming.rich.DEFAULT_OUTPUT_DIR / "handoff_manifest.json", session
        ),
        "reviewed_speaker_handoff_manifest": identity(reviewed_root / "handoff_manifest.json", session),
        "review_decisions": identity(decision_path, session),
        "anonymous_rich_json": identity(anonymous_json, session),
        "anonymous_rich_markdown": identity(anonymous_markdown, session),
        "reviewed_rich_json": identity(reviewed_json, session),
        "reviewed_rich_markdown": identity(reviewed_markdown, session),
        **{f"handoff_{key}": identity(path, session) for key, path in evidence_paths.items()},
    }
    baseline = {**inputs}
    for key, row in ((anonymous_manifest.get("safety") or {}).get("baseline_identities") or {}).items():
        if not naming.identity_matches(row, session):
            raise MemoryError(f"anonymous_baseline_stale:{key}")
        baseline[f"ordinary_{key}"] = row
    return {
        "evidence_manifest": evidence_manifest,
        "reviewed_manifest": reviewed_manifest,
        "inputs": inputs,
        "baseline": baseline,
        "speaker_bindings": speaker_bindings,
        "outputs": outputs,
        "summary": {
            "speaker_bindings": len(speaker_bindings),
            "reviewed_labels": sum(row["display_mode"] == "reviewed_label" for row in speaker_bindings),
            "anonymous_labels": sum(row["display_mode"] == "anonymous_id" for row in speaker_bindings),
            "aggregate_labels": sum(row["display_mode"] == "aggregate_remote" for row in speaker_bindings),
            "utterances": len(utterances),
            "statements": len(statement_bindings),
        },
    }


def evidence_render(ids: list[str], statement: dict[str, Any]) -> str:
    by_id = {row["utterance_id"]: row for row in statement["speaker_evidence"]}
    return ", ".join(f"{by_id[item]['display_label']} [`{item}`]" for item in ids)


def render_notes(evidence: dict[str, Any], statements: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Reviewed Speaker-Aware Evidence Notes",
        "",
        f"Session: `{evidence.get('session_id')}`  ",
        f"Profile: `{evidence.get('selected_profile') or 'unknown'}`  ",
        f"Handoff state: `{evidence.get('state')}`",
        "",
        "Every speaker name below comes from the current session review decision. Claims and evidence IDs are unchanged.",
        "",
        "## Conversation Outline",
        "",
    ]
    for block in evidence.get("outline") or []:
        if not isinstance(block, dict):
            continue
        title = ", ".join(str(item) for item in (block.get("keywords") or [])[:4]) or "discussion block"
        lines.extend([f"### {seconds_label(block.get('start'))}-{seconds_label(block.get('end'))}: {title}", ""])
        representatives = block.get("representatives") if isinstance(block.get("representatives"), list) else []
        for index, representative in enumerate(representatives):
            statement = statements[f"outline:{block.get('id') or 'outline'}:{index}"]
            utterance_id = statement["evidence_utterance_ids"][0]
            lines.append(
                f"- **{statement['speaker_evidence'][0]['display_label']}**: "
                f"{representative.get('text') or ''} (`{utterance_id}`)"
            )
        if not representatives:
            lines.append("- No evidence-backed outline items.")
        lines.append("")
    claims = evidence.get("claims") if isinstance(evidence.get("claims"), dict) else {}
    headings = (
        ("decisions", "Potential Decisions"),
        ("actions", "Potential Actions"),
        ("risks", "Risks"),
        ("open_questions", "Open Questions"),
    )
    for category, heading in headings:
        lines.extend([f"## {heading}", ""])
        rows = claims.get(category) or []
        if not rows:
            lines.append("- None selected.")
        for index, row in enumerate(rows):
            statement = statements[f"claim:{category}:{row.get('id') or index}"]
            review = " `needs_review`" if row.get("needs_review") else ""
            lines.append(
                f"- {row.get('text') or ''} (evidence: "
                f"{evidence_render(statement['evidence_utterance_ids'], statement)}){review}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_transcript(
    utterances: list[dict[str, Any]],
    bindings: dict[str, dict[str, Any]],
    evidence_manifest: dict[str, Any],
) -> str:
    lines = [
        "# Reviewed Speaker-Aware Transcript",
        "",
        f"Session: `{evidence_manifest.get('session_id')}`  ",
        f"Profile: `{evidence_manifest.get('selected_profile')}`  ",
        "Optional explicit session-local speaker labels. Text, order and timestamps are unchanged.",
        "",
    ]
    for utterance in utterances:
        utterance_id = str(utterance["id"])
        label = bindings[utterance_id]["display_label"]
        lines.extend(
            [
                f"## {seconds_label(utterance.get('start'))} {label} [{utterance_id}]",
                "",
                str(utterance.get("text") or ""),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def render_meeting(memory: dict[str, Any], evidence: dict[str, Any]) -> str:
    claims = evidence.get("claims") if isinstance(evidence.get("claims"), dict) else {}
    summary = {
        "Outline blocks": len(evidence.get("outline") or []),
        "Potential decisions": len(claims.get("decisions") or []),
        "Potential actions": len(claims.get("actions") or []),
        "Risks": len(claims.get("risks") or []),
        "Open questions": len(claims.get("open_questions") or []),
    }
    lines = [
        f"# {memory['session_id']}",
        "",
        f"- Handoff state: `{memory.get('handoff_state')}`",
        f"- Transcript profile: `{memory.get('selected_profile')}`",
        "- Speaker source: `explicit_session_review`",
        "",
        "Artifacts: [Transcript](transcript.md) | [Evidence Notes](notes.md) | "
        "[Quality Verdict](quality_verdict.md) | [Speaker Memory](speaker_aware_memory.json) | "
        "[Evidence JSON](handoff_evidence.json)",
        "",
        "## Evidence Summary",
        "",
    ]
    lines.extend(f"- {key}: `{value}`" for key, value in summary.items())
    return "\n".join(lines).rstrip() + "\n"


def semantic_basis(session: Path, material: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "source_evidence_handoff_fingerprint": material["evidence_manifest"].get("semantic_fingerprint"),
        "source_reviewed_speaker_fingerprint": material["reviewed_manifest"].get("semantic_fingerprint"),
        "inputs": material["inputs"],
        "outputs": {
            key: {
                "filename": OUTPUT_FILENAMES[key],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
            for key, payload in sorted(material["outputs"].items())
        },
        "speaker_bindings": material["speaker_bindings"],
        "summary": material["summary"],
        "scope": "optional_explicit_session_local_speaker_memory",
    }


def report_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "session_id": manifest["session_id"],
        "state": manifest["state"],
        "semantic_fingerprint": manifest.get("semantic_fingerprint"),
        "summary": manifest.get("summary") or {},
        "reasons": manifest.get("reasons") or [],
        "privacy": {
            "display_labels_in_report": False,
            "voice_identity_inference": False,
            "cross_session_identity": False,
        },
    }


def report_markdown(manifest: dict[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# Reviewed Speaker-Aware Meeting Memory v1",
        "",
        f"- State: `{manifest.get('state')}`",
        f"- Reviewed labels: `{summary.get('reviewed_labels', 0)}`",
        f"- Anonymous labels: `{summary.get('anonymous_labels', 0)}`",
        f"- Aggregate labels: `{summary.get('aggregate_labels', 0)}`",
        f"- Evidence statements: `{summary.get('statements', 0)}`",
        "",
        "Display labels are intentionally omitted from this report.",
    ]
    lines.extend(f"- Reason: `{reason}`" for reason in manifest.get("reasons") or [])
    return "\n".join(lines) + "\n"


def immutable_bundle_valid(bundle: Path, expected: dict[str, bytes]) -> bool:
    return all((bundle / name).is_file() and (bundle / name).read_bytes() == payload for name, payload in expected.items())


def build_handoff(
    session: Path,
    decision_path: Path,
    root: Path,
    *,
    reviewed_root: Path | None = None,
    policy: Path | None = None,
    simulate_interruption_before_publish: bool = False,
) -> dict[str, Any]:
    material = build_material(session, decision_path, reviewed_root, policy)
    basis = semantic_basis(session, material)
    fingerprint = sha256_bytes(compact_bytes(basis))
    bundle_relative = f"{session_relative(root, session)}/bundles/{fingerprint}"
    bundle = session / bundle_relative
    files = {
        key: output_identity(f"{bundle_relative}/{OUTPUT_FILENAMES[key]}", payload)
        for key, payload in sorted(material["outputs"].items())
    }
    if not all(identity_matches(row, session) for row in material["baseline"].values()):
        raise MemoryError("source_output_changed_before_publication")
    manifest = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "ready",
        "semantic_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "inputs": material["inputs"],
        "bundle": {"path": bundle_relative, "files": files},
        "speaker_bindings": material["speaker_bindings"],
        "summary": material["summary"],
        "gates": {
            "publish_optional_memory": True,
            "explicit_review_complete": True,
            "evidence_handoff_current": True,
            "anonymous_handoff_current": True,
            "reviewed_speaker_handoff_current": True,
            "referential_integrity": True,
            "ordinary_outputs_unchanged": True,
        },
        "safety": {
            "baseline_identities": material["baseline"],
            "default_notes_export_unchanged": True,
            "plain_transcript_authoritative": True,
            "fallback": "ordinary_evidence_handoff_v2",
            "voice_identity_inference": False,
            "cross_session_identity": False,
            "generated_claims": False,
            "external_writes": False,
        },
        "reasons": [],
        "recommended_next": f'murmurmark notes "sessions/{session.name}" --reviewed-speakers',
    }
    expected = {
        OUTPUT_FILENAMES[key]: payload for key, payload in material["outputs"].items()
    }
    expected["handoff_manifest.json"] = canonical_bytes(manifest)

    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging.", dir=root))
    try:
        for name, payload in expected.items():
            write_durable(staging / name, payload)
        fsync_directory(staging)
        bundles = root / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            if not immutable_bundle_valid(bundle, expected):
                raise MemoryError("existing_immutable_bundle_invalid")
            shutil.rmtree(staging)
        else:
            os.replace(staging, bundle)
            fsync_directory(bundles)
        if not all(identity_matches(row, session) for row in material["baseline"].values()):
            raise MemoryError("source_output_changed_during_publication")
        if simulate_interruption_before_publish:
            raise SimulatedInterruption("simulated interruption before speaker-aware memory publish")
        atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))
        atomic_write(root / "report.json", canonical_bytes(report_payload(manifest)))
        atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def unavailable_manifest(session: Path, reason: str) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "unavailable",
        "semantic_fingerprint": None,
        "bundle": None,
        "speaker_bindings": [],
        "summary": {},
        "gates": {"publish_optional_memory": False},
        "safety": {
            "default_notes_export_unchanged": True,
            "plain_transcript_authoritative": True,
            "fallback": "ordinary_evidence_handoff_v2",
        },
        "reasons": [reason],
    }


def publish_unavailable(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))
    atomic_write(root / "report.json", canonical_bytes(report_payload(manifest)))
    atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))


def verify_handoff(
    session: Path,
    decision_path: Path,
    root: Path,
    reviewed_root: Path | None = None,
    policy: Path | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        current = read_json(root / "handoff_manifest.json")
    except MemoryError as error:
        return None, [str(error)]
    if current.get("schema") != HANDOFF_SCHEMA:
        return None, ["handoff_schema_mismatch"]
    if current.get("state") != "ready":
        return None, [str(item) for item in current.get("reasons") or ["memory_handoff_unavailable"]]
    try:
        material = build_material(session, decision_path, reviewed_root, policy)
    except MemoryError as error:
        return None, [str(error)]
    basis = semantic_basis(session, material)
    fingerprint = sha256_bytes(compact_bytes(basis))
    if current.get("generator") != implementation():
        return None, ["generator_fingerprint_mismatch"]
    if current.get("fingerprint_basis") != basis or current.get("semantic_fingerprint") != fingerprint:
        return None, ["semantic_fingerprint_mismatch"]
    if current.get("inputs") != material["inputs"] or current.get("speaker_bindings") != material["speaker_bindings"]:
        return None, ["manifest_material_mismatch"]
    bundle = current.get("bundle") if isinstance(current.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    try:
        bundle_path = resolve_inside(session, str(bundle.get("path") or ""))
    except MemoryError:
        return None, ["bundle_path_invalid"]
    if bundle_path.name != fingerprint or set(files) != set(OUTPUT_FILENAMES):
        return None, ["bundle_path_invalid"]
    for key, row in files.items():
        if not identity_matches(row, session):
            return None, [f"bundle_file_identity_mismatch:{key}"]
        if row.get("sha256") != sha256_bytes(material["outputs"][key]):
            return None, [f"bundle_semantic_mismatch:{key}"]
    bundle_manifest = bundle_path / "handoff_manifest.json"
    if not bundle_manifest.is_file() or bundle_manifest.read_bytes() != canonical_bytes(current):
        return None, ["bundle_manifest_mismatch"]
    baseline = (current.get("safety") or {}).get("baseline_identities")
    if not isinstance(baseline, dict) or not all(identity_matches(row, session) for row in baseline.values()):
        return None, ["ordinary_output_fingerprint_mismatch"]
    return current, []


def artifact_path(manifest: dict[str, Any], session: Path, key: str) -> Path | None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    row = files.get(key)
    path = resolve_identity(row, session)
    return path if path is not None and path.is_file() else None


def print_summary(manifest: dict[str, Any], root: Path) -> None:
    summary = manifest.get("summary") or {}
    print("reviewed_speaker_memory:")
    print(f"  state: {manifest.get('state')}")
    print(f"  reviewed_labels: {summary.get('reviewed_labels', 0)}")
    print(f"  anonymous_labels: {summary.get('anonymous_labels', 0)}")
    print(f"  aggregate_labels: {summary.get('aggregate_labels', 0)}")
    print(f"  statements: {summary.get('statements', 0)}")
    if manifest.get("reasons"):
        print(f"  fallback_reason: {manifest['reasons'][0]}")
    print(f"  manifest: {root / 'handoff_manifest.json'}")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        print(f"error: session.json not found under {session}", file=sys.stderr)
        return 2
    try:
        decision_path = decisions_path(args, session)
        root = output_root(args, session)
        policy = policy_path(args)
    except MemoryError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.verify_only:
        manifest, reasons = verify_handoff(session, decision_path, root, policy=policy)
        if manifest is None:
            for reason in reasons:
                print(reason)
            return 2
        if args.print_path:
            path = artifact_path(manifest, session, args.print_path)
            if path is None:
                print(f"artifact_missing:{args.print_path}")
                return 2
            print(path)
        else:
            print_summary(manifest, root)
        return 0

    try:
        manifest = build_handoff(
            session,
            decision_path,
            root,
            policy=policy,
            simulate_interruption_before_publish=args.simulate_interruption_before_publish,
        )
    except SimulatedInterruption as error:
        print(str(error))
        return 3
    except MemoryError as error:
        manifest = unavailable_manifest(session, str(error))
        publish_unavailable(root, manifest)
        print_summary(manifest, root)
        return 2
    print_summary(manifest, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
