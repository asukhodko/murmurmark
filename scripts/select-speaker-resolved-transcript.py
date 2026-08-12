#!/usr/bin/env python3
"""Select promoted speaker-resolved transcript evidence or exact aggregate fallback."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.1"
SCHEMA = "murmurmark.speaker_resolved_transcript_selection/v1"
POLICY_SCHEMA = "murmurmark.speaker_resolved_transcript_default_policy/v1"
READINESS_SCHEMA = "murmurmark.session_readiness/v1"
V3_REPORT_SCHEMA = "murmurmark.remote_speaker_coverage_report/v3"
V3_RICH_SCHEMA = "murmurmark.remote_speaker_rich_transcript/v3"
DEFAULT_POLICY = ROOT / "policies/speaker-resolved-transcript-default-v1.json"
DEFAULT_OUTPUT_DIR = Path("derived/transcript-rich/speaker-resolved-default-v1")
DEFAULT_V3_DIR = Path("derived/audit/remote-speaker-coverage-v3")
DEFAULT_ROSTER = Path("derived/transcript-rich/speaker-roster-v1.json")
DEFAULT_WESPEAKER_MODEL = (
    Path.home()
    / ".local/share/murmurmark/models/remote-speaker-representation-v1"
    / "wespeaker-resnet34-lm/speaker-embedding.onnx"
)


class SelectionError(RuntimeError):
    pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select promoted session-local speaker labels with exact aggregate fallback."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--coverage-dir", type=Path, default=DEFAULT_V3_DIR)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--refresh-evidence", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--require-speaker-resolved", action="store_true")
    parser.add_argument("--print-path", action="store_true")
    return parser.parse_args()


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SelectionError(f"expected_json_object:{path.name}")
    return value


def within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def resolve_session_path(session: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser()
    candidate = candidate if candidate.is_absolute() else session / candidate
    resolved = candidate.resolve()
    return resolved if within(resolved, session.resolve()) else None


def relative(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def identity(path: Path, session: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    if session is not None:
        display_path = relative(resolved, session)
    elif within(resolved, ROOT.resolve()):
        display_path = str(resolved.relative_to(ROOT.resolve()))
    else:
        display_path = path.name
    row: dict[str, Any] = {
        "path": display_path,
        "exists": path.is_file(),
    }
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return row


def same_identity(row: Any, path: Path) -> bool:
    return (
        isinstance(row, dict)
        and row.get("exists") is True
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def readiness_inputs(session: Path) -> tuple[dict[str, Any], str, Path, Path]:
    readiness_path = session / "derived/readiness/session_readiness.json"
    readiness = read_json(readiness_path)
    if readiness.get("schema") != READINESS_SCHEMA:
        raise SelectionError("readiness_schema_invalid")
    profile = str(readiness.get("selected_profile") or "").strip()
    if not profile:
        raise SelectionError("readiness_profile_missing")
    outputs = readiness.get("outputs") if isinstance(readiness.get("outputs"), dict) else {}
    transcript_row = outputs.get("transcript") if isinstance(outputs.get("transcript"), dict) else {}
    dialogue_row = outputs.get("clean_dialogue") if isinstance(outputs.get("clean_dialogue"), dict) else {}
    aggregate = resolve_session_path(session, transcript_row.get("path"))
    dialogue = resolve_session_path(session, dialogue_row.get("path"))
    if aggregate is None or not aggregate.is_file():
        raise SelectionError("aggregate_transcript_missing")
    if dialogue is None or not dialogue.is_file():
        raise SelectionError("selected_dialogue_missing")
    return readiness, profile, aggregate, dialogue


def validate_policy(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    try:
        policy = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError, SelectionError) as error:
        return None, [f"selector_policy_invalid:{type(error).__name__}"]
    if policy.get("schema") != POLICY_SCHEMA:
        reasons.append("selector_policy_schema_invalid")
    if policy.get("state") != "promoted":
        reasons.append("selector_policy_not_promoted")
    for key, row in (policy.get("required_evidence") or {}).items():
        if not isinstance(row, dict):
            reasons.append(f"policy_evidence_invalid:{key}")
            continue
        candidate = ROOT / str(row.get("path") or "")
        if not candidate.is_file():
            reasons.append(f"policy_evidence_missing:{key}")
        elif row.get("sha256") != sha256_file(candidate):
            reasons.append(f"policy_evidence_stale:{key}")
        elif key in {"coverage_corpus_manifest", "default_corpus_manifest"}:
            try:
                manifest = read_json(candidate)
            except (OSError, ValueError, json.JSONDecodeError, SelectionError):
                reasons.append(f"policy_evidence_invalid:{key}")
            else:
                if manifest.get("decision") != "PROMOTE":
                    reasons.append(f"policy_evidence_not_promoted:{key}")
    return policy, reasons


def run_checked(command: list[str]) -> tuple[bool, str]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    output = "\n".join(value.strip() for value in (completed.stdout, completed.stderr) if value.strip())
    return completed.returncode == 0, output[-2000:]


def refresh_key(session: Path, profile: str, dialogue: Path) -> str:
    roster = session / DEFAULT_ROSTER
    configured_model = os.environ.get("MURMURMARK_REMOTE_SPEAKER_WESPEAKER_MODEL")
    consensus_model = Path(configured_model).expanduser() if configured_model else DEFAULT_WESPEAKER_MODEL
    basis = {
        "profile": profile,
        "dialogue_sha256": sha256_file(dialogue),
        "speaker_roster_sha256": sha256_file(roster) if roster.is_file() else None,
        "remote_speaker_evidence_implementation_sha256": sha256_file(
            ROOT / "scripts/audit-remote-speaker-evidence.py"
        ),
        "remote_speaker_diarization_implementation_sha256": sha256_file(
            ROOT / "scripts/audit-remote-speaker-diarization.py"
        ),
        "coverage_implementation_sha256": sha256_file(
            ROOT / "scripts/audit-remote-speaker-coverage-v3.py"
        ),
        "consensus_model_sha256": sha256_file(consensus_model)
        if consensus_model.is_file()
        else None,
        "version": VERSION,
    }
    return sha256_bytes(compact_json_bytes(basis))


def refresh_evidence(
    session: Path, profile: str, dialogue: Path, output_dir: Path, policy: Path
) -> tuple[Path | None, str | None]:
    evidence_root = output_dir / "evidence" / refresh_key(session, profile, dialogue)
    v1_dir = evidence_root / "remote-speaker-evidence-v1"
    v2_dir = evidence_root / "remote-speaker-diarization-v2"
    v3_dir = evidence_root / "remote-speaker-coverage-v3"
    if v3_dir.is_dir():
        ok, detail = verify_v3(session, profile, v3_dir, policy)
        if ok:
            return v3_dir, None
        shutil.rmtree(evidence_root, ignore_errors=True)
    evidence_root.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            sys.executable,
            str(ROOT / "scripts/audit-remote-speaker-evidence.py"),
            str(session),
            "--profile",
            profile,
            "--out-dir",
            str(v1_dir),
            "--no-progress",
        ],
        [
            sys.executable,
            str(ROOT / "scripts/audit-remote-speaker-diarization.py"),
            str(session),
            "--profile",
            profile,
            "--v1-dir",
            str(v1_dir),
            "--out-dir",
            str(v2_dir),
            "--no-progress",
        ],
        [
            sys.executable,
            str(ROOT / "scripts/audit-remote-speaker-coverage-v3.py"),
            str(session),
            "--input-dir",
            str(v2_dir),
            "--out-dir",
            str(v3_dir),
        ],
    ]
    for index, command in enumerate(commands, start=1):
        ok, detail = run_checked(command)
        if not ok:
            return None, f"evidence_refresh_stage_{index}_failed:{detail or 'unknown'}"
    ok, detail = verify_v3(session, profile, v3_dir, policy)
    if not ok:
        return None, f"refreshed_v3_invalid:{detail}"
    return v3_dir, None


def coverage_failure_reason(session: Path, report: dict[str, Any]) -> str:
    """Return the deepest reproducible reason from the v3 -> v2 -> v1 report chain."""

    current = report
    deepest_reason = "coverage_not_publishable"
    visited: set[Path] = set()
    for _ in range(3):
        reasons = current.get("reasons") if isinstance(current.get("reasons"), list) else []
        specific = [
            str(reason)
            for reason in reasons
            if reason and str(reason) not in {"v1_speaker_evidence_not_publishable"}
        ]
        if specific:
            deepest_reason = specific[0]

        source = current.get("source") if isinstance(current.get("source"), dict) else {}
        v2_artifacts = source.get("v2_artifacts") if isinstance(source.get("v2_artifacts"), dict) else {}
        upstream = v2_artifacts.get("report") if isinstance(v2_artifacts.get("report"), dict) else None
        if upstream is None and isinstance(source.get("v1_report"), dict):
            upstream = source.get("v1_report")
        upstream_path = resolve_session_path(session, upstream.get("path") if upstream else None)
        if upstream_path is None or upstream_path in visited or not upstream_path.is_file():
            break
        visited.add(upstream_path)
        try:
            current = read_json(upstream_path)
        except (OSError, ValueError, json.JSONDecodeError, SelectionError):
            break
    return f"coverage_not_publishable:{deepest_reason}"


def verify_v3(
    session: Path, profile: str, coverage_dir: Path, policy_path: Path
) -> tuple[bool, str]:
    _, policy_reasons = validate_policy(policy_path)
    if policy_reasons:
        return False, policy_reasons[0]
    report_path = coverage_dir / "report.json"
    rich_json = coverage_dir / "transcript.rich.shadow.json"
    rich_md = coverage_dir / "transcript.rich.shadow.md"
    if not report_path.is_file():
        return False, "coverage_artifact_missing"
    try:
        report = read_json(report_path)
    except (OSError, ValueError, json.JSONDecodeError, SelectionError):
        return False, "coverage_json_invalid"
    if report.get("schema") != V3_REPORT_SCHEMA:
        return False, "coverage_schema_invalid"
    if report.get("decision") != "PUBLISH_EVIDENCE":
        return False, coverage_failure_reason(session, report)
    if not all(path.is_file() for path in (rich_json, rich_md)):
        return False, "coverage_artifact_missing"
    ok, detail = run_checked(
        [
            sys.executable,
            str(ROOT / "scripts/audit-remote-speaker-coverage-v3.py"),
            str(session),
            "--out-dir",
            str(coverage_dir),
            "--verify-only",
            "--require-promoted",
        ]
    )
    if not ok:
        return False, "coverage_verification_failed"
    try:
        rich = read_json(rich_json)
    except (OSError, ValueError, json.JSONDecodeError, SelectionError):
        return False, "coverage_json_invalid"
    if rich.get("schema") != V3_RICH_SCHEMA or rich.get("decision") != "PUBLISH_EVIDENCE":
        return False, "coverage_rich_not_publishable"
    source = report.get("source") if isinstance(report.get("source"), dict) else {}
    if str(source.get("profile") or "") != profile or str(rich.get("selected_profile") or "") != profile:
        return False, "coverage_profile_mismatch"
    policy = read_json(policy_path)
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    for gate in policy.get("required_session_gates") or []:
        if gates.get(gate) is not True:
            return False, f"coverage_gate_failed:{gate}"
    return True, detail


def selection_payload(
    session: Path,
    profile: str,
    aggregate: Path,
    dialogue: Path,
    coverage_dir: Path | None,
    policy_path: Path,
    fallback_reason: str | None,
) -> dict[str, Any]:
    selected = aggregate
    speaker_profile = "aggregate_colleagues"
    state = "fallback"
    rich_identity: dict[str, Any] | None = None
    coverage_report: dict[str, Any] | None = None
    if coverage_dir is not None and fallback_reason is None:
        selected = coverage_dir / "transcript.rich.shadow.md"
        speaker_profile = "remote_speaker_coverage_v3"
        state = "selected"
        rich_identity = identity(coverage_dir / "transcript.rich.shadow.json", session)
        coverage_report = identity(coverage_dir / "report.json", session)
    basis = {
        "schema": SCHEMA,
        "version": 1,
        "session_id": session.name,
        "state": state,
        "selected_profile": profile,
        "selected_speaker_profile": speaker_profile,
        "fallback_reason": fallback_reason,
        "aggregate_transcript": identity(aggregate, session),
        "selected_dialogue": identity(dialogue, session),
        "selected_transcript": identity(selected, session),
        "rich_transcript": rich_identity,
        "coverage_report": coverage_report,
        "speaker_roster": identity(session / DEFAULT_ROSTER, session),
        "policy": identity(policy_path),
    }
    return {
        **basis,
        "schema": SCHEMA,
        "version": 1,
        "generator": {
            "name": "select-speaker-resolved-transcript",
            "version": VERSION,
            "mode": "deterministic_fail_open",
        },
        "identity_scope": "session_local_anonymous",
        "unsupported_remote_words": "aggregate_colleagues",
        "human_names": "complete_fingerprint_bound_review_only",
        "batch_authoritative": True,
        "gates": {
            "aggregate_unchanged": True,
            "current_profile": True,
            "speaker_evidence_promoted": state == "selected",
            "exact_aggregate_fallback": state == "fallback"
            and same_identity(basis["selected_transcript"], aggregate),
        },
        "semantic_fingerprint": sha256_bytes(compact_json_bytes(basis)),
    }


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Speaker-Resolved Transcript Selection v1",
        "",
        f"State: `{payload['state']}`  ",
        f"Transcript profile: `{payload['selected_profile']}`  ",
        f"Speaker profile: `{payload['selected_speaker_profile']}`  ",
        f"Selected path: `{payload['selected_transcript']['path']}`",
    ]
    if payload.get("fallback_reason"):
        lines += [f"Fallback reason: `{payload['fallback_reason']}`"]
    lines += [
        "",
        "Speaker labels are session-local. Unsupported remote words remain `Colleagues`.",
        "The aggregate transcript remains unchanged and is the exact fail-open fallback.",
        "",
    ]
    return "\n".join(lines)


def verify_selection(
    session: Path, output_dir: Path, policy_path: Path, require_resolved: bool
) -> tuple[dict[str, Any] | None, list[str]]:
    report_path = output_dir / "selection.json"
    try:
        payload = read_json(report_path)
        _, profile, aggregate, dialogue = readiness_inputs(session)
    except (OSError, ValueError, json.JSONDecodeError, SelectionError) as error:
        return None, [f"selection_unavailable:{error}"]
    reasons: list[str] = []
    if payload.get("schema") != SCHEMA:
        reasons.append("selection_schema_invalid")
    if payload.get("selected_profile") != profile:
        reasons.append("selection_profile_stale")
    if not same_identity(payload.get("aggregate_transcript"), aggregate):
        reasons.append("selection_aggregate_stale")
    if not same_identity(payload.get("selected_dialogue"), dialogue):
        reasons.append("selection_dialogue_stale")
    if not same_identity(payload.get("policy"), policy_path):
        reasons.append("selection_policy_stale")
    roster_path = session / DEFAULT_ROSTER
    roster_row = payload.get("speaker_roster")
    if roster_path.is_file():
        if not same_identity(roster_row, roster_path):
            reasons.append("selection_speaker_roster_stale")
    elif not (
        isinstance(roster_row, dict)
        and roster_row.get("exists") is False
        and roster_row.get("path") == str(DEFAULT_ROSTER)
    ):
        reasons.append("selection_speaker_roster_stale")
    selected = resolve_session_path(session, (payload.get("selected_transcript") or {}).get("path"))
    if selected is None or not same_identity(payload.get("selected_transcript"), selected):
        reasons.append("selection_output_stale")
    state = payload.get("state")
    if state == "selected":
        coverage_report = resolve_session_path(session, (payload.get("coverage_report") or {}).get("path"))
        if coverage_report is None:
            reasons.append("selection_coverage_report_missing")
        else:
            coverage_dir = coverage_report.parent
            valid, reason = verify_v3(session, profile, coverage_dir, policy_path)
            if not valid:
                reasons.append(reason)
    elif state == "fallback":
        if selected != aggregate or payload.get("selected_speaker_profile") != "aggregate_colleagues":
            reasons.append("selection_fallback_not_exact")
        if require_resolved:
            reasons.append("speaker_resolved_required")
    else:
        reasons.append("selection_state_invalid")
    basis = {
        key: payload.get(key)
        for key in (
            "schema",
            "version",
            "session_id",
            "state",
            "selected_profile",
            "selected_speaker_profile",
            "fallback_reason",
            "aggregate_transcript",
            "selected_dialogue",
            "selected_transcript",
            "rich_transcript",
            "coverage_report",
            "speaker_roster",
            "policy",
        )
    }
    if payload.get("semantic_fingerprint") != sha256_bytes(compact_json_bytes(basis)):
        reasons.append("selection_fingerprint_invalid")
    return payload, reasons


def materialize(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        raise SelectionError(f"session_json_missing:{session}")
    output_dir = args.out_dir if args.out_dir.is_absolute() else session / args.out_dir
    coverage_dir = args.coverage_dir if args.coverage_dir.is_absolute() else session / args.coverage_dir
    policy_path = args.policy.expanduser().resolve()
    _, profile, aggregate, dialogue = readiness_inputs(session)
    if not args.refresh_evidence:
        existing, reasons = verify_selection(
            session, output_dir, policy_path, args.require_speaker_resolved
        )
        if existing is not None and not reasons and existing.get("state") == "selected":
            return existing, 0
    fallback_reason: str | None = None
    selected_coverage: Path | None = None
    roster_configured = (session / DEFAULT_ROSTER).is_file()
    _, policy_reasons = validate_policy(policy_path)
    if policy_reasons:
        fallback_reason = policy_reasons[0]
    elif args.refresh_evidence:
        valid, reason = (
            (False, "roster_requires_fingerprint_bound_refresh")
            if roster_configured
            else verify_v3(session, profile, coverage_dir, policy_path)
        )
        if valid:
            selected_coverage = coverage_dir
        else:
            selected_coverage, fallback_reason = refresh_evidence(
                session, profile, dialogue, output_dir, policy_path
            )
    else:
        valid, reason = (
            (False, "roster_requires_fingerprint_bound_cache")
            if roster_configured
            else verify_v3(session, profile, coverage_dir, policy_path)
        )
        if valid:
            selected_coverage = coverage_dir
        else:
            cached_coverage = (
                output_dir
                / "evidence"
                / refresh_key(session, profile, dialogue)
                / "remote-speaker-coverage-v3"
            )
            cached_valid, cached_reason = verify_v3(
                session, profile, cached_coverage, policy_path
            )
            if cached_valid:
                selected_coverage = cached_coverage
            elif roster_configured:
                selected_coverage, fallback_reason = refresh_evidence(
                    session, profile, dialogue, output_dir, policy_path
                )
            else:
                fallback_reason = (
                    f"cached_coverage_invalid:{cached_reason}"
                    if cached_coverage.exists()
                    else reason
                )
    payload = selection_payload(
        session,
        profile,
        aggregate,
        dialogue,
        selected_coverage,
        policy_path,
        fallback_reason,
    )
    atomic_write(output_dir / "selection.json", canonical_json_bytes(payload))
    atomic_write(output_dir / "selection.md", render_report(payload).encode())
    code = 0
    if args.require_speaker_resolved and payload["state"] != "selected":
        code = 2
    return payload, code


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    output_dir = args.out_dir if args.out_dir.is_absolute() else session / args.out_dir
    policy_path = args.policy.expanduser().resolve()
    try:
        if args.verify_only:
            payload, reasons = verify_selection(
                session, output_dir, policy_path, args.require_speaker_resolved
            )
            valid = payload is not None and not reasons
            print(f"speaker_resolved_default: verify={'ok' if valid else 'stale_or_invalid'}")
            if reasons:
                print(f"reasons: {','.join(reasons)}")
            if valid and args.print_path:
                assert payload is not None
                print(str(session / payload["selected_transcript"]["path"]))
            return 0 if valid else 2
        payload, code = materialize(args)
    except (OSError, ValueError, json.JSONDecodeError, SelectionError) as error:
        print(f"speaker_resolved_default: error={error}", file=sys.stderr)
        return 2
    print(
        "speaker_resolved_default: "
        f"state={payload['state']} profile={payload['selected_profile']} "
        f"speaker_profile={payload['selected_speaker_profile']}"
    )
    if payload.get("fallback_reason"):
        print(f"fallback_reason: {payload['fallback_reason']}")
    if args.print_path:
        print(str(session / payload["selected_transcript"]["path"]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
