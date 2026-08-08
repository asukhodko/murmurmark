#!/usr/bin/env python3
"""Qualify one stronger local speaker-identity backend on a one-shot hard-v4."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import secrets
import shutil
import subprocess
import sys
from typing import Any, Iterable

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.stronger_remote_speaker_identity_backend_qualification_policy/v1"
SPEC_SCHEMA = "murmurmark.remote_speaker_identity_hard_v4_private_spec/v1"
FROZEN_SCHEMA = "murmurmark.remote_speaker_identity_hard_v4_frozen_manifest/v1"
PUBLIC_HARD_SCHEMA = "murmurmark.remote_speaker_identity_hard_v4_public_manifest/v1"
CANDIDATE_SCHEMA = "murmurmark.remote_speaker_identity_candidate_freeze/v1"
LEDGER_SCHEMA = "murmurmark.remote_speaker_identity_hard_v4_opening_ledger/v1"
PREDICTION_SCHEMA = "murmurmark.remote_speaker_identity_exact_event_prediction/v1"
REPORT_SCHEMA = "murmurmark.stronger_remote_speaker_identity_backend_qualification_report/v1"
REPLAY_SCHEMA = "murmurmark.stronger_remote_speaker_identity_backend_replay/v1"
TRACKED_SCHEMA = "murmurmark.stronger_remote_speaker_identity_backend_qualification_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/stronger-remote-speaker-identity-backend-qualification-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/stronger-remote-speaker-identity-backend-qualification-v1"
DEFAULT_TRACKED = ROOT / "docs/testing/stronger-remote-speaker-identity-backend-qualification-v1-manifest.json"
BASE_BUILDER = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
BASE_POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
HARD_V2_SPEC = ROOT / "sessions/_reports/duration-aware-remote-speaker-attribution-v2/private/hard-v2/hard_v2_spec.json"
HARD_V3_SPEC = ROOT / "sessions/_reports/segment-context-remote-speaker-attribution-v1/private/hard-v3/hard_v3_spec.json"
ECAPA_WORKER = ROOT / "scripts/ecapa-speaker-embedding-worker.py"


class QualificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioRequest:
    key: str
    path: Path
    start: float
    end: float
    minimum_sec: float


@dataclass
class Corpus:
    corpus_id: str
    enrollment: dict[str, list[AudioRequest]]
    scenarios: list[dict[str, Any]]


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def compact_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"invalid_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise QualificationError(f"json_object_required:{path.name}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as error:
        raise QualificationError(f"invalid_jsonl:{path.name}:{type(error).__name__}") from error
    if not all(isinstance(row, dict) for row in rows):
        raise QualificationError(f"jsonl_objects_required:{path.name}")
    return rows


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def write_json(path: Path, value: Any) -> None:
    atomic_write(path, canonical_json(value))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(compact_json(row) for row in rows))


def portable(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return f"external/{path.name}"


def load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_identity_truth_lab_v1", BASE_BUILDER)
    if spec is None or spec.loader is None:
        raise QualificationError("truth_lab_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def resolve_repo_path(value: str, repo_root: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def backend_rows(policy: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = policy.get("shortlist") or []
    if not 2 <= len(rows) <= 3:
        raise QualificationError("shortlist_must_contain_two_or_three_backends")
    controls = [row for row in rows if row.get("role") == "control"]
    candidates = [row for row in rows if row.get("role") == "candidate"]
    if len(controls) != 1 or len(candidates) != 1:
        raise QualificationError("exactly_one_control_and_candidate_required")
    if controls[0].get("family") == candidates[0].get("family"):
        raise QualificationError("candidate_must_be_independent_model_family")
    return controls[0], candidates[0]


def verify_hash(path: Path, expected: str, reason: str) -> None:
    if not path.is_file() or sha256(path) != expected:
        raise QualificationError(reason)


def validate_policy(policy: dict[str, Any], policy_path: Path, repo_root: Path, *, fixture_mode: bool) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise QualificationError("policy_schema_invalid")
    control, candidate = backend_rows(policy)
    if policy["calibration"].get("maximum_selected_candidates") != 1:
        raise QualificationError("maximum_one_candidate_required")
    gates = policy.get("promotion_gates") or {}
    expected_gates = {
        "minimum_bcubed_f1": 0.85,
        "minimum_pairwise_precision": 0.99,
        "minimum_known_speaker_recall": 0.8,
        "maximum_open_set_false_attributions": 0,
    }
    if any(gates.get(key) != value for key, value in expected_gates.items()):
        raise QualificationError("promotion_gates_changed")
    if policy.get("terminal_decisions") != [
        "PROMOTE_LAB_IDENTITY_CANDIDATE",
        "DO_NOT_PROMOTE_IDENTITY_BACKEND",
    ]:
        raise QualificationError("terminal_decisions_changed")
    if fixture_mode:
        return
    for row in policy.get("upstream_guards") or []:
        verify_hash(
            resolve_repo_path(str(row["path"]), repo_root),
            str(row["sha256"]),
            f"upstream_guard_missing_or_stale:{row['id']}",
        )
    for row in policy.get("development_corpora") or []:
        verify_hash(
            resolve_repo_path(str(row["frozen_manifest"]), repo_root),
            str(row["frozen_manifest_sha256"]),
            f"development_frozen_manifest_stale:{row['id']}",
        )
        if row.get("opening_ledger"):
            verify_hash(
                resolve_repo_path(str(row["opening_ledger"]), repo_root),
                str(row["opening_ledger_sha256"]),
                f"development_opening_ledger_stale:{row['id']}",
            )
    if control["family"] != "wavlm_xvector" or candidate["family"] != "ecapa_tdnn":
        raise QualificationError("backend_families_changed")
    if sha256(policy_path) != sha256_bytes(policy_path.read_bytes()):
        raise QualificationError("policy_hash_failed")


def model_path(row: dict[str, Any]) -> Path:
    environment = (
        "MURMURMARK_REMOTE_SPEAKER_ECAPA_MODEL"
        if row["family"] == "ecapa_tdnn"
        else "MURMURMARK_REMOTE_SPEAKER_WAVLM_MODEL"
    )
    return Path(os.environ.get(environment, row["default_path"])).expanduser().resolve()


def runtime_path(row: dict[str, Any]) -> Path:
    return Path(
        os.environ.get("MURMURMARK_REMOTE_SPEAKER_IDENTITY_RUNTIME", row["default_runtime"])
    ).expanduser().resolve()


def runtime_versions(python: Path, names: list[str]) -> dict[str, str]:
    code = (
        "import importlib.metadata as m,json;"
        f"print(json.dumps({{p:m.version(p) for p in {names!r}}},sort_keys=True))"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=False)
    if result.returncode:
        raise QualificationError("candidate_runtime_unavailable")
    return {str(key): str(value) for key, value in json.loads(result.stdout).items()}


def backend_provenance(row: dict[str, Any], *, fixture_mode: bool) -> dict[str, Any]:
    if fixture_mode:
        return {
            "backend_id": row["id"],
            "family": row["family"],
            "mode": "deterministic_fixture",
        }
    path = model_path(row)
    files = []
    for name, expected in sorted(row["files"].items()):
        target = path / name
        verify_hash(target, str(expected), f"model_missing_or_stale:{row['id']}:{name}")
        files.append({"name": name, "bytes": target.stat().st_size, "sha256": expected})
    if row["family"] == "ecapa_tdnn":
        python = runtime_path(row) / "bin/python"
        expected_runtime = row["runtime"]
        versions = runtime_versions(python, list(expected_runtime))
        if any(versions.get(name) != expected for name, expected in expected_runtime.items()):
            raise QualificationError("candidate_runtime_version_mismatch")
    else:
        versions = {
            "torch": importlib.metadata.version("torch"),
            "transformers": importlib.metadata.version("transformers"),
            "numpy": importlib.metadata.version("numpy"),
            "soundfile": importlib.metadata.version("soundfile"),
        }
        if any(versions.get(name) != expected for name, expected in row["runtime"].items() if name != "python"):
            raise QualificationError("control_runtime_version_mismatch")
    return {
        "backend_id": row["id"],
        "role": row["role"],
        "family": row["family"],
        "model_id": row["model_id"],
        "revision": row["revision"],
        "license": row["license"],
        "model_card_url": row["model_card_url"],
        "model_tree_sha256": sha256_bytes(canonical_json(files)),
        "files": files,
        "runtime": versions,
        "device": "cpu",
        "offline": True,
    }


def private_tokens(rng: random.Random, count: int, forbidden: set[str]) -> list[str]:
    consonants = ("b", "d", "f", "g", "k", "l", "m", "n", "p", "r", "s", "t", "v", "z")
    vowels = ("a", "e", "i", "o", "u")
    rows: list[str] = []
    seen: set[str] = set()
    while len(rows) < count:
        token = "".join(rng.choice(consonants) + rng.choice(vowels) for _ in range(rng.randint(2, 4)))
        if token in forbidden or token in seen:
            continue
        seen.add(token)
        rows.append(token)
    return rows


def development_private_values() -> tuple[set[str], set[str]]:
    base = read_json(BASE_POLICY)
    voices = {str(row["system_voice"]) for row in base["renderer"]["speakers"]}
    tokens: set[str] = set()
    lab = load_base_module()
    tokens.update(str(value) for value in lab.VOCABULARY)
    for path in (HARD_V2_SPEC, HARD_V3_SPEC):
        value = read_json(path)
        voices.update(str(row["system_voice"]) for row in value.get("speakers") or [])
        tokens.update(str(item) for item in value.get("hard_vocabulary") or [])
        for script in (value.get("enrollment_scripts") or {}).values():
            tokens.update(str(item) for item in script)
    return voices, tokens


def resolve_private_spec(policy: dict[str, Any], *, fixture_mode: bool) -> dict[str, Any]:
    used_voices, used_tokens = development_private_values()
    seed = "fixture-hard-v4-seed" if fixture_mode else secrets.token_hex(32)
    rng = random.Random(seed)
    renderer = policy["hard_v4"]["renderer"]
    eligible = [voice for voice in renderer["eligible_unused_voices"] if voice not in used_voices]
    rng.shuffle(eligible)
    enrolled_count = int(renderer["enrolled_speaker_count"])
    open_count = int(renderer["open_set_speaker_count"])
    selected = eligible[: enrolled_count + open_count]
    if len(selected) != enrolled_count + open_count:
        raise QualificationError("hard_v4_voice_pool_too_small")
    speakers = []
    slots: dict[str, dict[str, Any]] = {}
    for index, voice in enumerate(selected[:enrolled_count], start=1):
        row = {"speaker_id": f"remote_speaker_{index:02d}", "system_voice": voice, "enrolled": True}
        speakers.append(row)
        slots[f"enrolled_{index}"] = row
    for index, voice in enumerate(selected[enrolled_count:], start=1):
        row = {
            "speaker_id": f"open_set_speaker_hard_v4_{index:02d}",
            "system_voice": voice,
            "enrolled": False,
        }
        speakers.append(row)
        slots[f"open_set_{index}"] = row
    token_count = int(policy["hard_v4"]["private_token_generator"]["token_count"])
    vocabulary = private_tokens(rng, token_count, used_tokens)
    words_per_speaker = int(renderer["enrollment_words_per_speaker"])
    cursor = 0
    enrollment_scripts = {}
    for row in speakers:
        if not row["enrolled"]:
            continue
        enrollment_scripts[row["speaker_id"]] = vocabulary[cursor : cursor + words_per_speaker]
        cursor += words_per_speaker
    scenarios = {}
    for scenario_id, template in policy["hard_v4"]["scenario_templates"].items():
        events = []
        for event in template["events"]:
            slot = str(event["speaker_slot"])
            if slot not in slots:
                raise QualificationError(f"hard_v4_unknown_speaker_slot:{slot}")
            events.append(
                {
                    "speaker_id": slots[slot]["speaker_id"],
                    **{key: value for key, value in event.items() if key != "speaker_slot"},
                }
            )
        scenarios[scenario_id] = {"meeting_mode": template["meeting_mode"], "events": events}
    return {
        "schema": SPEC_SCHEMA,
        "private_seed": seed,
        "speakers": speakers,
        "enrollment_scripts": enrollment_scripts,
        "hard_vocabulary": vocabulary[cursor:],
        "scenarios": scenarios,
        "disjoint_development_voices": not bool({row["system_voice"] for row in speakers} & used_voices),
        "disjoint_development_tokens": not bool(set(vocabulary) & used_tokens),
    }


def rendered_policy(policy: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    renderer = {
        key: value
        for key, value in policy["hard_v4"]["renderer"].items()
        if key not in {"eligible_unused_voices", "enrolled_speaker_count", "open_set_speaker_count", "enrollment_words_per_speaker"}
    }
    renderer["speakers"] = spec["speakers"]
    return {"seed": spec["private_seed"], "renderer": renderer, "analysis": {"overlap_min_sec": 0.04}}


def build_enrollment(root: Path, policy: dict[str, Any], spec: dict[str, Any], renderer: Any, lab: Any) -> dict[str, Any]:
    settings = policy["hard_v4"]["renderer"]
    sample_rate = int(settings["sample_rate"])
    gap = lab.np.zeros(int(round(0.08 * sample_rate)), dtype=lab.np.int16)
    directory = root / "enrollment"
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for speaker in spec["speakers"]:
        if not speaker["enrolled"]:
            continue
        speaker_id = str(speaker["speaker_id"])
        pieces = []
        tokens = spec["enrollment_scripts"][speaker_id]
        for index, token in enumerate(tokens):
            if index:
                pieces.append(gap)
            pieces.append(renderer.render(speaker, token))
        pcm = lab.np.concatenate(pieces).astype(lab.np.int16)
        path = directory / f"{speaker_id}.wav"
        lab.sf.write(path, pcm, sample_rate, subtype=str(settings["audio_subtype"]))
        rows.append(
            {
                "speaker_id": speaker_id,
                "path": str(path.relative_to(root)),
                "word_count": len(tokens),
                "duration_sec": round(len(pcm) / sample_rate, 6),
                "sha256": sha256(path),
            }
        )
    manifest = {
        "schema": "murmurmark.remote_speaker_identity_hard_v4_private_enrollment/v1",
        "sample_rate": sample_rate,
        "rows": rows,
        "scripts_disjoint_from_hard_v4": True,
    }
    write_json(directory / "enrollment_manifest.json", manifest)
    return {
        "speaker_count": len(rows),
        "word_count": sum(int(row["word_count"]) for row in rows),
        "duration_sec": round(sum(float(row["duration_sec"]) for row in rows), 6),
    }


def artifact_hashes(root: Path) -> dict[str, str]:
    rows = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = str(path.relative_to(root))
        if relative in {"frozen_manifest.json", "hard_v4_opening_ledger.json"} or relative.startswith("cache/"):
            continue
        rows[relative] = sha256(path)
    return rows


def verify_hard_v4(private: Path, policy_path: Path) -> dict[str, Any]:
    manifest = read_json(private / "frozen_manifest.json")
    if manifest.get("schema") != FROZEN_SCHEMA:
        raise QualificationError("hard_v4_frozen_schema_invalid")
    if manifest.get("policy_sha256") != sha256(policy_path):
        raise QualificationError("hard_v4_policy_changed")
    actual = artifact_hashes(private)
    if actual != manifest.get("artifacts"):
        raise QualificationError("hard_v4_artifacts_changed")
    if sha256_bytes(canonical_json(actual)) != manifest.get("corpus_sha256"):
        raise QualificationError("hard_v4_corpus_hash_invalid")
    spec = read_json(private / "hard_v4_spec.json")
    if not spec.get("disjoint_development_voices") or not spec.get("disjoint_development_tokens"):
        raise QualificationError("hard_v4_not_disjoint")
    return manifest


def freeze_hard_v4(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    private = args.out_dir / "private/hard-v4"
    frozen = private / "frozen_manifest.json"
    if frozen.is_file():
        return verify_hard_v4(private, args.policy)
    if private.exists():
        raise QualificationError("partial_hard_v4_exists")
    staging = private.with_name(f".hard-v4-freezing-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        spec = resolve_private_spec(policy, fixture_mode=args.fixture_mode)
        write_json(staging / "hard_v4_spec.json", spec)
        lab.VOCABULARY = tuple(spec["hard_vocabulary"])
        compatible = rendered_policy(policy, spec)
        renderer = (
            lab.FixtureRenderer(compatible, staging / "cache/words")
            if args.fixture_mode
            else lab.SayRenderer(compatible, staging / "cache/words")
        )
        enrollment = build_enrollment(staging, policy, spec, renderer, lab)
        summaries = []
        for scenario_id, scenario in spec["scenarios"].items():
            print(f"freeze: hard-v4/{scenario_id}", flush=True)
            summaries.append(
                lab.build_scenario(staging / "sealed", scenario_id, "hard_v4", scenario, compatible, renderer)
            )
        artifacts = artifact_hashes(staging)
        manifest = {
            "schema": FROZEN_SCHEMA,
            "version": VERSION,
            "policy_sha256": sha256(args.policy),
            "private_spec_sha256": sha256(staging / "hard_v4_spec.json"),
            "renderer": renderer.provenance,
            "enrollment_summary": enrollment,
            "scenario_summaries": summaries,
            "speaker_count": len(spec["speakers"]),
            "enrolled_speaker_count": sum(bool(row["enrolled"]) for row in spec["speakers"]),
            "disjoint_development_voices": True,
            "disjoint_development_tokens": True,
            "artifacts": artifacts,
            "corpus_sha256": sha256_bytes(canonical_json(artifacts)),
        }
        write_json(staging / "frozen_manifest.json", manifest)
        private.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, private)
        return verify_hard_v4(private, args.policy)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def public_hard_manifest(args: argparse.Namespace, manifest: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema": PUBLIC_HARD_SCHEMA,
        "version": VERSION,
        "corpus_sha256": manifest["corpus_sha256"],
        "frozen_manifest_sha256": sha256(args.out_dir / "private/hard-v4/frozen_manifest.json"),
        "scenario_count": len(manifest["scenario_summaries"]),
        "word_count": sum(int(row["word_count"]) for row in manifest["scenario_summaries"]),
        "boundary_count": sum(int(row["boundary_count"]) for row in manifest["scenario_summaries"]),
        "overlap_word_count": sum(int(row["overlap_word_count"]) for row in manifest["scenario_summaries"]),
        "speaker_count": manifest["speaker_count"],
        "enrolled_speaker_count": manifest["enrolled_speaker_count"],
        "disjoint_development_voices": True,
        "disjoint_development_tokens": True,
        "opening_status": "opened" if (args.out_dir / "private/hard-v4/hard_v4_opening_ledger.json").is_file() else "sealed",
        "private_values_excluded": ["renderer_identity", "scripts", "randomization", "audio"],
    }
    write_json(args.out_dir / "hard_v4_public_manifest.json", payload)
    return payload


def scenario_row(corpus_id: str, directory: Path) -> dict[str, Any]:
    return {
        "corpus_id": corpus_id,
        "scenario_id": directory.name,
        "mixture": directory / "mixture.wav",
        "scenario": read_json(directory / "scenario.json"),
        "words": read_jsonl(directory / "truth_words.jsonl"),
        "boundaries": read_jsonl(directory / "truth_boundaries.jsonl"),
    }


def load_truth_lab_corpus(spec: dict[str, Any], repo_root: Path, minimum: float) -> Corpus:
    root = resolve_repo_path(str(spec["root"]), repo_root)
    scenarios = []
    for split in spec["evaluation_splits"]:
        scenarios.extend(scenario_row(spec["id"], path) for path in sorted((root / "sessions" / split).iterdir()) if path.is_dir())
    enrollment: dict[str, list[AudioRequest]] = defaultdict(list)
    for directory in sorted((root / "sessions/train").iterdir()):
        if not directory.is_dir():
            continue
        scenario = read_json(directory / "scenario.json")
        for event in scenario["events"]:
            if not event["enrolled"] or float(event["overlap_previous_sec"]) > 0:
                continue
            speaker = str(event["speaker_id"])
            enrollment[speaker].append(
                AudioRequest(
                    f"{spec['id']}:enroll:{event['event_id']}",
                    directory / "mixture.wav",
                    float(event["start"]),
                    float(event["end"]),
                    minimum,
                )
            )
    return Corpus(str(spec["id"]), dict(enrollment), scenarios)


def load_sealed_corpus(corpus_id: str, root: Path, split: str, minimum: float) -> Corpus:
    enrollment_manifest = read_json(root / "enrollment/enrollment_manifest.json")
    enrollment: dict[str, list[AudioRequest]] = defaultdict(list)
    for row in enrollment_manifest["rows"]:
        path = root / str(row["path"])
        with sf.SoundFile(path) as source:
            duration = len(source) / source.samplerate
        enrollment[str(row["speaker_id"])].append(
            AudioRequest(f"{corpus_id}:enroll:{row['speaker_id']}", path, 0.0, duration, minimum)
        )
    directory = root / "sealed/sessions" / split
    scenarios = [scenario_row(corpus_id, path) for path in sorted(directory.iterdir()) if path.is_dir()]
    return Corpus(corpus_id, dict(enrollment), scenarios)


def load_development_corpora(args: argparse.Namespace, policy: dict[str, Any]) -> list[Corpus]:
    minimum = float(policy["audio_preparation"]["minimum_enrollment_sec"])
    if args.fixture_mode:
        return [load_sealed_corpus("fixture_hard_v4", args.out_dir / "private/hard-v4", "hard_v4", minimum)]
    rows = []
    for spec in policy["development_corpora"]:
        if spec["kind"] == "truth_lab":
            rows.append(load_truth_lab_corpus(spec, args.repo_root, minimum))
        else:
            rows.append(
                load_sealed_corpus(
                    str(spec["id"]),
                    resolve_repo_path(str(spec["root"]), args.repo_root),
                    str(spec["sealed_split"]),
                    minimum,
                )
            )
    return rows


def load_hard_v4(args: argparse.Namespace, policy: dict[str, Any]) -> Corpus:
    minimum = float(policy["audio_preparation"]["minimum_enrollment_sec"])
    return load_sealed_corpus("hard_v4", args.out_dir / "private/hard-v4", "hard_v4", minimum)


def build_requests(corpora: list[Corpus], policy: dict[str, Any]) -> list[AudioRequest]:
    minimum = float(policy["audio_preparation"]["minimum_segment_sec"])
    requests = []
    for corpus in corpora:
        for rows in corpus.enrollment.values():
            requests.extend(rows)
        for scenario in corpus.scenarios:
            for event in scenario["scenario"]["events"]:
                requests.append(
                    AudioRequest(
                        f"{corpus.corpus_id}:event:{event['event_id']}",
                        scenario["mixture"],
                        float(event["start"]),
                        float(event["end"]),
                        minimum,
                    )
                )
    keys = [row.key for row in requests]
    if len(keys) != len(set(keys)):
        raise QualificationError("embedding_request_key_collision")
    return requests


def fixture_embeddings(requests: list[AudioRequest], lab: Any) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    backend = lab.FixtureEmbeddingBackend("identity_backend_fixture", 1.0)
    translated = [lab.AudioRequest(row.key, row.key.split(":", 1)[0], row.path, row.start, row.end) for row in requests]
    return backend.embed_requests(translated), backend.provenance


def control_embeddings(requests: list[AudioRequest], policy: dict[str, Any], lab: Any, fixture_mode: bool) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    if fixture_mode:
        return fixture_embeddings(requests, lab)
    backend = lab.WavLMBackend(policy, float(policy["audio_preparation"]["minimum_segment_sec"]), 8)
    translated = [lab.AudioRequest(row.key, row.key.split(":", 1)[0], row.path, row.start, row.end) for row in requests]
    return backend.embed_requests(translated), backend.provenance


def candidate_embeddings(
    requests: list[AudioRequest],
    policy: dict[str, Any],
    row: dict[str, Any],
    output: Path,
    lab: Any,
    fixture_mode: bool,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    if fixture_mode:
        return fixture_embeddings(requests, lab)
    request_path = output.with_name(f"{output.stem}_request.json")
    request = {
        "schema": "murmurmark.speaker_embedding_request/v1",
        "model_id": row["model_id"],
        "model_revision": row["revision"],
        "requests": [
            {
                "key": item.key,
                "path": str(item.path.resolve()),
                "start": item.start,
                "end": item.end,
                "minimum_sec": item.minimum_sec,
            }
            for item in requests
        ],
    }
    write_json(request_path, request)
    python = runtime_path(row) / "bin/python"
    environment = dict(os.environ)
    environment.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    result = subprocess.run(
        [
            "nice", "-n", "20", str(python), str(ECAPA_WORKER),
            "--request", str(request_path), "--output", str(output),
            "--model", str(model_path(row)), "--threads", "4",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise QualificationError(f"ecapa_worker_failed:{result.stderr.strip()[-500:]}")
    payload = read_json(output)
    if payload.get("request_sha256") != sha256(request_path):
        raise QualificationError("ecapa_worker_request_hash_mismatch")
    embeddings = {
        str(item["key"]): np.asarray(item["embedding"], dtype=np.float32)
        for item in payload["rows"]
    }
    return embeddings, backend_provenance(row, fixture_mode=False)


def normalize(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm <= 0:
        raise QualificationError("invalid_embedding_norm")
    return np.asarray(vector / norm, dtype=np.float32)


def centroids(corpus: Corpus, embeddings: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    rows = {}
    for speaker, requests in corpus.enrollment.items():
        values = [embeddings[row.key] for row in requests]
        if values:
            rows[speaker] = normalize(np.mean(values, axis=0))
    if set(rows) != set(corpus.enrollment):
        raise QualificationError(f"incomplete_enrollment:{corpus.corpus_id}")
    return rows


def classify(vector: np.ndarray, centers: dict[str, np.ndarray], similarity: float, margin: float) -> dict[str, Any]:
    scores = sorted(((float(vector @ center), speaker) for speaker, center in centers.items()), reverse=True)
    top_score, top_speaker = scores[0]
    second = scores[1][0] if len(scores) > 1 else -1.0
    observed_margin = top_score - second
    accepted = top_score >= similarity and observed_margin >= margin
    return {
        "speaker_id": top_speaker if accepted else "unknown_speaker",
        "top_speaker_id": top_speaker,
        "similarity": round(top_score, 6),
        "margin": round(observed_margin, 6),
        "reason": "accepted_centroid" if accepted else "open_set_abstention",
    }


def predict(
    corpora: list[Corpus], embeddings: dict[str, np.ndarray], similarity: float, margin: float, backend_id: str
) -> list[dict[str, Any]]:
    rows = []
    for corpus in corpora:
        centers = centroids(corpus, embeddings)
        for scenario in corpus.scenarios:
            events = {str(event["event_id"]): event for event in scenario["scenario"]["events"]}
            event_results = {
                event_id: classify(
                    embeddings[f"{corpus.corpus_id}:event:{event_id}"], centers, similarity, margin
                )
                for event_id in events
            }
            for word in scenario["words"]:
                mixed = word["truth_class"] == "mixed" or bool(word.get("overlap_word_ids"))
                result = (
                    {
                        "speaker_id": "mixed",
                        "top_speaker_id": None,
                        "similarity": None,
                        "margin": None,
                        "reason": "exact_overlap_fail_closed",
                    }
                    if mixed
                    else event_results[str(word["event_id"])]
                )
                rows.append(
                    {
                        "schema": PREDICTION_SCHEMA,
                        "backend_id": backend_id,
                        "corpus_id": corpus.corpus_id,
                        "scenario_id": scenario["scenario_id"],
                        "word_id": word["word_id"],
                        "event_id": word["event_id"],
                        **result,
                    }
                )
    return rows


def bcubed(truth: list[str], predicted: list[str]) -> dict[str, float]:
    truth_members: dict[str, set[int]] = defaultdict(set)
    predicted_members: dict[str, set[int]] = defaultdict(set)
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        truth_members[expected].add(index)
        predicted_members[actual].add(index)
    precision_rows = []
    recall_rows = []
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        intersection = len(truth_members[expected] & predicted_members[actual])
        precision_rows.append(intersection / len(predicted_members[actual]))
        recall_rows.append(intersection / len(truth_members[expected]))
    precision = float(np.mean(precision_rows)) if precision_rows else 0.0
    recall = float(np.mean(recall_rows)) if recall_rows else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def pairwise(truth: list[str], predicted: list[str]) -> dict[str, Any]:
    tp = fp = fn = 0
    for left in range(len(truth)):
        for right in range(left + 1, len(truth)):
            truth_same = truth[left] == truth[right]
            predicted_same = predicted[left] == predicted[right] and not predicted[left].startswith("unknown:")
            if truth_same and predicted_same:
                tp += 1
            elif not truth_same and predicted_same:
                fp += 1
            elif truth_same and not predicted_same:
                fn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6),
        "true_positive_pairs": tp, "false_positive_pairs": fp, "false_negative_pairs": fn,
    }


def evaluate(corpora: list[Corpus], predictions: list[dict[str, Any]]) -> dict[str, Any]:
    predicted = {(row["corpus_id"], row["word_id"]): row for row in predictions}
    all_words = [(corpus, scenario, word) for corpus in corpora for scenario in corpus.scenarios for word in scenario["words"]]
    keys = {(corpus.corpus_id, str(word["word_id"])) for corpus, _, word in all_words}
    conservation = len(predicted) == len(all_words) and set(predicted) == keys
    truth_labels = []
    predicted_labels = []
    known_count = accepted = correct = open_false = mixed_count = mixed_safe = 0
    boundary_count = recovered = 0
    per_corpus: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for corpus, _, word in all_words:
        row = predicted[(corpus.corpus_id, str(word["word_id"]))]
        truth_class = str(word["truth_class"])
        if truth_class == "known_speaker":
            known_count += 1
            per_corpus[corpus.corpus_id]["known_words"] += 1
            truth_labels.append(f"{corpus.corpus_id}:{word['speaker_id']}")
            if row["speaker_id"] in {"unknown_speaker", "mixed"}:
                predicted_labels.append(f"unknown:{corpus.corpus_id}:{word['word_id']}")
            else:
                accepted += 1
                per_corpus[corpus.corpus_id]["known_attributed"] += 1
                correct += int(row["speaker_id"] == word["speaker_id"])
                predicted_labels.append(f"{corpus.corpus_id}:{row['speaker_id']}")
        elif truth_class == "open_set_speaker":
            is_false = str(row["speaker_id"]).startswith("remote_speaker_")
            open_false += int(is_false)
            per_corpus[corpus.corpus_id]["open_set_words"] += 1
            per_corpus[corpus.corpus_id]["open_set_false_attributions"] += int(is_false)
        else:
            mixed_count += 1
            safe = row["speaker_id"] == "mixed"
            mixed_safe += int(safe)
            per_corpus[corpus.corpus_id]["mixed_words"] += 1
            per_corpus[corpus.corpus_id]["mixed_safe"] += int(safe)
    for corpus in corpora:
        for scenario in corpus.scenarios:
            for boundary in scenario["boundaries"]:
                if not boundary["evaluation"]:
                    continue
                boundary_count += 1
                left = predicted[(corpus.corpus_id, str(boundary["left_word_id"]))]["speaker_id"]
                right = predicted[(corpus.corpus_id, str(boundary["right_word_id"]))]["speaker_id"]
                expected_left = str(boundary["left_speaker_id"])
                expected_right = str(boundary["right_speaker_id"])
                if not expected_left.startswith("remote_speaker_"):
                    expected_left = "unknown_speaker"
                if not expected_right.startswith("remote_speaker_"):
                    expected_right = "unknown_speaker"
                hit = left == expected_left and right == expected_right and left != right
                recovered += int(hit)
                per_corpus[corpus.corpus_id]["boundaries"] += 1
                per_corpus[corpus.corpus_id]["boundaries_recovered"] += int(hit)
    return {
        "word_count": len(all_words),
        "prediction_count": len(predicted),
        "word_conservation": conservation,
        "known_single_speaker_words": known_count,
        "known_attributed_words": accepted,
        "known_correct_words": correct,
        "known_attribution_recall": round(accepted / known_count, 6) if known_count else 0.0,
        "known_attributed_precision": round(correct / accepted, 6) if accepted else None,
        "bcubed": bcubed(truth_labels, predicted_labels),
        "pairwise": pairwise(truth_labels, predicted_labels),
        "open_set_false_attributions": open_false,
        "mixed_words": mixed_count,
        "mixed_safely_marked": mixed_safe,
        "boundary_count": boundary_count,
        "boundaries_recovered": recovered,
        "boundary_recall": round(recovered / boundary_count, 6) if boundary_count else 0.0,
        "per_corpus": {key: dict(sorted(value.items())) for key, value in sorted(per_corpus.items())},
    }


def select_thresholds(policy: dict[str, Any], corpora: list[Corpus], embeddings: dict[str, np.ndarray], backend_id: str) -> tuple[dict[str, float], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    trials = []
    for similarity in policy["calibration"]["similarity_grid"]:
        for margin in policy["calibration"]["margin_grid"]:
            predictions = predict(corpora, embeddings, float(similarity), float(margin), backend_id)
            metrics = evaluate(corpora, predictions)
            trials.append({"minimum_similarity": float(similarity), "minimum_margin": float(margin), "metrics": metrics})
    selected = max(
        trials,
        key=lambda row: (
            -int(row["metrics"]["open_set_false_attributions"]),
            int(row["metrics"]["mixed_safely_marked"] == row["metrics"]["mixed_words"]),
            row["metrics"]["pairwise"]["precision"],
            row["metrics"]["known_attribution_recall"],
            row["metrics"]["bcubed"]["f1"],
            row["metrics"]["boundary_recall"],
            row["minimum_similarity"],
            row["minimum_margin"],
        ),
    )
    thresholds = {
        "minimum_similarity": selected["minimum_similarity"],
        "minimum_margin": selected["minimum_margin"],
    }
    predictions = predict(corpora, embeddings, thresholds["minimum_similarity"], thresholds["minimum_margin"], backend_id)
    return thresholds, trials, predictions, selected["metrics"]


def development_paths(args: argparse.Namespace) -> dict[str, Path]:
    root = args.out_dir / "private/development"
    return {
        "root": root,
        "candidate_freeze": args.out_dir / "private/candidate_freeze.json",
        "control_predictions": root / "wavlm_xvector_control_predictions.jsonl",
        "candidate_predictions": root / "speechbrain_ecapa_voxceleb_candidate_predictions.jsonl",
        "control_trials": root / "wavlm_xvector_control_trials.json",
        "candidate_trials": root / "speechbrain_ecapa_voxceleb_candidate_trials.json",
        "candidate_embeddings": root / "speechbrain_ecapa_voxceleb_candidate_embeddings.json",
    }


def develop(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    hard_manifest = verify_hard_v4(args.out_dir / "private/hard-v4", args.policy)
    paths = development_paths(args)
    if paths["candidate_freeze"].is_file():
        frozen = read_json(paths["candidate_freeze"])
        if frozen.get("schema") != CANDIDATE_SCHEMA:
            raise QualificationError("candidate_freeze_schema_invalid")
        return frozen
    control_row, candidate_row = backend_rows(policy)
    control_provenance = backend_provenance(control_row, fixture_mode=args.fixture_mode)
    candidate_provenance = backend_provenance(candidate_row, fixture_mode=args.fixture_mode)
    corpora = load_development_corpora(args, policy)
    requests = build_requests(corpora, policy)
    print(f"develop: {control_row['id']} ({len(requests)} audio slices)", flush=True)
    control_vectors, control_runtime = control_embeddings(requests, policy, lab, args.fixture_mode)
    print(f"develop: {candidate_row['id']} ({len(requests)} audio slices)", flush=True)
    candidate_vectors, candidate_runtime = candidate_embeddings(
        requests, policy, candidate_row, paths["candidate_embeddings"], lab, args.fixture_mode
    )
    control_thresholds, control_trials, control_predictions, control_metrics = select_thresholds(
        policy, corpora, control_vectors, control_row["id"]
    )
    candidate_thresholds, candidate_trials, candidate_predictions, candidate_metrics = select_thresholds(
        policy, corpora, candidate_vectors, candidate_row["id"]
    )
    candidate_eligible = (
        candidate_metrics["word_conservation"]
        and candidate_metrics["open_set_false_attributions"] == 0
        and candidate_metrics["mixed_safely_marked"] == candidate_metrics["mixed_words"]
        and candidate_metrics["pairwise"]["precision"] >= 0.99
        and candidate_metrics["known_attribution_recall"] >= control_metrics["known_attribution_recall"]
        and candidate_metrics["bcubed"]["f1"] >= control_metrics["bcubed"]["f1"]
    )
    write_jsonl(paths["control_predictions"], control_predictions)
    write_jsonl(paths["candidate_predictions"], candidate_predictions)
    write_json(paths["control_trials"], {"backend_id": control_row["id"], "trials": control_trials})
    write_json(paths["candidate_trials"], {"backend_id": candidate_row["id"], "trials": candidate_trials})
    payload = {
        "schema": CANDIDATE_SCHEMA,
        "version": VERSION,
        "policy_sha256": sha256(args.policy),
        "hard_v4_corpus_sha256": hard_manifest["corpus_sha256"],
        "hard_v4_status_at_selection": "sealed",
        "development_corpora": [corpus.corpus_id for corpus in corpora],
        "exact_partition": policy["audio_preparation"]["partition"],
        "shortlist": [control_provenance, candidate_provenance],
        "control": {
            "backend_id": control_row["id"], "thresholds": control_thresholds,
            "metrics": control_metrics, "runtime": control_runtime,
        },
        "candidate": {
            "backend_id": candidate_row["id"], "thresholds": candidate_thresholds,
            "metrics": candidate_metrics, "runtime": candidate_runtime,
        },
        "selected_candidate_id": candidate_row["id"] if candidate_eligible else None,
        "selection_decision": "CANDIDATE_FROZEN_FOR_ONE_SHOT_HARD_V4" if candidate_eligible else "NO_ELIGIBLE_CANDIDATE",
        "maximum_selected_candidates": 1,
    }
    write_json(paths["candidate_freeze"], payload)
    return payload


def hard_predictions(
    args: argparse.Namespace,
    policy: dict[str, Any],
    lab: Any,
    candidate_freeze: dict[str, Any],
) -> dict[str, Any]:
    control_row, candidate_row = backend_rows(policy)
    corpus = load_hard_v4(args, policy)
    corpora = [corpus]
    requests = build_requests(corpora, policy)
    private = args.out_dir / "private/hard-v4-evaluation"
    control_vectors, control_runtime = control_embeddings(requests, policy, lab, args.fixture_mode)
    candidate_vectors, candidate_runtime = candidate_embeddings(
        requests, policy, candidate_row, private / "candidate_embeddings.json", lab, args.fixture_mode
    )
    outputs = {}
    for row, vectors, runtime, key in (
        (control_row, control_vectors, control_runtime, "control"),
        (candidate_row, candidate_vectors, candidate_runtime, "candidate"),
    ):
        thresholds = candidate_freeze[key]["thresholds"]
        predictions = predict(
            corpora, vectors, float(thresholds["minimum_similarity"]),
            float(thresholds["minimum_margin"]), row["id"]
        )
        metrics = evaluate(corpora, predictions)
        write_jsonl(private / f"{key}_predictions.jsonl", predictions)
        outputs[key] = {
            "backend_id": row["id"], "thresholds": thresholds,
            "metrics": metrics, "runtime": runtime,
            "predictions_sha256": sha256(private / f"{key}_predictions.jsonl"),
        }
    return outputs


def report_markdown(report: dict[str, Any]) -> str:
    control = report["hard_v4"]["control"]["metrics"]
    candidate = report["hard_v4"]["candidate"]["metrics"]
    gates = report["promotion_gates"]
    return "\n".join(
        [
            "# Stronger Remote Speaker Identity Backend Qualification v1",
            "",
            f"Decision: `{report['decision']}`",
            f"Candidate: `{report['selected_candidate_id']}`",
            f"Hard-v4 corpus: `{report['hard_v4_corpus_sha256']}`",
            "",
            "| Metric | Control | Candidate |",
            "|---|---:|---:|",
            f"| B-cubed F1 | {control['bcubed']['f1']:.6f} | {candidate['bcubed']['f1']:.6f} |",
            f"| Pairwise precision | {control['pairwise']['precision']:.6f} | {candidate['pairwise']['precision']:.6f} |",
            f"| Known-speaker recall | {control['known_attribution_recall']:.6f} | {candidate['known_attribution_recall']:.6f} |",
            f"| Boundary recall | {control['boundary_recall']:.6f} | {candidate['boundary_recall']:.6f} |",
            f"| Open-set false attribution | {control['open_set_false_attributions']} | {candidate['open_set_false_attributions']} |",
            "",
            "## Gates",
            "",
            *[f"- {key}: `{'PASS' if value else 'FAIL'}`" for key, value in sorted(gates.items())],
            "",
            "Production, Coverage v3 and selected transcripts remain unchanged.",
            "",
        ]
    )


def build_report(args: argparse.Namespace, policy: dict[str, Any], candidate_freeze: dict[str, Any], outputs: dict[str, Any]) -> dict[str, Any]:
    candidate = outputs["candidate"]["metrics"]
    control = outputs["control"]["metrics"]
    gates_policy = policy["promotion_gates"]
    gates = {
        "exact_word_conservation": candidate["word_conservation"] is True,
        "minimum_bcubed_f1": candidate["bcubed"]["f1"] >= float(gates_policy["minimum_bcubed_f1"]),
        "minimum_pairwise_precision": candidate["pairwise"]["precision"] >= float(gates_policy["minimum_pairwise_precision"]),
        "minimum_known_speaker_recall": candidate["known_attribution_recall"] >= float(gates_policy["minimum_known_speaker_recall"]),
        "zero_open_set_false_attribution": candidate["open_set_false_attributions"] <= int(gates_policy["maximum_open_set_false_attributions"]),
        "mixed_fail_closed": candidate["mixed_safely_marked"] == candidate["mixed_words"],
        "boundary_no_regression": candidate["boundary_recall"] >= control["boundary_recall"],
        "single_candidate": candidate_freeze["selected_candidate_id"] == outputs["candidate"]["backend_id"],
    }
    decision = "PROMOTE_LAB_IDENTITY_CANDIDATE" if all(gates.values()) else "DO_NOT_PROMOTE_IDENTITY_BACKEND"
    hard_manifest = read_json(args.out_dir / "private/hard-v4/frozen_manifest.json")
    return {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "decision": decision,
        "selected_candidate_id": candidate_freeze["selected_candidate_id"],
        "qualification_scope": "synthetic_exact_event_identity_only",
        "policy_sha256": sha256(args.policy),
        "candidate_freeze_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
        "hard_v4_corpus_sha256": hard_manifest["corpus_sha256"],
        "hard_v4_frozen_manifest_sha256": sha256(args.out_dir / "private/hard-v4/frozen_manifest.json"),
        "hard_v4_open_count": 1,
        "development": {
            "control": candidate_freeze["control"],
            "candidate": candidate_freeze["candidate"],
        },
        "hard_v4": outputs,
        "promotion_gates": gates,
        "safety": {
            "production_mutated": False,
            "coverage_v3_mutated": False,
            "selected_transcript_mutated": False,
            "synthetic_identity_transferred_to_real_sessions": False,
            "next_scope": "separate_real_session_fail_open_qualification" if decision.startswith("PROMOTE") else "new_identity_model_family_or_reviewed_evidence",
        },
    }


def evaluate_hard(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    verify_hard_v4(args.out_dir / "private/hard-v4", args.policy)
    candidate_freeze = develop(args, policy, lab)
    if candidate_freeze.get("selected_candidate_id") is None:
        raise QualificationError("no_candidate_selected_for_hard_v4")
    ledger_path = args.out_dir / "private/hard-v4/hard_v4_opening_ledger.json"
    report_path = args.out_dir / "remote_speaker_identity_backend_qualification_report.json"
    if report_path.is_file():
        report = read_json(report_path)
        if not ledger_path.is_file():
            raise QualificationError("hard_v4_report_without_opening_ledger")
        return report
    if ledger_path.is_file():
        ledger = read_json(ledger_path)
        if ledger.get("open_count") != 1 or ledger.get("selected_candidate_id") != candidate_freeze["selected_candidate_id"]:
            raise QualificationError("hard_v4_opening_ledger_conflict")
    else:
        write_json(
            ledger_path,
            {
                "schema": LEDGER_SCHEMA,
                "status": "opened_for_single_frozen_candidate",
                "open_count": 1,
                "selected_candidate_id": candidate_freeze["selected_candidate_id"],
                "candidate_freeze_sha256": sha256(args.out_dir / "private/candidate_freeze.json"),
                "hard_v4_frozen_manifest_sha256": sha256(args.out_dir / "private/hard-v4/frozen_manifest.json"),
            },
        )
    outputs = hard_predictions(args, policy, lab, candidate_freeze)
    report = build_report(args, policy, candidate_freeze, outputs)
    write_json(report_path, report)
    atomic_write(args.out_dir / "remote_speaker_identity_backend_qualification_report.md", report_markdown(report).encode())
    manifest = verify_hard_v4(args.out_dir / "private/hard-v4", args.policy)
    public_hard_manifest(args, manifest)
    return report


def tracked_manifest(args: argparse.Namespace, report: dict[str, Any]) -> dict[str, Any]:
    public_hard = args.out_dir / "hard_v4_public_manifest.json"
    report_path = args.out_dir / "remote_speaker_identity_backend_qualification_report.json"
    replay_path = args.out_dir / "replay_report.json"
    value = {
        "schema": TRACKED_SCHEMA,
        "version": VERSION,
        "decision": report["decision"],
        "selected_candidate_id": report["selected_candidate_id"],
        "policy": {"path": portable(args.policy, args.repo_root), "bytes": args.policy.stat().st_size, "sha256": sha256(args.policy)},
        "hard_v4_public_manifest": {"path": portable(public_hard, args.repo_root), "bytes": public_hard.stat().st_size, "sha256": sha256(public_hard)},
        "qualification_report": {"path": portable(report_path, args.repo_root), "bytes": report_path.stat().st_size, "sha256": sha256(report_path)},
        "replay_report": {"path": portable(replay_path, args.repo_root), "bytes": replay_path.stat().st_size, "sha256": sha256(replay_path)} if replay_path.is_file() else None,
        "hard_v4_corpus_sha256": report["hard_v4_corpus_sha256"],
        "hard_v4_open_count": report["hard_v4_open_count"],
        "production_mutated": False,
        "private_values_excluded": True,
    }
    write_json(args.write_manifest, value)
    return value


def replay(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    report_path = args.out_dir / "remote_speaker_identity_backend_qualification_report.json"
    if not report_path.is_file():
        raise QualificationError("qualification_report_missing")
    expected = report_path.read_bytes()
    candidate_freeze = read_json(args.out_dir / "private/candidate_freeze.json")
    outputs = hard_predictions(args, policy, lab, candidate_freeze)
    actual = canonical_json(build_report(args, policy, candidate_freeze, outputs))
    matched = actual == expected
    payload = {
        "schema": REPLAY_SCHEMA,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if matched else "REPLAY_MISMATCH",
        "qualification_report_sha256": sha256(report_path),
        "recomputed_report_sha256": sha256_bytes(actual),
        "byte_identical": matched,
        "hard_v4_open_count": read_json(args.out_dir / "private/hard-v4/hard_v4_opening_ledger.json")["open_count"],
    }
    write_json(args.out_dir / "replay_report.json", payload)
    if not matched:
        raise QualificationError("qualification_replay_mismatch")
    return payload


def status(args: argparse.Namespace) -> int:
    private = args.out_dir / "private/hard-v4"
    report_path = args.out_dir / "remote_speaker_identity_backend_qualification_report.json"
    if not (private / "frozen_manifest.json").is_file():
        print("decision: HARD_V4_NOT_FROZEN")
        print("next: murmurmark corpus remote-identity-v1 freeze")
        return 1
    print(f"hard_v4: {'opened' if (private / 'hard_v4_opening_ledger.json').is_file() else 'sealed'}")
    candidate = args.out_dir / "private/candidate_freeze.json"
    print(f"candidate: {'frozen' if candidate.is_file() else 'not_selected'}")
    if report_path.is_file():
        report = read_json(report_path)
        print(f"decision: {report['decision']}")
        print(f"selected_candidate: {report['selected_candidate_id']}")
        return 0
    print("decision: QUALIFICATION_INCOMPLETE")
    print("next: murmurmark corpus remote-identity-v1 develop")
    return 1


def preflight(args: argparse.Namespace, policy: dict[str, Any]) -> int:
    rows = []
    for row in backend_rows(policy):
        try:
            provenance = backend_provenance(row, fixture_mode=args.fixture_mode)
            rows.append({"backend_id": row["id"], "status": "ready", "provenance": provenance})
        except QualificationError as error:
            rows.append({"backend_id": row["id"], "status": "unavailable", "reason": str(error)})
    payload = {
        "schema": "murmurmark.remote_speaker_identity_backend_preflight/v1",
        "ready": all(row["status"] == "ready" for row in rows),
        "backends": rows,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if payload["ready"] else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("preflight", "freeze", "hard-status", "develop", "evaluate-hard", "status", "replay", "all"),
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--write-manifest", type=Path, default=DEFAULT_TRACKED)
    parser.add_argument("--fixture-mode", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.repo_root = args.repo_root.expanduser().resolve()
    args.policy = resolve_repo_path(str(args.policy), args.repo_root)
    args.out_dir = resolve_repo_path(str(args.out_dir), args.repo_root)
    args.write_manifest = resolve_repo_path(str(args.write_manifest), args.repo_root)
    try:
        policy = read_json(args.policy)
        validate_policy(policy, args.policy, args.repo_root, fixture_mode=args.fixture_mode)
        if args.action == "preflight":
            return preflight(args, policy)
        if args.action == "hard-status":
            return status(args)
        lab = load_base_module()
        if args.action in {"freeze", "all"}:
            manifest = freeze_hard_v4(args, policy, lab)
            public_hard_manifest(args, manifest)
        if args.action in {"develop", "all"}:
            develop(args, policy, lab)
        if args.action in {"evaluate-hard", "all"}:
            report = evaluate_hard(args, policy, lab)
        elif args.action == "status":
            return status(args)
        elif args.action == "replay":
            replay(args, policy, lab)
            report = read_json(args.out_dir / "remote_speaker_identity_backend_qualification_report.json")
        if args.action == "all":
            replay(args, policy, lab)
        if args.action in {"evaluate-hard", "replay", "all"}:
            tracked_manifest(args, report)
            print(f"decision: {report['decision']}")
            print(f"selected_candidate: {report['selected_candidate_id']}")
        return 0
    except QualificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
