#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_VERSION = "0.1.1"
SCHEMA = "murmurmark.provider_payload_manifest/v1"
POLICY_SCHEMA = "murmurmark.retention_policy/v1"
EXPORT_SCHEMA = "murmurmark.export_manifest/v1"
RAW_AUDIO_SUFFIXES = {".caf", ".wav", ".flac", ".m4a", ".mp3", ".mp4", ".mkv"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an external-provider payload manifest without sending data.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, default=Path("examples/retention-policy.local-first.json"))
    parser.add_argument("--export-manifest", type=Path, help="Defaults to exports/private/<session>/export_manifest.json.")
    parser.add_argument("--provider", default="unspecified_provider")
    parser.add_argument("--purpose", default="reviewed_export_handoff")
    parser.add_argument("--out", type=Path, help="Defaults to SESSION/derived/retention/provider_payload_manifest.json.")
    return parser.parse_args()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_path(path: Path) -> str:
    if not path.is_absolute():
        return shlex.quote(str(path))
    try:
        display = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        display = path
    return shlex.quote(str(display))


def command_item(id_: str, label: str, command: str) -> dict[str, str]:
    return {"id": id_, "label": label, "command": command}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def session_id(session: Path) -> str:
    payload = read_json(session / "session.json") or {}
    return str(payload.get("session_id") or session.name)


def export_manifest_matches_session(manifest: dict[str, Any], session: Path) -> bool:
    manifest_session = manifest.get("session")
    if isinstance(manifest_session, str) and manifest_session.strip():
        try:
            return Path(manifest_session).expanduser().resolve() == session.expanduser().resolve()
        except OSError:
            return False
    return str(manifest.get("session_id") or "") == session_id(session)


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if not policy:
        raise SystemExit(f"retention policy not found or invalid JSON: {path}")
    if policy.get("schema") != POLICY_SCHEMA:
        raise SystemExit(f"unsupported retention policy schema: {policy.get('schema')}")
    return policy


def default_export_manifest(session: Path) -> Path:
    return Path("exports/private") / session.name / "export_manifest.json"


def exported_files(export_manifest: dict[str, Any], export_manifest_path: Path) -> list[dict[str, Any]]:
    files = export_manifest.get("files") if isinstance(export_manifest.get("files"), dict) else {}
    result: list[dict[str, Any]] = []
    for key, value in sorted(files.items()):
        if not isinstance(value, dict) or not value.get("path"):
            continue
        path = Path(str(value["path"]))
        if not path.is_absolute():
            path = export_manifest_path.parent / path.name
        exists = path.exists()
        suffix = path.suffix.lower()
        raw_audio = suffix in RAW_AUDIO_SUFFIXES
        item: dict[str, Any] = {
            "key": key,
            "path": str(path),
            "exists": exists,
            "bytes": path.stat().st_size if exists else int(value.get("bytes") or 0),
            "sha256": sha256_file(path) if exists and path.is_file() else None,
            "content_class": content_class(key, path),
            "raw_audio": raw_audio,
        }
        result.append(item)
    return result


def content_class(key: str, path: Path) -> str:
    name = path.name.lower()
    if "transcript" in key or "transcript" in name or "clean_dialogue" in name:
        return "transcript_or_dialogue"
    if "notes" in key or "notes" in name:
        return "meeting_notes"
    if "quality" in key or "verdict" in key or "quality" in name:
        return "quality_metadata"
    if "review" in key or "review" in name:
        return "review_metadata"
    if path.suffix.lower() == ".json":
        return "evidence_json"
    return "export_file"


