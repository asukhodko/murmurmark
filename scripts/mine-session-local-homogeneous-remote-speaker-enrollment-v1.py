#!/usr/bin/env python3
"""Mine session-local speaker-homogeneous remote enrollment without production mutation."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from itertools import combinations
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/session-local-homogeneous-remote-speaker-enrollment-mining-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/session-local-homogeneous-remote-speaker-enrollment-mining-v1"
ECAPA_WORKER = ROOT / "scripts/ecapa-speaker-embedding-worker.py"
WAVLM_WORKER = ROOT / "scripts/wavlm-speaker-embedding-worker.py"

POLICY_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_policy/v1"
INPUT_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_input/v1"
INVENTORY_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_candidate/v1"
PACK_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_pack/v1"
EVALUATION_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_evaluation/v1"
REPORT_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_report/v1"
REPLAY_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_replay/v1"
MANIFEST_SCHEMA = "murmurmark.session_local_homogeneous_remote_speaker_enrollment_manifest/v1"
REQUEST_SCHEMA = "murmurmark.speaker_embedding_request/v1"
EMBEDDING_SCHEMA = "murmurmark.speaker_embedding_result/v1"


class MiningError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2).encode() + b"\n"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, *values: Any) -> str:
    return prefix + "_" + hashlib.sha256(canonical(list(values))).hexdigest()[:16]


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
        raise MiningError(f"expected JSON object: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise MiningError(f"expected JSON object at {path}:{line_number}")
        rows.append(value)
    return rows


def portable(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def artifact(path: Path) -> dict[str, Any]:
    return {"path": portable(path), "bytes": path.stat().st_size, "sha256": sha256(path)}


def repo_path(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise MiningError(f"repository-relative path required: {raw}")
    return ROOT / path


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise MiningError("invalid embedding vector")
    return value / norm


def cosine(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.dot(normalize(left), normalize(right)))


def centroid(vectors: list[np.ndarray]) -> np.ndarray:
    if not vectors:
        raise MiningError("empty centroid")
    return normalize(np.mean(np.stack([normalize(row) for row in vectors]), axis=0))


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise MiningError(f"unsupported policy schema: {policy.get('schema')}")
    if policy.get("state") != "frozen_before_development_truth_evaluation":
        raise MiningError("policy is not frozen before development truth")
    mining = policy.get("mining") or {}
    fixed = {
        "target_text_read": False,
        "human_names_read": False,
        "cross_session_voice_linking": False,
        "window_sec": 4.0,
        "minimum_selected_windows_per_profile": 3,
        "maximum_selected_windows_per_profile": 5,
    }
    for key, expected in fixed.items():
        if mining.get(key) != expected:
            raise MiningError(f"frozen mining contract changed: {key}")
    evaluation = policy.get("evaluation") or {}
    if evaluation.get("threshold_grid_search_allowed") is not False:
        raise MiningError("threshold grid search must remain disabled")
    if evaluation.get("post_hoc_tuning_allowed") is not False:
        raise MiningError("post-hoc tuning must remain disabled")
    if (policy.get("decision") or {}).get("production_promotion_allowed") is not False:
        raise MiningError("production promotion must remain disabled")
    return policy


def verify_policy_sources(policy: dict[str, Any], phase: str | None = None) -> list[dict[str, Any]]:
    results = []
    for source_id, expected in sorted((policy.get("sources") or {}).items()):
        if phase is not None and expected.get("phase") != phase:
            continue
        path = repo_path(str(expected["path"]))
        status = "verified"
        actual = None
        size = None
        if not path.is_file():
            status = "missing"
        else:
            actual = sha256(path)
            size = path.stat().st_size
            if actual != expected.get("sha256"):
                status = "sha256_mismatch"
        results.append(
            {
                "id": source_id,
                "phase": expected.get("phase"),
                "path": expected.get("path"),
                "bytes": size,
                "sha256": actual,
                "status": status,
            }
        )
    failures = [row for row in results if row["status"] != "verified"]
    if failures:
        raise MiningError("frozen source verification failed: " + ",".join(row["id"] for row in failures))
    return results


def session_paths(session_id: str) -> dict[str, Path]:
    session = ROOT / "sessions" / session_id
    coverage = session / "derived/audit/remote-speaker-coverage-v3"
    return {
        "remote_audio": session / "audio/remote/000001.caf",
        "utterances": coverage / "utterance_attribution.jsonl",
        "speaker_map": coverage / "speaker_map.json",
        "coverage_report": coverage / "report.json",
        "coverage_manifest": coverage / "artifact_manifest.json",
    }


def verify_production_guards(policy: dict[str, Any]) -> dict[str, Any]:
    previous = ROOT / "sessions/_reports/session-local-remote-speaker-enrollment-hardening-v1/private/input_manifest.json"
    value = read_json(previous)
    inherited = value.get("inherited_artifacts") or []
    expected = int(policy["scope"]["expected_production_guards"])
    failures = []
    for row in inherited:
        path = repo_path(str(row.get("path") or ""))
        if not path.is_file():
            failures.append(f"missing:{row.get('id')}")
        elif path.stat().st_size != int(row.get("bytes", -1)):
            failures.append(f"size:{row.get('id')}")
        elif sha256(path) != row.get("sha256"):
            failures.append(f"sha256:{row.get('id')}")
    if len(inherited) != expected:
        failures.append(f"count:{len(inherited)}")
    if failures:
        raise MiningError("production guard verification failed: " + ",".join(failures[:8]))
    return {"manifest": artifact(previous), "verified_artifacts": len(inherited)}


def verify_model_provenance(policy: dict[str, Any]) -> dict[str, Any]:
    ecapa_model = Path(
        os.environ.get("MURMURMARK_REMOTE_SPEAKER_ECAPA_MODEL", policy["ecapa"]["default_model_path"])
    ).expanduser().resolve()
    ecapa_runtime = Path(
        os.environ.get(
            "MURMURMARK_REMOTE_SPEAKER_IDENTITY_RUNTIME", policy["ecapa"]["default_runtime_path"]
        )
    ).expanduser().resolve()
    wavlm_model = Path(
        os.environ.get("MURMURMARK_REMOTE_SPEAKER_WAVLM_MODEL", policy["wavlm"]["default_model_path"])
    ).expanduser().resolve()
    ecapa_manifest = read_json(
        ROOT / "sessions/_reports/ecapa-remote-speaker-shadow-qualification-v1/private/input_manifest.json"
    )
    expected_ecapa = {row["name"]: row["sha256"] for row in ecapa_manifest["candidate"]["files"]}
    ecapa_files = []
    for name, expected in sorted(expected_ecapa.items()):
        path = ecapa_model / name
        if not path.is_file() or sha256(path) != expected:
            raise MiningError(f"ECAPA model missing or changed: {name}")
        ecapa_files.append({"name": name, "bytes": path.stat().st_size, "sha256": expected})
    python = ecapa_runtime / "bin/python"
    if not python.is_file():
        raise MiningError("ECAPA runtime is unavailable")

    wavlm_policy = read_json(repo_path(policy["sources"]["wavlm_policy"]["path"]))
    wavlm_files = []
    for name, expected in sorted(wavlm_policy["backend"]["files"].items()):
        path = wavlm_model / name
        if not path.is_file() or sha256(path) != expected:
            raise MiningError(f"WavLM model missing or changed: {name}")
        wavlm_files.append({"name": name, "bytes": path.stat().st_size, "sha256": expected})
    return {
        "ecapa": {
            "model_id": policy["ecapa"]["model_id"],
            "model_path": str(ecapa_model),
            "runtime_path": str(ecapa_runtime),
            "files": ecapa_files,
        },
        "wavlm": {
            "model_id": policy["wavlm"]["model_id"],
            "model_path": str(wavlm_model),
            "files": wavlm_files,
        },
    }


def spread_indices(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    if limit <= 1:
        return [count // 2]
    return sorted({int(round(index * (count - 1) / (limit - 1))) for index in range(limit)})


def rms_dbfs(path: Path, start: float, end: float) -> float:
    with sf.SoundFile(path) as source:
        first = max(0, int(round(start * source.samplerate)))
        last = min(len(source), int(round(end * source.samplerate)))
        if last <= first:
            return -120.0
        source.seek(first)
        values = source.read(last - first, dtype="float32", always_2d=True).mean(axis=1)
    rms = float(np.sqrt(np.mean(np.square(values), dtype=np.float64))) if values.size else 0.0
    return round(20.0 * math.log10(max(rms, 1e-12)), 6)


def build_candidate_inventory(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mining = policy["mining"]
    window = float(mining["window_sec"])
    inset = float(mining["boundary_inset_sec"])
    minimum_turn = float(mining["minimum_turn_sec"])
    limit = int(mining["maximum_pool_windows_per_profile"])
    minimum_rms = float(mining["minimum_rms_dbfs"])
    inventory: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    profile_count = 0
    for session_id in policy["scope"]["sessions"]:
        paths = session_paths(session_id)
        for name, path in sorted(paths.items()):
            if not path.is_file():
                raise MiningError(f"session source missing: {session_id}:{name}")
            source_rows.append({"session_id": session_id, "kind": name, **artifact(path)})
        speaker_map = read_json(paths["speaker_map"])
        speakers = sorted(str(row["speaker_id"]) for row in speaker_map.get("speakers") or [])
        profile_count += len(speakers)
        turns: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for utterance in read_jsonl(paths["utterances"]):
            utterance_id = str(utterance.get("utterance_id") or "")
            for turn in utterance.get("speaker_turns") or []:
                speaker = turn.get("speaker_id")
                start = float(turn.get("start") or 0)
                end = float(turn.get("end") or 0)
                if (
                    turn.get("status") != mining["eligible_turn_status"]
                    or speaker not in speakers
                    or end - start < minimum_turn
                ):
                    continue
                center = (start + end) / 2.0
                candidate_start = max(start + inset, center - window / 2.0)
                candidate_end = candidate_start + window
                if candidate_end > end - inset:
                    candidate_end = end - inset
                    candidate_start = candidate_end - window
                if candidate_start < start + inset - 1e-6 or candidate_end > end - inset + 1e-6:
                    continue
                turns[str(speaker)].append(
                    {
                        "utterance_id": utterance_id,
                        "turn_start": start,
                        "turn_end": end,
                        "start": round(candidate_start, 6),
                        "end": round(candidate_end, 6),
                    }
                )
        for speaker in speakers:
            eligible = sorted(turns.get(speaker) or [], key=lambda row: (row["start"], row["utterance_id"]))
            for index in spread_indices(len(eligible), limit):
                row = eligible[index]
                level = rms_dbfs(paths["remote_audio"], row["start"], row["end"])
                key = stable_id("hew", session_id, speaker, row["utterance_id"], row["start"], row["end"])
                inventory.append(
                    {
                        "schema": INVENTORY_SCHEMA,
                        "key": key,
                        "session_id": session_id,
                        "speaker_id": speaker,
                        "utterance_id": row["utterance_id"],
                        "turn_start": round(row["turn_start"], 6),
                        "turn_end": round(row["turn_end"], 6),
                        "start": row["start"],
                        "end": row["end"],
                        "duration_sec": round(row["end"] - row["start"], 6),
                        "rms_dbfs": level,
                        "energy_gate_passed": level >= minimum_rms,
                        "source_audio": portable(paths["remote_audio"]),
                    }
                )
    if profile_count != int(policy["scope"]["expected_profiles"]):
        raise MiningError(f"profile count changed: {profile_count}")
    return sorted(inventory, key=lambda row: row["key"]), {
        "schema": INPUT_SCHEMA,
        "policy": artifact(DEFAULT_POLICY),
        "sources": source_rows,
        "counts": {"sessions": len(policy["scope"]["sessions"]), "profiles": profile_count},
        "text_fields_read": [],
        "development_truth_read": False,
    }


def embedding_request(
    policy: dict[str, Any], rows: list[dict[str, Any]], backend: str
) -> dict[str, Any]:
    definition = policy[backend]
    return {
        "schema": REQUEST_SCHEMA,
        "model_id": definition["model_id"],
        "model_revision": "frozen_local",
        "allow_errors": True,
        "requests": [
            {
                "key": row["key"],
                "path": str(repo_path(row["source_audio"]).resolve()),
                "start": float(row["start"]),
                "end": float(row["end"]),
                "minimum_sec": 1.0,
            }
            for row in rows
            if row.get("energy_gate_passed")
        ],
    }


def run_workers(policy: dict[str, Any], out: Path, stem: str, rows: list[dict[str, Any]]) -> None:
    provenance = verify_model_provenance(policy)
    ecapa_request = out / "private" / f"{stem}_ecapa_request.json"
    wavlm_request = out / "private" / f"{stem}_wavlm_request.json"
    ecapa_output = out / "private" / f"{stem}_ecapa_embeddings.json"
    wavlm_output = out / "private" / f"{stem}_wavlm_embeddings.json"
    write_json(ecapa_request, embedding_request(policy, rows, "ecapa"))
    write_json(wavlm_request, embedding_request(policy, rows, "wavlm"))
    commands = [
        [
            "nice", "-n", "20", str(Path(provenance["ecapa"]["runtime_path"]) / "bin/python"),
            str(ECAPA_WORKER), "--request", str(ecapa_request), "--output", str(ecapa_output),
            "--model", provenance["ecapa"]["model_path"], "--threads", "4",
        ],
        [
            "nice", "-n", "20", sys.executable, str(WAVLM_WORKER), "--request", str(wavlm_request),
            "--output", str(wavlm_output), "--model", provenance["wavlm"]["model_path"],
            "--threads", "4", "--batch-size", "8",
        ],
    ]
    for command in commands:
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if completed.returncode != 0:
            raise MiningError(f"embedding worker failed: {Path(command[3]).name}")


def load_embeddings(path: Path) -> dict[str, np.ndarray]:
    payload = read_json(path)
    if payload.get("schema") != EMBEDDING_SCHEMA:
        raise MiningError(f"unsupported embedding output: {path}")
    return {str(row["key"]): normalize(np.asarray(row["embedding"], dtype=np.float64)) for row in payload.get("rows") or []}


def pairwise_min(keys: tuple[str, ...], vectors: dict[str, np.ndarray]) -> float:
    return min(cosine(vectors[left], vectors[right]) for left, right in combinations(keys, 2))


def pairwise_mean(keys: tuple[str, ...], vectors: dict[str, np.ndarray]) -> float:
    scores = [cosine(vectors[left], vectors[right]) for left, right in combinations(keys, 2)]
    return float(np.mean(scores))


def choose_joint_clique(
    rows: list[dict[str, Any]],
    ecapa: dict[str, np.ndarray],
    wavlm: dict[str, np.ndarray],
    policy: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    mining = policy["mining"]
    minimum = int(mining["minimum_selected_windows_per_profile"])
    maximum = int(mining["maximum_selected_windows_per_profile"])
    temporal_span = float(mining["minimum_temporal_span_sec"])
    ecapa_min = float(policy["ecapa"]["minimum_pairwise_similarity"])
    wavlm_min = float(policy["wavlm"]["minimum_pairwise_similarity"])
    by_key = {row["key"]: row for row in rows}
    available = sorted(key for key in by_key if key in ecapa and key in wavlm)
    for size in range(min(maximum, len(available)), minimum - 1, -1):
        accepted: list[tuple[float, tuple[str, ...], float, float]] = []
        for keys in combinations(available, size):
            if len({by_key[key]["utterance_id"] for key in keys}) != size:
                continue
            centers = [(float(by_key[key]["start"]) + float(by_key[key]["end"])) / 2 for key in keys]
            if max(centers) - min(centers) < temporal_span:
                continue
            ecapa_pairwise = pairwise_min(keys, ecapa)
            wavlm_pairwise = pairwise_min(keys, wavlm)
            if ecapa_pairwise < ecapa_min or wavlm_pairwise < wavlm_min:
                continue
            score = pairwise_mean(keys, ecapa) + pairwise_mean(keys, wavlm)
            accepted.append((score, keys, ecapa_pairwise, wavlm_pairwise))
        if accepted:
            accepted.sort(key=lambda row: (-row[0], row[1]))
            score, keys, ecapa_pairwise, wavlm_pairwise = accepted[0]
            return list(keys), {
                "reason": "largest_joint_pairwise_clique",
                "joint_score": round(score, 6),
                "ecapa_pairwise_min": round(ecapa_pairwise, 6),
                "wavlm_pairwise_min": round(wavlm_pairwise, 6),
            }
    return [], {"reason": "no_joint_pairwise_clique"}


def build_pack(
    policy: dict[str, Any], inventory: list[dict[str, Any]], ecapa: dict[str, np.ndarray], wavlm: dict[str, np.ndarray]
) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in inventory:
        if row.get("energy_gate_passed"):
            grouped[(row["session_id"], row["speaker_id"])].append(row)
    provisional: dict[tuple[str, str], dict[str, Any]] = {}
    for identity, rows in sorted(grouped.items()):
        if len(rows) < int(policy["mining"]["minimum_pool_windows_per_profile"]):
            provisional[identity] = {"keys": [], "reason": "insufficient_pool_windows"}
            continue
        keys, details = choose_joint_clique(rows, ecapa, wavlm, policy)
        provisional[identity] = {"keys": keys, **details}

    provisional_centroids: dict[str, dict[tuple[str, str], np.ndarray]] = {
        "ecapa": {},
        "wavlm": {},
    }
    for identity, selection in provisional.items():
        keys = selection["keys"]
        if keys:
            provisional_centroids["ecapa"][identity] = centroid([ecapa[key] for key in keys])
            provisional_centroids["wavlm"][identity] = centroid([wavlm[key] for key in keys])

    refined: dict[tuple[str, str], dict[str, Any]] = {}
    for identity, selection in sorted(provisional.items()):
        session_id, _ = identity
        eligible = []
        for row in grouped.get(identity) or []:
            key = row["key"]
            if key not in ecapa or key not in wavlm or identity not in provisional_centroids["ecapa"]:
                continue
            passed = True
            for backend, vectors in (("ecapa", ecapa), ("wavlm", wavlm)):
                own = cosine(vectors[key], provisional_centroids[backend][identity])
                impostors = [
                    cosine(vectors[key], center)
                    for other, center in provisional_centroids[backend].items()
                    if other[0] == session_id and other != identity
                ]
                margin = own - max(impostors) if impostors else None
                passed = passed and own >= float(policy[backend]["minimum_centroid_similarity"])
                passed = passed and (
                    margin is None or margin >= float(policy[backend]["minimum_session_impostor_margin"])
                )
            if passed:
                eligible.append(row)
        keys, details = choose_joint_clique(eligible, ecapa, wavlm, policy)
        refined[identity] = {
            "keys": keys,
            **details,
            "impostor_filtered_windows": len(eligible),
            "provisional_reason": selection["reason"],
        }

    centroids: dict[str, dict[tuple[str, str], np.ndarray]] = {"ecapa": {}, "wavlm": {}}
    for identity, selection in refined.items():
        keys = selection["keys"]
        if keys:
            centroids["ecapa"][identity] = centroid([ecapa[key] for key in keys])
            centroids["wavlm"][identity] = centroid([wavlm[key] for key in keys])

    profiles = []
    inventory_by_key = {row["key"]: row for row in inventory}
    for identity, selection in sorted(refined.items()):
        session_id, speaker_id = identity
        keys = selection["keys"]
        reasons = []
        window_evidence = []
        for key in keys:
            backend_evidence = {}
            for backend, vectors in (("ecapa", ecapa), ("wavlm", wavlm)):
                own = cosine(vectors[key], centroids[backend][identity])
                impostors = [
                    (other[1], cosine(vectors[key], center))
                    for other, center in centroids[backend].items()
                    if other[0] == session_id and other != identity
                ]
                impostors.sort(key=lambda row: (-row[1], row[0]))
                nearest = impostors[0] if impostors else None
                margin = own - nearest[1] if nearest else None
                similarity_ok = own >= float(policy[backend]["minimum_centroid_similarity"])
                margin_ok = nearest is None or margin >= float(policy[backend]["minimum_session_impostor_margin"])
                backend_evidence[backend] = {
                    "own_similarity": round(own, 6),
                    "nearest_impostor": nearest[0] if nearest else None,
                    "nearest_impostor_similarity": round(nearest[1], 6) if nearest else None,
                    "impostor_margin": round(margin, 6) if margin is not None else None,
                    "impostor_gate": "not_applicable_single_profile" if nearest is None else "evaluated",
                    "passed": similarity_ok and margin_ok,
                }
                if not similarity_ok:
                    reasons.append(f"{backend}_centroid_similarity")
                if not margin_ok:
                    reasons.append(f"{backend}_session_impostor_margin")
            window_evidence.append({"key": key, "backends": backend_evidence})
        qualified = bool(keys) and not reasons
        profiles.append(
            {
                "session_id": session_id,
                "speaker_id": speaker_id,
                "status": "qualified" if qualified else "abstained",
                "reason": "joint_model_homogeneous_enrollment" if qualified else selection["reason"],
                "reasons": sorted(set(reasons)),
                "pool_windows": len(grouped.get(identity) or []),
                "selected_windows": len(keys),
                "selected": [
                    {
                        "key": key,
                        "utterance_id": inventory_by_key[key]["utterance_id"],
                        "start": inventory_by_key[key]["start"],
                        "end": inventory_by_key[key]["end"],
                        "rms_dbfs": inventory_by_key[key]["rms_dbfs"],
                    }
                    for key in keys
                ],
                "selection": {key: value for key, value in selection.items() if key != "keys"},
                "window_evidence": window_evidence,
            }
        )
    return {
        "schema": PACK_SCHEMA,
        "policy_sha256": sha256(DEFAULT_POLICY),
        "development_truth_read": False,
        "profile_count": len(profiles),
        "qualified_profiles": sum(row["status"] == "qualified" for row in profiles),
        "profiles": profiles,
    }


def action_preflight(policy: dict[str, Any], out: Path) -> int:
    prepare_sources = verify_policy_sources(policy, "prepare")
    guards = verify_production_guards(policy)
    models = verify_model_provenance(policy)
    result = {
        "schema": "murmurmark.session_local_homogeneous_remote_speaker_enrollment_preflight/v1",
        "status": "ready",
        "prepare_sources": prepare_sources,
        "production_guards": guards,
        "models": models,
        "development_truth_read": False,
    }
    write_json(out / "private/preflight.json", result)
    print("preflight: ready")
    return 0


def action_prepare(policy: dict[str, Any], out: Path) -> int:
    action_preflight(policy, out)
    inventory, input_manifest = build_candidate_inventory(policy)
    write_jsonl(out / "private/candidate_inventory.jsonl", inventory)
    write_json(out / "private/input_manifest.json", input_manifest)
    run_workers(policy, out, "candidate", inventory)
    ecapa = load_embeddings(out / "private/candidate_ecapa_embeddings.json")
    wavlm = load_embeddings(out / "private/candidate_wavlm_embeddings.json")
    pack = build_pack(policy, inventory, ecapa, wavlm)
    pack["frozen_inputs"] = {
        "input_manifest": artifact(out / "private/input_manifest.json"),
        "candidate_inventory": artifact(out / "private/candidate_inventory.jsonl"),
        "ecapa_embeddings": artifact(out / "private/candidate_ecapa_embeddings.json"),
        "wavlm_embeddings": artifact(out / "private/candidate_wavlm_embeddings.json"),
    }
    write_json(out / "private/candidate_pack.prepared.json", pack)
    print(f"prepared: profiles={pack['profile_count']} qualified={pack['qualified_profiles']}")
    return 0


def action_freeze(policy: dict[str, Any], out: Path) -> int:
    prepared_path = out / "private/candidate_pack.prepared.json"
    if not prepared_path.is_file():
        raise MiningError("candidate pack is not prepared")
    pack = read_json(prepared_path)
    if pack.get("schema") != PACK_SCHEMA or pack.get("development_truth_read") is not False:
        raise MiningError("prepared candidate pack violates staged truth contract")
    frozen = {
        **pack,
        "state": "frozen_before_development_truth_evaluation",
        "prepared_sha256": sha256(prepared_path),
    }
    frozen_path = out / "private/candidate_pack.frozen.json"
    if frozen_path.is_file() and read_json(frozen_path) != frozen:
        raise MiningError("existing frozen candidate pack differs; remove report directory explicitly")
    write_json(frozen_path, frozen)
    write_json(
        out / "candidate_pack.public.json",
        {
            "schema": PACK_SCHEMA,
            "state": frozen["state"],
            "candidate_pack_sha256": sha256(frozen_path),
            "profile_count": frozen["profile_count"],
            "qualified_profiles": frozen["qualified_profiles"],
            "profiles": [
                {
                    "session_alias": f"session_{policy['scope']['sessions'].index(row['session_id']) + 1:02d}",
                    "speaker_id": row["speaker_id"],
                    "status": row["status"],
                    "pool_windows": row["pool_windows"],
                    "selected_windows": row["selected_windows"],
                    "reason": row["reason"],
                }
                for row in frozen["profiles"]
            ],
            "development_truth_read": False,
        },
    )
    print(f"frozen: sha256={sha256(frozen_path)}")
    return 0


def verify_frozen_pack(out: Path) -> dict[str, Any]:
    path = out / "private/candidate_pack.frozen.json"
    if not path.is_file():
        raise MiningError("candidate pack is not frozen")
    pack = read_json(path)
    if pack.get("state") != "frozen_before_development_truth_evaluation":
        raise MiningError("candidate pack state changed")
    if pack.get("policy_sha256") != sha256(DEFAULT_POLICY):
        raise MiningError("frozen candidate policy changed")
    for name, expected in (pack.get("frozen_inputs") or {}).items():
        source = repo_path(str(expected["path"]))
        if not source.is_file() or source.stat().st_size != expected["bytes"] or sha256(source) != expected["sha256"]:
            raise MiningError(f"frozen candidate input changed: {name}")
    return pack


def target_rows(policy: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    verify_policy_sources(policy, "evaluate_after_candidate_pack_freeze")
    development = {
        row["item_id"]: row
        for row in read_jsonl(repo_path(policy["sources"]["development_adjudication"]["path"]))
    }
    selections = read_jsonl(repo_path(policy["sources"]["development_items"]["path"]))
    rows = []
    for row in selections:
        item_id = str(row["item_id"])
        if item_id not in development:
            continue
        audio = repo_path(str(row["materialized_audio"]["path"]))
        if not audio.is_file() or sha256(audio) != row["materialized_audio"]["sha256"]:
            raise MiningError(f"development audio changed: {item_id}")
        with sf.SoundFile(audio) as source:
            duration = len(source) / source.samplerate
        rows.append(
            {
                "key": item_id,
                "session_id": row["session_id"],
                "source_audio": portable(audio),
                "start": 0.0,
                "end": round(duration, 6),
                "energy_gate_passed": True,
            }
        )
    expected = int(policy["scope"]["expected_development_items"])
    if len(rows) != expected or len(development) != expected:
        raise MiningError(f"development item count changed: rows={len(rows)} truth={len(development)}")
    return sorted(rows, key=lambda row: row["key"]), development


def pack_centroids(
    pack: dict[str, Any], ecapa: dict[str, np.ndarray], wavlm: dict[str, np.ndarray]
) -> dict[str, dict[tuple[str, str], np.ndarray]]:
    result: dict[str, dict[tuple[str, str], np.ndarray]] = {"ecapa": {}, "wavlm": {}}
    for profile in pack["profiles"]:
        if profile["status"] != "qualified":
            continue
        identity = (profile["session_id"], profile["speaker_id"])
        keys = [row["key"] for row in profile["selected"]]
        result["ecapa"][identity] = centroid([ecapa[key] for key in keys])
        result["wavlm"][identity] = centroid([wavlm[key] for key in keys])
    return result


def classify_target(
    row: dict[str, Any],
    target_vectors: dict[str, dict[str, np.ndarray]],
    centroids: dict[str, dict[tuple[str, str], np.ndarray]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    backend_rows = {}
    accepted = []
    for backend in ("ecapa", "wavlm"):
        vector = target_vectors[backend].get(row["key"])
        candidates = [
            (identity[1], cosine(vector, center))
            for identity, center in centroids[backend].items()
            if identity[0] == row["session_id"] and vector is not None
        ]
        candidates.sort(key=lambda item: (-item[1], item[0]))
        if not candidates:
            backend_rows[backend] = {"status": "unavailable"}
            continue
        top = candidates[0]
        runner_up = candidates[1] if len(candidates) > 1 else None
        margin = top[1] - runner_up[1] if runner_up else None
        passed = top[1] >= float(policy[backend]["minimum_centroid_similarity"]) and (
            runner_up is None or margin >= float(policy[backend]["minimum_session_impostor_margin"])
        )
        backend_rows[backend] = {
            "status": "accepted" if passed else "abstained",
            "speaker_id": top[0],
            "similarity": round(top[1], 6),
            "margin": round(margin, 6) if margin is not None else None,
        }
        if passed:
            accepted.append(top[0])
    prediction = accepted[0] if len(accepted) == 2 and len(set(accepted)) == 1 else None
    return {
        "item_id": row["key"],
        "session_id": row["session_id"],
        "prediction": prediction,
        "outcome": "accepted" if prediction else "abstained",
        "reason": "ecapa_wavlm_agreement" if prediction else "insufficient_or_conflicting_model_evidence",
        "backends": backend_rows,
    }


def evaluation_core(policy: dict[str, Any], out: Path, pack: dict[str, Any]) -> dict[str, Any]:
    development_rows, truth = target_rows(policy)
    run_workers(policy, out, "development", development_rows)
    candidate_ecapa = load_embeddings(out / "private/candidate_ecapa_embeddings.json")
    candidate_wavlm = load_embeddings(out / "private/candidate_wavlm_embeddings.json")
    target_vectors = {
        "ecapa": load_embeddings(out / "private/development_ecapa_embeddings.json"),
        "wavlm": load_embeddings(out / "private/development_wavlm_embeddings.json"),
    }
    centroids = pack_centroids(pack, candidate_ecapa, candidate_wavlm)
    decisions = []
    for row in development_rows:
        decision = classify_target(row, target_vectors, centroids, policy)
        expected = truth[row["key"]]
        prediction = decision["prediction"]
        truth_kind = expected["truth_kind"]
        truth_outcome = expected["truth_outcome"]
        if prediction is None:
            result = "abstained_positive" if truth_kind == "positive_identity" else "safe_abstention"
        elif truth_kind == "positive_identity" and prediction == truth_outcome:
            result = "correct_identity"
        else:
            result = "unsafe_fail_closed_acceptance"
        decisions.append(
            {
                **decision,
                "truth_kind": truth_kind,
                "truth_outcome": truth_outcome,
                "candidate_result": result,
                "control_outcome": expected["control_outcome"],
                "control_prediction": expected["control_prediction"],
                "confirmed_v1_additive_gain": bool(expected["confirmed_v1_additive_gain"]),
                "word_count": int(expected["word_count"]),
                "coverage_weight_sec": float(expected["coverage_weight_sec"]),
            }
        )
    counts = Counter(row["candidate_result"] for row in decisions)
    unsafe = int(counts["unsafe_fail_closed_acceptance"])
    preserved_gains = sum(
        row["confirmed_v1_additive_gain"] and row["candidate_result"] == "correct_identity"
        for row in decisions
    )
    new_false = sum(
        row["candidate_result"] == "unsafe_fail_closed_acceptance"
        and row["control_outcome"] != "unsafe_fail_closed_acceptance"
        for row in decisions
    )
    lost_control = sum(
        row["control_outcome"] == "correct_identity"
        and not (
            row["candidate_result"] == "correct_identity"
            and row["prediction"] == row["control_prediction"]
        )
        for row in decisions
    )
    evaluation = policy["evaluation"]
    gates = {
        "minimum_qualified_profiles": pack["qualified_profiles"] >= int(evaluation["minimum_qualified_profiles"]),
        "minimum_preserved_confirmed_v1_additive_gains": preserved_gains
        >= int(evaluation["minimum_preserved_confirmed_v1_additive_gains"]),
        "maximum_unsafe_fail_closed_accepts": unsafe <= int(evaluation["maximum_unsafe_fail_closed_accepts"]),
        "no_new_false_identity": new_false == 0,
        "no_lost_correct_control_identity": lost_control == 0,
        "candidate_pack_frozen_before_truth": pack.get("development_truth_read") is False,
        "production_promotion_disabled": policy["decision"]["production_promotion_allowed"] is False,
    }
    return {
        "schema": EVALUATION_SCHEMA,
        "counts": dict(sorted(counts.items())),
        "qualified_profiles": pack["qualified_profiles"],
        "confirmed_v1_additive_gains": int(policy["scope"]["expected_confirmed_v1_additive_gains"]),
        "preserved_confirmed_v1_additive_gains": preserved_gains,
        "unsafe_fail_closed_accepts": unsafe,
        "new_false_identity_items": new_false,
        "lost_correct_control_identity_items": lost_control,
        "gates": gates,
        "decisions": decisions,
    }


def build_report(policy: dict[str, Any], out: Path, pack: dict[str, Any], core: dict[str, Any]) -> dict[str, Any]:
    all_gates = all(core["gates"].values())
    decision = "HOMOGENEOUS_ENROLLMENT_READY" if all_gates else "KEEP_EXISTING_ENROLLMENT"
    return {
        "schema": REPORT_SCHEMA,
        "decision": decision,
        "replay_verified": True,
        "decision_reason": (
            "joint ECAPA/WavLM enrollment passed frozen development gates"
            if all_gates
            else "homogeneous enrollment did not pass every frozen development gate"
        ),
        "scope": {
            "sessions": len(policy["scope"]["sessions"]),
            "profiles": int(policy["scope"]["expected_profiles"]),
            "development_items": int(policy["scope"]["expected_development_items"]),
        },
        "mining": {
            "qualified_profiles": pack["qualified_profiles"],
            "selected_windows": sum(row["selected_windows"] for row in pack["profiles"] if row["status"] == "qualified"),
            "abstained_profiles": sum(row["status"] != "qualified" for row in pack["profiles"]),
            "models": [policy["ecapa"]["model_id"], policy["wavlm"]["model_id"]],
            "session_local_only": True,
            "target_text_read": False,
            "human_names_read": False,
        },
        "development": {key: value for key, value in core.items() if key not in {"schema", "decisions"}},
        "gates": core["gates"],
        "failed_gates": sorted(key for key, passed in core["gates"].items() if not passed),
        "safety": {
            **policy["safety"],
            "candidate_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
            "coverage_v3_accepts_preserved": int(policy["scope"]["expected_coverage_v3_accepts"]),
            "production_guards_verified": int(policy["scope"]["expected_production_guards"]),
        },
        "next": (
            "open_separate_monotonic_additive_candidate"
            if all_gates
            else "keep_coverage_v3_and_leave_residual_unknown"
        ),
    }


def report_markdown(report: dict[str, Any]) -> str:
    mining = report["mining"]
    development = report["development"]
    lines = [
        "# Session-Local Homogeneous Remote Speaker Enrollment Mining v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        f"- Qualified profiles: `{mining['qualified_profiles']}/{report['scope']['profiles']}`",
        f"- Selected homogeneous windows: `{mining['selected_windows']}`",
        f"- Preserved confirmed gains: `{development['preserved_confirmed_v1_additive_gains']}/{development['confirmed_v1_additive_gains']}`",
        f"- Unsafe fail-closed accepts: `{development['unsafe_fail_closed_accepts']}`",
        f"- New false identities: `{development['new_false_identity_items']}`",
        f"- Lost correct control identities: `{development['lost_correct_control_identity_items']}`",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- `{name}`: `{'pass' if passed else 'fail'}`" for name, passed in report["gates"].items())
    lines.extend(
        (
            "",
            "## Safety",
            "",
            "Candidate mining was session-local and did not read target text, names or cross-session voices.",
            "Coverage v3, selected transcripts, raw CAF, Echo Guard and primary ASR were not modified.",
            f"Next: `{report['next']}`.",
        )
    )
    return "\n".join(lines) + "\n"


def action_evaluate(policy: dict[str, Any], out: Path) -> int:
    pack = verify_frozen_pack(out)
    core = evaluation_core(policy, out, pack)
    write_jsonl(out / "private/development_decisions.jsonl", core.pop("decisions"))
    write_json(out / "private/evaluation_core.json", core)
    report = build_report(policy, out, pack, core)
    write_json(out / "session_local_homogeneous_remote_speaker_enrollment_report.json", report)
    atomic_write(out / "session_local_homogeneous_remote_speaker_enrollment_report.md", report_markdown(report).encode())
    print(f"decision: {report['decision']}")
    return 0


def action_replay(policy: dict[str, Any], out: Path) -> int:
    report_path = out / "session_local_homogeneous_remote_speaker_enrollment_report.json"
    core_path = out / "private/evaluation_core.json"
    decisions_path = out / "private/development_decisions.jsonl"
    if not report_path.is_file() or not core_path.is_file() or not decisions_path.is_file():
        raise MiningError("evaluation artifacts are incomplete")
    pack = verify_frozen_pack(out)
    original = read_json(report_path)
    core = read_json(core_path)
    replayed = build_report(policy, out, pack, core)
    verified = canonical(original) == canonical(replayed)
    replay = {
        "schema": REPLAY_SCHEMA,
        "verified": verified,
        "report_sha256": sha256(report_path),
        "candidate_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
        "evaluation_core_sha256": sha256(core_path),
        "development_decisions_sha256": sha256(decisions_path),
    }
    write_json(out / "replay_report.json", replay)
    if not verified:
        raise MiningError("deterministic replay mismatch")
    print("replay: verified")
    return 0


def action_finalize(policy: dict[str, Any], out: Path) -> int:
    action_replay(policy, out)
    report = read_json(out / "session_local_homogeneous_remote_speaker_enrollment_report.json")
    names = [
        "candidate_pack.public.json",
        "session_local_homogeneous_remote_speaker_enrollment_report.json",
        "session_local_homogeneous_remote_speaker_enrollment_report.md",
        "replay_report.json",
    ]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": report["decision"],
        "artifacts": {name: artifact(out / name) for name in names},
        "private_artifacts_present": True,
        "production_promotion_allowed": False,
    }
    write_json(out / "artifact_manifest.json", manifest)
    print(f"finalized: {report['decision']}")
    return 0


def action_status(out: Path) -> int:
    report_path = out / "session_local_homogeneous_remote_speaker_enrollment_report.json"
    if not report_path.is_file():
        print("status: not_evaluated")
        return 1
    report = read_json(report_path)
    print(f"decision: {report['decision']}")
    print(f"qualified_profiles: {report['mining']['qualified_profiles']}/{report['scope']['profiles']}")
    print(
        "preserved_confirmed_gains: "
        f"{report['development']['preserved_confirmed_v1_additive_gains']}/"
        f"{report['development']['confirmed_v1_additive_gains']}"
    )
    print(f"unsafe_accepts: {report['development']['unsafe_fail_closed_accepts']}")
    print(f"next: {report['next']}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=("preflight", "prepare", "freeze", "evaluate", "replay", "finalize", "status", "all"),
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out = args.out.expanduser().resolve()
    global DEFAULT_POLICY
    DEFAULT_POLICY = policy_path
    policy = load_policy(policy_path)
    actions = {
        "preflight": action_preflight,
        "prepare": action_prepare,
        "freeze": action_freeze,
        "evaluate": action_evaluate,
        "replay": action_replay,
        "finalize": action_finalize,
        "status": lambda _policy, output: action_status(output),
    }
    try:
        if args.action == "all":
            for action in ("prepare", "freeze", "evaluate", "finalize"):
                actions[action](policy, out)
            return 0
        return actions[args.action](policy, out)
    except (MiningError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
