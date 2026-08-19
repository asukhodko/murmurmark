#!/usr/bin/env python3
"""Build a private graded lexical reference corpus and a public aggregate report."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
POLICY_SCHEMA = "murmurmark.lexical_accuracy_reference_policy/v1"
REGISTRY_SCHEMA = "murmurmark.lexical_accuracy_reference_registry/v1"
PRIVATE_ROW_SCHEMA = "murmurmark.lexical_accuracy_private_evaluation/v1"
REPORT_SCHEMA = "murmurmark.lexical_accuracy_reference_corpus_report/v1"
MANIFEST_SCHEMA = "murmurmark.lexical_accuracy_reference_frozen_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/lexical-accuracy-reference-corpus-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/lexical-accuracy-reference-corpus-v1"
DEFAULT_TRACKED_MANIFEST = ROOT / "docs/testing/lexical-accuracy-reference-corpus-v1-manifest.json"
WORD_RE = re.compile(r"[0-9a-zа-яё]+", re.IGNORECASE)
RANGE_RE = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(?P<end>\d{1,2}:\d{2}(?::\d{2})?)$"
)
ABSOLUTE_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")


class CorpusError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CorpusError(f"expected JSON object: {path}")
    return payload


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError as error:
        raise CorpusError(f"path is outside repository: {path}") from error


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(WORD_RE.findall(value))


def tokens(text: str) -> list[str]:
    normalized = normalize_text(text)
    return normalized.split() if normalized else []


def edit_metrics(reference_text: str, hypothesis_text: str) -> dict[str, Any]:
    reference = tokens(reference_text)
    hypothesis = tokens(hypothesis_text)
    # Each cell carries distance plus operation counts. Keeping only the previous
    # row makes whole-meeting comparisons linear in memory.
    previous = [(index, 0, 0, index) for index in range(len(hypothesis) + 1)]
    priority = {"equal": 0, "substitution": 1, "deletion": 2, "insertion": 3}
    for i, reference_token in enumerate(reference, 1):
        current = [(i, 0, i, 0)]
        for j, hypothesis_token in enumerate(hypothesis, 1):
            candidates: list[tuple[int, int, int, int, int]] = []
            diagonal = previous[j - 1]
            if reference_token == hypothesis_token:
                candidates.append((diagonal[0], priority["equal"], diagonal[1], diagonal[2], diagonal[3]))
            else:
                candidates.append((diagonal[0] + 1, priority["substitution"], diagonal[1] + 1, diagonal[2], diagonal[3]))
            above = previous[j]
            candidates.append((above[0] + 1, priority["deletion"], above[1], above[2] + 1, above[3]))
            left = current[j - 1]
            candidates.append((left[0] + 1, priority["insertion"], left[1], left[2], left[3] + 1))
            best = min(candidates, key=lambda item: (item[0], item[1]))
            current.append((best[0], best[2], best[3], best[4]))
        previous = current
    word_errors, substitutions, deletions, insertions = previous[-1]

    reference_chars = list(normalize_text(reference_text))
    hypothesis_chars = list(normalize_text(hypothesis_text))
    previous = list(range(len(hypothesis_chars) + 1))
    for i, character in enumerate(reference_chars, 1):
        current = [i]
        for j, candidate in enumerate(hypothesis_chars, 1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (character != candidate),
                )
            )
        previous = current
    char_errors = previous[-1]
    return {
        "reference_words": len(reference),
        "hypothesis_words": len(hypothesis),
        "word_errors": word_errors,
        "wer": round(word_errors / len(reference), 6) if reference else None,
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "reference_characters": len(reference_chars),
        "character_errors": char_errors,
        "cer": round(char_errors / len(reference_chars), 6) if reference_chars else None,
    }


def domain_term_metrics(reference_text: str, hypothesis_text: str, terms: Iterable[str]) -> dict[str, Any]:
    reference = f" {normalize_text(reference_text)} "
    hypothesis = f" {normalize_text(hypothesis_text)} "
    present = [normalize_text(term) for term in terms if f" {normalize_text(term)} " in reference]
    correct = sum(1 for term in present if f" {term} " in hypothesis)
    return {
        "reference_terms": len(present),
        "correct_terms": correct,
        "accuracy": round(correct / len(present), 6) if present else None,
    }


def parse_clock(value: str) -> float:
    parts = [int(part) for part in value.split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    raise CorpusError(f"invalid timestamp: {value}")


def parse_tab_transcript(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3 or not re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", fields[0].strip()):
            continue
        rows.append({"start": parse_clock(fields[0].strip()), "speaker": fields[1].strip(), "text": fields[2].strip()})
    for index, row in enumerate(rows):
        next_start = rows[index + 1]["start"] if index + 1 < len(rows) else row["start"] + 30.0
        row["end"] = max(row["start"] + 0.01, next_start)
    return rows


def parse_range_transcript(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        match = RANGE_RE.match(lines[index].strip())
        if not match:
            index += 1
            continue
        start = parse_clock(match.group("start"))
        end = parse_clock(match.group("end"))
        index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        speaker = lines[index].strip() if index < len(lines) else ""
        index += 1
        content: list[str] = []
        while index < len(lines) and not RANGE_RE.match(lines[index].strip()):
            if lines[index].strip():
                content.append(lines[index].strip())
            index += 1
        rows.append({"start": start, "end": max(start + 0.01, end), "speaker": speaker, "text": " ".join(content)})
    return rows


def detect_format(text: str) -> str:
    if any(re.match(r"^\d{1,2}:\d{2}:\d{2}\t", line) for line in text.splitlines()):
        return "timestamp_tab"
    if any(RANGE_RE.match(line.strip()) for line in text.splitlines()):
        return "range_blocks"
    raise CorpusError("cannot detect external transcript format")


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise CorpusError(f"unsupported policy schema: {policy.get('schema')}")
    eligible = set(policy.get("correctness_eligible_trust_grades") or [])
    diagnostic = set(policy.get("diagnostic_only_trust_grades") or [])
    if not eligible or not diagnostic or eligible & diagnostic:
        raise CorpusError("policy trust grades must be non-empty and disjoint")
    return policy


def resolve_session(raw: str, sessions_root: Path) -> tuple[Path, str]:
    value = Path(raw).expanduser()
    candidates = [value] if value.is_absolute() else [value, sessions_root / value, sessions_root / value.name]
    root = sessions_root.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if not resolved.is_dir():
            continue
        try:
            session_id = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        if session_id and session_id != ".":
            return resolved, session_id
    raise CorpusError(f"session not found under {sessions_root}: {raw}")


def import_external(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    source = args.source.resolve()
    if not source.is_file():
        raise CorpusError(f"source not found: {source}")
    _, session_id = resolve_session(args.session_id, args.sessions_root)
    allowed = set(policy["correctness_eligible_trust_grades"]) | set(policy["diagnostic_only_trust_grades"])
    if args.trust_grade not in allowed:
        raise CorpusError(f"unsupported trust grade: {args.trust_grade}")
    text = source.read_text(encoding="utf-8-sig")
    source_format = detect_format(text) if args.format == "auto" else args.format
    entries = parse_tab_transcript(text) if source_format == "timestamp_tab" else parse_range_transcript(text)
    if not entries:
        raise CorpusError("external transcript contains no timestamped entries")

    private_root = args.out_dir / "private"
    source_dir = private_root / "sources" / args.source_id
    source_dir.mkdir(parents=True, exist_ok=True)
    copied = source_dir / "source.txt"
    copied.write_bytes(source.read_bytes())
    local_speakers = set(args.local_speaker or [])
    parsed = []
    for entry in entries:
        parsed.append(
            {
                **entry,
                "role": "me" if entry["speaker"] in local_speakers else "remote",
            }
        )
    parsed_path = source_dir / "parsed.json"
    write_json(parsed_path, {"entries": parsed, "format": source_format})

    registry_path = private_root / "source_registry.json"
    registry = read_json(registry_path) if registry_path.is_file() else {"schema": REGISTRY_SCHEMA, "sources": []}
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise CorpusError("invalid private source registry")
    row = {
        "source_id": args.source_id,
        "kind": "external_transcript",
        "trust_grade": args.trust_grade,
        "session_id": session_id,
        "meeting_mode": args.meeting_mode,
        "acoustic_mode": args.acoustic_mode,
        "source": fingerprint(copied),
        "parsed": fingerprint(parsed_path),
        "entry_count": len(parsed),
        "local_speaker_count": len(local_speakers),
    }
    registry["sources"] = [item for item in registry["sources"] if item.get("source_id") != args.source_id] + [row]
    registry["sources"].sort(key=lambda item: item["source_id"])
    write_json(registry_path, registry)
    print(f"imported: {args.source_id}")
    print(f"entries: {len(parsed)}")
    print(f"private_registry: {portable(registry_path)}")
    return 0


def whisper_model_path(args: argparse.Namespace) -> Path:
    candidate = args.model or os.environ.get("MURMURMARK_WHISPER_MODEL")
    if candidate:
        return Path(candidate).expanduser().resolve()
    return Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"


def run_whisper(input_path: Path, cache_base: Path, args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    model = whisper_model_path(args)
    if not model.is_file():
        raise CorpusError(f"whisper model missing: {model}")
    executable = shutil.which(args.whisper_cli)
    if not executable:
        raise CorpusError(f"whisper-cli missing: {args.whisper_cli}")
    cache_base.parent.mkdir(parents=True, exist_ok=True)
    json_path = cache_base.with_suffix(".json")
    meta_path = cache_base.with_suffix(".meta.json")
    identity = {
        "audio_sha256": sha256(input_path),
        "model_sha256": sha256(model),
        "language": args.language,
        "threads": args.threads,
    }
    if json_path.is_file() and meta_path.is_file() and read_json(meta_path) == identity:
        payload = read_json(json_path)
    else:
        command = [
            executable,
            "--model", str(model),
            "--language", args.language,
            "--threads", str(args.threads),
            "--max-context", "0",
            "--temperature", "0",
            "--temperature-inc", "0",
            "--no-fallback",
            "--output-json-full",
            "--output-file", str(cache_base),
            "--no-prints",
            "--log-score",
            "--suppress-nst",
            "--file", str(input_path),
        ]
        subprocess.run(["nice", "-n", "20", *command], stdin=subprocess.DEVNULL, check=True)
        payload = read_json(json_path)
        write_json(meta_path, identity)
    text = " ".join(str(row.get("text") or "") for row in payload.get("transcription") or [])
    timestamped_tokens = sum(
        1
        for row in payload.get("transcription") or []
        for token in row.get("tokens") or []
        if token.get("offsets", {}).get("to") is not None and str(token.get("text") or "").strip()
    )
    return text, {"segments": len(payload.get("transcription") or []), "timestamped_tokens": timestamped_tokens}


def extract_exact_clip(stimulus: Path, output: Path, duration_sec: float) -> None:
    try:
        import soundfile as sf
    except ImportError as error:
        raise CorpusError("soundfile is required for exact generated clip extraction") from error
    data, sample_rate = sf.read(stimulus, always_2d=False)
    frames = min(len(data), int(round(duration_sec * sample_rate)))
    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output, data[:frames], sample_rate, subtype="PCM_16")


def exact_generated_row(policy: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    stimuli_manifest_path = args.sessions_root / "_echo_lab/controlled-echo-supervision-v1/stimuli/stimuli_manifest.json"
    manifest = read_json(stimuli_manifest_path)
    config = policy["exact_generated_clip"]
    stimulus = next((row for row in manifest.get("stimuli", []) if row.get("id") == config["stimulus_id"]), None)
    if not stimulus:
        raise CorpusError(f"stimulus not found: {config['stimulus_id']}")
    audio_path = args.sessions_root / str(stimulus["path"])
    if sha256(audio_path) != stimulus["sha256"]:
        raise CorpusError("exact generated stimulus hash mismatch")
    clip = args.out_dir / "private/asr/exact_generated.wav"
    extract_exact_clip(audio_path, clip, float(config["duration_sec"]))
    hypothesis, timing = run_whisper(clip, args.out_dir / "private/asr/exact_generated", args)
    reference = str(stimulus["expected_text"])
    metrics = edit_metrics(reference, hypothesis)
    metrics["domain_terms"] = domain_term_metrics(reference, hypothesis, policy["exact_generated_domain_terms"])
    return {
        "schema": PRIVATE_ROW_SCHEMA,
        "source_id": config["source_id"],
        "kind": "exact_generated",
        "trust_grade": "exact_generated",
        "correctness_eligible": True,
        "session_id": None,
        "meeting_mode": "controlled",
        "acoustic_mode": "digital_source",
        "role_scope": ["remote"],
        "split": "frozen_exact",
        "reference_text": reference,
        "hypothesis_text": hypothesis,
        "metrics": metrics,
        "timing": timing,
        "inputs": [fingerprint(stimuli_manifest_path), fingerprint(audio_path), fingerprint(clip)],
    }


def prompt_events(path: Path, phase_id: str) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("type") == "prompt_shown" and row.get("phase_id") == phase_id:
            rows.append(row)
    return rows


def phase_file(session: Path, phase_id: str) -> Path:
    inventory_path = session / "derived/echo-lab/phase_inventory.jsonl"
    for line in inventory_path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("phase_id") == phase_id and row.get("source") == "mic":
            candidate = session / str(row["audio"]["path"])
            if candidate.is_file():
                return candidate
    matches = sorted((session / "derived/echo-lab/phases").glob(f"*_{phase_id}_mic.wav"))
    if not matches:
        raise CorpusError(f"phase audio missing: {session.name}/{phase_id}")
    return matches[0]


def scripted_rows(policy: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    result = []
    for config in policy["scripted_echo_sessions"]:
        session = args.sessions_root / config["session_id"]
        events_path = session / "derived/echo-lab/echo_lab_events.jsonl"
        for phase_id in policy["scripted_phases"]:
            events = prompt_events(events_path, phase_id)
            if not events:
                raise CorpusError(f"prompt events missing: {session.name}/{phase_id}")
            audio = phase_file(session, phase_id)
            hypothesis, timing = run_whisper(
                audio,
                args.out_dir / f"private/asr/{session.name}_{phase_id}",
                args,
            )
            reference = " ".join(str(row["text"]) for row in events)
            result.append(
                {
                    "schema": PRIVATE_ROW_SCHEMA,
                    "source_id": f"echo_{config['split']}_{phase_id}",
                    "kind": "scripted_echo_prompt",
                    "trust_grade": "scripted_expected",
                    "correctness_eligible": False,
                    "session_id": session.name,
                    "meeting_mode": config["meeting_mode"],
                    "acoustic_mode": config["acoustic_mode"],
                    "role_scope": ["me"],
                    "split": config["split"],
                    "reference_text": reference,
                    "hypothesis_text": hypothesis,
                    "metrics": edit_metrics(reference, hypothesis),
                    "timing": {
                        **timing,
                        "prompt_events": len(events),
                        "first_prompt_sec": min(float(row["actual_at_sec"]) for row in events),
                        "last_prompt_sec": max(float(row["actual_at_sec"]) for row in events),
                    },
                    "inputs": [fingerprint(events_path), fingerprint(audio)],
                }
            )
    return result


def selected_dialogue(session: Path) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    lineage: list[Path] = []
    if selection_path.is_file():
        selection = read_json(selection_path)
        dialogue_path = session / str(selection["selected_dialogue"]["path"])
        lineage.append(selection_path)
    else:
        readiness_path = session / "derived/readiness/session_readiness.json"
        readiness = read_json(readiness_path)
        profile = str(readiness.get("selected_profile") or "")
        dialogue_path = session / f"derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.{profile}.json"
        lineage.append(readiness_path)
    payload = read_json(dialogue_path)
    utterances = payload.get("utterances") or []
    if not utterances:
        raise CorpusError(f"selected dialogue is empty: {session.name}")
    return dialogue_path, utterances, [fingerprint(path) for path in [*lineage, dialogue_path]]


def role_text(rows: list[dict[str, Any]], role: str | None = None) -> str:
    return " ".join(
        str(row.get("text") or "")
        for row in rows
        if role is None or str(row.get("role") or "").lower() == role
    )


def sum_metrics(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    totals = {
        "reference_words": 0,
        "hypothesis_words": 0,
        "word_errors": 0,
        "substitutions": 0,
        "deletions": 0,
        "insertions": 0,
        "reference_characters": 0,
        "character_errors": 0,
    }
    for row in rows:
        for key in totals:
            totals[key] += int(row.get(key) or 0)
    totals["wer"] = round(totals["word_errors"] / totals["reference_words"], 6) if totals["reference_words"] else None
    totals["cer"] = round(totals["character_errors"] / totals["reference_characters"], 6) if totals["reference_characters"] else None
    return totals


def aligned_external_metrics(
    reference_rows: list[dict[str, Any]], utterances: list[dict[str, Any]]
) -> tuple[dict[str, Any], str, str, dict[str, Any]]:
    authoritative_start = min(float(row["start"]) for row in utterances)
    authoritative_end = max(float(row["end"]) for row in utterances)
    comparable = [
        row
        for row in reference_rows
        if float(row["end"]) >= authoritative_start and float(row["start"]) <= authoritative_end
    ]
    if not comparable:
        raise CorpusError("external reference has no interval overlapping the authoritative dialogue")
    aligned_metrics: list[dict[str, Any]] = []
    aligned_references: list[str] = []
    aligned_hypotheses: list[str] = []
    by_role_rows: dict[str, list[dict[str, Any]]] = {"me": [], "remote": []}
    matched_utterance_ids: set[str] = set()
    for reference in comparable:
        start = float(reference["start"])
        end = float(reference["end"])
        role = str(reference.get("role") or "remote")
        matching = []
        for utterance in utterances:
            if str(utterance.get("role") or "").lower() != role:
                continue
            midpoint = (float(utterance.get("start") or 0) + float(utterance.get("end") or 0)) / 2.0
            if start - 0.5 <= midpoint <= end + 0.5:
                matching.append(utterance)
                matched_utterance_ids.add(str(utterance.get("id") or ""))
        hypothesis = role_text(matching)
        metric = edit_metrics(str(reference.get("text") or ""), hypothesis)
        aligned_metrics.append(metric)
        aligned_references.append(str(reference.get("text") or ""))
        by_role_rows.setdefault(role, []).append(metric)
        aligned_hypotheses.append(hypothesis)
    result = sum_metrics(aligned_metrics)
    result["by_role"] = {
        role: sum_metrics(metrics)
        for role, metrics in by_role_rows.items()
        if metrics
    }
    timing = {
        "reference_intervals": len(reference_rows),
        "compared_reference_intervals": len(comparable),
        "reference_interval_coverage": round(len(comparable) / len(reference_rows), 6),
        "authoritative_utterances": len(utterances),
        "matched_authoritative_utterances": len(matched_utterance_ids),
        "authoritative_utterance_coverage": round(len(matched_utterance_ids) / len(utterances), 6) if utterances else None,
        "reference_start_sec": min(float(row["start"]) for row in comparable),
        "reference_end_sec": max(float(row["end"]) for row in comparable),
        "authoritative_start_sec": authoritative_start,
        "authoritative_end_sec": authoritative_end,
    }
    return result, " ".join(aligned_references), " ".join(aligned_hypotheses), timing


def external_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    registry_path = args.out_dir / "private/source_registry.json"
    if not registry_path.is_file():
        return []
    registry = read_json(registry_path)
    if registry.get("schema") != REGISTRY_SCHEMA:
        raise CorpusError("invalid private source registry")
    result = []
    for source in registry.get("sources") or []:
        parsed_path = ROOT / source["parsed"]["path"]
        if fingerprint(parsed_path) != source["parsed"]:
            raise CorpusError(f"parsed source changed: {source['source_id']}")
        original_path = ROOT / source["source"]["path"]
        if fingerprint(original_path) != source["source"]:
            raise CorpusError(f"source changed: {source['source_id']}")
        reference_rows = read_json(parsed_path)["entries"]
        session, session_id = resolve_session(str(source["session_id"]), args.sessions_root)
        dialogue_path, utterances, lineage = selected_dialogue(session)
        metrics, overall_reference, overall_hypothesis, timing = aligned_external_metrics(reference_rows, utterances)
        roles = sorted({str(row.get("role") or "remote") for row in reference_rows})
        result.append(
            {
                "schema": PRIVATE_ROW_SCHEMA,
                "source_id": source["source_id"],
                "kind": "external_transcript",
                "trust_grade": source["trust_grade"],
                "correctness_eligible": source["trust_grade"] in {"exact_generated", "human_reviewed"},
                "session_id": session_id,
                "meeting_mode": source["meeting_mode"],
                "acoustic_mode": source["acoustic_mode"],
                "role_scope": roles,
                "split": "external",
                "reference_text": overall_reference,
                "hypothesis_text": overall_hypothesis,
                "metrics": metrics,
                "timing": timing,
                "inputs": [source["source"], source["parsed"], *lineage],
                "selected_dialogue_path": portable(dialogue_path),
            }
        )
    return result


def public_source(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "kind": row["kind"],
        "trust_grade": row["trust_grade"],
        "correctness_eligible": row["correctness_eligible"],
        "session_id": row["session_id"],
        "meeting_mode": row["meeting_mode"],
        "acoustic_mode": row["acoustic_mode"],
        "role_scope": row["role_scope"],
        "split": row["split"],
        "reference_sha256": text_sha(normalize_text(row["reference_text"])),
        "hypothesis_sha256": text_sha(normalize_text(row["hypothesis_text"])),
        "metrics": row["metrics"],
        "timing": row["timing"],
        "inputs": row["inputs"],
    }


def aggregate_eligible(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return sum_metrics(row["metrics"] for row in rows)


def build_public(policy: dict[str, Any], rows: list[dict[str, Any]], args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    eligible = [row for row in rows if row["correctness_eligible"]]
    human = [row for row in eligible if row["trust_grade"] == "human_reviewed" and row["session_id"]]
    gates_policy = policy["real_meeting_baseline_gates"]
    meeting_modes = sorted({row["meeting_mode"] for row in human})
    acoustic_modes = sorted({row["acoustic_mode"] for row in human})
    roles = sorted({role for row in human for role in row["role_scope"]})
    gates = {
        "exact_generated_reference_present": any(row["trust_grade"] == "exact_generated" for row in eligible),
        "minimum_human_reviewed_sessions": len({row["session_id"] for row in human}) >= int(gates_policy["minimum_human_reviewed_sessions"]),
        "required_meeting_modes": set(gates_policy["required_meeting_modes"]).issubset(meeting_modes),
        "required_acoustic_modes": set(gates_policy["required_acoustic_modes"]).issubset(acoustic_modes),
        "required_roles": set(gates_policy["required_roles"]).issubset(roles),
        "weak_references_excluded_from_correctness": all(row["trust_grade"] not in policy["diagnostic_only_trust_grades"] for row in eligible),
    }
    real_ready = all(value for key, value in gates.items() if key != "exact_generated_reference_present")
    decision = "LEXICAL_BASELINE_ESTABLISHED" if real_ready and gates["exact_generated_reference_present"] else "REFERENCE_INSUFFICIENT"
    public_sources = [public_source(row) for row in sorted(rows, key=lambda item: item["source_id"])]
    report = {
        "schema": REPORT_SCHEMA,
        "generator": policy["generator"],
        "decision": decision,
        "policy": fingerprint(args.policy),
        "summary": {
            "sources": len(rows),
            "correctness_eligible_sources": len(eligible),
            "diagnostic_only_sources": len(rows) - len(eligible),
            "human_reviewed_real_sessions": len({row["session_id"] for row in human}),
            "exact_subset": aggregate_eligible(eligible),
            "meeting_modes_present": sorted({row["meeting_mode"] for row in rows}),
            "acoustic_modes_present": sorted({row["acoustic_mode"] for row in rows}),
            "role_scopes_present": sorted({role for row in rows for role in row["role_scope"]}),
        },
        "gates": {**gates, "real_meeting_lexical_baseline_ready": real_ready},
        "sources": public_sources,
        "evidence_limit": {
            "status": "closed" if decision == "LEXICAL_BASELINE_ESTABLISHED" else "open",
            "reason": None if decision == "LEXICAL_BASELINE_ESTABLISHED" else "No human-reviewed real-meeting word reference is frozen. Exact generated speech measures only the synthetic digital-source subset; scripted prompts and independent machine transcripts are diagnostic disagreement evidence, not ground truth.",
            "missing_human_reviewed_sessions": max(0, int(gates_policy["minimum_human_reviewed_sessions"]) - len({row["session_id"] for row in human})),
            "missing_meeting_modes": sorted(set(gates_policy["required_meeting_modes"]) - set(meeting_modes)),
            "missing_acoustic_modes": sorted(set(gates_policy["required_acoustic_modes"]) - set(acoustic_modes)),
            "missing_roles": sorted(set(gates_policy["required_roles"]) - set(roles)),
        },
        "safety": {
            "selected_transcripts_modified": False,
            "speaker_profiles_modified": False,
            "primary_asr_modified": False,
            "weak_reference_promoted": False,
            "aggregate_quality_score": None,
        },
        "next_goal": {
            "id": "human-reviewed-lexical-seed-v1" if decision == "REFERENCE_INSUFFICIENT" else "largest-measured-lexical-defect-v1",
            "title": "Human-Reviewed Lexical Seed v1" if decision == "REFERENCE_INSUFFICIENT" else "Largest Measured Lexical Defect v1",
            "status": "blocked_external_evidence" if decision == "REFERENCE_INSUFFICIENT" else "ready",
        },
    }
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": decision,
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__)),
        "summary": report["summary"],
        "gates": report["gates"],
        "sources": public_sources,
    }
    assert_public_safe(report)
    assert_public_safe(manifest)
    return report, manifest


def assert_public_safe(value: Any) -> None:
    text = canonical_json(value).decode("utf-8")
    if ABSOLUTE_PATH_RE.search(text):
        raise CorpusError("public lexical artifact contains an absolute path")
    forbidden_keys = {"reference_text", "hypothesis_text", "speaker", "local_speaker", "human_name"}

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                if key in forbidden_keys:
                    raise CorpusError(f"public lexical artifact contains private field: {key}")
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)


def report_markdown(report: dict[str, Any]) -> str:
    exact = report["summary"]["exact_subset"]
    lines = [
        "# Lexical Accuracy Reference Corpus v1",
        "",
        f"Decision: `{report['decision']}`  ",
        f"Sources: `{report['summary']['sources']}`  ",
        f"Human-reviewed real sessions: `{report['summary']['human_reviewed_real_sessions']}`",
        "",
        "## Correctness-Eligible Subset",
        "",
        f"- words: `{exact['reference_words']}`",
        f"- WER: `{exact['wer']}`",
        f"- CER: `{exact['cer']}`",
        f"- substitutions/deletions/insertions: `{exact['substitutions']}/{exact['deletions']}/{exact['insertions']}`",
        "",
        "This subset currently contains exact generated speech only. Scripted operator prompts and",
        "independent machine transcripts remain diagnostic and do not establish real-meeting accuracy.",
        "",
        "## Coverage Gates",
        "",
    ]
    lines.extend(f"- `{key}`: `{'pass' if value else 'fail'}`" for key, value in report["gates"].items())
    lines.extend(["", "## Evidence Limit", "", report["evidence_limit"]["reason"] or "The required real-meeting reference coverage is complete.", "", "## Sources", "", "| Source | Trust | Mode | Acoustic | WER |", "|---|---|---|---|---:|"])
    for row in report["sources"]:
        lines.append(f"| `{row['source_id']}` | `{row['trust_grade']}` | `{row['meeting_mode']}` | `{row['acoustic_mode']}` | `{row['metrics'].get('wer')}` |")
    return "\n".join(lines) + "\n"


def build(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    rows = [exact_generated_row(policy, args), *scripted_rows(policy, args), *external_rows(args)]
    private_path = args.out_dir / "private/evaluation_rows.json"
    write_json(private_path, {"schema": "murmurmark.lexical_accuracy_private_evaluations/v1", "rows": rows})
    report, manifest = build_public(policy, rows, args)
    outputs = {
        "lexical_accuracy_reference_report.json": canonical_json(report),
        "lexical_accuracy_reference_report.md": report_markdown(report).encode("utf-8"),
        "reference_manifest.json": canonical_json(manifest),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        (args.out_dir / name).write_bytes(content)
    if args.write_manifest:
        args.write_manifest.parent.mkdir(parents=True, exist_ok=True)
        args.write_manifest.write_bytes(canonical_json(manifest))
    print(f"decision: {report['decision']}")
    print(f"sources: {report['summary']['sources']}")
    print(f"exact_subset_wer: {report['summary']['exact_subset']['wer']}")
    print(f"human_reviewed_real_sessions: {report['summary']['human_reviewed_real_sessions']}")
    print(f"report: {portable(args.out_dir / 'lexical_accuracy_reference_report.md')}")
    return 0


def replay(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    private_path = args.out_dir / "private/evaluation_rows.json"
    if not private_path.is_file():
        raise CorpusError("private evaluation rows are missing; run build first")
    rows = read_json(private_path).get("rows") or []
    report, manifest = build_public(policy, rows, args)
    expected = {
        "lexical_accuracy_reference_report.json": canonical_json(report),
        "lexical_accuracy_reference_report.md": report_markdown(report).encode("utf-8"),
        "reference_manifest.json": canonical_json(manifest),
    }
    stale = [name for name, content in expected.items() if not (args.out_dir / name).is_file() or (args.out_dir / name).read_bytes() != content]
    if args.write_manifest and (not args.write_manifest.is_file() or args.write_manifest.read_bytes() != canonical_json(manifest)):
        stale.append(portable(args.write_manifest))
    if stale:
        print("stale lexical outputs: " + ", ".join(stale), file=sys.stderr)
        return 2
    print("replay: deterministic")
    print(f"decision: {report['decision']}")
    return 0


def status(args: argparse.Namespace) -> int:
    path = args.out_dir / "lexical_accuracy_reference_report.json"
    if not path.is_file():
        print("status: missing")
        print("next: murmurmark corpus lexical build")
        return 2
    report = read_json(path)
    print(f"decision: {report.get('decision')}")
    print(f"sources: {report.get('summary', {}).get('sources')}")
    print(f"exact_subset_wer: {report.get('summary', {}).get('exact_subset', {}).get('wer')}")
    print(f"human_reviewed_real_sessions: {report.get('summary', {}).get('human_reviewed_real_sessions')}")
    print(f"next_goal: {report.get('next_goal', {}).get('title')}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build MurmurMark's private graded lexical reference corpus.")
    subparsers = result.add_subparsers(dest="action", required=True)
    imported = subparsers.add_parser("import", help="Import an external transcript into ignored private storage.")
    imported.add_argument("session_id")
    imported.add_argument("source", type=Path)
    imported.add_argument("--source-id", required=True)
    imported.add_argument("--format", choices=["auto", "timestamp_tab", "range_blocks"], default="auto")
    imported.add_argument("--trust-grade", default="independent_machine")
    imported.add_argument("--meeting-mode", choices=["1x1", "group", "controlled"], required=True)
    imported.add_argument("--acoustic-mode", required=True)
    imported.add_argument("--local-speaker", action="append")
    for action in ("build", "replay", "status"):
        subparsers.add_parser(action)
    for child in subparsers.choices.values():
        child.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
        child.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
        child.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    for action in ("build", "replay"):
        child = subparsers.choices[action]
        child.add_argument("--write-manifest", type=Path)
        child.add_argument("--model", type=Path)
        child.add_argument("--whisper-cli", default=os.environ.get("WHISPER_CLI", "whisper-cli"))
        child.add_argument("--language", default="ru")
        child.add_argument("--threads", type=int, default=4)
    return result


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.resolve()
    args.out_dir = args.out_dir.resolve()
    args.sessions_root = args.sessions_root.resolve()
    if getattr(args, "write_manifest", None):
        args.write_manifest = args.write_manifest.resolve()
    policy = load_policy(args.policy)
    if args.action == "import":
        return import_external(args, policy)
    if args.action == "build":
        return build(args, policy)
    if args.action == "replay":
        return replay(args, policy)
    return status(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CorpusError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
