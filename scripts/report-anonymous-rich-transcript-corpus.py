#!/usr/bin/env python3
"""Freeze the corpus decision for Anonymous Rich Transcript Handoff v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import evidence_handoff_v2 as evidence_handoff  # noqa: E402

import importlib.util  # noqa: E402


SCRIPT_VERSION = "0.1.0"
SCHEMA = "murmurmark.anonymous_rich_frozen_manifest/v1"
DEFAULT_POLICY = ROOT / "policies/anonymous-rich-transcript-v1.json"
DEFAULT_OUT_DIR = ROOT / "sessions/_reports/anonymous-rich-transcript-v1"


def load_rich_module() -> Any:
    path = ROOT / "scripts/materialize-anonymous-rich-transcript.py"
    spec = importlib.util.spec_from_file_location("anonymous_rich_materializer", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rich = load_rich_module()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--refresh", action="store_true")
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
    relative = str(resolved.relative_to(root.resolve()))
    data = resolved.read_bytes()
    return {"path": relative, "bytes": len(data), "sha256": sha256_bytes(data)}


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
    manifest = ROOT / str(source.get("frozen_manifest_path") or "")
    frozen = json.loads(manifest.read_text(encoding="utf-8"))
    return [ROOT / "sessions" / str(row["session_id"]) for row in frozen.get("sessions") or []]


def analyze_session(session: Path, policy: Path, refresh: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "session_id": session.name,
        "status": "failed",
        "reasons": [],
    }
    audit_dir = session / rich.DEFAULT_AUDIT_DIR
    output_root = session / rich.DEFAULT_OUTPUT_DIR
    try:
        material_before = rich.build_material(session, policy, audit_dir)
        ordinary_before = material_before["baseline"]
        replay_identical = True
        if refresh:
            first = rich.build_handoff(session, policy, audit_dir, output_root)
            pointer = output_root / "handoff_manifest.json"
            first_pointer = pointer.read_bytes()
            first_bundle = {
                name: path.read_bytes()
                for name, path in {
                    "json": rich.rich_path(first, session, "transcript_json"),
                    "markdown": rich.rich_path(first, session, "transcript_markdown"),
                    "manifest": session / first["bundle"]["path"] / "handoff_manifest.json",
                }.items()
                if path is not None
            }
            second = rich.build_handoff(session, policy, audit_dir, output_root)
            replay_identical = (
                second["semantic_fingerprint"] == first["semantic_fingerprint"]
                and pointer.read_bytes() == first_pointer
                and all(path.read_bytes() == first_bundle[name] for name, path in {
                    "json": rich.rich_path(second, session, "transcript_json"),
                    "markdown": rich.rich_path(second, session, "transcript_markdown"),
                    "manifest": session / second["bundle"]["path"] / "handoff_manifest.json",
                }.items() if path is not None)
            )
        manifest, reasons = rich.verify_handoff(session, policy, audit_dir, output_root)
        if manifest is None:
            row["reasons"] = reasons
            return row

        selected_manifest, selected_reasons = evidence_handoff.load_valid_handoff(session)
        if selected_manifest is None:
            row["reasons"] = [f"selected_handoff:{reason}" for reason in selected_reasons]
            return row
        selected_path = session / selected_manifest["inputs"]["clean_dialogue"]["path"]
        selected = json.loads(selected_path.read_text(encoding="utf-8"))
        rich_json_path = rich.rich_path(manifest, session, "transcript_json")
        rich_md_path = rich.rich_path(manifest, session, "transcript_markdown")
        if rich_json_path is None or rich_md_path is None:
            row["reasons"] = ["rich_output_missing"]
            return row
        payload = json.loads(rich_json_path.read_text(encoding="utf-8"))
        attributions = payload.get("remote_speaker_attributions") or []
        remote = [item for item in selected.get("utterances") or [] if item.get("role") == "remote"]
        remote_ids = [str(item.get("id") or "") for item in remote]
        attribution_ids = [str(item.get("utterance_id") or "") for item in attributions]
        speaker_ids = [item.get("speaker_id") for item in attributions if item.get("speaker_id")]
        anonymous_only = all(
            isinstance(value, str) and rich.SPEAKER_ID_RE.fullmatch(value)
            for value in speaker_ids
        ) and all(
            item.get("speaker_label") in {"Colleagues", item.get("speaker_id")}
            for item in attributions
        )
        ordinary_unchanged = all(
            rich.identity_matches(identity_row, session)
            for identity_row in ordinary_before.values()
        )
        selected_exact = payload.get("utterances") == selected.get("utterances")
        references_exact = attribution_ids == remote_ids and len(attribution_ids) == len(set(attribution_ids))
        gates = {
            "ready": manifest.get("state") == "ready",
            "replay_identical": replay_identical,
            "ordinary_outputs_unchanged": ordinary_unchanged,
            "selected_dialogue_exact": selected_exact,
            "remote_references_exact": references_exact,
            "anonymous_labels_only": anonymous_only,
            "plain_transcript_authoritative": (manifest.get("safety") or {}).get("plain_transcript_authoritative") is True,
        }
        failed = [name for name, passed in gates.items() if not passed]
        row.update(
            {
                "status": "passed" if not failed else "failed",
                "reasons": failed,
                "selected_profile": manifest.get("selected_profile"),
                "semantic_fingerprint": manifest.get("semantic_fingerprint"),
                "gates": gates,
                "counts": {
                    "selected_utterances": len(selected.get("utterances") or []),
                    "remote_utterances": len(remote),
                    "attributed_remote_utterances": len(speaker_ids),
                    "aggregate_remote_utterances": len(attributions) - len(speaker_ids),
                    "anonymous_speakers": len(set(speaker_ids)),
                },
                "inputs": {
                    "selected_dialogue": identity(selected_path, root=session),
                    "rich_handoff": identity(output_root / "handoff_manifest.json", root=session),
                },
                "outputs": {
                    "rich_json": identity(rich_json_path, root=session),
                    "rich_markdown": identity(rich_md_path, root=session),
                },
                "ordinary_baseline": ordinary_before,
            }
        )
    except Exception as error:  # fail-open corpus accounting
        row["reasons"] = [f"{type(error).__name__}:{error}"]
    return row


def render_markdown(payload: dict[str, Any], frozen_match: bool | None) -> str:
    metrics = payload["metrics"]
    lines = [
        "# Anonymous Rich Transcript Handoff v1 Corpus",
        "",
        f"- Decision: `{payload['decision']}`",
        f"- Sessions: `{metrics['sessions_passed']}/{metrics['sessions_total']}`",
        f"- Remote utterances: `{metrics['remote_utterances']}`",
        f"- Attributed: `{metrics['attributed_remote_utterances']}`",
        f"- Aggregate fallback: `{metrics['aggregate_remote_utterances']}`",
        f"- Anonymous speakers: `{metrics['anonymous_speakers']}`",
        f"- Frozen manifest match: `{frozen_match if frozen_match is not None else 'not_checked'}`",
        "",
        "The decision covers only the optional `--rich` read surface. Plain transcript, notes,",
        "Evidence Handoff v2 and guarded export remain authoritative and unchanged.",
        "",
        "## Sessions",
        "",
    ]
    for row in payload["sessions"]:
        lines.append(
            f"- `{row['session_id']}`: `{row['status']}`; profile `{row.get('selected_profile') or 'none'}`; "
            f"remote `{(row.get('counts') or {}).get('remote_utterances', 0)}`"
        )
        for reason in row.get("reasons") or []:
            lines.append(f"  - `{reason}`")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    policy = args.policy.expanduser().resolve()
    sessions = [resolve_session(path) for path in args.sessions] if args.sessions else policy_sessions(policy)
    rows = [analyze_session(session, policy, args.refresh) for session in sessions]
    passed = sum(row["status"] == "passed" for row in rows)
    totals = {
        key: sum(int((row.get("counts") or {}).get(key) or 0) for row in rows)
        for key in (
            "selected_utterances",
            "remote_utterances",
            "attributed_remote_utterances",
            "aggregate_remote_utterances",
        )
    }
    totals["anonymous_speakers"] = sum(
        int((row.get("counts") or {}).get("anonymous_speakers") or 0) for row in rows
    )
    gates = {
        "minimum_frozen_sessions": len(rows) >= 6,
        "all_sessions_ready": passed == len(rows),
        "all_replays_identical": all((row.get("gates") or {}).get("replay_identical") is True for row in rows),
        "all_selected_dialogues_exact": all((row.get("gates") or {}).get("selected_dialogue_exact") is True for row in rows),
        "all_remote_references_exact": all((row.get("gates") or {}).get("remote_references_exact") is True for row in rows),
        "all_labels_anonymous": all((row.get("gates") or {}).get("anonymous_labels_only") is True for row in rows),
        "all_ordinary_outputs_unchanged": all((row.get("gates") or {}).get("ordinary_outputs_unchanged") is True for row in rows),
    }
    decision = "PROMOTE_OPTIONAL_RICH" if all(gates.values()) else "DO_NOT_PROMOTE"
    payload = {
        "schema": SCHEMA,
        "version": 1,
        "generator": {
            "script": Path(__file__).name,
            "version": SCRIPT_VERSION,
            "fingerprint": identity(Path(__file__), root=ROOT),
        },
        "decision": decision,
        "scope": "optional_session_local_anonymous_read_surface",
        "inputs": {
            "policy": identity(policy, root=ROOT),
            "materializer": identity(ROOT / "scripts/materialize-anonymous-rich-transcript.py", root=ROOT),
            "source_corpus": identity(ROOT / "docs/testing/remote-speaker-evidence-map-v1-manifest.json", root=ROOT),
        },
        "gates": gates,
        "metrics": {
            "sessions_total": len(rows),
            "sessions_passed": passed,
            **totals,
        },
        "sessions": rows,
        "constraints": {
            "plain_transcript_authoritative": True,
            "speaker_names_allowed": False,
            "cross_session_identity_allowed": False,
            "notes_or_export_integration_promoted": False,
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
    print("anonymous_rich_corpus:")
    print(f"  decision: {decision}")
    print(f"  sessions: {passed}/{len(rows)}")
    print(f"  remote_utterances: {totals['remote_utterances']}")
    print(f"  attributed_remote_utterances: {totals['attributed_remote_utterances']}")
    print(f"  aggregate_remote_utterances: {totals['aggregate_remote_utterances']}")
    if frozen_match is not None:
        print(f"  frozen_manifest_match: {str(frozen_match).lower()}")
    print(f"  report: {out_dir / 'corpus_report.json'}")
    if args.strict and (decision != "PROMOTE_OPTIONAL_RICH" or frozen_match is False):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
