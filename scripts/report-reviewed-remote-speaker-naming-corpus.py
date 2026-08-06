#!/usr/bin/env python3
"""Freeze the corpus decision for Reviewed Remote Speaker Naming v1."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.reviewed_speaker_naming_frozen_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/anonymous-rich-transcript-v1.json"
DEFAULT_OUT_DIR = ROOT / "sessions/_reports/reviewed-remote-speaker-naming-v1"


def load_module(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


naming = load_module(
    "review_remote_speaker_labels_corpus",
    ROOT / "scripts/review-remote-speaker-labels.py",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--write-frozen-manifest", type=Path)
    parser.add_argument("--frozen-manifest", type=Path)
    return parser.parse_args()


def canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def identity(path: Path, *, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    data = resolved.read_bytes()
    return {
        "path": str(resolved.relative_to(root.resolve())),
        "bytes": len(data),
        "sha256": sha256_bytes(data),
    }


def resolve_session(path: Path) -> Path:
    candidate = path.expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    if len(candidate.parts) == 1:
        return (ROOT / "sessions" / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def policy_sessions(policy: Path) -> list[Path]:
    payload = json.loads(policy.read_text(encoding="utf-8"))
    source = payload.get("source_evidence") or {}
    frozen = json.loads((ROOT / str(source["frozen_manifest_path"])).read_text(encoding="utf-8"))
    return [ROOT / "sessions" / str(row["session_id"]) for row in frozen.get("sessions") or []]


def contains_forbidden_content(value: Any) -> bool:
    if isinstance(value, dict):
        if any(key in {"text", "transcript", "display_name", "person_name"} for key in value):
            return True
        return any(contains_forbidden_content(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_forbidden_content(item) for item in value)
    return False


def analyze_session(session: Path) -> dict[str, Any]:
    row: dict[str, Any] = {"session_id": session.name, "status": "failed", "reasons": []}
    try:
        manifest, payload, _, _ = naming.load_anonymous(session)
        baseline = (manifest.get("safety") or {}).get("baseline_identities") or {}
        before_current = all(naming.identity_matches(item, session) for item in baseline.values())
        first = naming.template_payload(session)
        second = naming.template_payload(session)
        expected_ids = [item["speaker_id"] for item in naming.speaker_rows(payload)]
        template_ids = [item.get("speaker_id") for item in first.get("labels") or []]
        actions = [item.get("action") for item in first.get("labels") or []]
        labels = [item.get("display_label") for item in first.get("labels") or []]
        missing_decisions = session / "review/.reviewed-speaker-corpus-missing.json"
        verified, fail_open_reasons = naming.verify_handoff(
            session,
            missing_decisions,
            session / naming.DEFAULT_OUTPUT,
        )
        after_current = all(naming.identity_matches(item, session) for item in baseline.values())
        gates = {
            "anonymous_handoff_current": manifest.get("state") == "ready",
            "template_replay_identical": first == second,
            "template_speaker_set_exact": template_ids == expected_ids,
            "template_has_no_private_labels": all(value is None for value in labels),
            "template_requires_explicit_resolution": (
                first.get("review_completed") is False
                and all(action == "unresolved" for action in actions)
            ),
            "template_contains_no_transcript_content": not contains_forbidden_content(first),
            "missing_decision_fails_open": verified is None and bool(fail_open_reasons),
            "ordinary_outputs_current_before": before_current,
            "ordinary_outputs_current_after": after_current,
        }
        failed = [name for name, passed in gates.items() if not passed]
        attributions = payload.get("remote_speaker_attributions") or []
        attributed = [item for item in attributions if item.get("speaker_id")]
        row.update(
            {
                "status": "passed" if not failed else "failed",
                "reasons": failed,
                "anonymous_semantic_fingerprint": manifest.get("semantic_fingerprint"),
                "gates": gates,
                "counts": {
                    "speaker_ids": len(expected_ids),
                    "remote_utterances": len(attributions),
                    "attributed_remote_utterances": len(attributed),
                    "aggregate_remote_utterances": len(attributions) - len(attributed),
                },
                "inputs": {
                    "anonymous_handoff": identity(
                        session / naming.rich.DEFAULT_OUTPUT_DIR / "handoff_manifest.json",
                        root=session,
                    )
                },
            }
        )
    except Exception as error:  # fail-open corpus accounting
        row["reasons"] = [f"{type(error).__name__}:{error}"]
    return row


def run_synthetic_checker() -> tuple[bool, str | None]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check-reviewed-remote-speaker-naming.py")],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if result.returncode == 0:
        return True, None
    tail = result.stdout.strip().splitlines()[-1:] or ["synthetic checker failed"]
    return False, tail[0]


def render_markdown(payload: dict[str, Any], frozen_match: bool | None) -> str:
    metrics = payload["metrics"]
    lines = [
        "# Reviewed Remote Speaker Naming v1 Corpus",
        "",
        f"- Decision: `{payload['decision']}`",
        f"- Sessions: `{metrics['sessions_passed']}/{metrics['sessions_total']}`",
        f"- Anonymous speaker IDs available for review: `{metrics['speaker_ids']}`",
        f"- Remote utterances protected: `{metrics['remote_utterances']}`",
        f"- Synthetic contract checker: `{payload['gates']['synthetic_contract_checker']}`",
        f"- Frozen manifest match: `{frozen_match if frozen_match is not None else 'not_checked'}`",
        "",
        "Promotion covers only an explicit session-local reviewed read surface. Labels are omitted",
        "from this report. Plain transcript, notes, Evidence Handoff v2 and export remain unchanged.",
        "",
        "## Sessions",
        "",
    ]
    for row in payload["sessions"]:
        counts = row.get("counts") or {}
        lines.append(
            f"- `{row['session_id']}`: `{row['status']}`; speakers `{counts.get('speaker_ids', 0)}`; "
            f"remote `{counts.get('remote_utterances', 0)}`"
        )
        for reason in row.get("reasons") or []:
            lines.append(f"  - `{reason}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    policy = args.policy.expanduser().resolve()
    sessions = [resolve_session(path) for path in args.sessions] if args.sessions else policy_sessions(policy)
    rows = [analyze_session(session) for session in sessions]
    synthetic_passed, synthetic_reason = run_synthetic_checker()
    passed = sum(row["status"] == "passed" for row in rows)
    totals = {
        key: sum(int((row.get("counts") or {}).get(key) or 0) for row in rows)
        for key in (
            "speaker_ids",
            "remote_utterances",
            "attributed_remote_utterances",
            "aggregate_remote_utterances",
        )
    }
    gates = {
        "minimum_frozen_sessions": len(rows) >= 6,
        "all_sessions_passed": passed == len(rows),
        "all_templates_deterministic": all(
            (row.get("gates") or {}).get("template_replay_identical") is True for row in rows
        ),
        "all_missing_decisions_fail_open": all(
            (row.get("gates") or {}).get("missing_decision_fails_open") is True for row in rows
        ),
        "all_ordinary_outputs_current": all(
            (row.get("gates") or {}).get("ordinary_outputs_current_after") is True for row in rows
        ),
        "synthetic_contract_checker": synthetic_passed,
    }
    decision = "PROMOTE_OPTIONAL_REVIEWED_NAMING" if all(gates.values()) else "DO_NOT_PROMOTE"
    payload = {
        "schema": SCHEMA,
        "version": 1,
        "generator": {
            "script": Path(__file__).name,
            "version": SCRIPT_VERSION,
            "fingerprint": identity(Path(__file__), root=ROOT),
        },
        "decision": decision,
        "scope": "optional_explicit_session_local_speaker_labels",
        "inputs": {
            "anonymous_policy": identity(policy, root=ROOT),
            "reviewed_materializer": identity(
                ROOT / "scripts/review-remote-speaker-labels.py",
                root=ROOT,
            ),
            "anonymous_source_corpus": identity(
                ROOT / "docs/testing/anonymous-rich-transcript-v1-manifest.json",
                root=ROOT,
            ),
        },
        "gates": gates,
        "metrics": {"sessions_total": len(rows), "sessions_passed": passed, **totals},
        "sessions": rows,
        "synthetic_failure": synthetic_reason,
        "constraints": {
            "labels_explicit_only": True,
            "voice_identity_inference_allowed": False,
            "cross_session_identity_allowed": False,
            "plain_transcript_authoritative": True,
            "notes_or_export_integration_promoted": False,
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
    print("reviewed_speaker_naming_corpus:")
    print(f"  decision: {decision}")
    print(f"  sessions: {passed}/{len(rows)}")
    print(f"  speaker_ids: {totals['speaker_ids']}")
    print(f"  remote_utterances: {totals['remote_utterances']}")
    print(f"  synthetic_contract_checker: {str(synthetic_passed).lower()}")
    if frozen_match is not None:
        print(f"  frozen_manifest_match: {str(frozen_match).lower()}")
    print(f"  report: {out_dir / 'corpus_report.json'}")
    if args.strict and (decision != "PROMOTE_OPTIONAL_REVIEWED_NAMING" or frozen_match is False):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
