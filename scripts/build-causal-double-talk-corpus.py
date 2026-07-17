#!/usr/bin/env python3
"""Freeze the bounded Causal Double-Talk Me Recovery v1 evaluation corpus.

The manifest deliberately keeps causal selection inputs separate from batch-derived evaluation
references. Selection code may read only ``causal_selection`` and ``causal_inputs``. The
``evaluation_reference`` field is reserved for reporters and acceptance gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.causal_double_talk_corpus/v1"
ROW_SCHEMA = "murmurmark.causal_double_talk_corpus_row/v1"
SCRIPT_VERSION = "1.0.0"
EXPECTED_ROWS = 16
EXPECTED_SECONDS = 65.07
SOURCE_SCOPE = "mixed_double_talk_cross_check"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze the fixed causal double-talk corpus.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--source-rows",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/causal_remote_active_me_separation_v1.jsonl"
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(
            "sessions/_reports/live-pipeline/causal-double-talk-me-recovery-v1"
        ),
    )
    parser.add_argument("--require-valid", action="store_true")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Explicitly replace the frozen manifest. Without this flag any drift fails closed.",
    )
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
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def file_record(path: Path, root: Path, *, role: str) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def source_selection_ids(outcome: dict[str, Any]) -> list[str]:
    machine = outcome.get("machine_evidence")
    machine = machine if isinstance(machine, dict) else {}
    rows = machine.get("selection_evidence")
    rows = rows if isinstance(rows, list) else []
    return sorted(
        {
            str(row.get("id"))
            for row in rows
            if isinstance(row, dict) and row.get("id")
        }
    )


def causal_row_contract(row: dict[str, Any]) -> dict[str, Any]:
    recording = row.get("recording_time_evidence")
    recording = recording if isinstance(recording, dict) else {}
    checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
    return {
        "timeline_causal": row.get("timeline_causal") is True,
        "selection_does_not_use_batch": row.get("used_batch_fields_for_selection") is False,
        "recording_time_committed_pcm": checks.get("recording_time_committed_pcm") is True,
        "recording_time_evidence": recording.get("status") == "passed",
        "past_only_enrollment": checks.get("past_only_enrollment") is True,
    }


def causal_selection_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "duration_sec": row.get("duration_sec"),
        "text": row.get("text"),
        "status": row.get("status"),
        "reasons": row.get("reasons") or [],
        "checks": row.get("checks") or {},
        "source_evaluation": row.get("source_evaluation") or {},
        "speaker_evidence": row.get("speaker_evidence") or {},
        "remote_audio_guard": row.get("remote_audio_guard") or {},
        "recording_time_evidence": row.get("recording_time_evidence") or {},
        "timeline_causal": row.get("timeline_causal"),
        "used_batch_fields_for_selection": row.get("used_batch_fields_for_selection"),
        "causal_contract": causal_row_contract(row),
    }


def session_input_files(session: Path) -> list[tuple[Path, str]]:
    candidates: list[tuple[Path, str]] = [
        (session / "derived/live/causal-local-island-micro-asr-v2/selection.jsonl", "causal_selection"),
        (session / "derived/live/causal-target-me/evaluations.jsonl", "causal_target_me_evaluations"),
        (session / "derived/live/causal-target-me/enrollment.jsonl", "past_only_enrollment"),
        (session / "derived/live/chunks.jsonl", "committed_chunk_index"),
        (session / "derived/live/transcript.draft.md", "normal_live_preview"),
        (session / "derived/preprocess/audio/mic_raw_for_asr.wav", "echo_guard_input"),
        (session / "derived/preprocess/audio/remote_for_aec.wav", "echo_guard_input"),
        (session / "derived/preprocess/audio/mic_for_asr.wav", "echo_guard_output"),
    ]
    outcome = read_json(session / "derived/outcome/outcome.json")
    summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
    transcript_path = summary.get("transcript_path")
    if transcript_path:
        candidates.append((session / str(transcript_path), "authoritative_transcript"))
    selected_profile = str(outcome.get("selected_profile") or "").strip()
    if selected_profile:
        candidates.extend(
            [
                (
                    session
                    / "derived/transcript-simple/whisper-cpp/resolved"
                    / f"clean_dialogue.{selected_profile}.json",
                    "authoritative_dialogue",
                ),
                (
                    session
                    / "derived/transcript-simple/whisper-cpp/resolved"
                    / f"quality_report.{selected_profile}.json",
                    "authoritative_quality",
                ),
            ]
        )
    candidates.extend((path, "raw_capture") for path in sorted((session / "audio/mic").glob("*.caf")))
    candidates.extend((path, "raw_capture") for path in sorted((session / "audio/remote").glob("*.caf")))
    candidates.extend(
        (path, "committed_chunk")
        for path in sorted((session / "derived/live/chunks").glob("*/chunk.json"))
    )
    return [(path, role) for path, role in candidates if path.is_file()]


def markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Causal Double-Talk Me Recovery v1 Corpus",
        "",
        f"Status: **{payload['status']}**",
        "",
        f"Rows: `{summary['row_count']}` / `{summary['seconds']}s`.",
        f"Sessions: `{summary['session_count']}`.",
        f"Corpus fingerprint: `{payload['corpus_fingerprint_sha256']}`.",
        "",
        "Batch text and timestamps below are evaluation-only. Selection consumes only causal rows",
        "and committed PCM available through each row's closed chunk.",
        "",
        "| Session | Row | Interval | Causal selections | Contract |",
        "|---|---|---:|---:|---|",
    ]
    for row in payload["rows"]:
        reference = row["evaluation_reference"]
        contract = row["selection_contract"]
        lines.append(
            f"| `{row['session']}` | `{row['id']}` | "
            f"{reference['start']:.2f}-{reference['end']:.2f} | "
            f"{len(row['causal_selection'])} | "
            f"{'pass' if contract['all_causal_inputs_valid'] else 'fail'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    sessions_root = args.sessions_root.expanduser().resolve()
    source_rows_path = args.source_rows.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    outcomes = [
        row for row in read_jsonl(source_rows_path) if row.get("scope") == SOURCE_SCOPE
    ]
    rows: list[dict[str, Any]] = []
    input_records: list[dict[str, Any]] = []
    session_ids: set[str] = set()
    for outcome in sorted(
        outcomes,
        key=lambda row: (
            str(row.get("session") or ""),
            safe_float((row.get("evaluation_reference") or {}).get("start")),
        ),
    ):
        session_id = str(outcome.get("session") or "")
        session = sessions_root / session_id
        session_ids.add(session_id)
        selection_path = session / "derived/live/causal-local-island-micro-asr-v2/selection.jsonl"
        selection_by_id = {
            str(row.get("id")): row for row in read_jsonl(selection_path) if row.get("id")
        }
        ids = source_selection_ids(outcome)
        selected = [causal_selection_row(selection_by_id[row_id]) for row_id in ids if row_id in selection_by_id]
        missing = [row_id for row_id in ids if row_id not in selection_by_id]
        valid_inputs = bool(selected) and not missing and all(
            all(row["causal_contract"].values()) for row in selected
        )
        reference = outcome.get("evaluation_reference")
        reference = reference if isinstance(reference, dict) else {}
        row = {
            "schema": ROW_SCHEMA,
            "id": outcome.get("id"),
            "session": session_id,
            "scope": SOURCE_SCOPE,
            "causal_selection": selected,
            "selection_contract": {
                "selection_fields": "causal_selection_only",
                "evaluation_fields_forbidden": True,
                "all_causal_inputs_valid": valid_inputs,
                "missing_selection_ids": missing,
            },
            "baseline_outcome": {
                "status": outcome.get("status"),
                "outcome": outcome.get("outcome"),
                "reason": outcome.get("reason"),
            },
            "evaluation_reference": {
                "use": "evaluation_only",
                "source": "authoritative_batch",
                "batch_id": reference.get("batch_id"),
                "start": safe_float(reference.get("start")),
                "end": safe_float(reference.get("end")),
                "duration_sec": safe_float(reference.get("duration_sec")),
                "text": reference.get("text"),
                "recall_in_live_me": reference.get("recall_in_live_me"),
                "recall_in_suppressed_mic": reference.get("recall_in_suppressed_mic"),
            },
        }
        row["row_fingerprint_sha256"] = canonical_sha256(row)
        rows.append(row)

    for session_id in sorted(session_ids):
        session = sessions_root / session_id
        for path, role in session_input_files(session):
            input_records.append(
                {
                    "session": session_id,
                    **file_record(path, repo_root, role=role),
                }
            )

    total_seconds = round(
        sum(safe_float(row["evaluation_reference"].get("duration_sec")) for row in rows),
        2,
    )
    checks = {
        "expected_row_count": len(rows) == EXPECTED_ROWS,
        "expected_seconds": abs(total_seconds - EXPECTED_SECONDS) <= 0.01,
        "all_rows_have_causal_selection": all(bool(row["causal_selection"]) for row in rows),
        "all_causal_inputs_valid": all(
            row["selection_contract"]["all_causal_inputs_valid"] for row in rows
        ),
        "evaluation_fields_separated": all(
            row["evaluation_reference"].get("use") == "evaluation_only" for row in rows
        ),
        "source_rows_present": source_rows_path.is_file(),
    }
    corpus_contract = {
        "selection_allowed": [
            "closed committed mic/remote PCM",
            "causal_selection",
            "past-only enrollment",
            "past remote-dominant training",
            "recording-time remote ASR",
        ],
        "selection_forbidden": [
            "evaluation_reference",
            "authoritative batch text",
            "authoritative batch timestamps",
            "future chunks or future enrollment",
        ],
        "batch_fields_use": "evaluation_only",
        "publication": "explicit shadow only",
    }
    fingerprint_payload = {
        "schema": SCHEMA,
        "contract": corpus_contract,
        "rows": rows,
        "causal_input_manifest": input_records,
    }
    status = "valid" if all(checks.values()) else "invalid"
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "build-causal-double-talk-corpus", "version": SCRIPT_VERSION},
        "generated_at": datetime.now(UTC).isoformat(),
        "status": status,
        "corpus_fingerprint_sha256": canonical_sha256(fingerprint_payload),
        "source_rows": relative(source_rows_path, repo_root),
        "contract": corpus_contract,
        "summary": {
            "row_count": len(rows),
            "seconds": total_seconds,
            "session_count": len(session_ids),
            "causal_selection_count": sum(len(row["causal_selection"]) for row in rows),
        },
        "checks": checks,
        "rows": rows,
        "causal_input_manifest": input_records,
    }
    frozen_path = out_dir / "corpus_manifest_v1.json"
    frozen = read_json(frozen_path)
    if frozen and not args.refresh:
        frozen_fingerprint = str(frozen.get("corpus_fingerprint_sha256") or "")
        if frozen_fingerprint != payload["corpus_fingerprint_sha256"]:
            print(
                "error: frozen causal double-talk corpus drifted; "
                "inspect inputs and use --refresh only for an intentional re-freeze"
            )
            print(f"frozen: {frozen_fingerprint}")
            print(f"current: {payload['corpus_fingerprint_sha256']}")
            return 3
        print(f"causal_double_talk_corpus: {frozen_path}")
        print("status: verified_immutable")
        print(f"rows: {len(rows)}")
        print(f"seconds: {total_seconds}")
        print(f"fingerprint: {frozen_fingerprint}")
        return 2 if args.require_valid and status != "valid" else 0
    write_json(frozen_path, payload)
    write_jsonl(out_dir / "corpus_rows_v1.jsonl", rows)
    write_json(
        out_dir / "causal_input_manifest_v1.json",
        {
            "schema": "murmurmark.causal_double_talk_input_manifest/v1",
            "corpus_fingerprint_sha256": payload["corpus_fingerprint_sha256"],
            "files": input_records,
        },
    )
    (out_dir / "corpus_manifest_v1.md").write_text(markdown(payload), encoding="utf-8")
    print(f"causal_double_talk_corpus: {out_dir / 'corpus_manifest_v1.json'}")
    print(f"status: {status}")
    print(f"rows: {len(rows)}")
    print(f"seconds: {total_seconds}")
    print(f"fingerprint: {payload['corpus_fingerprint_sha256']}")
    return 2 if args.require_valid and status != "valid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
