#!/usr/bin/env python3
"""Materialize the best current speaker-attributed read view without weakening strict gates."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0"
SELECTION_SCHEMA = "murmurmark.provisional_speaker_transcript_selection/v1"
TRANSCRIPT_SCHEMA = "murmurmark.provisional_speaker_transcript/v1"
READINESS_SCHEMA = "murmurmark.session_readiness/v1"
STRICT_SELECTION_SCHEMA = "murmurmark.speaker_resolved_transcript_selection/v1"
V1_REPORT_SCHEMA = "murmurmark.remote_speaker_evidence_report/v1"
V1_MANIFEST_SCHEMA = "murmurmark.remote_speaker_evidence_artifact_manifest/v1"
DEFAULT_OUT_DIR = Path("derived/transcript-rich/speaker-resolved-default-v1/provisional")
STRICT_SELECTION = Path("derived/transcript-rich/speaker-resolved-default-v1/selection.json")
STRICT_EVIDENCE_ROOT = Path("derived/transcript-rich/speaker-resolved-default-v1/evidence")
CANONICAL_V1 = Path("derived/audit/remote-speaker-evidence-v1")
DEFAULT_ROSTER = Path("derived/transcript-rich/speaker-roster-v1.json")
V1_IMPLEMENTATION = ROOT / "scripts/audit-remote-speaker-evidence.py"
DISALLOWED_ASSIGNMENT_REASONS = {
    "possible_remote_double_talk",
    "input_changed_during_run",
}
PROVISIONAL_SECONDARY_UNIT_RATIO = 0.8
PROVISIONAL_SECONDARY_SPEECH_RATIO = 0.8
PROVISIONAL_SECONDARY_MIN_COHESION = 0.9


class ProvisionalSpeakerError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Materialize a disclaimer-bearing provisional speaker-attributed transcript when "
            "strict speaker publication gates do not pass."
        )
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    return parser.parse_args()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProvisionalSpeakerError(f"expected_json_object:{path.name}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ProvisionalSpeakerError(f"expected_jsonl_object:{path.name}:{number}")
        rows.append(value)
    return rows


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_session_path(session: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else session / candidate
    resolved = candidate.resolve()
    return resolved if within(resolved, session) else None


def relative(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def identity(path: Path, session: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    display = relative(resolved, session) if session is not None else str(resolved.relative_to(ROOT))
    result: dict[str, Any] = {"path": display, "exists": path.is_file()}
    if path.is_file():
        result.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return result


def same_identity(row: Any, path: Path) -> bool:
    return bool(
        isinstance(row, dict)
        and row.get("exists") is True
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def source_identity_matches(session: Path, row: Any, expected: Path | None = None) -> bool:
    if not isinstance(row, dict):
        return False
    path = resolve_session_path(session, row.get("path"))
    if path is None or (expected is not None and path != expected.resolve()):
        return False
    return same_identity(row, path)


def v1_source_current(session: Path, report: dict[str, Any], dialogue: Path) -> bool:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    if not source_identity_matches(session, source.get("dialogue"), dialogue):
        return False
    for key in ("remote_audio", "raw_remote_after"):
        row = source.get(key)
        if isinstance(row, dict) and row.get("exists") is True:
            if not source_identity_matches(session, row):
                return False
    roster = session / DEFAULT_ROSTER
    roster_row = source.get("speaker_roster")
    if roster.is_file():
        return source_identity_matches(session, roster_row, roster)
    return not isinstance(roster_row, dict) or roster_row.get("exists") is False


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def readiness_inputs(session: Path) -> tuple[str, Path, Path]:
    readiness_path = session / "derived/readiness/session_readiness.json"
    readiness = read_json(readiness_path)
    if readiness.get("schema") != READINESS_SCHEMA:
        raise ProvisionalSpeakerError("readiness_schema_invalid")
    profile = str(readiness.get("selected_profile") or "").strip()
    outputs = readiness.get("outputs") if isinstance(readiness.get("outputs"), dict) else {}
    transcript = outputs.get("transcript") if isinstance(outputs.get("transcript"), dict) else {}
    dialogue = outputs.get("clean_dialogue") if isinstance(outputs.get("clean_dialogue"), dict) else {}
    aggregate_path = resolve_session_path(session, transcript.get("path"))
    dialogue_path = resolve_session_path(session, dialogue.get("path"))
    if not profile or aggregate_path is None or not aggregate_path.is_file():
        raise ProvisionalSpeakerError("aggregate_transcript_missing")
    if dialogue_path is None or not dialogue_path.is_file():
        raise ProvisionalSpeakerError("selected_dialogue_missing")
    return profile, aggregate_path, dialogue_path


def strict_selection(session: Path, profile: str, aggregate: Path, dialogue: Path) -> dict[str, Any] | None:
    path = session / STRICT_SELECTION
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError, ProvisionalSpeakerError):
        return None
    if payload.get("schema") != STRICT_SELECTION_SCHEMA or payload.get("selected_profile") != profile:
        return None
    if not same_identity(payload.get("aggregate_transcript"), aggregate):
        return None
    if not same_identity(payload.get("selected_dialogue"), dialogue):
        return None
    if payload.get("state") == "selected":
        selected = resolve_session_path(session, (payload.get("selected_transcript") or {}).get("path"))
        if selected is None or not same_identity(payload.get("selected_transcript"), selected):
            return None
    return payload


def verify_manifest(directory: Path) -> bool:
    manifest_path = directory / "artifact_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError, json.JSONDecodeError, ProvisionalSpeakerError):
        return False
    if manifest.get("schema") != V1_MANIFEST_SCHEMA:
        return False
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    required = {"report.json", "utterance_attribution.jsonl"}
    if not required.issubset(artifacts):
        return False
    return all(
        (directory / name).is_file() and sha256_file(directory / name) == digest
        for name, digest in artifacts.items()
        if isinstance(name, str) and isinstance(digest, str)
    )


def current_v1_candidate(
    session: Path, profile: str, dialogue: Path
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]] | None:
    candidates = list((session / STRICT_EVIDENCE_ROOT).glob("*/remote-speaker-evidence-v1"))
    candidates.append(session / CANONICAL_V1)
    valid: list[tuple[tuple[int, str], Path, dict[str, Any], list[dict[str, Any]]]] = []
    implementation_sha = sha256_file(V1_IMPLEMENTATION)
    for directory in sorted(set(path.resolve() for path in candidates)):
        report_path = directory / "report.json"
        attribution_path = directory / "utterance_attribution.jsonl"
        if not report_path.is_file() or not attribution_path.is_file() or not verify_manifest(directory):
            continue
        try:
            report = read_json(report_path)
            attributions = read_jsonl(attribution_path)
        except (OSError, ValueError, json.JSONDecodeError, ProvisionalSpeakerError):
            continue
        source = report.get("source") if isinstance(report.get("source"), dict) else {}
        implementation = report.get("implementation") if isinstance(report.get("implementation"), dict) else {}
        fingerprint = implementation.get("fingerprint") if isinstance(implementation.get("fingerprint"), dict) else {}
        if report.get("schema") != V1_REPORT_SCHEMA or str(source.get("profile") or "") != profile:
            continue
        if fingerprint.get("sha256") != implementation_sha:
            continue
        if not v1_source_current(session, report, dialogue):
            continue
        raw_before = source.get("raw_remote_before")
        raw_after = source.get("raw_remote_after")
        if isinstance(raw_before, dict) and isinstance(raw_after, dict):
            if raw_before.get("sha256") != raw_after.get("sha256"):
                continue
        rank = 2 if report.get("decision") == "PUBLISH_AUDIT_EVIDENCE" else 1
        valid.append(((rank, str(directory)), directory, report, attributions))
    if not valid:
        return None
    _, directory, report, attributions = max(valid, key=lambda row: row[0])
    return directory, report, attributions


def relaxed_evidence_key(profile: str, dialogue: Path, source_report: dict[str, Any]) -> str:
    return sha256_bytes(
        compact_json_bytes(
            {
                "profile": profile,
                "dialogue_sha256": sha256_file(dialogue),
                "v1_implementation_sha256": sha256_file(V1_IMPLEMENTATION),
                "source_model": source_report.get("model"),
                "source_roster": (source_report.get("source") or {}).get("speaker_roster"),
                "source_remote_audio": (source_report.get("source") or {}).get("remote_audio"),
                "source_raw_remote": (source_report.get("source") or {}).get("raw_remote_after"),
                "mode": "provisional_zero_global_coverage_floor_v1",
            }
        )
    )


def can_refresh_relaxed_v1(session: Path, report: dict[str, Any]) -> bool:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    remote_audio = source.get("remote_audio")
    return source_identity_matches(session, remote_audio)


def refresh_relaxed_v1(
    session: Path,
    profile: str,
    dialogue: Path,
    out_dir: Path,
    source_report: dict[str, Any],
) -> tuple[Path, dict[str, Any], list[dict[str, Any]]] | None:
    evidence_dir = (
        out_dir
        / "evidence"
        / relaxed_evidence_key(profile, dialogue, source_report)
        / "remote-speaker-evidence-v1"
    )
    report_path = evidence_dir / "report.json"
    attribution_path = evidence_dir / "utterance_attribution.jsonl"
    if report_path.is_file() and attribution_path.is_file() and verify_manifest(evidence_dir):
        report = read_json(report_path)
        if (
            report.get("schema") == V1_REPORT_SCHEMA
            and report.get("decision") == "PUBLISH_AUDIT_EVIDENCE"
            and v1_source_current(session, report, dialogue)
        ):
            return evidence_dir, report, read_jsonl(attribution_path)
    command = [
        sys.executable,
        str(V1_IMPLEMENTATION),
        str(session),
        "--profile",
        profile,
        "--out-dir",
        str(evidence_dir),
        "--min-published-speech-ratio",
        "0",
        "--no-progress",
    ]
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0 or not report_path.is_file() or not attribution_path.is_file():
        return None
    try:
        report = read_json(report_path)
        attributions = read_jsonl(attribution_path)
    except (OSError, ValueError, json.JSONDecodeError, ProvisionalSpeakerError):
        return None
    if (
        report.get("schema") != V1_REPORT_SCHEMA
        or report.get("decision") != "PUBLISH_AUDIT_EVIDENCE"
        or not verify_manifest(evidence_dir)
        or not v1_source_current(session, report, dialogue)
    ):
        return None
    return evidence_dir, report, attributions


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def strict_major_cluster(cluster: dict[str, Any], parameters: dict[str, Any]) -> bool:
    return bool(
        integer(cluster.get("unit_count")) >= integer(parameters.get("min_cluster_units") or 10)
        and number(cluster.get("speech_sec")) >= number(parameters.get("min_cluster_sec") or 60.0)
        and number(cluster.get("span_sec")) >= number(parameters.get("min_cluster_span_sec") or 60.0)
        and number(cluster.get("cohesion_median")) >= number(parameters.get("min_cluster_cohesion") or 0.85)
    )


def provisional_secondary_cluster(
    cluster: dict[str, Any], parameters: dict[str, Any]
) -> bool:
    min_units = integer(parameters.get("min_cluster_units") or 10)
    min_speech_sec = number(parameters.get("min_cluster_sec") or 60.0)
    min_span_sec = number(parameters.get("min_cluster_span_sec") or 60.0)
    min_cohesion = number(parameters.get("min_cluster_cohesion") or 0.85)
    return bool(
        not cluster.get("speaker_id")
        and not strict_major_cluster(cluster, parameters)
        and integer(cluster.get("unit_count"))
        >= math.ceil(min_units * PROVISIONAL_SECONDARY_UNIT_RATIO)
        and number(cluster.get("speech_sec"))
        >= min_speech_sec * PROVISIONAL_SECONDARY_SPEECH_RATIO
        and number(cluster.get("span_sec")) >= min_span_sec
        and number(cluster.get("cohesion_median"))
        >= max(min_cohesion, PROVISIONAL_SECONDARY_MIN_COHESION)
    )


def speaker_candidates(report: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    parameters = report.get("parameters") if isinstance(report.get("parameters"), dict) else {}
    clusters = [row for row in report.get("clusters") or [] if isinstance(row, dict)]
    stable: list[dict[str, Any]] = []
    for cluster in clusters:
        if cluster.get("speaker_id"):
            stable.append(cluster)
        elif strict_major_cluster(cluster, parameters):
            stable.append(cluster)
    secondary = [
        cluster
        for cluster in clusters
        if stable and provisional_secondary_cluster(cluster, parameters)
    ]
    stable.sort(key=lambda row: (number(row.get("first_start")), integer(row.get("cluster"))))
    secondary.sort(key=lambda row: (number(row.get("first_start")), integer(row.get("cluster"))))
    mapping: dict[int, dict[str, Any]] = {}
    used: set[str] = set()
    next_index = 1
    for tier, rows in (
        ("stable_cluster", stable),
        ("provisional_secondary_cluster", secondary),
    ):
        for cluster in rows:
            existing = str(cluster.get("speaker_id") or "").strip()
            if existing:
                speaker_id = existing
            else:
                while f"remote_speaker_{next_index:02d}" in used:
                    next_index += 1
                speaker_id = f"remote_speaker_{next_index:02d}"
                next_index += 1
            used.add(speaker_id)
            mapping[integer(cluster.get("cluster"))] = {
                "speaker_id": speaker_id,
                "tier": tier,
                "cluster": integer(cluster.get("cluster")),
                "unit_count": integer(cluster.get("unit_count")),
                "speech_sec": round(number(cluster.get("speech_sec")), 6),
                "span_sec": round(number(cluster.get("span_sec")), 6),
                "cohesion_median": round(number(cluster.get("cohesion_median")), 6),
                "first_start": round(number(cluster.get("first_start")), 6),
            }
    return mapping, sorted(mapping.values(), key=lambda row: row["speaker_id"])


def attribution_map(
    rows: list[dict[str, Any]], speakers: dict[int, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        utterance_id = str(row.get("utterance_id") or "").strip()
        if not utterance_id:
            continue
        speaker_id = str(row.get("speaker_id") or "").strip()
        cluster = integer(row.get("cluster")) if row.get("cluster") is not None else None
        candidate = speakers.get(cluster) if cluster is not None else None
        reason = str(row.get("reason") or "speaker_evidence_unavailable")
        if reason in DISALLOWED_ASSIGNMENT_REASONS:
            speaker_id = ""
            candidate = None
        if not speaker_id and candidate is not None:
            speaker_id = candidate["speaker_id"]
        result[utterance_id] = {
            "speaker_id": speaker_id or None,
            "speaker_label": speaker_id or "remote_speaker_unknown",
            "tier": candidate.get("tier") if candidate is not None else (
                "strict_v1_assignment" if speaker_id else "unattributed"
            ),
            "reason": reason,
            "cluster": cluster,
        }
    return result


def format_time(seconds: Any) -> str:
    value = max(0.0, number(seconds))
    return f"{int(value // 60):02d}:{int(value % 60):02d}"


def render_markdown(
    utterances: list[dict[str, Any]],
    attributions: dict[str, dict[str, Any]],
    profile: str,
    state: str,
    reason: str,
    summary: dict[str, Any],
) -> str:
    if state == "provisional":
        warning = (
            "**Speaker attribution is provisional.** Anonymous `remote_speaker_NN` labels are "
            "best-effort acoustic clusters and may merge one person or split one person into several labels."
        )
    else:
        warning = (
            "**Speaker attribution is unavailable.** Remote speech is marked "
            "`remote_speaker_unknown`; do not interpret it as one person."
        )
    lines = [
        "# MurmurMark Speaker-Attributed Transcript",
        "",
        "> [!WARNING]",
        f"> {warning}",
        f"> Strict speaker gate: `{reason}`.",
        (
            "> Attributed remote speech: "
            f"`{number(summary.get('attributed_remote_speech_ratio')) * 100:.1f}%`; "
            f"anonymous clusters shown: `{integer(summary.get('speaker_clusters'))}`."
        ),
        (
            "> Secondary clusters below the strict publication gate: "
            f"`{integer(summary.get('provisional_secondary_clusters'))}`."
        ),
        "> The text, roles and timestamps come from the authoritative batch transcript.",
        "",
        f"Transcript profile: `{profile}`  ",
        f"Speaker attribution state: `{state}`  ",
        "Speaker identities: session-local and anonymous",
        "",
    ]
    for utterance in utterances:
        role = str(utterance.get("role") or "")
        timestamp = format_time(utterance.get("start"))
        text = str(utterance.get("text") or "").strip()
        if role == "remote":
            row = attributions.get(str(utterance.get("id") or ""), {})
            label = str(row.get("speaker_label") or "remote_speaker_unknown")
            suffix = ""
            if label == "remote_speaker_unknown":
                suffix = " [unattributed]"
            lines.extend([f"## {timestamp} {label}{suffix}", "", text, ""])
        else:
            lines.extend([f"## {timestamp} Me", "", text, ""])
    return "\n".join(lines).rstrip() + "\n"


def semantic_basis(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: payload.get(key)
        for key in (
            "schema",
            "version",
            "session_id",
            "state",
            "selected_profile",
            "selected_speaker_profile",
            "fallback_reason",
            "aggregate_transcript",
            "selected_dialogue",
            "selected_transcript",
            "rich_transcript",
            "source_evidence",
            "strict_selection",
            "implementation",
        )
    }


def verify_existing(
    session: Path, out_dir: Path, profile: str, aggregate: Path, dialogue: Path
) -> tuple[dict[str, Any] | None, list[str]]:
    selection_path = out_dir / "selection.json"
    try:
        payload = read_json(selection_path)
    except (OSError, ValueError, json.JSONDecodeError, ProvisionalSpeakerError) as error:
        return None, [f"selection_unavailable:{type(error).__name__}"]
    reasons: list[str] = []
    if payload.get("schema") != SELECTION_SCHEMA:
        reasons.append("selection_schema_invalid")
    if payload.get("selected_profile") != profile:
        reasons.append("selection_profile_stale")
    if not same_identity(payload.get("aggregate_transcript"), aggregate):
        reasons.append("selection_aggregate_stale")
    if not same_identity(payload.get("selected_dialogue"), dialogue):
        reasons.append("selection_dialogue_stale")
    selected = resolve_session_path(session, (payload.get("selected_transcript") or {}).get("path"))
    rich = resolve_session_path(session, (payload.get("rich_transcript") or {}).get("path"))
    if selected is None or not same_identity(payload.get("selected_transcript"), selected):
        reasons.append("selection_output_stale")
    if rich is None or not same_identity(payload.get("rich_transcript"), rich):
        reasons.append("selection_rich_output_stale")
    implementation = payload.get("implementation")
    if not same_identity(implementation, Path(__file__).resolve()):
        reasons.append("selection_implementation_stale")
    source = payload.get("source_evidence")
    if isinstance(source, dict) and source.get("exists") is True:
        source_path = resolve_session_path(session, source.get("path"))
        if source_path is None or not same_identity(source, source_path):
            reasons.append("selection_source_evidence_stale")
    strict_path = session / STRICT_SELECTION
    strict_row = payload.get("strict_selection")
    if strict_path.is_file():
        if not same_identity(strict_row, strict_path):
            reasons.append("selection_strict_selection_stale")
    elif isinstance(strict_row, dict) and strict_row.get("exists") is True:
        reasons.append("selection_strict_selection_stale")
    if payload.get("semantic_fingerprint") != sha256_bytes(compact_json_bytes(semantic_basis(payload))):
        reasons.append("selection_fingerprint_invalid")
    if payload.get("state") not in {"provisional", "unavailable"}:
        reasons.append("selection_state_invalid")
    return payload, reasons


def materialize(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        raise ProvisionalSpeakerError(f"session_json_missing:{session}")
    out_dir = args.out_dir if args.out_dir.is_absolute() else session / args.out_dir
    profile, aggregate, dialogue = readiness_inputs(session)
    strict = strict_selection(session, profile, aggregate, dialogue)
    if strict is not None and strict.get("state") == "selected":
        return {
            "state": "verified",
            "selected_profile": profile,
            "selected_speaker_profile": strict.get("selected_speaker_profile"),
            "selected_transcript": strict.get("selected_transcript"),
            "summary": {},
        }
    existing, reasons = verify_existing(session, out_dir, profile, aggregate, dialogue)
    if existing is not None and not reasons:
        return existing
    fallback_reason = str(
        (strict or {}).get("fallback_reason") or "strict_speaker_selection_unavailable"
    )
    dialogue_payload = read_json(dialogue)
    utterances = dialogue_payload.get("utterances") or dialogue_payload.get("dialogue") or []
    if not isinstance(utterances, list):
        raise ProvisionalSpeakerError("selected_dialogue_utterances_invalid")

    candidate = current_v1_candidate(session, profile, dialogue)
    if (
        candidate is not None
        and candidate[1].get("decision") != "PUBLISH_AUDIT_EVIDENCE"
        and can_refresh_relaxed_v1(session, candidate[1])
    ):
        candidate = (
            refresh_relaxed_v1(session, profile, dialogue, out_dir, candidate[1]) or candidate
        )
    source_evidence: dict[str, Any]
    warnings = [fallback_reason]
    speaker_rows: list[dict[str, Any]] = []
    mapped: dict[str, dict[str, Any]] = {}
    if candidate is None:
        source_evidence = {"path": str(CANONICAL_V1 / "report.json"), "exists": False}
        warnings.append("compatible_current_speaker_evidence_unavailable")
    else:
        directory, report, attribution_rows = candidate
        source_evidence = identity(directory / "report.json", session)
        speakers, speaker_rows = speaker_candidates(report)
        mapped = attribution_map(attribution_rows, speakers)
        used_speakers = {
            str(row["speaker_id"])
            for row in mapped.values()
            if isinstance(row.get("speaker_id"), str) and row.get("speaker_id")
        }
        speaker_rows = [row for row in speaker_rows if row["speaker_id"] in used_speakers]
        if any(row["tier"] == "provisional_secondary_cluster" for row in speaker_rows):
            warnings.append("provisional_secondary_cluster_evidence")
        warnings.extend(str(value) for value in report.get("reasons") or [])

    remote = [row for row in utterances if isinstance(row, dict) and row.get("role") == "remote"]
    remote_seconds = sum(max(0.0, number(row.get("end")) - number(row.get("start"))) for row in remote)
    attributed_seconds = sum(
        max(0.0, number(row.get("end")) - number(row.get("start")))
        for row in remote
        if mapped.get(str(row.get("id") or ""), {}).get("speaker_id")
    )
    attributed_count = sum(
        bool(mapped.get(str(row.get("id") or ""), {}).get("speaker_id")) for row in remote
    )
    state = "provisional" if attributed_count else "unavailable"
    speaker_profile = (
        "remote_speaker_provisional_v1"
        if state == "provisional"
        else "remote_speaker_attribution_unavailable_v1"
    )
    summary = {
        "remote_utterances": len(remote),
        "attributed_remote_utterances": attributed_count,
        "remote_speech_sec": round(remote_seconds, 6),
        "attributed_remote_speech_sec": round(attributed_seconds, 6),
        "attributed_remote_speech_ratio": round(attributed_seconds / remote_seconds, 6)
        if remote_seconds
        else 0.0,
        "speaker_clusters": len(speaker_rows),
        "stable_clusters": sum(row["tier"] == "stable_cluster" for row in speaker_rows),
        "provisional_secondary_clusters": sum(
            row["tier"] == "provisional_secondary_cluster" for row in speaker_rows
        ),
    }
    normalized_attributions: dict[str, dict[str, Any]] = {}
    output_utterances: list[dict[str, Any]] = []
    for utterance in utterances:
        if not isinstance(utterance, dict):
            continue
        output = dict(utterance)
        if utterance.get("role") == "remote":
            utterance_id = str(utterance.get("id") or "")
            row = mapped.get(
                utterance_id,
                {
                    "speaker_id": None,
                    "speaker_label": "remote_speaker_unknown",
                    "tier": "unattributed",
                    "reason": "speaker_evidence_unavailable",
                    "cluster": None,
                },
            )
            normalized_attributions[utterance_id] = row
            output["speaker_id"] = row["speaker_id"]
            output["speaker_label"] = row["speaker_label"]
            output["speaker_attribution"] = row
        output_utterances.append(output)

    transcript_payload = {
        "schema": TRANSCRIPT_SCHEMA,
        "version": 1,
        "session_id": session.name,
        "state": state,
        "selected_profile": profile,
        "selected_speaker_profile": speaker_profile,
        "fallback_reason": fallback_reason,
        "warnings": sorted(set(warnings)),
        "summary": summary,
        "speaker_map": speaker_rows,
        "remote_utterance_attributions": normalized_attributions,
        "utterances": output_utterances,
        "safety": {
            "aggregate_transcript_unchanged": True,
            "selected_dialogue_unchanged": True,
            "text_roles_timestamps_unchanged": True,
            "session_local_anonymous_only": True,
            "human_identity_inference": False,
            "strict_verified_profile_unchanged": True,
        },
    }
    rich_path = out_dir / "transcript.provisional.json"
    markdown_path = out_dir / "transcript.provisional.md"
    atomic_write(rich_path, canonical_json_bytes(transcript_payload))
    atomic_write(
        markdown_path,
        render_markdown(
            output_utterances,
            normalized_attributions,
            profile,
            state,
            fallback_reason,
            summary,
        ).encode(),
    )
    selection: dict[str, Any] = {
        "schema": SELECTION_SCHEMA,
        "version": 1,
        "session_id": session.name,
        "state": state,
        "selected_profile": profile,
        "selected_speaker_profile": speaker_profile,
        "fallback_reason": fallback_reason,
        "warnings": sorted(set(warnings)),
        "summary": summary,
        "aggregate_transcript": identity(aggregate, session),
        "selected_dialogue": identity(dialogue, session),
        "selected_transcript": identity(markdown_path, session),
        "rich_transcript": identity(rich_path, session),
        "source_evidence": source_evidence,
        "strict_selection": identity(session / STRICT_SELECTION, session),
        "implementation": identity(Path(__file__).resolve()),
        "batch_authoritative": True,
        "aggregate_fallback_available": True,
        "identity_scope": "session_local_anonymous",
    }
    selection["semantic_fingerprint"] = sha256_bytes(compact_json_bytes(semantic_basis(selection)))
    atomic_write(out_dir / "selection.json", canonical_json_bytes(selection))
    return selection


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    out_dir = args.out_dir if args.out_dir.is_absolute() else session / args.out_dir
    try:
        if args.verify_only:
            profile, aggregate, dialogue = readiness_inputs(session)
            payload, reasons = verify_existing(session, out_dir, profile, aggregate, dialogue)
            valid = payload is not None and not reasons
            print(f"provisional_speaker_transcript: verify={'ok' if valid else 'stale_or_invalid'}")
            if reasons:
                print("reasons: " + ",".join(reasons))
            if valid and args.print_path:
                assert payload is not None
                print(session / payload["selected_transcript"]["path"])
            return 0 if valid else 2
        payload = materialize(args)
    except (OSError, ValueError, json.JSONDecodeError, ProvisionalSpeakerError) as error:
        print(f"provisional_speaker_transcript: error={error}", file=os.sys.stderr)
        return 2
    message = (
        "provisional_speaker_transcript: "
        f"state={payload['state']} profile={payload['selected_profile']} "
        f"speaker_profile={payload['selected_speaker_profile']}"
    )
    coverage = (payload.get("summary") or {}).get("attributed_remote_speech_ratio")
    if coverage is not None:
        message += f" coverage={number(coverage):.6f}"
    print(message)
    if args.print_path:
        selected = payload.get("selected_transcript") or {}
        if selected.get("path"):
            print(session / selected["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
