#!/usr/bin/env python3
"""Select v2.14 audio after bounded iterative pre-ASR outcome rollback."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-14.json"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-14"
OUTCOME_WINDOW_CONTEXT_SEC = 3.0


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PARENT = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-11.py",
    "murmurmark_spne_v214_selector_parent",
)
AUDIO = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-14-audio.py",
    "murmurmark_spne_v214_audio_runtime",
)
SHADOW = load_module(
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-14.py",
    "murmurmark_spne_v214_shadow_runtime",
)
V28 = PARENT.V28


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    sub = value.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("session", type=Path)
    run_parser.add_argument("--refresh", action="store_true")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("session", type=Path)
    return value


def policy_artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = V28.read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.14":
        raise RuntimeError("unexpected v2.14 policy schema")
    pairs = (
        ("v2_7_policy", "audio_policy_sha256"),
        ("audio_runtime", "audio_runtime_sha256"),
        ("selector_parent", "selector_parent_sha256"),
        ("selector_runtime", "selector_runtime_sha256"),
        ("shadow_common", "shadow_common_sha256"),
        ("shadow_runtime", "shadow_runtime_sha256"),
        ("transcriber", "transcriber_sha256"),
        ("hard_set", "hard_set_sha256"),
        ("corpus_set", "corpus_set_sha256"),
        ("development_regressions", "development_regressions_sha256"),
        ("v2_13_hard_decision", "v2_13_hard_decision_sha256"),
        ("v2_13_hard_report", "v2_13_hard_report_sha256"),
    )
    checks = {
        key: policy_artifact(policy, key).is_file()
        and V28.sha256(policy_artifact(policy, key)) == policy.get(hash_key)
        for key, hash_key in pairs
    }
    AUDIO.V210.verify_policy(policy_artifact(policy, "v2_7_policy"))
    hard_set = V28.read_json(policy_artifact(policy, "hard_set"))
    corpus_set = V28.read_json(policy_artifact(policy, "corpus_set"))
    checks["hard_set_frozen"] = (
        hard_set.get("status") == "frozen_before_v2_14_implementation"
        and hard_set.get("training_use") == "forbidden"
        and hard_set.get("threshold_tuning_use") == "forbidden"
    )
    checks["corpus_set_frozen"] = (
        corpus_set.get("status") == "frozen_before_v2_14_implementation"
        and corpus_set.get("training_use") == "forbidden"
        and corpus_set.get("threshold_tuning_use") == "forbidden"
    )
    hard_ids = {row["id"] for row in hard_set.get("sessions", [])}
    corpus_ids = {row["id"] for row in corpus_set.get("sessions", [])}
    checks["evaluation_sets_disjoint"] = bool(hard_ids) and bool(corpus_ids) and hard_ids.isdisjoint(corpus_ids)
    checks["outcome_gate_profile"] = (
        policy.get("comparison_gate_profile") == "speaker_preserving_echo_v2"
    )
    checks["zero_post_asr_credit"] = (
        policy.get("post_asr_cleanup_promotion_credit") == 0
    )
    checks["policy_locked"] = (
        policy.get("status") == "locked_before_one_shot_hard_test"
    )
    checks["window_rollback_context"] = (
        float(policy.get("outcome_window_context_sec") or 0.0)
        == OUTCOME_WINDOW_CONTEXT_SEC
    )
    checks["v2_13_rejected"] = (
        V28.read_json(policy_artifact(policy, "v2_13_hard_decision")).get("decision")
        == "HARD_TEST_REJECTED_V2_13"
    )
    if not all(checks.values()):
        raise RuntimeError(f"v2.14 policy verification failed: {checks}")
    return policy


def output_root(session: Path) -> Path:
    return session / "derived/preprocess" / OUTPUT_NAME


def candidate_audio(session: Path) -> Path:
    return output_root(session) / "candidate_clean_mic_pcm16.wav"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def interval_match(left: tuple[float, float], right: tuple[float, float]) -> float:
    intersection = max(0.0, min(left[1], right[1]) - max(left[0], right[0]))
    return intersection / max(min(left[1] - left[0], right[1] - right[0]), 0.001)


def local_island_inventory(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for parent in read_jsonl(path):
        children = [
            child for child in parent.get("children", []) if isinstance(child, dict)
        ]
        for island_index, island in enumerate(parent.get("local_islands", []), start=1):
            if not isinstance(island, list) or len(island) != 2:
                continue
            start = float(island[0]) / 1000.0
            end = float(island[1]) / 1000.0
            if end > start:
                matching_children = []
                for child in children:
                    child_start = float(child.get("start_ms") or 0.0) / 1000.0
                    child_end = float(child.get("end_ms") or 0.0) / 1000.0
                    match = interval_match(
                        (start, end), (child_start, child_end)
                    )
                    matching_children.append((match, child))
                best_match, best_child = max(
                    matching_children,
                    key=lambda item: item[0],
                    default=(0.0, None),
                )
                rows.append(
                    {
                        "start": start,
                        "end": end,
                        "island_index": island_index,
                        "recovered": best_match >= 0.5,
                        "child_overlap_ratio": round(best_match, 6),
                        "text": str(best_child.get("text") or "")
                        if isinstance(best_child, dict)
                        else "",
                        "parent_candidate_id": parent.get("parent_candidate_id"),
                    }
                )
    return rows


def local_recall_regression_intervals(
    session: Path, stage: Path
) -> list[dict[str, Any]]:
    name = "timeline_repair_examples.shadow_v2.jsonl"
    baseline = local_island_inventory(
        session / "derived/transcript-simple/whisper-cpp/resolved" / name
    )
    candidate = local_island_inventory(
        stage / "derived/transcript-simple/whisper-cpp/resolved" / name
    )
    regressions: list[dict[str, Any]] = []

    for row in candidate:
        if row["recovered"]:
            continue
        matches = [
            (interval_match((row["start"], row["end"]), (other["start"], other["end"])), other)
            for other in baseline
        ]
        best, matched = max(matches, key=lambda item: item[0], default=(0.0, None))
        if best >= 0.5 and isinstance(matched, dict) and not matched["recovered"]:
            continue
        regressions.append(
            {
                **row,
                "type": "candidate_unrecovered_local_island",
                "best_baseline_overlap_ratio": round(best, 6),
                "matched_baseline_recovered": matched.get("recovered")
                if isinstance(matched, dict)
                else None,
            }
        )

    for row in baseline:
        if not row["recovered"]:
            continue
        interval = (row["start"], row["end"])
        matches = [
            (interval_match(interval, (other["start"], other["end"])), other)
            for other in candidate
        ]
        best, matched = max(matches, key=lambda item: item[0], default=(0.0, None))
        if best >= 0.5 and isinstance(matched, dict) and matched["recovered"]:
            continue
        regressions.append(
            {
                **row,
                "type": "lost_recovered_local_island",
                "best_candidate_overlap_ratio": round(best, 6),
                "matched_candidate_recovered": matched.get("recovered")
                if isinstance(matched, dict)
                else None,
            }
        )
    unique: dict[tuple[str, float, float], dict[str, Any]] = {}
    for row in regressions:
        key = (str(row["type"]), round(float(row["start"]), 3), round(float(row["end"]), 3))
        unique[key] = row
    return list(unique.values())


def candidate_only_review_intervals(
    session: Path, stage: Path
) -> list[dict[str, Any]]:
    name = "clean_dialogue.shadow_v2.json"
    baseline_payload = V28.read_json(
        session / "derived/transcript-simple/whisper-cpp/resolved" / name
    )
    candidate_payload = V28.read_json(
        stage / "derived/transcript-simple/whisper-cpp/resolved" / name
    )
    def needs_review(row: Any) -> bool:
        if not isinstance(row, dict):
            return False
        quality = row.get("quality")
        return isinstance(quality, dict) and quality.get("needs_review") is True

    baseline = [row for row in baseline_payload.get("utterances", []) if needs_review(row)]
    candidate = [row for row in candidate_payload.get("utterances", []) if needs_review(row)]
    rows: list[dict[str, Any]] = []
    for row in candidate:
        interval = (float(row.get("start") or 0.0), float(row.get("end") or 0.0))
        best = max(
            (
                interval_match(
                    interval,
                    (
                        float(other.get("start") or 0.0),
                        float(other.get("end") or 0.0),
                    ),
                )
                for other in baseline
                if other.get("role") == row.get("role")
            ),
            default=0.0,
        )
        if best < 0.8 and interval[1] > interval[0]:
            rows.append(
                {
                    "type": "candidate_only_needs_review_utterance",
                    "start": interval[0],
                    "end": interval[1],
                    "duration_sec": round(interval[1] - interval[0], 3),
                    "role": row.get("role"),
                    "utterance_id": row.get("id"),
                    "text_sha256": V28.stable_digest(str(row.get("text") or "")),
                    "best_baseline_review_overlap_ratio": round(best, 6),
                }
            )
    return rows


def outcome_rollback_windows(
    session: Path, source: dict[str, Any], shadow: dict[str, Any]
) -> tuple[list[str], list[dict[str, Any]]]:
    gates = shadow.get("gates") if isinstance(shadow.get("gates"), dict) else {}
    failed = {key for key, value in gates.items() if value is not True}
    actionable = {
        "chronology_not_worse",
        "local_recall_preserved",
        "needs_review_not_worse",
    }
    allowed = actionable | {"verdict_not_worse"}
    if not failed.intersection(actionable) or not failed.issubset(allowed):
        return [], []
    stage_value = shadow.get("stage")
    if not isinstance(stage_value, str) or not stage_value:
        return [], []
    stage = Path(stage_value)
    if not stage.is_absolute():
        stage = session / stage

    evidence: list[dict[str, Any]] = []
    if "chronology_not_worse" in failed:
        baseline = PARENT.PARENT.overlap_rows(
            session
            / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
        )
        candidate = PARENT.PARENT.overlap_rows(
            stage
            / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
        )
        for row in candidate:
            if float(row.get("duration_sec") or 0.0) <= 2.0:
                continue
            start = float(row.get("start") or 0.0)
            end = float(row.get("end") or 0.0)
            best = max(
                baseline,
                key=lambda other: PARENT.PARENT.overlap_match(row, other),
                default=None,
            )
            match = (
                PARENT.PARENT.overlap_match(row, best) if best is not None else 0.0
            )
            if best is None or match < 0.8:
                evidence.append(
                    {
                        "type": "candidate_only_cross_role_overlap",
                        "start": start,
                        "end": end,
                        "duration_sec": round(end - start, 3),
                    }
                )
                continue
            baseline_start = float(best.get("start") or 0.0)
            baseline_end = float(best.get("end") or 0.0)
            if start < baseline_start - 0.005:
                evidence.append(
                    {
                        "type": "candidate_extended_cross_role_overlap",
                        "start": start,
                        "end": baseline_start,
                        "duration_sec": round(baseline_start - start, 3),
                        "matched_baseline_start": baseline_start,
                        "matched_baseline_end": baseline_end,
                    }
                )
            if end > baseline_end + 0.005:
                evidence.append(
                    {
                        "type": "candidate_extended_cross_role_overlap",
                        "start": baseline_end,
                        "end": end,
                        "duration_sec": round(end - baseline_end, 3),
                        "matched_baseline_start": baseline_start,
                        "matched_baseline_end": baseline_end,
                    }
                )
    if "local_recall_preserved" in failed:
        evidence.extend(local_recall_regression_intervals(session, stage))
    if "needs_review_not_worse" in failed:
        evidence.extend(candidate_only_review_intervals(session, stage))

    output = output_root(session)
    selected = read_jsonl(output / "selected_windows.jsonl")
    rollback: set[str] = set()
    for item in evidence:
        start = float(item["start"])
        end = float(item["end"])
        matching = [
            row
            for row in selected
            if float(row.get("end") or 0.0) > start - OUTCOME_WINDOW_CONTEXT_SEC
            and float(row.get("start") or 0.0) < end + OUTCOME_WINDOW_CONTEXT_SEC
            and str(row.get("proposal_id") or "")
        ]
        item["rollback_context_sec"] = OUTCOME_WINDOW_CONTEXT_SEC
        item["rollback_windows"] = [
            {
                "proposal_id": str(row["proposal_id"]),
                "start": float(row["start"]),
                "end": float(row["end"]),
                "diagnostic_chunk": int(row.get("diagnostic_chunk") or 0),
            }
            for row in matching
        ]
        item["diagnostic_chunks"] = sorted(
            {int(row.get("diagnostic_chunk") or 0) for row in matching}
        )
        rollback.update(str(row["proposal_id"]) for row in matching)
    return sorted(rollback), [row for row in evidence if row["rollback_windows"]]


def archive_initial_attempt(
    session: Path,
    source: dict[str, Any],
    shadow: dict[str, Any],
    window_ids: list[str],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    output = output_root(session)
    archive = output / "full-shadow-audio-rollback"
    archive.mkdir(parents=True, exist_ok=True)
    V28.write_json(archive / "initial_audio_runtime_report.json", source)
    V28.write_json(archive / "initial_full_shadow_report.json", shadow)
    for name in ("selected_windows.jsonl", "diagnostic_chunk_decisions.jsonl"):
        path = output / name
        if path.is_file():
            shutil.copy2(path, archive / f"initial_{name}")
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_audio_rollback/v2.14",
        "reason": "outcome_regression_mapped_to_changed_pre_asr_windows",
        "excluded_window_ids": window_ids,
        "outcome_regression_evidence": evidence,
        "initial_audio_sha256": source.get("output", {})
        .get("candidate", {})
        .get("sha256"),
        "post_asr_cleanup_promotion_credit": 0,
    }
    V28.write_json(archive / "rollback_decision.json", payload)
    return payload


def audio_checks(source: dict[str, Any]) -> dict[str, bool]:
    source_checks = source.get("checks") if isinstance(source.get("checks"), dict) else {}
    return {
        "audio_runtime_candidate": source.get("status") == "candidate",
        "audio_runtime_checks_all_pass": bool(source_checks)
        and all(source_checks.values()),
        "candidate_audio_is_primary_whisper_input": source.get(
            "candidate_audio_is_primary_whisper_input"
        )
        is True,
        "exact_local_token_retention": float(
            source.get("metrics", {}).get("local_retention", {}).get("ratio")
            or 0.0
        )
        == 1.0,
    }


def run_audio(
    *,
    session: Path,
    policy: dict[str, Any],
    args: argparse.Namespace,
    dialogue: Path,
    excluded_chunks: list[str],
) -> dict[str, Any]:
    """Keep the parent selector API while rolling back stable window IDs."""

    original_guard = AUDIO.me_guard_dialogue_path
    AUDIO.me_guard_dialogue_path = lambda _session: dialogue
    try:
        return AUDIO.run_session(
            SimpleNamespace(
                session=session,
                policy=policy_artifact(policy, "v2_7_policy"),
                output=ROOT
                / "sessions/_reports/speaker-preserving-neural-echo-v2-14",
                whisper_model=args.whisper_model,
                refresh=False,
                proposal_only=False,
                excluded_window_ids=excluded_chunks,
            )
        )
    finally:
        AUDIO.me_guard_dialogue_path = original_guard


def archive_rollback_iteration(
    session: Path,
    *,
    pass_index: int,
    source: dict[str, Any],
    shadow: dict[str, Any],
    new_window_ids: list[str],
    cumulative_window_ids: list[str],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    archive = output_root(session) / "full-shadow-audio-rollback"
    iteration = archive / f"iteration-{pass_index:02d}"
    iteration.mkdir(parents=True, exist_ok=True)
    V28.write_json(iteration / "audio_runtime_report.json", source)
    V28.write_json(iteration / "full_shadow_report.json", shadow)
    for name in ("selected_windows.jsonl", "diagnostic_chunk_decisions.jsonl"):
        path = output_root(session) / name
        if path.is_file():
            shutil.copy2(path, iteration / name)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_rollback_iteration/v2.14",
        "pass": pass_index,
        "new_excluded_window_ids": new_window_ids,
        "cumulative_excluded_window_ids": cumulative_window_ids,
        "outcome_regression_evidence": evidence,
        "candidate_audio_sha256": source.get("output", {})
        .get("candidate", {})
        .get("sha256"),
        "post_asr_cleanup_promotion_credit": 0,
    }
    V28.write_json(iteration / "rollback_iteration.json", payload)
    return payload


def continue_iterative_rollback(
    args: argparse.Namespace, payload: dict[str, Any]
) -> dict[str, Any]:
    """Repeat precise window rollback until outcome gates pass or evidence runs out."""

    session = args.session.expanduser().resolve()
    output = output_root(session)
    baseline = V28.baseline_audio(session)
    dialogue = V28.baseline_dialogue(session)
    policy = verify_policy(args.policy)
    source = payload.get("source_runtime", {})
    shadow = payload.get("full_shadow", {})
    initial = payload.get("audio_rollback", {})
    cumulative = {
        str(value)
        for value in initial.get("excluded_window_ids", [])
        if isinstance(value, str) and value
    }
    existing_iterations = initial.get("iterations")
    iterations: list[dict[str, Any]] = (
        copy.deepcopy(existing_iterations)
        if isinstance(existing_iterations, list)
        else []
    )
    if cumulative and not iterations:
        iterations.append(
            {
                "schema": "murmurmark.speaker_preserving_neural_echo_rollback_iteration/v2.14",
                "pass": 1,
                "new_excluded_window_ids": sorted(cumulative),
                "cumulative_excluded_window_ids": sorted(cumulative),
                "outcome_regression_evidence": initial.get(
                    "outcome_regression_evidence", []
                ),
                "candidate_audio_sha256": initial.get("initial_audio_sha256"),
                "post_asr_cleanup_promotion_credit": 0,
            }
        )

    checks = audio_checks(source)
    checks.update(V28.full_shadow_checks(shadow))
    max_passes = int(policy.get("max_outcome_rollback_passes") or 4)
    termination = "initial_outcome_passed" if all(checks.values()) else "no_actionable_evidence"
    while not all(checks.values()) and len(iterations) < max_passes:
        rollback_window_ids, evidence = outcome_rollback_windows(
            session, source, shadow
        )
        new_window_ids = sorted(set(rollback_window_ids) - cumulative)
        if not new_window_ids:
            break
        cumulative.update(new_window_ids)
        pass_index = len(iterations) + 1
        iterations.append(
            archive_rollback_iteration(
                session,
                pass_index=pass_index,
                source=source,
                shadow=shadow,
                new_window_ids=new_window_ids,
                cumulative_window_ids=sorted(cumulative),
                evidence=evidence,
            )
        )
        source = PARENT.PARENT.run_audio(
            session=session,
            policy=policy,
            args=args,
            dialogue=dialogue,
            excluded_chunks=sorted(cumulative),
        )
        checks = audio_checks(source)
        if all(checks.values()):
            shadow = SHADOW.run(
                SimpleNamespace(
                    session=session,
                    whisper_model=args.whisper_model,
                    refresh=True,
                )
            )
            checks.update(V28.full_shadow_checks(shadow))
        else:
            shadow = {}
            checks.update(
                {"full_shadow_passed": False, "full_shadow_gates_all_pass": False}
            )
        termination = (
            "all_pre_asr_and_outcome_gates_passed"
            if all(checks.values())
            else "audio_candidate_exhausted"
            if source.get("status") != "candidate"
            else "rollback_pass_limit_reached"
            if len(iterations) >= max_passes
            else "no_actionable_evidence"
        )

    selected = all(checks.values())
    selected_path = output / "selected_clean_mic_pcm16.wav"
    source_audio = candidate_audio(session) if selected else baseline
    V28.copy_selected(source_audio, selected_path)
    exact_fallback = not selected and V28.sha256(selected_path) == V28.sha256(baseline)
    rollback = {
        "schema": "murmurmark.speaker_preserving_neural_echo_audio_rollback/v2.14",
        "strategy": "bounded_iterative_pre_asr_window_rollback",
        "max_passes": max_passes,
        "passes_completed": len(iterations),
        "excluded_window_ids": sorted(cumulative),
        "iterations": iterations,
        "termination": termination,
        "post_asr_cleanup_promotion_credit": 0,
    }
    V28.write_json(output / "full-shadow-audio-rollback/rollback_decision.json", rollback)
    payload.update(
        {
            "status": "candidate" if selected else "fallback",
            "reason": "all_pre_asr_and_full_shadow_gates_passed"
            if selected
            else "session_gate_failed_exact_fallback",
            "candidate": policy["candidate_revision"] if selected else None,
            "audio_candidate": policy["audio_candidate_revision"],
            "fallback": policy["fallback"],
            "source_runtime": source,
            "full_shadow": shadow,
            "audio_rollback": rollback,
            "checks": checks,
            "failed_checks": sorted(key for key, value in checks.items() if not value),
            "exact_fallback": exact_fallback,
            "selected_audio": V28.fingerprint(selected_path, session),
            "selected_source_audio": V28.fingerprint(source_audio, session),
            "candidate_audio_is_primary_whisper_input": selected,
        }
    )
    return payload


def configure_parent() -> None:
    PARENT.OUTPUT_NAME = OUTPUT_NAME
    PARENT.POLICY_PATH = POLICY_PATH
    PARENT.AUDIO = AUDIO
    PARENT.SHADOW = SHADOW
    PARENT.verify_policy = verify_policy
    PARENT.output_root = output_root
    PARENT.candidate_audio = candidate_audio
    PARENT.PARENT.run_audio = run_audio
    PARENT.chronology_rollback_chunks = outcome_rollback_windows
    PARENT.archive_initial_attempt = archive_initial_attempt
    PARENT.__file__ = str(Path(__file__).resolve())


def run(args: argparse.Namespace) -> dict[str, Any]:
    configure_parent()
    payload = PARENT.run(args)
    if (
        payload.get("status") != "candidate"
        and payload.get("reason") == "session_gate_failed_exact_fallback"
        and isinstance(payload.get("source_runtime"), dict)
        and payload.get("source_runtime")
    ):
        payload = continue_iterative_rollback(args, payload)
    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_selection/v2.14"
    payload["selection_contract"] = (
        "identity_bounded_candidate_asr_then_exact_pre_asr_window_rollback"
    )
    payload["post_asr_cleanup_promotion_credit"] = 0
    payload["selection_fingerprint"] = V28.stable_digest(
        {
            "basis": payload.get("basis"),
            "status": payload.get("status"),
            "checks": payload.get("checks"),
            "selected_audio_sha256": payload.get("selected_audio", {}).get("sha256"),
            "selection_contract": payload["selection_contract"],
        }
    )
    V28.write_json(output_root(args.session.expanduser().resolve()) / "selection_report.json", payload)
    return payload


def verify(args: argparse.Namespace) -> dict[str, Any]:
    verify_policy(args.policy)
    session = args.session.expanduser().resolve()
    report = V28.read_json(output_root(session) / "selection_report.json")
    selected = output_root(session) / "selected_clean_mic_pcm16.wav"
    baseline = V28.baseline_audio(session)
    candidate = report.get("status") == "candidate"
    checks = {
        "report_schema": report.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_selection/v2.14",
        "terminal_status": report.get("status") in {"candidate", "fallback"},
        "selected_audio_exists": selected.is_file(),
        "selected_audio_hash": selected.is_file()
        and report.get("selected_audio", {}).get("sha256") == V28.sha256(selected),
        "fallback_exact": candidate
        or (baseline.is_file() and V28.sha256(selected) == V28.sha256(baseline)),
        "candidate_full_shadow_passed": not candidate
        or report.get("full_shadow", {}).get("passed") is True,
        "candidate_outcome_profile": not candidate
        or report.get("full_shadow", {}).get("comparison_gate_profile")
        == "speaker_preserving_echo_v2",
        "candidate_exact_local": not candidate
        or float(
            report.get("source_runtime", {})
            .get("metrics", {})
            .get("local_retention", {})
            .get("ratio")
            or 0.0
        )
        == 1.0,
        "zero_post_asr_credit": report.get("post_asr_cleanup_promotion_credit") == 0,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_verification/v2.14",
        "session": session.name,
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "run":
        payload = run(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 0 if verify(args).get("passed") else 6


if __name__ == "__main__":
    raise SystemExit(main())
