#!/usr/bin/env python3
"""Publish a promoted pre-ASR echo candidate with an exact FIR fallback."""

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
POLICY = ROOT / "policies/speaker-preserving-neural-echo-production-v2.json"
WHISPER_MODEL = (
    Path.home() / ".local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
)
OUTPUT_NAME = "speaker-preserving-neural-echo-v2"
PROMOTION_DECISION = "PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2"
PRODUCTION_SCHEMA = "murmurmark.speaker_preserving_neural_echo_production_policy/v2.16"
SELECTION_SCHEMA = "murmurmark.speaker_preserving_neural_echo_production_selection/v2.16"
TRANSACTION_SCHEMA = (
    "murmurmark.speaker_preserving_neural_echo_publication_transaction/v2.16"
)
SHADOW_FILES = (
    "clean_dialogue.shadow_v2.json",
    "overlaps.shadow_v2.json",
    "quality_report.shadow_v2.json",
    "role_decisions.shadow_v2.json",
    "transcript.shadow_v2.md",
    "transcript.simple.shadow_v2.json",
    "transcribe_simple_report.shadow_v2.json",
)
CANONICAL_AUDIO_FILES = (
    "derived/preprocess/audio/mic_for_asr.wav",
    "derived/preprocess/audio/mic_role_masked_for_asr.wav",
    "derived/asr/mic.wav",
)
TRANSACTION_NAME = "publication_transaction.json"


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SELECTOR = load_module(
    ROOT / "scripts/speaker-preserving-neural-echo-v2-15.py",
    "murmurmark_spne_v2_production_selector",
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("session", type=Path)
    value.add_argument("--policy", type=Path, default=POLICY)
    value.add_argument("--whisper-model", type=Path, default=WHISPER_MODEL)
    value.add_argument("--refresh", action="store_true")
    value.add_argument("--verify-only", action="store_true")
    value.add_argument("--prepare-baseline", action="store_true")
    value.add_argument(
        "--fresh-preprocess",
        action="store_true",
        help="declare that Echo Guard just rebuilt the canonical local-FIR audio",
    )
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


def fingerprint(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": relative(path, root),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def policy_path(policy: dict[str, Any], key: str) -> Path:
    return (ROOT / str(policy[key])).resolve()


def verify_policy(path: Path) -> tuple[dict[str, Any], dict[str, bool]]:
    policy = read_json(path)
    checks: dict[str, bool] = {
        "schema": policy.get("schema") == PRODUCTION_SCHEMA,
        "promoted": policy.get("decision") == PROMOTION_DECISION,
        "fallback": policy.get("fallback") == "local_fir_role_masked",
        "candidate_primary_asr": policy.get("candidate_audio_is_primary_whisper_input")
        is True,
        "zero_post_asr_credit": policy.get("post_asr_cleanup_promotion_credit") == 0,
    }
    for key, hash_key in (
        ("selector_policy", "selector_policy_sha256"),
        ("selector_runtime", "selector_runtime_sha256"),
        ("audio_runtime", "audio_runtime_sha256"),
        ("shadow_runtime", "shadow_runtime_sha256"),
        ("transcriber_runtime", "transcriber_runtime_sha256"),
        ("evaluation_policy", "evaluation_policy_sha256"),
        ("evaluation_runtime", "evaluation_runtime_sha256"),
        ("hard_report", "hard_report_sha256"),
        ("hard_decision", "hard_decision_sha256"),
        ("corpus_report", "corpus_report_sha256"),
        ("corpus_decision", "corpus_decision_sha256"),
    ):
        artifact = policy_path(policy, key) if key in policy else Path("/")
        checks[key] = (
            artifact.is_file()
            and isinstance(policy.get(hash_key), str)
            and sha256(artifact) == policy[hash_key]
        )
    hard_report = (
        read_json(policy_path(policy, "hard_report")) if checks["hard_report"] else {}
    )
    hard_decision = (
        read_json(policy_path(policy, "hard_decision"))
        if checks["hard_decision"]
        else {}
    )
    report = read_json(policy_path(policy, "corpus_report")) if checks["corpus_report"] else {}
    corpus_decision = (
        read_json(policy_path(policy, "corpus_decision"))
        if checks["corpus_decision"]
        else {}
    )
    checks["hard_passed"] = (
        hard_report.get("passed") is True
        and hard_decision.get("decision") == "HARD_TEST_PASSED_V2_16"
        and hard_decision.get("report_sha256") == policy.get("hard_report_sha256")
        and hard_report.get("hard_fingerprint") == policy.get("hard_fingerprint")
    )
    checks["corpus_decision"] = (
        report.get("passed") is True
        and report.get("promotion", {}).get("decision") == PROMOTION_DECISION
        and corpus_decision.get("decision") == PROMOTION_DECISION
        and corpus_decision.get("report_sha256") == policy.get("corpus_report_sha256")
        and report.get("corpus_fingerprint") == policy.get("corpus_fingerprint")
    )
    summary = policy.get("promotion_summary", {})
    aggregate = report.get("aggregate", {})
    aggregate_keys = (
        "candidate_sessions",
        "fallback_sessions",
        "remote_supported_reduction_sec",
        "remote_supported_token_reduction",
        "selector_runtime_factor_max",
    )
    candidate_rows = [
        row for row in report.get("rows", []) if row.get("status") == "candidate"
    ]
    checks["promotion_summary"] = (
        summary.get("sessions") == len(report.get("rows", []))
        and all(aggregate.get(key) == summary.get(key) for key in aggregate_keys)
        and candidate_rows
        and all(
            row.get("local_retention_ratio")
            == summary.get("candidate_local_retention_ratio")
            for row in candidate_rows
        )
    )
    if checks["selector_policy"]:
        try:
            SELECTOR.verify_policy(policy_path(policy, "selector_policy"))
        except Exception:
            checks["selector_policy_contract"] = False
        else:
            checks["selector_policy_contract"] = True
    else:
        checks["selector_policy_contract"] = False
    return policy, checks


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def baseline_name(name: str) -> str:
    if ".shadow_v2." in name:
        return name.replace(".shadow_v2.", ".local_fir_role_masked.")
    return f"{Path(name).stem}.local_fir_role_masked{Path(name).suffix}"


def snapshot_baseline(resolved: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for name in (*SHADOW_FILES, "repair_comparison.json"):
        source = resolved / name
        if not source.is_file():
            raise RuntimeError(f"baseline shadow artifact missing: {source}")
        destination = resolved / baseline_name(name)
        # The first snapshot is the exact FIR fallback. Never replace it with an
        # already-published candidate during a resumed or repeated run.
        if not destination.is_file():
            atomic_copy(source, destination)
        rows[name] = fingerprint(destination, resolved.parent.parent.parent.parent)
    return rows


def restore_baseline(resolved: Path) -> None:
    for name in (*SHADOW_FILES, "repair_comparison.json"):
        source = resolved / baseline_name(name)
        if not source.is_file():
            raise RuntimeError(f"baseline snapshot missing during rollback: {source}")
        atomic_copy(source, resolved / name)


def snapshot_audio_baseline(session: Path, output: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    backup_root = output / "baseline-local-fir-role-masked"
    for relative_path in CANONICAL_AUDIO_FILES:
        source = session / relative_path
        if not source.is_file():
            raise RuntimeError(f"baseline audio artifact missing: {source}")
        destination = backup_root / relative_path
        if not destination.is_file():
            atomic_copy(source, destination)
        rows[relative_path] = fingerprint(destination, session)
    return rows


def restore_audio_baseline(session: Path, output: Path) -> None:
    backup_root = output / "baseline-local-fir-role-masked"
    for relative_path in CANONICAL_AUDIO_FILES:
        source = backup_root / relative_path
        if not source.is_file():
            raise RuntimeError(f"baseline audio snapshot missing during rollback: {source}")
        atomic_copy(source, session / relative_path)


def restore_all_baseline_artifacts(session: Path, output: Path) -> None:
    restore_baseline(session / "derived/transcript-simple/whisper-cpp/resolved")
    restore_audio_baseline(session, output)


def clear_baseline_snapshots(session: Path, output: Path) -> None:
    shutil.rmtree(output / "baseline-local-fir-role-masked", ignore_errors=True)
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for name in (*SHADOW_FILES, "repair_comparison.json"):
        (resolved / baseline_name(name)).unlink(missing_ok=True)


def prepare_primary_asr_baseline(
    session: Path, output: Path, *, fresh_preprocess: bool
) -> dict[str, Any]:
    transaction_path = output / TRANSACTION_NAME
    transaction = read_json(transaction_path)
    previous_state = transaction.get("state")
    if fresh_preprocess:
        clear_baseline_snapshots(session, output)
        audio_snapshot = snapshot_audio_baseline(session, output)
        transaction = {
            "schema": TRANSACTION_SCHEMA,
            "state": "baseline_prepared_from_fresh_local_fir",
            "previous_state": previous_state,
            "audio_baseline_snapshot": audio_snapshot,
        }
        write_json(transaction_path, transaction)
        action = "snapshotted_fresh_local_fir"
    else:
        recovered = recover_incomplete_publication(session, output)
        if recovered is not None:
            action = "recovered_incomplete_publication"
        elif previous_state == "committed":
            if committed_publication_is_active(session, output):
                restore_all_baseline_artifacts(session, output)
            elif exact_fallback_is_active(session, output):
                restore_baseline(
                    session / "derived/transcript-simple/whisper-cpp/resolved"
                )
            else:
                raise RuntimeError(
                    "committed candidate and exact local-FIR fallback are both unverifiable"
                )
            transaction["state"] = "baseline_prepared_before_primary_asr"
            write_json(transaction_path, transaction)
            action = "restored_exact_local_fir"
        else:
            action = "already_baseline_or_no_publication"
    payload = {
        "schema": "murmurmark.speaker_preserving_neural_echo_baseline_prepare/v2.16",
        "status": "prepared",
        "action": action,
        "fresh_preprocess": fresh_preprocess,
        "previous_transaction_state": previous_state,
        "selected_profile": "local_fir_role_masked",
        "batch_authoritative": True,
    }
    write_json(output / "baseline_prepare_report.json", payload)
    return payload


def recover_incomplete_publication(session: Path, output: Path) -> dict[str, Any] | None:
    path = output / TRANSACTION_NAME
    payload = read_json(path)
    if payload.get("state") not in {"prepared", "publishing"}:
        return None
    restore_all_baseline_artifacts(session, output)
    payload["state"] = "recovered_on_next_run"
    write_json(path, payload)
    return payload


def committed_publication_is_active(session: Path, output: Path) -> bool:
    transaction = read_json(output / TRANSACTION_NAME)
    published = transaction.get("published")
    if transaction.get("state") != "committed" or not isinstance(published, dict):
        return False
    required = (*SHADOW_FILES, "repair_comparison.json", *CANONICAL_AUDIO_FILES)
    for key in required:
        item = published.get(key)
        if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
            return False
        destination = (
            session / key
            if key in CANONICAL_AUDIO_FILES
            else session / "derived/transcript-simple/whisper-cpp/resolved" / key
        )
        if not destination.is_file() or sha256(destination) != item["sha256"]:
            return False
    return True


def restore_committed_publication(session: Path, output: Path, reason: str) -> bool:
    if not committed_publication_is_active(session, output):
        return False
    restore_all_baseline_artifacts(session, output)
    transaction = read_json(output / TRANSACTION_NAME)
    transaction["state"] = "reverted_to_exact_fallback"
    transaction["revert_reason"] = reason
    write_json(output / TRANSACTION_NAME, transaction)
    return True


def safely_restore_committed_publication(
    session: Path, output: Path, reason: str
) -> tuple[bool, dict[str, str] | None]:
    try:
        return restore_committed_publication(session, output, reason), None
    except Exception as error:
        return False, {"type": type(error).__name__, "message": str(error)}


def publish_candidate(session: Path, selection: dict[str, Any]) -> dict[str, Any]:
    stage_value = selection.get("full_shadow", {}).get("stage")
    if not isinstance(stage_value, str) or not stage_value:
        raise RuntimeError("selected candidate has no full-shadow stage")
    stage = Path(stage_value)
    if not stage.is_absolute():
        stage = session / stage
    source_resolved = stage / "derived/transcript-simple/whisper-cpp/resolved"
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    candidate_files = {name: source_resolved / name for name in SHADOW_FILES}
    candidate_files["repair_comparison.json"] = source_resolved / "repair_comparison.json"
    missing = [str(path) for path in candidate_files.values() if not path.is_file()]
    if missing:
        raise RuntimeError(f"candidate shadow artifacts missing: {missing}")
    published: dict[str, Any] = {}
    for name in SHADOW_FILES:
        source = candidate_files[name]
        destination = resolved / name
        atomic_copy(source, destination)
        published[name] = fingerprint(destination, session)
    comparison = candidate_files["repair_comparison.json"]
    atomic_copy(comparison, resolved / "repair_comparison.json")
    published["repair_comparison.json"] = fingerprint(
        resolved / "repair_comparison.json", session
    )
    selected_audio = (
        session
        / "derived/preprocess/speaker-preserving-neural-echo-v2-15/"
        "selected_clean_mic_pcm16.wav"
    )
    expected_selected = selection.get("selected_audio", {}).get("sha256")
    if not selected_audio.is_file() or sha256(selected_audio) != expected_selected:
        raise RuntimeError("selected v2.15 audio is missing or changed")
    destination_audio = (
        session
        / "derived/preprocess/audio/"
        "mic_for_asr.speaker_preserving_neural_echo_v2.wav"
    )
    atomic_copy(selected_audio, destination_audio)
    published["selected_audio"] = fingerprint(destination_audio, session)
    for relative_path in CANONICAL_AUDIO_FILES:
        destination = session / relative_path
        atomic_copy(selected_audio, destination)
        published[relative_path] = fingerprint(destination, session)
    return published


def acoustic_mode(session: Path) -> str:
    report = read_json(session / "derived/preprocess/echo/local_fir_report.json")
    value = report.get("acoustic_mode")
    return str(value.get("mode") or "missing") if isinstance(value, dict) else "missing"


def exact_fallback_is_active(session: Path, output: Path) -> bool:
    baseline = session / "derived/asr/mic.wav"
    if not baseline.is_file():
        return False
    backup = output / "baseline-local-fir-role-masked/derived/asr/mic.wav"
    if backup.is_file():
        return sha256(baseline) == sha256(backup)
    return not (output / TRANSACTION_NAME).exists()


def fallback_report(
    *,
    session: Path,
    output: Path,
    reason: str,
    checks: dict[str, bool],
    details: Any = None,
) -> dict[str, Any]:
    baseline = session / "derived/asr/mic.wav"
    exact_fallback = exact_fallback_is_active(session, output)
    payload = {
        "schema": SELECTION_SCHEMA,
        "status": "fallback",
        "reason": reason,
        "details": details,
        "selected_profile": "local_fir_role_masked",
        "batch_authoritative": True,
        "policy_checks": checks,
        "exact_fallback": exact_fallback,
        "selected_audio": fingerprint(baseline, session) if exact_fallback else None,
        "published": {},
        "post_asr_cleanup_promotion_credit": 0,
    }
    payload["selection_fingerprint"] = stable_digest(payload)
    write_json(output / "production_selection_report.json", payload)
    return payload


def run(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser().resolve()
    output = session / "derived/preprocess" / OUTPUT_NAME
    try:
        recovered_transaction = recover_incomplete_publication(session, output)
    except Exception as error:
        return fallback_report(
            session=session,
            output=output,
            reason="incomplete_publication_recovery_failed",
            checks={},
            details={"type": type(error).__name__, "message": str(error)},
        )
    policy, policy_checks = verify_policy(args.policy)
    if not all(policy_checks.values()):
        restored, restore_error = safely_restore_committed_publication(
            session, output, "production_policy_not_promoted_or_incompatible"
        )
        return fallback_report(
            session=session,
            output=output,
            reason="production_policy_not_promoted_or_incompatible",
            checks=policy_checks,
            details={
                "restored_committed_candidate": restored,
                "restore_error": restore_error,
            },
        )
    mode = acoustic_mode(session)
    if mode != "speaker_playback":
        restored, restore_error = safely_restore_committed_publication(
            session, output, "acoustic_mode_not_speaker_playback"
        )
        return fallback_report(
            session=session,
            output=output,
            reason="acoustic_mode_not_speaker_playback",
            checks=policy_checks,
            details={
                "acoustic_mode": mode,
                "restored_committed_candidate": restored,
                "restore_error": restore_error,
            },
        )
    restored_for_reselection, reselection_restore_error = safely_restore_committed_publication(
        session, output, "restore_exact_fallback_before_reselection"
    )
    if reselection_restore_error is not None:
        return fallback_report(
            session=session,
            output=output,
            reason="committed_publication_recovery_failed",
            checks=policy_checks,
            details=reselection_restore_error,
        )
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    transaction_started = False
    try:
        selection = SELECTOR.run(
            SimpleNamespace(
                session=session,
                policy=policy_path(policy, "selector_policy"),
                whisper_model=args.whisper_model,
                refresh=args.refresh,
            )
        )
        if selection.get("status") != "candidate":
            restored, restore_error = safely_restore_committed_publication(
                session, output, "session_selector_fallback"
            )
            return fallback_report(
                session=session,
                output=output,
                reason="session_selector_fallback",
                checks=policy_checks,
                details={
                    "selector_reason": selection.get("reason"),
                    "failed_checks": selection.get("failed_checks", []),
                    "recovered_transaction": recovered_transaction,
                    "restored_for_reselection": restored_for_reselection,
                    "restored_committed_candidate": restored,
                    "restore_error": restore_error,
                },
            )
        baseline_snapshot = snapshot_baseline(resolved)
        audio_baseline_snapshot = snapshot_audio_baseline(session, output)
        transaction = {
            "schema": TRANSACTION_SCHEMA,
            "state": "prepared",
            "selection_fingerprint": selection.get("selection_fingerprint"),
            "baseline_snapshot": baseline_snapshot,
            "audio_baseline_snapshot": audio_baseline_snapshot,
        }
        write_json(output / TRANSACTION_NAME, transaction)
        transaction_started = True
        transaction["state"] = "publishing"
        write_json(output / TRANSACTION_NAME, transaction)
        published = publish_candidate(session, selection)
        payload = {
            "schema": SELECTION_SCHEMA,
            "status": "candidate",
            "reason": "promoted_policy_and_session_gates_passed",
            "selected_profile": "speaker_preserving_neural_echo_v2",
            "selected_transcript_base_profile": "shadow_v2",
            "batch_authoritative": True,
            "policy_checks": policy_checks,
            "selector": selection,
            "restored_for_reselection": restored_for_reselection,
            "baseline_snapshot": baseline_snapshot,
            "audio_baseline_snapshot": audio_baseline_snapshot,
            "published": published,
            "post_asr_cleanup_promotion_credit": 0,
        }
        payload["selection_fingerprint"] = stable_digest(
            {
                "policy": fingerprint(args.policy, session),
                "selector_fingerprint": selection.get("selection_fingerprint"),
                "published": published,
            }
        )
        write_json(output / "production_selection_report.json", payload)
        transaction["state"] = "committed"
        transaction["published"] = published
        write_json(output / TRANSACTION_NAME, transaction)
        return payload
    except Exception as error:
        if transaction_started:
            try:
                restore_all_baseline_artifacts(session, output)
                transaction = read_json(output / TRANSACTION_NAME)
                transaction["state"] = "rolled_back"
                transaction["error"] = {
                    "type": type(error).__name__,
                    "message": str(error),
                }
                write_json(output / TRANSACTION_NAME, transaction)
            except Exception as rollback_error:
                error = RuntimeError(f"{error}; baseline rollback failed: {rollback_error}")
        return fallback_report(
            session=session,
            output=output,
            reason="production_selection_failure",
            checks=policy_checks,
            details={"type": type(error).__name__, "message": str(error)},
        )


def main() -> int:
    args = parser().parse_args()
    args.policy = args.policy.expanduser().resolve()
    args.whisper_model = args.whisper_model.expanduser().resolve()
    if args.fresh_preprocess and not args.prepare_baseline:
        raise SystemExit("--fresh-preprocess requires --prepare-baseline")
    if args.verify_only:
        policy, checks = verify_policy(args.policy)
        payload = {
            "schema": "murmurmark.speaker_preserving_neural_echo_production_policy_verification/v2.16",
            "decision": policy.get("decision"),
            "checks": checks,
            "passed": all(checks.values()),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 6
    if args.prepare_baseline:
        session = args.session.expanduser().resolve()
        output = session / "derived/preprocess" / OUTPUT_NAME
        try:
            payload = prepare_primary_asr_baseline(
                session, output, fresh_preprocess=args.fresh_preprocess
            )
        except Exception as error:
            payload = {
                "schema": "murmurmark.speaker_preserving_neural_echo_baseline_prepare/v2.16",
                "status": "failed",
                "reason": type(error).__name__,
                "message": str(error),
                "batch_authoritative": True,
            }
            write_json(output / "baseline_prepare_report.json", payload)
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            return 7
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    payload = run(args)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
