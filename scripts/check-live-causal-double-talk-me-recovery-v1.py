#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import soundfile as sf
from scipy import signal


def load_module(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ProgressiveStub:
    @staticmethod
    def tokens(value: Any) -> list[str]:
        return re.findall(r"[a-zа-я0-9]+", str(value or "").lower())

    @classmethod
    def asr_text_similarity(cls, left: Any, right: Any) -> float:
        left_tokens = set(cls.tokens(left))
        right_tokens = set(cls.tokens(right))
        if not left_tokens or not right_tokens:
            return 0.0
        return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

    @staticmethod
    def token_average_probability(path: Path | None) -> float:
        return 0.91 if path and path.is_file() else 0.0

    @staticmethod
    def asr_rows(path: Path | None) -> list[dict[str, Any]]:
        if not path or not path.is_file():
            return []
        payload = json.loads(path.read_text(encoding="utf-8"))
        return list(payload.get("transcription") or [])


def selection(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "selection_000002_0001",
        "chunk_index": 2,
        "start": 35.0,
        "end": 39.0,
        "duration_sec": 4.0,
        "text": "проверить локальную реплику пользователя",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "checks": {
            "timeline_causal": True,
            "selection_does_not_use_batch": True,
            "recording_time_committed_pcm": True,
            "past_only_enrollment": True,
            "past_enrollment_ready": True,
            "speaker_supported": True,
            "source_text_contentful": True,
            "not_already_published": True,
        },
        "recording_time_evidence": {"status": "passed"},
        "remote_audio_guard": {"remote_db": -28.0},
        "speaker_evidence": {"scores": {"target": 0.42}},
        "source_window": {"mode": "causal_boundary_bridge"},
    }
    row.update(overrides)
    return row


def accepted_candidate(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "causal_double_talk_fixture",
        "status": "accepted",
        "classification": "genuine_double_talk",
        "chunk_index": 2,
        "start": 35.0,
        "end": 39.0,
        "text": "проверить локальную реплику пользователя",
        "timeline_causal": True,
        "used_batch_fields_for_selection": False,
        "selection_mode": "recording_time_causal_double_talk_me_recovery_v1",
        "causal_echo_training": {"status": "passed", "past_only": True},
        "independent_evidence": {
            "acceptance_mode": "multi_residual_family_consensus",
            "target_me_family_count": 2,
            "local_asr_consensus": True,
            "remote_text_forbiddance": True,
            "remote_audio_forbiddance": True,
        },
        "publication_allowed": False,
        "promotion_allowed": False,
        "batch_authoritative": True,
    }
    row.update(overrides)
    return row


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def check_selection_contract(module: ModuleType) -> None:
    selected, decisions = module.eligible_selections([selection()], None)
    assert len(selected) == 1 and decisions[0]["status"] == "selected", decisions
    assert decisions[0]["used_batch_fields_for_selection"] is False
    selected_repeat, decisions_repeat = module.eligible_selections([selection()], None)
    assert selected_repeat == selected
    assert decisions_repeat == decisions

    mutated = selection(batch_text="future authoritative answer", batch_start=-900.0)
    selected_mutated, decisions_mutated = module.eligible_selections([mutated], None)
    assert len(selected_mutated) == 1, decisions_mutated
    assert decisions_mutated[0]["checks"] == decisions[0]["checks"]

    future_enrollment = selection(
        checks={**selection()["checks"], "past_only_enrollment": False}
    )
    rejected, rejected_decisions = module.eligible_selections([future_enrollment], None)
    assert not rejected
    assert "past_only_enrollment" in rejected_decisions[0]["reasons"]

    quiet = selection(remote_audio_guard={"remote_db": -90.0})
    rejected, rejected_decisions = module.eligible_selections([quiet], None)
    assert not rejected
    assert "remote_audio_active" in rejected_decisions[0]["reasons"]


def check_runtime_scope_and_priority(module: ModuleType) -> None:
    supported = selection(id="supported", start=10.0, end=11.0)
    adjacent = selection(
        id="adjacent",
        start=12.0,
        end=13.0,
        checks={**selection()["checks"], "speaker_supported": False},
        speaker_evidence={"scores": {"target": 0.02}},
    )
    unsupported = selection(
        id="unsupported",
        start=20.0,
        end=21.0,
        checks={**selection()["checks"], "speaker_supported": False},
        speaker_evidence={"scores": {"target": 0.02}},
    )
    scoped = module.runtime_selection_scope([unsupported, adjacent, supported])
    assert [row["id"] for row in scoped] == ["supported", "adjacent"], scoped
    assert module.runtime_group_priority([supported])[0] == 1
    non_bridge = selection(source_window={"mode": "ordinary"})
    assert module.runtime_group_priority([supported]) > module.runtime_group_priority([non_bridge])


def check_past_only_training(module: ModuleType) -> None:
    progressive = ProgressiveStub()

    def training(start: float, end: float) -> dict[str, Any]:
        return {
            "chunk_index": 1,
            "start": start,
            "end": end,
            "duration_sec": end - start,
            "text": "remote phrase",
            "remote_text": "remote phrase",
            "classification": "not_supported",
            "timeline_causal": True,
            "used_batch_fields_for_selection": False,
            "audio": {"remote_db": -25.0, "mic_minus_remote_db": -8.0, "corr": 0.3},
        }

    class Audio:
        @staticmethod
        def pair(_chunk: int, start: float, end: float) -> tuple[np.ndarray, np.ndarray]:
            count = int(round((end - start) * module.SAMPLE_RATE))
            remote = np.linspace(-0.1, 0.1, count, dtype=np.float64)
            return remote, 0.7 * remote

    class Separation:
        @staticmethod
        def estimate_delay(_windows: Any) -> tuple[int, dict[str, Any]]:
            return 0, {"status": "fixture"}

        @staticmethod
        def fit_fir(_windows: Any, _delay: int, *, taps: int, regularization: float) -> np.ndarray:
            del regularization
            result = np.zeros(taps, dtype=np.float64)
            result[0] = 0.7
            return result

        @staticmethod
        def fit_spectral_transfer(_windows: Any, _delay: int, *, regularization: float) -> np.ndarray:
            del regularization
            return np.asarray([0.7])

    model = module.build_echo_model(
        target_start=20.0,
        evaluations=[training(1.0, 7.0), training(21.0, 27.0)],
        audio=Audio(),
        progressive=progressive,
        separation=Separation(),
    )
    assert model is not None
    assert len(model.training_rows) == 1
    assert float(model.training_rows[0]["end"]) <= model.target_start


def check_residual_families(module: ModuleType) -> None:
    rng = np.random.default_rng(42)
    count = 32_000
    remote = signal.lfilter([1.0], [1.0, -0.85], rng.normal(0.0, 0.04, count))
    local = signal.lfilter([1.0, -0.65], [1.0], rng.normal(0.0, 0.018, count))
    mic = 0.72 * remote + local

    class Separation:
        @staticmethod
        def shift_reference(value: np.ndarray, _delay: int) -> np.ndarray:
            return value

        @staticmethod
        def apply_spectral_transfer(value: np.ndarray, _transfer: np.ndarray) -> np.ndarray:
            return 0.72 * value

    filters: dict[str, np.ndarray] = {}
    for name, taps, _regularization in module.FIR_CONFIGS:
        fitted = np.zeros(taps, dtype=np.float64)
        fitted[0] = 0.72
        filters[name] = fitted
    model = module.CausalEchoModel(
        target_start=10.0,
        training_rows=[],
        training_seconds=6.0,
        delay_samples=0,
        delay_evidence={"status": "fixture"},
        fir_filters=filters,
        spectral_transfer=np.asarray([0.72]),
    )
    views = module.residual_views(model, remote, mic, Separation())
    assert {row["family"] for row in views} == {"fir", "spectral", "hybrid", "ratio_mask"}
    fir = next(row for row in views if row["method"] == "fir_80ms_reg_2e2")
    before_corr = abs(float(np.corrcoef(remote, mic)[0, 1]))
    after_corr = abs(float(np.corrcoef(remote, fir["residual"])[0, 1]))
    assert after_corr < before_corr * 0.25, (before_corr, after_corr)
    assert np.sqrt(np.mean(np.square(fir["residual"]))) > 0.005
    assert all(np.isfinite(row["residual"]).all() for row in views)

    remote_only = module.residual_views(model, remote, 0.72 * remote, Separation())
    remote_only_fir = next(row for row in remote_only if row["method"] == "fir_80ms_reg_2e2")
    assert np.sqrt(np.mean(np.square(remote_only_fir["residual"]))) < 1.0e-8


def check_rejection_classification(module: ModuleType) -> None:
    remote_leak = [
        {
            "text": "это удаленная реплика",
            "text_evidence": {"remote_guard_status": "rejected"},
            "audio_metrics": {"after": {"remote_strength": 0.7}},
        }
    ]
    assert module.classify_rejection(remote_leak, []) == "probable_remote_leak"

    asr_noise = [
        {
            "text": "да",
            "text_evidence": {"remote_guard_status": "passed"},
            "audio_metrics": {"after": {"remote_strength": 0.5}},
        }
    ]
    assert module.classify_rejection(asr_noise, []) == "probable_asr_noise"

    insufficient = [
        {
            "text": "локальная фраза",
            "text_evidence": {"remote_guard_status": "passed"},
            "audio_metrics": {"after": {"remote_strength": 0.5}},
        }
    ]
    assert (
        module.classify_rejection(insufficient, ["insufficient_causal_echo_training"])
        == "insufficient_evidence"
    )

    timing = [
        {
            "text": "локальная фраза",
            "text_evidence": {"remote_guard_status": "passed"},
            "audio_metrics": {"after": {"remote_strength": 0.08}},
        }
    ]
    assert module.classify_rejection(timing, []) == "probable_timing_overlap"
    assert module.classify_rejection(insufficient, []) == "insufficient_evidence"


def check_shadow_contract(compare: ModuleType) -> None:
    policy = compare.CAUSAL_DOUBLE_TALK_ME_RECOVERY_V1_PROFILE_POLICY
    assert policy in compare.MATERIALIZED_TARGET_ME_SHADOW_POLICIES
    assert policy not in compare.DEFAULT_TARGET_ME_SHADOW_POLICIES
    explicit = compare.selected_lab_policies(
        SimpleNamespace(only_lab_policy=[policy], with_labs=False, lab_policy=[])
    )
    assert explicit == (policy,)
    with tempfile.TemporaryDirectory(prefix="murmurmark-double-talk-shadow-") as temporary:
        session = Path(temporary) / "session"
        candidates = session / "derived/live/causal-double-talk-me-recovery-v1/candidates.jsonl"
        write_jsonl(candidates, [accepted_candidate()])
        turns, rejected = compare.causal_double_talk_me_recovery_v1_shadow_turns(session)
        assert len(turns) == 1 and not rejected, (turns, rejected)
        assert turns[0]["role"] == "Me"

        unsafe = accepted_candidate(
            independent_evidence={
                **accepted_candidate()["independent_evidence"],
                "remote_text_forbiddance": False,
            }
        )
        write_jsonl(candidates, [unsafe])
        turns, rejected = compare.causal_double_talk_me_recovery_v1_shadow_turns(session)
        assert not turns
        assert "remote_text_forbidden" in rejected[0]["failed_checks"]

        unsafe_audio = accepted_candidate(
            independent_evidence={
                **accepted_candidate()["independent_evidence"],
                "remote_audio_forbiddance": False,
            }
        )
        write_jsonl(candidates, [unsafe_audio])
        turns, rejected = compare.causal_double_talk_me_recovery_v1_shadow_turns(session)
        assert not turns
        assert "remote_audio_forbidden" in rejected[0]["failed_checks"]


def check_corrupt_whisper_cache(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-double-talk-cache-") as temporary:
        root = Path(temporary)
        model = root / "model.bin"
        model.write_bytes(b"fixture")
        wav = root / "clip.wav"
        sf.write(wav, np.zeros(1_600, dtype=np.float32), 16_000, subtype="PCM_16")
        counter = root / "counter.txt"
        fake = root / "whisper-cli"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json,os,sys\n"
            "from pathlib import Path\n"
            "args=sys.argv\n"
            "base=Path(args[args.index('--output-file')+1])\n"
            "counter=Path(os.environ['FAKE_WHISPER_COUNTER'])\n"
            "count=int(counter.read_text() if counter.exists() else '0')+1\n"
            "counter.write_text(str(count))\n"
            "base.with_suffix('.json').write_text(json.dumps({'transcription':[{'text':'локальная фраза'}]}))\n"
            "base.with_suffix('.txt').write_text('локальная фраза\\n')\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        output = root / "asr/candidate"
        output.parent.mkdir(parents=True)
        json_path = output.with_suffix(".json")
        txt_path = output.with_suffix(".txt")
        metadata_path = output.with_suffix(".cache.json")
        json_path.write_text("{}\n", encoding="utf-8")
        txt_path.write_text("stale\n", encoding="utf-8")
        wav_hash = hashlib.sha256(wav.read_bytes()).hexdigest()
        metadata_path.write_text(
            json.dumps(
                {
                    "backend": "whisper_cpp_cpu_large_v3_q5_0",
                    "wav_sha256": wav_hash,
                    "model": str(model.resolve()),
                    "language": "ru",
                    "threads": 6,
                    "beam_size": 1,
                    "best_of": 1,
                }
            ),
            encoding="utf-8",
        )
        os.environ["FAKE_WHISPER_COUNTER"] = str(counter)
        asr = module.WhisperCppCPUMicroASR(
            model,
            language="ru",
            whisper_cli=str(fake),
            progressive=ProgressiveStub(),
        )
        repaired = asr.run(wav, output, force=False)
        assert repaired["status"] == "passed" and repaired["cache_hit"] is False, repaired
        assert counter.read_text() == "1"
        cached = asr.run(wav, output, force=False)
        assert cached["cache_hit"] is True, cached
        assert counter.read_text() == "1"


def check_missing_model_fail_open(module: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-double-talk-missing-model-") as temporary:
        root = Path(temporary)
        wav = root / "clip.wav"
        sf.write(wav, np.zeros(1_600, dtype=np.float32), 16_000, subtype="PCM_16")
        asr = module.WhisperCppCPUMicroASR(
            root / "missing-model.bin",
            language="ru",
            whisper_cli=str(root / "missing-whisper-cli"),
            progressive=ProgressiveStub(),
        )
        result = asr.run(wav, root / "asr/candidate", force=False)
        assert result["status"] == "failed", result
        assert result["reason"] == "model_missing", result
        assert result["text"] == "", result


def check_stage_timeout_fail_open(runtime: ModuleType) -> None:
    with tempfile.TemporaryDirectory(prefix="murmurmark-double-talk-timeout-") as temporary:
        sleeper = Path(temporary) / "sleep.py"
        sleeper.write_text("import time; time.sleep(2)\n", encoding="utf-8")
        result = runtime.run_stage([sys.executable, str(sleeper)], timeout_sec=0.05)
        assert result["status"] == "timed_out_fail_open", result
        assert result["timed_out"] is True, result


def main() -> int:
    module = load_module(
        "live-causal-double-talk-me-recovery.py",
        "murmurmark_double_talk_recovery_check",
    )
    compare = load_module("compare-live-batch.py", "murmurmark_double_talk_compare_check")
    runtime = load_module(
        "live-causal-me-recovery-runtime.py",
        "murmurmark_double_talk_runtime_check",
    )
    check_selection_contract(module)
    check_runtime_scope_and_priority(module)
    check_past_only_training(module)
    check_residual_families(module)
    check_rejection_classification(module)
    check_shadow_contract(compare)
    check_corrupt_whisper_cache(module)
    check_missing_model_fail_open(module)
    check_stage_timeout_fail_open(runtime)
    print("live causal double-talk Me recovery v1 checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
