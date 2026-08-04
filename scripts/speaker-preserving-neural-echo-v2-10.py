#!/usr/bin/env python3
"""Select exact-local v2.10 audio or the bit-exact local-FIR baseline."""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-10.json"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-10"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V28 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-8.py",
    "murmurmark_spne_v210_selector_base",
)
AUDIO = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-10-audio.py",
    "murmurmark_spne_v210_audio_runtime",
)
SHADOW = load_module(
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-10.py",
    "murmurmark_spne_v210_shadow_runtime",
)


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
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.10":
        raise RuntimeError("unexpected v2.10 policy schema")
    pairs = (
        ("v2_7_policy", "audio_policy_sha256"),
        ("audio_runtime", "audio_runtime_sha256"),
        ("selector_base", "selector_base_sha256"),
        ("shadow_common", "shadow_common_sha256"),
        ("shadow_runtime", "shadow_runtime_sha256"),
        ("transcriber", "transcriber_sha256"),
        ("hard_set", "hard_set_sha256"),
    )
    checks = {
        key: policy_artifact(policy, key).is_file()
        and V28.sha256(policy_artifact(policy, key)) == policy.get(hash_key)
        for key, hash_key in pairs
    }
    AUDIO.verify_policy(policy_artifact(policy, "v2_7_policy"))
    hard_set = V28.read_json(policy_artifact(policy, "hard_set"))
    checks["hard_set_frozen"] = (
        hard_set.get("status") == "frozen_before_v2_10_implementation"
        and hard_set.get("training_use") == "forbidden"
        and hard_set.get("threshold_tuning_use") == "forbidden"
    )
    checks["outcome_gate_profile"] = (
        policy.get("comparison_gate_profile") == "speaker_preserving_echo_v2"
    )
    checks["zero_post_asr_credit"] = (
        policy.get("post_asr_cleanup_promotion_credit") == 0
    )
    if not all(checks.values()):
        raise RuntimeError(f"v2.10 policy verification failed: {checks}")
    return policy


def candidate_audio(session: Path) -> Path:
    return (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-10/"
        "candidate_clean_mic_pcm16.wav"
    )


def output_root(session: Path) -> Path:
    return session / "derived/preprocess" / OUTPUT_NAME


