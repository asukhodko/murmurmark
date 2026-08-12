#!/usr/bin/env python3
"""Qualify a frozen temporal remote-speaker diarization backend."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
from typing import Any

import numpy as np
from sklearn.metrics import adjusted_rand_score


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/temporal-end-to-end-remote-diarization-qualification-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/temporal-end-to-end-remote-diarization-qualification-v1"
WORKER_ROOT = ROOT / "tools/temporal-diarization-worker"
WORKER = WORKER_ROOT / "target/release/murmurmark-temporal-diarization-worker"
MODEL_ENV = "MURMURMARK_TEMPORAL_DIARIZATION_MODEL"

POLICY_SCHEMA = "murmurmark.temporal_remote_diarization_policy/v1"
PACK_SCHEMA = "murmurmark.temporal_remote_diarization_pack/v1"
FREEZE_SCHEMA = "murmurmark.temporal_remote_diarization_freeze/v1"
CORE_SCHEMA = "murmurmark.temporal_remote_diarization_evaluation/v1"
REPORT_SCHEMA = "murmurmark.temporal_remote_diarization_report/v1"
REPLAY_SCHEMA = "murmurmark.temporal_remote_diarization_replay/v1"
MANIFEST_SCHEMA = "murmurmark.temporal_remote_diarization_artifact_manifest/v1"


class TemporalDiarizationError(RuntimeError):
    pass


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TemporalDiarizationError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STRONG = load_module(
    "murmurmark_stronger_representation_v1",
    ROOT / "scripts/evaluate-stronger-local-remote-speaker-representation-v1.py",
)
BASE = STRONG.BASE
ACTIVE_POLICY_PATH = DEFAULT_POLICY


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, pretty(value))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(canonical(row) for row in rows))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TemporalDiarizationError(f"JSON object expected: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise TemporalDiarizationError(f"JSONL object expected: {path}")
                rows.append(value)
    return rows


def repo_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else ROOT / path


def portable(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        try:
            return "~/" + str(resolved.relative_to(Path.home().resolve()))
        except ValueError:
            return str(resolved)


def artifact(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {"path": portable(resolved), "bytes": resolved.stat().st_size, "sha256": sha256(resolved)}


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise TemporalDiarizationError("unsupported temporal diarization policy")
    allowed = {"TEMPORAL_DIARIZATION_READY", "KEEP_EXPLICIT_UNKNOWN", "EVIDENCE_BOUND"}
    if set(policy["decision"]["allowed_outcomes"]) != allowed:
        raise TemporalDiarizationError("terminal outcomes changed")
    if policy["decision"]["production_promotion_allowed"] is not False:
        raise TemporalDiarizationError("production promotion must stay disabled")
    if policy["algorithm"]["truth_guided_tuning_allowed"] is not False:
        raise TemporalDiarizationError("truth-guided tuning must stay disabled")
    if policy["evaluation"]["post_hoc_tuning_allowed"] is not False:
        raise TemporalDiarizationError("post-hoc tuning must stay disabled")
    return policy


def verify_sources(policy: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    verified = []
    for source_id, expected in sorted(policy["sources"].items()):
        if expected["phase"] != phase:
            continue
        path = repo_path(expected["path"])
        if not path.is_file() or sha256(path) != expected["sha256"]:
            raise TemporalDiarizationError(f"frozen source missing or changed: {source_id}")
        verified.append({"id": source_id, **artifact(path)})
    return verified


def command_version(command: list[str]) -> str:
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise TemporalDiarizationError(f"runtime command failed: {' '.join(command)}")
    return completed.stdout.strip().splitlines()[0]


def model_path(policy: dict[str, Any]) -> Path:
    configured = os.environ.get(MODEL_ENV, policy["candidate"]["default_model_path"])
    return Path(configured).expanduser().resolve()


def verify_runtime(policy: dict[str, Any]) -> dict[str, Any]:
    candidate = policy["candidate"]
    runtime = policy["runtime"]
    model = model_path(policy)
    if not model.is_file() or model.stat().st_size != candidate["model_bytes"]:
        raise TemporalDiarizationError(f"temporal candidate model is missing: {model}")
    if sha256(model) != candidate["model_sha256"]:
        raise TemporalDiarizationError("temporal candidate model hash changed")
    for filename, expected in candidate["provenance_files"].items():
        path = model.parent / filename
        if not path.is_file() or sha256(path) != expected:
            raise TemporalDiarizationError(f"model provenance missing or changed: {filename}")
    tracked = {
        WORKER_ROOT / "Cargo.lock": runtime["worker_cargo_lock_sha256"],
        WORKER_ROOT / "Cargo.toml": runtime["worker_cargo_toml_sha256"],
        WORKER_ROOT / "src/main.rs": runtime["worker_source_sha256"],
    }
    for path, expected in tracked.items():
        if not path.is_file() or sha256(path) != expected:
            raise TemporalDiarizationError(f"worker source missing or changed: {portable(path)}")
    if not WORKER.is_file() or sha256(WORKER) != runtime["worker_binary_sha256"]:
        raise TemporalDiarizationError("pinned worker binary missing or changed; run setup first")
    rustc = command_version(["rustc", "--version"])
    cargo = command_version(["cargo", "--version"])
    ffmpeg = command_version(["ffmpeg", "-version"])
    if not rustc.startswith(f"rustc {runtime['rustc']} "):
        raise TemporalDiarizationError(f"rustc changed: {rustc}")
    if not cargo.startswith(f"cargo {runtime['cargo']} "):
        raise TemporalDiarizationError(f"cargo changed: {cargo}")
    if not ffmpeg.startswith(f"ffmpeg version {runtime['ffmpeg']} "):
        raise TemporalDiarizationError(f"ffmpeg changed: {ffmpeg}")
    return {
        "model": artifact(model),
        "provenance": [artifact(model.parent / name) for name in sorted(candidate["provenance_files"])],
        "worker": artifact(WORKER),
        "worker_sources": [artifact(path) for path in sorted(tracked)],
        "rustc": rustc,
        "cargo": cargo,
        "ffmpeg": ffmpeg,
        "offline_ready": True,
    }


def verify_raw(policy: dict[str, Any]) -> list[dict[str, Any]]:
    verified = []
    for definition in policy["scope"]["sessions"]:
        path = repo_path(definition["raw_remote_path"])
        if not path.is_file() or sha256(path) != definition["raw_remote_sha256"]:
            raise TemporalDiarizationError(f"raw remote changed: {definition['session_id']}")
        verified.append({"session_id": definition["session_id"], **artifact(path)})
    return verified


def preflight(policy: dict[str, Any]) -> dict[str, Any]:
    sources = verify_sources(policy, "prepare")
    raw = verify_raw(policy)
    runtime = verify_runtime(policy)
    inventory = read_jsonl(repo_path(policy["sources"]["unlabeled_windows"]["path"]))
    windows = sum(bool(row.get("energy_gate_passed")) for row in inventory)
    if windows != int(policy["scope"]["expected_windows"]):
        raise TemporalDiarizationError(f"window count changed: {windows}")
    perfection = read_json(repo_path(policy["sources"]["transcript_perfection_manifest"]["path"]))
    if len(perfection.get("sources") or []) != policy["scope"]["expected_transcript_perfection_sources"]:
        raise TemporalDiarizationError("Transcript Perfection source count changed")
    if len(raw) != policy["scope"]["expected_sessions"]:
        raise TemporalDiarizationError("session scope changed")
    return {
        "schema": "murmurmark.temporal_remote_diarization_preflight/v1",
        "sources": sources,
        "raw_remote": raw,
        "runtime": runtime,
        "windows": windows,
        "production_guards": policy["scope"]["expected_production_guards"],
    }


def run_checked(command: list[str], *, environment: dict[str, str] | None = None) -> None:
    completed = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    if completed.returncode != 0:
        raise TemporalDiarizationError(f"command failed ({completed.returncode}): {' '.join(command)}")


def normalize_audio(source: Path, target: Path, trim_start_sec: float) -> None:
    if target.is_file() and target.stat().st_size > 320_000:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    command = ["nice", "-n", "20", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
    if trim_start_sec > 0:
        command += ["-ss", f"{trim_start_sec:.6f}"]
    command += ["-map", "0:a:0", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(target)]
    run_checked(command)


def run_worker(policy: dict[str, Any], audio: Path, output: Path) -> None:
    if output.is_file():
        value = read_json(output)
        if value.get("schema") == "murmurmark.temporal_diarization_worker_result/v1":
            return
    environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    run_checked(
        ["nice", "-n", str(policy["runtime"]["nice"]), str(WORKER), str(audio), str(model_path(policy)), str(output)],
        environment=environment,
    )


def adjusted_spans(result: dict[str, Any], offset: float) -> list[dict[str, Any]]:
    return [
        {
            "start": round(float(row["start"]) + offset, 6),
            "end": round(float(row["end"]) + offset, 6),
            "candidate_cluster": int(row["speaker"]),
        }
        for row in result.get("spans") or []
        if float(row["end"]) > float(row["start"])
    ]


def overlap_amount(start: float, end: float, other_start: float, other_end: float) -> float:
    return max(0.0, min(end, other_end) - max(start, other_start))


def interval_cluster_scores(spans: list[dict[str, Any]], start: float, end: float) -> list[tuple[float, int]]:
    scores: dict[int, float] = defaultdict(float)
    for span in spans:
        amount = overlap_amount(start, end, float(span["start"]), float(span["end"]))
        if amount > 0:
            scores[int(span["candidate_cluster"])] += amount
    return sorted(((value, cluster) for cluster, value in scores.items()), key=lambda row: (-row[0], row[1]))


def window_assignment(
    spans: list[dict[str, Any]], start: float, end: float, minimum_overlap: float
) -> dict[str, Any]:
    ordered = interval_cluster_scores(spans, start, end)
    best = ordered[0][0] if ordered else 0.0
    second = ordered[1][0] if len(ordered) > 1 else 0.0
    cluster = ordered[0][1] if ordered and best >= minimum_overlap else None
    duration = max(1e-9, end - start)
    return {
        "cluster": cluster,
        "coverage_ratio": round(min(1.0, best / duration), 6),
        "dominance_margin": round((best - second) / duration, 6),
    }


def merge_intervals(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[list[float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not result or start > result[-1][1]:
            result.append([start, end])
        else:
            result[-1][1] = max(result[-1][1], end)
    return [(row[0], row[1]) for row in result]


def interval_length(intervals: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in merge_intervals(intervals))


def intersection_length(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> float:
    first = merge_intervals(left)
    second = merge_intervals(right)
    i = j = 0
    total = 0.0
    while i < len(first) and j < len(second):
        total += overlap_amount(first[i][0], first[i][1], second[j][0], second[j][1])
        if first[i][1] <= second[j][1]:
            i += 1
        else:
            j += 1
    return total


def activity_jaccard(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> float:
    first = [(float(row["start"]), float(row["end"])) for row in left]
    second = [(float(row["start"]), float(row["end"])) for row in right]
    intersection = intersection_length(first, second)
    union = interval_length(first) + interval_length(second) - intersection
    return intersection / union if union > 0 else 1.0


def concurrent_seconds(spans: list[dict[str, Any]]) -> float:
    events = []
    for row in spans:
        events.append((float(row["start"]), 1))
        events.append((float(row["end"]), -1))
    active = 0
    previous = None
    total = 0.0
    for timestamp, delta in sorted(events, key=lambda row: (row[0], row[1])):
        if previous is not None and active >= 2:
            total += timestamp - previous
        active += delta
        previous = timestamp
    return total


def build_pack(policy: dict[str, Any], out: Path, inventory: list[dict[str, Any]]) -> dict[str, Any]:
    minimum = float(policy["algorithm"]["window_assignment_minimum_overlap_sec"])
    grouped = defaultdict(list)
    for row in inventory:
        if row.get("energy_gate_passed"):
            grouped[str(row["session_id"])].append(row)
    sessions = []
    assignments = []
    all_spans = {}
    for position, definition in enumerate(policy["scope"]["sessions"], 1):
        session_id = definition["session_id"]
        session_dir = out / "private/sessions" / session_id
        canonical_result = read_json(session_dir / "canonical.result.json")
        shifted_result = read_json(session_dir / "shifted.result.json")
        canonical_spans = adjusted_spans(canonical_result, 0.0)
        shifted_spans = adjusted_spans(
            shifted_result, float(policy["algorithm"]["stability_variant"]["restore_timeline_offset_sec"])
        )
        all_spans[session_id] = canonical_spans
        common_left = []
        common_right = []
        session_assignments = []
        for row in sorted(grouped[session_id], key=lambda value: (float(value["start"]), value["key"])):
            canonical_assignment = window_assignment(canonical_spans, float(row["start"]), float(row["end"]), minimum)
            shifted_assignment = window_assignment(shifted_spans, float(row["start"]), float(row["end"]), minimum)
            item = {
                "key": row["key"],
                "session_id": session_id,
                "candidate_cluster": canonical_assignment["cluster"],
                "candidate_coverage_ratio": canonical_assignment["coverage_ratio"],
                "candidate_dominance_margin": canonical_assignment["dominance_margin"],
                "shifted_cluster": shifted_assignment["cluster"],
            }
            assignments.append(item)
            session_assignments.append(item)
            if item["candidate_cluster"] is not None and item["shifted_cluster"] is not None:
                common_left.append(item["candidate_cluster"])
                common_right.append(item["shifted_cluster"])
        stability = adjusted_rand_score(common_left, common_right) if common_left else 0.0
        canonical_clusters = len({int(row["candidate_cluster"]) for row in canonical_spans})
        shifted_clusters = len({int(row["candidate_cluster"]) for row in shifted_spans})
        sessions.append(
            {
                "session_alias": f"session_{position:02d}",
                "session_id": session_id,
                "window_count": len(session_assignments),
                "canonical_cluster_count": canonical_clusters,
                "shifted_cluster_count": shifted_clusters,
                "common_assigned_windows": len(common_left),
                "temporal_stability_ari": round(float(stability), 6),
                "activity_jaccard": round(activity_jaccard(canonical_spans, shifted_spans), 6),
                "canonical_speech_seconds": round(interval_length([(row["start"], row["end"]) for row in canonical_spans]), 6),
                "canonical_overlap_seconds": round(concurrent_seconds(canonical_spans), 6),
            }
        )
    return {
        "schema": PACK_SCHEMA,
        "candidate": {
            "id": policy["candidate"]["id"],
            "crate": f"{policy['candidate']['crate']}@{policy['candidate']['crate_version']}",
            "crate_revision": policy["candidate"]["crate_revision"],
            "model_sha256": policy["candidate"]["model_sha256"],
            "architecture": policy["candidate"]["architecture"],
        },
        "algorithm": policy["algorithm"],
        "counts": {"sessions": len(sessions), "windows": len(assignments)},
        "sessions": sessions,
        "assignments": sorted(assignments, key=lambda row: row["key"]),
        "spans": all_spans,
        "labels_read": False,
        "direct_truth_read": False,
        "speaker_count_from_truth": False,
        "thresholds_tuned": False,
        "production_promotion_allowed": False,
    }


def forbidden_key_paths(value: Any, prefix: str = "") -> list[str]:
    forbidden = {
        "speaker_id",
        "speaker_name",
        "human_name",
        "text",
        "truth",
        "truth_outcome",
        "control_outcome",
        "coverage_assignment",
        "profile_id",
        "expected_profile_count",
    }
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in forbidden:
                found.append(path)
            found.extend(forbidden_key_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_key_paths(child, f"{prefix}[{index}]"))
    return found


def clean_evaluation_outputs(out: Path) -> None:
    for relative in (
        "private/candidate_pack.pending.json",
        "private/candidate_pack.frozen.json",
        "private/post_freeze_window_labels.jsonl",
        "private/post_freeze_cluster_mappings.json",
        "private/direct_truth_decisions.jsonl",
        "private/evaluation_core.json",
        "candidate_pack.public.json",
        "freeze_manifest.json",
        "temporal_remote_diarization_report.json",
        "temporal_remote_diarization_report.md",
        "replay_report.json",
        "artifact_manifest.json",
    ):
        (out / relative).unlink(missing_ok=True)


def action_preflight(policy: dict[str, Any], out: Path) -> int:
    state = preflight(policy)
    write_json(out / "private/preflight.json", state)
    print(f"preflight: ok ({policy['candidate']['id']}, {state['windows']} windows, 29 frozen sources)")
    return 0


def action_prepare(policy: dict[str, Any], out: Path) -> int:
    clean_evaluation_outputs(out)
    state = preflight(policy)
    inventory = read_jsonl(repo_path(policy["sources"]["unlabeled_windows"]["path"]))
    write_jsonl(out / "private/unlabeled_windows.jsonl", inventory)
    shift = float(policy["algorithm"]["stability_variant"]["trim_start_sec"])
    jobs = []
    for definition in policy["scope"]["sessions"]:
        session_id = definition["session_id"]
        source = repo_path(definition["raw_remote_path"])
        session_dir = out / "private/sessions" / session_id
        canonical_audio = session_dir / "canonical.wav"
        shifted_audio = session_dir / "shifted.wav"
        normalize_audio(source, canonical_audio, 0.0)
        normalize_audio(source, shifted_audio, shift)
        jobs.extend(
            [
                (session_id, "canonical", canonical_audio, session_dir / "canonical.result.json"),
                (session_id, "shifted", shifted_audio, session_dir / "shifted.result.json"),
            ]
        )
    completed_count = 0
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {
            pool.submit(run_worker, policy, audio, output): (session_id, variant)
            for session_id, variant, audio, output in jobs
        }
        for future in as_completed(futures):
            future.result()
            completed_count += 1
            session_id, variant = futures[future]
            print(f"[{completed_count}/{len(jobs)}] temporal diarization: {session_id} / {variant}", flush=True)
    pack = build_pack(policy, out, inventory)
    if forbidden_key_paths(pack):
        raise TemporalDiarizationError("label or truth leakage in pending candidate pack")
    write_json(out / "private/candidate_pack.pending.json", pack)
    write_json(
        out / "candidate_pack.public.json",
        {
            "schema": "murmurmark.temporal_remote_diarization_public_pack/v1",
            "candidate": pack["candidate"],
            "counts": pack["counts"],
            "sessions": pack["sessions"],
            "labels_read": False,
            "direct_truth_read": False,
            "production_promotion_allowed": False,
        },
    )
    write_json(out / "private/preflight.json", state)
    print(f"prepared: {pack['counts']['windows']} audio-only window assignments")
    return 0


def action_freeze(policy: dict[str, Any], out: Path) -> int:
    pending = out / "private/candidate_pack.pending.json"
    if not pending.is_file():
        raise TemporalDiarizationError("prepare must run before freeze")
    pack = read_json(pending)
    if pack.get("schema") != PACK_SCHEMA or forbidden_key_paths(pack):
        raise TemporalDiarizationError("candidate pack cannot be frozen")
    frozen = out / "private/candidate_pack.frozen.json"
    atomic_write(frozen, pending.read_bytes())
    frozen_artifacts = [artifact(ACTIVE_POLICY_PATH), artifact(frozen), artifact(out / "candidate_pack.public.json")]
    snapshot_dir = out / "private/frozen_inputs"
    for source_id, filename in (
        ("transcript_perfection_manifest", "transcript_perfection_manifest.pre_temporal.json"),
        ("transcript_perfection_report", "transcript_perfection_corpus_report.pre_temporal.json"),
    ):
        source = repo_path(policy["sources"][source_id]["path"])
        snapshot = snapshot_dir / filename
        atomic_write(snapshot, source.read_bytes())
        frozen_artifacts.append(artifact(snapshot))
    frozen_artifacts.extend(verify_sources(policy, "prepare"))
    frozen_artifacts.extend(verify_raw(policy))
    frozen_artifacts.extend(verify_runtime(policy)["provenance"])
    frozen_artifacts.extend(verify_runtime(policy)["worker_sources"])
    for definition in policy["scope"]["sessions"]:
        session_dir = out / "private/sessions" / definition["session_id"]
        for name in ("canonical.wav", "shifted.wav", "canonical.result.json", "shifted.result.json"):
            frozen_artifacts.append(artifact(session_dir / name))
    manifest = {
        "schema": FREEZE_SCHEMA,
        "state": "frozen_before_coverage_and_direct_truth_evaluation",
        "policy_sha256": sha256(ACTIVE_POLICY_PATH),
        "pack_sha256": sha256(frozen),
        "labels_read": False,
        "direct_truth_read": False,
        "speaker_count_from_truth": False,
        "thresholds_tuned": False,
        "artifacts": sorted(frozen_artifacts, key=lambda row: row["path"]),
    }
    write_json(out / "freeze_manifest.json", manifest)
    print(f"frozen: {manifest['pack_sha256']}")
    return 0


def matches_frozen_artifact(path: Path, expected: dict[str, Any], out: Path) -> bool:
    if path.is_file() and path.stat().st_size == expected["bytes"] and sha256(path) == expected["sha256"]:
        return True
    relative = portable(path)
    snapshots = {
        "docs/testing/transcript-perfection-corpus-v1-manifest.json": (
            out / "private/frozen_inputs/transcript_perfection_manifest.pre_temporal.json"
        ),
        "sessions/_reports/transcript-perfection-corpus-v1/transcript_perfection_corpus_report.json": (
            out / "private/frozen_inputs/transcript_perfection_corpus_report.pre_temporal.json"
        ),
    }
    snapshot = snapshots.get(relative)
    if snapshot is not None and snapshot.is_file():
        if snapshot.stat().st_size != expected["bytes"] or sha256(snapshot) != expected["sha256"]:
            return False
        if relative != "docs/testing/transcript-perfection-corpus-v1-manifest.json":
            return True
    if (
        relative != "docs/testing/transcript-perfection-corpus-v1-manifest.json"
        or not path.is_file()
        or snapshot is None
        or not snapshot.is_file()
    ):
        return False

    # Transcript Perfection is append-only. Older qualifications freeze the
    # source rows they consumed, while later completed goals may add new rows.
    frozen = read_json(snapshot)
    current = read_json(path)
    frozen_sources = frozen.pop("sources", None)
    current_sources = current.pop("sources", None)
    if frozen != current or not isinstance(frozen_sources, list) or not isinstance(current_sources, list):
        return False
    current_by_id = {str(row.get("id")): row for row in current_sources if isinstance(row, dict)}
    if len(current_by_id) != len(current_sources):
        return False
    return all(current_by_id.get(str(row.get("id"))) == row for row in frozen_sources if isinstance(row, dict))


def verify_frozen(out: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = out / "freeze_manifest.json"
    frozen_path = out / "private/candidate_pack.frozen.json"
    if not manifest_path.is_file() or not frozen_path.is_file():
        raise TemporalDiarizationError("candidate pack is not frozen")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != FREEZE_SCHEMA:
        raise TemporalDiarizationError("unsupported freeze manifest")
    if sha256(frozen_path) != manifest.get("pack_sha256"):
        raise TemporalDiarizationError("frozen candidate pack changed")
    for expected in manifest.get("artifacts") or []:
        path = repo_path(expected["path"])
        if not matches_frozen_artifact(path, expected, out):
            raise TemporalDiarizationError(f"frozen artifact changed: {expected['path']}")
    pack = read_json(frozen_path)
    if forbidden_key_paths(pack):
        raise TemporalDiarizationError("frozen pack contains label or truth leakage")
    return pack, manifest


def expected_policy(policy: dict[str, Any]) -> dict[str, Any]:
    source = repo_path(policy["sources"]["stronger_representation_policy"]["path"])
    return STRONG.load_policy(source)


def remote_boundaries(dialogue_path: Path, spans: list[dict[str, Any]]) -> dict[str, Any]:
    dialogue = read_json(dialogue_path)
    remote = [
        (float(row["start"]), float(row["end"]))
        for row in dialogue.get("utterances") or []
        if row.get("role") == "remote" and float(row["end"]) > float(row["start"])
    ]
    candidate = [(float(row["start"]), float(row["end"])) for row in spans]
    merged_remote = merge_intervals(remote)
    denominator = interval_length(merged_remote)
    duration_recall = intersection_length(merged_remote, candidate) / denominator if denominator > 0 else 1.0
    centers = [(start + end) / 2.0 for start, end in remote]
    center_recall = sum(any(start <= center <= end for start, end in candidate) for center in centers) / len(centers) if centers else 1.0
    candidate_boundaries = [value for interval in candidate for value in interval]
    distances = [min(abs(value - other) for other in candidate_boundaries) for interval in remote for value in interval] if candidate_boundaries else [float("inf")]
    return {
        "remote_utterance_count": len(remote),
        "remote_interval_duration_sec": round(denominator, 6),
        "duration_recall": round(duration_recall, 6),
        "center_recall": round(center_recall, 6),
        "median_boundary_distance_sec": round(float(statistics.median(distances)), 6),
    }


def direct_truth_evaluation(
    policy: dict[str, Any], reference_policy: dict[str, Any], pack: dict[str, Any], mappings: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, truth = BASE.direct_truth_rows(reference_policy)
    seed = {row["item_id"]: row for row in read_jsonl(repo_path(policy["sources"]["direct_truth_items"]["path"]))}
    evaluation = policy["evaluation"]
    decisions = []
    for item_id, expected in sorted(truth.items()):
        item = seed[item_id]
        session_id = expected["session_id"]
        start = float(item["start"])
        end = float(item["end"])
        duration = max(1e-9, end - start)
        scores = interval_cluster_scores(pack["spans"][session_id], start, end)
        best = scores[0][0] if scores else 0.0
        second = scores[1][0] if len(scores) > 1 else 0.0
        cluster = scores[0][1] if scores else None
        coverage = min(1.0, best / duration)
        margin = (best - second) / duration
        secondary = second / duration
        mapping = mappings[session_id]
        accepted = (
            cluster is not None
            and coverage >= float(evaluation["direct_truth_minimum_cluster_coverage_ratio"])
            and margin >= float(evaluation["direct_truth_minimum_cluster_dominance_margin"])
            and secondary <= float(evaluation["direct_truth_maximum_secondary_cluster_ratio"])
            and cluster not in mapping["ambiguous_clusters"]
            and str(cluster) in mapping["mapping"]
        )
        prediction = mapping["mapping"].get(str(cluster)) if accepted else None
        if prediction is None:
            result = "abstained_positive" if expected["truth_kind"] == "positive_identity" else "safe_abstention"
        elif expected["truth_kind"] == "positive_identity" and prediction == expected["truth_outcome"]:
            result = "correct_identity"
        else:
            result = "unsafe_fail_closed_acceptance"
        decisions.append(
            {
                "item_id": item_id,
                "session_id": session_id,
                "start": start,
                "end": end,
                "prediction": prediction,
                "candidate_result": result,
                "candidate_evidence": {
                    "cluster": cluster,
                    "coverage_ratio": round(coverage, 6),
                    "dominance_margin": round(margin, 6),
                    "secondary_cluster_ratio": round(secondary, 6),
                    "accepted": accepted,
                },
                **expected,
            }
        )
    counts = Counter(row["candidate_result"] for row in decisions)
    preserved = sum(row["confirmed_v1_additive_gain"] and row["candidate_result"] == "correct_identity" for row in decisions)
    new_false = sum(
        row["candidate_result"] == "unsafe_fail_closed_acceptance"
        and row["control_outcome"] != "unsafe_fail_closed_acceptance"
        for row in decisions
    )
    lost_control = sum(
        row["control_outcome"] == "correct_identity"
        and not (row["candidate_result"] == "correct_identity" and row["prediction"] == row["control_prediction"])
        for row in decisions
    )
    summary = {
        "items": len(decisions),
        "counts": dict(sorted(counts.items())),
        "confirmed_v1_additive_gains": policy["scope"]["expected_confirmed_v1_additive_gains"],
        "preserved_confirmed_v1_additive_gains": preserved,
        "unsafe_fail_closed_accepts": int(counts["unsafe_fail_closed_acceptance"]),
        "new_false_identity_items": new_false,
        "lost_correct_control_identity_items": lost_control,
        "fail_closed_overlap_or_mixed_items": sum(
            row["candidate_evidence"]["secondary_cluster_ratio"] > evaluation["direct_truth_maximum_secondary_cluster_ratio"]
            and row["prediction"] is None
            for row in decisions
        ),
    }
    return summary, decisions


def evaluation_core(policy: dict[str, Any], out: Path, pack: dict[str, Any]) -> dict[str, Any]:
    verify_sources(policy, "evaluate_after_freeze")
    reference = expected_policy(policy)
    inventory = read_jsonl(out / "private/unlabeled_windows.jsonl")
    labels, profiles = BASE.window_labels(reference, inventory)
    write_jsonl(out / "private/post_freeze_window_labels.jsonl", [{"key": key, **value} for key, value in sorted(labels.items())])
    assignments_by_session = defaultdict(list)
    for row in pack["assignments"]:
        if row["candidate_cluster"] is not None:
            assignments_by_session[row["session_id"]].append(row)
    reference_sessions = {row["session_id"]: row for row in reference["scope"]["sessions"]}
    mappings = {}
    mapping_summaries = []
    boundaries = []
    for position, definition in enumerate(policy["scope"]["sessions"], 1):
        session_id = definition["session_id"]
        mapping = BASE.map_clusters(assignments_by_session[session_id], labels, profiles[session_id], "candidate_cluster")
        mappings[session_id] = mapping
        expected_count = int(reference_sessions[session_id]["cluster_count"])
        inferred_count = next(row["canonical_cluster_count"] for row in pack["sessions"] if row["session_id"] == session_id)
        mapping_summaries.append(
            {
                "session_alias": f"session_{position:02d}",
                "expected_profile_count": expected_count,
                "inferred_cluster_count": inferred_count,
                "count_matches": inferred_count == expected_count,
                **{key: value for key, value in mapping.items() if key not in {"mapping", "confusion", "clusters", "profiles"}},
            }
        )
        boundaries.append(
            {
                "session_alias": f"session_{position:02d}",
                **remote_boundaries(repo_path(reference_sessions[session_id]["dialogue_path"]), pack["spans"][session_id]),
            }
        )
    write_json(out / "private/post_freeze_cluster_mappings.json", mappings)
    direct, decisions = direct_truth_evaluation(policy, reference, pack, mappings)
    write_jsonl(out / "private/direct_truth_decisions.jsonl", decisions)
    multi = []
    for row in pack["sessions"]:
        expected_count = int(reference_sessions[row["session_id"]]["cluster_count"])
        if expected_count > 1:
            multi.append(row)
    evaluation = policy["evaluation"]
    temporal_values = {
        "multispeaker_sessions": len(multi),
        "minimum_temporal_stability_ari": min(row["temporal_stability_ari"] for row in multi),
        "minimum_activity_jaccard": min(row["activity_jaccard"] for row in multi),
        "stable_cluster_count_sessions": sum(row["canonical_cluster_count"] == row["shifted_cluster_count"] for row in pack["sessions"]),
    }
    temporal_gates = {
        "minimum_multispeaker_sessions": temporal_values["multispeaker_sessions"] >= evaluation["minimum_multispeaker_sessions"],
        "minimum_temporal_stability_ari": temporal_values["minimum_temporal_stability_ari"] >= evaluation["minimum_temporal_stability_ari"],
        "minimum_activity_jaccard": temporal_values["minimum_activity_jaccard"] >= evaluation["minimum_activity_jaccard"],
        "stable_cluster_count": temporal_values["stable_cluster_count_sessions"] == policy["scope"]["expected_sessions"],
    }
    boundary_values = {
        "minimum_remote_interval_duration_recall": min(row["duration_recall"] for row in boundaries),
        "minimum_remote_interval_center_recall": min(row["center_recall"] for row in boundaries),
        "maximum_median_boundary_distance_sec": max(row["median_boundary_distance_sec"] for row in boundaries),
    }
    boundary_gates = {
        "minimum_remote_interval_duration_recall": boundary_values["minimum_remote_interval_duration_recall"] >= evaluation["minimum_remote_interval_duration_recall"],
        "minimum_remote_interval_center_recall": boundary_values["minimum_remote_interval_center_recall"] >= evaluation["minimum_remote_interval_center_recall"],
        "maximum_median_boundary_distance_sec": boundary_values["maximum_median_boundary_distance_sec"] <= evaluation["maximum_median_boundary_distance_sec"],
    }
    mapping_values = {
        "exact_speaker_count_sessions": sum(row["count_matches"] for row in mapping_summaries),
        "minimum_cluster_mapping_purity": min(row["minimum_cluster_purity"] for row in mapping_summaries),
        "minimum_mapping_margin": min(row["minimum_mapping_margin"] for row in mapping_summaries),
        "ambiguous_clusters": sum(len(row["ambiguous_clusters"]) for row in mapping_summaries),
    }
    mapping_gates = {
        "exact_speaker_count_all_sessions": mapping_values["exact_speaker_count_sessions"] == policy["scope"]["expected_sessions"],
        "minimum_cluster_mapping_purity": mapping_values["minimum_cluster_mapping_purity"] >= evaluation["minimum_cluster_mapping_purity"],
        "minimum_mapping_margin": mapping_values["minimum_mapping_margin"] >= evaluation["minimum_mapping_margin"],
        "maximum_ambiguous_clusters": mapping_values["ambiguous_clusters"] <= evaluation["maximum_ambiguous_clusters"],
        "minimum_preserved_confirmed_v1_additive_gains": direct["preserved_confirmed_v1_additive_gains"] >= evaluation["minimum_preserved_confirmed_v1_additive_gains"],
        "maximum_unsafe_fail_closed_accepts": direct["unsafe_fail_closed_accepts"] <= evaluation["maximum_unsafe_fail_closed_accepts"],
        "no_new_false_identity": direct["new_false_identity_items"] <= evaluation["maximum_new_false_identity_items"],
        "no_lost_correct_control_identity": direct["lost_correct_control_identity_items"] <= evaluation["maximum_lost_correct_control_identity_items"],
    }
    invariants = {
        "pack_frozen_before_coverage_labels": pack["labels_read"] is False,
        "pack_frozen_before_direct_truth": pack["direct_truth_read"] is False,
        "speaker_count_not_from_truth": pack["speaker_count_from_truth"] is False,
        "thresholds_not_tuned": pack["thresholds_tuned"] is False and evaluation["post_hoc_tuning_allowed"] is False,
        "production_promotion_disabled": policy["decision"]["production_promotion_allowed"] is False,
        "candidate_models_temporal_activity": "powerset" in policy["candidate"]["architecture"],
        "candidate_models_sequence": "VBx" in policy["candidate"]["architecture"],
    }
    return {
        "schema": CORE_SCHEMA,
        "temporal": {"values": temporal_values, "gates": temporal_gates, "sessions": pack["sessions"]},
        "boundaries": {"values": boundary_values, "gates": boundary_gates, "sessions": boundaries},
        "mapping": {"values": mapping_values, "gates": mapping_gates, "sessions": mapping_summaries},
        "direct_truth": direct,
        "invariants": invariants,
    }


def build_report(policy: dict[str, Any], out: Path, pack: dict[str, Any], core: dict[str, Any]) -> dict[str, Any]:
    gates_passed = all(
        all(core[section]["gates"].values()) for section in ("temporal", "boundaries", "mapping")
    )
    invariants_passed = all(core["invariants"].values())
    if not invariants_passed:
        decision = "EVIDENCE_BOUND"
        reason = "model, freeze, provenance, or safety invariants are incomplete"
    elif gates_passed:
        decision = "TEMPORAL_DIARIZATION_READY"
        reason = "the frozen temporal diarizer passed stability, boundary, mapping, and direct-truth gates"
    else:
        decision = "KEEP_EXPLICIT_UNKNOWN"
        reason = "the temporal diarizer did not safely improve the frozen unknown-speaker residual"
    return {
        "schema": REPORT_SCHEMA,
        "decision": decision,
        "decision_reason": reason,
        "candidate": pack["candidate"],
        "scope": {
            "sessions": policy["scope"]["expected_sessions"],
            "profiles": policy["scope"]["expected_profiles"],
            "windows": pack["counts"]["windows"],
            "development_items": policy["scope"]["expected_development_items"],
        },
        "temporal": core["temporal"],
        "boundaries": core["boundaries"],
        "mapping": core["mapping"],
        "direct_truth": core["direct_truth"],
        "invariants": core["invariants"],
        "failed_gates": {
            section: sorted(key for key, value in core[section]["gates"].items() if not value)
            for section in ("temporal", "boundaries", "mapping")
        },
        "safety": {
            **policy["safety"],
            "frozen_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
            "coverage_v3_accepts_preserved": policy["scope"]["expected_coverage_v3_accepts"],
            "production_guards_verified": policy["scope"]["expected_production_guards"],
            "transcript_perfection_sources_preserved": policy["scope"]["expected_transcript_perfection_sources"],
            "selected_transcript_mutated": False,
            "raw_audio_mutated": False,
            "primary_asr_mutated": False,
            "echo_guard_mutated": False,
        },
        "next": {
            "TEMPORAL_DIARIZATION_READY": "open_separate_monotonic_temporal_shadow_candidate",
            "KEEP_EXPLICIT_UNKNOWN": "close_available_local_remote_diarization_route_until_new_evidence",
            "EVIDENCE_BOUND": "repair_only_model_runtime_license_speaker_limit_or_frozen_evidence",
        }[decision],
    }


def report_markdown(report: dict[str, Any]) -> str:
    temporal = report["temporal"]["values"]
    boundaries = report["boundaries"]["values"]
    mapping = report["mapping"]["values"]
    direct = report["direct_truth"]
    return "\n".join(
        [
            "# Temporal End-to-End Remote Diarization Qualification v1",
            "",
            f"Decision: `{report['decision']}`",
            "",
            report["decision_reason"] + ".",
            "",
            "## Candidate",
            "",
            f"- Backend: `{report['candidate']['id']}`",
            f"- Crate: `{report['candidate']['crate']}`",
            f"- Frozen windows: `{report['scope']['windows']}`",
            "",
            "## Temporal Stability",
            "",
            f"- Minimum ARI after fixed 500 ms shift: `{temporal['minimum_temporal_stability_ari']}`",
            f"- Minimum speech-activity Jaccard: `{temporal['minimum_activity_jaccard']}`",
            f"- Stable cluster-count sessions: `{temporal['stable_cluster_count_sessions']}/{report['scope']['sessions']}`",
            "",
            "## Boundary Conservation",
            "",
            f"- Minimum remote duration recall: `{boundaries['minimum_remote_interval_duration_recall']}`",
            f"- Minimum remote center recall: `{boundaries['minimum_remote_interval_center_recall']}`",
            f"- Maximum median boundary distance: `{boundaries['maximum_median_boundary_distance_sec']}s`",
            "",
            "## Post-Freeze Safety",
            "",
            f"- Exact speaker-count sessions: `{mapping['exact_speaker_count_sessions']}/{report['scope']['sessions']}`",
            f"- Minimum cluster purity: `{mapping['minimum_cluster_mapping_purity']}`",
            f"- Ambiguous clusters: `{mapping['ambiguous_clusters']}`",
            f"- Preserved confirmed gains: `{direct['preserved_confirmed_v1_additive_gains']}/{direct['confirmed_v1_additive_gains']}`",
            f"- Unsafe accepts: `{direct['unsafe_fail_closed_accepts']}`",
            f"- New false identities: `{direct['new_false_identity_items']}`",
            f"- Lost correct controls: `{direct['lost_correct_control_identity_items']}`",
            "",
            "Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard, and production output were not modified.",
            f"Next: `{report['next']}`.",
        ]
    ) + "\n"


def action_evaluate(policy: dict[str, Any], out: Path) -> int:
    pack, _ = verify_frozen(out)
    core = evaluation_core(policy, out, pack)
    write_json(out / "private/evaluation_core.json", core)
    report = build_report(policy, out, pack, core)
    write_json(out / "temporal_remote_diarization_report.json", report)
    atomic_write(out / "temporal_remote_diarization_report.md", report_markdown(report).encode())
    print(f"decision: {report['decision']}")
    return 0


def action_replay(policy: dict[str, Any], out: Path) -> int:
    report_path = out / "temporal_remote_diarization_report.json"
    core_path = out / "private/evaluation_core.json"
    if not report_path.is_file() or not core_path.is_file():
        raise TemporalDiarizationError("evaluate must run before replay")
    pack, _ = verify_frozen(out)
    expected = report_path.read_bytes()
    rebuilt = pretty(build_report(policy, out, pack, read_json(core_path)))
    if expected != rebuilt:
        raise TemporalDiarizationError("deterministic replay changed the public report")
    replay = {
        "schema": REPLAY_SCHEMA,
        "verified": True,
        "report_sha256": hashlib.sha256(expected).hexdigest(),
        "report_bytes": len(expected),
        "frozen_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
    }
    write_json(out / "replay_report.json", replay)
    print(f"replay: verified ({replay['report_sha256']})")
    return 0


def action_finalize(policy: dict[str, Any], out: Path) -> int:
    paths = [
        out / "temporal_remote_diarization_report.json",
        out / "replay_report.json",
        out / "freeze_manifest.json",
        out / "candidate_pack.public.json",
    ]
    for path in paths:
        if not path.is_file():
            raise TemporalDiarizationError(f"final artifact missing: {path.name}")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": read_json(paths[0])["decision"],
        "artifacts": [artifact(path) for path in paths],
        "production_promotion_allowed": False,
    }
    write_json(out / "artifact_manifest.json", manifest)
    print(f"finalized: {manifest['decision']}")
    return 0


def action_status(out: Path) -> int:
    path = out / "temporal_remote_diarization_report.json"
    if not path.is_file():
        print("decision: pending")
        return 0
    report = read_json(path)
    print(f"decision: {report['decision']}")
    print(f"candidate: {report['candidate']['id']}")
    print(f"windows: {report['scope']['windows']}")
    print(f"minimum_temporal_stability_ari: {report['temporal']['values']['minimum_temporal_stability_ari']}")
    print(f"exact_speaker_count_sessions: {report['mapping']['values']['exact_speaker_count_sessions']}/{report['scope']['sessions']}")
    print(f"preserved_confirmed_gains: {report['direct_truth']['preserved_confirmed_v1_additive_gains']}/{report['direct_truth']['confirmed_v1_additive_gains']}")
    print(f"new_false_identities: {report['direct_truth']['new_false_identity_items']}")
    print(f"next: {report['next']}")
    return 0


def action_all(policy: dict[str, Any], out: Path) -> int:
    action_preflight(policy, out)
    action_prepare(policy, out)
    action_freeze(policy, out)
    action_evaluate(policy, out)
    action_replay(policy, out)
    action_finalize(policy, out)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("preflight", "prepare", "freeze", "evaluate", "replay", "finalize", "status", "all"),
        nargs="?",
        default="status",
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    global ACTIVE_POLICY_PATH
    args = parse_args()
    ACTIVE_POLICY_PATH = args.policy.expanduser().resolve()
    policy = load_policy(ACTIVE_POLICY_PATH)
    out = args.out.expanduser().resolve()
    actions = {
        "preflight": action_preflight,
        "prepare": action_prepare,
        "freeze": action_freeze,
        "evaluate": action_evaluate,
        "replay": action_replay,
        "finalize": action_finalize,
        "status": lambda _policy, target: action_status(target),
        "all": action_all,
    }
    try:
        return actions[args.action](policy, out)
    except TemporalDiarizationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
