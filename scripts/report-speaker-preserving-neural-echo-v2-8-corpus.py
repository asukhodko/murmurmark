#!/usr/bin/env python3
"""Freeze and evaluate the v2.8 fail-open pre-ASR selector on ordinary sessions."""

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
POLICY = ROOT / "policies/speaker-preserving-neural-echo-corpus-v2-8.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-8-corpus"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
DECISION = "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2"


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
    "murmurmark_spne_v28_corpus_runtime",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY)
    value.add_argument("--output", type=Path, default=OUTPUT)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    value.add_argument("--refresh", action="store_true")
    value.add_argument("--session", action="append", default=[])
    value.add_argument("command", choices=("freeze", "run", "report", "verify"))
    return value


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
    return {
        "path": relative(path),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def artifact_path(item: dict[str, Any]) -> Path:
    path = Path(str(item["path"]))
    return path if path.is_absolute() else ROOT / path


def policy_artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_corpus_policy/v2.8":
        raise RuntimeError("unexpected v2.8 corpus policy schema")
    pairs = (
        ("selector_policy", "selector_policy_sha256"),
        ("selector_runtime", "selector_runtime_sha256"),
        ("hard_decision", "hard_decision_sha256"),
        ("evaluator", "evaluator_sha256"),
    )
    checks = {
        key: policy_artifact(policy, key).is_file()
        and sha256(policy_artifact(policy, key)) == policy[hash_key]
        for key, hash_key in pairs
    }
    checks["hard_passed"] = (
        read_json(policy_artifact(policy, "hard_decision")).get("decision")
        == "HARD_TEST_PASSED_V2_8"
    )
    checks["zero_post_asr_credit"] = policy.get(
        "post_asr_cleanup_promotion_credit"
    ) == 0
    if not all(checks.values()):
        raise RuntimeError(f"v2.8 corpus policy verification failed: {checks}")
    return policy


def session_artifacts(session: Path) -> list[Path]:
    transcript = session / "derived/transcript-simple/whisper-cpp"
    return [
        session / "session.json",
        session / "audio/mic/000001.caf",
        session / "audio/remote/000001.caf",
        session / "derived/asr/mic.wav",
        session / "derived/preprocess/audio/remote_for_aec.wav",
        session / "derived/preprocess/echo/speaker_state.jsonl",
        transcript / "raw/mic.json",
        transcript / "raw/remote.json",
        transcript / "resolved/clean_dialogue.shadow_v2.json",
        transcript / "resolved/quality_report.shadow_v2.json",
        transcript / "resolved/overlaps.shadow_v2.json",
        transcript / "resolved/transcript.shadow_v2.md",
        session / "derived/synthesis-simple/extractive/quality_verdict.json",
    ]


def acoustic_mode(session: Path) -> str:
    report = read_json(session / "derived/preprocess/echo/local_fir_report.json")
    value = report.get("acoustic_mode")
    return str(value.get("mode") or "missing") if isinstance(value, dict) else "missing"


def selected_ids(args: argparse.Namespace, policy: dict[str, Any]) -> list[str]:
    available = [str(row["id"]) for row in policy["sessions"]]
    if not args.session:
        return available
    unknown = sorted(set(args.session) - set(available))
    if unknown:
        raise RuntimeError(f"sessions are not in v2.8 corpus policy: {unknown}")
    chosen = set(args.session)
    return [session_id for session_id in available if session_id in chosen]


def freeze(args: argparse.Namespace) -> dict[str, Any]:
    policy = verify_policy(args.policy)
    rows = []
    for session_id in selected_ids(args, policy):
        session = ROOT / "sessions" / session_id
        artifacts = session_artifacts(session)
        missing = [relative(path) for path in artifacts if not path.is_file()]
        if missing:
            raise RuntimeError(f"{session_id}: missing corpus inputs {missing}")
        configured = next(row for row in policy["sessions"] if row["id"] == session_id)
        mode = acoustic_mode(session)
        if mode != configured["expected_mode"]:
            raise RuntimeError(
                f"{session_id}: expected acoustic mode {configured['expected_mode']}, got {mode}"
            )
        if (session / "derived/preprocess/speaker-preserving-neural-echo-v2-7").exists():
            raise RuntimeError(f"{session_id}: v2.7 candidate output exists before freeze")
        if (session / "derived/preprocess/speaker-preserving-neural-echo-v2-8").exists():
            raise RuntimeError(f"{session_id}: v2.8 selector output exists before freeze")
        rows.append(
            {
                "session": session_id,
                "expected_mode": configured["expected_mode"],
                "artifacts": [fingerprint(path) for path in artifacts],
            }
        )
    basis = {"policy_sha256": sha256(args.policy), "sessions": rows}
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_frozen_corpus/v2.8",
        "status": "frozen_before_v2_7_audio_and_v2_8_selection",
        "training_use": "forbidden",
        "selection_use": "corpus_promotion_only",
        "basis": basis,
        "fingerprint": stable_digest(basis),
    }
    destination = args.output / "frozen_corpus.json"
    existing = read_json(destination)
    if existing and existing != payload:
        raise RuntimeError("v2.8 frozen corpus already exists with different inputs")
    write_json(destination, payload)
    return payload


