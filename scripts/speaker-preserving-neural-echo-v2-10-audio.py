#!/usr/bin/env python3
"""Run the v2.7 audio candidate with exact local-token preservation gates."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "policies/speaker-preserving-neural-echo-v2-10-audio.json"
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-10"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V27 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-7.py",
    "murmurmark_spne_v210_audio_base",
)
me_guard_dialogue_path: Callable[[Path], Path] = V27.me_guard_dialogue_path


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    checks = {
        "schema": policy.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_audio_policy/v2.10",
        "base_policy": artifact(policy, "base_policy").is_file()
        and sha256(artifact(policy, "base_policy")) == policy.get("base_policy_sha256"),
        "base_runtime": artifact(policy, "base_runtime").is_file()
        and sha256(artifact(policy, "base_runtime")) == policy.get("base_runtime_sha256"),
        "exact_chunk_local_retention": policy.get("overrides", {})
        .get("diagnostic_asr_guard", {})
        .get("local_token_retention_ratio_min")
        == 1.0,
        "exact_final_local_retention": policy.get("overrides", {})
        .get("development_gates", {})
        .get("final_local_token_retention_ratio_min")
        == 1.0,
        "zero_post_asr_credit": policy.get("post_asr_cleanup_promotion_credit") == 0,
    }
    if not all(checks.values()):
        raise RuntimeError(f"v2.10 audio policy verification failed: {checks}")
    return policy


def merge_policy(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for section, values in overrides.items():
        if not isinstance(values, dict) or not isinstance(result.get(section), dict):
            raise RuntimeError(f"unsupported v2.10 audio policy override: {section}")
        result[section].update(values)
    result["candidate_revision"] = "exact_local_chunk_guard_v1"
    result["status"] = "v2_10_locked_exact_local_selector"
    return result


def materialize_policy(
    source: Path, destination: Path, *, excluded_chunks: list[int]
) -> Path:
    policy = verify_policy(source)
    base = read_json(artifact(policy, "base_policy"))
    materialized = merge_policy(base, policy["overrides"])
    materialized["v2_10_excluded_diagnostic_chunks"] = excluded_chunks
    write_json(destination, materialized)
    V27.verify_policy(destination)
    return destination


def seed_v27_cache(session: Path, output: Path) -> dict[str, int]:
    source = session / "derived/preprocess/speaker-preserving-neural-echo-v2-7"
    copied_files = 0
    for name in (
        "proposal_manifest.json",
        "proposed_windows.jsonl",
        "rejected_windows.jsonl",
    ):
        source_file = source / name
        destination = output / name
        if source_file.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
            copied_files += 1
    copied_entries = 0
    source_cache = source / "diagnostic-asr-cache"
    destination_cache = output / "diagnostic-asr-cache"
    if source_cache.is_dir():
        destination_cache.mkdir(parents=True, exist_ok=True)
        for entry in source_cache.iterdir():
            if not entry.is_dir() or (destination_cache / entry.name).exists():
                continue
            try:
                shutil.copytree(entry, destination_cache / entry.name, copy_function=os.link)
            except OSError:
                shutil.rmtree(destination_cache / entry.name, ignore_errors=True)
                shutil.copytree(entry, destination_cache / entry.name, copy_function=shutil.copy2)
            copied_entries += 1
    return {"proposal_files": copied_files, "diagnostic_entries": copied_entries}


def run_session(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = session / "derived/preprocess" / OUTPUT_NAME
    excluded_chunks = sorted(
        {int(value) for value in getattr(args, "excluded_chunks", []) if int(value) > 0}
    )
    materialized = materialize_policy(
        args.policy.expanduser().resolve(),
        output / "materialized_audio_policy.json",
        excluded_chunks=excluded_chunks,
    )
    seeded = seed_v27_cache(session, output) if not args.refresh else {}
    runtime_args = argparse.Namespace(
        session=session,
        policy=materialized,
        output=args.output,
        whisper_model=args.whisper_model,
        refresh=args.refresh,
        proposal_only=getattr(args, "proposal_only", False),
    )
    original_guard = V27.me_guard_dialogue_path
    original_diagnostic_select = V27.diagnostic_select

    def exact_local_select(**kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        selected, decisions = original_diagnostic_select(**kwargs)
        if not excluded_chunks:
            return selected, decisions
        excluded = set(excluded_chunks)
        selected = [
            row for row in selected if int(row.get("diagnostic_chunk") or 0) not in excluded
        ]
        return selected, decisions

    V27.me_guard_dialogue_path = me_guard_dialogue_path
    V27.diagnostic_select = exact_local_select
    try:
        with V27.exclusive_session_run(output):
            payload = V27.run_session_locked(runtime_args, session=session, output=output)
    except V27.SessionRunBusy as error:
        payload = {
            "schema": "murmurmark.speaker_preserving_neural_echo_runtime/v2.10",
            "status": "busy",
            "reason": "session_run_already_active",
            "session": session.name,
            "lock_holder": str(error),
        }
    finally:
        V27.me_guard_dialogue_path = original_guard
        V27.diagnostic_select = original_diagnostic_select
    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_runtime/v2.10"
    payload["selection_contract"] = "exact_local_token_retention_per_changed_chunk"
    payload["audio_policy_sha256"] = sha256(args.policy.expanduser().resolve())
    payload["v2_7_cache_seed"] = seeded
    payload["excluded_diagnostic_chunks"] = excluded_chunks
    if payload.get("status") in {"candidate", "fallback"}:
        write_json(output / "runtime_report.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--whisper-model", type=Path, required=True)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--exclude-chunk", action="append", type=int, default=[])
    args = parser.parse_args()
    args.output = ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-10"
    args.excluded_chunks = args.exclude_chunk
    payload = run_session(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"candidate", "fallback"} else 6


if __name__ == "__main__":
    raise SystemExit(main())
