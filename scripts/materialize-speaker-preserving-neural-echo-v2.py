#!/usr/bin/env python3
"""Materialize and guardedly apply Speaker-Preserving Neural Echo v2."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import soundfile as sf
from scipy import signal

import speaker_preserving_echo_arbitration as ARBITER
import speaker_preserving_echo_hypothesis_bank as BANK
import speaker_preserving_neural_echo_v2 as CORE
from echo_promotion_timeline import align_remote_constant


ROOT = Path(__file__).resolve().parent.parent
CANDIDATE_POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-2.json"
PRODUCTION_POLICY = ROOT / "policies/speaker-preserving-neural-echo-production-v2.json"
HARD_DECISION = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-2/hard_test_decision.json"
DEFAULT_MODEL = (
    Path.home()
    / ".local/share/murmurmark/models/speaker-preserving-neural-echo-v2/echo_mapper_h128_l1.pt"
)
SOURCE_CHECKPOINT = (
    ROOT
    / "sessions/_reports/speaker-preserving-neural-echo-v2/trials/echo_mapper_h128_l1/best.pt"
)
SAMPLE_RATE = 16_000
WINDOW_SAMPLES = 4 * SAMPLE_RATE
HOP_SAMPLES = 2 * SAMPLE_RATE
STATE_FADE_SAMPLES = int(0.05 * SAMPLE_RATE)
DOUBLE_TALK_STATES = {"double_talk", "double_talk_correlation"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install-model")
    install.add_argument("--source", type=Path, default=SOURCE_CHECKPOINT)
    install.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    materialize = sub.add_parser("materialize")
    materialize.add_argument("session", type=Path)
    materialize.add_argument("--model", type=Path, default=None)
    materialize.add_argument("--refresh", action="store_true")
    materialize.add_argument("--require-candidate", action="store_true")
    apply = sub.add_parser("apply-policy")
    apply.add_argument("session", type=Path)
    apply.add_argument("--policy", type=Path, default=PRODUCTION_POLICY)
    apply.add_argument("--model", type=Path, default=None)
    return parser.parse_args()


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


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


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


def model_path(value: Path | None) -> Path:
    configured = os.environ.get("MURMURMARK_SPEAKER_PRESERVING_ECHO_MODEL")
    return (value or (Path(configured).expanduser() if configured else DEFAULT_MODEL)).expanduser().resolve()


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V22 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-2.py",
    "murmurmark_speaker_preserving_echo_v22_runtime",
)


def command_install_model(args: argparse.Namespace) -> int:
    source = args.source.expanduser().resolve()
    destination = args.model.expanduser().resolve()
    policy = read_json(CANDIDATE_POLICY)
    expected = str(policy.get("source", {}).get("checkpoint_sha256") or "")
    if not source.is_file() or sha256(source) != expected:
        raise SystemExit("source checkpoint missing or changed")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)
    if sha256(destination) != expected:
        raise SystemExit("installed checkpoint hash mismatch")
    _, metadata = CORE.load_checkpoint(destination, device="cpu")
    manifest = {
        "schema": "murmurmark.speaker_preserving_neural_echo_model/v2",
        "model": str(destination),
        "sha256": expected,
        "candidate_policy": relative(CANDIDATE_POLICY),
        "candidate_policy_sha256": sha256(CANDIDATE_POLICY),
        "hard_decision": relative(HARD_DECISION),
        "hard_decision_sha256": sha256(HARD_DECISION) if HARD_DECISION.is_file() else None,
        "checkpoint_metadata": metadata,
    }
    write_json(destination.parent / "model_manifest.json", manifest)
    print(f"model: {destination}")
    print(f"sha256: {expected}")
    return 0


def read_audio(path: Path, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    values, sample_rate = sf.read(path, dtype="float32", always_2d=True)
    mono = values.mean(axis=1).astype(np.float32)
    if int(sample_rate) != target_rate:
        divisor = math.gcd(int(sample_rate), target_rate)
        mono = signal.resample_poly(
            mono, target_rate // divisor, int(sample_rate) // divisor
        ).astype(np.float32)
    return np.nan_to_num(mono).astype(np.float32)


def ensure_remote_16k(source: Path, output: Path, source_fingerprint: dict[str, Any]) -> Path:
    metadata_path = output.with_suffix(".json")
    existing = read_json(metadata_path)
    if (
        output.is_file()
        and existing.get("source") == source_fingerprint
        and existing.get("output_sha256") == sha256(output)
    ):
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_f32le",
        str(temporary),
    ]
    subprocess.run(command, check=True)
    os.replace(temporary, output)
    write_json(
        metadata_path,
        {
            "schema": "murmurmark.speaker_preserving_echo_remote_cache/v1",
            "source": source_fingerprint,
            "output_sha256": sha256(output),
        },
    )
    return output


def double_talk_mask(rows: list[dict[str, Any]], count: int) -> tuple[np.ndarray, list[tuple[int, int]]]:
    intervals: list[tuple[int, int]] = []
    for row in rows:
        if str(row.get("state") or "") not in DOUBLE_TALK_STATES:
            continue
        start = max(0, int(round(float(row.get("start") or 0.0) * SAMPLE_RATE)))
        end = min(count, int(round(float(row.get("end") or 0.0) * SAMPLE_RATE)))
        if end > start:
            intervals.append((start, end))
    merged: list[tuple[int, int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    mask = np.zeros(count, dtype=np.float32)
    for start, end in merged:
        mask[start:end] = 1.0
        fade = min(STATE_FADE_SAMPLES, max((end - start) // 2, 0))
        if fade:
            ramp = np.sin(np.linspace(0.0, np.pi / 2.0, fade, endpoint=False)) ** 2
            mask[start : start + fade] = ramp.astype(np.float32)
            mask[end - fade : end] = ramp[::-1].astype(np.float32)
    return mask, merged


def padded_slice(values: np.ndarray, start: int, size: int) -> np.ndarray:
    piece = values[start : min(start + size, values.size)]
    if piece.size < size:
        piece = np.pad(piece, (0, size - piece.size))
    return piece.astype(np.float32)


def verify_candidate_contract(model: Path) -> dict[str, Any]:
    policy = read_json(CANDIDATE_POLICY)
    hard = read_json(HARD_DECISION)
    expected_model = str(policy.get("source", {}).get("checkpoint_sha256") or "")
    checks = {
        "candidate_policy_schema": policy.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_policy/v2.2",
        "hard_test_passed": hard.get("decision") == "HARD_TEST_PASSED_V2_2",
        "model_exists": model.is_file(),
        "model_sha256": model.is_file() and sha256(model) == expected_model,
        "arbitration_sha256": sha256(Path(ARBITER.__file__))
        == policy.get("source", {}).get("arbitration_sha256"),
        "hypothesis_bank_sha256": sha256(Path(BANK.__file__))
        == policy.get("source", {}).get("hypothesis_bank_sha256"),
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "policy": policy,
        "hard_decision_sha256": sha256(HARD_DECISION) if HARD_DECISION.is_file() else None,
    }


def input_paths(session: Path) -> dict[str, Path]:
    audio = session / "derived/preprocess/audio"
    return {
        "baseline_clean": audio / "mic_clean_local_fir.wav",
        "baseline_role": audio / "mic_role_masked_for_asr.wav",
        "echo_estimate": audio / "echo_hat_local_fir.wav",
        "remote": audio / "remote_for_aec.wav",
        "speaker_state": session / "derived/preprocess/echo/speaker_state.jsonl",
        "local_fir_report": session / "derived/preprocess/echo/local_fir_report.json",
    }


def materialize(session: Path, model: Path, refresh: bool) -> dict[str, Any]:
    session = session.resolve()
    output = session / "derived/preprocess/speaker-preserving-neural-echo-v2"
    report_path = output / "runtime_report.json"
    candidate_path = output / "mic_for_asr.wav"
    windows_path = output / "windows.jsonl"
    paths = input_paths(session)
    missing = [relative(path, session) for path in paths.values() if not path.is_file()]
    contract = verify_candidate_contract(model)
    source_fingerprints = {
        key: fingerprint(path, session) for key, path in paths.items() if path.is_file()
    }
    basis = {
        "candidate_policy_sha256": sha256(CANDIDATE_POLICY),
        "hard_decision_sha256": contract["hard_decision_sha256"],
        "runtime_sha256": sha256(Path(__file__)),
        "model_sha256": sha256(model) if model.is_file() else None,
        "inputs": source_fingerprints,
    }
    existing = read_json(report_path)
    if (
        not refresh
        and existing.get("basis") == basis
        and candidate_path.is_file()
        and existing.get("output", {}).get("sha256") == sha256(candidate_path)
    ):
        return existing
    output.mkdir(parents=True, exist_ok=True)
    if missing or not contract["passed"]:
        baseline = paths["baseline_role"]
        if baseline.is_file():
            shutil.copy2(baseline, candidate_path)
        payload = {
            "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2",
            "status": "fallback",
            "reason": "missing_inputs" if missing else "candidate_contract_failed",
            "missing": missing,
            "contract": contract["checks"],
            "basis": basis,
            "fallback": "local_fir_role_masked",
            "output": fingerprint(candidate_path, session) if candidate_path.is_file() else None,
            "batch_authoritative": True,
        }
        write_json(report_path, payload)
        write_jsonl(windows_path, [])
        return payload

    started = time.monotonic()
    baseline_clean = read_audio(paths["baseline_clean"])
    baseline_role = read_audio(paths["baseline_role"])
    echo_estimate = read_audio(paths["echo_estimate"])
    remote_source_fingerprint = source_fingerprints["remote"]
    remote_cache = ensure_remote_16k(
        paths["remote"], output / "remote_16k.wav", remote_source_fingerprint
    )
    remote = read_audio(remote_cache)
    count = min(
        baseline_clean.size,
        baseline_role.size,
        echo_estimate.size,
        remote.size,
    )
    if count <= WINDOW_SAMPLES:
        raise RuntimeError("session is too short for v2 runtime")
    baseline_clean = baseline_clean[:count]
    baseline_role = baseline_role[:count]
    echo_estimate = echo_estimate[:count]
    remote = remote[:count]
    delay_ms = float(
        read_json(paths["local_fir_report"]).get("parameters", {}).get("delay_ms") or 0.0
    )
    aligned_remote = align_remote_constant(remote, SAMPLE_RATE, delay_ms).astype(np.float32)
    state_rows = read_jsonl(paths["speaker_state"])
    state_mask, intervals = double_talk_mask(state_rows, count)
    eligible_starts = [
        start
        for start in range(0, count, HOP_SAMPLES)
        if np.any(state_mask[start : min(start + WINDOW_SAMPLES, count)] > 0.0)
    ]
    model_instance, _ = CORE.load_checkpoint(model, device="cpu")
    model_instance.eval()
    delta_sum = np.zeros(count, dtype=np.float32)
    weight_sum = np.zeros(count, dtype=np.float32)
    window_weight = np.hanning(WINDOW_SAMPLES + 2)[1:-1].astype(np.float32)
    window_rows: list[dict[str, Any]] = []
    selection_counts: Counter[str] = Counter()
    inference_started = time.monotonic()
    for batch_start in range(0, len(eligible_starts), 16):
        starts = eligible_starts[batch_start : batch_start + 16]
        batch = np.stack(
            [
                np.stack(
                    (
                        padded_slice(baseline_role, start, WINDOW_SAMPLES),
                        padded_slice(aligned_remote, start, WINDOW_SAMPLES),
                        np.zeros(WINDOW_SAMPLES, dtype=np.float32),
                        padded_slice(echo_estimate, start, WINDOW_SAMPLES),
                    )
                )
                for start in starts
            ]
        )
        neural = V22.apply_model_batch(model_instance, batch)
        for start, row, neural_candidate in zip(starts, batch, neural):
            selected, decision = BANK.select(
                baseline=row[0],
                echo_estimate=row[3],
                remote=row[1],
                neural_candidate=neural_candidate,
            )
            selected_name = str(decision["selected"])
            selection_counts[selected_name] += 1
            valid = min(WINDOW_SAMPLES, count - start)
            state_weight = padded_slice(state_mask, start, WINDOW_SAMPLES)
            weight = window_weight * state_weight
            if selected_name != "baseline":
                delta = selected - row[0]
                delta_sum[start : start + valid] += delta[:valid] * weight[:valid]
                weight_sum[start : start + valid] += weight[:valid]
            winner = decision.get("winner") if isinstance(decision.get("winner"), dict) else {}
            window_rows.append(
                {
                    "schema": "murmurmark.speaker_preserving_neural_echo_window/v2",
                    "start_sec": round(start / SAMPLE_RATE, 6),
                    "end_sec": round(min(start + WINDOW_SAMPLES, count) / SAMPLE_RATE, 6),
                    "selected": selected_name,
                    "fail_open": bool(decision.get("fail_open")),
                    "baseline_remote_coherence": winner.get("baseline_remote_coherence"),
                    "candidate_remote_coherence": winner.get("candidate_remote_coherence"),
                }
            )
    inference_runtime = time.monotonic() - inference_started
    candidate = baseline_role.copy()
    active = weight_sum > 1.0e-8
    candidate[active] += delta_sum[active] / weight_sum[active]
    outside = state_mask <= 0.0
    checks = {
        "finite": bool(np.all(np.isfinite(candidate))),
        "clipped_sample_ratio_lte_0_0001": float(np.mean(np.abs(candidate) >= 0.995)) <= 0.0001,
        "outside_double_talk_exact": bool(np.array_equal(candidate[outside], baseline_role[outside])),
        "output_length_preserved": candidate.size == baseline_role.size,
    }
    if not all(checks.values()):
        candidate = baseline_role.copy()
        status = "fallback"
        reason = "runtime_output_gate_failed"
    elif not eligible_starts:
        status = "fallback"
        reason = "no_double_talk_intervals"
    elif selection_counts.get("baseline", 0) == len(eligible_starts):
        status = "fallback"
        reason = "no_hypothesis_improved_remote_evidence"
    else:
        status = "candidate"
        reason = "improvement_only_hypothesis_selected"
    sf.write(candidate_path, candidate, SAMPLE_RATE, subtype="FLOAT")
    write_jsonl(windows_path, window_rows)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2",
        "status": status,
        "reason": reason,
        "basis": basis,
        "contract": contract["checks"],
        "algorithm": {
            "profile": "speaker_preserving_neural_echo_v2",
            "revision": "v2.2_physical_hypothesis_bank",
            "sample_rate": SAMPLE_RATE,
            "window_sec": WINDOW_SAMPLES / SAMPLE_RATE,
            "hop_sec": HOP_SAMPLES / SAMPLE_RATE,
            "state_scope": sorted(DOUBLE_TALK_STATES),
            "delay_ms": delay_ms,
            "post_asr_cleanup_credit": 0,
        },
        "coverage": {
            "duration_sec": round(count / SAMPLE_RATE, 6),
            "double_talk_intervals": len(intervals),
            "double_talk_sec": round(float(np.count_nonzero(state_mask > 0.0)) / SAMPLE_RATE, 6),
            "eligible_windows": len(eligible_starts),
            "selection_counts": dict(sorted(selection_counts.items())),
            "candidate_window_ratio": round(
                (len(eligible_starts) - selection_counts.get("baseline", 0))
                / max(len(eligible_starts), 1),
                6,
            ),
        },
        "checks": checks,
        "runtime": {
            "inference_sec": round(inference_runtime, 6),
            "total_sec": round(time.monotonic() - started, 6),
            "inference_realtime_factor": round(
                inference_runtime / max(len(eligible_starts) * 4.0, 1.0), 6
            ),
        },
        "output": fingerprint(candidate_path, session),
        "windows": relative(windows_path, session),
        "fallback": "local_fir_role_masked",
        "batch_authoritative": True,
    }
    write_json(report_path, payload)
    return payload


def command_materialize(args: argparse.Namespace) -> int:
    report = materialize(args.session, model_path(args.model), args.refresh)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.require_candidate and report.get("status") != "candidate":
        return 4
    return 0


def valid_production_policy(path: Path) -> tuple[bool, dict[str, Any], str]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_production_policy/v2":
        return False, policy, "policy_missing_or_invalid"
    if policy.get("decision") != "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2":
        return False, policy, "corpus_policy_not_promoted"
    corpus_path = Path(str(policy.get("corpus_report") or ""))
    if not corpus_path.is_absolute():
        corpus_path = ROOT / corpus_path
    if not corpus_path.is_file() or sha256(corpus_path) != policy.get("corpus_report_sha256"):
        return False, policy, "stale_or_missing_corpus_report"
    corpus = read_json(corpus_path)
    if corpus.get("promotion", {}).get("decision") != policy.get("decision"):
        return False, policy, "corpus_decision_mismatch"
    if policy.get("candidate_policy_sha256") != sha256(CANDIDATE_POLICY):
        return False, policy, "candidate_policy_changed"
    return True, policy, "promoted_policy_verified"


def command_apply_policy(args: argparse.Namespace) -> int:
    session = args.session.resolve()
    policy_path = args.policy.expanduser().resolve()
    valid, policy, reason = valid_production_policy(policy_path)
    baseline = input_paths(session)["baseline_role"]
    mic_for_asr = session / "derived/preprocess/audio/mic_for_asr.wav"
    selected = "local_fir_role_masked"
    runtime_report: dict[str, Any] | None = None
    applied = False
    if valid:
        runtime_report = materialize(session, model_path(args.model), refresh=False)
        candidate = (
            session
            / "derived/preprocess/speaker-preserving-neural-echo-v2/mic_for_asr.wav"
        )
        if runtime_report.get("status") == "candidate" and candidate.is_file():
            shutil.copy2(candidate, mic_for_asr)
            selected = "speaker_preserving_neural_echo_v2"
            reason = "promoted_candidate_applied"
            applied = True
        else:
            reason = f"candidate_failed_open:{runtime_report.get('reason')}"
    if not applied and baseline.is_file():
        if not mic_for_asr.is_file() or sha256(mic_for_asr) != sha256(baseline):
            shutil.copy2(baseline, mic_for_asr)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_selection/v2",
        "policy": relative(policy_path),
        "policy_decision": policy.get("decision"),
        "selected": selected,
        "reason": reason,
        "candidate_applied": applied,
        "runtime_status": runtime_report.get("status") if runtime_report else None,
        "fallback": "local_fir_role_masked",
        "mic_for_asr": fingerprint(mic_for_asr, session) if mic_for_asr.is_file() else None,
        "batch_authoritative": True,
    }
    destination = session / "derived/preprocess/echo/speaker_preserving_neural_echo_selection.json"
    write_json(destination, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "install-model":
        return command_install_model(args)
    if args.command == "materialize":
        return command_materialize(args)
    if args.command == "apply-policy":
        return command_apply_policy(args)
    raise RuntimeError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
