#!/usr/bin/env python3
"""Lock and evaluate the improvement-only Speaker-Preserving Neural Echo v2.1."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

import speaker_preserving_echo_arbitration as ARBITER
import speaker_preserving_neural_echo_v2 as CORE


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-1.json"
OUTPUT = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-1"
DEV_CACHE = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2/cache"


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


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.1":
        raise RuntimeError("unexpected v2.1 policy schema")
    source = policy["source"]
    pairs = (
        ("training_policy", "training_policy_sha256"),
        ("failed_candidate_lock", "failed_candidate_lock_sha256"),
        ("failed_hard_decision", "failed_hard_decision_sha256"),
        ("checkpoint", "checkpoint_sha256"),
        ("arbitration", "arbitration_sha256"),
        ("hard_builder", "hard_builder_sha256"),
        ("hard_manifest", "hard_manifest_sha256"),
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
    hard_manifest = read_json(ROOT / str(source["hard_manifest"]))
    semantic = {
        "first_candidate_rejected": (
            read_json(ROOT / str(source["failed_hard_decision"])).get("decision")
            == "DO_NOT_PROMOTE"
        ),
        "new_hard_sealed": hard_manifest.get("status") == "sealed_unopened",
        "new_hard_fingerprint": hard_manifest.get("fingerprint") == source["hard_fingerprint"],
        "new_hard_not_consumed": hard_manifest.get("evaluation_attempts_consumed") == 0,
    }
    passed = all(row["passed"] for row in artifacts.values()) and all(semantic.values())
    if not passed:
        raise RuntimeError("v2.1 frozen source verification failed")
    return {"policy": policy, "artifacts": artifacts, "semantic": semantic, "passed": passed}


def checkpoint(policy: dict[str, Any]) -> tuple[Any, Path]:
    path = ROOT / str(policy["source"]["checkpoint"])
    model, _ = CORE.load_checkpoint(path, device="cpu")
    model.eval()
    return model, path


def apply_model_batch(model: Any, values: np.ndarray) -> np.ndarray:
    import torch

    window = torch.from_numpy(CORE.analysis_window())
    with torch.no_grad():
        output, _ = CORE.apply_model(
            model,
            torch.from_numpy(values[:, 0].astype(np.float32)),
            torch.from_numpy(values[:, 1].astype(np.float32)),
            window,
            torch.from_numpy(values[:, 3].astype(np.float32)),
        )
    return output.numpy().astype(np.float32)


def evaluate_waveforms(values: np.ndarray, model: Any) -> dict[str, Any]:
    kinds: list[str] = []
    metrics: dict[str, list[float]] = defaultdict(list)
    selections: list[dict[str, Any]] = []
    output_hash = __import__("hashlib").sha256()
    started = time.monotonic()
    for start in range(0, values.shape[0], 16):
        batch = values[start : start + 16].astype(np.float32)
        proposed = apply_model_batch(model, batch)
        for offset, candidate in enumerate(proposed):
            baseline, remote, target = batch[offset, 0], batch[offset, 1], batch[offset, 2]
            selected, arbitration = ARBITER.arbitrate(
                baseline=baseline,
                candidate=candidate,
                remote=remote,
            )
            output_hash.update(selected.tobytes())
            baseline_snr = CORE.snr_db(target, baseline)
            selected_snr = CORE.snr_db(target, selected)
            metrics["baseline_snr_db"].append(baseline_snr)
            metrics["selected_snr_db"].append(selected_snr)
            metrics["snr_improvement_db"].append(selected_snr - baseline_snr)
            metrics["baseline_target_correlation"].append(CORE.correlation(target, baseline))
            metrics["selected_target_correlation"].append(CORE.correlation(target, selected))
            selections.append(arbitration)
            kinds.append("synthetic_double_talk")
    runtime = time.monotonic() - started
    return {
        "metrics": {key: CORE.percentile_summary(series) for key, series in metrics.items()},
        "selections": {
            "candidate": sum(row["selected"] == "candidate" for row in selections),
            "baseline": sum(row["selected"] == "baseline" for row in selections),
            "candidate_ratio": round(
                sum(row["selected"] == "candidate" for row in selections)
                / max(len(selections), 1),
                6,
            ),
            "reasons": dict(
                sorted(
                    {
                        reason: sum(reason in row["reasons"] for row in selections)
                        for reason in {reason for row in selections for reason in row["reasons"]}
                    }.items()
                )
            ),
        },
        "candidate_output_stream_sha256": output_hash.hexdigest(),
        "runtime_sec": round(runtime, 6),
        "realtime_factor": round(runtime / (values.shape[0] * 4.0), 6),
        "selected_waveforms": selections,
    }


def dev_values() -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    manifest = read_json(DEV_CACHE / "dev_cache_manifest.json")
    waveforms = DEV_CACHE / "dev_waveforms.npy"
    kinds = DEV_CACHE / "dev_kinds.npy"
    if CORE.sha256(waveforms) != manifest["artifacts"]["waveforms"]["sha256"]:
        raise RuntimeError("dev waveforms changed")
    if CORE.sha256(kinds) != manifest["artifacts"]["kinds"]["sha256"]:
        raise RuntimeError("dev kinds changed")
    return np.load(waveforms, mmap_mode="r"), np.load(kinds, mmap_mode="r"), manifest


def command_evaluate_dev(args: argparse.Namespace) -> int:
    verification = verify_policy(args.policy)
    policy = verification["policy"]
    model, checkpoint_path = checkpoint(policy)
    values, kinds, manifest = dev_values()
    synthetic = np.asarray(values[kinds == CORE.KIND_IDS["synthetic_double_talk"]], dtype=np.float32)
    evaluation = evaluate_waveforms(synthetic, model)
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
        proposed = apply_model_batch(model, subset)
        for row, candidate in zip(subset, proposed):
            selected, _ = ARBITER.arbitrate(
                baseline=row[0], candidate=candidate, remote=row[1]
            )
            negative_count += 1
            negative_exact = negative_exact and np.array_equal(selected, row[0])
    controlled_root = (
        ROOT
        / "sessions/_reports/speaker-preserving-neural-echo-v2/controlled-dev-validation/"
        "2026-08-02_22-57-31-echo-dev-normal"
    )
    import soundfile as sf

    baseline = sf.read(
        ROOT
        / "sessions/2026-08-02_22-57-31-echo-dev-normal/derived/echo-lab/analysis/"
        "controlled_double_talk.mic_clean_local_fir.wav",
        dtype="float32",
    )[0]
    proposed = sf.read(controlled_root / "controlled_double_talk.candidate.wav", dtype="float32")[0]
    remote = sf.read(
        ROOT
        / "sessions/2026-08-02_22-57-31-echo-dev-normal/derived/echo-lab/phases/"
        "10_controlled_double_talk_remote.wav",
        dtype="float32",
    )[0]
    selected, controlled_arbitration = ARBITER.arbitrate(
        baseline=baseline,
        candidate=proposed,
        remote=remote[: baseline.size],
    )
    controlled_exact = np.array_equal(selected, baseline)
    metrics = evaluation["metrics"]
    gates = {
        "synthetic_snr_improvement_p50_gte_3db": metrics["snr_improvement_db"]["p50"] >= 3.0,
        "synthetic_snr_improvement_min_gte_0db": metrics["snr_improvement_db"]["min"] >= 0.0,
        "synthetic_candidate_ratio_gte_0_75": evaluation["selections"]["candidate_ratio"] >= 0.75,
        "negative_exact_fallback": negative_exact,
        "controlled_dev_exact_fallback": controlled_exact,
        "cpu_realtime_factor_lte_0_10": evaluation["realtime_factor"] <= 0.10,
    }
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_dev_evaluation/v2.1",
        "policy_sha256": CORE.sha256(args.policy),
        "checkpoint_sha256": CORE.sha256(checkpoint_path),
        "dev_cache_fingerprint": manifest["fingerprint"],
        "evaluation": {key: value for key, value in evaluation.items() if key != "selected_waveforms"},
        "negative_exact_examples": negative_count,
        "controlled_dev_arbitration": controlled_arbitration,
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
        raise RuntimeError("v2.1 dev evaluation is stale or failed")
    first_hash = report["evaluation"]["candidate_output_stream_sha256"]
    command_evaluate_dev(args)
    replay = read_json(report_path)
    if replay["evaluation"]["candidate_output_stream_sha256"] != first_hash:
        raise RuntimeError("v2.1 dev replay is not deterministic")
    policy = verification["policy"]
    basis = {
        "policy_sha256": CORE.sha256(args.policy),
        "evaluator_sha256": CORE.sha256(Path(__file__)),
        "arbitration_sha256": CORE.sha256(Path(ARBITER.__file__)),
        "checkpoint_sha256": policy["source"]["checkpoint_sha256"],
        "dev_evaluation_sha256": CORE.sha256(report_path),
        "hard_manifest_sha256": policy["source"]["hard_manifest_sha256"],
        "hard_fingerprint": policy["source"]["hard_fingerprint"],
    }
    lock = {
        "schema": "murmurmark.speaker_preserving_neural_echo_candidate_lock/v2.1",
        "status": "locked_for_single_hard_test",
        "basis": basis,
        "fingerprint": CORE.digest_json(basis),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    lock_path = args.output / "candidate_lock.json"
    write_json(lock_path, lock)
    unlock = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_unlock/v2.1",
        "candidate_lock_sha256": CORE.sha256(lock_path),
        "candidate_lock_fingerprint": lock["fingerprint"],
        "attempts_allowed": 1,
        "attempts_consumed": 0,
    }
    write_json(args.output / "hard_test_unlock.json", unlock)
    print(json.dumps(lock, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_wave(path: Path, values: np.ndarray) -> None:
    import soundfile as sf

    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, values.astype(np.float32), CORE.SAMPLE_RATE, subtype="FLOAT")


def transcribe(path: Path, cache: Path, model: Any | None) -> tuple[dict[str, Any], Any]:
    lab = load_module(
        ROOT / "scripts/controlled-echo-supervision-lab.py",
        "murmurmark_spne_v21_asr",
    )
    model_path = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
    loaded = model or lab.load_faster_whisper_model(model_path)
    payload = lab.transcribe_words(
        path,
        model_path=model_path,
        cache_path=cache,
        source_sha256=CORE.sha256(path),
        model_instance=loaded,
    )
    return payload, loaded


def tokens(text: str) -> list[str]:
    contract = load_module(
        ROOT / "scripts/controlled_echo_supervision.py",
        "murmurmark_spne_v21_tokens",
    )
    return contract.tokens(text)


def token_recall(expected: list[str], observed: list[str]) -> float:
    observed_set = set(observed)
    return sum(token in observed_set for token in expected) / max(len(expected), 1)


def remote_unique_tokens(remote: list[str], local: list[str]) -> set[str]:
    local_set = set(local)
    return {
        token
        for token in remote
        if token not in local_set
        and not any(
            len(token) >= 5 and len(local_token) >= 5 and token[:4] == local_token[:4]
            for local_token in local_set
        )
    }


def lcs_length(left: list[str], right: list[str]) -> int:
    previous = [0] * (len(right) + 1)
    for left_value in left:
        current = [0]
        for index, right_value in enumerate(right, 1):
            current.append(
                previous[index - 1] + 1
                if left_value == right_value
                else max(previous[index], current[-1])
            )
        previous = current
    return previous[-1]


def command_hard_test(args: argparse.Namespace) -> int:
    verification = verify_policy(args.policy)
    policy = verification["policy"]
    lock_path = args.output / "candidate_lock.json"
    unlock_path = args.output / "hard_test_unlock.json"
    lock, unlock = read_json(lock_path), read_json(unlock_path)
    if unlock.get("attempts_consumed") != 0:
        raise RuntimeError("v2.1 hard-test attempt already consumed")
    if CORE.sha256(lock_path) != unlock.get("candidate_lock_sha256"):
        raise RuntimeError("v2.1 lock changed")
    if lock["basis"]["evaluator_sha256"] != CORE.sha256(Path(__file__)):
        raise RuntimeError("v2.1 evaluator changed after lock")
    if lock["basis"]["arbitration_sha256"] != CORE.sha256(Path(ARBITER.__file__)):
        raise RuntimeError("v2.1 arbitration changed after lock")
    attempt_path = args.output / "hard_test_attempt.json"
    if attempt_path.exists():
        raise RuntimeError("v2.1 hard-test marker already exists")
    attempt = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_attempt/v2.1",
        "status": "started",
        "candidate_lock_fingerprint": lock["fingerprint"],
    }
    write_json(attempt_path, attempt)
    unlock["attempts_consumed"] = 1
    write_json(unlock_path, unlock)
    hard_manifest_path = ROOT / str(policy["source"]["hard_manifest"])
    hard_manifest = read_json(hard_manifest_path)
    cache_path = ROOT / str(hard_manifest["cache"]["path"])
    if CORE.sha256(cache_path) != hard_manifest["cache"]["sha256"]:
        raise RuntimeError("v2.1 hard cache changed")
    values = np.load(cache_path)["waveforms"].astype(np.float32)
    model, checkpoint_path = checkpoint(policy)
    evaluation = evaluate_waveforms(values, model)
    selected_audio: list[np.ndarray] = []
    for start in range(0, values.shape[0], 16):
        batch = values[start : start + 16]
        proposed = apply_model_batch(model, batch)
        for row, candidate in zip(batch, proposed):
            selected, _ = ARBITER.arbitrate(
                baseline=row[0], candidate=candidate, remote=row[1]
            )
            selected_audio.append(selected)
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
        asr[name], asr_model = transcribe(path, audio_dir / f"{name}.faster_whisper.json", asr_model)
    texts = {
        name: " ".join(str(row.get("text") or "") for row in payload.get("segments", []))
        for name, payload in asr.items()
    }
    token_rows = {name: tokens(text) for name, text in texts.items()}
    baseline_recall = token_recall(token_rows["target"], token_rows["baseline"])
    candidate_recall = token_recall(token_rows["target"], token_rows["candidate"])
    remote_unique = remote_unique_tokens(token_rows["remote"], token_rows["target"])
    baseline_remote = len(remote_unique & set(token_rows["baseline"]))
    candidate_remote = len(remote_unique & set(token_rows["candidate"]))
    baseline_lcs = lcs_length(token_rows["target"], token_rows["baseline"])
    candidate_lcs = lcs_length(token_rows["target"], token_rows["candidate"])
    metrics = evaluation["metrics"]
    correlation_regressions = sum(
        CORE.correlation(row[2], selected) + 1.0e-8
        < CORE.correlation(row[2], row[0])
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
        "candidate_not_clipped": max(float(np.mean(np.abs(row) >= 0.995)) for row in selected_audio) <= 0.0001,
    }
    report = {
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_evaluation/v2.1",
        "hard_fingerprint": hard_manifest["fingerprint"],
        "checkpoint_sha256": CORE.sha256(checkpoint_path),
        "evaluation": {key: value for key, value in evaluation.items() if key != "selected_waveforms"},
        "direct_asr": {
            "baseline_local_token_recall": round(baseline_recall, 6),
            "candidate_local_token_recall": round(candidate_recall, 6),
            "baseline_remote_unique_token_count": baseline_remote,
            "candidate_remote_unique_token_count": candidate_remote,
            "baseline_target_lcs": baseline_lcs,
            "candidate_target_lcs": candidate_lcs,
            "target_token_count": len(token_rows["target"]),
            "text_sha256": {
                name: __import__("hashlib").sha256(text.encode("utf-8")).hexdigest()
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
        "schema": "murmurmark.speaker_preserving_neural_echo_hard_decision/v2.1",
        "decision": "HARD_TEST_PASSED_V2_1" if report["passed"] else "DO_NOT_PROMOTE",
        "candidate_lock_fingerprint": lock["fingerprint"],
        "hard_report_sha256": attempt["report_sha256"],
        "gates": gates,
    }
    write_json(args.output / "hard_test_decision.json", decision)
    print(json.dumps({"attempt": attempt, "report": report, "decision": decision}, ensure_ascii=False, indent=2, sort_keys=True))
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
