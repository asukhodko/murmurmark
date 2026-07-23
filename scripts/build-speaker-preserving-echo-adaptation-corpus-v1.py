#!/usr/bin/env python3
"""Build the private Speaker-Preserving Echo Adaptation Corpus v1."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf

import speaker_preserving_echo_corpus as core


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/speaker-preserving-echo-adaptation-corpus-v1.json"
DEFAULT_OUTPUT = (
    ROOT / "sessions/_reports/speaker-preserving-echo-adaptation-corpus-v1"
)
PROFILE = "speaker_preserving_echo_adaptation_corpus_v1"
GENERATOR_VERSION = "0.1.0"
CORE_ARTIFACTS = (
    "corpus_policy.json",
    "corpus_manifest.json",
    "interval_inventory.jsonl",
    "split_manifest.json",
    "immutable_hard_test.json",
    "supervision_manifest.jsonl",
    "oracle_report.json",
    "privacy_licensing_manifest.json",
    "corpus_decision.json",
    "corpus_decision.md",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or replay the private speaker-preserving echo corpus."
    )
    parser.add_argument(
        "command",
        choices=("build", "replay", "inspect"),
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def source_path(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else ROOT / path


def verify_expected_file(path: Path, expected_sha256: str) -> dict[str, Any]:
    require(path.exists(), f"required frozen input missing: {path}")
    observed = core.sha256(path)
    require(
        observed == expected_sha256,
        f"frozen input changed: {path} ({observed} != {expected_sha256})",
    )
    return core.fingerprint(path, ROOT)


def load_and_verify_sources(
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    source = policy["source"]
    frozen_path = source_path(source["frozen_corpus"])
    neural_report_path = source_path(source["neural_decision_report"])
    production_policy_path = source_path(source["production_policy"])
    verify_expected_file(frozen_path, source["frozen_corpus_sha256"])
    verify_expected_file(
        neural_report_path,
        source["neural_decision_report_sha256"],
    )
    verify_expected_file(
        production_policy_path,
        source["production_policy_sha256"],
    )
    frozen = core.read_json(frozen_path)
    neural_report = core.read_json(neural_report_path)
    production = core.read_json(production_policy_path)
    require(
        neural_report.get("decision_fingerprint")
        == source["neural_decision_fingerprint"],
        "neural decision fingerprint changed",
    )
    require(
        neural_report.get("promotion", {}).get("decision") == "DO_NOT_PROMOTE",
        "neural predecessor must remain DO_NOT_PROMOTE",
    )
    require(
        production.get("fallback") == source["production_fallback"],
        "production fallback changed",
    )
    require(
        production.get("decision") == "DO_NOT_PROMOTE",
        "production echo policy changed unexpectedly",
    )
    sessions = frozen.get("sessions")
    require(isinstance(sessions, list) and sessions, "frozen corpus has no sessions")
    frozen_hard = set(frozen.get("hard_counterexamples") or [])
    require(
        frozen_hard == set(policy["hard_test"]["sessions"]),
        "hard counterexample set differs from policy",
    )
    return frozen, neural_report, sessions


def verify_session_freeze(
    session_row: dict[str, Any],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    session_id = str(session_row["session"])
    session = ROOT / "sessions" / session_id
    freeze = session_row["freeze"]
    fingerprints: list[dict[str, Any]] = []
    for group in ("raw", "risk_evidence"):
        for row in freeze.get(group) or []:
            path = session / row["path"]
            fingerprints.append(verify_expected_file(path, row["sha256"]))
    for row in (freeze.get("derived_inputs") or {}).values():
        path = session / row["path"]
        fingerprints.append(verify_expected_file(path, row["sha256"]))

    canonical_dir = session / "derived/preprocess/echo-promotion-v1/canonical"
    canonical_paths = {
        "mic": canonical_dir / "mic.wav",
        "remote_aligned": canonical_dir / "remote_aligned.wav",
        "timeline_contract": canonical_dir / "timeline_contract.json",
    }
    for path in canonical_paths.values():
        require(path.exists(), f"canonical aligned input missing: {path}")
        fingerprints.append(core.fingerprint(path, ROOT))
    neural_probe: Path | None = None
    if session_id in set(policy["hard_test"]["sessions"]):
        evidence = policy["hard_test"]["evidence"].get(session_id)
        require(evidence is not None, f"hard-test evidence missing: {session_id}")
        neural_probe = session / evidence["path"]
        fingerprints.append(
            verify_expected_file(neural_probe, evidence["sha256"])
        )
    timeline = core.read_json(canonical_paths["timeline_contract"])
    expected_inputs = freeze.get("derived_inputs") or {}
    observed_inputs = timeline.get("input_fingerprint") or {}
    for key in ("mic", "remote", "speaker_state", "local_fir_clean", "local_fir_role_masked"):
        require(
            observed_inputs.get(key, {}).get("sha256")
            == expected_inputs.get(key, {}).get("sha256"),
            f"canonical timeline input mismatch: {session_id}:{key}",
        )
    require(
        timeline.get("aligned_remote_sha256")
        == core.sha256(canonical_paths["remote_aligned"]),
        f"aligned remote hash mismatch: {session_id}",
    )
    return sorted(fingerprints, key=lambda row: row["path"]), {
        "session": session,
        "mic": canonical_paths["mic"],
        "remote": canonical_paths["remote_aligned"],
        "timeline": canonical_paths["timeline_contract"],
        "speaker_state": session / expected_inputs["speaker_state"]["path"],
        "dialogue": session
        / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json",
        "neural_probe": neural_probe,
    }


def read_dialogue(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("utterances") or payload.get("dialogue") or []
    require(isinstance(payload, list), f"invalid clean dialogue: {path}")
    return [row for row in payload if isinstance(row, dict)]


def preliminary_state_reasons(
    *,
    row: dict[str, Any],
    category: str,
    split: str,
    acoustic_mode: str,
    me_evidence: dict[str, Any],
    remote_evidence: dict[str, Any],
    policy: dict[str, Any],
) -> list[str]:
    interval_policy = policy["intervals"]
    category_policy = interval_policy[category]
    duration = float(row.get("end", 0.0)) - float(row.get("start", 0.0))
    reasons: list[str] = []
    if acoustic_mode == "uncertain":
        reasons.append("uncertain_acoustic_mode")
    if str(row.get("state") or "") not in category_policy["accepted_states"]:
        reasons.append("state_not_accepted")
    if float(row.get("confidence") or 0.0) < float(
        interval_policy["minimum_state_confidence"]
    ):
        reasons.append("state_confidence_below_threshold")
    if duration + 1.0e-9 < float(interval_policy["clip_duration_sec"]):
        reasons.append("interval_too_short")

    required = float(interval_policy["minimum_utterance_coverage_ratio"])
    opposite_max = float(interval_policy["maximum_opposite_role_coverage_ratio"])
    me_coverage = float(me_evidence["coverage_ratio"])
    remote_coverage = float(remote_evidence["coverage_ratio"])
    if category == "local_only":
        if me_coverage < required:
            reasons.append("confirmed_me_coverage_below_threshold")
        if remote_coverage > opposite_max:
            reasons.append("remote_text_overlaps_local_target")
        if float(row.get("remote_db") or -120.0) > float(
            category_policy["maximum_remote_db"]
        ):
            reasons.append("speaker_state_remote_level_too_high")
        if float(row.get("mic_db") or -120.0) < float(
            category_policy["minimum_mic_db"]
        ):
            reasons.append("speaker_state_mic_level_too_low")
    elif category == "remote_only":
        if remote_coverage < required:
            reasons.append("authoritative_remote_coverage_below_threshold")
        if me_coverage > opposite_max:
            reasons.append("confirmed_me_overlaps_remote_echo")
        if float(row.get("remote_db") or -120.0) < float(
            category_policy["minimum_remote_db"]
        ):
            reasons.append("speaker_state_remote_level_too_low")
        if float(row.get("mic_db") or -120.0) < float(
            category_policy["minimum_mic_db"]
        ):
            reasons.append("speaker_state_echo_level_too_low")
    else:
        if me_coverage < required:
            reasons.append("confirmed_me_coverage_below_threshold")
        if remote_coverage < required:
            reasons.append("authoritative_remote_coverage_below_threshold")
        if float(row.get("remote_db") or -120.0) < float(
            category_policy["minimum_remote_db"]
        ):
            reasons.append("speaker_state_remote_level_too_low")
        if float(row.get("mic_db") or -120.0) < float(
            category_policy["minimum_mic_db"]
        ):
            reasons.append("speaker_state_mic_level_too_low")
        if split != "hard_test":
            reasons.append("measured_double_talk_has_no_clean_target")
    return reasons


def audio_reasons(
    metrics: dict[str, Any],
    category: str,
    policy: dict[str, Any],
) -> list[str]:
    exclusions = policy["intervals"]["exclusion"]
    category_policy = policy["intervals"][category]
    reasons: list[str] = []
    expected_samples = int(
        round(
            float(policy["intervals"]["clip_duration_sec"])
            * int(policy["intervals"]["sample_rate"])
        )
    )
    if int(metrics["samples"]) != expected_samples:
        reasons.append("wrong_duration")
    if bool(exclusions["reject_non_finite"]) and not metrics["finite"]:
        reasons.append("non_finite_audio")
    if metrics["mic_peak"] > float(exclusions["maximum_abs_peak"]):
        reasons.append("mic_peak_above_threshold")
    if metrics["remote_peak"] > float(exclusions["maximum_abs_peak"]):
        reasons.append("remote_peak_above_threshold")
    if metrics["mic_clipped_ratio"] > float(
        exclusions["maximum_clipped_sample_ratio"]
    ):
        reasons.append("mic_clipping")
    if metrics["remote_clipped_ratio"] > float(
        exclusions["maximum_clipped_sample_ratio"]
    ):
        reasons.append("remote_clipping")
    if category == "local_only":
        if metrics["remote_rms_db"] > float(category_policy["maximum_remote_db"]):
            reasons.append("aligned_remote_level_too_high")
        if metrics["mic_rms_db"] < float(category_policy["minimum_mic_db"]):
            reasons.append("measured_local_level_too_low")
        if metrics["abs_remote_correlation"] > float(
            category_policy["maximum_abs_remote_correlation"]
        ):
            reasons.append("target_remote_correlation_too_high")
    elif category == "remote_only":
        if metrics["remote_rms_db"] < float(category_policy["minimum_remote_db"]):
            reasons.append("measured_remote_level_too_low")
        if metrics["mic_rms_db"] < float(category_policy["minimum_mic_db"]):
            reasons.append("measured_echo_level_too_low")
        if metrics["abs_remote_correlation"] < float(
            category_policy["minimum_abs_remote_correlation"]
        ):
            reasons.append("measured_echo_not_correlated_with_remote")
    return reasons


def build_state_inventory(
    *,
    session_id: str,
    split: str,
    acoustic_mode: str,
    paths: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = core.read_jsonl(paths["speaker_state"])
    utterances = read_dialogue(paths["dialogue"])
    minimum_role_confidence = float(
        policy["intervals"]["minimum_role_confidence"]
    )
    clip_duration = float(policy["intervals"]["clip_duration_sec"])
    accepted_states = {
        state: category
        for category in ("local_only", "remote_only", "double_talk")
        for state in policy["intervals"][category]["accepted_states"]
    }
    inventory: list[dict[str, Any]] = []
    state_sha = core.sha256(paths["speaker_state"])
    dialogue_sha = core.sha256(paths["dialogue"])
    mic_sha = core.sha256(paths["mic"])
    remote_sha = core.sha256(paths["remote"])
    for index, row in enumerate(rows):
        state = str(row.get("state") or "")
        if state not in accepted_states:
            continue
        category = accepted_states[state]
        start = float(row.get("start", 0.0))
        source_end = float(row.get("end", start))
        end = min(source_end, start + clip_duration)
        me = core.role_evidence(
            start,
            end,
            utterances,
            "me",
            minimum_role_confidence,
        )
        remote = core.role_evidence(
            start,
            end,
            utterances,
            "remote",
            minimum_role_confidence,
        )
        reasons = preliminary_state_reasons(
            row=row,
            category=category,
            split=split,
            acoustic_mode=acoustic_mode,
            me_evidence=me,
            remote_evidence=remote,
            policy=policy,
        )
        metrics: dict[str, Any] | None = None
        if not reasons:
            mic_audio = core.read_audio_slice(paths["mic"], start, end)
            remote_audio = core.read_audio_slice(paths["remote"], start, end)
            metrics = core.audio_metrics(mic_audio, remote_audio)
            reasons.extend(audio_reasons(metrics, category, policy))
        usage = (
            "hard_evaluation"
            if split == "hard_test"
            else (
                "supervision_source"
                if category in {"local_only", "remote_only"}
                else "measured_evaluation_only"
            )
        )
        interval_id = (
            f"int_{core.stable_id(session_id, 'speaker_state', index, category)}"
        )
        inventory.append(
            {
                "schema": "murmurmark.echo_adaptation_interval/v1",
                "id": interval_id,
                "session": session_id,
                "split": split,
                "category": category,
                "source_kind": "measured_speaker_state",
                "start": round(start, 6),
                "end": round(end, 6),
                "duration_sec": round(max(0.0, end - start), 6),
                "status": "included" if not reasons else "excluded",
                "usage": usage,
                "reasons": sorted(set(reasons)),
                "acoustic_mode": acoustic_mode,
                "evidence": {
                    "speaker_state": {
                        "row_index": index,
                        "state": state,
                        "confidence": row.get("confidence"),
                        "remote_db": row.get("remote_db"),
                        "mic_db": row.get("mic_db"),
                        "artifact": core.relative(paths["speaker_state"], ROOT),
                        "sha256": state_sha,
                    },
                    "me": me,
                    "remote": remote,
                    "dialogue_artifact": core.relative(paths["dialogue"], ROOT),
                    "dialogue_sha256": dialogue_sha,
                },
                "audio": metrics,
                "audio_sources": {
                    "mic": {
                        "path": core.relative(paths["mic"], ROOT),
                        "sha256": mic_sha,
                    },
                    "remote_aligned": {
                        "path": core.relative(paths["remote"], ROOT),
                        "sha256": remote_sha,
                    },
                },
            }
        )
    return inventory


def build_hard_probe_inventory(
    *,
    session_id: str,
    split: str,
    acoustic_mode: str,
    paths: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    if split != "hard_test":
        return []
    probe_path = paths["neural_probe"]
    require(probe_path.exists(), f"hard-test probe report missing: {probe_path}")
    probe = core.read_json(probe_path)
    require(probe.get("status") == "completed", f"incomplete hard probe: {probe_path}")
    mapping = {
        "opening_local": "opening_ack",
        "chronology_risk": "chronology_boundary",
        "protected_local": "protected_local",
        "double_talk": "double_talk",
    }
    probe_sha = core.sha256(probe_path)
    mic_sha = core.sha256(paths["mic"])
    remote_sha = core.sha256(paths["remote"])
    rows: list[dict[str, Any]] = []
    exclusions = policy["intervals"]["exclusion"]
    for window in probe.get("windows") or []:
        source_category = str(window.get("category") or "")
        if source_category not in mapping:
            continue
        category = mapping[source_category]
        start = float(window.get("start", 0.0))
        end = float(window.get("end", start))
        reasons: list[str] = []
        local_text = core.text_evidence(
            window.get("reference_text") or window.get("baseline_text")
        )
        remote_text = core.text_evidence(window.get("remote_text"))
        if category in {"opening_ack", "protected_local", "chronology_boundary", "double_talk"}:
            if not local_text["present"]:
                reasons.append("local_words_not_independently_confirmed")
        mic_audio = core.read_audio_slice(paths["mic"], start, end)
        remote_audio = core.read_audio_slice(paths["remote"], start, end)
        metrics = core.audio_metrics(mic_audio, remote_audio)
        if not metrics["finite"]:
            reasons.append("non_finite_audio")
        if metrics["mic_peak"] > float(exclusions["maximum_abs_peak"]):
            reasons.append("mic_peak_above_threshold")
        if metrics["remote_peak"] > float(exclusions["maximum_abs_peak"]):
            reasons.append("remote_peak_above_threshold")
        if metrics["mic_clipped_ratio"] > float(
            exclusions["maximum_clipped_sample_ratio"]
        ):
            reasons.append("mic_clipping")
        if metrics["remote_clipped_ratio"] > float(
            exclusions["maximum_clipped_sample_ratio"]
        ):
            reasons.append("remote_clipping")
        rows.append(
            {
                "schema": "murmurmark.echo_adaptation_interval/v1",
                "id": f"hard_{core.stable_id(session_id, window.get('id'), category)}",
                "session": session_id,
                "split": split,
                "category": category,
                "source_kind": "neural_hard_probe",
                "source_category": source_category,
                "start": round(start, 6),
                "end": round(end, 6),
                "duration_sec": round(max(0.0, end - start), 6),
                "status": "included" if not reasons else "excluded",
                "usage": "hard_evaluation",
                "reasons": sorted(set(reasons)),
                "acoustic_mode": acoustic_mode,
                "evidence": {
                    "probe_id": window.get("id"),
                    "source_artifact": core.relative(probe_path, ROOT),
                    "source_sha256": probe_sha,
                    "source_row_id": window.get("source_row_id"),
                    "source_row_artifact": window.get("source_artifact"),
                    "local_text": local_text,
                    "remote_text": remote_text,
                },
                "audio": metrics,
                "audio_sources": {
                    "mic": {
                        "path": core.relative(paths["mic"], ROOT),
                        "sha256": mic_sha,
                    },
                    "remote_aligned": {
                        "path": core.relative(paths["remote"], ROOT),
                        "sha256": remote_sha,
                    },
                },
            }
        )
    return rows


def select_intervals(
    rows: Iterable[dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    maximum = int(
        policy["materialization"]["maximum_intervals_per_category_per_session"]
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["status"] != "included":
            continue
        grouped[(row["session"], row["category"])].append(row)
    selected: list[dict[str, Any]] = []
    for key in sorted(grouped):
        candidates = sorted(
            grouped[key],
            key=lambda row: core.digest_json(
                {
                    "id": row["id"],
                    "start": row["start"],
                    "end": row["end"],
                }
            ),
        )
        selected.extend(candidates[:maximum])
    return selected


def artifact_row(
    path: Path,
    output_root: Path,
    *,
    expected_samples: int,
) -> dict[str, Any]:
    info = core.fingerprint(path, output_root)
    with sf.SoundFile(path) as handle:
        info.update(
            {
                "sample_rate": handle.samplerate,
                "channels": handle.channels,
                "samples": handle.frames,
                "duration_sec": round(handle.frames / handle.samplerate, 6),
                "expected_samples": expected_samples,
            }
        )
    return info


def materialize_sources_and_pairs(
    *,
    selected: list[dict[str, Any]],
    session_paths: dict[str, dict[str, Any]],
    output_root: Path,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    audio_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    measured: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for interval in selected:
        session_id = interval["session"]
        paths = session_paths[session_id]
        start = float(interval["start"])
        end = float(interval["end"])
        mic = core.read_audio_slice(paths["mic"], start, end)
        remote = core.read_audio_slice(paths["remote"], start, end)
        audio_cache[interval["id"]] = (mic, remote)
        split = interval["split"]
        category = interval["category"]
        base = (
            output_root
            / "examples"
            / split
            / session_id
            / category
            / interval["id"]
        )
        mic_name = "target.wav" if category == "local_only" else "measured_mic.wav"
        mic_path = base / mic_name
        remote_path = base / "remote_reference.wav"
        core.write_audio(mic_path, mic)
        core.write_audio(remote_path, remote)
        expected_samples = mic.size
        kind = {
            "local_only": "measured_local_reference",
            "remote_only": "measured_remote_echo",
            "double_talk": "measured_double_talk_evaluation",
            "opening_ack": "measured_opening_evaluation",
            "chronology_boundary": "measured_chronology_evaluation",
            "protected_local": "measured_protected_local_evaluation",
        }[category]
        row = {
            "schema": "murmurmark.echo_adaptation_supervision/v1",
            "id": f"sup_{core.stable_id(interval['id'], kind)}",
            "kind": kind,
            "status": "materialized",
            "split": split,
            "session_ids": [session_id],
            "source_interval_ids": [interval["id"]],
            "paired_target_available": category == "local_only",
            "training_eligible": (
                split in {"train", "dev"}
                and category in {"local_only", "remote_only"}
            ),
            "artifacts": {
                "mic": artifact_row(
                    mic_path,
                    output_root,
                    expected_samples=expected_samples,
                ),
                "remote_reference": artifact_row(
                    remote_path,
                    output_root,
                    expected_samples=expected_samples,
                ),
            },
            "audio": core.audio_metrics(mic, remote),
        }
        rows.append(row)
        if split in {"train", "dev"} and category in {"local_only", "remote_only"}:
            measured[(session_id, category)].append(interval)

    max_pairs = int(policy["materialization"]["maximum_synthetic_pairs_per_session"])
    gain_db = float(policy["materialization"]["synthetic_echo_gain_db"])
    maximum_peak = float(policy["intervals"]["exclusion"]["maximum_abs_peak"])
    for session_id in sorted({key[0] for key in measured}):
        local_rows = measured.get((session_id, "local_only"), [])
        remote_rows = measured.get((session_id, "remote_only"), [])
        if not local_rows or not remote_rows:
            continue
        pair_count = min(max_pairs, len(local_rows), len(remote_rows))
        for index in range(pair_count):
            local_row = local_rows[index]
            remote_row = remote_rows[(index * 7) % len(remote_rows)]
            target, local_remote = audio_cache[local_row["id"]]
            measured_echo, far_end = audio_cache[remote_row["id"]]
            pair_audio, report = core.make_synthetic_pair(
                target,
                measured_echo,
                gain_db=gain_db,
                maximum_peak=maximum_peak,
            )
            pair_id = (
                f"pair_{core.stable_id(session_id, local_row['id'], remote_row['id'])}"
            )
            base = (
                output_root
                / "examples"
                / local_row["split"]
                / session_id
                / "synthetic"
                / pair_id
            )
            pair_row: dict[str, Any] = {
                "schema": "murmurmark.echo_adaptation_supervision/v1",
                "id": pair_id,
                "kind": "synthetic_pair",
                "status": "materialized" if pair_audio is not None else "excluded",
                "split": local_row["split"],
                "session_ids": [session_id],
                "source_interval_ids": [local_row["id"], remote_row["id"]],
                "paired_target_available": True,
                "training_eligible": pair_audio is not None,
                "synthetic": True,
                "measured_double_talk": False,
                "report": report,
                "source_checks": {
                    "target_remote_correlation": round(
                        abs(core.normalized_corr(target, local_remote)),
                        9,
                    ),
                    "echo_remote_correlation": round(
                        abs(core.normalized_corr(measured_echo, far_end)),
                        9,
                    ),
                },
                "artifacts": {},
            }
            if pair_audio is not None:
                artifact_audio = {
                    "mixture": pair_audio["mixture"],
                    "target": pair_audio["target"],
                    "echo_component": pair_audio["echo_component"],
                    "remote_reference": far_end[: pair_audio["target"].size],
                }
                for name, audio in artifact_audio.items():
                    path = base / f"{name}.wav"
                    core.write_audio(path, audio)
                    pair_row["artifacts"][name] = artifact_row(
                        path,
                        output_root,
                        expected_samples=pair_audio["target"].size,
                    )
            rows.append(pair_row)
    return sorted(rows, key=lambda row: row["id"])


def build_privacy_manifest(
    policy: dict[str, Any],
    supervision_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema": "murmurmark.echo_adaptation_privacy_licensing/v1",
        "profile": PROFILE,
        "processing": policy["privacy"]["processing"],
        "network_allowed": policy["privacy"]["network_allowed"],
        "training_scope": policy["privacy"]["training_scope"],
        "redistribution": {
            "raw_audio": policy["privacy"]["raw_audio_redistribution"],
            "derived_audio": policy["privacy"]["derived_audio_redistribution"],
            "work_text": policy["privacy"]["work_text_redistribution"],
            "public_metadata": policy["privacy"]["public_metadata"],
        },
        "source_control": {
            "audio": policy["privacy"]["source_control_audio"],
            "work_text": policy["privacy"]["source_control_work_text"],
            "allowed": [
                "implementation",
                "policy_without_content",
                "aggregate_non_content_result",
            ],
        },
        "provenance": {
            "capture_owner": "local_user_controlled_sessions",
            "third_party_voice_redistribution": "not_granted",
            "cloud_upload": "forbidden",
            "model_training": "private_local_experiment_only",
        },
        "materialized_artifact_count": sum(
            len(row.get("artifacts") or {})
            for row in supervision_rows
            if row["status"] == "materialized"
        ),
        "passed": (
            policy["privacy"]["processing"] == "local_only"
            and policy["privacy"]["network_allowed"] is False
            and policy["privacy"]["source_control_audio"] == "forbidden"
            and policy["privacy"]["source_control_work_text"] == "forbidden"
        ),
    }


def coverage_report(
    supervision_rows: list[dict[str, Any]],
    hard_rows: list[dict[str, Any]],
    split_report: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    seconds: Counter[tuple[str, str]] = Counter()
    for row in supervision_rows:
        if row["status"] != "materialized":
            continue
        kind = str(row["kind"])
        split = str(row["split"])
        if kind == "measured_local_reference":
            seconds[(split, "local_only")] += float(row["audio"]["duration_sec"])
        elif kind == "measured_remote_echo":
            seconds[(split, "remote_only")] += float(row["audio"]["duration_sec"])
        elif kind == "synthetic_pair":
            seconds[(split, "synthetic_pair")] += float(
                row["report"]["duration_sec"]
            )
    included_hard = [row for row in hard_rows if row["status"] == "included"]
    hard_double = [
        row for row in included_hard if row["category"] == "double_talk"
    ]
    counts = Counter(row["category"] for row in included_hard)
    observed = {
        "train_local_only_seconds": round(seconds[("train", "local_only")], 6),
        "train_remote_only_seconds": round(seconds[("train", "remote_only")], 6),
        "train_synthetic_pair_seconds": round(
            seconds[("train", "synthetic_pair")],
            6,
        ),
        "dev_local_only_seconds": round(seconds[("dev", "local_only")], 6),
        "dev_remote_only_seconds": round(seconds[("dev", "remote_only")], 6),
        "dev_synthetic_pair_seconds": round(
            seconds[("dev", "synthetic_pair")],
            6,
        ),
        "hard_test_measured_double_talk_seconds": core.union_seconds(hard_double),
        "hard_test_protected_local_items": counts["protected_local"],
        "hard_test_chronology_items": counts["chronology_boundary"],
        "hard_test_opening_items": counts["opening_ack"],
    }
    thresholds = policy["coverage_gates"]
    gates = {
        key: float(observed[key.removeprefix("minimum_")]) >= float(value)
        for key, value in thresholds.items()
    }
    gates.update(
        {
            f"split_{name}": passed
            for name, passed in split_report["gates"].items()
        }
    )
    return {
        "observed": observed,
        "thresholds": thresholds,
        "gates": gates,
        "failed_gates": sorted(key for key, value in gates.items() if not value),
        "passed": all(gates.values()),
    }


def oracle_report(
    *,
    supervision_rows: list[dict[str, Any]],
    inventory_rows: list[dict[str, Any]],
    split_report: dict[str, Any],
    coverage: dict[str, Any],
    privacy: dict[str, Any],
    sources_unchanged: bool,
    policy: dict[str, Any],
) -> dict[str, Any]:
    materialized = [
        row for row in supervision_rows if row["status"] == "materialized"
    ]
    audio_artifacts = [
        artifact
        for row in materialized
        for artifact in (row.get("artifacts") or {}).values()
    ]
    synthetic = [row for row in materialized if row["kind"] == "synthetic_pair"]
    measured = [row for row in materialized if row["kind"] != "synthetic_pair"]
    reconstruction_limit = float(
        policy["oracle_gates"]["synthetic_reconstruction_max_abs_error"]
    )
    target_remote_limit = float(
        policy["oracle_gates"]["target_remote_correlation_max"]
    )
    mixture_target_min = float(
        policy["oracle_gates"]["mixture_contains_target_min_correlation"]
    )
    gates = {
        "exact_duration": all(
            artifact["sample_rate"] == core.SAMPLE_RATE
            and artifact["channels"] == 1
            and artifact["samples"] == artifact["expected_samples"]
            for artifact in audio_artifacts
        ),
        "finite_audio": all(
            (row.get("audio") or row.get("report") or {}).get("finite", True)
            for row in materialized
        ),
        "no_clipping": all(
            (
                float((row.get("audio") or {}).get("mic_peak", 0.0)) <= 0.995
                and float((row.get("audio") or {}).get("remote_peak", 0.0))
                <= 0.995
                and float((row.get("report") or {}).get("peak", 0.0)) <= 0.995
            )
            for row in materialized
        ),
        "session_disjoint": bool(split_report["gates"]["session_disjoint"]),
        "hard_test_isolated": all(
            row["split"] == "hard_test"
            for row in inventory_rows
            if row["source_kind"] == "neural_hard_probe"
        )
        and all(
            row["split"] != "hard_test" or not row["training_eligible"]
            for row in materialized
        ),
        "synthetic_and_measured_distinct": bool(synthetic)
        and all(row.get("synthetic") is True for row in synthetic)
        and all(row.get("synthetic") is not True for row in measured),
        "synthetic_reconstructable": bool(synthetic)
        and all(
            float(row["report"]["reconstruction_max_abs_error"])
            <= reconstruction_limit
            for row in synthetic
        ),
        "target_remote_leakage_bounded": bool(synthetic)
        and all(
            float(row["source_checks"]["target_remote_correlation"])
            <= target_remote_limit
            for row in synthetic
        ),
        "mixture_contains_target": bool(synthetic)
        and all(
            float(row["report"]["mixture_target_correlation"])
            >= mixture_target_min
            for row in synthetic
        ),
        "measured_double_talk_not_training_target": all(
            not (
                row["kind"] == "measured_double_talk_evaluation"
                and row["training_eligible"]
            )
            for row in materialized
        ),
        "raw_and_production_inputs_unchanged": sources_unchanged,
        "privacy_allows_local_training_only": bool(privacy["passed"]),
        "coverage": bool(coverage["passed"]),
    }
    return {
        "schema": "murmurmark.echo_adaptation_oracle/v1",
        "profile": PROFILE,
        "gates": gates,
        "failed_gates": sorted(key for key, value in gates.items() if not value),
        "coverage": coverage,
        "summary": {
            "inventory_rows": len(inventory_rows),
            "included_intervals": sum(
                row["status"] == "included" for row in inventory_rows
            ),
            "excluded_intervals": sum(
                row["status"] == "excluded" for row in inventory_rows
            ),
            "materialized_rows": len(materialized),
            "synthetic_pairs": len(synthetic),
            "audio_artifacts": len(audio_artifacts),
        },
        "passed": all(gates.values()),
    }


def render_decision_markdown(decision: dict[str, Any]) -> str:
    metrics = decision["coverage"]["observed"]
    lines = [
        "# Speaker-Preserving Echo Adaptation Corpus v1",
        "",
        f"Decision: **{decision['decision']}**",
        "",
        f"Decision fingerprint: `{decision['decision_fingerprint']}`",
        "",
        "Production remains `local_fir_role_masked`.",
        "",
        "## Coverage",
        "",
        "| Evidence | Observed |",
        "| --- | ---: |",
        f"| train local-only | {metrics['train_local_only_seconds']:.3f}s |",
        f"| train remote-only | {metrics['train_remote_only_seconds']:.3f}s |",
        f"| train synthetic pairs | {metrics['train_synthetic_pair_seconds']:.3f}s |",
        f"| dev local-only | {metrics['dev_local_only_seconds']:.3f}s |",
        f"| dev remote-only | {metrics['dev_remote_only_seconds']:.3f}s |",
        f"| dev synthetic pairs | {metrics['dev_synthetic_pair_seconds']:.3f}s |",
        (
            "| hard measured double-talk | "
            f"{metrics['hard_test_measured_double_talk_seconds']:.3f}s |"
        ),
        f"| hard protected-local | {metrics['hard_test_protected_local_items']} items |",
        f"| hard chronology | {metrics['hard_test_chronology_items']} items |",
        f"| hard opening | {metrics['hard_test_opening_items']} items |",
        "",
        "## Decision Reasons",
        "",
    ]
    if decision["reasons"]:
        lines.extend(f"- `{reason}`" for reason in decision["reasons"])
    else:
        lines.append("- all corpus and oracle gates passed")
    lines.extend(
        [
            "",
            "## Next",
            "",
            decision["next"],
            "",
            "No training or production profile change was performed.",
            "",
        ]
    )
    return "\n".join(lines)


def snapshot_sources(
    frozen_sessions: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    fingerprints: list[dict[str, Any]] = []
    paths: dict[str, dict[str, Any]] = {}
    for session_row in frozen_sessions:
        session_fingerprints, session_paths = verify_session_freeze(
            session_row,
            policy,
        )
        fingerprints.extend(session_fingerprints)
        paths[str(session_row["session"])] = session_paths
    fingerprints = sorted(
        {row["path"]: row for row in fingerprints}.values(),
        key=lambda row: row["path"],
    )
    return fingerprints, paths


def build_into(
    *,
    policy_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    policy = core.read_json(policy_path)
    require(policy.get("profile") == PROFILE, "unexpected corpus policy profile")
    policy_sha = core.sha256(policy_path)
    frozen, neural_report, frozen_sessions = load_and_verify_sources(policy)
    source_before, session_paths = snapshot_sources(frozen_sessions, policy)
    source_before_fingerprint = core.digest_json(source_before)

    assignments = core.assign_session_splits(
        frozen_sessions,
        policy,
        policy["split"]["assignment_seed"],
    )
    split_report = core.validate_splits(assignments, policy)
    split_by_session = {
        row["session"]: row["split"] for row in assignments
    }
    mode_by_session = {
        str(row["session"]): str(
            row.get("acoustic_mode", {}).get("mode") or "uncertain"
        )
        for row in frozen_sessions
    }

    inventory: list[dict[str, Any]] = []
    for session_id in sorted(session_paths):
        inventory.extend(
            build_state_inventory(
                session_id=session_id,
                split=split_by_session[session_id],
                acoustic_mode=mode_by_session[session_id],
                paths=session_paths[session_id],
                policy=policy,
            )
        )
        inventory.extend(
            build_hard_probe_inventory(
                session_id=session_id,
                split=split_by_session[session_id],
                acoustic_mode=mode_by_session[session_id],
                paths=session_paths[session_id],
                policy=policy,
            )
        )
    inventory = sorted(inventory, key=lambda row: row["id"])
    selected = select_intervals(inventory, policy)
    supervision = materialize_sources_and_pairs(
        selected=selected,
        session_paths=session_paths,
        output_root=output_root,
        policy=policy,
    )
    hard_rows = [
        row
        for row in inventory
        if row["split"] == "hard_test"
        and row["category"]
        in {
            "double_talk",
            "opening_ack",
            "chronology_boundary",
            "protected_local",
        }
    ]
    hard_manifest = {
        "schema": "murmurmark.echo_adaptation_immutable_hard_test/v1",
        "profile": PROFILE,
        "sessions": policy["hard_test"]["sessions"],
        "training_forbidden": True,
        "items": hard_rows,
        "fingerprint": core.digest_json(hard_rows),
    }
    split_manifest = {
        "schema": "murmurmark.echo_adaptation_split_manifest/v1",
        "profile": PROFILE,
        "strategy": policy["split"]["strategy"],
        **split_report,
    }
    split_manifest["fingerprint"] = core.digest_json(
        {
            "strategy": split_manifest["strategy"],
            "assignments": split_manifest["assignments"],
            "gates": split_manifest["gates"],
        }
    )
    privacy = build_privacy_manifest(policy, supervision)

    core.write_json(output_root / "corpus_policy.json", policy)
    core.write_jsonl(output_root / "interval_inventory.jsonl", inventory)
    core.write_json(output_root / "split_manifest.json", split_manifest)
    core.write_json(output_root / "immutable_hard_test.json", hard_manifest)
    core.write_jsonl(output_root / "supervision_manifest.jsonl", supervision)
    core.write_json(
        output_root / "privacy_licensing_manifest.json",
        privacy,
    )

    source_after, _ = snapshot_sources(frozen_sessions, policy)
    sources_unchanged = source_after == source_before
    coverage = coverage_report(
        supervision,
        hard_rows,
        split_report,
        policy,
    )
    oracle = oracle_report(
        supervision_rows=supervision,
        inventory_rows=inventory,
        split_report=split_report,
        coverage=coverage,
        privacy=privacy,
        sources_unchanged=sources_unchanged,
        policy=policy,
    )

    generated_artifacts = {
        name: core.fingerprint(output_root / name, output_root)
        for name in (
            "corpus_policy.json",
            "interval_inventory.jsonl",
            "split_manifest.json",
            "immutable_hard_test.json",
            "supervision_manifest.jsonl",
            "privacy_licensing_manifest.json",
        )
    }
    example_rows = [
        artifact
        for row in supervision
        for artifact in (row.get("artifacts") or {}).values()
    ]
    corpus_manifest = {
        "schema": "murmurmark.echo_adaptation_corpus_manifest/v1",
        "profile": PROFILE,
        "generator": {
            "script": "build-speaker-preserving-echo-adaptation-corpus-v1.py",
            "version": GENERATOR_VERSION,
            "script_sha256": core.sha256(Path(__file__)),
            "core_sha256": core.sha256(
                Path(__file__).with_name("speaker_preserving_echo_corpus.py")
            ),
        },
        "policy": {
            "path": core.relative(policy_path, ROOT),
            "sha256": policy_sha,
        },
        "source": {
            "frozen_corpus_sha256": policy["source"]["frozen_corpus_sha256"],
            "neural_decision_fingerprint": neural_report["decision_fingerprint"],
            "input_snapshot_sha256": source_before_fingerprint,
            "inputs": source_before,
            "inputs_unchanged_after_build": sources_unchanged,
        },
        "splits": split_manifest["counts"],
        "inventory": {
            "rows": len(inventory),
            "included": sum(row["status"] == "included" for row in inventory),
            "excluded": sum(row["status"] == "excluded" for row in inventory),
            "by_category": dict(
                sorted(Counter(row["category"] for row in inventory).items())
            ),
        },
        "supervision": {
            "rows": len(supervision),
            "materialized": sum(
                row["status"] == "materialized" for row in supervision
            ),
            "by_kind": dict(
                sorted(Counter(row["kind"] for row in supervision).items())
            ),
            "examples": len(example_rows),
            "examples_fingerprint": core.digest_json(example_rows),
        },
        "artifacts": generated_artifacts,
        "authoritative_pipeline_changed": False,
    }
    corpus_manifest["corpus_fingerprint"] = core.digest_json(
        {
            "policy": corpus_manifest["policy"],
            "source": corpus_manifest["source"],
            "splits": corpus_manifest["splits"],
            "inventory": corpus_manifest["inventory"],
            "supervision": corpus_manifest["supervision"],
            "artifacts": corpus_manifest["artifacts"],
        }
    )
    core.write_json(output_root / "corpus_manifest.json", corpus_manifest)
    oracle["corpus_fingerprint"] = corpus_manifest["corpus_fingerprint"]
    core.write_json(output_root / "oracle_report.json", oracle)

    reasons = sorted(
        {
            *(f"coverage:{reason}" for reason in coverage["failed_gates"]),
            *(f"oracle:{reason}" for reason in oracle["failed_gates"]),
        }
    )
    decision_value = "READY_FOR_ADAPTATION" if not reasons else "DO_NOT_TRAIN"
    next_step = (
        "Speaker-Preserving Neural Echo v2 may begin as a separate shadow-only goal."
        if decision_value == "READY_FOR_ADAPTATION"
        else (
            "Neural adaptation is closed for this evidence scope; continue with "
            "Evidence Notes And Export v2 unless materially new supervision appears."
        )
    )
    decision_basis = {
        "policy_sha256": policy_sha,
        "corpus_manifest_sha256": core.sha256(
            output_root / "corpus_manifest.json"
        ),
        "split_manifest_sha256": core.sha256(output_root / "split_manifest.json"),
        "oracle_report_sha256": core.sha256(output_root / "oracle_report.json"),
        "privacy_manifest_sha256": core.sha256(
            output_root / "privacy_licensing_manifest.json"
        ),
        "hard_test_sha256": core.sha256(output_root / "immutable_hard_test.json"),
        "decision": decision_value,
        "reasons": reasons,
    }
    decision = {
        "schema": "murmurmark.echo_adaptation_corpus_decision/v1",
        "profile": PROFILE,
        "decision": decision_value,
        "reasons": reasons,
        "fallback": policy["source"]["production_fallback"],
        "coverage": coverage,
        "oracle_passed": oracle["passed"],
        "privacy_passed": privacy["passed"],
        "raw_and_production_inputs_unchanged": sources_unchanged,
        "authoritative_pipeline_changed": False,
        "training_performed": False,
        "next": next_step,
        "decision_basis": decision_basis,
        "decision_fingerprint": core.digest_json(decision_basis),
    }
    core.write_json(output_root / "corpus_decision.json", decision)
    (output_root / "corpus_decision.md").write_text(
        render_decision_markdown(decision),
        encoding="utf-8",
    )
    return decision


def atomic_build(policy_path: Path, output_root: Path) -> dict[str, Any]:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f".{output_root.name}.",
            dir=output_root.parent,
        )
    )
    try:
        decision = build_into(
            policy_path=policy_path,
            output_root=temporary,
        )
        old = output_root.with_name(f".{output_root.name}.old")
        if old.exists():
            shutil.rmtree(old)
        if output_root.exists():
            output_root.replace(old)
        temporary.replace(output_root)
        if old.exists():
            shutil.rmtree(old)
        return decision
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def comparable_files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): core.sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "replay_report.json"
    }


def replay(policy_path: Path, output_root: Path) -> dict[str, Any]:
    require(output_root.exists(), f"build output missing: {output_root}")
    temporary = Path(
        tempfile.mkdtemp(
            prefix=f"{output_root.name}-replay-",
            dir=os.environ.get("TMPDIR") or "/tmp",
        )
    )
    try:
        decision = build_into(
            policy_path=policy_path,
            output_root=temporary,
        )
        expected = comparable_files(output_root)
        observed = comparable_files(temporary)
        missing = sorted(set(expected) - set(observed))
        unexpected = sorted(set(observed) - set(expected))
        changed = sorted(
            path
            for path in set(expected) & set(observed)
            if expected[path] != observed[path]
        )
        report = {
            "schema": "murmurmark.echo_adaptation_corpus_replay/v1",
            "profile": PROFILE,
            "passed": not missing and not unexpected and not changed,
            "compared_files": len(expected),
            "missing": missing,
            "unexpected": unexpected,
            "changed": changed,
            "decision": decision["decision"],
            "decision_fingerprint": decision["decision_fingerprint"],
        }
        core.write_json(output_root / "replay_report.json", report)
        require(report["passed"], "corpus replay differs from frozen build")
        return report
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def inspect(output_root: Path) -> dict[str, Any]:
    decision_path = output_root / "corpus_decision.json"
    replay_path = output_root / "replay_report.json"
    require(decision_path.exists(), f"corpus decision missing: {decision_path}")
    decision = core.read_json(decision_path)
    replay_report = core.read_json(replay_path) if replay_path.exists() else None
    return {
        "decision": decision["decision"],
        "decision_fingerprint": decision["decision_fingerprint"],
        "reasons": decision["reasons"],
        "replay": replay_report,
        "next": decision["next"],
    }


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    output_root = args.output.expanduser().resolve()
    if args.command == "build":
        payload = atomic_build(policy_path, output_root)
    elif args.command == "replay":
        payload = replay(policy_path, output_root)
    else:
        payload = inspect(output_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
