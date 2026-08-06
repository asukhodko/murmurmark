#!/usr/bin/env python3
"""Publish a fingerprint-bound optional rich transcript over anonymous remote evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import evidence_handoff_v2 as evidence_handoff


SCRIPT_VERSION = "0.1.0"
HANDOFF_SCHEMA = "murmurmark.anonymous_rich_handoff/v1"
TRANSCRIPT_SCHEMA = "murmurmark.anonymous_rich_transcript/v1"
POLICY_SCHEMA = "murmurmark.anonymous_rich_policy/v1"
AUDIT_REPORT_SCHEMA = "murmurmark.remote_speaker_evidence_report/v1"
AUDIT_MAP_SCHEMA = "murmurmark.remote_speaker_map/v1"
AUDIT_ATTRIBUTION_SCHEMA = "murmurmark.remote_utterance_attribution/v1"
AUDIT_MANIFEST_SCHEMA = "murmurmark.remote_speaker_evidence_artifact_manifest/v1"
CORPUS_MANIFEST_SCHEMA = "murmurmark.remote_speaker_evidence_frozen_manifest/v1"
DEFAULT_AUDIT_DIR = Path("derived/audit/remote-speaker-evidence-v1")
DEFAULT_OUTPUT_DIR = Path("derived/transcript-rich/anonymous-v1")
DEFAULT_POLICY = Path("policies/anonymous-rich-transcript-v1.json")
SPEAKER_ID_RE = re.compile(r"^remote_speaker_[0-9]{2}$")


class RichHandoffError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Materialize or verify an optional anonymous rich transcript handoff."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--audit-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    parser.add_argument("--simulate-interruption-before-publish", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compact_json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
        raise RichHandoffError(f"invalid_or_missing_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(payload, dict):
        raise RichHandoffError(f"invalid_json_object:{path.name}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise RichHandoffError(f"missing_jsonl:{path.name}") from error
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise RichHandoffError(f"invalid_jsonl:{path.name}:{number}") from error
        if not isinstance(row, dict):
            raise RichHandoffError(f"invalid_jsonl_row:{path.name}:{number}")
        rows.append(row)
    return rows


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


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_inside(root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else root / candidate
    resolved = candidate.resolve()
    return resolved if within(resolved, root) else None


def session_relative(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def repository_relative(path: Path) -> str:
    return str(path.resolve().relative_to(repo_root()))


def identity(path: Path, *, session: Path | None = None, repository: bool = False) -> dict[str, Any]:
    if not path.is_file():
        raise RichHandoffError(f"input_missing:{path.name}")
    if session is not None:
        if not within(path, session):
            raise RichHandoffError(f"input_outside_session:{path.name}")
        scope = "session"
        display = session_relative(path, session)
    elif repository:
        if not within(path, repo_root()):
            raise RichHandoffError(f"input_outside_repository:{path.name}")
        scope = "repository"
        display = repository_relative(path)
    else:
        raise RichHandoffError("identity_scope_missing")
    return {
        "scope": scope,
        "path": display,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def resolve_identity(row: dict[str, Any], session: Path) -> Path | None:
    scope = row.get("scope")
    raw = row.get("path")
    if scope == "session":
        return resolve_inside(session, raw)
    if scope == "repository":
        return resolve_inside(repo_root(), raw)
    return None


def identity_matches(row: dict[str, Any], session: Path) -> bool:
    path = resolve_identity(row, session)
    if path is None or not path.is_file():
        return False
    return bool(
        int(row.get("bytes") or -1) == path.stat().st_size
        and isinstance(row.get("sha256"), str)
        and row["sha256"] == sha256_file(path)
    )


def implementation_provenance() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {
        "script": path.name,
        "version": SCRIPT_VERSION,
        "fingerprint": identity(path, repository=True),
    }


def policy_path(args: argparse.Namespace) -> Path:
    path = args.policy.expanduser() if args.policy else repo_root() / DEFAULT_POLICY
    return path.resolve()


def audit_root(args: argparse.Namespace, session: Path) -> Path:
    if args.audit_dir is None:
        return session / DEFAULT_AUDIT_DIR
    path = args.audit_dir.expanduser()
    path = path if path.is_absolute() else Path.cwd() / path
    resolved = path.resolve()
    if not within(resolved, session):
        raise RichHandoffError("audit_dir_outside_session")
    return resolved


def output_root(args: argparse.Namespace, session: Path) -> Path:
    if args.out_dir is None:
        return session / DEFAULT_OUTPUT_DIR
    path = args.out_dir.expanduser()
    path = path if path.is_absolute() else Path.cwd() / path
    resolved = path.resolve()
    if not within(resolved, session):
        raise RichHandoffError("out_dir_outside_session")
    return resolved


def policy_corpus_manifest(policy: dict[str, Any]) -> Path:
    source = policy.get("source_evidence")
    if not isinstance(source, dict):
        raise RichHandoffError("policy_source_evidence_missing")
    raw = source.get("frozen_manifest_path")
    path = resolve_inside(repo_root(), raw)
    if path is None:
        raise RichHandoffError("policy_corpus_manifest_outside_repository")
    return path


def validate_policy(policy: dict[str, Any], path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if policy.get("schema") != POLICY_SCHEMA:
        raise RichHandoffError("policy_schema_mismatch")
    if policy.get("decision") != "PROMOTE_OPTIONAL_RICH":
        raise RichHandoffError("policy_not_promoted")
    source = policy.get("source_evidence")
    if not isinstance(source, dict):
        raise RichHandoffError("policy_source_evidence_missing")
    if source.get("decision") != "PROMOTE_AUDIT_ONLY":
        raise RichHandoffError("source_evidence_not_promoted")
    manifest_path = policy_corpus_manifest(policy)
    manifest = read_json(manifest_path)
    if manifest.get("schema") != CORPUS_MANIFEST_SCHEMA:
        raise RichHandoffError("corpus_manifest_schema_mismatch")
    expected_manifest = source.get("frozen_manifest")
    if not isinstance(expected_manifest, dict):
        raise RichHandoffError("policy_manifest_fingerprint_missing")
    actual_manifest = identity(manifest_path, repository=True)
    if any(actual_manifest.get(key) != expected_manifest.get(key) for key in ("bytes", "sha256")):
        raise RichHandoffError("corpus_manifest_fingerprint_mismatch")
    if manifest.get("generator") != source.get("corpus_reporter"):
        raise RichHandoffError("corpus_reporter_fingerprint_mismatch")
    sessions = manifest.get("sessions")
    if not isinstance(sessions, list) or not sessions:
        raise RichHandoffError("corpus_manifest_sessions_missing")
    approved_implementation = source.get("audit_implementation")
    approved_model = source.get("model")
    approved_parameters = source.get("parameters")
    for row in sessions:
        inputs = row.get("inputs") if isinstance(row, dict) else None
        if not isinstance(inputs, dict):
            raise RichHandoffError("corpus_manifest_inputs_missing")
        if inputs.get("implementation") != approved_implementation:
            raise RichHandoffError("corpus_audit_implementation_mismatch")
        if inputs.get("model") != approved_model:
            raise RichHandoffError("corpus_model_mismatch")
        if inputs.get("parameters") != approved_parameters:
            raise RichHandoffError("corpus_parameters_mismatch")
    return source, manifest


def selected_handoff(session: Path) -> tuple[dict[str, Any], Path, Path, Path]:
    manifest, reasons = evidence_handoff.load_valid_handoff(session)
    if manifest is None:
        raise RichHandoffError("selected_handoff_invalid:" + ",".join(reasons))
    profile = manifest.get("selected_profile")
    if not isinstance(profile, str) or not profile:
        raise RichHandoffError("selected_profile_missing")
    inputs = manifest.get("inputs")
    bundle = manifest.get("bundle")
    files = bundle.get("files") if isinstance(bundle, dict) else None
    if not isinstance(inputs, dict) or not isinstance(files, dict):
        raise RichHandoffError("selected_handoff_artifacts_missing")
    dialogue_row = inputs.get("clean_dialogue")
    resolved_transcript_row = inputs.get("transcript")
    bundle_transcript_row = files.get("transcript")
    if not all(isinstance(row, dict) for row in (dialogue_row, resolved_transcript_row, bundle_transcript_row)):
        raise RichHandoffError("selected_handoff_transcript_inputs_missing")
    dialogue = resolve_inside(session, dialogue_row.get("path"))
    resolved_transcript = resolve_inside(session, resolved_transcript_row.get("path"))
    bundle_transcript = resolve_inside(session, bundle_transcript_row.get("path"))
    if not all(path is not None and path.is_file() for path in (dialogue, resolved_transcript, bundle_transcript)):
        raise RichHandoffError("selected_handoff_path_invalid")
    return manifest, dialogue, resolved_transcript, bundle_transcript  # type: ignore[return-value]


def identity_from_audit(row: Any, session: Path) -> Path | None:
    if not isinstance(row, dict):
        return None
    path = resolve_inside(session, row.get("path"))
    if path is None or not path.is_file():
        return None
    if int(row.get("bytes") or -1) != path.stat().st_size:
        return None
    if row.get("sha256") != sha256_file(path):
        return None
    return path


def remote_projection(utterances: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return [
        (
            row.get("id"),
            row.get("role"),
            row.get("start"),
            row.get("end"),
            row.get("source_start"),
            row.get("source_end"),
            row.get("text"),
        )
        for row in utterances
        if row.get("role") == "remote"
    ]


def baseline_identities(
    session: Path,
    selected_manifest: dict[str, Any],
    dialogue: Path,
    resolved_transcript: Path,
    bundle_transcript: Path,
) -> dict[str, dict[str, Any]]:
    paths: dict[str, Path] = {
        "selected_dialogue": dialogue,
        "resolved_plain_transcript": resolved_transcript,
        "authoritative_plain_transcript": bundle_transcript,
        "evidence_handoff_manifest": session / "derived/handoff-v2/handoff_manifest.json",
    }
    inputs = selected_manifest.get("inputs") if isinstance(selected_manifest.get("inputs"), dict) else {}
    for key in ("readiness", "notes", "quality_verdict_json", "quality_verdict_md", "evidence_notes"):
        row = inputs.get(key)
        path = resolve_inside(session, row.get("path")) if isinstance(row, dict) else None
        if path is not None and path.is_file():
            paths[f"handoff_input_{key}"] = path
    bundle = selected_manifest.get("bundle") if isinstance(selected_manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    for key, row in files.items():
        path = resolve_inside(session, row.get("path")) if isinstance(row, dict) else None
        if path is not None and path.is_file():
            paths[f"handoff_bundle_{key}"] = path
    authoritative = session / "derived/pipeline-run/authoritative_handoff.json"
    if authoritative.is_file():
        paths["authoritative_handoff"] = authoritative
    return {key: identity(path, session=session) for key, path in sorted(paths.items())}


def validate_audit_artifacts(
    session: Path,
    root: Path,
    source_policy: dict[str, Any],
    selected_dialogue: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, dict[str, Any]],
]:
    paths = {
        "audit_report": root / "report.json",
        "speaker_map": root / "speaker_map.json",
        "utterance_attribution": root / "utterance_attribution.jsonl",
        "audit_artifact_manifest": root / "artifact_manifest.json",
    }
    report = read_json(paths["audit_report"])
    speaker_map = read_json(paths["speaker_map"])
    attributions = read_jsonl(paths["utterance_attribution"])
    artifact_manifest = read_json(paths["audit_artifact_manifest"])
    if report.get("schema") != AUDIT_REPORT_SCHEMA:
        raise RichHandoffError("audit_report_schema_mismatch")
    if report.get("status") != "completed" or report.get("decision") != "PUBLISH_AUDIT_EVIDENCE":
        raise RichHandoffError("audit_session_not_published")
    gates = report.get("gates")
    safety = report.get("safety")
    if not isinstance(gates, dict) or gates.get("publish_session_speaker_map") is not True:
        raise RichHandoffError("audit_publish_gate_failed")
    if not isinstance(safety, dict) or safety.get("selected_dialogue_unchanged") is not True or safety.get("raw_remote_unchanged") is not True:
        raise RichHandoffError("audit_input_integrity_failed")
    if report.get("implementation") != source_policy.get("audit_implementation"):
        raise RichHandoffError("audit_implementation_not_promoted")
    if report.get("model") != source_policy.get("model"):
        raise RichHandoffError("audit_model_not_promoted")
    if report.get("parameters") != source_policy.get("parameters"):
        raise RichHandoffError("audit_parameters_not_promoted")
    if speaker_map.get("schema") != AUDIT_MAP_SCHEMA or speaker_map.get("status") != "published_audit_only":
        raise RichHandoffError("speaker_map_not_published")
    if any(row.get("schema") != AUDIT_ATTRIBUTION_SCHEMA for row in attributions):
        raise RichHandoffError("attribution_schema_mismatch")
    if artifact_manifest.get("schema") != AUDIT_MANIFEST_SCHEMA:
        raise RichHandoffError("audit_artifact_manifest_schema_mismatch")
    artifacts = artifact_manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RichHandoffError("audit_artifact_hashes_missing")
    for name, expected in artifacts.items():
        path = root / str(name)
        if not isinstance(expected, str) or not path.is_file() or sha256_file(path) != expected:
            raise RichHandoffError(f"audit_artifact_hash_mismatch:{name}")

    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    audit_dialogue_path = identity_from_audit(source.get("dialogue"), session)
    remote_audio_path = identity_from_audit(source.get("remote_audio"), session)
    raw_before = source.get("raw_remote_before")
    raw_after = source.get("raw_remote_after")
    if audit_dialogue_path is None:
        raise RichHandoffError("audit_source_dialogue_stale")
    if remote_audio_path is None:
        raise RichHandoffError("audit_remote_audio_stale")
    if not isinstance(raw_before, dict) or raw_before != raw_after or identity_from_audit(raw_after, session) is None:
        raise RichHandoffError("audit_raw_remote_stale")
    audit_dialogue = read_json(audit_dialogue_path)
    selected_utterances = selected_dialogue.get("utterances")
    audit_utterances = audit_dialogue.get("utterances")
    if not isinstance(selected_utterances, list) or not isinstance(audit_utterances, list):
        raise RichHandoffError("dialogue_utterances_missing")
    if remote_projection(selected_utterances) != remote_projection(audit_utterances):
        raise RichHandoffError("selected_remote_projection_mismatch")

    selected_ids = [str(row.get("id") or "") for row in selected_utterances]
    if any(not value for value in selected_ids) or len(selected_ids) != len(set(selected_ids)):
        raise RichHandoffError("selected_utterance_ids_not_unique")
    remote_rows = [row for row in selected_utterances if row.get("role") == "remote"]
    remote_ids = [str(row["id"]) for row in remote_rows]
    attribution_ids = [str(row.get("utterance_id") or "") for row in attributions]
    if attribution_ids != remote_ids or len(attribution_ids) != len(set(attribution_ids)):
        raise RichHandoffError("remote_attribution_referential_integrity_failed")
    selected_by_id = {str(row["id"]): row for row in remote_rows}
    speakers = speaker_map.get("speakers")
    if not isinstance(speakers, list):
        raise RichHandoffError("speaker_map_rows_missing")
    speaker_ids = [row.get("speaker_id") for row in speakers if isinstance(row, dict)]
    if any(not isinstance(value, str) or not SPEAKER_ID_RE.fullmatch(value) for value in speaker_ids):
        raise RichHandoffError("invalid_anonymous_speaker_id")
    if len(speaker_ids) != len(set(speaker_ids)):
        raise RichHandoffError("duplicate_anonymous_speaker_id")
    known_speakers = set(speaker_ids)
    for row in attributions:
        utterance = selected_by_id[str(row["utterance_id"])]
        if row.get("start") != utterance.get("start") or row.get("end") != utterance.get("end"):
            raise RichHandoffError("attribution_boundary_mismatch")
        speaker_id = row.get("speaker_id")
        if speaker_id is None:
            if row.get("speaker_label") != "Colleagues" or row.get("status") != "aggregate":
                raise RichHandoffError("aggregate_attribution_invalid")
        elif speaker_id not in known_speakers or row.get("speaker_label") != speaker_id:
            raise RichHandoffError("speaker_attribution_invalid")

    input_identities = {key: identity(path, session=session) for key, path in paths.items()}
    input_identities["audit_source_dialogue"] = identity(audit_dialogue_path, session=session)
    input_identities["audit_remote_audio"] = identity(remote_audio_path, session=session)
    return report, speaker_map, attributions, artifact_manifest, input_identities


def render_markdown(
    selected_dialogue: dict[str, Any],
    attributions: list[dict[str, Any]],
    profile: str,
) -> str:
    by_id = {str(row["utterance_id"]): row for row in attributions}
    lines = [
        "# Anonymous Rich Transcript",
        "",
        "Optional session-local speaker evidence. The ordinary transcript remains authoritative.",
        "",
        f"Source profile: `{profile}`",
        "",
    ]
    for utterance in selected_dialogue.get("utterances") or []:
        if not isinstance(utterance, dict):
            continue
        start = max(0, int(float(utterance.get("start", 0))))
        minutes, seconds = divmod(start, 60)
        if utterance.get("role") == "remote":
            attribution = by_id[str(utterance["id"])]
            label = str(attribution.get("speaker_id") or "Colleagues")
        else:
            label = "Me"
        lines.extend(
            [
                f"## {minutes:02d}:{seconds:02d} {label}",
                "",
                str(utterance.get("text") or ""),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def output_identity(path: str, payload: bytes) -> dict[str, Any]:
    return {"scope": "session", "path": path, "bytes": len(payload), "sha256": sha256_bytes(payload)}


def semantic_basis(
    session: Path,
    material: dict[str, Any],
    filenames: dict[str, str],
) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation_provenance(),
        "session_id": session.name,
        "selected_profile": material["selected_profile"],
        "source_handoff_fingerprint": material["selected_manifest"].get("semantic_fingerprint"),
        "inputs": material["inputs"],
        "outputs": {
            key: {
                "filename": filenames[key],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
            for key, payload in sorted(material["output_payloads"].items())
        },
        "referential_integrity": material["integrity"],
        "scope": "optional_session_local_anonymous",
    }


def report_markdown(manifest: dict[str, Any]) -> str:
    reasons = manifest.get("reasons") or []
    lines = [
        "# Anonymous Rich Transcript Handoff v1",
        "",
        f"- State: `{manifest.get('state')}`",
        f"- Selected profile: `{manifest.get('selected_profile') or 'unknown'}`",
        f"- Semantic fingerprint: `{manifest.get('semantic_fingerprint') or 'none'}`",
        f"- Ordinary outputs unchanged: `{bool((manifest.get('safety') or {}).get('ordinary_outputs_unchanged'))}`",
        "",
        "This artifact is optional. Plain transcript, notes, verdict and guarded export remain authoritative.",
    ]
    if reasons:
        lines.extend(["", "## Reasons", ""])
        lines.extend(f"- `{reason}`" for reason in reasons)
    return "\n".join(lines) + "\n"


def unavailable_manifest(session: Path, policy: Path, reason: str) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if policy.is_file() and within(policy, repo_root()):
        inputs["policy"] = identity(policy, repository=True)
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation_provenance(),
        "session_id": session.name,
        "state": "unavailable",
        "selected_profile": None,
        "semantic_fingerprint": None,
        "inputs": inputs,
        "bundle": None,
        "gates": {"publish_optional_rich": False},
        "safety": {
            "ordinary_outputs_unchanged": True,
            "plain_transcript_authoritative": True,
            "speaker_names_published": False,
            "cross_session_identity_linking": False,
        },
        "reasons": [reason],
        "fallback": {"command": f'murmurmark transcript "sessions/{session.name}"'},
    }


def publish_unavailable(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "handoff_manifest.json", canonical_json_bytes(manifest))
    atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))


def build_material(session: Path, policy: Path, audit_dir: Path) -> dict[str, Any]:
    policy_payload = read_json(policy)
    source_policy, corpus_manifest = validate_policy(policy_payload, policy)
    selected_manifest, dialogue_path, resolved_transcript, bundle_transcript = selected_handoff(session)
    selected_dialogue = read_json(dialogue_path)
    selected_profile = selected_manifest["selected_profile"]
    baseline = baseline_identities(
        session, selected_manifest, dialogue_path, resolved_transcript, bundle_transcript
    )
    report, speaker_map, attributions, audit_manifest, audit_inputs = validate_audit_artifacts(
        session, audit_dir, source_policy, selected_dialogue
    )
    inputs: dict[str, dict[str, Any]] = {
        "policy": identity(policy, repository=True),
        "corpus_manifest": identity(policy_corpus_manifest(policy_payload), repository=True),
        "selected_dialogue": identity(dialogue_path, session=session),
        "resolved_plain_transcript": identity(resolved_transcript, session=session),
        "authoritative_plain_transcript": identity(bundle_transcript, session=session),
        "evidence_handoff_manifest": identity(
            session / "derived/handoff-v2/handoff_manifest.json", session=session
        ),
        **audit_inputs,
    }
    corpus_session = next(
        (
            row
            for row in corpus_manifest.get("sessions") or []
            if isinstance(row, dict) and row.get("session_id") == session.name
        ),
        None,
    )
    if corpus_session is not None:
        frozen_outputs = corpus_session.get("outputs")
        current_outputs = {
            "report": inputs["audit_report"],
            "map": inputs["speaker_map"],
            "attribution": inputs["utterance_attribution"],
            "manifest": inputs["audit_artifact_manifest"],
        }
        if not isinstance(frozen_outputs, dict) or any(
            not isinstance(frozen_outputs.get(key), dict)
            or frozen_outputs[key].get("bytes") != value.get("bytes")
            or frozen_outputs[key].get("sha256") != value.get("sha256")
            for key, value in current_outputs.items()
        ):
            raise RichHandoffError("frozen_session_audit_output_mismatch")

    integrity = {
        "selected_utterances": len(selected_dialogue.get("utterances") or []),
        "remote_utterances": len(attributions),
        "attribution_ids_exact": True,
        "remote_projection_exact": True,
        "boundaries_exact": True,
        "anonymous_labels_only": True,
        "selected_dialogue_logically_exact": True,
    }
    rich_payload = {
        "schema": TRANSCRIPT_SCHEMA,
        "version": 1,
        "status": "optional_anonymous_evidence",
        "session_id": session.name,
        "selected_profile": selected_profile,
        "source_handoff_fingerprint": selected_manifest.get("semantic_fingerprint"),
        "source_evidence_decision": source_policy.get("decision"),
        "utterances": selected_dialogue["utterances"],
        "remote_speaker_attributions": attributions,
        "speaker_map": speaker_map.get("speakers") or [],
        "referential_integrity": integrity,
        "constraints": {
            "scope": "session_local_anonymous",
            "authoritative": False,
            "speaker_names_allowed": False,
            "cross_session_identity_allowed": False,
            "plain_transcript_unchanged": True,
        },
    }
    rich_json = canonical_json_bytes(rich_payload)
    rich_markdown = render_markdown(selected_dialogue, attributions, selected_profile).encode("utf-8")
    return {
        "policy": policy_payload,
        "source_policy": source_policy,
        "selected_manifest": selected_manifest,
        "selected_profile": selected_profile,
        "inputs": inputs,
        "baseline": baseline,
        "integrity": integrity,
        "audit_report": report,
        "audit_manifest": audit_manifest,
        "output_payloads": {
            "transcript_json": rich_json,
            "transcript_markdown": rich_markdown,
        },
    }


def immutable_bundle_valid(bundle: Path, expected: dict[str, bytes]) -> bool:
    return all((bundle / name).is_file() and (bundle / name).read_bytes() == payload for name, payload in expected.items())


def build_handoff(
    session: Path,
    policy: Path,
    audit_dir: Path,
    root: Path,
    *,
    simulate_interruption_before_publish: bool = False,
) -> dict[str, Any]:
    material = build_material(session, policy, audit_dir)
    filenames = {
        "transcript_json": "transcript.rich.json",
        "transcript_markdown": "transcript.rich.md",
    }
    basis = semantic_basis(session, material, filenames)
    fingerprint = sha256_bytes(compact_json_bytes(basis))
    bundle_relative = f"{session_relative(root, session)}/bundles/{fingerprint}"
    bundle = session / bundle_relative
    files = {
        key: output_identity(f"{bundle_relative}/{filenames[key]}", payload)
        for key, payload in sorted(material["output_payloads"].items())
    }
    expected_bundle = {
        filenames[key]: payload for key, payload in material["output_payloads"].items()
    }

    unchanged = all(identity_matches(row, session) for row in material["baseline"].values())
    if not unchanged:
        raise RichHandoffError("ordinary_output_changed_before_publication")
    manifest = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation_provenance(),
        "session_id": session.name,
        "state": "ready",
        "selected_profile": material["selected_profile"],
        "semantic_fingerprint": fingerprint,
        "fingerprint_basis": basis,
        "inputs": material["inputs"],
        "bundle": {"path": bundle_relative, "files": files},
        "gates": {
            "publish_optional_rich": True,
            "policy_current": True,
            "corpus_promoted": True,
            "session_audit_promoted": True,
            "selected_handoff_current": True,
            "remote_projection_exact": True,
            "referential_integrity": True,
            "anonymous_labels_only": True,
            "ordinary_outputs_unchanged": unchanged,
        },
        "referential_integrity": material["integrity"],
        "safety": {
            "baseline_identities": material["baseline"],
            "ordinary_outputs_unchanged": unchanged,
            "plain_transcript_authoritative": True,
            "speaker_names_published": False,
            "cross_session_identity_linking": False,
            "notes_mutated": False,
            "evidence_handoff_mutated": False,
            "guarded_export_mutated": False,
        },
        "reasons": [],
        "recommended_next": f'murmurmark transcript "sessions/{session.name}" --rich',
    }
    expected_bundle["handoff_manifest.json"] = canonical_json_bytes(manifest)

    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging.", dir=root))
    try:
        for name, payload in expected_bundle.items():
            write_durable(staging / name, payload)
        fsync_directory(staging)
        bundles = root / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            if not immutable_bundle_valid(bundle, expected_bundle):
                raise RichHandoffError("existing_immutable_bundle_invalid")
            shutil.rmtree(staging)
        else:
            os.replace(staging, bundle)
            fsync_directory(bundles)

        if not all(identity_matches(row, session) for row in material["baseline"].values()):
            raise RichHandoffError("ordinary_output_changed_during_publication")
        if simulate_interruption_before_publish:
            raise SimulatedInterruption("simulated interruption before rich handoff publish")
        atomic_write(root / "handoff_manifest.json", canonical_json_bytes(manifest))
        atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def verify_handoff(session: Path, policy: Path, audit_dir: Path, root: Path) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        current = read_json(root / "handoff_manifest.json")
    except RichHandoffError as error:
        return None, [str(error)]
    if current.get("schema") != HANDOFF_SCHEMA:
        return None, ["handoff_schema_mismatch"]
    if current.get("state") != "ready":
        return None, [str(item) for item in current.get("reasons") or ["rich_handoff_unavailable"]]
    if current.get("generator") != implementation_provenance():
        return None, ["generator_fingerprint_mismatch"]
    try:
        material = build_material(session, policy, audit_dir)
    except RichHandoffError as error:
        return None, [str(error)]
    basis = current.get("fingerprint_basis")
    fingerprint = current.get("semantic_fingerprint")
    if not isinstance(basis, dict) or not isinstance(fingerprint, str):
        return None, ["semantic_fingerprint_missing"]
    filenames = {
        "transcript_json": "transcript.rich.json",
        "transcript_markdown": "transcript.rich.md",
    }
    expected_basis = semantic_basis(session, material, filenames)
    if basis != expected_basis:
        return None, ["semantic_basis_mismatch"]
    if current.get("inputs") != material["inputs"]:
        return None, ["manifest_inputs_mismatch"]
    if current.get("selected_profile") != material["selected_profile"]:
        return None, ["selected_profile_mismatch"]
    if current.get("referential_integrity") != material["integrity"]:
        return None, ["manifest_referential_integrity_mismatch"]
    if sha256_bytes(compact_json_bytes(basis)) != fingerprint:
        return None, ["semantic_fingerprint_mismatch"]
    bundle = current.get("bundle")
    files = bundle.get("files") if isinstance(bundle, dict) else None
    bundle_path = resolve_inside(session, bundle.get("path")) if isinstance(bundle, dict) else None
    if (
        not isinstance(files, dict)
        or set(files) != {"transcript_json", "transcript_markdown"}
        or bundle_path is None
        or bundle_path.name != fingerprint
    ):
        return None, ["bundle_path_invalid"]
    if not all(isinstance(row, dict) and identity_matches(row, session) for row in files.values()):
        return None, ["bundle_file_identity_mismatch"]
    bundle_manifest = bundle_path / "handoff_manifest.json"
    if (
        not bundle_manifest.is_file()
        or bundle_manifest.read_bytes() != canonical_json_bytes(current)
    ):
        return None, ["bundle_manifest_mismatch"]
    expected = {
        "transcript_json": sha256_bytes(material["output_payloads"]["transcript_json"]),
        "transcript_markdown": sha256_bytes(material["output_payloads"]["transcript_markdown"]),
    }
    for key, expected_sha in expected.items():
        row = files.get(key)
        if not isinstance(row, dict) or row.get("sha256") != expected_sha:
            return None, [f"bundle_semantic_mismatch:{key}"]
    baseline = (current.get("safety") or {}).get("baseline_identities")
    if not isinstance(baseline, dict) or not all(
        isinstance(row, dict) and identity_matches(row, session) for row in baseline.values()
    ):
        return None, ["ordinary_output_fingerprint_mismatch"]
    return current, []


def rich_path(manifest: dict[str, Any], session: Path, kind: str) -> Path | None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    row = files.get(kind)
    return resolve_identity(row, session) if isinstance(row, dict) else None


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    policy = policy_path(args)
    try:
        audit_dir = audit_root(args, session)
        root = output_root(args, session)
    except RichHandoffError as error:
        print(f"anonymous_rich_handoff: unavailable ({error})")
        return 2

    if args.verify_only:
        manifest, reasons = verify_handoff(session, policy, audit_dir, root)
        if manifest is None:
            print("anonymous_rich_handoff: invalid")
            for reason in reasons:
                print(f"  {reason}")
            return 2
        path = rich_path(manifest, session, "transcript_markdown")
        if args.print_path and path is not None:
            print(path)
        else:
            print(f"anonymous_rich_handoff: ready fingerprint={manifest['semantic_fingerprint']}")
        return 0

    try:
        manifest = build_handoff(
            session,
            policy,
            audit_dir,
            root,
            simulate_interruption_before_publish=args.simulate_interruption_before_publish,
        )
    except SimulatedInterruption as error:
        print(str(error))
        return 3
    except RichHandoffError as error:
        manifest = unavailable_manifest(session, policy, str(error))
        publish_unavailable(root, manifest)

    path = rich_path(manifest, session, "transcript_markdown")
    if args.print_path and path is not None:
        print(path)
    else:
        print("anonymous_rich_handoff:")
        print(f"  state: {manifest.get('state')}")
        print(f"  profile: {manifest.get('selected_profile') or 'none'}")
        print(f"  fingerprint: {manifest.get('semantic_fingerprint') or 'none'}")
        print(f"  ordinary_outputs_unchanged: {bool((manifest.get('safety') or {}).get('ordinary_outputs_unchanged'))}")
        if manifest.get("reasons"):
            print(f"  reason: {manifest['reasons'][0]}")
        print(f"  manifest: {root / 'handoff_manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
