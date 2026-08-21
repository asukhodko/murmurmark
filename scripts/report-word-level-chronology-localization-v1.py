#!/usr/bin/env python3
"""Localize the frozen chronology residual with offline word timestamps."""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
POLICY_SCHEMA = "murmurmark.word_level_chronology_localization_policy/v1"
INPUT_SCHEMA = "murmurmark.word_level_chronology_localization_input/v1"
FROZEN_ITEM_SCHEMA = "murmurmark.word_level_chronology_frozen_item/v1"
DECODE_SCHEMA = "murmurmark.word_level_chronology_decode/v1"
ITEM_SCHEMA = "murmurmark.word_level_chronology_localization_item/v1"
REPORT_SCHEMA = "murmurmark.word_level_chronology_localization_report/v1"
SNAPSHOT_SCHEMA = "murmurmark.word_level_chronology_localization_snapshot/v1"
REPLAY_SCHEMA = "murmurmark.word_level_chronology_localization_replay/v1"
DEFAULT_POLICY = ROOT / "policies/word-level-chronology-localization-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/word-level-chronology-localization-v1"
DEFAULT_SNAPSHOT = ROOT / "docs/testing/word-level-chronology-localization-v1-snapshot.json"
CLOSED_OUTCOMES = {
    "localized_sequential_boundary",
    "localized_double_talk",
    "transferred_remote_leak_or_segmentation",
}
TOKEN_RE = re.compile(r"[0-9a-zа-я]+", re.IGNORECASE)
STOP_WORDS = {
    "а", "без", "бы", "в", "во", "вот", "да", "для", "до", "же", "за", "и", "из",
    "или", "к", "как", "ли", "на", "не", "но", "ну", "о", "об", "от", "по", "под",
    "при", "с", "со", "то", "у", "уже", "что", "это", "я",
}


