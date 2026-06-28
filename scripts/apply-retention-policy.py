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
PLAN_SCHEMA = "murmurmark.retention_plan/v1"
POLICY_SCHEMA = "murmurmark.retention_policy/v1"
AUDIT_SCHEMA = "murmurmark.retention_audit_event/v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan or apply MurmurMark retention policy for one session.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--policy", type=Path, default=Path("examples/retention-policy.local-first.json"))
    parser.add_argument("--export-manifest", type=Path, help="Path to export_manifest.json. Defaults to exports/private/<session>/export_manifest.json.")
    parser.add_argument("--apply", action="store_true", help="Apply allowed destructive actions. Default is plan only.")
    parser.add_argument("--confirm-delete-raw", action="store_true", help="Required together with --apply before raw CAF deletion.")
    parser.add_argument("--out", type=Path, help="Plan path. Defaults to SESSION/derived/retention/retention_plan.json.")
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


def append_jsonl(path: Path, payloads: list[dict[str, Any]]) -> None:
    if not payloads:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for payload in payloads:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


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


def load_policy(path: Path) -> dict[str, Any]:
    policy = read_json(path)
    if not policy:
        raise SystemExit(f"retention policy not found or invalid JSON: {path}")
    if policy.get("schema") != POLICY_SCHEMA:
        raise SystemExit(f"unsupported retention policy schema: {policy.get('schema')}")
    return policy


def raw_audio_files(session: Path) -> list[dict[str, Any]]:
    manifest = read_json(session / "session.json") or {}
    files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
    results: list[dict[str, Any]] = []
    for source in ("mic", "remote"):
        entries = files.get(source) if isinstance(files.get(source), list) else []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("path"):
                continue
            path = session / str(entry["path"])
            if path.exists():
                stat = path.stat()
                results.append(
                    {
                        "source": source,
                        "path": rel(path, session),
                        "bytes": stat.st_size,
                        "sha256": sha256_file(path),
                        "exists": True,
                    }
                )
            else:
                results.append(
                    {
                        "source": source,
                        "path": str(entry["path"]),
                        "bytes": 0,
                        "sha256": None,
                        "exists": False,
                    }
                )
    return results


def default_export_manifest(session: Path) -> Path:
    return Path("exports/private") / session.name / "export_manifest.json"


def export_manifest_matches_session(manifest: dict[str, Any], session: Path) -> bool:
    manifest_session = manifest.get("session")
    if isinstance(manifest_session, str) and manifest_session.strip():
        try:
            return Path(manifest_session).expanduser().resolve() == session.expanduser().resolve()
        except OSError:
            return False
    return str(manifest.get("session_id") or "") == session_id(session)


def export_status(path: Path | None, session: Path) -> dict[str, Any]:
    if not path:
        return {"path": None, "found": False, "valid": False, "successful": False, "reason": "missing_export_manifest_path"}
    manifest = read_json(path)
    if not manifest:
        return {"path": str(path), "found": path.exists(), "valid": False, "successful": False, "reason": "invalid_or_missing_export_manifest"}
    blockers = manifest.get("blockers") if isinstance(manifest.get("blockers"), list) else []
    status = manifest.get("status")
    valid = manifest.get("schema") == "murmurmark.export_manifest/v1"
    session_matches = valid and export_manifest_matches_session(manifest, session)
    successful = valid and session_matches and status in {"exported", "exported_with_warnings"} and not blockers
    reason = None
    if not valid:
        reason = "unsupported_export_manifest_schema"
    elif not session_matches:
        reason = "export_manifest_session_mismatch"
    elif not successful:
        reason = "export_not_successful"
    return {
        "path": str(path),
        "found": True,
        "valid": valid,
        "session_matches": session_matches,
        "successful": successful,
        "reason": reason,
        "status": status,
        "blockers": blockers,
        "selected_profile": manifest.get("selected_profile"),
        "verdict": manifest.get("verdict"),
        "use_gate": manifest.get("use_gate"),
    }


