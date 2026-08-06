#!/usr/bin/env python3
"""Freeze the corpus decision for Reviewed Speaker-Aware Meeting Memory v1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.reviewed_speaker_memory_frozen_manifest/v1"
DEFAULT_SOURCE_MANIFEST = ROOT / "docs/testing/anonymous-rich-transcript-v1-manifest.json"
DEFAULT_OUT_DIR = ROOT / "sessions/_reports/reviewed-speaker-memory-v1"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


memory = load_module(
    "reviewed_speaker_memory_corpus",
    ROOT / "scripts/materialize-reviewed-speaker-memory.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--source-manifest", type=Path, default=DEFAULT_SOURCE_MANIFEST)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--write-frozen-manifest", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: Path, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    data = resolved.read_bytes()
    return {
        "path": str(resolved.relative_to(root.resolve())),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(payload))


def resolve_session(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if len(candidate.parts) == 1:
        return (ROOT / "sessions" / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def source_sessions(path: Path) -> list[Path]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ROOT / "sessions" / str(row["session_id"]) for row in payload.get("sessions") or []]


def protected_paths(session: Path) -> dict[str, Path]:
    evidence_manifest, reasons = memory.evidence_handoff.load_valid_handoff(session)
    if evidence_manifest is None:
        raise RuntimeError("evidence_handoff_invalid:" + ",".join(reasons))
    anonymous_manifest, _, anonymous_json, anonymous_markdown = memory.naming.load_anonymous(session)
    paths = {
        "evidence_handoff_manifest": session / "derived/handoff-v2/handoff_manifest.json",
        "anonymous_handoff_manifest": session
        / memory.naming.rich.DEFAULT_OUTPUT_DIR
        / "handoff_manifest.json",
        "anonymous_rich_json": anonymous_json,
        "anonymous_rich_markdown": anonymous_markdown,
    }
    for key in ("handoff_evidence", "meeting", "notes", "transcript", "quality_verdict"):
        artifact = memory.evidence_handoff.artifact_path(evidence_manifest, session, key)
        if artifact is None:
            raise RuntimeError(f"evidence_handoff_artifact_missing:{key}")
        paths[f"handoff_{key}"] = artifact
    for key, row in ((anonymous_manifest.get("safety") or {}).get("baseline_identities") or {}).items():
        path = memory.naming.resolve_identity(row, session)
        if path is None:
            raise RuntimeError(f"anonymous_baseline_invalid:{key}")
        paths[f"ordinary_{key}"] = path
    return paths


def hashes(paths: dict[str, Path]) -> dict[str, str]:
    return {key: sha256_file(path) for key, path in sorted(paths.items())}


def complete_keep_anonymous(template: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(template))
    payload["review_completed"] = True
    for row in payload.get("labels") or []:
        row["action"] = "keep_anonymous"
        row["display_label"] = None
    return payload


def cleanup(paths: list[Path]) -> None:
    for path in reversed(paths):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()


def analyze_session(session: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"session_id": session.name, "status": "failed", "reasons": []}
    decision_path = session / "review/.reviewed-speaker-memory-corpus-v1.json"
    naming_root = session / "derived/transcript-rich/.reviewed-speaker-memory-corpus-v1"
    memory_root = session / "derived/meeting-memory/.reviewed-speaker-memory-corpus-v1"
    temporary_paths = [decision_path, naming_root, memory_root]
    cleanup(temporary_paths)
    try:
        protected = protected_paths(session)
        before = hashes(protected)
        template = memory.naming.template_payload(session)
        decisions = complete_keep_anonymous(template)
        write_json(decision_path, decisions)
        naming_manifest = memory.naming.build_handoff(session, decision_path, naming_root)
        first = memory.build_handoff(
            session,
            decision_path,
            memory_root,
            reviewed_root=naming_root,
        )
        pointer = memory_root / "handoff_manifest.json"
        first_pointer = pointer.read_bytes()
        replay = memory.build_handoff(
            session,
            decision_path,
            memory_root,
            reviewed_root=naming_root,
        )
        verified, verify_reasons = memory.verify_handoff(
            session,
            decision_path,
            memory_root,
            naming_root,
        )
        memory_path = memory.artifact_path(first, session, "memory_json")
        if memory_path is None:
            raise RuntimeError("speaker_memory_artifact_missing")
        payload = json.loads(memory_path.read_text(encoding="utf-8"))
        known_ids = {item["utterance_id"] for item in payload.get("utterance_bindings") or []}
        statement_ids_valid = all(
            set(item.get("evidence_utterance_ids") or []).issubset(known_ids)
            and set(item.get("context_utterance_ids") or []).issubset(known_ids)
            for item in payload.get("statement_bindings") or []
        )
        speaker_bindings_valid = all(
            item.get("display_mode") in {"local_role", "aggregate_remote", "anonymous_id"}
            and (
                item.get("display_mode") != "anonymous_id"
                or item.get("decision_row_index") is not None
            )
            for item in payload.get("speaker_bindings") or []
        )

        partial = json.loads(json.dumps(decisions))
        partial["review_completed"] = False
        write_json(decision_path, partial)
        stale, stale_reasons = memory.verify_handoff(
            session,
            decision_path,
            memory_root,
            naming_root,
        )
        write_json(decision_path, decisions)
        after = hashes(protected)
        gates = {
            "naming_handoff_ready": naming_manifest.get("state") == "ready",
            "memory_handoff_ready": first.get("state") == "ready",
            "deterministic_replay": (
                replay.get("semantic_fingerprint") == first.get("semantic_fingerprint")
                and pointer.read_bytes() == first_pointer
            ),
            "verification_passed": verified is not None and not verify_reasons,
            "statement_references_exact": statement_ids_valid,
            "speaker_bindings_safe": speaker_bindings_valid,
            "partial_review_fails_open": stale is None and any(
                "review_not_completed" in reason for reason in stale_reasons
            ),
            "ordinary_outputs_unchanged": before == after,
            "no_reviewed_names_generated": first["summary"].get("reviewed_labels") == 0,
        }
        failed = [key for key, passed in gates.items() if not passed]
        row.update(
            {
                "status": "passed" if not failed else "failed",
                "reasons": failed,
                "gates": gates,
                "source": {
                    "evidence_handoff": identity(
                        session / "derived/handoff-v2/handoff_manifest.json", session
                    ),
                    "anonymous_handoff": identity(
                        session / memory.naming.rich.DEFAULT_OUTPUT_DIR / "handoff_manifest.json",
                        session,
                    ),
                },
                "counts": {
                    "anonymous_speaker_ids": len(template.get("labels") or []),
                    "utterances": first["summary"].get("utterances", 0),
                    "statements": first["summary"].get("statements", 0),
                    "speaker_bindings": first["summary"].get("speaker_bindings", 0),
                    "reviewed_labels": first["summary"].get("reviewed_labels", 0),
                    "anonymous_labels": first["summary"].get("anonymous_labels", 0),
                    "aggregate_labels": first["summary"].get("aggregate_labels", 0),
                },
            }
        )
    except Exception as error:  # fail-open corpus accounting
        row["reasons"] = [f"{type(error).__name__}:{error}"]
    finally:
        cleanup(temporary_paths)
    return row


def run_synthetic_checker() -> tuple[bool, str | None]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-reviewed-speaker-memory.py")],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode == 0:
        return True, None
    lines = result.stdout.strip().splitlines()
    return False, lines[-1] if lines else "synthetic checker failed"


def render_markdown(payload: dict[str, Any], frozen_match: bool | None) -> str:
    metrics = payload["metrics"]
    lines = [
        "# Reviewed Speaker-Aware Meeting Memory v1 Corpus",
        "",
        f"- Decision: `{payload['decision']}`",
        f"- Sessions: `{metrics['sessions_passed']}/{metrics['sessions_total']}`",
        f"- Anonymous speaker IDs resolved safely: `{metrics['anonymous_speaker_ids']}`",
        f"- Utterances protected: `{metrics['utterances']}`",
        f"- Evidence statements protected: `{metrics['statements']}`",
        f"- Synthetic reviewed-label checker: `{payload['gates']['synthetic_contract_checker']}`",
        f"- Frozen manifest match: `{frozen_match if frozen_match is not None else 'not_checked'}`",
        "",
        "Corpus decisions use keep-anonymous rows only. The synthetic checker covers explicit labels;",
        "no private display label is stored in this report.",
        "",
        "## Sessions",
        "",
    ]
    for row in payload["sessions"]:
        counts = row.get("counts") or {}
        lines.append(
            f"- `{row['session_id']}`: `{row['status']}`; utterances `{counts.get('utterances', 0)}`; "
            f"statements `{counts.get('statements', 0)}`"
        )
        lines.extend(f"  - `{reason}`" for reason in row.get("reasons") or [])
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    source_manifest = args.source_manifest.expanduser().resolve()
    sessions = [resolve_session(path) for path in args.sessions] if args.sessions else source_sessions(source_manifest)
    rows = [analyze_session(session) for session in sessions]
    synthetic_passed, synthetic_reason = run_synthetic_checker()
    passed = sum(row["status"] == "passed" for row in rows)
    totals = {
        key: sum(int((row.get("counts") or {}).get(key) or 0) for row in rows)
        for key in (
            "anonymous_speaker_ids",
            "utterances",
            "statements",
            "speaker_bindings",
            "reviewed_labels",
            "anonymous_labels",
            "aggregate_labels",
        )
    }
    gates = {
        "minimum_frozen_sessions": len(rows) >= 6,
        "all_sessions_passed": passed == len(rows),
        "all_replays_deterministic": all(
            (row.get("gates") or {}).get("deterministic_replay") is True for row in rows
        ),
        "all_references_exact": all(
            (row.get("gates") or {}).get("statement_references_exact") is True for row in rows
        ),
        "all_partial_reviews_fail_open": all(
            (row.get("gates") or {}).get("partial_review_fails_open") is True for row in rows
        ),
        "all_ordinary_outputs_unchanged": all(
            (row.get("gates") or {}).get("ordinary_outputs_unchanged") is True for row in rows
        ),
        "synthetic_contract_checker": synthetic_passed,
    }
    decision = (
        "PROMOTE_OPTIONAL_REVIEWED_SPEAKER_MEMORY"
        if all(gates.values())
        else "DO_NOT_PROMOTE"
    )
    payload = {
        "schema": SCHEMA,
        "version": 1,
        "generator": {
            "script": Path(__file__).name,
            "version": SCRIPT_VERSION,
            "fingerprint": identity(Path(__file__), ROOT),
        },
        "decision": decision,
        "scope": "optional_explicit_session_local_speaker_memory",
        "inputs": {
            "anonymous_source_corpus": identity(source_manifest, ROOT),
            "reviewed_naming_source_corpus": identity(
                ROOT / "docs/testing/reviewed-remote-speaker-naming-v1-manifest.json", ROOT
            ),
            "memory_materializer": identity(
                ROOT / "scripts/materialize-reviewed-speaker-memory.py", ROOT
            ),
            "reviewed_naming_materializer": identity(
                ROOT / "scripts/review-remote-speaker-labels.py", ROOT
            ),
        },
        "gates": gates,
        "metrics": {"sessions_total": len(rows), "sessions_passed": passed, **totals},
        "sessions": rows,
        "synthetic_failure": synthetic_reason,
        "constraints": {
            "explicit_session_review_only": True,
            "voice_identity_inference_allowed": False,
            "cross_session_identity_allowed": False,
            "generated_claims_allowed": False,
            "external_writes_allowed": False,
            "default_notes_export_unchanged": True,
            "private_labels_in_corpus_report": False,
        },
    }
    encoded = canonical_bytes(payload)
    frozen_match: bool | None = None
    if args.write_frozen_manifest:
        target = args.write_frozen_manifest.expanduser()
        target = target if target.is_absolute() else ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(encoded)
    if args.frozen_manifest:
        target = args.frozen_manifest.expanduser()
        target = target if target.is_absolute() else ROOT / target
        frozen_match = target.is_file() and target.read_bytes() == encoded

    out_dir = args.out_dir.expanduser()
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "corpus_report.json").write_bytes(encoded)
    (out_dir / "corpus_report.md").write_text(render_markdown(payload, frozen_match), encoding="utf-8")
    print("reviewed_speaker_memory_corpus:")
    print(f"  decision: {decision}")
    print(f"  sessions: {passed}/{len(rows)}")
    print(f"  utterances: {totals['utterances']}")
    print(f"  statements: {totals['statements']}")
    print(f"  synthetic_contract_checker: {str(synthetic_passed).lower()}")
    if frozen_match is not None:
        print(f"  frozen_manifest_match: {str(frozen_match).lower()}")
    print(f"  report: {out_dir / 'corpus_report.json'}")
    if args.strict and (
        decision != "PROMOTE_OPTIONAL_REVIEWED_SPEAKER_MEMORY" or frozen_match is False
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
