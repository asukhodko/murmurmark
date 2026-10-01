#!/usr/bin/env python3
"""Materialize the best current speaker-attributed read view without weakening strict gates."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

import transcript_publication as publication
import micro_asr_evidence
import acoustic_timing_evidence

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.3.0"
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
    parser.add_argument("--cached-only", action="store_true",
                        help="Publish compatible cached evidence without starting inference.")
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
    return v1_audio_source_current(session, report)


def v1_audio_source_current(session: Path, report: dict[str, Any]) -> bool:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
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
    candidates.extend((session / DEFAULT_OUT_DIR / "evidence").glob("*/remote-speaker-evidence-v1"))
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


def strict_selection_basis(session: Path) -> dict[str, Any]:
    """Bind decisions and evidence, not diagnostic cached/refreshed reason strings."""
    try:
        payload = read_json(session / STRICT_SELECTION)
    except (OSError, ValueError, ProvisionalSpeakerError):
        return {"exists": False}
    return {key: payload.get(key) for key in (
        "schema", "state", "selected_profile", "selected_speaker_profile",
        "aggregate_transcript", "selected_dialogue", "selected_transcript",
        "rich_transcript", "coverage_report", "policy", "speaker_roster",
    )}


def review_compatible_projection(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in utterances:
        # The frozen v1/v2/v3 backends use remote-only evidence units and overlaps.
        if row.get("role") != "remote":
            continue
        projected = {key: row.get(key) for key in (
            "id", "role", "text", "start", "end", "source_track", "source_start",
            "source_end", "source_candidate_id",
        )}
        projected["quality"] = {key: value for key, value in (row.get("quality") or {}).items()
                                if key not in {"human_review", "agent_review", "review_evidence",
                                               "transcript_order_review"}}
        result.append(projected)
    return result


def review_evidence_compatible(snapshot: list[dict[str, Any]], current: list[dict[str, Any]]) -> bool:
    for rows in (snapshot, current):
        ids = [row.get("id") for row in rows]
        if not all(ids) or len(set(ids)) != len(ids):
            return False
    frozen = review_compatible_projection(snapshot)
    reviewed = review_compatible_projection(current)
    if len(frozen) != len(reviewed):
        return False
    for old, new in zip(frozen, reviewed):
        # Clearing a review flag may admit NEW enrollment, but reuse never does that:
        # preserve the old labels/unknowns with the old, narrower enrollment basis.
        if old["quality"].get("needs_review") is True and new["quality"].get("needs_review") is False:
            new["quality"]["needs_review"] = True
        if old != new:
            return False
    return bool(frozen)


def valid_artifact_bundle(directory: Path, schema: str, required: set[str]) -> bool:
    manifest = read_json(directory / "artifact_manifest.json")
    artifacts = manifest.get("artifacts") or {}
    if manifest.get("schema") != schema or not required.issubset(artifacts):
        return False
    return all(isinstance(name, str) and isinstance(digest, str)
               and within(directory / name, directory) and (directory / name).is_file()
               and sha256_file(directory / name) == digest
               for name, digest in artifacts.items())


def installed_model_current(report: dict[str, Any]) -> bool:
    spec = importlib.util.find_spec("resemblyzer")
    if spec is None or spec.origin is None:
        return False
    return same_identity((report.get("model") or {}).get("model"),
                         Path(spec.origin).with_name("pretrained.pt"))


def compatible_v1_evidence(session: Path, directory: Path, utterances: list[dict[str, Any]]) -> tuple | None:
    try:
        report = read_json(directory / "report.json")
        rich = read_json(directory / "transcript.rich.shadow.json")
        snapshot = rich.get("utterances") or []
        if (report.get("schema") != V1_REPORT_SCHEMA
                or rich.get("schema") != "murmurmark.transcript_rich_shadow/v1"
                or not snapshot or len({row.get("id") for row in snapshot}) != len(snapshot)
                or not review_evidence_compatible(snapshot, utterances)
                or not valid_artifact_bundle(directory, V1_MANIFEST_SCHEMA,
                                              {"report.json", "utterance_attribution.jsonl", "transcript.rich.shadow.json"})
                or (report.get("implementation") or {}).get("fingerprint", {}).get("sha256")
                != sha256_file(V1_IMPLEMENTATION)
                or not v1_audio_source_current(session, report)
                or not installed_model_current(report)
                or (report.get("model") or {}).get("consensus")):
            return None
        source = report["source"]
        if (not source_identity_matches(session, source.get("raw_remote_after"))
                or not source_identity_matches(session, source.get("remote_audio"))
                or (source.get("raw_remote_before") or {}).get("sha256")
                != (source.get("raw_remote_after") or {}).get("sha256")):
            return None
        return directory, report, read_jsonl(directory / "utterance_attribution.jsonl")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ProvisionalSpeakerError):
        return None


def best_compatible_v1(session: Path, utterances: list[dict[str, Any]]) -> tuple | None:
    directories = list((session / STRICT_EVIDENCE_ROOT).glob("*/remote-speaker-evidence-v1"))
    directories.extend((session / DEFAULT_OUT_DIR / "evidence").glob("*/remote-speaker-evidence-v1"))
    directories.append(session / CANONICAL_V1)
    candidates = [candidate for directory in sorted(set(directories))
                  if (candidate := compatible_v1_evidence(session, directory, utterances)) is not None]
    return max(candidates, key=lambda item: (item[1].get("decision") == "PUBLISH_AUDIT_EVIDENCE",
                                           str(item[0]))) if candidates else None


def compatible_v3_evidence(
    session: Path, directory: Path, utterances: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Reuse frozen word/turn labels as provisional, never promote a new profile."""
    try:
        report = read_json(directory / "report.json")
        rich = read_json(directory / "transcript.rich.shadow.json")
        if (report.get("schema") != "murmurmark.remote_speaker_coverage_report/v3"
                or report.get("decision") != "PUBLISH_EVIDENCE"
                or rich.get("schema") != "murmurmark.remote_speaker_rich_transcript/v3"
                or not report.get("gates") or not all(report["gates"].values())):
            return None
        snapshot = rich.get("utterances") or []
        if (not snapshot or len({row.get("id") for row in snapshot}) != len(snapshot)
                or not review_evidence_compatible(snapshot, utterances)):
            return None
        if not valid_artifact_bundle(directory, "murmurmark.remote_speaker_coverage_artifact_manifest/v3",
                                     {"report.json", "transcript.rich.shadow.json", "word_attribution.jsonl"}):
            return None
        source = report["source"]
        if rich.get("source") != source:
            return None
        if not {"report", "manifest", "frames", "words", "utterances", "speaker_map", "rich"}.issubset(
            source.get("v2_artifacts") or {}
        ):
            return None
        # Dialogue byte hashes may differ after keep-only review. Every other input
        # and the original, manifest-bound dialogue snapshot must still be intact.
        for key in ("remote_audio", "raw_remote_json", "v1_report", "v1_attribution"):
            if not source_identity_matches(session, source.get(key)):
                return None
        for row in (source.get("v2_artifacts") or {}).values():
            if not source_identity_matches(session, row):
                return None
        v2_path = resolve_session_path(session, source["v2_artifacts"]["report"]["path"])
        v1_path = resolve_session_path(session, source["v1_report"]["path"])
        if v2_path is None or v1_path is None:
            return None
        v2, v1 = read_json(v2_path), read_json(v1_path)
        if (v2.get("schema") != "murmurmark.remote_speaker_diarization_report/v2"
                or v2.get("decision") != "PUBLISH_EVIDENCE" or not v2.get("gates")
                or not all(v2["gates"].values()) or not verify_manifest(v1_path.parent)):
            return None
        if not valid_artifact_bundle(v2_path.parent, "murmurmark.remote_speaker_diarization_artifact_manifest/v2",
                                     {"report.json", "transcript.rich.shadow.json", "word_attribution.jsonl"}):
            return None
        for key in ("dialogue", "remote_audio", "raw_remote_json", "v1_report", "v1_attribution"):
            if v2["source"].get(key) != source.get(key):
                return None
        if v1["source"].get("dialogue") != source.get("dialogue"):
            return None
        if not v1_audio_source_current(session, v1):
            return None
        if not source_identity_matches(session, v1["source"].get("raw_remote_after")):
            return None
        if ((v1["source"].get("raw_remote_before") or {}).get("sha256")
                != (v1["source"].get("raw_remote_after") or {}).get("sha256")):
            return None
        if (v1.get("implementation", {}).get("fingerprint", {}).get("sha256")
                != sha256_file(V1_IMPLEMENTATION)):
            return None
        # Resolve the installed model without importing torch or initializing an encoder.
        if (not installed_model_current(v2) or not installed_model_current(v1)
                or (v1.get("model") or {}).get("consensus")):
            return None
        verifier_spec = importlib.util.spec_from_file_location(
            "speaker_coverage_reuse_verifier", ROOT / "scripts/audit-remote-speaker-coverage-v3.py")
        assert verifier_spec is not None and verifier_spec.loader is not None
        verifier = importlib.util.module_from_spec(verifier_spec)
        verifier_spec.loader.exec_module(verifier)
        if not verifier.verify_v2_promotion(v2) or not verifier.verify_v3_promotion(report):
            return None
        for row in snapshot:
            if row.get("role") == "remote" and "".join(
                str(turn.get("text") or "") for turn in row.get("speaker_turns") or []
            ) != row.get("text"):
                return None
        return report, rich
    except (OSError, ValueError, KeyError, TypeError, AttributeError, ProvisionalSpeakerError):
        return None


