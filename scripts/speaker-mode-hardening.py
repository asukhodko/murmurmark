#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import math
import os
import subprocess
import statistics
import sys
import tempfile
import time
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any


SCRIPT_VERSION = "0.2.0"
REPORT_DIR_NAME = "speaker-mode-hardening-v1"
PROFILE = "speaker_mode_hardening_v1"
REPO_ROOT = Path(__file__).resolve().parents[1]
ECHO_HELPER = REPO_ROOT / "scripts/echo-guard-session-local-fir.py"
SPEAKER_COUPLED_RATIO_MIN = 0.30
SPEAKER_P75_MIN = 0.04
LOW_LEAK_COUPLED_RATIO_MAX = 0.15
LOW_LEAK_P75_MAX = 0.03
LOW_LEAK_P90_MAX = 0.06
MIN_REMOTE_ONLY_WINDOWS = 5
REMOTE_REDUCTION_TOLERANCE_DB = 0.25
REMOTE_SIMILARITY_TOLERANCE = 0.002
DUPLICATE_TEXT_SIMILARITY = 0.65
PROFILE_DIR_NAME = "speaker-mode-hardening-v1"
DEFAULT_MODEL = Path.home() / ".local/share/murmurmark/models/faster-whisper/large-v3"
PROFILE_OUTCOMES = {
    "genuine_me",
    "remote_duplicate_or_leak",
    "asr_noise",
    "real_double_talk",
    "timing_overlap",
    "insufficient_evidence",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze and evaluate the speaker-mode hardening corpus.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--sessions-root", type=Path, default=Path("sessions"))
        command.add_argument("--out-dir", type=Path)

    freeze = subparsers.add_parser("freeze", help="Freeze selected sessions, profiles and raw CAF identities.")
    common(freeze)
    freeze.add_argument("--session", action="append", type=Path, default=[])
    freeze.add_argument(
        "--context",
        action="append",
        default=[],
        metavar="SESSION=TAG[,TAG]",
        help="Attach local acceptance context without storing it in source code.",
    )
    freeze.add_argument("--force", action="store_true")

    classify = subparsers.add_parser("classify", help="Classify frozen sessions by remote-to-mic coupling.")
    common(classify)

    audit_echo = subparsers.add_parser(
        "audit-echo",
        help="Recompute Echo Guard metrics in isolation and compare them with the frozen baseline.",
    )
    common(audit_echo)
    audit_echo.add_argument("--session", action="append", default=[], help="Limit the audit to a session id or path.")
    audit_echo.add_argument("--force", action="store_true")

    freeze_risks = subparsers.add_parser(
        "freeze-risks",
        help="Freeze duplicate, local-recall and chronology risks for the hardening profile.",
    )
    common(freeze_risks)
    freeze_risks.add_argument("--force", action="store_true")

    evidence = subparsers.add_parser(
        "evidence",
        help="Build targeted multi-source ASR and Target-Me evidence for frozen risks.",
    )
    common(evidence)
    evidence.add_argument("sessions", nargs="*", type=Path)
    add_model_args(evidence)

    apply_profile = subparsers.add_parser(
        "apply",
        help="Apply conservative evidence decisions to one isolated profile.",
    )
    common(apply_profile)
    apply_profile.add_argument("session", type=Path)
    apply_profile.add_argument("--mode", choices=("conservative", "dry-run"), default="conservative")

    evaluate = subparsers.add_parser("evaluate", help="Evaluate the hardening profile on the frozen corpus.")
    common(evaluate)
    evaluate.add_argument("--apply", action="store_true")

    profile = subparsers.add_parser(
        "profile",
        help="Freeze risks, build targeted evidence, apply profiles and evaluate corpus gates.",
    )
    common(profile)
    profile.add_argument("--force-freeze", action="store_true")
    add_model_args(profile)

    run = subparsers.add_parser("run", help="Freeze an explicit corpus when needed and classify it.")
    common(run)
    run.add_argument("--session", action="append", type=Path, default=[])
    run.add_argument("--context", action="append", default=[], metavar="SESSION=TAG[,TAG]")
    run.add_argument("--force-freeze", action="store_true")
    return parser.parse_args()


def add_model_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", type=Path)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="ru")
    parser.add_argument("--beam-size", type=int, default=1)
    parser.add_argument("--padding-sec", type=float, default=0.4)
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--no-cache", action="store_true")


def load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load sibling module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def report_dir(sessions_root: Path, explicit: Path | None) -> Path:
    return explicit.expanduser().resolve() if explicit else sessions_root / "_reports" / REPORT_DIR_NAME


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as output:
        for row in rows:
            output.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) or math.isinf(number) else number


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def interval(row: dict[str, Any]) -> tuple[float, float]:
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    start = safe_float(nested.get("start", row.get("start", row.get("start_sec"))))
    end = safe_float(nested.get("end", row.get("end", row.get("end_sec"))), start)
    return start, max(start, end)


def duration(row: dict[str, Any]) -> float:
    start, end = interval(row)
    nested = row.get("interval") if isinstance(row.get("interval"), dict) else {}
    return max(0.0, safe_float(nested.get("duration_sec"), end - start))


def role_name(row: dict[str, Any]) -> str:
    role = str(row.get("role") or row.get("speaker_label") or "").lower()
    source = str(row.get("source_track") or "").lower()
    if role == "me" or source == "mic":
        return "Me"
    if role in {"remote", "colleagues"} or source == "remote" or "colleague" in role:
        return "Colleagues"
    return str(row.get("speaker_label") or row.get("role") or "Unknown")


def profile_audit_dir(session: Path) -> Path:
    return session / "derived/audit" / PROFILE_DIR_NAME


def profile_report_dir(session: Path) -> Path:
    return session / "derived/transcript-simple/whisper-cpp" / PROFILE_DIR_NAME


def suffix(profile: str) -> str:
    return "" if profile == "current" else f".{profile}"


def profile_paths(session: Path, profile: str) -> dict[str, Path]:
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    profile_suffix = suffix(profile)
    return {
        "dialogue": resolved / f"clean_dialogue{profile_suffix}.json",
        "quality": resolved / f"quality_report{profile_suffix}.json",
        "overlaps": resolved / f"overlaps{profile_suffix}.json",
        "transcript": resolved / f"transcript{profile_suffix}.md",
        "transcript_json": resolved / f"transcript.simple{profile_suffix}.json",
    }


def selected_profile(session: Path) -> str:
    verdict = read_json(session / "derived/synthesis-simple/extractive/quality_verdict.json") or {}
    selected = str(verdict.get("selected_transcript_profile") or "")
    if selected and profile_paths(session, selected)["dialogue"].exists():
        return selected
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    for profile in (
        "residual_local_recall_v1",
        "residual_audio_arbitration_v1",
        "residual_me_evidence_v1",
        "authoritative_boundary_v1",
        "audit_cleanup_v7",
        "agent_reviewed_v1",
        "reviewed_v1",
        "audit_cleanup_v2",
        "shadow_v2",
        "current",
    ):
        if (resolved / f"clean_dialogue{suffix(profile)}.json").exists():
            return profile
    return "missing"


def parse_contexts(values: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for value in values:
        session_id, separator, raw_tags = value.partition("=")
        tags = sorted({item.strip() for item in raw_tags.split(",") if item.strip()})
        if not separator or not session_id.strip() or not tags:
            raise ValueError(f"invalid --context value: {value}")
        result[Path(session_id.strip()).name] = tags
    return result


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "size": path.stat().st_size if path.exists() and path.is_file() else None,
        "sha256": sha256_file(path),
    }


def raw_artifacts(session: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for track in ("mic", "remote"):
        for path in sorted((session / "audio" / track).glob("*.caf")):
            rows.append({"track": track, **artifact(path)})
    return rows


def freeze_session(session: Path, contexts: dict[str, list[str]]) -> tuple[dict[str, Any], list[str]]:
    profile = selected_profile(session)
    paths = profile_paths(session, profile) if profile != "missing" else {}
    failures: list[str] = []
    raw = raw_artifacts(session)
    if not raw or not any(row.get("track") == "mic" for row in raw) or not any(row.get("track") == "remote" for row in raw):
        failures.append("missing_raw_capture")
    if profile == "missing":
        failures.append("missing_selected_profile")
    elif not paths["dialogue"].exists() or not paths["quality"].exists():
        failures.append("missing_selected_profile_artifacts")
    echo_report = session / "derived/preprocess/echo/local_fir_report.json"
    echo_segments = session / "derived/preprocess/echo/local_fir_segments.jsonl"
    if not echo_report.exists() or not echo_segments.exists():
        failures.append("missing_echo_evidence")
    session_manifest = session / "session.json"
    return (
        {
            "session_id": session.name,
            "session": str(session),
            "context_tags": contexts.get(session.name, []),
            "selected_profile": profile,
            "artifacts": {
                "session_manifest": artifact(session_manifest),
                "raw_capture": raw,
                "selected_profile": {name: artifact(path) for name, path in paths.items()},
                "echo_report": artifact(echo_report),
                "echo_segments": artifact(echo_segments),
                "speaker_state": artifact(session / "derived/preprocess/echo/speaker_state.jsonl"),
                "working_audio": {
                    name: artifact(session / "derived/preprocess/audio" / filename)
                    for name, filename in {
                        "mic_raw": "mic_raw_for_asr.wav",
                        "mic_clean": "mic_clean_local_fir.wav",
                        "mic_role_masked": "mic_role_masked_for_asr.wav",
                        "remote": "remote_for_aec.wav",
                    }.items()
                },
            },
        },
        failures,
    )


def freeze_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest_path = output / "baseline_manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"baseline_manifest: {manifest_path}")
        print("status: already_frozen")
        return 0
    if not args.session:
        print("status: explicit --session values are required for the first freeze")
        return 2
    try:
        contexts = parse_contexts(args.context)
    except ValueError as error:
        print(f"status: {error}")
        return 2
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    for value in args.session:
        session = value.expanduser()
        if not session.is_absolute():
            session = (Path.cwd() / session).resolve()
        record, session_failures = freeze_session(session, contexts)
        records.append(record)
        failures.extend(f"{session.name}:{reason}" for reason in session_failures)
        print(f"freeze: {session.name}: profile={record['selected_profile']}", flush=True)
    records.sort(key=lambda row: str(row.get("session_id") or ""))
    manifest = {
        "schema": "murmurmark.speaker_mode_hardening_baseline/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "scope": {
            "session_count": len(records),
            "session_ids": [row["session_id"] for row in records],
            "context_tags": dict(sorted(Counter(tag for row in records for tag in row.get("context_tags") or []).items())),
        },
        "sessions": records,
        "frozen_identity": sha256_bytes(canonical_bytes(records)),
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(manifest_path, manifest)
    print(f"baseline_manifest: {manifest_path}")
    print(f"sessions: {len(records)}")
    print(f"status: {'frozen' if not failures else 'failed_open'}")
    return 0 if not failures else 2


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(math.floor(len(ordered) * quantile))))
    return ordered[index]


def classify_mode(remote_only: list[dict[str, Any]]) -> tuple[str, float, list[str], dict[str, Any]]:
    similarities = [safe_float(row.get("remote_similarity_before")) for row in remote_only]
    count = len(similarities)
    coupled = sum(1 for value in similarities if value >= SPEAKER_P75_MIN)
    coupled_ratio = coupled / count if count else 0.0
    metrics = {
        "remote_only_windows": count,
        "similarity_p50": round(percentile(similarities, 0.50), 6),
        "similarity_p75": round(percentile(similarities, 0.75), 6),
        "similarity_p90": round(percentile(similarities, 0.90), 6),
        "coupled_window_count": coupled,
        "coupled_window_ratio": round(coupled_ratio, 6),
    }
    if count < MIN_REMOTE_ONLY_WINDOWS:
        return "uncertain", 0.0, ["not_enough_remote_only_windows"], metrics
    p75 = metrics["similarity_p75"]
    p90 = metrics["similarity_p90"]
    if coupled_ratio >= SPEAKER_COUPLED_RATIO_MIN and p75 >= SPEAKER_P75_MIN:
        confidence = 0.9 if coupled_ratio >= 0.50 and p75 >= 0.08 else 0.75
        return "speaker_playback", confidence, ["remote_only_windows_show_repeatable_remote_to_mic_coupling"], metrics
    if (
        coupled_ratio <= LOW_LEAK_COUPLED_RATIO_MAX
        and p75 <= LOW_LEAK_P75_MAX
        and p90 <= LOW_LEAK_P90_MAX
    ):
        confidence = 0.9 if coupled_ratio <= 0.10 and p75 <= 0.02 else 0.75
        return "headphones_or_low_leak", confidence, ["remote_only_windows_show_low_remote_to_mic_coupling"], metrics
    return "uncertain", 0.5, ["remote_to_mic_coupling_is_between_calibrated_modes"], metrics