def policy_external(policy: dict[str, Any]) -> dict[str, Any]:
    providers = policy.get("external_providers") if isinstance(policy.get("external_providers"), dict) else {}
    return {
        "allow": bool(providers.get("allow", False)),
        "require_payload_manifest": bool(providers.get("require_payload_manifest", True)),
        "raw_audio_allowed": False,
    }


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser()
    policy_path = args.policy.expanduser()
    policy = load_policy(policy_path)
    external_policy = policy_external(policy)
    export_path = args.export_manifest.expanduser() if args.export_manifest else default_export_manifest(session)
    export = read_json(export_path)
    blockers: list[str] = []
    warnings: list[str] = []

    if not external_policy["allow"]:
        blockers.append("external_providers_disabled_by_policy")
    if not export:
        blockers.append("missing_or_invalid_export_manifest")
        export_status: dict[str, Any] = {"path": str(export_path), "found": export_path.exists(), "valid": False}
        candidates: list[dict[str, Any]] = []
    else:
        blockers_in_export = export.get("blockers") if isinstance(export.get("blockers"), list) else []
        valid_export_schema = export.get("schema") == EXPORT_SCHEMA
        session_matches = valid_export_schema and export_manifest_matches_session(export, session)
        export_status = {
            "path": str(export_path),
            "found": True,
            "valid": valid_export_schema,
            "session_matches": session_matches,
            "status": export.get("status"),
            "blockers": blockers_in_export,
            "selected_profile": export.get("selected_profile"),
            "verdict": export.get("verdict"),
            "use_gate": export.get("use_gate"),
        }
        if not valid_export_schema:
            blockers.append("unsupported_export_manifest_schema")
        if valid_export_schema and not session_matches:
            blockers.append("export_manifest_session_mismatch")
        if export.get("status") not in {"exported", "exported_with_warnings"}:
            blockers.append("export_not_successful")
        if blockers_in_export:
            blockers.append("export_manifest_has_blockers")
        candidates = exported_files(export, export_path)

    for item in candidates:
        if item["raw_audio"]:
            blockers.append(f"raw_audio_in_export_manifest:{item['key']}")
        if not item["exists"]:
            warnings.append(f"missing_export_file:{item['key']}")

    payload_files = [] if blockers else candidates
    status = "ready_for_review" if not blockers else "blocked"
    return {
        "schema": SCHEMA,
        "generator": {"name": "build-provider-payload-manifest", "version": SCRIPT_VERSION},
        "created_at": now(),
        "status": status,
        "session": str(session),
        "session_id": session_id(session),
        "provider": args.provider,
        "purpose": args.purpose,
        "policy": {
            "path": str(policy_path),
            "name": policy.get("name"),
            "external_providers": external_policy,
        },
        "export_manifest": export_status,
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "candidate_files": candidates,
        "payload_files": payload_files,
        "payload_file_count": len(payload_files),
        "payload_bytes": sum(int(item.get("bytes") or 0) for item in payload_files),
        "raw_audio_included": any(bool(item.get("raw_audio")) for item in payload_files),
        "sends_data": False,
    }


def add_handoff(payload: dict[str, Any], export_path: Path, out: Path) -> None:
    open_commands = [
        command_item("open_provider_payload_manifest", "Inspect provider payload manifest.", f"less {command_path(out)}")
    ]
    if export_path:
        open_commands.append(command_item("open_export_manifest", "Inspect export manifest.", f"less {command_path(export_path)}"))
    next_commands = [
        command_item(
            "inspect_payload_manifest",
            "Inspect the payload manifest before any external handoff.",
            f"less {command_path(out)}",
        )
    ]
    payload["recommended_next"] = next_commands[0]["command"]
    payload["next_commands"] = next_commands
    payload["open_commands"] = open_commands


def main() -> int:
    args = parse_args()
    session = args.session.expanduser()
    out = args.out.expanduser() if args.out else session / "derived/retention/provider_payload_manifest.json"
    manifest = build_manifest(args)
    export_path = args.export_manifest.expanduser() if args.export_manifest else default_export_manifest(session)
    add_handoff(manifest, export_path, out)
    write_json(out, manifest)
    print(f"provider_payload_manifest: {out}")
    print(f"status: {manifest['status']}")
    print(f"payload_files: {manifest['payload_file_count']}")
    if manifest["blockers"]:
        print("blockers: " + ", ".join(manifest["blockers"]))
    if manifest["warnings"]:
        print("warnings: " + ", ".join(manifest["warnings"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
