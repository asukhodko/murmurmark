#!/usr/bin/env python3
"""Resolve transcript-order false positives with frozen local audio evidence."""

from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.speaker_bounded_chronology_arbitration_policy/v1"
INPUT_SCHEMA = "murmurmark.speaker_bounded_chronology_arbitration_input/v1"
ITEM_SCHEMA = "murmurmark.speaker_bounded_chronology_arbitration_item/v1"
REPORT_SCHEMA = "murmurmark.speaker_bounded_chronology_arbitration_report/v1"
SNAPSHOT_SCHEMA = "murmurmark.speaker_bounded_chronology_arbitration_snapshot/v1"
REPLAY_SCHEMA = "murmurmark.speaker_bounded_chronology_arbitration_replay/v1"
DEFAULT_POLICY = ROOT / "policies/speaker-bounded-chronology-arbitration-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/speaker-bounded-chronology-arbitration-v1"
DEFAULT_SNAPSHOT = ROOT / "docs/testing/speaker-bounded-chronology-arbitration-v1-snapshot.json"
BLOCKING_LABELS = {"needs_review", "probable_order_risk"}
CLOSED_OUTCOMES = {"benign_turn_boundary", "confirmed_double_talk"}
HARMFUL_GROUP_LABELS = {
    "probable_duplicate",
    "probable_remote_leak",
    "probable_asr_noise",
}
SAFE_JUDGE_LABELS = {"confirm_me", "confirm_timing_or_doubletalk"}
TOKEN_RE = re.compile(r"[^0-9a-zа-я_+-]+")


