#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


SCRIPT = Path(__file__).with_name("authoritative-boundary.py")
INPUT_PROFILE = "audit_cleanup_v2"


def load_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_authoritative_boundary", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_sibling(name: str, module_name: str) -> Any:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
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
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


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


def build_fixture(module: Any, sessions_root: Path) -> tuple[Path, Path]:
    session = sessions_root / "fixture"
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    paths = module.profile_paths(session, INPUT_PROFILE)
    dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": "fixture",
        "utterances": [
            utterance("remote_keep", "remote", 1.0, 3.0, "Обсуждаем настройки кластера"),
            utterance("me_keep", "me", 1.2, 2.6, "Я добавлю отдельный комментарий"),
            utterance("remote_drop", "remote", 4.0, 5.0, "Проверяем систему"),
            utterance("me_drop", "me", 4.0, 5.0, "Проверяем систему"),
            utterance("me_unresolved", "me", 6.0, 7.0, "Моя отдельная мысль"),
        ],
    }
    quality = {
        "schema": "murmurmark.simple_transcript_quality/v1",
        "utterances": 5,
        "local_only_island_recall": 0.72,
        "audit_harmful_seconds_after": 1.0,
        "audit_review_seconds": 2.0,
    }
    write_json(paths["dialogue"], dialogue)
    write_json(paths["quality"], quality)
    write_json(paths["overlaps"], {"schema": "murmurmark.transcript_overlaps/v1", "overlaps": []})
    write_json(resolved / "transcribe_simple_report.json", {"model": "fixture.bin", "language": "ru"})
    write_json(
        session
        / "derived/transcript-simple/whisper-cpp/audit-cleanup"
        / f"audit_cleanup_report.{INPUT_PROFILE}.json",
        {
            "schema": "murmurmark.audit_cleanup_report/v1",
            "gates": {
                "passed": True,
                "local_recall_explanation": "local_recall_risk_explained:fixture",
            },
        },
    )

    queue = [
        {
            "schema": "murmurmark.authoritative_boundary_queue_item/v1",
            "boundary_queue_id": "boundary_keep",
            "session_id": "fixture",
            "source": "transcript_order",
            "source_audit_id": "missing_order_item",
            "review_lane": "check_transcript_order",
            "utterance_ids": ["me_keep", "remote_keep"],
            "me_utterance_ids": ["me_keep"],
            "remote_utterance_ids": ["remote_keep"],
            "interval": {"start": 1.2, "end": 2.6, "duration_sec": 1.4},
        },
        {
            "schema": "murmurmark.authoritative_boundary_queue_item/v1",
            "boundary_queue_id": "boundary_drop",
            "session_id": "fixture",
            "source": "audio_review",
            "source_audit_id": "audio_drop",
            "review_lane": "fast_drop",
            "utterance_ids": ["me_drop", "remote_drop"],
            "me_utterance_ids": ["me_drop"],
            "remote_utterance_ids": ["remote_drop"],
            "interval": {"start": 4.0, "end": 5.0, "duration_sec": 1.0},
            "text": [
                {"id": "me_drop", "source_track": "mic", "text": "Проверяем систему"},
                {"id": "remote_drop", "source_track": "remote", "text": "Проверяем систему"},
            ],
        },
        {
            "schema": "murmurmark.authoritative_boundary_queue_item/v1",
            "boundary_queue_id": "boundary_unresolved",
            "session_id": "fixture",
            "source": "local_recall",
            "source_audit_id": "local_missing",
            "review_lane": "recover_missing_me",
            "utterance_ids": ["me_unresolved"],
            "me_utterance_ids": ["me_unresolved"],
            "interval": {"start": 6.0, "end": 7.0, "duration_sec": 1.0},
        },
    ]
    report_dir = sessions_root / "_reports/authoritative-boundary-v1"
    manifest = {
        "schema": "murmurmark.authoritative_boundary_baseline/v1",
        "queue": {"sha256": module.sha256_bytes(module.canonical_bytes(queue))},
        "sessions": [
            {
                "session_id": "fixture",
                "session": str(session),
                "selected_profile": INPUT_PROFILE,
                "artifacts": module.artifact_fingerprints(session, INPUT_PROFILE),
                "utterance_fingerprint": module.utterance_fingerprint(dialogue),
                "token_inventory": module.token_fingerprint(dialogue),
            }
        ],
    }
    write_json(report_dir / "baseline_manifest.json", manifest)
    write_jsonl(report_dir / "boundary_review_queue.jsonl", queue)

    evidence_dir = session / "derived/audit/audio-review-pack"
    write_jsonl(
        evidence_dir / "faster_whisper_judge.jsonl",
        [
            {
                "id": "judge_keep",
                "utterance_ids": ["me_keep", "remote_keep"],
                "start": 1.2,
                "end": 2.6,
                "classification": {"label": "confirm_timing_or_doubletalk", "confidence": 0.95},
            },
            {
                "id": "judge_drop",
                "utterance_ids": ["me_drop", "remote_drop"],
                "start": 4.0,
                "end": 5.0,
                "classification": {"label": "confirm_remote_duplicate", "confidence": 0.97},
            },
        ],
    )
    write_jsonl(
        evidence_dir / "audio_review_audit.jsonl",
        [
            {
                "id": "audio_drop",
                "utterance_ids": ["me_drop", "remote_drop"],
                "start": 4.0,
                "end": 5.0,
                "classification": {
                    "label": "remote_duplicate",
                    "confidence": 0.95,
                    "verdict": "probable_transcript_error",
                },
                "scores": {"local_support": 10.0},
            }
        ],
    )
    return session, evidence_dir / "faster_whisper_judge.jsonl"


