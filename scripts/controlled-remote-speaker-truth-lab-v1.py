#!/usr/bin/env python3
"""Build and evaluate exact-scripted anonymous remote-speaker truth."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Protocol
import warnings

import numpy as np
from scipy.signal import resample_poly
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.controlled_remote_speaker_truth_lab_policy/v1"
FROZEN_SCHEMA = "murmurmark.controlled_remote_speaker_truth_lab_frozen_manifest/v1"
SCENARIO_SCHEMA = "murmurmark.controlled_remote_speaker_truth_scenario/v1"
WORD_SCHEMA = "murmurmark.controlled_remote_speaker_truth_word/v1"
BOUNDARY_SCHEMA = "murmurmark.controlled_remote_speaker_truth_boundary/v1"
PREDICTION_SCHEMA = "murmurmark.controlled_remote_speaker_prediction/v1"
REPORT_SCHEMA = "murmurmark.controlled_remote_speaker_truth_lab_report/v1"
PUBLIC_MANIFEST_SCHEMA = "murmurmark.controlled_remote_speaker_truth_lab_public_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/controlled-remote-speaker-truth-lab-v1"
INDEPENDENT_POLICY = ROOT / "policies/independent-remote-speaker-evidence-v1.json"
ABSOLUTE_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")
PUBLIC_FORBIDDEN_KEYS = {
    "text",
    "token",
    "tokens",
    "system_voice",
    "voice_name",
    "speaker_name",
    "display_name",
    "transcript",
    "scripted_words",
}

VOCABULARY = (
    "anchor", "apricot", "atlas", "bamboo", "beacon", "birch", "breeze", "bronze",
    "cabin", "cactus", "canvas", "cedar", "circle", "cobalt", "comet", "coral",
    "delta", "drift", "ember", "falcon", "fern", "fjord", "forest", "fossil",
    "garden", "glacier", "granite", "harbor", "hazel", "helium", "horizon", "island",
    "jasper", "juniper", "lagoon", "lantern", "lilac", "lotus", "maple", "marble",
    "meadow", "meteor", "mosaic", "nebula", "nickel", "oasis", "olive", "onyx",
    "orbit", "orchid", "pebble", "pepper", "pioneer", "plasma", "quartz", "raven",
    "river", "rocket", "saffron", "signal", "silver", "spruce", "stone", "summit",
    "tango", "timber", "topaz", "valley", "velvet", "violet", "willow", "zephyr",
)


class LabError(RuntimeError):
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
        raise LabError(f"expected_json_object:{path}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    if not all(isinstance(row, dict) for row in rows):
        raise LabError(f"expected_jsonl_objects:{path}")
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
    atomic_write(path, b"".join(compact_json(row) + b"\n" for row in rows))


def portable(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError as error:
        raise LabError(f"path_outside_repository:{path}") from error


def fingerprint(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise LabError(f"artifact_missing:{path}")
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def normalize(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(values))
    if not math.isfinite(norm) or norm <= 1e-12:
        raise ValueError("zero_embedding")
    return values / norm


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
        raise LabError("public_private_keys:" + ",".join(sorted(forbidden)))
    rendered = canonical_json(value).decode("utf-8")
    if ABSOLUTE_PATH_RE.search(rendered):
        raise LabError("public_absolute_path")


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise LabError("policy_schema_invalid")
    speakers = list((policy.get("renderer") or {}).get("speakers") or [])
    ids = [str(row.get("speaker_id") or "") for row in speakers]
    if len(ids) != len(set(ids)) or not ids:
        raise LabError("policy_speakers_invalid")
    enrolled = [row for row in speakers if row.get("enrolled") is True]
    if len(enrolled) < int(policy["gates"]["minimum_anonymous_enrolled_speakers"]):
        raise LabError("policy_enrollment_too_small")
    splits = policy["corpus"]["splits"]
    scenarios = policy["corpus"]["scenarios"]
    flattened = [scenario for split in ("train", "dev", "hard") for scenario in splits[split]]
    if len(flattened) != len(set(flattened)) or set(flattened) != set(scenarios):
        raise LabError("policy_split_not_session_disjoint")
    train_speakers = {
        event["speaker_id"]
        for scenario in splits["train"]
        for event in scenarios[scenario]["events"]
    }
    if any(speaker not in train_speakers for speaker in (row["speaker_id"] for row in enrolled)):
        raise LabError("policy_train_enrollment_incomplete")
    hard_open = {
        event["speaker_id"]
        for scenario in splits["hard"]
        for event in scenarios[scenario]["events"]
        if event["speaker_id"] not in {row["speaker_id"] for row in enrolled}
    }
    prior = {
        event["speaker_id"]
        for split in ("train", "dev")
        for scenario in splits[split]
        for event in scenarios[scenario]["events"]
    }
    if not hard_open or hard_open & prior:
        raise LabError("policy_hard_open_set_not_unseen")
    return policy


def scenario_split_map(policy: dict[str, Any]) -> dict[str, str]:
    return {
        scenario: split
        for split, scenarios in policy["corpus"]["splits"].items()
        for scenario in scenarios
    }


def scenario_tokens(seed: str, scenario_id: str, count: int) -> list[str]:
    digest = hashlib.sha256(f"{seed}:{scenario_id}".encode()).digest()
    offset = int.from_bytes(digest[:4], "big") % len(VOCABULARY)
    step = 11 + digest[4] % 17
    while math.gcd(step, len(VOCABULARY)) != 1:
        step += 1
    return [VOCABULARY[(offset + index * step) % len(VOCABULARY)] for index in range(count)]


def to_pcm16(values: np.ndarray, peak: float) -> np.ndarray:
    audio = np.asarray(values, dtype=np.float64).reshape(-1)
    if not audio.size:
        raise LabError("renderer_empty_audio")
    finite = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0)
    current = float(np.max(np.abs(finite)))
    if current <= 1e-8:
        raise LabError("renderer_silent_audio")
    finite *= float(peak) / current
    fade = min(len(finite) // 2, 160)
    if fade:
        ramp = np.linspace(0.0, 1.0, fade, endpoint=False)
        finite[:fade] *= ramp
        finite[-fade:] *= ramp[::-1]
    return np.asarray(np.clip(np.rint(finite * 32767.0), -32768, 32767), dtype=np.int16)


class WordRenderer(Protocol):
    provenance: dict[str, Any]

    def render(self, speaker: dict[str, Any], token: str) -> np.ndarray: ...


class SayRenderer:
    def __init__(self, policy: dict[str, Any], cache_dir: Path):
        settings = policy["renderer"]
        command = shutil.which(str(settings["command"]))
        if command is None:
            raise LabError("say_renderer_missing")
        inventory = subprocess.run(
            [command, "-v", "?"], check=True, capture_output=True, text=True
        ).stdout
        for speaker in settings["speakers"]:
            name = str(speaker["system_voice"])
            if not re.search(rf"^{re.escape(name)}(?:\s|$)", inventory, flags=re.MULTILINE):
                raise LabError(f"say_voice_missing:{speaker['speaker_id']}")
        self.command = command
        self.settings = settings
        self.cache_dir = cache_dir
        self.provenance = {
            "method": "macos_say_word_renderer_v1",
            "inventory_sha256": sha256_bytes(inventory.encode()),
            "speaker_renderer_count": len(settings["speakers"]),
            "rate_words_per_minute": int(settings["rate_words_per_minute"]),
        }

    def render(self, speaker: dict[str, Any], token: str) -> np.ndarray:
        key = sha256_bytes(
            canonical_json(
                {
                    "speaker_id": speaker["speaker_id"],
                    "system_voice": speaker["system_voice"],
                    "rate": self.settings["rate_words_per_minute"],
                    "token": token,
                }
            )
        )
        path = self.cache_dir / str(speaker["speaker_id"]) / f"{key}.wav"
        if path.is_file():
            values, sample_rate = sf.read(path, dtype="int16")
            if sample_rate != int(self.settings["sample_rate"]):
                raise LabError("cached_word_sample_rate_stale")
            return np.asarray(values, dtype=np.int16).reshape(-1)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = path.with_suffix(".aiff")
        subprocess.run(
            [
                self.command,
                "-v",
                str(speaker["system_voice"]),
                "-r",
                str(self.settings["rate_words_per_minute"]),
                "-o",
                str(raw),
                token,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        values, sample_rate = sf.read(raw, dtype="float32", always_2d=True)
        raw.unlink(missing_ok=True)
        mono = values.mean(axis=1)
        target_rate = int(self.settings["sample_rate"])
        if sample_rate != target_rate:
            divisor = math.gcd(int(sample_rate), target_rate)
            mono = resample_poly(mono, target_rate // divisor, int(sample_rate) // divisor)
        magnitude = np.abs(mono)
        threshold = max(1e-5, float(magnitude.max(initial=0.0)) * 0.012)
        active = np.flatnonzero(magnitude >= threshold)
        if not len(active):
            raise LabError("say_renderer_silent_word")
        trim_pad = int(round(target_rate * 0.025))
        left = max(0, int(active[0]) - trim_pad)
        right = min(len(mono), int(active[-1]) + trim_pad + 1)
        pcm = to_pcm16(mono[left:right], float(self.settings["word_peak"]))
        edge = int(round(target_rate * float(self.settings["word_edge_padding_sec"])))
        minimum = int(round(target_rate * float(self.settings["minimum_word_sec"])))
        pcm = np.pad(pcm, (edge, edge))
        if len(pcm) < minimum:
            missing = minimum - len(pcm)
            pcm = np.pad(pcm, (missing // 2, missing - missing // 2))
        sf.write(path, pcm, target_rate, subtype=str(self.settings["audio_subtype"]))
        return pcm


class FixtureRenderer:
    def __init__(self, policy: dict[str, Any], cache_dir: Path):
        self.settings = policy["renderer"]
        self.cache_dir = cache_dir
        self.speaker_index = {
            row["speaker_id"]: index for index, row in enumerate(self.settings["speakers"])
        }
        self.provenance = {
            "method": "deterministic_harmonic_test_fixture_v1",
            "speaker_renderer_count": len(self.speaker_index),
        }

    def render(self, speaker: dict[str, Any], token: str) -> np.ndarray:
        sample_rate = int(self.settings["sample_rate"])
        speaker_id = str(speaker["speaker_id"])
        digest = hashlib.sha256(f"{speaker_id}:{token}".encode()).digest()
        duration = 0.72 + digest[0] / 2550.0
        time = np.arange(int(round(duration * sample_rate)), dtype=np.float64) / sample_rate
        base = 170.0 + self.speaker_index[speaker_id] * 73.0
        phase = digest[1] / 255.0 * math.pi
        signal = (
            np.sin(2 * math.pi * base * time + phase)
            + 0.42 * np.sin(2 * math.pi * base * 2.0 * time)
            + 0.18 * np.sin(2 * math.pi * (base * 3.0 + digest[2]) * time)
        )
        envelope = np.sin(np.linspace(0.0, math.pi, len(signal))) ** 0.45
        pcm = to_pcm16(signal * envelope, float(self.settings["word_peak"]))
        minimum = int(round(sample_rate * float(self.settings["minimum_word_sec"])))
        if len(pcm) < minimum:
            pcm = np.pad(pcm, (0, minimum - len(pcm)))
        return pcm


def speaker_lookup(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["speaker_id"]): row for row in policy["renderer"]["speakers"]}


def mark_overlaps(words: list[dict[str, Any]], minimum: float) -> None:
    for row in words:
        row["overlap_word_ids"] = []
    ordered = sorted(words, key=lambda row: (int(row["start_sample"]), int(row["end_sample"])))
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if int(right["start_sample"]) >= int(left["end_sample"]):
                break
            if left["speaker_id"] == right["speaker_id"]:
                continue
            overlap = min(int(left["end_sample"]), int(right["end_sample"])) - max(
                int(left["start_sample"]), int(right["start_sample"])
            )
            if overlap >= minimum:
                left["overlap_word_ids"].append(right["word_id"])
                right["overlap_word_ids"].append(left["word_id"])
    for row in words:
        row["overlap_word_ids"] = sorted(set(row["overlap_word_ids"]))
        row["truth_class"] = "mixed" if row["overlap_word_ids"] else (
            "known_speaker" if row["enrolled"] else "open_set_speaker"
        )


def build_scenario(
    private_root: Path,
    scenario_id: str,
    split: str,
    spec: dict[str, Any],
    policy: dict[str, Any],
    renderer: WordRenderer,
) -> dict[str, Any]:
    settings = policy["renderer"]
    sample_rate = int(settings["sample_rate"])
    speakers = speaker_lookup(policy)
    event_word_count = sum(int(event["word_count"]) for event in spec["events"])
    tokens = scenario_tokens(str(policy["seed"]), scenario_id, event_word_count)
    token_cursor = 0
    cursor = 0
    previous_end = 0
    words: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    rendered: list[tuple[str, int, np.ndarray]] = []
    gap_samples = int(round(float(settings["inter_word_gap_sec"]) * sample_rate))

    for event_index, event in enumerate(spec["events"]):
        speaker_id = str(event["speaker_id"])
        speaker = speakers.get(speaker_id)
        if speaker is None:
            raise LabError(f"unknown_speaker_in_scenario:{scenario_id}:{speaker_id}")
        if "overlap_previous_sec" in event:
            event_start = max(0, previous_end - int(round(float(event["overlap_previous_sec"]) * sample_rate)))
        else:
            event_start = previous_end + int(round(float(event.get("gap_before_sec") or 0) * sample_rate))
        event_words: list[str] = []
        local_cursor = event_start
        for word_index in range(int(event["word_count"])):
            token = tokens[token_cursor]
            token_cursor += 1
            pcm = renderer.render(speaker, token)
            start = local_cursor
            end = start + len(pcm)
            word_id = f"{split}:{scenario_id}:event:{event_index:03d}:word:{word_index:03d}"
            row = {
                "schema": WORD_SCHEMA,
                "word_id": word_id,
                "scenario_id": scenario_id,
                "split": split,
                "event_id": f"{scenario_id}:event:{event_index:03d}",
                "speaker_id": speaker_id,
                "enrolled": bool(speaker["enrolled"]),
                "text": token,
                "start_sample": start,
                "end_sample": end,
                "start": round(start / sample_rate, 6),
                "end": round(end / sample_rate, 6),
                "truth_source": "exact_scripted",
            }
            words.append(row)
            event_words.append(word_id)
            rendered.append((speaker_id, start, pcm))
            local_cursor = end + gap_samples
        event_end = local_cursor - gap_samples
        events.append(
            {
                "event_id": f"{scenario_id}:event:{event_index:03d}",
                "speaker_id": speaker_id,
                "enrolled": bool(speaker["enrolled"]),
                "start_sample": event_start,
                "end_sample": event_end,
                "start": round(event_start / sample_rate, 6),
                "end": round(event_end / sample_rate, 6),
                "word_ids": event_words,
                "overlap_previous_sec": float(event.get("overlap_previous_sec") or 0),
                "gap_before_sec": float(event.get("gap_before_sec") or 0),
            }
        )
        previous_end = max(previous_end, event_end)
        cursor = max(cursor, event_end)

    total_frames = cursor + int(round(0.5 * sample_rate))
    stems = {speaker_id: np.zeros(total_frames, dtype=np.int16) for speaker_id in speakers}
    for speaker_id, start, pcm in rendered:
        target = stems[speaker_id]
        segment = target[start : start + len(pcm)].astype(np.int32) + pcm.astype(np.int32)
        if int(np.max(np.abs(segment), initial=0)) > 32767:
            raise LabError(f"same_speaker_stem_clipping:{scenario_id}:{speaker_id}")
        target[start : start + len(pcm)] = segment.astype(np.int16)
    active_speakers = sorted({row["speaker_id"] for row in words})
    mixture_i32 = sum((stems[speaker].astype(np.int32) for speaker in active_speakers), start=np.zeros(total_frames, dtype=np.int32))
    if int(np.max(np.abs(mixture_i32), initial=0)) > 32767:
        raise LabError(f"mixture_clipping:{scenario_id}")
    mixture = mixture_i32.astype(np.int16)
    mark_overlaps(words, int(round(float(policy["analysis"]["overlap_min_sec"]) * sample_rate)))

    boundaries: list[dict[str, Any]] = []
    for left, right in zip(events, events[1:]):
        if left["speaker_id"] == right["speaker_id"]:
            continue
        overlap = int(left["end_sample"]) - int(right["start_sample"])
        gap = int(right["start_sample"]) - int(left["end_sample"])
        evaluation = overlap <= 0 and gap <= int(round(0.6 * sample_rate))
        boundaries.append(
            {
                "schema": BOUNDARY_SCHEMA,
                "boundary_id": f"{scenario_id}:boundary:{len(boundaries):03d}",
                "scenario_id": scenario_id,
                "split": split,
                "left_event_id": left["event_id"],
                "right_event_id": right["event_id"],
                "left_word_id": left["word_ids"][-1],
                "right_word_id": right["word_ids"][0],
                "left_speaker_id": left["speaker_id"],
                "right_speaker_id": right["speaker_id"],
                "time": round(int(right["start_sample"]) / sample_rate, 6),
                "evaluation": evaluation,
                "kind": "overlap" if overlap > 0 else ("silence" if gap > sample_rate else "internal_change"),
            }
        )

    scenario_dir = private_root / "sessions" / split / scenario_id
    source_dir = scenario_dir / "sources"
    source_dir.mkdir(parents=True, exist_ok=True)
    for speaker_id in active_speakers:
        sf.write(
            source_dir / f"{speaker_id}.wav",
            stems[speaker_id],
            sample_rate,
            subtype=str(settings["audio_subtype"]),
        )
    mixture_path = scenario_dir / "mixture.wav"
    sf.write(mixture_path, mixture, sample_rate, subtype=str(settings["audio_subtype"]))
    write_jsonl(scenario_dir / "truth_words.jsonl", words)
    write_jsonl(scenario_dir / "truth_boundaries.jsonl", boundaries)
    scenario_payload = {
        "schema": SCENARIO_SCHEMA,
        "scenario_id": scenario_id,
        "split": split,
        "meeting_mode": spec["meeting_mode"],
        "sample_rate": sample_rate,
        "frames": total_frames,
        "duration_sec": round(total_frames / sample_rate, 6),
        "active_speakers": active_speakers,
        "events": events,
        "word_count": len(words),
        "boundary_count": len(boundaries),
        "overlap_word_count": sum(bool(row["overlap_word_ids"]) for row in words),
    }
    write_json(scenario_dir / "scenario.json", scenario_payload)

    reconstructed = sum(
        (
            sf.read(source_dir / f"{speaker}.wav", dtype="int16")[0].astype(np.int32)
            for speaker in active_speakers
        ),
        start=np.zeros(total_frames, dtype=np.int32),
    )
    written_mixture = sf.read(mixture_path, dtype="int16")[0].astype(np.int32)
    reconstruction_error = int(np.max(np.abs(reconstructed - written_mixture), initial=0))
    if reconstruction_error != 0:
        raise LabError(f"mixture_reconstruction_failed:{scenario_id}:{reconstruction_error}")
    return {
        "scenario_id": scenario_id,
        "split": split,
        "word_count": len(words),
        "boundary_count": len(boundaries),
        "overlap_word_count": scenario_payload["overlap_word_count"],
        "duration_sec": scenario_payload["duration_sec"],
        "reconstruction_max_sample_error": reconstruction_error,
    }


def artifact_hashes(root: Path, *, exclude_prefixes: tuple[str, ...] = ()) -> dict[str, str]:
    rows: dict[str, str] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root))
        if relative == "frozen_manifest.json" or any(relative.startswith(prefix) for prefix in exclude_prefixes):
            continue
        rows[relative] = sha256(path)
    return rows


def build_corpus(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    out_dir = args.out_dir.resolve()
    private = out_dir / "private"
    frozen = private / "frozen_manifest.json"
    if frozen.is_file() and not args.force:
        verify_frozen(private, policy, args.policy)
        print(f"corpus already frozen: {portable(frozen)}")
        return read_json(frozen)
    staging = out_dir / f".private-building-{os.getpid()}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        if private.is_dir() and (private / "cache").is_dir():
            shutil.copytree(private / "cache", staging / "cache", copy_function=os.link)
        cache = staging / "cache/words"
        renderer: WordRenderer = (
            FixtureRenderer(policy, cache) if args.fixture_mode else SayRenderer(policy, cache)
        )
        split_map = scenario_split_map(policy)
        summaries = []
        for scenario_id in [
            scenario
            for split in ("train", "dev", "hard")
            for scenario in policy["corpus"]["splits"][split]
        ]:
            print(f"build: {split_map[scenario_id]}/{scenario_id}", flush=True)
            summaries.append(
                build_scenario(
                    staging,
                    scenario_id,
                    split_map[scenario_id],
                    policy["corpus"]["scenarios"][scenario_id],
                    policy,
                    renderer,
                )
            )
        artifacts = artifact_hashes(staging)
        manifest = {
            "schema": FROZEN_SCHEMA,
            "version": VERSION,
            "mode": "fixture" if args.fixture_mode else "local_speech",
            "policy": fingerprint(args.policy),
            "implementation": fingerprint(Path(__file__).resolve()),
            "renderer": renderer.provenance,
            "split_scenarios": policy["corpus"]["splits"],
            "speaker_count": len(policy["renderer"]["speakers"]),
            "enrolled_speaker_count": sum(bool(row["enrolled"]) for row in policy["renderer"]["speakers"]),
            "scenario_summaries": summaries,
            "artifacts": artifacts,
            "corpus_sha256": sha256_bytes(canonical_json(artifacts)),
        }
        write_json(staging / "frozen_manifest.json", manifest)
        if private.exists():
            shutil.rmtree(private)
        os.replace(staging, private)
        print(f"frozen: {portable(private / 'frozen_manifest.json')}")
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify_frozen(private: Path, policy: dict[str, Any], policy_path: Path) -> dict[str, Any]:
    manifest = read_json(private / "frozen_manifest.json")
    if manifest.get("schema") != FROZEN_SCHEMA:
        raise LabError("frozen_manifest_schema_invalid")
    if manifest.get("policy", {}).get("sha256") != sha256(policy_path):
        raise LabError("frozen_policy_stale")
    if manifest.get("implementation", {}).get("sha256") != sha256(Path(__file__).resolve()):
        raise LabError("frozen_implementation_stale")
    expected = manifest.get("artifacts") or {}
    actual = artifact_hashes(private, exclude_prefixes=("predictions/",))
    if expected != actual:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        changed = sorted(name for name in set(expected) & set(actual) if expected[name] != actual[name])
        raise LabError(
            "frozen_artifacts_stale:"
            + json.dumps({"missing": missing, "extra": extra, "changed": changed}, sort_keys=True)
        )
    if manifest.get("corpus_sha256") != sha256_bytes(canonical_json(expected)):
        raise LabError("frozen_corpus_hash_stale")
    split_map = scenario_split_map(policy)
    if set(split_map) != {row["scenario_id"] for row in manifest["scenario_summaries"]}:
        raise LabError("frozen_scenario_scope_stale")
    return manifest


@dataclass(frozen=True)
class AudioRequest:
    key: str
    scenario_id: str
    path: Path
    start: float
    end: float


class EmbeddingBackend(Protocol):
    provenance: dict[str, Any]

    def embed_requests(self, requests: list[AudioRequest]) -> dict[str, np.ndarray]: ...


def padded_slice(path: Path, start: float, end: float, minimum_sec: float) -> tuple[np.ndarray, int]:
    with sf.SoundFile(path) as audio:
        start_frame = max(0, int(round(start * audio.samplerate)))
        end_frame = min(len(audio), int(round(end * audio.samplerate)))
        if end_frame <= start_frame:
            raise LabError("empty_audio_slice")
        audio.seek(start_frame)
        values = audio.read(end_frame - start_frame, dtype="float32", always_2d=True).mean(axis=1)
        sample_rate = int(audio.samplerate)
    minimum = int(round(minimum_sec * sample_rate))
    if len(values) < minimum:
        missing = minimum - len(values)
        values = np.pad(values, (missing // 2, missing - missing // 2))
    if float(np.sqrt(np.mean(np.square(values), dtype=np.float64))) < 1e-7:
        raise LabError("silent_audio_slice")
    return np.asarray(values, dtype=np.float32), sample_rate


class FixtureEmbeddingBackend:
    def __init__(self, method: str, minimum_sec: float):
        self.minimum_sec = minimum_sec
        self.provenance = {"method": method, "mode": "deterministic_audio_fixture"}

    def embed_requests(self, requests: list[AudioRequest]) -> dict[str, np.ndarray]:
        rows: dict[str, np.ndarray] = {}
        for request in requests:
            values, sample_rate = padded_slice(request.path, request.start, request.end, self.minimum_sec)
            spectrum = np.abs(np.fft.rfft(values * np.hanning(len(values))))
            frequencies = np.fft.rfftfreq(len(values), 1.0 / sample_rate)
            bands = []
            for left in np.linspace(80, 650, 49)[:-1]:
                right = left + (650 - 80) / 48
                mask = (frequencies >= left) & (frequencies < right)
                bands.append(float(spectrum[mask].sum()))
            rows[request.key] = normalize(np.asarray(bands, dtype=np.float32))
        return rows


class WavLMBackend:
    def __init__(self, policy: dict[str, Any], minimum_sec: float, batch_size: int):
        model_policy = read_json(INDEPENDENT_POLICY)
        backend = model_policy["backend"]
        runtime_policy = model_policy["runtime"]
        model_path = Path(
            os.environ.get("MURMURMARK_REMOTE_SPEAKER_WAVLM_MODEL", backend["default_path"])
        ).expanduser().resolve()
        files = []
        for name, expected in sorted(backend["files"].items()):
            path = model_path / name
            if not path.is_file() or sha256(path) != expected:
                raise LabError(f"wavlm_model_missing_or_stale:{name}")
            files.append({"name": name, "bytes": path.stat().st_size, "sha256": expected})
        try:
            import torch
            import transformers
            from transformers import AutoFeatureExtractor, AutoModelForAudioXVector
        except (ImportError, ModuleNotFoundError) as error:
            raise LabError(f"wavlm_runtime_missing:{type(error).__name__}") from error
        versions = {
            "torch": str(torch.__version__),
            "transformers": str(transformers.__version__),
            "numpy": np.__version__,
            "soundfile": importlib.metadata.version("soundfile"),
        }
        stale = [key for key, value in versions.items() if value != str(runtime_policy[key])]
        if stale:
            raise LabError("wavlm_runtime_stale:" + ",".join(stale))
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        torch.set_num_threads(int(runtime_policy["max_torch_threads"]))
        torch.use_deterministic_algorithms(True, warn_only=True)
        self.torch = torch
        self.processor = AutoFeatureExtractor.from_pretrained(str(model_path), local_files_only=True)
        self.model = AutoModelForAudioXVector.from_pretrained(str(model_path), local_files_only=True)
        self.model.eval()
        self.minimum_sec = minimum_sec
        self.batch_size = batch_size
        self.provenance = {
            "method": "wavlm_xvector_open_set_lab_v1",
            "model_id": backend["model_id"],
            "model_tree_sha256": sha256_bytes(canonical_json(files)),
            "runtime": versions,
            "device": "cpu",
            "offline": True,
        }

    def embed_requests(self, requests: list[AudioRequest]) -> dict[str, np.ndarray]:
        prepared: list[tuple[AudioRequest, np.ndarray]] = []
        for request in requests:
            values, sample_rate = padded_slice(request.path, request.start, request.end, self.minimum_sec)
            if sample_rate != 16000:
                divisor = math.gcd(sample_rate, 16000)
                values = resample_poly(values, 16000 // divisor, sample_rate // divisor).astype(np.float32)
            prepared.append((request, values))
        rows: dict[str, np.ndarray] = {}
        prepared.sort(key=lambda row: (len(row[1]), row[0].key))
        for offset in range(0, len(prepared), self.batch_size):
            batch = prepared[offset : offset + self.batch_size]
            inputs = self.processor(
                [values for _, values in batch],
                sampling_rate=16000,
                return_tensors="pt",
                padding=True,
            )
            with self.torch.no_grad():
                vectors = self.model(**inputs).embeddings.detach().cpu().numpy()
            for (request, _), vector in zip(batch, vectors):
                rows[request.key] = normalize(vector)
        return rows


class ResemblyzerBackend:
    def __init__(self, minimum_sec: float):
        try:
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", message="pkg_resources is deprecated as an API.*")
                import resemblyzer
                from resemblyzer import VoiceEncoder, preprocess_wav
        except (ImportError, ModuleNotFoundError) as error:
            raise LabError(f"resemblyzer_runtime_missing:{type(error).__name__}") from error
        model_path = Path(resemblyzer.__file__).resolve().with_name("pretrained.pt")
        if not model_path.is_file():
            raise LabError("resemblyzer_model_missing")
        self.encoder = VoiceEncoder(device="cpu", verbose=False, weights_fpath=model_path)
        self.preprocess = preprocess_wav
        self.minimum_sec = max(1.2, minimum_sec)
        self.provenance = {
            "method": "coverage_v3_seeded_centroid_topology_audit",
            "backend": "resemblyzer",
            "package_version": importlib.metadata.version("resemblyzer"),
            "model_sha256": sha256(model_path),
        }

    def embed_requests(self, requests: list[AudioRequest]) -> dict[str, np.ndarray]:
        rows: dict[str, np.ndarray] = {}
        for request in requests:
            values, sample_rate = padded_slice(request.path, request.start, request.end, self.minimum_sec)
            prepared = self.preprocess(values, source_sr=sample_rate)
            if len(prepared) < 16000:
                repeats = int(math.ceil(16000 / max(1, len(prepared))))
                prepared = np.tile(prepared, repeats)[:16000]
            rows[request.key] = normalize(self.encoder.embed_utterance(prepared))
        return rows


def scenario_paths(private: Path, policy: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for split in ("train", "dev", "hard"):
        for scenario_id in policy["corpus"]["splits"][split]:
            directory = private / "sessions" / split / scenario_id
            rows.append(
                {
                    "split": split,
                    "scenario_id": scenario_id,
                    "directory": directory,
                    "mixture": directory / "mixture.wav",
                    "scenario": read_json(directory / "scenario.json"),
                    "words": read_jsonl(directory / "truth_words.jsonl"),
                    "boundaries": read_jsonl(directory / "truth_boundaries.jsonl"),
                }
            )
    return rows


def build_requests(scenarios: list[dict[str, Any]]) -> tuple[list[AudioRequest], list[AudioRequest]]:
    enrollment = []
    words = []
    for scenario in scenarios:
        mixture = scenario["mixture"]
        if scenario["split"] == "train":
            for event in scenario["scenario"]["events"]:
                enrollment.append(
                    AudioRequest(
                        key=f"enrollment:{event['event_id']}",
                        scenario_id=scenario["scenario_id"],
                        path=mixture,
                        start=float(event["start"]),
                        end=float(event["end"]),
                    )
                )
        for word in scenario["words"]:
            words.append(
                AudioRequest(
                    key=f"word:{word['word_id']}",
                    scenario_id=scenario["scenario_id"],
                    path=mixture,
                    start=float(word["start"]),
                    end=float(word["end"]),
                )
            )
    return enrollment, words


def build_centroids(
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    enrollment_mode: str,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    by_event = {
        event["event_id"]: event
        for scenario in scenarios
        if scenario["split"] == "train"
        for event in scenario["scenario"]["events"]
    }
    values: dict[str, list[np.ndarray]] = defaultdict(list)
    if enrollment_mode in {"event_only", "word_and_event"}:
        for event_id, event in by_event.items():
            key = f"enrollment:{event_id}"
            if key in embeddings:
                values[str(event["speaker_id"])].append(embeddings[key])
    if enrollment_mode in {"word_only", "word_and_event"}:
        for scenario in scenarios:
            if scenario["split"] != "train":
                continue
            for word in scenario["words"]:
                if word["truth_class"] != "known_speaker":
                    continue
                key = f"word:{word['word_id']}"
                if key in embeddings:
                    values[str(word["speaker_id"])].append(embeddings[key])
    centroids = {speaker: normalize(np.mean(rows, axis=0)) for speaker, rows in values.items() if rows}
    return centroids, {
        "speaker_count": len(centroids),
        "sample_count": sum(len(rows) for rows in values.values()),
        "samples_per_speaker": dict(sorted((speaker, len(rows)) for speaker, rows in values.items())),
        "mode": enrollment_mode,
    }


def classify(vector: np.ndarray, centroids: dict[str, np.ndarray], similarity: float, margin: float) -> dict[str, Any]:
    scores = sorted(
        ((float(vector @ centroid), speaker) for speaker, centroid in centroids.items()),
        reverse=True,
    )
    if not scores:
        return {"speaker_id": None, "top_speaker_id": None, "similarity": None, "margin": None}
    top_similarity, top_speaker = scores[0]
    top_margin = top_similarity - scores[1][0] if len(scores) > 1 else top_similarity
    accepted = top_similarity >= similarity and top_margin >= margin
    return {
        "speaker_id": top_speaker if accepted else None,
        "top_speaker_id": top_speaker,
        "similarity": round(top_similarity, 6),
        "margin": round(top_margin, 6),
    }


def predict_words(
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    centroids: dict[str, np.ndarray],
    similarity: float,
    margin: float,
    track: str,
) -> list[dict[str, Any]]:
    rows = []
    for scenario in scenarios:
        for word in scenario["words"]:
            if word["overlap_word_ids"]:
                result = {
                    "speaker_id": "mixed",
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "reason": "timestamp_overlap",
                }
            else:
                raw = classify(
                    embeddings[f"word:{word['word_id']}"], centroids, similarity, margin
                )
                result = {
                    **raw,
                    "speaker_id": raw["speaker_id"] or "unknown_speaker",
                    "reason": "accepted_centroid" if raw["speaker_id"] else "open_set_abstention",
                }
            rows.append(
                {
                    "schema": PREDICTION_SCHEMA,
                    "track": track,
                    "word_id": word["word_id"],
                    "scenario_id": scenario["scenario_id"],
                    "split": scenario["split"],
                    "speaker_id": result["speaker_id"],
                    "top_speaker_id": result["top_speaker_id"],
                    "similarity": result["similarity"],
                    "margin": result["margin"],
                    "reason": result["reason"],
                }
            )
    return rows


def bcubed(truth: list[str], predicted: list[str]) -> dict[str, float]:
    truth_members: dict[str, set[int]] = defaultdict(set)
    predicted_members: dict[str, set[int]] = defaultdict(set)
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        truth_members[expected].add(index)
        predicted_members[actual].add(index)
    precisions = []
    recalls = []
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        intersection = len(truth_members[expected] & predicted_members[actual])
        precisions.append(intersection / len(predicted_members[actual]))
        recalls.append(intersection / len(truth_members[expected]))
    precision = float(np.mean(precisions)) if precisions else 0.0
    recall = float(np.mean(recalls)) if recalls else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def pairwise(truth: list[str], predicted: list[str]) -> dict[str, Any]:
    true_positive = false_positive = false_negative = 0
    for left in range(len(truth)):
        for right in range(left + 1, len(truth)):
            truth_same = truth[left] == truth[right]
            predicted_same = predicted[left] == predicted[right] and not predicted[left].startswith("unknown:")
            if truth_same and predicted_same:
                true_positive += 1
            elif not truth_same and predicted_same:
                false_positive += 1
            elif truth_same and not predicted_same:
                false_negative += 1
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "true_positive_pairs": true_positive,
        "false_positive_pairs": false_positive,
        "false_negative_pairs": false_negative,
    }


def evaluate_predictions(
    scenarios: list[dict[str, Any]], predictions: list[dict[str, Any]], split: str
) -> dict[str, Any]:
    truth_rows = [word for scenario in scenarios if scenario["split"] == split for word in scenario["words"]]
    predicted_by_id = {str(row["word_id"]): row for row in predictions if row["split"] == split}
    conservation = len(predicted_by_id) == len(truth_rows) and set(predicted_by_id) == {
        str(row["word_id"]) for row in truth_rows
    }
    known = [row for row in truth_rows if row["truth_class"] == "known_speaker"]
    truth_labels = [str(row["speaker_id"]) for row in known]
    predicted_labels = []
    correct = 0
    accepted = 0
    for row in known:
        prediction = predicted_by_id[str(row["word_id"])]["speaker_id"]
        if prediction in {"unknown_speaker", "mixed"}:
            predicted_labels.append(f"unknown:{row['word_id']}")
        else:
            predicted_labels.append(str(prediction))
            accepted += 1
            correct += int(prediction == row["speaker_id"])
    open_set = [row for row in truth_rows if row["truth_class"] == "open_set_speaker"]
    open_false = sum(
        str(predicted_by_id[str(row["word_id"])]["speaker_id"]).startswith("remote_speaker_")
        for row in open_set
    )
    mixed = [row for row in truth_rows if row["truth_class"] == "mixed"]
    mixed_safe = sum(
        predicted_by_id[str(row["word_id"])]["speaker_id"] == "mixed" for row in mixed
    )
    boundaries = [
        boundary
        for scenario in scenarios
        if scenario["split"] == split
        for boundary in scenario["boundaries"]
        if boundary["evaluation"]
    ]
    recovered = 0
    for boundary in boundaries:
        left = predicted_by_id[str(boundary["left_word_id"])]["speaker_id"]
        right = predicted_by_id[str(boundary["right_word_id"])]["speaker_id"]
        expected_left = (
            str(boundary["left_speaker_id"])
            if str(boundary["left_speaker_id"]).startswith("remote_speaker_")
            else "unknown_speaker"
        )
        expected_right = (
            str(boundary["right_speaker_id"])
            if str(boundary["right_speaker_id"]).startswith("remote_speaker_")
            else "unknown_speaker"
        )
        recovered += int(left == expected_left and right == expected_right and left != right)
    return {
        "word_count": len(truth_rows),
        "prediction_count": len(predicted_by_id),
        "word_conservation": conservation,
        "direct_truth_coverage": all(row.get("truth_source") == "exact_scripted" for row in truth_rows),
        "known_single_speaker_words": len(known),
        "known_attributed_words": accepted,
        "known_correct_words": correct,
        "known_attribution_recall": round(accepted / len(known), 6) if known else 0.0,
        "known_attributed_precision": round(correct / accepted, 6) if accepted else None,
        "bcubed": bcubed(truth_labels, predicted_labels),
        "pairwise": pairwise(truth_labels, predicted_labels),
        "open_set_words": len(open_set),
        "open_set_false_attributions": open_false,
        "mixed_words": len(mixed),
        "mixed_safely_marked": mixed_safe,
        "boundary_count": len(boundaries),
        "boundaries_recovered": recovered,
        "boundary_recall": round(recovered / len(boundaries), 6) if boundaries else 0.0,
    }


def choose_thresholds(
    policy: dict[str, Any],
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    centroids: dict[str, np.ndarray],
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    trials = []
    for similarity in policy["analysis"]["dev_similarity_grid"]:
        for margin in policy["analysis"]["dev_margin_grid"]:
            predictions = predict_words(
                scenarios, embeddings, centroids, float(similarity), float(margin), "wavlm_dev_trial"
            )
            metrics = evaluate_predictions(scenarios, predictions, "dev")
            trials.append(
                {
                    "minimum_similarity": float(similarity),
                    "minimum_margin": float(margin),
                    "metrics": metrics,
                }
            )
    eligible = [
        row
        for row in trials
        if row["metrics"]["open_set_false_attributions"] == 0
        and row["metrics"]["pairwise"]["precision"]
        >= float(policy["gates"]["minimum_held_out_pairwise_precision"])
    ]
    pool = eligible or trials
    selected = max(
        pool,
        key=lambda row: (
            -int(row["metrics"]["open_set_false_attributions"]),
            row["metrics"]["bcubed"]["f1"],
            row["metrics"]["known_attribution_recall"],
            row["metrics"]["pairwise"]["precision"],
            -row["minimum_similarity"],
            -row["minimum_margin"],
        ),
    )
    return {
        "minimum_similarity": selected["minimum_similarity"],
        "minimum_margin": selected["minimum_margin"],
        "eligible_zero_open_set_trial": bool(eligible),
    }, trials


def private_prediction_payload(
    backend: EmbeddingBackend,
    scenarios: list[dict[str, Any]],
    policy: dict[str, Any],
    *,
    tune: bool,
    similarity: float | None = None,
    margin: float | None = None,
    track: str,
    enrollment_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    enrollment_requests, word_requests = build_requests(scenarios)
    embeddings = backend.embed_requests(enrollment_requests + word_requests)
    centroids, enrollment_summary = build_centroids(scenarios, embeddings, enrollment_mode)
    expected_enrolled = {
        row["speaker_id"] for row in policy["renderer"]["speakers"] if row["enrolled"]
    }
    if set(centroids) != expected_enrolled:
        raise LabError("enrollment_centroids_incomplete")
    trials: list[dict[str, Any]] = []
    if tune:
        thresholds, trials = choose_thresholds(policy, scenarios, embeddings, centroids)
        similarity = float(thresholds["minimum_similarity"])
        margin = float(thresholds["minimum_margin"])
    else:
        thresholds = {
            "minimum_similarity": float(similarity),
            "minimum_margin": float(margin),
            "eligible_zero_open_set_trial": None,
        }
    predictions = predict_words(
        scenarios, embeddings, centroids, float(similarity), float(margin), track
    )
    metrics = {
        "dev": evaluate_predictions(scenarios, predictions, "dev"),
        "hard": evaluate_predictions(scenarios, predictions, "hard"),
    }
    summary = {
        "backend": backend.provenance,
        "enrollment": enrollment_summary,
        "thresholds": thresholds,
        "dev": metrics["dev"],
        "hard": metrics["hard"],
        "threshold_trials": len(trials),
        "hard_used_for_tuning": False,
    }
    return predictions, summary, {"trials": trials}


def split_aggregate(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    rows = {}
    for split in ("train", "dev", "hard"):
        selected = [row for row in scenarios if row["split"] == split]
        words = [word for row in selected for word in row["words"]]
        rows[split] = {
            "sessions": len(selected),
            "words": len(words),
            "known_words": sum(word["truth_class"] == "known_speaker" for word in words),
            "open_set_words": sum(word["truth_class"] == "open_set_speaker" for word in words),
            "mixed_words": sum(word["truth_class"] == "mixed" for word in words),
            "boundaries": sum(len(row["boundaries"]) for row in selected),
            "evaluation_boundaries": sum(
                boundary["evaluation"] for row in selected for boundary in row["boundaries"]
            ),
            "duration_sec": round(sum(float(row["scenario"]["duration_sec"]) for row in selected), 6),
        }
    return rows


def public_report(
    args: argparse.Namespace,
    policy: dict[str, Any],
    frozen: dict[str, Any],
    scenarios: list[dict[str, Any]],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    prediction_hashes: dict[str, str],
) -> dict[str, Any]:
    hard = candidate["hard"]
    baseline_hard = baseline["hard"]
    candidate_track_gates = {
        "held_out_bcubed_f1": hard["bcubed"]["f1"]
        >= float(policy["gates"]["minimum_held_out_bcubed_f1"]),
        "held_out_pairwise_precision": hard["pairwise"]["precision"]
        >= float(policy["gates"]["minimum_held_out_pairwise_precision"]),
        "boundary_recall": hard["boundary_recall"]
        >= float(policy["gates"]["minimum_boundary_recall"]),
        "zero_open_set_false_attribution": hard["open_set_false_attributions"]
        <= int(policy["gates"]["maximum_open_set_false_attributions"]),
    }
    baseline_track_gates = {
        "held_out_bcubed_f1": baseline_hard["bcubed"]["f1"]
        >= float(policy["gates"]["minimum_held_out_bcubed_f1"]),
        "held_out_pairwise_precision": baseline_hard["pairwise"]["precision"]
        >= float(policy["gates"]["minimum_held_out_pairwise_precision"]),
        "boundary_recall": baseline_hard["boundary_recall"]
        >= float(policy["gates"]["minimum_boundary_recall"]),
        "zero_open_set_false_attribution": baseline_hard["open_set_false_attributions"]
        <= int(policy["gates"]["maximum_open_set_false_attributions"]),
    }
    gates = {
        "minimum_anonymous_enrolled_speakers": frozen["enrolled_speaker_count"]
        >= int(policy["gates"]["minimum_anonymous_enrolled_speakers"]),
        "source_stem_reconstruction_exact": all(
            int(row["reconstruction_max_sample_error"])
            <= int(policy["gates"]["maximum_mixture_reconstruction_sample_error"])
            for row in frozen["scenario_summaries"]
        ),
        "session_disjoint_splits": len(scenario_split_map(policy))
        == sum(len(value) for value in policy["corpus"]["splits"].values()),
        "hard_split_untuned": candidate["hard_used_for_tuning"] is False,
        "all_words_conserved": hard["word_conservation"],
        "direct_truth_coverage": hard["direct_truth_coverage"],
        **{f"wavlm_candidate_{name}": passed for name, passed in candidate_track_gates.items()},
        "mixed_words_fail_closed": hard["mixed_safely_marked"] == hard["mixed_words"],
        "public_artifacts_private_safe": True,
        "synthetic_evidence_not_promoted": bool(
            policy["gates"]["synthetic_evidence_cannot_promote_real_transcripts"]
        ),
    }
    decision = "LAB_READY" if all(gates.values()) else "DO_NOT_ADVANCE"
    blockers = [name for name, passed in gates.items() if not passed]
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "mode": frozen["mode"],
        "safety": {
            "audit_only": True,
            "real_transcript_changed": False,
            "coverage_v3_changed": False,
            "primary_asr_changed": False,
            "echo_guard_changed": False,
            "synthetic_labels_promoted": False,
        },
        "source": {
            "policy": fingerprint(args.policy),
            "implementation": fingerprint(Path(__file__).resolve()),
            "frozen_manifest": fingerprint(args.out_dir / "private/frozen_manifest.json"),
            "corpus_sha256": frozen["corpus_sha256"],
        },
        "corpus": {
            "splits": split_aggregate(scenarios),
            "scenario_count": len(scenarios),
            "speaker_count": frozen["speaker_count"],
            "enrolled_speaker_count": frozen["enrolled_speaker_count"],
            "open_set_speaker_count": frozen["speaker_count"] - frozen["enrolled_speaker_count"],
            "maximum_reconstruction_sample_error": max(
                int(row["reconstruction_max_sample_error"]) for row in frozen["scenario_summaries"]
            ),
        },
        "evaluation": {
            "coverage_v3_topology": baseline,
            "wavlm_open_set_candidate": candidate,
            "track_decisions": {
                "coverage_v3_topology": {
                    "decision": "CONTROL_QUALIFIED" if all(baseline_track_gates.values()) else "CONTROL_LIMITED",
                    "gates": baseline_track_gates,
                },
                "wavlm_open_set_candidate": {
                    "decision": "CANDIDATE_QUALIFIED" if all(candidate_track_gates.values()) else "DO_NOT_ADVANCE",
                    "gates": candidate_track_gates,
                },
            },
            "prediction_sha256": prediction_hashes,
        },
        "gates": gates,
        "blockers": blockers,
        "next_step": (
            "bounded_real_candidate_requires_independent_reference"
            if decision == "LAB_READY"
            else "keep_coverage_v3_and_reject_wavlm_candidate"
        ),
    }
    assert_public_safe(report)
    return report


def report_markdown(report: dict[str, Any]) -> str:
    candidate = report["evaluation"]["wavlm_open_set_candidate"]
    track_decisions = report["evaluation"]["track_decisions"]
    hard = candidate["hard"]
    baseline = report["evaluation"]["coverage_v3_topology"]["hard"]
    lines = [
        "# Controlled Remote Speaker Truth Lab v1",
        "",
        f"Decision: **{report['decision']}**",
        "",
        "Synthetic evidence is audit-only and did not change a real transcript or Coverage v3.",
        "",
        "## Corpus",
        "",
        f"- Scenarios: `{report['corpus']['scenario_count']}`",
        f"- Enrolled anonymous speakers: `{report['corpus']['enrolled_speaker_count']}`",
        f"- Open-set speakers: `{report['corpus']['open_set_speaker_count']}`",
        f"- Maximum stem reconstruction error: `{report['corpus']['maximum_reconstruction_sample_error']}` PCM samples",
        "",
        "## Hard Split",
        "",
        f"- Candidate B-cubed F1: `{hard['bcubed']['f1']:.6f}`",
        f"- Candidate pairwise precision: `{hard['pairwise']['precision']:.6f}`",
        f"- Candidate boundary recall: `{hard['boundary_recall']:.6f}`",
        f"- Candidate open-set false attributions: `{hard['open_set_false_attributions']}`",
        f"- Candidate known-word attribution recall: `{hard['known_attribution_recall']:.6f}`",
        f"- Coverage v3 topology B-cubed F1: `{baseline['bcubed']['f1']:.6f}`",
        f"- Coverage v3 topology open-set false attributions: `{baseline['open_set_false_attributions']}`",
        f"- Coverage v3 topology decision: `{track_decisions['coverage_v3_topology']['decision']}`",
        f"- WavLM candidate decision: `{track_decisions['wavlm_open_set_candidate']['decision']}`",
        "",
        "## Gates",
        "",
    ]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'} `{name}`" for name, passed in report["gates"].items()
    )
    lines.extend(["", f"Next: `{report['next_step']}`", ""])
    return "\n".join(lines)


def evaluate_corpus(
    args: argparse.Namespace,
    policy: dict[str, Any],
    *,
    write: bool,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    private = args.out_dir / "private"
    frozen = verify_frozen(private, policy, args.policy)
    scenarios = scenario_paths(private, policy)
    minimum = float(policy["analysis"]["minimum_analysis_sec"])
    if frozen["mode"] == "fixture":
        baseline_backend: EmbeddingBackend = FixtureEmbeddingBackend(
            "coverage_v3_seeded_centroid_fixture", minimum
        )
        candidate_backend: EmbeddingBackend = FixtureEmbeddingBackend(
            "wavlm_open_set_candidate_fixture", minimum
        )
    else:
        baseline_backend = ResemblyzerBackend(minimum)
        candidate_backend = WavLMBackend(
            policy, minimum, int(policy["analysis"]["wavlm_batch_size"])
        )
    print("evaluate: coverage_v3_topology", flush=True)
    baseline_predictions, baseline_summary, _ = private_prediction_payload(
        baseline_backend,
        scenarios,
        policy,
        tune=False,
        similarity=float(policy["analysis"]["coverage_v3_similarity"]),
        margin=float(policy["analysis"]["coverage_v3_margin"]),
        track="coverage_v3_topology",
        enrollment_mode="word_and_event",
    )
    print("evaluate: wavlm_open_set_candidate", flush=True)
    candidate_predictions, candidate_summary, tuning = private_prediction_payload(
        candidate_backend,
        scenarios,
        policy,
        tune=True,
        track="wavlm_open_set_candidate",
        enrollment_mode="word_only",
    )
    payloads = {
        "coverage_v3_predictions.jsonl": b"".join(
            compact_json(row) + b"\n" for row in baseline_predictions
        ),
        "wavlm_predictions.jsonl": b"".join(
            compact_json(row) + b"\n" for row in candidate_predictions
        ),
        "wavlm_dev_threshold_trials.json": canonical_json(tuning),
    }
    prediction_hashes = {name: sha256_bytes(payload) for name, payload in payloads.items()}
    report = public_report(
        args,
        policy,
        frozen,
        scenarios,
        baseline_summary,
        candidate_summary,
        prediction_hashes,
    )
    report_payload = canonical_json(report)
    markdown_payload = report_markdown(report).encode()
    outputs = {
        **payloads,
        "controlled_remote_speaker_truth_lab_report.json": report_payload,
        "controlled_remote_speaker_truth_lab_report.md": markdown_payload,
    }
    if write:
        predictions_dir = private / "predictions"
        predictions_dir.mkdir(parents=True, exist_ok=True)
        for name, payload in payloads.items():
            atomic_write(predictions_dir / name, payload)
        atomic_write(
            args.out_dir / "controlled_remote_speaker_truth_lab_report.json", report_payload
        )
        atomic_write(
            args.out_dir / "controlled_remote_speaker_truth_lab_report.md", markdown_payload
        )
        public_manifest = {
            "schema": PUBLIC_MANIFEST_SCHEMA,
            "artifacts": {
                "controlled_remote_speaker_truth_lab_report.json": sha256_bytes(report_payload),
                "controlled_remote_speaker_truth_lab_report.md": sha256_bytes(markdown_payload),
            },
        }
        assert_public_safe(public_manifest)
        write_json(args.out_dir / "artifact_manifest.json", public_manifest)
    return report, outputs


def replay(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    report_path = args.out_dir / "controlled_remote_speaker_truth_lab_report.json"
    if not report_path.is_file():
        raise LabError("report_missing_run_evaluate")
    expected_report = report_path.read_bytes()
    private_predictions = args.out_dir / "private/predictions"
    expected_private = {
        name: (private_predictions / name).read_bytes()
        for name in (
            "coverage_v3_predictions.jsonl",
            "wavlm_predictions.jsonl",
            "wavlm_dev_threshold_trials.json",
        )
    }
    report, outputs = evaluate_corpus(args, policy, write=False)
    mismatches = []
    if outputs["controlled_remote_speaker_truth_lab_report.json"] != expected_report:
        mismatches.append("public_report")
    for name, payload in expected_private.items():
        if outputs[name] != payload:
            mismatches.append(name)
    replay_report = {
        "schema": "murmurmark.controlled_remote_speaker_truth_lab_replay/v1",
        "deterministic": not mismatches,
        "decision": report["decision"],
        "mismatches": mismatches,
        "frozen_corpus_sha256": report["source"]["corpus_sha256"],
    }
    assert_public_safe(replay_report)
    write_json(args.out_dir / "replay_report.json", replay_report)
    if mismatches:
        raise LabError("replay_not_deterministic:" + ",".join(mismatches))
    print("replay: deterministic")
    return replay_report


def status(args: argparse.Namespace) -> int:
    report_path = args.out_dir / "controlled_remote_speaker_truth_lab_report.json"
    if not report_path.is_file():
        frozen = args.out_dir / "private/frozen_manifest.json"
        print("decision: BLOCKED")
        print(f"corpus_frozen: {str(frozen.is_file()).lower()}")
        print("next: murmurmark corpus remote-truth-lab build")
        return 2
    report = read_json(report_path)
    candidate = report["evaluation"]["wavlm_open_set_candidate"]["hard"]
    print(f"decision: {report['decision']}")
    print(f"scenarios: {report['corpus']['scenario_count']}")
    print(f"hard_bcubed_f1: {candidate['bcubed']['f1']:.6f}")
    print(f"hard_pairwise_precision: {candidate['pairwise']['precision']:.6f}")
    print(f"hard_boundary_recall: {candidate['boundary_recall']:.6f}")
    print(f"open_set_false_attributions: {candidate['open_set_false_attributions']}")
    print(f"blockers: {len(report['blockers'])}")
    print(f"report: {portable(report_path)}")
    return 0 if report["decision"] == "LAB_READY" else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and evaluate exact-scripted local remote-speaker truth."
    )
    parser.add_argument("action", choices=("build", "evaluate", "status", "replay"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--fixture-mode", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    return args


def main() -> int:
    args = parse_args()
    try:
        policy = load_policy(args.policy)
        if args.action == "build":
            build_corpus(args, policy)
            return 0
        if args.action == "evaluate":
            report, _ = evaluate_corpus(args, policy, write=True)
            print(f"decision: {report['decision']}")
            print(f"report: {portable(args.out_dir / 'controlled_remote_speaker_truth_lab_report.json')}")
            return 0 if report["decision"] == "LAB_READY" else 2
        if args.action == "replay":
            replay(args, policy)
            return 0
        return status(args)
    except (LabError, OSError, ValueError, KeyError, json.JSONDecodeError, subprocess.SubprocessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