def build_actions(session: Path, policy: dict[str, Any], export: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    action = ((policy.get("raw_audio") or {}).get("after_successful_export") or "keep").strip().lower()
    raw_files = raw_audio_files(session)
    if not raw_files:
        warnings.append("no_raw_audio_files_found")

    actions: list[dict[str, Any]] = []
    for item in raw_files:
        if action == "delete" and export.get("successful"):
            planned = "delete_raw_audio"
            reason = "policy_delete_after_successful_export"
        elif action == "delete":
            planned = "keep_raw_audio"
            reason = "export_not_successful"
        else:
            planned = "keep_raw_audio"
            reason = f"policy_{action}"
        actions.append({**item, "planned_action": planned, "reason": reason})
    return actions, warnings


def external_payload_policy(policy: dict[str, Any]) -> dict[str, Any]:
    providers = policy.get("external_providers") if isinstance(policy.get("external_providers"), dict) else {}
    return {
        "allow": bool(providers.get("allow", False)),
        "require_payload_manifest": bool(providers.get("require_payload_manifest", True)),
        "raw_audio_allowed": False,
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    session = args.session.expanduser()
    policy_path = args.policy.expanduser()
    policy = load_policy(policy_path)
    export_manifest = args.export_manifest.expanduser() if args.export_manifest else default_export_manifest(session)
    export = export_status(export_manifest, session)
    actions, warnings = build_actions(session, policy, export)

    delete_actions = [item for item in actions if item.get("planned_action") == "delete_raw_audio"]
    can_apply = bool(args.apply)
    if delete_actions and not args.confirm_delete_raw:
        can_apply = False
        warnings.append("delete_raw_requires_--confirm-delete-raw")
    if delete_actions and not export.get("successful"):
        can_apply = False
        warnings.append("delete_raw_requires_successful_export_manifest")

    return {
        "schema": PLAN_SCHEMA,
        "generator": {"name": "apply-retention-policy", "version": SCRIPT_VERSION},
        "created_at": now(),
        "mode": "apply" if args.apply else "plan",
        "session": str(session),
        "session_id": session_id(session),
        "policy": {
            "path": str(policy_path),
            "name": policy.get("name"),
            "raw_audio_after_successful_export": (policy.get("raw_audio") or {}).get("after_successful_export", "keep"),
            "exports": policy.get("exports", {}),
            "external_providers": external_payload_policy(policy),
        },
        "export_manifest": export,
        "actions": actions,
        "warnings": sorted(set(warnings)),
        "can_apply": can_apply,
        "applied": False,
        "audit_log": str(session / "derived/retention/retention_audit.jsonl"),
    }


def normalize_command_items(items: Any) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    if not isinstance(items, list):
        return commands
    for item in items:
        if not isinstance(item, dict) or not item.get("command"):
            continue
        commands.append(
            command_item(
                str(item.get("id") or f"step_{len(commands) + 1}"),
                str(item.get("label") or "Run the next readiness step."),
                str(item["command"]),
            )
        )
    return commands


def readiness_handoff(session: Path) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    readiness = read_json(session / "derived/readiness/session_readiness.json")
    if not readiness:
        return None, []
    gate = str(readiness.get("use_gate") or "")
    if gate == "ready_for_notes":
        return readiness, []
    commands = normalize_command_items(readiness.get("next_commands"))
    if commands:
        return readiness, commands
    if gate.startswith("pipeline_incomplete"):
        return readiness, [
            command_item(
                "process_session",
                "Run or refresh the full post-recording pipeline.",
                f"murmurmark process {command_path(session)}",
            )
        ]
    return readiness, [
        command_item(
            "review_next",
            "Refresh this session's review handoff.",
            f"murmurmark review next {command_path(session)}",
        )
    ]


def add_handoff(plan: dict[str, Any], out: Path) -> None:
    session = Path(str(plan["session"]))
    export = plan.get("export_manifest") if isinstance(plan.get("export_manifest"), dict) else {}
    export_path = Path(str(export.get("path"))) if export.get("path") else None
    readiness, readiness_next = readiness_handoff(session)
    next_commands: list[dict[str, str]] = []
    open_commands: list[dict[str, str]] = [
        command_item("open_retention_plan", "Inspect retention plan.", f"less {command_path(out)}")
    ]
    if readiness:
        open_commands.append(
            command_item(
                "open_session_readiness",
                "Inspect session readiness before retention/export decisions.",
                f"less {command_path(session / 'derived/readiness/session_readiness.md')}",
            )
        )
    if export_path:
        open_commands.append(command_item("open_export_manifest", "Inspect export manifest.", f"less {command_path(export_path)}"))
    if export.get("successful") and export_path:
        next_commands.append(
            command_item(
                "retention_payload",
                "Inventory any external-provider payload before handoff.",
                f"murmurmark retention payload {command_path(session)} --export-manifest {command_path(export_path)}",
            )
        )
    elif readiness_next:
        next_commands.extend(readiness_next)
    else:
        next_commands.append(
            command_item(
                "export_markdown",
                "Export a local Markdown handoff bundle before retention decisions.",
                f"murmurmark export {command_path(session)} --format markdown --include-json",
            )
        )
    plan["recommended_next"] = next_commands[0]["command"]
    plan["next_commands"] = next_commands
    plan["open_commands"] = open_commands


def apply_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    session = Path(str(plan["session"]))
    if not plan.get("can_apply"):
        return events
    for item in plan.get("actions", []):
        if item.get("planned_action") != "delete_raw_audio":
            continue
        path = session / str(item["path"])
        if not path.exists():
            item["applied_action"] = "already_missing"
            continue
        path.unlink()
        item["applied_action"] = "deleted"
        events.append(
            {
                "schema": AUDIT_SCHEMA,
                "created_at": now(),
                "session_id": plan.get("session_id"),
                "action": "delete_raw_audio",
                "path": item.get("path"),
                "source": item.get("source"),
                "bytes": item.get("bytes"),
                "sha256": item.get("sha256"),
                "reason": item.get("reason"),
            }
        )
    plan["applied"] = bool(events)
    return events


def main() -> int:
    args = parse_args()
    session = args.session.expanduser()
    out = args.out.expanduser() if args.out else session / "derived/retention/retention_plan.json"
    plan = build_plan(args)
    add_handoff(plan, out)
    events = apply_plan(plan)
    write_json(out, plan)
    append_jsonl(session / "derived/retention/retention_audit.jsonl", events)

    print(f"retention_plan: {out}")
    print(f"mode: {plan['mode']}")
    print(f"can_apply: {str(plan['can_apply']).lower()}")
    print(f"applied: {str(plan['applied']).lower()}")
    delete_count = sum(1 for item in plan["actions"] if item.get("planned_action") == "delete_raw_audio")
    keep_count = sum(1 for item in plan["actions"] if item.get("planned_action") == "keep_raw_audio")
    print(f"raw_audio: keep={keep_count} delete={delete_count}")
    if plan["warnings"]:
        print("warnings: " + ", ".join(plan["warnings"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