def output_hashes(module: Any, session: Path) -> dict[str, str | None]:
    paths = module.profile_paths(session, module.PROFILE)
    result = {
        name: module.sha256_file(path)
        for name, path in paths.items()
        if name in {"dialogue", "quality", "overlaps", "transcript", "transcript_json"}
    }
    boundary = module.session_boundary_dir(session)
    for name in (
        "boundary_repair_report.json",
        "boundary_repair_applied.jsonl",
        "boundary_repair_rejected.jsonl",
        "boundary_repair_diff.json",
        "boundary_review_queue.jsonl",
    ):
        result[name] = module.sha256_file(boundary / name)
    return result


def main() -> int:
    module = load_module()
    with tempfile.TemporaryDirectory(prefix="murmurmark-boundary-") as raw_root:
        sessions_root = Path(raw_root) / "sessions"
        session, stronger_path = build_fixture(module, sessions_root)
        args = argparse.Namespace(
            session=session,
            sessions_root=sessions_root,
            out_dir=None,
            mode="conservative",
        )
        input_paths = module.profile_paths(session, INPUT_PROFILE)
        baseline_hashes = {name: module.sha256_file(path) for name, path in input_paths.items()}

        assert module.apply_session(args) == 0
        output = module.read_json(module.profile_paths(session, module.PROFILE)["dialogue"])
        assert output is not None
        by_id = {row["id"]: row for row in output["utterances"]}
        assert "me_drop" not in by_id, by_id
        assert "me_keep" in by_id
        assert by_id["me_keep"]["quality"]["authoritative_boundary"]["decisions"][0]["disposition"] == "keep"
        assert by_id["me_unresolved"]["quality"]["needs_review"] is True
        quality = module.read_json(module.profile_paths(session, module.PROFILE)["quality"])
        assert quality is not None
        assert quality["local_recall_low_score_explained"] is True
        assert quality["local_recall_explanation"] == "local_recall_risk_explained:fixture"
        assert baseline_hashes == {name: module.sha256_file(path) for name, path in input_paths.items()}

        first_hashes = output_hashes(module, session)
        assert module.apply_session(args) == 0
        assert first_hashes == output_hashes(module, session)

        stronger_payload = stronger_path.read_text(encoding="utf-8")
        stronger_path.unlink()
        assert module.apply_session(args) == 0
        failed_open = module.read_json(module.profile_paths(session, module.PROFILE)["dialogue"])
        assert failed_open is not None
        failed_open_ids = {row["id"] for row in failed_open["utterances"]}
        assert "me_drop" in failed_open_ids
        report = module.read_json(module.session_boundary_dir(session) / "boundary_repair_report.json")
        assert report is not None
        assert report["summary"]["closed_items"] == 0
        assert report["summary"]["remaining_items"] == 3

        stronger_path.write_text(stronger_payload, encoding="utf-8")
        assert module.apply_session(args) == 0
        assert first_hashes == output_hashes(module, session)

        corpus_report_path = sessions_root / "_reports/authoritative-boundary-v1/boundary_corpus_report.json"
        baseline_manifest_path = sessions_root / "_reports/authoritative-boundary-v1/baseline_manifest.json"
        boundary_report = module.read_json(module.session_boundary_dir(session) / "boundary_repair_report.json")
        assert boundary_report is not None
        corpus_report = {
            "schema": "murmurmark.authoritative_boundary_corpus_report/v1",
            "decision": "PROMOTE_AUTHORITATIVE_BOUNDARY_V1",
            "baseline_manifest_sha256": module.sha256_file(baseline_manifest_path),
            "gates": {"passed": True, "hard_failures": []},
            "promoted_sessions": ["fixture"],
            "sessions": [
                {
                    "session_id": "fixture",
                    "output_fingerprint": boundary_report["output_fingerprint"],
                }
            ],
        }
        write_json(corpus_report_path, corpus_report)
        quality_module = load_sibling("report-session-quality.py", "murmurmark_boundary_quality")
        synthesis_module = load_sibling("synthesize-simple-extractive.py", "murmurmark_boundary_synthesis")
        operational_module = load_sibling("report-operational-readiness.py", "murmurmark_boundary_operational")
        assert quality_module.authoritative_boundary_usable(session) is True
        assert quality_module.selected_profile(session) == module.PROFILE
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected == module.PROFILE
        assert "audio_drop" in operational_module.authoritative_boundary_resolved_ids(
            session, module.PROFILE, {"audio_review"}
        )
        assert "missing_order_item" in operational_module.authoritative_boundary_resolved_ids(
            session, module.PROFILE, {"transcript_order"}
        )

        input_dialogue_path = module.profile_paths(session, INPUT_PROFILE)["dialogue"]
        input_dialogue_text = input_dialogue_path.read_text(encoding="utf-8")
        input_dialogue_path.write_text(input_dialogue_text + "\n", encoding="utf-8")
        assert quality_module.authoritative_boundary_usable(session) is False
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected == INPUT_PROFILE
        input_dialogue_path.write_text(input_dialogue_text, encoding="utf-8")

        input_quality_path = module.profile_paths(session, INPUT_PROFILE)["quality"]
        input_quality_text = input_quality_path.read_text(encoding="utf-8")
        input_quality_path.write_text(input_quality_text + "\n", encoding="utf-8")
        assert quality_module.authoritative_boundary_usable(session) is False
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected == INPUT_PROFILE
        input_quality_path.write_text(input_quality_text, encoding="utf-8")

        output_transcript_path = module.profile_paths(session, module.PROFILE)["transcript"]
        output_transcript_text = output_transcript_path.read_text(encoding="utf-8")
        output_transcript_path.write_text(output_transcript_text + "\n", encoding="utf-8")
        assert quality_module.authoritative_boundary_usable(session) is False
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected == INPUT_PROFILE
        output_transcript_path.write_text(output_transcript_text, encoding="utf-8")
        assert quality_module.authoritative_boundary_usable(session) is True

        corpus_report["decision"] = "DO_NOT_PROMOTE"
        corpus_report["gates"]["passed"] = False
        write_json(corpus_report_path, corpus_report)
        assert quality_module.authoritative_boundary_usable(session) is False
        assert quality_module.selected_profile(session) == INPUT_PROFILE
        selected, _paths, _comparison, _risks = synthesis_module.choose_profile(
            session / "derived/transcript-simple/whisper-cpp/resolved",
            "auto",
        )
        assert selected == INPUT_PROFILE
        corpus_report["decision"] = "PROMOTE_AUTHORITATIVE_BOUNDARY_V1"
        corpus_report["gates"]["passed"] = True
        write_json(corpus_report_path, corpus_report)

        protected = dict(next(row for row in output["utterances"] if row["id"] == "me_keep"))
        protected["text"] = "Нужно проверить систему"
        drop_queue = {
            "source": "audio_review",
            "me_utterance_ids": ["me_keep"],
            "text": [{"source_track": "remote", "text": "Нужно проверить систему"}],
        }
        drop_ok, reason = module.strict_drop(
            drop_queue,
            {
                "audio_review": [
                    {
                        "classification": {
                            "label": "remote_duplicate",
                            "confidence": 0.99,
                            "verdict": "probable_transcript_error",
                        },
                        "scores": {"local_support": 0.0},
                    }
                ],
                "stronger_judge": [
                    {"classification": {"label": "confirm_remote_duplicate", "confidence": 0.99}}
                ],
            },
            {"me_keep": protected},
        )
        assert drop_ok is False
        assert reason == "protected_action_decision_or_risk_marker"

    print("authoritative boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