def expected_modes(tags: list[str]) -> list[str]:
    if "headphones" in tags:
        return ["headphones_or_low_leak"]
    if "verified_no_speech" in tags:
        return ["uncertain"]
    if "speakers" in tags:
        # A quiet or well-isolated loudspeaker is acoustically indistinguishable
        # from headphones here. The classifier describes measured coupling, not
        # the physical output device.
        return ["speaker_playback", "headphones_or_low_leak"]
    return []


def classify_session(record: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    session = Path(str(record.get("session") or ""))
    segments_path = session / "derived/preprocess/echo/local_fir_segments.jsonl"
    report_path = session / "derived/preprocess/echo/local_fir_report.json"
    rows = read_jsonl(segments_path)
    report = read_json(report_path) or {}
    failures: list[str] = []
    if not rows:
        failures.append("missing_or_empty_echo_segments")
    remote_only = [row for row in rows if str(row.get("state") or "") == "remote_only"]
    mode, confidence, reasons, metrics = classify_mode(remote_only)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    quality = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    tags = [str(value) for value in record.get("context_tags") or []]
    expected = expected_modes(tags)
    validation = "not_labeled" if not expected else ("matched" if mode in expected else "mismatched")
    if expected and mode not in expected:
        failures.append(f"expected_mode_mismatch:{','.join(expected)}:{mode}")
    payload = {
        "schema": "murmurmark.acoustic_mode_report/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "mode": mode,
        "confidence": confidence,
        "reasons": reasons,
        "context_tags": tags,
        "validation": {"expected_modes": expected, "status": validation},
        "metrics": {
            **metrics,
            "remote_only_median_reduction_db": safe_float(quality.get("remote_only_median_reduction_db")),
            "remote_similarity_before": safe_float(quality.get("remote_similarity_before")),
            "remote_similarity_after": safe_float(quality.get("remote_similarity_after")),
            "local_only_windows": int(safe_float(summary.get("local_only_windows"))),
            "double_talk_windows": int(safe_float(summary.get("double_talk_windows"))),
        },
        "echo_guard": {
            "accepted_for_asr": decision.get("accepted_for_asr"),
            "reason": decision.get("reason"),
            "quality_gate_failures": decision.get("quality_gate_failures") or [],
            "input_conditioning": report.get("input_conditioning"),
        },
        "thresholds": {
            "min_remote_only_windows": MIN_REMOTE_ONLY_WINDOWS,
            "coupled_similarity": SPEAKER_P75_MIN,
            "speaker_coupled_ratio_min": SPEAKER_COUPLED_RATIO_MIN,
            "speaker_p75_min": SPEAKER_P75_MIN,
            "low_leak_coupled_ratio_max": LOW_LEAK_COUPLED_RATIO_MAX,
            "low_leak_p75_max": LOW_LEAK_P75_MAX,
            "low_leak_p90_max": LOW_LEAK_P90_MAX,
        },
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    return payload, failures


def corpus_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Speaker-Mode Acoustic Classification v1",
        "",
        f"Sessions: `{summary.get('session_count')}`",
        f"By mode: `{json.dumps(summary.get('by_mode') or {}, ensure_ascii=False, sort_keys=True)}`",
        f"Labeled validation: `{summary.get('labeled_matches')}/{summary.get('labeled_sessions')}`",
        f"Gates: `{'passed' if (report.get('gates') or {}).get('passed') else 'failed'}`",
        "",
        "| Session | Mode | Confidence | Coupled windows | p75 | Echo accepted |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for row in report.get("sessions") or []:
        metrics = row.get("metrics") or {}
        echo = row.get("echo_guard") or {}
        lines.append(
            f"| `{row.get('session_id')}` | `{row.get('mode')}` | {safe_float(row.get('confidence')):.2f} | "
            f"{safe_float(metrics.get('coupled_window_ratio')):.3f} | {safe_float(metrics.get('similarity_p75')):.3f} | "
            f"`{echo.get('accepted_for_asr')}` |"
        )
    lines.append("")
    return "\n".join(lines)


def classify_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "baseline_manifest.json")
    if not manifest:
        print("status: missing_frozen_baseline")
        return 2
    session_reports: list[dict[str, Any]] = []
    failures: list[str] = []
    for record in manifest.get("sessions") or []:
        if not isinstance(record, dict):
            continue
        payload, session_failures = classify_session(record)
        session_reports.append(payload)
        failures.extend(f"{payload['session_id']}:{reason}" for reason in session_failures)
        session = Path(str(record.get("session") or ""))
        write_json(session / "derived/audit/speaker-mode-hardening-v1/acoustic_mode_report.json", payload)
        print(f"classify: {payload['session_id']}: {payload['mode']} ({payload['confidence']:.2f})", flush=True)
    modes = Counter(str(row.get("mode") or "unknown") for row in session_reports)
    labeled = [row for row in session_reports if (row.get("validation") or {}).get("expected_modes")]
    matches = [row for row in labeled if (row.get("validation") or {}).get("status") == "matched"]
    report = {
        "schema": "murmurmark.acoustic_mode_corpus_report/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "baseline_frozen_identity": manifest.get("frozen_identity"),
        "sessions": session_reports,
        "summary": {
            "session_count": len(session_reports),
            "by_mode": dict(sorted(modes.items())),
            "labeled_sessions": len(labeled),
            "labeled_matches": len(matches),
        },
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(output / "acoustic_mode_corpus_report.json", report)
    (output / "acoustic_mode_corpus_report.md").write_text(corpus_markdown(report), encoding="utf-8")
    print(f"acoustic_mode_corpus_report: {output / 'acoustic_mode_corpus_report.json'}")
    print(f"status: {'passed' if not failures else 'failed_open'}")
    return 0 if not failures else 2


def compact_echo_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": report.get("schema"),
        "profile": report.get("profile"),
        "role_policy": report.get("role_policy"),
        "parameters": report.get("parameters") or {},
        "input_conditioning": report.get("input_conditioning") or {},
        "summary": report.get("summary") or {},
        "metrics": report.get("metrics") or {},
        "decision": report.get("decision") or {},
        "warnings": report.get("warnings") or [],
    }


def frozen_input_identity(record: dict[str, Any]) -> str:
    artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    working = artifacts.get("working_audio") if isinstance(artifacts.get("working_audio"), dict) else {}
    values = {
        key: {"path": row.get("path"), "size": row.get("size"), "sha256": row.get("sha256")}
        for key, row in sorted(working.items())
        if key in {"mic_raw", "remote"} and isinstance(row, dict)
    }
    return sha256_bytes(canonical_bytes(values))


def echo_comparison(baseline: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    candidate_metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    decision = candidate.get("decision") if isinstance(candidate.get("decision"), dict) else {}
    conditioning = candidate.get("input_conditioning") if isinstance(candidate.get("input_conditioning"), dict) else {}
    failures: list[str] = []

    finite = candidate_metrics.get("finite") is True
    peak = safe_float(candidate_metrics.get("max_abs_clean"), default=math.inf)
    local_delta = safe_float(candidate_metrics.get("local_only_energy_delta_db_median"), default=-math.inf)
    local_vad = safe_float(candidate_metrics.get("local_only_vad_duration_ratio"), default=-math.inf)
    local_loss = safe_float(candidate_metrics.get("near_end_speech_loss_ratio"), default=math.inf)
    reduction = safe_float(candidate_metrics.get("remote_only_median_reduction_db"), default=-math.inf)
    baseline_reduction = safe_float(baseline_metrics.get("remote_only_median_reduction_db"), default=reduction)
    remote_similarity_after = safe_float(candidate_metrics.get("remote_similarity_after"), default=math.inf)
    baseline_remote_similarity_after = safe_float(
        baseline_metrics.get("remote_similarity_after"),
        default=remote_similarity_after,
    )
    baseline_local_loss = safe_float(baseline_metrics.get("near_end_speech_loss_ratio"), default=local_loss)
    if not finite:
        failures.append("candidate_not_finite")
    if peak > 1.0:
        failures.append("candidate_peak_out_of_range")
    if not -1.5 <= local_delta <= 1.0:
        failures.append("candidate_local_energy_delta_out_of_range")
    if not 0.90 <= local_vad <= 1.10:
        failures.append("candidate_local_vad_ratio_out_of_range")
    if local_loss > baseline_local_loss + 0.005:
        failures.append("near_end_speech_loss_regressed")
    if reduction + REMOTE_REDUCTION_TOLERANCE_DB < baseline_reduction:
        failures.append("remote_reduction_regressed")
    if remote_similarity_after > baseline_remote_similarity_after + REMOTE_SIMILARITY_TOLERANCE:
        failures.append("remote_residual_similarity_regressed")

    sparse_sources: list[str] = []
    sustained_sources: list[str] = []
    for source in ("mic", "remote"):
        row = conditioning.get(source) if isinstance(conditioning.get(source), dict) else {}
        samples = int(safe_float(row.get("sample_count")))
        sparse_eligible = bool(
            samples
            and safe_float(row.get("duration_ms")) <= safe_float(row.get("max_sparse_duration_ms"), 250.0)
            and safe_float(row.get("fraction")) <= safe_float(row.get("max_sparse_fraction"), 0.001)
        )
        if sparse_eligible:
            sparse_sources.append(source)
            if row.get("applied") is not True:
                failures.append(f"sparse_overrange_not_limited:{source}")
        elif samples:
            sustained_sources.append(source)
            if row.get("applied") is True:
                failures.append(f"sustained_overrange_was_limited:{source}")
    if sparse_sources and safe_float(candidate_metrics.get("peak_scale"), 0.0) < 0.95:
        failures.append("sparse_overrange_still_requires_global_scaling")

    comparison = {
        "accepted_for_asr_before": (baseline.get("decision") or {}).get("accepted_for_asr"),
        "accepted_for_asr_after": decision.get("accepted_for_asr"),
        "reason_before": (baseline.get("decision") or {}).get("reason"),
        "reason_after": decision.get("reason"),
        "remote_reduction_delta_db": round(reduction - baseline_reduction, 6),
        "remote_similarity_after_delta": round(remote_similarity_after - baseline_remote_similarity_after, 6),
        "near_end_speech_loss_delta": round(local_loss - baseline_local_loss, 6),
        "local_energy_delta_db": round(local_delta, 6),
        "local_vad_ratio": round(local_vad, 6),
        "peak_scale": safe_float(candidate_metrics.get("peak_scale")),
        "sparse_overrange_sources": sparse_sources,
        "sustained_overrange_sources": sustained_sources,
        "tolerances": {
            "remote_reduction_db": REMOTE_REDUCTION_TOLERANCE_DB,
            "remote_similarity_after": REMOTE_SIMILARITY_TOLERANCE,
        },
    }
    return comparison, failures


def echo_audit_path(record: dict[str, Any]) -> Path:
    return Path(str(record.get("session") or "")) / "derived/audit/speaker-mode-hardening-v1/echo_guard_candidate_audit.json"


def echo_audit_policy() -> dict[str, Any]:
    return {
        "remote_reduction_tolerance_db": REMOTE_REDUCTION_TOLERANCE_DB,
        "remote_similarity_tolerance": REMOTE_SIMILARITY_TOLERANCE,
        "local_energy_delta_db": [-1.5, 1.0],
        "local_vad_ratio": [0.90, 1.10],
        "near_end_speech_loss_tolerance": 0.005,
        "sparse_peak_scale_min": 0.95,
    }


def run_echo_candidate(record: dict[str, Any], baseline_identity: str, force: bool) -> tuple[dict[str, Any], list[str]]:
    session = Path(str(record.get("session") or ""))
    output_path = echo_audit_path(record)
    helper_sha = sha256_file(ECHO_HELPER)
    input_identity = frozen_input_identity(record)
    cached = read_json(output_path)
    cache_identity_matches = bool(
        not force
        and cached
        and cached.get("baseline_frozen_identity") == baseline_identity
        and cached.get("helper_sha256") == helper_sha
        and cached.get("input_identity") == input_identity
    )
    if cache_identity_matches and cached.get("audit_policy") == echo_audit_policy():
        failures = [str(value) for value in (cached.get("gates") or {}).get("hard_failures") or []]
        return cached, failures
    if cache_identity_matches and isinstance(cached.get("baseline"), dict) and isinstance(cached.get("candidate"), dict):
        comparison, failures = echo_comparison(cached["baseline"], cached["candidate"])
        cached["comparison"] = comparison
        cached["audit_policy"] = echo_audit_policy()
        cached["gates"] = {"passed": not failures, "hard_failures": failures}
        write_json(output_path, cached)
        return cached, failures

    artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
    baseline_path = Path(str((artifacts.get("echo_report") or {}).get("path") or ""))
    baseline = read_json(baseline_path)
    failures: list[str] = []
    if not baseline:
        payload = {
            "schema": "murmurmark.echo_guard_candidate_audit/v1",
            "session_id": session.name,
            "baseline_frozen_identity": baseline_identity,
            "helper_sha256": helper_sha,
            "input_identity": input_identity,
            "audit_policy": echo_audit_policy(),
            "gates": {"passed": False, "hard_failures": ["missing_baseline_echo_report"]},
        }
        write_json(output_path, payload)
        return payload, ["missing_baseline_echo_report"]

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=f"murmurmark-echo-audit-{session.name}-") as value:
        temp = Path(value)
        candidate_report_path = temp / "report.json"
        command = [
            sys.executable,
            str(ECHO_HELPER),
            str(session),
            "--metrics-only",
            "--output-clean",
            str(temp / "clean.wav"),
            "--output-echo",
            str(temp / "echo.wav"),
            "--output-role-mask",
            str(temp / "role.wav"),
            "--output-role-preview",
            str(temp / "preview.wav"),
            "--asr-segments-dir",
            str(temp / "segments"),
            "--report",
            str(candidate_report_path),
            "--segments",
            str(temp / "segments.jsonl"),
            "--speaker-state",
            str(temp / "speaker_state.jsonl"),
        ]
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        candidate = read_json(candidate_report_path)
    if completed.returncode != 0 or not candidate:
        failures.append(f"echo_helper_failed:{completed.returncode}")
        comparison: dict[str, Any] = {}
    else:
        comparison, failures = echo_comparison(baseline, candidate)
    payload = {
        "schema": "murmurmark.echo_guard_candidate_audit/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "baseline_frozen_identity": baseline_identity,
        "helper_sha256": helper_sha,
        "input_identity": input_identity,
        "audit_policy": echo_audit_policy(),
        "runtime_sec": round(time.monotonic() - started, 3),
        "baseline": compact_echo_report(baseline),
        "candidate": compact_echo_report(candidate or {}),
        "comparison": comparison,
        "helper": {
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout.splitlines()[-8:],
            "stderr_tail": completed.stderr.splitlines()[-8:],
        },
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(output_path, payload)
    return payload, failures


def echo_corpus_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Echo Guard Sparse-Overrange Corpus Audit",
        "",
        f"Sessions: `{summary.get('session_count')}`",
        f"Accepted before/after: `{summary.get('accepted_before')}/{summary.get('accepted_after')}`",
        f"Sparse limiter used: `{summary.get('sparse_limited_sessions')}`",
        f"Gates: `{'passed' if (report.get('gates') or {}).get('passed') else 'failed'}`",
        "",
        "| Session | Accepted | Sparse source | Peak scale | Reduction delta | Gate |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for row in report.get("sessions") or []:
        comparison = row.get("comparison") or {}
        lines.append(
            f"| `{row.get('session_id')}` | `{comparison.get('accepted_for_asr_before')} -> "
            f"{comparison.get('accepted_for_asr_after')}` | "
            f"`{','.join(comparison.get('sparse_overrange_sources') or []) or '-'}` | "
            f"{safe_float(comparison.get('peak_scale')):.3f} | "
            f"{safe_float(comparison.get('remote_reduction_delta_db')):+.3f} | "
            f"`{'pass' if (row.get('gates') or {}).get('passed') else 'fail'}` |"
        )
    lines.append("")
    return "\n".join(lines)


def audit_echo_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "baseline_manifest.json")
    if not manifest:
        print("status: missing_frozen_baseline")
        return 2
    requested = {Path(value).name for value in args.session}
    records = [
        row
        for row in manifest.get("sessions") or []
        if isinstance(row, dict) and (not requested or str(row.get("session_id") or "") in requested)
    ]
    if requested and len(records) != len(requested):
        found = {str(row.get("session_id") or "") for row in records}
        print(f"status: sessions_not_in_frozen_baseline:{','.join(sorted(requested - found))}")
        return 2
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    baseline_identity = str(manifest.get("frozen_identity") or "")
    for record in records:
        row, row_failures = run_echo_candidate(record, baseline_identity, args.force)
        rows.append(row)
        failures.extend(f"{row.get('session_id')}:{reason}" for reason in row_failures)
        comparison = row.get("comparison") or {}
        print(
            f"echo: {row.get('session_id')}: accepted={comparison.get('accepted_for_asr_after')} "
            f"peak_scale={safe_float(comparison.get('peak_scale')):.3f} "
            f"gate={'pass' if not row_failures else 'fail'}",
            flush=True,
        )
    rows.sort(key=lambda row: str(row.get("session_id") or ""))
    latest_speaker = next(
        (
            row
            for row in reversed(rows)
            if "speakers"
            in next(
                (
                    record.get("context_tags") or []
                    for record in records
                    if record.get("session_id") == row.get("session_id")
                ),
                [],
            )
        ),
        None,
    )
    if latest_speaker:
        latest_comparison = latest_speaker.get("comparison") or {}
        if latest_comparison.get("accepted_for_asr_after") is not True:
            reason = latest_comparison.get("reason_after") or "unknown"
            failures.append(f"{latest_speaker.get('session_id')}:latest_speaker_candidate_not_accepted:{reason}")
    report = {
        "schema": "murmurmark.echo_guard_candidate_corpus_audit/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "baseline_frozen_identity": baseline_identity,
        "sessions": rows,
        "summary": {
            "session_count": len(rows),
            "accepted_before": sum(1 for row in rows if (row.get("comparison") or {}).get("accepted_for_asr_before") is True),
            "accepted_after": sum(1 for row in rows if (row.get("comparison") or {}).get("accepted_for_asr_after") is True),
            "sparse_limited_sessions": sum(1 for row in rows if (row.get("comparison") or {}).get("sparse_overrange_sources")),
            "latest_speaker_session": latest_speaker.get("session_id") if latest_speaker else None,
        },
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    path = output / "echo_guard_corpus_report.json"
    write_json(path, report)
    (output / "echo_guard_corpus_report.md").write_text(echo_corpus_markdown(report), encoding="utf-8")
    print(f"echo_guard_corpus_report: {path}")
    print(f"status: {'passed' if not failures else 'failed_open'}")
    return 0 if not failures else 2


def me_remote_ids(overlap: dict[str, Any]) -> tuple[str, str]:
    me_id = str(overlap.get("me_utterance_id") or "")
    remote_id = str(overlap.get("remote_utterance_id") or "")
    if not me_id:
        if str(overlap.get("left_role") or "").lower() == "me":
            me_id = str(overlap.get("left_utterance_id") or "")
            remote_id = remote_id or str(overlap.get("right_utterance_id") or "")
        elif str(overlap.get("right_role") or "").lower() == "me":
            me_id = str(overlap.get("right_utterance_id") or "")
            remote_id = remote_id or str(overlap.get("left_utterance_id") or "")
    return me_id, remote_id


def risk_id(session_id: str, source: str, me_ids: list[str], start: float, end: float) -> str:
    identity = {"session_id": session_id, "source": source, "me_ids": me_ids, "start": start, "end": end}
    return f"smh_{sha256_bytes(canonical_bytes(identity))[:16]}"


def strongest_audio_review(session: Path, me_id: str, start: float, end: float) -> dict[str, Any]:
    rows = read_jsonl(session / "derived/audit/audio-review-pack/audio_review_audit.jsonl")
    matching = []
    for row in rows:
        row_ids = {str(value) for value in row.get("utterance_ids") or []}
        row_start, row_end = interval(row)
        overlap_sec = max(0.0, min(end, row_end) - max(start, row_start))
        if me_id in row_ids or overlap_sec >= min(0.5, max(0.05, (end - start) * 0.5)):
            classification = row.get("classification") if isinstance(row.get("classification"), dict) else {}
            matching.append((safe_float(classification.get("confidence")), row))
    return max(matching, key=lambda item: item[0])[1] if matching else {}


def duplicate_risks_for_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    session = Path(str(record.get("session") or ""))
    profile = str(record.get("selected_profile") or "missing")
    paths = profile_paths(session, profile)
    dialogue = read_json(paths["dialogue"]) or {}
    overlaps_payload = read_json(paths["overlaps"]) or {}
    utterances = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    by_id = {str(row.get("id") or ""): row for row in utterances}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for overlap in overlaps_payload.get("overlaps") or []:
        if not isinstance(overlap, dict) or safe_float(overlap.get("text_similarity")) < DUPLICATE_TEXT_SIMILARITY:
            continue
        me_id, _remote_id = me_remote_ids(overlap)
        if me_id and me_id in by_id:
            grouped.setdefault(me_id, []).append(overlap)
    risks: list[dict[str, Any]] = []
    for me_id, overlap_rows in sorted(grouped.items()):
        me = by_id[me_id]
        start = min(safe_float(row.get("start")) for row in overlap_rows)
        end = max(safe_float(row.get("end"), start) for row in overlap_rows)
        remote_ids = sorted({me_remote_ids(row)[1] for row in overlap_rows if me_remote_ids(row)[1]})
        remote_text = " ".join(str(by_id.get(item, {}).get("text") or "").strip() for item in remote_ids).strip()
        source_detail = strongest_audio_review(session, me_id, start, end)
        whole_duration = max(0.001, safe_float(me.get("end")) - safe_float(me.get("start")))
        risks.append(
            {
                "schema": "murmurmark.speaker_mode_risk/v1",
                "session_id": session.name,
                "residual_queue_id": risk_id(session.name, "remote_duplicate", [me_id], start, end),
                "source": "remote_duplicate",
                "source_audit_id": overlap_rows[0].get("id") or f"duplicate_{me_id}",
                "input_profile": profile,
                "interval": {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration_sec": round(max(0.0, end - start), 3),
                },
                "whole_utterance": {
                    "start": round(safe_float(me.get("start")), 3),
                    "end": round(safe_float(me.get("end")), 3),
                    "coverage_ratio": round(max(0.0, end - start) / whole_duration, 6),
                },
                "utterance_ids": [me_id, *remote_ids],
                "me_utterance_ids": [me_id],
                "remote_utterance_ids": remote_ids,
                "target_text": me.get("text") or "",
                "remote_text": remote_text,
                "source_detail": source_detail,
                "source_overlap_rows": overlap_rows,
            }
        )
    return risks


def unresolved_residual_audio_rows(sessions_root: Path, speaker_ids: set[str]) -> list[dict[str, Any]]:
    path = sessions_root / "_reports/residual-audio-arbitration-v1/residual_audio_queue.jsonl"
    rows = []
    for row in read_jsonl(path):
        if str(row.get("session_id") or "") not in speaker_ids:
            continue
        value = copy.deepcopy(row)
        value["schema"] = "murmurmark.speaker_mode_risk/v1"
        value["source"] = "remote_duplicate"
        rows.append(value)
    return rows


def freeze_chronology_rows(sessions_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pattern = "*/derived/transcript-simple/whisper-cpp/residual-me-evidence-v1/residual_me_review_queue.jsonl"
    for path in sorted(sessions_root.glob(pattern)):
        session = path.parents[4]
        order_items = {
            str(row.get("item_id") or ""): row
            for row in read_jsonl(session / "derived/audit/order/transcript_order_items.jsonl")
        }
        for source_row in read_jsonl(path):
            if str(source_row.get("source") or "") != "transcript_order":
                continue
            queue_id = str(source_row.get("residual_queue_id") or "")
            key = (session.name, queue_id)
            if key in seen:
                continue
            seen.add(key)
            detail = order_items.get(str(source_row.get("source_audit_id") or ""), {})
            utterances = detail.get("utterances") if isinstance(detail.get("utterances"), dict) else {}
            me = utterances.get("me") if isinstance(utterances.get("me"), dict) else {}
            remote = utterances.get("remote") if isinstance(utterances.get("remote"), dict) else {}
            value = copy.deepcopy(source_row)
            value.update(
                {
                    "schema": "murmurmark.speaker_mode_risk/v1",
                    "source": "transcript_order",
                    "input_profile": selected_profile(session),
                    "utterance_ids": [item for item in (me.get("id"), remote.get("id")) if item],
                    "me_utterance_ids": [me.get("id")] if me.get("id") else [],
                    "remote_utterance_ids": [remote.get("id")] if remote.get("id") else [],
                    "target_text": me.get("text") or "",
                    "remote_text": remote.get("text") or "",
                    "source_detail": detail,
                }
            )
            rows.append(value)
    return sorted(rows, key=lambda row: (str(row.get("session_id") or ""), interval(row)[0]))


def freeze_local_recall_rows(sessions_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = "*/derived/transcript-simple/whisper-cpp/residual-local-recall-v1/residual_local_recall_review_queue.jsonl"
    for path in sorted(sessions_root.glob(pattern)):
        session = path.parents[4]
        for source_row in read_jsonl(path):
            value = copy.deepcopy(source_row)
            value["schema"] = "murmurmark.speaker_mode_risk/v1"
            value["source"] = "local_recall"
            value["input_profile"] = selected_profile(session)
            rows.append(value)
    return sorted(rows, key=lambda row: (str(row.get("session_id") or ""), interval(row)[0]))


def merge_audio_risks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        session_id = str(row.get("session_id") or "")
        me_ids = [str(value) for value in row.get("me_utterance_ids") or [] if value]
        key = (session_id, me_ids[0] if len(me_ids) == 1 else str(row.get("residual_queue_id") or ""))
        current = by_key.get(key)
        if current is None:
            by_key[key] = copy.deepcopy(row)
            continue
        current_start, current_end = interval(current)
        row_start, row_end = interval(row)
        current["interval"] = {
            "start": round(min(current_start, row_start), 3),
            "end": round(max(current_end, row_end), 3),
            "duration_sec": round(max(current_end, row_end) - min(current_start, row_start), 3),
        }
        current["remote_utterance_ids"] = sorted(
            {str(value) for value in (current.get("remote_utterance_ids") or []) + (row.get("remote_utterance_ids") or []) if value}
        )
        if not current.get("source_detail") and row.get("source_detail"):
            current["source_detail"] = row.get("source_detail")
    return sorted(by_key.values(), key=lambda row: (str(row.get("session_id") or ""), interval(row)[0]))


def profile_record(session: Path, profile: str, contexts: list[str] | None = None) -> dict[str, Any]:
    paths = profile_paths(session, profile)
    return {
        "session_id": session.name,
        "session": str(session),
        "input_profile": profile,
        "context_tags": contexts or [],
        "artifacts": {
            "dialogue": artifact(paths["dialogue"]),
            "quality": artifact(paths["quality"]),
            "overlaps": artifact(paths["overlaps"]),
            "raw_capture": raw_artifacts(session),
        },
    }


def freeze_profile_risks(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "profile_baseline_manifest.json"
    if path.exists() and not args.force:
        print(f"profile_baseline_manifest: {path}")
        print("status: already_frozen")
        return 0
    baseline = read_json(output / "baseline_manifest.json")
    acoustic = read_json(output / "acoustic_mode_corpus_report.json")
    echo = read_json(output / "echo_guard_corpus_report.json")
    if not baseline or (baseline.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_baseline")
        return 2
    if not acoustic or (acoustic.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_acoustic_classification")
        return 2
    if not echo or (echo.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_echo_audit")
        return 2
    speaker_records = [row for row in baseline.get("sessions") or [] if isinstance(row, dict)]
    speaker_ids = {str(row.get("session_id") or "") for row in speaker_records}
    duplicate_rows = merge_audio_risks(
        unresolved_residual_audio_rows(sessions_root, speaker_ids)
        + [risk for record in speaker_records for risk in duplicate_risks_for_record(record)]
    )
    chronology_rows = freeze_chronology_rows(sessions_root)
    local_rows = freeze_local_recall_rows(sessions_root)
    records = [
        profile_record(
            Path(str(row.get("session") or "")),
            str(row.get("selected_profile") or "missing"),
            [str(value) for value in row.get("context_tags") or []],
        )
        for row in speaker_records
    ]
    known = {str(row.get("session_id") or "") for row in records}
    for session_id in sorted({str(row.get("session_id") or "") for row in chronology_rows} - known):
        session = sessions_root / session_id
        records.append(profile_record(session, selected_profile(session), ["chronology_regression_only"]))
    failures: list[str] = []
    for record in records:
        artifacts = record.get("artifacts") if isinstance(record.get("artifacts"), dict) else {}
        if not (artifacts.get("dialogue") or {}).get("sha256"):
            failures.append(f"{record.get('session_id')}:missing_input_dialogue")
        raw = artifacts.get("raw_capture") if isinstance(artifacts.get("raw_capture"), list) else []
        if not raw or any(not row.get("sha256") for row in raw):
            failures.append(f"{record.get('session_id')}:missing_raw_capture_fingerprint")
    write_jsonl(output / "duplicate_risk_queue.jsonl", duplicate_rows)
    write_jsonl(output / "chronology_risk_queue.jsonl", chronology_rows)
    write_jsonl(output / "local_recall_risk_queue.jsonl", local_rows)
    evidence_rows = duplicate_rows + chronology_rows
    write_jsonl(output / "evidence_queue.jsonl", evidence_rows)
    records.sort(key=lambda row: str(row.get("session_id") or ""))
    manifest = {
        "schema": "murmurmark.speaker_mode_profile_baseline/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "speaker_corpus_identity": baseline.get("frozen_identity"),
        "sessions": records,
        "queues": {
            "duplicate": {"items": len(duplicate_rows), "seconds": round(sum(duration(row) for row in duplicate_rows), 3)},
            "chronology": {"items": len(chronology_rows), "seconds": round(sum(duration(row) for row in chronology_rows), 3)},
            "local_recall": {"items": len(local_rows), "seconds": round(sum(duration(row) for row in local_rows), 3)},
            "evidence_sha256": sha256_bytes(canonical_bytes(evidence_rows)),
        },
        "frozen_identity": sha256_bytes(canonical_bytes(records + evidence_rows + local_rows)),
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(path, manifest)
    print(f"profile_baseline_manifest: {path}")
    print(f"duplicate_risks: {len(duplicate_rows)}")
    print(f"chronology_risks: {len(chronology_rows)}")
    print(f"local_recall_risks: {len(local_rows)}")
    print(f"status: {'frozen' if not failures else 'failed_open'}")
    return 0 if not failures else 2


def resolved_model_path(args: argparse.Namespace) -> Path:
    explicit = args.model or (Path(os.environ["MURMURMARK_FASTER_WHISPER_MODEL"]) if os.environ.get("MURMURMARK_FASTER_WHISPER_MODEL") else None)
    return (explicit or DEFAULT_MODEL).expanduser().resolve()


def transcript_words(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        word
        for segment in payload.get("segments") or []
        if isinstance(segment, dict)
        for word in segment.get("words") or []
        if isinstance(word, dict) and str(word.get("word") or "").strip()
    ]


def normalized_tokens(text: Any) -> list[str]:
    import re

    return [value.lower().replace("ё", "е") for value in re.findall(r"[0-9A-Za-zА-Яа-яЁё_./+-]+", str(text or ""))]


def chronology_patch(evidence: dict[str, Any], queue_row: dict[str, Any], audio_module: ModuleType) -> dict[str, Any] | None:
    target = str(queue_row.get("target_text") or "").strip()
    remote = str(queue_row.get("remote_text") or "").strip()
    original_tokens = normalized_tokens(target)
    if len(original_tokens) < 2:
        return None
    whole = (evidence.get("intervals") or {}).get("whole") or {}
    whole_start = safe_float(whole.get("start"))
    remote_start, remote_end = interval(queue_row)
    choices: list[tuple[float, str, dict[str, Any]]] = []
    for key, payload in (evidence.get("micro_asr") or {}).items():
        if not key.startswith(("mic_clean:whole", "mic_raw:whole", "mic_role_masked:whole")):
            continue
        words = transcript_words(payload)
        decoded_tokens = normalized_tokens(" ".join(str(word.get("word") or "") for word in words))
        if not decoded_tokens:
            continue
        similarity = SequenceMatcher(None, original_tokens, decoded_tokens).ratio()
        choices.append((similarity, key, payload))
    if not choices:
        return None
    similarity, source, payload = max(choices, key=lambda item: item[0])
    words = transcript_words(payload)
    decoded_tokens = [normalized_tokens(word.get("word")) for word in words]
    flat_tokens = [tokens[0] if tokens else "" for tokens in decoded_tokens]
    matcher = SequenceMatcher(None, original_tokens, flat_tokens)
    mapping: dict[int, dict[str, Any]] = {}
    for block in matcher.get_matching_blocks():
        for offset in range(block.size):
            original_index = block.a + offset
            word = words[block.b + offset]
            mapping[original_index] = {
                "start": whole_start + safe_float(word.get("start")),
                "end": whole_start + safe_float(word.get("end")),
            }
    coverage = len(mapping) / max(1, len(original_tokens))
    if similarity < 0.82 or coverage < 0.80:
        return None
    mapped = [mapping[index] for index in sorted(mapping)]
    aligned_start = min(value["start"] for value in mapped)
    aligned_end = max(value["end"] for value in mapped)
    guard = 0.12
    if aligned_end <= remote_start - guard or aligned_start >= remote_end + guard:
        return {
            "action": "retime_lossless",
            "start": round(aligned_start, 3),
            "end": round(aligned_end, 3),
            "source": source,
            "alignment_similarity": round(similarity, 6),
            "token_coverage": round(coverage, 6),
        }
    before = [index for index, value in mapping.items() if value["end"] <= remote_start - guard]
    after = [index for index, value in mapping.items() if value["start"] >= remote_end + guard]
    middle = [index for index, value in mapping.items() if index not in before and index not in after]
    if before and after and not middle and max(before) < min(after):
        split_index = min(after)
        if 0 < split_index < len(original_tokens):
            return {
                "action": "split_lossless",
                "split_token_index": split_index,
                "pre_start": round(min(mapping[index]["start"] for index in before), 3),
                "pre_end": round(max(mapping[index]["end"] for index in before), 3),
                "post_start": round(min(mapping[index]["start"] for index in after), 3),
                "post_end": round(max(mapping[index]["end"] for index in after), 3),
                "source": source,
                "alignment_similarity": round(similarity, 6),
                "token_coverage": round(coverage, 6),
                "remote_text_similarity": round(
                    safe_float(audio_module.text_similarity(target, remote).get("similarity")), 6
                ),
            }
    return None


def profile_decision(evidence: dict[str, Any], queue_row: dict[str, Any], audio_module: ModuleType) -> dict[str, Any]:
    source = str(queue_row.get("source") or "")
    base = evidence.get("decision") if isinstance(evidence.get("decision"), dict) else {}
    checks = base.get("checks") if isinstance(base.get("checks"), dict) else {}
    state = evidence.get("speaker_state") if isinstance(evidence.get("speaker_state"), dict) else {}
    calibration = evidence.get("calibration") if isinstance(evidence.get("calibration"), dict) else {}
    thresholds = calibration.get("thresholds") if isinstance(calibration.get("thresholds"), dict) else {}
    calibration_reliable = calibration.get("status") == "reliable" and calibration.get("required_evidence_ready", True) is True
    weak_threshold = safe_float(thresholds.get("weak"), 1.0)
    keep_threshold = safe_float(thresholds.get("keep"), 1.0)
    mic_scores = [
        safe_float(value.get("positive_similarity"))
        for key, value in (evidence.get("voice_scores") or {}).items()
        if key.startswith(("mic_clean:", "mic_raw:", "mic_role_masked:")) and isinstance(value, dict)
    ]
    best_mic = max(mic_scores, default=0.0)
    all_mic_below_weak = bool(mic_scores) and all(score < weak_threshold for score in mic_scores)
    local_support = min(1.0, safe_float(state.get("local_only_ratio")) + 0.75 * safe_float(state.get("double_talk_ratio")))
    remote_active = safe_float(state.get("remote_active_ratio"))
    double_talk = safe_float(state.get("double_talk_ratio"))
    judge = evidence.get("stronger_audio_judge") if isinstance(evidence.get("stronger_audio_judge"), dict) else {}
    judge_class = judge.get("classification") if isinstance(judge.get("classification"), dict) else {}
    judge_label = str(judge_class.get("label") or "")
    judge_confidence = safe_float(judge_class.get("confidence"))
    detail = queue_row.get("source_detail") if isinstance(queue_row.get("source_detail"), dict) else {}
    detail_class = detail.get("classification") if isinstance(detail.get("classification"), dict) else {}
    detail_label = str(detail_class.get("label") or "")
    detail_confidence = safe_float(detail_class.get("confidence"))
    protected = bool(checks.get("protected_content"))
    unique_tokens = [str(value) for value in checks.get("unique_target_tokens") or []]
    target_text = str(queue_row.get("target_text") or "")
    remote_text = str(queue_row.get("remote_text") or "")
    remote_match = audio_module.text_similarity(remote_text, target_text)
    coverage = safe_float((queue_row.get("whole_utterance") or {}).get("coverage_ratio"), 1.0)
    independent_duplicate = (
        detail_label in {"remote_duplicate", "remote_leak"} and detail_confidence >= 0.90
    ) or (judge_label == "confirm_remote_duplicate" and judge_confidence >= 0.90)
    independent_noise = judge_label == "confirm_asr_noise" and judge_confidence >= 0.90

    if source == "transcript_order":
        patch = chronology_patch(evidence, queue_row, audio_module)
        if patch:
            return {
                "outcome": "timing_overlap",
                "action": patch["action"],
                "reason": "word_timestamps_prove_a_lossless_boundary",
                "confidence": min(0.98, 0.80 + safe_float(patch.get("alignment_similarity")) * 0.18),
                "chronology_patch": patch,
            }
        if (
            calibration_reliable
            and best_mic >= keep_threshold
            and local_support >= 0.25
            and remote_active >= 0.20
            and double_talk >= 0.12
            and safe_float(remote_match.get("similarity")) < DUPLICATE_TEXT_SIMILARITY
        ):
            return {
                "outcome": "real_double_talk",
                "action": "close_as_double_talk",
                "reason": "target_me_and_remote_activity_confirm_real_simultaneous_speech",
                "confidence": min(0.97, 0.72 + best_mic * 0.15 + local_support * 0.10),
            }
        return {
            "outcome": "insufficient_evidence",
            "action": "needs_review",
            "reason": "chronology_cannot_be_changed_without_lossless_word_boundary_evidence",
            "confidence": max(safe_float(base.get("confidence")), judge_confidence),
        }

    if base.get("action") == "keep_me" and base.get("outcome") == "genuine_me":
        return {
            "outcome": "genuine_me",
            "action": "keep_me",
            "reason": str(base.get("reason") or "independent_target_me_evidence"),
            "confidence": safe_float(base.get("confidence")),
        }
    if base.get("outcome") == "real_double_talk" and (
        checks.get("voice_confirmed") is True or (judge_label == "confirm_timing_or_doubletalk" and judge_confidence >= 0.85)
    ):
        return {
            "outcome": "real_double_talk",
            "action": "keep_me",
            "reason": "independent_evidence_confirms_target_me_during_remote_activity",
            "confidence": max(0.85, safe_float(base.get("confidence")), judge_confidence),
        }
    if (
        len(queue_row.get("me_utterance_ids") or []) == 1
        and calibration_reliable
        and all_mic_below_weak
        and local_support <= 0.12
        and coverage >= 0.80
        and safe_float(remote_match.get("similarity")) >= 0.78
        and safe_float(remote_match.get("containment")) >= 0.75
        and not unique_tokens
        and not protected
        and independent_duplicate
    ):
        return {
            "outcome": "remote_duplicate_or_leak",
            "action": "drop_duplicate_or_noise",
            "reason": "whole_me_is_remote_text_without_calibrated_target_me_support",
            "confidence": min(0.99, max(detail_confidence, judge_confidence, 0.90)),
        }
    if (
        len(queue_row.get("me_utterance_ids") or []) == 1
        and calibration_reliable
        and all_mic_below_weak
        and local_support <= 0.08
        and coverage >= 0.80
        and independent_noise
        and len(audio_module.content_tokens(target_text)) <= 3
        and not protected
    ):
        return {
            "outcome": "asr_noise",
            "action": "drop_duplicate_or_noise",
            "reason": "whole_short_me_is_not_supported_by_target_voice_or_mic_asr",
            "confidence": min(0.97, judge_confidence),
        }
    probable = "remote_duplicate_or_leak" if independent_duplicate else ("asr_noise" if independent_noise else "insufficient_evidence")
    return {
        "outcome": probable,
        "action": "needs_review",
        "reason": "independent_evidence_is_weak_or_conflicting",
        "confidence": max(safe_float(base.get("confidence")), judge_confidence, detail_confidence),
    }


def augment_whole_transcripts(
    session: Path,
    rows: list[dict[str, Any]],
    queue_by_id: dict[str, dict[str, Any]],
    model: Any | None,
    args: argparse.Namespace,
    audio_module: ModuleType,
) -> list[dict[str, Any]]:
    for row in rows:
        queue_row = queue_by_id.get(str(row.get("residual_queue_id") or ""), {})
        if str(queue_row.get("source") or "") != "transcript_order":
            continue
        transcripts = row.get("micro_asr") if isinstance(row.get("micro_asr"), dict) else {}
        for key, clip_info in (row.get("clips") or {}).items():
            if not key.endswith(":whole") or not key.startswith(("mic_clean:", "mic_raw:", "mic_role_masked:")):
                continue
            if key not in transcripts:
                transcripts[key] = audio_module.transcribe_clip_with_words(
                    session, model, Path(str(clip_info.get("path") or "")), args
                )
        row["micro_asr"] = transcripts
    return rows


def legacy_micro_asr_by_clip(session: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    paths = (
        session / "derived/audit/residual-audio-arbitration-v1/residual_audio_evidence.jsonl",
        session / "derived/audit/residual-me-evidence-v1/residual_me_evidence.jsonl",
        session / "derived/audit/audio-review-pack/faster_whisper_judge.jsonl",
    )

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            identity = value.get("identity") if isinstance(value.get("identity"), dict) else {}
            clip_sha = str(identity.get("clip_sha256") or "")
            if not clip_sha and value.get("path") and isinstance(value.get("segments"), list):
                clip_sha = str(sha256_file(Path(str(value.get("path")))) or "")
            if clip_sha and isinstance(value.get("segments"), list):
                result.setdefault(clip_sha, copy.deepcopy(value))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for path in paths:
        for row in read_jsonl(path):
            visit(row)
    return result


def build_profile_evidence(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "profile_baseline_manifest.json")
    queue = read_jsonl(output / "evidence_queue.jsonl")
    if not manifest or not queue or (manifest.get("gates") or {}).get("passed") is not True:
        print("status: missing_or_failed_profile_baseline")
        return 2
    audio_module = load_sibling("residual-audio-arbitration.py", "murmurmark_speaker_mode_audio")
    audio_module.PROFILE = PROFILE
    audio_module.audit_dir = profile_audit_dir
    # Exact intervals are sufficient for role arbitration. Whole-utterance
    # windows are decoded separately only for chronology, avoiding a second
    # decode of every duplicate candidate.
    audio_module.speaker_bounded_interval = lambda *_args, **_kwargs: None
    model_path = resolved_model_path(args)
    model_bin = model_path / "model.bin" if model_path.is_dir() else model_path
    if not args.allow_download:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    model = audio_module.LazyWhisperModel(model_path, args) if model_bin.exists() else None
    backend, backend_status = audio_module.BASE.target_module().resolve_embedding_backend(
        SimpleNamespace(method="resemblyzer_dvector", wavlm_model=None)
    )
    ready, reason = backend.ready()
    if not ready:
        backend = None
        backend_status = {**backend_status, "ready": False, "reason": reason}
    wanted = {path.expanduser().resolve().name for path in args.sessions} if args.sessions else set()
    records = {str(row.get("session_id") or ""): row for row in manifest.get("sessions") or []}
    failures: list[str] = []
    session_reports: list[dict[str, Any]] = []
    for session_id in sorted({str(row.get("session_id") or "") for row in queue}):
        if wanted and session_id not in wanted:
            continue
        record = records.get(session_id)
        if not record:
            failures.append(f"{session_id}:missing_profile_record")
            continue
        session = sessions_root / session_id
        session_rows = [row for row in queue if str(row.get("session_id") or "") == session_id]
        input_profile = str(record.get("input_profile") or "missing")
        audio_module.INPUT_PROFILE = input_profile
        legacy_cache = legacy_micro_asr_by_clip(session)
        original_transcribe = audio_module.transcribe_clip_with_words

        def transcribe_with_legacy_cache(
            cache_session: Path,
            cache_model: Any | None,
            clip: Path,
            cache_args: argparse.Namespace,
        ) -> dict[str, Any]:
            clip_sha = sha256_file(clip)
            cached = legacy_cache.get(str(clip_sha or ""))
            if cached:
                return copy.deepcopy(cached)
            return original_transcribe(cache_session, cache_model, clip, cache_args)

        audio_module.transcribe_clip_with_words = transcribe_with_legacy_cache
        dialogue_sha = ((record.get("artifacts") or {}).get("dialogue") or {}).get("sha256")
        adapter = {
            "artifacts": {"dialogue": {"sha256": dialogue_sha}},
            "input_review_queue": {"audio_review_count": len(session_rows)},
        }
        started = time.monotonic()
        summary = audio_module.build_session_evidence(
            session, session_rows, adapter, model, backend, backend_status, args
        )
        evidence_path = profile_audit_dir(session) / "residual_audio_evidence.jsonl"
        queue_by_id = {str(row.get("residual_queue_id") or ""): row for row in session_rows}
        evidence_rows = augment_whole_transcripts(
            session, read_jsonl(evidence_path), queue_by_id, model, args, audio_module
        )
        for evidence in evidence_rows:
            queue_row = queue_by_id.get(str(evidence.get("residual_queue_id") or ""), {})
            evidence["schema"] = "murmurmark.speaker_mode_evidence/v1"
            evidence["speaker_mode_decision"] = profile_decision(evidence, queue_row, audio_module)
            evidence["profile_provenance_sha256"] = sha256_bytes(canonical_bytes(evidence))
        write_jsonl(profile_audit_dir(session) / "speaker_mode_evidence.jsonl", evidence_rows)
        audio_module.transcribe_clip_with_words = original_transcribe
        session_evidence_ok = len(evidence_rows) == len(session_rows) and all(
            str((row.get("speaker_mode_decision") or {}).get("outcome") or "") in PROFILE_OUTCOMES
            for row in evidence_rows
        )
        summary["gates"] = {
            "passed": session_evidence_ok,
            "hard_failures": [] if session_evidence_ok else ["not_all_frozen_rows_have_valid_profile_evidence"],
        }
        write_json(profile_audit_dir(session) / "residual_audio_evidence_summary.json", summary)
        if not session_evidence_ok:
            failures.append(f"{session_id}:evidence_failed_open")
        session_reports.append(
            {
                "session_id": session_id,
                "risk_items": len(session_rows),
                "runtime_sec": round(time.monotonic() - started, 3),
                "gates_passed": session_evidence_ok,
            }
        )
        print(f"evidence: {session_id}: {len(evidence_rows)}/{len(session_rows)}", flush=True)
    report = {
        "schema": "murmurmark.speaker_mode_evidence_corpus/v1",
        "profile": PROFILE,
        "model": str(model_path),
        "model_available": model is not None,
        "target_me_backend": backend_status,
        "sessions": session_reports,
        "summary": {
            "session_count": len(session_reports),
            "risk_items": sum(safe_int(row.get("risk_items")) for row in session_reports),
            "runtime_sec": round(sum(safe_float(row.get("runtime_sec")) for row in session_reports), 3),
        },
        "gates": {"passed": not failures, "hard_failures": failures},
    }
    write_json(output / "evidence_corpus_report.json", report)
    print(f"evidence_corpus_report: {output / 'evidence_corpus_report.json'}")
    return 0 if not failures else 2


def raw_capture_matches(record: dict[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    raw = ((record.get("artifacts") or {}).get("raw_capture") or [])
    for item in raw:
        path = Path(str(item.get("path") or ""))
        if not path.is_file() or path.stat().st_size != safe_int(item.get("size")) or sha256_file(path) != item.get("sha256"):
            failures.append(str(path))
    return not failures, failures


def output_fingerprint(paths: dict[str, Path]) -> str:
    return sha256_bytes(
        canonical_bytes({name: sha256_file(path) for name, path in sorted(paths.items()) if path.exists()})
    )


def split_text_lossless(text: str, token_index: int) -> tuple[str, str] | None:
    import re

    matches = list(re.finditer(r"[0-9A-Za-zА-Яа-яЁё_./+-]+", text))
    if token_index <= 0 or token_index >= len(matches):
        return None
    cut = matches[token_index].start()
    left = text[:cut].strip()
    right = text[cut:].strip()
    if not left or not right:
        return None
    if normalized_tokens(f"{left} {right}") != normalized_tokens(text):
        return None
    return left, right


def annotate_profile(row: dict[str, Any], risk: dict[str, Any], evidence: dict[str, Any], closed: bool) -> dict[str, Any]:
    result = copy.deepcopy(row)
    quality = copy.deepcopy(result.get("quality")) if isinstance(result.get("quality"), dict) else {}
    values = quality.get("speaker_mode_hardening") if isinstance(quality.get("speaker_mode_hardening"), list) else []
    decision = evidence.get("speaker_mode_decision") if isinstance(evidence.get("speaker_mode_decision"), dict) else {}
    values.append(
        {
            "profile": PROFILE,
            "risk_id": risk.get("residual_queue_id"),
            "source": risk.get("source"),
            "outcome": decision.get("outcome"),
            "action": decision.get("action"),
            "reason": decision.get("reason"),
            "confidence": decision.get("confidence"),
            "closed": closed,
            "provenance_sha256": evidence.get("profile_provenance_sha256"),
        }
    )
    quality["speaker_mode_hardening"] = values
    result["quality"] = quality
    return result


def has_confirmed_me_evidence(row: dict[str, Any]) -> bool:
    """Recognize only explicit prior keep/confirm decisions, never absence of a warning."""

    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            action = str(value.get("action") or value.get("suggested_decision") or "")
            label = str(value.get("label") or "")
            confidence = safe_float(value.get("confidence"))
            closed = value.get("closed")
            if action == "keep_me" and (closed is True or confidence >= 0.85):
                return True
            if label == "confirm_me" and confidence >= 0.85:
                return True
            return any(visit(child) for child in value.values())
        if isinstance(value, list):
            return any(visit(child) for child in value)
        return False

    return visit(row.get("quality") or {})


def apply_chronology_patch(
    row: dict[str, Any], risk: dict[str, Any], evidence: dict[str, Any]
) -> tuple[list[dict[str, Any]] | None, str | None]:
    decision = evidence.get("speaker_mode_decision") if isinstance(evidence.get("speaker_mode_decision"), dict) else {}
    patch = decision.get("chronology_patch") if isinstance(decision.get("chronology_patch"), dict) else {}
    action = str(decision.get("action") or "")
    if action == "retime_lossless":
        result = annotate_profile(row, risk, evidence, True)
        start = safe_float(patch.get("start"))
        end = safe_float(patch.get("end"), start)
        if end <= start:
            return None, "invalid_retime_interval"
        result["start"] = round(start, 3)
        result["end"] = round(end, 3)
        result["source_start"] = result["start"]
        result["source_end"] = result["end"]
        return [result], None
    if action == "split_lossless":
        parts = split_text_lossless(str(row.get("text") or ""), safe_int(patch.get("split_token_index")))
        if not parts:
            return None, "lossless_text_split_failed"
        pre_text, post_text = parts
        pre = annotate_profile(row, risk, evidence, True)
        post = annotate_profile(row, risk, evidence, True)
        pre["id"] = f"{row.get('id')}__speaker_pre"
        post["id"] = f"{row.get('id')}__speaker_post"
        pre["text"] = pre_text
        post["text"] = post_text
        pre["start"] = safe_float(patch.get("pre_start"))
        pre["end"] = safe_float(patch.get("pre_end"), pre["start"])
        post["start"] = safe_float(patch.get("post_start"))
        post["end"] = safe_float(patch.get("post_end"), post["start"])
        for value in (pre, post):
            value["source_start"] = value["start"]
            value["source_end"] = value["end"]
        if pre["end"] <= pre["start"] or post["end"] <= post["start"]:
            return None, "invalid_split_intervals"
        if normalized_tokens(f"{pre['text']} {post['text']}") != normalized_tokens(row.get("text")):
            return None, "split_changed_token_sequence"
        return [pre, post], None
    return None, "unsupported_chronology_action"


def apply_profile_session(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    manifest = read_json(output / "profile_baseline_manifest.json")
    evidence_queue = read_jsonl(output / "evidence_queue.jsonl")
    local_queue = read_jsonl(output / "local_recall_risk_queue.jsonl")
    if not manifest:
        print("status: missing_profile_baseline")
        return 2
    session = args.session.expanduser().resolve()
    record = next(
        (row for row in manifest.get("sessions") or [] if str(row.get("session_id") or "") == session.name),
        None,
    )
    if not record:
        print(f"status: session_not_in_profile_baseline:{session.name}")
        return 2
    input_profile = str(record.get("input_profile") or "missing")
    input_paths = profile_paths(session, input_profile)
    output_paths = profile_paths(session, PROFILE)
    dialogue = read_json(input_paths["dialogue"])
    input_quality = read_json(input_paths["quality"])
    risks = [row for row in evidence_queue if str(row.get("session_id") or "") == session.name]
    evidence_rows = read_jsonl(profile_audit_dir(session) / "speaker_mode_evidence.jsonl")
    evidence_by_id = {str(row.get("residual_queue_id") or ""): row for row in evidence_rows}
    failures: list[str] = []
    frozen_sha = ((record.get("artifacts") or {}).get("dialogue") or {}).get("sha256")
    if not dialogue or not input_quality:
        failures.append("missing_input_profile")
    if sha256_file(input_paths["dialogue"]) != frozen_sha:
        failures.append("frozen_input_dialogue_changed")
    raw_ok, raw_failures = raw_capture_matches(record)
    if not raw_ok:
        failures.append("raw_capture_changed")
    if risks and len(evidence_by_id) != len(risks):
        failures.append("not_all_frozen_risks_have_evidence")
    if failures:
        report = {
            "schema": "murmurmark.speaker_mode_hardening_profile_report/v1",
            "session_id": session.name,
            "status": "failed_open",
            "gates": {"passed": False, "hard_failures": failures, "raw_capture_failures": raw_failures},
        }
        write_json(profile_report_dir(session) / "speaker_mode_hardening_report.json", report)
        print("status: failed_open")
        return 2
    assert dialogue is not None and input_quality is not None
    input_utterances = [copy.deepcopy(row) for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    output_by_id = {str(row.get("id") or ""): copy.deepcopy(row) for row in input_utterances}
    dropped_ids: set[str] = set()
    replacements: dict[str, list[dict[str, Any]]] = {}
    benign_ids: set[str] = set()
    closed_ids: set[str] = set()
    dispositions: list[dict[str, Any]] = []
    for risk in risks:
        risk_id_value = str(risk.get("residual_queue_id") or "")
        evidence = evidence_by_id.get(risk_id_value, {})
        decision = evidence.get("speaker_mode_decision") if isinstance(evidence.get("speaker_mode_decision"), dict) else {}
        action = str(decision.get("action") or "needs_review")
        me_ids = [str(value) for value in risk.get("me_utterance_ids") or [] if value]
        closed = False
        applied_action = action
        rejection: str | None = None
        if len(me_ids) == 1 and me_ids[0] in output_by_id:
            me_id = me_ids[0]
            if action == "drop_duplicate_or_noise":
                if has_confirmed_me_evidence(output_by_id[me_id]):
                    rejection = "prior_confirmed_me_evidence_protects_utterance"
                    applied_action = "needs_review"
                    output_by_id[me_id] = annotate_profile(output_by_id[me_id], risk, evidence, False)
                else:
                    dropped_ids.add(me_id)
                    closed = True
            elif action in {"keep_me", "close_as_double_talk"}:
                output_by_id[me_id] = annotate_profile(output_by_id[me_id], risk, evidence, True)
                benign_ids.add(me_id)
                closed = True
            elif action in {"retime_lossless", "split_lossless"}:
                replacement, rejection = apply_chronology_patch(output_by_id[me_id], risk, evidence)
                if replacement:
                    replacements[me_id] = replacement
                    closed = True
                else:
                    applied_action = "needs_review"
            else:
                output_by_id[me_id] = annotate_profile(output_by_id[me_id], risk, evidence, False)
        else:
            rejection = "whole_me_utterance_not_available"
            applied_action = "needs_review"
        if closed:
            closed_ids.add(risk_id_value)
        dispositions.append(
            {
                "schema": "murmurmark.speaker_mode_disposition/v1",
                "session_id": session.name,
                "risk_id": risk_id_value,
                "source": risk.get("source"),
                "interval": risk.get("interval"),
                "me_utterance_ids": me_ids,
                "outcome": decision.get("outcome"),
                "action": applied_action,
                "reason": rejection or decision.get("reason"),
                "confidence": decision.get("confidence"),
                "closed": closed,
                "provenance_sha256": evidence.get("profile_provenance_sha256"),
            }
        )
    output_utterances: list[dict[str, Any]] = []
    for original in input_utterances:
        utterance_id = str(original.get("id") or "")
        if utterance_id in dropped_ids:
            continue
        if utterance_id in replacements:
            output_utterances.extend(replacements[utterance_id])
        else:
            output_utterances.append(output_by_id[utterance_id])
    output_utterances.sort(key=lambda row: (safe_float(row.get("start")), safe_float(row.get("end")), 0 if role_name(row) == "Colleagues" else 1))
    order_module = load_sibling("apply-transcript-order-repair.py", "murmurmark_speaker_mode_order")
    overlaps = order_module.build_overlaps(output_utterances)
    legacy_duplicates = [row for row in overlaps if safe_float(row.get("text_similarity")) >= DUPLICATE_TEXT_SIMILARITY]
    harmful_duplicates = [
        row
        for row in legacy_duplicates
        if str(row.get("me_utterance_id") or row.get("left_utterance_id") or "") not in benign_ids
    ]
    session_local = [row for row in local_queue if str(row.get("session_id") or "") == session.name]
    remaining = [row for row in dispositions if row.get("closed") is not True]
    summary = {
        "risk_items": len(risks),
        "risk_seconds": round(sum(duration(row) for row in risks), 3),
        "closed_items": len(dispositions) - len(remaining),
        "closed_seconds": round(
            sum(duration(risk) for risk in risks if str(risk.get("residual_queue_id") or "") in closed_ids), 3
        ),
        "remaining_items": len(remaining),
        "remaining_seconds": round(
            sum(duration(risk) for risk in risks if str(risk.get("residual_queue_id") or "") not in closed_ids), 3
        ),
        "preserved_local_recall_items": len(session_local),
        "preserved_local_recall_seconds": round(sum(duration(row) for row in session_local), 3),
        "dropped_me_items": len(dropped_ids),
        "confirmed_benign_me_items": len(benign_ids),
        "chronology_patches": len(replacements),
        "by_outcome": dict(sorted(Counter(str(row.get("outcome") or "unknown") for row in dispositions).items())),
    }
    output_quality = order_module.quality_report(input_quality, output_utterances, overlaps, summary)
    output_quality["legacy_remote_duplicate_in_me_count"] = len(legacy_duplicates)
    output_quality["legacy_remote_duplicate_in_me_seconds"] = round(sum(duration(row) for row in legacy_duplicates), 3)
    # The active metric must remain paired with the frozen input profile. A
    # detector recomputation can use different overlap geometry and must not
    # make an unresolved risk disappear. Only closed, evidence-backed
    # duplicate rows may reduce the frozen value.
    closed_duplicate_risks = [
        risk
        for risk in risks
        if str(risk.get("source") or "") == "remote_duplicate"
        and str(risk.get("residual_queue_id") or "") in closed_ids
    ]
    closed_duplicate_seconds = sum(duration(row) for row in closed_duplicate_risks)
    closed_duplicate_items = len(closed_duplicate_risks)
    output_quality["remote_duplicate_in_me_count"] = max(
        0,
        safe_int(input_quality.get("remote_duplicate_in_me_count")) - closed_duplicate_items,
    )
    output_quality["remote_duplicate_in_me_seconds"] = round(
        max(0.0, safe_float(input_quality.get("remote_duplicate_in_me_seconds")) - closed_duplicate_seconds),
        3,
    )
    output_quality["speaker_mode_harmful_overlap_detector_count"] = len(harmful_duplicates)
    output_quality["speaker_mode_harmful_overlap_detector_seconds"] = round(
        sum(duration(row) for row in harmful_duplicates), 3
    )
    output_quality["speaker_mode_hardening"] = summary
    output_quality["profile"] = PROFILE
    output_dialogue = {
        "schema": "murmurmark.clean_dialogue/v1",
        "session": dialogue.get("session", session.name),
        "utterances": output_utterances,
    }
    input_remote = [row for row in input_utterances if role_name(row) == "Colleagues"]
    output_remote = [row for row in output_utterances if role_name(row) == "Colleagues"]
    input_roles = {str(row.get("id") or ""): role_name(row) for row in input_utterances}
    changed_roles = [
        str(row.get("id") or "")
        for row in output_utterances
        if str(row.get("id") or "") in input_roles and role_name(row) != input_roles[str(row.get("id") or "")]
    ]
    hard_failures: list[str] = []
    if input_remote != output_remote:
        hard_failures.append("remote_utterances_changed")
    if changed_roles:
        hard_failures.append("existing_utterance_roles_changed")
    if any(str(row.get("outcome") or "") not in PROFILE_OUTCOMES for row in dispositions):
        hard_failures.append("invalid_profile_outcome")
    raw_ok_after, raw_after_failures = raw_capture_matches(record)
    if not raw_ok_after:
        hard_failures.append("raw_capture_changed_after_apply")
    gates = {"passed": not hard_failures, "hard_failures": hard_failures, "raw_capture_failures": raw_after_failures}
    if args.mode == "conservative" and gates["passed"]:
        write_json(output_paths["dialogue"], output_dialogue)
        write_json(output_paths["quality"], output_quality)
        write_json(output_paths["overlaps"], {"schema": "murmurmark.transcript_overlaps/v1", "session": session.name, "overlaps": overlaps})
        write_json(
            output_paths["transcript_json"],
            {
                "schema": "murmurmark.transcript_simple/v1",
                "session": dialogue.get("session", session.name),
                "backend": "whisper.cpp",
                "utterances": [
                    {**copy.deepcopy(row), "raw_text": row.get("text"), "corrected_text": row.get("text"), "corrections": []}
                    for row in output_utterances
                ],
            },
        )
        transcribe_report = read_json(input_paths["dialogue"].parent / "transcribe_simple_report.json") or {}
        order_module.write_markdown(output_paths["transcript"], output_utterances, transcribe_report.get("model"), transcribe_report.get("language"))
    report = {
        "schema": "murmurmark.speaker_mode_hardening_profile_report/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "session_id": session.name,
        "input_profile": input_profile,
        "output_profile": PROFILE,
        "status": "ok" if gates["passed"] else "failed_open",
        "summary": summary,
        "inputs": {
            "frozen_dialogue_sha256": frozen_sha,
            "evidence_sha256": sha256_file(profile_audit_dir(session) / "speaker_mode_evidence.jsonl"),
        },
        "gates": gates,
    }
    output_dir = profile_report_dir(session)
    write_jsonl(output_dir / "speaker_mode_dispositions.jsonl", dispositions)
    write_jsonl(output_dir / "speaker_mode_review_queue.jsonl", remaining)
    write_jsonl(output_dir / "local_recall_review_queue.jsonl", session_local)
    write_json(
        output_dir / "speaker_mode_diff.json",
        {
            "schema": "murmurmark.speaker_mode_hardening_diff/v1",
            "session_id": session.name,
            "dropped_ids": sorted(dropped_ids),
            "benign_ids": sorted(benign_ids),
            "replaced_ids": sorted(replacements),
            "remote_unchanged": input_remote == output_remote,
            "changed_role_ids": changed_roles,
        },
    )
    if args.mode == "conservative" and gates["passed"]:
        report["output_fingerprint"] = output_fingerprint(output_paths)
    write_json(output_dir / "speaker_mode_hardening_report.json", report)
    print(f"profile_report: {output_dir / 'speaker_mode_hardening_report.json'}")
    print(f"closed_items: {summary['closed_items']}/{summary['risk_items']}")
    return 0 if gates["passed"] else 2


def order_summary(
    dialogue: dict[str, Any], order_module: ModuleType, overlap_module: ModuleType
) -> dict[str, Any]:
    args = SimpleNamespace(min_overlap_sec=0.5, long_me_sec=6.0, tail_sec=0.8)
    rows = [row for row in dialogue.get("utterances") or [] if isinstance(row, dict)]
    # Rebuild both baseline and candidate overlaps with the same implementation.
    # Comparing a frozen legacy overlap file with a freshly generated file can
    # report a chronology regression even when the dialogue is unchanged.
    overlap_rows = overlap_module.build_overlaps(rows)
    return order_module.summarize(order_module.build_items(rows, overlap_rows, args))


def process_runtime(session: Path) -> float:
    payload = read_json(session / "derived/pipeline-run/pipeline_run_report.json") or {}
    return sum(
        safe_float(row.get("duration_sec"))
        for row in payload.get("steps") or []
        if isinstance(row, dict) and row.get("status") == "passed"
    )


def evaluate_profile_corpus(args: argparse.Namespace) -> int:
    sessions_root = args.sessions_root.expanduser().resolve()
    output = report_dir(sessions_root, args.out_dir)
    profile_manifest = read_json(output / "profile_baseline_manifest.json")
    speaker_manifest = read_json(output / "baseline_manifest.json")
    evidence_report = read_json(output / "evidence_corpus_report.json") or {}
    if not profile_manifest or not speaker_manifest:
        print("status: missing_profile_or_speaker_baseline")
        return 2
    records = [row for row in profile_manifest.get("sessions") or [] if isinstance(row, dict)]
    if args.apply:
        for record in records:
            session = Path(str(record.get("session") or ""))
            code = apply_profile_session(
                SimpleNamespace(
                    sessions_root=sessions_root,
                    out_dir=args.out_dir,
                    session=session,
                    mode="conservative",
                )
            )
            if code != 0:
                print(f"warning: profile apply failed open: {session.name}", file=sys.stderr)
    speaker_ids = {str(row.get("session_id") or "") for row in speaker_manifest.get("sessions") or []}
    duplicate_queue = read_jsonl(output / "duplicate_risk_queue.jsonl")
    chronology_queue = read_jsonl(output / "chronology_risk_queue.jsonl")
    local_queue = read_jsonl(output / "local_recall_risk_queue.jsonl")
    order_module = load_sibling("audit-transcript-order.py", "murmurmark_speaker_mode_order_audit")
    overlap_module = load_sibling("apply-transcript-order-repair.py", "murmurmark_speaker_mode_order_overlap")
    hard_failures: list[str] = []
    sessions: list[dict[str, Any]] = []
    baseline_duplicate_seconds = 0.0
    candidate_duplicate_seconds = 0.0
    baseline_process_runtime = 0.0
    evidence_runtime_by_session = {
        str(row.get("session_id") or ""): safe_float(row.get("runtime_sec"))
        for row in evidence_report.get("sessions") or []
        if isinstance(row, dict)
    }
    evidence_runtime = 0.0
    total_closed_items = 0
    total_closed_seconds = 0.0
    total_remaining_items = 0
    total_remaining_seconds = 0.0
    disposition_actions: Counter[str] = Counter()
    disposition_outcomes: Counter[str] = Counter()
    disposition_reasons: Counter[str] = Counter()
    unresolved_reasons: Counter[str] = Counter()
    disposition_sources: Counter[str] = Counter()
    disposition_seconds_by_action: Counter[str] = Counter()
    disposition_seconds_by_source: Counter[str] = Counter()
    latest_speaker_id = next(
        (
            str(row.get("session_id") or "")
            for row in reversed(speaker_manifest.get("sessions") or [])
            if "speakers" in (row.get("context_tags") or [])
        ),
        "",
    )
    latest_row: dict[str, Any] | None = None
    for record in records:
        session_id = str(record.get("session_id") or "")
        session = Path(str(record.get("session") or ""))
        input_profile = str(record.get("input_profile") or "missing")
        input_paths = profile_paths(session, input_profile)
        candidate_paths = profile_paths(session, PROFILE)
        report = read_json(profile_report_dir(session) / "speaker_mode_hardening_report.json")
        if not report or (report.get("gates") or {}).get("passed") is not True:
            hard_failures.append(f"{session_id}:profile_gates_failed")
            continue
        if report.get("output_fingerprint") != output_fingerprint(candidate_paths):
            hard_failures.append(f"{session_id}:output_fingerprint_mismatch")
        raw_ok, raw_failures = raw_capture_matches(record)
        if not raw_ok:
            hard_failures.append(f"{session_id}:raw_capture_changed")
        input_dialogue = read_json(input_paths["dialogue"]) or {}
        output_dialogue = read_json(candidate_paths["dialogue"]) or {}
        input_quality = read_json(input_paths["quality"]) or {}
        output_quality = read_json(candidate_paths["quality"]) or {}
        input_remote = [row for row in input_dialogue.get("utterances") or [] if role_name(row) == "Colleagues"]
        output_remote = [row for row in output_dialogue.get("utterances") or [] if role_name(row) == "Colleagues"]
        if input_remote != output_remote:
            hard_failures.append(f"{session_id}:remote_content_changed")
        before_recall = safe_float(input_quality.get("local_only_island_recall"))
        after_recall = safe_float(output_quality.get("local_only_island_recall"))
        if after_recall + 1e-6 < before_recall:
            hard_failures.append(f"{session_id}:local_recall_regressed:{before_recall}->{after_recall}")
        before_order = order_summary(input_dialogue, order_module, overlap_module)
        after_order = order_summary(output_dialogue, order_module, overlap_module)
        if safe_int(after_order.get("probable_order_risk_count")) > safe_int(before_order.get("probable_order_risk_count")):
            hard_failures.append(f"{session_id}:chronology_count_regressed")
        if safe_float(after_order.get("probable_order_risk_seconds")) > safe_float(before_order.get("probable_order_risk_seconds")) + 1e-6:
            hard_failures.append(f"{session_id}:chronology_seconds_regressed")
        summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
        risk_by_id = {
            str(row.get("residual_queue_id") or ""): row
            for row in duplicate_queue + chronology_queue
            if str(row.get("session_id") or "") == session_id
        }
        for disposition in read_jsonl(profile_report_dir(session) / "speaker_mode_dispositions.jsonl"):
            action = str(disposition.get("action") or "unknown")
            outcome = str(disposition.get("outcome") or "unknown")
            reason = str(disposition.get("reason") or "unknown")
            source = str(disposition.get("source") or "unknown")
            risk_duration = duration(risk_by_id.get(str(disposition.get("risk_id") or ""), disposition))
            disposition_actions[action] += 1
            disposition_outcomes[outcome] += 1
            disposition_reasons[reason] += 1
            if action == "needs_review":
                unresolved_reasons[reason] += 1
            disposition_sources[source] += 1
            disposition_seconds_by_action[action] += risk_duration
            disposition_seconds_by_source[source] += risk_duration
        total_closed_items += safe_int(summary.get("closed_items"))
        total_closed_seconds += safe_float(summary.get("closed_seconds"))
        total_remaining_items += safe_int(summary.get("remaining_items"))
        total_remaining_seconds += safe_float(summary.get("remaining_seconds"))
        if session_id in speaker_ids:
            baseline_duplicate_seconds += safe_float(input_quality.get("remote_duplicate_in_me_seconds"))
            candidate_duplicate_seconds += safe_float(output_quality.get("remote_duplicate_in_me_seconds"))
            baseline_process_runtime += process_runtime(session)
            evidence_runtime += evidence_runtime_by_session.get(session_id, 0.0)
        first_fingerprint = output_fingerprint(candidate_paths)
        repeat_code = apply_profile_session(
            SimpleNamespace(
                sessions_root=sessions_root,
                out_dir=args.out_dir,
                session=session,
                mode="conservative",
            )
        )
        deterministic = repeat_code == 0 and first_fingerprint == output_fingerprint(candidate_paths)
        if not deterministic:
            hard_failures.append(f"{session_id}:profile_not_deterministic")
        row = {
            "session_id": session_id,
            "speaker_corpus": session_id in speaker_ids,
            "input_profile": input_profile,
            "local_recall_before": before_recall,
            "local_recall_after": after_recall,
            "remote_duplicate_seconds_before": safe_float(input_quality.get("remote_duplicate_in_me_seconds")),
            "remote_duplicate_seconds_after": safe_float(output_quality.get("remote_duplicate_in_me_seconds")),
            "order_risk_count_before": safe_int(before_order.get("probable_order_risk_count")),
            "order_risk_count_after": safe_int(after_order.get("probable_order_risk_count")),
            "order_risk_seconds_before": safe_float(before_order.get("probable_order_risk_seconds")),
            "order_risk_seconds_after": safe_float(after_order.get("probable_order_risk_seconds")),
            "summary": summary,
            "raw_capture_unchanged": raw_ok,
            "raw_capture_failures": raw_failures,
            "deterministic": deterministic,
        }
        sessions.append(row)
        if session_id == latest_speaker_id:
            latest_row = row
    queue_items = len(duplicate_queue) + len(chronology_queue) + len(local_queue)
    queue_seconds = sum(duration(row) for row in duplicate_queue + chronology_queue + local_queue)
    remaining_items = total_remaining_items + len(local_queue)
    remaining_seconds = total_remaining_seconds + sum(duration(row) for row in local_queue)
    duplicate_reduction = (
        (baseline_duplicate_seconds - candidate_duplicate_seconds) / baseline_duplicate_seconds
        if baseline_duplicate_seconds > 0
        else 0.0
    )
    review_reduction = (queue_seconds - remaining_seconds) / queue_seconds if queue_seconds > 0 else 0.0
    runtime_ratio = evidence_runtime / baseline_process_runtime if baseline_process_runtime > 0 else 0.0
    if duplicate_reduction + 1e-9 < 0.25:
        hard_failures.append(f"aggregate_remote_duplicate_reduction_below_25_percent:{duplicate_reduction:.6f}")
    if review_reduction + 1e-9 < 0.15:
        hard_failures.append(f"mandatory_review_burden_reduction_below_15_percent:{review_reduction:.6f}")
    if runtime_ratio > 0.25 + 1e-9:
        hard_failures.append(f"additional_runtime_above_25_percent:{runtime_ratio:.6f}")
    latest_failures: list[str] = []
    if not latest_row:
        latest_failures.append("latest_speaker_session_missing")
    else:
        if safe_float(latest_row.get("local_recall_after")) + 1e-6 < 0.849:
            latest_failures.append("local_recall_below_0_849")
        if safe_float(latest_row.get("remote_duplicate_seconds_after")) > 1.08 + 1e-6:
            latest_failures.append("remote_duplicate_above_1_08_seconds")
        if safe_int(latest_row.get("order_risk_count_after")) > 2:
            latest_failures.append("order_risk_count_above_2")
        if safe_float(latest_row.get("order_risk_seconds_after")) > 10.2 + 1e-6:
            latest_failures.append("order_risk_seconds_above_10_2")
    hard_failures.extend(f"{latest_speaker_id}:{reason}" for reason in latest_failures)
    hard_failures = sorted(set(hard_failures))
    decision = "PROMOTE_SPEAKER_MODE_HARDENING_V1" if not hard_failures else "DO_NOT_PROMOTE"
    evidence_limit = {
        "safe_drop_candidates": disposition_actions.get("drop_duplicate_or_noise", 0),
        "proven_actions": dict(sorted(disposition_actions.items())),
        "proven_action_seconds": {
            key: round(value, 3) for key, value in sorted(disposition_seconds_by_action.items())
        },
        "outcomes": dict(sorted(disposition_outcomes.items())),
        "decision_reasons": dict(sorted(disposition_reasons.items())),
        "unresolved_reasons": dict(sorted(unresolved_reasons.items())),
        "risk_sources": dict(sorted(disposition_sources.items())),
        "risk_source_seconds": {
            key: round(value, 3) for key, value in sorted(disposition_seconds_by_source.items())
        },
        "preserved_local_recall_items": len(local_queue),
        "preserved_local_recall_seconds": round(sum(duration(row) for row in local_queue), 3),
        "interpretation": (
            "No whole Me utterance satisfied the independent remote-duplicate/noise deletion gates. "
            "Most duplicate candidates mix remote-supported text with unique or protected Me content; "
            "the remaining chronology rows lack a lossless word-boundary proof."
        ),
    }
    report = {
        "schema": "murmurmark.speaker_mode_hardening_corpus_report/v1",
        "generator": {"name": "speaker-mode-hardening", "version": SCRIPT_VERSION},
        "profile": PROFILE,
        "decision": decision,
        "baseline_frozen_identity": profile_manifest.get("frozen_identity"),
        "summary": {
            "speaker_session_count": len(speaker_ids),
            "evaluated_session_count": len(sessions),
            "baseline_remote_duplicate_seconds": round(baseline_duplicate_seconds, 3),
            "candidate_remote_duplicate_seconds": round(candidate_duplicate_seconds, 3),
            "remote_duplicate_reduction_ratio": round(duplicate_reduction, 6),
            "mandatory_review_items_before": queue_items,
            "mandatory_review_items_after": remaining_items,
            "mandatory_review_seconds_before": round(queue_seconds, 3),
            "mandatory_review_seconds_after": round(remaining_seconds, 3),
            "mandatory_review_reduction_ratio": round(review_reduction, 6),
            "closed_items": total_closed_items,
            "closed_seconds": round(total_closed_seconds, 3),
            "baseline_process_runtime_sec": round(baseline_process_runtime, 3),
            "additional_evidence_runtime_sec": round(evidence_runtime, 3),
            "additional_runtime_ratio": round(runtime_ratio, 6),
            "latest_speaker_session": latest_speaker_id,
        },
        "latest_speaker_gate": {"session": latest_speaker_id, "row": latest_row, "failures": latest_failures},
        "evidence_limit": evidence_limit,
        "gates": {
            "passed": not hard_failures,
            "hard_failures": hard_failures,
            "thresholds": {
                "remote_duplicate_reduction_ratio_min": 0.25,
                "mandatory_review_reduction_ratio_min": 0.15,
                "additional_runtime_ratio_max": 0.25,
            },
        },
        "sessions": sessions,
    }
    write_json(output / "speaker_mode_hardening_corpus_report.json", report)
    lines = [
        "# Speaker-Mode Transcript Quality Hardening v1",
        "",
        f"- Decision: `{decision}`",
        f"- Duplicate/leak seconds: `{baseline_duplicate_seconds:.3f} -> {candidate_duplicate_seconds:.3f}` "
        f"(`{duplicate_reduction:.1%}` reduction)",
        f"- Mandatory review seconds: `{queue_seconds:.3f} -> {remaining_seconds:.3f}` "
        f"(`{review_reduction:.1%}` reduction)",
        f"- Additional runtime: `{evidence_runtime:.3f}s` / `{runtime_ratio:.1%}` of frozen batch runtime",
        f"- Gates: `{'passed' if not hard_failures else 'failed'}`",
        "",
    ]
    if hard_failures:
        lines.extend(
            [
                "## Evidence Limit",
                "",
                *[f"- `{failure}`" for failure in hard_failures],
                "",
                f"- Proven actions: `{json.dumps(evidence_limit['proven_actions'], sort_keys=True)}`",
                f"- Action seconds: `{json.dumps(evidence_limit['proven_action_seconds'], sort_keys=True)}`",
                f"- Unresolved reasons: `{json.dumps(evidence_limit['unresolved_reasons'], sort_keys=True)}`",
                f"- Safe whole-Me drops: `{evidence_limit['safe_drop_candidates']}`",
                f"- Preserved local-recall queue: `{len(local_queue)}` rows / "
                f"`{sum(duration(row) for row in local_queue):.3f}s`",
                "",
                str(evidence_limit["interpretation"]),
                "",
            ]
        )
    (output / "speaker_mode_hardening_corpus_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"corpus_report: {output / 'speaker_mode_hardening_corpus_report.json'}")
    print(f"decision: {decision}")
    print(f"duplicate_reduction: {duplicate_reduction:.1%}")
    print(f"review_reduction: {review_reduction:.1%}")
    return 0 if not hard_failures else 2


def run_profile(args: argparse.Namespace) -> int:
    code = freeze_profile_risks(
        SimpleNamespace(sessions_root=args.sessions_root, out_dir=args.out_dir, force=args.force_freeze)
    )
    if code != 0:
        return code
    code = build_profile_evidence(args)
    if code != 0:
        return code
    return evaluate_profile_corpus(
        SimpleNamespace(sessions_root=args.sessions_root, out_dir=args.out_dir, apply=True)
    )


def main() -> int:
    args = parse_args()
    if args.command == "freeze":
        return freeze_corpus(args)
    if args.command == "classify":
        return classify_corpus(args)
    if args.command == "audit-echo":
        return audit_echo_corpus(args)
    if args.command == "freeze-risks":
        return freeze_profile_risks(args)
    if args.command == "evidence":
        return build_profile_evidence(args)
    if args.command == "apply":
        return apply_profile_session(args)
    if args.command == "evaluate":
        return evaluate_profile_corpus(args)
    if args.command == "profile":
        return run_profile(args)
    if args.command == "run":
        freeze_args = argparse.Namespace(
            sessions_root=args.sessions_root,
            out_dir=args.out_dir,
            session=args.session,
            context=args.context,
            force=args.force_freeze,
        )
        code = freeze_corpus(freeze_args)
        if code != 0:
            return code
        return classify_corpus(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
