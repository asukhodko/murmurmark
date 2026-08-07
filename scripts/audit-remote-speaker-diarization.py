#!/usr/bin/env python3
"""Build word/frame-level anonymous remote-speaker evidence.

The selected dialogue and authoritative remote audio stay immutable. Resemblyzer
centroids from the conservative v1 map seed bounded frame classification; every
unsupported selected word remains explicit aggregate Colleagues evidence.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sys
from typing import Any
import warnings

import numpy as np
import soundfile as sf


VERSION = "0.2.0"
REPORT_SCHEMA = "murmurmark.remote_speaker_diarization_report/v2"
FRAME_SCHEMA = "murmurmark.remote_speaker_frame/v2"
WORD_SCHEMA = "murmurmark.remote_speaker_word/v2"
UTTERANCE_SCHEMA = "murmurmark.remote_speaker_utterance/v2"
MAP_SCHEMA = "murmurmark.remote_speaker_map/v2"
RICH_SCHEMA = "murmurmark.remote_speaker_rich_transcript/v2"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_diarization_artifact_manifest/v2"
FIXTURE_SCHEMA = "murmurmark.remote_speaker_embedding_fixture/v2"
V1_REPORT_SCHEMA = "murmurmark.remote_speaker_evidence_report/v1"
V1_ATTRIBUTION_SCHEMA = "murmurmark.remote_utterance_attribution/v1"
DEFAULT_OUT_DIR = "derived/audit/remote-speaker-diarization-v2"
DEFAULT_V1_DIR = "derived/audit/remote-speaker-evidence-v1"
RAW_REMOTE_JSON = "derived/transcript-simple/whisper-cpp/raw/remote.json"
ROOT = Path(__file__).resolve().parents[1]
PROMOTION_POLICY = ROOT / "policies/remote-speaker-diarization-v2.json"
WORD_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
SPECIAL_TOKEN_RE = re.compile(r"^\[_.*_\]$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build fail-open word/frame remote-speaker evidence from v1 seed voices."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--profile", default="auto")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR)
    parser.add_argument("--v1-dir", default=DEFAULT_V1_DIR)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--embedding-fixture", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--window-sec", type=float, default=6.0)
    parser.add_argument("--stride-sec", type=float, default=3.0)
    parser.add_argument("--short-window-max-sec", type=float, default=8.0)
    parser.add_argument("--min-similarity", type=float, default=0.72)
    parser.add_argument("--min-margin", type=float, default=0.02)
    parser.add_argument("--strict-similarity", type=float, default=0.82)
    parser.add_argument("--strict-margin", type=float, default=0.08)
    parser.add_argument("--overlap-min-sec", type=float, default=0.5)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--require-promoted", action="store_true")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.window_sec < 2 or args.stride_sec <= 0 or args.stride_sec > args.window_sec:
        parser.error("window-sec must be >=2 and stride-sec must be in (0, window-sec]")
    for name in ("min_similarity", "strict_similarity"):
        if not 0 <= getattr(args, name) <= 1:
            parser.error(f"{name.replace('_', '-')} must be in [0, 1]")
    for name in ("min_margin", "strict_margin"):
        if not 0 <= getattr(args, name) <= 1:
            parser.error(f"{name.replace('_', '-')} must be in [0, 1]")
    return args


def progress(args: argparse.Namespace, message: str) -> None:
    if args.progress:
        print(f"remote_diarization: {message}", flush=True)


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"expected JSON object row: {path}")
        rows.append(row)
    return rows


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


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


def relative(path: Path, session: Path) -> str:
    try:
        return str(path.resolve().relative_to(session.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path, session: Path | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {
        "path": relative(path, session) if session else str(path.resolve()),
        "exists": path.is_file(),
    }
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def implementation_provenance() -> dict[str, Any]:
    return {
        "script": fingerprint(Path(__file__).resolve()),
        "version": VERSION,
    }


def same_fingerprint(expected: Any, path: Path) -> bool:
    return (
        isinstance(expected, dict)
        and expected.get("exists") is True
        and path.is_file()
        and expected.get("sha256") == sha256(path)
        and int(expected.get("bytes") or -1) == path.stat().st_size
    )


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("zero_embedding")
    return np.asarray(vector / norm, dtype=np.float32)


def lexical_words(text: str) -> list[dict[str, Any]]:
    return [
        {
            "text": match.group(0),
            "start_char": match.start(),
            "end_char": match.end(),
            "normalized": match.group(0).lower().replace("ё", "е"),
        }
        for match in WORD_RE.finditer(text)
    ]


def millis(row: Any, key: str) -> float | None:
    if not isinstance(row, dict):
        return None
    offsets = row.get("offsets")
    if not isinstance(offsets, dict):
        return None
    try:
        return float(offsets[key]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None


def raw_whisper_words(payload: dict[str, Any]) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(payload.get("transcription") or []):
        if not isinstance(segment, dict):
            continue
        pieces: list[dict[str, Any]] = []
        joined = ""
        for token in segment.get("tokens") or []:
            if not isinstance(token, dict):
                continue
            text = str(token.get("text") or "")
            if not text or SPECIAL_TOKEN_RE.match(text):
                continue
            start_char = len(joined)
            joined += text
            pieces.append(
                {
                    "start_char": start_char,
                    "end_char": len(joined),
                    "start": millis(token, "from"),
                    "end": millis(token, "to"),
                }
            )
        segment_start = millis(segment, "from")
        segment_end = millis(segment, "to")
        if segment_start is None or segment_end is None:
            segment_start = float((segment.get("offsets") or {}).get("from") or 0) / 1000.0
            segment_end = float((segment.get("offsets") or {}).get("to") or 0) / 1000.0
        spans = lexical_words(joined)
        for word_index, word in enumerate(spans):
            related = [
                piece
                for piece in pieces
                if piece["end_char"] > word["start_char"] and piece["start_char"] < word["end_char"]
            ]
            starts = [float(piece["start"]) for piece in related if piece.get("start") is not None]
            ends = [float(piece["end"]) for piece in related if piece.get("end") is not None]
            if starts and ends:
                start = min(starts)
                end = max(ends)
            else:
                divisor = max(1, len(spans))
                start = segment_start + (segment_end - segment_start) * word_index / divisor
                end = segment_start + (segment_end - segment_start) * (word_index + 1) / divisor
            words.append(
                {
                    "text": word["text"],
                    "normalized": word["normalized"],
                    "start": round(max(segment_start, start), 6),
                    "end": round(max(start, min(segment_end, end)), 6),
                    "segment_index": segment_index,
                }
            )
    return words


def source_interval(utterance: dict[str, Any]) -> tuple[float, float]:
    start = utterance.get("source_start", utterance.get("start", 0.0))
    end = utterance.get("source_end", utterance.get("end", start))
    try:
        left, right = float(start), float(end)
    except (TypeError, ValueError):
        left = float(utterance.get("start") or 0.0)
        right = float(utterance.get("end") or left)
    if right <= left:
        left = float(utterance.get("start") or left)
        right = max(left, float(utterance.get("end") or left))
    return left, right


def align_selected_words(
    utterance: dict[str, Any], raw_words: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    text = str(utterance.get("text") or "")
    selected = lexical_words(text)
    if not selected:
        return []
    start, end = source_interval(utterance)
    duration = max(0.0, end - start)
    candidates = [
        row
        for row in raw_words
        if float(row["end"]) >= start - 0.75 and float(row["start"]) <= end + 0.75
    ]

    # Monotonic dynamic alignment. Exact word matches are preferred; bounded
    # interpolation preserves selected corrections that do not exist in raw ASR.
    mapped: dict[int, int] = {}
    cursor = 0
    for selected_index, word in enumerate(selected):
        matches = [
            index
            for index in range(cursor, min(len(candidates), cursor + 12))
            if candidates[index]["normalized"] == word["normalized"]
        ]
        if not matches:
            continue
        best = min(
            matches,
            key=lambda index: abs(
                ((float(candidates[index]["start"]) + float(candidates[index]["end"])) / 2)
                - (start + duration * (selected_index + 0.5) / len(selected))
            ),
        )
        mapped[selected_index] = best
        cursor = best + 1

    rows: list[dict[str, Any]] = []
    previous_start = start
    for index, word in enumerate(selected):
        raw_index = mapped.get(index)
        if raw_index is not None:
            raw = candidates[raw_index]
            word_start = max(start, min(end, float(raw["start"])))
            word_end = max(word_start, min(end, float(raw["end"])))
            timing_source = "raw_whisper_token"
        else:
            left_ratio = word["start_char"] / max(1, len(text))
            right_ratio = word["end_char"] / max(1, len(text))
            word_start = start + duration * left_ratio
            word_end = start + duration * right_ratio
            timing_source = "bounded_text_interpolation"
        word_start = max(previous_start, word_start)
        word_end = max(word_start, min(end, word_end))
        previous_start = word_start
        rows.append(
            {
                **word,
                "start": round(word_start, 6),
                "end": round(word_end, 6),
                "timing_source": timing_source,
            }
        )

    # Coverage weights partition the immutable utterance duration exactly.
    boundaries = [0.0]
    for left, right in zip(selected, selected[1:]):
        boundaries.append((left["end_char"] + right["start_char"]) / 2)
    boundaries.append(float(len(text)))
    for index, row in enumerate(rows):
        share = (boundaries[index + 1] - boundaries[index]) / max(1, len(text))
        row["coverage_weight_sec"] = round(duration * max(0.0, share), 9)
    correction = round(duration - sum(float(row["coverage_weight_sec"]) for row in rows), 9)
    rows[-1]["coverage_weight_sec"] = round(float(rows[-1]["coverage_weight_sec"]) + correction, 9)
    return rows


class EmbeddingBackend:
    def __init__(self, args: argparse.Namespace):
        self.status = "unavailable"
        self.reason: str | None = None
        self.fixture: dict[str, list[float]] | None = None
        self.encoder: Any = None
        self.preprocess: Any = None
        self.provenance: dict[str, Any] = {
            "method": "resemblyzer_seeded_frames_v1",
            "runtime": {"python": sys.version.split()[0]},
        }
        if args.embedding_fixture:
            fixture_path = args.embedding_fixture.expanduser().resolve()
            try:
                fixture = read_json(fixture_path)
            except Exception as error:
                self.reason = f"embedding_fixture_invalid:{type(error).__name__}"
                return
            if fixture.get("schema") != FIXTURE_SCHEMA or not isinstance(fixture.get("embeddings"), dict):
                self.reason = "embedding_fixture_invalid_schema"
                return
            self.fixture = fixture["embeddings"]
            self.provenance = {
                "method": "deterministic_fixture",
                "fixture": fingerprint(fixture_path),
                "runtime": {"python": sys.version.split()[0]},
            }
            self.status = "ready"
            return
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
                import resemblyzer
                from resemblyzer import VoiceEncoder, preprocess_wav
        except (ImportError, ModuleNotFoundError) as error:
            self.reason = f"resemblyzer_unavailable:{type(error).__name__}"
            return
        default_model = Path(resemblyzer.__file__).resolve().with_name("pretrained.pt")
        model = (args.model_path or default_model).expanduser().resolve()
        if not model.is_file():
            self.reason = "speaker_model_missing"
            self.provenance["model"] = fingerprint(model)
            return
        try:
            self.encoder = VoiceEncoder(device="cpu", verbose=False, weights_fpath=model)
        except Exception as error:
            self.reason = f"speaker_model_load_failed:{type(error).__name__}"
            self.provenance["model"] = fingerprint(model)
            return
        self.preprocess = preprocess_wav
        self.provenance = {
            "method": "resemblyzer_seeded_frames_v1",
            "package_version": importlib.metadata.version("resemblyzer"),
            "model": fingerprint(model),
            "license": "Apache-2.0",
            "runtime": {"python": sys.version.split()[0], "numpy": np.__version__},
        }
        self.status = "ready"

    def embed(self, audio: sf.SoundFile | None, key: str, start: float, end: float) -> np.ndarray:
        if self.fixture is not None:
            value = self.fixture.get(key)
            if not isinstance(value, list):
                raise ValueError("fixture_embedding_missing")
            return normalize(np.asarray(value, dtype=np.float32))
        assert audio is not None and self.encoder is not None and self.preprocess is not None
        start_frame = max(0, int(round(start * audio.samplerate)))
        end_frame = min(len(audio), int(round(end * audio.samplerate)))
        if end_frame <= start_frame:
            raise ValueError("empty_audio_slice")
        audio.seek(start_frame)
        waveform = audio.read(end_frame - start_frame, dtype="float32", always_2d=True).mean(axis=1)
        if not len(waveform) or float(np.sqrt(np.mean(np.square(waveform)))) < 1e-7:
            raise ValueError("silent_audio")
        prepared = self.preprocess(waveform, source_sr=audio.samplerate)
        if len(prepared) < 16_000:
            raise ValueError("insufficient_voiced_audio")
        return normalize(self.encoder.embed_utterance(prepared))


def classify(
    embedding: np.ndarray,
    centroids: dict[str, np.ndarray],
    min_similarity: float,
    min_margin: float,
) -> dict[str, Any]:
    scores = sorted(
        ((float(embedding @ centroid), speaker) for speaker, centroid in centroids.items()),
        reverse=True,
    )
    if not scores:
        return {"speaker_id": None, "similarity": None, "margin": None, "scores": {}}
    similarity, speaker = scores[0]
    margin = similarity - scores[1][0] if len(scores) > 1 else similarity
    accepted = similarity >= min_similarity and margin >= min_margin
    return {
        "speaker_id": speaker if accepted else None,
        "similarity": round(similarity, 6),
        "margin": round(margin, 6),
        "scores": {candidate: round(score, 6) for score, candidate in scores},
    }


def analysis_windows(start: float, end: float, args: argparse.Namespace) -> list[tuple[float, float]]:
    duration = end - start
    if duration <= args.short_window_max_sec:
        return [(start, end)]
    rows: list[tuple[float, float]] = []
    cursor = start
    while cursor < end:
        right = min(end, cursor + args.window_sec)
        if right - cursor >= 1.0:
            rows.append((cursor, right))
        if right >= end:
            break
        cursor += args.stride_sec
    if rows and rows[-1][1] < end and end - max(start, end - args.window_sec) >= 1.0:
        tail = (max(start, end - args.window_sec), end)
        if tail != rows[-1]:
            rows.append(tail)
    return rows


def overlap_pairs(utterances: list[dict[str, Any]], minimum: float) -> list[tuple[str, str, float, float]]:
    remote = sorted(
        [row for row in utterances if row.get("role") == "remote" and row.get("id")],
        key=lambda row: (float(row.get("start") or 0), float(row.get("end") or 0), str(row["id"])),
    )
    pairs: list[tuple[str, str, float, float]] = []
    for index, left in enumerate(remote):
        left_end = float(left.get("end") or 0)
        for right in remote[index + 1 :]:
            right_start = float(right.get("start") or 0)
            if right_start >= left_end:
                break
            overlap_start = right_start
            overlap_end = min(left_end, float(right.get("end") or 0))
            if overlap_end - overlap_start >= minimum:
                pairs.append((str(left["id"]), str(right["id"]), overlap_start, overlap_end))
    return pairs


def build_turns(text: str, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not words:
        return [{"speaker_id": None, "speaker_label": "Colleagues", "text": text, "word_ids": []}]
    runs: list[tuple[int, int, str | None]] = []
    run_start = 0
    current = words[0].get("speaker_id")
    for index, word in enumerate(words[1:], start=1):
        speaker = word.get("speaker_id")
        if speaker != current:
            runs.append((run_start, index, current))
            run_start = index
            current = speaker
    runs.append((run_start, len(words), current))

    turns: list[dict[str, Any]] = []
    char_start = 0
    for run_index, (left, right, speaker) in enumerate(runs):
        char_end = words[right]["start_char"] if right < len(words) else len(text)
        selected_text = text[char_start:char_end]
        turns.append(
            {
                "speaker_id": speaker,
                "speaker_label": speaker or "Colleagues",
                "status": "attributed" if speaker else "unknown",
                "start": words[left]["start"],
                "end": words[right - 1]["end"],
                "start_char": char_start,
                "end_char": char_end,
                "text": selected_text,
                "word_ids": [word["word_id"] for word in words[left:right]],
            }
        )
        char_start = char_end
    assert "".join(row["text"] for row in turns) == text
    return turns


def transcript_markdown(utterances: list[dict[str, Any]], selected_profile: str, decision: str) -> str:
    lines = [
        "# Remote Speaker Diarization v2",
        "",
        f"Selected profile: `{selected_profile}`  ",
        f"Decision: `{decision}`  ",
        "Plain transcript remains authoritative.",
        "",
    ]
    for utterance in utterances:
        start = float(utterance.get("start") or 0)
        minute = int(start // 60)
        second = int(start % 60)
        if utterance.get("role") == "remote":
            turns = utterance.get("speaker_turns") or []
            for turn in turns:
                label = str(turn.get("speaker_label") or "Colleagues")
                suffix = "" if turn.get("speaker_id") else " [unknown]"
                lines.extend([f"## {minute:02d}:{second:02d} {label}{suffix}", "", str(turn.get("text") or "").strip(), ""])
        else:
            label = str(utterance.get("speaker_label") or "Me")
            lines.extend([f"## {minute:02d}:{second:02d} {label}", "", str(utterance.get("text") or "").strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Remote Speaker Diarization v2 Report",
        "",
        f"- Status: `{report['status']}`",
        f"- Decision: `{report['decision']}`",
        f"- Selected profile: `{report['source']['profile']}`",
        f"- Anonymous speakers: `{summary['published_speakers']}`",
        f"- Remote words: `{summary['remote_words']}`",
        f"- Attributed words: `{summary['attributed_words']}`",
        f"- Attributable speech ratio: `{summary['attributable_remote_speech_ratio']:.6f}`",
        f"- Internal speaker-change utterances: `{summary['internal_change_utterances']}`",
        f"- Unknown overlap words: `{summary['unknown_overlap_words']}`",
        "",
        "## Reasons",
        "",
    ]
    reasons = report.get("reasons") or []
    lines.extend(f"- `{reason}`" for reason in reasons)
    if not reasons:
        lines.append("- none")
    lines.extend(["", "## Gates", ""])
    lines.extend(f"- `{key}`: `{str(value).lower()}`" for key, value in report["gates"].items())
    return "\n".join(lines) + "\n"


def output_manifest(out_dir: Path, session_id: str) -> dict[str, Any]:
    names = [
        "frame_attribution.jsonl",
        "word_attribution.jsonl",
        "utterance_attribution.jsonl",
        "speaker_map.json",
        "transcript.rich.shadow.json",
        "transcript.rich.shadow.md",
        "report.json",
        "report.md",
    ]
    return {
        "schema": MANIFEST_SCHEMA,
        "session_id": session_id,
        "artifacts": {name: sha256(out_dir / name) for name in names},
    }


def finalize_outputs(
    session: Path,
    out_dir: Path,
    report: dict[str, Any],
    utterances: list[dict[str, Any]],
    frames: list[dict[str, Any]],
    words: list[dict[str, Any]],
    attributions: list[dict[str, Any]],
    speakers: list[dict[str, Any]],
) -> None:
    write_jsonl(out_dir / "frame_attribution.jsonl", frames)
    write_jsonl(out_dir / "word_attribution.jsonl", words)
    write_jsonl(out_dir / "utterance_attribution.jsonl", attributions)
    write_json(
        out_dir / "speaker_map.json",
        {
            "schema": MAP_SCHEMA,
            "session_id": session.name,
            "selected_profile": report["source"]["profile"],
            "decision": report["decision"],
            "speakers": speakers,
        },
    )
    rich = {
        "schema": RICH_SCHEMA,
        "session_id": session.name,
        "selected_profile": report["source"]["profile"],
        "decision": report["decision"],
        "source": report["source"],
        "utterances": utterances,
        "remote_speaker_attributions": attributions,
        "remote_word_attributions": words,
        "speaker_map": speakers,
        "safety": report["safety"],
    }
    write_json(out_dir / "transcript.rich.shadow.json", rich)
    (out_dir / "transcript.rich.shadow.md").write_text(
        transcript_markdown(utterances, report["source"]["profile"], report["decision"]),
        encoding="utf-8",
    )
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(out_dir / "artifact_manifest.json", output_manifest(out_dir, session.name))


def aggregate_fallback(
    session: Path,
    out_dir: Path,
    dialogue_path: Path | None,
    profile: str,
    source: dict[str, Any],
    reason: str,
    model: dict[str, Any] | None = None,
) -> dict[str, Any]:
    utterances: list[dict[str, Any]] = []
    if dialogue_path and dialogue_path.is_file():
        payload = read_json(dialogue_path)
        utterances = deepcopy(payload.get("utterances") or payload.get("dialogue") or [])
    attributions: list[dict[str, Any]] = []
    for utterance in utterances:
        if utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        text = str(utterance.get("text") or "")
        utterance["speaker_turns"] = [
            {
                "speaker_id": None,
                "speaker_label": "Colleagues",
                "status": "unknown",
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or 0),
                "start_char": 0,
                "end_char": len(text),
                "text": text,
                "word_ids": [],
            }
        ]
        attributions.append(
            {
                "schema": UTTERANCE_SCHEMA,
                "utterance_id": str(utterance["id"]),
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or 0),
                "speaker_id": None,
                "status": "aggregate",
                "reason": reason,
                "speaker_turns": utterance["speaker_turns"],
            }
        )
    gates = {
        "inputs_current": False,
        "backend_ready": False,
        "seed_map_ready": False,
        "raw_word_timestamps_ready": False,
        "word_conservation": True,
        "timestamp_order": True,
        "publish_session_evidence": False,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "status": "fallback",
        "decision": "FALLBACK_AGGREGATE",
        "reasons": [reason],
        "source": {"session_id": session.name, "profile": profile, **source},
        "model": model or {},
        "implementation": implementation_provenance(),
        "parameters": {},
        "summary": {
            "remote_utterances": len(attributions),
            "remote_words": 0,
            "attributed_words": 0,
            "unknown_words": 0,
            "remote_speech_sec": round(
                sum(float(row["end"]) - float(row["start"]) for row in attributions), 6
            ),
            "attributed_speech_sec": 0.0,
            "attributable_remote_speech_ratio": 0.0,
            "published_speakers": 0,
            "internal_change_utterances": 0,
            "unknown_overlap_words": 0,
        },
        "gates": gates,
        "safety": {
            "plain_transcript_unchanged": True,
            "selected_text_unchanged": True,
            "me_unchanged": True,
            "session_local_anonymous_only": True,
            "identity_inference": False,
            "external_writes": False,
        },
    }
    finalize_outputs(session, out_dir, report, utterances, [], [], attributions, [])
    return report


def verify_promotion(report: dict[str, Any]) -> bool:
    if not PROMOTION_POLICY.is_file():
        return False
    try:
        policy = read_json(PROMOTION_POLICY)
        manifest_row = policy.get("corpus_manifest") or {}
        manifest_path = ROOT / str(manifest_row["path"])
        manifest = read_json(manifest_path)
        implementation = manifest.get("implementation") or {}
        audit = implementation.get("audit") or {}
        report_script = (report.get("implementation") or {}).get("script") or {}
        return (
            policy.get("schema") == "murmurmark.remote_speaker_diarization_policy/v2"
            and policy.get("state") == "promoted"
            and manifest.get("schema") == "murmurmark.remote_speaker_diarization_frozen_manifest/v2"
            and manifest.get("decision") == "PROMOTE"
            and manifest_row.get("sha256") == sha256(manifest_path)
            and audit.get("sha256") == sha256(Path(__file__).resolve())
            and report_script.get("sha256") == audit.get("sha256")
        )
    except Exception:
        return False


def verify_existing(session: Path, out_dir: Path, require_promoted: bool = False) -> int:
    report_path = out_dir / "report.json"
    manifest_path = out_dir / "artifact_manifest.json"
    if not report_path.is_file() or not manifest_path.is_file():
        print("remote_diarization: missing output")
        return 2
    try:
        report = read_json(report_path)
        manifest = read_json(manifest_path)
        valid = (
            report.get("schema") == REPORT_SCHEMA
            and report.get("decision") == "PUBLISH_EVIDENCE"
            and manifest.get("schema") == MANIFEST_SCHEMA
            and all(
                (out_dir / name).is_file() and sha256(out_dir / name) == digest
                for name, digest in (manifest.get("artifacts") or {}).items()
            )
        )
        source = report.get("source") or {}
        for key in ("dialogue", "remote_audio", "raw_remote_json", "v1_report", "v1_attribution"):
            row = source.get(key)
            if not isinstance(row, dict) or not row.get("path"):
                valid = False
                continue
            path = Path(str(row["path"]))
            if not path.is_absolute():
                path = session / path
            valid = valid and same_fingerprint(row, path)
        if require_promoted:
            valid = valid and verify_promotion(report)
    except Exception:
        valid = False
    print(f"remote_diarization: verify={'ok' if valid else 'stale_or_invalid'}")
    return 0 if valid else 2


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = session / out_dir
    if args.verify_only:
        return verify_existing(session, out_dir, args.require_promoted)
    out_dir.mkdir(parents=True, exist_ok=True)

    v1_dir = Path(args.v1_dir)
    if not v1_dir.is_absolute():
        v1_dir = session / v1_dir
    v1_report_path = v1_dir / "report.json"
    v1_attribution_path = v1_dir / "utterance_attribution.jsonl"
    source: dict[str, Any] = {
        "v1_report": fingerprint(v1_report_path, session),
        "v1_attribution": fingerprint(v1_attribution_path, session),
    }
    if not v1_report_path.is_file() or not v1_attribution_path.is_file():
        report = aggregate_fallback(
            session, out_dir, None, args.profile, source, "v1_speaker_evidence_missing"
        )
        print(f"remote_diarization: {report['decision']} (v1_speaker_evidence_missing)")
        return 0

    try:
        v1_report = read_json(v1_report_path)
        if (
            v1_report.get("schema") != V1_REPORT_SCHEMA
            or v1_report.get("decision") != "PUBLISH_AUDIT_EVIDENCE"
        ):
            raise ValueError("v1_speaker_evidence_not_publishable")
        v1_source = v1_report["source"]
        profile = str(v1_source["profile"])
        if args.profile != "auto" and args.profile != profile:
            raise ValueError("requested_profile_differs_from_v1")
        dialogue_path = session / str(v1_source["dialogue"]["path"])
        audio_path = session / str(v1_source["remote_audio"]["path"])
        raw_remote_path = session / RAW_REMOTE_JSON
        source.update(
            {
                "session_id": session.name,
                "profile": profile,
                "dialogue": fingerprint(dialogue_path, session),
                "remote_audio": fingerprint(audio_path, session),
                "raw_remote_json": fingerprint(raw_remote_path, session),
            }
        )
        current = (
            same_fingerprint(v1_source.get("dialogue"), dialogue_path)
            and same_fingerprint(v1_source.get("remote_audio"), audio_path)
        )
        if not current:
            raise ValueError("v1_source_stale")
        if not raw_remote_path.is_file():
            raise ValueError("raw_remote_word_timestamps_missing")
        dialogue_payload = read_json(dialogue_path)
        utterances = deepcopy(dialogue_payload.get("utterances") or dialogue_payload.get("dialogue") or [])
        if not isinstance(utterances, list):
            raise ValueError("selected_dialogue_invalid")
        raw_words = raw_whisper_words(read_json(raw_remote_path))
        if not raw_words:
            raise ValueError("raw_remote_word_timestamps_empty")
        v1_rows = read_jsonl(v1_attribution_path)
        if any(row.get("schema") != V1_ATTRIBUTION_SCHEMA for row in v1_rows):
            raise ValueError("v1_attribution_schema_invalid")
    except Exception as error:
        reason = str(error) if str(error) else type(error).__name__
        dialogue_guess = None
        try:
            candidate = session / str((v1_report.get("source") or {}).get("dialogue", {}).get("path"))
            dialogue_guess = candidate if candidate.is_file() else None
        except Exception:
            pass
        report = aggregate_fallback(
            session,
            out_dir,
            dialogue_guess,
            str((v1_report.get("source") or {}).get("profile") or args.profile),
            source,
            reason,
        )
        print(f"remote_diarization: {report['decision']} ({reason})")
        return 0

    backend = EmbeddingBackend(args)
    if backend.status != "ready":
        report = aggregate_fallback(
            session,
            out_dir,
            dialogue_path,
            profile,
            source,
            backend.reason or "speaker_backend_unavailable",
            backend.provenance,
        )
        print(f"remote_diarization: {report['decision']} ({report['reasons'][0]})")
        return 0

    remote = [row for row in utterances if row.get("role") == "remote" and row.get("id")]
    remote_by_id = {str(row["id"]): row for row in remote}
    v1_by_id = {str(row["utterance_id"]): row for row in v1_rows if row.get("utterance_id")}
    seed_values: dict[str, list[np.ndarray]] = defaultdict(list)
    seed_failures: dict[str, str] = {}
    audio: sf.SoundFile | None = None
    try:
        if backend.fixture is None:
            audio = sf.SoundFile(str(audio_path))
        seed_rows = [row for row in v1_rows if row.get("speaker_id") in {s.get("speaker_id") for s in (read_json(v1_dir / "speaker_map.json").get("speakers") or [])}]
        for index, row in enumerate(seed_rows, start=1):
            utterance = remote_by_id.get(str(row["utterance_id"]))
            if not utterance:
                continue
            start, end = source_interval(utterance)
            key = f"seed:{utterance['id']}"
            try:
                seed_values[str(row["speaker_id"])].append(backend.embed(audio, key, start, end))
            except Exception as error:
                seed_failures[str(utterance["id"])] = type(error).__name__
            if index % 50 == 0:
                progress(args, f"seeded {index}/{len(seed_rows)} v1 units")
        centroids = {
            speaker: normalize(np.mean(values, axis=0))
            for speaker, values in seed_values.items()
            if values
        }
        if not centroids:
            raise ValueError("no_seed_centroids")

        frame_rows: list[dict[str, Any]] = []
        words_by_utterance: dict[str, list[dict[str, Any]]] = {}
        frame_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
        whole_by_utterance: dict[str, dict[str, Any]] = {}
        for utterance_index, utterance in enumerate(remote, start=1):
            uid = str(utterance["id"])
            selected_words = align_selected_words(utterance, raw_words)
            for word_index, word in enumerate(selected_words, start=1):
                word.update(
                    {
                        "schema": WORD_SCHEMA,
                        "word_id": f"{uid}:word:{word_index:04d}",
                        "utterance_id": uid,
                        "speaker_id": None,
                        "speaker_label": "Colleagues",
                        "status": "unknown",
                        "reason": "speaker_evidence_pending",
                        "confidence": {"similarity": None, "margin": None},
                        "frame_ids": [],
                    }
                )
            words_by_utterance[uid] = selected_words
            start, end = source_interval(utterance)
            windows = analysis_windows(start, end, args)
            for frame_index, (left, right) in enumerate(windows, start=1):
                frame_id = f"{uid}:frame:{frame_index:04d}"
                try:
                    embedding = backend.embed(audio, f"frame:{uid}:{frame_index:04d}", left, right)
                    result = classify(embedding, centroids, args.min_similarity, args.min_margin)
                    reason = "seed_centroid_match" if result["speaker_id"] else "weak_or_ambiguous_frame"
                except Exception as error:
                    result = {"speaker_id": None, "similarity": None, "margin": None, "scores": {}}
                    reason = f"frame_embedding_failed:{type(error).__name__}"
                frame = {
                    "schema": FRAME_SCHEMA,
                    "frame_id": frame_id,
                    "utterance_id": uid,
                    "start": round(left, 6),
                    "end": round(right, 6),
                    "speaker_id": result["speaker_id"],
                    "speaker_label": result["speaker_id"] or "Colleagues",
                    "status": "attributed" if result["speaker_id"] else "unknown",
                    "reason": reason,
                    "confidence": {
                        "similarity": result["similarity"],
                        "margin": result["margin"],
                        "speaker_scores": result["scores"],
                    },
                }
                frame_rows.append(frame)
                frame_by_utterance[uid].append(frame)

            try:
                whole_embedding = backend.embed(audio, f"whole:{uid}", start, end)
                whole_by_utterance[uid] = classify(
                    whole_embedding, centroids, args.strict_similarity, args.strict_margin
                )
            except Exception as error:
                whole_by_utterance[uid] = {
                    "speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "scores": {},
                    "reason": f"whole_embedding_failed:{type(error).__name__}",
                }
            if utterance_index % 50 == 0 or utterance_index == len(remote):
                progress(args, f"analyzed {utterance_index}/{len(remote)} remote utterances")
    except Exception as error:
        if audio is not None:
            audio.close()
        report = aggregate_fallback(
            session, out_dir, dialogue_path, profile, source, str(error), backend.provenance
        )
        print(f"remote_diarization: {report['decision']} ({report['reasons'][0]})")
        return 0
    finally:
        if audio is not None:
            audio.close()

    # Initial word decisions combine local frame evidence with the conservative
    # v1 assignment. A minor v1 cluster may only recover through strict whole-
    # utterance evidence; long utterances rely on frame agreement.
    for utterance in remote:
        uid = str(utterance["id"])
        words = words_by_utterance[uid]
        frames = frame_by_utterance[uid]
        v1 = v1_by_id.get(uid, {})
        v1_speaker = v1.get("speaker_id")
        v1_reason = str(v1.get("reason") or "v1_unknown")
        frame_speakers = {str(row["speaker_id"]) for row in frames if row.get("speaker_id")}
        whole = whole_by_utterance[uid]
        strict_speaker = whole.get("speaker_id")
        for word in words:
            midpoint = (float(word["start"]) + float(word["end"])) / 2
            related = [row for row in frames if float(row["start"]) <= midpoint <= float(row["end"])]
            votes: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for frame in related:
                if frame.get("speaker_id"):
                    votes[str(frame["speaker_id"])].append(frame)
            ranked = sorted(
                votes.items(),
                key=lambda item: (
                    len(item[1]),
                    sum(float(row["confidence"]["similarity"] or 0) for row in item[1]),
                    item[0],
                ),
                reverse=True,
            )
            chosen: str | None = None
            evidence: list[dict[str, Any]] = []
            reason = "weak_or_ambiguous_word_evidence"
            if ranked and (len(ranked) == 1 or len(ranked[0][1]) > len(ranked[1][1])):
                chosen, evidence = ranked[0]
                reason = "frame_consensus"
            elif v1_speaker and len(frame_speakers - {str(v1_speaker)}) == 0:
                chosen = str(v1_speaker)
                evidence = [row for row in related if row.get("speaker_id") == chosen]
                reason = "retained_v1_seed"
            elif strict_speaker and v1_reason not in {
                "possible_remote_double_talk",
                "source_needs_review",
            }:
                chosen = str(strict_speaker)
                reason = "strict_whole_utterance_match"
            if chosen:
                similarities = [float(row["confidence"]["similarity"]) for row in evidence if row["confidence"]["similarity"] is not None]
                margins = [float(row["confidence"]["margin"]) for row in evidence if row["confidence"]["margin"] is not None]
                word.update(
                    {
                        "speaker_id": chosen,
                        "speaker_label": chosen,
                        "status": "attributed",
                        "reason": reason,
                        "confidence": {
                            "similarity": round(float(np.mean(similarities)), 6) if similarities else whole.get("similarity"),
                            "margin": round(float(np.mean(margins)), 6) if margins else whole.get("margin"),
                        },
                        "frame_ids": [str(row["frame_id"]) for row in evidence],
                    }
                )
            else:
                word["reason"] = reason

    # Concurrent remote utterances with different or unsupported labels remain
    # unknown in the overlapping interval. A single-channel mix cannot prove
    # two simultaneous identities safely.
    unknown_overlap_words = 0
    for left_id, right_id, overlap_start, overlap_end in overlap_pairs(remote, args.overlap_min_sec):
        left_words = [
            row
            for row in words_by_utterance[left_id]
            if float(row["end"]) >= overlap_start and float(row["start"]) <= overlap_end
        ]
        right_words = [
            row
            for row in words_by_utterance[right_id]
            if float(row["end"]) >= overlap_start and float(row["start"]) <= overlap_end
        ]
        left_speakers = {row.get("speaker_id") for row in left_words if row.get("speaker_id")}
        right_speakers = {row.get("speaker_id") for row in right_words if row.get("speaker_id")}
        if left_speakers and left_speakers == right_speakers:
            continue
        for row in left_words + right_words:
            if row.get("speaker_id") is not None:
                unknown_overlap_words += 1
            row.update(
                {
                    "speaker_id": None,
                    "speaker_label": "Colleagues",
                    "status": "unknown",
                    "reason": "possible_remote_overlap",
                }
            )

    all_words: list[dict[str, Any]] = []
    attributions: list[dict[str, Any]] = []
    internal_changes = 0
    speaker_weights: Counter[str] = Counter()
    for utterance in remote:
        uid = str(utterance["id"])
        words = words_by_utterance[uid]
        turns = build_turns(str(utterance.get("text") or ""), words)
        utterance["speaker_turns"] = turns
        labelled = [turn for turn in turns if turn.get("speaker_id")]
        distinct = list(dict.fromkeys(str(turn["speaker_id"]) for turn in labelled))
        if len(distinct) > 1:
            internal_changes += 1
        weights = Counter()
        for word in words:
            if word.get("speaker_id"):
                weight = float(word["coverage_weight_sec"])
                weights[str(word["speaker_id"])] += weight
                speaker_weights[str(word["speaker_id"])] += weight
        total_weight = sum(float(row["coverage_weight_sec"]) for row in words)
        attributed_weight = sum(weights.values())
        # An utterance-level label is only a compatibility summary. Mixed turns
        # and partially covered utterances must not masquerade as one speaker;
        # their word-level turns remain available in the rich profile.
        dominant = weights.most_common(1)[0][0] if len(weights) == 1 else None
        if dominant and (not total_weight or attributed_weight / total_weight < 0.80):
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
                "reason": "word_level_evidence" if dominant else "insufficient_word_level_evidence",
                "speaker_turns": turns,
                "attributed_weight_sec": round(attributed_weight, 9),
                "total_weight_sec": round(total_weight, 9),
            }
        )
        all_words.extend(words)

    selected_text_unchanged = all(
        "".join(turn["text"] for turn in utterance.get("speaker_turns") or [])
        == str(utterance.get("text") or "")
        for utterance in remote
    )
    timestamp_order = all(
        all(
            source_interval(remote_by_id[uid])[0] <= float(word["start"]) <= float(word["end"]) <= source_interval(remote_by_id[uid])[1]
            and (index == 0 or float(words[index - 1]["start"]) <= float(word["start"]))
            for index, word in enumerate(words)
        )
        for uid, words in words_by_utterance.items()
    )
    remote_speech = sum(max(0.0, source_interval(row)[1] - source_interval(row)[0]) for row in remote)
    attributed_speech = sum(
        float(row["coverage_weight_sec"]) for row in all_words if row.get("speaker_id")
    )
    published_speakers = sorted(speaker_weights)
    speakers = [
        {
            "speaker_id": speaker,
            "speaker_label": speaker,
            "session_local": True,
            "display_name": None,
            "seed_units": len(seed_values.get(speaker, [])),
            "attributed_speech_sec": round(float(speaker_weights[speaker]), 6),
        }
        for speaker in published_speakers
    ]
    inputs_current = same_fingerprint(v1_report["source"]["dialogue"], dialogue_path) and same_fingerprint(
        v1_report["source"]["remote_audio"], audio_path
    )
    gates = {
        "inputs_current": inputs_current,
        "backend_ready": backend.status == "ready",
        "seed_map_ready": bool(centroids),
        "raw_word_timestamps_ready": bool(raw_words),
        "word_conservation": selected_text_unchanged,
        "timestamp_order": timestamp_order,
    }
    publish = all(gates.values())
    gates["publish_session_evidence"] = publish
    reasons = [key for key, value in gates.items() if key != "publish_session_evidence" and not value]
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "status": "completed" if publish else "fallback",
        "decision": "PUBLISH_EVIDENCE" if publish else "FALLBACK_AGGREGATE",
        "reasons": reasons,
        "source": source,
        "model": backend.provenance,
        "implementation": implementation_provenance(),
        "parameters": {
            "profile": "resemblyzer_seeded_frames_v1",
            "window_sec": args.window_sec,
            "stride_sec": args.stride_sec,
            "short_window_max_sec": args.short_window_max_sec,
            "min_similarity": args.min_similarity,
            "min_margin": args.min_margin,
            "strict_similarity": args.strict_similarity,
            "strict_margin": args.strict_margin,
            "overlap_min_sec": args.overlap_min_sec,
        },
        "summary": {
            "remote_utterances": len(remote),
            "remote_words": len(all_words),
            "attributed_words": sum(row.get("speaker_id") is not None for row in all_words),
            "unknown_words": sum(row.get("speaker_id") is None for row in all_words),
            "remote_speech_sec": round(remote_speech, 6),
            "attributed_speech_sec": round(attributed_speech, 6),
            "attributable_remote_speech_ratio": round(attributed_speech / remote_speech, 6) if remote_speech else 0.0,
            "published_speakers": len(speakers),
            "internal_change_utterances": internal_changes,
            "unknown_overlap_words": unknown_overlap_words,
            "frames": len(frame_rows),
            "attributed_frames": sum(row.get("speaker_id") is not None for row in frame_rows),
            "seed_embedding_failures": len(seed_failures),
        },
        "gates": gates,
        "safety": {
            "plain_transcript_unchanged": True,
            "selected_text_unchanged": selected_text_unchanged,
            "me_unchanged": True,
            "session_local_anonymous_only": True,
            "identity_inference": False,
            "external_writes": False,
            "raw_audio_unchanged": same_fingerprint(v1_report["source"]["remote_audio"], audio_path),
        },
    }
    if not publish:
        report = aggregate_fallback(
            session,
            out_dir,
            dialogue_path,
            profile,
            source,
            reasons[0] if reasons else "session_gate_failed",
            backend.provenance,
        )
    else:
        finalize_outputs(session, out_dir, report, utterances, frame_rows, all_words, attributions, speakers)
    print(
        f"remote_diarization: decision={report['decision']} "
        f"speakers={report['summary']['published_speakers']} "
        f"coverage={report['summary']['attributable_remote_speech_ratio']:.6f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
