#!/usr/bin/env python3
"""Lock and evaluate the physical-only echo candidate v2.3."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import speaker_preserving_echo_arbitration as ARBITER
import speaker_preserving_echo_physical_bank as BANK
import speaker_preserving_neural_echo_v2 as CORE


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-3.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-3"
DEV_CACHE = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2/cache"
V21_PATH = ROOT / "scripts/speaker-preserving-neural-echo-v2-1.py"
CONTROLLED_SESSION = ROOT / "sessions/2026-08-02_22-57-31-echo-dev-normal"
CONTROLLED_VALIDATION = (
    ROOT
    / "sessions/_reports/speaker-preserving-neural-echo-v2/controlled-dev-validation/"
    "2026-08-02_22-57-31-echo-dev-normal"
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--output", type=Path, default=OUTPUT)
    sub = value.add_subparsers(dest="command", required=True)
    sub.add_parser("evaluate-dev")
    sub.add_parser("lock")
    sub.add_parser("hard-test")
    return value


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, payload: Any) -> None:
    CORE.write_json(path, payload)


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V21 = load_module(V21_PATH, "murmurmark_spne_v23_v21_helpers")


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.3":
        raise RuntimeError("unexpected v2.3 policy schema")
    source = policy["source"]
    pairs = (
        ("training_policy", "training_policy_sha256"),
        ("previous_policy", "previous_policy_sha256"),
        ("failed_corpus_report", "failed_corpus_report_sha256"),
        ("arbitration", "arbitration_sha256"),
        ("physical_bank", "physical_bank_sha256"),
        ("hard_builder", "hard_builder_sha256"),
        ("hard_manifest", "hard_manifest_sha256"),
        ("hard_cache", "hard_cache_sha256"),
    )
    artifacts: dict[str, Any] = {}
    for path_key, sha_key in pairs:
        artifact = ROOT / str(source[path_key])
        observed = CORE.sha256(artifact) if artifact.is_file() else None
        expected = str(source[sha_key])
        artifacts[path_key] = {
            "path": relative(artifact),
            "expected_sha256": expected,
            "observed_sha256": observed,
            "passed": observed == expected,
        }
    previous_report = read_json(ROOT / str(source["failed_corpus_report"]))
    hard_manifest = read_json(ROOT / str(source["hard_manifest"]))
    semantic = {
        "previous_candidate_rejected": (
            previous_report.get("promotion", {}).get("decision") == "DO_NOT_PROMOTE"
        ),
        "previous_decision_fingerprint": (
            previous_report.get("decision_fingerprint")
            == source["failed_corpus_decision_fingerprint"]
        ),
        "new_hard_sealed": hard_manifest.get("status") == "sealed_unopened",
        "new_hard_fingerprint": hard_manifest.get("fingerprint") == source["hard_fingerprint"],
        "new_hard_not_consumed": hard_manifest.get("evaluation_attempts_consumed") == 0,
        "new_hard_single_attempt": hard_manifest.get("evaluation_attempts_allowed") == 1,
        "hard_cache_matches_manifest": (
            hard_manifest.get("cache", {}).get("sha256") == source["hard_cache_sha256"]
        ),
        "hard_sources_match_policy": sorted(
            [
                str(hard_manifest.get("sources", {}).get("local_session")),
                str(hard_manifest.get("sources", {}).get("remote_session")),
            ]
        )
        == sorted(policy["isolation"]["hard_source_sessions"]),
        "physical_bank_matches_policy": list(BANK.ADDITIONAL_FIR_MULTIPLIERS)
        == policy["hypothesis_bank"]["additional_fir_multipliers"],
        "neural_candidate_forbidden": policy["hypothesis_bank"]["neural_candidate_allowed"]
        is False,
    }
    passed = all(row["passed"] for row in artifacts.values()) and all(semantic.values())
    if not passed:
        raise RuntimeError("v2.3 frozen source verification failed")
    return {"policy": policy, "artifacts": artifacts, "semantic": semantic, "passed": True}


def select_row(row: np.ndarray) -> tuple[np.ndarray, dict[str, Any]]:
    return BANK.select(
        baseline=row[0],
        echo_estimate=row[3],
        remote=row[1],
    )


def evaluate_waveforms(values: np.ndarray) -> tuple[dict[str, Any], list[np.ndarray]]:
    metrics: dict[str, list[float]] = defaultdict(list)
    selection_names: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    selected_audio: list[np.ndarray] = []
    output_hash = hashlib.sha256()
    started = time.monotonic()
    for start in range(0, values.shape[0], 16):
        batch = values[start : start + 16].astype(np.float32)
        for row in batch:
            selected, decision = select_row(row)
            selected_audio.append(selected)
            output_hash.update(selected.tobytes())
            selection_names[str(decision["selected"])] += 1
            for hypothesis in decision.get("hypotheses", []):
                for reason in hypothesis.get("reasons", []):
                    rejection_reasons[str(reason)] += 1
            baseline_snr = CORE.snr_db(row[2], row[0])
            selected_snr = CORE.snr_db(row[2], selected)
            metrics["baseline_snr_db"].append(baseline_snr)
            metrics["selected_snr_db"].append(selected_snr)
            metrics["snr_improvement_db"].append(selected_snr - baseline_snr)
            metrics["baseline_target_correlation"].append(CORE.correlation(row[2], row[0]))
            metrics["selected_target_correlation"].append(CORE.correlation(row[2], selected))
    runtime = time.monotonic() - started
    selected_count = len(selected_audio) - selection_names.get("baseline", 0)
    report = {
        "metrics": {key: CORE.percentile_summary(series) for key, series in metrics.items()},
        "selections": {
            "by_hypothesis": dict(sorted(selection_names.items())),
            "candidate": selected_count,
            "baseline": selection_names.get("baseline", 0),
            "candidate_ratio": round(selected_count / max(len(selected_audio), 1), 6),
            "rejection_reasons": dict(sorted(rejection_reasons.items())),
        },
        "candidate_output_stream_sha256": output_hash.hexdigest(),
        "runtime_sec": round(runtime, 6),
        "realtime_factor": round(runtime / max(values.shape[0] * 4.0, 1.0), 6),
    }
    return report, selected_audio


def dev_values() -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    manifest = read_json(DEV_CACHE / "dev_cache_manifest.json")
    waveforms = DEV_CACHE / "dev_waveforms.npy"
    kinds = DEV_CACHE / "dev_kinds.npy"
    if CORE.sha256(waveforms) != manifest["artifacts"]["waveforms"]["sha256"]:
        raise RuntimeError("dev waveforms changed")
    if CORE.sha256(kinds) != manifest["artifacts"]["kinds"]["sha256"]:
        raise RuntimeError("dev kinds changed")
    return np.load(waveforms, mmap_mode="r"), np.load(kinds, mmap_mode="r"), manifest


def controlled_dev() -> dict[str, Any]:
    import soundfile as sf

    baseline = sf.read(
        CONTROLLED_SESSION
        / "derived/echo-lab/analysis/controlled_double_talk.mic_clean_local_fir.wav",
        dtype="float32",
    )[0]
    raw = sf.read(
        CONTROLLED_SESSION / "derived/echo-lab/phases/10_controlled_double_talk_mic.wav",
        dtype="float32",
    )[0]
    remote = sf.read(
        CONTROLLED_SESSION / "derived/echo-lab/phases/10_controlled_double_talk_remote.wav",
        dtype="float32",
    )[0]
    count = min(baseline.size, raw.size, remote.size)
    baseline = baseline[:count]
    raw = raw[:count]
    remote = remote[:count]
    chunk = CORE.CLIP_SAMPLES
    selected_parts: list[np.ndarray] = []
    selections: Counter[str] = Counter()
    for start in range(0, count, chunk):
        end = min(start + chunk, count)
        selected, decision = BANK.select(
            baseline=baseline[start:end],
            echo_estimate=raw[start:end] - baseline[start:end],
            remote=remote[start:end],
        )
        selected_parts.append(selected)
        selections[str(decision["selected"])] += 1
    selected = np.concatenate(selected_parts) if selected_parts else baseline.copy()
    return {
        "exact_fallback": bool(np.array_equal(selected, baseline)),
        "selection_counts": dict(sorted(selections.items())),
        "baseline_remote_coherence": ARBITER.remote_coherence(baseline, remote),
        "selected_remote_coherence": ARBITER.remote_coherence(selected, remote),
        "output_sha256": hashlib.sha256(selected.tobytes()).hexdigest(),
    }


def command_evaluate_dev(args: argparse.Namespace) -> int:
    verification = verify_policy(args.policy)
    policy = verification["policy"]
    values, kinds, manifest = dev_values()
    synthetic = np.asarray(
        values[kinds == CORE.KIND_IDS["synthetic_double_talk"]], dtype=np.float32
    )
    evaluation, _ = evaluate_waveforms(synthetic)
    negative_exact = True
    negative_count = 0
    for kind in (
        "measured_local_target",
        "opening_backchannel",
        "keyboard_noise",
        "silence_background",
        "local_remote_negative",
    ):
        subset = np.asarray(values[kinds == CORE.KIND_IDS[kind]], dtype=np.float32)
        if not subset.size:
            continue
        for row in subset:
            selected, _ = select_row(row)
            negative_count += 1
            negative_exact = negative_exact and np.array_equal(selected, row[0])
    controlled = controlled_dev()
    metrics = evaluation["metrics"]
    gates = {
        "synthetic_snr_improvement_p50_gte_3db": metrics["snr_improvement_db"]["p50"] >= 3.0,
        "synthetic_snr_improvement_min_gte_0db": metrics["snr_improvement_db"]["min"] >= 0.0,
        "synthetic_candidate_ratio_gte_0_75": evaluation["selections"]["candidate_ratio"] >= 0.75,
        "negative_exact_fallback": negative_exact,
        "controlled_dev_exact_fallback": controlled["exact_fallback"],
        "cpu_realtime_factor_lte_0_10": evaluation["realtime_factor"] <= 0.10,
    }
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_dev_evaluation/v2.3",
        "policy_sha256": CORE.sha256(args.policy),
        "dev_cache_fingerprint": manifest["fingerprint"],
        "evaluation": evaluation,
        "negative_exact_examples": negative_count,
        "controlled_dev": controlled,
        "gates": gates,
        "passed": all(gates.values()),
        "post_asr_cleanup_credit": 0,
    }
    destination = args.output / "dev_evaluation.json"
    write_json(destination, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 4


def command_lock(args: argparse.Namespace) -> int:
    verification = verify_policy(args.policy)
    report_path = args.output / "dev_evaluation.json"
    if not report_path.is_file():
        raise RuntimeError("run evaluate-dev before lock")
    report = read_json(report_path)
    if report.get("passed") is not True or report.get("policy_sha256") != CORE.sha256(args.policy):
        raise RuntimeError("v2.3 dev evaluation is stale or failed")
    first_hash = report["evaluation"]["candidate_output_stream_sha256"]
    first_controlled_hash = report["controlled_dev"]["output_sha256"]
    command_evaluate_dev(args)
    replay = read_json(report_path)
    if replay["evaluation"]["candidate_output_stream_sha256"] != first_hash:
        raise RuntimeError("v2.3 dev replay is not deterministic")
    if replay["controlled_dev"]["output_sha256"] != first_controlled_hash:
        raise RuntimeError("v2.3 controlled replay is not deterministic")
    policy = verification["policy"]
    basis = {
        "policy_sha256": CORE.sha256(args.policy),
        "evaluator_sha256": CORE.sha256(Path(__file__)),
        "arbitration_sha256": CORE.sha256(Path(ARBITER.__file__)),
        "physical_bank_sha256": CORE.sha256(Path(BANK.__file__)),
        "dev_evaluation_sha256": CORE.sha256(report_path),
        "hard_manifest_sha256": policy["source"]["hard_manifest_sha256"],
        "hard_cache_sha256": policy["source"]["hard_cache_sha256"],
        "hard_fingerprint": policy["source"]["hard_fingerprint"],
    }
    lock = {
        "schema": "murmurmark.speaker_preserving_neural_echo_candidate_lock/v2.3",
        "status": "locked_for_single_hard_test",
        "basis": basis,
        "fingerprint": CORE.digest_json(basis),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    lock_path = args.output / "candidate_lock.json"
    write_json(lock_path, lock)
    write_json(
        args.output / "hard_test_unlock.json",
        {
            "schema": "murmurmark.speaker_preserving_neural_echo_hard_unlock/v2.3",
            "candidate_lock_sha256": CORE.sha256(lock_path),
            "candidate_lock_fingerprint": lock["fingerprint"],
            "attempts_allowed": 1,
            "attempts_consumed": 0,
        },
    )
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def write_wave(path: Path, values: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, values.astype(np.float32), CORE.SAMPLE_RATE, subtype="FLOAT")


def command_hard_test(args: argparse.Namespace) -> int:
    verification = verify_policy(args.policy)
    policy = verification["policy"]
    lock_path = args.output / "candidate_lock.json"
    unlock_path = args.output / "hard_test_unlock.json"
    lock, unlock = read_json(lock_path), read_json(unlock_path)
    if unlock.get("attempts_consumed") != 0:
        raise RuntimeError("v2.3 hard-test attempt already consumed")
    if CORE.sha256(lock_path) != unlock.get("candidate_lock_sha256"):
        raise RuntimeError("v2.3 lock changed")
    frozen = {
        "evaluator_sha256": CORE.sha256(Path(__file__)),
        "arbitration_sha256": CORE.sha256(Path(ARBITER.__file__)),
        "physical_bank_sha256": CORE.sha256(Path(BANK.__file__)),
    }
    if any(lock["basis"].get(key) != value for key, value in frozen.items()):
        raise RuntimeError("v2.3 implementation changed after lock")
    attempt_path = args.output / "hard_test_attempt.json"
    if attempt_path.exists():
        raise RuntimeError("v2.3 hard-test marker already exists")
    attempt = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_attempt/v2.3",
        "status": "started",
        "candidate_lock_fingerprint": lock["fingerprint"],
    }
    write_json(attempt_path, attempt)
    unlock["attempts_consumed"] = 1
    write_json(unlock_path, unlock)
    hard_manifest = read_json(ROOT / str(policy["source"]["hard_manifest"]))
    cache_path = ROOT / str(policy["source"]["hard_cache"])
    if CORE.sha256(cache_path) != policy["source"]["hard_cache_sha256"]:
        raise RuntimeError("v2.3 hard cache changed")
    values = np.load(cache_path)["waveforms"].astype(np.float32)
    evaluation, selected_audio = evaluate_waveforms(values)
    silence = np.zeros(CORE.SAMPLE_RATE // 2, dtype=np.float32)

    def joined(channel: int | None) -> np.ndarray:
        rows = selected_audio if channel is None else [row[channel] for row in values]
        return np.concatenate([piece for row in rows for piece in (row, silence)])

    audio_dir = args.output / "hard-test"
    paths = {
        "target": audio_dir / "target.wav",
        "baseline": audio_dir / "baseline.wav",
        "candidate": audio_dir / "candidate.wav",
        "remote": audio_dir / "remote.wav",
    }
    write_wave(paths["target"], joined(2))
    write_wave(paths["baseline"], joined(0))
    write_wave(paths["candidate"], joined(None))
    write_wave(paths["remote"], joined(1))
    asr_model = None
    asr: dict[str, Any] = {}
    for name, path in paths.items():
        asr[name], asr_model = V21.transcribe(
            path, audio_dir / f"{name}.faster_whisper.json", asr_model
        )
    texts = {
        name: " ".join(str(row.get("text") or "") for row in payload.get("segments", []))
        for name, payload in asr.items()
    }
    token_rows = {name: V21.tokens(text) for name, text in texts.items()}
    baseline_recall = V21.token_recall(token_rows["target"], token_rows["baseline"])
    candidate_recall = V21.token_recall(token_rows["target"], token_rows["candidate"])
    remote_unique = V21.remote_unique_tokens(token_rows["remote"], token_rows["target"])
    baseline_remote = len(remote_unique & set(token_rows["baseline"]))
    candidate_remote = len(remote_unique & set(token_rows["candidate"]))
    baseline_lcs = V21.lcs_length(token_rows["target"], token_rows["baseline"])
    candidate_lcs = V21.lcs_length(token_rows["target"], token_rows["candidate"])
    metrics = evaluation["metrics"]
    correlation_regressions = sum(
        CORE.correlation(row[2], selected) + 1.0e-8 < CORE.correlation(row[2], row[0])
        for row, selected in zip(values, selected_audio)
    )
    gates = {
        "known_target_snr_improvement_p50_gte_1db": metrics["snr_improvement_db"]["p50"] >= 1.0,
        "known_target_snr_improvement_min_gte_0db": metrics["snr_improvement_db"]["min"] >= 0.0,
        "known_target_correlation_regressions_eq_0": correlation_regressions == 0,
        "direct_asr_local_recall_ratio_gte_0_99": (
            candidate_recall / max(baseline_recall, 1.0e-9) >= 0.99
        ),
        "direct_asr_remote_unique_tokens_not_increased": candidate_remote <= baseline_remote,
        "direct_asr_chronology_not_worse": candidate_lcs >= baseline_lcs,
        "candidate_selection_ratio_gte_0_20": evaluation["selections"]["candidate_ratio"] >= 0.20,
        "candidate_finite": all(np.all(np.isfinite(row)) for row in selected_audio),
        "candidate_not_clipped": max(
            float(np.mean(np.abs(row) >= 0.995)) for row in selected_audio
        )
        <= 0.0001,
    }
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_evaluation/v2.3",
        "hard_fingerprint": hard_manifest["fingerprint"],
        "evaluation": evaluation,
        "direct_asr": {
            "baseline_local_token_recall": round(baseline_recall, 6),
            "candidate_local_token_recall": round(candidate_recall, 6),
            "baseline_remote_unique_token_count": baseline_remote,
            "candidate_remote_unique_token_count": candidate_remote,
            "baseline_target_lcs": baseline_lcs,
            "candidate_target_lcs": candidate_lcs,
            "target_token_count": len(token_rows["target"]),
            "text_sha256": {
                name: hashlib.sha256(text.encode("utf-8")).hexdigest()
                for name, text in texts.items()
            },
        },
        "correlation_regressions": correlation_regressions,
        "gates": gates,
        "passed": all(gates.values()),
        "post_asr_cleanup_credit": 0,
    }
    report_path = audio_dir / "hard_evaluation.json"
    write_json(report_path, report)
    attempt.update(
        {
            "status": "completed",
            "passed": report["passed"],
            "report": relative(report_path),
            "report_sha256": CORE.sha256(report_path),
        }
    )
    write_json(attempt_path, attempt)
    decision = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_decision/v2.3",
        "decision": "HARD_TEST_PASSED_V2_3" if report["passed"] else "DO_NOT_PROMOTE",
        "candidate_lock_fingerprint": lock["fingerprint"],
        "hard_report_sha256": attempt["report_sha256"],
        "gates": gates,
    }
    write_json(args.output / "hard_test_decision.json", decision)
    print(
        json.dumps(
            {"attempt": attempt, "report": report, "decision": decision},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if report["passed"] else 6


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    if args.command == "evaluate-dev":
        return command_evaluate_dev(args)
    if args.command == "lock":
        return command_lock(args)
    if args.command == "hard-test":
        return command_hard_test(args)
    raise RuntimeError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
