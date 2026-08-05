#!/usr/bin/env python3
"""Build the frozen cold/cache/live-origin decision for authoritative incremental ASR."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.authoritative_incremental_asr_corpus/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_MANIFEST = Path("docs/testing/authoritative-incremental-asr-v1-manifest.json")
DEFAULT_OUT = Path("sessions/_reports/authoritative-incremental-asr-v1")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--no-fail", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path, expected: str | None = None) -> dict[str, Any]:
    exists = path.exists()
    actual = sha256_file(path) if exists else None
    return {
        "path": str(path),
        "exists": exists,
        "bytes": path.stat().st_size if exists else None,
        "sha256": actual,
        "expected_sha256": expected,
        "matches_frozen": exists and (expected is None or actual == expected),
    }


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 6)


def transcript_path(report: dict[str, Any], root: Path) -> Path:
    path = Path(str(report.get("transcript") or ""))
    if path.is_absolute():
        candidate = root / "sessions" / Path(str(report["session"])).name / "derived/transcript-simple/whisper-cpp/resolved" / path.name
        return candidate if candidate.exists() else path
    return root / path


def cold_cache_rows(root: Path, section: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    cache_source = artifact(root / section["cache_corpus_report"]["path"], section["cache_corpus_report"]["sha256"])
    if not cache_source["matches_frozen"]:
        errors.append("cache_corpus_report_changed")
        cache_payload: dict[str, Any] = {"sessions": []}
    else:
        cache_payload = read_json(Path(cache_source["path"]))
    cached = {str(row.get("session_id")): row for row in cache_payload.get("sessions") or [] if isinstance(row, dict)}
    rows: list[dict[str, Any]] = []
    for item in section.get("sessions") or []:
        session_id = str(item["session_id"])
        session = root / "sessions" / session_id
        cold_source = artifact(root / item["cold_report"], item["cold_report_sha256"])
        if not cold_source["matches_frozen"]:
            errors.append(f"{session_id}:cold_report_changed")
            continue
        cold = read_json(Path(cold_source["path"]))
        cache = cached.get(session_id)
        if cache is None:
            errors.append(f"{session_id}:cache_row_missing")
            continue
        raw_asr: dict[str, Any] = {}
        for track in ("mic", "remote"):
            raw_asr[track] = artifact(
                session / f"derived/transcript-simple/whisper-cpp/raw/{track}.json",
                item["raw_asr_sha256"][track],
            )
            if not raw_asr[track]["matches_frozen"]:
                errors.append(f"{session_id}:raw_asr_{track}_changed")
        selected = artifact(transcript_path(cold, root), item["selected_transcript_sha256"])
        if not selected["matches_frozen"]:
            errors.append(f"{session_id}:selected_transcript_changed")
        cold_sec = float(((cold.get("elapsed_sec") or {}).get("authoritative_process")) or 0.0)
        cache_sec = float(((cache.get("action_elapsed_sec") or {}).get("process")) or 0.0)
        reduction = 1.0 - cache_sec / cold_sec if cold_sec > 0 else 0.0
        rows.append(
            {
                "session_id": session_id,
                "cold_process_sec": round(cold_sec, 3),
                "cache_process_sec": round(cache_sec, 3),
                "reduction_ratio": round(reduction, 6),
                "at_least_50_percent_faster": reduction >= 0.5,
                "cold_source": cold_source,
                "raw_asr": raw_asr,
                "selected_transcript": selected,
                "evidence_class": "historical_checkpoint_cache",
            }
        )
    return rows, errors


def live_rows(root: Path, section: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    proof_schema = str(section["required_proof_schema"])
    expected_window = float(section["authoritative_window_sec"])
    expected_overlap = float(section["authoritative_overlap_sec"])
    rows: list[dict[str, Any]] = []
    for item in section.get("sessions") or []:
        session_id = str(item["session_id"])
        session = root / "sessions" / session_id
        live_report = artifact(session / "derived/live/live_pipeline_report.json", item["live_report_sha256"])
        chunks_source = artifact(session / "derived/live/chunks.jsonl", item["chunks_sha256"])
        if not live_report["matches_frozen"]:
            errors.append(f"{session_id}:live_report_changed")
        if not chunks_source["matches_frozen"]:
            errors.append(f"{session_id}:live_chunks_changed")
        chunks: list[dict[str, Any]] = []
        if chunks_source["matches_frozen"]:
            for line in Path(chunks_source["path"]).read_text(encoding="utf-8").splitlines():
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    chunks.append(value)
        proofs = []
        for chunk in chunks:
            for track in ("mic", "remote"):
                source = chunk.get(track)
                proof = source.get("batch_cache_compatibility") if isinstance(source, dict) else None
                if isinstance(proof, dict):
                    proofs.append(proof)
        intervals = [
            (float(row.get("start_sec") or 0.0), float(row.get("end_sec") or 0.0), float(row.get("clip_start_sec") or 0.0), float(row.get("clip_end_sec") or 0.0))
            for row in chunks
        ]
        hard_durations = [round(end - start, 3) for start, end, _, _ in intervals if end > start]
        inferred_window = max(hard_durations) if hard_durations else None
        inferred_overlap = None
        if intervals:
            overlap_values = [max(0.0, start - clip_start) for start, _, clip_start, _ in intervals]
            inferred_overlap = round(max(overlap_values), 3)
        raw: dict[str, Any] = {}
        for track in ("mic", "remote"):
            raw[track] = artifact(session / f"audio/{track}/000001.caf", item["raw_sha256"][track])
            if not raw[track]["matches_frozen"]:
                errors.append(f"{session_id}:raw_{track}_changed")
        exact_proofs = [proof for proof in proofs if proof.get("schema") == proof_schema]
        reasons: list[str] = []
        if len(exact_proofs) != len(chunks) * 2:
            reasons.append("authoritative_live_chunk_proofs_missing")
        if inferred_window is not None and abs(inferred_window - expected_window) > 0.05:
            reasons.append(f"window_geometry_mismatch:{inferred_window}!={expected_window}")
        if inferred_overlap is not None and abs(inferred_overlap - expected_overlap) > 0.05:
            reasons.append(f"overlap_geometry_mismatch:{inferred_overlap}!={expected_overlap}")
        rows.append(
            {
                "session_id": session_id,
                "chunks": len(chunks),
                "expected_proofs": len(chunks) * 2,
                "authoritative_proofs": len(exact_proofs),
                "inferred_window_sec": inferred_window,
                "inferred_overlap_sec": inferred_overlap,
                "eligible": not reasons,
                "reasons": sorted(reasons),
                "live_report": live_report,
                "chunks_source": chunks_source,
                "raw": raw,
            }
        )
    return rows, errors


def render_markdown(payload: dict[str, Any]) -> str:
    cold = payload["cold_cache"]
    live = payload["live_origin"]
    lines = [
        "# Authoritative Incremental ASR v1 Corpus Decision",
        "",
        f"Overall: `{payload['decision']['overall']}`",
        f"Batch interruption/resume: `{payload['decision']['batch_resume']}`",
        f"Live-origin reuse: `{payload['decision']['live_origin']}`",
        "",
        "## Cold / Cache Evidence",
        "",
        f"Frozen sessions: `{cold['sessions']}`",
        f"Median reduction: `{cold['median_reduction_ratio']}`",
        f"p90 reduction: `{cold['p90_reduction_ratio']}`",
        "",
    ]
    for row in cold["rows"]:
        lines.append(
            f"- `{row['session_id']}`: `{row['cold_process_sec']}s -> {row['cache_process_sec']}s`, "
            f"reduction `{row['reduction_ratio']}`."
        )
    lines.extend(
        [
            "",
            "These timings prove checkpoint/cache value. They predate the v2 identity and do not prove live-origin compatibility.",
            "",
            "## Live-Origin Evidence",
            "",
            f"Frozen real sessions: `{live['sessions']}`",
            f"Eligible: `{live['eligible_sessions']}`",
            f"Authoritative proofs: `{live['authoritative_proofs']}/{live['expected_proofs']}`",
            "",
        ]
    )
    for row in live["rows"]:
        lines.append(f"- `{row['session_id']}`: `{', '.join(row['reasons']) or 'eligible'}`.")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "Promote strict v2 cache validation for interrupted/repeated batch work. Keep live-origin reuse disabled until the sidecar emits exact canonical 60s/5s chunks with `authoritative_live_asr_chunk/v1` proof for both tracks.",
            "",
            "Any mismatch, missing proof, corruption or partial write falls back to ordinary whisper.cpp decoding.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = Path(__file__).resolve().parent.parent
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    out_dir = args.out_dir if args.out_dir.is_absolute() else root / args.out_dir
    manifest = read_json(manifest_path)
    cold_rows, cold_errors = cold_cache_rows(root, manifest["cold_cache"])
    live_rows_value, live_errors = live_rows(root, manifest["live_origin"])
    reductions = [float(row["reduction_ratio"]) for row in cold_rows]
    expected_proofs = sum(int(row["expected_proofs"]) for row in live_rows_value)
    actual_proofs = sum(int(row["authoritative_proofs"]) for row in live_rows_value)
    eligible_live = sum(1 for row in live_rows_value if row["eligible"])
    evidence_errors = sorted(cold_errors + live_errors)
    batch_promote = (
        len(cold_rows) >= 3
        and all(row["at_least_50_percent_faster"] for row in cold_rows)
        and not cold_errors
    )
    live_promote = len(live_rows_value) >= 3 and eligible_live >= 3 and not live_errors
    payload = {
        "schema": SCHEMA,
        "generator": {"name": "report-authoritative-incremental-asr", "version": SCRIPT_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": artifact(manifest_path),
        "decision": {
            "overall": "DO_NOT_PROMOTE_LIVE_ORIGIN" if not live_promote else "PROMOTE",
            "batch_resume": "PROMOTE" if batch_promote else "DO_NOT_PROMOTE",
            "live_origin": "PROMOTE" if live_promote else "DO_NOT_PROMOTE",
            "reason": "strict_batch_resume_proven_live_origin_contract_absent" if batch_promote and not live_promote else "evidence_gate_result",
        },
        "cold_cache": {
            "sessions": len(cold_rows),
            "median_reduction_ratio": round(statistics.median(reductions), 6) if reductions else None,
            "p90_reduction_ratio": percentile(reductions, 0.9),
            "rows": cold_rows,
        },
        "live_origin": {
            "sessions": len(live_rows_value),
            "eligible_sessions": eligible_live,
            "expected_proofs": expected_proofs,
            "authoritative_proofs": actual_proofs,
            "rows": live_rows_value,
        },
        "fixture_contract": {
            "identity_schema": "murmurmark.authoritative_asr_chunk_identity/v1",
            "cache_schema": "murmurmark.authoritative_asr_chunk_cache/v1",
            "raw_cache_schema": "murmurmark.authoritative_asr_raw_cache/v1",
            "live_proof_schema": "murmurmark.authoritative_live_asr_chunk/v1",
            "required_check": "scripts/check-authoritative-incremental-asr.py",
        },
        "evidence_errors": evidence_errors,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "authoritative_incremental_asr_v1.json"
    out_md = out_dir / "authoritative_incremental_asr_v1.md"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    print(f"overall: {payload['decision']['overall']}")
    print(f"batch_resume: {payload['decision']['batch_resume']}")
    print(f"live_origin: {payload['decision']['live_origin']}")
    print(f"written: {out_json}")
    passed = batch_promote and not evidence_errors
    return 0 if args.no_fail or passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
