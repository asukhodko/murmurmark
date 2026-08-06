#!/usr/bin/env python3
"""Freeze and decide Remote Speaker Evidence Map v1 across real sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.metrics import adjusted_rand_score

import authoritative_asr_cache as cache


SCHEMA = "murmurmark.remote_speaker_evidence_corpus_report/v1"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_evidence_frozen_manifest/v1"
REFERENCE_SCHEMA = "murmurmark.remote_speaker_reference_evaluation/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_OUTPUT = Path("sessions/_reports/remote-speaker-evidence-v1")
DEFAULT_MANIFEST = Path("docs/testing/remote-speaker-evidence-map-v1-manifest.json")
REPORT_RELATIVE = Path("derived/audit/remote-speaker-evidence-v1")
REFERENCE_LINE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\t([^\t]+)\t(.+)$")
TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", flags=re.IGNORECASE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Decide anonymous remote-speaker evidence on a frozen corpus.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--refresh-manifest", action="store_true")
    parser.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="SESSION=MIN:MAX",
        help="Expected published anonymous speaker range for a session.",
    )
    parser.add_argument("--reference-transcript", type=Path)
    parser.add_argument("--reference-session")
    parser.add_argument("--reference-local-speaker")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    return cache.read_json(path) or {}


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    cache.atomic_write_bytes(path, canonical_json_bytes(payload))


def sha256(path: Path) -> str:
    return cache.sha256_file(path)


def parse_expectations(values: list[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for value in values:
        try:
            session_id, bounds = value.split("=", 1)
            minimum, maximum = (int(item) for item in bounds.split(":", 1))
        except (ValueError, TypeError) as error:
            raise ValueError(f"invalid --expect {value!r}; expected SESSION=MIN:MAX") from error
        if minimum < 1 or maximum < minimum:
            raise ValueError(f"invalid speaker range in --expect {value!r}")
        result[session_id] = {"min": minimum, "max": maximum}
    return result


def strip_path(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: strip_path(item) for key, item in value.items() if key not in {"path", "pid"}}
    if isinstance(value, list):
        return [strip_path(item) for item in value]
    return value


def artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    return {"exists": True, **cache.file_fingerprint(path, include_path=False)}


def implementation_provenance() -> dict[str, Any]:
    return {
        "script": Path(__file__).name,
        "version": SCRIPT_VERSION,
        "fingerprint": artifact(Path(__file__).resolve()),
    }


def report_paths(session: Path) -> dict[str, Path]:
    root = session / REPORT_RELATIVE
    return {
        "root": root,
        "report": root / "report.json",
        "map": root / "speaker_map.json",
        "attribution": root / "utterance_attribution.jsonl",
        "rich": root / "transcript.rich.shadow.json",
        "manifest": root / "artifact_manifest.json",
    }


def selected_dialogue(session: Path, report: dict[str, Any]) -> Path:
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    dialogue = source.get("dialogue") if isinstance(source.get("dialogue"), dict) else {}
    path = dialogue.get("path")
    if isinstance(path, str) and path:
        candidate = Path(path)
        return candidate if candidate.is_absolute() else session / candidate
    profile = str(source.get("profile") or "current")
    suffix = "" if profile == "current" else f".{profile}"
    return session / f"derived/transcript-simple/whisper-cpp/resolved/clean_dialogue{suffix}.json"


def session_row(session: Path, expected: dict[str, int]) -> dict[str, Any]:
    session = session.expanduser().resolve()
    paths = report_paths(session)
    report = read_json(paths["report"])
    speaker_map = read_json(paths["map"])
    rich = read_json(paths["rich"])
    dialogue_path = selected_dialogue(session, report)
    dialogue = read_json(dialogue_path)
    source_utterances = dialogue.get("utterances") if isinstance(dialogue.get("utterances"), list) else None
    rich_utterances = rich.get("utterances") if isinstance(rich.get("utterances"), list) else None
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    stability = report.get("stability") if isinstance(report.get("stability"), dict) else {}
    safety = report.get("safety") if isinstance(report.get("safety"), dict) else {}
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    published = int(summary.get("published_speakers") or 0)
    expected_passed = expected["min"] <= published <= expected["max"]
    return {
        "session_id": session.name,
        "expected_speakers": expected,
        "status": report.get("status") or "missing",
        "decision": report.get("decision") or "missing",
        "published_speakers": published,
        "published_utterances": int(summary.get("published_utterances") or 0),
        "aggregate_utterances": int(summary.get("aggregate_utterances") or 0),
        "published_speech_sec": round(float(summary.get("published_speech_sec") or 0), 6),
        "remote_speech_sec": round(float(summary.get("remote_speech_sec") or 0), 6),
        "published_speech_ratio": round(float(summary.get("published_speech_ratio") or 0), 6),
        "reverse_order_ari": round(float(stability.get("reverse_order_ari") or 0), 6),
        "chunk_replay_ari": round(float(stability.get("chunk_replay_ari") or 0), 6),
        "boundary_shift_sec": round(float(stability.get("boundary_shift_sec") or 0), 6),
        "publish_gate": gates.get("publish_session_speaker_map") is True,
        "expected_count_gate": expected_passed,
        "lossless_utterances": source_utterances is not None and source_utterances == rich_utterances,
        "raw_remote_unchanged": safety.get("raw_remote_unchanged") is True,
        "selected_dialogue_unchanged": safety.get("selected_dialogue_unchanged") is True,
        "inputs": {
            "implementation": strip_path(report.get("implementation") or {}),
            "source": strip_path(report.get("source") or {}),
            "model": strip_path(report.get("model") or {}),
            "parameters": strip_path(report.get("parameters") or {}),
            "dialogue": artifact(dialogue_path),
        },
        "outputs": {name: artifact(path) for name, path in paths.items() if name != "root"},
    }


def normalize_text(text: str) -> str:
    return " ".join(TOKEN_RE.findall(text.lower().replace("ё", "е")))


def text_similarity(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = left_norm.split()
    right_tokens = right_norm.split()
    left_counts = Counter(left_tokens)
    right_counts = Counter(right_tokens)
    common = sum((left_counts & right_counts).values())
    token_f1 = 2 * common / (len(left_tokens) + len(right_tokens))
    containment = common / min(len(left_tokens), len(right_tokens))
    return max(sequence, 0.55 * token_f1 + 0.45 * containment)


def parse_reference(path: Path, local_speaker: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    speaker_map: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = REFERENCE_LINE.match(line.strip())
        if not match:
            continue
        hour, minute, second, speaker, text = match.groups()
        if speaker.strip() == local_speaker.strip():
            continue
        if speaker not in speaker_map:
            speaker_map[speaker] = f"reference_speaker_{len(speaker_map) + 1:02d}"
        rows.append(
            {
                "start": int(hour) * 3600 + int(minute) * 60 + int(second),
                "speaker": speaker_map[speaker],
                "text": text,
            }
        )
    return rows


def fit_clock(dialogue_rows: list[dict[str, Any]], reference: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[tuple[float, float, float]] = []
    for utterance in dialogue_rows:
        text = str(utterance.get("text") or "")
        if len(normalize_text(text)) < 16:
            continue
        scores = sorted(
            ((text_similarity(text, row["text"]), row) for row in reference),
            key=lambda item: item[0],
            reverse=True,
        )
        if not scores or scores[0][0] < 0.82:
            continue
        if scores[0][0] < 0.995 and len(scores) > 1 and scores[0][0] - scores[1][0] < 0.05:
            continue
        candidates.append((float(scores[0][1]["start"]), float(utterance["start"]), scores[0][0]))
    if len(candidates) < 3:
        return {"status": "insufficient_alignment", "scale": 1.0, "offset_sec": 0.0, "inliers": 0}
    offsets = np.asarray([session - reference_start for reference_start, session, _score in candidates])
    median_offset = float(np.median(offsets))
    inliers = [row for row in candidates if abs((row[1] - row[0]) - median_offset) <= 30.0]
    if len(inliers) < 3:
        return {"status": "insufficient_alignment", "scale": 1.0, "offset_sec": median_offset, "inliers": len(inliers)}
    x = np.asarray([row[0] for row in inliers], dtype=np.float64)
    y = np.asarray([row[1] for row in inliers], dtype=np.float64)
    scale, offset = np.polyfit(x, y, 1)
    refined = [row for row in inliers if abs((scale * row[0] + offset) - row[1]) <= 10.0]
    if len(refined) >= 3:
        x = np.asarray([row[0] for row in refined], dtype=np.float64)
        y = np.asarray([row[1] for row in refined], dtype=np.float64)
        scale, offset = np.polyfit(x, y, 1)
    residuals = [abs((scale * row[0] + offset) - row[1]) for row in refined]
    return {
        "status": "aligned",
        "scale": round(float(scale), 9),
        "offset_sec": round(float(offset), 6),
        "inliers": len(refined),
        "median_residual_sec": round(float(np.median(residuals)), 6) if residuals else None,
    }


def bcubed(true: list[str], predicted: list[str]) -> dict[str, float]:
    true_groups: dict[str, set[int]] = defaultdict(set)
    predicted_groups: dict[str, set[int]] = defaultdict(set)
    for index, (truth, guess) in enumerate(zip(true, predicted)):
        true_groups[truth].add(index)
        predicted_groups[guess].add(index)
    precisions: list[float] = []
    recalls: list[float] = []
    for index, (truth, guess) in enumerate(zip(true, predicted)):
        overlap = len(true_groups[truth] & predicted_groups[guess])
        precisions.append(overlap / len(predicted_groups[guess]))
        recalls.append(overlap / len(true_groups[truth]))
    precision = float(np.mean(precisions)) if precisions else 0.0
    recall = float(np.mean(recalls)) if recalls else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def pairwise(true: list[str], predicted: list[str]) -> dict[str, float | int]:
    tp = fp = fn = 0
    for left in range(len(true)):
        for right in range(left + 1, len(true)):
            same_true = true[left] == true[right]
            same_predicted = predicted[left] == predicted[right]
            if same_true and same_predicted:
                tp += 1
            elif not same_true and same_predicted:
                fp += 1
            elif same_true and not same_predicted:
                fn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
    }


def reference_evaluation(
    session: Path,
    reference_path: Path,
    local_speaker: str,
) -> dict[str, Any]:
    paths = report_paths(session)
    rich = read_json(paths["rich"])
    utterances = [
        row for row in rich.get("utterances") or []
        if isinstance(row, dict) and row.get("role") == "remote" and row.get("id")
    ]
    attributions = {
        row["utterance_id"]: row
        for row in rich.get("remote_speaker_attributions") or []
        if isinstance(row, dict) and row.get("utterance_id")
    }
    reference = parse_reference(reference_path, local_speaker)
    clock = fit_clock(utterances, reference)
    if clock["status"] != "aligned":
        return {
            "schema": REFERENCE_SCHEMA,
            "status": "insufficient_alignment",
            "session_id": session.name,
            "reference": artifact(reference_path),
            "clock": clock,
            "aligned_rows": 0,
        }

    candidates: list[tuple[float, float, int, int]] = []
    scale = float(clock["scale"])
    offset = float(clock["offset_sec"])
    for utterance_index, utterance in enumerate(utterances):
        text = str(utterance.get("text") or "")
        if len(normalize_text(text)) < 8:
            continue
        for reference_index, row in enumerate(reference):
            mapped = scale * float(row["start"]) + offset
            delta = abs(mapped - float(utterance["start"]))
            if delta > 12.0:
                continue
            score = text_similarity(text, row["text"])
            if score >= 0.68:
                candidates.append((score, -delta, utterance_index, reference_index))
    candidates.sort(reverse=True)
    used_utterances: set[int] = set()
    used_reference: set[int] = set()
    matches: list[dict[str, Any]] = []
    for score, negative_delta, utterance_index, reference_index in candidates:
        if utterance_index in used_utterances or reference_index in used_reference:
            continue
        used_utterances.add(utterance_index)
        used_reference.add(reference_index)
        utterance = utterances[utterance_index]
        reference_row = reference[reference_index]
        attribution = attributions.get(str(utterance["id"]), {})
        predicted = attribution.get("speaker_id")
        matches.append(
            {
                "utterance_id": str(utterance["id"]),
                "reference_speaker": reference_row["speaker"],
                "predicted_speaker": predicted,
                "alignment_score": round(score, 6),
                "clock_delta_sec": round(-negative_delta, 6),
            }
        )
    true = [row["reference_speaker"] for row in matches]
    predicted = [
        str(row["predicted_speaker"] or f"aggregate:{row['utterance_id']}") for row in matches
    ]
    attributed = sum(row["predicted_speaker"] is not None for row in matches)
    b3 = bcubed(true, predicted)
    pairs = pairwise(true, predicted)
    attributed_matches = [row for row in matches if row["predicted_speaker"] is not None]
    attributed_true = [str(row["reference_speaker"]) for row in attributed_matches]
    attributed_predicted = [str(row["predicted_speaker"]) for row in attributed_matches]
    attributed_only = {
        "rows": len(attributed_matches),
        "adjusted_rand_index": round(
            float(adjusted_rand_score(attributed_true, attributed_predicted)), 6
        ) if len(attributed_matches) >= 2 else 0.0,
        "bcubed": bcubed(attributed_true, attributed_predicted),
        "pairwise": pairwise(attributed_true, attributed_predicted),
    }
    result = {
        "schema": REFERENCE_SCHEMA,
        "status": "completed" if matches else "insufficient_alignment",
        "session_id": session.name,
        "reference": artifact(reference_path),
        "clock": clock,
        "reference_remote_speakers": len(set(true)),
        "aligned_rows": len(matches),
        "attributed_rows": attributed,
        "attributed_row_ratio": round(attributed / len(matches), 6) if matches else 0.0,
        "adjusted_rand_index": round(float(adjusted_rand_score(true, predicted)), 6) if len(matches) >= 2 else 0.0,
        "bcubed": b3,
        "pairwise": pairs,
        "attributed_only": attributed_only,
        "rows": matches,
    }
    return result


def manifest_payload(
    rows: list[dict[str, Any]], expectations: dict[str, dict[str, int]], reference: dict[str, Any]
) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "generator": implementation_provenance(),
        "sessions": [
            {
                "session_id": row["session_id"],
                "expected_speakers": expectations[row["session_id"]],
                "inputs": row["inputs"],
                "outputs": row["outputs"],
            }
            for row in rows
        ],
        "reference": {
            "session_id": reference.get("session_id"),
            "sha256": (reference.get("reference") or {}).get("sha256"),
            "bytes": (reference.get("reference") or {}).get("bytes"),
        },
    }


def compare_manifest(current: dict[str, Any], frozen: dict[str, Any]) -> bool:
    return bool(frozen) and canonical_json_bytes(current) == canonical_json_bytes(frozen)


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    reference = report["reference_evaluation"]
    lines = [
        "# Remote Speaker Evidence Map v1 Corpus",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Sessions: `{summary['sessions']}`",
        f"- Published anonymous speakers: `{summary['published_speakers']}`",
        f"- Published speech: `{summary['published_speech_sec']:.3f}s`",
        f"- Aggregate speech: `{summary['aggregate_speech_sec']:.3f}s`",
        f"- Reference aligned rows: `{reference.get('aligned_rows', 0)}`",
        f"- Reference attributed ratio: `{reference.get('attributed_row_ratio', 0):.6f}`",
        f"- Reference attributed-only ARI: `{(reference.get('attributed_only') or {}).get('adjusted_rand_index', 0):.6f}`",
        f"- Reference attributed-only B-cubed F1: `{((reference.get('attributed_only') or {}).get('bcubed') or {}).get('f1', 0):.6f}`",
        "",
        "## Sessions",
        "",
        "| Session | Expected | Published | Coverage | Reverse ARI | Chunk ARI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["sessions"]:
        expected = row["expected_speakers"]
        lines.append(
            f"| `{row['session_id']}` | `{expected['min']}..{expected['max']}` | "
            f"`{row['published_speakers']}` | `{row['published_speech_ratio']:.3f}` | "
            f"`{row['reverse_order_ari']:.3f}` | `{row['chunk_replay_ari']:.3f}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This decision promotes only optional, anonymous audit evidence. Selected transcript text,",
            "Evidence Handoff v2, notes and guarded export remain unchanged. Names and cross-session",
            "identity are outside this profile.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    manifest_path = args.frozen_manifest.expanduser().resolve()
    frozen = read_json(manifest_path)
    expectations = parse_expectations(args.expect)
    if not expectations and frozen.get("schema") == MANIFEST_SCHEMA:
        expectations = {
            str(row["session_id"]): dict(row["expected_speakers"])
            for row in frozen.get("sessions") or []
            if isinstance(row, dict) and row.get("session_id") and isinstance(row.get("expected_speakers"), dict)
        }
    session_ids = [path.expanduser().resolve().name for path in args.sessions]
    missing_expectations = [session_id for session_id in session_ids if session_id not in expectations]
    if missing_expectations:
        raise SystemExit(f"missing --expect for: {', '.join(missing_expectations)}")

    rows = [session_row(session, expectations[session.expanduser().resolve().name]) for session in args.sessions]
    reference_session_id = args.reference_session or str((frozen.get("reference") or {}).get("session_id") or "")
    reference_path = args.reference_transcript.expanduser().resolve() if args.reference_transcript else None
    reference: dict[str, Any]
    if reference_path and args.reference_local_speaker and reference_session_id:
        session = next((path.expanduser().resolve() for path in args.sessions if path.expanduser().resolve().name == reference_session_id), None)
        reference = (
            reference_evaluation(session, reference_path, args.reference_local_speaker)
            if session is not None
            else {"schema": REFERENCE_SCHEMA, "status": "reference_session_missing", "session_id": reference_session_id}
        )
    else:
        reference = {"schema": REFERENCE_SCHEMA, "status": "reference_not_provided", "session_id": reference_session_id}

    current_manifest = manifest_payload(rows, expectations, reference)
    if args.refresh_manifest:
        write_json(manifest_path, current_manifest)
        frozen = current_manifest
    frozen_match = compare_manifest(current_manifest, frozen)
    total_remote = sum(row["remote_speech_sec"] for row in rows)
    published = sum(row["published_speech_sec"] for row in rows)

    reference_gates = {
        "reference_completed": reference.get("status") == "completed",
        "reference_minimum_rows": int(reference.get("aligned_rows") or 0) >= 50,
        "reference_attributed_ratio": float(reference.get("attributed_row_ratio") or 0) >= 0.50,
        "reference_attributed_ari": float(
            (reference.get("attributed_only") or {}).get("adjusted_rand_index") or 0
        ) >= 0.80,
        "reference_attributed_bcubed_f1": float(
            ((reference.get("attributed_only") or {}).get("bcubed") or {}).get("f1") or 0
        ) >= 0.80,
        "reference_conservative_bcubed_precision": float(
            (reference.get("bcubed") or {}).get("precision") or 0
        ) >= 0.90,
        "reference_pairwise_precision": float((reference.get("pairwise") or {}).get("precision") or 0) >= 0.90,
    }
    gates = {
        "minimum_corpus_size": len(rows) >= 6,
        "at_least_two_one_to_one_controls": sum(row["expected_speakers"] == {"min": 1, "max": 1} for row in rows) >= 2,
        "at_least_three_group_sessions": sum(row["expected_speakers"]["min"] >= 2 for row in rows) >= 3,
        "frozen_inputs_match": frozen_match,
        "all_session_publish_gates": bool(rows) and all(row["publish_gate"] for row in rows),
        "all_expected_speaker_ranges": bool(rows) and all(row["expected_count_gate"] for row in rows),
        "all_lossless_utterances": bool(rows) and all(row["lossless_utterances"] for row in rows),
        "all_raw_remote_unchanged": bool(rows) and all(row["raw_remote_unchanged"] for row in rows),
        "all_selected_dialogue_unchanged": bool(rows) and all(row["selected_dialogue_unchanged"] for row in rows),
        "all_reverse_order_stable": bool(rows) and all(row["reverse_order_ari"] >= 0.99 for row in rows),
        "all_chunk_replay_stable": bool(rows) and all(row["chunk_replay_ari"] >= 0.80 for row in rows),
        "all_boundaries_lossless": bool(rows) and all(row["boundary_shift_sec"] == 0 for row in rows),
        "corpus_published_speech_ratio_at_least_45_percent": bool(total_remote)
        and published / total_remote >= 0.45,
        **reference_gates,
    }
    promote = all(gates.values())
    report = {
        "schema": SCHEMA,
        "generator": implementation_provenance(),
        "decision": "PROMOTE_AUDIT_ONLY" if promote else "DO_NOT_PROMOTE",
        "promotion_scope": "optional_anonymous_remote_speaker_evidence" if promote else "none",
        "gates": gates,
        "summary": {
            "sessions": len(rows),
            "published_speakers": sum(row["published_speakers"] for row in rows),
            "published_utterances": sum(row["published_utterances"] for row in rows),
            "aggregate_utterances": sum(row["aggregate_utterances"] for row in rows),
            "published_speech_sec": round(published, 6),
            "aggregate_speech_sec": round(total_remote - published, 6),
            "published_speech_ratio": round(published / total_remote, 6) if total_remote else 0.0,
        },
        "sessions": rows,
        "reference_evaluation": reference,
        "safety": {
            "selected_transcript_mutated": False,
            "evidence_handoff_mutated": False,
            "guarded_export_mutated": False,
            "speaker_names_published": False,
            "cross_session_identity_linking": False,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "remote_speaker_evidence_corpus_report.json", report)
    write_json(output / "reference_evaluation.json", reference)
    cache.atomic_write_bytes(
        output / "remote_speaker_evidence_corpus_report.md", report_markdown(report).encode("utf-8")
    )
    print(
        f"remote speaker corpus: decision={report['decision']} sessions={len(rows)} "
        f"speakers={report['summary']['published_speakers']} "
        f"coverage={report['summary']['published_speech_ratio']:.3f}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
