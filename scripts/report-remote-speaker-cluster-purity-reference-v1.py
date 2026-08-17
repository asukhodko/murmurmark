#!/usr/bin/env python3
"""Compare session-local remote speaker clusters with a private external reference."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import sys
import unicodedata
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-cluster-purity-reference-v1"
DEFAULT_POLICY = ROOT / "policies/remote-speaker-cluster-purity-reference-v1.json"
REGISTRY_SCHEMA = "murmurmark.remote_speaker_cluster_purity_registry/v1"
PRIVATE_EVALUATION_SCHEMA = "murmurmark.remote_speaker_cluster_purity_private_evaluation/v1"
SESSION_SUMMARY_SCHEMA = "murmurmark.remote_speaker_cluster_purity_session_summary/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_cluster_purity_reference_report/v1"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_cluster_purity_reference_manifest/v1"
POLICY_SCHEMA = "murmurmark.remote_speaker_cluster_purity_reference_policy/v1"
RANGE_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)$"
)
WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)


class PurityError(RuntimeError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise PurityError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as error:
        raise PurityError(f"input is outside session: {path}") from error
    return {"path": relative, "bytes": path.stat().st_size, "sha256": sha256(path)}


def resolve_session(raw: str, sessions_root: Path) -> Path:
    candidate = Path(raw).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    candidate = sessions_root / raw
    if candidate.is_dir():
        return candidate.resolve()
    candidate = sessions_root / Path(raw).name
    if candidate.is_dir():
        return candidate.resolve()
    raise PurityError(f"session not found: {raw}")


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(WORD_RE.findall(value))


def words(text: str) -> list[str]:
    value = normalize(text)
    return value.split() if value else []


def clock_seconds(value: str) -> float:
    parts = [int(part) for part in value.split(":" )]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    raise PurityError(f"invalid timestamp: {value}")


def parse_range_blocks(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = RANGE_RE.match(lines[index].strip())
        if not match:
            index += 1
            continue
        start = clock_seconds(match.group("start"))
        end = max(start + 0.01, clock_seconds(match.group("end")))
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        speaker = lines[index].strip() if index < len(lines) else ""
        index += 1
        content: list[str] = []
        while index < len(lines) and not RANGE_RE.match(lines[index].strip()):
            if lines[index].strip() and lines[index].strip() != "---":
                content.append(lines[index].strip())
            index += 1
        text_value = " ".join(content).strip()
        if speaker and text_value:
            rows.append({"start": start, "end": end, "speaker": speaker, "text": text_value})
    if not rows:
        raise PurityError("external transcript contains no timestamped range blocks")
    return rows


def load_policy(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if payload.get("schema") != POLICY_SCHEMA:
        raise PurityError(f"unsupported policy schema: {payload.get('schema')}")
    return payload


def identity_path(row: dict[str, Any], session: Path) -> Path:
    raw = row.get("path")
    expected = row.get("sha256")
    if not isinstance(raw, str) or raw.startswith("/") or ".." in Path(raw).parts:
        raise PurityError("selection contains unsafe artifact path")
    path = (session / raw).resolve()
    if not path.is_file() or not str(path).startswith(str(session.resolve()) + "/"):
        raise PurityError(f"selected artifact missing: {raw}")
    if row.get("bytes") != path.stat().st_size or expected != sha256(path):
        raise PurityError(f"selected artifact changed: {raw}")
    return path


def selected_inputs(session: Path) -> dict[str, Any]:
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    selection = read_json(selection_path)
    if selection.get("schema") != "murmurmark.speaker_resolved_transcript_selection/v1":
        raise PurityError("speaker selection schema is unsupported")
    if selection.get("state") != "selected":
        raise PurityError(f"speaker selection is not selected: {selection.get('state')}")
    rich_path = identity_path(selection.get("rich_transcript") or {}, session)
    coverage_path = identity_path(selection.get("coverage_report") or {}, session)
    dialogue_path = identity_path(selection.get("selected_dialogue") or {}, session)
    aggregate_path = identity_path(selection.get("aggregate_transcript") or {}, session)
    rich = read_json(rich_path)
    if rich.get("schema") != "murmurmark.remote_speaker_rich_transcript/v3":
        raise PurityError("selected rich transcript schema is unsupported")
    return {
        "selection": selection,
        "selection_path": selection_path,
        "rich": rich,
        "rich_path": rich_path,
        "coverage_path": coverage_path,
        "dialogue_path": dialogue_path,
        "aggregate_path": aggregate_path,
        "profile": selection.get("selected_profile"),
        "speaker_profile": selection.get("selected_speaker_profile"),
    }


def utterance_text_rows(rich: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for utterance in rich.get("utterances") or []:
        text = str(utterance.get("text") or "").strip()
        if not text:
            continue
        rows.append(
            {
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or utterance.get("start") or 0),
                "role": utterance.get("role"),
                "text": text,
            }
        )
    return rows


def remote_turns(rich: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for utterance in rich.get("utterances") or []:
        if utterance.get("role") != "remote":
            continue
        turns = utterance.get("speaker_turns") or []
        if not turns:
            rows.append(
                {
                    "start": float(utterance.get("start") or 0),
                    "end": float(utterance.get("end") or utterance.get("start") or 0),
                    "speaker_id": "unknown",
                    "status": "unknown",
                    "text": str(utterance.get("text") or ""),
                    "utterance_id": utterance.get("id"),
                }
            )
            continue
        for turn in turns:
            speaker = str(turn.get("speaker_id") or "unknown")
            status = str(turn.get("status") or "unknown")
            if status != "attributed":
                speaker = "unknown"
            rows.append(
                {
                    "start": float(turn.get("start") or utterance.get("start") or 0),
                    "end": float(turn.get("end") or utterance.get("end") or 0),
                    "speaker_id": speaker,
                    "status": status,
                    "text": str(turn.get("text") or utterance.get("text") or ""),
                    "utterance_id": utterance.get("id"),
                }
            )
    return rows


def lexical_similarity(left: str, right: str) -> tuple[float, int]:
    left_words = words(left)
    right_words = words(right)
    if not left_words or not right_words:
        return 0.0, 0
    left_counts = Counter(left_words)
    right_counts = Counter(right_words)
    shared = sum((left_counts & right_counts).values())
    containment = shared / min(len(left_words), len(right_words))
    sequence = SequenceMatcher(None, " ".join(left_words), " ".join(right_words), autojunk=False).ratio()
    return max(containment, sequence), shared


def weighted_median(rows: list[tuple[float, float]]) -> float:
    ordered = sorted(rows)
    threshold = sum(weight for _, weight in ordered) / 2
    total = 0.0
    for value, weight in ordered:
        total += weight
        if total >= threshold:
            return value
    return ordered[-1][0]


def estimate_offset(
    reference: list[dict[str, Any]],
    local: list[dict[str, Any]],
    policy: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    matches: list[tuple[float, float, float]] = []
    minimum_words = int(policy["alignment"]["anchor_min_words"])
    minimum_score = float(policy["alignment"]["anchor_min_score"])
    token_rows: dict[str, set[int]] = defaultdict(set)
    for index, candidate in enumerate(local):
        for token in set(words(candidate["text"])):
            token_rows[token].add(index)
    for row in reference:
        row_words = words(row["text"])
        if len(row_words) < minimum_words:
            continue
        indexed_tokens = [token for token in set(row_words) if token in token_rows]
        indexed_tokens.sort(key=lambda token: (len(token_rows[token]), token))
        candidate_indices: set[int] = set()
        for token in indexed_tokens[:3]:
            candidate_indices.update(token_rows[token])
        best: tuple[float, int, dict[str, Any]] | None = None
        for candidate_index in sorted(candidate_indices):
            candidate = local[candidate_index]
            score, shared = lexical_similarity(row["text"], candidate["text"])
            if shared < minimum_words:
                continue
            value = (score, shared, candidate)
            if best is None or (score, shared) > (best[0], best[1]):
                best = value
        if best is None or best[0] < minimum_score:
            continue
        candidate = best[2]
        reference_mid = (row["start"] + row["end"]) / 2
        local_mid = (candidate["start"] + candidate["end"]) / 2
        matches.append((local_mid - reference_mid, best[0], float(best[1])))
    if not matches:
        raise PurityError("cannot estimate external transcript offset")
    strongest = sorted(matches, key=lambda item: (item[1], item[2]), reverse=True)[:160]
    initial = weighted_median([(offset, score * shared) for offset, score, shared in strongest])
    tolerance = float(policy["alignment"]["offset_inlier_sec"])
    inliers = [row for row in strongest if abs(row[0] - initial) <= tolerance]
    minimum_anchors = int(policy["alignment"]["minimum_offset_anchors"])
    if len(inliers) < minimum_anchors:
        raise PurityError(
            f"external transcript offset is weak: {len(inliers)} anchors, need {minimum_anchors}"
        )
    offset = weighted_median([(value, score * shared) for value, score, shared in inliers])
    deviations = [abs(value - offset) for value, _, _ in inliers]
    return round(offset, 3), {
        "anchors": len(inliers),
        "candidate_anchors": len(matches),
        "median_absolute_deviation_sec": round(statistics.median(deviations), 3),
        "method": "weighted_lexical_anchor_median",
    }


def temporal_similarity(ref_start: float, ref_end: float, turn: dict[str, Any]) -> float:
    overlap = max(0.0, min(ref_end, turn["end"]) - max(ref_start, turn["start"]))
    if overlap > 0:
        return min(1.0, overlap / max(0.01, ref_end - ref_start))
    gap = min(abs(ref_start - turn["end"]), abs(turn["start"] - ref_end))
    return max(0.0, 1.0 - gap / 12.0)


def align_reference(
    reference: list[dict[str, Any]],
    turns: list[dict[str, Any]],
    offset: float,
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    tolerance = float(policy["alignment"]["candidate_tolerance_sec"])
    minimum_score = float(policy["alignment"]["row_min_score"])
    aligned: list[dict[str, Any]] = []
    for index, row in enumerate(reference, 1):
        shifted_start = row["start"] + offset
        shifted_end = row["end"] + offset
        candidates = [
            turn
            for turn in turns
            if turn["end"] >= shifted_start - tolerance and turn["start"] <= shifted_end + tolerance
        ]
        best: tuple[float, float, float, int, dict[str, Any]] | None = None
        for turn in candidates:
            lexical, shared = lexical_similarity(row["text"], turn["text"])
            temporal = temporal_similarity(shifted_start, shifted_end, turn)
            score = 0.75 * lexical + 0.25 * temporal
            candidate = (score, lexical, temporal, shared, turn)
            if best is None or candidate[:4] > best[:4]:
                best = candidate
        token_count = len(words(row["text"]))
        matched = bool(
            best
            and best[0] >= minimum_score
            and best[3] >= (1 if token_count <= 3 else 2)
        )
        turn = best[4] if matched and best else None
        aligned.append(
            {
                "row_id": f"reference_{index:04d}",
                "reference_start": row["start"],
                "reference_end": row["end"],
                "reference_speaker": row["speaker"],
                "reference_text": row["text"],
                "reference_words": token_count,
                "role": row["role"],
                "shifted_start": round(shifted_start, 3),
                "shifted_end": round(shifted_end, 3),
                "matched": matched,
                "predicted_speaker_id": turn["speaker_id"] if turn else "unmatched",
                "predicted_status": turn["status"] if turn else "unmatched",
                "utterance_id": turn["utterance_id"] if turn else None,
                "local_start": round(turn["start"], 3) if turn else None,
                "local_end": round(turn["end"], 3) if turn else None,
                "local_text": turn["text"] if turn else None,
                "score": round(best[0], 6) if best else 0.0,
                "lexical_score": round(best[1], 6) if best else 0.0,
                "temporal_score": round(best[2], 6) if best else 0.0,
                "shared_words": best[3] if best else 0,
            }
        )
    return aligned


def evaluate_metrics(
    aligned: list[dict[str, Any]],
    published_clusters: int,
    policy: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    remote_rows = [row for row in aligned if row["role"] == "remote"]
    reference_words = sum(row["reference_words"] for row in remote_rows)
    matched = [row for row in remote_rows if row["matched"]]
    matched_words = sum(row["reference_words"] for row in matched)
    attributed = [row for row in matched if row["predicted_speaker_id"].startswith("remote_speaker_")]
    attributed_words = sum(row["reference_words"] for row in attributed)
    unknown_words = sum(
        row["reference_words"] for row in matched if row["predicted_speaker_id"] == "unknown"
    )
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    speaker_totals: Counter[str] = Counter()
    for row in matched:
        speaker = row["reference_speaker"]
        count = row["reference_words"]
        confusion[speaker][row["predicted_speaker_id"]] += count
        speaker_totals[speaker] += count
    reference_all_totals: Counter[str] = Counter()
    for row in remote_rows:
        reference_all_totals[row["reference_speaker"]] += row["reference_words"]

    cluster_totals: Counter[str] = Counter()
    cluster_reference: dict[str, Counter[str]] = defaultdict(Counter)
    for row in attributed:
        cluster = row["predicted_speaker_id"]
        count = row["reference_words"]
        cluster_totals[cluster] += count
        cluster_reference[cluster][row["reference_speaker"]] += count
    dominant_words = sum(max(values.values()) for values in cluster_reference.values() if values)
    weighted_purity = dominant_words / attributed_words if attributed_words else 0.0

    min_speaker_words = int(policy["purity"]["minimum_speaker_words"])
    speaker_rows: list[dict[str, Any]] = []
    dominant_clusters: dict[str, list[str]] = defaultdict(list)
    for speaker in sorted(reference_all_totals):
        predicted = confusion.get(speaker, Counter())
        cluster_only = Counter({key: value for key, value in predicted.items() if key.startswith("remote_speaker_")})
        dominant_cluster, dominant = cluster_only.most_common(1)[0] if cluster_only else (None, 0)
        matched_total = speaker_totals.get(speaker, 0)
        if dominant_cluster and matched_total >= min_speaker_words:
            dominant_clusters[dominant_cluster].append(speaker)
        speaker_rows.append(
            {
                "speaker": speaker,
                "reference_words": reference_all_totals[speaker],
                "matched_words": matched_total,
                "dominant_cluster": dominant_cluster,
                "dominant_cluster_words": dominant,
                "dominant_cluster_recall": round(dominant / reference_all_totals[speaker], 6)
                if reference_all_totals[speaker]
                else 0.0,
                "cluster_distribution": dict(sorted(predicted.items())),
            }
        )
    collisions = {
        cluster: speakers
        for cluster, speakers in sorted(dominant_clusters.items())
        if len(speakers) > 1
    }
    split_threshold = float(policy["purity"]["split_min_speaker_share"])
    split_speakers = 0
    for speaker, predicted in confusion.items():
        total = speaker_totals[speaker]
        substantial = [
            count
            for cluster, count in predicted.items()
            if cluster.startswith("remote_speaker_")
            and count >= min_speaker_words
            and count / max(1, total) >= split_threshold
        ]
        if len(substantial) > 1:
            split_speakers += 1

    minority_share = float(policy["purity"]["minority_max_reference_share"])
    minority = {
        speaker
        for speaker, count in reference_all_totals.items()
        if count / max(1, reference_words) <= minority_share
    }
    cluster_dominant = {
        cluster: counts.most_common(1)[0][0]
        for cluster, counts in cluster_reference.items()
        if counts
    }
    minority_words = sum(reference_all_totals[speaker] for speaker in minority)
    minority_separated_words = sum(
        row["reference_words"]
        for row in attributed
        if row["reference_speaker"] in minority
        and cluster_dominant.get(row["predicted_speaker_id"]) == row["reference_speaker"]
    )
    alignment_ratio = matched_words / reference_words if reference_words else 0.0
    minority_recall = minority_separated_words / minority_words if minority_words else None
    route: str
    if alignment_ratio < float(policy["decision"]["minimum_alignment_ratio"]):
        route = "EVIDENCE_BOUND"
    elif collisions or len(reference_all_totals) > published_clusters:
        route = "ADVANCE_SEGMENTATION"
    elif weighted_purity < float(policy["decision"]["minimum_diagnostic_purity"]):
        route = "ADVANCE_USABILITY_GATE"
    else:
        route = "EVIDENCE_BOUND"
    metrics = {
        "reference_remote_speakers": len(reference_all_totals),
        "published_clusters": published_clusters,
        "reference_remote_words": reference_words,
        "aligned_reference_words": matched_words,
        "alignment_ratio": round(alignment_ratio, 6),
        "attributed_reference_words": attributed_words,
        "explicit_unknown_reference_words": unknown_words,
        "dominant_cluster_weighted_purity": round(weighted_purity, 6),
        "dominant_cluster_collisions": len(collisions),
        "merged_reference_speakers": sum(len(value) for value in collisions.values()),
        "split_reference_speakers": split_speakers,
        "minority_reference_speakers": len(minority),
        "minority_reference_words": minority_words,
        "minority_separated_words": minority_separated_words,
        "minority_speaker_recall": round(minority_recall, 6) if minority_recall is not None else None,
    }
    private = {
        "speaker_metrics": speaker_rows,
        "cluster_confusion": {
            cluster: dict(sorted(counts.items())) for cluster, counts in sorted(cluster_reference.items())
        },
        "dominant_cluster_collisions": collisions,
        "minority_speakers": sorted(minority),
        "recommended_route": route,
    }
    return metrics, private


def import_source(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    if not source.is_file():
        raise PurityError(f"source not found: {source}")
    session = resolve_session(args.session, args.sessions_root)
    selected = selected_inputs(session)
    text = source.read_text(encoding="utf-8-sig")
    entries = parse_range_blocks(text)
    local_speakers = set(args.local_speaker or [])
    for row in entries:
        row["role"] = "me" if row["speaker"] in local_speakers else "remote"

    private = args.out_dir / "private"
    source_dir = private / "sources" / args.source_id
    registry_path = private / "registry.json"
    registry = read_json(registry_path) if registry_path.is_file() else {"schema": REGISTRY_SCHEMA, "sources": []}
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise PurityError("private registry schema is unsupported")
    existing = next((row for row in registry["sources"] if row.get("source_id") == args.source_id), None)
    if existing and not args.replace:
        raise PurityError(f"source id already exists: {args.source_id}; pass --replace to update it")
    source_dir.mkdir(parents=True, exist_ok=True)
    copied = source_dir / "source.txt"
    shutil.copyfile(source, copied)
    parsed_path = source_dir / "parsed.json"
    write_json(parsed_path, {"format": "range_blocks", "entries": entries})
    row = {
        "source_id": args.source_id,
        "session": session.relative_to(args.sessions_root).as_posix(),
        "trust_grade": args.trust_grade,
        "local_speakers": sorted(local_speakers),
        "offset_sec": args.offset_sec,
        "source": {"bytes": copied.stat().st_size, "sha256": sha256(copied)},
        "parsed": {"bytes": parsed_path.stat().st_size, "sha256": sha256(parsed_path)},
        "selected_profile_at_import": selected["profile"],
        "selected_rich_sha256_at_import": sha256(selected["rich_path"]),
    }
    rows = [item for item in registry["sources"] if item.get("source_id") != args.source_id]
    rows.append(row)
    registry["sources"] = sorted(rows, key=lambda item: item["source_id"])
    write_json(registry_path, registry)
    print(f"imported: {args.source_id}")
    print(f"entries: {len(entries)}")
    print(f"source_sha256: {row['source']['sha256']}")
    print("next: murmurmark corpus remote-cluster-purity-v1 evaluate")
    return 0


def source_inputs(row: dict[str, Any], out_dir: Path, sessions_root: Path) -> tuple[Path, list[dict[str, Any]]]:
    source_dir = out_dir / "private/sources" / row["source_id"]
    source_path = source_dir / "source.txt"
    parsed_path = source_dir / "parsed.json"
    for key, path in (("source", source_path), ("parsed", parsed_path)):
        expected = row.get(key) or {}
        if not path.is_file() or path.stat().st_size != expected.get("bytes") or sha256(path) != expected.get("sha256"):
            raise PurityError(f"private {key} missing or changed for {row['source_id']}")
    session = resolve_session(row["session"], sessions_root)
    entries = read_json(parsed_path).get("entries") or []
    return session, entries


def compute(args: argparse.Namespace, policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[Path, bytes]]:
    registry_path = args.out_dir / "private/registry.json"
    if not registry_path.is_file():
        raise PurityError("private registry is missing; import a reference first")
    registry = read_json(registry_path)
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise PurityError("private registry schema is unsupported")
    private_outputs: dict[Path, bytes] = {}
    public_evidence: list[dict[str, Any]] = []
    total_reference_words = 0
    total_aligned_words = 0
    total_attributed_words = 0
    total_dominant_words = 0.0
    total_minority_words = 0
    total_minority_separated = 0
    routes: list[str] = []
    for ordinal, row in enumerate(registry.get("sources") or [], 1):
        session, reference = source_inputs(row, args.out_dir, args.sessions_root)
        selected = selected_inputs(session)
        local_rows = utterance_text_rows(selected["rich"])
        turns = remote_turns(selected["rich"])
        if row.get("offset_sec") is None:
            offset, offset_evidence = estimate_offset(reference, local_rows, policy)
        else:
            offset = float(row["offset_sec"])
            offset_evidence = {"anchors": None, "candidate_anchors": None, "method": "operator_override"}
        aligned = align_reference(reference, turns, offset, policy)
        coverage = read_json(selected["coverage_path"])
        published = int((coverage.get("summary") or {}).get("published_speakers") or 0)
        metrics, private_metrics = evaluate_metrics(aligned, published, policy)
        route = private_metrics["recommended_route"]
        routes.append(route)
        source_id = row["source_id"]
        evaluation_dir = args.out_dir / "private/evaluations" / source_id
        item_bytes = b"".join(canonical_bytes(item) for item in aligned)
        private_outputs[evaluation_dir / "item_alignment.jsonl"] = item_bytes
        private_payload = {
            "schema": PRIVATE_EVALUATION_SCHEMA,
            "source_id": source_id,
            "session": row["session"],
            "trust_grade": row["trust_grade"],
            "offset_sec": offset,
            "offset_evidence": offset_evidence,
            "source": row["source"],
            "selected_inputs": {
                "profile": selected["profile"],
                "speaker_profile": selected["speaker_profile"],
                "selection": fingerprint(selected["selection_path"], session),
                "rich_transcript": fingerprint(selected["rich_path"], session),
                "coverage_report": fingerprint(selected["coverage_path"], session),
                "dialogue": fingerprint(selected["dialogue_path"], session),
                "aggregate_transcript": fingerprint(selected["aggregate_path"], session),
            },
            "metrics": metrics,
            "details": private_metrics,
            "safety": {
                "production_selection_changed": False,
                "selected_text_changed": False,
                "identity_claim_allowed": False,
            },
        }
        private_outputs[evaluation_dir / "evaluation.json"] = canonical_bytes(private_payload)
        generic_id = f"evidence_{ordinal:03d}"
        identity_safety = (
            "diagnostic_external_machine_reference"
            if row["trust_grade"] == "independent_machine"
            else "reference_supported_not_production_gated"
        )
        public_row = {
            "evidence_id": generic_id,
            "trust_grade": row["trust_grade"],
            "source_sha256": row["source"]["sha256"],
            "selected_rich_sha256": sha256(selected["rich_path"]),
            "metrics": metrics,
            "recommended_route": route,
            "identity_safety": identity_safety,
        }
        public_evidence.append(public_row)
        total_reference_words += metrics["reference_remote_words"]
        total_aligned_words += metrics["aligned_reference_words"]
        total_attributed_words += metrics["attributed_reference_words"]
        total_dominant_words += metrics["dominant_cluster_weighted_purity"] * metrics["attributed_reference_words"]
        total_minority_words += metrics["minority_reference_words"]
        total_minority_separated += metrics["minority_separated_words"]

        session_summary_path = session / "derived/audit/remote-speaker-cluster-purity-reference-v1/summary.json"
        aggregate_command = (
            f"murmurmark transcript sessions/{session.name} --aggregate --cat"
        )
        session_summary = {
            "schema": SESSION_SUMMARY_SCHEMA,
            "selected_profile": selected["profile"],
            "selected_speaker_profile": selected["speaker_profile"],
            "selected_rich_sha256": sha256(selected["rich_path"]),
            "cluster_scope": "session_local_acoustic_cluster",
            "identity_safety": identity_safety,
            "purity_evidence": "private_external_reference_diagnostic",
            "metrics": metrics,
            "recommended_route": route,
            "aggregate_fallback_command": aggregate_command,
        }
        private_outputs[session_summary_path] = canonical_bytes(session_summary)

    if not public_evidence:
        raise PurityError("private registry contains no sources")
    if "ADVANCE_SEGMENTATION" in routes:
        decision = "ADVANCE_SEGMENTATION"
    elif "ADVANCE_USABILITY_GATE" in routes:
        decision = "ADVANCE_USABILITY_GATE"
    else:
        decision = "EVIDENCE_BOUND"
    summary = {
        "evidence_sources": len(public_evidence),
        "reference_remote_words": total_reference_words,
        "aligned_reference_words": total_aligned_words,
        "alignment_ratio": round(total_aligned_words / total_reference_words, 6)
        if total_reference_words
        else 0.0,
        "attributed_reference_words": total_attributed_words,
        "dominant_cluster_weighted_purity": round(total_dominant_words / total_attributed_words, 6)
        if total_attributed_words
        else 0.0,
        "minority_reference_words": total_minority_words,
        "minority_speaker_recall": round(total_minority_separated / total_minority_words, 6)
        if total_minority_words
        else None,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "version": "0.1.0",
        "decision": decision,
        "summary": summary,
        "evidence": public_evidence,
        "gates": {
            "private_item_level_evidence_present": True,
            "public_report_contains_speaker_names": False,
            "production_selection_unchanged": True,
            "coverage_v3_thresholds_unchanged": True,
            "external_machine_reference_is_diagnostic_only": all(
                row["trust_grade"] == "independent_machine" for row in public_evidence
            ),
        },
        "evidence_limit": (
            "External machine transcripts expose disagreement and cluster collisions but do not "
            "establish human-reviewed identity correctness."
        ),
        "next_goal": {
            "route": decision,
            "title": (
                "Remote Speaker Boundary and Minority-Voice Segmentation v1"
                if decision == "ADVANCE_SEGMENTATION"
                else "Remote Speaker Usability Rejector v1"
                if decision == "ADVANCE_USABILITY_GATE"
                else "Await stronger independent speaker-purity evidence"
            ),
        },
        "privacy": {
            "speaker_names": "private_only",
            "reference_text": "private_only",
            "session_ids": "private_only",
            "absolute_paths": False,
        },
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "version": 1,
        "decision": decision,
        "policy_sha256": sha256(args.policy),
        "summary": summary,
        "evidence": [
            {
                "evidence_id": row["evidence_id"],
                "trust_grade": row["trust_grade"],
                "source_sha256": row["source_sha256"],
                "selected_rich_sha256": row["selected_rich_sha256"],
                "recommended_route": row["recommended_route"],
            }
            for row in public_evidence
        ],
    }
    return report, manifest, private_outputs


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Remote Speaker Cluster Purity Reference v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "## Aggregate",
        "",
        f"- Evidence sources: {summary['evidence_sources']}",
        f"- Alignment: {summary['alignment_ratio']:.2%}",
        f"- Dominant-cluster weighted purity: {summary['dominant_cluster_weighted_purity']:.2%}",
        "- Minority-speaker recall: "
        + (f"{summary['minority_speaker_recall']:.2%}" if summary["minority_speaker_recall"] is not None else "n/a"),
        "",
        "## Evidence",
        "",
        "| Evidence | Reference speakers | Published clusters | Collisions | Purity | Route |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in report["evidence"]:
        metrics = row["metrics"]
        lines.append(
            f"| `{row['evidence_id']}` | {metrics['reference_remote_speakers']} | "
            f"{metrics['published_clusters']} | {metrics['dominant_cluster_collisions']} | "
            f"{metrics['dominant_cluster_weighted_purity']:.2%} | `{row['recommended_route']}` |"
        )
    lines += [
        "",
        "## Limit",
        "",
        report["evidence_limit"],
        "",
        "Speaker names, reference text, session IDs and item-level alignments remain private.",
        "This report never changes transcript selection or production thresholds.",
        "",
    ]
    return "\n".join(lines)


def expected_outputs(args: argparse.Namespace, policy: dict[str, Any]) -> dict[Path, bytes]:
    report, manifest, private = compute(args, policy)
    outputs = {
        args.out_dir / "report.json": canonical_bytes(report),
        args.out_dir / "report.md": report_markdown(report).encode("utf-8"),
        args.out_dir / "reference_manifest.json": canonical_bytes(manifest),
        **private,
    }
    if args.write_manifest:
        outputs[args.write_manifest] = canonical_bytes(manifest)
    return outputs


def evaluate(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    outputs = expected_outputs(args, policy)
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    report = read_json(args.out_dir / "report.json")
    print(f"decision: {report['decision']}")
    print(f"evidence_sources: {report['summary']['evidence_sources']}")
    print(f"alignment_ratio: {report['summary']['alignment_ratio']}")
    print(f"dominant_cluster_weighted_purity: {report['summary']['dominant_cluster_weighted_purity']}")
    print(f"report: {args.out_dir / 'report.json'}")
    return 0


def replay(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    outputs = expected_outputs(args, policy)
    stale = [str(path) for path, content in outputs.items() if not path.is_file() or path.read_bytes() != content]
    if stale:
        print("stale outputs:", file=sys.stderr)
        for path in stale:
            print(f"  {path}", file=sys.stderr)
        return 2
    digest = hashlib.sha256()
    for path in sorted(outputs, key=lambda value: str(value)):
        digest.update(outputs[path])
    print(f"replay: byte-exact ({digest.hexdigest()})")
    return 0


def status(args: argparse.Namespace) -> int:
    report_path = args.out_dir / "report.json"
    if not report_path.is_file():
        print("status: missing")
        print("next: murmurmark corpus remote-cluster-purity-v1 import SESSION SOURCE --source-id ID")
        return 2
    report = read_json(report_path)
    print(f"decision: {report.get('decision')}")
    summary = report.get("summary") or {}
    print(f"evidence_sources: {summary.get('evidence_sources')}")
    print(f"alignment_ratio: {summary.get('alignment_ratio')}")
    print(f"dominant_cluster_weighted_purity: {summary.get('dominant_cluster_weighted_purity')}")
    print(f"minority_speaker_recall: {summary.get('minority_speaker_recall')}")
    print(f"next_goal: {(report.get('next_goal') or {}).get('title')}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Measure remote speaker cluster purity against a private external transcript."
    )
    subparsers = result.add_subparsers(dest="action", required=True)
    imported = subparsers.add_parser("import")
    imported.add_argument("session")
    imported.add_argument("source", type=Path)
    imported.add_argument("--source-id", required=True)
    imported.add_argument("--trust-grade", choices=["independent_machine", "human_reviewed"], default="independent_machine")
    imported.add_argument("--local-speaker", action="append")
    imported.add_argument("--offset-sec", type=float)
    imported.add_argument("--replace", action="store_true")
    for action in ("evaluate", "replay", "status"):
        subparsers.add_parser(action)
    for child in subparsers.choices.values():
        child.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
        child.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
        child.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    for action in ("evaluate", "replay"):
        subparsers.choices[action].add_argument("--write-manifest", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    args.out_dir = args.out_dir.resolve()
    args.sessions_root = args.sessions_root.resolve()
    args.policy = args.policy.resolve()
    if getattr(args, "write_manifest", None):
        args.write_manifest = args.write_manifest.resolve()
    policy = load_policy(args.policy)
    if args.action == "import":
        return import_source(args)
    if args.action == "evaluate":
        return evaluate(args, policy)
    if args.action == "replay":
        return replay(args, policy)
    return status(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PurityError, OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
