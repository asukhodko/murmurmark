#!/usr/bin/env python3
"""Build a read-only, fingerprint-bound rebaseline of current transcript surfaces."""

from __future__ import annotations

import argparse
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
POLICY_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_policy/v1"
INPUT_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_input/v1"
REPORT_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_report/v1"
SESSION_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_session/v1"
RESIDUAL_SCHEMA = "murmurmark.post_segmentation_transcript_residual/v1"
ARTIFACT_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_artifacts/v1"
REPLAY_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_replay/v1"
SNAPSHOT_SCHEMA = "murmurmark.post_segmentation_transcript_rebaseline_snapshot/v1"
DEFAULT_POLICY = ROOT / "policies/post-segmentation-transcript-rebaseline-v1.json"
DEFAULT_OUT = ROOT / "sessions/_reports/post-segmentation-transcript-rebaseline-v1"
DEFAULT_SNAPSHOT = ROOT / "docs/testing/post-segmentation-transcript-rebaseline-v1-snapshot.json"
SELECTION = Path("derived/transcript-rich/speaker-resolved-default-v1/selection.json")
PROVISIONAL = Path(
    "derived/transcript-rich/speaker-resolved-default-v1/provisional/selection.json"
)
READINESS = Path("derived/readiness/session_readiness.json")
CAPTURE = Path("derived/audit/capture-continuity/capture_continuity_report.json")
ORDER = Path("derived/audit/order/transcript_order_audit.json")
REVIEW_PLAN = Path("derived/readiness/review-plan/review_plan.json")
REVIEW_PROGRESS = Path("derived/readiness/review-plan/review_decisions_progress.json")
SESSION_ID_PATTERN = re.compile(r"\b20\d\d-\d\d-\d\d[_T]\d\d[-:]\d\d[-:]\d\d")
SESSION_DIR_PATTERN = re.compile(r"^20\d\d-\d\d-\d\d_\d\d-\d\d-\d\d(?:-[a-z0-9_-]+)?$")


class RebaselineError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze and verify fresh selected/provisional/aggregate transcript surfaces without "
            "rewriting session artifacts."
        )
    )
    parser.add_argument("scope", nargs="?", choices=("all",), default="all")
    parser.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--refresh", action="store_true", help="Replace the private frozen input manifest.")
    parser.add_argument(
        "--verify-existing",
        action="store_true",
        help="Rebuild in memory and require byte-identical existing public outputs.",
    )
    parser.add_argument("--write-snapshot", action="store_true")
    return parser.parse_args()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def canonical_jsonl(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode() for row in rows
    )


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RebaselineError(f"cannot_read_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise RebaselineError(f"expected_json_object:{path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repo_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT.resolve()))
    except ValueError:
        return str(resolved)


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


def identity(path: Path, *, display: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"path": display or str(path.resolve()), "exists": path.is_file()}
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return row


def identity_matches(expected: Any, path: Path) -> bool:
    if not isinstance(expected, dict) or bool(expected.get("exists")) != path.is_file():
        return False
    if not path.is_file():
        return True
    return bool(
        int(expected.get("bytes") or -1) == path.stat().st_size
        and expected.get("sha256") == sha256_file(path)
    )


def resolve_session_path(session: Path, row: Any) -> Path | None:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return None
    raw = Path(row["path"])
    candidate = raw if raw.is_absolute() else session / raw
    try:
        candidate.resolve().relative_to(session.resolve())
    except ValueError:
        return None
    return candidate.resolve()


def selection_artifact(session: Path, selection: dict[str, Any], key: str) -> Path | None:
    return resolve_session_path(session, selection.get(key))


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != POLICY_SCHEMA:
        raise RebaselineError(f"unsupported_policy_schema:{policy.get('schema')}")
    if policy.get("safety", {}).get("read_only_sessions") is not True:
        raise RebaselineError("policy_must_be_read_only")
    if policy.get("safety", {}).get("aggregate_quality_score") is not False:
        raise RebaselineError("aggregate_quality_score_must_remain_disabled")
    controls = policy.get("controls") or []
    if not controls:
        raise RebaselineError("policy_controls_missing")
    ids = [str(row.get("id") or "") for row in controls if isinstance(row, dict)]
    if not all(ids) or len(ids) != len(set(ids)):
        raise RebaselineError("policy_control_ids_invalid")


def discover_sessions(sessions_root: Path, policy: dict[str, Any]) -> list[Path]:
    discovery = policy.get("discovery") or {}
    minimum_name = str(discovery.get("minimum_session_name") or "")
    required = [Path(str(value)) for value in discovery.get("required_files") or []]
    candidates = [
        path
        for path in sessions_root.iterdir()
        if path.is_dir()
        and SESSION_DIR_PATTERN.fullmatch(path.name)
        and path.name >= minimum_name
        and all((path / relative).is_file() for relative in required)
    ]
    maximum = int(discovery.get("maximum_sessions") or len(candidates))
    return sorted(candidates, key=lambda value: value.name)[-maximum:]


