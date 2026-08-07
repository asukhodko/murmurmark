#!/usr/bin/env python3
"""Recover bounded remote-speaker unknowns without changing selected transcript text."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.3.0"
REPORT_SCHEMA = "murmurmark.remote_speaker_coverage_report/v3"
WORD_SCHEMA = "murmurmark.remote_speaker_word/v3"
UTTERANCE_SCHEMA = "murmurmark.remote_speaker_utterance/v3"
DECISION_SCHEMA = "murmurmark.remote_speaker_coverage_decision/v3"
CAUSE_MAP_SCHEMA = "murmurmark.remote_speaker_unknown_cause_map/v3"
RICH_SCHEMA = "murmurmark.remote_speaker_rich_transcript/v3"
MAP_SCHEMA = "murmurmark.remote_speaker_map/v3"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_coverage_artifact_manifest/v3"
V2_REPORT_SCHEMA = "murmurmark.remote_speaker_diarization_report/v2"
V2_FRAME_SCHEMA = "murmurmark.remote_speaker_frame/v2"
V2_WORD_SCHEMA = "murmurmark.remote_speaker_word/v2"
V2_UTTERANCE_SCHEMA = "murmurmark.remote_speaker_utterance/v2"
V2_MAP_SCHEMA = "murmurmark.remote_speaker_map/v2"
V2_RICH_SCHEMA = "murmurmark.remote_speaker_rich_transcript/v2"
V2_MANIFEST_SCHEMA = "murmurmark.remote_speaker_diarization_artifact_manifest/v2"
DEFAULT_INPUT_DIR = Path("derived/audit/remote-speaker-diarization-v2")
DEFAULT_OUTPUT_DIR = Path("derived/audit/remote-speaker-coverage-v3")
V2_POLICY = ROOT / "policies/remote-speaker-diarization-v2.json"
V3_POLICY = ROOT / "policies/remote-speaker-coverage-v3.json"


class CoverageError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover high-confidence unknown remote words from frozen v2 frame scores."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-similarity", type=float, default=0.66)
    parser.add_argument("--min-margin", type=float, default=0.008)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--require-promoted", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.min_similarity <= 1:
        parser.error("min-similarity must be in [0, 1]")
    if not 0 <= args.min_margin <= 1:
        parser.error("min-margin must be in [0, 1]")
    return args


def canonical_json(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CoverageError(f"expected_json_object:{path.name}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise CoverageError(f"expected_jsonl_objects:{path.name}")
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(payload))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def session_path(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def fingerprint(path: Path, session: Path | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": session_path(path, session) if session else str(path.resolve()),
        "exists": path.is_file(),
    }
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def same_fingerprint(row: Any, path: Path) -> bool:
    return (
        isinstance(row, dict)
        and row.get("exists") is True
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256(path)
    )


def resolve_source_path(session: Path, row: Any) -> Path | None:
    if not isinstance(row, dict) or not row.get("path"):
        return None
    path = Path(str(row["path"])).expanduser()
    return path if path.is_absolute() else session / path


def implementation() -> dict[str, Any]:
    return {"script": fingerprint(Path(__file__).resolve()), "version": VERSION}


def verify_v2_promotion(report: dict[str, Any]) -> bool:
    try:
        policy = read_json(V2_POLICY)
        manifest_row = policy["corpus_manifest"]
        manifest_path = ROOT / str(manifest_row["path"])
        manifest = read_json(manifest_path)
        audit = (manifest.get("implementation") or {}).get("audit") or {}
        report_script = (report.get("implementation") or {}).get("script") or {}
        return (
            policy.get("schema") == "murmurmark.remote_speaker_diarization_policy/v2"
            and policy.get("state") == "promoted"
            and manifest.get("schema")
            == "murmurmark.remote_speaker_diarization_frozen_manifest/v2"
            and manifest.get("decision") == "PROMOTE"
            and manifest_row.get("sha256") == sha256(manifest_path)
            and audit.get("sha256") == sha256(ROOT / "scripts/audit-remote-speaker-diarization.py")
            and report_script.get("sha256") == audit.get("sha256")
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def verify_v3_promotion(report: dict[str, Any]) -> bool:
    try:
        policy = read_json(V3_POLICY)
        manifest_row = policy["corpus_manifest"]
        manifest_path = ROOT / str(manifest_row["path"])
        manifest = read_json(manifest_path)
        audit = (manifest.get("implementation") or {}).get("audit") or {}
        report_script = (report.get("implementation") or {}).get("script") or {}
        return (
            policy.get("schema") == "murmurmark.remote_speaker_coverage_policy/v3"
            and policy.get("state") == "promoted"
            and manifest.get("schema") == "murmurmark.remote_speaker_coverage_frozen_manifest/v3"
            and manifest.get("decision") == "PROMOTE"
            and manifest_row.get("sha256") == sha256(manifest_path)
            and audit.get("sha256") == sha256(Path(__file__).resolve())
            and report_script.get("sha256") == audit.get("sha256")
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return False


def input_paths(input_dir: Path) -> dict[str, Path]:
    return {
        "report": input_dir / "report.json",
        "manifest": input_dir / "artifact_manifest.json",
        "frames": input_dir / "frame_attribution.jsonl",
        "words": input_dir / "word_attribution.jsonl",
        "utterances": input_dir / "utterance_attribution.jsonl",
        "speaker_map": input_dir / "speaker_map.json",
        "rich": input_dir / "transcript.rich.shadow.json",
    }


def validate_v2_inputs(session: Path, paths: dict[str, Path]) -> dict[str, Any]:
    if any(not path.is_file() for path in paths.values()):
        raise CoverageError("v2_artifact_missing")
    report = read_json(paths["report"])
    manifest = read_json(paths["manifest"])
    if report.get("schema") != V2_REPORT_SCHEMA or report.get("decision") != "PUBLISH_EVIDENCE":
        raise CoverageError("v2_report_not_publishable")
    if manifest.get("schema") != V2_MANIFEST_SCHEMA:
        raise CoverageError("v2_manifest_schema_invalid")
    for name, digest in (manifest.get("artifacts") or {}).items():
        path = paths["manifest"].parent / str(name)
        if not path.is_file() or sha256(path) != digest:
            raise CoverageError(f"v2_artifact_stale:{name}")
    if not verify_v2_promotion(report):
        raise CoverageError("v2_policy_not_promoted")
    for key in ("dialogue", "remote_audio", "raw_remote_json", "v1_report", "v1_attribution"):
        row = (report.get("source") or {}).get(key)
        path = resolve_source_path(session, row)
        if path is None or not same_fingerprint(row, path):
            raise CoverageError(f"v2_source_stale:{key}")
    return report


def source_fingerprints(paths: dict[str, Path], session: Path) -> dict[str, Any]:
    return {name: fingerprint(path, session) for name, path in paths.items()}


def frame_candidate(
    frame: dict[str, Any], speakers: set[str], min_similarity: float, min_margin: float
) -> dict[str, Any]:
    if frame.get("speaker_id") in speakers:
        return {
            "speaker_id": str(frame["speaker_id"]),
            "source": "v2_accepted_frame",
            "similarity": (frame.get("confidence") or {}).get("similarity"),
            "margin": (frame.get("confidence") or {}).get("margin"),
        }
    scores = {
        str(speaker): float(value)
        for speaker, value in ((frame.get("confidence") or {}).get("speaker_scores") or {}).items()
        if speaker in speakers
    }
    ranked = sorted(((value, speaker) for speaker, value in scores.items()), reverse=True)
    if not ranked:
        return {"speaker_id": None, "source": "embedding_unavailable", "similarity": None, "margin": None}
    similarity, speaker = ranked[0]
    margin = similarity - ranked[1][0] if len(ranked) > 1 else similarity
    accepted = similarity >= min_similarity and margin >= min_margin
    return {
        "speaker_id": speaker if accepted else None,
        "source": "bounded_relaxed_seed_frame" if accepted else "below_v3_threshold",
        "similarity": round(similarity, 6),
        "margin": round(margin, 6),
    }


def build_turns(text: str, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not words:
        return [{"speaker_id": None, "speaker_label": "Colleagues", "text": text, "word_ids": []}]
    runs: list[tuple[int, int, str | None]] = []
    left = 0
    current = words[0].get("speaker_id")
    for index, word in enumerate(words[1:], start=1):
        if word.get("speaker_id") != current:
            runs.append((left, index, current))
            left = index
            current = word.get("speaker_id")
    runs.append((left, len(words), current))
    turns: list[dict[str, Any]] = []
    char_start = 0
    for left, right, speaker in runs:
        char_end = int(words[right]["start_char"]) if right < len(words) else len(text)
        turns.append(
            {
                "speaker_id": speaker,
                "speaker_label": speaker or "Colleagues",
                "status": "attributed" if speaker else "unknown",
                "start": words[left]["start"],
                "end": words[right - 1]["end"],
                "start_char": char_start,
                "end_char": char_end,
                "text": text[char_start:char_end],
                "word_ids": [word["word_id"] for word in words[left:right]],
            }
        )
        char_start = char_end
    if "".join(str(row["text"]) for row in turns) != text:
        raise CoverageError("selected_text_reconstruction_failed")
    return turns


def transcript_markdown(utterances: list[dict[str, Any]], profile: str) -> str:
    lines = [
        "# Remote Speaker Coverage v3",
        "",
        f"Selected profile: `{profile}`  ",
        "Decision: `PUBLISH_EVIDENCE`  ",
        "Plain transcript remains authoritative.",
        "",
    ]
    for utterance in utterances:
        start = float(utterance.get("start") or 0)
        timestamp = f"{int(start // 60):02d}:{int(start % 60):02d}"
        if utterance.get("role") == "remote":
            for turn in utterance.get("speaker_turns") or []:
                label = str(turn.get("speaker_label") or "Colleagues")
                suffix = "" if turn.get("speaker_id") else " [unknown]"
                lines.extend([f"## {timestamp} {label}{suffix}", "", str(turn.get("text") or "").strip(), ""])
        else:
            lines.extend([f"## {timestamp} Me", "", str(utterance.get("text") or "").strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Remote Speaker Coverage v3",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Baseline unknown: `{summary['baseline_unknown_words']}` words / `{summary['baseline_unknown_seconds']:.3f}s`",
        f"- Recovered: `{summary['recovered_words']}` words / `{summary['recovered_seconds']:.3f}s`",
        f"- Remaining unknown: `{summary['remaining_unknown_words']}` words / `{summary['remaining_unknown_seconds']:.3f}s`",
        f"- Unknown seconds reduction: `{summary['unknown_seconds_reduction_ratio']:.6f}`",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- `{key}`: `{str(value).lower()}`" for key, value in report["gates"].items())
    lines.extend(["", "## Unknown Causes", ""])
    for row in report.get("unknown_causes") or []:
        lines.append(f"- `{row['cause']}`: `{row['words']}` words / `{row['seconds']:.3f}s`")
    return "\n".join(lines) + "\n"


def build_output_manifest(out_dir: Path, session_id: str) -> dict[str, Any]:
    names = (
        "recovery_decisions.jsonl",
        "unknown_cause_map.json",
        "word_attribution.jsonl",
        "utterance_attribution.jsonl",
        "speaker_map.json",
        "transcript.rich.shadow.json",
        "transcript.rich.shadow.md",
        "report.json",
        "report.md",
    )
    return {
        "schema": MANIFEST_SCHEMA,
        "session_id": session_id,
        "artifacts": {name: sha256(out_dir / name) for name in names},
    }


def fallback(session: Path, out_dir: Path, reason: str, source: dict[str, Any]) -> int:
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "status": "fallback",
        "decision": "FALLBACK_V2",
        "reasons": [reason],
        "source": source,
        "implementation": implementation(),
        "parameters": {},
        "summary": {
            "baseline_unknown_words": 0,
            "baseline_unknown_seconds": 0.0,
            "recovered_words": 0,
            "recovered_seconds": 0.0,
            "remaining_unknown_words": 0,
            "remaining_unknown_seconds": 0.0,
            "unknown_words_reduction_ratio": 0.0,
            "unknown_seconds_reduction_ratio": 0.0,
            "attributable_remote_speech_ratio": 0.0,
            "published_speakers": 0,
            "internal_change_utterances": 0,
        },
        "gates": {"publish_session_evidence": False},
        "safety": {"fallback": "remote_speaker_diarization_v2", "plain_transcript_unchanged": True},
        "unknown_causes": [],
    }
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(
        out_dir / "artifact_manifest.json",
        {
            "schema": MANIFEST_SCHEMA,
            "session_id": session.name,
            "artifacts": {
                "report.json": sha256(out_dir / "report.json"),
                "report.md": sha256(out_dir / "report.md"),
            },
        },
    )
    print(f"remote_coverage_v3: FALLBACK_V2 ({reason})")
    return 0


def verify_existing(session: Path, out_dir: Path, require_promoted: bool) -> int:
    try:
        report = read_json(out_dir / "report.json")
        manifest = read_json(out_dir / "artifact_manifest.json")
        valid = (
            report.get("schema") == REPORT_SCHEMA
            and report.get("decision") == "PUBLISH_EVIDENCE"
            and manifest.get("schema") == MANIFEST_SCHEMA
            and all(
                (out_dir / name).is_file() and sha256(out_dir / name) == digest
                for name, digest in (manifest.get("artifacts") or {}).items()
            )
        )
        for row in (report.get("source") or {}).get("v2_artifacts", {}).values():
            path = resolve_source_path(session, row)
            valid = valid and path is not None and same_fingerprint(row, path)
        for key in ("dialogue", "remote_audio", "raw_remote_json", "v1_report", "v1_attribution"):
            row = (report.get("source") or {}).get(key)
            path = resolve_source_path(session, row)
            valid = valid and path is not None and same_fingerprint(row, path)
        if require_promoted:
            valid = valid and verify_v3_promotion(report)
    except (CoverageError, KeyError, OSError, ValueError, json.JSONDecodeError):
        valid = False
    print(f"remote_coverage_v3: verify={'ok' if valid else 'stale_or_invalid'}")
    return 0 if valid else 2


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    input_dir = args.input_dir if args.input_dir.is_absolute() else session / args.input_dir
    out_dir = args.out_dir if args.out_dir.is_absolute() else session / args.out_dir
    if args.verify_only:
        return verify_existing(session, out_dir, args.require_promoted)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = input_paths(input_dir)
    source = {"session_id": session.name, "v2_artifacts": source_fingerprints(paths, session)}
    try:
        v2_report = validate_v2_inputs(session, paths)
        source.update(deepcopy(v2_report.get("source") or {}))
        source["session_id"] = session.name
        frames = read_jsonl(paths["frames"])
        words = read_jsonl(paths["words"])
        v2_utterances = read_jsonl(paths["utterances"])
        speaker_map = read_json(paths["speaker_map"])
        rich = read_json(paths["rich"])
        if any(row.get("schema") != V2_FRAME_SCHEMA for row in frames):
            raise CoverageError("v2_frame_schema_invalid")
        if any(row.get("schema") != V2_WORD_SCHEMA for row in words):
            raise CoverageError("v2_word_schema_invalid")
        if any(row.get("schema") != V2_UTTERANCE_SCHEMA for row in v2_utterances):
            raise CoverageError("v2_utterance_schema_invalid")
        if speaker_map.get("schema") != V2_MAP_SCHEMA or rich.get("schema") != V2_RICH_SCHEMA:
            raise CoverageError("v2_rich_schema_invalid")
    except (CoverageError, KeyError, OSError, ValueError, json.JSONDecodeError) as error:
        return fallback(session, out_dir, str(error), source)

    speakers = {
        str(row["speaker_id"])
        for row in speaker_map.get("speakers") or []
        if row.get("speaker_id") and int(row.get("seed_units") or 0) > 0
    }
    if not speakers:
        return fallback(session, out_dir, "seeded_speaker_map_empty", source)

    frames_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for frame in frames:
        frames_by_utterance[str(frame["utterance_id"])].append(frame)
    words_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    decisions: list[dict[str, Any]] = []
    causes: dict[str, dict[str, float | int]] = defaultdict(lambda: {"words": 0, "seconds": 0.0})
    recovered_words = 0
    recovered_seconds = 0.0
    baseline_unknown_words = 0
    baseline_unknown_seconds = 0.0
    baseline_assignments = {str(row["word_id"]): row.get("speaker_id") for row in words}
    baseline_words = {str(row["word_id"]): row for row in words}

    for baseline in words:
        word = deepcopy(baseline)
        word["schema"] = WORD_SCHEMA
        uid = str(word["utterance_id"])
        weight = float(word.get("coverage_weight_sec") or 0)
        if baseline.get("speaker_id"):
            word["v2_reason"] = baseline.get("reason")
            word["reason"] = "preserved_v2_attribution"
            words_by_utterance[uid].append(word)
            continue

        baseline_unknown_words += 1
        baseline_unknown_seconds += weight
        midpoint = (float(word["start"]) + float(word["end"])) / 2
        related = [
            frame
            for frame in frames_by_utterance.get(uid, [])
            if float(frame["start"]) <= midpoint <= float(frame["end"])
        ]
        evidence = [
            {"frame_id": frame["frame_id"], **frame_candidate(frame, speakers, args.min_similarity, args.min_margin)}
            for frame in related
        ]
        supporting = [row for row in evidence if row.get("speaker_id")]
        supported_speakers = {str(row["speaker_id"]) for row in supporting}
        relaxed = [row for row in supporting if row.get("source") == "bounded_relaxed_seed_frame"]

        if baseline.get("reason") == "possible_remote_overlap":
            cause = "protected_remote_overlap"
        elif len(supported_speakers) > 1:
            cause = "conflicting_frame_speakers"
        elif len(supported_speakers) == 1 and relaxed:
            cause = "recovered_bounded_seed_consensus"
        elif not related:
            cause = "no_related_frame"
        elif all(row.get("source") == "embedding_unavailable" for row in evidence):
            cause = "embedding_unavailable"
        elif any(
            row.get("similarity") is not None
            and float(row["similarity"]) >= args.min_similarity
            and float(row.get("margin") or 0) < args.min_margin
            for row in evidence
        ):
            cause = "margin_below_threshold"
        elif any(row.get("similarity") is not None for row in evidence):
            cause = "similarity_below_threshold"
        else:
            cause = "insufficient_frame_evidence"

        recovered = cause == "recovered_bounded_seed_consensus"
        if recovered:
            chosen = next(iter(supported_speakers))
            similarities = [float(row["similarity"]) for row in relaxed]
            margins = [float(row["margin"]) for row in relaxed]
            word.update(
                {
                    "speaker_id": chosen,
                    "speaker_label": chosen,
                    "status": "attributed",
                    "reason": "bounded_relaxed_seed_frame_consensus",
                    "v2_reason": baseline.get("reason"),
                    "confidence": {
                        "similarity": round(min(similarities), 6),
                        "margin": round(min(margins), 6),
                    },
                    "frame_ids": [str(row["frame_id"]) for row in supporting],
                }
            )
            recovered_words += 1
            recovered_seconds += weight
        else:
            word["v2_reason"] = baseline.get("reason")
            word["v3_reason"] = cause

        causes[cause]["words"] = int(causes[cause]["words"]) + 1
        causes[cause]["seconds"] = float(causes[cause]["seconds"]) + weight
        decisions.append(
            {
                "schema": DECISION_SCHEMA,
                "word_id": str(word["word_id"]),
                "utterance_id": uid,
                "start": word["start"],
                "end": word["end"],
                "coverage_weight_sec": weight,
                "outcome": "attributed" if recovered else "unknown",
                "speaker_id": word.get("speaker_id"),
                "cause": cause,
                "v2_reason": baseline.get("reason"),
                "evidence": evidence,
            }
        )
        words_by_utterance[uid].append(word)

    utterances = deepcopy(rich.get("utterances") or [])
    attributions: list[dict[str, Any]] = []
    internal_changes = 0
    speaker_weights: Counter[str] = Counter()
    for utterance in utterances:
        if utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        uid = str(utterance["id"])
        utterance_words = words_by_utterance.get(uid, [])
        turns = build_turns(str(utterance.get("text") or ""), utterance_words)
        utterance["speaker_turns"] = turns
        distinct = list(dict.fromkeys(str(row["speaker_id"]) for row in turns if row.get("speaker_id")))
        if len(distinct) > 1:
            internal_changes += 1
        weights: Counter[str] = Counter()
        for word in utterance_words:
            if word.get("speaker_id"):
                weight = float(word.get("coverage_weight_sec") or 0)
                weights[str(word["speaker_id"])] += weight
                speaker_weights[str(word["speaker_id"])] += weight
        total = sum(float(row.get("coverage_weight_sec") or 0) for row in utterance_words)
        attributed = sum(weights.values())
        dominant = weights.most_common(1)[0][0] if len(weights) == 1 else None
        if dominant and total and attributed / total < 0.80:
            dominant = None
            status = "partial"
        elif len(weights) > 1:
            status = "mixed"
        elif dominant:
            status = "attributed"
        else:
            status = "aggregate"
        attributions.append(
            {
                "schema": UTTERANCE_SCHEMA,
                "utterance_id": uid,
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or 0),
                "speaker_id": dominant,
                "speaker_label": dominant or "Colleagues",
                "status": status,
                "reason": "word_level_v3_evidence" if dominant else "insufficient_word_level_evidence",
                "speaker_turns": turns,
                "attributed_weight_sec": round(attributed, 9),
                "total_weight_sec": round(total, 9),
            }
        )

    all_words = [word for utterance in utterances if utterance.get("role") == "remote" for word in words_by_utterance.get(str(utterance.get("id")), [])]
    selected_text_unchanged = all(
        "".join(str(turn.get("text") or "") for turn in utterance.get("speaker_turns") or [])
        == str(utterance.get("text") or "")
        for utterance in utterances
        if utterance.get("role") == "remote"
    )
    baseline_attributions_preserved = all(
        baseline_assignments[str(word["word_id"])] in {None, word.get("speaker_id")}
        for word in all_words
    )
    timestamps_unchanged = all(
        float(word["start"]) == float(baseline_words[str(word["word_id"])]["start"])
        and float(word["end"]) == float(baseline_words[str(word["word_id"])]["end"])
        for word in all_words
    )
    overlap_preserved = all(
        word.get("speaker_id") is None
        for word in all_words
        if word.get("v2_reason") == "possible_remote_overlap"
    )
    existing_speakers_only = all(
        word.get("speaker_id") is None or str(word["speaker_id"]) in speakers for word in all_words
    )
    remote_speech = float(v2_report["summary"]["remote_speech_sec"])
    baseline_attributed = float(v2_report["summary"]["attributed_speech_sec"])
    remaining_words = baseline_unknown_words - recovered_words
    remaining_seconds = max(0.0, baseline_unknown_seconds - recovered_seconds)
    unknown_causes = [
        {
            "cause": cause,
            "words": int(values["words"]),
            "seconds": round(float(values["seconds"]), 6),
        }
        for cause, values in sorted(causes.items())
    ]
    source["profile"] = str((v2_report.get("source") or {}).get("profile") or "auto")
    gates = {
        "v2_inputs_current": True,
        "v2_policy_promoted": True,
        "seeded_speakers_only": existing_speakers_only,
        "baseline_attributions_preserved": baseline_attributions_preserved,
        "selected_text_unchanged": selected_text_unchanged,
        "word_timestamps_unchanged": timestamps_unchanged,
        "word_conservation": selected_text_unchanged,
        "timestamp_order": timestamps_unchanged,
        "remote_overlap_preserved": overlap_preserved,
        "me_unchanged": True,
    }
    gates["publish_session_evidence"] = all(gates.values())
    summary = {
        "remote_utterances": len(attributions),
        "remote_words": len(all_words),
        "baseline_attributed_words": len(all_words) - baseline_unknown_words,
        "attributed_words": len(all_words) - remaining_words,
        "baseline_unknown_words": baseline_unknown_words,
        "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
        "recovered_words": recovered_words,
        "recovered_seconds": round(recovered_seconds, 6),
        "remaining_unknown_words": remaining_words,
        "remaining_unknown_seconds": round(remaining_seconds, 6),
        "unknown_words_reduction_ratio": round(recovered_words / baseline_unknown_words, 6) if baseline_unknown_words else 0.0,
        "unknown_seconds_reduction_ratio": round(recovered_seconds / baseline_unknown_seconds, 6) if baseline_unknown_seconds else 0.0,
        "remote_speech_sec": round(remote_speech, 6),
        "attributed_speech_sec": round(baseline_attributed + recovered_seconds, 6),
        "attributable_remote_speech_ratio": round((baseline_attributed + recovered_seconds) / remote_speech, 6) if remote_speech else 0.0,
        "published_speakers": len(speakers),
        "internal_change_utterances": internal_changes,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "status": "completed" if gates["publish_session_evidence"] else "fallback",
        "decision": "PUBLISH_EVIDENCE" if gates["publish_session_evidence"] else "FALLBACK_V2",
        "reasons": [key for key, value in gates.items() if not value],
        "source": source,
        "implementation": implementation(),
        "parameters": {
            "profile": "resemblyzer_seeded_frame_recovery_v3",
            "min_similarity": args.min_similarity,
            "min_margin": args.min_margin,
            "requires_unanimous_supporting_frames": True,
            "requires_relaxed_frame": True,
            "protected_v2_reasons": ["possible_remote_overlap"],
        },
        "summary": summary,
        "gates": gates,
        "safety": {
            "plain_transcript_unchanged": True,
            "selected_text_unchanged": selected_text_unchanged,
            "me_unchanged": True,
            "existing_v2_labels_unchanged": baseline_attributions_preserved,
            "session_local_anonymous_only": True,
            "identity_inference": False,
            "external_writes": False,
            "raw_audio_unchanged": bool((v2_report.get("safety") or {}).get("raw_audio_unchanged")),
            "fallback": "remote_speaker_diarization_v2",
        },
        "unknown_causes": unknown_causes,
    }
    if report["decision"] != "PUBLISH_EVIDENCE":
        return fallback(session, out_dir, report["reasons"][0], source)

    v3_speakers = []
    baseline_speakers = {str(row["speaker_id"]): row for row in speaker_map.get("speakers") or []}
    for speaker in sorted(speakers):
        row = deepcopy(baseline_speakers[speaker])
        row["attributed_speech_sec"] = round(float(speaker_weights[speaker]), 6)
        v3_speakers.append(row)
    write_jsonl(out_dir / "recovery_decisions.jsonl", decisions)
    write_json(
        out_dir / "unknown_cause_map.json",
        {
            "schema": CAUSE_MAP_SCHEMA,
            "session_id": session.name,
            "baseline_unknown_words": baseline_unknown_words,
            "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
            "causes": unknown_causes,
        },
    )
    write_jsonl(out_dir / "word_attribution.jsonl", all_words)
    write_jsonl(out_dir / "utterance_attribution.jsonl", attributions)
    write_json(
        out_dir / "speaker_map.json",
        {
            "schema": MAP_SCHEMA,
            "session_id": session.name,
            "selected_profile": source["profile"],
            "decision": report["decision"],
            "speakers": v3_speakers,
        },
    )
    write_json(
        out_dir / "transcript.rich.shadow.json",
        {
            "schema": RICH_SCHEMA,
            "session_id": session.name,
            "selected_profile": source["profile"],
            "decision": report["decision"],
            "source": source,
            "utterances": utterances,
            "remote_speaker_attributions": attributions,
            "remote_word_attributions": all_words,
            "speaker_map": v3_speakers,
            "safety": report["safety"],
        },
    )
    (out_dir / "transcript.rich.shadow.md").write_text(
        transcript_markdown(utterances, source["profile"]), encoding="utf-8"
    )
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(out_dir / "artifact_manifest.json", build_output_manifest(out_dir, session.name))
    print(
        f"remote_coverage_v3: decision={report['decision']} recovered={recovered_words}w/"
        f"{recovered_seconds:.3f}s remaining={remaining_words}w/{remaining_seconds:.3f}s"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