def verify_frozen(args: argparse.Namespace) -> dict[str, Any]:
    frozen = read_json(args.output / "frozen_corpus.json")
    if frozen.get("schema") != "murmurmark.speaker_preserving_neural_echo_frozen_corpus/v2.8":
        raise RuntimeError("freeze the v2.8 corpus first")
    if stable_digest(frozen["basis"]) != frozen.get("fingerprint"):
        raise RuntimeError("v2.8 frozen corpus fingerprint changed")
    if frozen["basis"]["policy_sha256"] != sha256(args.policy):
        raise RuntimeError("v2.8 corpus policy changed after freeze")
    for row in frozen["basis"]["sessions"]:
        for artifact in row["artifacts"]:
            path = artifact_path(artifact)
            if not path.is_file() or sha256(path) != artifact["sha256"]:
                raise RuntimeError(f"v2.8 frozen corpus input changed: {path}")
    return frozen


def run_one(args: argparse.Namespace, policy: dict[str, Any], session_id: str) -> dict[str, Any]:
    session = ROOT / "sessions" / session_id
    result = V28.run(
        SimpleNamespace(
            session=session,
            policy=policy_artifact(policy, "selector_policy"),
            whisper_model=args.whisper_model,
            refresh=args.refresh,
        )
    )
    source_metrics = result.get("source_runtime", {}).get("metrics", {})
    status = str(result.get("status") or "")
    local = source_metrics.get("local_retention", {}) if status == "candidate" else {}
    reviewed = source_metrics.get("reviewed_me", {}) if status == "candidate" else {}
    checks = {
        "terminal_status": status in {"candidate", "fallback"},
        "candidate_or_exact_fallback": status == "candidate"
        or result.get("exact_fallback") is True,
        "candidate_primary_asr": status != "candidate"
        or result.get("candidate_audio_is_primary_whisper_input") is True,
        "candidate_full_shadow_passed": status != "candidate"
        or result.get("full_shadow", {}).get("passed") is True,
        "candidate_local_retention": status != "candidate"
        or float(local.get("ratio") or 0.0) >= 1.0,
        "candidate_opening_retention": status != "candidate"
        or float(local.get("opening_ratio") or 0.0) >= 1.0,
        "candidate_reviewed_me_no_regression": status != "candidate"
        or int(reviewed.get("regression_count") or 0) == 0,
        "zero_post_asr_credit": result.get("post_asr_cleanup_promotion_credit") == 0,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_session/v2.8",
        "session": session_id,
        "status": status,
        "reason": result.get("reason"),
        "selection_fingerprint": result.get("selection_fingerprint"),
        "remote_supported_reduction_sec": float(
            source_metrics.get("remote_like_reduction_sec") or 0.0
        )
        if status == "candidate"
        else 0.0,
        "remote_supported_token_reduction": int(
            source_metrics.get("remote_supported_token_reduction") or 0
        )
        if status == "candidate"
        else 0,
        "local_retention": local,
        "reviewed_me": reviewed,
        "full_shadow": result.get("full_shadow", {}),
        "exact_fallback": result.get("exact_fallback") is True,
        "checks": checks,
        "passed": all(checks.values()),
        "post_asr_cleanup_promotion_credit": 0,
    }
    write_json(args.output / "sessions" / f"{session_id}.json", payload)
    return payload


