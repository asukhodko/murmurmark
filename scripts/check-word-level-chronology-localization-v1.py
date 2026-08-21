#!/usr/bin/env python3
"""Regression checks for word-level chronology localization v1."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/report-word-level-chronology-localization-v1.py"


def load_module():
    spec = importlib.util.spec_from_file_location("word_level_chronology", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def word(text: str, start: float, end: float, probability: float = 0.95) -> dict:
    return {"text": text, "start": start, "end": end, "probability": probability}


def decode(alias: str, item_id: str, source: str, words: list[dict], config: dict, clip: Path) -> dict:
    return {
        "schema": "murmurmark.word_level_chronology_decode/v1",
        "alias": alias,
        "item_id": item_id,
        "source": source,
        "clip_sha256": load_module().sha256_file(clip),
        "decode_config": config,
        "result": {"status": "ok", "text": " ".join(row["text"] for row in words), "words": words},
    }


def fixture(root: Path) -> tuple[Path, Path, list[Path]]:
    upstream = root / "upstream"
    report = upstream / "report.json"
    private = upstream / "private_items.jsonl"
    manifest = upstream / "input_manifest.json"
    order_path = upstream / "order_items.jsonl"
    judge_path = upstream / "judge_items.jsonl"
    clips = root / "clips"
    clips.mkdir(parents=True)
    clip_paths: list[Path] = []
    order_rows: list[dict] = []
    judge_rows: list[dict] = []
    upstream_rows: list[dict] = []
    cases = [
        ("order_0001", "insufficient_evidence", {"local_evidence": 70, "local_only_ratio": 1.0, "double_talk_ratio": 0.0, "remote_only_ratio": 0.0}),
        ("order_0002", "insufficient_evidence", {"local_evidence": 70, "local_only_ratio": 0.0, "double_talk_ratio": 0.8, "remote_only_ratio": 0.0}),
        ("order_0003", "remote_leak_or_asr_segmentation", {"local_evidence": 0, "local_only_ratio": 0.0, "double_talk_ratio": 0.0, "remote_only_ratio": 1.0}),
        ("order_0004", "insufficient_evidence", {"local_evidence": 10, "local_only_ratio": 0.0, "double_talk_ratio": 0.0, "remote_only_ratio": 0.0}),
    ]
    for index, (item_id, outcome, evidence) in enumerate(cases, start=1):
        me_id = f"me_{index}"
        remote_id = f"remote_{index}"
        mic = clips / f"{item_id}_mic.wav"
        remote = clips / f"{item_id}_remote.wav"
        mic.write_bytes(f"mic-{index}".encode())
        remote.write_bytes(f"remote-{index}".encode())
        clip_paths.extend([mic, remote])
        order_rows.append(
            {
                "item_id": item_id,
                "label": "needs_review",
                "interval": {"start": index, "end": index + 1, "duration_sec": 1.0},
                "utterances": {
                    "me": {"id": me_id, "text": "локальная фраза"},
                    "remote": {"id": remote_id, "text": "удаленная реплика"},
                },
            }
        )
        judge_rows.append(
            {
                "utterance_ids": [me_id, remote_id],
                "clips": {"mic_clean": str(mic), "remote": str(remote)},
                "classification": {"label": "confirm_timing_or_doubletalk", "confidence": 0.9},
            }
        )
        upstream_rows.append(
            {
                "alias": "session_fixture",
                "item_id": item_id,
                "closed": False,
                "duration_sec": 1.0,
                "outcome": outcome,
                "utterance_ids": [me_id, remote_id],
                "evidence": evidence,
                "source_paths": {"order_items": str(order_path), "stronger_items": str(judge_path)},
            }
        )
    write_jsonl(order_path, order_rows)
    write_jsonl(judge_path, judge_rows)
    write_jsonl(private, upstream_rows)
    write_json(
        report,
        {
            "schema": "murmurmark.speaker_bounded_chronology_arbitration_report/v1",
            "decision": "PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1",
            "summary": {"frozen_items": 8, "frozen_seconds": 8.0, "closed_items": 4, "closed_seconds": 4.0},
        },
    )
    write_json(manifest, {"schema": "fixture.upstream/v1"})
    policy = root / "policy.json"
    write_json(
        policy,
        {
            "schema": "murmurmark.word_level_chronology_localization_policy/v1",
            "version": 1,
            "state": "fixture",
            "upstream_report": str(report),
            "upstream_private_items": str(private),
            "upstream_input_manifest": str(manifest),
            "model": {
                "default_path": str(root / "missing-model"),
                "environment_override": "FIXTURE_MODEL_OVERRIDE",
                "device": "cpu", "compute_type": "int8", "language": "ru", "beam_size": 1,
                "vad_filter": False, "condition_on_previous_text": False, "word_timestamps": True,
            },
            "thresholds": {
                "expected_residual_items": 4, "expected_residual_seconds": 4.0,
                "minimum_closed_item_ratio": 0.5, "minimum_closed_seconds_ratio": 0.5,
                "minimum_alignment_score": 0.6, "minimum_alignment_containment": 0.5,
                "minimum_word_probability": 0.2, "minimum_independent_local_margin": 0.12,
                "maximum_sequential_overlap_sec": 0.35, "minimum_double_talk_overlap_sec": 0.5,
                "minimum_remote_only_ratio": 0.8,
                "maximum_remote_only_local_active_ratio": 0.1,
                "maximum_remote_only_local_evidence": 20,
            },
            "safety": {
                "read_only": True, "raw_audio_mutation": False,
                "selected_transcript_mutation": False, "role_mutation": False,
                "timestamp_mutation": False, "primary_asr_mutation": False,
                "cloud_inference": False,
            },
            "privacy": {
                "public_session_ids": False, "public_absolute_paths": False,
                "public_speech_text": False, "private_provenance_under_sessions": True,
            },
        },
    )
    return policy, root / "out", clip_paths


def run(*args: str, expected: int = 0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == expected, (result.stdout, result.stderr)
    return result


def main() -> None:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-word-chronology-") as raw:
        root = Path(raw)
        policy, out, clips = fixture(root)
        original_hashes = {path: module.sha256_file(path) for path in clips}
        run("freeze", "--policy", str(policy), "--out-dir", str(out))
        manifest = module.read_json(out / "private/input_manifest.json")
        config = module.decode_config(manifest)
        run("decode", "--policy", str(policy), "--out-dir", str(out))
        run("evaluate", "--policy", str(policy), "--out-dir", str(out))
        unavailable = module.read_json(out / "word_level_chronology_localization_report.json")
        assert unavailable["decision"] == "EVIDENCE_BOUND"
        assert unavailable["summary"]["remaining_items"] == 4
        assert unavailable["summary"]["by_outcome"]["evidence_unavailable"]["items"] == 4
        rows: list[dict] = []
        for index in range(1, 5):
            item_id = f"order_{index:04d}"
            mic = root / "clips" / f"{item_id}_mic.wav"
            remote = root / "clips" / f"{item_id}_remote.wav"
            if index == 1:
                mic_words = [word("локальная", 0.0, 0.5), word("фраза", 0.5, 1.0)]
                remote_words = [word("удаленная", 1.6, 2.1), word("реплика", 2.1, 2.6)]
            elif index == 2:
                mic_words = [word("локальная", 0.0, 1.0), word("фраза", 1.0, 2.0)]
                remote_words = [word("удаленная", 1.0, 2.0), word("реплика", 2.0, 3.0)]
            elif index == 3:
                mic_words = [word("удаленная", 0.0, 0.5), word("реплика", 0.5, 1.0)]
                remote_words = [word("удаленная", 0.0, 0.5), word("реплика", 0.5, 1.0)]
            else:
                mic_words = []
                remote_words = []
            rows.append(decode("session_fixture", item_id, "mic_clean", mic_words, config, mic))
            rows.append(decode("session_fixture", item_id, "remote", remote_words, config, remote))
        write_jsonl(out / "private/word_decodes.jsonl", rows)
        run("evaluate", "--policy", str(policy), "--out-dir", str(out))
        report = module.read_json(out / "word_level_chronology_localization_report.json")
        assert report["decision"] == "PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1"
        assert report["summary"]["closed_items"] == 3
        assert report["summary"]["remaining_items"] == 1
        assert report["chronology"]["final_remaining_seconds"] == 1.0
        public = (out / "localization_items.jsonl").read_text(encoding="utf-8")
        for forbidden in (str(root), "локальная фраза", "удаленная реплика"):
            assert forbidden not in public
        run("replay", "--policy", str(policy), "--out-dir", str(out))
        replay = module.read_json(out / "replay_report.json")
        assert replay["exact_outputs"] is True
        assert original_hashes == {path: module.sha256_file(path) for path in clips}
        stale_decodes = [dict(row) for row in rows]
        stale_decodes[0]["clip_sha256"] = "0" * 64
        write_jsonl(out / "private/word_decodes.jsonl", stale_decodes)
        run("evaluate", "--policy", str(policy), "--out-dir", str(out), expected=2)
        incomplete = module.read_json(out / "word_level_chronology_localization_report.json")
        assert incomplete["decision"] == "EVIDENCE_INCOMPLETE"
        assert any("decode_clip_mismatch" in issue for issue in incomplete["issues"])
        write_jsonl(out / "private/word_decodes.jsonl", rows)
        run("evaluate", "--policy", str(policy), "--out-dir", str(out))
        clips[0].write_bytes(b"changed")
        run("evaluate", "--policy", str(policy), "--out-dir", str(out), expected=2)
        incomplete = module.read_json(out / "word_level_chronology_localization_report.json")
        assert incomplete["decision"] == "EVIDENCE_INCOMPLETE"
        assert any("mic_clean_stale" in issue for issue in incomplete["issues"])

    with tempfile.TemporaryDirectory(prefix="murmurmark-word-chronology-model-") as raw:
        root = Path(raw)
        policy, out, _ = fixture(root)
        model = root / "model"
        model.mkdir()
        (model / "model.bin").write_bytes(b"model-v1")
        payload = module.read_json(policy)
        payload["model"]["default_path"] = str(model)
        write_json(policy, payload)
        run("freeze", "--policy", str(policy), "--out-dir", str(out))
        manifest = module.read_json(out / "private/input_manifest.json")
        assert module.manifest_issues(manifest, policy.resolve()) == []
        (model / "model.bin").write_bytes(b"model-v2-longer")
        assert "model_files_stale" in module.manifest_issues(manifest, policy.resolve())

    swift = (ROOT / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text(encoding="utf-8")
    assert 'case "chronology-localization-v1"' in swift
    assert "report-word-level-chronology-localization-v1.py" in swift
    print("word-level chronology localization checks passed")


if __name__ == "__main__":
    main()
