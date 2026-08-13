#!/usr/bin/env python3
"""One-shot qualification of a frozen ERes2NetV2 remote-speaker candidate."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "policies/disjoint-remote-speaker-model-qualification-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/disjoint-remote-speaker-model-qualification-v1"
DEFAULT_MODEL_ROOT = (
    Path.home()
    / ".local/share/murmurmark/models/disjoint-remote-speaker-model-qualification-v1/eres2netv2-common"
)
MODEL_ROOT_ENV = "MURMURMARK_DISJOINT_REMOTE_SPEAKER_MODEL_ROOT"
WORKER = ROOT / "scripts/eres2netv2-speaker-embedding-worker.py"
SETUP = ROOT / "scripts/setup-disjoint-remote-speaker-model-v1.py"
CONTROLLED_SCRIPT = ROOT / "scripts/controlled-remote-speaker-truth-lab-v1.py"
CONTROLLED_POLICY = ROOT / "policies/controlled-remote-speaker-truth-lab-v1.json"
CONTROLLED_PRIVATE = ROOT / "sessions/_reports/controlled-remote-speaker-truth-lab-v1/private"

POLICY_SCHEMA = "murmurmark.disjoint_remote_speaker_model_qualification_policy/v1"
REQUEST_SCHEMA = "murmurmark.eres2netv2_embedding_request/v1"
EMBEDDING_SCHEMA = "murmurmark.eres2netv2_embedding_result/v1"
PACK_SCHEMA = "murmurmark.disjoint_remote_speaker_model_candidate_pack/v1"
FREEZE_SCHEMA = "murmurmark.disjoint_remote_speaker_model_freeze/v1"
CORE_SCHEMA = "murmurmark.disjoint_remote_speaker_model_evaluation/v1"
REPORT_SCHEMA = "murmurmark.disjoint_remote_speaker_model_qualification_report/v1"
REPLAY_SCHEMA = "murmurmark.disjoint_remote_speaker_model_replay/v1"
MANIFEST_SCHEMA = "murmurmark.disjoint_remote_speaker_model_artifact_manifest/v1"


class QualificationError(RuntimeError):
    pass


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def pretty_json(value: Any) -> bytes:
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
    atomic_write(path, pretty_json(value))


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    atomic_write(path, b"".join(canonical_json(row) for row in rows))


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise QualificationError(f"JSON object expected: {path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise QualificationError(f"JSONL object expected: {path}")
                rows.append(value)
    return rows


def resolve(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


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
        raise QualificationError("unsupported qualification policy")
    if set(policy["decision"]["allowed_outcomes"]) != {
        "PROMOTE_SHADOW",
        "KEEP_COVERAGE_V3",
        "MODEL_UNAVAILABLE",
    }:
        raise QualificationError("terminal outcome set changed")
    if policy["decision"]["production_promotion_allowed"] is not False:
        raise QualificationError("production promotion must remain disabled")
    if policy["calibration"]["disjoint_truth_v2_tuning_allowed"] is not False:
        raise QualificationError("Disjoint Truth v2 tuning must remain disabled")
    if policy["calibration"]["post_unseal_tuning_allowed"] is not False:
        raise QualificationError("post-unseal tuning must remain disabled")
    return policy


def source_map(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = {str(row["id"]): row for row in policy["sources"]}
    if len(rows) != len(policy["sources"]):
        raise QualificationError("duplicate frozen source id")
    return rows


def verify_sources(policy: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    verified = []
    for source in policy["sources"]:
        if source["phase"] != phase:
            continue
        path = resolve(source["path"])
        if not path.is_file():
            raise QualificationError(f"frozen source missing: {source['id']}")
        if path.stat().st_size != int(source["bytes"]) or sha256(path) != source["sha256"]:
            raise QualificationError(f"frozen source changed: {source['id']}")
        verified.append({"id": source["id"], **artifact(path)})
    return verified


def verify_fingerprint(expected: dict[str, Any]) -> None:
    path = resolve(expected["path"])
    if not path.is_file() or path.stat().st_size != int(expected["bytes"]) or sha256(path) != expected["sha256"]:
        raise QualificationError(f"frozen artifact changed: {expected['path']}")


def model_root(policy: dict[str, Any]) -> Path:
    configured = os.environ.get(MODEL_ROOT_ENV, policy["candidate"]["default_model_root"])
    return Path(configured).expanduser().resolve()


def verify_model(policy: dict[str, Any]) -> dict[str, Any]:
    root = model_root(policy)
    manifest_path = root / "model_manifest.json"
    if not manifest_path.is_file():
        raise QualificationError(f"candidate model manifest is missing: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != "murmurmark.disjoint_remote_speaker_model_manifest/v1":
        raise QualificationError("candidate model manifest schema changed")
    if manifest.get("candidate_id") != policy["candidate"]["id"] or manifest.get("offline_ready") is not True:
        raise QualificationError("candidate model is not offline-ready")
    for expected in policy["candidate"]["artifacts"]:
        path = root / expected["path"]
        if not path.is_file() or path.stat().st_size != int(expected["bytes"]) or sha256(path) != expected["sha256"]:
            raise QualificationError(f"candidate model artifact changed: {expected['path']}")
    return {
        "manifest": artifact(manifest_path),
        "model": artifact(root / "model-repo/pretrained_eres2netv2.ckpt"),
        "code_root": portable(root / "code-repo"),
        "offline_ready": True,
    }


def load_controlled_module() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_controlled_truth_for_disjoint_model", CONTROLLED_SCRIPT)
    if spec is None or spec.loader is None:
        raise QualificationError("cannot load controlled truth helpers")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CONTROLLED = load_controlled_module()


def normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float64).reshape(-1)
    norm = float(np.linalg.norm(value))
    if not np.isfinite(norm) or norm <= 0:
        raise QualificationError("invalid speaker embedding")
    return value / norm


def load_embeddings(
    path: Path,
) -> tuple[dict[str, np.ndarray], dict[str, dict[str, Any]], list[dict[str, str]]]:
    payload = read_json(path)
    if payload.get("schema") != EMBEDDING_SCHEMA:
        raise QualificationError("unsupported ERes2NetV2 embedding result")
    vectors = {}
    metadata = {}
    for row in payload.get("rows") or []:
        key = str(row["key"])
        vectors[key] = normalize(np.asarray(row["embedding"], dtype=np.float64))
        metadata[key] = row.get("audio") or {}
    errors = list(payload.get("errors") or [])
    return vectors, metadata, errors


def audio_duration(path: Path) -> float:
    info = sf.info(path)
    return float(info.frames) / float(info.samplerate)


def verified_audio(raw: dict[str, Any]) -> Path:
    path = resolve(str(raw["path"]))
    if not path.is_file() or sha256(path) != str(raw["sha256"]):
        raise QualificationError(f"frozen audio changed: {raw['path']}")
    return path


def audio_key(prefix: str, digest: str, start: float | None = None, end: float | None = None) -> str:
    suffix = "full" if start is None or end is None else f"{int(round(start * 1000)):07d}_{int(round(end * 1000)):07d}"
    return f"{prefix}:{digest}:{suffix}"


def add_audio_requests(
    requests: dict[str, dict[str, Any]],
    prefix: str,
    raw: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    path = verified_audio(raw)
    digest = str(raw["sha256"])
    duration = audio_duration(path)
    full_key = audio_key(prefix, digest)
    requests.setdefault(
        full_key,
        {
            "key": full_key,
            "path": portable(path),
            "start": 0.0,
            "end": None,
            "minimum_sec": float(policy["preprocessing"]["minimum_audio_sec"]),
        },
    )
    window_sec = float(policy["preprocessing"]["subwindow_sec"])
    hop_sec = float(policy["preprocessing"]["subwindow_hop_sec"])
    minimum = float(policy["preprocessing"]["minimum_subwindow_sec"])
    window_keys: list[str] = []
    if duration <= window_sec + 1e-6:
        window_keys.append(full_key)
    else:
        starts = []
        position = 0.0
        while position + minimum <= duration + 1e-9:
            starts.append(position)
            position += hop_sec
        final_start = max(0.0, duration - window_sec)
        starts.append(final_start)
        for start in sorted({round(value, 6) for value in starts}):
            end = min(duration, start + window_sec)
            if end - start < minimum:
                continue
            key = audio_key(prefix, digest, start, end)
            requests.setdefault(
                key,
                {
                    "key": key,
                    "path": portable(path),
                    "start": round(start, 6),
                    "end": round(end, 6),
                    "minimum_sec": minimum,
                },
            )
            window_keys.append(key)
    return {
        "sha256": digest,
        "duration_sec": round(duration, 6),
        "full_key": full_key,
        "window_keys": window_keys,
    }


def queue_inventory(
    queue: list[dict[str, Any]],
    prefix: str,
    policy: dict[str, Any],
    requests: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets = []
    exemplars: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in queue:
        target = add_audio_requests(requests, f"{prefix}:target", row["audio"], policy)
        targets.append(
            {
                "slot_id": str(row["slot_id"]),
                "session_alias": str(row["session_alias"]),
                "speaker_choices": list(row["speaker_choices"]),
                **target,
            }
        )
        for exemplar in row["exemplars"]:
            raw = {"path": exemplar["path"], "sha256": exemplar["sha256"]}
            value = add_audio_requests(requests, f"{prefix}:exemplar", raw, policy)
            key = (str(row["session_alias"]), str(exemplar["speaker"]), str(exemplar["sha256"]))
            exemplars[key] = {
                "session_alias": key[0],
                "speaker_id": key[1],
                **value,
            }
    return sorted(targets, key=lambda row: row["slot_id"]), sorted(
        exemplars.values(), key=lambda row: (row["session_alias"], row["speaker_id"], row["sha256"])
    )


def controlled_inventory(
    policy: dict[str, Any], requests: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    controlled_policy = CONTROLLED.load_policy(CONTROLLED_POLICY)
    scenarios = CONTROLLED.scenario_paths(CONTROLLED_PRIVATE, controlled_policy)
    enrollment, words = CONTROLLED.build_requests(scenarios)
    rows = []
    minimum = float(policy["preprocessing"]["minimum_audio_sec"])
    for request in enrollment + words:
        key = f"controlled:{request.key}"
        rows.append(
            {
                "key": key,
                "path": portable(request.path),
                "start": round(float(request.start), 6),
                "end": round(float(request.end), 6),
                "minimum_sec": minimum,
            }
        )
    for row in rows:
        requests.setdefault(row["key"], row)
    return controlled_policy, scenarios


def run_worker(policy: dict[str, Any], request: Path, output: Path) -> None:
    root = model_root(policy)
    environment = os.environ.copy()
    environment["HF_HUB_OFFLINE"] = "1"
    environment["TRANSFORMERS_OFFLINE"] = "1"
    command = [
        "nice",
        "-n",
        str(policy["runtime"]["nice"]),
        str(ROOT / ".venv/bin/python"),
        str(WORKER),
        "--request",
        str(request),
        "--output",
        str(output),
        "--model",
        str(root / "model-repo/pretrained_eres2netv2.ckpt"),
        "--code-root",
        str(root / "code-repo"),
        "--threads",
        str(policy["runtime"]["threads"]),
    ]
    result = subprocess.run(command, cwd=ROOT, env=environment, check=False)
    if result.returncode != 0:
        raise QualificationError("ERes2NetV2 embedding worker failed")


def controlled_calibration(
    policy: dict[str, Any],
    controlled_policy: dict[str, Any],
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    controlled_embeddings = {
        key.removeprefix("controlled:"): value
        for key, value in embeddings.items()
        if key.startswith("controlled:")
    }
    centroids, enrollment = CONTROLLED.build_centroids(
        scenarios, controlled_embeddings, "word_and_event"
    )
    calibration_policy = json.loads(json.dumps(controlled_policy))
    calibration_policy["analysis"]["dev_similarity_grid"] = policy["calibration"]["similarity_grid"]
    calibration_policy["analysis"]["dev_margin_grid"] = policy["calibration"]["margin_grid"]
    calibration_policy["gates"]["minimum_held_out_pairwise_precision"] = policy["calibration"][
        "minimum_dev_pairwise_precision"
    ]
    trials = []
    for similarity in calibration_policy["analysis"]["dev_similarity_grid"]:
        for margin in calibration_policy["analysis"]["dev_margin_grid"]:
            candidate = predict_controlled_words(
                scenarios,
                controlled_embeddings,
                centroids,
                float(similarity),
                float(margin),
                "eres2netv2_disjoint_model_dev_trial",
            )
            trials.append(
                {
                    "minimum_similarity": float(similarity),
                    "minimum_margin": float(margin),
                    "metrics": CONTROLLED.evaluate_predictions(scenarios, candidate, "dev"),
                }
            )
    eligible = [
        row
        for row in trials
        if row["metrics"]["open_set_false_attributions"] == 0
        and row["metrics"]["pairwise"]["precision"]
        >= float(policy["calibration"]["minimum_dev_pairwise_precision"])
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
    thresholds = {
        "minimum_similarity": selected["minimum_similarity"],
        "minimum_margin": selected["minimum_margin"],
        "eligible_zero_open_set_trial": bool(eligible),
    }
    if not thresholds.get("eligible_zero_open_set_trial"):
        raise QualificationError("controlled dev cannot calibrate a zero-open-set threshold")
    predictions = predict_controlled_words(
        scenarios,
        controlled_embeddings,
        centroids,
        float(thresholds["minimum_similarity"]),
        float(thresholds["minimum_margin"]),
        "eres2netv2_disjoint_model_v1",
    )
    dev = CONTROLLED.evaluate_predictions(scenarios, predictions, "dev")
    summary = {
        "source": policy["calibration"]["source"],
        "thresholds": thresholds,
        "trials": len(trials),
        "dev": dev,
        "hard_used_for_tuning": False,
        "truth_v1_used_for_tuning": False,
        "disjoint_truth_v2_used_for_tuning": False,
        "enrollment": enrollment,
    }
    return summary, predictions, centroids


def predict_controlled_words(
    scenarios: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    centroids: dict[str, np.ndarray],
    similarity: float,
    margin: float,
    track: str,
) -> list[dict[str, Any]]:
    """Predict scripted words while treating silent/unreadable slices as abstentions."""
    rows = []
    for scenario in scenarios:
        for word in scenario["words"]:
            key = f"word:{word['word_id']}"
            if word["overlap_word_ids"]:
                result = {
                    "speaker_id": "mixed",
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "reason": "timestamp_overlap",
                }
            elif key not in embeddings:
                result = {
                    "speaker_id": "unknown_speaker",
                    "top_speaker_id": None,
                    "similarity": None,
                    "margin": None,
                    "reason": "embedding_unavailable",
                }
            else:
                raw = CONTROLLED.classify(embeddings[key], centroids, similarity, margin)
                result = {
                    **raw,
                    "speaker_id": raw["speaker_id"] or "unknown_speaker",
                    "reason": "accepted_centroid" if raw["speaker_id"] else "open_set_abstention",
                }
            rows.append(
                {
                    "schema": CONTROLLED.PREDICTION_SCHEMA,
                    "track": track,
                    "word_id": word["word_id"],
                    "scenario_id": scenario["scenario_id"],
                    "split": scenario["split"],
                    **result,
                }
            )
    return rows


def centroids_from_exemplars(
    exemplars: list[dict[str, Any]], embeddings: dict[str, np.ndarray]
) -> dict[str, dict[str, np.ndarray]]:
    grouped: dict[tuple[str, str], list[np.ndarray]] = defaultdict(list)
    for row in exemplars:
        key = str(row["full_key"])
        if key not in embeddings:
            continue
        grouped[(str(row["session_alias"]), str(row["speaker_id"]))].append(embeddings[key])
    result: dict[str, dict[str, np.ndarray]] = defaultdict(dict)
    for (session_alias, speaker), values in grouped.items():
        result[session_alias][speaker] = normalize(np.mean(values, axis=0))
    return dict(result)


def score(vector: np.ndarray, centroids: dict[str, np.ndarray]) -> dict[str, Any]:
    values = sorted(
        ((float(vector @ centroid), speaker) for speaker, centroid in centroids.items()),
        reverse=True,
    )
    if not values:
        return {"speaker_id": None, "similarity": None, "margin": None, "scores": []}
    top_similarity, top_speaker = values[0]
    margin = top_similarity - values[1][0] if len(values) > 1 else top_similarity
    return {
        "speaker_id": top_speaker,
        "similarity": round(top_similarity, 6),
        "margin": round(margin, 6),
        "scores": [
            {"speaker_id": speaker, "similarity": round(similarity, 6)}
            for similarity, speaker in values
        ],
    }


def classify_target(
    row: dict[str, Any],
    embeddings: dict[str, np.ndarray],
    centroids: dict[str, np.ndarray],
    thresholds: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    similarity_floor = float(thresholds["minimum_similarity"])
    margin_floor = float(thresholds["minimum_margin"])

    def classify_key(key: str) -> dict[str, Any]:
        if key not in embeddings:
            return {"speaker_id": None, "accepted": False, "reason": "embedding_unavailable"}
        value = score(embeddings[key], centroids)
        accepted = (
            value["speaker_id"] is not None
            and float(value["similarity"]) >= similarity_floor
            and float(value["margin"]) >= margin_floor
        )
        return {
            **value,
            "accepted": accepted,
            "reason": "accepted" if accepted else "threshold_or_margin",
        }

    full = classify_key(str(row["full_key"]))
    windows = [classify_key(str(key)) for key in row["window_keys"]]
    accepted = [value for value in windows if value.get("accepted")]
    accepted_speakers = Counter(str(value["speaker_id"]) for value in accepted)
    if len(accepted_speakers) > 1:
        prediction = "mixed"
        reason = "conflicting_subwindow_speakers"
    elif not full.get("accepted"):
        prediction = "unknown_speaker"
        reason = "full_clip_not_accepted"
    elif not accepted:
        prediction = "unknown_speaker"
        reason = "no_confident_subwindow"
    else:
        speaker, votes = accepted_speakers.most_common(1)[0]
        consensus = votes / len(accepted)
        confident_ratio = len(accepted) / max(1, len(windows))
        if speaker != full.get("speaker_id"):
            prediction = "mixed"
            reason = "full_subwindow_conflict"
        elif consensus < float(policy["classification"]["minimum_subwindow_consensus"]):
            prediction = "unknown_speaker"
            reason = "subwindow_consensus_below_floor"
        elif confident_ratio < float(policy["classification"]["minimum_confident_subwindow_ratio"]):
            prediction = "unknown_speaker"
            reason = "confident_subwindow_ratio_below_floor"
        else:
            prediction = speaker
            reason = "accepted_full_and_subwindow_consensus"
    return {
        "prediction": prediction,
        "reason": reason,
        "full": full,
        "subwindows": {
            "count": len(windows),
            "accepted": len(accepted),
            "speaker_votes": dict(sorted(accepted_speakers.items())),
        },
    }


def classify_queue(
    targets: list[dict[str, Any]],
    exemplars: list[dict[str, Any]],
    embeddings: dict[str, np.ndarray],
    thresholds: dict[str, Any],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    centers = centroids_from_exemplars(exemplars, embeddings)
    rows = []
    for target in targets:
        session_alias = str(target["session_alias"])
        if session_alias not in centers:
            result = {
                "prediction": "unknown_speaker",
                "reason": "session_exemplars_unavailable",
                "full": {},
                "subwindows": {"count": 0, "accepted": 0, "speaker_votes": {}},
            }
        else:
            result = classify_target(target, embeddings, centers[session_alias], thresholds, policy)
        if result["prediction"] not in target["speaker_choices"]:
            result = {
                **result,
                "prediction": "unknown_speaker",
                "reason": "prediction_outside_frozen_choices",
            }
        rows.append(
            {
                "slot_id": target["slot_id"],
                "session_alias": session_alias,
                "audio_sha256": target["sha256"],
                "duration_sec": target["duration_sec"],
                **result,
            }
        )
    return rows


def forbidden_candidate_keys(value: Any, prefix: str = "") -> list[str]:
    forbidden = {
        "truth",
        "truth_outcome",
        "answer",
        "answers",
        "human_name",
        "speaker_name",
        "transcript_fragment",
        "speech_text",
        "item_id",
    }
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if key in forbidden:
                found.append(path)
            found.extend(forbidden_candidate_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(forbidden_candidate_keys(child, f"{prefix}[{index}]"))
    return found


def preflight(policy: dict[str, Any]) -> dict[str, Any]:
    sources = verify_sources(policy, "prepare")
    model = verify_model(policy)
    truth_report = read_json(resolve(source_map(policy)["truth_v2_public_report"]["path"]))
    if truth_report.get("decision") != "DIRECT_TRUTH_V2_READY":
        raise QualificationError("Disjoint Truth v2 is not ready")
    candidate_pack = read_json(resolve(source_map(policy)["truth_v2_candidate_pack"]["path"]))
    review_pack = read_json(resolve(source_map(policy)["truth_v2_review_pack"]["path"]))
    if candidate_pack.get("prior_truth_read") is not False:
        raise QualificationError("Disjoint Truth v2 candidate boundary changed")
    if candidate_pack.get("inherited_production_guards") != int(policy["scope"]["production_guards"]):
        raise QualificationError("production guard count changed")
    for expected in candidate_pack.get("frozen_artifacts", {}).values():
        verify_fingerprint(expected)
    for expected in review_pack.get("frozen_artifacts", {}).values():
        verify_fingerprint(expected)
    return {
        "schema": "murmurmark.disjoint_remote_speaker_model_preflight/v1",
        "candidate": policy["candidate"]["id"],
        "model": model,
        "prepare_sources": sources,
        "truth_v2_decision": truth_report["decision"],
        "truth_v2_primary_items": truth_report["scope"]["primary_items"],
        "truth_v2_repeat_items": truth_report["scope"]["repeat_items"],
        "production_guards": candidate_pack["inherited_production_guards"],
        "item_truth_read": False,
        "offline_ready": True,
    }


def clean_prepare(out: Path) -> None:
    if out.joinpath("unseal_marker.json").exists():
        raise QualificationError("terminal evaluation already unsealed; candidate rebuild is forbidden")
    if out.exists():
        if out.name != "disjoint-remote-speaker-model-qualification-v1":
            raise QualificationError(f"refusing to clean unexpected output directory: {out}")
        shutil.rmtree(out)
    out.joinpath("private").mkdir(parents=True, exist_ok=True)


def action_preflight(policy: dict[str, Any], out: Path) -> int:
    value = preflight(policy)
    write_json(out / "private/preflight.json", value)
    print(f"preflight: ok ({value['candidate']}, offline, truth v2 sealed)")
    return 0


def action_prepare(policy: dict[str, Any], out: Path) -> int:
    clean_prepare(out)
    state = preflight(policy)
    sources = source_map(policy)
    v2_queue = read_jsonl(resolve(sources["truth_v2_queue"]["path"]))
    v1_queue = read_jsonl(resolve(sources["truth_v1_queue"]["path"]))
    requests: dict[str, dict[str, Any]] = {}
    v2_targets, v2_exemplars = queue_inventory(v2_queue, "v2", policy, requests)
    v1_targets, v1_exemplars = queue_inventory(v1_queue, "v1", policy, requests)
    controlled_policy, scenarios = controlled_inventory(policy, requests)
    request = {
        "schema": REQUEST_SCHEMA,
        "model_id": policy["candidate"]["model_id"],
        "model_revision": policy["candidate"]["model_revision"],
        "source_revision": policy["candidate"]["source_revision"],
        "allow_errors": True,
        "requests": [requests[key] for key in sorted(requests)],
    }
    request_path = out / "private/embedding_request.json"
    embedding_path = out / "private/embeddings.json"
    replay_embedding_path = out / "private/embeddings.replay.json"
    write_json(request_path, request)
    run_worker(policy, request_path, embedding_path)
    run_worker(policy, request_path, replay_embedding_path)
    if embedding_path.read_bytes() != replay_embedding_path.read_bytes():
        raise QualificationError("ERes2NetV2 embedding replay is not byte-exact")
    embeddings, _, embedding_errors = load_embeddings(embedding_path)
    calibration, controlled_predictions, _ = controlled_calibration(
        policy, controlled_policy, scenarios, embeddings
    )
    thresholds = calibration["thresholds"]
    v2_predictions = classify_queue(v2_targets, v2_exemplars, embeddings, thresholds, policy)
    v1_predictions = classify_queue(v1_targets, v1_exemplars, embeddings, thresholds, policy)
    write_jsonl(out / "private/truth_v2_candidate_predictions.jsonl", v2_predictions)
    write_jsonl(out / "private/truth_v1_candidate_predictions.jsonl", v1_predictions)
    write_jsonl(out / "private/controlled_candidate_predictions.jsonl", controlled_predictions)
    pack = {
        "schema": PACK_SCHEMA,
        "state": "prepared_before_disjoint_truth_v2_unseal",
        "candidate": {
            "id": policy["candidate"]["id"],
            "model_id": policy["candidate"]["model_id"],
            "model_revision": policy["candidate"]["model_revision"],
            "source_revision": policy["candidate"]["source_revision"],
            "architecture": policy["candidate"]["architecture"],
        },
        "calibration": calibration,
        "classification": policy["classification"],
        "counts": {
            "embedding_requests": len(requests),
            "truth_v2_slots": len(v2_predictions),
            "truth_v1_slots": len(v1_predictions),
            "controlled_predictions": len(controlled_predictions),
            "truth_v2_exemplars": len(v2_exemplars),
            "truth_v1_exemplars": len(v1_exemplars),
            "embedding_errors": len(embedding_errors),
        },
        "candidate_outputs": {
            "truth_v2": artifact(out / "private/truth_v2_candidate_predictions.jsonl"),
            "truth_v1": artifact(out / "private/truth_v1_candidate_predictions.jsonl"),
            "controlled": artifact(out / "private/controlled_candidate_predictions.jsonl"),
        },
        "embedding_replay": {
            "byte_exact": True,
            "sha256": sha256(embedding_path),
            "requests": len(requests),
        },
        "disjoint_truth_v2_item_labels_read": False,
        "truth_v1_labels_used_for_tuning": False,
        "controlled_hard_used_for_tuning": False,
        "thresholds_tuned_after_unseal": False,
        "production_promotion_allowed": False,
    }
    forbidden = forbidden_candidate_keys(pack)
    if forbidden:
        raise QualificationError("candidate pack leaked truth keys: " + ",".join(forbidden[:8]))
    write_json(out / "private/preflight.json", state)
    write_json(out / "private/input_manifest.json", state)
    write_json(out / "private/candidate_pack.pending.json", pack)
    write_json(
        out / "candidate_pack.public.json",
        {
            "schema": PACK_SCHEMA,
            "state": pack["state"],
            "candidate": pack["candidate"],
            "calibration": pack["calibration"],
            "classification": pack["classification"],
            "counts": pack["counts"],
            "embedding_replay": pack["embedding_replay"],
            "disjoint_truth_v2_item_labels_read": False,
            "production_promotion_allowed": False,
        },
    )
    print(
        "prepared: "
        f"{len(v2_predictions)} truth-v2 slots, {len(v1_predictions)} truth-v1 controls, "
        f"threshold={thresholds['minimum_similarity']}/{thresholds['minimum_margin']}"
    )
    return 0


def action_freeze(policy: dict[str, Any], out: Path) -> int:
    if out.joinpath("unseal_marker.json").exists():
        raise QualificationError("terminal evaluation already unsealed")
    pending = out / "private/candidate_pack.pending.json"
    if not pending.is_file():
        raise QualificationError("prepare must run before freeze")
    pack = read_json(pending)
    if pack.get("schema") != PACK_SCHEMA or pack.get("disjoint_truth_v2_item_labels_read") is not False:
        raise QualificationError("pending candidate pack is invalid")
    if forbidden_candidate_keys(pack):
        raise QualificationError("pending candidate pack contains truth leakage")
    frozen = out / "private/candidate_pack.frozen.json"
    atomic_write(frozen, pending.read_bytes())
    paths = [
        out / "private/preflight.json",
        out / "private/input_manifest.json",
        out / "private/embedding_request.json",
        out / "private/embeddings.json",
        out / "private/embeddings.replay.json",
        out / "private/truth_v2_candidate_predictions.jsonl",
        out / "private/truth_v1_candidate_predictions.jsonl",
        out / "private/controlled_candidate_predictions.jsonl",
        frozen,
        out / "candidate_pack.public.json",
        DEFAULT_POLICY,
        WORKER,
        Path(__file__).resolve(),
    ]
    if any(not path.is_file() for path in paths):
        missing = [str(path) for path in paths if not path.is_file()]
        raise QualificationError("freeze artifact missing: " + ",".join(missing))
    root = model_root(policy)
    model_artifacts = [artifact(root / row["path"]) for row in policy["candidate"]["artifacts"]]
    manifest = {
        "schema": FREEZE_SCHEMA,
        "state": "frozen_before_disjoint_truth_v2_unseal",
        "candidate_pack_sha256": sha256(frozen),
        "artifacts": [artifact(path) for path in paths] + model_artifacts,
        "prepare_sources": verify_sources(policy, "prepare"),
        "disjoint_truth_v2_item_labels_read": False,
        "truth_v1_labels_used_for_tuning": False,
        "controlled_hard_used_for_tuning": False,
        "thresholds_tuned_after_unseal": False,
        "production_promotion_allowed": False,
    }
    write_json(out / "freeze_manifest.json", manifest)
    print(f"frozen: {manifest['candidate_pack_sha256']}")
    return 0


def verify_freeze(out: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = out / "freeze_manifest.json"
    frozen = out / "private/candidate_pack.frozen.json"
    if not manifest_path.is_file() or not frozen.is_file():
        raise QualificationError("candidate freeze is unavailable")
    manifest = read_json(manifest_path)
    if manifest.get("schema") != FREEZE_SCHEMA or manifest.get("disjoint_truth_v2_item_labels_read") is not False:
        raise QualificationError("candidate freeze manifest is invalid")
    for expected in manifest.get("artifacts") or []:
        verify_fingerprint(expected)
    if sha256(frozen) != manifest.get("candidate_pack_sha256"):
        raise QualificationError("frozen candidate pack changed")
    pack = read_json(frozen)
    if forbidden_candidate_keys(pack):
        raise QualificationError("frozen candidate pack contains truth leakage")
    return pack, manifest


def is_identity(value: str | None) -> bool:
    return str(value or "").startswith("remote_speaker_")


def prediction_result(truth: str, prediction: str) -> str:
    if is_identity(prediction):
        if is_identity(truth):
            return "correct_identity" if prediction == truth else "false_identity"
        return "unsafe_special_accept"
    return "abstained_identity" if is_identity(truth) else "safe_abstention"


def bcubed(truth: list[str], predicted: list[str]) -> dict[str, float]:
    truth_members: dict[str, set[int]] = defaultdict(set)
    predicted_members: dict[str, set[int]] = defaultdict(set)
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        truth_members[expected].add(index)
        predicted_members[actual].add(index)
    precision_rows = []
    recall_rows = []
    for index, (expected, actual) in enumerate(zip(truth, predicted)):
        overlap = len(truth_members[expected] & predicted_members[actual])
        precision_rows.append(overlap / len(predicted_members[actual]))
        recall_rows.append(overlap / len(truth_members[expected]))
    precision = float(np.mean(precision_rows)) if precision_rows else 0.0
    recall = float(np.mean(recall_rows)) if recall_rows else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["result"] for row in rows)
    positive = [row for row in rows if is_identity(row["truth_outcome"])]
    attributed = [row for row in rows if is_identity(row["prediction"])]
    correct = counts["correct_identity"]
    positive_seconds = sum(float(row["coverage_weight_sec"]) for row in positive)
    attributed_seconds = sum(float(row["coverage_weight_sec"]) for row in attributed)
    correct_seconds = sum(
        float(row["coverage_weight_sec"]) for row in rows if row["result"] == "correct_identity"
    )
    special = [row for row in rows if not is_identity(row["truth_outcome"])]
    special_by_kind = {}
    for kind in ("unknown_speaker", "mixed", "unusable"):
        selected = [row for row in special if row["truth_outcome"] == kind]
        safe = sum(not is_identity(row["prediction"]) for row in selected)
        special_by_kind[kind] = {
            "items": len(selected),
            "safe_abstentions": safe,
            "abstention_rate": round(safe / len(selected), 6) if selected else None,
        }
    truth_labels = [f"{row['session_alias']}:{row['truth_outcome']}" for row in positive]
    predicted_labels = [
        f"{row['session_alias']}:{row['prediction']}"
        if is_identity(row["prediction"])
        else f"unknown:{row['item_id']}"
        for row in positive
    ]
    return {
        "items": len(rows),
        "positive_items": len(positive),
        "special_items": len(special),
        "counts": dict(sorted(counts.items())),
        "attributed_items": len(attributed),
        "correct_identity_items": correct,
        "attributed_precision": round(correct / len(attributed), 6) if attributed else 1.0,
        "attributed_recall": round(correct / len(positive), 6) if positive else 0.0,
        "attributed_seconds": round(attributed_seconds, 6),
        "correct_identity_seconds": round(correct_seconds, 6),
        "positive_seconds": round(positive_seconds, 6),
        "seconds_precision": round(correct_seconds / attributed_seconds, 6) if attributed_seconds else 1.0,
        "seconds_recall": round(correct_seconds / positive_seconds, 6) if positive_seconds else 0.0,
        "special_abstention": special_by_kind,
        "bcubed": bcubed(truth_labels, predicted_labels),
    }


def evaluate_truth_v2(
    policy: dict[str, Any], out: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sources = source_map(policy)
    predictions = {
        row["slot_id"]: row
        for row in read_jsonl(out / "private/truth_v2_candidate_predictions.jsonl")
    }
    slot_map = read_jsonl(resolve(sources["truth_v2_slot_map"]["path"]))
    answers = {row["slot_id"]: row for row in read_jsonl(resolve(sources["truth_v2_answers"]["path"]))}
    selection = {
        row["item_id"]: row for row in read_jsonl(resolve(sources["truth_v2_selection"]["path"]))
    }
    if len(predictions) != int(policy["scope"]["truth_v2_primary_items"]) + int(policy["scope"]["truth_v2_repeat_items"]):
        raise QualificationError("truth v2 candidate slot count changed")
    primary_rows = []
    repeat_pairs: dict[str, dict[str, str]] = defaultdict(dict)
    for slot in slot_map:
        slot_id = str(slot["slot_id"])
        if slot_id not in predictions or slot_id not in answers:
            raise QualificationError(f"truth v2 slot evidence missing: {slot_id}")
        truth = str(answers[slot_id].get("outcome") or "")
        prediction = str(predictions[slot_id]["prediction"])
        repeat_pairs[str(slot["item_id"])][str(slot["kind"])] = prediction
        if slot["kind"] != "primary":
            continue
        selected = selection[str(slot["item_id"])]
        primary_rows.append(
            {
                "item_id": str(slot["item_id"]),
                "session_alias": str(slot["session_alias"]),
                "truth_outcome": truth,
                "prediction": prediction,
                "result": prediction_result(truth, prediction),
                "reason": predictions[slot_id]["reason"],
                "word_count": int(selected["word_count"]),
                "coverage_weight_sec": float(selected["coverage_weight_sec"]),
                "tags": list(selected["tags"]),
            }
        )
    primary_rows.sort(key=lambda row: row["item_id"])
    aggregate = aggregate_rows(primary_rows)
    compared = [row for row in repeat_pairs.values() if set(row) == {"primary", "repeat"}]
    repeat_matches = sum(row["primary"] == row["repeat"] for row in compared)
    repeat_determinism = round(repeat_matches / len(compared), 6) if compared else 0.0
    counts_by_session: dict[str, dict[str, set[str]]] = defaultdict(
        lambda: {"truth": set(), "candidate": set()}
    )
    for row in primary_rows:
        if is_identity(row["truth_outcome"]):
            counts_by_session[row["session_alias"]]["truth"].add(row["truth_outcome"])
        if is_identity(row["prediction"]):
            counts_by_session[row["session_alias"]]["candidate"].add(row["prediction"])
    count_errors = [
        abs(len(value["truth"]) - len(value["candidate"])) for value in counts_by_session.values()
    ]
    boundary_rows = [
        row
        for row in primary_rows
        if {"utterance_boundary", "temporal_boundary_uncertain"} & set(row["tags"])
    ]
    aggregate["repeat"] = {
        "compared": len(compared),
        "matches": repeat_matches,
        "determinism": repeat_determinism,
    }
    aggregate["speaker_count"] = {
        "sessions": len(counts_by_session),
        "exact_sessions": sum(
            len(value["truth"]) == len(value["candidate"]) for value in counts_by_session.values()
        ),
        "mean_absolute_error": round(float(np.mean(count_errors)), 6) if count_errors else 0.0,
    }
    aggregate["boundary_cohort"] = aggregate_rows(boundary_rows)
    return aggregate, primary_rows


def evaluate_truth_v1(
    policy: dict[str, Any], out: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sources = source_map(policy)
    predictions = {
        row["slot_id"]: row
        for row in read_jsonl(out / "private/truth_v1_candidate_predictions.jsonl")
    }
    slot_map = read_jsonl(resolve(sources["truth_v1_slot_map"]["path"]))
    answers = {row["slot_id"]: row for row in read_jsonl(resolve(sources["truth_v1_answers"]["path"]))}
    control = {
        row["item_id"]: row for row in read_jsonl(resolve(sources["truth_v1_control"]["path"]))
    }
    rows = []
    for slot in slot_map:
        if slot["kind"] != "primary":
            continue
        slot_id = str(slot["slot_id"])
        truth = str(answers[slot_id].get("outcome") or "")
        prediction = str(predictions[slot_id]["prediction"])
        item_id = str(slot["item_id"])
        previous = control[item_id]
        rows.append(
            {
                "item_id": item_id,
                "session_alias": str(slot["session_alias"]),
                "truth_outcome": truth,
                "prediction": prediction,
                "result": prediction_result(truth, prediction),
                "control_prediction": previous.get("control_prediction"),
                "control_outcome": previous.get("control_outcome"),
                "coverage_weight_sec": float(previous["coverage_weight_sec"]),
                "tags": [],
            }
        )
    rows.sort(key=lambda row: row["item_id"])
    aggregate = aggregate_rows(rows)
    aggregate["lost_correct_control_identity_items"] = sum(
        row["control_outcome"] == "correct_identity" and row["result"] != "correct_identity"
        for row in rows
    )
    aggregate["new_false_identity_items"] = sum(
        row["result"] == "false_identity" and row["control_outcome"] != "false_identity"
        for row in rows
    )
    aggregate["control_unsafe_special_accepts"] = sum(
        row["control_outcome"] == "unsafe_fail_closed_acceptance" for row in rows
    )
    aggregate["candidate_unsafe_special_accepts"] = sum(
        row["result"] == "unsafe_special_accept" for row in rows
    )
    return aggregate, rows


def evaluate_controlled(policy: dict[str, Any], out: Path) -> dict[str, Any]:
    controlled_policy = CONTROLLED.load_policy(CONTROLLED_POLICY)
    scenarios = CONTROLLED.scenario_paths(CONTROLLED_PRIVATE, controlled_policy)
    predictions = read_jsonl(out / "private/controlled_candidate_predictions.jsonl")
    candidate = CONTROLLED.evaluate_predictions(scenarios, predictions, "hard")
    report = read_json(resolve(source_map(policy)["controlled_report"]["path"]))
    control = report["evaluation"]["coverage_v3_topology"]["hard"]
    gates = {
        "bcubed_f1": candidate["bcubed"]["f1"] >= control["bcubed"]["f1"],
        "pairwise_precision": candidate["pairwise"]["precision"] >= control["pairwise"]["precision"],
        "boundary_recall": candidate["boundary_recall"] >= control["boundary_recall"],
        "open_set_false_attributions": candidate["open_set_false_attributions"]
        <= control["open_set_false_attributions"],
        "known_attributed_precision": (candidate["known_attributed_precision"] or 0.0)
        >= (control["known_attributed_precision"] or 0.0),
        "word_conservation": candidate["word_conservation"] is True,
    }
    return {"candidate": candidate, "coverage_v3_control": control, "gates": gates}


def build_core(policy: dict[str, Any], out: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pack, freeze = verify_freeze(out)
    truth_v2, rows_v2 = evaluate_truth_v2(policy, out)
    truth_v1, rows_v1 = evaluate_truth_v1(policy, out)
    controlled = evaluate_controlled(policy, out)
    evaluation = policy["evaluation"]
    special_unsafe = truth_v2["counts"].get("unsafe_special_accept", 0)
    false_identity = truth_v2["counts"].get("false_identity", 0)
    gates = {
        "minimum_new_correct_identity_items": truth_v2["correct_identity_items"]
        >= int(evaluation["minimum_new_correct_identity_items"]),
        "minimum_attributed_recall": truth_v2["attributed_recall"]
        >= float(evaluation["minimum_attributed_recall"]),
        "minimum_attributed_precision": truth_v2["attributed_precision"]
        >= float(evaluation["minimum_attributed_precision"]),
        "maximum_false_identity_items": false_identity
        <= int(evaluation["maximum_false_identity_items"]),
        "maximum_special_unsafe_accepts": special_unsafe
        <= int(evaluation["maximum_special_unsafe_accepts"]),
        "minimum_repeat_determinism": truth_v2["repeat"]["determinism"]
        >= float(evaluation["minimum_repeat_determinism"]),
        "no_lost_truth_v1_control_identity": truth_v1["lost_correct_control_identity_items"] == 0,
        "no_new_truth_v1_false_identity": truth_v1["new_false_identity_items"] == 0,
        "truth_v1_special_no_regression": truth_v1["candidate_unsafe_special_accepts"]
        <= truth_v1["control_unsafe_special_accepts"],
        "controlled_hard_no_regression": all(controlled["gates"].values()),
    }
    invariants = {
        "candidate_frozen_before_unseal": freeze["disjoint_truth_v2_item_labels_read"] is False,
        "candidate_pack_truth_free": pack["disjoint_truth_v2_item_labels_read"] is False,
        "embedding_replay_byte_exact": pack["embedding_replay"]["byte_exact"] is True,
        "truth_v1_not_used_for_tuning": pack["truth_v1_labels_used_for_tuning"] is False,
        "controlled_hard_not_used_for_tuning": pack["controlled_hard_used_for_tuning"] is False,
        "post_unseal_tuning_disabled": policy["calibration"]["post_unseal_tuning_allowed"] is False,
        "production_promotion_disabled": policy["decision"]["production_promotion_allowed"] is False,
        "truth_v2_primary_conservation": truth_v2["items"] == int(policy["scope"]["truth_v2_primary_items"]),
        "truth_v2_repeat_conservation": truth_v2["repeat"]["compared"]
        == int(policy["scope"]["truth_v2_repeat_items"]),
        "truth_v1_primary_conservation": truth_v1["items"] == int(policy["scope"]["truth_v1_primary_items"]),
        "production_guards_preserved": int(policy["scope"]["production_guards"]) == 355,
    }
    decision = "PROMOTE_SHADOW" if all(gates.values()) and all(invariants.values()) else "KEEP_COVERAGE_V3"
    core = {
        "schema": CORE_SCHEMA,
        "generator": {
            "name": "evaluate-disjoint-remote-speaker-model-v1",
            "version": "1.0.0",
            "mode": "deterministic_offline_one_shot",
        },
        "decision": decision,
        "candidate": pack["candidate"],
        "calibration": pack["calibration"],
        "truth_v2": truth_v2,
        "truth_v1_control": truth_v1,
        "controlled_corpus": controlled,
        "gates": gates,
        "invariants": invariants,
        "failed_gates": sorted(name for name, passed in gates.items() if not passed),
        "failed_invariants": sorted(name for name, passed in invariants.items() if not passed),
        "safety": {
            **policy["safety"],
            "candidate_pack_sha256": freeze["candidate_pack_sha256"],
            "production_profile": "remote_speaker_coverage_v3",
            "candidate_shadow_only": decision == "PROMOTE_SHADOW",
            "production_promoted": False,
            "thresholds_tuned_after_unseal": False,
        },
    }
    private_rows = [
        {"scope": "truth_v2", **row} for row in rows_v2
    ] + [{"scope": "truth_v1", **row} for row in rows_v1]
    return core, private_rows


def public_report(core: dict[str, Any], replay_verified: bool) -> dict[str, Any]:
    truth_v2 = core["truth_v2"]
    truth_v1 = core["truth_v1_control"]
    controlled = core["controlled_corpus"]
    return {
        "schema": REPORT_SCHEMA,
        "decision": core["decision"],
        "decision_reason": (
            "the materially new candidate passed every frozen real-session and controlled safety gate"
            if core["decision"] == "PROMOTE_SHADOW"
            else "the materially new candidate did not safely and materially beat Coverage v3"
        ),
        "candidate": core["candidate"],
        "calibration": {
            "source": core["calibration"]["source"],
            "thresholds": core["calibration"]["thresholds"],
            "hard_used_for_tuning": False,
            "truth_v1_used_for_tuning": False,
            "disjoint_truth_v2_used_for_tuning": False,
        },
        "scope": {
            "truth_v2_primary_items": truth_v2["items"],
            "truth_v2_repeat_items": truth_v2["repeat"]["compared"],
            "truth_v2_positive_items": truth_v2["positive_items"],
            "truth_v2_special_items": truth_v2["special_items"],
            "truth_v1_primary_items": truth_v1["items"],
            "controlled_hard_words": controlled["candidate"]["word_count"],
        },
        "truth_v2": truth_v2,
        "truth_v1_control": truth_v1,
        "controlled_corpus": controlled,
        "gates": core["gates"],
        "invariants": core["invariants"],
        "failed_gates": core["failed_gates"],
        "failed_invariants": core["failed_invariants"],
        "replay_verified": replay_verified,
        "safety": core["safety"],
        "next": (
            "open_isolated_eres2netv2_shadow_without_auto_selection"
            if core["decision"] == "PROMOTE_SHADOW"
            else "keep_coverage_v3_and_close_eres2netv2_route"
        ),
    }


def report_markdown(report: dict[str, Any]) -> str:
    truth = report["truth_v2"]
    controlled = report["controlled_corpus"]
    return "\n".join(
        [
            "# Disjoint Remote Speaker Model Qualification v1",
            "",
            f"Decision: `{report['decision']}`",
            "",
            report["decision_reason"] + ".",
            "",
            "## Candidate",
            "",
            f"- backend: `{report['candidate']['id']}`;",
            f"- model: `{report['candidate']['model_id']}@{report['candidate']['model_revision']}`;",
            f"- controlled-dev threshold: `{report['calibration']['thresholds']['minimum_similarity']}` similarity / `{report['calibration']['thresholds']['minimum_margin']}` margin.",
            "",
            "## Independent Truth v2",
            "",
            f"- correct identities: `{truth['correct_identity_items']}/{truth['positive_items']}`;",
            f"- attributed precision / recall: `{truth['attributed_precision']}` / `{truth['attributed_recall']}`;",
            f"- false identities: `{truth['counts'].get('false_identity', 0)}`; unsafe special accepts: `{truth['counts'].get('unsafe_special_accept', 0)}`;",
            f"- hidden repeat determinism: `{truth['repeat']['determinism']}`;",
            f"- B-cubed F1: `{truth['bcubed']['f1']}`; speaker-count MAE: `{truth['speaker_count']['mean_absolute_error']}`.",
            "",
            "## Controls",
            "",
            f"- truth-v1 lost correct control identities: `{report['truth_v1_control']['lost_correct_control_identity_items']}`;",
            f"- truth-v1 new false identities: `{report['truth_v1_control']['new_false_identity_items']}`;",
            f"- controlled hard B-cubed F1: `{controlled['candidate']['bcubed']['f1']}` (Coverage v3 `{controlled['coverage_v3_control']['bcubed']['f1']}`);",
            f"- controlled hard boundary recall: `{controlled['candidate']['boundary_recall']}` (Coverage v3 `{controlled['coverage_v3_control']['boundary_recall']}`).",
            "",
            "## Safety",
            "",
            f"Failed gates: `{', '.join(report['failed_gates']) if report['failed_gates'] else 'none'}`.",
            f"Byte-exact replay: `{'passed' if report['replay_verified'] else 'pending'}`.",
            "Coverage v3, raw CAF, selected transcripts, ASR and Echo Guard were not modified.",
            f"Next: `{report['next']}`.",
            "",
        ]
    )


def action_evaluate(policy: dict[str, Any], out: Path) -> int:
    pack, freeze = verify_freeze(out)
    verify_sources(policy, "evaluate")
    marker = {
        "schema": "murmurmark.disjoint_remote_speaker_model_unseal/v1",
        "candidate_pack_sha256": freeze["candidate_pack_sha256"],
        "candidate_id": pack["candidate"]["id"],
        "truth_v2_answers_sha256": source_map(policy)["truth_v2_answers"]["sha256"],
        "tuning_after_unseal_allowed": False,
    }
    marker_path = out / "unseal_marker.json"
    if marker_path.is_file() and marker_path.read_bytes() != pretty_json(marker):
        raise QualificationError("unseal marker changed")
    if not marker_path.is_file():
        write_json(marker_path, marker)
    core, rows = build_core(policy, out)
    write_json(out / "private/evaluation_core.json", core)
    write_jsonl(out / "private/item_evaluation.jsonl", rows)
    report = public_report(core, replay_verified=False)
    write_json(out / "disjoint_remote_speaker_model_qualification_report.json", report)
    atomic_write(out / "disjoint_remote_speaker_model_qualification_report.md", report_markdown(report).encode())
    print(f"decision: {report['decision']}")
    return 0


def action_replay(policy: dict[str, Any], out: Path) -> int:
    report_path = out / "disjoint_remote_speaker_model_qualification_report.json"
    core_path = out / "private/evaluation_core.json"
    rows_path = out / "private/item_evaluation.jsonl"
    if not report_path.is_file() or not core_path.is_file() or not rows_path.is_file():
        raise QualificationError("evaluate must run before replay")
    rebuilt_core, rebuilt_rows = build_core(policy, out)
    if pretty_json(rebuilt_core) != core_path.read_bytes():
        raise QualificationError("evaluation core replay changed")
    if b"".join(canonical_json(row) for row in rebuilt_rows) != rows_path.read_bytes():
        raise QualificationError("item evaluation replay changed")
    report_before = report_path.read_bytes()
    expected_without_replay = pretty_json(public_report(rebuilt_core, replay_verified=False))
    expected_with_replay = pretty_json(public_report(rebuilt_core, replay_verified=True))
    if report_before not in {expected_without_replay, expected_with_replay}:
        raise QualificationError("public report replay changed")
    replay = {
        "schema": REPLAY_SCHEMA,
        "byte_exact": True,
        "candidate_pack_sha256": sha256(out / "private/candidate_pack.frozen.json"),
        "evaluation_core_sha256": sha256(core_path),
        "item_evaluation_sha256": sha256(rows_path),
        "report_sha256_before_replay_flag": hashlib.sha256(expected_without_replay).hexdigest(),
    }
    write_json(out / "replay_report.json", replay)
    final_report = public_report(rebuilt_core, replay_verified=True)
    atomic_write(report_path, expected_with_replay)
    atomic_write(
        out / "disjoint_remote_speaker_model_qualification_report.md",
        report_markdown(final_report).encode(),
    )
    print(f"replay: byte-exact ({replay['evaluation_core_sha256']})")
    return 0


def action_finalize(policy: dict[str, Any], out: Path) -> int:
    report = out / "disjoint_remote_speaker_model_qualification_report.json"
    replay = out / "replay_report.json"
    freeze = out / "freeze_manifest.json"
    public_pack = out / "candidate_pack.public.json"
    for path in (report, replay, freeze, public_pack):
        if not path.is_file():
            raise QualificationError(f"final artifact is missing: {path.name}")
    value = read_json(report)
    if value.get("replay_verified") is not True:
        raise QualificationError("final report replay is not verified")
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "decision": value["decision"],
        "artifacts": [artifact(path) for path in (report, replay, freeze, public_pack)],
        "production_promotion_allowed": False,
        "auto_selection_allowed": False,
    }
    write_json(out / "artifact_manifest.json", manifest)
    print(f"finalized: {manifest['decision']}")
    return 0


def action_status(out: Path) -> int:
    report_path = out / "disjoint_remote_speaker_model_qualification_report.json"
    if not report_path.is_file():
        state = "frozen" if out.joinpath("freeze_manifest.json").is_file() else "pending"
        print(f"decision: {state}")
        return 0
    report = read_json(report_path)
    print(f"decision: {report['decision']}")
    print(f"candidate: {report['candidate']['id']}")
    print(
        "truth_v2_correct: "
        f"{report['truth_v2']['correct_identity_items']}/{report['truth_v2']['positive_items']}"
    )
    print(f"truth_v2_precision: {report['truth_v2']['attributed_precision']}")
    print(f"truth_v2_recall: {report['truth_v2']['attributed_recall']}")
    print(f"failed_gates: {','.join(report['failed_gates']) if report['failed_gates'] else 'none'}")
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
    args = parse_args()
    policy = load_policy(args.policy.expanduser().resolve())
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
    except QualificationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
