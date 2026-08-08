#!/usr/bin/env python3
"""Build and grade a blind private reference pack for remote-speaker residuals."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.remote_speaker_residual_reference_policy/v1"
PACK_SCHEMA = "murmurmark.remote_speaker_residual_reference_pack/v1"
ITEM_SCHEMA = "murmurmark.remote_speaker_residual_reference_item/v1"
ANSWER_SCHEMA = "murmurmark.remote_speaker_residual_reference_answer/v1"
PREDICTION_SCHEMA = "murmurmark.remote_speaker_residual_sealed_prediction/v1"
EXEMPLAR_SCHEMA = "murmurmark.remote_speaker_residual_reference_exemplar/v1"
REPORT_SCHEMA = "murmurmark.remote_speaker_residual_reference_corpus_report/v1"
MANIFEST_SCHEMA = "murmurmark.remote_speaker_residual_reference_frozen_manifest/v1"
V3_REPORT_SCHEMA = "murmurmark.remote_speaker_coverage_report/v3"
INDEPENDENT_REPORT_SCHEMA = "murmurmark.independent_remote_speaker_evidence_report/v1"
DEFAULT_POLICY = ROOT / "policies/remote-speaker-residual-reference-corpus-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/remote-speaker-residual-reference-corpus-v1"
DEFAULT_TRACKED_MANIFEST = (
    ROOT / "docs/testing/remote-speaker-residual-reference-corpus-v1-manifest.json"
)
V3_DIR = Path("derived/audit/remote-speaker-coverage-v3")
INDEPENDENT_DIR = Path("derived/audit/independent-remote-speaker-evidence-v1")
SPEAKER_RE = re.compile(r"remote_speaker_\d{2,}")
ABSOLUTE_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")
PUBLIC_FORBIDDEN_KEYS = {
    "text",
    "transcript_fragment",
    "speaker_name",
    "display_name",
    "reviewer_id",
    "note",
}
BLIND_FORBIDDEN_KEYS = {
    "candidate_speaker_id",
    "candidate_speakers",
    "prediction",
    "wavlm_speaker_id",
}


class ReferenceCorpusError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ReferenceCorpusError(f"expected_json_object:{path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise ReferenceCorpusError(f"expected_jsonl_objects:{path}")
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical_json(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    payload = b"".join(compact_json(row) + b"\n" for row in rows)
    atomic_write(path, payload)


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError as error:
        raise ReferenceCorpusError(f"path_outside_repository:{path}") from error


def fingerprint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReferenceCorpusError(f"artifact_missing:{path}")
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def resolve_session_path(session: Path, row: Any) -> Path:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        raise ReferenceCorpusError("source_fingerprint_missing")
    candidate = Path(row["path"])
    path = candidate if candidate.is_absolute() else session / candidate
    resolved = path.resolve()
    try:
        resolved.relative_to(session.resolve())
    except ValueError as error:
        raise ReferenceCorpusError("source_path_outside_session") from error
    return resolved


def fingerprint_matches(row: Any, path: Path) -> bool:
    return bool(
        isinstance(row, dict)
        and path.is_file()
        and row.get("sha256") == sha256(path)
        and int(row.get("bytes") or -1) == path.stat().st_size
    )


def nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(nested_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(nested_keys(child))
    return keys


def assert_public_safe(value: Any) -> None:
    forbidden = nested_keys(value) & PUBLIC_FORBIDDEN_KEYS
    if forbidden:
        raise ReferenceCorpusError("public_private_keys:" + ",".join(sorted(forbidden)))
    rendered = canonical_json(value).decode("utf-8")
    if ABSOLUTE_PATH_RE.search(rendered):
        raise ReferenceCorpusError("public_absolute_path")


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise ReferenceCorpusError("policy_schema_invalid")
    sessions = list((policy.get("corpus") or {}).get("sessions") or [])
    if len(sessions) != len(set(sessions)) or not sessions:
        raise ReferenceCorpusError("policy_sessions_invalid")
    truth = policy.get("truth") or {}
    if set(truth.get("eligible_grades") or []) != {"human_reviewed", "exact_scripted"}:
        raise ReferenceCorpusError("policy_truth_grades_invalid")
    return policy


def verify_artifact_manifest(directory: Path, schema_prefix: str) -> dict[str, Any]:
    path = directory / "artifact_manifest.json"
    manifest = read_json(path)
    if not str(manifest.get("schema") or "").startswith(schema_prefix):
        raise ReferenceCorpusError(f"artifact_manifest_schema_invalid:{directory.name}")
    for name, expected in (manifest.get("artifacts") or {}).items():
        artifact = directory / str(name)
        if not artifact.is_file() or sha256(artifact) != expected:
            raise ReferenceCorpusError(f"artifact_manifest_stale:{directory.name}:{name}")
    return manifest


def session_inputs(session: Path) -> dict[str, Any]:
    v3 = session / V3_DIR
    independent = session / INDEPENDENT_DIR
    v3_report = read_json(v3 / "report.json")
    independent_report = read_json(independent / "report.json")
    if v3_report.get("schema") != V3_REPORT_SCHEMA or v3_report.get("decision") != "PUBLISH_EVIDENCE":
        raise ReferenceCorpusError(f"v3_not_publishable:{session.name}")
    if (
        independent_report.get("schema") != INDEPENDENT_REPORT_SCHEMA
        or independent_report.get("decision") != "PUBLISH_EVIDENCE"
    ):
        raise ReferenceCorpusError(f"independent_not_publishable:{session.name}")
    verify_artifact_manifest(v3, "murmurmark.remote_speaker_coverage_artifact_manifest/")
    verify_artifact_manifest(
        independent, "murmurmark.independent_remote_speaker_artifact_manifest/"
    )
    remote_audio = resolve_session_path(session, (v3_report.get("source") or {}).get("remote_audio"))
    if not fingerprint_matches((v3_report.get("source") or {}).get("remote_audio"), remote_audio):
        raise ReferenceCorpusError(f"remote_audio_stale:{session.name}")
    dialogue = resolve_session_path(session, (v3_report.get("source") or {}).get("dialogue"))
    if not fingerprint_matches((v3_report.get("source") or {}).get("dialogue"), dialogue):
        raise ReferenceCorpusError(f"selected_dialogue_stale:{session.name}")
    raw_paths = [session / "audio/mic/000001.caf", session / "audio/remote/000001.caf"]
    if any(not path.is_file() for path in raw_paths):
        raise ReferenceCorpusError(f"raw_audio_missing:{session.name}")
    words = read_jsonl(v3 / "word_attribution.jsonl")
    residual = [row for row in words if not row.get("speaker_id")]
    independent_decisions = read_jsonl(independent / "residual_decisions.jsonl")
    proposals = {
        str(row["word_id"]): row
        for row in independent_decisions
        if row.get("outcome") == "attributed" and row.get("speaker_id")
    }
    speaker_map = read_json(v3 / "speaker_map.json")
    speakers = sorted(
        str(row["speaker_id"])
        for row in speaker_map.get("speakers") or []
        if isinstance(row, dict) and SPEAKER_RE.fullmatch(str(row.get("speaker_id") or ""))
    )
    if not speakers:
        raise ReferenceCorpusError(f"speaker_map_empty:{session.name}")
    return {
        "session": session,
        "v3_dir": v3,
        "independent_dir": independent,
        "v3_report": v3_report,
        "independent_report": independent_report,
        "remote_audio": remote_audio,
        "dialogue": dialogue,
        "raw_paths": raw_paths,
        "words": words,
        "residual": residual,
        "proposals": proposals,
        "speakers": speakers,
    }


def group_residual_words(rows: list[dict[str, Any]], policy: dict[str, Any]) -> list[list[dict[str, Any]]]:
    settings = policy["review_pack"]
    join_gap = float(settings["join_gap_sec"])
    target_duration = float(settings["target_item_sec"])
    by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    order: list[str] = []
    for row in rows:
        uid = str(row["utterance_id"])
        if uid not in by_utterance:
            order.append(uid)
        by_utterance[uid].append(row)
    groups: list[list[dict[str, Any]]] = []
    for uid in order:
        current: list[dict[str, Any]] = []
        for row in sorted(by_utterance[uid], key=lambda item: (float(item["start"]), str(item["word_id"]))):
            start = float(row["start"])
            split = bool(
                current
                and (
                    start - float(current[-1]["end"]) > join_gap
                    or float(row["end"]) - float(current[0]["start"]) > target_duration
                )
            )
            if split:
                groups.append(current)
                current = []
            current.append(row)
        if current:
            groups.append(current)
    return groups


class AudioSlicer:
    def __init__(self, source: Path):
        self.source = source
        self.handle = sf.SoundFile(source)
        self.sample_rate = int(self.handle.samplerate)
        self.frames = int(self.handle.frames)

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate

    def close(self) -> None:
        self.handle.close()

    def write(self, start: float, end: float, destination: Path, subtype: str) -> dict[str, Any]:
        bounded_start = max(0.0, min(float(start), self.duration))
        bounded_end = max(bounded_start + 1 / self.sample_rate, min(float(end), self.duration))
        start_frame = max(0, min(self.frames - 1, int(round(bounded_start * self.sample_rate))))
        end_frame = max(start_frame + 1, min(self.frames, int(round(bounded_end * self.sample_rate))))
        self.handle.seek(start_frame)
        audio = self.handle.read(end_frame - start_frame, dtype="float32", always_2d=True)
        if audio.shape[1] > 1:
            audio = np.mean(audio, axis=1, keepdims=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        sf.write(destination, audio, self.sample_rate, subtype=subtype, format="WAV")
        return {
            "path": portable(destination),
            "start": round(start_frame / self.sample_rate, 6),
            "end": round(end_frame / self.sample_rate, 6),
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
        }


def speaker_runs(words: list[dict[str, Any]], join_gap: float) -> dict[str, list[list[dict[str, Any]]]]:
    runs: dict[str, list[list[dict[str, Any]]]] = defaultdict(list)
    current: list[dict[str, Any]] = []
    current_speaker: str | None = None
    current_utterance: str | None = None
    for row in words:
        speaker = str(row.get("speaker_id") or "")
        utterance = str(row.get("utterance_id") or "")
        contiguous = bool(
            current
            and speaker == current_speaker
            and utterance == current_utterance
            and float(row["start"]) - float(current[-1]["end"]) <= join_gap
        )
        if not contiguous:
            if current and current_speaker:
                runs[current_speaker].append(current)
            current = []
        if speaker:
            current.append(row)
            current_speaker = speaker
            current_utterance = utterance
        else:
            current_speaker = None
            current_utterance = None
    if current and current_speaker:
        runs[current_speaker].append(current)
    return runs


def choose_exemplars(data: dict[str, Any], policy: dict[str, Any]) -> dict[str, list[list[dict[str, Any]]]]:
    settings = policy["review_pack"]
    minimum = float(settings["minimum_exemplar_sec"])
    count = int(settings["exemplars_per_speaker"])
    candidates = speaker_runs(data["words"], float(settings["join_gap_sec"]))
    selected: dict[str, list[list[dict[str, Any]]]] = {}
    for speaker in data["speakers"]:
        valid = [
            run for run in candidates.get(speaker, [])
            if float(run[-1]["end"]) - float(run[0]["start"]) >= minimum
        ]
        valid.sort(
            key=lambda run: (
                -(float(run[-1]["end"]) - float(run[0]["start"])),
                str(run[0]["utterance_id"]),
                float(run[0]["start"]),
            )
        )
        chosen: list[list[dict[str, Any]]] = []
        seen_utterances: set[str] = set()
        for run in valid:
            uid = str(run[0]["utterance_id"])
            if uid in seen_utterances and len(valid) > count:
                continue
            chosen.append(run)
            seen_utterances.add(uid)
            if len(chosen) == count:
                break
        if len(chosen) < count:
            raise ReferenceCorpusError(f"speaker_exemplars_insufficient:{data['session'].name}:{speaker}")
        selected[speaker] = chosen
    return selected


def item_id(session_id: str, word_ids: list[str]) -> str:
    digest = sha256_bytes((session_id + "\0" + "\0".join(word_ids)).encode())[:16]
    return f"rrr_{digest}"


def build_private_pack(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    private = args.out_dir / "private"
    clips_root = private / "clips"
    exemplars_root = private / "exemplars"
    settings = policy["review_pack"]
    padding = float(settings["clip_padding_sec"])
    subtype = str(settings["audio_subtype"])
    session_data = [session_inputs(args.sessions_root / session_id) for session_id in policy["corpus"]["sessions"]]
    before_raw = {
        portable(path): fingerprint(path)
        for data in session_data
        for path in data["raw_paths"]
    }
    review_items: list[dict[str, Any]] = []
    sealed: list[dict[str, Any]] = []
    exemplars: list[dict[str, Any]] = []
    session_sources: list[dict[str, Any]] = []
    exact_scripted = set(policy["truth"].get("exact_scripted_sessions") or [])

    for data in session_data:
        session = data["session"]
        slicer = AudioSlicer(data["remote_audio"])
        try:
            session_exemplars = choose_exemplars(data, policy)
            for speaker, runs in sorted(session_exemplars.items()):
                for index, run in enumerate(runs, 1):
                    start = float(run[0]["start"])
                    end = min(
                        float(run[-1]["end"]),
                        start + float(settings["maximum_exemplar_sec"]),
                    )
                    path = exemplars_root / session.name / speaker / f"{index:02d}.wav"
                    audio = slicer.write(start, end, path, subtype)
                    exemplars.append(
                        {
                            "schema": EXEMPLAR_SCHEMA,
                            "session_id": session.name,
                            "speaker_id": speaker,
                            "utterance_id": str(run[0]["utterance_id"]),
                            "audio": audio,
                        }
                    )

            for group in group_residual_words(data["residual"], policy):
                word_ids = [str(row["word_id"]) for row in group]
                identifier = item_id(session.name, word_ids)
                path = clips_root / session.name / f"{identifier}.wav"
                audio = slicer.write(
                    float(group[0]["start"]) - padding,
                    float(group[-1]["end"]) + padding,
                    path,
                    subtype,
                )
                allowed_grades = ["human_reviewed"]
                if session.name in exact_scripted:
                    allowed_grades.append("exact_scripted")
                item = {
                    "schema": ITEM_SCHEMA,
                    "item_id": identifier,
                    "session_id": session.name,
                    "utterance_id": str(group[0]["utterance_id"]),
                    "word_ids": word_ids,
                    "word_count": len(word_ids),
                    "start": round(float(group[0]["start"]), 6),
                    "end": round(float(group[-1]["end"]), 6),
                    "coverage_weight_sec": round(
                        sum(float(row.get("coverage_weight_sec") or 0) for row in group), 9
                    ),
                    "baseline_causes": sorted(
                        {str(row.get("v3_reason") or row.get("reason") or "unknown") for row in group}
                    ),
                    "transcript_fragment": "".join(str(row.get("text") or "") for row in group),
                    "speaker_choices": data["speakers"],
                    "truth_grades_allowed": allowed_grades,
                    "audio": audio,
                }
                item["item_sha256"] = sha256_bytes(compact_json(item))
                review_items.append(item)
                for row in group:
                    proposal = data["proposals"].get(str(row["word_id"]))
                    if proposal:
                        sealed.append(
                            {
                                "schema": PREDICTION_SCHEMA,
                                "item_id": identifier,
                                "item_sha256": item["item_sha256"],
                                "session_id": session.name,
                                "word_id": str(row["word_id"]),
                                "candidate_speaker_id": str(proposal["speaker_id"]),
                                "coverage_weight_sec": float(row.get("coverage_weight_sec") or 0),
                                "source_reason": str(proposal.get("reason") or ""),
                            }
                        )
        finally:
            slicer.close()
        session_sources.append(
            {
                "session_id": session.name,
                "v3_report": fingerprint(data["v3_dir"] / "report.json"),
                "v3_manifest": fingerprint(data["v3_dir"] / "artifact_manifest.json"),
                "independent_report": fingerprint(data["independent_dir"] / "report.json"),
                "independent_manifest": fingerprint(data["independent_dir"] / "artifact_manifest.json"),
                "remote_audio": fingerprint(data["remote_audio"]),
                "selected_dialogue": fingerprint(data["dialogue"]),
                "raw_audio": [fingerprint(path) for path in data["raw_paths"]],
            }
        )

    review_items.sort(key=lambda row: (row["session_id"], row["start"], row["item_id"]))
    sealed.sort(key=lambda row: (row["session_id"], row["word_id"]))
    exemplars.sort(key=lambda row: (row["session_id"], row["speaker_id"], row["audio"]["start"]))
    if nested_keys(review_items) & BLIND_FORBIDDEN_KEYS:
        raise ReferenceCorpusError("blind_review_items_contain_prediction")
    review_path = private / "review_items.jsonl"
    sealed_path = private / "sealed_predictions.jsonl"
    exemplar_path = private / "speaker_exemplars.jsonl"
    answers_path = private / "answers.jsonl"
    write_jsonl(review_path, review_items)
    write_jsonl(sealed_path, sealed)
    write_jsonl(exemplar_path, exemplars)

    previous = {str(row.get("item_id")): row for row in read_jsonl(answers_path)} if answers_path.is_file() else {}
    answers: list[dict[str, Any]] = []
    for item in review_items:
        old = previous.get(item["item_id"])
        if isinstance(old, dict) and old.get("item_sha256") == item["item_sha256"]:
            answers.append(old)
        else:
            answers.append(
                {
                    "schema": ANSWER_SCHEMA,
                    "item_id": item["item_id"],
                    "item_sha256": item["item_sha256"],
                    "outcome": None,
                    "truth_grade": None,
                    "reviewer_id": None,
                    "reviewed_at": None,
                    "note": None,
                }
            )
    write_jsonl(answers_path, answers)
    after_raw = {portable(path): fingerprint(path) for data in session_data for path in data["raw_paths"]}
    if before_raw != after_raw:
        raise ReferenceCorpusError("raw_audio_changed_during_pack_build")

    pack = {
        "schema": PACK_SCHEMA,
        "version": VERSION,
        "implementation": fingerprint(Path(__file__).resolve()),
        "policy": fingerprint(args.policy),
        "sessions": session_sources,
        "artifacts": {
            "review_items": fingerprint(review_path),
            "sealed_predictions": fingerprint(sealed_path),
            "speaker_exemplars": fingerprint(exemplar_path),
            "answers": fingerprint(answers_path),
        },
        "summary": {
            "sessions": len(session_data),
            "review_items": len(review_items),
            "residual_words": sum(int(row["word_count"]) for row in review_items),
            "referenceable_word_seconds": round(
                sum(float(row["coverage_weight_sec"]) for row in review_items), 6
            ),
            "residual_seconds": round(float(policy["corpus"]["expected_residual_seconds"]), 6),
            "unaligned_residual_seconds": round(
                float(policy["corpus"]["expected_residual_seconds"])
                - sum(float(row["coverage_weight_sec"]) for row in review_items),
                6,
            ),
            "wavlm_proposal_words": len(sealed),
            "wavlm_proposal_seconds": round(sum(float(row["coverage_weight_sec"]) for row in sealed), 6),
            "speaker_exemplars": len(exemplars),
        },
        "safety": {
            "blind_prediction_separation": True,
            "raw_audio_unchanged": True,
            "selected_transcript_unchanged": True,
            "private_root": portable(private),
        },
    }
    write_json(private / "pack.json", pack)
    return pack


def load_pack(args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    private = args.out_dir / "private"
    pack = read_json(private / "pack.json")
    if pack.get("schema") != PACK_SCHEMA:
        raise ReferenceCorpusError("pack_schema_invalid")
    for row in (pack.get("artifacts") or {}).values():
        path = ROOT / str(row["path"])
        if not fingerprint_matches(row, path):
            raise ReferenceCorpusError(f"pack_artifact_stale:{path.name}")
    for session in pack.get("sessions") or []:
        for key in (
            "v3_report",
            "v3_manifest",
            "independent_report",
            "independent_manifest",
            "remote_audio",
            "selected_dialogue",
        ):
            row = session[key]
            if not fingerprint_matches(row, ROOT / row["path"]):
                raise ReferenceCorpusError(f"frozen_source_stale:{session['session_id']}:{key}")
        for row in session["raw_audio"]:
            if not fingerprint_matches(row, ROOT / row["path"]):
                raise ReferenceCorpusError(f"raw_audio_stale:{session['session_id']}")
    review_items = read_jsonl(private / "review_items.jsonl")
    sealed = read_jsonl(private / "sealed_predictions.jsonl")
    exemplars = read_jsonl(private / "speaker_exemplars.jsonl")
    answers = read_jsonl(private / "answers.jsonl")
    return pack, review_items, sealed, exemplars, answers


def valid_answers(
    items: list[dict[str, Any]], answers: list[dict[str, Any]], policy: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    item_by_id = {str(row["item_id"]): row for row in items}
    if len(item_by_id) != len(items):
        raise ReferenceCorpusError("review_item_ids_duplicate")
    eligible = set(policy["truth"]["eligible_grades"])
    result: dict[str, dict[str, Any]] = {}
    for answer in answers:
        identifier = str(answer.get("item_id") or "")
        item = item_by_id.get(identifier)
        if item is None or answer.get("item_sha256") != item.get("item_sha256"):
            continue
        outcome = answer.get("outcome")
        grade = answer.get("truth_grade")
        if not outcome or grade not in eligible or grade not in item["truth_grades_allowed"]:
            continue
        allowed = set(item["speaker_choices"]) | set(policy["truth"]["special_outcomes"])
        if outcome not in allowed:
            continue
        result[identifier] = answer
    return result


def build_report(
    pack: dict[str, Any],
    items: list[dict[str, Any]],
    sealed: list[dict[str, Any]],
    answers: list[dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    accepted = valid_answers(items, answers, policy)
    items_by_id = {str(row["item_id"]): row for row in items}
    specials = set(policy["truth"]["special_outcomes"])
    reviewed_items = len(accepted)
    directly_resolved_items = sum(
        answer["outcome"] not in {"mixed", "unusable"} for answer in accepted.values()
    )
    proposal_rows: list[dict[str, Any]] = []
    for prediction in sealed:
        item = items_by_id[str(prediction["item_id"])]
        answer = accepted.get(str(prediction["item_id"]))
        outcome = answer.get("outcome") if answer else None
        reviewed = answer is not None
        direct = bool(reviewed and outcome not in {"mixed", "unusable"})
        attributable = bool(direct and outcome not in specials)
        correct = bool(attributable and outcome == prediction["candidate_speaker_id"])
        proposal_rows.append(
            {
                "session_id": item["session_id"],
                "reviewed": reviewed,
                "direct": direct,
                "attributable": attributable,
                "correct": correct,
                "incorrect": bool(direct and not correct),
            }
        )
    reviewed_proposals = sum(row["reviewed"] for row in proposal_rows)
    direct_proposals = sum(row["direct"] for row in proposal_rows)
    attributable_proposals = sum(row["attributable"] for row in proposal_rows)
    correct_proposals = sum(row["correct"] for row in proposal_rows)
    incorrect_proposals = sum(row["incorrect"] for row in proposal_rows)
    precision = correct_proposals / direct_proposals if direct_proposals else None
    corpus = policy["corpus"]
    readiness = policy["readiness"]
    summary = pack["summary"]
    item_word_ids = [
        f"{item['session_id']}:{word_id}"
        for item in items
        for word_id in item["word_ids"]
    ]
    gates = {
        "six_session_scope": int(summary["sessions"]) == len(corpus["sessions"]) == 6,
        "review_item_count_exact": int(summary["review_items"])
        == int(corpus["expected_review_items"]),
        "all_residual_words_once": (
            len(item_word_ids) == len(set(item_word_ids)) == int(corpus["expected_residual_words"])
        ),
        "residual_scope_seconds_exact": abs(
            float(summary["residual_seconds"]) - float(corpus["expected_residual_seconds"])
        ) <= 1e-6,
        "referenceable_word_seconds_exact": abs(
            float(summary["referenceable_word_seconds"])
            - float(corpus["expected_referenceable_word_seconds"])
        ) <= 1e-6,
        "unaligned_residual_seconds_accounted": abs(
            float(summary["unaligned_residual_seconds"])
            - float(corpus["expected_unaligned_residual_seconds"])
        ) <= 1e-6,
        "wavlm_proposal_words_exact": len(sealed) == int(corpus["expected_wavlm_proposal_words"]),
        "wavlm_proposal_seconds_exact": abs(
            float(summary["wavlm_proposal_seconds"])
            - float(corpus["expected_wavlm_proposal_seconds"])
        ) <= 1e-6,
        "blind_prediction_separation": not bool(nested_keys(items) & BLIND_FORBIDDEN_KEYS),
        "reviewed_all_proposals": reviewed_proposals
        >= int(readiness["required_reviewed_proposal_words"]),
        "direct_reference_all_proposals": direct_proposals
        >= int(readiness["required_direct_reference_proposal_words"]),
        "minimum_attributable_proposals": attributable_proposals
        >= int(readiness["minimum_attributable_proposal_words"]),
        "candidate_precision": precision is not None
        and precision >= float(readiness["minimum_candidate_precision"]),
        "raw_audio_unchanged": bool(pack["safety"]["raw_audio_unchanged"]),
        "selected_transcript_unchanged": bool(pack["safety"]["selected_transcript_unchanged"]),
    }
    decision = "REFERENCE_READY" if all(gates.values()) else "REFERENCE_INSUFFICIENT"
    by_session: list[dict[str, Any]] = []
    for session_id in corpus["sessions"]:
        session_items = [row for row in items if row["session_id"] == session_id]
        session_proposals = [row for row in proposal_rows if row["session_id"] == session_id]
        by_session.append(
            {
                "session_id": session_id,
                "review_items": len(session_items),
                "residual_words": sum(int(row["word_count"]) for row in session_items),
                "residual_seconds": round(
                    sum(float(row["coverage_weight_sec"]) for row in session_items), 6
                ),
                "proposal_words": len(session_proposals),
                "reviewed_proposal_words": sum(row["reviewed"] for row in session_proposals),
                "direct_reference_proposal_words": sum(row["direct"] for row in session_proposals),
            }
        )
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "summary": {
            **summary,
            "reviewed_items": reviewed_items,
            "directly_resolved_items": directly_resolved_items,
            "unreviewed_items": len(items) - reviewed_items,
            "reviewed_proposal_words": reviewed_proposals,
            "direct_reference_proposal_words": direct_proposals,
            "attributable_proposal_words": attributable_proposals,
            "candidate_correct_words": correct_proposals,
            "candidate_incorrect_words": incorrect_proposals,
            "candidate_precision": round(precision, 6) if precision is not None else None,
        },
        "gates": gates,
        "failed_gates": [name for name, passed in gates.items() if not passed],
        "sessions": by_session,
        "truth_contract": {
            "eligible_grades": policy["truth"]["eligible_grades"],
            "machine_agreement_is_truth": False,
            "prediction_hidden_before_answer": True,
        },
        "private_artifacts": {
            "pack": f"{pack['safety']['private_root']}/pack.json",
            "speech_and_answers_tracked": False,
        },
        "safety": pack["safety"],
        "next_action": (
            "evaluate_constrained_open_set_remote_diarization"
            if decision == "REFERENCE_READY"
            else "complete_blind_private_review"
        ),
    }
    assert_public_safe(report)
    return report


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Remote Speaker Residual Reference Corpus v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Residual: `{summary['residual_words']}` words / `{summary['residual_seconds']:.3f}s`",
        f"Referenceable word intervals: `{summary['referenceable_word_seconds']:.3f}s`",
        f"Unaligned residual accounting: `{summary['unaligned_residual_seconds']:.3f}s`",
        f"Review items: `{summary['reviewed_items']}` / `{summary['review_items']}`",
        f"WavLM proposals reviewed: `{summary['reviewed_proposal_words']}` / `{summary['wavlm_proposal_words']}`",
        f"Direct candidate reference: `{summary['direct_reference_proposal_words']}` words",
        f"Attributable candidate reference: `{summary['attributable_proposal_words']}` words",
        f"Candidate precision: `{summary['candidate_precision']}`",
        "",
        "## Failed Gates",
        "",
    ]
    lines.extend(f"- `{name}`" for name in report["failed_gates"])
    if not report["failed_gates"]:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def current_manifest(
    args: argparse.Namespace, pack: dict[str, Any], report_path: Path
) -> dict[str, Any]:
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": read_json(report_path)["decision"],
        "implementation": fingerprint(Path(__file__).resolve()),
        "policy": fingerprint(args.policy),
        "report": fingerprint(report_path),
        "private_pack": fingerprint(args.out_dir / "private/pack.json"),
        "private_artifacts": (pack.get("artifacts") or {}),
        "sessions": [
            {
                "session_id": row["session_id"],
                "v3_report": row["v3_report"],
                "v3_manifest": row["v3_manifest"],
                "independent_report": row["independent_report"],
                "independent_manifest": row["independent_manifest"],
                "remote_audio": row["remote_audio"],
                "selected_dialogue": row["selected_dialogue"],
                "raw_audio": row["raw_audio"],
            }
            for row in pack["sessions"]
        ],
    }
    assert_public_safe(manifest)
    return manifest


def publish(args: argparse.Namespace, policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    loaded = load_pack(args)
    pack, items, sealed, _, answers = loaded
    report = build_report(pack, items, sealed, answers, policy)
    report_path = args.out_dir / "remote_speaker_residual_reference_report.json"
    markdown_path = args.out_dir / "remote_speaker_residual_reference_report.md"
    write_json(report_path, report)
    atomic_write(markdown_path, report_markdown(report).encode())
    manifest = current_manifest(args, pack, report_path)
    write_json(args.out_dir / "current_manifest.json", manifest)
    return report, manifest


def build(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    build_private_pack(args, policy)
    report, manifest = publish(args, policy)
    if args.write_manifest:
        write_json(args.write_manifest, manifest)
    if args.frozen_manifest:
        if read_json(args.frozen_manifest) != manifest:
            print("frozen reference manifest mismatch", file=sys.stderr)
            return 2
    print(f"decision: {report['decision']}")
    print(f"review_items: {report['summary']['review_items']}")
    print(f"reviewed_items: {report['summary']['reviewed_items']}")
    print("next: murmurmark corpus remote-reference next")
    return 0


def next_item(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    _, items, _, exemplars, answers = load_pack(args)
    accepted = valid_answers(items, answers, policy)
    selected = next(
        (
            item for item in items
            if item["item_id"] not in accepted
            and (not args.session_id or item["session_id"] == args.session_id)
        ),
        None,
    )
    if selected is None:
        print("review_queue: complete")
        return 0
    print(f"item_id: {selected['item_id']}")
    print(f"session_id: {selected['session_id']}")
    print(f"word_count: {selected['word_count']}")
    print(f"interval: {selected['start']:.3f}..{selected['end']:.3f}")
    print(f"clip: {selected['audio']['path']}")
    print(f"play: afplay {json.dumps(selected['audio']['path'])}")
    print("speaker_exemplars:")
    for speaker in selected["speaker_choices"]:
        examples = [
            row for row in exemplars
            if row["session_id"] == selected["session_id"] and row["speaker_id"] == speaker
        ]
        paths = " ".join(json.dumps(row["audio"]["path"]) for row in examples)
        print(f"  {speaker}: afplay {paths}")
    print("outcomes: " + " | ".join(selected["speaker_choices"] + policy["truth"]["special_outcomes"]))
    print(
        "grade: murmurmark corpus remote-reference grade "
        f"{selected['item_id']} --outcome <outcome> --truth-grade human_reviewed"
    )
    return 0


def grade(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    pack, items, _, _, answers = load_pack(args)
    item = next((row for row in items if row["item_id"] == args.item_id), None)
    if item is None:
        raise ReferenceCorpusError("review_item_not_found")
    allowed = set(item["speaker_choices"]) | set(policy["truth"]["special_outcomes"])
    if args.outcome not in allowed:
        raise ReferenceCorpusError("review_outcome_invalid")
    if args.truth_grade not in item["truth_grades_allowed"]:
        raise ReferenceCorpusError("truth_grade_not_allowed_for_item")
    reviewed_at = args.reviewed_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    replacement = {
        "schema": ANSWER_SCHEMA,
        "item_id": item["item_id"],
        "item_sha256": item["item_sha256"],
        "outcome": args.outcome,
        "truth_grade": args.truth_grade,
        "reviewer_id": args.reviewer_id,
        "reviewed_at": reviewed_at,
        "note": args.note,
    }
    updated = [replacement if row.get("item_id") == item["item_id"] else row for row in answers]
    write_jsonl(args.out_dir / "private/answers.jsonl", updated)
    pack["artifacts"]["answers"] = fingerprint(args.out_dir / "private/answers.jsonl")
    write_json(args.out_dir / "private/pack.json", pack)
    report, _ = publish(args, policy)
    print(f"graded: {item['item_id']}")
    print(f"decision: {report['decision']}")
    print(f"reviewed_items: {report['summary']['reviewed_items']}/{report['summary']['review_items']}")
    print("next: murmurmark corpus remote-reference next")
    return 0


def status(args: argparse.Namespace) -> int:
    path = args.out_dir / "remote_speaker_residual_reference_report.json"
    if not path.is_file():
        print("decision: MISSING")
        print("next: murmurmark corpus remote-reference build")
        return 2
    report = read_json(path)
    summary = report["summary"]
    print(f"decision: {report['decision']}")
    print(f"review_items: {summary['reviewed_items']}/{summary['review_items']}")
    print(f"proposal_words: {summary['reviewed_proposal_words']}/{summary['wavlm_proposal_words']}")
    print(f"direct_reference_words: {summary['direct_reference_proposal_words']}")
    print(f"candidate_precision: {summary['candidate_precision']}")
    print("next: murmurmark corpus remote-reference next")
    return 0


def replay(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    pack, items, sealed, _, answers = load_pack(args)
    expected_report = build_report(pack, items, sealed, answers, policy)
    report_path = args.out_dir / "remote_speaker_residual_reference_report.json"
    markdown_path = args.out_dir / "remote_speaker_residual_reference_report.md"
    stale: list[str] = []
    if not report_path.is_file() or report_path.read_bytes() != canonical_json(expected_report):
        stale.append(report_path.name)
    if not markdown_path.is_file() or markdown_path.read_bytes() != report_markdown(expected_report).encode():
        stale.append(markdown_path.name)
    if not stale:
        expected_manifest = current_manifest(args, pack, report_path)
        current_path = args.out_dir / "current_manifest.json"
        if not current_path.is_file() or read_json(current_path) != expected_manifest:
            stale.append(current_path.name)
        frozen = args.frozen_manifest
        if frozen and (not frozen.is_file() or read_json(frozen) != expected_manifest):
            stale.append(frozen.name)
    if stale:
        print("stale remote reference outputs: " + ", ".join(stale), file=sys.stderr)
        return 2
    print("replay: deterministic")
    print(f"decision: {expected_report['decision']}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="action", required=True)
    build_parser = subparsers.add_parser("build")
    next_parser = subparsers.add_parser("next")
    grade_parser = subparsers.add_parser("grade")
    status_parser = subparsers.add_parser("status")
    replay_parser = subparsers.add_parser("replay")
    grade_parser.add_argument("item_id")
    grade_parser.add_argument("--outcome", required=True)
    grade_parser.add_argument(
        "--truth-grade", choices=["human_reviewed", "exact_scripted"], required=True
    )
    grade_parser.add_argument("--reviewer-id", default="local_operator")
    grade_parser.add_argument("--reviewed-at")
    grade_parser.add_argument("--note")
    next_parser.add_argument("--session-id")
    for child in subparsers.choices.values():
        child.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
        child.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
        child.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    build_parser.add_argument("--write-manifest", type=Path)
    build_parser.add_argument("--frozen-manifest", type=Path)
    replay_parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_TRACKED_MANIFEST)
    return result


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    args.sessions_root = args.sessions_root.expanduser().resolve()
    if getattr(args, "write_manifest", None):
        args.write_manifest = args.write_manifest.expanduser().resolve()
    if getattr(args, "frozen_manifest", None):
        args.frozen_manifest = args.frozen_manifest.expanduser().resolve()
    policy = load_policy(args.policy)
    if args.action == "build":
        return build(args, policy)
    if args.action == "next":
        return next_item(args, policy)
    if args.action == "grade":
        return grade(args, policy)
    if args.action == "replay":
        return replay(args, policy)
    return status(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReferenceCorpusError, OSError, ValueError, json.JSONDecodeError, sf.LibsndfileError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
