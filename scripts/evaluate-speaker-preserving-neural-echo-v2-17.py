#!/usr/bin/env python3
"""Requalify the immutable v2.15 echo algorithm under the current ASR runtime."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-17-evaluation.json"
HARD_OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-17-hard"
CORPUS_OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-17-corpus"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
HARD_PASS = "HARD_TEST_PASSED_V2_17"
HARD_REJECT = "HARD_TEST_REJECTED_V2_17"
PROMOTE = "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2"
SAFE_SPEAKER_DISPOSITIONS = {
    "applicable_candidate",
    "not_applicable_exact_fallback",
    "safety_exact_fallback",
}


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SELECTOR = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-17.py",
    "murmurmark_spne_v217_evaluation_selector",
)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": relative(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def artifact_path(row: dict[str, Any]) -> Path:
    path = Path(str(row["path"]))
    return path if path.is_absolute() else ROOT / path


def policy_artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def session_artifacts(session: Path) -> list[Path]:
    transcript = session / "derived/transcript-simple/whisper-cpp"
    return [
        session / "session.json",
        session / "audio/mic/000001.caf",
        session / "audio/remote/000001.caf",
        session / "derived/asr/mic.wav",
        session / "derived/preprocess/audio/remote_for_aec.wav",
        session / "derived/preprocess/echo/speaker_state.jsonl",
        session / "derived/preprocess/echo/local_fir_report.json",
        transcript / "raw/mic.json",
        transcript / "raw/remote.json",
        transcript / "resolved/clean_dialogue.shadow_v2.json",
        transcript / "resolved/quality_report.shadow_v2.json",
        transcript / "resolved/overlaps.shadow_v2.json",
        transcript / "resolved/transcript.shadow_v2.md",
        transcript / "resolved/repair_comparison.json",
        session / "derived/synthesis-simple/extractive/quality_verdict.json",
    ]


def materialize_missing_asr_exports(session: Path) -> list[dict[str, Any]]:
    """Restore reproducible ASR exports removed by retention before sealing."""

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to materialize frozen ASR exports")
    working_pairs = (
        (
            session / "audio/mic/000001.caf",
            session / "derived/preprocess/audio/mic_raw_for_asr.wav",
        ),
        (
            session / "audio/remote/000001.caf",
            session / "derived/preprocess/audio/remote_for_aec.wav",
        ),
    )
    rows: list[dict[str, Any]] = []
    for source, destination in working_pairs:
        if destination.is_file():
            rows.append(
                {
                    "path": relative(destination),
                    "status": "available_before_seal",
                    "source": relative(source),
                }
            )
            continue
        if not source.is_file():
            raise RuntimeError(f"working audio source is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_f32le",
                str(destination),
            ],
            check=True,
        )
        rows.append(
            {
                "path": relative(destination),
                "status": "available_before_seal",
                "source": relative(source),
            }
        )
    pairs = (
        (session / "derived/preprocess/audio/mic_for_asr.wav", session / "derived/asr/mic.wav"),
        (session / "audio/remote/000001.caf", session / "derived/asr/remote.wav"),
    )
    for source, destination in pairs:
        if destination.is_file():
            rows.append(
                {
                    "path": relative(destination),
                    "status": "available_before_seal",
                    "source": relative(source),
                }
            )
            continue
        if not source.is_file():
            raise RuntimeError(f"ASR export source is missing: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                str(destination),
            ],
            check=True,
        )
        rows.append(
            {
                "path": relative(destination),
                "status": "available_before_seal",
                "source": relative(source),
            }
        )
    return rows


def detected_mode(session: Path) -> str | None:
    verdict = read_json(
        session / "derived/synthesis-simple/extractive/quality_verdict.json"
    )
    no_speech = read_json(
        session / "derived/synthesis-simple/extractive/no_speech_evidence.json"
    )
    if (
        verdict.get("session_classification") == "verified_no_speech"
        and no_speech.get("status") == "verified_no_speech"
        and not no_speech.get("failures")
    ):
        return "no_speech_control"
    local_fir = read_json(session / "derived/preprocess/echo/local_fir_report.json")
    mode = local_fir.get("acoustic_mode", {}).get("mode")
    if isinstance(mode, str) and mode:
        return mode
    audit = read_json(
        session
        / "derived/audit/speaker-mode-hardening-v1/acoustic_mode_report.json"
    )
    value = audit.get("mode")
    return value if isinstance(value, str) and value else None


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_evaluation_policy/v2.17":
        raise RuntimeError("unexpected v2.17 evaluation policy schema")
    checks = {}
    for key, hash_key in (
        ("selector_policy", "selector_policy_sha256"),
        ("selector_runtime", "selector_runtime_sha256"),
        ("selector_audio_runtime", "selector_audio_runtime_sha256"),
        ("shadow_runtime", "shadow_runtime_sha256"),
        ("transcriber_runtime", "transcriber_runtime_sha256"),
        (
            "authoritative_asr_cache_runtime",
            "authoritative_asr_cache_runtime_sha256",
        ),
        ("hard_set", "hard_set_sha256"),
        ("corpus_set", "corpus_set_sha256"),
        ("evaluator", "evaluator_sha256"),
        ("prior_v2_16_hard_report", "prior_v2_16_hard_report_sha256"),
        ("prior_v2_16_hard_decision", "prior_v2_16_hard_decision_sha256"),
        ("prior_v2_16_corpus_report", "prior_v2_16_corpus_report_sha256"),
        ("prior_v2_16_corpus_decision", "prior_v2_16_corpus_decision_sha256"),
    ):
        artifact = policy_artifact(policy, key)
        checks[key] = artifact.is_file() and sha256(artifact) == policy.get(hash_key)
    SELECTOR.verify_policy(policy_artifact(policy, "selector_policy"))
    selector_policy = read_json(policy_artifact(policy, "selector_policy"))
    hard = read_json(policy_artifact(policy, "hard_set"))
    corpus = read_json(policy_artifact(policy, "corpus_set"))
    hard_ids = {row["id"] for row in hard.get("sessions", [])}
    corpus_ids = {row["id"] for row in corpus.get("sessions", [])}
    checks["sets_frozen"] = (
        hard.get("status") == "frozen_before_v2_17_requalification"
        and corpus.get("status") == "frozen_before_v2_17_requalification"
    )
    checks["sets_disjoint"] = bool(hard_ids) and bool(corpus_ids) and hard_ids.isdisjoint(corpus_ids)
    checks["policy_locked"] = policy.get("status") == "locked_before_one_shot_requalification"
    checks["selector_policy_locked"] = (
        selector_policy.get("status") == "locked_before_one_shot_requalification"
    )
    prior_hard = read_json(policy_artifact(policy, "prior_v2_16_hard_report"))
    prior_hard_decision = read_json(
        policy_artifact(policy, "prior_v2_16_hard_decision")
    )
    prior_corpus = read_json(policy_artifact(policy, "prior_v2_16_corpus_report"))
    prior_corpus_decision = read_json(
        policy_artifact(policy, "prior_v2_16_corpus_decision")
    )
    checks["v2_16_evidence_preserved"] = (
        prior_hard.get("passed") is True
        and prior_hard_decision.get("decision") == "HARD_TEST_PASSED_V2_16"
        and prior_hard_decision.get("report_sha256")
        == policy.get("prior_v2_16_hard_report_sha256")
        and prior_corpus.get("passed") is True
        and prior_corpus.get("promotion", {}).get("decision") == PROMOTE
        and prior_corpus_decision.get("decision") == PROMOTE
        and prior_corpus_decision.get("report_sha256")
        == policy.get("prior_v2_16_corpus_report_sha256")
    )
    checks["contract_change_only"] = policy.get("contract_change") == (
        "current_transcriber_and_authoritative_cache_requalification_without_audio_or_threshold_changes"
    )
    checks["algorithm_unchanged"] = (
        policy.get("algorithm_revision") == "speaker_preserving_neural_echo_v2_15"
        and policy.get("threshold_changes") == 0
    )
    checks["zero_post_asr_credit"] = policy.get("post_asr_cleanup_promotion_credit") == 0
    if not all(checks.values()):
        raise RuntimeError(f"v2.17 evaluation policy verification failed: {checks}")
    return policy


def manifest_path(output: Path) -> Path:
    return output / "evaluation_manifest.json"


def verify_manifest(output: Path, kind: str) -> dict[str, Any]:
    manifest = read_json(manifest_path(output))
    expected = f"murmurmark.speaker_preserving_neural_echo_{kind}_manifest/v2.17"
    if manifest.get("schema") != expected:
        raise RuntimeError(f"seal the v2.17 {kind} inputs first")
    if stable_digest(manifest.get("basis")) != manifest.get("fingerprint"):
        raise RuntimeError(f"v2.17 {kind} manifest fingerprint changed")
    groups = [manifest["basis"]["artifacts"]]
    groups.extend(row["artifacts"] for row in manifest["basis"]["sessions"])
    for group in groups:
        for item in group:
            path = artifact_path(item)
            if not path.is_file() or sha256(path) != item["sha256"]:
                raise RuntimeError(f"v2.17 {kind} input changed: {path}")
    return manifest


def seal(args: argparse.Namespace, kind: str) -> dict[str, Any]:
    policy = verify_policy(args.policy)
    set_key = "hard_set" if kind == "hard" else "corpus_set"
    set_payload = read_json(policy_artifact(policy, set_key))
    output = args.hard_output if kind == "hard" else args.corpus_output
    artifacts = [
        args.policy,
        policy_artifact(policy, "selector_policy"),
        policy_artifact(policy, "selector_runtime"),
        policy_artifact(policy, "selector_audio_runtime"),
        policy_artifact(policy, "shadow_runtime"),
        policy_artifact(policy, "transcriber_runtime"),
        policy_artifact(policy, "authoritative_asr_cache_runtime"),
        policy_artifact(policy, "prior_v2_16_hard_report"),
        policy_artifact(policy, "prior_v2_16_hard_decision"),
        policy_artifact(policy, "prior_v2_16_corpus_report"),
        policy_artifact(policy, "prior_v2_16_corpus_decision"),
        policy_artifact(policy, set_key),
        Path(__file__),
        args.whisper_model,
    ]
    sessions = []
    for row in set_payload["sessions"]:
        session = ROOT / "sessions" / row["id"]
        export_rows = materialize_missing_asr_exports(session)
        inputs = session_artifacts(session)
        if row["expected_mode"] == "no_speech_control":
            inputs.append(
                session
                / "derived/synthesis-simple/extractive/no_speech_evidence.json"
            )
        missing = [relative(path) for path in inputs if not path.is_file()]
        if missing:
            raise RuntimeError(f"{session.name}: missing v2.17 {kind} inputs: {missing}")
        mode = detected_mode(session)
        if mode != row["expected_mode"]:
            raise RuntimeError(
                f"{session.name}: expected {row['expected_mode']}, got {mode}"
            )
        candidate = session / "derived/preprocess/speaker-preserving-neural-echo-v2-17"
        if candidate.exists():
            raise RuntimeError(
                f"{session.name}: v2.17 selector output exists before {kind} seal"
            )
        sessions.append(
            {
                "session_id": session.name,
                "expected_mode": row["expected_mode"],
                "candidate_output_preexisted_at_seal": candidate.exists(),
                "asr_exports": export_rows,
                "artifacts": [fingerprint(path) for path in inputs],
            }
        )
    basis = {
        "artifacts": [fingerprint(path) for path in artifacts],
        "sessions": sessions,
        "development_reuse_allowed": kind == "corpus",
        "training_use": "forbidden",
        "threshold_tuning_use": "forbidden",
    }
    payload = {
        "schema": f"murmurmark.speaker_preserving_neural_echo_{kind}_manifest/v2.17",
        "status": f"sealed_before_v2_17_{kind}_requalification",
        "basis": basis,
        "fingerprint": stable_digest(basis),
    }
    destination = manifest_path(output)
    existing = read_json(destination)
    if existing and existing != payload:
        raise RuntimeError(f"v2.17 {kind} manifest already has different inputs")
    write_json(destination, payload)
    return payload


def lock_hard(args: argparse.Namespace) -> dict[str, Any]:
    manifest = verify_manifest(args.hard_output, "hard")
    basis = {
        "hard_manifest_sha256": sha256(manifest_path(args.hard_output)),
        "hard_fingerprint": manifest["fingerprint"],
        "policy_sha256": sha256(args.policy),
        "selector_runtime_sha256": sha256(
            ROOT / "scripts/speaker-preserving-neural-echo-v2-17.py"
        ),
        "evaluator_sha256": sha256(Path(__file__)),
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_candidate_lock/v2.17",
        "status": "locked_before_hard_test",
        "basis": basis,
        "fingerprint": stable_digest(basis),
    }
    destination = args.hard_output / "candidate_lock.json"
    existing = read_json(destination)
    if existing and existing != payload:
        raise RuntimeError("v2.17 candidate lock already differs")
    write_json(destination, payload)
    unlock = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_unlock/v2.17",
        "attempts_allowed": 1,
        "attempts_consumed": 0,
        "candidate_lock_sha256": sha256(destination),
    }
    unlock_path = args.hard_output / "hard_test_unlock.json"
    existing_unlock = read_json(unlock_path)
    if existing_unlock and existing_unlock != unlock:
        raise RuntimeError("v2.17 hard unlock already differs")
    write_json(unlock_path, unlock)
    return payload


def evaluate_sessions(
    args: argparse.Namespace, manifest: dict[str, Any]
) -> list[dict[str, Any]]:
    policy = verify_policy(args.policy)
    rows: list[dict[str, Any]] = []
    for frozen in manifest["basis"]["sessions"]:
        session = ROOT / "sessions" / frozen["session_id"]
        started = time.monotonic()
        result = SELECTOR.run(
            SimpleNamespace(
                session=session,
                policy=policy_artifact(policy, "selector_policy"),
                whisper_model=args.whisper_model,
                refresh=True,
            )
        )
        elapsed = time.monotonic() - started
        duration = float(
            read_json(session / "session.json")
            .get("health", {})
            .get("actual_duration_sec")
            or 0.0
        )
        candidate = result.get("status") == "candidate"
        expected_mode = frozen["expected_mode"]
        applicability = result.get("applicability", {}).get("classification")
        metrics = result.get("source_runtime", {}).get("metrics", {})
        local_ratio = float(metrics.get("local_retention", {}).get("ratio") or 0.0)
        checks = {
            "terminal": result.get("status") in {"candidate", "fallback"},
            "candidate_or_exact_fallback": candidate or result.get("exact_fallback") is True,
            "speaker_candidate_or_fallback": expected_mode != "speaker_playback"
            or result.get("status") in {"candidate", "fallback"},
            "speaker_terminal_disposition": expected_mode != "speaker_playback"
            or applicability in SAFE_SPEAKER_DISPOSITIONS,
            "headphones_exact_fallback": expected_mode != "headphones_or_low_leak"
            or (not candidate and result.get("exact_fallback") is True),
            "no_speech_exact_fallback": expected_mode != "no_speech_control"
            or (not candidate and result.get("exact_fallback") is True),
            "candidate_primary_asr": not candidate
            or result.get("candidate_audio_is_primary_whisper_input") is True,
            "candidate_exact_local": not candidate or local_ratio == 1.0,
            "candidate_full_shadow": not candidate
            or result.get("full_shadow", {}).get("passed") is True,
            "candidate_outcome_profile": not candidate
            or result.get("full_shadow", {}).get("comparison_gate_profile")
            == "speaker_preserving_echo_v2",
            "zero_post_asr_credit": result.get("post_asr_cleanup_promotion_credit") == 0,
        }
        rows.append(
            {
                "session_id": session.name,
                "expected_mode": expected_mode,
                "status": result.get("status"),
                "reason": result.get("reason"),
                "applicability": applicability,
                "checks": checks,
                "passed": all(checks.values()),
                "exact_fallback": result.get("exact_fallback") is True,
                "remote_supported_reduction_sec": round(
                    float(metrics.get("remote_like_reduction_sec") or 0.0), 3
                )
                if candidate
                else 0.0,
                "remote_supported_token_reduction": int(
                    metrics.get("remote_supported_token_reduction") or 0
                )
                if candidate
                else 0,
                "local_retention_ratio": local_ratio if candidate else None,
                "selector_runtime_sec": round(elapsed, 3),
                "selector_runtime_factor": round(elapsed / duration, 6)
                if duration > 0
                else None,
                "audio_rollback": result.get("audio_rollback", {}),
                "selection_report": fingerprint(
                    session
                    / "derived/preprocess/speaker-preserving-neural-echo-v2-17/selection_report.json"
                ),
            }
        )
    return rows


def aggregate_checks(
    rows: list[dict[str, Any]], gates: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    speaker = [row for row in rows if row["expected_mode"] == "speaker_playback"]
    headphones = [
        row for row in rows if row["expected_mode"] == "headphones_or_low_leak"
    ]
    no_speech = [row for row in rows if row["expected_mode"] == "no_speech_control"]
    candidates = [row for row in speaker if row["status"] == "candidate"]
    fallbacks = [row for row in rows if row["status"] == "fallback"]
    terminal_speaker = [
        row
        for row in speaker
        if row.get("applicability") in SAFE_SPEAKER_DISPOSITIONS
    ]
    reduction = round(sum(row["remote_supported_reduction_sec"] for row in candidates), 3)
    tokens = sum(row["remote_supported_token_reduction"] for row in candidates)
    runtime_max = max(
        (float(row["selector_runtime_factor"] or 0.0) for row in rows), default=0.0
    )
    checks: dict[str, bool] = {
        "all_sessions_passed": all(row["passed"] for row in rows),
        "candidate_local_exact": all(row["local_retention_ratio"] == 1.0 for row in candidates),
        "fallbacks_exact": all(row["exact_fallback"] for row in fallbacks),
        "headphones_exact_fallback": not gates.get("headphones_exact_fallback_required")
        or all(row["status"] == "fallback" and row["exact_fallback"] for row in headphones),
        "no_speech_exact_fallback": not gates.get("no_speech_exact_fallback_required")
        or bool(no_speech)
        and all(row["status"] == "fallback" and row["exact_fallback"] for row in no_speech),
        "selector_runtime_factor": runtime_max
        <= float(gates.get("selector_runtime_factor_max", 1.0)),
        "zero_post_asr_credit": gates.get("post_asr_cleanup_promotion_credit", 0) == 0,
    }
    if "speaker_terminal_dispositions_min" in gates:
        checks["speaker_terminal_dispositions_min"] = len(terminal_speaker) >= int(
            gates["speaker_terminal_dispositions_min"]
        )
    if "speaker_candidate_sessions_min" in gates:
        checks["speaker_candidate_sessions_min"] = len(candidates) >= int(
            gates["speaker_candidate_sessions_min"]
        )
    if "remote_reduction_sec_min" in gates:
        checks["remote_reduction_min"] = reduction >= float(
            gates["remote_reduction_sec_min"]
        )
    if "remote_token_reduction_min" in gates:
        checks["remote_token_reduction_min"] = tokens >= int(
            gates["remote_token_reduction_min"]
        )
    aggregate = {
        "speaker_sessions": len(speaker),
        "headphones_sessions": len(headphones),
        "no_speech_sessions": len(no_speech),
        "candidate_sessions": len(candidates),
        "terminal_speaker_sessions": len(terminal_speaker),
        "safety_fallback_speaker_sessions": sum(
            row.get("applicability") == "safety_exact_fallback"
            for row in speaker
        ),
        "fallback_sessions": len(fallbacks),
        "remote_supported_reduction_sec": reduction,
        "remote_supported_token_reduction": tokens,
        "selector_runtime_factor_max": round(runtime_max, 6),
    }
    return checks, aggregate


def run_hard(args: argparse.Namespace) -> dict[str, Any]:
    policy = verify_policy(args.policy)
    manifest = verify_manifest(args.hard_output, "hard")
    lock = read_json(args.hard_output / "candidate_lock.json")
    unlock_path = args.hard_output / "hard_test_unlock.json"
    unlock = read_json(unlock_path)
    if lock.get("basis", {}).get("evaluator_sha256") != sha256(Path(__file__)):
        raise RuntimeError("v2.17 evaluator changed after lock")
    if unlock.get("attempts_consumed") != 0:
        raise RuntimeError("v2.17 hard-test attempt already consumed")
    if sha256(args.hard_output / "candidate_lock.json") != unlock.get("candidate_lock_sha256"):
        raise RuntimeError("v2.17 candidate lock changed")
    attempt_path = args.hard_output / "hard_test_attempt.json"
    if attempt_path.exists():
        raise RuntimeError("v2.17 hard-test marker already exists")
    write_json(
        attempt_path,
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_hard_attempt/v2.17",
            "status": "started",
            "candidate_lock_fingerprint": lock["fingerprint"],
        },
    )
    unlock["attempts_consumed"] = 1
    write_json(unlock_path, unlock)
    rows = evaluate_sessions(args, manifest)
    gates = policy["hard_gates"]
    checks, aggregate = aggregate_checks(rows, gates)
    try:
        verify_manifest(args.hard_output, "hard")
    except RuntimeError:
        checks["frozen_inputs_unchanged"] = False
    else:
        checks["frozen_inputs_unchanged"] = True
    passed = all(checks.values())
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_evaluation/v2.17",
        "hard_fingerprint": manifest["fingerprint"],
        "candidate_lock_fingerprint": lock["fingerprint"],
        "rows": rows,
        "aggregate": aggregate,
        "checks": checks,
        "passed": passed,
        "post_asr_cleanup_promotion_credit": 0,
    }
    report_path = args.hard_output / "hard_evaluation.json"
    write_json(report_path, report)
    decision = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_decision/v2.17",
        "decision": HARD_PASS if passed else HARD_REJECT,
        "report_sha256": sha256(report_path),
        "candidate_lock_fingerprint": lock["fingerprint"],
    }
    write_json(args.hard_output / "hard_test_decision.json", decision)
    attempt = read_json(attempt_path)
    attempt.update({"status": "completed", "passed": passed})
    write_json(attempt_path, attempt)
    return decision


def run_corpus(args: argparse.Namespace) -> dict[str, Any]:
    policy = verify_policy(args.policy)
    hard = read_json(args.hard_output / "hard_test_decision.json")
    if hard.get("decision") != HARD_PASS:
        raise RuntimeError("v2.17 corpus is blocked until the hard test passes")
    hard_report_path = args.hard_output / "hard_evaluation.json"
    if (
        not hard_report_path.is_file()
        or hard.get("report_sha256") != sha256(hard_report_path)
        or read_json(hard_report_path).get("passed") is not True
    ):
        raise RuntimeError("v2.17 hard-test evidence is missing or changed")
    verify_manifest(args.hard_output, "hard")
    manifest = verify_manifest(args.corpus_output, "corpus")
    rows = evaluate_sessions(args, manifest)
    gates = policy["promotion_gates"]
    checks, aggregate = aggregate_checks(rows, gates)
    checks["hard_test_passed"] = True
    checks["post_asr_cleanup_zero"] = gates.get("post_asr_cleanup_promotion_credit") == 0
    try:
        verify_manifest(args.corpus_output, "corpus")
    except RuntimeError:
        checks["frozen_inputs_unchanged"] = False
    else:
        checks["frozen_inputs_unchanged"] = True
    passed = all(checks.values())
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_report/v2.17",
        "corpus_fingerprint": manifest["fingerprint"],
        "hard_decision": hard["decision"],
        "rows": rows,
        "aggregate": aggregate,
        "checks": checks,
        "passed": passed,
        "promotion": {"decision": PROMOTE if passed else "DO_NOT_PROMOTE"},
        "post_asr_cleanup_promotion_credit": 0,
    }
    report_path = args.corpus_output / "corpus_report.json"
    write_json(report_path, report)
    write_json(
        args.corpus_output / "promotion_decision.json",
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_promotion_decision/v2.17",
            "decision": report["promotion"]["decision"],
            "report_sha256": sha256(report_path),
            "corpus_fingerprint": manifest["fingerprint"],
        },
    )
    return report


def verify_result(args: argparse.Namespace, kind: str) -> dict[str, Any]:
    verify_policy(args.policy)
    output = args.hard_output if kind == "hard" else args.corpus_output
    manifest = verify_manifest(output, kind)
    if kind == "hard":
        decision_payload = read_json(output / "hard_test_decision.json")
        decision = decision_payload.get("decision")
        expected = HARD_PASS
        report_path = output / "hard_evaluation.json"
    else:
        decision_payload = read_json(output / "promotion_decision.json")
        decision = decision_payload.get("decision")
        expected = PROMOTE
        report_path = output / "corpus_report.json"
    checks = {
        "decision": decision == expected,
        "report_exists": report_path.is_file(),
        "report_hash": report_path.is_file()
        and decision_payload.get("report_sha256") == sha256(report_path),
        "report_passed": report_path.is_file()
        and read_json(report_path).get("passed") is True,
    }
    payload = {
        "schema": f"murmurmark.speaker_preserving_neural_echo_{kind}_verification/v2.17",
        "fingerprint": manifest["fingerprint"],
        "decision": decision,
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "seal-hard",
            "lock-hard",
            "run-hard",
            "verify-hard",
            "seal-corpus",
            "run-corpus",
            "verify-corpus",
        ),
    )
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--hard-output", type=Path, default=HARD_OUTPUT)
    parser.add_argument("--corpus-output", type=Path, default=CORPUS_OUTPUT)
    parser.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    args = parser.parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.hard_output = args.hard_output.expanduser().resolve()
    args.corpus_output = args.corpus_output.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "seal-hard":
        payload = seal(args, "hard")
    elif args.command == "lock-hard":
        payload = lock_hard(args)
    elif args.command == "run-hard":
        payload = run_hard(args)
    elif args.command == "verify-hard":
        return 0 if verify_result(args, "hard")["passed"] else 7
    elif args.command == "seal-corpus":
        payload = seal(args, "corpus")
    elif args.command == "run-corpus":
        payload = run_corpus(args)
    else:
        return 0 if verify_result(args, "corpus")["passed"] else 8
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
