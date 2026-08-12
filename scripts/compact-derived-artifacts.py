#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shlex
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCRIPT_VERSION = "0.3.0"
MANIFEST_SCHEMA = "murmurmark.derived_compaction/v1"
REPORT_SCHEMA = "murmurmark.derived_compaction_report/v1"
AUDIT_SCHEMA = "murmurmark.derived_compaction_audit_event/v1"
KEEP_RAW_MODE = "keep_raw"
TRANSCRIPT_ONLY_MODE = "transcript_only"
MODES = (KEEP_RAW_MODE, TRANSCRIPT_ONLY_MODE)
MEDIA_SUFFIXES = {
    ".aac",
    ".aif",
    ".aiff",
    ".caf",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".pcm",
    ".wav",
    ".wv",
}
SESSION_NAME_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:[-_].*)?$")
AGE_RE = re.compile(r"^(?P<value>\d+)(?P<unit>[smhdw])$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Plan, apply or verify session compaction. The default keeps raw audio; "
            "transcript_only also removes declared raw mic/remote files."
        )
    )
    parser.add_argument("action", choices=("plan", "apply", "verify"))
    parser.add_argument("target", help="Session path, session id, latest, or all.")
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument("--mode", choices=MODES, default=KEEP_RAW_MODE)
    parser.add_argument("--older-than", help="Bulk age threshold such as 7d, 24h or 30m.")
    pin_group = parser.add_mutually_exclusive_group()
    pin_group.add_argument("--exclude-pinned", action="store_true")
    pin_group.add_argument("--include-pinned", action="store_true")
    parser.add_argument("--pin-file", type=Path, action="append", default=[])
    parser.add_argument("--confirm-delete-derived-media", action="store_true")
    parser.add_argument("--confirm-delete-raw", action="store_true")
    parser.add_argument("--require-successful-export", action="store_true")
    parser.add_argument("--export-manifest", type=Path)
    parser.add_argument(
        "--allow-active-lifecycle",
        action="store_true",
        help="Internal finish-only escape hatch while the lifecycle owns its lock.",
    )
    parser.add_argument("--out", type=Path, help="Single-session manifest path.")
    parser.add_argument("--report-out", type=Path, help="Bulk JSON report path.")
    return parser.parse_args()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_identity(path: Path, session: Path, *, hash_content: bool) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.relative_to(session)),
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path) if hash_content else None,
    }


def command_path(path: Path) -> str:
    try:
        display = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        display = path.resolve()
    return shlex.quote(str(display))


def parse_age(value: str | None) -> timedelta | None:
    if value is None:
        return None
    match = AGE_RE.fullmatch(value.strip().lower())
    if not match:
        raise SystemExit("--older-than must look like 30m, 24h, 7d or 4w")
    amount = int(match.group("value"))
    unit = match.group("unit")
    seconds = amount * {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}[unit]
    return timedelta(seconds=seconds)


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def session_ended_at(session: Path, manifest: dict[str, Any]) -> datetime:
    for key in ("ended_at", "stopped_at", "created_at"):
        parsed = parse_datetime(manifest.get(key))
        if parsed is not None:
            return parsed
    return datetime.fromtimestamp(session.stat().st_mtime, tz=timezone.utc)


def resolve_session(target: str, sessions_root: Path) -> Path:
    if target == "latest":
        sessions = list_sessions(sessions_root)
        if not sessions:
            raise SystemExit(f"no sessions with session.json under {sessions_root}")
        return sessions[0]
    direct = Path(target).expanduser()
    if (direct / "session.json").is_file():
        return direct.resolve()
    rooted = sessions_root.expanduser() / target
    if (rooted / "session.json").is_file():
        return rooted.resolve()
    raise SystemExit(f"session.json not found for {target}")


def list_sessions(sessions_root: Path) -> list[Path]:
    root = sessions_root.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"sessions root not found: {root}")
    sessions = [
        path.resolve()
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith("_") and (path / "session.json").is_file()
    ]
    return sorted(sessions, key=lambda path: path.stat().st_mtime, reverse=True)


def all_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_strings(item)