class ChronologyError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze and arbitrate speaker-bounded chronology evidence."
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
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--write-snapshot", action="store_true")
    return parser.parse_args()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
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
        raise ChronologyError(f"cannot_read_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise ChronologyError(f"expected_json_object:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as error:
        raise ChronologyError(f"cannot_read_jsonl:{path}:{error}") from error
    if not all(isinstance(row, dict) for row in rows):
        raise ChronologyError(f"expected_jsonl_objects:{path}")
    return rows


def resolve_path(value: Any, policy_path: Path) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path.resolve()
    repo_path = (ROOT / path).resolve()
    if repo_path.exists() or policy_path.resolve().is_relative_to(ROOT):
        return repo_path
    return (policy_path.parent / path).resolve()


def identity(path: Path, *, required: bool = True) -> dict[str, Any]:
    exists = path.is_file()
    if required and not exists:
        raise ChronologyError(f"required_artifact_missing:{path}")
    row: dict[str, Any] = {"path": str(path.resolve()), "exists": exists}
    if exists:
        row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return row


def identity_current(row: Any) -> bool:
    if not isinstance(row, dict) or not row.get("path"):
        return False
    path = Path(str(row["path"])).expanduser().resolve()
    if bool(row.get("exists")) != path.is_file():
        return False
    if not path.is_file():
        return True
    expected_bytes = row.get("bytes")
    return bool(
        expected_bytes is not None
        and int(expected_bytes) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def artifact_path(row: Any) -> Path:
    if not isinstance(row, dict) or not row.get("path"):
        raise ChronologyError("artifact_path_missing")
    return Path(str(row["path"])).expanduser().resolve()


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


def nested(value: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def normalize(text: Any) -> str:
    return " ".join(TOKEN_RE.sub(" ", str(text or "").lower().replace("ё", "е")).split())


def text_similarity(left: Any, right: Any) -> float:
    left_value = normalize(left)
    right_value = normalize(right)
    if not left_value or not right_value:
        return float(left_value == right_value)
    return SequenceMatcher(None, left_value, right_value).ratio()


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise ChronologyError(f"unsupported_policy_schema:{policy.get('schema')}")
    thresholds = policy.get("thresholds") or {}
    required = {
        "expected_queue_items",
        "expected_queue_seconds",
        "minimum_closed_item_ratio",
        "minimum_closed_seconds_ratio",
        "group_timing_confidence",
        "judge_confidence",
        "minimum_local_evidence",
        "maximum_safe_audio_leak",
        "minimum_clean_rms_db",
        "remote_active_rms_db",
        "minimum_text_identity_similarity",
        "maximum_interval_identity_delta_sec",
    }
    if not isinstance(thresholds, dict) or not required.issubset(thresholds):
        raise ChronologyError("policy_thresholds_incomplete")
    if number(thresholds["minimum_closed_item_ratio"]) < 0.5:
        raise ChronologyError("minimum_closed_item_ratio_below_goal")
    if number(thresholds["minimum_closed_seconds_ratio"]) < 0.5:
        raise ChronologyError("minimum_closed_seconds_ratio_below_goal")
    safety = policy.get("safety") or {}
    required_false = {
        "raw_audio_mutation",
        "selected_transcript_mutation",
        "role_mutation",
        "timestamp_mutation",
        "primary_asr_mutation",
        "cloud_inference",
    }
    if safety.get("read_only") is not True or any(safety.get(key) is not False for key in required_false):
        raise ChronologyError("read_only_safety_contract_invalid")
    privacy = policy.get("privacy") or {}
    if any(
        privacy.get(key) is not False
        for key in ("public_session_ids", "public_absolute_paths", "public_speech_text")
    ):
        raise ChronologyError("public_privacy_contract_invalid")


def configured_rebaseline(policy: dict[str, Any], policy_path: Path) -> Path:
    value = policy.get("rebaseline_manifest")
    if not value:
        raise ChronologyError("rebaseline_manifest_not_configured")
    return resolve_path(value, policy_path)


def session_sources(source: dict[str, Any]) -> tuple[dict[str, Path], bool]:
    session = Path(str(source.get("session_path") or "")).expanduser().resolve()
    artifacts = source.get("artifacts") or {}
    order_audit = artifact_path(artifacts.get("order_audit"))
    selected_dialogue = artifact_path(artifacts.get("selected_dialogue"))
    order_rows = order_audit.with_name("transcript_order_items.jsonl")
    blocking = any(str(row.get("label")) in BLOCKING_LABELS for row in read_jsonl(order_rows))
    paths = {
        "order_audit": order_audit,
        "order_items": order_rows,
        "selected_dialogue": selected_dialogue,
    }
    if blocking:
        paths.update(
            {
                "group_items": session / "derived/audit/group-overlaps/group_overlap_audit.jsonl",
                "group_summary": session / "derived/audit/group-overlaps/group_overlap_summary.json",
                "stronger_items": session / "derived/audit/audio-review-pack/faster_whisper_judge.jsonl",
                "stronger_summary": session / "derived/audit/audio-review-pack/faster_whisper_judge_summary.json",
                "speaker_state": session / "derived/preprocess/echo/speaker_state.jsonl",
                "mic_clean": session / "derived/preprocess/audio/mic_clean_local_fir.wav",
                "mic_role_masked": session / "derived/preprocess/audio/mic_role_masked_for_asr.wav",
            }
        )
    return paths, blocking


def preflight(policy_path: Path) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    policy = read_json(policy_path)
    validate_policy(policy)
    rebaseline_path = configured_rebaseline(policy, policy_path)
    rebaseline = read_json(rebaseline_path)
    if rebaseline.get("schema") != "murmurmark.post_segmentation_transcript_rebaseline_input/v1":
        raise ChronologyError(f"unsupported_rebaseline_manifest:{rebaseline.get('schema')}")
    discovered: list[dict[str, Any]] = []
    for source in rebaseline.get("sessions") or []:
        if not isinstance(source, dict):
            continue
        paths, blocking = session_sources(source)
        missing = [
            name
            for name, path in paths.items()
            if name not in {"stronger_items", "stronger_summary"} and not path.is_file()
        ]
        session = Path(str(source.get("session_path") or "")).expanduser().resolve()
        raw_paths = sorted((session / "audio/mic").glob("*.caf")) + sorted(
            (session / "audio/remote").glob("*.caf")
        )
        if blocking and not raw_paths:
            missing.append("raw_audio")
        discovered.append(
            {
                "alias": str(source.get("alias") or ""),
                "session_id": str(source.get("session_name") or session.name),
                "session_path": str(session),
                "selected_profile": str(source.get("selected_profile") or ""),
                "semantic_fingerprint": str(source.get("semantic_fingerprint") or ""),
                "blocking": blocking,
                "paths": paths,
                "raw_paths": raw_paths,
                "missing": missing,
            }
        )
    if not discovered:
        raise ChronologyError("rebaseline_sessions_empty")
    return policy, rebaseline, discovered


def manifest_path(out_dir: Path) -> Path:
    return out_dir / "private/input_manifest.json"


def freeze(policy_path: Path, out_dir: Path) -> dict[str, Any]:
    policy, _, discovered = preflight(policy_path)
    missing = [f"{row['alias']}:{name}" for row in discovered for name in row["missing"]]
    if missing:
        raise ChronologyError("preflight_missing:" + ",".join(missing))
    sessions: list[dict[str, Any]] = []
    for row in discovered:
        artifacts = {
            name: identity(path, required=name not in {"stronger_items", "stronger_summary"})
            for name, path in row["paths"].items()
        }
        if row["blocking"]:
            artifacts["raw_audio"] = [identity(path) for path in row["raw_paths"]]
        sessions.append(
            {
                "alias": row["alias"],
                "session_id": row["session_id"],
                "session_path": row["session_path"],
                "selected_profile": row["selected_profile"],
                "semantic_fingerprint": row["semantic_fingerprint"],
                "blocking": row["blocking"],
                "artifacts": artifacts,
            }
        )
    rebaseline_path = configured_rebaseline(policy, policy_path)
    manifest = {
        "schema": INPUT_SCHEMA,
        "version": 1,
        "policy": identity(policy_path.resolve()),
        "implementation": identity(Path(__file__).resolve()),
        "rebaseline_manifest": identity(rebaseline_path),
        "sessions": sessions,
        "safety": policy["safety"],
    }
    atomic_write(manifest_path(out_dir), canonical_json(manifest))
    return manifest


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = manifest_path(out_dir)
    if not path.is_file():
        raise ChronologyError("frozen_input_manifest_missing; run freeze")
    manifest = read_json(path)
    if manifest.get("schema") != INPUT_SCHEMA:
        raise ChronologyError(f"unsupported_input_schema:{manifest.get('schema')}")
    return manifest


def manifest_issues(manifest: dict[str, Any], policy_path: Path) -> list[str]:
    issues: list[str] = []
    if not identity_current(manifest.get("policy")) or artifact_path(manifest["policy"]) != policy_path.resolve():
        issues.append("policy_stale")
    if not identity_current(manifest.get("implementation")):
        issues.append("implementation_stale")
    if not identity_current(manifest.get("rebaseline_manifest")):
        issues.append("rebaseline_manifest_stale")
    for session in manifest.get("sessions") or []:
        alias = str(session.get("alias") or "unknown")
        for name, row in (session.get("artifacts") or {}).items():
            values = row if isinstance(row, list) else [row]
            if any(not identity_current(value) for value in values):
                issues.append(f"{alias}:{name}_stale")
    return issues


def pair_key_from_order(row: dict[str, Any]) -> tuple[str, ...]:
    utterances = row.get("utterances") or {}
    return tuple(
        sorted(
            str((utterances.get(role) or {}).get("id") or "")
            for role in ("me", "remote")
        )
    )


def pair_key(row: dict[str, Any]) -> tuple[str, ...]:
    ids = row.get("utterance_ids")
    if isinstance(ids, list) and ids:
        return tuple(sorted(str(value) for value in ids))
    return pair_key_from_order(row)


def identity_matches(
    order: dict[str, Any], group: dict[str, Any], thresholds: dict[str, Any]
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if pair_key_from_order(order) != pair_key(group):
        reasons.append("utterance_pair_mismatch")
    order_interval = order.get("interval") or {}
    group_interval = group.get("interval") or {}
    delta = number(thresholds["maximum_interval_identity_delta_sec"])
    if abs(number(order_interval.get("start")) - number(group_interval.get("start"))) > delta:
        reasons.append("interval_start_mismatch")
    if abs(number(order_interval.get("end")) - number(group_interval.get("end"))) > delta:
        reasons.append("interval_end_mismatch")
    minimum_text = number(thresholds["minimum_text_identity_similarity"])
    order_utterances = order.get("utterances") or {}
    group_utterances = group.get("utterances") or {}
    for role in ("me", "remote"):
        similarity = text_similarity(
            nested(order_utterances, role, "text", default=""),
            nested(group_utterances, role, "text", default=""),
        )
        if similarity < minimum_text:
            reasons.append(f"{role}_text_identity_mismatch")
    return not reasons, reasons


def best_judge(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(
        rows,
        key=lambda row: number(nested(row, "classification", "confidence")),
        default=None,
    )


def classify(
    order: dict[str, Any],
    group: dict[str, Any] | None,
    judge: dict[str, Any] | None,
    thresholds: dict[str, Any],
) -> tuple[str, float, list[str], dict[str, Any]]:
    if group is None:
        return "insufficient_evidence", 0.0, ["group_audio_evidence_missing"], {}
    matches, identity_reasons = identity_matches(order, group, thresholds)
    if not matches:
        return "insufficient_evidence", 0.0, identity_reasons, {}

    group_label = str(nested(group, "classification", "label", default=""))
    group_confidence = number(nested(group, "classification", "confidence"))
    local_evidence = number(nested(group, "scores", "local_evidence"))
    audio_leak = number(nested(group, "scores", "audio_leak"))
    clean_rms = number(nested(group, "features", "audio", "rms_db", "mic_clean", default=-120.0))
    remote_rms = number(nested(group, "features", "audio", "rms_db", "remote", default=-120.0))
    local_only = number(nested(group, "features", "speaker_state", "local_only_ratio"))
    remote_only = number(nested(group, "features", "speaker_state", "remote_only_ratio"))
    double_talk = number(nested(group, "features", "speaker_state", "double_talk_ratio"))
    near_boundary = nested(group, "features", "interval", "near_boundary") is True
    text_similarity_value = number(nested(group, "features", "text", "similarity_max"))
    judge_label = str(nested(judge, "classification", "label", default=""))
    judge_confidence = number(nested(judge, "classification", "confidence"))
    judge_supported = bool(
        judge_label in SAFE_JUDGE_LABELS
        and judge_confidence >= number(thresholds["judge_confidence"])
    )
    local_supported = bool(
        local_evidence >= number(thresholds["minimum_local_evidence"])
        and clean_rms >= number(thresholds["minimum_clean_rms_db"])
        and audio_leak < 90.0
    )
    remote_active = bool(
        remote_rms >= number(thresholds["remote_active_rms_db"])
        or remote_only >= 0.20
        or double_talk >= 0.10
    )
    evidence = {
        "group_label": group_label,
        "group_confidence": round(group_confidence, 6),
        "judge_label": judge_label or None,
        "judge_confidence": round(judge_confidence, 6),
        "local_evidence": round(local_evidence, 6),
        "audio_leak": round(audio_leak, 6),
        "clean_rms_db": round(clean_rms, 6),
        "remote_rms_db": round(remote_rms, 6),
        "local_only_ratio": round(local_only, 6),
        "remote_only_ratio": round(remote_only, 6),
        "double_talk_ratio": round(double_talk, 6),
        "near_boundary": near_boundary,
        "text_similarity": round(text_similarity_value, 6),
        "local_supported": local_supported,
        "remote_active": remote_active,
    }

    if group_label in HARMFUL_GROUP_LABELS:
        return (
            "remote_leak_or_asr_segmentation",
            group_confidence,
            ["harmful_audio_class_transferred_out_of_chronology"],
            evidence,
        )

    timing_supported = bool(
        group_label == "probable_timing_overlap"
        and group_confidence >= number(thresholds["group_timing_confidence"])
        and near_boundary
        and text_similarity_value < 0.55
        and audio_leak < number(thresholds["maximum_safe_audio_leak"])
        and local_supported
    )
    group_double_talk = bool(
        group_label == "probable_double_talk"
        and group_confidence >= 0.70
        and local_supported
        and remote_active
    )
    judge_consensus = bool(
        judge_supported
        and local_supported
        and (
            remote_rms < number(thresholds["remote_active_rms_db"])
            or double_talk >= 0.10
            or group_label in {"probable_timing_overlap", "probable_double_talk"}
        )
    )

    if group_double_talk or (judge_consensus and remote_active):
        confidence = max(group_confidence if group_double_talk else 0.0, judge_confidence)
        return (
            "confirmed_double_talk",
            confidence,
            [
                "independent_local_audio_and_remote_activity_agree",
                "linear_order_is_not_defined_for_true_overlap",
            ],
            evidence,
        )
    if timing_supported or judge_consensus:
        confidence = group_confidence if timing_supported and not judge_supported else max(
            group_confidence if timing_supported else 0.0,
            judge_confidence,
        )
        return (
            "benign_turn_boundary",
            confidence,
            [
                "local_audio_and_timeline_boundary_agree",
                "wide_asr_segments_do_not_prove_order_damage",
            ],
            evidence,
        )

    features = order.get("features") or {}
    if (
        str(order.get("label")) == "probable_order_risk"
        and bool(features.get("me_wraps_remote"))
        and number(features.get("post_remote_tail_sec")) >= 0.8
    ):
        return (
            "true_chronology_risk",
            number(order.get("confidence")),
            ["wrapped_remote_turn_lacks_lossless_order_proof"],
            evidence,
        )
    return (
        "insufficient_evidence",
        max(group_confidence, judge_confidence),
        ["audio_or_timeline_judges_are_weak_or_conflicting"],
        evidence,
    )


def source_fingerprint(row: dict[str, Any] | None) -> str | None:
    return sha256_bytes(canonical_json(row)) if isinstance(row, dict) else None


def build_items(
    manifest: dict[str, Any], policy: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    thresholds = policy["thresholds"]
    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    aliases = Counter()
    for session in manifest.get("sessions") or []:
        artifacts = session.get("artifacts") or {}
        order_rows = [
            row
            for row in read_jsonl(artifact_path(artifacts["order_items"]))
            if str(row.get("label")) in BLOCKING_LABELS
        ]
        if not order_rows:
            continue
        group_rows = read_jsonl(artifact_path(artifacts["group_items"]))
        group_by_pair = {pair_key(row): row for row in group_rows}
        judge_rows = (
            read_jsonl(artifact_path(artifacts["stronger_items"]))
            if artifacts.get("stronger_items", {}).get("exists")
            else []
        )
        judges_by_pair: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in judge_rows:
            judges_by_pair.setdefault(pair_key(row), []).append(row)
        alias = str(session.get("alias") or "")
        for order in order_rows:
            pair = pair_key_from_order(order)
            group = group_by_pair.get(pair)
            judge = best_judge(judges_by_pair.get(pair, []))
            outcome, confidence, reasons, evidence = classify(
                order, group, judge, thresholds
            )
            duration = round(number(nested(order, "interval", "duration_sec")), 6)
            aliases[outcome] += 1
            public = {
                "schema": ITEM_SCHEMA,
                "alias": alias,
                "item_id": str(order.get("item_id") or ""),
                "original_label": str(order.get("label") or ""),
                "duration_sec": duration,
                "outcome": outcome,
                "closed": outcome in CLOSED_OUTCOMES,
                "confidence": round(confidence, 6),
                "reason_codes": reasons,
                "evidence": evidence,
                "source_fingerprints": {
                    "order": source_fingerprint(order),
                    "group_audio_state": source_fingerprint(group),
                    "stronger_audio_judge": source_fingerprint(judge),
                },
            }
            utterances = order.get("utterances") or {}
            private = {
                **public,
                "session_id": session.get("session_id"),
                "session_path": session.get("session_path"),
                "selected_profile": session.get("selected_profile"),
                "interval": order.get("interval"),
                "utterance_ids": [
                    nested(utterances, "me", "id"),
                    nested(utterances, "remote", "id"),
                ],
                "source_paths": {
                    name: value.get("path")
                    for name, value in artifacts.items()
                    if isinstance(value, dict)
                },
            }
            public_rows.append(public)
            private_rows.append(private)
    public_rows.sort(key=lambda row: (row["alias"], row["item_id"]))
    private_rows.sort(key=lambda row: (row["alias"], row["item_id"]))
    queue_items = len(public_rows)
    queue_seconds = round(sum(number(row["duration_sec"]) for row in public_rows), 6)
    expected_items = integer(thresholds["expected_queue_items"])
    expected_seconds = number(thresholds["expected_queue_seconds"])
    if queue_items != expected_items or abs(queue_seconds - expected_seconds) > 0.001:
        raise ChronologyError(
            f"frozen_queue_mismatch:items={queue_items}/{expected_items}:seconds={queue_seconds}/{expected_seconds}"
        )
    return public_rows, private_rows, {"by_outcome": dict(sorted(aliases.items()))}


def summarize(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    by_outcome: dict[str, dict[str, Any]] = {}
    for outcome in sorted({str(row["outcome"]) for row in rows}):
        selected = [row for row in rows if row["outcome"] == outcome]
        by_outcome[outcome] = {
            "items": len(selected),
            "seconds": round(sum(number(row["duration_sec"]) for row in selected), 6),
        }
    total_items = len(rows)
    total_seconds = round(sum(number(row["duration_sec"]) for row in rows), 6)
    closed = [row for row in rows if row["closed"]]
    closed_items = len(closed)
    closed_seconds = round(sum(number(row["duration_sec"]) for row in closed), 6)
    remaining_items = total_items - closed_items
    remaining_seconds = round(total_seconds - closed_seconds, 6)
    return {
        "frozen_items": total_items,
        "frozen_seconds": total_seconds,
        "closed_items": closed_items,
        "closed_seconds": closed_seconds,
        "closed_item_ratio": round(closed_items / total_items, 6) if total_items else 0.0,
        "closed_seconds_ratio": round(closed_seconds / total_seconds, 6) if total_seconds else 0.0,
        "remaining_items": remaining_items,
        "remaining_seconds": remaining_seconds,
        "by_outcome": by_outcome,
        "minimum_closed_item_ratio": policy["thresholds"]["minimum_closed_item_ratio"],
        "minimum_closed_seconds_ratio": policy["thresholds"]["minimum_closed_seconds_ratio"],
    }


def build_report(
    manifest: dict[str, Any], policy: dict[str, Any], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    summary = summarize(rows, policy)
    threshold_passed = bool(
        summary["closed_item_ratio"] >= number(summary["minimum_closed_item_ratio"])
        and summary["closed_seconds_ratio"] >= number(summary["minimum_closed_seconds_ratio"])
    )
    all_stable = summary["frozen_items"] == integer(policy["thresholds"]["expected_queue_items"])
    decision = (
        "PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1"
        if threshold_passed and all_stable
        else "EVIDENCE_BOUND"
    )
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": {
            "name": "report-speaker-bounded-chronology-arbitration-v1",
            "version": VERSION,
        },
        "decision": decision,
        "summary": summary,
        "gates": {
            "all_rows_have_stable_outcome": all_stable,
            "minimum_item_closure": summary["closed_item_ratio"]
            >= number(summary["minimum_closed_item_ratio"]),
            "minimum_seconds_closure": summary["closed_seconds_ratio"]
            >= number(summary["minimum_closed_seconds_ratio"]),
            "selected_text_roles_timestamps_unchanged": True,
            "raw_audio_unchanged": True,
            "local_only_offline_evidence": True,
            "public_report_privacy_safe": True,
        },
        "inputs": {
            "manifest": "private/input_manifest.json",
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
            "session_aliases": len(manifest.get("sessions") or []),
        },
        "safety": policy["safety"],
        "privacy": policy["privacy"],
        "next_command": (
            "murmurmark corpus terminal-gate-v1 all --refresh"
            if decision == "PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1"
            else None
        ),
    }


def incomplete_report(policy: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": {
            "name": "report-speaker-bounded-chronology-arbitration-v1",
            "version": VERSION,
        },
        "decision": "EVIDENCE_INCOMPLETE",
        "summary": {
            "frozen_items": 0,
            "frozen_seconds": 0.0,
            "closed_items": 0,
            "closed_seconds": 0.0,
            "remaining_items": None,
            "remaining_seconds": None,
            "by_outcome": {},
        },
        "gates": {"frozen_inputs_current": False},
        "issues": issues,
        "safety": policy.get("safety") or {},
        "privacy": policy.get("privacy") or {},
        "next_command": "murmurmark corpus chronology-arbitration-v1 freeze --refresh",
    }


def render_markdown(report: dict[str, Any]) -> bytes:
    summary = report.get("summary") or {}
    lines = [
        "# Speaker-Bounded Chronology Evidence Arbitration v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Queue",
        "",
        f"- Frozen: `{summary.get('frozen_items')}` rows / `{summary.get('frozen_seconds')}` sec",
        f"- Closed as benign: `{summary.get('closed_items')}` rows / `{summary.get('closed_seconds')}` sec",
        f"- Remaining: `{summary.get('remaining_items')}` rows / `{summary.get('remaining_seconds')}` sec",
        "",
        "## Outcomes",
        "",
        "| Outcome | Rows | Seconds |",
        "|---|---:|---:|",
    ]
    for outcome, values in (summary.get("by_outcome") or {}).items():
        lines.append(f"| `{outcome}` | {values['items']} | {values['seconds']} |")
    lines.extend(
        [
            "",
            "Only independently supported turn boundaries and true double-talk close chronology rows.",
            "Remote leak, ASR segmentation errors, true order risks and insufficient evidence remain explicit.",
            "No transcript text, role, timestamp, speaker evidence or raw audio is changed.",
            "",
        ]
    )
    return "\n".join(lines).encode()


def snapshot(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "version": 1,
        "decision": report["decision"],
        "summary": report["summary"],
        "gates": report["gates"],
    }


def output_paths(out_dir: Path) -> dict[str, Path]:
    return {
        "report": out_dir / "speaker_bounded_chronology_arbitration_report.json",
        "markdown": out_dir / "speaker_bounded_chronology_arbitration_report.md",
        "items": out_dir / "arbitration_items.jsonl",
        "private_items": out_dir / "private/arbitration_items.jsonl",
        "replay": out_dir / "replay_report.json",
        "artifacts": out_dir / "artifact_manifest.json",
    }


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in rows
    )


def evaluate(
    policy_path: Path, out_dir: Path, snapshot_path: Path, write_snapshot: bool
) -> tuple[dict[str, Any], int]:
    policy = read_json(policy_path)
    validate_policy(policy)
    manifest = load_manifest(out_dir)
    issues = manifest_issues(manifest, policy_path)
    paths = output_paths(out_dir)
    if issues:
        report = incomplete_report(policy, issues)
        atomic_write(paths["report"], canonical_json(report))
        atomic_write(paths["markdown"], render_markdown(report))
        return report, 2
    public_rows, private_rows, _ = build_items(manifest, policy)
    report = build_report(manifest, policy, public_rows)
    atomic_write(paths["items"], jsonl_bytes(public_rows))
    atomic_write(paths["private_items"], jsonl_bytes(private_rows))
    atomic_write(paths["report"], canonical_json(report))
    atomic_write(paths["markdown"], render_markdown(report))
    if write_snapshot:
        atomic_write(snapshot_path, canonical_json(snapshot(report)))
    artifacts = {
        name: sha256_file(path)
        for name, path in paths.items()
        if name in {"report", "markdown", "items", "private_items"} and path.is_file()
    }
    atomic_write(
        paths["artifacts"],
        canonical_json(
            {
                "schema": "murmurmark.speaker_bounded_chronology_arbitration_artifacts/v1",
                "artifacts": artifacts,
            }
        ),
    )
    return report, 0


def replay(policy_path: Path, out_dir: Path, snapshot_path: Path, write_snapshot: bool) -> int:
    paths = output_paths(out_dir)
    expected = {
        name: path.read_bytes()
        for name, path in paths.items()
        if name in {"report", "markdown", "items", "private_items"} and path.is_file()
    }
    if len(expected) != 4:
        raise ChronologyError("evaluated_outputs_missing; run evaluate")
    report, status = evaluate(policy_path, out_dir, snapshot_path, write_snapshot)
    actual = {name: paths[name].read_bytes() for name in expected}
    exact = status == 0 and expected == actual
    replay_report = {
        "schema": REPLAY_SCHEMA,
        "version": 1,
        "decision": "REPLAY_EXACT" if exact else "REPLAY_MISMATCH",
        "report_decision": report["decision"],
        "exact_outputs": exact,
        "checked_outputs": sorted(expected),
    }
    atomic_write(paths["replay"], canonical_json(replay_report))
    return 0 if exact else 2


def print_status(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    print(f"decision: {report.get('decision')}")
    print(f"frozen: {summary.get('frozen_items')} rows / {summary.get('frozen_seconds')}s")
    print(f"closed: {summary.get('closed_items')} rows / {summary.get('closed_seconds')}s")
    print(f"remaining: {summary.get('remaining_items')} rows / {summary.get('remaining_seconds')}s")
    if report.get("next_command"):
        print(f"next: {report['next_command']}")


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    snapshot_path = args.snapshot.expanduser().resolve()
    try:
        if args.action == "preflight":
            _, _, rows = preflight(policy_path)
            missing = [f"{row['alias']}:{name}" for row in rows for name in row["missing"]]
            print(f"sessions: {len(rows)}")
            print(f"blocking_sessions: {sum(bool(row['blocking']) for row in rows)}")
            print(f"status: {'blocked' if missing else 'ready'}")
            if missing:
                print("missing: " + ",".join(missing))
                return 2
            return 0
        if args.action == "freeze":
            freeze(policy_path, out_dir)
            print(f"frozen_manifest: {manifest_path(out_dir)}")
            return 0
        if args.action == "status":
            report = read_json(output_paths(out_dir)["report"])
            print_status(report)
            return 0 if report.get("decision") != "EVIDENCE_INCOMPLETE" else 2
        if args.action == "evaluate":
            report, status = evaluate(
                policy_path, out_dir, snapshot_path, args.write_snapshot
            )
            print_status(report)
            return status
        if args.action == "replay":
            return replay(policy_path, out_dir, snapshot_path, args.write_snapshot)
        if args.refresh or not manifest_path(out_dir).is_file():
            freeze(policy_path, out_dir)
        report, status = evaluate(policy_path, out_dir, snapshot_path, args.write_snapshot)
        print_status(report)
        if status:
            return status
        return replay(policy_path, out_dir, snapshot_path, args.write_snapshot)
    except ChronologyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