def overlap_rows(path: Path) -> list[dict[str, Any]]:
    payload = V28.read_json(path)
    rows = payload.get("overlaps") if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def overlap_match(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_start = float(left.get("start") or 0.0)
    left_end = float(left.get("end") or 0.0)
    right_start = float(right.get("start") or 0.0)
    right_end = float(right.get("end") or 0.0)
    intersection = max(0.0, min(left_end, right_end) - max(left_start, right_start))
    return intersection / max(min(left_end - left_start, right_end - right_start), 0.001)


def chronology_rollback_chunks(
    session: Path, source: dict[str, Any], shadow: dict[str, Any]
) -> tuple[list[int], list[dict[str, Any]]]:
    gates = shadow.get("gates") if isinstance(shadow.get("gates"), dict) else {}
    failed = {key for key, value in gates.items() if value is not True}
    if "chronology_not_worse" not in failed or not failed.issubset(
        {"chronology_not_worse", "verdict_not_worse"}
    ):
        return [], []
    stage_value = shadow.get("stage")
    if not isinstance(stage_value, str) or not stage_value:
        return [], []
    stage = Path(stage_value)
    if not stage.is_absolute():
        stage = session / stage
    baseline = overlap_rows(
        session
        / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
    )
    candidate = overlap_rows(
        stage / "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json"
    )
    candidate_only = [
        row
        for row in candidate
        if float(row.get("duration_sec") or 0.0) > 2.0
        and not any(overlap_match(row, other) >= 0.8 for other in baseline)
    ]
    output = output_root(session)
    decisions = AUDIO.V27.read_jsonl(output / "diagnostic_chunk_decisions.jsonl")
    selected = AUDIO.V27.read_jsonl(output / "selected_windows.jsonl")
    selected_chunks = {
        int(row.get("diagnostic_chunk") or 0) for row in selected
    }
    rollback: set[int] = set()
    evidence: list[dict[str, Any]] = []
    for interval in candidate_only:
        start = float(interval.get("start") or 0.0)
        end = float(interval.get("end") or 0.0)
        matching = [
            int(row.get("chunk_index") or 0)
            for row in decisions
            if int(row.get("chunk_index") or 0) in selected_chunks
            and min(end, float(row.get("hard_end_sec") or 0.0))
            > max(start, float(row.get("hard_start_sec") or 0.0))
        ]
        if matching:
            rollback.update(matching)
            evidence.append(
                {
                    "start": start,
                    "end": end,
                    "duration_sec": round(end - start, 3),
                    "diagnostic_chunks": sorted(matching),
                }
            )
    return sorted(rollback), evidence


def archive_initial_attempt(
    session: Path,
    source: dict[str, Any],
    shadow: dict[str, Any],
    chunks: list[int],
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
        "schema": "murmurmark.speaker_preserving_neural_echo_audio_rollback/v2.10",
        "reason": "candidate_only_cross_role_overlap",
        "excluded_diagnostic_chunks": chunks,
        "candidate_only_overlap_evidence": evidence,
        "initial_audio_sha256": source.get("output", {})
        .get("candidate", {})
        .get("sha256"),
        "post_asr_cleanup_promotion_credit": 0,
    }
    V28.write_json(archive / "rollback_decision.json", payload)
    return payload


def run_audio(
    *,
    session: Path,
    policy: dict[str, Any],
    args: argparse.Namespace,
    dialogue: Path,
    excluded_chunks: list[int],
) -> dict[str, Any]:
    original_guard = AUDIO.me_guard_dialogue_path
    AUDIO.me_guard_dialogue_path = lambda _session: dialogue
    try:
        return AUDIO.run_session(
            SimpleNamespace(
                session=session,
                policy=policy_artifact(policy, "v2_7_policy"),
                output=ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-10",
                whisper_model=args.whisper_model,
                refresh=False,
                proposal_only=False,
                excluded_chunks=excluded_chunks,
            )
        )
    finally:
        AUDIO.me_guard_dialogue_path = original_guard


def run(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = output_root(session)
    baseline = V28.baseline_audio(session)
    dialogue = V28.baseline_dialogue(session)
    if not baseline.is_file():
        raise RuntimeError(f"baseline local-FIR audio is required: {baseline}")
    basis = {
        "baseline_audio": V28.fingerprint(baseline, session),
        "selector_runtime": V28.fingerprint(Path(__file__)),
    }
    if args.policy.is_file():
        basis["policy"] = V28.fingerprint(args.policy)
    if dialogue.is_file():
        basis["baseline_dialogue"] = V28.fingerprint(dialogue, session)
    if args.whisper_model.is_file():
        basis["whisper_model"] = V28.fingerprint(args.whisper_model)
    try:
        policy = verify_policy(args.policy)
        if not dialogue.is_file():
            raise RuntimeError(f"baseline shadow_v2 dialogue is required: {dialogue}")
        if not args.whisper_model.is_file():
            raise RuntimeError(f"whisper model is required: {args.whisper_model}")
    except Exception as error:
        payload = V28.fail_open(
            session=session,
            output=output,
            baseline=baseline,
            reason="preflight_failure",
            details={"type": type(error).__name__, "message": str(error)},
            basis=basis,
        )
        payload["schema"] = "murmurmark.speaker_preserving_neural_echo_selection/v2.10"
        V28.write_json(output / "selection_report.json", payload)
        return payload
    existing = V28.read_json(output / "selection_report.json")
    selected_path = output / "selected_clean_mic_pcm16.wav"
    if (
        not args.refresh
        and existing.get("basis") == basis
        and existing.get("status") in {"candidate", "fallback"}
        and selected_path.is_file()
        and existing.get("selected_audio", {}).get("sha256") == V28.sha256(selected_path)
    ):
        return existing

    source = run_audio(
        session=session,
        policy=policy,
        args=args,
        dialogue=dialogue,
        excluded_chunks=[],
    )
    source_checks = source.get("checks") if isinstance(source.get("checks"), dict) else {}
    checks = {
        "audio_runtime_candidate": source.get("status") == "candidate",
        "audio_runtime_checks_all_pass": bool(source_checks) and all(source_checks.values()),
        "candidate_audio_is_primary_whisper_input": source.get(
            "candidate_audio_is_primary_whisper_input"
        )
        is True,
        "exact_local_token_retention": float(
            source.get("metrics", {}).get("local_retention", {}).get("ratio") or 0.0
        )
        == 1.0,
    }
    shadow: dict[str, Any] = {}
    rollback: dict[str, Any] = {}
    if all(checks.values()):
        try:
            shadow = SHADOW.run(
                SimpleNamespace(
                    session=session,
                    whisper_model=args.whisper_model,
                    refresh=args.refresh,
                )
            )
            rollback_chunks, evidence = chronology_rollback_chunks(session, source, shadow)
            if rollback_chunks:
                rollback = archive_initial_attempt(
                    session, source, shadow, rollback_chunks, evidence
                )
                source = run_audio(
                    session=session,
                    policy=policy,
                    args=args,
                    dialogue=dialogue,
                    excluded_chunks=rollback_chunks,
                )
                source_checks = (
                    source.get("checks") if isinstance(source.get("checks"), dict) else {}
                )
                checks.update(
                    {
                        "audio_runtime_candidate": source.get("status") == "candidate",
                        "audio_runtime_checks_all_pass": bool(source_checks)
                        and all(source_checks.values()),
                        "candidate_audio_is_primary_whisper_input": source.get(
                            "candidate_audio_is_primary_whisper_input"
                        )
                        is True,
                        "exact_local_token_retention": float(
                            source.get("metrics", {})
                            .get("local_retention", {})
                            .get("ratio")
                            or 0.0
                        )
                        == 1.0,
                    }
                )
                if all(checks.values()):
                    shadow = SHADOW.run(
                        SimpleNamespace(
                            session=session,
                            whisper_model=args.whisper_model,
                            refresh=True,
                        )
                    )
            checks.update(V28.full_shadow_checks(shadow))
        except Exception as error:
            shadow = {
                "status": "failed",
                "error": {"type": type(error).__name__, "message": str(error)},
            }
            checks.update(
                {"full_shadow_passed": False, "full_shadow_gates_all_pass": False}
            )
    else:
        checks.update(
            {"full_shadow_passed": False, "full_shadow_gates_all_pass": False}
        )

    selected = all(checks.values())
    source_audio = candidate_audio(session) if selected else baseline
    V28.copy_selected(source_audio, selected_path)
    exact_fallback = not selected and V28.sha256(selected_path) == V28.sha256(baseline)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_selection/v2.10",
        "status": "candidate" if selected else "fallback",
        "reason": "all_pre_asr_and_full_shadow_gates_passed"
        if selected
        else "session_gate_failed_exact_fallback",
        "candidate": policy["candidate_revision"] if selected else None,
        "audio_candidate": policy["audio_candidate_revision"],
        "fallback": policy["fallback"],
        "basis": basis,
        "source_runtime": source,
        "full_shadow": shadow,
        "audio_rollback": rollback,
        "checks": checks,
        "failed_checks": sorted(key for key, value in checks.items() if not value),
        "exact_fallback": exact_fallback,
        "selected_audio": V28.fingerprint(selected_path, session),
        "selected_source_audio": V28.fingerprint(source_audio, session),
        "candidate_audio_is_primary_whisper_input": selected,
        "selection_contract": (
            "exact_local_audio_then_outcome_gated_profile_matched_shadow_v2_fail_open"
        ),
        "post_asr_cleanup_promotion_credit": 0,
    }
    payload["selection_fingerprint"] = V28.stable_digest(
        {
            "basis": basis,
            "status": payload["status"],
            "checks": checks,
            "selected_audio_sha256": payload["selected_audio"]["sha256"],
            "selection_contract": payload["selection_contract"],
        }
    )
    V28.write_json(output / "selection_report.json", payload)
    return payload


def verify(args: argparse.Namespace) -> dict[str, Any]:
    verify_policy(args.policy)
    session = args.session.expanduser().resolve()
    report = V28.read_json(output_root(session) / "selection_report.json")
    selected = output_root(session) / "selected_clean_mic_pcm16.wav"
    baseline = V28.baseline_audio(session)
    checks = {
        "report_schema": report.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_selection/v2.10",
        "terminal_status": report.get("status") in {"candidate", "fallback"},
        "selected_audio_exists": selected.is_file(),
        "selected_audio_hash": selected.is_file()
        and report.get("selected_audio", {}).get("sha256") == V28.sha256(selected),
        "fallback_exact": report.get("status") != "fallback"
        or (baseline.is_file() and V28.sha256(selected) == V28.sha256(baseline)),
        "candidate_full_shadow_passed": report.get("status") != "candidate"
        or report.get("full_shadow", {}).get("passed") is True,
        "candidate_outcome_profile": report.get("status") != "candidate"
        or report.get("full_shadow", {}).get("comparison_gate_profile")
        == "speaker_preserving_echo_v2",
        "candidate_exact_local": report.get("status") != "candidate"
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
        "schema": "murmurmark.speaker_preserving_neural_echo_verification/v2.10",
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