def session_artifacts(session: Path, selection: dict[str, Any]) -> dict[str, dict[str, Any]]:
    paths: dict[str, Path | None] = {
        "session": session / "session.json",
        "readiness": session / READINESS,
        "selection": session / SELECTION,
        "selected_transcript": selection_artifact(session, selection, "selected_transcript"),
        "aggregate_transcript": selection_artifact(session, selection, "aggregate_transcript"),
        "selected_dialogue": selection_artifact(session, selection, "selected_dialogue"),
        "coverage_report": selection_artifact(session, selection, "coverage_report"),
        "rich_transcript": selection_artifact(session, selection, "rich_transcript"),
        "provisional_selection": session / PROVISIONAL,
        "capture_continuity": session / CAPTURE,
        "order_audit": session / ORDER,
        "review_plan": session / REVIEW_PLAN,
        "review_progress": session / REVIEW_PROGRESS,
    }
    provisional_path = paths["provisional_selection"]
    provisional = read_json(provisional_path) if provisional_path and provisional_path.is_file() else {}
    paths["provisional_transcript"] = selection_artifact(
        session, provisional, "selected_transcript"
    )
    coverage_path = paths["coverage_report"]
    paths["speaker_map"] = coverage_path.parent / "speaker_map.json" if coverage_path else None
    return {
        key: identity(path) if path is not None else {"path": "", "exists": False}
        for key, path in paths.items()
    }


def freeze_inputs(
    sessions_root: Path, policy_path: Path, policy: dict[str, Any]
) -> dict[str, Any]:
    controls: list[dict[str, Any]] = []
    for configured in policy["controls"]:
        path = ROOT / str(configured["path"])
        row = {"id": configured["id"], **identity(path, display=str(configured["path"]))}
        row["expected_sha256"] = configured["sha256"]
        row["valid"] = row.get("sha256") == configured["sha256"]
        controls.append(row)
    sessions: list[dict[str, Any]] = []
    for index, session in enumerate(discover_sessions(sessions_root, policy), start=1):
        selection = read_json(session / SELECTION)
        sessions.append(
            {
                "alias": f"session_{index:02d}",
                "session_name": session.name,
                "session_path": str(session.resolve()),
                "semantic_fingerprint": selection.get("semantic_fingerprint"),
                "selected_profile": selection.get("selected_profile"),
                "artifacts": session_artifacts(session, selection),
            }
        )
    return {
        "schema": INPUT_SCHEMA,
        "version": 1,
        "policy": identity(policy_path, display=repo_path(policy_path)),
        "controls": controls,
        "sessions": sessions,
    }


def manifest_path(out_dir: Path) -> Path:
    return out_dir / "private/input_manifest.json"


def load_or_freeze_inputs(args: argparse.Namespace, policy: dict[str, Any]) -> dict[str, Any]:
    path = manifest_path(args.out_dir)
    if args.refresh or not path.is_file():
        if args.verify_existing:
            raise RebaselineError("verify_existing_requires_frozen_input_manifest")
        manifest = freeze_inputs(args.sessions_root.resolve(), args.policy.resolve(), policy)
        atomic_write(path, canonical_json(manifest))
        return manifest
    manifest = read_json(path)
    if manifest.get("schema") != INPUT_SCHEMA:
        raise RebaselineError(f"unsupported_input_schema:{manifest.get('schema')}")
    return manifest


def private_artifact_path(row: dict[str, Any]) -> Path:
    raw = str(row.get("path") or "")
    return Path(raw).expanduser().resolve()


