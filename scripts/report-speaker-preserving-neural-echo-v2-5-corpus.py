#!/usr/bin/env python3
"""Evaluate v2.4 candidate audio with the source-preserving v2.5 shadow harness."""

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
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-corpus-v2-5.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-5-corpus"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
CANDIDATE = "speaker_preserving_neural_echo_v2_4"


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--output", type=Path, default=OUTPUT)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    value.add_argument("--refresh", action="store_true")
    value.add_argument("--session", action="append", default=[])
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("freeze")
    sub.add_parser("run")
    sub.add_parser("report")
    sub.add_parser("verify")
    return value


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-4.py",
    "murmurmark_spne_v24_corpus_runtime",
)
PROMOTION = load_module(
    ROOT / "scripts/speaker_preserving_echo_full_shadow_v2_5.py",
    "murmurmark_spne_v25_corpus_shadow",
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


def relative(path: Path, root: Path = ROOT) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def policy_path(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_corpus_policy/v2.5":
        raise RuntimeError("unexpected corpus policy schema")
    pairs = (
        ("candidate_policy", "candidate_policy_sha256"),
        ("hard_decision", "hard_decision_sha256"),
        ("runtime", "runtime_sha256"),
        ("evaluator", "evaluator_sha256"),
        ("shadow_helper", "shadow_helper_sha256"),
        ("transcriber", "transcriber_sha256"),
        ("enrollment_manifest", "enrollment_manifest_sha256"),
    )
    checks: dict[str, bool] = {}
    for path_key, hash_key in pairs:
        artifact = policy_path(policy, path_key)
        checks[path_key] = artifact.is_file() and sha256(artifact) == policy[hash_key]
    checks["hard_passed"] = (
        read_json(policy_path(policy, "hard_decision")).get("decision")
        == "HARD_TEST_PASSED_V2_4"
    )
    checks["zero_post_asr_credit"] = policy.get("post_asr_cleanup_promotion_credit") == 0
    if not all(checks.values()):
        raise RuntimeError(f"corpus policy verification failed: {checks}")
    return policy


def baseline_paths(session: Path) -> dict[str, Path]:
    runtime_paths = RUNTIME.session_paths(session)
    transcript = session / "derived/transcript-simple/whisper-cpp"
    paths = dict(runtime_paths)
    paths.update(
        {
            "session_manifest": session / "session.json",
            "raw_mic": session / "audio/mic/000001.caf",
            "raw_remote": session / "audio/remote/000001.caf",
            "baseline_quality": transcript / "resolved/quality_report.shadow_v2.json",
            "baseline_dialogue": transcript / "resolved/clean_dialogue.shadow_v2.json",
            "baseline_verdict": session
            / "derived/synthesis-simple/extractive/quality_verdict.json",
        }
    )
    return paths


def acoustic_mode(session: Path) -> str:
    report = read_json(session / "derived/preprocess/echo/local_fir_report.json")
    return str(report.get("acoustic_mode", {}).get("mode") or "missing")


def command_freeze(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    rows: list[dict[str, Any]] = []
    for configured in policy["sessions"]:
        session = ROOT / "sessions" / str(configured["id"])
        paths = baseline_paths(session)
        missing = [key for key, path in paths.items() if not path.is_file()]
        if missing:
            raise RuntimeError(f"{session.name}: missing {missing}")
        mode = acoustic_mode(session)
        if mode != configured["expected_mode"]:
            raise RuntimeError(
                f"{session.name}: expected {configured['expected_mode']}, got {mode}"
            )
        runtime_report = (
            session
            / "derived/preprocess/speaker-preserving-neural-echo-v2-4/runtime_report.json"
        )
        if runtime_report.exists():
            raise RuntimeError(f"{session.name}: candidate output exists before corpus freeze")
        rows.append(
            {
                "session": session.name,
                "expected_mode": configured["expected_mode"],
                "artifacts": {
                    key: fingerprint(value, session) for key, value in paths.items()
                },
            }
        )
    basis = {"policy_sha256": sha256(args.policy), "sessions": rows}
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_frozen_corpus/v2.5",
        "status": "frozen_before_candidate_audio_and_asr",
        "training_use": "forbidden",
        "selection_use": "corpus_promotion_only",
        "basis": basis,
        "fingerprint": stable_digest(basis),
    }
    destination = args.output / "frozen_corpus.json"
    existing = read_json(destination)
    if existing and existing != payload:
        raise RuntimeError("frozen corpus already exists with different artifacts")
    write_json(destination, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def verify_frozen(args: argparse.Namespace) -> dict[str, Any]:
    frozen = read_json(args.output / "frozen_corpus.json")
    if frozen.get("schema") != "murmurmark.speaker_preserving_neural_echo_frozen_corpus/v2.5":
        raise RuntimeError("freeze the corpus first")
    if stable_digest(frozen["basis"]) != frozen.get("fingerprint"):
        raise RuntimeError("frozen corpus fingerprint changed")
    if frozen["basis"]["policy_sha256"] != sha256(args.policy):
        raise RuntimeError("corpus policy changed after freeze")
    for row in frozen["basis"]["sessions"]:
        session = ROOT / "sessions" / row["session"]
        for artifact in row["artifacts"].values():
            artifact_path = session / artifact["path"]
            if not artifact_path.is_file() or sha256(artifact_path) != artifact["sha256"]:
                raise RuntimeError(f"frozen artifact changed: {artifact_path}")
    return frozen


def selected_ids(args: argparse.Namespace, frozen: dict[str, Any]) -> list[str]:
    available = [row["session"] for row in frozen["basis"]["sessions"]]
    if not args.session:
        return available
    unknown = sorted(set(args.session) - set(available))
    if unknown:
        raise RuntimeError(f"sessions are not frozen: {unknown}")
    selected = set(args.session)
    return [value for value in available if value in selected]


def safe_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    destination.symlink_to(source.resolve())


def prepare_shadow_root(session: Path, candidate_audio: Path) -> Path:
    paths = RUNTIME.session_paths(session)
    output_root = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-4/corpus-evaluation-v2-5"
    )
    safe_link(candidate_audio, output_root / f"candidates/{CANDIDATE}/mic_for_asr.wav")
    safe_link(paths["baseline_audio"], output_root / "canonical/mic.wav")
    safe_link(paths["remote_audio"], output_root / "canonical/remote_aligned.wav")
    return output_root


def baseline_remote_like(session: Path) -> dict[str, Any]:
    paths = RUNTIME.session_paths(session)
    mic = RUNTIME.METRICS.asr_segments(paths["baseline_mic_asr"])
    remote = RUNTIME.METRICS.asr_segments(paths["baseline_remote_asr"])
    states = RUNTIME.read_jsonl(paths["speaker_state"])
    return RUNTIME.METRICS.remote_like(mic, remote, states)


def fallback_metrics(session: Path) -> dict[str, Any]:
    remote = baseline_remote_like(session)
    return {
        "local_retention": {
            "baseline_tokens": 0,
            "matched_tokens": 0,
            "ratio": 1.0,
            "opening_baseline_tokens": 0,
            "opening_matched_tokens": 0,
            "opening_ratio": 1.0,
        },
        "remote_like_before": remote,
        "remote_like_after": remote,
        "remote_like_reduction_sec": 0.0,
        "reviewed_me": {"regression_count": 0},
    }


def run_runtime(args: argparse.Namespace, policy: dict[str, Any], session: Path) -> dict[str, Any]:
    runtime_args = SimpleNamespace(
        session=session,
        policy=policy_path(policy, "candidate_policy"),
        output=ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-4",
        whisper_model=args.whisper_model,
        refresh=args.refresh,
        proposal_only=False,
    )
    return RUNTIME.run_session(runtime_args)


def run_one(
    args: argparse.Namespace,
    policy: dict[str, Any],
    frozen: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    session = ROOT / "sessions" / session_id
    runtime = run_runtime(args, policy, session)
    source_status = str(runtime.get("status") or "")
    if source_status not in {"candidate", "fallback"}:
        raise RuntimeError(f"{session_id}: non-terminal runtime status {source_status}")
    source_metrics = runtime.get("metrics") or {}
    direct = policy["direct_asr_gates"]
    source_local = source_metrics.get("local_retention") or {}
    source_reviewed = source_metrics.get("reviewed_me") or {}
    source_before = source_metrics.get("remote_like_before") or {}
    source_after = source_metrics.get("remote_like_after") or {}
    strict_candidate_gates = {
        "local_token_retention": float(source_local.get("ratio") or 0.0)
        >= float(direct["local_token_retention_ratio_per_session_min"]),
        "opening_token_retention": float(source_local.get("opening_ratio") or 0.0)
        >= float(direct["opening_token_retention_ratio_min"]),
        "reviewed_me_no_regression": int(source_reviewed.get("regression_count") or 0)
        <= int(direct["reviewed_me_token_regressions_max"]),
        "remote_like_not_increased": float(source_after.get("seconds") or 0.0)
        <= float(source_before.get("seconds") or 0.0)
        + float(direct["remote_like_seconds_increase_per_session_max"]),
    }
    status = source_status
    selection_reason = str(runtime.get("reason") or source_status)
    if source_status == "candidate" and not all(strict_candidate_gates.values()):
        status = "fallback"
        selection_reason = "strict_direct_asr_gate_failed"
    candidate_audio = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-4/"
        "candidate_clean_mic_pcm16.wav"
    )
    baseline_audio = RUNTIME.session_paths(session)["baseline_audio"]
    selected_audio = candidate_audio if status == "candidate" else baseline_audio
    fallback_exact = status == "fallback" and sha256(selected_audio) == sha256(baseline_audio)
    if status == "candidate":
        output_root = prepare_shadow_root(session, candidate_audio)
        full_shadow = PROMOTION.full_shadow_stage(
            session=session,
            output_root=output_root,
            candidate=CANDIDATE,
            candidate_mic_asr=session
            / "derived/preprocess/speaker-preserving-neural-echo-v2-4/direct-asr/raw/mic.json",
            candidate_asr_report=session
            / "derived/preprocess/speaker-preserving-neural-echo-v2-4/direct-asr/chunk_report.json",
            whisper_model=args.whisper_model,
            refresh=args.refresh,
        )
        metrics = source_metrics
    else:
        full_shadow = {
            "status": "skipped_exact_fallback",
            "passed": fallback_exact,
            "gates": {"bit_exact_authoritative_baseline": fallback_exact},
        }
        metrics = fallback_metrics(session)
    local = metrics.get("local_retention") or {}
    reviewed = metrics.get("reviewed_me") or {}
    before = metrics.get("remote_like_before") or {}
    after = metrics.get("remote_like_after") or {}
    checks = runtime.get("checks") if isinstance(runtime.get("checks"), dict) else {}
    gates = {
        "runtime_terminal": status in {"candidate", "fallback"},
        "candidate_or_exact_fallback": status == "candidate" or fallback_exact,
        "primary_asr_used_candidate_audio": status == "fallback"
        or runtime.get("candidate_audio_is_primary_whisper_input") is True,
        "local_token_retention": float(local.get("ratio") or 0.0)
        >= float(direct["local_token_retention_ratio_per_session_min"]),
        "opening_token_retention": float(local.get("opening_ratio") or 0.0)
        >= float(direct["opening_token_retention_ratio_min"]),
        "reviewed_me_no_regression": int(reviewed.get("regression_count") or 0)
        <= int(direct["reviewed_me_token_regressions_max"]),
        "remote_like_not_increased": float(after.get("seconds") or 0.0)
        <= float(before.get("seconds") or 0.0)
        + float(direct["remote_like_seconds_increase_per_session_max"]),
        "runtime_checks": status == "fallback" or all(checks.values()),
        "full_shadow_no_regression": full_shadow.get("passed") is True,
        "frozen_inputs_unchanged": True,
        "zero_post_asr_cleanup_credit": runtime.get("post_asr_cleanup_promotion_credit", 0)
        == 0,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_session/v2.5",
        "session": session_id,
        "acoustic_mode": acoustic_mode(session),
        "runtime": {
            "status": status,
            "source_status": source_status,
            "reason": selection_reason,
            "strict_candidate_gates": strict_candidate_gates,
            "rejected_candidate_metrics": source_metrics
            if source_status == "candidate" and status == "fallback"
            else None,
            "runtime_sec": runtime.get("runtime_sec"),
            "proposal": runtime.get("proposal"),
            "diagnostic": runtime.get("diagnostic"),
            "audio": runtime.get("audio"),
            "output": runtime.get("output"),
            "selected_audio": fingerprint(selected_audio, session),
        },
        "direct_asr": {
            "local_retention": local,
            "remote_like_baseline": before,
            "remote_like_candidate": after,
            "remote_like_reduction_sec": round(
                float(before.get("seconds") or 0.0) - float(after.get("seconds") or 0.0),
                3,
            ),
            "reviewed_me": reviewed,
        },
        "full_shadow": full_shadow,
        "gates": gates,
        "passed": all(gates.values()),
        "post_asr_cleanup_promotion_credit": 0,
        "frozen_fingerprint": frozen["fingerprint"],
    }
    verify_frozen(args)
    destination = args.output / "sessions" / f"{session_id}.json"
    write_json(destination, payload)
    return payload


def command_run(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    frozen = verify_frozen(args)
    if not args.whisper_model.is_file():
        raise RuntimeError(f"whisper model missing: {args.whisper_model}")
    failed = False
    for session_id in selected_ids(args, frozen):
        print(f"[corpus] {session_id}", flush=True)
        payload = run_one(args, policy, frozen, session_id)
        summary = {
            "session": session_id,
            "status": payload["runtime"]["status"],
            "passed": payload["passed"],
            "local_retention": payload["direct_asr"]["local_retention"]["ratio"],
            "remote_like_reduction_sec": payload["direct_asr"][
                "remote_like_reduction_sec"
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        failed = failed or not payload["passed"]
    return 4 if failed else 0


def render_report(payload: dict[str, Any]) -> str:
    aggregate = payload["aggregate"]
    lines = [
        "# Speaker-Preserving Neural Echo v2.5 Corpus",
        "",
        f"- Decision: `{payload['promotion']['decision']}`",
        f"- Sessions: `{aggregate['sessions']}`",
        f"- Candidate/fallback: `{aggregate['candidate_sessions']}` / "
        f"`{aggregate['fallback_sessions']}`",
        f"- Local token regressions: `{aggregate['local_token_regressions']}`",
        f"- Remote-like mic: `{aggregate['remote_like_seconds_baseline']:.3f}s` -> "
        f"`{aggregate['remote_like_seconds_candidate']:.3f}s`",
        f"- Remote-like reduction: `{aggregate['remote_like_seconds_reduction']:.3f}s` "
        f"(`{aggregate['remote_like_seconds_reduction_ratio']:.3%}`)",
        "- Post-ASR cleanup promotion credit: `0`",
        "",
        "| Session | Runtime | Local | Remote-like delta | Full shadow |",
        "|---|---|---:|---:|---|",
    ]
    for row in payload["sessions"]:
        lines.append(
            f"| `{row['session']}` | `{row['runtime']['status']}` | "
            f"{row['direct_asr']['local_retention']['ratio']:.3f} | "
            f"{row['direct_asr']['remote_like_reduction_sec']:+.3f}s | "
            f"`{row['full_shadow']['passed']}` |"
        )
    lines.extend(["", "## Promotion Gates", ""])
    for key, value in payload["promotion"]["gates"].items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    return "\n".join(lines) + "\n"


def command_report(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    frozen = verify_frozen(args)
    ids = selected_ids(args, frozen)
    rows: list[dict[str, Any]] = []
    for session_id in ids:
        payload = read_json(args.output / "sessions" / f"{session_id}.json")
        if payload.get("schema") != "murmurmark.speaker_preserving_neural_echo_corpus_session/v2.5":
            raise RuntimeError(f"missing corpus result: {session_id}")
        rows.append(payload)
    if not args.session and len(rows) != len(frozen["basis"]["sessions"]):
        raise RuntimeError("not all frozen sessions were evaluated")
    baseline_remote = sum(
        float(row["direct_asr"]["remote_like_baseline"].get("seconds") or 0.0)
        for row in rows
    )
    candidate_remote = sum(
        float(row["direct_asr"]["remote_like_candidate"].get("seconds") or 0.0)
        for row in rows
    )
    reduction = baseline_remote - candidate_remote
    reduction_ratio = reduction / max(baseline_remote, 1.0e-9)
    positive = sum(row["direct_asr"]["remote_like_reduction_sec"] > 0.0 for row in rows)
    candidate_count = sum(row["runtime"]["status"] == "candidate" for row in rows)
    fallback_count = sum(row["runtime"]["status"] == "fallback" for row in rows)
    local_regressions = sum(
        int(row["direct_asr"]["local_retention"].get("baseline_tokens") or 0)
        - int(row["direct_asr"]["local_retention"].get("matched_tokens") or 0)
        for row in rows
    )
    opening_regressions = sum(
        int(row["direct_asr"]["local_retention"].get("opening_baseline_tokens") or 0)
        - int(row["direct_asr"]["local_retention"].get("opening_matched_tokens") or 0)
        for row in rows
    )
    reviewed_regressions = sum(
        int(row["direct_asr"]["reviewed_me"].get("regression_count") or 0)
        for row in rows
    )
    promotion = policy["promotion_gates"]
    gates = {
        "all_session_gates_passed": all(row["passed"] for row in rows),
        "enough_candidate_sessions": candidate_count
        >= int(promotion["candidate_sessions_min"]),
        "aggregate_remote_like_reduction_seconds": reduction
        >= float(promotion["aggregate_remote_like_reduction_sec_min"]),
        "aggregate_remote_like_reduction_ratio": reduction_ratio
        >= float(promotion["aggregate_remote_like_reduction_ratio_min"]),
        "enough_positive_sessions": positive
        >= int(promotion["sessions_with_remote_like_reduction_min"]),
        "zero_local_token_regressions": local_regressions
        <= int(promotion["local_token_regressions_max"]),
        "zero_opening_token_regressions": opening_regressions
        <= int(promotion["opening_token_regressions_max"]),
        "zero_reviewed_me_regressions": reviewed_regressions
        <= int(promotion["reviewed_me_token_regressions_max"]),
        "frozen_inputs_unchanged": True,
        "zero_post_asr_cleanup_credit": policy["post_asr_cleanup_promotion_credit"] == 0,
    }
    decision = (
        "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2"
        if all(gates.values())
        else "DO_NOT_PROMOTE"
    )
    aggregate = {
        "sessions": len(rows),
        "candidate_sessions": candidate_count,
        "fallback_sessions": fallback_count,
        "local_token_regressions": local_regressions,
        "opening_token_regressions": opening_regressions,
        "reviewed_me_token_regressions": reviewed_regressions,
        "remote_like_seconds_baseline": round(baseline_remote, 3),
        "remote_like_seconds_candidate": round(candidate_remote, 3),
        "remote_like_seconds_reduction": round(reduction, 3),
        "remote_like_seconds_reduction_ratio": round(reduction_ratio, 6),
        "sessions_with_remote_like_reduction": positive,
    }
    decision_basis = {
        "frozen_fingerprint": frozen["fingerprint"],
        "aggregate": aggregate,
        "session_gates": {row["session"]: row["gates"] for row in rows},
        "promotion_gates": gates,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_report/v2.5",
        "frozen_fingerprint": frozen["fingerprint"],
        "aggregate": aggregate,
        "sessions": rows,
        "promotion": {
            "decision": decision,
            "candidate": CANDIDATE if decision.startswith("PROMOTE") else None,
            "fallback": "local_fir_role_masked",
            "gates": gates,
            "post_asr_cleanup_credit": 0,
        },
        "decision_fingerprint": stable_digest(decision_basis),
    }
    verify_frozen(args)
    write_json(args.output / "corpus_report.json", payload)
    (args.output / "corpus_report.md").write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload["promotion"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if decision.startswith("PROMOTE") else 6


def command_verify(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    frozen = verify_frozen(args)
    report = read_json(args.output / "corpus_report.json")
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_verification/v2.5",
        "policy": policy.get("schema"),
        "frozen_fingerprint": frozen["fingerprint"],
        "report_decision": report.get("promotion", {}).get("decision"),
        "passed": report.get("promotion", {}).get("decision")
        == "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload["passed"] else 7


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "freeze":
        return command_freeze(args)
    if args.command == "run":
        return command_run(args)
    if args.command == "report":
        return command_report(args)
    if args.command == "verify":
        return command_verify(args)
    raise RuntimeError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
