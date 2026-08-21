#!/usr/bin/env python3
"""Build a blind, private human-reviewed lexical seed from real meetings."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
DEFAULT_POLICY = ROOT / "policies/human-reviewed-lexical-seed-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/human-reviewed-lexical-seed-v1"
LEXICAL_SCRIPT = ROOT / "scripts/report-lexical-accuracy-reference-corpus.py"

POLICY_SCHEMA = "murmurmark.human_reviewed_lexical_seed_policy/v1"
FREEZE_SCHEMA = "murmurmark.human_reviewed_lexical_seed_freeze/v1"
SLOT_SCHEMA = "murmurmark.human_reviewed_lexical_seed_slot/v1"
QUEUE_SCHEMA = "murmurmark.human_reviewed_lexical_seed_queue_slot/v1"
ANSWER_SCHEMA = "murmurmark.human_reviewed_lexical_seed_answer/v1"
PRIVATE_EVALUATION_SCHEMA = "murmurmark.human_reviewed_lexical_seed_private_evaluation/v1"
REPORT_SCHEMA = "murmurmark.human_reviewed_lexical_seed_report/v1"
SNAPSHOT_SCHEMA = "murmurmark.human_reviewed_lexical_seed_snapshot/v1"
ARTIFACT_SCHEMA = "murmurmark.human_reviewed_lexical_seed_artifacts/v1"

PRIVATE = Path("private")
FREEZE = PRIVATE / "frozen_input_manifest.json"
SLOTS = PRIVATE / "slots.jsonl"
QUEUE = PRIVATE / "review_queue.jsonl"
ANSWERS = PRIVATE / "answers.jsonl"
EVALUATION = PRIVATE / "evaluation.jsonl"
ARTIFACTS = PRIVATE / "artifact_manifest.json"
REPORT = Path("human_reviewed_lexical_seed_report.json")
REPORT_MD = Path("human_reviewed_lexical_seed_report.md")

SPECIAL_OUTCOMES = {"inaudible", "mixed", "unusable"}
WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
ABSOLUTE_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")


class SeedError(RuntimeError):
    pass


def load_lexical() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_human_lexical_metrics", LEXICAL_SCRIPT)
    if spec is None or spec.loader is None:
        raise SeedError("lexical_metrics_module_unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LEXICAL = load_lexical()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SeedError(f"json_object_required:{path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    write_jsonl(temporary, rows)
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def rank(salt: str, token: str) -> str:
    return hashlib.sha256(f"{salt}:{token}".encode()).hexdigest()


def portable(path: Path, workspace: Path) -> str:
    resolved = path.expanduser().resolve()
    for root in (workspace.resolve(), ROOT.resolve()):
        try:
            return str(resolved.relative_to(root))
        except ValueError:
            continue
    return str(resolved)


def fingerprint(path: Path, workspace: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "path": portable(resolved, workspace),
        "bytes": resolved.stat().st_size,
        "sha256": sha256(resolved),
    }


def fingerprint_path(row: dict[str, Any], workspace: Path) -> Path:
    path = Path(str(row.get("path") or "")).expanduser()
    if path.is_absolute():
        return path
    workspace_candidate = workspace / path
    root_candidate = ROOT / path
    return workspace_candidate if workspace_candidate.exists() else root_candidate


def fingerprint_current(row: Any, workspace: Path) -> bool:
    if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
        return False
    path = fingerprint_path(row, workspace)
    return (
        path.is_file()
        and row.get("bytes") == path.stat().st_size
        and row.get("sha256") == sha256(path)
    )


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise SeedError("policy_schema_invalid")
    sessions = policy.get("sessions") or []
    aliases = [str(row.get("alias") or "") for row in sessions]
    if not sessions or len(set(aliases)) != len(aliases) or any(not value for value in aliases):
        raise SeedError("policy_sessions_invalid")
    return policy


def source_from_selection(session: Path, row: Any) -> Path:
    if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
        raise SeedError(f"selection_source_invalid:{session.name}")
    path = session / str(row["path"])
    if (
        not path.is_file()
        or row.get("bytes") != path.stat().st_size
        or row.get("sha256") != sha256(path)
    ):
        raise SeedError(f"selection_source_stale:{session.name}:{path.name}")
    return path


def raw_audio(session: Path, session_json: dict[str, Any], role: str) -> Path:
    track = "mic" if role == "me" else "remote"
    rows = ((session_json.get("files") or {}).get(track) or [])
    if len(rows) != 1 or not rows[0].get("path"):
        raise SeedError(f"canonical_raw_track_required:{session.name}:{track}")
    path = session / str(rows[0]["path"])
    if not path.is_file():
        raise SeedError(f"raw_audio_missing:{session.name}:{track}")
    return path


def session_inputs(config: dict[str, Any], sessions_root: Path, workspace: Path) -> dict[str, Any]:
    session = sessions_root / str(config["session_id"])
    if not session.is_dir():
        raise SeedError(f"session_missing:{config['session_id']}")
    session_path = session / "session.json"
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    echo_path = session / "derived/preprocess/echo/echo_suppression_report.json"
    for path in (session_path, selection_path, echo_path):
        if not path.is_file():
            raise SeedError(f"required_source_missing:{path}")
    session_json = read_json(session_path)
    selection = read_json(selection_path)
    if (
        selection.get("state") != "selected"
        or not selection.get("batch_authoritative")
        or not (selection.get("gates") or {}).get("speaker_evidence_promoted")
    ):
        raise SeedError(f"speaker_resolved_selection_not_ready:{session.name}")
    dialogue = source_from_selection(session, selection.get("selected_dialogue"))
    coverage = source_from_selection(session, selection.get("coverage_report"))
    transcript = source_from_selection(session, selection.get("selected_transcript"))
    rich = source_from_selection(session, selection.get("rich_transcript"))
    coverage_report = read_json(coverage)
    echo = read_json(echo_path)
    speakers = int((coverage_report.get("summary") or {}).get("published_speakers") or 0)
    meeting_mode = str(config["meeting_mode"])
    if (meeting_mode == "1x1" and speakers != 1) or (meeting_mode == "group" and speakers < 2):
        raise SeedError(f"meeting_mode_not_supported:{session.name}:{speakers}")
    similarity = float((echo.get("metrics") or {}).get("remote_similarity_before") or 0)
    mode = str(config["acoustic_mode"])
    validation = config["acoustic_validation"]
    if mode == "headphones_or_low_leak" and similarity > float(validation["maximum_low_leak_similarity"]):
        raise SeedError(f"low_leak_gate_failed:{session.name}:{similarity}")
    if mode == "speaker_playback" and similarity < float(validation["minimum_speaker_playback_similarity"]):
        raise SeedError(f"speaker_playback_gate_failed:{session.name}:{similarity}")
    mic = raw_audio(session, session_json, "me")
    remote = raw_audio(session, session_json, "remote")
    return {
        "alias": config["alias"],
        "session_id": session.name,
        "session": session,
        "meeting_mode": meeting_mode,
        "acoustic_mode": mode,
        "remote_similarity_before": similarity,
        "published_speakers": speakers,
        "selected_profile": selection.get("selected_profile"),
        "selected_speaker_profile": selection.get("selected_speaker_profile"),
        "paths": {
            "session": session_path,
            "selection": selection_path,
            "dialogue": dialogue,
            "coverage": coverage,
            "transcript": transcript,
            "rich": rich,
            "echo": echo_path,
            "mic_raw": mic,
            "remote_raw": remote,
        },
        "fingerprints": {
            name: fingerprint(path, workspace)
            for name, path in {
                "session": session_path,
                "selection": selection_path,
                "dialogue": dialogue,
                "coverage": coverage,
                "transcript": transcript,
                "rich": rich,
                "echo": echo_path,
                "mic_raw": mic,
                "remote_raw": remote,
            }.items()
        },
    }


def candidate_rows(source: dict[str, Any], role: str, policy: dict[str, Any]) -> list[dict[str, Any]]:
    dialogue = read_json(source["paths"]["dialogue"])
    selection = policy["selection"]
    rows = []
    for utterance in dialogue.get("utterances") or []:
        quality = utterance.get("quality") or {}
        text = str(utterance.get("text") or "").strip()
        words = WORD_RE.findall(text)
        start = float(utterance.get("start") or 0)
        end = float(utterance.get("end") or 0)
        duration = end - start
        if (
            utterance.get("role") != role
            or not utterance.get("id")
            or duration < float(selection["minimum_duration_sec"])
            or duration > float(selection["maximum_duration_sec"])
            or len(words) < int(selection["minimum_hypothesis_words"])
            or len(words) > int(selection["maximum_hypothesis_words"])
            or quality.get("needs_review")
            or quality.get("overlap")
            or float(quality.get("role_confidence") or 0) < float(selection["minimum_role_confidence"])
        ):
            continue
        rows.append(
            {
                "utterance_id": str(utterance["id"]),
                "role": role,
                "start": start,
                "end": end,
                "hypothesis_text": text,
                "hypothesis_words": len(words),
            }
        )
    return sorted(rows, key=lambda row: (row["start"], row["end"], row["utterance_id"]))


def contains_domain(text: str, terms: list[str]) -> bool:
    normalized = f" {LEXICAL.normalize_text(text)} "
    return any(f" {LEXICAL.normalize_text(term)} " in normalized for term in terms)


def choose_spread(candidates: list[dict[str, Any]], count: int, salt: str, terms: list[str]) -> list[dict[str, Any]]:
    if len(candidates) < count:
        raise SeedError(f"insufficient_review_candidates:{len(candidates)}<{count}")
    selected: list[dict[str, Any]] = []
    domain = [row for row in candidates if contains_domain(row["hypothesis_text"], terms)]
    if domain:
        selected.append(min(domain, key=lambda row: rank(salt, row["utterance_id"])))
    remaining = [row for row in candidates if row not in selected]
    first = candidates[0]["start"]
    last = candidates[-1]["end"]
    while len(selected) < count:
        index = len(selected)
        target = first + (last - first) * ((index + 0.5) / count)
        row = min(
            remaining,
            key=lambda item: (
                abs(((item["start"] + item["end"]) / 2) - target),
                rank(salt, item["utterance_id"]),
            ),
        )
        selected.append(row)
        remaining.remove(row)
    return sorted(selected, key=lambda row: (row["start"], row["utterance_id"]))


def materialize_clip(source: Path, output: Path, start: float, end: float) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SeedError("ffmpeg_missing")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{max(0.01, end - start):.6f}",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, check=False)
    if completed.returncode:
        raise SeedError(f"clip_materialization_failed:{output.name}:{completed.stderr.decode(errors='replace')}")


def freeze_payload(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    workspace = sessions_root.parent.resolve()
    config_rows = []
    for row in policy["sessions"]:
        config = deepcopy(row)
        config["acoustic_validation"] = policy["acoustic_validation"]
        config_rows.append(session_inputs(config, sessions_root, workspace))
    selection = policy["selection"]
    primary_count = int(selection["primary_slots_per_session_role"])
    repeat_count = int(selection["repeat_slots_per_session_role"])
    padding = float(selection["clip_padding_sec"])
    slots: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    for source in config_rows:
        for role in ("me", "remote"):
            source_audio = source["paths"]["mic_raw" if role == "me" else "remote_raw"]
            source_audio_sha = source["fingerprints"]["mic_raw" if role == "me" else "remote_raw"]["sha256"]
            token = f"{source['alias']}:{role}"
            selected = choose_spread(
                candidate_rows(source, role, policy),
                primary_count,
                f"{selection['salt']}:{token}",
                list(policy.get("domain_terms") or []),
            )
            role_slots = []
            for row in selected:
                identity = f"{source['session_id']}:{role}:{row['utterance_id']}:{row['start']:.6f}:{row['end']:.6f}:{source_audio_sha}"
                item_id = f"lex_{rank(str(selection['salt']), identity)[:16]}"
                clip_start = max(0.0, row["start"] - padding)
                clip_end = row["end"] + padding
                clip = out / PRIVATE / "clips" / str(source["alias"]) / role / f"{item_id}.wav"
                materialize_clip(source_audio, clip, clip_start, clip_end)
                slot = {
                    "schema": SLOT_SCHEMA,
                    "item_id": item_id,
                    "session_alias": source["alias"],
                    "meeting_mode": source["meeting_mode"],
                    "acoustic_mode": source["acoustic_mode"],
                    "role": role,
                    "utterance_id": row["utterance_id"],
                    "start": row["start"],
                    "end": row["end"],
                    "clip_start": round(clip_start, 6),
                    "clip_end": round(clip_end, 6),
                    "hypothesis_text": row["hypothesis_text"],
                    "hypothesis_sha256": hashlib.sha256(row["hypothesis_text"].encode()).hexdigest(),
                    "clip": fingerprint(clip, workspace),
                    "source_audio_sha256": source_audio_sha,
                    "contains_hypothesis_domain_term": contains_domain(
                        row["hypothesis_text"], list(policy.get("domain_terms") or [])
                    ),
                }
                slots.append(slot)
                role_slots.append(slot)
                primary_slot_id = f"hrl_{rank(str(selection['salt']), 'primary:' + item_id)[:16]}"
                queue.append(
                    {
                        "schema": QUEUE_SCHEMA,
                        "slot_id": primary_slot_id,
                        "kind": "primary",
                        "item_id": item_id,
                        "session_alias": source["alias"],
                        "meeting_mode": source["meeting_mode"],
                        "acoustic_mode": source["acoustic_mode"],
                        "role": role,
                        "start": row["start"],
                        "end": row["end"],
                        "clip": slot["clip"],
                    }
                )
            repeats = sorted(
                role_slots,
                key=lambda row: rank(f"{selection['salt']}:repeat:{token}", row["item_id"]),
            )[:repeat_count]
            for row in repeats:
                queue.append(
                    {
                        "schema": QUEUE_SCHEMA,
                        "slot_id": f"hrl_{rank(str(selection['salt']), 'repeat:' + row['item_id'])[:16]}",
                        "kind": "repeat",
                        "item_id": row["item_id"],
                        "session_alias": source["alias"],
                        "meeting_mode": source["meeting_mode"],
                        "acoustic_mode": source["acoustic_mode"],
                        "role": role,
                        "start": row["start"],
                        "end": row["end"],
                        "clip": row["clip"],
                    }
                )
    slots.sort(key=lambda row: row["item_id"])
    queue.sort(key=lambda row: rank(f"{selection['salt']}:queue", row["slot_id"]))
    manifest = {
        "schema": FREEZE_SCHEMA,
        "version": VERSION,
        "policy": fingerprint(policy_path, workspace),
        "implementation": {
            "builder": fingerprint(Path(__file__).resolve(), workspace),
            "lexical_metrics": fingerprint(LEXICAL_SCRIPT, workspace),
        },
        "sessions_root": portable(sessions_root, workspace),
        "sessions": [
            {
                "alias": row["alias"],
                "session_id": row["session_id"],
                "meeting_mode": row["meeting_mode"],
                "acoustic_mode": row["acoustic_mode"],
                "remote_similarity_before": row["remote_similarity_before"],
                "published_speakers": row["published_speakers"],
                "selected_profile": row["selected_profile"],
                "selected_speaker_profile": row["selected_speaker_profile"],
                "sources": row["fingerprints"],
            }
            for row in config_rows
        ],
        "selection": policy["selection"],
        "domain_terms_sha256": hashlib.sha256(canonical(policy.get("domain_terms") or [])).hexdigest(),
        "primary_slots": sum(row["kind"] == "primary" for row in queue),
        "repeat_slots": sum(row["kind"] == "repeat" for row in queue),
    }
    return manifest, slots, queue


def artifact_manifest(out: Path, workspace: Path, slots: list[dict[str, Any]], queue: list[dict[str, Any]]) -> dict[str, Any]:
    clips = sorted({str(row["clip"]["path"]): row["clip"] for row in queue}.values(), key=lambda row: row["path"])
    return {
        "schema": ARTIFACT_SCHEMA,
        "artifacts": {
            "freeze": fingerprint(out / FREEZE, workspace),
            "slots": fingerprint(out / SLOTS, workspace),
            "queue": fingerprint(out / QUEUE, workspace),
        },
        "clips": clips,
        "slot_count": len(slots),
        "queue_count": len(queue),
    }


def freeze(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> int:
    existing_answers = read_jsonl(out / ANSWERS)
    if existing_answers:
        # Once review starts, the original clips and source fingerprints are immutable.
        bundle = load_bundle(policy_path, policy, sessions_root, out)
        print(
            "lexical_seed_v1: already_frozen "
            f"primary={bundle['manifest']['primary_slots']} "
            f"repeats={bundle['manifest']['repeat_slots']}"
        )
        return 0
    old_freeze = (out / FREEZE).read_bytes() if (out / FREEZE).is_file() else None
    manifest, slots, queue = freeze_payload(policy_path, policy, sessions_root, out)
    payload = canonical(manifest)
    if existing_answers and old_freeze is not None and old_freeze != payload:
        raise SeedError("frozen_inputs_changed_with_answers")
    write_json(out / FREEZE, manifest)
    write_jsonl(out / SLOTS, slots)
    write_jsonl(out / QUEUE, queue)
    workspace = sessions_root.parent.resolve()
    write_json(out / ARTIFACTS, artifact_manifest(out, workspace, slots, queue))
    write_json(
        out / "freeze_summary.json",
        {
            "schema": "murmurmark.human_reviewed_lexical_seed_freeze_summary/v1",
            "sessions": len(manifest["sessions"]),
            "primary_slots": manifest["primary_slots"],
            "repeat_slots": manifest["repeat_slots"],
            "meeting_modes": sorted({row["meeting_mode"] for row in manifest["sessions"]}),
            "acoustic_modes": sorted({row["acoustic_mode"] for row in manifest["sessions"]}),
            "roles": ["me", "remote"],
            "private_text_tracked": False,
        },
    )
    print(f"lexical_seed_v1: frozen primary={manifest['primary_slots']} repeats={manifest['repeat_slots']}")
    return 0


def load_bundle(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> dict[str, Any]:
    required = (out / FREEZE, out / SLOTS, out / QUEUE, out / ARTIFACTS)
    if any(not path.is_file() for path in required):
        raise SeedError("lexical_seed_not_frozen")
    workspace = sessions_root.parent.resolve()
    manifest = read_json(out / FREEZE)
    if manifest.get("schema") != FREEZE_SCHEMA:
        raise SeedError("freeze_schema_invalid")
    if not fingerprint_current(manifest.get("policy"), workspace):
        raise SeedError("policy_stale")
    if not all(fingerprint_current(row, workspace) for row in (manifest.get("implementation") or {}).values()):
        raise SeedError("implementation_stale")
    for session in manifest.get("sessions") or []:
        if not all(fingerprint_current(row, workspace) for row in (session.get("sources") or {}).values()):
            raise SeedError(f"session_sources_stale:{session.get('alias')}")
    artifacts = read_json(out / ARTIFACTS)
    if not all(fingerprint_current(row, workspace) for row in (artifacts.get("artifacts") or {}).values()):
        raise SeedError("frozen_artifacts_stale")
    if not all(fingerprint_current(row, workspace) for row in artifacts.get("clips") or []):
        raise SeedError("review_clip_stale")
    slots = read_jsonl(out / SLOTS)
    queue = read_jsonl(out / QUEUE)
    if len({row["slot_id"] for row in queue}) != len(queue):
        raise SeedError("duplicate_slot_id")
    answers = read_jsonl(out / ANSWERS)
    answer_map: dict[str, dict[str, Any]] = {}
    choices = {"exact_text", *SPECIAL_OUTCOMES}
    queue_ids = {row["slot_id"] for row in queue}
    for answer in answers:
        slot_id = str(answer.get("slot_id") or "")
        if slot_id not in queue_ids or answer.get("outcome") not in choices:
            raise SeedError(f"invalid_answer:{slot_id}")
        if answer.get("outcome") == "exact_text" and not LEXICAL.normalize_text(str(answer.get("text") or "")):
            raise SeedError(f"empty_exact_text:{slot_id}")
        if slot_id in answer_map:
            raise SeedError(f"duplicate_answer:{slot_id}")
        answer_map[slot_id] = answer
    return {
        "manifest": manifest,
        "slots": slots,
        "queue": queue,
        "answers": answers,
        "answer_map": answer_map,
        "workspace": workspace,
    }


def save_answer(out: Path, bundle: dict[str, Any], slot_id: str, outcome: str, text: str | None) -> None:
    if outcome not in {"exact_text", *SPECIAL_OUTCOMES}:
        raise SeedError("outcome_invalid")
    queue_ids = {row["slot_id"] for row in bundle["queue"]}
    if slot_id not in queue_ids:
        raise SeedError("slot_not_found")
    normalized = LEXICAL.normalize_text(text or "")
    if outcome == "exact_text" and not normalized:
        raise SeedError("exact_text_required")
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    answer = {
        "schema": ANSWER_SCHEMA,
        "slot_id": slot_id,
        "outcome": outcome,
        "truth_grade": "human_reviewed",
        "reviewed_at": now,
    }
    if outcome == "exact_text":
        answer["text"] = str(text).strip()
    rows = [row for row in bundle["answers"] if row["slot_id"] != slot_id] + [answer]
    order = {row["slot_id"]: index for index, row in enumerate(bundle["queue"])}
    rows.sort(key=lambda row: order[row["slot_id"]])
    atomic_jsonl(out / ANSWERS, rows)


def play(path: Path) -> None:
    player = shutil.which("afplay")
    if not player:
        raise SeedError("afplay_missing")
    subprocess.run([player, str(path)], check=True)


def next_unanswered(bundle: dict[str, Any]) -> dict[str, Any] | None:
    return next((row for row in bundle["queue"] if row["slot_id"] not in bundle["answer_map"]), None)


def queue_clip(slot: dict[str, Any], workspace: Path) -> Path:
    path = fingerprint_path(slot["clip"], workspace)
    if not fingerprint_current(slot["clip"], workspace):
        raise SeedError("queue_clip_stale")
    return path


def print_slot(slot: dict[str, Any], bundle: dict[str, Any]) -> None:
    answered = len(bundle["answer_map"])
    print(f"slot: {slot['slot_id']} ({answered + 1}/{len(bundle['queue'])})")
    print(f"session: {slot['session_alias']}")
    print(f"role: {slot['role']}")
    print(f"interval: {slot['start']:.2f}-{slot['end']:.2f}s")
    print(f"clip: {portable(queue_clip(slot, bundle['workspace']), bundle['workspace'])}")
    print("The production hypothesis is intentionally hidden.")


def review(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> int:
    while True:
        bundle = load_bundle(policy_path, policy, sessions_root, out)
        slot = next_unanswered(bundle)
        if slot is None:
            print("review complete")
            return evaluate(policy_path, policy, sessions_root, out, None, write_outputs=True)
        print()
        print_slot(slot, bundle)
        play(queue_clip(slot, bundle["workspace"]))
        while True:
            value = input("exact text [/r replay, /i inaudible, /m mixed, /x unusable, /q quit]: ").strip()
            if value == "/q":
                print("review stopped; progress saved")
                return 0
            if value == "/r":
                play(queue_clip(slot, bundle["workspace"]))
                continue
            aliases = {"/i": "inaudible", "/m": "mixed", "/x": "unusable"}
            outcome = aliases.get(value, "exact_text")
            text = None if value in aliases else value
            try:
                save_answer(out, bundle, slot["slot_id"], outcome, text)
            except SeedError as error:
                print(f"invalid answer: {error}")
                continue
            break


def metric_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fields = (
        "reference_words",
        "hypothesis_words",
        "word_errors",
        "substitutions",
        "deletions",
        "insertions",
        "reference_characters",
        "character_errors",
    )
    result = {field: sum(int(row["metrics"][field]) for row in rows) for field in fields}
    result["wer"] = round(result["word_errors"] / result["reference_words"], 6) if result["reference_words"] else None
    result["cer"] = round(result["character_errors"] / result["reference_characters"], 6) if result["reference_characters"] else None
    reference_terms = sum(int((row["metrics"].get("domain_terms") or {}).get("reference_terms") or 0) for row in rows)
    correct_terms = sum(int((row["metrics"].get("domain_terms") or {}).get("correct_terms") or 0) for row in rows)
    result["domain_terms"] = {
        "reference_terms": reference_terms,
        "correct_terms": correct_terms,
        "accuracy": round(correct_terms / reference_terms, 6) if reference_terms else None,
    }
    return result


def grouped_metrics(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[field])].append(row)
    return {name: metric_totals(values) for name, values in sorted(groups.items())}


def repeat_summary(queue: list[dict[str, Any]], answers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    primary_by_item = {row["item_id"]: row for row in queue if row["kind"] == "primary"}
    compared = 0
    consistent = 0
    unresolved = 0
    for repeat in (row for row in queue if row["kind"] == "repeat"):
        primary = primary_by_item[repeat["item_id"]]
        left = answers.get(primary["slot_id"])
        right = answers.get(repeat["slot_id"])
        if not left or not right:
            unresolved += 1
            continue
        compared += 1
        same = left["outcome"] == right["outcome"]
        if same and left["outcome"] == "exact_text":
            same = LEXICAL.normalize_text(str(left.get("text") or "")) == LEXICAL.normalize_text(str(right.get("text") or ""))
        consistent += int(same)
    return {
        "repeat_slots": sum(row["kind"] == "repeat" for row in queue),
        "compared": compared,
        "consistent": consistent,
        "unresolved": unresolved,
        "consistency": round(consistent / compared, 6) if compared else None,
    }


def assert_public_safe(value: Any) -> None:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if ABSOLUTE_PATH_RE.search(serialized):
        raise SeedError("public_report_contains_absolute_path")
    forbidden = {"reference_text", "hypothesis_text", "utterance_id", "slot_id", "text"}
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if forbidden & set(node):
                raise SeedError("public_report_contains_private_field")
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(value)


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    metrics = report.get("metrics") or {}
    lines = [
        "# Human-Reviewed Lexical Seed v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Review: `{summary['answered_slots']}/{summary['queue_slots']}` slots",
        f"Usable primary: `{summary['primary_exact_slots']}`; reference words: `{summary['reference_words']}`",
        "",
    ]
    if metrics:
        overall = metrics["overall"]
        lines.extend(
            [
                "## Accuracy",
                "",
                f"WER: `{overall['wer']}`; CER: `{overall['cer']}`",
                f"S/D/I: `{overall['substitutions']}/{overall['deletions']}/{overall['insertions']}`",
                f"Domain-term accuracy: `{overall['domain_terms']['accuracy']}`",
                "",
            ]
        )
    lines.extend(["## Gates", ""])
    lines.extend(f"- `{name}`: `{str(value).lower()}`" for name, value in report["gates"].items())
    lines.extend(["", "Private reference text is stored only under the ignored sessions report directory.", ""])
    return "\n".join(lines)


def evaluate_payload(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    bundle = load_bundle(policy_path, policy, sessions_root, out)
    slots = {row["item_id"]: row for row in bundle["slots"]}
    answers = bundle["answer_map"]
    outcomes = Counter(str(row["outcome"]) for row in bundle["answers"])
    evaluation = []
    for queue_row in bundle["queue"]:
        if queue_row["kind"] != "primary":
            continue
        answer = answers.get(queue_row["slot_id"])
        if not answer or answer["outcome"] != "exact_text":
            continue
        slot = slots[queue_row["item_id"]]
        reference = str(answer["text"])
        hypothesis = str(slot["hypothesis_text"])
        metrics = LEXICAL.edit_metrics(reference, hypothesis)
        metrics["domain_terms"] = LEXICAL.domain_term_metrics(
            reference, hypothesis, policy.get("domain_terms") or []
        )
        evaluation.append(
            {
                "schema": PRIVATE_EVALUATION_SCHEMA,
                "item_id": slot["item_id"],
                "session_alias": slot["session_alias"],
                "meeting_mode": slot["meeting_mode"],
                "acoustic_mode": slot["acoustic_mode"],
                "role": slot["role"],
                "reference_text": reference,
                "hypothesis_text": hypothesis,
                "reference_sha256": hashlib.sha256(reference.encode()).hexdigest(),
                "hypothesis_sha256": slot["hypothesis_sha256"],
                "metrics": metrics,
            }
        )
    repeats = repeat_summary(bundle["queue"], answers)
    exact_by_slice = Counter((row["session_alias"], row["role"]) for row in evaluation)
    gates_policy = policy["gates"]
    sessions = bundle["manifest"]["sessions"]
    complete = len(answers) == len(bundle["queue"])
    gates = {
        "all_review_slots_answered": complete,
        "minimum_sessions": len(sessions) >= int(gates_policy["minimum_sessions"]),
        "required_meeting_modes": set(gates_policy["required_meeting_modes"]) <= {row["meeting_mode"] for row in sessions},
        "required_acoustic_modes": set(gates_policy["required_acoustic_modes"]) <= {row["acoustic_mode"] for row in sessions},
        "required_roles": set(gates_policy["required_roles"]) <= {row["role"] for row in evaluation},
        "minimum_primary_exact_slots_per_session_role": all(
            exact_by_slice[(row["alias"], role)] >= int(gates_policy["minimum_primary_exact_slots_per_session_role"])
            for row in sessions
            for role in gates_policy["required_roles"]
        ),
        "minimum_reference_words": sum(int(row["metrics"]["reference_words"]) for row in evaluation)
        >= int(gates_policy["minimum_reference_words"]),
        "repeat_consistency": repeats["consistency"] is not None
        and float(repeats["consistency"]) >= float(gates_policy["minimum_repeat_consistency"]),
        "frozen_sources_current": True,
        "production_unchanged": not bool(policy.get("production_changes_allowed")),
    }
    if not complete:
        decision = "REVIEW_REQUIRED"
    else:
        decision = "REFERENCE_READY" if all(gates.values()) else "EVIDENCE_BOUND"
    metrics = {
        "overall": metric_totals(evaluation),
        "by_session": grouped_metrics(evaluation, "session_alias"),
        "by_role": grouped_metrics(evaluation, "role"),
        "by_meeting_mode": grouped_metrics(evaluation, "meeting_mode"),
        "by_acoustic_mode": grouped_metrics(evaluation, "acoustic_mode"),
    } if evaluation else {}
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "summary": {
            "sessions": len(sessions),
            "queue_slots": len(bundle["queue"]),
            "primary_slots": sum(row["kind"] == "primary" for row in bundle["queue"]),
            "repeat_slots": sum(row["kind"] == "repeat" for row in bundle["queue"]),
            "answered_slots": len(answers),
            "remaining_slots": len(bundle["queue"]) - len(answers),
            "primary_exact_slots": len(evaluation),
            "reference_words": sum(int(row["metrics"]["reference_words"]) for row in evaluation),
            "outcomes": dict(sorted(outcomes.items())),
            "meeting_modes": sorted({row["meeting_mode"] for row in sessions}),
            "acoustic_modes": sorted({row["acoustic_mode"] for row in sessions}),
            "roles": sorted({row["role"] for row in evaluation}),
        },
        "metrics": metrics,
        "repeat_review": repeats,
        "gates": gates,
        "failed_gates": [name for name, value in gates.items() if not value],
        "limitations": (
            ["Direct human review is incomplete; no lexical accuracy claim is available."]
            if decision == "REVIEW_REQUIRED"
            else []
        ),
        "provenance": {
            "freeze_sha256": sha256(out / FREEZE),
            "answers_sha256": sha256(out / ANSWERS) if (out / ANSWERS).is_file() else None,
            "policy_sha256": sha256(policy_path),
            "implementation_sha256": sha256(Path(__file__).resolve()),
            "private_evaluation_sha256": hashlib.sha256(
                b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in evaluation)
            ).hexdigest(),
        },
        "safety": {
            "private_text_in_public_report": False,
            "selected_transcript_mutated": False,
            "production_asr_mutated": False,
            "coverage_v3_mutated": False,
            "raw_audio_mutated": False,
        },
    }
    snapshot = {
        "schema": SNAPSHOT_SCHEMA,
        "decision": decision,
        "summary": report["summary"],
        "metrics": metrics,
        "repeat_review": repeats,
        "gates": gates,
        "failed_gates": report["failed_gates"],
        "provenance": report["provenance"],
    }
    assert_public_safe(report)
    assert_public_safe(snapshot)
    return report, evaluation, snapshot


def evaluate(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path, snapshot_path: Path | None, write_outputs: bool) -> int:
    report, evaluation, snapshot = evaluate_payload(policy_path, policy, sessions_root, out)
    if write_outputs:
        write_jsonl(out / EVALUATION, evaluation)
        write_json(out / REPORT, report)
        (out / REPORT_MD).write_text(report_markdown(report), encoding="utf-8")
        if snapshot_path:
            write_json(snapshot_path, snapshot)
    print(
        f"lexical_seed_v1: decision={report['decision']} "
        f"review={report['summary']['answered_slots']}/{report['summary']['queue_slots']} "
        f"words={report['summary']['reference_words']}"
    )
    return 0 if report["decision"] == "REFERENCE_READY" else 2


def replay(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path, snapshot_path: Path | None) -> int:
    report, evaluation, snapshot = evaluate_payload(policy_path, policy, sessions_root, out)
    expected = {
        EVALUATION: b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in evaluation),
        REPORT: canonical(report),
        REPORT_MD: report_markdown(report).encode(),
    }
    if snapshot_path:
        expected[snapshot_path] = canonical(snapshot)
    stale = []
    for path, content in expected.items():
        target = path if path.is_absolute() else out / path
        if not target.is_file() or target.read_bytes() != content:
            stale.append(str(target))
    if stale:
        print("lexical_seed_v1: stale=" + ",".join(stale), file=sys.stderr)
        return 2
    print(f"lexical_seed_v1: replay=byte_exact decision={report['decision']}")
    return 0


def progress(policy_path: Path, policy: dict[str, Any], sessions_root: Path, out: Path) -> int:
    bundle = load_bundle(policy_path, policy, sessions_root, out)
    remaining = len(bundle["queue"]) - len(bundle["answer_map"])
    print(f"reviewed: {len(bundle['answer_map'])}/{len(bundle['queue'])}")
    print(f"remaining: {remaining}")
    if remaining:
        print("next: murmurmark corpus lexical-seed-v1 review")
    else:
        print("next: murmurmark corpus lexical-seed-v1 evaluate")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "action",
        choices=["preflight", "freeze", "next", "grade", "review", "progress", "evaluate", "status", "replay", "all"],
    )
    result.add_argument("slot_id", nargs="?")
    result.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    result.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    result.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    result.add_argument("--outcome", choices=["exact_text", *sorted(SPECIAL_OUTCOMES)])
    result.add_argument("--text")
    result.add_argument("--play", action="store_true")
    result.add_argument("--write-snapshot", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out_dir.expanduser().resolve()
    sessions_root = args.sessions_root.expanduser().resolve()
    snapshot = args.write_snapshot.expanduser().resolve() if args.write_snapshot else None
    policy = load_policy(policy_path)
    if args.action == "preflight":
        for row in policy["sessions"]:
            config = deepcopy(row)
            config["acoustic_validation"] = policy["acoustic_validation"]
            session_inputs(config, sessions_root, sessions_root.parent)
        print(f"lexical_seed_v1: preflight=ok sessions={len(policy['sessions'])}")
        return 0
    if args.action == "freeze":
        return freeze(policy_path, policy, sessions_root, out)
    if args.action in {"progress", "status"}:
        return progress(policy_path, policy, sessions_root, out)
    if args.action == "review":
        return review(policy_path, policy, sessions_root, out)
    if args.action == "next":
        bundle = load_bundle(policy_path, policy, sessions_root, out)
        slot = next_unanswered(bundle)
        if slot is None:
            print("review complete")
            return 0
        print_slot(slot, bundle)
        if args.play:
            play(queue_clip(slot, bundle["workspace"]))
        return 0
    if args.action == "grade":
        if not args.slot_id or not args.outcome:
            raise SeedError("grade_requires_slot_and_outcome")
        bundle = load_bundle(policy_path, policy, sessions_root, out)
        save_answer(out, bundle, args.slot_id, args.outcome, args.text)
        return progress(policy_path, policy, sessions_root, out)
    if args.action == "evaluate":
        return evaluate(policy_path, policy, sessions_root, out, snapshot, write_outputs=True)
    if args.action == "replay":
        return replay(policy_path, policy, sessions_root, out, snapshot)
    if args.action == "all":
        freeze(policy_path, policy, sessions_root, out)
        return evaluate(policy_path, policy, sessions_root, out, snapshot, write_outputs=True)
    raise SeedError(f"unsupported_action:{args.action}")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, SeedError, subprocess.SubprocessError) as error:
        print(f"lexical_seed_v1: error={error}", file=sys.stderr)
        raise SystemExit(2)
