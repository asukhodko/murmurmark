#!/usr/bin/env python3
"""Build the fingerprint-bound product terminal-gate instrument."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.speaker_resolved_terminal_gate_policy/v1"
INPUT_SCHEMA = "murmurmark.speaker_resolved_terminal_gate_input/v1"
REPORT_SCHEMA = "murmurmark.speaker_resolved_terminal_gate_report/v1"
SNAPSHOT_SCHEMA = "murmurmark.speaker_resolved_terminal_gate_snapshot/v1"
DEFAULT_POLICY = ROOT / "policies/speaker-resolved-terminal-gate-instrument-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/speaker-resolved-terminal-gate-instrument-v1"
DEFAULT_SNAPSHOT = ROOT / "docs/testing/speaker-resolved-terminal-gate-instrument-v1-snapshot.json"
REQUIRED_SOURCE_IDS = {
    "post_segmentation_rebaseline",
    "capture_continuity_closure",
    "speaker_preserving_echo",
    "residual_local_recall",
    "human_lexical_seed",
    "remote_direct_truth",
    "remote_unknown_recovery",
    "chronology_arbitration",
    "chronology_localization",
    "speaker_resolved_publication",
}
REQUIRED_DIMENSIONS = {
    "durable_capture",
    "target_me_preservation",
    "lexical_accuracy",
    "chronology_and_conservation",
    "remote_speaker_attribution",
    "explicit_unknown",
    "review_burden",
    "speaker_resolved_publication",
}
REQUIRED_THRESHOLDS = {
    "maximum_capture_gap_seconds",
    "maximum_remaining_local_recall_seconds",
    "maximum_wer",
    "maximum_cer",
    "minimum_domain_term_accuracy",
    "maximum_chronology_review_seconds",
    "maximum_unknown_word_ratio",
    "maximum_unknown_seconds_ratio",
    "maximum_review_burden_ratio",
    "minimum_fresh_sessions",
}
DIMENSION_SOURCES = {
    "durable_capture": {"post_segmentation_rebaseline", "capture_continuity_closure"},
    "target_me_preservation": {"speaker_preserving_echo", "residual_local_recall"},
    "lexical_accuracy": {"human_lexical_seed"},
    "chronology_and_conservation": {
        "post_segmentation_rebaseline",
        "chronology_arbitration",
        "chronology_localization",
    },
    "remote_speaker_attribution": {"post_segmentation_rebaseline", "remote_direct_truth"},
    "explicit_unknown": {"post_segmentation_rebaseline", "remote_unknown_recovery"},
    "review_burden": {"post_segmentation_rebaseline"},
    "speaker_resolved_publication": {
        "post_segmentation_rebaseline",
        "speaker_resolved_publication",
    },
}
SOURCE_REMEDIATION_COMMANDS = {
    "remote_unknown_recovery": (
        "HF_HUB_OFFLINE=1 .venv/bin/python "
        "scripts/report-remote-unknown-evidence-recovery-v1-corpus.py all --refresh"
    ),
    "chronology_arbitration": (
        "murmurmark corpus chronology-arbitration-v1 all --refresh"
    ),
    "chronology_localization": (
        "murmurmark corpus chronology-localization-v1 all --refresh"
    ),
}


class TerminalGateError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze and evaluate separate product-level transcript quality gates."
    )
    parser.add_argument(
        "action",
        choices=("preflight", "freeze", "evaluate", "status", "replay", "all"),
        nargs="?",
        default="all",
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--refresh", action="store_true", help="Replace the frozen input manifest.")
    parser.add_argument("--write-snapshot", action="store_true")
    return parser.parse_args()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    except Exception:
        Path(raw).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TerminalGateError(f"cannot_read_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise TerminalGateError(f"expected_json_object:{path}")
    return value


def resolve_configured_path(value: Any, policy_path: Path) -> Path:
    path = Path(str(value))
    if path.is_absolute():
        return path.resolve()
    repo_candidate = (ROOT / path).resolve()
    if repo_candidate.exists() or policy_path.resolve().is_relative_to(ROOT.resolve()):
        return repo_candidate
    return (policy_path.parent / path).resolve()


def identity(path: Path, display: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"path": display or str(path), "exists": path.is_file()}
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return row


def identity_current(row: Any, path: Path) -> bool:
    if not isinstance(row, dict) or bool(row.get("exists")) != path.is_file():
        return False
    if not path.is_file():
        return True
    expected_bytes = row.get("bytes")
    return bool(
        expected_bytes is not None
        and int(expected_bytes) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def resolve_artifact_path(row: Any) -> Path | None:
    if not isinstance(row, dict) or not row.get("path"):
        return None
    path = Path(str(row["path"])).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def source_provenance_issues(
    source_id: str, report_path: Path, payload: dict[str, Any]
) -> list[str]:
    """Validate source-specific transitive fingerprints without publishing private paths."""
    if source_id not in {
        "remote_unknown_recovery", "chronology_arbitration", "chronology_localization",
    }:
        return []
    inputs = payload.get("inputs") or {}
    manifest_name = inputs.get("manifest")
    expected_manifest_sha = inputs.get("manifest_sha256")
    if not isinstance(manifest_name, str) or not expected_manifest_sha:
        return ["provenance_manifest_not_declared"]
    manifest_path = (report_path.parent / manifest_name).resolve()
    if not manifest_path.is_file():
        return ["provenance_manifest_missing"]
    if sha256_file(manifest_path) != str(expected_manifest_sha):
        return ["provenance_manifest_sha256_mismatch"]
    try:
        manifest = read_json(manifest_path)
    except TerminalGateError:
        return ["provenance_manifest_invalid"]
    issues: list[str] = []
    if source_id == "chronology_localization":
        decode_name = inputs.get("word_decodes")
        expected_decode_sha = inputs.get("word_decodes_sha256")
        if not isinstance(decode_name, str) or not expected_decode_sha:
            issues.append("chronology_localization_word_decodes_not_declared")
        else:
            decode_path = (report_path.parent / decode_name).resolve()
            if not decode_path.is_file() or sha256_file(decode_path) != str(expected_decode_sha):
                issues.append("chronology_localization_word_decodes_stale")
        for name in ("policy", "implementation", "frozen_items"):
            row = manifest.get(name)
            path = resolve_artifact_path(row)
            if path is None or not identity_current(row, path):
                issues.append(f"chronology_localization_{name}_stale")
        for name, row in (manifest.get("upstream") or {}).items():
            path = resolve_artifact_path(row)
            if path is None or not identity_current(row, path):
                issues.append(f"chronology_localization_upstream_{name}_stale")
        model = manifest.get("model") or {}
        model_identity = model.get("identity")
        model_identity_path = resolve_artifact_path(model_identity)
        if model_identity_path is None or not identity_current(model_identity, model_identity_path):
            issues.append("chronology_localization_model_identity_stale")
        else:
            try:
                model_payload = read_json(model_identity_path)
            except TerminalGateError:
                issues.append("chronology_localization_model_identity_invalid")
            else:
                model_path = Path(str(model.get("path") or "")).expanduser().resolve()
                signature = [
                    {
                        "path": str(path.relative_to(model_path)),
                        "bytes": path.stat().st_size,
                        "mtime_ns": path.stat().st_mtime_ns,
                    }
                    for path in sorted(
                        path for path in model_path.rglob("*")
                        if path.is_file() and ".cache" not in path.relative_to(model_path).parts
                    )
                ] if model_path.is_dir() else None
                if signature != model_payload.get("signature"):
                    issues.append("chronology_localization_model_files_stale")
        for item in manifest.get("clip_identities") or []:
            alias = str(item.get("alias") or "unknown")
            item_id = str(item.get("item_id") or "unknown")
            for source, row in (item.get("clips") or {}).items():
                path = resolve_artifact_path(row)
                if path is None or not identity_current(row, path):
                    issues.append(
                        f"chronology_localization_{alias}_{item_id}_{source}_stale"
                    )
        return issues
    upstream = manifest.get("rebaseline_manifest")
    upstream_path = resolve_artifact_path(upstream)
    if upstream_path is None or not identity_current(upstream, upstream_path):
        return ["upstream_rebaseline_manifest_stale"]
    if source_id == "remote_unknown_recovery":
        return []
    for name in ("policy", "implementation"):
        row = manifest.get(name)
        path = resolve_artifact_path(row)
        if path is None or not identity_current(row, path):
            issues.append(f"chronology_{name}_stale")
    for session in manifest.get("sessions") or []:
        alias = str(session.get("alias") or "unknown")
        for name, row in (session.get("artifacts") or {}).items():
            values = row if isinstance(row, list) else [row]
            if any(
                (path := resolve_artifact_path(value)) is None
                or not identity_current(value, path)
                for value in values
            ):
                issues.append(f"chronology_{alias}_{name}_stale")
    return issues


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


def nested(source: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = source
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise TerminalGateError(f"unsupported_policy_schema:{policy.get('schema')}")
    sources = policy.get("sources") or []
    ids = [str(row.get("id") or "") for row in sources if isinstance(row, dict)]
    if set(ids) != REQUIRED_SOURCE_IDS or len(ids) != len(REQUIRED_SOURCE_IDS):
        raise TerminalGateError("source_ids_invalid")
    if any(not isinstance(row.get("path"), str) or not isinstance(row.get("schema"), str) for row in sources):
        raise TerminalGateError("source_contract_invalid")
    dimensions = [str(value) for value in policy.get("dimensions") or []]
    if set(dimensions) != REQUIRED_DIMENSIONS or len(dimensions) != len(REQUIRED_DIMENSIONS):
        raise TerminalGateError("eight_unique_dimensions_required")
    thresholds = policy.get("thresholds") or {}
    if not isinstance(thresholds, dict) or not REQUIRED_THRESHOLDS.issubset(thresholds):
        raise TerminalGateError("terminal_thresholds_incomplete")
    decisions = policy.get("required_decisions") or {}
    if not isinstance(decisions, dict) or not {
        "post_segmentation_rebaseline", "speaker_preserving_echo", "residual_local_recall",
        "human_lexical_seed", "remote_direct_truth", "speaker_resolved_publication",
    }.issubset(decisions):
        raise TerminalGateError("required_decisions_incomplete")
    safety = policy.get("safety") or {}
    if safety.get("read_only_sources") is not True:
        raise TerminalGateError("sources_must_be_read_only")
    if safety.get("aggregate_quality_score") is not False:
        raise TerminalGateError("aggregate_quality_score_must_be_disabled")
    privacy = policy.get("privacy") or {}
    if any(privacy.get(key) is not False for key in (
        "public_session_ids", "public_absolute_paths", "public_speech_text", "public_speaker_names"
    )):
        raise TerminalGateError("public_output_must_be_privacy_safe")


def source_rows(policy: dict[str, Any], policy_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for configured in policy["sources"]:
        path = resolve_configured_path(configured["path"], policy_path)
        row = {
            "id": configured["id"],
            "configured_path": str(configured["path"]),
            "expected_schema": configured["schema"],
            **identity(path),
        }
        if path.is_file():
            try:
                payload = read_json(path)
                row["actual_schema"] = payload.get("schema")
                row["decision"] = semantic_decision(str(configured["id"]), payload)
            except TerminalGateError:
                row["actual_schema"] = None
                row["decision"] = None
        row["valid"] = bool(row.get("exists") and row.get("actual_schema") == row["expected_schema"])
        rows.append(row)
    return rows


def semantic_decision(source_id: str, payload: dict[str, Any]) -> Any:
    if source_id == "speaker_preserving_echo":
        return nested(payload, "promotion", "decision")
    return payload.get("decision")


def preflight(policy_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    policy = read_json(policy_path)
    validate_policy(policy)
    rows = source_rows(policy, policy_path)
    return policy, rows


def manifest_path(out_dir: Path) -> Path:
    return out_dir / "private/input_manifest.json"


def freeze(policy_path: Path, out_dir: Path) -> dict[str, Any]:
    policy, rows = preflight(policy_path)
    if not all(row["valid"] for row in rows):
        invalid = ",".join(row["id"] for row in rows if not row["valid"])
        raise TerminalGateError(f"source_preflight_failed:{invalid}")
    manifest = {
        "schema": INPUT_SCHEMA,
        "version": 1,
        "policy": identity(policy_path.resolve()),
        "implementation": identity(Path(__file__).resolve()),
        "sources": rows,
        "safety": policy["safety"],
    }
    atomic_write(manifest_path(out_dir), canonical_json(manifest))
    return manifest


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = manifest_path(out_dir)
    if not path.is_file():
        raise TerminalGateError("frozen_input_manifest_missing; run freeze")
    manifest = read_json(path)
    if manifest.get("schema") != INPUT_SCHEMA:
        raise TerminalGateError(f"unsupported_input_schema:{manifest.get('schema')}")
    return manifest


def verify_manifest(
    manifest: dict[str, Any], policy_path: Path, policy: dict[str, Any]
) -> tuple[bool, bool, list[dict[str, Any]], dict[str, dict[str, Any]]]:
    controls_current = bool(
        identity_current(manifest.get("policy"), policy_path.resolve())
        and identity_current(manifest.get("implementation"), Path(__file__).resolve())
    )
    configured = {str(row["id"]): row for row in policy["sources"]}
    frozen_ids = {
        str(row.get("id") or "")
        for row in manifest.get("sources") or []
        if isinstance(row, dict)
    }
    controls_current = controls_current and frozen_ids == set(configured)
    public_sources: list[dict[str, Any]] = []
    payloads: dict[str, dict[str, Any]] = {}
    sources_current = True
    for frozen in manifest.get("sources") or []:
        source_id = str(frozen.get("id") or "")
        config = configured.get(source_id)
        if not config:
            current = False
            path = Path("/missing")
        else:
            path = resolve_configured_path(config["path"], policy_path)
            current = identity_current(frozen, path)
        actual_schema = None
        decision = None
        if current and path.is_file():
            try:
                payload = read_json(path)
                actual_schema = payload.get("schema")
                decision = semantic_decision(source_id, payload)
                current = actual_schema == frozen.get("expected_schema")
                issues = source_provenance_issues(source_id, path, payload) if current else []
                current = current and not issues
                if current:
                    payloads[source_id] = payload
            except TerminalGateError:
                current = False
                issues = ["source_payload_invalid"]
        else:
            issues = ["source_fingerprint_stale"] if not current else []
        sources_current = sources_current and current
        public_sources.append({
            "id": source_id,
            "schema": actual_schema or frozen.get("actual_schema"),
            "decision": decision if current else frozen.get("decision"),
            "sha256": frozen.get("sha256"),
            "current": current,
            "issues": issues,
        })
    return controls_current and sources_current, controls_current, public_sources, payloads


def dimension(
    identifier: str,
    state: str,
    metrics: dict[str, Any],
    evidence: list[str],
    blockers: list[str],
    next_command: str | None = None,
) -> dict[str, Any]:
    return {
        "id": identifier,
        "state": state,
        "metrics": metrics,
        "evidence": evidence,
        "blockers": blockers,
        "next_command": next_command,
    }


def build_dimensions(
    policy: dict[str, Any], payloads: dict[str, dict[str, Any]],
    sources: list[dict[str, Any]], controls_current: bool,
) -> list[dict[str, Any]]:
    thresholds = policy["thresholds"]
    rebaseline = payloads.get("post_segmentation_rebaseline", {})
    capture = payloads.get("capture_continuity_closure", {})
    echo = payloads.get("speaker_preserving_echo", {})
    local = payloads.get("residual_local_recall", {})
    lexical = payloads.get("human_lexical_seed", {})
    direct = payloads.get("remote_direct_truth", {})
    unknown = payloads.get("remote_unknown_recovery", {})
    chronology = payloads.get("chronology_arbitration", {})
    chronology_localization = payloads.get("chronology_localization", {})
    publication = payloads.get("speaker_resolved_publication", {})

    baseline_ready = (
        rebaseline.get("decision")
        == policy["required_decisions"]["post_segmentation_rebaseline"]
    )
    current_gap = number(nested(rebaseline, "dimensions", "capture_completeness", "gap_seconds"))
    no_restart_ok = nested(capture, "no_restart_soak", "capture_complete") is True
    controlled_complete = nested(capture, "controlled_restart", "capture_complete") is True
    capture_ok = bool(
        baseline_ready
        and no_restart_ok
        and controlled_complete
        and current_gap <= number(thresholds["maximum_capture_gap_seconds"])
    )
    capture_blockers = [] if capture_ok else [
        "controlled restart or current corpus still contains measured source gaps"
    ]

    local_remaining = number(nested(local, "summary", "remaining_seconds"))
    echo_exact = nested(echo, "checks", "candidate_local_exact") is True
    echo_fallback = nested(echo, "checks", "fallbacks_exact") is True
    target_ok = bool(
        semantic_decision("speaker_preserving_echo", echo)
        == policy["required_decisions"]["speaker_preserving_echo"]
        and local.get("decision") == policy["required_decisions"]["residual_local_recall"]
        and echo_exact
        and echo_fallback
        and local_remaining <= number(thresholds["maximum_remaining_local_recall_seconds"])
    )
    target_blockers = [] if target_ok else [
        "local speech preservation is promoted, but a bounded local-recall residual remains"
    ]

    lexical_summary = lexical.get("summary") or {}
    lexical_decision = lexical.get("decision")
    lexical_wer = nested(lexical, "metrics", "overall", "wer")
    lexical_cer = nested(lexical, "metrics", "overall", "cer")
    domain_accuracy = nested(lexical, "metrics", "overall", "domain_terms", "accuracy")
    lexical_ok = bool(
        lexical_decision == policy["required_decisions"]["human_lexical_seed"]
        and lexical_wer is not None
        and lexical_cer is not None
        and domain_accuracy is not None
        and number(lexical_wer) <= number(thresholds["maximum_wer"])
        and number(lexical_cer) <= number(thresholds["maximum_cer"])
        and number(domain_accuracy) >= number(thresholds["minimum_domain_term_accuracy"])
    )
    lexical_metrics = {
        "decision": lexical_decision,
        "answered_slots": integer(lexical_summary.get("answered_slots")),
        "remaining_slots": integer(lexical_summary.get("remaining_slots")),
        "reference_words": integer(lexical_summary.get("reference_words")),
        "wer": lexical_wer,
        "maximum_wer": thresholds["maximum_wer"],
        "cer": lexical_cer,
        "maximum_cer": thresholds["maximum_cer"],
        "domain_term_accuracy": domain_accuracy,
        "minimum_domain_term_accuracy": thresholds["minimum_domain_term_accuracy"],
    }

    conservation = rebaseline.get("gates") or {}
    initial_chronology_seconds = number(
        nested(rebaseline, "dimensions", "overlap_and_chronology", "chronology_seconds")
    )
    chronology_seconds = number(
        nested(chronology_localization, "chronology", "final_remaining_seconds")
    )
    chronology_evidence_ready = bool(
        chronology.get("decision") in {
            "PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1",
            "EVIDENCE_BOUND",
        }
        and chronology_localization.get("decision") in {
            "PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1",
            "EVIDENCE_BOUND",
        }
    )
    chronology_ok = bool(
        baseline_ready
        and chronology_evidence_ready
        and conservation.get("word_order_role_conserved") is True
        and chronology_seconds <= number(thresholds["maximum_chronology_review_seconds"])
    )

    direct_gates = direct.get("gates") or {}
    direct_ok = bool(
        direct.get("decision") == policy["required_decisions"]["remote_direct_truth"]
        and direct_gates
        and all(value is True for value in direct_gates.values())
    )
    published_speakers = integer(
        nested(rebaseline, "dimensions", "remote_speaker_topology", "published_speakers")
    )
    topology_status = nested(rebaseline, "dimensions", "remote_speaker_topology", "status")
    speaker_ok = bool(
        baseline_ready and direct_ok and topology_status == "measured_with_direct_truth"
    )

    summary = rebaseline.get("summary") or {}
    unknown_word_ratio = number(summary.get("unknown_remote_words_ratio"))
    unknown_seconds_ratio = number(summary.get("unknown_remote_seconds_ratio"))
    unknown_ok = bool(
        baseline_ready
        and unknown_word_ratio <= number(thresholds["maximum_unknown_word_ratio"])
        and unknown_seconds_ratio <= number(thresholds["maximum_unknown_seconds_ratio"])
    )
    recovery_summary = nested(unknown, "summary", "frozen", default={}) or {}

    capture_seconds = number(summary.get("capture_seconds"))
    review_seconds = number(nested(rebaseline, "dimensions", "review_burden", "remaining_seconds"))
    review_ratio = review_seconds / capture_seconds if capture_seconds else 1.0
    review_ok = bool(
        baseline_ready and review_ratio <= number(thresholds["maximum_review_burden_ratio"])
    )

    publication_gates = publication.get("gates") or {}
    fresh_sessions = integer(summary.get("included_sessions") or summary.get("frozen_sessions"))
    publication_ok = bool(
        baseline_ready
        and publication.get("decision") == policy["required_decisions"]["speaker_resolved_publication"]
        and conservation.get("read_surfaces_coherent") is True
        and conservation.get("word_order_role_conserved") is True
        and publication_gates
        and all(value is True for value in publication_gates.values())
        and fresh_sessions >= integer(thresholds["minimum_fresh_sessions"])
    )

    measured = [
        dimension(
            "durable_capture", "pass" if capture_ok else "bounded",
            {
                "no_restart_soak_complete": no_restart_ok,
                "controlled_restart_complete": controlled_complete,
                "current_corpus_gap_seconds": round(current_gap, 6),
                "maximum_gap_seconds": thresholds["maximum_capture_gap_seconds"],
            },
            ["capture_continuity_closure", "post_segmentation_rebaseline"], capture_blockers,
        ),
        dimension(
            "target_me_preservation", "pass" if target_ok else "bounded",
            {
                "candidate_local_exact": echo_exact,
                "fallbacks_exact": echo_fallback,
                "remaining_local_recall_items": integer(nested(local, "summary", "remaining_items")),
                "remaining_local_recall_seconds": round(local_remaining, 6),
            },
            ["speaker_preserving_echo", "residual_local_recall"], target_blockers,
        ),
        dimension(
            "lexical_accuracy", "pass" if lexical_ok else "blocked", lexical_metrics,
            ["human_lexical_seed"], [] if lexical_ok else [
                "direct human lexical truth is incomplete; WER/CER cannot be claimed"
            ],
            "murmurmark corpus lexical-seed-v1 review" if not lexical_ok else None,
        ),
        dimension(
            "chronology_and_conservation", "pass" if chronology_ok else "bounded",
            {
                "word_order_role_conserved": conservation.get("word_order_role_conserved") is True,
                "initial_chronology_review_seconds": round(initial_chronology_seconds, 6),
                "closed_chronology_review_seconds": round(
                    initial_chronology_seconds - chronology_seconds, 6
                ),
                "speaker_bounded_closed_seconds": round(
                    number(nested(chronology, "summary", "closed_seconds")), 6
                ),
                "word_level_closed_seconds": round(
                    number(nested(chronology_localization, "summary", "closed_seconds")), 6
                ),
                "chronology_review_seconds": round(chronology_seconds, 6),
                "maximum_chronology_review_seconds": thresholds["maximum_chronology_review_seconds"],
            },
            [
                "post_segmentation_rebaseline", "chronology_arbitration",
                "chronology_localization",
            ], [] if chronology_ok else [
                "word and role conservation pass, but chronology review remains"
            ],
        ),
        dimension(
            "remote_speaker_attribution", "pass" if speaker_ok else "bounded",
            {
                "direct_truth_decision": direct.get("decision"),
                "direct_truth_repeat_consistency": direct_gates.get("repeat_consistency") is True,
                "published_speakers": published_speakers,
                "current_topology_evidence": topology_status,
            },
            ["remote_direct_truth", "post_segmentation_rebaseline"], [] if speaker_ok else [
                "speaker publication works, but current-corpus speaker-count correctness lacks direct truth"
            ],
        ),
        dimension(
            "explicit_unknown", "pass" if unknown_ok else "bounded",
            {
                "unknown_words": integer(summary.get("unknown_remote_words_coverage_v3")),
                "unknown_word_ratio": round(unknown_word_ratio, 6),
                "maximum_unknown_word_ratio": thresholds["maximum_unknown_word_ratio"],
                "unknown_seconds": round(number(summary.get("unknown_remote_seconds_coverage_v3")), 6),
                "unknown_seconds_ratio": round(unknown_seconds_ratio, 6),
                "maximum_unknown_seconds_ratio": thresholds["maximum_unknown_seconds_ratio"],
                "recovery_frozen_remaining_words": integer(
                    recovery_summary.get("remaining_unknown_words")
                ),
            },
            ["post_segmentation_rebaseline", "remote_unknown_recovery"], [] if unknown_ok else [
                "explicit unknown speech exceeds the product-level seconds bound"
            ],
        ),
        dimension(
            "review_burden", "pass" if review_ok else "bounded",
            {
                "remaining_rows": integer(nested(rebaseline, "dimensions", "review_burden", "remaining_rows")),
                "remaining_seconds": round(review_seconds, 6),
                "capture_seconds": round(capture_seconds, 6),
                "ratio": round(review_ratio, 6),
                "maximum_ratio": thresholds["maximum_review_burden_ratio"],
            },
            ["post_segmentation_rebaseline"], [] if review_ok else [
                "mandatory review burden exceeds the corpus gate bound"
            ],
        ),
        dimension(
            "speaker_resolved_publication", "pass" if publication_ok else "blocked",
            {
                "publication_decision": publication.get("decision"),
                "fresh_sessions": fresh_sessions,
                "minimum_fresh_sessions": thresholds["minimum_fresh_sessions"],
                "strict_rich_sessions": integer(summary.get("strict_rich_sessions")),
                "provisional_sessions": integer(summary.get("provisional_sessions")),
                "aggregate_only_sessions": integer(summary.get("aggregate_only_sessions")),
                "read_surfaces_coherent": conservation.get("read_surfaces_coherent") is True,
                "exact_fallback": publication_gates.get("all_session_gates") is True,
            },
            ["speaker_resolved_publication", "post_segmentation_rebaseline"], [] if publication_ok else [
                "speaker-resolved publication or exact aggregate fallback is not qualified"
            ],
        ),
    ]
    source_by_id = {str(row["id"]): row for row in sources}
    result: list[dict[str, Any]] = []
    for item in measured:
        required = DIMENSION_SOURCES[item["id"]]
        stale = sorted(
            source_id
            for source_id in required
            if not bool((source_by_id.get(source_id) or {}).get("current"))
        )
        if controls_current and not stale:
            result.append(item)
            continue
        blockers = ["terminal_gate_controls_stale"] if not controls_current else []
        for source_id in stale:
            source = source_by_id.get(source_id) or {}
            issues = source.get("issues") or ["source_missing_or_stale"]
            blockers.extend(f"source_stale:{source_id}:{issue}" for issue in issues)
        next_command = next(
            (
                SOURCE_REMEDIATION_COMMANDS[source_id]
                for source_id in stale
                if source_id in SOURCE_REMEDIATION_COMMANDS
            ),
            item.get("next_command"),
        )
        result.append(
            dimension(
                item["id"],
                "not_measured",
                {},
                sorted(required),
                blockers,
                next_command,
            )
        )
    return result


def blocker_rows(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in dimensions:
        for reason in item["blockers"]:
            row = {
                "dimension": item["id"],
                "state": item["state"],
                "reason": reason,
                "next_command": item.get("next_command"),
            }
            rows.append(row)
    return rows


def build_report(
    policy: dict[str, Any], manifest: dict[str, Any], inputs_current: bool,
    controls_current: bool, sources: list[dict[str, Any]], payloads: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    dimensions = build_dimensions(policy, payloads, sources, controls_current)
    all_explicit = len(dimensions) == len(policy["dimensions"]) and all(
        row["state"] in {"pass", "bounded", "blocked", "not_measured"} for row in dimensions
    )
    instrument_ready = bool(inputs_current and all_explicit and all(
        row["state"] != "not_measured" for row in dimensions
    ))
    product_ready = bool(instrument_ready and all(row["state"] == "pass" for row in dimensions))
    blockers = blocker_rows(dimensions)
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": {"name": "report-speaker-resolved-terminal-gate-v1", "version": VERSION},
        "decision": "TERMINAL_GATE_INSTRUMENT_READY" if instrument_ready else "EVIDENCE_INCOMPLETE",
        "instrument_status": "ready" if instrument_ready else "not_ready",
        "product_decision": "READY" if product_ready else "NOT_READY",
        "aggregate_quality_score": None,
        "sources": sources,
        "dimensions": dimensions,
        "blockers": blockers,
        "gates": {
            "frozen_inputs_current": inputs_current,
            "all_dimensions_explicit": all_explicit,
            "aggregate_quality_score_disabled": True,
            "instrument_ready": instrument_ready,
            "product_ready": product_ready,
        },
        "next_command": next(
            (row["next_command"] for row in blockers if row.get("next_command")), None
        ),
        "provenance": {
            "input_manifest_sha256": sha256_bytes(canonical_json(manifest)),
            "source_count": len(sources),
            "dimension_count": len(dimensions),
        },
        "privacy": policy["privacy"],
        "safety": policy["safety"],
    }


def render_markdown(report: dict[str, Any]) -> bytes:
    lines = [
        "# Speaker-Resolved Transcript Terminal Gate v1",
        "",
        f"Instrument: `{report['decision']}`  ",
        f"Product: `{report['product_decision']}`  ",
        "Aggregate quality score: disabled",
        "",
        "## Separate Gates",
        "",
        "| Gate | State | Key evidence |",
        "|---|---:|---|",
    ]
    for item in report["dimensions"]:
        metrics = ", ".join(f"{key}={value}" for key, value in item["metrics"].items())
        lines.append(f"| `{item['id']}` | `{item['state']}` | {metrics or 'unavailable'} |")
    lines.extend(["", "## Product Blockers", ""])
    if report["blockers"]:
        for row in report["blockers"]:
            lines.append(f"- `{row['dimension']}`: {row['reason']}")
            if row.get("next_command"):
                lines.append(f"  - `{row['next_command']}`")
    else:
        lines.append("- none")
    lines.extend([
        "",
        "The report is fingerprint-bound, contains no speech text, session IDs, speaker names or absolute paths,",
        "and does not mutate capture, ASR, transcript, speaker evidence or human answers.",
        "",
    ])
    return "\n".join(lines).encode()


def snapshot(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "version": 1,
        "decision": report["decision"],
        "instrument_status": report["instrument_status"],
        "product_decision": report["product_decision"],
        "aggregate_quality_score": report["aggregate_quality_score"],
        "dimensions": report["dimensions"],
        "blockers": report["blockers"],
        "gates": report["gates"],
        "safety": report["safety"],
    }


def output_paths(out_dir: Path) -> tuple[Path, Path]:
    return (
        out_dir / "speaker_resolved_terminal_gate_report.json",
        out_dir / "speaker_resolved_terminal_gate_report.md",
    )


def evaluate(args: argparse.Namespace, *, write: bool) -> tuple[dict[str, Any], bytes, bytes]:
    policy = read_json(args.policy)
    validate_policy(policy)
    manifest = load_manifest(args.out_dir)
    inputs_current, controls_current, sources, payloads = verify_manifest(
        manifest, args.policy, policy
    )
    report = build_report(
        policy, manifest, inputs_current, controls_current, sources, payloads
    )
    report_bytes = canonical_json(report)
    markdown_bytes = render_markdown(report)
    if write:
        json_path, markdown_path = output_paths(args.out_dir)
        atomic_write(json_path, report_bytes)
        atomic_write(markdown_path, markdown_bytes)
        if args.write_snapshot:
            atomic_write(args.snapshot, canonical_json(snapshot(report)))
    return report, report_bytes, markdown_bytes


def replay(args: argparse.Namespace) -> None:
    report, report_bytes, markdown_bytes = evaluate(args, write=False)
    json_path, markdown_path = output_paths(args.out_dir)
    expected = {
        json_path: report_bytes,
        markdown_path: markdown_bytes,
    }
    if args.write_snapshot or args.snapshot.is_file():
        expected[args.snapshot] = canonical_json(snapshot(report))
    mismatches = [str(path) for path, data in expected.items() if not path.is_file() or path.read_bytes() != data]
    if mismatches:
        raise TerminalGateError("byte_exact_replay_failed:" + ",".join(mismatches))


def print_status(report: dict[str, Any]) -> None:
    print(f"terminal_gate_instrument: {report['decision']}")
    print(f"product: {report['product_decision']}")
    for item in report["dimensions"]:
        print(f"  {item['id']}: {item['state']}")
    if report.get("next_command"):
        print(f"next: {report['next_command']}")


def main() -> int:
    args = parse_args()
    args.policy = args.policy.resolve()
    args.out_dir = args.out_dir.resolve()
    args.snapshot = args.snapshot.resolve()
    try:
        if args.action == "preflight":
            _, rows = preflight(args.policy)
            invalid = [row["id"] for row in rows if not row["valid"]]
            print(f"terminal_gate_preflight: {'passed' if not invalid else 'failed'}")
            if invalid:
                print("invalid_sources: " + ", ".join(invalid))
                return 2
            return 0
        if args.action == "freeze":
            manifest = freeze(args.policy, args.out_dir)
            print(f"terminal_gate_freeze: {len(manifest['sources'])} sources")
            return 0
        if args.action == "all" and (args.refresh or not manifest_path(args.out_dir).is_file()):
            freeze(args.policy, args.out_dir)
        if args.action == "replay":
            replay(args)
            print("terminal_gate: byte-exact replay passed")
            return 0
        if args.action == "status":
            path, _ = output_paths(args.out_dir)
            if not path.is_file():
                raise TerminalGateError("terminal_gate_report_missing; run all")
            report = read_json(path)
        else:
            report, _, _ = evaluate(args, write=True)
        print_status(report)
        return 0 if report.get("decision") == "TERMINAL_GATE_INSTRUMENT_READY" else 2
    except TerminalGateError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
