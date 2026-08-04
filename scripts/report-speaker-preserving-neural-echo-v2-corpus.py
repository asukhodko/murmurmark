#!/usr/bin/env python3
"""Freeze and evaluate the ordinary-meeting corpus for echo candidate v2."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib.util
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-corpus-v2.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-corpus"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
CANDIDATE = "speaker_preserving_neural_echo_v2"
TOKEN_RE = re.compile(r"[\wёЁ]+", re.UNICODE)
KNOWN_HALLUCINATIONS = (
    re.compile(r"^\s*продолжение следует\s*[.!?…-]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*субтитры.*$", re.IGNORECASE),
    re.compile(r"^\s*редактор субтитров.*$", re.IGNORECASE),
)
LOCAL_STATES = {"local_only", "double_talk", "double_talk_correlation"}
REMOTE_STATES = {
    "remote_only",
    "remote_only_correlation",
    "remote_only_level",
    "double_talk",
    "double_talk_correlation",
}


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
    return value


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


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


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME = load_module(
    ROOT / "scripts/materialize-speaker-preserving-neural-echo-v2.py",
    "murmurmark_spne_v2_corpus_runtime",
)
PROMOTION = load_module(
    ROOT / "scripts/echo-suppression-promotion-v1.py",
    "murmurmark_spne_v2_corpus_shadow",
)


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_corpus_policy/v2":
        raise RuntimeError("unexpected corpus policy schema")
    pairs = (
        ("candidate_policy", "candidate_policy_sha256"),
        ("hard_decision", "hard_decision_sha256"),
        ("runtime", "runtime_sha256"),
        ("evaluator", "evaluator_sha256"),
        ("shadow_helper", "shadow_helper_sha256"),
    )
    checks: dict[str, bool] = {}
    for path_key, hash_key in pairs:
        artifact = ROOT / str(policy[path_key])
        checks[path_key] = artifact.is_file() and sha256(artifact) == policy[hash_key]
    checks["hard_passed"] = (
        read_json(ROOT / str(policy["hard_decision"])).get("decision")
        == "HARD_TEST_PASSED_V2_2"
    )
    checks["zero_post_asr_credit"] = policy.get("post_asr_cleanup_promotion_credit") == 0
    if not all(checks.values()):
        raise RuntimeError(f"corpus policy verification failed: {checks}")
    return policy


def session_paths(session: Path) -> dict[str, Path]:
    runtime_root = session / "derived/preprocess/speaker-preserving-neural-echo-v2"
    transcript = session / "derived/transcript-simple/whisper-cpp"
    return {
        "runtime_report": runtime_root / "runtime_report.json",
        "candidate_audio": runtime_root / "mic_for_asr.wav",
        "remote_16k": runtime_root / "remote_16k.wav",
        "mic_raw": session / "derived/preprocess/audio/mic_raw_for_asr.wav",
        "speaker_state": session / "derived/preprocess/echo/speaker_state.jsonl",
        "local_fir_report": session / "derived/preprocess/echo/local_fir_report.json",
        "baseline_mic_asr": transcript / "raw/mic.json",
        "baseline_remote_asr": transcript / "raw/remote.json",
        "baseline_quality": transcript / "resolved/quality_report.shadow_v2.json",
        "baseline_dialogue": transcript / "resolved/clean_dialogue.shadow_v2.json",
    }


def acoustic_mode(session: Path) -> str:
    report = read_json(session_paths(session)["local_fir_report"])
    return str(report.get("acoustic_mode", {}).get("mode") or "missing")


def command_freeze(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    rows: list[dict[str, Any]] = []
    for configured in policy["sessions"]:
        session = ROOT / "sessions" / str(configured["id"])
        paths = session_paths(session)
        missing = [key for key, path in paths.items() if not path.is_file()]
        if missing:
            raise RuntimeError(f"{session.name}: missing {missing}")
        runtime = read_json(paths["runtime_report"])
        if runtime.get("status") != "candidate":
            raise RuntimeError(f"{session.name}: runtime is not a candidate")
        mode = acoustic_mode(session)
        if mode != configured["expected_mode"]:
            raise RuntimeError(f"{session.name}: expected {configured['expected_mode']}, got {mode}")
        rows.append(
            {
                "session": session.name,
                "expected_mode": configured["expected_mode"],
                "runtime_status": runtime.get("status"),
                "artifacts": {
                    key: fingerprint(path, session) for key, path in paths.items()
                },
            }
        )
    basis = {
        "policy_sha256": sha256(args.policy),
        "sessions": rows,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_frozen_corpus/v2",
        "status": "frozen_before_candidate_asr",
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
    if frozen.get("schema") != "murmurmark.speaker_preserving_neural_echo_frozen_corpus/v2":
        raise RuntimeError("freeze the corpus first")
    if stable_digest(frozen["basis"]) != frozen.get("fingerprint"):
        raise RuntimeError("frozen corpus fingerprint changed")
    if frozen["basis"]["policy_sha256"] != sha256(args.policy):
        raise RuntimeError("corpus policy changed after freeze")
    for row in frozen["basis"]["sessions"]:
        session = ROOT / "sessions" / row["session"]
        for artifact in row["artifacts"].values():
            path = session / artifact["path"]
            if not path.is_file() or sha256(path) != artifact["sha256"]:
                raise RuntimeError(f"frozen artifact changed: {path}")
    return frozen


def safe_link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    destination.symlink_to(source.resolve())


def prepare_shadow_root(session: Path) -> Path:
    paths = session_paths(session)
    output_root = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2/corpus-evaluation"
    )
    safe_link(paths["candidate_audio"], output_root / f"candidates/{CANDIDATE}/mic_for_asr.wav")
    safe_link(paths["mic_raw"], output_root / "canonical/mic.wav")
    safe_link(paths["remote_16k"], output_root / "canonical/remote_aligned.wav")
    return output_root


def tokens(text: str) -> list[str]:
    return [match.group(0).lower().replace("ё", "е") for match in TOKEN_RE.finditer(text)]


def is_hallucination(text: str) -> bool:
    return any(pattern.match(text) for pattern in KNOWN_HALLUCINATIONS)


def asr_segments(path: Path) -> list[dict[str, Any]]:
    payload = read_json(path)
    rows: list[dict[str, Any]] = []
    for item in payload.get("transcription", []):
        if not isinstance(item, dict):
            continue
        offsets = item.get("offsets") if isinstance(item.get("offsets"), dict) else {}
        text = str(item.get("text") or "").strip()
        observed = tokens(text)
        start = float(offsets.get("from") or 0.0) / 1000.0
        end = float(offsets.get("to") or 0.0) / 1000.0
        if end <= start or not observed or is_hallucination(text):
            continue
        rows.append({"start": start, "end": end, "text": text, "tokens": observed})
    return rows


def overlap(left: dict[str, Any], right: dict[str, Any], padding: float = 0.0) -> float:
    return max(
        0.0,
        min(float(left["end"]) + padding, float(right["end"]))
        - max(float(left["start"]) - padding, float(right["start"])),
    )


def state_ratios(
    states: list[dict[str, Any]], start: float, end: float
) -> dict[str, float]:
    duration = max(end - start, 1.0e-9)
    totals: Counter[str] = Counter()
    for row in states:
        shared = max(
            0.0,
            min(end, float(row.get("end") or 0.0))
            - max(start, float(row.get("start") or 0.0)),
        )
        if shared:
            totals[str(row.get("state") or "unknown")] += shared
    return {key: value / duration for key, value in totals.items()}


def counter_recall(expected: list[str], observed: list[str]) -> tuple[int, int]:
    expected_counter = Counter(expected)
    observed_counter = Counter(observed)
    matched = sum((expected_counter & observed_counter).values())
    return matched, sum(expected_counter.values())


def local_retention(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], states: list[dict[str, Any]]
) -> dict[str, Any]:
    matched = total = 0
    opening_matched = opening_total = 0
    examples: list[dict[str, Any]] = []
    for row in baseline:
        ratios = state_ratios(states, row["start"], row["end"])
        local_ratio = sum(ratios.get(key, 0.0) for key in LOCAL_STATES)
        if local_ratio < 0.5:
            continue
        nearby = [item for item in candidate if overlap(row, item, padding=1.0) > 0.0]
        observed = [token for item in nearby for token in item["tokens"]]
        row_matched, row_total = counter_recall(row["tokens"], observed)
        matched += row_matched
        total += row_total
        if row["start"] < 15.0:
            opening_matched += row_matched
            opening_total += row_total
        if row_total and row_matched / row_total < 0.8:
            examples.append(
                {
                    "start": round(row["start"], 3),
                    "end": round(row["end"], 3),
                    "baseline_text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
                    "token_recall": round(row_matched / row_total, 6),
                }
            )
    return {
        "matched_tokens": matched,
        "baseline_tokens": total,
        "ratio": round(matched / max(total, 1), 6),
        "opening_matched_tokens": opening_matched,
        "opening_baseline_tokens": opening_total,
        "opening_ratio": round(opening_matched / max(opening_total, 1), 6)
        if opening_total
        else 1.0,
        "low_retention_examples": examples[:20],
    }


def text_similarity(left: list[str], right: list[str]) -> float:
    if not left or not right:
        return 0.0
    sequence = difflib.SequenceMatcher(a=left, b=right, autojunk=False).ratio()
    left_set, right_set = set(left), set(right)
    containment = len(left_set & right_set) / max(min(len(left_set), len(right_set)), 1)
    return max(sequence, containment)


def merged_seconds(intervals: list[tuple[float, float]]) -> float:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return sum(end - start for start, end in merged)


def remote_like(
    mic: list[dict[str, Any]], remote: list[dict[str, Any]], states: list[dict[str, Any]]
) -> dict[str, Any]:
    intervals: list[tuple[float, float]] = []
    examples: list[dict[str, Any]] = []
    for row in mic:
        ratios = state_ratios(states, row["start"], row["end"])
        remote_ratio = sum(ratios.get(key, 0.0) for key in REMOTE_STATES)
        if remote_ratio < 0.5:
            continue
        overlapping = [item for item in remote if overlap(row, item, padding=0.4) > 0.0]
        if not overlapping:
            continue
        similarity = max(text_similarity(row["tokens"], item["tokens"]) for item in overlapping)
        if similarity < 0.55:
            continue
        intervals.append((row["start"], row["end"]))
        examples.append(
            {
                "start": round(row["start"], 3),
                "end": round(row["end"], 3),
                "similarity": round(similarity, 6),
                "text_sha256": hashlib.sha256(row["text"].encode()).hexdigest(),
            }
        )
    return {
        "seconds": round(merged_seconds(intervals), 3),
        "segments": len(intervals),
        "examples": examples[:30],
    }


def direct_asr_report(session: Path, output_root: Path) -> dict[str, Any]:
    paths = session_paths(session)
    stage_raw = (
        output_root
        / f"candidates/{CANDIDATE}/full-shadow-session/derived/"
        "transcript-simple/whisper-cpp/raw"
    )
    baseline_mic = asr_segments(paths["baseline_mic_asr"])
    candidate_mic = asr_segments(stage_raw / "mic.json")
    remote = asr_segments(paths["baseline_remote_asr"])
    states = read_jsonl(paths["speaker_state"])
    retention = local_retention(baseline_mic, candidate_mic, states)
    baseline_remote_like = remote_like(baseline_mic, remote, states)
    candidate_remote_like = remote_like(candidate_mic, remote, states)
    return {
        "schema": "murmurmark.speaker_preserving_neural_echo_direct_asr/v2",
        "local_retention": retention,
        "remote_like_baseline": baseline_remote_like,
        "remote_like_candidate": candidate_remote_like,
        "remote_like_reduction_sec": round(
            baseline_remote_like["seconds"] - candidate_remote_like["seconds"], 3
        ),
        "segment_counts": {
            "baseline_mic": len(baseline_mic),
            "candidate_mic": len(candidate_mic),
            "remote": len(remote),
        },
    }


def run_one(args: argparse.Namespace, session_id: str) -> dict[str, Any]:
    session = ROOT / "sessions" / session_id
    output_root = prepare_shadow_root(session)
    full_shadow = PROMOTION.full_shadow_stage(
        session=session,
        output_root=output_root,
        candidate=CANDIDATE,
        whisper_model=args.whisper_model,
        refresh=args.refresh,
    )
    direct = direct_asr_report(session, output_root)
    runtime = read_json(session_paths(session)["runtime_report"])
    policy = verify_policy(args.policy)
    direct_gates = policy["direct_asr_gates"]
    gates = {
        "full_shadow_no_regression": full_shadow.get("passed") is True,
        "local_token_retention_gte_session_min": direct["local_retention"]["ratio"]
        >= direct_gates["local_token_retention_ratio_per_session_min"],
        "opening_token_retention_gte_min": direct["local_retention"]["opening_ratio"]
        >= direct_gates["opening_token_retention_ratio_min"],
        "remote_like_seconds_not_increased": direct["remote_like_candidate"]["seconds"]
        <= direct["remote_like_baseline"]["seconds"]
        + direct_gates["remote_like_seconds_increase_per_session_max"],
        "outside_double_talk_exact": runtime.get("checks", {}).get("outside_double_talk_exact")
        is True,
        "runtime_finite": runtime.get("checks", {}).get("finite") is True,
        "runtime_not_clipped": runtime.get("checks", {}).get(
            "clipped_sample_ratio_lte_0_0001"
        )
        is True,
        "runtime_realtime": float(runtime.get("runtime", {}).get("inference_realtime_factor") or 1.0)
        <= policy["runtime_gates"]["cpu_realtime_factor_max"],
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_session/v2",
        "session": session.name,
        "acoustic_mode": acoustic_mode(session),
        "runtime": {
            "status": runtime.get("status"),
            "coverage": runtime.get("coverage"),
            "runtime": runtime.get("runtime"),
            "output": runtime.get("output"),
        },
        "direct_asr": direct,
        "full_shadow": full_shadow,
        "gates": gates,
        "passed": all(gates.values()),
        "post_asr_cleanup_promotion_credit": 0,
    }
    destination = args.output / "sessions" / f"{session.name}.json"
    write_json(destination, payload)
    return payload


def selected_ids(args: argparse.Namespace, frozen: dict[str, Any]) -> list[str]:
    available = [row["session"] for row in frozen["basis"]["sessions"]]
    if not args.session:
        return available
    unknown = sorted(set(args.session) - set(available))
    if unknown:
        raise RuntimeError(f"sessions are not frozen: {unknown}")
    return [value for value in available if value in set(args.session)]


def command_run(args: argparse.Namespace) -> int:
    verify_policy(args.policy)
    frozen = verify_frozen(args)
    if not args.whisper_model.is_file():
        raise RuntimeError(f"whisper model missing: {args.whisper_model}")
    failed = False
    for session_id in selected_ids(args, frozen):
        print(f"[corpus] {session_id}", flush=True)
        payload = run_one(args, session_id)
        print(
            json.dumps(
                {
                    "session": session_id,
                    "passed": payload["passed"],
                    "local_retention": payload["direct_asr"]["local_retention"]["ratio"],
                    "remote_like_reduction_sec": payload["direct_asr"][
                        "remote_like_reduction_sec"
                    ],
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            flush=True,
        )
        failed = failed or not payload["passed"]
    return 4 if failed else 0


def render_report(payload: dict[str, Any]) -> str:
    aggregate = payload["aggregate"]
    lines = [
        "# Speaker-Preserving Neural Echo v2 Corpus",
        "",
        f"- Decision: `{payload['promotion']['decision']}`",
        f"- Sessions: `{aggregate['sessions']}`",
        f"- Local token retention: `{aggregate['local_token_retention_ratio']:.3f}`",
        f"- Raw remote-like mic: `{aggregate['remote_like_seconds_baseline']:.3f}s` -> "
        f"`{aggregate['remote_like_seconds_candidate']:.3f}s`",
        f"- Candidate windows: `{aggregate['candidate_windows']}`",
        "- Post-ASR cleanup promotion credit: `0`",
        "",
        "| Session | Mode | Local retention | Remote-like delta | Pipeline |",
        "|---|---|---:|---:|---|",
    ]
    for row in payload["sessions"]:
        lines.append(
            f"| `{row['session']}` | `{row['acoustic_mode']}` | "
            f"{row['direct_asr']['local_retention']['ratio']:.3f} | "
            f"{row['direct_asr']['remote_like_reduction_sec']:+.3f}s | "
            f"`{row['passed']}` |"
        )
    lines.extend(["", "## Gates", ""])
    for key, value in payload["promotion"]["gates"].items():
        lines.append(f"- `{key}`: `{str(value).lower()}`")
    return "\n".join(lines) + "\n"


def command_report(args: argparse.Namespace) -> int:
    policy = verify_policy(args.policy)
    frozen = verify_frozen(args)
    rows: list[dict[str, Any]] = []
    for session_id in selected_ids(args, frozen):
        path = args.output / "sessions" / f"{session_id}.json"
        payload = read_json(path)
        if payload.get("schema") != "murmurmark.speaker_preserving_neural_echo_corpus_session/v2":
            raise RuntimeError(f"missing corpus result: {session_id}")
        rows.append(payload)
    if len(rows) != len(frozen["basis"]["sessions"]) and not args.session:
        raise RuntimeError("not all frozen sessions were evaluated")
    local_matched = sum(row["direct_asr"]["local_retention"]["matched_tokens"] for row in rows)
    local_total = sum(row["direct_asr"]["local_retention"]["baseline_tokens"] for row in rows)
    baseline_remote = sum(row["direct_asr"]["remote_like_baseline"]["seconds"] for row in rows)
    candidate_remote = sum(row["direct_asr"]["remote_like_candidate"]["seconds"] for row in rows)
    reduction = baseline_remote - candidate_remote
    reduction_ratio = reduction / max(baseline_remote, 1.0e-9)
    positive = sum(row["direct_asr"]["remote_like_reduction_sec"] > 0.0 for row in rows)
    candidate_windows = sum(
        int(row["runtime"]["coverage"].get("eligible_windows") or 0)
        - int(row["runtime"]["coverage"].get("selection_counts", {}).get("baseline") or 0)
        for row in rows
    )
    direct = policy["direct_asr_gates"]
    gates = {
        "all_session_gates_passed": all(row["passed"] for row in rows),
        "aggregate_local_token_retention": local_matched / max(local_total, 1)
        >= direct["local_token_retention_ratio_aggregate_min"],
        "aggregate_remote_like_reduction_seconds": reduction
        >= direct["remote_like_seconds_reduction_aggregate_min"],
        "aggregate_remote_like_reduction_ratio": reduction_ratio
        >= direct["remote_like_seconds_reduction_ratio_min"],
        "enough_positive_sessions": positive
        >= direct["sessions_with_remote_like_reduction_min"],
        "enough_candidate_windows": candidate_windows
        >= policy["runtime_gates"]["candidate_windows_min"],
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
        "local_tokens_matched": local_matched,
        "local_tokens_baseline": local_total,
        "local_token_retention_ratio": round(local_matched / max(local_total, 1), 6),
        "remote_like_seconds_baseline": round(baseline_remote, 3),
        "remote_like_seconds_candidate": round(candidate_remote, 3),
        "remote_like_seconds_reduction": round(reduction, 3),
        "remote_like_seconds_reduction_ratio": round(reduction_ratio, 6),
        "sessions_with_remote_like_reduction": positive,
        "candidate_windows": candidate_windows,
    }
    decision_basis = {
        "frozen_fingerprint": frozen["fingerprint"],
        "aggregate": aggregate,
        "session_gates": {row["session"]: row["gates"] for row in rows},
        "promotion_gates": gates,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_corpus_report/v2",
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
    json_path = args.output / "corpus_report.json"
    md_path = args.output / "corpus_report.md"
    write_json(json_path, payload)
    md_path.write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload["promotion"], ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if decision.startswith("PROMOTE") else 6


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
    raise RuntimeError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
