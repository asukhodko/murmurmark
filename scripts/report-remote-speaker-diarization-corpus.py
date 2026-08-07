#!/usr/bin/env python3
"""Evaluate and freeze Remote Speaker Diarization v2 on a local corpus."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
from sklearn.metrics import adjusted_rand_score


SCHEMA = "murmurmark.remote_speaker_diarization_corpus_report/v2"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_diarization_frozen_manifest/v2"
REFERENCE_SCHEMA = "murmurmark.remote_speaker_diarization_reference_evaluation/v2"
BOUNDARY_SCHEMA = "murmurmark.remote_speaker_boundary_cases/v2"
REPORT_DIR = "derived/audit/remote-speaker-diarization-v2"
ROOT = Path(__file__).resolve().parents[1]
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
REFERENCE_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)\s+([^\t]+)\t(.+)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Remote Speaker Diarization v2 corpus gates.")
    parser.add_argument("sessions", nargs="+", type=Path)
    parser.add_argument(
        "--expected",
        action="append",
        default=[],
        metavar="SESSION:MIN:MAX",
        help="Expected session-local anonymous remote speaker range.",
    )
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-session", required=True)
    parser.add_argument("--reference-local-speaker", required=True)
    parser.add_argument("--boundary-cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("sessions/_reports/remote-speaker-diarization-v2"))
    parser.add_argument("--write-manifest", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"path": str(path), "exists": path.is_file()}
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def portable_artifact(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    path = str(row.get("path") or "")
    portable_path: str | None = None
    for marker in ("sessions/", "docs/", "scripts/", "policies/"):
        if marker in path:
            portable_path = marker + path.split(marker, 1)[1]
            break
    result = {key: row[key] for key in ("exists", "bytes", "sha256") if key in row}
    if portable_path:
        result["path"] = portable_path
    return result


def parse_expectations(values: list[str]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for value in values:
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError(f"invalid --expected value: {value}")
        session, minimum, maximum = parts
        result[session] = {"min": int(minimum), "max": int(maximum)}
    return result


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
    common = sum((Counter(left_tokens) & Counter(right_tokens)).values())
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


def fit_clock(dialogue: list[dict[str, Any]], reference: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[tuple[float, float]] = []
    for utterance in dialogue:
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
        candidates.append((float(scores[0][1]["start"]), float(utterance["start"])))
    if len(candidates) < 3:
        return {"status": "insufficient_alignment", "scale": 1.0, "offset_sec": 0.0, "inliers": 0}
    offsets = np.asarray([session - reference_start for reference_start, session in candidates])
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


def session_paths(session: Path) -> dict[str, Path]:
    out = session / REPORT_DIR
    return {
        "report": out / "report.json",
        "manifest": out / "artifact_manifest.json",
        "rich": out / "transcript.rich.shadow.json",
        "words": out / "word_attribution.jsonl",
        "utterances": out / "utterance_attribution.jsonl",
    }


def exact_selected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("id", "role", "speaker_label", "start", "end", "source_start", "source_end", "text")
    return [{key: row.get(key) for key in keys if key in row} for row in rows]


def session_row(session: Path, expected: dict[str, int]) -> dict[str, Any]:
    paths = session_paths(session)
    report = read_json(paths["report"])
    rich = read_json(paths["rich"])
    dialogue_path = session / str(report["source"]["dialogue"]["path"])
    selected = read_json(dialogue_path)
    selected_rows = selected.get("utterances") or selected.get("dialogue") or []
    rich_rows = rich.get("utterances") or []
    words = read_jsonl(paths["words"])
    remote_selected = [row for row in selected_rows if row.get("role") == "remote"]
    remote_rich = [row for row in rich_rows if row.get("role") == "remote"]
    word_ids = [str(row.get("word_id")) for row in words]
    turns_exact = all(
        "".join(str(turn.get("text") or "") for turn in row.get("speaker_turns") or [])
        == str(row.get("text") or "")
        for row in remote_rich
    )
    selected_exact = exact_selected_rows(selected_rows) == exact_selected_rows(rich_rows)
    published = int(report["summary"]["published_speakers"])
    expected_gate = expected["min"] <= published <= expected["max"]
    speakers = read_json(session / REPORT_DIR / "speaker_map.json").get("speakers") or []
    weights = [float(row.get("attributed_speech_sec") or 0) for row in speakers]
    dominance = max(weights) / sum(weights) if weights and sum(weights) else 0.0
    one_to_one_gate = expected["max"] > 1 or (published == 1 and dominance >= 0.98)
    return {
        "session_id": session.name,
        "expected_speakers": expected,
        "status": report.get("status"),
        "decision": report.get("decision"),
        "published_speakers": published,
        "expected_count_gate": expected_gate,
        "one_to_one_dominance": round(dominance, 6),
        "one_to_one_gate": one_to_one_gate,
        "remote_utterances": len(remote_selected),
        "remote_words": int(report["summary"]["remote_words"]),
        "attributed_words": int(report["summary"]["attributed_words"]),
        "remote_speech_sec": float(report["summary"]["remote_speech_sec"]),
        "attributed_speech_sec": float(report["summary"]["attributed_speech_sec"]),
        "attributable_remote_speech_ratio": float(report["summary"]["attributable_remote_speech_ratio"]),
        "internal_change_utterances": int(report["summary"]["internal_change_utterances"]),
        "selected_dialogue_exact": selected_exact,
        "turn_text_exact": turns_exact,
        "word_ids_unique": len(word_ids) == len(set(word_ids)),
        "word_conservation_gate": bool(report["gates"].get("word_conservation")),
        "timestamp_order_gate": bool(report["gates"].get("timestamp_order")),
        "raw_audio_unchanged": bool(report["safety"].get("raw_audio_unchanged")),
        "publish_gate": bool(report["gates"].get("publish_session_evidence")),
        "artifacts": {key: artifact(value) for key, value in paths.items()},
    }


def reference_evaluation(session: Path, reference_path: Path, local_speaker: str) -> dict[str, Any]:
    paths = session_paths(session)
    rich = read_json(paths["rich"])
    utterances = [
        row for row in rich.get("utterances") or []
        if row.get("role") == "remote" and row.get("id")
    ]
    attributions = {
        str(row["utterance_id"]): row for row in read_jsonl(paths["utterances"])
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
    for utterance_index, utterance in enumerate(utterances):
        text = str(utterance.get("text") or "")
        if len(normalize_text(text)) < 8:
            continue
        for reference_index, row in enumerate(reference):
            mapped = float(clock["scale"]) * float(row["start"]) + float(clock["offset_sec"])
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
                "prediction_status": attribution.get("status"),
            }
        )
    attributed = [row for row in matches if row["predicted_speaker"] is not None]
    true = [str(row["reference_speaker"]) for row in attributed]
    predicted = [str(row["predicted_speaker"]) for row in attributed]
    attributed_only = {
        "rows": len(attributed),
        "adjusted_rand_index": round(float(adjusted_rand_score(true, predicted)), 6) if len(attributed) >= 2 else 0.0,
        "bcubed": bcubed(true, predicted),
        "pairwise": pairwise(true, predicted),
    }
    return {
        "schema": REFERENCE_SCHEMA,
        "status": "completed" if matches else "insufficient_alignment",
        "session_id": session.name,
        "reference": artifact(reference_path),
        "clock": clock,
        "reference_remote_speakers": len({row["reference_speaker"] for row in matches}),
        "aligned_rows": len(matches),
        "attributed_rows": len(attributed),
        "attributed_row_ratio": round(len(attributed) / len(matches), 6) if matches else 0.0,
        "attributed_only": attributed_only,
        "rows": matches,
    }


def boundary_evaluation(sessions: dict[str, Path], cases_path: Path) -> dict[str, Any]:
    payload = read_json(cases_path)
    if payload.get("schema") != BOUNDARY_SCHEMA:
        raise ValueError("boundary case schema mismatch")
    rows: list[dict[str, Any]] = []
    for case in payload.get("cases") or []:
        session_id = str(case["session_id"])
        session = sessions.get(session_id)
        if not session:
            rows.append({**case, "passed": False, "reason": "session_missing"})
            continue
        attributions = {
            str(row["utterance_id"]): row
            for row in read_jsonl(session_paths(session)["utterances"])
        }
        row = attributions.get(str(case["utterance_id"]))
        if not row:
            rows.append({**case, "passed": False, "reason": "utterance_missing"})
            continue
        turns = row.get("speaker_turns") or []
        supported_runs = sum(turn.get("speaker_id") is not None for turn in turns)
        distinct = len({turn.get("speaker_id") for turn in turns if turn.get("speaker_id")})
        exact = "".join(str(turn.get("text") or "") for turn in turns) == str(case.get("selected_text") or "")
        passed = (
            supported_runs >= int(case.get("min_supported_runs") or 2)
            and distinct >= int(case.get("min_distinct_speakers") or 2)
            and exact
        )
        rows.append(
            {
                "session_id": session_id,
                "utterance_id": case["utterance_id"],
                "supported_runs": supported_runs,
                "distinct_speakers": distinct,
                "text_exact": exact,
                "passed": passed,
                "reason": "supported_internal_change" if passed else "boundary_gate_failed",
            }
        )
    return {
        "schema": BOUNDARY_SCHEMA,
        "source": artifact(cases_path),
        "cases": rows,
        "passed": bool(rows) and all(row["passed"] for row in rows),
    }


def manifest_payload(
    sessions: list[dict[str, Any]], reference: dict[str, Any], boundaries: dict[str, Any], decision: str
) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "decision": decision,
        "implementation": {
            "audit": portable_artifact(artifact(ROOT / "scripts/audit-remote-speaker-diarization.py")),
            "corpus_report": portable_artifact(artifact(Path(__file__).resolve())),
        },
        "sessions": [
            {
                "session_id": row["session_id"],
                "expected_speakers": row["expected_speakers"],
                "report": portable_artifact(row["artifacts"]["report"]),
                "manifest": portable_artifact(row["artifacts"]["manifest"]),
                "rich": portable_artifact(row["artifacts"]["rich"]),
            }
            for row in sessions
        ],
        "reference": portable_artifact(reference.get("reference")),
        "boundary_cases": portable_artifact(boundaries.get("source")),
    }


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    reference = report["reference_evaluation"]
    lines = [
        "# Remote Speaker Diarization v2 Corpus",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Sessions: `{summary['sessions']}`",
        f"- Attributable remote speech: `{summary['attributable_remote_speech_ratio']:.6f}`",
        f"- Reference attributed rows: `{reference.get('attributed_rows', 0)}/{reference.get('aligned_rows', 0)}`",
        f"- Reference B-cubed F1: `{((reference.get('attributed_only') or {}).get('bcubed') or {}).get('f1', 0):.6f}`",
        f"- Reference pairwise precision: `{((reference.get('attributed_only') or {}).get('pairwise') or {}).get('precision', 0):.6f}`",
        f"- Boundary cases: `{sum(row['passed'] for row in report['boundary_evaluation']['cases'])}/{len(report['boundary_evaluation']['cases'])}`",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- `{key}`: `{str(value).lower()}`" for key, value in report["gates"].items())
    lines.extend(["", "## Sessions", ""])
    for row in report["sessions"]:
        lines.append(
            f"- `{row['session_id']}`: speakers `{row['published_speakers']}`, "
            f"coverage `{row['attributable_remote_speech_ratio']:.6f}`, publish `{str(row['publish_gate']).lower()}`"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    sessions = [path.expanduser().resolve() for path in args.sessions]
    expectations = parse_expectations(args.expected)
    missing = [session.name for session in sessions if session.name not in expectations]
    if missing:
        raise SystemExit(f"missing --expected for: {', '.join(missing)}")
    rows = [session_row(session, expectations[session.name]) for session in sessions]
    by_name = {session.name: session for session in sessions}
    reference_session = by_name.get(args.reference_session)
    if not reference_session:
        raise SystemExit(f"reference session not in corpus: {args.reference_session}")
    reference = reference_evaluation(
        reference_session, args.reference.expanduser().resolve(), args.reference_local_speaker
    )
    boundaries = boundary_evaluation(by_name, args.boundary_cases.expanduser().resolve())
    total_remote = sum(row["remote_speech_sec"] for row in rows)
    attributed = sum(row["attributed_speech_sec"] for row in rows)
    ratio = attributed / total_remote if total_remote else 0.0
    attributed_only = reference.get("attributed_only") or {}
    gates = {
        "minimum_corpus_size": len(rows) >= 6,
        "attributable_remote_speech_ratio": ratio >= 0.85,
        "reference_minimum_rows": int(reference.get("aligned_rows") or 0) >= 50,
        "reference_attributed_bcubed_f1": float((attributed_only.get("bcubed") or {}).get("f1") or 0) >= 0.90,
        "reference_attributed_pairwise_precision": float((attributed_only.get("pairwise") or {}).get("precision") or 0) >= 0.90,
        "all_session_publish_gates": all(row["publish_gate"] for row in rows),
        "all_expected_speaker_ranges": all(row["expected_count_gate"] for row in rows),
        "all_one_to_one_controls": all(row["one_to_one_gate"] for row in rows),
        "all_selected_dialogue_exact": all(row["selected_dialogue_exact"] for row in rows),
        "all_turn_text_exact": all(row["turn_text_exact"] for row in rows),
        "all_word_ids_unique": all(row["word_ids_unique"] for row in rows),
        "all_word_conservation": all(row["word_conservation_gate"] for row in rows),
        "all_timestamp_order": all(row["timestamp_order_gate"] for row in rows),
        "all_raw_audio_unchanged": all(row["raw_audio_unchanged"] for row in rows),
        "boundary_cases": boundaries["passed"],
    }
    decision = "PROMOTE" if all(gates.values()) else "DO_NOT_PROMOTE"
    report = {
        "schema": SCHEMA,
        "decision": decision,
        "summary": {
            "sessions": len(rows),
            "remote_speech_sec": round(total_remote, 6),
            "attributed_speech_sec": round(attributed, 6),
            "attributable_remote_speech_ratio": round(ratio, 6),
            "remote_words": sum(row["remote_words"] for row in rows),
            "attributed_words": sum(row["attributed_words"] for row in rows),
            "published_speakers": sum(row["published_speakers"] for row in rows),
            "internal_change_utterances": sum(row["internal_change_utterances"] for row in rows),
        },
        "gates": gates,
        "sessions": rows,
        "reference_evaluation": reference,
        "boundary_evaluation": boundaries,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    write_json(args.output / "remote_speaker_diarization_corpus_report.json", report)
    (args.output / "remote_speaker_diarization_corpus_report.md").write_text(
        report_markdown(report), encoding="utf-8"
    )
    write_json(args.output / "reference_evaluation.json", reference)
    current_manifest = manifest_payload(rows, reference, boundaries, decision)
    if args.write_manifest:
        write_json(args.write_manifest, current_manifest)
    if args.frozen_manifest:
        frozen = read_json(args.frozen_manifest)
        gates["frozen_manifest_match"] = frozen == current_manifest
        if not gates["frozen_manifest_match"]:
            report["decision"] = "DO_NOT_PROMOTE"
            write_json(args.output / "remote_speaker_diarization_corpus_report.json", report)
            (args.output / "remote_speaker_diarization_corpus_report.md").write_text(
                report_markdown(report), encoding="utf-8"
            )
    print(
        f"remote diarization corpus: decision={report['decision']} sessions={len(rows)} "
        f"coverage={ratio:.6f}"
    )
    return 0 if report["decision"] == "PROMOTE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
