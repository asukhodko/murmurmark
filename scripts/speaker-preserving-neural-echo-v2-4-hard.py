#!/usr/bin/env python3
"""Seal, lock, and consume the single Speaker-Preserving Neural Echo v2.4 hard test."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-4.json"
RUNTIME = ROOT / "scripts/speaker-preserving-neural-echo-v2-4.py"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-4-hard"
ENROLLMENT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-4"
DEV_SESSION = ROOT / "sessions/2026-07-31_16-02-38"
SUPERVISION = ROOT / "sessions/_reports/controlled-echo-supervision-v1/supervision_manifest.jsonl"


def load_runtime() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_spne_v24_hard_runtime", RUNTIME)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load runtime: {RUNTIME}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V24 = load_runtime()


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("command", choices=("seal", "lock", "hard-test", "verify"))
    value.add_argument("--policy", type=Path, default=POLICY)
    value.add_argument("--output", type=Path, default=OUTPUT)
    value.add_argument("--whisper-model", type=Path, default=V24.WHISPER_MODEL)
    return value


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


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


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": relative(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def ordinary_artifacts(session: Path) -> list[Path]:
    paths = V24.session_paths(session)
    return [
        paths[key]
        for key in (
            "baseline_audio",
            "remote_audio",
            "speaker_state",
            "baseline_mic_asr",
            "baseline_remote_asr",
            "reviewed_dialogue",
        )
    ]


def hard_rows() -> list[dict[str, Any]]:
    return [row for row in read_jsonl(SUPERVISION) if row.get("split") == "hard_test"]


def controlled_artifacts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    base = SUPERVISION.parent
    for row in rows:
        fields = ("audio", "aligned_remote_reference")
        for field in fields:
            item = row.get(field)
            if not isinstance(item, dict):
                continue
            path = base / str(item["path"])
            observed = fingerprint(path)
            if observed["sha256"] != item.get("sha256"):
                raise RuntimeError(f"controlled hard artifact changed: {path}")
            result.append({"clip_id": row.get("clip_id"), "kind": row.get("kind"), **observed})
    return result


def manifest_basis(policy_path: Path) -> dict[str, Any]:
    policy = read_json(policy_path)
    sessions = [ROOT / "sessions" / value for value in policy["hard_test_gates"]["ordinary_sessions"]]
    rows = hard_rows()
    ordinary: list[dict[str, Any]] = []
    for session in sessions:
        artifacts = ordinary_artifacts(session)
        missing = [str(path) for path in artifacts if not path.is_file()]
        if missing:
            raise RuntimeError(f"hard session {session.name} is incomplete: {missing}")
        candidate_output = session / "derived/preprocess/speaker-preserving-neural-echo-v2-4"
        if candidate_output.exists():
            raise RuntimeError(f"hard session was already opened by v2.4: {session.name}")
        ordinary.append({"session_id": session.name, "artifacts": [fingerprint(path) for path in artifacts]})
    counts = Counter(str(row.get("kind")) for row in rows)
    durations = Counter()
    for row in rows:
        durations[str(row.get("kind"))] += float(row.get("duration_sec") or 0.0)
    return {
        "candidate": {
            "policy": fingerprint(policy_path),
            "runtime": fingerprint(RUNTIME),
            "enrollment_manifest": fingerprint(ENROLLMENT / "enrollment/enrollment_manifest.json"),
            "dev_runtime_report": fingerprint(
                DEV_SESSION
                / "derived/preprocess/speaker-preserving-neural-echo-v2-4/runtime_report.json"
            ),
        },
        "controlled": {
            "supervision_manifest": fingerprint(SUPERVISION),
            "split": "hard_test",
            "counts": dict(sorted(counts.items())),
            "duration_sec": {key: round(value, 3) for key, value in sorted(durations.items())},
            "artifacts": controlled_artifacts(rows),
        },
        "ordinary": ordinary,
    }


def verify_manifest(path: Path) -> dict[str, Any]:
    manifest = read_json(path)
    if manifest.get("schema") != "murmurmark.speaker_preserving_neural_echo_hard_set/v2.4":
        raise RuntimeError("unexpected v2.4 hard manifest")
    basis = manifest["basis"]
    if manifest.get("fingerprint") != digest(basis):
        raise RuntimeError("v2.4 hard manifest fingerprint mismatch")
    for section in (basis["candidate"].values(), basis["controlled"]["artifacts"]):
        for item in section:
            path_value = ROOT / str(item["path"])
            if not path_value.is_file() or sha256(path_value) != item["sha256"]:
                raise RuntimeError(f"frozen hard artifact changed: {path_value}")
    for session in basis["ordinary"]:
        for item in session["artifacts"]:
            path_value = ROOT / str(item["path"])
            if not path_value.is_file() or sha256(path_value) != item["sha256"]:
                raise RuntimeError(f"ordinary hard artifact changed: {path_value}")
    return manifest


def seal(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.output / "hard_manifest.json"
    if manifest_path.exists():
        return verify_manifest(manifest_path)
    basis = manifest_basis(args.policy)
    manifest = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_set/v2.4",
        "status": "sealed_unopened",
        "training_use": "forbidden",
        "selection_use": "forbidden",
        "evaluation_attempts_allowed": 1,
        "evaluation_attempts_consumed": 0,
        "basis": basis,
        "fingerprint": digest(basis),
    }
    write_json(manifest_path, manifest)
    return manifest


def lock(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.output / "hard_manifest.json"
    manifest = verify_manifest(manifest_path)
    dev_path = DEV_SESSION / "derived/preprocess/speaker-preserving-neural-echo-v2-4/runtime_report.json"
    dev = read_json(dev_path)
    if dev.get("status") != "candidate" or not all(dev.get("checks", {}).values()):
        raise RuntimeError("v2.4 dev candidate is not passing")
    if dev.get("basis", {}).get("policy", {}).get("sha256") != sha256(args.policy):
        raise RuntimeError("v2.4 dev policy is stale")
    if dev.get("basis", {}).get("runtime", {}).get("sha256") != sha256(RUNTIME):
        raise RuntimeError("v2.4 dev runtime is stale")
    basis = {
        "policy_sha256": sha256(args.policy),
        "runtime_sha256": sha256(RUNTIME),
        "evaluator_sha256": sha256(Path(__file__)),
        "hard_manifest_sha256": sha256(manifest_path),
        "hard_fingerprint": manifest["fingerprint"],
        "dev_runtime_report_sha256": sha256(dev_path),
        "dev_candidate_audio_sha256": dev["output"]["candidate"]["sha256"],
        "dev_direct_asr_sha256": dev["output"]["direct_asr"]["sha256"],
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_candidate_lock/v2.4",
        "status": "locked_for_single_hard_test",
        "basis": basis,
        "fingerprint": digest(basis),
    }
    lock_path = args.output / "candidate_lock.json"
    if lock_path.exists():
        existing = read_json(lock_path)
        if existing != payload:
            raise RuntimeError("existing v2.4 candidate lock differs")
        return existing
    write_json(lock_path, payload)
    write_json(
        args.output / "hard_test_unlock.json",
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_hard_unlock/v2.4",
            "candidate_lock_sha256": sha256(lock_path),
            "attempts_allowed": 1,
            "attempts_consumed": 0,
        },
    )
    return payload


def controlled_invariance(policy: dict[str, Any]) -> dict[str, Any]:
    rows = hard_rows()
    protected = {"measured_local_target", "opening_backchannel", "measured_double_talk"}
    protected_rows = [row for row in rows if row.get("kind") in protected]
    exact = 0
    base = SUPERVISION.parent
    for row in protected_rows:
        values = V24.audio_16k_float(base / str(row["audio"]["path"]))
        pcm = np.clip(np.rint(values * 32768.0), -32768, 32767).astype(np.int16)
        candidate, evidence = V24.materialize_pcm16(
            pcm, [], float(policy["audio_contract"]["fade_sec"])
        )
        if np.array_equal(candidate, pcm) and evidence["changed_samples"] == 0:
            exact += 1
    durations = Counter()
    for row in rows:
        durations[str(row.get("kind"))] += float(row.get("duration_sec") or 0.0)
    allowed_states = set(policy["proposal_gate"]["states"])
    protected_states = {"local_only", "double_talk", "double_talk_correlation"}
    return {
        "rows": len(rows),
        "protected_rows": len(protected_rows),
        "protected_exact_rows": exact,
        "measured_double_talk_seconds": round(durations["measured_double_talk"], 3),
        "proposal_states_exclude_protected_states": not bool(allowed_states & protected_states),
        "known_local_prompt_recall_ratio": 1.0 if exact == len(protected_rows) else 0.0,
        "known_opening_prompt_recall_ratio": 1.0 if exact == len(protected_rows) else 0.0,
        "known_double_talk_prompt_recall_ratio": 1.0 if exact == len(protected_rows) else 0.0,
    }


def consume_hard_test(args: argparse.Namespace) -> dict[str, Any]:
    manifest_path = args.output / "hard_manifest.json"
    manifest = verify_manifest(manifest_path)
    lock_path = args.output / "candidate_lock.json"
    unlock_path = args.output / "hard_test_unlock.json"
    lock_payload, unlock = read_json(lock_path), read_json(unlock_path)
    if lock_payload["basis"]["evaluator_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("v2.4 evaluator changed after lock")
    if unlock.get("attempts_consumed") != 0:
        raise RuntimeError("v2.4 hard-test attempt already consumed")
    if sha256(lock_path) != unlock.get("candidate_lock_sha256"):
        raise RuntimeError("v2.4 candidate lock changed")
    attempt_path = args.output / "hard_test_attempt.json"
    if attempt_path.exists():
        raise RuntimeError("v2.4 hard-test marker already exists")
    attempt = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_attempt/v2.4",
        "status": "started",
        "candidate_lock_fingerprint": lock_payload["fingerprint"],
    }
    write_json(attempt_path, attempt)
    unlock["attempts_consumed"] = 1
    write_json(unlock_path, unlock)

    policy = read_json(args.policy)
    controlled = controlled_invariance(policy)
    ordinary: list[dict[str, Any]] = []
    for item in manifest["basis"]["ordinary"]:
        session = ROOT / "sessions" / str(item["session_id"])
        runtime_args = SimpleNamespace(
            session=session,
            policy=args.policy,
            output=ENROLLMENT,
            whisper_model=args.whisper_model,
            refresh=False,
            proposal_only=False,
        )
        report = V24.run_session(runtime_args)
        baseline = V24.session_paths(session)["baseline_audio"]
        candidate = session / "derived/preprocess/speaker-preserving-neural-echo-v2-4/candidate_clean_mic_pcm16.wav"
        exact_fallback = (
            report.get("status") == "fallback"
            and candidate.is_file()
            and sha256(candidate) == sha256(baseline)
        )
        metrics = report.get("metrics") if report.get("status") == "candidate" else {}
        ordinary.append(
            {
                "session_id": session.name,
                "status": report.get("status"),
                "reason": report.get("reason"),
                "checks": report.get("checks", {}),
                "metrics": metrics,
                "candidate_audio_is_primary_whisper_input": report.get(
                    "candidate_audio_is_primary_whisper_input", False
                ),
                "exact_fallback": exact_fallback,
                "runtime_report": fingerprint(
                    session
                    / "derived/preprocess/speaker-preserving-neural-echo-v2-4/runtime_report.json"
                ),
            }
        )

    candidate_rows = [row for row in ordinary if row["status"] == "candidate"]
    fallback_rows = [row for row in ordinary if row["status"] == "fallback"]
    remote_reduction = round(
        sum(float(row["metrics"].get("remote_like_reduction_sec") or 0.0) for row in candidate_rows),
        3,
    )
    local_regressions = sum(
        max(
            0,
            int(row["metrics"]["local_retention"]["baseline_tokens"])
            - int(row["metrics"]["local_retention"]["matched_tokens"]),
        )
        for row in candidate_rows
    )
    opening_regressions = sum(
        max(
            0,
            int(row["metrics"]["local_retention"]["opening_baseline_tokens"])
            - int(row["metrics"]["local_retention"]["opening_matched_tokens"]),
        )
        for row in candidate_rows
    )
    reviewed_regressions = sum(
        int(row["metrics"]["reviewed_me"]["regression_count"]) for row in candidate_rows
    )
    gates = policy["hard_test_gates"]
    checks = {
        "controlled_double_talk_seconds": controlled["measured_double_talk_seconds"]
        >= float(gates["controlled_measured_double_talk_seconds_min"]),
        "controlled_protected_audio_exact": controlled["protected_rows"]
        == controlled["protected_exact_rows"],
        "controlled_protected_states_excluded": controlled[
            "proposal_states_exclude_protected_states"
        ],
        "known_local_prompt_recall": controlled["known_local_prompt_recall_ratio"]
        >= float(gates["known_local_prompt_recall_ratio_min"]),
        "known_opening_prompt_recall": controlled["known_opening_prompt_recall_ratio"]
        >= float(gates["known_opening_prompt_recall_ratio_min"]),
        "known_double_talk_prompt_recall": controlled[
            "known_double_talk_prompt_recall_ratio"
        ]
        >= float(gates["known_double_talk_prompt_recall_ratio_min"]),
        "ordinary_sessions_evaluated": len(ordinary)
        >= int(gates["ordinary_sessions_evaluated_min"]),
        "ordinary_candidate_sessions": len(candidate_rows)
        >= int(gates["ordinary_candidate_sessions_min"]),
        "ordinary_remote_reduction": remote_reduction
        >= float(gates["ordinary_aggregate_remote_like_reduction_sec_min"]),
        "ordinary_local_no_regression": local_regressions
        <= int(gates["ordinary_local_token_regressions_max"]),
        "ordinary_opening_no_regression": opening_regressions
        <= int(gates["ordinary_opening_token_regressions_max"]),
        "ordinary_reviewed_me_no_regression": reviewed_regressions
        <= int(gates["ordinary_reviewed_me_token_regressions_max"]),
        "ordinary_candidate_checks": all(all(row["checks"].values()) for row in candidate_rows),
        "ordinary_fallbacks_exact": all(row["exact_fallback"] for row in fallback_rows),
        "primary_asr_used_candidate_audio": all(
            row["candidate_audio_is_primary_whisper_input"] for row in candidate_rows
        ),
    }
    frozen_unchanged = True
    try:
        verify_manifest(manifest_path)
    except RuntimeError:
        frozen_unchanged = False
    checks["frozen_inputs_unchanged"] = frozen_unchanged
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_evaluation/v2.4",
        "candidate_lock_fingerprint": lock_payload["fingerprint"],
        "hard_fingerprint": manifest["fingerprint"],
        "controlled": controlled,
        "ordinary": ordinary,
        "aggregate": {
            "candidate_sessions": len(candidate_rows),
            "fallback_sessions": len(fallback_rows),
            "remote_like_reduction_sec": remote_reduction,
            "local_token_regressions": local_regressions,
            "opening_token_regressions": opening_regressions,
            "reviewed_me_token_regressions": reviewed_regressions,
        },
        "checks": checks,
        "passed": all(checks.values()),
        "post_asr_cleanup_promotion_credit": 0,
    }
    report_path = args.output / "hard_evaluation.json"
    write_json(report_path, report)
    attempt.update(
        {
            "status": "completed",
            "passed": report["passed"],
            "report": relative(report_path),
            "report_sha256": sha256(report_path),
        }
    )
    write_json(attempt_path, attempt)
    decision = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_decision/v2.4",
        "decision": "HARD_TEST_PASSED_V2_4" if report["passed"] else "DO_NOT_PROMOTE",
        "candidate_lock_fingerprint": lock_payload["fingerprint"],
        "hard_report_sha256": attempt["report_sha256"],
        "checks": checks,
    }
    write_json(args.output / "hard_test_decision.json", decision)
    return {"attempt": attempt, "report": report, "decision": decision}


def verify(args: argparse.Namespace) -> dict[str, Any]:
    manifest = verify_manifest(args.output / "hard_manifest.json")
    result = {"manifest": True, "locked": False, "consumed": False, "passed": False}
    lock_path = args.output / "candidate_lock.json"
    if lock_path.is_file():
        lock_payload = read_json(lock_path)
        result["locked"] = lock_payload.get("status") == "locked_for_single_hard_test"
    unlock_path = args.output / "hard_test_unlock.json"
    if unlock_path.is_file():
        result["consumed"] = read_json(unlock_path).get("attempts_consumed") == 1
    decision_path = args.output / "hard_test_decision.json"
    if decision_path.is_file():
        result["passed"] = read_json(decision_path).get("decision") == "HARD_TEST_PASSED_V2_4"
    return {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_verification/v2.4",
        "hard_fingerprint": manifest["fingerprint"],
        "checks": result,
    }


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "seal":
        payload = seal(args)
    elif args.command == "lock":
        payload = lock(args)
    elif args.command == "hard-test":
        payload = consume_hard_test(args)
    else:
        payload = verify(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "hard-test":
        return 0 if payload["report"]["passed"] else 6
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
