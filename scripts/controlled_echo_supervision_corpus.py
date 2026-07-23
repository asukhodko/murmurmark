#!/usr/bin/env python3
"""Deterministic corpus builder for Controlled Echo Supervision Lab v1."""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from controlled_echo_supervision import (
    ANALYSIS_SAMPLE_RATE,
    SCHEMA_DECISION,
    SCHEMA_FROZEN_CORPUS,
    SCHEMA_INSPECTION,
    SCHEMA_SUPERVISION,
    audio_metrics,
    canonical_json,
    default_policy_path,
    default_report_dir,
    deterministic_tree_manifest,
    digest_json,
    fingerprint,
    load_policy,
    normalized_correlation,
    policy_sha,
    read_audio,
    read_json,
    read_jsonl,
    relative,
    safe_output_dir,
    sha256,
    stable_id,
    verify_fingerprints,
    write_audio,
    write_json,
    write_jsonl,
)


EXACT_OUTPUTS = (
    "frozen_corpus.json",
    "phase_inventory.jsonl",
    "exclusion_report.json",
    "split_manifest.json",
    "supervision_manifest.jsonl",
    "oracle_report.json",
    "privacy_licensing_manifest.json",
    "corpus_decision.json",
    "corpus_decision.md",
    "replay_report.json",
)


