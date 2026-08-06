#!/usr/bin/env python3
"""Requalify the unchanged v2.15 selector against the current ASR contract."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-17.json"
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-17"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BASE = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-15.py",
    "murmurmark_spne_v217_selector_base",
)
AUDIO = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-17-audio.py",
    "murmurmark_spne_v217_audio",
)
SHADOW = load_module(
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-17.py",
    "murmurmark_spne_v217_shadow",
)
V28 = BASE.V28


def policy_artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = V28.read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.17":
        raise RuntimeError("unexpected v2.17 policy schema")
    pairs = (
        ("base_selector_policy", "base_selector_policy_sha256"),
        ("base_selector_runtime", "base_selector_runtime_sha256"),
        ("audio_policy", "audio_policy_sha256"),
        ("audio_base_runtime", "audio_base_runtime_sha256"),
        ("audio_adapter_runtime", "audio_adapter_runtime_sha256"),
        ("shadow_base_runtime", "shadow_base_runtime_sha256"),
        ("shadow_adapter_runtime", "shadow_adapter_runtime_sha256"),
        ("selector_runtime", "selector_runtime_sha256"),
        ("transcriber_runtime", "transcriber_runtime_sha256"),
        ("authoritative_asr_cache_runtime", "authoritative_asr_cache_runtime_sha256"),
        ("hard_set", "hard_set_sha256"),
        ("corpus_set", "corpus_set_sha256"),
    )
    checks = {
        key: policy_artifact(policy, key).is_file()
        and V28.sha256(policy_artifact(policy, key)) == policy.get(hash_key)
        for key, hash_key in pairs
    }
    hard = V28.read_json(policy_artifact(policy, "hard_set"))
    corpus = V28.read_json(policy_artifact(policy, "corpus_set"))
    hard_ids = {row["id"] for row in hard.get("sessions", [])}
    corpus_ids = {row["id"] for row in corpus.get("sessions", [])}
    checks.update(
        {
            "policy_locked": policy.get("status")
            == "locked_before_one_shot_requalification",
            "contract_change_only": policy.get("contract_change")
            == "current_transcriber_and_authoritative_cache_requalification_without_audio_or_threshold_changes",
            "algorithm_revision_unchanged": policy.get("algorithm_revision")
            == "speaker_preserving_neural_echo_v2_15",
            "threshold_changes_zero": policy.get("threshold_changes") == 0,
            "hard_set_frozen": hard.get("status")
            == "frozen_before_v2_17_requalification",
            "corpus_set_frozen": corpus.get("status")
            == "frozen_before_v2_17_requalification",
            "sets_disjoint": bool(hard_ids)
            and bool(corpus_ids)
            and hard_ids.isdisjoint(corpus_ids),
            "exact_fallback": policy.get("fallback") == "local_fir_role_masked",
            "zero_post_asr_credit": policy.get("post_asr_cleanup_promotion_credit")
            == 0,
        }
    )
    AUDIO.V210.verify_policy(policy_artifact(policy, "audio_policy"))
    if not all(checks.values()):
        raise RuntimeError(f"v2.17 policy verification failed: {checks}")
    return policy


def output_root(session: Path) -> Path:
    return session / "derived/preprocess" / OUTPUT_NAME


def candidate_audio(session: Path) -> Path:
    return output_root(session) / "candidate_clean_mic_pcm16.wav"


def configure_base() -> None:
    BASE.POLICY_PATH = POLICY_PATH
    BASE.OUTPUT_NAME = OUTPUT_NAME
    BASE.AUDIO = AUDIO
    BASE.SHADOW = SHADOW
    BASE.verify_policy = verify_policy
    BASE.output_root = output_root
    BASE.candidate_audio = candidate_audio
    BASE.__file__ = str(Path(__file__).resolve())


def run(args: argparse.Namespace) -> dict[str, Any]:
    configure_base()
    payload = BASE.run(args)
    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_selection/v2.17"
    payload["candidate"] = (
        "speaker_preserving_neural_echo_v2_17"
        if payload.get("status") == "candidate"
        else None
    )
    payload["requalification_contract"] = {
        "algorithm_revision": "speaker_preserving_neural_echo_v2_15",
        "threshold_changes": 0,
        "transcriber_runtime": str(
            policy_artifact(verify_policy(args.policy), "transcriber_runtime")
        ),
        "authoritative_asr_cache_runtime": str(
            policy_artifact(verify_policy(args.policy), "authoritative_asr_cache_runtime")
        ),
    }
    payload["selection_fingerprint"] = V28.stable_digest(
        {
            "basis": payload.get("basis"),
            "status": payload.get("status"),
            "checks": payload.get("checks"),
            "applicability": payload.get("applicability"),
            "selected_audio_sha256": payload.get("selected_audio", {}).get("sha256"),
            "requalification_contract": payload["requalification_contract"],
        }
    )
    V28.write_json(output_root(args.session.expanduser().resolve()) / "selection_report.json", payload)
    return payload


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    sub = value.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("session", type=Path)
    run_parser.add_argument("--refresh", action="store_true")
    verify_parser = sub.add_parser("verify-policy")
    verify_parser.add_argument("session", nargs="?", type=Path)
    return value


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "verify-policy":
        payload = verify_policy(args.policy)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("status") in {"candidate", "fallback"} else 6


if __name__ == "__main__":
    raise SystemExit(main())
