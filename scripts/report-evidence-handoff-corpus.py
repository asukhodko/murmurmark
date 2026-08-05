#!/usr/bin/env python3
"""Build a deterministic corpus report for Evidence Handoff v2."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import evidence_handoff_v2 as handoff


SCHEMA = "murmurmark.evidence_handoff_corpus/v2"
SCRIPT_VERSION = "0.1.0"
SESSION_RE = re.compile(r"^20\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(?:[-_].*)?$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report Evidence Handoff v2 corpus gates.")
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--sessions-root", type=Path, default=Path("sessions"))
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("sessions/_reports/evidence-handoff-v2"),
    )
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--no-replay-check", action="store_true")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--max-sessions", type=int)
    return parser.parse_args()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def discover(args: argparse.Namespace) -> list[Path]:
    if args.sessions:
        rows = [path.expanduser().resolve() for path in args.sessions]
    else:
        root = args.sessions_root.expanduser().resolve()
        rows = [
            path.resolve()
            for path in root.iterdir()
            if path.is_dir()
            and SESSION_RE.fullmatch(path.name)
            and (path / "session.json").is_file()
            and (path / "derived/readiness/session_readiness.json").is_file()
        ]
    rows = sorted(set(rows), key=lambda path: path.name)
    if args.max_sessions is not None:
        rows = rows[: max(0, args.max_sessions)]
    return rows


def session_row(
    session: Path,
    *,
    refresh: bool,
    replay_check: bool,
) -> dict[str, Any]:
    errors: list[str] = []
    first_bytes: bytes | None = None
    if refresh:
        try:
            handoff.build_handoff(session)
        except handoff.HandoffError as error:
            errors.append(f"build:{error}")
    manifest_path = session / "derived/handoff-v2/handoff_manifest.json"
    if manifest_path.is_file():
        first_bytes = manifest_path.read_bytes()
    manifest, validation = handoff.load_valid_handoff(session)
    errors.extend(validation)
    replay_passed: bool | None = None
    if replay_check and manifest is not None:
        try:
            replay = handoff.build_handoff(session)
            replay_passed = (
                replay.get("semantic_fingerprint") == manifest.get("semantic_fingerprint")
                and first_bytes == manifest_path.read_bytes()
            )
        except handoff.HandoffError as error:
            errors.append(f"replay:{error}")
            replay_passed = False
    integrity = (
        manifest.get("referential_integrity")
        if isinstance(manifest, dict)
        and isinstance(manifest.get("referential_integrity"), dict)
        else {}
    )
    review = (
        manifest.get("review")
        if isinstance(manifest, dict) and isinstance(manifest.get("review"), dict)
        else {}
    )
    return {
        "session": f"sessions/{session.name}",
        "session_id": session.name,
        "valid": manifest is not None and not errors,
        "state": manifest.get("state") if manifest else "missing",
        "selected_profile": manifest.get("selected_profile") if manifest else None,
        "verdict": manifest.get("verdict") if manifest else None,
        "semantic_fingerprint": manifest.get("semantic_fingerprint") if manifest else None,
        "export_allowed": bool((manifest.get("export") or {}).get("allowed")) if manifest else False,
        "referential_integrity_passed": bool(integrity.get("passed")) if manifest else False,
        "missing_evidence_ids": len(integrity.get("missing_utterance_ids") or []),
        "items_without_evidence": len(integrity.get("items_without_evidence") or []),
        "mandatory_review_count": int(review.get("mandatory_count") or 0),
        "mandatory_review_seconds": float(review.get("mandatory_seconds") or 0.0),
        "deterministic_replay_passed": replay_passed,
        "blockers": list(manifest.get("blockers") or []) if manifest else [],
        "validation_errors": sorted(set(errors)),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    gates = report["gates"]
    lines = [
        "# Evidence Handoff v2 Corpus",
        "",
        f"- Sessions: `{summary['sessions']}`",
        f"- Valid manifests: `{summary['valid_manifests']}`",
        f"- Exportable: `{summary['exportable']}`",
        f"- Referential-integrity failures: `{summary['referential_integrity_failures']}`",
        f"- Deterministic replay failures: `{summary['deterministic_replay_failures']}`",
        f"- Corpus gate: `{'PASS' if gates['passed'] else 'FAIL'}`",
        "",
        "## States",
        "",
    ]
    for state, count in report["by_state"].items():
        lines.append(f"- `{state}`: `{count}`")
    lines.extend(["", "## Sessions", ""])
    lines.append("| Session | State | Profile | Export | Integrity | Replay |")
    lines.append("|---|---|---|---:|---:|---:|")
    for row in report["sessions"]:
        lines.append(
            f"| `{row['session_id']}` | `{row['state']}` | `{row['selected_profile'] or '-'}` | "
            f"`{str(row['export_allowed']).lower()}` | "
            f"`{str(row['referential_integrity_passed']).lower()}` | "
            f"`{str(row['deterministic_replay_passed']).lower()}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    sessions = discover(args)
    rows = [
        session_row(
            session,
            refresh=args.refresh,
            replay_check=not args.no_replay_check,
        )
        for session in sessions
    ]
    by_state = dict(sorted(Counter(str(row["state"]) for row in rows).items()))
    invalid = sum(not row["valid"] for row in rows)
    integrity_failures = sum(not row["referential_integrity_passed"] for row in rows)
    replay_failures = sum(row["deterministic_replay_passed"] is False for row in rows)
    stale = sum(
        any(str(error).startswith("stale_input") for error in row["validation_errors"])
        for row in rows
    )
    passed = bool(rows) and invalid == 0 and integrity_failures == 0 and replay_failures == 0 and stale == 0
    report = {
        "schema": SCHEMA,
        "version": 2,
        "generator": {"name": "report-evidence-handoff-corpus", "version": SCRIPT_VERSION},
        "summary": {
            "sessions": len(rows),
            "valid_manifests": len(rows) - invalid,
            "invalid_manifests": invalid,
            "exportable": sum(bool(row["export_allowed"]) for row in rows),
            "review_required": by_state.get("review_required", 0),
            "blocked": by_state.get("blocked", 0),
            "no_speech": by_state.get("no_speech", 0),
            "referential_integrity_failures": integrity_failures,
            "deterministic_replay_failures": replay_failures,
            "stale_manifests": stale,
        },
        "by_state": by_state,
        "gates": {
            "passed": passed,
            "all_manifests_valid": invalid == 0,
            "all_evidence_references_valid": integrity_failures == 0,
            "all_replays_deterministic": replay_failures == 0,
            "no_stale_manifests": stale == 0,
        },
        "sessions": rows,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.out_dir / "evidence_handoff_corpus.json"
    md_path = args.out_dir / "evidence_handoff_corpus.md"
    write_atomic(json_path, canonical_json(report))
    write_atomic(md_path, render_markdown(report))
    print("evidence_handoff_corpus:")
    print(f"  sessions: {len(rows)}")
    print(f"  states: {json.dumps(by_state, sort_keys=True)}")
    print(f"  gate: {'PASS' if passed else 'FAIL'}")
    print(f"  report: {json_path}")
    if args.strict and not passed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
