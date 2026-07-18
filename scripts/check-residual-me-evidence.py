#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).with_name("residual-me-evidence.py")


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_residual_me_evidence", SCRIPT)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_sibling(name: str, module_name: str) -> Any:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def utterance(row_id: str, role: str, start: float, end: float, text: str) -> dict[str, Any]:
    return {
        "id": row_id,
        "role": role,
        "speaker_label": "Me" if role == "me" else "Colleagues",
        "source_track": "mic" if role == "me" else "remote",
        "start": start,
        "end": end,
        "text": text,
        "quality": {},
    }


def main() -> int:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-residual-me-") as raw_root:
        sessions_root = Path(raw_root) / "sessions"
        session = sessions_root / "fixture"
        input_paths = module.profile_paths(session, module.INPUT_PROFILE)
        dialogue = {
            "schema": "murmurmark.clean_dialogue/v1",
            "session": "fixture",
            "utterances": [
                utterance("remote_keep", "remote", 1.0, 2.0, "Удаленный ответ"),
                utterance("me_keep", "me", 1.1, 1.8, "Моя подтвержденная мысль"),
                utterance("remote_drop", "remote", 3.0, 4.0, "Повтор удаленной фразы"),
                utterance("me_drop", "me", 3.0, 4.0, "Повтор удаленной фразы"),
                utterance("remote_insert", "remote", 5.0, 6.0, "Другой удаленный ответ"),
                utterance("me_order", "me", 7.0, 9.0, "Спорная длинная реплика"),
                utterance("remote_order", "remote", 7.5, 8.5, "Пересекающийся ответ"),
                utterance("me_partial", "me", 10.7, 11.3, "существующая часть"),
            ],
        }
        quality = {"schema": "murmurmark.simple_transcript_quality/v1", "utterances": 8}
        write_json(input_paths["dialogue"], dialogue)
        write_json(input_paths["quality"], quality)
        write_json(input_paths["overlaps"], {"schema": "murmurmark.transcript_overlaps/v1", "overlaps": []})
        write_json(input_paths["dialogue"].parent / "transcribe_simple_report.json", {"model": "fixture.bin", "language": "ru"})
        for track in ("mic", "remote"):
            path = session / "audio" / track / "000001.caf"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((track * 16).encode("ascii"))

        queue = [
            {
                "schema": "murmurmark.residual_me_queue_item/v1",
                "session_id": "fixture",
                "residual_queue_id": "keep",
                "source": "audio_review",
                "source_audit_id": "audio_keep",
                "interval": {"start": 1.1, "end": 1.8, "duration_sec": 0.7},
                "utterance_ids": ["me_keep", "remote_keep"],
                "me_utterance_ids": ["me_keep"],
                "remote_utterance_ids": ["remote_keep"],
                "target_text": "Моя подтвержденная мысль",
                "remote_text": "Удаленный ответ",
            },
            {
                "schema": "murmurmark.residual_me_queue_item/v1",
                "session_id": "fixture",
                "residual_queue_id": "drop",
                "source": "audio_review",
                "source_audit_id": "audio_drop",
                "interval": {"start": 3.0, "end": 4.0, "duration_sec": 1.0},
                "utterance_ids": ["me_drop", "remote_drop"],
                "me_utterance_ids": ["me_drop"],
                "remote_utterance_ids": ["remote_drop"],
                "target_text": "Повтор удаленной фразы",
                "remote_text": "Повтор удаленной фразы",
            },
            {
                "schema": "murmurmark.residual_me_queue_item/v1",
                "session_id": "fixture",
                "residual_queue_id": "insert",
                "source": "local_recall",
                "source_audit_id": "local_insert",
                "interval": {"start": 5.1, "end": 5.8, "duration_sec": 0.7},
                "utterance_ids": [],
                "me_utterance_ids": [],
                "remote_utterance_ids": ["remote_insert"],
                "target_text": "Проверю локальные логи",
                "remote_text": "Другой удаленный ответ",
            },
            {
                "schema": "murmurmark.residual_me_queue_item/v1",
                "session_id": "fixture",
                "residual_queue_id": "order",
                "source": "transcript_order",
                "source_audit_id": "order_item",
                "interval": {"start": 7.5, "end": 8.5, "duration_sec": 1.0},
                "utterance_ids": ["me_order", "remote_order"],
                "me_utterance_ids": ["me_order"],
                "remote_utterance_ids": ["remote_order"],
                "target_text": "Спорная длинная реплика",
                "remote_text": "Пересекающийся ответ",
            },
            {
                "schema": "murmurmark.residual_me_queue_item/v1",
                "session_id": "fixture",
                "residual_queue_id": "partial",
                "source": "local_recall",
                "source_audit_id": "local_partial",
                "interval": {"start": 10.0, "end": 12.0, "duration_sec": 2.0},
                "utterance_ids": [],
                "me_utterance_ids": [],
                "remote_utterance_ids": [],
                "target_text": "Новая мысль существующая часть полезный хвост",
                "remote_text": "",
            },
        ]
        record = {
            "session_id": "fixture",
            "session": str(session),
            "input_profile": module.INPUT_PROFILE,
            "artifacts": module.artifact_fingerprints(session, module.INPUT_PROFILE),
            "raw_capture": module.raw_fingerprints(session),
            "residual_items": len(queue),
            "residual_seconds": sum(module.duration(row) for row in queue),
        }
        out_dir = sessions_root / "_reports/residual-me-evidence-v1"
        manifest = {
            "schema": "murmurmark.residual_me_evidence_baseline/v1",
            "queue": {
                "item_count": len(queue),
                "seconds": sum(module.duration(row) for row in queue),
                "sha256": module.sha256_bytes(module.canonical_bytes(queue)),
            },
            "sessions": [record],
        }
        write_json(out_dir / "baseline_manifest.json", manifest)
        write_jsonl(out_dir / "residual_queue.jsonl", queue)
        evidence = [
            {
                "schema": "murmurmark.residual_me_evidence/v1",
                "session_id": "fixture",
                "residual_queue_id": "keep",
                "disposition": {"action": "keep_me", "reason": "fixture_keep", "confidence": 0.95},
            },
            {
                "schema": "murmurmark.residual_me_evidence/v1",
                "session_id": "fixture",
                "residual_queue_id": "drop",
                "disposition": {"action": "drop_me", "reason": "fixture_drop", "confidence": 0.97},
            },
            {
                "schema": "murmurmark.residual_me_evidence/v1",
                "session_id": "fixture",
                "residual_queue_id": "insert",
                "disposition": {
                    "action": "insert_me",
                    "reason": "fixture_insert",
                    "confidence": 0.96,
                    "candidate_text": "Проверю локальные логи",
                    "selected_mic_source": "mic_clean",
                },
            },
            {
                "schema": "murmurmark.residual_me_evidence/v1",
                "session_id": "fixture",
                "residual_queue_id": "order",
                "disposition": {"action": "needs_review", "reason": "fixture_order", "confidence": 0.5},
            },
            {
                "schema": "murmurmark.residual_me_evidence/v1",
                "session_id": "fixture",
                "residual_queue_id": "partial",
                "disposition": {
                    "action": "insert_me",
                    "reason": "fixture_partial",
                    "confidence": 0.96,
                    "candidate_text": "Новая мысль существующая часть полезный хвост",
                    "candidate_words": [
                        {"start": 10.0, "end": 10.2, "text": "Новая"},
                        {"start": 10.2, "end": 10.5, "text": "мысль"},
                        {"start": 10.8, "end": 11.0, "text": "существующая"},
                        {"start": 11.0, "end": 11.2, "text": "часть"},
                        {"start": 11.5, "end": 11.7, "text": "полезный"},
                        {"start": 11.7, "end": 11.9, "text": "хвост"},
                    ],
                    "selected_mic_source": "mic_clean",
                },
            },
        ]
        evidence_path = session / "derived/audit/residual-me-evidence-v1/residual_me_evidence.jsonl"
        write_jsonl(evidence_path, evidence)
        args = argparse.Namespace(
            session=session,
            sessions_root=sessions_root,
            out_dir=out_dir,
            mode="conservative",
        )
        input_hashes = {name: module.sha256_file(path) for name, path in input_paths.items()}
        assert module.apply_session(args) == 0
        output = module.read_json(module.profile_paths(session, module.PROFILE)["dialogue"])
        assert output is not None
        rows = {str(row["id"]): row for row in output["utterances"]}
        assert "me_keep" in rows
        assert "me_drop" not in rows
        inserted = [row for row in output["utterances"] if str(row.get("id", "")).startswith("utt_rme_")]
        assert {row["text"] for row in inserted} == {
            "Проверю локальные логи",
            "Новая мысль",
            "полезный хвост",
        }
        assert rows["me_partial"]["text"] == "существующая часть"
        dispositions = module.read_jsonl(module.session_profile_dir(session) / "residual_me_dispositions.jsonl")
        partial = next(row for row in dispositions if row["residual_queue_id"] == "partial")
        assert partial["action"] == "insert_me_partial" and partial["closed"] is True
        assert rows["me_order"]["quality"]["needs_review"] is True
        remote_before = [row for row in dialogue["utterances"] if row["source_track"] == "remote"]
        remote_after = [row for row in output["utterances"] if row["source_track"] == "remote"]
        assert remote_before == remote_after
        first_fingerprint = module.output_fingerprint(module.profile_paths(session, module.PROFILE))
        assert module.apply_session(args) == 0
        assert first_fingerprint == module.output_fingerprint(module.profile_paths(session, module.PROFILE))
        assert input_hashes == {name: module.sha256_file(path) for name, path in input_paths.items()}

        profile_report = module.read_json(module.session_profile_dir(session) / "residual_me_evidence_profile_report.json")
        assert profile_report is not None
        corpus_report_path = out_dir / "residual_me_corpus_report.json"
        corpus_report = {
            "schema": "murmurmark.residual_me_evidence_corpus_report/v1",
            "decision": "PROMOTE_RESIDUAL_ME_EVIDENCE_V1",
            "baseline_manifest_sha256": module.sha256_file(out_dir / "baseline_manifest.json"),
            "gates": {"passed": True, "hard_failures": []},
            "promoted_sessions": ["fixture"],
            "sessions": [
                {
                    "session_id": "fixture",
                    "output_fingerprint": profile_report["output_fingerprint"],
                }
            ],
        }
        write_json(corpus_report_path, corpus_report)
        quality_module = load_sibling("report-session-quality.py", "murmurmark_residual_quality")
        synthesis_module = load_sibling("synthesize-simple-extractive.py", "murmurmark_residual_synthesis")
        operational_module = load_sibling("report-operational-readiness.py", "murmurmark_residual_operational")
        assert quality_module.residual_me_evidence_usable(session) is True
        assert quality_module.selected_profile(session) == module.PROFILE
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected == module.PROFILE
        assert "audio_keep" in operational_module.residual_me_evidence_resolved_ids(
            session, module.PROFILE, {"audio_review"}
        )
        assert "local_insert" in operational_module.residual_me_evidence_resolved_ids(
            session, module.PROFILE, {"local_recall"}
        )

        output_transcript_path = module.profile_paths(session, module.PROFILE)["transcript"]
        output_transcript_text = output_transcript_path.read_text(encoding="utf-8")
        output_transcript_path.write_text(output_transcript_text + "\n", encoding="utf-8")
        assert quality_module.residual_me_evidence_usable(session) is False
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected != module.PROFILE
        output_transcript_path.write_text(output_transcript_text, encoding="utf-8")
        assert quality_module.residual_me_evidence_usable(session) is True

        corpus_report["decision"] = "DO_NOT_PROMOTE"
        corpus_report["gates"]["passed"] = False
        write_json(corpus_report_path, corpus_report)
        assert quality_module.residual_me_evidence_usable(session) is False
        assert quality_module.selected_profile(session) != module.PROFILE
        corpus_report["decision"] = "PROMOTE_RESIDUAL_ME_EVIDENCE_V1"
        corpus_report["gates"]["passed"] = True
        write_json(corpus_report_path, corpus_report)

        raw_path = session / "audio/mic/000001.caf"
        original_raw = raw_path.read_bytes()
        raw_path.write_bytes(original_raw + b"changed")
        assert module.apply_session(args) == 2
        failed = module.read_json(module.session_profile_dir(session) / "residual_me_evidence_profile_report.json")
        assert failed is not None and failed["status"] == "failed_open"
        raw_path.write_bytes(original_raw)

    print("residual Me evidence smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