def discover_pins(sessions_root: Path, explicit_files: list[Path]) -> tuple[set[str], list[str]]:
    root = sessions_root.expanduser().resolve()
    automatic_sources: list[Path] = []
    explicit_pin_sources: list[Path] = []
    retired_sources: list[Path] = []
    reports = root / "_reports"
    if reports.is_dir():
        automatic_sources.extend(sorted(reports.glob("**/frozen_corpus.json")))
        automatic_sources.extend(sorted(reports.glob("**/split_manifest.json")))
        automatic_sources.extend(sorted(reports.glob("**/*baseline*.json")))
        automatic_sources.extend(sorted(reports.glob("**/*hard_test*.json")))
        explicit_pin_sources.extend(sorted(reports.glob("**/pinned_sessions.json")))
        retired_sources.extend(sorted(reports.glob("**/retired_sessions.json")))
    policies = Path.cwd() / "policies"
    if policies.is_dir():
        automatic_sources.extend(sorted(policies.glob("*.json")))
    explicit_pin_sources.extend(path.expanduser() for path in explicit_files)

    def session_ids(sources: list[Path]) -> tuple[set[str], list[str]]:
        found_ids: set[str] = set()
        used: list[str] = []
        for source in dict.fromkeys(path.resolve() for path in sources if path.is_file()):
            payload = read_json(source)
            if payload is None:
                continue
            found = {
                Path(value).name
                for value in all_strings(payload)
                if SESSION_NAME_RE.fullmatch(Path(value).name)
            }
            if found:
                found_ids.update(found)
                used.append(str(source))
        return found_ids, used

    automatic_pins, automatic_used = session_ids(automatic_sources)
    explicit_pins, explicit_used = session_ids(explicit_pin_sources)
    retired, retired_used = session_ids(retired_sources)
    pins = (automatic_pins - retired) | explicit_pins
    used_sources: list[str] = []
    used_sources.extend(automatic_used)
    used_sources.extend(explicit_used)
    used_sources.extend(retired_used)
    return pins, used_sources


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def raw_files(session: Path, manifest: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    blockers: list[str] = []
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    identities: list[dict[str, Any]] = []
    for source in ("mic", "remote"):
        entries = files.get(source) if isinstance(files.get(source), list) else []
        if not entries:
            blockers.append(f"missing_{source}_manifest_entries")
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                blockers.append(f"invalid_{source}_manifest_entry")
                continue
            relative = Path(str(entry["path"]))
            candidate = session / relative
            try:
                resolved = candidate.resolve(strict=True)
            except OSError:
                blockers.append(f"missing_raw:{relative}")
                continue
            if candidate.is_symlink() or not is_within(resolved, (session / "audio").resolve()):
                blockers.append(f"unsafe_raw_path:{relative}")
                continue
            if not candidate.is_file():
                blockers.append(f"missing_raw:{relative}")
                continue
            identity = path_identity(candidate, session, hash_content=False)
            expected_bytes = entry.get("bytes")
            identity.update({"source": source, "expected_bytes": expected_bytes})
            if isinstance(expected_bytes, int) and expected_bytes > 0 and identity["bytes"] != expected_bytes:
                blockers.append(f"raw_size_mismatch:{relative}")
            if identity["bytes"] <= 0:
                blockers.append(f"empty_raw:{relative}")
            identities.append(identity)
    return identities, blockers


def selected_paths(session: Path) -> tuple[list[Path], list[str]]:
    outcome = read_json(session / "derived/outcome/outcome.json") or {}
    readiness = read_json(session / "derived/readiness/session_readiness.json") or {}
    summary = outcome.get("summary") if isinstance(outcome.get("summary"), dict) else {}
    relative_values: list[str] = []
    for key in ("transcript_path", "notes_path", "quality_verdict_path"):
        value = summary.get(key)
        if isinstance(value, str) and value:
            relative_values.append(value)

    profile = str(outcome.get("selected_profile") or readiness.get("selected_profile") or "").strip()
    resolved = session / "derived/transcript-simple/whisper-cpp/resolved"
    synthesis = session / "derived/synthesis-simple/extractive"
    if profile:
        relative_values.extend(
            [
                f"derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.{profile}.json",
                f"derived/transcript-simple/whisper-cpp/resolved/transcript.simple.{profile}.json",
                f"derived/synthesis-simple/extractive/evidence_notes.{profile}.json",
                f"derived/synthesis-simple/extractive/quality_verdict.{profile}.json",
            ]
        )
    if not any("transcript" in value and value.endswith(".md") for value in relative_values):
        profile_transcript = resolved / f"transcript.{profile}.md" if profile else None
        if profile_transcript and profile_transcript.is_file():
            relative_values.append(str(profile_transcript.relative_to(session)))
        elif (resolved / "transcript.md").is_file():
            relative_values.append(str((resolved / "transcript.md").relative_to(session)))

    fixed = [
        session / "session.json",
        session / "events.jsonl",
        session / "pipeline_job.json",
        session / "derived/outcome/outcome.json",
        session / "derived/readiness/session_readiness.json",
        session / "derived/pipeline-run/authoritative_handoff.json",
        session / "derived/audit/capture-continuity/capture_continuity_report.json",
    ]
    paths: list[Path] = []
    warnings: list[str] = []
    seen: set[Path] = set()
    for candidate in fixed + [session / value for value in relative_values]:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except OSError:
            continue
        if not is_within(resolved_candidate, session.resolve()):
            warnings.append(f"ignored_external_selected_path:{candidate}")
            continue
        paths.append(candidate)
    return paths, warnings


def has_selected_transcript(paths: list[Path]) -> bool:
    return any(path.suffix.lower() == ".md" and path.name.startswith("transcript") for path in paths)


def media_inventory(session: Path) -> tuple[list[dict[str, Any]], list[str]]:
    derived = (session / "derived").resolve()
    if not derived.is_dir():
        return [], ["missing_derived_directory"]
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for root, directories, files in os.walk(derived, followlinks=False):
        root_path = Path(root)
        directories[:] = [
            name for name in directories if not (root_path / name).is_symlink()
        ]
        for name in files:
            path = root_path / name
            if path.suffix.lower() not in MEDIA_SUFFIXES:
                continue
            if path.is_symlink():
                warnings.append(f"ignored_media_symlink:{path.relative_to(session)}")
                continue
            try:
                resolved = path.resolve(strict=True)
                stat = path.stat()
            except OSError as error:
                warnings.append(f"unreadable_media:{path.relative_to(session)}:{error}")
                continue
            if not is_within(resolved, derived):
                warnings.append(f"ignored_media_outside_derived:{path.relative_to(session)}")
                continue
            relative = path.relative_to(session)
            category_parts = relative.parts[1:3]
            items.append(
                {
                    "path": str(relative),
                    "bytes": stat.st_size,
                    "suffix": path.suffix.lower(),
                    "category": "/".join(category_parts),
                }
            )
    return sorted(items, key=lambda item: str(item["path"])), warnings


def inventory_fingerprint(items: list[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for item in items:
        digest.update(str(item["path"]).encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(item["bytes"]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def aggregate(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    values: dict[str, dict[str, int]] = {}
    for item in items:
        name = str(item[key])
        row = values.setdefault(name, {"files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += int(item["bytes"])
    return dict(sorted(values.items(), key=lambda pair: (-pair[1]["bytes"], pair[0])))


def raw_inventory(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "candidate_files": len(items),
        "candidate_bytes": sum(int(item["bytes"]) for item in items),
        "by_source": aggregate(items, "source"),
    }


def absent_identity_failures(session: Path, identities: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for identity in identities:
        relative = Path(str(identity["path"]))
        if (session / relative).exists():
            failures.append(f"raw_still_present:{relative}")
    return failures


def export_gate(
    session: Path,
    required: bool,
    explicit_manifest: Path | None,
) -> tuple[dict[str, Any], list[str]]:
    if not required:
        return {"required": False, "passed": None, "path": None}, []
    path = explicit_manifest
    if path is None:
        path = Path("exports/private") / session.name / "export_manifest.json"
    path = path.expanduser()
    payload = read_json(path)
    blockers: list[str] = []
    if payload is None:
        blockers.append("missing_or_invalid_export_manifest")
    else:
        status = str(payload.get("status") or "")
        if payload.get("schema") != "murmurmark.export_manifest/v1":
            blockers.append("unsupported_export_manifest_schema")
        if status not in {"exported", "exported_with_warnings"}:
            blockers.append("export_not_successful")
        if isinstance(payload.get("blockers"), list) and payload["blockers"]:
            blockers.append("export_manifest_has_blockers")
        expected_id = str((read_json(session / "session.json") or {}).get("session_id") or session.name)
        if str(payload.get("session_id") or "") != expected_id:
            blockers.append("export_manifest_session_mismatch")
    return {
        "required": True,
        "passed": not blockers,
        "path": str(path.resolve()) if path.exists() else str(path),
        "status": payload.get("status") if payload else None,
    }, blockers


def pipeline_blockers(session: Path, allow_active_lifecycle: bool) -> list[str]:
    blockers: list[str] = []
    if (session / "session.lock").exists():
        blockers.append("capture_session_lock_present")
    pipeline = read_json(session / "derived/pipeline-run/pipeline_run_state.json") or {}
    if pipeline.get("status") == "running":
        blockers.append("pipeline_running")
    lifecycle = read_json(session / "derived/meeting-lifecycle/state.json") or {}
    if not allow_active_lifecycle and lifecycle.get("current_action"):
        blockers.append("meeting_lifecycle_running")
    return blockers


@contextmanager
def compaction_lock(session: Path) -> Iterator[None]:
    lock_path = session / "derived/retention/derived_compaction.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("another derived compaction owns this session") from error
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def lifecycle_lock_available(session: Path) -> bool:
    lock_path = session / "derived/meeting-lifecycle/lifecycle.lock"
    if not lock_path.exists():
        return True
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def build_manifest(
    session: Path,
    args: argparse.Namespace,
    *,
    pinned: bool,
    pin_sources: list[str],
) -> dict[str, Any]:
    session_payload = read_json(session / "session.json") or {}
    inventory, inventory_warnings = media_inventory(session)
    raw, raw_blockers = raw_files(session, session_payload)
    selected, selected_warnings = selected_paths(session)
    export, export_blockers = export_gate(
        session,
        args.require_successful_export,
        args.export_manifest,
    )
    blockers = pipeline_blockers(session, args.allow_active_lifecycle)
    blockers.extend(raw_blockers)
    blockers.extend(export_blockers)
    if not has_selected_transcript(selected):
        blockers.append("selected_transcript_missing")
    if str(session_payload.get("status") or "") not in {"completed", "completed_with_warnings"}:
        blockers.append("capture_not_completed")
    if pinned and not args.include_pinned:
        blockers.append("pinned_corpus_session")
    if not args.allow_active_lifecycle and not lifecycle_lock_available(session):
        blockers.append("meeting_lifecycle_lock_held")

    warnings = sorted(set(inventory_warnings + selected_warnings))
    critical = [path_identity(path, session, hash_content=True) for path in selected]
    total_bytes = sum(int(item["bytes"]) for item in inventory)
    status = "eligible" if not blockers else "blocked"
    return {
        "schema": MANIFEST_SCHEMA,
        "generator": {"name": "compact-derived-artifacts", "version": SCRIPT_VERSION},
        "created_at": now(),
        "updated_at": now(),
        "session": str(session),
        "session_id": str(session_payload.get("session_id") or session.name),
        "mode": args.mode,
        "action": args.action,
        "status": status,
        "pinned": pinned,
        "pin_sources": pin_sources if pinned else [],
        "eligibility": {"passed": not blockers, "blockers": sorted(set(blockers)), "warnings": warnings},
        "export_gate": export,
        "raw_audio": raw,
        "raw_inventory": raw_inventory(raw),
        "retained_outputs": critical,
        "inventory": {
            "media_suffixes": sorted(MEDIA_SUFFIXES),
            "candidate_files": len(inventory),
            "candidate_bytes": total_bytes,
            "fingerprint": inventory_fingerprint(inventory),
            "by_category": aggregate(inventory, "category"),
            "by_suffix": aggregate(inventory, "suffix"),
        },
        "application": {
            "requested": args.action == "apply",
            "derived_media_confirmed": args.confirm_delete_derived_media,
            "raw_delete_requested": args.mode == TRANSCRIPT_ONLY_MODE,
            "raw_delete_confirmed": args.confirm_delete_raw,
            "deleted_files": 0,
            "deleted_bytes": 0,
            "deleted_derived_files": 0,
            "deleted_derived_bytes": 0,
            "deleted_raw_files": 0,
            "deleted_raw_bytes": 0,
            "failures": [],
        },
        "verification": None,
        "audit_log": str(session / "derived/retention/derived_compaction_audit.jsonl"),
    }


def compare_identities(session: Path, identities: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    for identity in identities:
        relative = Path(str(identity["path"]))
        path = session / relative
        if not path.is_file():
            failures.append(f"missing:{relative}")
            continue
        stat = path.stat()
        if stat.st_size != int(identity["bytes"]):
            failures.append(f"size_changed:{relative}")
            continue
        expected_hash = identity.get("sha256")
        if expected_hash and sha256_file(path) != expected_hash:
            failures.append(f"sha256_changed:{relative}")
    return failures


def apply_manifest(session: Path, manifest: dict[str, Any], args: argparse.Namespace) -> None:
    application = manifest["application"]
    if not args.confirm_delete_derived_media:
        manifest["status"] = "blocked"
        manifest["eligibility"]["blockers"].append("confirmation_required")
        return
    if args.mode == TRANSCRIPT_ONLY_MODE and not args.confirm_delete_raw:
        manifest["status"] = "blocked"
        manifest["eligibility"]["blockers"].append("raw_delete_confirmation_required")
        return
    if not manifest["eligibility"]["passed"]:
        return

    output_preflight_failures = compare_identities(session, manifest["retained_outputs"])
    if output_preflight_failures:
        manifest["status"] = "blocked"
        manifest["eligibility"]["blockers"].extend(output_preflight_failures)
        manifest["eligibility"]["blockers"] = sorted(
            set(manifest["eligibility"]["blockers"])
        )
        return

    inventory, warnings = media_inventory(session)
    manifest["eligibility"]["warnings"] = sorted(
        set(manifest["eligibility"]["warnings"] + warnings)
    )
    failures: list[dict[str, Any]] = []
    deleted_files = 0
    deleted_bytes = 0
    derived = (session / "derived").resolve()
    for item in inventory:
        path = session / str(item["path"])
        try:
            resolved = path.resolve(strict=True)
            if path.is_symlink() or not is_within(resolved, derived):
                raise OSError("candidate no longer resolves inside derived")
            path.unlink()
        except OSError as error:
            failures.append({"path": item["path"], "reason": str(error)})
            continue
        deleted_files += 1
        deleted_bytes += int(item["bytes"])

    deleted_raw_files = 0
    deleted_raw_bytes = 0
    if args.mode == TRANSCRIPT_ONLY_MODE:
        audio_root = (session / "audio").resolve()
        for item in manifest["raw_audio"]:
            path = session / str(item["path"])
            try:
                resolved = path.resolve(strict=True)
                if path.is_symlink() or not is_within(resolved, audio_root):
                    raise OSError("raw candidate no longer resolves inside audio")
                path.unlink()
            except OSError as error:
                failures.append({"path": item["path"], "reason": str(error)})
                continue
            deleted_raw_files += 1
            deleted_raw_bytes += int(item["bytes"])

    raw_failures = (
        absent_identity_failures(session, manifest["raw_audio"])
        if args.mode == TRANSCRIPT_ONLY_MODE
        else compare_identities(session, manifest["raw_audio"])
    )
    output_failures = compare_identities(session, manifest["retained_outputs"])
    remaining, _ = media_inventory(session)
    application.update(
        {
            "deleted_files": deleted_files + deleted_raw_files,
            "deleted_bytes": deleted_bytes + deleted_raw_bytes,
            "deleted_derived_files": deleted_files,
            "deleted_derived_bytes": deleted_bytes,
            "deleted_raw_files": deleted_raw_files,
            "deleted_raw_bytes": deleted_raw_bytes,
            "failures": failures[:50],
            "failure_count": len(failures),
            "completed_at": now(),
        }
    )
    verification_failures = raw_failures + output_failures
    if remaining:
        verification_failures.append(f"derived_media_remaining:{len(remaining)}")
    manifest["verification"] = {
        "passed": not verification_failures,
        "checked_at": now(),
        "raw_preserved": args.mode == KEEP_RAW_MODE and not raw_failures,
        "raw_deleted": args.mode == TRANSCRIPT_ONLY_MODE and not raw_failures,
        "retained_outputs_preserved": not output_failures,
        "remaining_media_files": len(remaining),
        "remaining_media_bytes": sum(int(item["bytes"]) for item in remaining),
        "failures": verification_failures[:100],
    }
    manifest["status"] = "applied" if not verification_failures else "partial"
    manifest["updated_at"] = now()
    append_jsonl(
        session / "derived/retention/derived_compaction_audit.jsonl",
        {
            "schema": AUDIT_SCHEMA,
            "created_at": now(),
            "session_id": manifest["session_id"],
            "action": (
                "delete_derived_media_and_raw"
                if args.mode == TRANSCRIPT_ONLY_MODE
                else "delete_derived_media"
            ),
            "mode": manifest["mode"],
            "status": manifest["status"],
            "inventory_fingerprint": manifest["inventory"]["fingerprint"],
            "deleted_files": deleted_files + deleted_raw_files,
            "deleted_bytes": deleted_bytes + deleted_raw_bytes,
            "deleted_derived_files": deleted_files,
            "deleted_derived_bytes": deleted_bytes,
            "deleted_raw_files": deleted_raw_files,
            "deleted_raw_bytes": deleted_raw_bytes,
            "failure_count": len(failures),
            "raw_preserved": args.mode == KEEP_RAW_MODE and not raw_failures,
            "raw_deleted": args.mode == TRANSCRIPT_ONLY_MODE and not raw_failures,
            "retained_outputs_preserved": not output_failures,
        },
    )


def verify_manifest(session: Path, existing: dict[str, Any] | None) -> dict[str, Any]:
    if existing is None or existing.get("schema") != MANIFEST_SCHEMA:
        return {
            "schema": MANIFEST_SCHEMA,
            "generator": {"name": "compact-derived-artifacts", "version": SCRIPT_VERSION},
            "created_at": now(),
            "updated_at": now(),
            "session": str(session),
            "session_id": session.name,
            "mode": KEEP_RAW_MODE,
            "action": "verify",
            "status": "not_compacted",
            "eligibility": {
                "passed": False,
                "blockers": ["compaction_manifest_missing"],
                "warnings": [],
            },
            "verification": {
                "passed": False,
                "checked_at": now(),
                "failures": ["compaction_manifest_missing"],
            },
        }

    if existing.get("status") not in {"applied", "verified", "partial", "reexpanded_or_invalid"}:
        existing["action"] = "verify"
        existing["updated_at"] = now()
        existing["status"] = "not_compacted"
        eligibility = existing.setdefault("eligibility", {})
        eligibility["passed"] = False
        eligibility["blockers"] = sorted(
            set(list(eligibility.get("blockers") or []) + ["compaction_not_applied"])
        )
        existing["verification"] = {
            "passed": False,
            "checked_at": now(),
            "failures": ["compaction_not_applied"],
        }
        return existing

    mode = str(existing.get("mode") or KEEP_RAW_MODE)
    raw_failures = (
        absent_identity_failures(session, list(existing.get("raw_audio") or []))
        if mode == TRANSCRIPT_ONLY_MODE
        else compare_identities(session, list(existing.get("raw_audio") or []))
    )
    output_failures = compare_identities(session, list(existing.get("retained_outputs") or []))
    remaining, warnings = media_inventory(session)
    failures = raw_failures + output_failures
    if remaining:
        failures.append(f"derived_media_present:{len(remaining)}")
    existing["action"] = "verify"
    existing["updated_at"] = now()
    existing["status"] = "verified" if not failures else "reexpanded_or_invalid"
    existing.setdefault("eligibility", {}).setdefault("warnings", [])
    existing["eligibility"]["warnings"] = sorted(
        set(existing["eligibility"]["warnings"] + warnings)
    )
    existing["verification"] = {
        "passed": not failures,
        "checked_at": now(),
        "raw_preserved": mode == KEEP_RAW_MODE and not raw_failures,
        "raw_deleted": mode == TRANSCRIPT_ONLY_MODE and not raw_failures,
        "retained_outputs_preserved": not output_failures,
        "remaining_media_files": len(remaining),
        "remaining_media_bytes": sum(int(item["bytes"]) for item in remaining),
        "failures": failures[:100],
    }
    append_jsonl(
        session / "derived/retention/derived_compaction_audit.jsonl",
        {
            "schema": AUDIT_SCHEMA,
            "created_at": now(),
            "session_id": existing.get("session_id") or session.name,
            "action": "verify_derived_compaction",
            "mode": mode,
            "status": existing["status"],
            "remaining_media_files": len(remaining),
            "remaining_media_bytes": sum(int(item["bytes"]) for item in remaining),
            "raw_preserved": mode == KEEP_RAW_MODE and not raw_failures,
            "raw_deleted": mode == TRANSCRIPT_ONLY_MODE and not raw_failures,
            "retained_outputs_preserved": not output_failures,
        },
    )
    return existing


def manifest_markdown(manifest: dict[str, Any]) -> str:
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    raw = manifest.get("raw_inventory") if isinstance(manifest.get("raw_inventory"), dict) else {}
    application = manifest.get("application") if isinstance(manifest.get("application"), dict) else {}
    verification = manifest.get("verification") if isinstance(manifest.get("verification"), dict) else {}
    eligibility = manifest.get("eligibility") if isinstance(manifest.get("eligibility"), dict) else {}
    blockers = eligibility.get("blockers") if isinstance(eligibility.get("blockers"), list) else []
    warnings = eligibility.get("warnings") if isinstance(eligibility.get("warnings"), list) else []
    lines = [
        "# Derived Compaction",
        "",
        f"- Session: `{manifest.get('session')}`",
        f"- Status: `{manifest.get('status')}`",
        f"- Mode: `{manifest.get('mode')}`",
        f"- Pinned: `{str(bool(manifest.get('pinned'))).lower()}`",
        f"- Candidate files: `{int(inventory.get('candidate_files') or 0)}`",
        f"- Candidate bytes: `{int(inventory.get('candidate_bytes') or 0)}`",
        f"- Raw candidate files: `{int(raw.get('candidate_files') or 0)}`",
        f"- Raw candidate bytes: `{int(raw.get('candidate_bytes') or 0)}`",
        f"- Deleted files: `{int(application.get('deleted_files') or 0)}`",
        f"- Deleted bytes: `{int(application.get('deleted_bytes') or 0)}`",
        f"- Deleted raw files: `{int(application.get('deleted_raw_files') or 0)}`",
        f"- Deleted raw bytes: `{int(application.get('deleted_raw_bytes') or 0)}`",
        f"- Verification passed: `{str(bool(verification.get('passed'))).lower()}`",
        "",
    ]
    if blockers:
        lines += ["## Blockers", ""] + [f"- `{item}`" for item in blockers] + [""]
    if warnings:
        lines += ["## Warnings", ""] + [f"- `{item}`" for item in warnings] + [""]
    raw_line = (
        "- Raw `audio/` files are intentionally absent; their former identities remain in this manifest."
        if manifest.get("mode") == TRANSCRIPT_ONLY_MODE
        else "- Raw `audio/` files."
    )
    lines += [
        "## Preserved",
        "",
        raw_line,
        "- Selected transcript, notes and verdict.",
        "- JSON, JSONL and Markdown provenance.",
        "",
    ]
    return "\n".join(lines)


def manifest_path(session: Path, args: argparse.Namespace) -> Path:
    if args.out and args.target != "all":
        return args.out.expanduser()
    return session / "derived/retention/derived_compaction.json"


def process_session(
    session: Path,
    args: argparse.Namespace,
    *,
    pins: set[str],
    pin_sources: list[str],
) -> tuple[dict[str, Any], int]:
    output = manifest_path(session, args)
    markdown = output.with_suffix(".md")
    with compaction_lock(session):
        existing = read_json(output)
        existing_applied = bool(
            existing
            and existing.get("status") in {"applied", "verified"}
            and isinstance(existing.get("verification"), dict)
            and existing["verification"].get("passed") is True
        )
        existing_archive = bool(
            existing_applied
            and existing.get("mode") == TRANSCRIPT_ONLY_MODE
            and existing.get("raw_audio")
            and not absent_identity_failures(session, list(existing.get("raw_audio") or []))
        )
        preserve_existing = bool(
            existing_applied
            and (
                args.action == "verify"
                or existing_archive
                or args.mode == KEEP_RAW_MODE
                or (session.name in pins and not args.include_pinned)
            )
        )
        if args.action == "verify" or preserve_existing:
            manifest = verify_manifest(session, existing)
            manifest["action"] = args.action
        else:
            manifest = build_manifest(
                session,
                args,
                pinned=session.name in pins,
                pin_sources=pin_sources,
            )
            if args.action == "apply":
                apply_manifest(session, manifest, args)
        atomic_write_json(output, manifest)
        atomic_write_text(markdown, manifest_markdown(manifest))
    passed = (
        manifest.get("status") in {"eligible", "applied", "verified"}
        and (
            args.action == "plan"
            or bool((manifest.get("verification") or {}).get("passed"))
        )
    )
    return manifest, 0 if passed else 2


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Derived Compaction Report",
        "",
        f"- Action: `{report['action']}`",
        f"- Sessions considered: `{summary['sessions_considered']}`",
        f"- Sessions eligible: `{summary['sessions_eligible']}`",
        f"- Sessions applied: `{summary['sessions_applied']}`",
        f"- Sessions skipped: `{summary['sessions_skipped']}`",
        f"- Candidate bytes: `{summary['candidate_bytes']}`",
        f"- Eligible candidate bytes: `{summary['eligible_candidate_bytes']}`",
        f"- Raw candidate bytes: `{summary['raw_candidate_bytes']}`",
        f"- Deleted bytes: `{summary['deleted_bytes']}`",
        f"- Deleted raw bytes: `{summary['deleted_raw_bytes']}`",
        "",
        "| Session | Status | Pinned | Derived bytes | Raw bytes | Deleted bytes |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["sessions"]:
        lines.append(
            f"| `{row['session_id']}` | `{row['status']}` | "
            f"`{str(row['pinned']).lower()}` | `{row['derived_candidate_bytes']}` | "
            f"`{row['raw_candidate_bytes']}` | `{row['deleted_bytes']}` |"
        )
    lines.append("")
    return "\n".join(lines)


def run_bulk(args: argparse.Namespace, pins: set[str], pin_sources: list[str]) -> int:
    root = args.sessions_root.expanduser().resolve()
    age = parse_age(args.older_than)
    cutoff = datetime.now(timezone.utc) - age if age else None
    rows: list[dict[str, Any]] = []
    worst_status = 0
    for session in reversed(list_sessions(root)):
        payload = read_json(session / "session.json") or {}
        if cutoff is not None and session_ended_at(session, payload) > cutoff:
            continue
        manifest, status = process_session(
            session,
            args,
            pins=pins,
            pin_sources=pin_sources,
        )
        inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
        raw = manifest.get("raw_inventory") if isinstance(manifest.get("raw_inventory"), dict) else {}
        application = manifest.get("application") if isinstance(manifest.get("application"), dict) else {}
        verification = manifest.get("verification") if isinstance(manifest.get("verification"), dict) else {}
        derived_candidate_files = int(inventory.get("candidate_files") or 0)
        derived_candidate_bytes = int(inventory.get("candidate_bytes") or 0)
        raw_already_deleted = verification.get("raw_deleted") is True
        raw_candidate_files = 0 if raw_already_deleted else int(raw.get("candidate_files") or 0)
        raw_candidate_bytes = 0 if raw_already_deleted else int(raw.get("candidate_bytes") or 0)
        include_raw = args.mode == TRANSCRIPT_ONLY_MODE
        rows.append(
            {
                "session": str(session),
                "session_id": session.name,
                "status": manifest.get("status"),
                "mode": manifest.get("mode"),
                "pinned": bool(manifest.get("pinned")),
                "derived_candidate_files": derived_candidate_files,
                "derived_candidate_bytes": derived_candidate_bytes,
                "raw_candidate_files": raw_candidate_files,
                "raw_candidate_bytes": raw_candidate_bytes,
                "archived_raw_files": (
                    int(raw.get("candidate_files") or 0) if raw_already_deleted else 0
                ),
                "archived_raw_bytes": (
                    int(raw.get("candidate_bytes") or 0) if raw_already_deleted else 0
                ),
                "candidate_files": derived_candidate_files + (raw_candidate_files if include_raw else 0),
                "candidate_bytes": derived_candidate_bytes + (raw_candidate_bytes if include_raw else 0),
                "deleted_files": int(application.get("deleted_files") or 0),
                "deleted_bytes": int(application.get("deleted_bytes") or 0),
                "deleted_raw_files": int(application.get("deleted_raw_files") or 0),
                "deleted_raw_bytes": int(application.get("deleted_raw_bytes") or 0),
                "blockers": list((manifest.get("eligibility") or {}).get("blockers") or []),
            }
        )
        if status != 0 and manifest.get("status") not in {"blocked", "not_compacted"}:
            worst_status = 2

    summary = {
        "sessions_considered": len(rows),
        "sessions_eligible": sum(row["status"] in {"eligible", "applied", "verified"} for row in rows),
        "sessions_applied": sum(row["status"] in {"applied", "verified"} for row in rows),
        "sessions_skipped": sum(row["status"] in {"blocked", "not_compacted"} for row in rows),
        "candidate_files": sum(row["candidate_files"] for row in rows),
        "candidate_bytes": sum(row["candidate_bytes"] for row in rows),
        "derived_candidate_files": sum(row["derived_candidate_files"] for row in rows),
        "derived_candidate_bytes": sum(row["derived_candidate_bytes"] for row in rows),
        "raw_candidate_files": sum(row["raw_candidate_files"] for row in rows),
        "raw_candidate_bytes": sum(row["raw_candidate_bytes"] for row in rows),
        "archived_raw_files": sum(row["archived_raw_files"] for row in rows),
        "archived_raw_bytes": sum(row["archived_raw_bytes"] for row in rows),
        "eligible_candidate_files": sum(
            row["candidate_files"]
            for row in rows
            if row["status"] in {"eligible", "applied", "verified"}
        ),
        "eligible_candidate_bytes": sum(
            row["candidate_bytes"]
            for row in rows
            if row["status"] in {"eligible", "applied", "verified"}
        ),
        "deleted_files": sum(row["deleted_files"] for row in rows),
        "deleted_bytes": sum(row["deleted_bytes"] for row in rows),
        "deleted_raw_files": sum(row["deleted_raw_files"] for row in rows),
        "deleted_raw_bytes": sum(row["deleted_raw_bytes"] for row in rows),
    }
    report = {
        "schema": REPORT_SCHEMA,
        "generator": {"name": "compact-derived-artifacts", "version": SCRIPT_VERSION},
        "created_at": now(),
        "action": args.action,
        "mode": "from_manifest" if args.action == "verify" else args.mode,
        "sessions_root": str(root),
        "older_than": args.older_than,
        "exclude_pinned": not args.include_pinned,
        "pin_sources": pin_sources,
        "summary": summary,
        "sessions": rows,
    }
    output = args.report_out.expanduser() if args.report_out else (
        root / "_reports/retention-compaction/derived_compaction_report.json"
    )
    atomic_write_json(output, report)
    atomic_write_text(output.with_suffix(".md"), report_markdown(report))
    print(f"derived_compaction_report: {output}")
    print(
        "summary: "
        f"sessions={summary['sessions_considered']} "
        f"eligible={summary['sessions_eligible']} "
        f"applied={summary['sessions_applied']} "
        f"eligible_candidate_bytes={summary['eligible_candidate_bytes']} "
        f"deleted_bytes={summary['deleted_bytes']}"
    )
    return worst_status


def main() -> int:
    args = parse_args()
    if args.action == "apply" and not args.confirm_delete_derived_media:
        print("apply requires --confirm-delete-derived-media", file=sys.stderr)
        return 2
    if (
        args.action == "apply"
        and args.mode == TRANSCRIPT_ONLY_MODE
        and not args.confirm_delete_raw
    ):
        print("transcript_only apply requires --confirm-delete-raw", file=sys.stderr)
        return 2
    sessions_root = args.sessions_root.expanduser().resolve()
    pins, pin_sources = discover_pins(sessions_root, args.pin_file)
    if args.target == "all":
        return run_bulk(args, pins, pin_sources)
    session = resolve_session(args.target, sessions_root)
    manifest, status = process_session(
        session,
        args,
        pins=pins,
        pin_sources=pin_sources,
    )
    inventory = manifest.get("inventory") if isinstance(manifest.get("inventory"), dict) else {}
    raw = manifest.get("raw_inventory") if isinstance(manifest.get("raw_inventory"), dict) else {}
    application = manifest.get("application") if isinstance(manifest.get("application"), dict) else {}
    print(f"derived_compaction: {manifest_path(session, args)}")
    print(f"status: {manifest.get('status')}")
    print(f"candidate_files: {int(inventory.get('candidate_files') or 0)}")
    print(f"candidate_bytes: {int(inventory.get('candidate_bytes') or 0)}")
    print(f"raw_candidate_files: {int(raw.get('candidate_files') or 0)}")
    print(f"raw_candidate_bytes: {int(raw.get('candidate_bytes') or 0)}")
    print(f"deleted_files: {int(application.get('deleted_files') or 0)}")
    print(f"deleted_bytes: {int(application.get('deleted_bytes') or 0)}")
    print(f"deleted_raw_files: {int(application.get('deleted_raw_files') or 0)}")
    print(f"deleted_raw_bytes: {int(application.get('deleted_raw_bytes') or 0)}")
    blockers = list((manifest.get("eligibility") or {}).get("blockers") or [])
    if blockers:
        print("blockers: " + ", ".join(blockers))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
