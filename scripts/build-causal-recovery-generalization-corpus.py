#!/usr/bin/env python3
"""Freeze the Causal Recovery Generalization v1 corpus.

The corpus intentionally stores causal selection evidence and authoritative batch evidence in
separate fields. Selection code must never read ``evaluation_reference``. The latter exists only
for post-selection scoring and promotion gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.causal_recovery_generalization_corpus/v1"
ROW_SCHEMA = "murmurmark.causal_recovery_generalization_corpus_row/v1"
SCRIPT_VERSION = "1.0.0"
ALLOWED_EVALUATION_CLASSES = (
    "genuine_double_talk",
    "probable_remote_leak",
    "probable_timing_overlap",
    "probable_asr_noise",
    "insufficient_evidence",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze causal recovery generalization corpus.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-recovery-generalization-v1"),
    )
    parser.add_argument(
        "--fixed-corpus-dir",
        type=Path,
        default=Path("sessions/_reports/live-pipeline/causal-double-talk-me-recovery-v1"),
    )
    parser.add_argument("--regression-session", action="append", default=[])
    parser.add_argument("--holdout-session", action="append", default=[])
    parser.add_argument(
        "--holdout-kind",
        action="append",
        default=[],
        metavar="SESSION=group|one_to_one",
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--require-valid", action="store_true")
    return parser.parse_args()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def file_record(path: Path, root: Path, role: str, session: str | None = None) -> dict[str, Any]:
    row = {
        "path": relative(path, root),
        "role": role,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if session:
        row["session"] = session
    return row


def normalize_paths(value: Any, repo_root: Path) -> Any:
    if isinstance(value, list):
        return [normalize_paths(item, repo_root) for item in value]
    if isinstance(value, dict):
        return {key: normalize_paths(item, repo_root) for key, item in value.items()}
    if isinstance(value, str) and value.startswith("/"):
        try:
            return str(Path(value).resolve().relative_to(repo_root.resolve()))
        except ValueError:
            return value
    return value


def authoritative_dialogue_path(session: Path) -> Path | None:
    outcome = read_json(session / "derived/outcome/outcome.json")
    profile = str(outcome.get("selected_profile") or "audit_cleanup_v2").strip()
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidates = [
        resolved / f"clean_dialogue.{profile}.json",
        resolved / "clean_dialogue.audit_cleanup_v2.json",
        resolved / "clean_dialogue.shadow_v2.json",
        resolved / "clean_dialogue.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def authoritative_quality_path(session: Path) -> Path | None:
    dialogue = authoritative_dialogue_path(session)
    if dialogue is None:
        return None
    suffix = dialogue.name.removeprefix("clean_dialogue")
    candidate = dialogue.with_name(f"quality_report{suffix}")
    return candidate if candidate.is_file() else None


def batch_utterances(session: Path) -> list[dict[str, Any]]:
    dialogue = authoritative_dialogue_path(session)
    payload = read_json(dialogue) if dialogue else {}
    rows = payload.get("utterances") if isinstance(payload.get("utterances"), list) else []
    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not str(row.get("text") or "").strip():
            continue
        role = str(row.get("speaker_label") or row.get("role") or "")
        role = "Me" if role.lower() in {"me", "mic"} else "Colleagues"
        start = safe_float(row.get("start"), safe_float(row.get("source_start")))
        end = safe_float(row.get("end"), safe_float(row.get("source_end"), start))
        result.append(
            {
                "id": str(row.get("id") or f"batch_{index:06d}"),
                "start": round(start, 3),
                "end": round(max(start, end), 3),
                "role": role,
                "text": " ".join(str(row.get("text") or "").split()),
                "needs_review": bool((row.get("quality") or {}).get("needs_review"))
                if isinstance(row.get("quality"), dict)
                else False,
            }
        )
    return result


def overlap(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def evaluation_reference(
    utterances: list[dict[str, Any]],
    start: float,
    end: float,
) -> dict[str, Any]:
    overlapping = [
        {**row, "overlap_sec": round(overlap(start, end, row["start"], row["end"]), 3)}
        for row in utterances
        if overlap(start, end, row["start"], row["end"]) > 0.0
    ]
    nearest = sorted(
        utterances,
        key=lambda row: min(abs(start - row["end"]), abs(end - row["start"])),
    )[:4]
    return {
        "use": "evaluation_only",
        "source": "authoritative_batch",
        "overlapping_utterances": overlapping,
        "nearest_utterances": nearest,
    }


def recovery_output_dir(session: Path) -> Path | None:
    isolated = session / "derived/live/causal-recovery-generalization-v1/offline"
    canonical = session / "derived/live/causal-double-talk-me-recovery-v1"
    return isolated if (isolated / "state.json").is_file() else canonical if (canonical / "state.json").is_file() else None


def runtime_candidate_files(session: Path) -> list[Path]:
    roots = [
        session / "derived/live/causal-recovery-generalization-v1/runtime",
        session / "derived/live/causal-me-recovery-runtime-v1",
    ]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(root.glob("**/double-talk-v1/candidates.jsonl"))
    return sorted(set(files))


def session_files(session: Path, recovery: Path | None) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = [
        (session / "session.json", "session_metadata"),
        (session / "derived/live/chunks.jsonl", "committed_chunk_index"),
        (session / "derived/live/transcript.draft.md", "normal_live_preview"),
        (session / "derived/live/transcript.preview.md", "normal_live_preview"),
        (session / "derived/live/transcript.preview.json", "normal_live_preview"),
        (session / "derived/preprocess/audio/mic_raw_for_asr.wav", "echo_guard_input"),
        (session / "derived/preprocess/audio/remote_for_aec.wav", "echo_guard_input"),
        (session / "derived/preprocess/audio/mic_for_asr.wav", "echo_guard_output"),
        (session / "derived/live/causal-target-me/evaluations.jsonl", "target_me_evaluations"),
        (session / "derived/live/causal-target-me/enrollment.jsonl", "past_only_enrollment"),
    ]
    dialogue = authoritative_dialogue_path(session)
    quality = authoritative_quality_path(session)
    if dialogue:
        files.append((dialogue, "authoritative_dialogue_evaluation_only"))
    if quality:
        files.append((quality, "authoritative_quality_evaluation_only"))
    files.extend((path, "raw_capture") for path in sorted((session / "audio/mic").glob("*.caf")))
    files.extend((path, "raw_capture") for path in sorted((session / "audio/remote").glob("*.caf")))
    files.extend((path, "committed_chunk_metadata") for path in sorted((session / "derived/live/chunks").glob("*/chunk.json")))
    if recovery:
        files.extend(
            (recovery / name, role)
            for name, role in (
                ("state.json", "offline_recovery_state"),
                ("source_decisions.jsonl", "causal_source_decisions"),
                ("candidates.jsonl", "offline_recovery_candidates"),
                ("residual_views.jsonl", "offline_residual_evidence"),
            )
        )
    for path in runtime_candidate_files(session):
        files.append((path, "recording_time_recovery_candidates"))
        state = path.with_name("state.json")
        if state.is_file():
            files.append((state, "recording_time_recovery_state"))
    runtime_root = session / "derived/live/causal-recovery-generalization-v1/runtime"
    for name in ("paced_replay.json", "runtime_runs.jsonl", "state.json"):
        path = runtime_root / name
        if path.is_file():
            files.append((path, "recording_time_replay"))
    return [(path, role) for path, role in files if path.is_file()]


def causal_projection(row: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    forbidden = {"evaluation_reference", "batch_text", "batch_timestamps"}
    return normalize_paths({key: value for key, value in row.items() if key not in forbidden}, repo_root)


def build_rows(
    repo_root: Path,
    sessions_root: Path,
    fixed_rows: list[dict[str, Any]],
    regression: list[str],
    holdout: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    input_records: list[dict[str, Any]] = []
    stats: dict[str, Any] = {"session_rows": {}, "runtime_only_count": 0}

    for fixed in fixed_rows:
        row = {
            "schema": ROW_SCHEMA,
            "id": f"fixed:{fixed.get('id')}",
            "row_kind": "fixed_positive",
            "session": fixed.get("session"),
            "split": "fixed_regression",
            "causal_selection": fixed.get("causal_selection") or [],
            "selection_contract": fixed.get("selection_contract") or {},
            "baseline_outcome": fixed.get("baseline_outcome") or {},
            "evaluation_reference": fixed.get("evaluation_reference") or {},
            "source_fixed_row_fingerprint": fixed.get("row_fingerprint_sha256"),
        }
        row["row_fingerprint_sha256"] = canonical_sha256(row)
        rows.append(row)

    for split, session_ids in (("regression", regression), ("holdout", holdout)):
        for session_id in session_ids:
            session = sessions_root / session_id
            recovery = recovery_output_dir(session)
            source_rows = read_jsonl(recovery / "source_decisions.jsonl") if recovery else []
            candidates = read_jsonl(recovery / "candidates.jsonl") if recovery else []
            recovery_state = read_json(recovery / "state.json") if recovery else {}
            utterances = batch_utterances(session)
            represented_ids = {
                str(source_id)
                for candidate in candidates
                for source_id in candidate.get("source_selection_ids") or []
            }
            session_stats = {
                "recovery_output_present": recovery is not None,
                "authoritative_utterance_count": len(utterances),
                "algorithm": {
                    "generator": recovery_state.get("generator") or {},
                    "profile": recovery_state.get("profile"),
                    "view_profile": recovery_state.get("view_profile"),
                    "runtime_shadow": recovery_state.get("runtime_shadow"),
                    "thresholds": recovery_state.get("thresholds") or {},
                    "causal_input_contract": recovery_state.get("causal_input_contract") or {},
                },
                "source_decision_count": len(source_rows),
                "eligible_remote_active_count": 0,
                "candidate_count": len(candidates),
                "runtime_only_count": 0,
            }
            for source in source_rows:
                if source.get("status") != "selected":
                    continue
                session_stats["eligible_remote_active_count"] += 1
                start = safe_float(source.get("start"))
                end = safe_float(source.get("end"), start)
                source_id = str(source.get("id") or "")
                row = {
                    "schema": ROW_SCHEMA,
                    "id": f"source:{session_id}:{source_id}",
                    "row_kind": "eligible_remote_active_source",
                    "session": session_id,
                    "split": split,
                    "interval": {"start": start, "end": end, "duration_sec": round(max(0.0, end - start), 3)},
                    "causal_selection": causal_projection(source, repo_root),
                    "algorithm_outcome": "grouped_candidate" if source_id in represented_ids else "runtime_prefilter_not_evaluated",
                    "evaluation_reference": evaluation_reference(utterances, start, end),
                    "selection_contract": {
                        "evaluation_fields_forbidden": True,
                        "timeline_causal": source.get("timeline_causal") is True,
                        "batch_fields_unused": source.get("used_batch_fields_for_selection") is False,
                    },
                }
                row["row_fingerprint_sha256"] = canonical_sha256(row)
                rows.append(row)

            offline_ids: set[str] = set()
            for candidate in candidates:
                start = safe_float(candidate.get("start"))
                end = safe_float(candidate.get("end"), start)
                candidate_id = str(candidate.get("id") or "")
                offline_ids.add(candidate_id)
                row = {
                    "schema": ROW_SCHEMA,
                    "id": f"candidate:{session_id}:{candidate_id}",
                    "row_kind": "offline_candidate",
                    "session": session_id,
                    "split": split,
                    "interval": {"start": start, "end": end, "duration_sec": round(max(0.0, end - start), 3)},
                    "causal_selection": causal_projection(candidate, repo_root),
                    "algorithm_outcome": candidate.get("status"),
                    "algorithm_classification": candidate.get("classification"),
                    "evaluation_reference": evaluation_reference(utterances, start, end),
                    "selection_contract": {
                        "evaluation_fields_forbidden": True,
                        "timeline_causal": candidate.get("timeline_causal") is True,
                        "batch_fields_unused": candidate.get("used_batch_fields_for_selection") is False,
                    },
                }
                row["row_fingerprint_sha256"] = canonical_sha256(row)
                rows.append(row)

            runtime_seen: set[str] = set()
            for path in runtime_candidate_files(session):
                for candidate in read_jsonl(path):
                    candidate_id = str(candidate.get("id") or "")
                    if not candidate_id or candidate_id in offline_ids or candidate_id in runtime_seen:
                        continue
                    runtime_seen.add(candidate_id)
                    start = safe_float(candidate.get("start"))
                    end = safe_float(candidate.get("end"), start)
                    row = {
                        "schema": ROW_SCHEMA,
                        "id": f"runtime:{session_id}:{candidate_id}",
                        "row_kind": "runtime_only_candidate",
                        "session": session_id,
                        "split": split,
                        "interval": {"start": start, "end": end, "duration_sec": round(max(0.0, end - start), 3)},
                        "causal_selection": causal_projection(candidate, repo_root),
                        "algorithm_outcome": candidate.get("status"),
                        "algorithm_classification": candidate.get("classification"),
                        "evaluation_reference": evaluation_reference(utterances, start, end),
                        "selection_contract": {
                            "evaluation_fields_forbidden": True,
                            "timeline_causal": candidate.get("timeline_causal") is True,
                            "batch_fields_unused": candidate.get("used_batch_fields_for_selection") is False,
                        },
                    }
                    row["row_fingerprint_sha256"] = canonical_sha256(row)
                    rows.append(row)
            session_stats["runtime_only_count"] = len(runtime_seen)
            stats["runtime_only_count"] += len(runtime_seen)
            stats["session_rows"][session_id] = session_stats
            for path, role in session_files(session, recovery):
                input_records.append(file_record(path, repo_root, role, session_id))

    rows.sort(key=lambda row: (str(row.get("session")), str(row.get("row_kind")), safe_float((row.get("interval") or row.get("evaluation_reference") or {}).get("start")), str(row.get("id"))))
    unique_records = {(row["path"], row["role"]): row for row in input_records}
    return rows, sorted(unique_records.values(), key=lambda row: (str(row.get("session")), row["role"], row["path"])), stats


def holdout_duration(sessions_root: Path, holdout: list[str]) -> tuple[float, dict[str, float]]:
    durations: dict[str, float] = {}
    for session_id in holdout:
        metadata = read_json(sessions_root / session_id / "session.json")
        health = metadata.get("health") if isinstance(metadata.get("health"), dict) else {}
        durations[session_id] = safe_float(health.get("actual_duration_sec"))
    return round(sum(durations.values()), 3), durations


def parse_holdout_kinds(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        session, separator, kind = value.partition("=")
        if not separator or kind not in {"group", "one_to_one"}:
            raise ValueError(f"invalid --holdout-kind {value!r}; expected SESSION=group|one_to_one")
        result[session] = kind
    return result


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    checks = payload["checks"]
    lines = [
        "# Causal Recovery Generalization Corpus v1",
        "",
        f"Status: **{payload['status']}**",
        f"Fingerprint: `{payload['corpus_fingerprint_sha256']}`",
        "",
        f"- Rows: `{summary['row_count']}`",
        f"- Fixed positives: `{summary['fixed_positive_count']}`",
        f"- Eligible remote-active source rows: `{summary['eligible_source_count']}`",
        f"- Offline candidates: `{summary['offline_candidate_count']}`",
        f"- Runtime-only candidates: `{summary['runtime_only_candidate_count']}`",
        f"- Holdout: `{summary['holdout_session_count']}` sessions / `{summary['holdout_duration_sec']}` sec",
        f"- Frozen inputs: `{summary['input_file_count']}`",
        "",
        "Selection evidence and authoritative batch references are separated by contract. Batch",
        "text and timestamps are evaluation-only and cannot affect candidate selection.",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- `{name}`: `{str(value).lower()}`" for name, value in checks.items())
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    sessions_root = args.sessions_root.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    fixed_dir = args.fixed_corpus_dir.expanduser().resolve()
    frozen_existing = read_json(out_dir / "corpus_manifest_v1.json")
    regression = list(
        dict.fromkeys(args.regression_session or frozen_existing.get("regression_sessions") or [])
    )
    holdout = list(
        dict.fromkeys(args.holdout_session or frozen_existing.get("holdout_sessions") or [])
    )
    holdout_kinds = parse_holdout_kinds(args.holdout_kind)
    if not holdout_kinds:
        frozen_kinds = frozen_existing.get("holdout_session_kinds")
        holdout_kinds = dict(frozen_kinds) if isinstance(frozen_kinds, dict) else {}
    fixed_manifest = read_json(fixed_dir / "corpus_manifest_v1.json")
    fixed_rows = read_jsonl(fixed_dir / "corpus_rows_v1.jsonl")
    rows, input_records, stats = build_rows(
        repo_root,
        sessions_root,
        fixed_rows,
        regression,
        holdout,
    )
    fixed_inputs = [
        file_record(path, repo_root, role)
        for path, role in (
            (fixed_dir / "corpus_manifest_v1.json", "fixed_positive_manifest"),
            (fixed_dir / "corpus_rows_v1.jsonl", "fixed_positive_rows"),
            (fixed_dir / "outcomes_v1.jsonl", "fixed_positive_outcomes"),
            (fixed_dir / "recovery_report_v1.json", "fixed_positive_report"),
        )
        if path.is_file()
    ]
    input_records = sorted(input_records + fixed_inputs, key=lambda row: (str(row.get("session")), row["role"], row["path"]))
    duration, durations = holdout_duration(sessions_root, holdout)
    row_kinds: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("row_kind") or "unknown")
        row_kinds[kind] = row_kinds.get(kind, 0) + 1
    checks = {
        "fixed_positive_rows_preserved": len(fixed_rows) == 16 and row_kinds.get("fixed_positive") == 16,
        "fixed_positive_fingerprint_preserved": bool(fixed_manifest.get("corpus_fingerprint_sha256")),
        "fixed_positive_row_fingerprints_preserved": {
            str(row.get("id")): row.get("row_fingerprint_sha256") for row in fixed_rows
        }
        == {
            str(row.get("id") or "").removeprefix("fixed:"): row.get(
                "source_fixed_row_fingerprint"
            )
            for row in rows
            if row.get("row_kind") == "fixed_positive"
        },
        "at_least_three_holdout_sessions": len(holdout) >= 3,
        "holdout_duration_at_least_90_minutes": duration >= 5400.0,
        "holdout_contains_group_and_one_to_one": set(holdout_kinds.values())
        == {"group", "one_to_one"}
        and set(holdout_kinds) == set(holdout),
        "all_sessions_have_recovery_outputs": all(
            (stats["session_rows"].get(session_id) or {}).get("recovery_output_present") is True
            for session_id in regression + holdout
        ),
        "all_sessions_have_authoritative_batch": all(
            (stats["session_rows"].get(session_id) or {}).get("authoritative_utterance_count", 0) > 0
            for session_id in regression + holdout
        ),
        "eligible_remote_active_rows_present": row_kinds.get("eligible_remote_active_source", 0) > 0,
        "offline_candidates_present": row_kinds.get("offline_candidate", 0) > 0,
        "all_rows_have_stable_id": all(row.get("id") and row.get("row_fingerprint_sha256") for row in rows),
        "selection_and_evaluation_separated": all((row.get("evaluation_reference") or {}).get("use") == "evaluation_only" for row in rows),
        "causal_selection_contract_present": all(bool(row.get("selection_contract")) for row in rows),
        "frozen_input_manifest_present": bool(input_records),
    }
    contract = {
        "selection_allowed": [
            "closed committed mic/remote PCM",
            "live ASR available through the closed chunk",
            "past-only Target-Me enrollment",
            "past-only remote-dominant echo training",
        ],
        "selection_forbidden": [
            "evaluation_reference",
            "authoritative batch text",
            "authoritative batch timestamps",
            "future chunks",
            "future Target-Me enrollment",
        ],
        "batch_fields_use": "evaluation_only",
        "publication": "explicit shadow only",
        "allowed_evaluation_classes": list(ALLOWED_EVALUATION_CLASSES),
    }
    fingerprint_payload = {
        "schema": SCHEMA,
        "contract": contract,
        "fixed_positive_corpus_fingerprint": fixed_manifest.get("corpus_fingerprint_sha256"),
        "regression_sessions": regression,
        "holdout_sessions": holdout,
        "algorithm_snapshots": {
            session_id: (stats["session_rows"].get(session_id) or {}).get("algorithm") or {}
            for session_id in regression + holdout
        },
        "rows": rows,
        "input_files": input_records,
    }
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "build-causal-recovery-generalization-corpus", "version": SCRIPT_VERSION},
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "valid" if all(checks.values()) else "invalid",
        "corpus_fingerprint_sha256": canonical_sha256(fingerprint_payload),
        "fixed_positive_corpus_fingerprint": fixed_manifest.get("corpus_fingerprint_sha256"),
        "regression_sessions": regression,
        "holdout_sessions": holdout,
        "holdout_session_kinds": holdout_kinds,
        "holdout_durations_sec": durations,
        "contract": contract,
        "algorithm_snapshots": {
            session_id: (stats["session_rows"].get(session_id) or {}).get("algorithm") or {}
            for session_id in regression + holdout
        },
        "checks": checks,
        "summary": {
            "row_count": len(rows),
            "fixed_positive_count": row_kinds.get("fixed_positive", 0),
            "eligible_source_count": row_kinds.get("eligible_remote_active_source", 0),
            "offline_candidate_count": row_kinds.get("offline_candidate", 0),
            "runtime_only_candidate_count": row_kinds.get("runtime_only_candidate", 0),
            "regression_session_count": len(regression),
            "holdout_session_count": len(holdout),
            "holdout_duration_sec": duration,
            "input_file_count": len(input_records),
            "row_kinds": row_kinds,
        },
        "session_rows": stats["session_rows"],
        "rows": rows,
        "input_files": input_records,
    }
    manifest_path = out_dir / "corpus_manifest_v1.json"
    frozen = read_json(manifest_path)
    if frozen and not args.refresh:
        expected = str(frozen.get("corpus_fingerprint_sha256") or "")
        actual = payload["corpus_fingerprint_sha256"]
        if expected != actual:
            print("error: frozen generalization corpus drifted; use --refresh only intentionally")
            print(f"frozen: {expected}")
            print(f"current: {actual}")
            return 3
        print(f"causal_recovery_generalization_corpus: {manifest_path}")
        print("status: verified_immutable")
        print(f"rows: {len(rows)}")
        print(f"fingerprint: {expected}")
        return 2 if args.require_valid and payload["status"] != "valid" else 0
    write_json(manifest_path, payload)
    write_jsonl(out_dir / "corpus_rows_v1.jsonl", rows)
    write_json(
        out_dir / "input_manifest_v1.json",
        {
            "schema": "murmurmark.causal_recovery_generalization_input_manifest/v1",
            "corpus_fingerprint_sha256": payload["corpus_fingerprint_sha256"],
            "files": input_records,
        },
    )
    (out_dir / "corpus_manifest_v1.md").write_text(render_markdown(payload), encoding="utf-8")
    print(f"causal_recovery_generalization_corpus: {manifest_path}")
    print(f"status: {payload['status']}")
    print(f"rows: {len(rows)}")
    print(f"holdout_seconds: {duration}")
    print(f"fingerprint: {payload['corpus_fingerprint_sha256']}")
    return 2 if args.require_valid and payload["status"] != "valid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
