#!/usr/bin/env python3
"""Select the frozen audio candidate using profile-matched shadow evidence."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-9.json"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-9"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V28 = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-8.py",
    "murmurmark_spne_v29_selector_base",
)
SHADOW = load_module(
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-9.py",
    "murmurmark_spne_v29_shadow",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    sub = value.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("session", type=Path)
    run_parser.add_argument("--refresh", action="store_true")
    verify_parser = sub.add_parser("verify")
    verify_parser.add_argument("session", type=Path)
    return value


def policy_artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = V28.read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.9":
        raise RuntimeError("unexpected v2.9 policy schema")
    pairs = (
        ("v2_7_policy", "v2_7_policy_sha256"),
        ("v2_7_runtime", "v2_7_runtime_sha256"),
        ("v2_7_shadow_shared", "v2_7_shadow_shared_sha256"),
        ("v2_9_shadow", "v2_9_shadow_sha256"),
        ("hard_set", "hard_set_sha256"),
    )
    checks = {
        key: policy_artifact(policy, key).is_file()
        and V28.sha256(policy_artifact(policy, key)) == policy.get(hash_key)
        for key, hash_key in pairs
    }
    hard_set = V28.read_json(policy_artifact(policy, "hard_set"))
    checks["hard_set_frozen"] = (
        hard_set.get("status") == "frozen_before_v2_9_implementation"
        and hard_set.get("training_use") == "forbidden"
        and hard_set.get("threshold_tuning_use") == "forbidden"
    )
    checks["audio_transform_unchanged"] = (
        policy.get("audio_transform_changed_from_v2_7") is False
    )
    checks["profile_matched_verdict"] = (
        policy.get("verdict_comparison_profile") == "shadow_v2"
    )
    checks["zero_post_asr_credit"] = (
        policy.get("post_asr_cleanup_promotion_credit") == 0
    )
    if not all(checks.values()):
        raise RuntimeError(f"v2.9 policy verification failed: {checks}")
    return policy


def output_root(session: Path) -> Path:
    return session / "derived/preprocess" / OUTPUT_NAME


def run(args: argparse.Namespace) -> dict[str, Any]:
    original_output_name = V28.OUTPUT_NAME
    original_verify_policy = V28.verify_policy
    original_shadow = V28.SHADOW
    V28.OUTPUT_NAME = OUTPUT_NAME
    V28.verify_policy = verify_policy
    V28.SHADOW = SHADOW
    try:
        payload = V28.run(args)
    finally:
        V28.OUTPUT_NAME = original_output_name
        V28.verify_policy = original_verify_policy
        V28.SHADOW = original_shadow

    payload["schema"] = "murmurmark.speaker_preserving_neural_echo_selection/v2.9"
    payload["selection_contract"] = "profile_matched_shadow_v2_fail_open"
    payload["candidate"] = (
        "speaker_preserving_neural_echo_v2_9"
        if payload.get("status") == "candidate"
        else None
    )
    payload["selection_fingerprint"] = V28.stable_digest(
        {
            "basis": payload.get("basis", {}),
            "status": payload.get("status"),
            "checks": payload.get("checks", {}),
            "selected_audio_sha256": payload.get("selected_audio", {}).get("sha256"),
            "selection_contract": payload["selection_contract"],
        }
    )
    V28.write_json(output_root(args.session.expanduser().resolve()) / "selection_report.json", payload)
    return payload


def verify(args: argparse.Namespace) -> dict[str, Any]:
    verify_policy(args.policy)
    session = args.session.expanduser().resolve()
    report = V28.read_json(output_root(session) / "selection_report.json")
    selected = output_root(session) / "selected_clean_mic_pcm16.wav"
    baseline = session / "derived/asr/mic.wav"
    checks = {
        "report_schema": report.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_selection/v2.9",
        "terminal_status": report.get("status") in {"candidate", "fallback"},
        "selected_audio_exists": selected.is_file(),
        "selected_audio_hash": selected.is_file()
        and report.get("selected_audio", {}).get("sha256") == V28.sha256(selected),
        "fallback_exact": report.get("status") != "fallback"
        or (baseline.is_file() and V28.sha256(selected) == V28.sha256(baseline)),
        "candidate_full_shadow_passed": report.get("status") != "candidate"
        or report.get("full_shadow", {}).get("passed") is True,
        "candidate_profiles_match": report.get("status") != "candidate"
        or report.get("full_shadow", {}).get("gates", {}).get(
            "verdict_profiles_match"
        )
        is True,
        "zero_post_asr_credit": report.get("post_asr_cleanup_promotion_credit") == 0,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_verification/v2.9",
        "session": session.name,
        "checks": checks,
        "passed": all(checks.values()),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.command == "run":
        payload = run(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    return 0 if verify(args).get("passed") else 6


if __name__ == "__main__":
    raise SystemExit(main())