def best_compatible_v3(session: Path, utterances: list[dict[str, Any]]) -> tuple[Path, dict, dict] | None:
    directories = list((session / STRICT_EVIDENCE_ROOT).glob("*/remote-speaker-coverage-v3"))
    directories.append(session / "derived/audit/remote-speaker-coverage-v3")
    for directory in sorted(set(directories), reverse=True):
        candidate = compatible_v3_evidence(session, directory, utterances)
        if candidate is not None:
            return directory, *candidate
    return None


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
    display_rows: list[dict[str, Any]] | None = None,
) -> str:
    if state == "provisional":
        warning = (
            "**Speaker attribution is provisional.** Anonymous `remote_speaker_NN` labels are "
            "best-effort acoustic clusters and may merge several people into one label or split one person across labels."
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
        "> The selected batch transcript is the source, not a human-verified truth. Local review warnings remain unresolved.",
        "> Coverage measures assigned labels, not speaker accuracy. Approximate parent intervals are marked explicitly.",
        "",
        f"Transcript profile: `{profile}`  ",
        f"Speaker attribution state: `{state}`  ",
        "Speaker identities: session-local and anonymous",
        "",
    ]
    lines.extend(publication.render_body(display_rows if display_rows is not None else publication.display_turns(utterances, attributions)))
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
            "strict_selection_basis",
            "evidence_reuse",
            "implementation",
            "publication_implementation",
            "micro_evidence_implementation",
            "acoustic_timing_implementation",
            "acoustic_timing_evidence",
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
    if not same_identity(payload.get("publication_implementation"), Path(publication.__file__).resolve()):
        reasons.append("selection_publication_implementation_stale")
    if not same_identity(payload.get("micro_evidence_implementation"), Path(micro_asr_evidence.__file__).resolve()):
        reasons.append("selection_micro_evidence_implementation_stale")
    if not same_identity(payload.get("acoustic_timing_implementation"), Path(acoustic_timing_evidence.__file__).resolve()):
        reasons.append("selection_acoustic_timing_implementation_stale")
    timing = payload.get("acoustic_timing_evidence") or {}
    audio = session / "audio/remote/000001.caf"
    if timing.get("audio") != acoustic_timing_evidence.fingerprint(audio, session):
        reasons.append("selection_timing_audio_stale")
    if timing.get("source_files") != [str(p.relative_to(session)) for p in sorted((session / "audio/remote").glob("*.caf"))]:
        reasons.append("selection_timing_layout_stale")
    source = payload.get("source_evidence")
    if isinstance(source, dict) and source.get("exists") is True:
        source_path = resolve_session_path(session, source.get("path"))
        if source_path is None or not same_identity(source, source_path):
            reasons.append("selection_source_evidence_stale")
    if payload.get("strict_selection_basis") != strict_selection_basis(session):
        reasons.append("selection_strict_selection_stale")
    if isinstance(source, dict) and source.get("exists") is True and not reasons:
        assert source_path is not None
        if payload.get("evidence_reuse"):
            utterances = read_json(dialogue).get("utterances") or []
            verifier = (compatible_v1_evidence if payload["evidence_reuse"].get("kind") == "v1"
                        else compatible_v3_evidence)
            if verifier(session, source_path.parent, utterances) is None:
                reasons.append("selection_reused_evidence_stale")
        else:
            source_report = read_json(source_path)
            if not verify_manifest(source_path.parent) or not v1_source_current(session, source_report, dialogue):
                reasons.append("selection_acoustic_inputs_stale")
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

    reused_v3 = best_compatible_v3(session, utterances)
    candidate = current_v1_candidate(session, profile, dialogue) if reused_v3 is None else None
    reused_v1 = candidate is None and reused_v3 is None
    if reused_v1:
        candidate = best_compatible_v1(session, utterances)
    if (
        candidate is not None
        and candidate[1].get("decision") != "PUBLISH_AUDIT_EVIDENCE"
        and can_refresh_relaxed_v1(session, candidate[1])
        and not getattr(args, "cached_only", False)
        and not reused_v1
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
    evidence_reuse = None
    if reused_v1 and candidate is not None:
        evidence_reuse = {
            "schema": "murmurmark.speaker_evidence_reuse/v1", "kind": "v1",
            "source_profile": candidate[1]["source"]["profile"],
            "source_dialogue": candidate[1]["source"]["dialogue"],
            "projection_sha256": sha256_bytes(compact_json_bytes(review_compatible_projection(utterances))),
            "strict_publication_promoted": False,
            "eligibility_basis": "frozen_nonexpanding",
        }
        warnings.append("review_compatible_v1_evidence_reused_without_inference")
    reused_turns = {}
    if reused_v3 is not None:
        directory, report, rich = reused_v3
        source_evidence = identity(directory / "report.json", session)
        reused_turns = {row["id"]: row["speaker_turns"] for row in rich["utterances"]
                        if row.get("role") == "remote"}
        speaker_ids = sorted({turn["speaker_id"] for turns in reused_turns.values()
                              for turn in turns if turn.get("speaker_id")})
        speaker_rows = [{"speaker_id": value, "tier": "compatible_v3_evidence"} for value in speaker_ids]
        state = "provisional" if speaker_ids else "unavailable"
        speaker_profile = "remote_speaker_provisional_v1" if speaker_ids else "remote_speaker_attribution_unavailable_v1"
        coverage = report["summary"]
        summary.update({
            "attributed_remote_utterances": sum(any(turn.get("speaker_id") for turn in turns)
                                                for turns in reused_turns.values()),
            "remote_speech_sec": coverage["remote_speech_sec"],
            "attributed_remote_speech_sec": coverage["attributed_speech_sec"],
            "attributed_remote_speech_ratio": coverage["attributable_remote_speech_ratio"],
            "attributed_remote_words": coverage["attributed_words"],
            "speaker_clusters": len(speaker_ids),
            "coverage_basis": "frozen_v3_word_weights",
        })
        warnings = [fallback_reason, "review_compatible_v3_evidence_reused_without_inference"]
        evidence_reuse = {
            "schema": "murmurmark.speaker_evidence_reuse/v1",
            "kind": "v3",
            "source_profile": report["source"]["profile"],
            "source_dialogue": report["source"]["dialogue"],
            "projection_sha256": sha256_bytes(compact_json_bytes(review_compatible_projection(utterances))),
            "strict_publication_promoted": False,
            "eligibility_basis": "frozen_nonexpanding",
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
            if utterance_id in reused_turns:
                output["speaker_turns"] = reused_turns[utterance_id]
                ids = {turn["speaker_id"] for turn in output["speaker_turns"] if turn.get("speaker_id")}
                output["speaker_id"] = next(iter(ids)) if len(ids) == 1 else None
                output["speaker_label"] = output["speaker_id"] or "remote_speaker_unknown"
                row = {"speaker_id": output["speaker_id"], "speaker_label": output["speaker_label"],
                       "tier": "compatible_v3_evidence", "speaker_turns": output["speaker_turns"]}
                output["speaker_attribution"] = row
                normalized_attributions[utterance_id] = row
        output_utterances.append(output)

    timing_evidence = acoustic_timing_evidence.inspect(session, output_utterances)
    display_rows = publication.display_turns(output_utterances, normalized_attributions, timing_evidence)
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
        "evidence_reuse": evidence_reuse,
        "remote_utterance_attributions": normalized_attributions,
        "utterances": output_utterances,
        "publication_version": publication.VERSION,
        "publication_implementation": identity(Path(publication.__file__).resolve()),
        "micro_evidence_implementation": identity(Path(micro_asr_evidence.__file__).resolve()),
        "acoustic_timing_implementation": identity(Path(acoustic_timing_evidence.__file__).resolve()),
        "acoustic_timing_evidence": timing_evidence,
        "display_turns": display_rows,
        "safety": {
            "aggregate_transcript_unchanged": True,
            "selected_dialogue_unchanged": True,
            "text_roles_timestamps_unchanged": True,
            "display_timing_is_separate_from_source": True,
            "session_local_anonymous_only": True,
            "human_identity_inference": False,
            "strict_verified_profile_unchanged": True,
        },
    }
    # Readers follow selection.json only after both immutable generation files exist.
    generation = sha256_bytes(canonical_json_bytes(transcript_payload))
    generation_dir = out_dir / "generations" / generation
    rich_path = generation_dir / "transcript.provisional.json"
    markdown_path = generation_dir / "transcript.provisional.md"
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
            display_rows,
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
        "evidence_reuse": evidence_reuse,
        "strict_selection": identity(session / STRICT_SELECTION, session),
        "strict_selection_basis": strict_selection_basis(session),
        "implementation": identity(Path(__file__).resolve()),
        "publication_implementation": identity(Path(publication.__file__).resolve()),
        "micro_evidence_implementation": identity(Path(micro_asr_evidence.__file__).resolve()),
        "acoustic_timing_implementation": identity(Path(acoustic_timing_evidence.__file__).resolve()),
        "acoustic_timing_evidence": timing_evidence,
        "batch_authoritative": True,
        "aggregate_fallback_available": True,
        "identity_scope": "session_local_anonymous",
    }
    selection["semantic_fingerprint"] = sha256_bytes(compact_json_bytes(semantic_basis(selection)))
    atomic_write(out_dir / "selection.json", canonical_json_bytes(selection))
    # Compatibility copies are not used as the publication commit point.
    atomic_write(out_dir / "transcript.provisional.json", rich_path.read_bytes())
    atomic_write(out_dir / "transcript.provisional.md", markdown_path.read_bytes())
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
