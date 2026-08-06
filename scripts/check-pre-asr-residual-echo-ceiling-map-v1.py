#!/usr/bin/env python3
"""Fixture checks for Pre-ASR Residual Echo Ceiling Map v1."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "scripts/pre-asr-residual-echo-ceiling-map-v1.py"


def load_runtime():
    spec = importlib.util.spec_from_file_location("murmurmark_residual_ceiling_fixture", RUNTIME)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load residual ceiling runtime")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


RUNTIME_MODULE = load_runtime()


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def asr(rows: list[tuple[int, int, str]]) -> dict[str, object]:
    return {
        "transcription": [
            {"offsets": {"from": start, "to": end}, "text": text}
            for start, end, text in rows
        ]
    }


def fixture(root: Path) -> tuple[Path, Path]:
    session_id = "fixture-session"
    session = root / "sessions" / session_id
    selector = session / "derived/preprocess/speaker-preserving-neural-echo-v2-15"
    transcript = session / "derived/transcript-simple/whisper-cpp/raw"
    echo = session / "derived/preprocess/echo"
    sample_rate = 16_000
    samples = sample_rate * 4
    rng = np.random.default_rng(17)
    remote = np.zeros(samples, dtype=np.float32)
    first = rng.normal(0.0, 0.08, int(1.5 * sample_rate)).astype(np.float32)
    second = rng.normal(0.0, 0.08, int(1.5 * sample_rate)).astype(np.float32)
    remote[: len(first)] = first
    remote[2 * sample_rate : 2 * sample_rate + len(second)] = second
    selected = remote * 0.45
    time = np.arange(len(second), dtype=np.float32) / sample_rate
    selected[2 * sample_rate : 2 * sample_rate + len(second)] += 0.05 * np.sin(2 * np.pi * 220 * time)
    baseline = selected.copy()
    candidate = selected.copy()

    for path, values in (
        (session / "derived/asr/mic.wav", baseline),
        (session / "derived/asr/remote.wav", remote),
        (selector / "candidate_clean_mic_pcm16.wav", candidate),
        (selector / "selected_clean_mic_pcm16.wav", selected),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, values, sample_rate, subtype="PCM_16")
    for path in (session / "audio/mic/000001.caf", session / "audio/remote/000001.caf"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"immutable raw fixture\n")

    rows = [(0, 1500, "Удаленный сигнал проверка"), (2000, 3500, "Удаленный ответ системы")]
    write_json(transcript / "mic.json", asr(rows))
    write_json(transcript / "remote.json", asr(rows))
    write_json(selector / "direct-asr/raw/mic.json", asr(rows))
    write_json(session / "session.json", {"schema": "fixture", "session_id": session_id})
    states = [
        {"start": 0.0, "end": 1.5, "state": "remote_only", "confidence": 1.0, "delay_ms": 0.0},
        {"start": 1.5, "end": 2.0, "state": "silence", "confidence": 1.0, "delay_ms": 0.0},
        {"start": 2.0, "end": 3.5, "state": "double_talk", "confidence": 1.0, "delay_ms": 0.0},
        {"start": 3.5, "end": 4.0, "state": "local_only", "confidence": 1.0, "delay_ms": 0.0},
    ]
    write_jsonl(echo / "speaker_state.jsonl", states)
    write_json(
        echo / "local_fir_report.json",
        {"acoustic_mode": {"mode": "speaker_playback"}, "parameters": {"delay_ms": 0.0}},
    )
    write_json(
        selector / "selection_report.json",
        {
            "status": "candidate",
            "reason": "fixture_candidate",
            "applicability": {"classification": "applicable_candidate"},
            "source_runtime": {
                "metrics": {
                    "remote_like_before": {"seconds": 3.0},
                    "remote_like_after": {"seconds": 3.0},
                }
            },
        },
    )
    write_jsonl(
        selector / "proposed_windows.jsonl",
        [
            {"start": 0.0, "end": 1.5, "proposal_id": "remote", "wavlm_mic_remote_similarity": 0.9, "resemblyzer_mic_remote_similarity": 0.9},
            {"start": 2.0, "end": 3.5, "proposal_id": "mixed", "wavlm_target_me_similarity": 0.9, "resemblyzer_target_me_similarity": 0.7},
        ],
    )
    write_jsonl(selector / "selected_windows.jsonl", [{"start": 0.0, "end": 1.5, "proposal_id": "remote"}])
    write_jsonl(selector / "rejected_windows.jsonl", [{"start": 2.0, "end": 3.5, "rejected_reason": "local_state_guard"}])

    policies = root / "policies"
    write_json(policies / "production.json", {"schema": "fixture-production"})
    write_json(
        policies / "corpus.json",
        {"sessions": [{"id": session_id, "expected_mode": "speaker_playback"}]},
    )
    source_policy = json.loads((ROOT / "policies/pre-asr-residual-echo-ceiling-map-v1.json").read_text(encoding="utf-8"))
    source_policy.update(
        {
            "production_policy": "policies/production.json",
            "production_corpus": "policies/corpus.json",
            "additional_discovery_sessions": [],
        }
    )
    source_policy["capability_decision"]["sessions_min"] = 1
    policy_path = policies / "residual-policy.json"
    write_json(policy_path, source_policy)
    return policy_path, root / "reports"


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="murmurmark-residual-ceiling-") as temp:
        root = Path(temp)
        policy, output = fixture(root)
        RUNTIME_MODULE.freeze_inputs(policy, output, root)
        first = RUNTIME_MODULE.run_corpus(policy, output, root)
        second = RUNTIME_MODULE.run_corpus(policy, output, root)
        verified = RUNTIME_MODULE.verify_outputs(policy, output, root, full_hash=True)
        events = RUNTIME_MODULE.read_jsonl(output / "residual_events.jsonl")
        assert first["corpus_fingerprint"] == second["corpus_fingerprint"]
        assert verified["passed"] is True
        assert len(events) == 2
        remote_event = next(row for row in events if row["start"] == 0.0)
        mixed_event = next(row for row in events if row["start"] == 2.0)
        assert remote_event["signal_truth"] == "confirmed_remote_echo", remote_event
        assert remote_event["production_blocker"] == "echo_path_mismatch", remote_event
        assert mixed_event["signal_truth"] == "mixed_double_talk", mixed_event
        assert mixed_event["production_blocker"] == "local_preservation_guard", mixed_event
        assert first["summary"]["reconciliation_passed_sessions"] == 1
        assert RUNTIME_MODULE.read_json(output / "decision.json")["promotion_authorized"] is False

    with tempfile.TemporaryDirectory(prefix="murmurmark-residual-ceiling-fallback-") as temp:
        root = Path(temp)
        policy, output = fixture(root)
        selector = root / "sessions/fixture-session/derived/preprocess/speaker-preserving-neural-echo-v2-15"
        (selector / "direct-asr/raw/mic.json").unlink()
        selection_report = RUNTIME_MODULE.read_json(selector / "selection_report.json")
        selection_report.update(
            {
                "status": "exact_fallback",
                "reason": "not_applicable_exact_fallback",
                "applicability": {"classification": "not_applicable_exact_fallback"},
            }
        )
        write_json(selector / "selection_report.json", selection_report)
        RUNTIME_MODULE.freeze_inputs(policy, output, root)
        result = RUNTIME_MODULE.run_corpus(policy, output, root)
        replay = RUNTIME_MODULE.run_corpus(policy, output, root)
        verified = RUNTIME_MODULE.verify_outputs(policy, output, root, full_hash=True)
        session_report = RUNTIME_MODULE.read_json(output / "session_reports/fixture-session.json")
        assert result["corpus_fingerprint"] == replay["corpus_fingerprint"]
        assert result["summary"]["reconciliation_passed_sessions"] == 1
        assert session_report["production"]["candidate_asr_available"] is False
        assert verified["passed"] is True
    print("pre-ASR residual echo ceiling map fixture ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