def discover_inspections(sessions_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    rows: list[tuple[Path, dict[str, Any]]] = []
    if not sessions_root.exists():
        return rows
    for session in sorted(path for path in sessions_root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        inspection_path = session / "derived" / "echo-lab" / "inspection.json"
        if not inspection_path.is_file():
            continue
        payload = read_json(inspection_path)
        if payload.get("schema") != SCHEMA_INSPECTION:
            continue
        rows.append((session, payload))
    return rows


def frozen_capture(
    *,
    session: Path,
    inspection: dict[str, Any],
    sessions_root: Path,
) -> dict[str, Any]:
    capture_manifest_path = session / "derived" / "echo-lab" / "capture_manifest.json"
    schedule_path = session / "derived" / "echo-lab" / "echo_lab_schedule.json"
    inspection_path = session / "derived" / "echo-lab" / "inspection.json"
    phase_inventory_path = session / "derived" / "echo-lab" / "phase_inventory.jsonl"
    capture_manifest = read_json(capture_manifest_path)
    immutable_files: list[dict[str, Any]] = []
    for row in capture_manifest.get("raw", []) + capture_manifest.get("session_files", []):
        immutable_files.append(
            {
                "path": str(row["path"]),
                "bytes": int(row["bytes"]),
                "sha256": str(row["sha256"]),
            }
        )
    for path in (capture_manifest_path, schedule_path, inspection_path, phase_inventory_path):
        immutable_files.append(fingerprint(path, session))
    for phase in inspection.get("phases", []):
        for artifact in phase.get("artifacts", {}).values():
            if isinstance(artifact, dict) and artifact.get("path"):
                immutable_files.append(
                    {
                        "path": str(artifact["path"]),
                        "bytes": int(artifact["bytes"]),
                        "sha256": str(artifact["sha256"]),
                    }
                )
    deduplicated = {
        str(row["path"]): row
        for row in immutable_files
    }
    return {
        "session_id": session.name,
        "session": relative(session, sessions_root),
        "scenario": inspection["scenario"],
        "split": inspection["split"],
        "accepted": bool(inspection["accepted"]),
        "inspection_sha256": sha256(inspection_path),
        "immutable_files": [deduplicated[key] for key in sorted(deduplicated)],
    }


def freeze_discovered(
    *,
    sessions_root: Path,
    policy_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    policy = load_policy(policy_path)
    policy_digest = policy_sha(policy_path)
    discovered = discover_inspections(sessions_root)
    captures: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for session, inspection in discovered:
        if inspection.get("policy_sha256") != policy_digest:
            exclusions.append(
                {
                    "session_id": session.name,
                    "outcome": "excluded",
                    "reasons": ["policy_sha256_mismatch"],
                }
            )
            continue
        if not inspection.get("accepted"):
            exclusions.append(
                {
                    "session_id": session.name,
                    "scenario": inspection.get("scenario"),
                    "outcome": "excluded",
                    "reasons": inspection.get("reasons", ["inspection_not_accepted"]),
                }
            )
            continue
        captures.append(
            frozen_capture(
                session=session,
                inspection=inspection,
                sessions_root=sessions_root,
            )
        )

    frozen = {
        "schema": SCHEMA_FROZEN_CORPUS,
        "profile": policy["profile"],
        "policy": relative(policy_path, policy_path.parent.parent),
        "policy_sha256": policy_digest,
        "capture_count": len(captures),
        "captures": sorted(captures, key=lambda row: row["session_id"]),
        "discovery_exclusions": sorted(
            exclusions,
            key=lambda row: str(row.get("session_id") or ""),
        ),
        "existing_real_counterexamples": {
            "role": "immutable_hard_test_only",
            "path": policy["source"]["previous_hard_test"],
            "sha256": policy["source"]["previous_hard_test_sha256"],
        },
    }
    frozen["fingerprint"] = digest_json(frozen)
    return frozen, exclusions


def resolve_frozen_sessions(
    frozen: dict[str, Any],
    sessions_root: Path,
) -> tuple[list[tuple[Path, dict[str, Any], dict[str, Any]]], list[str]]:
    resolved: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
    errors: list[str] = []
    for capture in frozen.get("captures", []):
        session = sessions_root / str(capture["session"])
        if not session.is_dir():
            errors.append(f"missing_session:{capture['session_id']}")
            continue
        fingerprint_errors = verify_fingerprints(capture.get("immutable_files", []), session)
        errors.extend(f"{capture['session_id']}:{error}" for error in fingerprint_errors)
        inspection_path = session / "derived" / "echo-lab" / "inspection.json"
        if not inspection_path.is_file():
            errors.append(f"missing_inspection:{capture['session_id']}")
            continue
        inspection = read_json(inspection_path)
        if sha256(inspection_path) != capture.get("inspection_sha256"):
            errors.append(f"inspection_sha256:{capture['session_id']}")
        resolved.append((session, capture, inspection))
    return resolved, errors


def phase_map(inspection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["kind"]): row for row in inspection.get("phases", []) if row.get("accepted")}


def clip_audio(
    *,
    source: Path,
    destination: Path,
    start_sample: int,
    sample_count: int,
) -> np.ndarray:
    source_audio = read_audio(source)
    end = start_sample + sample_count
    if start_sample < 0 or end > source_audio.samples.size:
        raise RuntimeError(f"clip exceeds source: {source} {start_sample}:{end}")
    audio = np.asarray(source_audio.samples[start_sample:end], dtype=np.float32)
    write_audio(destination, audio)
    return audio


def source_path(session: Path, phase: dict[str, Any], name: str) -> Path:
    artifact = phase.get("artifacts", {}).get(name)
    if not isinstance(artifact, dict) or not artifact.get("path"):
        raise RuntimeError(f"phase {phase.get('phase_id')} lacks {name} artifact")
    path = session / str(artifact["path"])
    if not path.is_file() or sha256(path) != artifact.get("sha256"):
        raise RuntimeError(f"phase artifact missing or changed: {path}")
    return path


def phase_clip_specs(
    *,
    session_id: str,
    split: str,
    phase: dict[str, Any],
    kind: str,
    clip_duration_sec: float,
    clip_hop_sec: float,
) -> list[dict[str, Any]]:
    duration = float(phase["metrics"]["duration_sec"])
    rows: list[dict[str, Any]] = []
    cursor = 0.0
    while cursor + clip_duration_sec <= duration + 1.0e-9:
        rows.append(
            {
                "clip_id": stable_id(session_id, phase["phase_id"], kind, f"{cursor:.6f}"),
                "session_id": session_id,
                "split": split,
                "kind": kind,
                "phase_id": phase["phase_id"],
                "start_sec": round(cursor, 6),
                "end_sec": round(cursor + clip_duration_sec, 6),
                "duration_sec": round(clip_duration_sec, 6),
            }
        )
        cursor += clip_hop_sec
    return rows


def finite_and_unclipped(audio: np.ndarray, maximum_peak: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not np.all(np.isfinite(audio)):
        reasons.append("non_finite")
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > maximum_peak:
        reasons.append("clipping")
    return not reasons, reasons


def write_item_audio(
    *,
    output_dir: Path,
    split: str,
    kind: str,
    item_id: str,
    audio: np.ndarray,
) -> Path:
    path = output_dir / "examples" / split / kind / f"{item_id}.wav"
    write_audio(path, audio)
    return path


def materialize_measured(
    *,
    resolved: Sequence[tuple[Path, dict[str, Any], dict[str, Any]]],
    output_dir: Path,
    sessions_root: Path,
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    materialization = policy["materialization"]
    clip_duration = float(materialization["clip_duration_sec"])
    clip_hop = float(materialization["clip_hop_sec"])
    sample_count = int(round(clip_duration * ANALYSIS_SAMPLE_RATE))
    maximum_peak = float(policy["validation"]["maximum_abs_peak"])
    manifest: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    phase_rows: list[dict[str, Any]] = []

    for session, capture, inspection in resolved:
        split = str(capture["split"])
        session_id = str(capture["session_id"])
        accepted_phases = phase_map(inspection)
        for phase in inspection.get("phases", []):
            row = dict(phase)
            row.update(
                {
                    "session_id": session_id,
                    "scenario": capture["scenario"],
                    "split": split,
                }
            )
            phase_rows.append(row)

        mappings = (
            ("local_only", "measured_local_target", "mic"),
            ("remote_only", "measured_remote_echo", "mic"),
            ("silence_background", "silence_background", "mic"),
            ("keyboard_noise", "keyboard_noise", "mic"),
            ("controlled_double_talk", "measured_double_talk", "mic"),
            ("opening_backchannel", "opening_backchannel", "mic"),
        )
        for phase_kind, item_kind, artifact_name in mappings:
            phase = accepted_phases.get(phase_kind)
            if not phase:
                continue
            source = source_path(session, phase, artifact_name)
            source_audio = read_audio(source).samples
            aligned_audio_full: np.ndarray | None = None
            if item_kind == "measured_remote_echo":
                aligned_source = source_path(session, phase, "aligned_remote")
                aligned_audio_full = read_audio(aligned_source).samples
            specs = phase_clip_specs(
                session_id=session_id,
                split=split,
                phase=phase,
                kind=item_kind,
                clip_duration_sec=clip_duration,
                clip_hop_sec=clip_hop,
            )
            for spec in specs:
                start_sample = int(round(float(spec["start_sec"]) * ANALYSIS_SAMPLE_RATE))
                audio = np.asarray(source_audio[start_sample : start_sample + sample_count], dtype=np.float32)
                valid, reasons = finite_and_unclipped(audio, maximum_peak)
                if audio.size != sample_count:
                    reasons.append("duration_mismatch")
                    valid = False
                if not valid:
                    exclusions.append({**spec, "reasons": sorted(set(reasons))})
                    continue
                destination = write_item_audio(
                    output_dir=output_dir,
                    split=split,
                    kind=item_kind,
                    item_id=spec["clip_id"],
                    audio=audio,
                )
                row = {
                    "schema": SCHEMA_SUPERVISION,
                    **spec,
                    "audio": fingerprint(destination, output_dir),
                    "measured": True,
                    "training_eligible": item_kind
                    in {
                        "measured_local_target",
                        "measured_remote_echo",
                        "silence_background",
                        "keyboard_noise",
                    }
                    and split in {"train", "dev"},
                }
                if item_kind == "measured_remote_echo":
                    assert aligned_audio_full is not None
                    aligned_audio = aligned_audio_full[start_sample : start_sample + sample_count]
                    aligned_id = stable_id(spec["clip_id"], "aligned_remote_reference")
                    aligned_destination = write_item_audio(
                        output_dir=output_dir,
                        split=split,
                        kind="aligned_remote_reference",
                        item_id=aligned_id,
                        audio=aligned_audio,
                    )
                    row["aligned_remote_reference"] = fingerprint(aligned_destination, output_dir)
                    row["aligned_remote_reference_id"] = aligned_id
                manifest.append(row)
    return manifest, exclusions, phase_rows


def synthetic_pairs(
    *,
    measured: list[dict[str, Any]],
    output_dir: Path,
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_split_kind: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in measured:
        by_split_kind[(str(row["split"]), str(row["kind"]))].append(row)
    for rows in by_split_kind.values():
        rows.sort(key=lambda row: row["clip_id"])

    gains = [float(value) for value in policy["materialization"]["synthetic_echo_gain_db"]]
    maximum_pairs = int(policy["materialization"]["maximum_pairs_per_local_clip"])
    maximum_peak = float(policy["validation"]["maximum_abs_peak"])
    target_corr_max = float(policy["oracle_gates"]["target_remote_correlation_max"])
    mixture_target_min = float(policy["oracle_gates"]["mixture_contains_target_min_correlation"])
    reconstruction_limit = float(policy["oracle_gates"]["synthetic_reconstruction_max_abs_error"])
    rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for split in ("train", "dev"):
        targets = by_split_kind[(split, "measured_local_target")]
        echoes = by_split_kind[(split, "measured_remote_echo")]
        if not targets or not echoes:
            continue
        for target_index, target in enumerate(targets):
            target_audio = read_audio(output_dir / str(target["audio"]["path"])).samples
            for gain_index, gain_db in enumerate(gains[:maximum_pairs]):
                echo = echoes[(target_index + gain_index) % len(echoes)]
                echo_audio = read_audio(output_dir / str(echo["audio"]["path"])).samples
                remote_audio = read_audio(
                    output_dir / str(echo["aligned_remote_reference"]["path"])
                ).samples
                count = min(target_audio.size, echo_audio.size, remote_audio.size)
                target_piece = np.asarray(target_audio[:count], dtype=np.float32)
                echo_piece = np.asarray(echo_audio[:count], dtype=np.float32)
                remote_piece = np.asarray(remote_audio[:count], dtype=np.float32)
                gain = float(10.0 ** (gain_db / 20.0))
                mixture = np.asarray(
                    target_piece.astype(np.float64) + gain * echo_piece.astype(np.float64),
                    dtype=np.float32,
                )
                item_id = stable_id(target["clip_id"], echo["clip_id"], f"{gain_db:.3f}")
                reasons: list[str] = []
                valid, base_reasons = finite_and_unclipped(mixture, maximum_peak)
                reasons.extend(base_reasons)
                target_remote_corr = abs(normalized_correlation(target_piece, remote_piece))
                mixture_target_corr = normalized_correlation(mixture, target_piece)
                reconstruction_error = float(
                    np.max(
                        np.abs(
                            mixture.astype(np.float64)
                            - (
                                target_piece.astype(np.float64)
                                + gain * echo_piece.astype(np.float64)
                            )
                        )
                    )
                )
                if target_remote_corr > target_corr_max:
                    reasons.append("target_remote_leakage")
                if mixture_target_corr < mixture_target_min:
                    reasons.append("target_not_preserved")
                if reconstruction_error > reconstruction_limit:
                    reasons.append("reconstruction_error")
                if count != target_audio.size or count != echo_audio.size or count != remote_audio.size:
                    reasons.append("duration_mismatch")
                if not valid or reasons:
                    exclusions.append(
                        {
                            "item_id": item_id,
                            "kind": "synthetic_double_talk",
                            "split": split,
                            "target_clip_id": target["clip_id"],
                            "echo_clip_id": echo["clip_id"],
                            "gain_db": gain_db,
                            "reasons": sorted(set(reasons)),
                        }
                    )
                    continue
                destination = write_item_audio(
                    output_dir=output_dir,
                    split=split,
                    kind="synthetic_double_talk",
                    item_id=item_id,
                    audio=mixture,
                )
                rows.append(
                    {
                        "schema": SCHEMA_SUPERVISION,
                        "item_id": item_id,
                        "kind": "synthetic_double_talk",
                        "split": split,
                        "session_id": target["session_id"],
                        "echo_session_id": echo["session_id"],
                        "duration_sec": round(count / ANALYSIS_SAMPLE_RATE, 6),
                        "gain_db": gain_db,
                        "gain_linear": round(gain, 12),
                        "mixture": fingerprint(destination, output_dir),
                        "target": target["audio"],
                        "target_clip_id": target["clip_id"],
                        "measured_echo": echo["audio"],
                        "echo_clip_id": echo["clip_id"],
                        "aligned_remote_reference": echo["aligned_remote_reference"],
                        "target_remote_correlation": round(target_remote_corr, 9),
                        "mixture_target_correlation": round(mixture_target_corr, 9),
                        "reconstruction_max_abs_error": round(reconstruction_error, 12),
                        "measured": False,
                        "synthetic": True,
                        "training_eligible": True,
                    }
                )
    return rows, exclusions


def prompt_item_counts(
    resolved: Sequence[tuple[Path, dict[str, Any], dict[str, Any]]],
) -> dict[str, int]:
    protected = 0
    opening = 0
    for session, _, inspection in resolved:
        schedule_path = session / "derived" / "echo-lab" / "echo_lab_schedule.json"
        schedule = read_json(schedule_path)
        accepted_phase_ids = {
            str(row["phase_id"])
            for row in inspection.get("phases", [])
            if row.get("accepted")
        }
        for prompt in schedule.get("prompts", []):
            if str(prompt.get("phase_id")) not in accepted_phase_ids:
                continue
            protected += 1
            if prompt.get("phase_id") == "opening_backchannel":
                opening += 1
    return {"protected_local_items": protected, "opening_backchannel_items": opening}


def coverage(
    *,
    captures: Sequence[dict[str, Any]],
    manifest: Sequence[dict[str, Any]],
    prompt_counts: dict[str, int],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    split_counts = Counter(str(row["split"]) for row in captures)
    result["train_capture_count"] = split_counts["train"]
    result["dev_capture_count"] = split_counts["dev"]
    result["controlled_hard_test_capture_count"] = split_counts["hard_test"]
    seconds: dict[tuple[str, str], float] = defaultdict(float)
    for row in manifest:
        split = str(row["split"])
        kind = str(row["kind"])
        seconds[(split, kind)] += float(row.get("duration_sec") or 0.0)
    result.update(
        {
            "train_local_only_seconds": round(seconds[("train", "measured_local_target")], 6),
            "train_remote_only_seconds": round(seconds[("train", "measured_remote_echo")], 6),
            "train_synthetic_mixture_seconds": round(seconds[("train", "synthetic_double_talk")], 6),
            "dev_local_only_seconds": round(seconds[("dev", "measured_local_target")], 6),
            "dev_remote_only_seconds": round(seconds[("dev", "measured_remote_echo")], 6),
            "dev_synthetic_mixture_seconds": round(seconds[("dev", "synthetic_double_talk")], 6),
            "controlled_hard_test_double_talk_seconds": round(
                seconds[("hard_test", "measured_double_talk")],
                6,
            ),
            **prompt_counts,
        }
    )
    return result


def coverage_gates(coverage_values: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, Any]]:
    gates = policy["coverage_gates"]
    mapping = {
        "minimum_train_capture_count": "train_capture_count",
        "minimum_dev_capture_count": "dev_capture_count",
        "minimum_controlled_hard_test_capture_count": "controlled_hard_test_capture_count",
        "minimum_train_local_only_seconds": "train_local_only_seconds",
        "minimum_train_remote_only_seconds": "train_remote_only_seconds",
        "minimum_train_synthetic_mixture_seconds": "train_synthetic_mixture_seconds",
        "minimum_dev_local_only_seconds": "dev_local_only_seconds",
        "minimum_dev_remote_only_seconds": "dev_remote_only_seconds",
        "minimum_dev_synthetic_mixture_seconds": "dev_synthetic_mixture_seconds",
        "minimum_controlled_hard_test_double_talk_seconds": "controlled_hard_test_double_talk_seconds",
        "minimum_protected_local_items": "protected_local_items",
        "minimum_opening_backchannel_items": "opening_backchannel_items",
    }
    rows: list[dict[str, Any]] = []
    for gate_name, metric_name in mapping.items():
        actual = coverage_values.get(metric_name, 0)
        required = gates[gate_name]
        rows.append(
            {
                "gate": gate_name,
                "metric": metric_name,
                "actual": actual,
                "required": required,
                "passed": actual >= required,
            }
        )
    return rows


def split_oracle(captures: Sequence[dict[str, Any]], manifest: Sequence[dict[str, Any]]) -> dict[str, Any]:
    session_splits: dict[str, set[str]] = defaultdict(set)
    for capture in captures:
        session_splits[str(capture["session_id"])].add(str(capture["split"]))
    for row in manifest:
        session_splits[str(row["session_id"])].add(str(row["split"]))
        if row.get("echo_session_id"):
            session_splits[str(row["echo_session_id"])].add(str(row["split"]))
    leaking = {
        session_id: sorted(splits)
        for session_id, splits in session_splits.items()
        if len(splits) > 1
    }
    hard_training_rows = [
        str(row.get("item_id") or row.get("clip_id"))
        for row in manifest
        if row.get("split") == "hard_test" and row.get("training_eligible")
    ]
    return {
        "session_disjoint": not leaking,
        "session_leakage": leaking,
        "hard_test_isolated": not hard_training_rows,
        "hard_test_training_items": hard_training_rows,
    }


def source_hash_oracle(policy: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    passed = True
    for path_key, hash_key in (
        ("previous_corpus_policy", "previous_corpus_policy_sha256"),
        ("previous_corpus_decision", "previous_corpus_decision_sha256"),
        ("previous_hard_test", "previous_hard_test_sha256"),
        ("production_policy", "production_policy_sha256"),
    ):
        path = repo_root / str(policy["source"][path_key])
        expected = str(policy["source"][hash_key])
        actual = sha256(path) if path.is_file() else None
        row_passed = actual == expected
        rows.append(
            {
                "path": relative(path, repo_root),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "passed": row_passed,
            }
        )
        passed = passed and row_passed
    return {"passed": passed, "files": rows}


def existing_hard_test_oracle(policy: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    manifest_path = repo_root / str(policy["source"]["previous_hard_test"])
    errors: list[str] = []
    if not manifest_path.is_file():
        return {
            "passed": False,
            "item_count": 0,
            "errors": ["manifest_missing"],
        }
    payload = read_json(manifest_path)
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return {
            "passed": False,
            "item_count": 0,
            "errors": ["items_missing"],
        }
    hash_cache: dict[Path, str] = {}

    def actual_sha(path: Path) -> str:
        if path not in hash_cache:
            hash_cache[path] = sha256(path)
        return hash_cache[path]

    for item in items:
        item_id = str(item.get("id") or "unknown")
        if item.get("split") != "hard_test" or item.get("usage") != "hard_evaluation":
            errors.append(f"{item_id}:not_hard_test_only")
        audio_sources = item.get("audio_sources")
        if not isinstance(audio_sources, dict) or not audio_sources:
            errors.append(f"{item_id}:audio_sources_missing")
            continue
        for source_name, source in audio_sources.items():
            if not isinstance(source, dict) or not source.get("path") or not source.get("sha256"):
                errors.append(f"{item_id}:{source_name}:fingerprint_missing")
                continue
            path = repo_root / str(source["path"])
            if not path.is_file():
                errors.append(f"{item_id}:{source_name}:missing")
            elif actual_sha(path) != source["sha256"]:
                errors.append(f"{item_id}:{source_name}:sha256")
        evidence = item.get("evidence")
        if isinstance(evidence, dict) and evidence.get("source_artifact") and evidence.get("source_sha256"):
            path = repo_root / str(evidence["source_artifact"])
            if not path.is_file():
                errors.append(f"{item_id}:evidence:missing")
            elif actual_sha(path) != evidence["source_sha256"]:
                errors.append(f"{item_id}:evidence:sha256")
    return {
        "passed": not errors,
        "item_count": len(items),
        "verified_file_count": len(hash_cache),
        "errors": errors,
    }


def decision_markdown(decision: dict[str, Any]) -> str:
    lines = [
        "# Controlled Echo Supervision Lab v1",
        "",
        f"Decision: **{decision['decision']}**",
        "",
        f"Accepted captures: `{decision['coverage']['train_capture_count']}` train, "
        f"`{decision['coverage']['dev_capture_count']}` dev, "
        f"`{decision['coverage']['controlled_hard_test_capture_count']}` hard-test.",
        "",
        "## Coverage",
        "",
    ]
    for gate in decision["coverage_gates"]:
        mark = "PASS" if gate["passed"] else "FAIL"
        lines.append(
            f"- `{mark}` {gate['metric']}: `{gate['actual']}` / required `{gate['required']}`"
        )
    lines.extend(["", "## Reasons", ""])
    if decision["reasons"]:
        lines.extend(f"- {reason}" for reason in decision["reasons"])
    else:
        lines.append("- All frozen coverage and oracle gates passed.")
    lines.extend(
        [
            "",
            "No training or production promotion was performed. "
            "`local_fir_role_masked` remains production.",
            "",
        ]
    )
    return "\n".join(lines)


def write_corpus(
    *,
    destination: Path,
    sessions_root: Path,
    repo_root: Path,
    policy_path: Path,
    frozen: dict[str, Any],
    discovery_exclusions: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    destination.mkdir(parents=True, exist_ok=True)
    resolved, immutable_errors = resolve_frozen_sessions(frozen, sessions_root)
    measured, materialization_exclusions, phase_rows = materialize_measured(
        resolved=resolved,
        output_dir=destination,
        sessions_root=sessions_root,
        policy=policy,
    )
    synthetic, synthetic_exclusions = synthetic_pairs(
        measured=measured,
        output_dir=destination,
        policy=policy,
    )
    manifest = sorted(
        measured + synthetic,
        key=lambda row: (
            str(row.get("split")),
            str(row.get("kind")),
            str(row.get("item_id") or row.get("clip_id")),
        ),
    )
    captures = frozen.get("captures", [])
    prompt_counts = prompt_item_counts(resolved)
    coverage_values = coverage(
        captures=captures,
        manifest=manifest,
        prompt_counts=prompt_counts,
    )
    gate_rows = coverage_gates(coverage_values, policy)
    split_checks = split_oracle(captures, manifest)
    source_checks = source_hash_oracle(policy, repo_root)
    existing_hard_test_checks = existing_hard_test_oracle(policy, repo_root)
    required_scenario_order = [
        scenario
        for scenario, config in policy["scenarios"].items()
        if bool(config.get("required"))
    ]
    required_scenarios = set(required_scenario_order)
    observed_scenarios = {str(row["scenario"]) for row in captures}
    missing_scenarios = [
        scenario for scenario in required_scenario_order if scenario not in observed_scenarios
    ]
    privacy_passed = (
        policy["privacy"]["processing"] == "local_only"
        and policy["privacy"]["raw_audio_redistribution"] == "forbidden"
        and policy["privacy"]["derived_audio_redistribution"] == "forbidden"
        and policy["privacy"]["user_voice_redistribution"] == "forbidden"
        and policy["privacy"]["source_control_audio"] == "forbidden"
    )
    oracle_reasons: list[str] = []
    if immutable_errors:
        oracle_reasons.append("frozen_input_hash_mismatch")
    if not split_checks["session_disjoint"]:
        oracle_reasons.append("session_leakage")
    if not split_checks["hard_test_isolated"]:
        oracle_reasons.append("hard_test_training_leakage")
    if not source_checks["passed"]:
        oracle_reasons.append("source_hash_mismatch")
    if not existing_hard_test_checks["passed"]:
        oracle_reasons.append("existing_hard_test_changed")
    if missing_scenarios:
        oracle_reasons.append("required_scenarios_missing")
    if not privacy_passed:
        oracle_reasons.append("privacy_policy_failed")
    if any(not row["passed"] for row in gate_rows):
        oracle_reasons.append("coverage_gates_failed")
    oracle = {
        "schema": "murmurmark.controlled_echo_oracle_report/v1",
        "profile": policy["profile"],
        "policy_sha256": policy_sha(policy_path),
        "passed": not oracle_reasons,
        "reasons": oracle_reasons,
        "immutable_input_errors": immutable_errors,
        "split": split_checks,
        "source_hashes": source_checks,
        "existing_hard_test": existing_hard_test_checks,
        "scenarios": {
            "required": required_scenario_order,
            "observed": sorted(observed_scenarios),
            "missing": missing_scenarios,
            "passed": not missing_scenarios,
        },
        "privacy_passed": privacy_passed,
        "synthetic": {
            "item_count": len(synthetic),
            "rejected_count": len(synthetic_exclusions),
            "maximum_reconstruction_error": max(
                (float(row["reconstruction_max_abs_error"]) for row in synthetic),
                default=0.0,
            ),
            "measured_and_synthetic_distinct": all(
                row.get("measured") is False and row.get("synthetic") is True
                for row in synthetic
            ),
        },
        "coverage": coverage_values,
        "coverage_gates": gate_rows,
    }
    decision_value = "READY_FOR_ADAPTATION" if oracle["passed"] else "DO_NOT_TRAIN"
    decision_reasons = list(oracle_reasons)
    for gate in gate_rows:
        if not gate["passed"]:
            decision_reasons.append(
                f"{gate['metric']}={gate['actual']} below {gate['required']}"
            )
    decision = {
        "schema": SCHEMA_DECISION,
        "profile": policy["profile"],
        "policy_sha256": policy_sha(policy_path),
        "decision": decision_value,
        "reasons": decision_reasons,
        "coverage": coverage_values,
        "coverage_gates": gate_rows,
        "oracle_passed": oracle["passed"],
        "training_performed": False,
        "production_changed": False,
        "production_profile": policy["source"]["production_fallback"],
    }
    decision["fingerprint"] = digest_json(decision)
    split_manifest = {
        "schema": "murmurmark.controlled_echo_split_manifest/v1",
        "policy_sha256": policy_sha(policy_path),
        "sessions": [
            {
                "session_id": row["session_id"],
                "scenario": row["scenario"],
                "split": row["split"],
            }
            for row in captures
        ],
        "session_disjoint": split_checks["session_disjoint"],
        "existing_real_counterexamples": frozen["existing_real_counterexamples"],
    }
    frozen_discovery_exclusions = list(
        frozen.get("discovery_exclusions", discovery_exclusions)
    )
    exclusions = {
        "schema": "murmurmark.controlled_echo_exclusion_report/v1",
        "discovery": frozen_discovery_exclusions,
        "materialization": materialization_exclusions,
        "synthetic": synthetic_exclusions,
        "counts": {
            "discovery": len(frozen_discovery_exclusions),
            "materialization": len(materialization_exclusions),
            "synthetic": len(synthetic_exclusions),
        },
    }
    privacy = {
        "schema": "murmurmark.controlled_echo_privacy_licensing/v1",
        "policy_sha256": policy_sha(policy_path),
        "processing": "local_only",
        "network_used": False,
        "redistribution": "forbidden",
        "contains_user_voice": bool(captures),
        "source_control_allowed": False,
        "training_scope": "private_local_experiment_only",
        "existing_real_counterexamples": "immutable_hard_test_only",
        "tts_source": "macos_system_voice",
        "tts_redistribution": "forbidden",
        "passed": privacy_passed,
    }
    write_json(destination / "frozen_corpus.json", frozen)
    write_jsonl(destination / "phase_inventory.jsonl", phase_rows)
    write_json(destination / "exclusion_report.json", exclusions)
    write_json(destination / "split_manifest.json", split_manifest)
    write_jsonl(destination / "supervision_manifest.jsonl", manifest)
    write_json(destination / "oracle_report.json", oracle)
    write_json(destination / "privacy_licensing_manifest.json", privacy)
    write_json(destination / "corpus_decision.json", decision)
    (destination / "corpus_decision.md").write_text(
        decision_markdown(decision),
        encoding="utf-8",
    )
    write_json(
        destination / "replay_report.json",
        {
            "schema": "murmurmark.controlled_echo_replay_report/v1",
            "status": "not_run",
            "matched_files": 0,
            "total_files": 0,
            "mismatches": [],
        },
    )
    return decision


def build(
    *,
    sessions_root: Path,
    output_dir: Path,
    policy_path: Path = default_policy_path(),
    frozen_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sessions_root = sessions_root.resolve()
    output_dir = output_dir.resolve()
    repo_root = policy_path.resolve().parent.parent
    safe_output_dir(output_dir, sessions_root)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    if frozen_input is None:
        frozen, exclusions = freeze_discovered(
            sessions_root=sessions_root,
            policy_path=policy_path.resolve(),
        )
    else:
        frozen = json.loads(canonical_json(frozen_input))
        exclusions = []
    with tempfile.TemporaryDirectory(
        prefix="controlled-echo-supervision-build-",
        dir=output_dir.parent,
    ) as temporary:
        staging = Path(temporary) / output_dir.name
        decision = write_corpus(
            destination=staging,
            sessions_root=sessions_root,
            repo_root=repo_root,
            policy_path=policy_path.resolve(),
            frozen=frozen,
            discovery_exclusions=exclusions,
        )
        replacement = output_dir.with_name(output_dir.name + ".next")
        if replacement.exists():
            shutil.rmtree(replacement)
        shutil.copytree(staging, replacement)
        if output_dir.exists():
            shutil.rmtree(output_dir)
        replacement.rename(output_dir)
    return decision


def replay(
    *,
    sessions_root: Path,
    output_dir: Path,
    policy_path: Path = default_policy_path(),
) -> dict[str, Any]:
    sessions_root = sessions_root.resolve()
    output_dir = output_dir.resolve()
    frozen_path = output_dir / "frozen_corpus.json"
    if not frozen_path.is_file():
        raise RuntimeError(f"frozen corpus not found: {frozen_path}")
    frozen = read_json(frozen_path)
    original_manifest = deterministic_tree_manifest(
        output_dir,
        exclude={"replay_report.json"},
    )
    with tempfile.TemporaryDirectory(
        prefix="controlled-echo-supervision-replay-",
        dir=output_dir.parent,
    ) as temporary:
        replay_dir = Path(temporary) / output_dir.name
        write_corpus(
            destination=replay_dir,
            sessions_root=sessions_root,
            repo_root=policy_path.resolve().parent.parent,
            policy_path=policy_path.resolve(),
            frozen=frozen,
            discovery_exclusions=[],
        )
        replay_manifest = deterministic_tree_manifest(
            replay_dir,
            exclude={"replay_report.json"},
        )
    original_by_path = {row["path"]: row for row in original_manifest}
    replay_by_path = {row["path"]: row for row in replay_manifest}
    paths = sorted(set(original_by_path) | set(replay_by_path))
    mismatches: list[dict[str, Any]] = []
    matched = 0
    for path in paths:
        left = original_by_path.get(path)
        right = replay_by_path.get(path)
        if left == right:
            matched += 1
        else:
            mismatches.append({"path": path, "original": left, "replay": right})
    report = {
        "schema": "murmurmark.controlled_echo_replay_report/v1",
        "status": "passed" if not mismatches else "failed",
        "matched_files": matched,
        "total_files": len(paths),
        "mismatches": mismatches,
        "frozen_corpus_sha256": sha256(frozen_path),
    }
    write_json(output_dir / "replay_report.json", report)
    return report


def status(output_dir: Path) -> dict[str, Any]:
    decision_path = output_dir / "corpus_decision.json"
    if not decision_path.is_file():
        return {
            "available": False,
            "decision": "DO_NOT_TRAIN",
            "reason": "corpus_not_built",
            "output_dir": str(output_dir),
        }
    decision = read_json(decision_path)
    replay_path = output_dir / "replay_report.json"
    replay_payload = read_json(replay_path) if replay_path.is_file() else {}
    oracle_path = output_dir / "oracle_report.json"
    oracle_payload = read_json(oracle_path) if oracle_path.is_file() else {}
    scenarios = oracle_payload.get("scenarios", {})
    missing_scenarios = list(scenarios.get("missing") or [])
    replay_status = replay_payload.get("status", "not_run")
    if replay_status != "passed":
        next_command = "murmurmark corpus echo-supervision replay"
    elif missing_scenarios:
        scenario = str(missing_scenarios[0])
        suffix = scenario.removeprefix("speaker_").replace("_", "-")
        next_command = (
            'SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-echo-'
            f'{suffix}"; murmurmark echo-lab capture --out "$SESSION" --scenario {scenario}'
        )
    elif decision.get("decision") == "READY_FOR_ADAPTATION":
        next_command = "start the separate Speaker-Preserving Neural Echo v2 goal"
    else:
        next_command = f"read {output_dir / 'corpus_decision.md'}"
    return {
        "available": True,
        "decision": decision.get("decision"),
        "fingerprint": decision.get("fingerprint"),
        "coverage": decision.get("coverage"),
        "failed_gates": [
            row for row in decision.get("coverage_gates", []) if not row.get("passed")
        ],
        "required_scenarios": scenarios.get("required", []),
        "observed_scenarios": scenarios.get("observed", []),
        "missing_scenarios": missing_scenarios,
        "next_command": next_command,
        "replay": replay_status,
        "output_dir": str(output_dir),
    }