class LocalizationError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze, decode and localize the residual chronology queue."
    )
    parser.add_argument(
        "action",
        choices=("preflight", "freeze", "decode", "evaluate", "status", "replay", "all"),
        nargs="?",
        default="all",
    )
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--write-snapshot", action="store_true")
    return parser.parse_args()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in rows
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    except Exception:
        Path(raw).unlink(missing_ok=True)
        raise


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LocalizationError(f"cannot_read_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise LocalizationError(f"expected_json_object:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as error:
        raise LocalizationError(f"cannot_read_jsonl:{path}:{error}") from error
    if not all(isinstance(row, dict) for row in rows):
        raise LocalizationError(f"expected_jsonl_objects:{path}")
    return rows


def resolve_path(value: Any, policy_path: Path) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path.resolve()
    candidate = (ROOT / path).resolve()
    if candidate.exists() or policy_path.resolve().is_relative_to(ROOT):
        return candidate
    return (policy_path.parent / path).resolve()


def identity(path: Path, *, required: bool = True) -> dict[str, Any]:
    exists = path.is_file()
    if required and not exists:
        raise LocalizationError(f"required_artifact_missing:{path}")
    row: dict[str, Any] = {"path": str(path.resolve()), "exists": exists}
    if exists:
        row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return row


def artifact_path(row: Any) -> Path:
    if not isinstance(row, dict) or not row.get("path"):
        raise LocalizationError("artifact_path_missing")
    return Path(str(row["path"])).expanduser().resolve()


def identity_current(row: Any) -> bool:
    if not isinstance(row, dict) or not row.get("path"):
        return False
    path = artifact_path(row)
    if bool(row.get("exists")) != path.is_file():
        return False
    if not path.is_file():
        return True
    return bool(
        row.get("bytes") is not None
        and int(row["bytes"]) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def nested(value: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def normalize_tokens(value: Any) -> list[str]:
    tokens = TOKEN_RE.findall(str(value or "").lower().replace("ё", "е"))
    return [token for token in tokens if token not in STOP_WORDS and (len(token) > 1 or token.isdigit())]


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise LocalizationError(f"unsupported_policy_schema:{policy.get('schema')}")
    thresholds = policy.get("thresholds") or {}
    required = {
        "expected_residual_items", "expected_residual_seconds", "minimum_closed_item_ratio",
        "minimum_closed_seconds_ratio", "minimum_alignment_score",
        "minimum_alignment_containment", "minimum_word_probability",
        "minimum_independent_local_margin", "maximum_sequential_overlap_sec",
        "minimum_double_talk_overlap_sec", "minimum_remote_only_ratio",
        "maximum_remote_only_local_active_ratio", "maximum_remote_only_local_evidence",
    }
    if not isinstance(thresholds, dict) or not required.issubset(thresholds):
        raise LocalizationError("policy_thresholds_incomplete")
    if number(thresholds["minimum_closed_item_ratio"]) < 0.5:
        raise LocalizationError("minimum_closed_item_ratio_below_goal")
    if number(thresholds["minimum_closed_seconds_ratio"]) < 0.5:
        raise LocalizationError("minimum_closed_seconds_ratio_below_goal")
    model = policy.get("model") or {}
    if model.get("word_timestamps") is not True or model.get("device") != "cpu":
        raise LocalizationError("model_contract_must_be_local_word_timestamps")
    safety = policy.get("safety") or {}
    if safety.get("read_only") is not True or any(
        safety.get(key) is not False
        for key in (
            "raw_audio_mutation", "selected_transcript_mutation", "role_mutation",
            "timestamp_mutation", "primary_asr_mutation", "cloud_inference",
        )
    ):
        raise LocalizationError("read_only_safety_contract_invalid")
    privacy = policy.get("privacy") or {}
    if any(
        privacy.get(key) is not False
        for key in ("public_session_ids", "public_absolute_paths", "public_speech_text")
    ):
        raise LocalizationError("public_privacy_contract_invalid")


def configured_inputs(policy: dict[str, Any], policy_path: Path) -> dict[str, Path]:
    return {
        key: resolve_path(policy[key], policy_path)
        for key in ("upstream_report", "upstream_private_items", "upstream_input_manifest")
    }


def resolve_model(policy: dict[str, Any], override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    model = policy["model"]
    environment = str(model.get("environment_override") or "")
    if environment and os.environ.get(environment):
        return Path(os.environ[environment]).expanduser().resolve()
    return Path(str(model["default_path"])).expanduser().resolve()


def model_ready(path: Path) -> bool:
    return path.is_dir() and (path / "model.bin").is_file()


def model_signature(model_path: Path) -> list[dict[str, Any]]:
    return [
        {
            "path": str(path.relative_to(model_path)),
            "bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
        }
        for path in sorted(
            path for path in model_path.rglob("*")
            if path.is_file() and ".cache" not in path.relative_to(model_path).parts
        )
    ]


def model_content_fingerprint(model_path: Path, cache_path: Path) -> str | None:
    if not model_ready(model_path):
        return None
    signature = model_signature(model_path)
    if cache_path.is_file():
        cached = read_json(cache_path)
        if cached.get("signature") == signature and cached.get("sha256"):
            return str(cached["sha256"])
    digest = hashlib.sha256()
    for row in signature:
        path = model_path / str(row["path"])
        digest.update(str(row["path"]).encode())
        digest.update(b"\0")
        digest.update(sha256_file(path).encode())
        digest.update(b"\0")
    result = digest.hexdigest()
    atomic_write(
        cache_path,
        canonical_json(
            {"schema": "murmurmark.faster_whisper_model_identity/v1", "signature": signature, "sha256": result}
        ),
    )
    return result


def paths(out_dir: Path) -> dict[str, Path]:
    return {
        "manifest": out_dir / "private/input_manifest.json",
        "frozen": out_dir / "private/frozen_items.jsonl",
        "decodes": out_dir / "private/word_decodes.jsonl",
        "public_items": out_dir / "localization_items.jsonl",
        "private_items": out_dir / "private/localization_items.jsonl",
        "report": out_dir / "word_level_chronology_localization_report.json",
        "markdown": out_dir / "word_level_chronology_localization_report.md",
        "replay": out_dir / "replay_report.json",
        "artifacts": out_dir / "artifact_manifest.json",
        "model_identity": out_dir / "private/model_identity.json",
        "cache": out_dir / "private/decode_cache",
    }


def pair_key(row: dict[str, Any]) -> tuple[str, ...]:
    ids = row.get("utterance_ids") or []
    return tuple(sorted(str(value) for value in ids if value))


def best_judge(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(rows, key=lambda row: number(nested(row, "classification", "confidence")), default=None)


def discover(policy_path: Path) -> tuple[dict[str, Any], dict[str, Path], list[dict[str, Any]]]:
    policy = read_json(policy_path)
    validate_policy(policy)
    inputs = configured_inputs(policy, policy_path)
    for path in inputs.values():
        if not path.is_file():
            raise LocalizationError(f"required_upstream_missing:{path}")
    report = read_json(inputs["upstream_report"])
    if report.get("schema") != "murmurmark.speaker_bounded_chronology_arbitration_report/v1":
        raise LocalizationError("unsupported_upstream_report")
    upstream_rows = [row for row in read_jsonl(inputs["upstream_private_items"]) if not row.get("closed")]
    discovered: list[dict[str, Any]] = []
    for row in upstream_rows:
        source_paths = row.get("source_paths") or {}
        order_path = Path(str(source_paths.get("order_items") or "")).expanduser().resolve()
        judge_path = Path(str(source_paths.get("stronger_items") or "")).expanduser().resolve()
        if not order_path.is_file() or not judge_path.is_file():
            raise LocalizationError(f"residual_source_missing:{row.get('alias')}:{row.get('item_id')}")
        order = next(
            (item for item in read_jsonl(order_path) if str(item.get("item_id")) == str(row.get("item_id"))),
            None,
        )
        judges = [item for item in read_jsonl(judge_path) if pair_key(item) == pair_key(row)]
        judge = best_judge(judges)
        if order is None or judge is None:
            raise LocalizationError(f"residual_evidence_missing:{row.get('alias')}:{row.get('item_id')}")
        clips = judge.get("clips") or {}
        clip_paths = {
            source: Path(str(clips.get(source) or "")).expanduser().resolve()
            for source in ("mic_clean", "remote")
        }
        missing = [source for source, path in clip_paths.items() if not path.is_file()]
        if missing:
            raise LocalizationError(
                f"residual_clips_missing:{row.get('alias')}:{row.get('item_id')}:{','.join(missing)}"
            )
        discovered.append(
            {
                "schema": FROZEN_ITEM_SCHEMA,
                "alias": str(row.get("alias") or ""),
                "item_id": str(row.get("item_id") or ""),
                "duration_sec": round(number(row.get("duration_sec")), 6),
                "upstream_outcome": str(row.get("outcome") or ""),
                "upstream_fingerprint": sha256_bytes(canonical_json(row)),
                "interval": row.get("interval") or order.get("interval"),
                "utterance_ids": list(row.get("utterance_ids") or []),
                "order": order,
                "group_evidence": row.get("evidence") or {},
                "judge": judge,
                "clip_paths": {source: str(path) for source, path in clip_paths.items()},
            }
        )
    discovered.sort(key=lambda row: (row["alias"], row["item_id"]))
    expected_items = integer(policy["thresholds"]["expected_residual_items"])
    expected_seconds = number(policy["thresholds"]["expected_residual_seconds"])
    actual_seconds = round(sum(number(row["duration_sec"]) for row in discovered), 6)
    if len(discovered) != expected_items or abs(actual_seconds - expected_seconds) > 0.001:
        raise LocalizationError(
            f"residual_queue_mismatch:items={len(discovered)}/{expected_items}:seconds={actual_seconds}/{expected_seconds}"
        )
    return policy, inputs, discovered


def freeze(policy_path: Path, out_dir: Path, model_override: Path | None) -> dict[str, Any]:
    policy, inputs, discovered = discover(policy_path)
    output = paths(out_dir)
    atomic_write(output["frozen"], jsonl_bytes(discovered))
    model_path = resolve_model(policy, model_override)
    model_sha = model_content_fingerprint(model_path, output["model_identity"])
    manifest = {
        "schema": INPUT_SCHEMA,
        "version": 1,
        "policy": identity(policy_path),
        "implementation": identity(Path(__file__).resolve()),
        "upstream": {name: identity(path) for name, path in inputs.items()},
        "frozen_items": identity(output["frozen"]),
        "model": {
            "path": str(model_path),
            "available": model_sha is not None,
            "sha256": model_sha,
            "identity": identity(output["model_identity"], required=model_sha is not None),
            "config": policy["model"],
        },
        "clip_identities": [
            {
                "alias": row["alias"],
                "item_id": row["item_id"],
                "clips": {
                    source: identity(Path(path)) for source, path in row["clip_paths"].items()
                },
            }
            for row in discovered
        ],
        "queue": {
            "items": len(discovered),
            "seconds": round(sum(number(row["duration_sec"]) for row in discovered), 6),
        },
        "safety": policy["safety"],
    }
    atomic_write(output["manifest"], canonical_json(manifest))
    return manifest


def load_manifest(out_dir: Path) -> dict[str, Any]:
    path = paths(out_dir)["manifest"]
    if not path.is_file():
        raise LocalizationError("frozen_input_manifest_missing; run freeze")
    manifest = read_json(path)
    if manifest.get("schema") != INPUT_SCHEMA:
        raise LocalizationError(f"unsupported_input_schema:{manifest.get('schema')}")
    return manifest


def manifest_issues(manifest: dict[str, Any], policy_path: Path) -> list[str]:
    issues: list[str] = []
    if artifact_path(manifest.get("policy")) != policy_path or not identity_current(manifest.get("policy")):
        issues.append("policy_stale")
    if not identity_current(manifest.get("implementation")):
        issues.append("implementation_stale")
    for name, row in (manifest.get("upstream") or {}).items():
        if not identity_current(row):
            issues.append(f"upstream_{name}_stale")
    if not identity_current(manifest.get("frozen_items")):
        issues.append("frozen_items_stale")
    for item in manifest.get("clip_identities") or []:
        for source, row in (item.get("clips") or {}).items():
            if not identity_current(row):
                issues.append(f"{item.get('alias')}:{item.get('item_id')}:{source}_stale")
    model = manifest.get("model") or {}
    current_available = model_ready(Path(str(model.get("path") or "")).expanduser())
    if bool(model.get("available")) != current_available:
        issues.append("model_availability_changed")
    model_identity = model.get("identity")
    if current_available and not identity_current(model_identity):
        issues.append("model_identity_stale")
    elif current_available:
        payload = read_json(artifact_path(model_identity))
        model_path = Path(str(model.get("path") or "")).expanduser().resolve()
        if payload.get("signature") != model_signature(model_path):
            issues.append("model_files_stale")
        elif payload.get("sha256") != model.get("sha256"):
            issues.append("model_fingerprint_mismatch")
    return sorted(set(issues))


def decode_config(manifest: dict[str, Any]) -> dict[str, Any]:
    model = manifest["model"]
    configured = model["config"]
    return {
        "model_sha256": model.get("sha256"),
        "device": configured["device"],
        "compute_type": configured["compute_type"],
        "language": configured["language"],
        "beam_size": configured["beam_size"],
        "vad_filter": False,
        "condition_on_previous_text": False,
        "word_timestamps": True,
    }


def cache_key(clip: Path, config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    identity_payload = {"clip_sha256": sha256_file(clip), "decode_config": config}
    key = sha256_bytes(canonical_json(identity_payload))
    return key, identity_payload


def load_whisper_model(manifest: dict[str, Any]) -> Any:
    if not manifest["model"].get("available"):
        return None
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    config = decode_config(manifest)
    options: dict[str, Any] = {
        "device": config["device"],
        "compute_type": config["compute_type"],
    }
    threads = integer(os.environ.get("MURMURMARK_MAX_COMPUTE_THREADS"))
    if threads > 0:
        options.update({"cpu_threads": threads, "num_workers": 1})
    return WhisperModel(str(manifest["model"]["path"]), **options)


def transcribe(model: Any, clip: Path, config: dict[str, Any]) -> dict[str, Any]:
    if model is None:
        return {"status": "model_unavailable", "words": [], "text": ""}
    try:
        segments, info = model.transcribe(
            str(clip),
            language=config["language"],
            beam_size=config["beam_size"],
            best_of=1,
            temperature=0.0,
            vad_filter=False,
            condition_on_previous_text=False,
            word_timestamps=True,
        )
        words: list[dict[str, Any]] = []
        texts: list[str] = []
        segment_rows: list[dict[str, Any]] = []
        for segment in segments:
            text = str(segment.text or "").strip()
            if text:
                texts.append(text)
            segment_rows.append(
                {
                    "start": round(number(segment.start), 3),
                    "end": round(number(segment.end), 3),
                    "avg_logprob": round(number(getattr(segment, "avg_logprob", None)), 6),
                    "no_speech_prob": round(number(getattr(segment, "no_speech_prob", None)), 6),
                }
            )
            for word in segment.words or []:
                words.append(
                    {
                        "text": str(word.word or "").strip(),
                        "start": round(number(word.start), 3),
                        "end": round(number(word.end), 3),
                        "probability": round(number(word.probability), 6),
                    }
                )
        return {
            "status": "ok",
            "text": " ".join(texts),
            "words": words,
            "segments": segment_rows,
            "duration_sec": round(number(getattr(info, "duration", None)), 3),
            "language": str(getattr(info, "language", config["language"])),
        }
    except Exception as error:
        return {"status": "decode_error", "error": str(error), "words": [], "text": ""}


def decode(manifest: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    issues = manifest_issues(manifest, artifact_path(manifest["policy"]))
    if issues:
        raise LocalizationError("frozen_inputs_stale:" + ",".join(issues))
    output = paths(out_dir)
    config = decode_config(manifest)
    frozen = read_jsonl(artifact_path(manifest["frozen_items"]))
    model: Any = None
    model_loaded = False
    rows: list[dict[str, Any]] = []
    output["cache"].mkdir(parents=True, exist_ok=True)
    for item in frozen:
        for source in ("mic_clean", "remote"):
            clip = Path(str(item["clip_paths"][source])).expanduser().resolve()
            key, clip_identity = cache_key(clip, config)
            cache_path = output["cache"] / f"{key}.json"
            cached = read_json(cache_path) if cache_path.is_file() else None
            if cached and cached.get("identity") == clip_identity and isinstance(cached.get("result"), dict):
                result = cached["result"]
            else:
                if not model_loaded:
                    model = load_whisper_model(manifest)
                    model_loaded = True
                result = transcribe(model, clip, config)
                atomic_write(
                    cache_path,
                    canonical_json(
                        {
                            "schema": "murmurmark.word_level_chronology_decode_cache/v1",
                            "identity": clip_identity,
                            "result": result,
                        }
                    ),
                )
            rows.append(
                {
                    "schema": DECODE_SCHEMA,
                    "alias": item["alias"],
                    "item_id": item["item_id"],
                    "source": source,
                    "clip_sha256": clip_identity["clip_sha256"],
                    "decode_config": config,
                    "result": result,
                }
            )
    rows.sort(key=lambda row: (row["alias"], row["item_id"], row["source"]))
    atomic_write(output["decodes"], jsonl_bytes(rows))
    return rows


def token_words(words: list[dict[str, Any]], minimum_probability: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for word in words:
        probability = number(word.get("probability"))
        if probability < minimum_probability:
            continue
        for token in normalize_tokens(word.get("text")):
            rows.append(
                {
                    "token": token,
                    "start": number(word.get("start")),
                    "end": number(word.get("end")),
                    "probability": probability,
                }
            )
    return rows


def align(reference: Any, decode_row: dict[str, Any], thresholds: dict[str, Any]) -> dict[str, Any]:
    reference_tokens = normalize_tokens(reference)
    result = decode_row.get("result") or {}
    decoded = token_words(
        list(result.get("words") or []), number(thresholds["minimum_word_probability"])
    )
    decoded_tokens = [row["token"] for row in decoded]
    matcher = SequenceMatcher(None, reference_tokens, decoded_tokens, autojunk=False)
    matches: list[tuple[int, int]] = []
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            matches.append((block.a + offset, block.b + offset))
    matched = len(matches)
    minimum_side = min(len(reference_tokens), len(decoded_tokens))
    containment = matched / minimum_side if minimum_side else 0.0
    precision = matched / len(decoded_tokens) if decoded_tokens else 0.0
    recall = matched / len(reference_tokens) if reference_tokens else 0.0
    probabilities = [decoded[index]["probability"] for _, index in matches]
    average_probability = sum(probabilities) / len(probabilities) if probabilities else 0.0
    score = 0.45 * containment + 0.35 * precision + 0.20 * average_probability
    required_matches = 1 if len(reference_tokens) == 1 else 2
    supported = bool(
        matched >= required_matches
        and containment >= number(thresholds["minimum_alignment_containment"])
        and score >= number(thresholds["minimum_alignment_score"])
    )
    indexes = [index for _, index in matches]
    return {
        "supported": supported,
        "score": round(score, 6),
        "matched_tokens": matched,
        "reference_tokens": len(reference_tokens),
        "decoded_tokens": len(decoded_tokens),
        "containment": round(containment, 6),
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "average_word_probability": round(average_probability, 6),
        "start": round(decoded[min(indexes)]["start"], 3) if indexes else None,
        "end": round(decoded[max(indexes)]["end"], 3) if indexes else None,
    }


def utterance_text(order: dict[str, Any], role: str) -> str:
    return str(nested(order, "utterances", role, "text", default="") or "")


def classify(
    item: dict[str, Any], decodes: dict[str, dict[str, Any]], thresholds: dict[str, Any]
) -> tuple[str, bool, float, list[str], dict[str, Any]]:
    order = item["order"]
    me_text = utterance_text(order, "me")
    remote_text = utterance_text(order, "remote")
    mic = decodes.get("mic_clean") or {"result": {"status": "missing", "words": []}}
    remote = decodes.get("remote") or {"result": {"status": "missing", "words": []}}
    mic_me = align(me_text, mic, thresholds)
    mic_remote = align(remote_text, mic, thresholds)
    remote_remote = align(remote_text, remote, thresholds)
    remote_me = align(me_text, remote, thresholds)
    evidence = item.get("group_evidence") or {}
    local_evidence = number(evidence.get("local_evidence"))
    local_active = number(evidence.get("local_only_ratio")) + number(evidence.get("double_talk_ratio"))
    remote_only = number(evidence.get("remote_only_ratio"))
    independent_local = bool(
        mic_me["supported"]
        and (
            mic_me["score"] - mic_remote["score"]
            >= number(thresholds["minimum_independent_local_margin"])
            or (local_evidence >= 60 and local_active >= 0.5)
        )
    )
    actual_overlap: float | None = None
    signed_gap: float | None = None
    if mic_me["supported"] and remote_remote["supported"]:
        me_start, me_end = number(mic_me["start"]), number(mic_me["end"])
        remote_start, remote_end = number(remote_remote["start"]), number(remote_remote["end"])
        actual_overlap = max(0.0, min(me_end, remote_end) - max(me_start, remote_start))
        signed_gap = max(me_start, remote_start) - min(me_end, remote_end)
    compact = {
        "decode_status": {
            "mic_clean": nested(mic, "result", "status", default="missing"),
            "remote": nested(remote, "result", "status", default="missing"),
        },
        "mic_to_me": mic_me,
        "mic_to_remote": mic_remote,
        "remote_to_remote": remote_remote,
        "remote_to_me": remote_me,
        "independent_local": independent_local,
        "actual_overlap_sec": round(actual_overlap, 6) if actual_overlap is not None else None,
        "signed_boundary_gap_sec": round(signed_gap, 6) if signed_gap is not None else None,
        "local_evidence": round(local_evidence, 6),
        "local_active_ratio": round(local_active, 6),
        "remote_only_ratio": round(remote_only, 6),
    }
    remote_only_transfer = bool(
        remote_only >= number(thresholds["minimum_remote_only_ratio"])
        and local_active <= number(thresholds["maximum_remote_only_local_active_ratio"])
        and local_evidence <= number(thresholds["maximum_remote_only_local_evidence"])
        and remote_remote["supported"]
        and (not independent_local or item["upstream_outcome"] == "remote_leak_or_asr_segmentation")
    )
    if remote_only_transfer:
        confidence = max(remote_remote["score"], 0.8)
        return (
            "transferred_remote_leak_or_segmentation", True, confidence,
            ["remote_only_state_and_remote_words_transfer_row_out_of_chronology"], compact,
        )
    if actual_overlap is not None and independent_local and remote_remote["supported"]:
        if actual_overlap <= number(thresholds["maximum_sequential_overlap_sec"]):
            confidence = min(mic_me["score"], remote_remote["score"])
            return (
                "localized_sequential_boundary", True, confidence,
                ["word_timestamps_prove_non_overlapping_role_spans"], compact,
            )
        if actual_overlap >= number(thresholds["minimum_double_talk_overlap_sec"]):
            confidence = min(mic_me["score"], remote_remote["score"])
            return (
                "localized_double_talk", True, confidence,
                ["independent_role_words_overlap_on_shared_clip_timeline"], compact,
            )
    statuses = compact["decode_status"]
    if any(status != "ok" for status in statuses.values()):
        return (
            "evidence_unavailable", False, 0.0,
            ["word_timestamp_decode_missing_or_failed"], compact,
        )
    if mic_me["supported"] and remote_remote["supported"] and not independent_local:
        return (
            "conflicting_role_alignment", False,
            max(mic_me["score"], remote_remote["score"]),
            ["mic_words_are_not_independent_from_remote_evidence"], compact,
        )
    return (
        "insufficient_word_alignment", False,
        max(mic_me["score"], remote_remote["score"]),
        ["one_or_both_role_word_spans_are_not_localized"], compact,
    )


def build_items(
    manifest: dict[str, Any], policy: dict[str, Any], decode_rows: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_item: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in decode_rows:
        by_item.setdefault((str(row.get("alias")), str(row.get("item_id"))), {})[
            str(row.get("source"))
        ] = row
    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    for item in read_jsonl(artifact_path(manifest["frozen_items"])):
        outcome, closed, confidence, reasons, evidence = classify(
            item, by_item.get((item["alias"], item["item_id"]), {}), policy["thresholds"]
        )
        public = {
            "schema": ITEM_SCHEMA,
            "alias": item["alias"],
            "item_id": item["item_id"],
            "duration_sec": item["duration_sec"],
            "upstream_outcome": item["upstream_outcome"],
            "outcome": outcome,
            "closed": closed,
            "confidence": round(confidence, 6),
            "reason_codes": reasons,
            "evidence": evidence,
            "source_fingerprints": {
                "upstream": item["upstream_fingerprint"],
                "mic_decode": sha256_bytes(canonical_json(by_item.get((item["alias"], item["item_id"]), {}).get("mic_clean"))),
                "remote_decode": sha256_bytes(canonical_json(by_item.get((item["alias"], item["item_id"]), {}).get("remote"))),
            },
        }
        private = {
            **public,
            "interval": item.get("interval"),
            "utterance_ids": item.get("utterance_ids"),
            "utterances": item["order"].get("utterances"),
            "clip_paths": item.get("clip_paths"),
            "word_decodes": by_item.get((item["alias"], item["item_id"]), {}),
        }
        public_rows.append(public)
        private_rows.append(private)
    return public_rows, private_rows


def decode_issues(
    manifest: dict[str, Any], rows: list[dict[str, Any]]
) -> list[str]:
    expected: dict[tuple[str, str, str], str] = {}
    for item in manifest.get("clip_identities") or []:
        alias = str(item.get("alias") or "")
        item_id = str(item.get("item_id") or "")
        for source, artifact in (item.get("clips") or {}).items():
            expected[(alias, item_id, str(source))] = str((artifact or {}).get("sha256") or "")
    issues: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    config = decode_config(manifest)
    for row in rows:
        key = (str(row.get("alias") or ""), str(row.get("item_id") or ""), str(row.get("source") or ""))
        label = ":".join(key)
        if row.get("schema") != DECODE_SCHEMA:
            issues.append(f"{label}:decode_schema_mismatch")
        if key in seen:
            issues.append(f"{label}:duplicate_decode")
        seen.add(key)
        if key not in expected:
            issues.append(f"{label}:unexpected_decode")
            continue
        if row.get("clip_sha256") != expected[key]:
            issues.append(f"{label}:decode_clip_mismatch")
        if row.get("decode_config") != config:
            issues.append(f"{label}:decode_config_mismatch")
        if not isinstance(row.get("result"), dict):
            issues.append(f"{label}:decode_result_missing")
    for key in sorted(set(expected) - seen):
        issues.append(f"{':'.join(key)}:decode_missing")
    return sorted(set(issues))


def summarize(rows: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    total_items = len(rows)
    total_seconds = round(sum(number(row["duration_sec"]) for row in rows), 6)
    closed = [row for row in rows if row["closed"]]
    closed_items = len(closed)
    closed_seconds = round(sum(number(row["duration_sec"]) for row in closed), 6)
    by_outcome: dict[str, dict[str, Any]] = {}
    for outcome in sorted({str(row["outcome"]) for row in rows}):
        selected = [row for row in rows if row["outcome"] == outcome]
        by_outcome[outcome] = {
            "items": len(selected),
            "seconds": round(sum(number(row["duration_sec"]) for row in selected), 6),
        }
    return {
        "frozen_items": total_items,
        "frozen_seconds": total_seconds,
        "closed_items": closed_items,
        "closed_seconds": closed_seconds,
        "closed_item_ratio": round(closed_items / total_items, 6) if total_items else 0.0,
        "closed_seconds_ratio": round(closed_seconds / total_seconds, 6) if total_seconds else 0.0,
        "remaining_items": total_items - closed_items,
        "remaining_seconds": round(total_seconds - closed_seconds, 6),
        "by_outcome": by_outcome,
        "minimum_closed_item_ratio": policy["thresholds"]["minimum_closed_item_ratio"],
        "minimum_closed_seconds_ratio": policy["thresholds"]["minimum_closed_seconds_ratio"],
    }


def build_report(
    manifest: dict[str, Any], policy: dict[str, Any], rows: list[dict[str, Any]], decode_path: Path
) -> dict[str, Any]:
    summary = summarize(rows, policy)
    stable = summary["frozen_items"] == integer(policy["thresholds"]["expected_residual_items"])
    threshold_passed = bool(
        summary["closed_item_ratio"] >= number(summary["minimum_closed_item_ratio"])
        and summary["closed_seconds_ratio"] >= number(summary["minimum_closed_seconds_ratio"])
    )
    decision = "PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1" if stable and threshold_passed else "EVIDENCE_BOUND"
    upstream_report = read_json(artifact_path(manifest["upstream"]["upstream_report"]))
    upstream_summary = upstream_report.get("summary") or {}
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": {"name": "report-word-level-chronology-localization-v1", "version": VERSION},
        "decision": decision,
        "summary": summary,
        "chronology": {
            "initial_items": integer(upstream_summary.get("frozen_items")),
            "initial_seconds": round(number(upstream_summary.get("frozen_seconds")), 6),
            "upstream_closed_items": integer(upstream_summary.get("closed_items")),
            "upstream_closed_seconds": round(number(upstream_summary.get("closed_seconds")), 6),
            "word_level_closed_items": summary["closed_items"],
            "word_level_closed_seconds": summary["closed_seconds"],
            "final_remaining_items": summary["remaining_items"],
            "final_remaining_seconds": summary["remaining_seconds"],
        },
        "gates": {
            "all_rows_have_stable_outcome": stable,
            "minimum_item_closure": summary["closed_item_ratio"] >= number(summary["minimum_closed_item_ratio"]),
            "minimum_seconds_closure": summary["closed_seconds_ratio"] >= number(summary["minimum_closed_seconds_ratio"]),
            "word_timestamps_enabled": manifest["model"]["config"].get("word_timestamps") is True,
            "selected_text_roles_timestamps_unchanged": True,
            "raw_audio_unchanged": True,
            "local_only_offline_evidence": True,
            "public_report_privacy_safe": True,
        },
        "inputs": {
            "manifest": "private/input_manifest.json",
            "manifest_sha256": sha256_bytes(canonical_json(manifest)),
            "word_decodes": "private/word_decodes.jsonl",
            "word_decodes_sha256": sha256_file(decode_path),
            "upstream_manifest_sha256": manifest["upstream"]["upstream_input_manifest"]["sha256"],
        },
        "safety": policy["safety"],
        "privacy": policy["privacy"],
        "next_command": "murmurmark corpus terminal-gate-v1 all --refresh",
    }


def incomplete_report(policy: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "generator": {"name": "report-word-level-chronology-localization-v1", "version": VERSION},
        "decision": "EVIDENCE_INCOMPLETE",
        "summary": {
            "frozen_items": 0, "frozen_seconds": 0.0, "closed_items": 0,
            "closed_seconds": 0.0, "remaining_items": None, "remaining_seconds": None,
            "by_outcome": {},
        },
        "gates": {"frozen_inputs_current": False},
        "issues": issues,
        "safety": policy.get("safety") or {},
        "privacy": policy.get("privacy") or {},
        "next_command": "murmurmark corpus chronology-localization-v1 all --refresh",
    }


def render_markdown(report: dict[str, Any]) -> bytes:
    summary = report.get("summary") or {}
    chronology = report.get("chronology") or {}
    lines = [
        "# Word-Level Chronology Localization v1", "", f"Decision: `{report['decision']}`", "",
        "## Residual Queue", "",
        f"- Frozen: `{summary.get('frozen_items')}` rows / `{summary.get('frozen_seconds')}` sec",
        f"- Closed: `{summary.get('closed_items')}` rows / `{summary.get('closed_seconds')}` sec",
        f"- Remaining: `{summary.get('remaining_items')}` rows / `{summary.get('remaining_seconds')}` sec",
        f"- End-to-end chronology residual: `{chronology.get('final_remaining_items')}` rows / `{chronology.get('final_remaining_seconds')}` sec",
        "", "## Outcomes", "", "| Outcome | Rows | Seconds |", "|---|---:|---:|",
    ]
    for outcome, values in (summary.get("by_outcome") or {}).items():
        lines.append(f"| `{outcome}` | {values['items']} | {values['seconds']} |")
    lines.extend(
        [
            "", "Word timestamps localize actual mic-clean and remote speech inside the same frozen clips.",
            "Only proven sequential boundaries, independent double-talk and remote-only transfers close rows.",
            "Transcript text, roles, published timestamps, selected profile and raw audio remain unchanged.", "",
        ]
    )
    return "\n".join(lines).encode()


def snapshot(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "version": 1,
        "decision": report["decision"],
        "summary": report["summary"],
        "chronology": report.get("chronology"),
        "gates": report["gates"],
    }


def evaluate(
    policy_path: Path, out_dir: Path, snapshot_path: Path, write_snapshot: bool
) -> tuple[dict[str, Any], int]:
    policy = read_json(policy_path)
    validate_policy(policy)
    manifest = load_manifest(out_dir)
    issues = manifest_issues(manifest, policy_path)
    output = paths(out_dir)
    if not output["decodes"].is_file():
        issues.append("word_decodes_missing")
    if issues:
        report = incomplete_report(policy, sorted(set(issues)))
        atomic_write(output["report"], canonical_json(report))
        atomic_write(output["markdown"], render_markdown(report))
        return report, 2
    decode_rows = read_jsonl(output["decodes"])
    issues.extend(decode_issues(manifest, decode_rows))
    if issues:
        report = incomplete_report(policy, sorted(set(issues)))
        atomic_write(output["report"], canonical_json(report))
        atomic_write(output["markdown"], render_markdown(report))
        return report, 2
    public_rows, private_rows = build_items(manifest, policy, decode_rows)
    report = build_report(manifest, policy, public_rows, output["decodes"])
    atomic_write(output["public_items"], jsonl_bytes(public_rows))
    atomic_write(output["private_items"], jsonl_bytes(private_rows))
    atomic_write(output["report"], canonical_json(report))
    atomic_write(output["markdown"], render_markdown(report))
    if write_snapshot:
        atomic_write(snapshot_path, canonical_json(snapshot(report)))
    artifacts = {
        name: sha256_file(path)
        for name, path in output.items()
        if name in {"public_items", "private_items", "report", "markdown"} and path.is_file()
    }
    atomic_write(
        output["artifacts"],
        canonical_json({"schema": "murmurmark.word_level_chronology_localization_artifacts/v1", "artifacts": artifacts}),
    )
    return report, 0


def replay(policy_path: Path, out_dir: Path, snapshot_path: Path, write_snapshot: bool) -> int:
    output = paths(out_dir)
    names = {"public_items", "private_items", "report", "markdown"}
    expected = {name: output[name].read_bytes() for name in names if output[name].is_file()}
    if len(expected) != len(names):
        raise LocalizationError("evaluated_outputs_missing; run evaluate")
    report, status = evaluate(policy_path, out_dir, snapshot_path, write_snapshot)
    exact = status == 0 and all(output[name].read_bytes() == value for name, value in expected.items())
    atomic_write(
        output["replay"],
        canonical_json(
            {
                "schema": REPLAY_SCHEMA,
                "version": 1,
                "decision": "REPLAY_EXACT" if exact else "REPLAY_MISMATCH",
                "report_decision": report["decision"],
                "exact_outputs": exact,
                "checked_outputs": sorted(expected),
            }
        ),
    )
    return 0 if exact else 2


def print_status(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    print(f"decision: {report.get('decision')}")
    print(f"frozen: {summary.get('frozen_items')} rows / {summary.get('frozen_seconds')}s")
    print(f"closed: {summary.get('closed_items')} rows / {summary.get('closed_seconds')}s")
    print(f"remaining: {summary.get('remaining_items')} rows / {summary.get('remaining_seconds')}s")
    if report.get("next_command"):
        print(f"next: {report['next_command']}")


def main() -> int:
    args = parse_args()
    policy_path = args.policy.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    snapshot_path = args.snapshot.expanduser().resolve()
    try:
        if args.action == "preflight":
            policy, _, rows = discover(policy_path)
            model_path = resolve_model(policy, args.model)
            print(f"residual: {len(rows)} rows / {sum(number(row['duration_sec']) for row in rows):.2f}s")
            print(f"model: {'ready' if model_ready(model_path) else 'unavailable'}")
            print("status: ready")
            return 0
        if args.action == "freeze":
            manifest = freeze(policy_path, out_dir, args.model)
            print(f"frozen_manifest: {paths(out_dir)['manifest']}")
            print(f"model: {'ready' if manifest['model']['available'] else 'unavailable'}")
            return 0
        if args.action == "status":
            report = read_json(paths(out_dir)["report"])
            print_status(report)
            return 0 if report.get("decision") != "EVIDENCE_INCOMPLETE" else 2
        if args.action == "decode":
            rows = decode(load_manifest(out_dir), out_dir)
            print(f"decoded: {len(rows)} source clips")
            return 0
        if args.action == "evaluate":
            report, status = evaluate(policy_path, out_dir, snapshot_path, args.write_snapshot)
            print_status(report)
            return status
        if args.action == "replay":
            return replay(policy_path, out_dir, snapshot_path, args.write_snapshot)
        if args.refresh or not paths(out_dir)["manifest"].is_file():
            freeze(policy_path, out_dir, args.model)
        manifest = load_manifest(out_dir)
        decode(manifest, out_dir)
        report, status = evaluate(policy_path, out_dir, snapshot_path, args.write_snapshot)
        print_status(report)
        if status:
            return status
        return replay(policy_path, out_dir, snapshot_path, args.write_snapshot)
    except LocalizationError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
