#!/usr/bin/env python3
"""Freeze the private, untouched hard-v3 remote-speaker truth corpus."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import re
import secrets
import shutil
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.segment_context_remote_speaker_attribution_policy/v1"
SPEC_SCHEMA = "murmurmark.remote_speaker_hard_v3_private_spec/v1"
FROZEN_SCHEMA = "murmurmark.remote_speaker_hard_v3_frozen_manifest/v1"
PUBLIC_SCHEMA = "murmurmark.remote_speaker_hard_v3_public_manifest/v1"
REPLAY_SCHEMA = "murmurmark.remote_speaker_hard_v3_replay/v1"
DEFAULT_POLICY = ROOT / "policies/segment-context-remote-speaker-attribution-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/segment-context-remote-speaker-attribution-v1"
DEFAULT_DURATION_OUT = ROOT / "sessions/_reports/duration-aware-remote-speaker-attribution-v2"
BASE_BUILDER = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
BASE_POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
DURATION_POLICY = ROOT / "policies/duration-aware-remote-speaker-attribution-v2.json"
ABSOLUTE_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")


class FreezeError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


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
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise FreezeError(f"invalid_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(value, dict):
        raise FreezeError(f"json_object_required:{path.name}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(canonical_json(value))
    os.replace(temporary, path)


def portable(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return f"external/{resolved.name}"


def fingerprint(path: Path) -> dict[str, Any]:
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_truth_lab_v1_frozen", BASE_BUILDER)
    if spec is None or spec.loader is None:
        raise FreezeError("base_builder_import_failed")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_policy(path: Path, *, fixture_mode: bool) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise FreezeError("policy_schema_invalid")
    if len(policy.get("topologies") or []) != 3:
        raise FreezeError("exactly_three_topologies_required")
    topology_ids = [str(row.get("id")) for row in policy["topologies"]]
    expected_topologies = {
        "silence_bounded_context_prototypes",
        "embedding_change_point_context",
        "conservative_dual_backend_context_fusion",
    }
    if set(topology_ids) != expected_topologies:
        raise FreezeError("predeclared_topology_scope_changed")
    if not fixture_mode:
        for source in ("truth_lab_v1", "duration_aware_v2"):
            for row in policy["development_evidence"][source].values():
                if not isinstance(row, dict) or "path" not in row:
                    continue
                evidence_path = ROOT / str(row["path"])
                if not evidence_path.is_file() or sha256(evidence_path) != str(row["sha256"]):
                    raise FreezeError(f"development_evidence_missing_or_stale:{source}")
    templates = policy.get("hard_v3", {}).get("scenario_templates") or {}
    if len(templates) < 3:
        raise FreezeError("hard_v3_scenarios_too_few")
    old = read_json(BASE_POLICY)
    duration = read_json(DURATION_POLICY)
    used = {str(row["system_voice"]) for row in old["renderer"]["speakers"]}
    used.update(str(value) for value in duration["hard_v2"]["renderer"]["eligible_unused_voices"])
    eligible = set(policy["hard_v3"]["renderer"]["eligible_unused_voices"])
    if eligible & used:
        raise FreezeError("hard_v3_renderer_voice_reused")
    if len(eligible) < int(policy["hard_v3"]["renderer"]["enrolled_speaker_count"]) + int(
        policy["hard_v3"]["renderer"]["open_set_speaker_count"]
    ):
        raise FreezeError("hard_v3_voice_pool_too_small")
    return policy


def private_tokens(
    rng: random.Random,
    count: int,
    minimum: int,
    maximum: int,
    forbidden: set[str],
) -> list[str]:
    consonants = ("b", "d", "f", "g", "k", "l", "m", "n", "p", "r", "s", "t", "v", "z")
    vowels = ("a", "e", "i", "o", "u")
    tokens: list[str] = []
    seen: set[str] = set()
    while len(tokens) < count:
        syllables = rng.randint(minimum, maximum)
        token = "".join(rng.choice(consonants) + rng.choice(vowels) for _ in range(syllables))
        if token in seen or token in forbidden:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def resolve_private_spec(policy: dict[str, Any], forbidden_tokens: set[str]) -> dict[str, Any]:
    seed = secrets.token_hex(32)
    rng = random.Random(int(seed, 16))
    renderer = policy["hard_v3"]["renderer"]
    voices = list(renderer["eligible_unused_voices"])
    rng.shuffle(voices)
    enrolled_count = int(renderer["enrolled_speaker_count"])
    open_count = int(renderer["open_set_speaker_count"])
    selected = voices[: enrolled_count + open_count]
    slots: dict[str, dict[str, Any]] = {}
    speakers = []
    for index, voice in enumerate(selected[:enrolled_count], start=1):
        speaker_id = f"remote_speaker_{index:02d}"
        slots[f"enrolled_{index}"] = {
            "speaker_id": speaker_id,
            "system_voice": voice,
            "enrolled": True,
        }
        speakers.append(slots[f"enrolled_{index}"])
    for index, voice in enumerate(selected[enrolled_count:], start=1):
        speaker_id = f"open_set_speaker_hard_v3_{index:02d}"
        slots[f"open_set_{index}"] = {
            "speaker_id": speaker_id,
            "system_voice": voice,
            "enrolled": False,
        }
        speakers.append(slots[f"open_set_{index}"])
    generator = policy["hard_v3"]["private_token_generator"]
    vocabulary = private_tokens(
        rng,
        int(generator["token_count"]),
        int(generator["minimum_syllables"]),
        int(generator["maximum_syllables"]),
        forbidden_tokens,
    )
    enrollment_words = int(renderer["enrollment_words_per_speaker"])
    enrollment_scripts: dict[str, list[str]] = {}
    token_cursor = 0
    for speaker in speakers:
        if not speaker["enrolled"]:
            continue
        enrollment_scripts[str(speaker["speaker_id"])] = vocabulary[
            token_cursor : token_cursor + enrollment_words
        ]
        token_cursor += enrollment_words
    hard_vocabulary = vocabulary[token_cursor:]
    maximum_scenario_words = max(
        sum(int(row["word_count"]) for row in template["events"])
        for template in policy["hard_v3"]["scenario_templates"].values()
    )
    if len(hard_vocabulary) < maximum_scenario_words:
        raise FreezeError("private_hard_v3_vocabulary_too_small")
    scenarios = {}
    for scenario_id, template in policy["hard_v3"]["scenario_templates"].items():
        events = []
        for row in template["events"]:
            slot = str(row["speaker_slot"])
            if slot not in slots:
                raise FreezeError(f"unknown_private_speaker_slot:{slot}")
            events.append({"speaker_id": slots[slot]["speaker_id"], **{k: v for k, v in row.items() if k != "speaker_slot"}})
        scenarios[scenario_id] = {"meeting_mode": template["meeting_mode"], "events": events}
    return {
        "schema": SPEC_SCHEMA,
        "private_seed": seed,
        "speakers": speakers,
        "enrollment_scripts": enrollment_scripts,
        "hard_vocabulary": hard_vocabulary,
        "scenarios": scenarios,
    }


def compatible_policy(policy: dict[str, Any], private_spec: dict[str, Any]) -> dict[str, Any]:
    renderer = {
        key: value
        for key, value in policy["hard_v3"]["renderer"].items()
        if key not in {"eligible_unused_voices", "enrolled_speaker_count", "open_set_speaker_count"}
    }
    renderer["speakers"] = private_spec["speakers"]
    return {
        "seed": private_spec["private_seed"],
        "renderer": renderer,
        "analysis": {"overlap_min_sec": 0.04},
    }


def build_enrollment(
    root: Path,
    policy: dict[str, Any],
    private_spec: dict[str, Any],
    renderer: Any,
    lab: Any,
) -> dict[str, Any]:
    settings = policy["hard_v3"]["renderer"]
    sample_rate = int(settings["sample_rate"])
    gap = lab.np.zeros(int(round(0.08 * sample_rate)), dtype=lab.np.int16)
    directory = root / "enrollment"
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for speaker in private_spec["speakers"]:
        if not speaker["enrolled"]:
            continue
        speaker_id = str(speaker["speaker_id"])
        tokens = private_spec["enrollment_scripts"][speaker_id]
        pieces = []
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
                "frames": len(pcm),
                "duration_sec": round(len(pcm) / sample_rate, 6),
                "sha256": sha256(path),
            }
        )
    write_json(
        directory / "enrollment_manifest.json",
        {
            "schema": "murmurmark.remote_speaker_hard_v3_private_enrollment/v1",
            "sample_rate": sample_rate,
            "rows": rows,
            "scripts_disjoint_from_sealed_hard_v3": True,
        },
    )
    return {
        "speaker_count": len(rows),
        "word_count": sum(int(row["word_count"]) for row in rows),
        "duration_sec": round(sum(float(row["duration_sec"]) for row in rows), 6),
    }


def freeze(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    private = args.out_dir.resolve() / "private/hard-v3"
    frozen_path = private / "frozen_manifest.json"
    if frozen_path.is_file():
        manifest = verify(private, args.policy, lab)
        print(f"hard-v3 already frozen: {portable(frozen_path)}")
        return manifest
    if private.exists():
        raise FreezeError("partial_hard_v3_exists_remove_manually")
    staging = private.with_name(f".hard-v3-freezing-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        old_tokens = set(lab.VOCABULARY)
        duration_private_spec = args.duration_out / "private/hard-v2/hard_v2_spec.json"
        if not duration_private_spec.is_file():
            raise FreezeError("hard_v2_private_spec_missing")
        duration_spec = read_json(duration_private_spec)
        duration_tokens = set(duration_spec.get("hard_vocabulary") or [])
        duration_tokens.update(
            token
            for tokens in (duration_spec.get("enrollment_scripts") or {}).values()
            for token in tokens
        )
        private_spec = resolve_private_spec(policy, old_tokens | duration_tokens)
        private_tokens_all = {
            token
            for tokens in private_spec["enrollment_scripts"].values()
            for token in tokens
        } | set(private_spec["hard_vocabulary"])
        if old_tokens & private_tokens_all:
            raise FreezeError("hard_v3_script_reuses_truth_lab_v1_token")
        if duration_tokens & private_tokens_all:
            raise FreezeError("hard_v3_script_reuses_hard_v2_token")
        write_json(staging / "hard_v3_spec.json", private_spec)
        lab.VOCABULARY = tuple(private_spec["hard_vocabulary"])
        rendered_policy = compatible_policy(policy, private_spec)
        cache = staging / "cache/words"
        renderer = (
            lab.FixtureRenderer(rendered_policy, cache)
            if args.fixture_mode
            else lab.SayRenderer(rendered_policy, cache)
        )
        enrollment_summary = build_enrollment(staging, policy, private_spec, renderer, lab)
        summaries = []
        for scenario_id, scenario in private_spec["scenarios"].items():
            print(f"freeze: hard-v3/{scenario_id}", flush=True)
            summaries.append(
                lab.build_scenario(
                    staging / "sealed",
                    scenario_id,
                    "hard_v3",
                    scenario,
                    rendered_policy,
                    renderer,
                )
            )
        artifacts = lab.artifact_hashes(staging)
        manifest = {
            "schema": FROZEN_SCHEMA,
            "version": VERSION,
            "mode": "fixture" if args.fixture_mode else "local_speech",
            "policy": fingerprint(args.policy),
            "implementation": fingerprint(Path(__file__).resolve()),
            "base_builder": fingerprint(BASE_BUILDER),
            "renderer": renderer.provenance,
            "enrollment_summary": enrollment_summary,
            "scenario_summaries": summaries,
            "speaker_count": len(private_spec["speakers"]),
            "enrolled_speaker_count": sum(bool(row["enrolled"]) for row in private_spec["speakers"]),
            "open_set_speaker_count": sum(not bool(row["enrolled"]) for row in private_spec["speakers"]),
            "artifacts": artifacts,
            "corpus_sha256": sha256_bytes(canonical_json(artifacts)),
            "sealed_for_candidate_development": True,
        }
        write_json(staging / "frozen_manifest.json", manifest)
        private.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, private)
        print(f"frozen: {portable(frozen_path)}")
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def verify(private: Path, policy_path: Path, lab: Any) -> dict[str, Any]:
    manifest = read_json(private / "frozen_manifest.json")
    if manifest.get("schema") != FROZEN_SCHEMA:
        raise FreezeError("hard_v3_frozen_schema_invalid")
    pins = {
        "policy": (policy_path, manifest.get("policy", {}).get("sha256")),
        "implementation": (Path(__file__).resolve(), manifest.get("implementation", {}).get("sha256")),
        "base_builder": (BASE_BUILDER, manifest.get("base_builder", {}).get("sha256")),
    }
    for name, (path, expected) in pins.items():
        if not path.is_file() or sha256(path) != expected:
            raise FreezeError(f"hard_v3_{name}_stale")
    expected_artifacts = manifest.get("artifacts") or {}
    actual_artifacts = lab.artifact_hashes(
        private,
        exclude_prefixes=("evaluation/", "hard_v3_opening_ledger.json", "replay_report.json"),
    )
    if expected_artifacts != actual_artifacts:
        raise FreezeError("hard_v3_artifacts_stale")
    if manifest.get("corpus_sha256") != sha256_bytes(canonical_json(expected_artifacts)):
        raise FreezeError("hard_v3_corpus_hash_stale")
    private_spec = read_json(private / "hard_v3_spec.json")
    if private_spec.get("schema") != SPEC_SCHEMA:
        raise FreezeError("hard_v3_private_spec_schema_invalid")
    return manifest


def replay(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    del policy
    private = args.out_dir.resolve() / "private/hard-v3"
    manifest = verify(private, args.policy, lab)
    max_error = 0
    word_count = 0
    boundary_count = 0
    for summary in manifest["scenario_summaries"]:
        scenario_id = str(summary["scenario_id"])
        directory = private / "sealed/sessions/hard_v3" / scenario_id
        scenario = read_json(directory / "scenario.json")
        words = lab.read_jsonl(directory / "truth_words.jsonl")
        boundaries = lab.read_jsonl(directory / "truth_boundaries.jsonl")
        if len(words) != int(scenario["word_count"]):
            raise FreezeError(f"hard_v3_word_conservation_failed:{scenario_id}")
        mixture, _ = lab.sf.read(directory / "mixture.wav", dtype="int16")
        reconstructed = sum(
            (
                lab.sf.read(directory / "sources" / f"{speaker}.wav", dtype="int16")[0].astype("int32")
                for speaker in scenario["active_speakers"]
            ),
            start=lab.np.zeros(len(mixture), dtype="int32"),
        )
        error = int(lab.np.max(lab.np.abs(reconstructed - mixture.astype("int32")), initial=0))
        max_error = max(max_error, error)
        word_count += len(words)
        boundary_count += len(boundaries)
    result = {
        "schema": REPLAY_SCHEMA,
        "decision": "DETERMINISTIC_REPLAY_VERIFIED" if max_error == 0 else "REPLAY_FAILED",
        "corpus_sha256": manifest["corpus_sha256"],
        "scenario_count": len(manifest["scenario_summaries"]),
        "word_count": word_count,
        "boundary_count": boundary_count,
        "maximum_reconstruction_sample_error": max_error,
        "private_artifacts_unchanged": True,
    }
    write_json(private / "replay_report.json", result)
    if result["decision"] != "DETERMINISTIC_REPLAY_VERIFIED":
        raise FreezeError("hard_v3_replay_failed")
    print("hard-v3 replay: deterministic")
    return result


def public_manifest(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> dict[str, Any]:
    private = args.out_dir.resolve() / "private/hard-v3"
    manifest = verify(private, args.policy, lab)
    private_spec = read_json(private / "hard_v3_spec.json")
    words = []
    boundaries = []
    mixed = 0
    for summary in manifest["scenario_summaries"]:
        directory = private / "sealed/sessions/hard_v3" / str(summary["scenario_id"])
        scenario_words = lab.read_jsonl(directory / "truth_words.jsonl")
        words.extend(scenario_words)
        boundaries.extend(lab.read_jsonl(directory / "truth_boundaries.jsonl"))
        mixed += sum(row.get("truth_class") == "mixed" for row in scenario_words)
    value = {
        "schema": PUBLIC_SCHEMA,
        "decision": "HARD_V3_FROZEN_UNOPENED",
        "policy": fingerprint(args.policy),
        "implementation": fingerprint(Path(__file__).resolve()),
        "base_builder": fingerprint(BASE_BUILDER),
        "private_frozen_manifest": fingerprint(private / "frozen_manifest.json"),
        "corpus_sha256": manifest["corpus_sha256"],
        "mode": manifest["mode"],
        "scenario_count": len(manifest["scenario_summaries"]),
        "speaker_count": len(private_spec["speakers"]),
        "enrolled_speaker_count": manifest["enrolled_speaker_count"],
        "open_set_speaker_count": manifest["open_set_speaker_count"],
        "enrollment_speaker_count": manifest["enrollment_summary"]["speaker_count"],
        "enrollment_word_count": manifest["enrollment_summary"]["word_count"],
        "word_count": len(words),
        "mixed_word_count": mixed,
        "boundary_count": len(boundaries),
        "maximum_reconstruction_sample_error": max(
            int(row["reconstruction_max_sample_error"]) for row in manifest["scenario_summaries"]
        ),
        "scripts_disjoint_from_truth_lab_v1": True,
        "scripts_disjoint_from_hard_v2": True,
        "renderer_voices_disjoint_from_truth_lab_v1": True,
        "renderer_voices_disjoint_from_hard_v2": True,
        "hard_v3_opened_for_candidate_decision": False,
        "private_payload_excluded": True,
    }
    encoded = canonical_json(value).decode()
    if ABSOLUTE_PATH_RE.search(encoded):
        raise FreezeError("public_manifest_contains_absolute_path")
    for forbidden in ("system_voice", "private_seed", "vocabulary", '"text"'):
        if forbidden in encoded:
            raise FreezeError(f"public_manifest_contains_private_key:{forbidden}")
    destination = args.public_manifest or (args.out_dir / "hard_v3_public_manifest.json")
    write_json(destination, value)
    print(f"public manifest: {portable(destination)}")
    return value


def status(args: argparse.Namespace, policy: dict[str, Any], lab: Any) -> int:
    del policy
    private = args.out_dir.resolve() / "private/hard-v3"
    if not (private / "frozen_manifest.json").is_file():
        print("decision: HARD_V3_NOT_FROZEN")
        print("next: murmurmark corpus remote-duration-v2 freeze")
        return 1
    manifest = verify(private, args.policy, lab)
    ledger = private / "hard_v3_opening_ledger.json"
    print("decision: HARD_V3_FROZEN" if not ledger.exists() else "decision: HARD_V3_OPENED")
    print(f"scenarios: {len(manifest['scenario_summaries'])}")
    print(f"corpus_sha256: {manifest['corpus_sha256']}")
    print(f"opened: {str(ledger.exists()).lower()}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("freeze", "status", "replay", "public-manifest"))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--duration-out", type=Path, default=DEFAULT_DURATION_OUT)
    parser.add_argument("--public-manifest", type=Path)
    parser.add_argument("--fixture-mode", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    args.duration_out = args.duration_out.expanduser().resolve()
    if args.public_manifest is not None:
        args.public_manifest = args.public_manifest.expanduser().resolve()
    try:
        policy = load_policy(args.policy, fixture_mode=args.fixture_mode)
        lab = load_base_module()
        if args.action == "freeze":
            freeze(args, policy, lab)
        elif args.action == "replay":
            replay(args, policy, lab)
        elif args.action == "public-manifest":
            public_manifest(args, policy, lab)
        else:
            return status(args, policy, lab)
        return 0
    except FreezeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