def current_input_status(
    manifest: dict[str, Any], policy_path: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    controls: list[dict[str, Any]] = []
    failures: list[str] = []
    for row in manifest.get("controls") or []:
        path = ROOT / str(row.get("path") or "")
        current = identity(path, display=str(row.get("path") or ""))
        valid = identity_matches(row, path) and current.get("sha256") == row.get("expected_sha256")
        controls.append({"id": row.get("id"), "valid": valid})
        if not valid:
            failures.append(f"control_changed:{row.get('id')}")
    expected_policy = manifest.get("policy") or {}
    if not identity_matches(expected_policy, policy_path):
        failures.append("policy_changed")
    return controls, failures


def session_staleness(entry: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    for key, row in (entry.get("artifacts") or {}).items():
        path = private_artifact_path(row)
        if not identity_matches(row, path):
            reasons.append(f"artifact_changed:{key}")
    return reasons


def safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def has_disclaimer(path: Path) -> bool:
    if not path.is_file():
        return False
    head = path.read_text(encoding="utf-8", errors="replace")[:2500].lower()
    return "[!warning]" in head and "speaker attribution" in head


def review_lane_metrics(review_plan: dict[str, Any], action: str) -> tuple[int, float]:
    rows = 0
    seconds = 0.0
    for cluster in review_plan.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for item in cluster.get("items") or []:
            if isinstance(item, dict) and item.get("review_action") == action:
                rows += 1
                seconds += number(safe_dict(item.get("interval")).get("duration_sec"))
    return rows, round(seconds, 6)


def public_session(entry: dict[str, Any]) -> dict[str, Any]:
    stale = session_staleness(entry)
    if stale:
        return {
            "schema": SESSION_SCHEMA,
            "alias": entry["alias"],
            "included": False,
            "exclusion_reasons": sorted(stale),
        }
    session = Path(entry["session_path"])
    selection = read_json(session / SELECTION)
    readiness = read_json(session / READINESS)
    session_json = read_json(session / "session.json")
    capture = read_json(session / CAPTURE) if (session / CAPTURE).is_file() else {}
    order = read_json(session / ORDER) if (session / ORDER).is_file() else {}
    review_plan = read_json(session / REVIEW_PLAN) if (session / REVIEW_PLAN).is_file() else {}
    progress = read_json(session / REVIEW_PROGRESS) if (session / REVIEW_PROGRESS).is_file() else {}
    provisional = read_json(session / PROVISIONAL) if (session / PROVISIONAL).is_file() else {}
    reasons: list[str] = []
    selected_profile = str(selection.get("selected_profile") or "")
    if readiness.get("selected_profile") != selected_profile:
        reasons.append("readiness_profile_mismatch")
    if selection.get("schema") != "murmurmark.speaker_resolved_transcript_selection/v1":
        reasons.append("selection_schema_mismatch")
    state = str(selection.get("state") or "")
    aggregate_path = selection_artifact(session, selection, "aggregate_transcript")
    selected_path = selection_artifact(session, selection, "selected_transcript")
    aggregate_current = bool(
        aggregate_path
        and identity_matches(selection.get("aggregate_transcript"), aggregate_path)
    )
    selected_current = bool(
        selected_path and identity_matches(selection.get("selected_transcript"), selected_path)
    )
    exact_fallback = bool(
        state == "fallback"
        and selected_path == aggregate_path
        and selection.get("selected_transcript", {}).get("sha256")
        == selection.get("aggregate_transcript", {}).get("sha256")
        and selection.get("gates", {}).get("exact_aggregate_fallback") is True
    )
    coverage: dict[str, Any] = {}
    coverage_summary: dict[str, Any] = {}
    coverage_gates: dict[str, Any] = {}
    if state == "selected":
        coverage_path = selection_artifact(session, selection, "coverage_report")
        if coverage_path and coverage_path.is_file():
            coverage = read_json(coverage_path)
            coverage_summary = safe_dict(coverage.get("summary"))
            coverage_gates = safe_dict(coverage.get("gates"))
        if coverage.get("schema") != "murmurmark.remote_speaker_coverage_report/v3":
            reasons.append("coverage_schema_mismatch")
        if coverage.get("decision") != "PUBLISH_EVIDENCE":
            reasons.append("coverage_not_publishable")
        surface = "strict_rich"
        disclaimer = True
    elif state == "fallback":
        provisional_profile = str(provisional.get("selected_profile") or "")
        provisional_path = selection_artifact(session, provisional, "selected_transcript")
        provisional_current = bool(
            provisional_path
            and provisional_profile == selected_profile
            and identity_matches(provisional.get("selected_transcript"), provisional_path)
        )
        if provisional_current and provisional.get("state") in {"provisional", "unavailable"}:
            surface = f"provisional_{provisional.get('state')}"
            disclaimer = has_disclaimer(provisional_path)
        else:
            surface = "aggregate_fallback"
            disclaimer = False
        if not exact_fallback:
            reasons.append("aggregate_fallback_not_exact")
    else:
        surface = "invalid"
        disclaimer = False
        reasons.append("selection_state_invalid")
    if not selected_current or not aggregate_current:
        reasons.append("selected_or_aggregate_identity_mismatch")

    provisional_summary = safe_dict(provisional.get("summary"))
    published = integer(coverage_summary.get("published_speakers"))
    if not published and surface == "provisional_provisional":
        published = integer(provisional_summary.get("speaker_clusters"))
    meeting_shape = "one_to_one" if published == 1 else "group" if published > 1 else "unresolved"
    remote_seconds = number(coverage_summary.get("remote_speech_sec"))
    unknown_seconds = number(coverage_summary.get("remaining_unknown_seconds"))
    attributed_ratio = number(coverage_summary.get("attributable_remote_speech_ratio"))
    unknown_words = integer(coverage_summary.get("remaining_unknown_words"))
    remote_words = integer(coverage_summary.get("remote_words"))
    if state == "fallback" and provisional_summary:
        remote_seconds = number(provisional_summary.get("remote_speech_sec"))
        attributed_ratio = number(provisional_summary.get("attributed_remote_speech_ratio"))
        unknown_seconds = max(
            0.0,
            remote_seconds - number(provisional_summary.get("attributed_remote_speech_sec")),
        )
        unknown_words = 0
        remote_words = 0
    capture_status = str(capture.get("status") or safe_dict(session_json.get("health")).get("summary") or "missing")
    capture_gap_seconds = number(capture.get("observed_gap_seconds"))
    capture_ok = capture_status == "ok" and capture_gap_seconds == 0
    order_summary = safe_dict(order.get("summary"))
    lexical_rows, lexical_seconds = review_lane_metrics(review_plan, "check_transcript_text")
    audio_rows, audio_seconds = review_lane_metrics(review_plan, "classify_audio")
    readiness_metrics = safe_dict(readiness.get("metrics"))
    progress_summary = safe_dict(progress.get("summary"))
    conservation = {
        "selected_text": coverage_gates.get("selected_text_unchanged") is True if state == "selected" else exact_fallback,
        "words": coverage_gates.get("word_conservation") is True if state == "selected" else exact_fallback,
        "word_timestamps": coverage_gates.get("word_timestamps_unchanged") is True if state == "selected" else exact_fallback,
        "me_role": coverage_gates.get("me_unchanged") is True if state == "selected" else exact_fallback,
        "timestamp_order": coverage_gates.get("timestamp_order") is True if state == "selected" else exact_fallback,
        "remote_overlap": coverage_gates.get("remote_overlap_preserved") is True if state == "selected" else exact_fallback,
    }
    return {
        "schema": SESSION_SCHEMA,
        "alias": entry["alias"],
        "included": not reasons,
        "exclusion_reasons": sorted(set(reasons)),
        "selected_profile": selected_profile,
        "strict_state": state,
        "read_surface": surface,
        "weak_surface_disclaimer": disclaimer,
        "meeting_shape": meeting_shape,
        "capture": {
            "status": capture_status,
            "duration_sec": round(number(capture.get("capture_duration_sec") or safe_dict(session_json.get("health")).get("actual_duration_sec")), 6),
            "gap_count": integer(capture.get("observed_gap_count")),
            "gap_seconds": round(capture_gap_seconds, 6),
            "complete": capture_ok,
        },
        "surfaces": {
            "selected_current": selected_current,
            "aggregate_current": aggregate_current,
            "aggregate_exact_fallback": exact_fallback if state == "fallback" else None,
            "provisional_state": provisional.get("state") if provisional else "missing",
            "provisional_selected": surface.startswith("provisional_"),
        },
        "conservation": conservation,
        "speaker": {
            "published_speakers": published,
            "remote_speech_sec": round(remote_seconds, 6),
            "remote_words": remote_words,
            "attributable_remote_speech_ratio": round(attributed_ratio, 6),
            "unknown_seconds": round(unknown_seconds, 6),
            "unknown_words": unknown_words,
            "internal_change_utterances": integer(coverage_summary.get("internal_change_utterances")),
            "evidence_level": "coverage_v3" if state == "selected" else "provisional_or_unmeasured",
            "unknown_causes": [
                {
                    "cause": row.get("cause"),
                    "seconds": round(number(row.get("seconds")), 6),
                    "words": integer(row.get("words")),
                }
                for row in coverage.get("unknown_causes") or []
                if isinstance(row, dict) and row.get("cause") != "recovered_bounded_seed_consensus"
            ],
        },
        "chronology": {
            "blocking": bool(order_summary.get("blocking_order_risk")),
            "risk_rows": integer(order_summary.get("probable_order_risk_count"))
            + integer(order_summary.get("needs_review_count")),
            "risk_seconds": round(
                number(order_summary.get("probable_order_risk_seconds"))
                + number(order_summary.get("needs_review_seconds")),
                6,
            ),
        },
        "lexical_evidence": {
            "truth_level": "machine_review_queue_only",
            "review_rows": lexical_rows,
            "review_seconds": lexical_seconds,
        },
        "overlap_review": {"review_rows": audio_rows, "review_seconds": audio_seconds},
        "review_burden": {
            "remaining_rows": integer(progress_summary.get("remaining"))
            or integer(readiness_metrics.get("manual_review_queue_rows")),
            "remaining_seconds": round(
                number(progress_summary.get("remaining_seconds"))
                or number(readiness_metrics.get("manual_review_queue_seconds")),
                6,
            ),
            "verdict": readiness.get("verdict"),
            "use_gate": readiness.get("use_gate"),
        },
    }


def aggregate_unknown_causes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        for cause in row.get("speaker", {}).get("unknown_causes") or []:
            name = str(cause.get("cause") or "unknown")
            target = totals.setdefault(name, {"cause": name, "sessions": 0, "seconds": 0.0, "words": 0})
            target["sessions"] += 1
            target["seconds"] += number(cause.get("seconds"))
            target["words"] += integer(cause.get("words"))
    return [
        {**row, "seconds": round(number(row["seconds"]), 6)}
        for row in sorted(totals.values(), key=lambda value: (-number(value["seconds"]), value["cause"]))
    ]


def residual_axes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    capture_rows = [row for row in rows if row["capture"]["gap_seconds"] > 0]
    unknown_rows = [row for row in rows if row["speaker"]["unknown_seconds"] > 0]
    chronology_rows = [row for row in rows if row["chronology"]["risk_seconds"] > 0]
    lexical_rows = [row for row in rows if row["lexical_evidence"]["review_seconds"] > 0]
    review_rows = [row for row in rows if row["review_burden"]["remaining_seconds"] > 0]
    surface_rows = [
        row for row in rows if row["strict_state"] == "fallback" or row["read_surface"] != "strict_rich"
    ]
    axes = [
        {
            "schema": RESIDUAL_SCHEMA,
            "axis": "capture_continuity",
            "evidence": "restart_bounded_pcm_scan",
            "sessions": len(capture_rows),
            "items": sum(row["capture"]["gap_count"] for row in capture_rows),
            "item_unit": "capture_gaps",
            "seconds": round(sum(row["capture"]["gap_seconds"] for row in capture_rows), 6),
            "actionable": bool(capture_rows),
            "hard_regression": bool(capture_rows),
            "reason": "recorded tracks contain restart-bounded intervals with no captured PCM",
        },
        {
            "schema": RESIDUAL_SCHEMA,
            "axis": "remote_unknown_evidence",
            "evidence": "explicit_coverage_abstention",
            "sessions": len(unknown_rows),
            "items": sum(row["speaker"]["unknown_words"] for row in unknown_rows),
            "item_unit": "words",
            "seconds": round(sum(row["speaker"]["unknown_seconds"] for row in unknown_rows), 6),
            "actionable": bool(unknown_rows),
            "hard_regression": False,
            "reason": "remote speech is preserved but lacks supported speaker attribution",
        },
        {
            "schema": RESIDUAL_SCHEMA,
            "axis": "manual_review_burden",
            "evidence": "current_review_progress",
            "sessions": len(review_rows),
            "items": sum(row["review_burden"]["remaining_rows"] for row in review_rows),
            "item_unit": "review_rows",
            "seconds": round(sum(row["review_burden"]["remaining_seconds"] for row in review_rows), 6),
            "actionable": bool(review_rows),
            "hard_regression": False,
            "reason": "current session use still requires unresolved review decisions",
        },
        {
            "schema": RESIDUAL_SCHEMA,
            "axis": "lexical_review",
            "evidence": "machine_review_queue_without_independent_truth",
            "sessions": len(lexical_rows),
            "items": sum(row["lexical_evidence"]["review_rows"] for row in lexical_rows),
            "item_unit": "review_rows",
            "seconds": round(sum(row["lexical_evidence"]["review_seconds"] for row in lexical_rows), 6),
            "actionable": False,
            "hard_regression": False,
            "reason": "text uncertainty is measured, but fresh independent lexical truth is absent",
        },
        {
            "schema": RESIDUAL_SCHEMA,
            "axis": "chronology_risk",
            "evidence": "current_order_audit",
            "sessions": len(chronology_rows),
            "items": sum(row["chronology"]["risk_rows"] for row in chronology_rows),
            "item_unit": "review_rows",
            "seconds": round(sum(row["chronology"]["risk_seconds"] for row in chronology_rows), 6),
            "actionable": bool(chronology_rows),
            "hard_regression": False,
            "reason": "speaker-bounded order risk remains explicit",
        },
        {
            "schema": RESIDUAL_SCHEMA,
            "axis": "read_surface_coherence",
            "evidence": "selected_provisional_aggregate_contract",
            "sessions": len(surface_rows),
            "items": len(surface_rows),
            "item_unit": "sessions",
            "seconds": None,
            "actionable": any(not row["weak_surface_disclaimer"] for row in surface_rows),
            "hard_regression": any(not row["weak_surface_disclaimer"] for row in surface_rows),
            "reason": "strict fallback or provisional output is visible on the ordinary read surface",
        },
    ]
    hard = [row for row in axes if row["actionable"] and row["hard_regression"]]
    hard.sort(key=lambda row: (-number(row["seconds"]), -integer(row["items"]), row["axis"]))
    measured = [
        row
        for row in axes
        if row["actionable"] and not row["hard_regression"] and row["seconds"] is not None
    ]
    measured.sort(key=lambda row: (-number(row["seconds"]), -integer(row["items"]), row["axis"]))
    remainder = [row for row in axes if row not in hard and row not in measured]
    remainder.sort(key=lambda row: (not row["actionable"], -integer(row["sessions"]), row["axis"]))
    ranked = hard + measured + remainder
    for index, row in enumerate(ranked, start=1):
        row["rank"] = index
    return ranked


def control_payload(manifest: dict[str, Any], control_id: str) -> dict[str, Any]:
    row = next((value for value in manifest.get("controls") or [] if value.get("id") == control_id), None)
    if not isinstance(row, dict):
        return {}
    path = ROOT / str(row.get("path") or "")
    return read_json(path) if path.suffix == ".json" and path.is_file() else {}


def build_report(
    manifest: dict[str, Any], policy: dict[str, Any], policy_path: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    controls, control_failures = current_input_status(manifest, policy_path)
    sessions = [public_session(entry) for entry in manifest.get("sessions") or []]
    included = [row for row in sessions if row.get("included")]
    excluded = [row for row in sessions if not row.get("included")]
    axes = residual_axes(included)
    priority_axis = next((row for row in axes if row["actionable"]), None)
    priority_map = safe_dict(safe_dict(policy.get("priority")).get("selected_goal_by_axis"))
    next_priority = {
        "axis": priority_axis["axis"] if priority_axis else None,
        "goal": priority_map.get(priority_axis["axis"]) if priority_axis else None,
        "reason": priority_axis["reason"] if priority_axis else "no_actionable_measured_residual",
    }
    boundary = control_payload(manifest, "boundary_minority_terminal_report")
    coverage_baseline = control_payload(manifest, "coverage_v3_frozen_baseline")
    perfection = control_payload(manifest, "transcript_perfection_baseline")
    baseline_summary = safe_dict(coverage_baseline.get("summary"))
    selected_rows = [row for row in included if row["strict_state"] == "selected"]
    remote_seconds = sum(row["speaker"]["remote_speech_sec"] for row in selected_rows)
    unknown_seconds = sum(row["speaker"]["unknown_seconds"] for row in selected_rows)
    remote_words = sum(row["speaker"]["remote_words"] for row in selected_rows)
    unknown_words = sum(row["speaker"]["unknown_words"] for row in selected_rows)
    fresh_unknown_seconds_ratio = unknown_seconds / remote_seconds if remote_seconds else None
    baseline_remote_seconds = number(baseline_summary.get("remote_speech_sec"))
    baseline_unknown_seconds = number(baseline_summary.get("remaining_unknown_seconds"))
    baseline_ratio = baseline_unknown_seconds / baseline_remote_seconds if baseline_remote_seconds else None
    shapes = sorted({row["meeting_shape"] for row in included if row["meeting_shape"] != "unresolved"})
    all_conservation = bool(included) and all(
        all(value is True for value in row["conservation"].values()) for row in included
    )
    all_surfaces = bool(included) and all(
        row["surfaces"]["selected_current"]
        and row["surfaces"]["aggregate_current"]
        and (row["strict_state"] != "fallback" or row["weak_surface_disclaimer"])
        for row in included
    )
    minimum = integer(safe_dict(policy.get("discovery")).get("minimum_sessions"))
    gates = {
        "controls_frozen": not control_failures and all(row["valid"] for row in controls),
        "boundary_terminal_keep_coverage_v3": boundary.get("decision")
        == safe_dict(policy.get("gates")).get("required_boundary_decision"),
        "coverage_v3_baseline_promoted": coverage_baseline.get("decision")
        == safe_dict(policy.get("gates")).get("required_coverage_baseline_decision"),
        "transcript_perfection_baseline_current": perfection.get("decision")
        == safe_dict(policy.get("gates")).get("required_perfection_baseline_decision"),
        "minimum_fresh_sessions": len(included) >= minimum,
        "no_stale_or_incompatible_sessions": not excluded,
        "one_to_one_and_group_present": len(shapes) >= integer(safe_dict(policy.get("gates")).get("minimum_meeting_shapes")),
        "capture_complete": bool(included) and all(row["capture"]["complete"] for row in included),
        "word_order_role_conserved": all_conservation,
        "read_surfaces_coherent": all_surfaces,
        "aggregate_quality_score_disabled": safe_dict(policy.get("safety")).get("aggregate_quality_score") is False,
        "zero_production_mutations": integer(
            safe_dict(policy.get("gates")).get("required_production_mutations")
        )
        == 0,
        "privacy_safe_public_report": True,
    }
    evidence_gate_names = (
        "controls_frozen",
        "boundary_terminal_keep_coverage_v3",
        "coverage_v3_baseline_promoted",
        "transcript_perfection_baseline_current",
        "minimum_fresh_sessions",
        "no_stale_or_incompatible_sessions",
        "one_to_one_and_group_present",
        "aggregate_quality_score_disabled",
        "zero_production_mutations",
        "privacy_safe_public_report",
    )
    gates["rebaseline_evidence_complete"] = all(gates[name] for name in evidence_gate_names)
    gates["product_no_regression"] = bool(
        gates["capture_complete"]
        and gates["word_order_role_conserved"]
        and gates["read_surfaces_coherent"]
    )
    report = {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "decision": "REBASELINE_ESTABLISHED"
        if gates["rebaseline_evidence_complete"]
        else "EVIDENCE_INCOMPLETE",
        "controls": {
            "boundary_minority_terminal_decision": boundary.get("decision"),
            "coverage_v3_baseline_decision": coverage_baseline.get("decision"),
            "transcript_perfection_baseline_decision": perfection.get("decision"),
            "all_fingerprints_current": not control_failures,
        },
        "summary": {
            "frozen_sessions": len(sessions),
            "included_sessions": len(included),
            "excluded_sessions": len(excluded),
            "strict_rich_sessions": sum(row["read_surface"] == "strict_rich" for row in included),
            "provisional_sessions": sum(row["read_surface"].startswith("provisional_") for row in included),
            "aggregate_only_sessions": sum(row["read_surface"] == "aggregate_fallback" for row in included),
            "meeting_shapes": shapes,
            "capture_seconds": round(sum(row["capture"]["duration_sec"] for row in included), 6),
            "remote_speech_seconds_coverage_v3": round(remote_seconds, 6),
            "remote_words_coverage_v3": remote_words,
            "unknown_remote_seconds_coverage_v3": round(unknown_seconds, 6),
            "unknown_remote_words_coverage_v3": unknown_words,
            "unknown_remote_seconds_ratio": round(fresh_unknown_seconds_ratio, 6) if fresh_unknown_seconds_ratio is not None else None,
            "unknown_remote_words_ratio": round(unknown_words / remote_words, 6) if remote_words else None,
            "frozen_baseline_unknown_seconds_ratio": round(baseline_ratio, 6) if baseline_ratio is not None else None,
            "aggregate_quality_score": None,
        },
        "sessions": sessions,
        "dimensions": {
            "capture_completeness": {
                "status": "passed" if gates["capture_complete"] else "failed",
                "gap_seconds": round(sum(row["capture"]["gap_seconds"] for row in included), 6),
            },
            "word_conservation": {"status": "passed" if all_conservation else "failed"},
            "timestamp_and_role_conservation": {"status": "passed" if all_conservation else "failed"},
            "read_surface_coherence": {"status": "passed" if all_surfaces else "failed"},
            "remote_speaker_topology": {
                "status": "measured_without_human_count_truth",
                "published_speakers": sum(row["speaker"]["published_speakers"] for row in selected_rows),
                "internal_change_utterances": sum(row["speaker"]["internal_change_utterances"] for row in selected_rows),
            },
            "explicit_unknown": {
                "status": "measured_abstention_not_correctness",
                "seconds": round(unknown_seconds, 6),
                "words": unknown_words,
                "causes": aggregate_unknown_causes(selected_rows),
            },
            "overlap_and_chronology": {
                "status": "measured",
                "chronology_seconds": round(sum(row["chronology"]["risk_seconds"] for row in included), 6),
                "overlap_review_seconds": round(sum(row["overlap_review"]["review_seconds"] for row in included), 6),
            },
            "lexical_evidence": {
                "status": "partial_no_fresh_independent_truth",
                "review_seconds": round(sum(row["lexical_evidence"]["review_seconds"] for row in included), 6),
                "review_rows": sum(row["lexical_evidence"]["review_rows"] for row in included),
            },
            "review_burden": {
                "status": "measured",
                "remaining_seconds": round(sum(row["review_burden"]["remaining_seconds"] for row in included), 6),
                "remaining_rows": sum(row["review_burden"]["remaining_rows"] for row in included),
            },
        },
        "residual_axes": [{key: value for key, value in row.items() if key != "schema"} for row in axes],
        "next_priority": next_priority,
        "gates": gates,
        "failures": sorted(control_failures),
        "privacy": {
            "session_ids": False,
            "absolute_paths": False,
            "speech_text": False,
            "speaker_names": False,
            "private_manifest": "private/input_manifest.json",
        },
        "safety": {
            "session_artifacts_written": 0,
            "production_profiles_changed": 0,
            "raw_audio_changed": 0,
            "coverage_v3_changed": 0,
        },
    }
    return report, axes


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Post-Segmentation Transcript Rebaseline v1",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "The public report contains no session identifiers, paths, speech text or speaker names.",
        "Coverage v3 and the authoritative batch transcript remain unchanged.",
        "",
        "## Scope",
        "",
        f"- Frozen sessions: `{summary['frozen_sessions']}`",
        f"- Included sessions: `{summary['included_sessions']}`",
        f"- Strict rich surfaces: `{summary['strict_rich_sessions']}`",
        f"- Provisional surfaces: `{summary['provisional_sessions']}`",
        f"- Meeting shapes: `{', '.join(summary['meeting_shapes']) or 'unresolved'}`",
        "",
        "## Transcript Surfaces",
        "",
        "| Alias | Shape | Strict | Read surface | Speakers | Unknown words | Review seconds |",
        "|---|---|---|---|---:|---:|---:|",
    ]
    for row in report["sessions"]:
        if not row.get("included"):
            lines.append(f"| `{row['alias']}` | excluded | - | - | - | - | - |")
            continue
        lines.append(
            f"| `{row['alias']}` | `{row['meeting_shape']}` | `{row['strict_state']}` | "
            f"`{row['read_surface']}` | {row['speaker']['published_speakers']} | "
            f"{row['speaker']['unknown_words']} | {row['review_burden']['remaining_seconds']:.3f} |"
        )
    lines += ["", "## Residual Axes", ""]
    for row in report["residual_axes"]:
        seconds = "not comparable" if row["seconds"] is None else f"{row['seconds']:.3f}s"
        lines.append(
            f"{row['rank']}. `{row['axis']}`: {seconds}, {row['items']} {row['item_unit']} "
            f"across {row['sessions']} sessions; actionable=`{str(row['actionable']).lower()}`."
        )
    lines += [
        "",
        "## Next Priority",
        "",
        f"`{report['next_priority']['goal']}` (`{report['next_priority']['axis']}`).",
        "",
        report["next_priority"]["reason"] + ".",
        "",
        "## Gates",
        "",
    ]
    lines.extend(f"- `{key}`: `{str(value).lower()}`" for key, value in report["gates"].items())
    lines.append("")
    return "\n".join(lines)


def public_manifest(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "decision": report["decision"],
        "controls": report["controls"],
        "summary": report["summary"],
        "dimensions": report["dimensions"],
        "residual_axes": report["residual_axes"],
        "next_priority": report["next_priority"],
        "gates": report["gates"],
        "privacy": report["privacy"],
        "safety": report["safety"],
    }


def output_bytes(report: dict[str, Any], axes: list[dict[str, Any]]) -> dict[str, bytes]:
    report_bytes = canonical_json(report)
    markdown_bytes = (report_markdown(report) + "\n").encode()
    residual_bytes = canonical_jsonl(axes)
    public_bytes = canonical_json(public_manifest(report))
    core = {
        "post_segmentation_rebaseline_report.json": report_bytes,
        "post_segmentation_rebaseline_report.md": markdown_bytes,
        "residual_axes.jsonl": residual_bytes,
        "public_manifest.json": public_bytes,
    }
    replay = {
        "schema": REPLAY_SCHEMA,
        "byte_exact": True,
        "artifacts": {name: sha256_bytes(data) for name, data in sorted(core.items())},
    }
    core["replay_report.json"] = canonical_json(replay)
    artifact_manifest = {
        "schema": ARTIFACT_SCHEMA,
        "artifacts": [
            {"path": name, "bytes": len(data), "sha256": sha256_bytes(data)}
            for name, data in sorted(core.items())
        ],
    }
    core["artifact_manifest.json"] = canonical_json(artifact_manifest)
    return core


def assert_public_privacy(outputs: dict[str, bytes]) -> None:
    for name, data in outputs.items():
        text = data.decode("utf-8")
        if "/Users/" in text or SESSION_ID_PATTERN.search(text):
            raise RebaselineError(f"privacy_violation:{name}")


def verify_existing(out_dir: Path, outputs: dict[str, bytes]) -> list[str]:
    mismatches: list[str] = []
    for name, expected in outputs.items():
        path = out_dir / name
        if not path.is_file():
            mismatches.append(f"missing:{name}")
        elif path.read_bytes() != expected:
            mismatches.append(f"byte_mismatch:{name}")
    return mismatches


def main() -> int:
    args = parse_args()
    args.sessions_root = args.sessions_root.expanduser().resolve()
    args.policy = args.policy.expanduser().resolve()
    args.out_dir = args.out_dir.expanduser().resolve()
    args.snapshot = args.snapshot.expanduser().resolve()
    policy = read_json(args.policy)
    validate_policy(policy)
    manifest = load_or_freeze_inputs(args, policy)
    report, axes = build_report(manifest, policy, args.policy)
    outputs = output_bytes(report, axes)
    assert_public_privacy(outputs)
    if args.verify_existing:
        mismatches = verify_existing(args.out_dir, outputs)
        if mismatches:
            print("post_segmentation_rebaseline: verification failed", file=sys.stderr)
            for mismatch in mismatches:
                print(f"  {mismatch}", file=sys.stderr)
            return 2
        if args.snapshot.is_file() and args.snapshot.read_bytes() != canonical_json(public_manifest(report)):
            print("post_segmentation_rebaseline: snapshot mismatch", file=sys.stderr)
            return 2
        print("post_segmentation_rebaseline: byte-exact replay passed")
        return 0
    for name, data in outputs.items():
        atomic_write(args.out_dir / name, data)
    if args.write_snapshot:
        atomic_write(args.snapshot, canonical_json(public_manifest(report)))
    print(f"post_segmentation_rebaseline: {report['decision']}")
    print(f"sessions: {report['summary']['included_sessions']}/{report['summary']['frozen_sessions']}")
    print(
        "next_priority: "
        f"{report['next_priority']['goal']} ({report['next_priority']['axis']})"
    )
    print(f"report: {args.out_dir / 'post_segmentation_rebaseline_report.md'}")
    return 0 if report["decision"] == "REBASELINE_ESTABLISHED" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RebaselineError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
