#!/usr/bin/env python3
"""Seal and consume the one-shot v2.10 ordinary-session hard test."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-10-hard.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-10-hard"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
PASS_DECISION = "HARD_TEST_PASSED_V2_10"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V210 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-10.py",
    "murmurmark_spne_v210_hard_runtime",
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
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
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


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_hard_policy/v2.10":
        raise RuntimeError("unexpected v2.10 hard policy schema")
    checks = {}
    for key, hash_key in (
        ("selector_policy", "selector_policy_sha256"),
        ("selector_runtime", "selector_runtime_sha256"),
        ("hard_set", "hard_set_sha256"),
        ("v2_9_corpus_report", "v2_9_corpus_report_sha256"),
    ):
        path_value = policy_artifact(policy, key)
        checks[key] = path_value.is_file() and sha256(path_value) == policy.get(hash_key)
    V210.verify_policy(policy_artifact(policy, "selector_policy"))
    hard_set = read_json(policy_artifact(policy, "hard_set"))
    checks["hard_set_matches"] = hard_set.get("sessions") == policy.get("ordinary_sessions")
    prior = read_json(policy_artifact(policy, "v2_9_corpus_report"))
    checks["v2_9_rejected"] = prior.get("promotion", {}).get("decision") == "DO_NOT_PROMOTE"
    checks["zero_post_asr_credit"] = policy.get("post_asr_cleanup_promotion_credit") == 0
    if not all(checks.values()):
        raise RuntimeError(f"v2.10 hard policy verification failed: {checks}")
    return policy


def verify_manifest(output: Path) -> dict[str, Any]:
    manifest = read_json(output / "hard_manifest.json")
    if manifest.get("schema") != "murmurmark.speaker_preserving_neural_echo_hard_manifest/v2.10":
        raise RuntimeError("seal the v2.10 hard inputs first")
    if stable_digest(manifest.get("basis")) != manifest.get("fingerprint"):
        raise RuntimeError("v2.10 hard manifest fingerprint changed")
    for group in (manifest["basis"]["artifacts"], *[row["artifacts"] for row in manifest["basis"]["sessions"]]):
        for item in group:
            path = artifact_path(item)
            if not path.is_file() or sha256(path) != item["sha256"]:
                raise RuntimeError(f"v2.10 hard input changed: {path}")
    return manifest


def seal(args: argparse.Namespace) -> dict[str, Any]:
    policy = verify_policy(args.policy)
    artifacts = [
        args.policy,
        policy_artifact(policy, "selector_policy"),
        policy_artifact(policy, "selector_runtime"),
        policy_artifact(policy, "hard_set"),
        policy_artifact(policy, "v2_9_corpus_report"),
        Path(__file__),
        args.whisper_model,
    ]
    sessions = []
    for session_id in policy["ordinary_sessions"]:
        session = ROOT / "sessions" / session_id
        inputs = session_artifacts(session)
        missing = [relative(path) for path in inputs if not path.is_file()]
        if missing:
            raise RuntimeError(f"{session_id}: missing v2.10 hard inputs: {missing}")
        mode = read_json(session / "derived/preprocess/echo/local_fir_report.json").get(
            "acoustic_mode", {}
        ).get("mode")
        if mode != "speaker_playback":
            raise RuntimeError(f"{session_id}: expected speaker_playback, got {mode}")
        candidate = session / "derived/preprocess/speaker-preserving-neural-echo-v2-10"
        if candidate.exists():
            raise RuntimeError(f"{session_id}: v2.10 output exists before seal: {candidate}")
        sessions.append(
            {"session_id": session_id, "artifacts": [fingerprint(path) for path in inputs]}
        )
    basis = {
        "artifacts": [fingerprint(path) for path in artifacts],
        "sessions": sessions,
        "training_use": "forbidden",
        "threshold_tuning_use": "forbidden",
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_manifest/v2.10",
        "status": "sealed_before_v2_10_hard_test",
        "basis": basis,
        "fingerprint": stable_digest(basis),
    }
    destination = args.output / "hard_manifest.json"
    existing = read_json(destination)
    if existing and existing != payload:
        raise RuntimeError("v2.10 hard manifest already exists with different inputs")
    write_json(destination, payload)
    return payload


def lock(args: argparse.Namespace) -> dict[str, Any]:
    manifest = verify_manifest(args.output)
    basis = {
        "hard_manifest_sha256": sha256(args.output / "hard_manifest.json"),
        "hard_fingerprint": manifest["fingerprint"],
        "policy_sha256": sha256(args.policy),
        "selector_runtime_sha256": sha256(ROOT / "scripts/speaker-preserving-neural-echo-v2-10.py"),
        "evaluator_sha256": sha256(Path(__file__)),
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_candidate_lock/v2.10",
        "status": "locked_before_hard_test",
        "basis": basis,
        "fingerprint": stable_digest(basis),
    }
    destination = args.output / "candidate_lock.json"
    existing = read_json(destination)
    if existing and existing != payload:
        raise RuntimeError("v2.10 candidate lock already differs")
    write_json(destination, payload)
    unlock = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_unlock/v2.10",
        "attempts_allowed": 1,
        "attempts_consumed": 0,
        "candidate_lock_sha256": sha256(destination),
    }
    unlock_path = args.output / "hard_test_unlock.json"
    existing_unlock = read_json(unlock_path)
    if existing_unlock and existing_unlock != unlock:
        raise RuntimeError("v2.10 hard unlock already differs")
    write_json(unlock_path, unlock)
    return payload


def hard_test(args: argparse.Namespace) -> dict[str, Any]:
    policy = verify_policy(args.policy)
    manifest = verify_manifest(args.output)
    lock_payload = read_json(args.output / "candidate_lock.json")
    unlock_path = args.output / "hard_test_unlock.json"
    unlock = read_json(unlock_path)
    if lock_payload.get("basis", {}).get("evaluator_sha256") != sha256(Path(__file__)):
        raise RuntimeError("v2.10 evaluator changed after lock")
    if unlock.get("attempts_consumed") != 0:
        raise RuntimeError("v2.10 hard-test attempt already consumed")
    if sha256(args.output / "candidate_lock.json") != unlock.get("candidate_lock_sha256"):
        raise RuntimeError("v2.10 candidate lock changed")
    attempt_path = args.output / "hard_test_attempt.json"
    if attempt_path.exists():
        raise RuntimeError("v2.10 hard-test marker already exists")
    write_json(
        attempt_path,
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_hard_attempt/v2.10",
            "status": "started",
            "candidate_lock_fingerprint": lock_payload["fingerprint"],
        },
    )
    unlock["attempts_consumed"] = 1
    write_json(unlock_path, unlock)

    rows = []
    for frozen in manifest["basis"]["sessions"]:
        session = ROOT / "sessions" / frozen["session_id"]
        result = V210.run(
            SimpleNamespace(
                session=session,
                policy=policy_artifact(policy, "selector_policy"),
                whisper_model=args.whisper_model,
                refresh=False,
            )
        )
        candidate = result.get("status") == "candidate"
        metrics = result.get("source_runtime", {}).get("metrics", {})
        local_ratio = float(metrics.get("local_retention", {}).get("ratio") or 0.0)
        row_checks = {
            "terminal": result.get("status") in {"candidate", "fallback"},
            "candidate_or_exact_fallback": candidate or result.get("exact_fallback") is True,
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
                "status": result.get("status"),
                "reason": result.get("reason"),
                "checks": row_checks,
                "passed": all(row_checks.values()),
                "exact_fallback": result.get("exact_fallback") is True,
                "remote_supported_reduction_sec": float(
                    metrics.get("remote_like_reduction_sec") or 0.0
                )
                if candidate
                else 0.0,
                "remote_supported_token_reduction": int(
                    metrics.get("remote_supported_token_reduction") or 0
                )
                if candidate
                else 0,
                "local_retention_ratio": local_ratio if candidate else None,
                "audio_rollback": result.get("audio_rollback", {}),
                "selection_report": fingerprint(
                    session
                    / "derived/preprocess/speaker-preserving-neural-echo-v2-10/selection_report.json"
                ),
            }
        )
    candidates = [row for row in rows if row["status"] == "candidate"]
    fallbacks = [row for row in rows if row["status"] == "fallback"]
    reduction = round(sum(row["remote_supported_reduction_sec"] for row in candidates), 3)
    tokens = sum(row["remote_supported_token_reduction"] for row in candidates)
    gates = policy["hard_gates"]
    checks = {
        "all_sessions_passed": all(row["passed"] for row in rows),
        "candidate_sessions_min": len(candidates) >= int(gates["candidate_sessions_min"]),
        "remote_reduction_min": reduction >= float(gates["remote_reduction_sec_min"]),
        "remote_token_reduction_min": tokens >= int(gates["remote_token_reduction_min"]),
        "candidate_local_exact": all(row["local_retention_ratio"] == 1.0 for row in candidates),
        "fallbacks_exact": all(row["exact_fallback"] for row in fallbacks),
        "frozen_inputs_unchanged": True,
        "zero_post_asr_credit": policy.get("post_asr_cleanup_promotion_credit") == 0,
    }
    try:
        verify_manifest(args.output)
    except RuntimeError:
        checks["frozen_inputs_unchanged"] = False
    passed = all(checks.values())
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_evaluation/v2.10",
        "hard_fingerprint": manifest["fingerprint"],
        "candidate_lock_fingerprint": lock_payload["fingerprint"],
        "rows": rows,
        "aggregate": {
            "candidate_sessions": len(candidates),
            "fallback_sessions": len(fallbacks),
            "remote_supported_reduction_sec": reduction,
            "remote_supported_token_reduction": tokens,
        },
        "checks": checks,
        "passed": passed,
        "post_asr_cleanup_promotion_credit": 0,
    }
    write_json(args.output / "hard_evaluation.json", report)
    decision = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_decision/v2.10",
        "decision": PASS_DECISION if passed else "HARD_TEST_REJECTED_V2_10",
        "report_sha256": sha256(args.output / "hard_evaluation.json"),
        "candidate_lock_fingerprint": lock_payload["fingerprint"],
    }
    write_json(args.output / "hard_test_decision.json", decision)
    attempt = read_json(attempt_path)
    attempt.update({"status": "completed", "passed": passed})
    write_json(attempt_path, attempt)
    return decision


def verify(args: argparse.Namespace) -> dict[str, Any]:
    verify_policy(args.policy)
    manifest = verify_manifest(args.output)
    decision = read_json(args.output / "hard_test_decision.json")
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_verification/v2.10",
        "hard_fingerprint": manifest["fingerprint"],
        "decision": decision.get("decision"),
        "passed": decision.get("decision") == PASS_DECISION,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seal", "lock", "hard-test", "verify"))
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    args = parser.parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "seal":
        payload = seal(args)
    elif args.command == "lock":
        payload = lock(args)
    elif args.command == "hard-test":
        payload = hard_test(args)
    else:
        return 0 if verify(args).get("passed") else 7
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
