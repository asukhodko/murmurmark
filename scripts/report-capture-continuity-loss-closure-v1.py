#!/usr/bin/env python3
"""Verify frozen restart loss and qualify the hardened capture restart path."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "murmurmark.capture_continuity_loss_closure_report/v1"
POLICY_SCHEMA = "murmurmark.capture_continuity_loss_closure_policy/v1"
MANIFEST_SCHEMA = "murmurmark.capture_continuity_loss_closure_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/capture-continuity-loss-closure-v1.json"
DEFAULT_MANIFEST = ROOT / "docs/testing/capture-continuity-loss-closure-v1-manifest.json"
DEFAULT_OUT = ROOT / "sessions/_reports/capture-continuity-loss-closure-v1"
DEFAULT_SNAPSHOT = ROOT / "docs/testing/capture-continuity-loss-closure-v1-snapshot.json"


class ClosureError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controlled-session", type=Path)
    parser.add_argument("--soak-session", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--verify-frozen-only", action="store_true")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--write-snapshot", action="store_true")
    return parser.parse_args()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ClosureError(f"cannot_read_json:{path}:{error}") from error
    if not isinstance(value, dict):
        raise ClosureError(f"expected_json_object:{path}")
    return value


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def identity(path: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"path": display_path(path), "exists": path.is_file()}
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return row


def recording_lock_available(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        return False
    return True


def safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def verify_frozen(manifest: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for expected in manifest.get("files") or []:
        path = ROOT / str(expected.get("path") or "")
        exists = path.is_file()
        actual_bytes = path.stat().st_size if exists else None
        actual_sha256 = sha256_file(path) if exists else None
        valid = bool(
            exists
            and actual_bytes == safe_int(expected.get("bytes"))
            and actual_sha256 == expected.get("sha256")
        )
        rows.append(
            {
                "path": expected.get("path"),
                "exists": exists,
                "bytes": actual_bytes,
                "sha256": actual_sha256,
                "valid": valid,
            }
        )

    frozen_case = policy.get("frozen_case") or {}
    continuity_path = (
        ROOT
        / str(frozen_case.get("session") or "")
        / "derived/audit/capture-continuity/capture_continuity_report.json"
    )
    continuity = read_json(continuity_path) if continuity_path.is_file() else {}
    metrics_valid = bool(
        safe_int(continuity.get("screen_capture_restart_count"))
        == safe_int(frozen_case.get("restart_count"))
        and safe_int(continuity.get("observed_gap_count"))
        == safe_int(frozen_case.get("gap_count"))
        and abs(
            (safe_float(continuity.get("observed_gap_seconds")) or -1.0)
            - (safe_float(frozen_case.get("gap_seconds")) or -2.0)
        )
        <= 0.000001
    )
    return {
        "status": "passed" if rows and all(row["valid"] for row in rows) and metrics_valid else "failed",
        "files": rows,
        "frozen_metrics_valid": metrics_valid,
        "restart_count": continuity.get("screen_capture_restart_count"),
        "gap_count": continuity.get("observed_gap_count"),
        "gap_seconds": continuity.get("observed_gap_seconds"),
    }


def assess_capture(session: Path | None, *, mode: str, policy: dict[str, Any]) -> dict[str, Any]:
    if session is None:
        return {"mode": mode, "status": "missing", "session": None, "gates": {}}
    session = session.expanduser().resolve()
    report_path = session / "derived/audit/capture-continuity/capture_continuity_report.json"
    manifest_path = session / "session.json"
    if not report_path.is_file() or not manifest_path.is_file():
        return {
            "mode": mode,
            "status": "missing",
            "session": display_path(session),
            "gates": {"report_exists": report_path.is_file(), "session_manifest_exists": manifest_path.is_file()},
        }
    report = read_json(report_path)
    manifest = read_json(manifest_path)
    event_rows = read_jsonl(session / "events.jsonl")
    event_types = [str(row.get("type") or "") for row in event_rows]
    health = manifest.get("health") if isinstance(manifest.get("health"), dict) else {}
    latency = report.get("restart_latency") if isinstance(report.get("restart_latency"), dict) else {}
    thresholds = policy.get("thresholds") or {}
    gap_seconds = safe_float(report.get("observed_gap_seconds")) or 0.0
    duration = safe_float(health.get("actual_duration_sec")) or 0.0
    provenance = report.get("restart_provenance") if isinstance(report.get("restart_provenance"), list) else []
    terminal_unique = all(safe_int(row.get("terminal_event_count")) == 1 for row in provenance)
    if mode == "controlled_restart":
        gates = {
            "restart_observed": safe_int(report.get("restart_attempt_count")) >= 1,
            "provenance_complete": report.get("restart_provenance_status") == "complete",
            "terminal_unique": bool(provenance) and terminal_unique,
            "software_idle_bounded": (
                safe_float(latency.get("max_software_idle_ms")) is not None
                and (safe_float(latency.get("max_software_idle_ms")) or 0.0)
                <= (safe_float(thresholds.get("maximum_software_idle_ms")) or 50.0)
            ),
            "raw_tracks_exist": all(
                (session / f"audio/{source}/000001.caf").is_file()
                for source in ("mic", "remote")
            ),
            "single_capture_stop": event_types.count("capture.stopped") == 1,
            "manifest_written_once": event_types.count("manifest.written") == 1,
            "recording_lock_released_once": event_types.count("capture.recording_lock_released") == 1,
            "no_writer_failure": "capture.write_failed" not in event_types,
        }
    else:
        gates = {
            "minimum_duration": duration
            >= (safe_float(thresholds.get("minimum_soak_duration_sec")) or 600.0),
            "capture_complete": report.get("capture_complete") is True,
            "zero_gap_seconds": gap_seconds
            <= (safe_float(thresholds.get("required_no_restart_gap_seconds")) or 0.0),
            "raw_tracks_exist": all(
                (session / f"audio/{source}/000001.caf").is_file()
                for source in ("mic", "remote")
            ),
            "single_capture_stop": event_types.count("capture.stopped") == 1,
            "manifest_written_once": event_types.count("manifest.written") == 1,
            "recording_lock_released_once": event_types.count("capture.recording_lock_released") == 1,
            "no_writer_failure": "capture.write_failed" not in event_types,
        }
    return {
        "mode": mode,
        "status": "passed" if gates and all(gates.values()) else "failed",
        "session": display_path(session),
        "capture_status": report.get("status"),
        "capture_complete": report.get("capture_complete"),
        "duration_sec": round(duration, 3),
        "gap_count": safe_int(report.get("observed_gap_count")),
        "gap_seconds": round(gap_seconds, 6),
        "restart_attempt_count": safe_int(report.get("restart_attempt_count")),
        "restart_provenance_status": report.get("restart_provenance_status"),
        "max_software_idle_ms": latency.get("max_software_idle_ms"),
        "max_start_api_ms": latency.get("max_start_api_ms"),
        "max_restart_to_pcm_ms": latency.get("max_request_to_all_sources_committed_ms"),
        "gates": gates,
        "artifacts": {
            "session_manifest": identity(manifest_path),
            "events": identity(session / "events.jsonl"),
            "mic_raw": identity(session / "audio/mic/000001.caf"),
            "remote_raw": identity(session / "audio/remote/000001.caf"),
            "continuity_report": identity(report_path),
        },
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    policy = read_json(args.policy.resolve())
    manifest = read_json(args.manifest.resolve())
    if policy.get("schema") != POLICY_SCHEMA:
        raise ClosureError(f"unsupported_policy_schema:{policy.get('schema')}")
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ClosureError(f"unsupported_manifest_schema:{manifest.get('schema')}")
    frozen = verify_frozen(manifest, policy)
    controlled = assess_capture(args.controlled_session, mode="controlled_restart", policy=policy)
    soak = assess_capture(args.soak_session, mode="no_restart_soak", policy=policy)
    global_safety = {
        "recording_lock_available": recording_lock_available(
            ROOT / "sessions/.murmurmark-recording.lock"
        ),
    }

    if frozen["status"] != "passed":
        decision = "FAILED_FROZEN_INPUT_DRIFT"
    elif args.verify_frozen_only or controlled["status"] == "missing" or soak["status"] == "missing":
        decision = "EVIDENCE_PENDING"
    elif (
        controlled["status"] != "passed"
        or soak["status"] != "passed"
        or not all(global_safety.values())
    ):
        decision = "DO_NOT_PROMOTE"
    elif (safe_float(controlled.get("gap_seconds")) or 0.0) > 0.0:
        decision = "EVIDENCE_BOUND"
    else:
        decision = "PROMOTE_RESTART_HARDENING"

    return {
        "schema": SCHEMA,
        "generator": {"name": "report-capture-continuity-loss-closure-v1", "version": "0.1.0"},
        "decision": decision,
        "frozen_evidence": frozen,
        "controlled_restart": controlled,
        "no_restart_soak": soak,
        "global_safety": global_safety,
        "software_delay_removed": controlled.get("gates", {}).get("software_idle_bounded"),
        "terminal_completeness_policy": "any_measured_gap_requires_review",
        "production_transcript_profiles_changed": False,
        "batch_authoritative": True,
    }


def markdown(report: dict[str, Any]) -> bytes:
    controlled = report["controlled_restart"]
    soak = report["no_restart_soak"]
    lines = [
        "# Capture Continuity Loss Closure v1",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Frozen evidence: `{report['frozen_evidence']['status']}`",
        f"- Controlled restart: `{controlled['status']}`",
        f"- No-restart soak: `{soak['status']}`",
        f"- Software delay removed: `{str(report.get('software_delay_removed')).lower()}`",
        "",
        "## Controlled Restart",
        "",
        f"- Gap: `{controlled.get('gap_count')}` / `{controlled.get('gap_seconds')}s`",
        f"- Max software idle: `{controlled.get('max_software_idle_ms')}ms`",
        f"- Max start API: `{controlled.get('max_start_api_ms')}ms`",
        f"- Max request-to-PCM: `{controlled.get('max_restart_to_pcm_ms')}ms`",
        "",
        "## No-Restart Soak",
        "",
        f"- Duration: `{soak.get('duration_sec')}s`",
        f"- Gap: `{soak.get('gap_count')}` / `{soak.get('gap_seconds')}s`",
        "",
    ]
    return ("\n".join(lines)).encode()


def pinned_sessions(report: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
    values = [
        str((policy.get("frozen_case") or {}).get("session") or ""),
        str((report.get("controlled_restart") or {}).get("session") or ""),
        str((report.get("no_restart_soak") or {}).get("session") or ""),
    ]
    return {
        "schema": "murmurmark.pinned_sessions/v1",
        "purpose": "Capture Continuity Loss Closure v1 frozen, controlled and soak evidence",
        "retention": "keep_raw_and_derived",
        "sessions": sorted({Path(value).name for value in values if value}),
    }


def main() -> int:
    args = parse_args()
    try:
        report = build_report(args)
    except ClosureError as error:
        print(f"error: {error}")
        return 2
    payloads = {
        args.out_dir / "capture_continuity_loss_closure_report.json": canonical_json(report),
        args.out_dir / "capture_continuity_loss_closure_report.md": markdown(report),
        args.out_dir / "pinned_sessions.json": canonical_json(
            pinned_sessions(report, read_json(args.policy.resolve()))
        ),
    }
    if args.verify_existing:
        for path, payload in payloads.items():
            if not path.is_file() or path.read_bytes() != payload:
                print(f"error: existing report differs: {path}")
                return 1
    else:
        for path, payload in payloads.items():
            atomic_write(path, payload)
    if args.write_snapshot:
        atomic_write(args.snapshot, canonical_json(report))
    print(f"decision: {report['decision']}")
    print(f"report: {args.out_dir / 'capture_continuity_loss_closure_report.json'}")
    return 0 if report["decision"] not in {"FAILED_FROZEN_INPUT_DRIFT", "DO_NOT_PROMOTE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
