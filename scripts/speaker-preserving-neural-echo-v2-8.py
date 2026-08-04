#!/usr/bin/env python3
"""Select the frozen v2.7 pre-ASR candidate or the exact local-FIR baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = ROOT / "policies/speaker-preserving-neural-echo-v2-8.json"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
OUTPUT_NAME = "speaker-preserving-neural-echo-v2-8"


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
    "murmurmark_spne_v28_audio_runtime",
)
SHADOW = load_module(
    ROOT / "scripts/speaker-preserving-echo-full-shadow-v2-7.py",
    "murmurmark_spne_v28_shadow_runtime",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--policy", type=Path, default=POLICY_PATH)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    sub = value.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("session", type=Path)
    run.add_argument("--refresh", action="store_true")
    verify = sub.add_parser("verify")
    verify.add_argument("session", type=Path)
    return value


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


def stable_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def fingerprint(path: Path, root: Path = ROOT) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def policy_artifact(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if policy.get("schema") != "murmurmark.speaker_preserving_neural_echo_policy/v2.8":
        raise RuntimeError("unexpected v2.8 policy schema")
    pairs = (
        ("v2_7_policy", "v2_7_policy_sha256"),
        ("v2_7_runtime", "v2_7_runtime_sha256"),
        ("v2_7_shadow", "v2_7_shadow_sha256"),
        ("v2_7_shadow_shared", "v2_7_shadow_shared_sha256"),
    )
    checks = {
        key: policy_artifact(policy, key).is_file()
        and sha256(policy_artifact(policy, key)) == policy[hash_key]
        for key, hash_key in pairs
    }
    checks["audio_transform_unchanged"] = (
        policy.get("audio_transform_changed_from_v2_7") is False
    )
    checks["zero_post_asr_credit"] = policy.get(
        "post_asr_cleanup_promotion_credit"
    ) == 0
    if not all(checks.values()):
        raise RuntimeError(f"v2.8 policy verification failed: {checks}")
    return policy


def baseline_dialogue(session: Path) -> Path:
    return (
        session
        / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json"
    )


def baseline_audio(session: Path) -> Path:
    return session / "derived/asr/mic.wav"


def candidate_audio(session: Path) -> Path:
    return (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-7/"
        "candidate_clean_mic_pcm16.wav"
    )


def output_root(session: Path) -> Path:
    return session / "derived/preprocess" / OUTPUT_NAME


def copy_selected(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def full_shadow_checks(payload: dict[str, Any]) -> dict[str, bool]:
    gates = payload.get("gates") if isinstance(payload.get("gates"), dict) else {}
    return {
        "full_shadow_passed": payload.get("passed") is True,
        "full_shadow_gates_all_pass": bool(gates) and all(gates.values()),
    }


def fail_open(
    *,
    session: Path,
    output: Path,
    baseline: Path,
    reason: str,
    details: Any,
    basis: dict[str, Any],
) -> dict[str, Any]:
    selected_path = output / "selected_clean_mic_pcm16.wav"
    copy_selected(baseline, selected_path)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_selection/v2.8",
        "status": "fallback",
        "reason": reason,
        "details": details,
        "candidate": None,
        "audio_candidate": "speaker_preserving_neural_echo_v2_7",
        "fallback": "local_fir_role_masked",
        "basis": basis,
        "source_runtime": {},
        "full_shadow": {},
        "checks": {},
        "failed_checks": [reason],
        "exact_fallback": sha256(selected_path) == sha256(baseline),
        "selected_audio": fingerprint(selected_path, session),
        "selected_source_audio": fingerprint(baseline, session),
        "candidate_audio_is_primary_whisper_input": False,
        "post_asr_cleanup_promotion_credit": 0,
    }
    payload["selection_fingerprint"] = stable_digest(
        {
            "basis": basis,
            "status": "fallback",
            "reason": reason,
            "selected_audio_sha256": payload["selected_audio"]["sha256"],
        }
    )
    write_json(output / "selection_report.json", payload)
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = output_root(session)
    baseline = baseline_audio(session)
    dialogue = baseline_dialogue(session)
    if not baseline.is_file():
        raise RuntimeError(f"baseline local-FIR audio is required: {baseline}")

    basis = {
        "baseline_audio": fingerprint(baseline, session),
    }
    if args.policy.is_file():
        basis["policy"] = fingerprint(args.policy)
    if dialogue.is_file():
        basis["baseline_dialogue"] = fingerprint(dialogue, session)
    if args.whisper_model.is_file():
        basis["whisper_model"] = fingerprint(args.whisper_model)
    try:
        policy = verify_policy(args.policy)
        if not dialogue.is_file():
            raise RuntimeError(f"baseline shadow_v2 dialogue is required: {dialogue}")
        if not args.whisper_model.is_file():
            raise RuntimeError(f"whisper model is required: {args.whisper_model}")
    except Exception as error:
        return fail_open(
            session=session,
            output=output,
            baseline=baseline,
            reason="preflight_failure",
            details={"type": type(error).__name__, "message": str(error)},
            basis=basis,
        )
    existing = read_json(output / "selection_report.json")
    selected_path = output / "selected_clean_mic_pcm16.wav"
    if (
        not args.refresh
        and existing.get("basis") == basis
        and existing.get("status") in {"candidate", "fallback"}
        and selected_path.is_file()
        and existing.get("selected_audio", {}).get("sha256") == sha256(selected_path)
    ):
        return existing

    try:
        original_guard = V27.me_guard_dialogue_path
        V27.me_guard_dialogue_path = lambda _session: dialogue
        try:
            source = V27.run_session(
                SimpleNamespace(
                    session=session,
                    policy=policy_artifact(policy, "v2_7_policy"),
                    output=ROOT / "sessions/_reports/speaker-preserving-neural-echo-v2-7",
                    whisper_model=args.whisper_model,
                    refresh=args.refresh,
                    proposal_only=False,
                )
            )
        finally:
            V27.me_guard_dialogue_path = original_guard
    except Exception as error:
        return fail_open(
            session=session,
            output=output,
            baseline=baseline,
            reason="candidate_runtime_failure",
            details={"type": type(error).__name__, "message": str(error)},
            basis=basis,
        )

    source_checks = source.get("checks") if isinstance(source.get("checks"), dict) else {}
    checks = {
        "v2_7_runtime_candidate": source.get("status") == "candidate",
        "v2_7_runtime_checks_all_pass": bool(source_checks) and all(source_checks.values()),
        "candidate_audio_is_primary_whisper_input": source.get(
            "candidate_audio_is_primary_whisper_input"
        )
        is True,
    }
    shadow: dict[str, Any] = {}
    if all(checks.values()):
        try:
            shadow = SHADOW.run(
                SimpleNamespace(
                    session=session,
                    whisper_model=args.whisper_model,
                    refresh=args.refresh,
                )
            )
            checks.update(full_shadow_checks(shadow))
        except Exception as error:
            shadow = {
                "status": "failed",
                "error": {"type": type(error).__name__, "message": str(error)},
            }
            checks.update(
                {"full_shadow_passed": False, "full_shadow_gates_all_pass": False}
            )
    else:
        checks.update(
            {"full_shadow_passed": False, "full_shadow_gates_all_pass": False}
        )

    selected = all(checks.values())
    source_audio = candidate_audio(session) if selected else baseline
    copy_selected(source_audio, selected_path)
    exact_fallback = not selected and sha256(selected_path) == sha256(baseline)
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_selection/v2.8",
        "status": "candidate" if selected else "fallback",
        "reason": "all_pre_asr_and_full_shadow_gates_passed"
        if selected
        else "session_gate_failed_exact_fallback",
        "candidate": policy["candidate_revision"] if selected else None,
        "audio_candidate": policy["audio_candidate_revision"],
        "fallback": policy["fallback"],
        "basis": basis,
        "source_runtime": source,
        "full_shadow": shadow,
        "checks": checks,
        "failed_checks": sorted(key for key, value in checks.items() if not value),
        "exact_fallback": exact_fallback,
        "selected_audio": fingerprint(selected_path, session),
        "selected_source_audio": fingerprint(source_audio, session),
        "candidate_audio_is_primary_whisper_input": selected,
        "post_asr_cleanup_promotion_credit": 0,
    }
    payload["selection_fingerprint"] = stable_digest(
        {
            "basis": basis,
            "status": payload["status"],
            "checks": checks,
            "selected_audio_sha256": payload["selected_audio"]["sha256"],
        }
    )
    write_json(output / "selection_report.json", payload)
    return payload


def verify(args: argparse.Namespace) -> dict[str, Any]:
    verify_policy(args.policy)
    session = args.session.expanduser().resolve()
    report = read_json(output_root(session) / "selection_report.json")
    selected = output_root(session) / "selected_clean_mic_pcm16.wav"
    baseline = baseline_audio(session)
    checks = {
        "report_schema": report.get("schema")
        == "murmurmark.speaker_preserving_neural_echo_selection/v2.8",
        "terminal_status": report.get("status") in {"candidate", "fallback"},
        "selected_audio_exists": selected.is_file(),
        "selected_audio_hash": selected.is_file()
        and report.get("selected_audio", {}).get("sha256") == sha256(selected),
        "fallback_exact": report.get("status") != "fallback"
        or (baseline.is_file() and sha256(selected) == sha256(baseline)),
        "candidate_full_shadow_passed": report.get("status") != "candidate"
        or report.get("full_shadow", {}).get("passed") is True,
        "zero_post_asr_credit": report.get("post_asr_cleanup_promotion_credit") == 0,
    }
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_verification/v2.8",
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
        return 0 if payload.get("status") in {"candidate", "fallback"} else 1
    if args.command == "verify":
        return 0 if verify(args)["passed"] else 6
    raise RuntimeError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