def command_run(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    frozen = verify_frozen(args)
    failed = False
    for session_id in selected_ids(args, policy):
        print(f"[corpus] {session_id}", flush=True)
        row = run_one(args, policy, session_id)
        print(
            json.dumps(
                {
                    "session": session_id,
                    "status": row["status"],
                    "passed": row["passed"],
                    "remote_supported_reduction_sec": row[
                        "remote_supported_reduction_sec"
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        failed = failed or not row["passed"]
    verify_frozen(args)
    return 4 if failed else 0


def render(payload: dict[str, Any]) -> str:
    aggregate = payload["aggregate"]
    lines = [
        "# Speaker-Preserving Neural Echo v2.8 Corpus",
        "",
        f"- Decision: `{payload['promotion']['decision']}`",
        f"- Candidate/fallback sessions: `{aggregate['candidate_sessions']}` / "
        f"`{aggregate['fallback_sessions']}`",
        f"- Remote-supported reduction: `{aggregate['remote_supported_reduction_sec']:.3f}s`, "
        f"`{aggregate['remote_supported_token_reduction']}` tokens",
        "- Post-ASR cleanup promotion credit: `0`",
        "",
        "| Session | Selection | Reduction | Tokens | Passed |",
        "|---|---|---:|---:|---|",
    ]
    for row in payload["sessions"]:
        lines.append(
            f"| `{row['session']}` | `{row['status']}` | "
            f"{row['remote_supported_reduction_sec']:+.3f}s | "
            f"{row['remote_supported_token_reduction']:+d} | `{row['passed']}` |"
        )
    lines.extend(["", "## Promotion Gates", ""])
    for key, value in payload["promotion"]["gates"].items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    return "\n".join(lines) + "\n"


def command_report(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    frozen = verify_frozen(args)
    rows = []
    for session_id in selected_ids(args, policy):
        row = read_json(args.output / "sessions" / f"{session_id}.json")
        if row.get("schema") != "murmurmark.speaker_preserving_neural_echo_corpus_session/v2.8":
            raise RuntimeError(f"missing v2.8 corpus result: {session_id}")
        rows.append(row)
    candidate_count = sum(row["status"] == "candidate" for row in rows)
    fallback_count = sum(row["status"] == "fallback" for row in rows)
    positive = sum(row["remote_supported_reduction_sec"] > 0.0 for row in rows)
    reduction = round(sum(row["remote_supported_reduction_sec"] for row in rows), 3)
    tokens = sum(row["remote_supported_token_reduction"] for row in rows)
    gates_policy = policy["promotion_gates"]
    gates = {
        "all_session_gates_passed": all(row["passed"] for row in rows),
        "candidate_sessions_min": candidate_count
        >= int(gates_policy["candidate_sessions_min"]),
        "remote_reduction_sec_min": reduction
        >= float(gates_policy["remote_reduction_sec_min"]),
        "remote_token_reduction_min": tokens
        >= int(gates_policy["remote_token_reduction_min"]),
        "positive_sessions_min": positive
        >= int(gates_policy["positive_sessions_min"]),
        "fallbacks_exact": all(
            row["status"] != "fallback" or row["exact_fallback"] for row in rows
        ),
        "frozen_inputs_unchanged": True,
        "zero_post_asr_credit": policy["post_asr_cleanup_promotion_credit"] == 0,
    }
    try:
        verify_frozen(args)
    except RuntimeError:
        gates["frozen_inputs_unchanged"] = False
    decision = DECISION if all(gates.values()) else "DO_NOT_PROMOTE"
    aggregate = {
        "sessions": len(rows),
        "candidate_sessions": candidate_count,
        "fallback_sessions": fallback_count,
        "sessions_with_remote_supported_reduction": positive,
        "remote_supported_reduction_sec": reduction,
        "remote_supported_token_reduction": tokens,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_report/v2.8",
        "frozen_fingerprint": frozen["fingerprint"],
        "aggregate": aggregate,
        "sessions": rows,
        "promotion": {
            "decision": decision,
            "candidate": "speaker_preserving_neural_echo_v2_8"
            if decision == DECISION
            else None,
            "fallback": "local_fir_role_masked",
            "gates": gates,
            "post_asr_cleanup_credit": 0,
        },
    }
    payload["decision_fingerprint"] = stable_digest(
        {
            "frozen_fingerprint": frozen["fingerprint"],
            "aggregate": aggregate,
            "gates": gates,
        }
    )
    write_json(args.output / "corpus_report.json", payload)
    (args.output / "corpus_report.md").write_text(render(payload), encoding="utf-8")
    print(json.dumps(payload["promotion"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if decision == DECISION else 6


def command_verify(args: argparse.Namespace) -> int:
    verify_policy(args.policy)
    frozen = verify_frozen(args)
    report = read_json(args.output / "corpus_report.json")
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_verification/v2.8",
        "frozen_fingerprint": frozen["fingerprint"],
        "decision": report.get("promotion", {}).get("decision"),
        "passed": report.get("promotion", {}).get("decision") == DECISION,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 7


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "freeze":
        print(json.dumps(freeze(args), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if args.command == "run":
        return command_run(args)
    if args.command == "report":
        return command_report(args)
    return command_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
