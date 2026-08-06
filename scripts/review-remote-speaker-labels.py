#!/usr/bin/env python3
"""Create and apply explicit session-local labels over an anonymous rich transcript."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_VERSION = "0.1.0"
DECISION_SCHEMA = "murmurmark.reviewed_speaker_decisions/v1"
HANDOFF_SCHEMA = "murmurmark.reviewed_speaker_handoff/v1"
TRANSCRIPT_SCHEMA = "murmurmark.reviewed_speaker_transcript/v1"
REPORT_SCHEMA = "murmurmark.reviewed_speaker_report/v1"
DEFAULT_DECISIONS = Path("review/remote-speaker-labels.v1.json")
DEFAULT_OUTPUT = Path("derived/transcript-rich/reviewed-speakers-v1")
ALLOWED_ACTIONS = {"label", "keep_anonymous"}
RESERVED_LABELS = {"me", "colleagues"}
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
LOCAL_PATH_RE = re.compile(r"(?:/Users/|/home/|[A-Za-z]:\\\\)")


class NamingError(RuntimeError):
    pass


class SimulatedInterruption(RuntimeError):
    pass


def load_rich_module() -> Any:
    path = ROOT / "scripts/materialize-anonymous-rich-transcript.py"
    spec = importlib.util.spec_from_file_location("anonymous_rich_materializer_for_naming", path)
    if spec is None or spec.loader is None:
        raise NamingError(f"anonymous rich helper cannot be loaded: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rich = load_rich_module()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("template", "apply", "status"):
        sub = subparsers.add_parser(name)
        sub.add_argument("session", type=Path)
        sub.add_argument("--decisions", type=Path)
        sub.add_argument("--out-dir", type=Path)
        if name == "template":
            sub.add_argument("--force", action="store_true")
        if name == "status":
            sub.add_argument("--verify-only", action="store_true")
            sub.add_argument("--print-path", action="store_true")
        if name == "apply":
            sub.add_argument("--simulate-interruption-before-publish", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args()


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def compact_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NamingError(f"invalid_or_missing_json:{path.name}:{type(error).__name__}") from error
    if not isinstance(payload, dict):
        raise NamingError(f"invalid_json_object:{path.name}")
    return payload


def within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def resolve_inside(root: Path, raw: str | Path) -> Path:
    path = Path(raw).expanduser()
    path = path if path.is_absolute() else root / path
    resolved = path.resolve()
    if not within(resolved, root):
        raise NamingError("path_outside_session")
    return resolved


def session_relative(path: Path, session: Path) -> str:
    return str(path.resolve().relative_to(session.resolve()))


def identity(path: Path, session: Path) -> dict[str, Any]:
    if not path.is_file() or not within(path, session):
        raise NamingError(f"input_missing_or_outside_session:{path.name}")
    return {
        "scope": "session",
        "path": session_relative(path, session),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def repo_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or not within(path, ROOT):
        raise NamingError(f"implementation_missing:{path.name}")
    return {
        "scope": "repository",
        "path": str(path.resolve().relative_to(ROOT)),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def resolve_identity(row: Any, session: Path) -> Path | None:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        return None
    scope = row.get("scope")
    try:
        if scope == "session":
            return resolve_inside(session, row["path"])
        if scope == "repository":
            path = (ROOT / row["path"]).resolve()
            return path if within(path, ROOT) else None
    except NamingError:
        return None
    return None


def identity_matches(row: Any, session: Path) -> bool:
    path = resolve_identity(row, session)
    return bool(
        path is not None
        and path.is_file()
        and int(row.get("bytes") or -1) == path.stat().st_size
        and row.get("sha256") == sha256_file(path)
    )


def implementation() -> dict[str, Any]:
    path = Path(__file__).resolve()
    return {"script": path.name, "version": SCRIPT_VERSION, "fingerprint": repo_identity(path)}


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_durable(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def atomic_write(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    write_durable(temporary, payload)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def decisions_path(args: argparse.Namespace, session: Path) -> Path:
    return resolve_inside(session, args.decisions or DEFAULT_DECISIONS)


def output_root(args: argparse.Namespace, session: Path) -> Path:
    return resolve_inside(session, args.out_dir or DEFAULT_OUTPUT)


def load_anonymous(session: Path) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    policy = ROOT / rich.DEFAULT_POLICY
    audit_dir = session / rich.DEFAULT_AUDIT_DIR
    anonymous_root = session / rich.DEFAULT_OUTPUT_DIR
    manifest, reasons = rich.verify_handoff(session, policy, audit_dir, anonymous_root)
    if manifest is None:
        raise NamingError("anonymous_handoff_invalid:" + ",".join(reasons))
    json_path = rich.rich_path(manifest, session, "transcript_json")
    markdown_path = rich.rich_path(manifest, session, "transcript_markdown")
    if json_path is None or markdown_path is None:
        raise NamingError("anonymous_outputs_missing")
    payload = read_json(json_path)
    if payload.get("schema") != rich.TRANSCRIPT_SCHEMA:
        raise NamingError("anonymous_transcript_schema_mismatch")
    return manifest, payload, json_path, markdown_path


def speaker_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("speaker_map")
    if not isinstance(rows, list):
        raise NamingError("anonymous_speaker_map_missing")
    result = [row for row in rows if isinstance(row, dict)]
    ids = [row.get("speaker_id") for row in result]
    if any(not isinstance(value, str) or not rich.SPEAKER_ID_RE.fullmatch(value) for value in ids):
        raise NamingError("anonymous_speaker_id_invalid")
    if len(ids) != len(set(ids)):
        raise NamingError("anonymous_speaker_ids_duplicate")
    return sorted(result, key=lambda row: str(row["speaker_id"]))


def template_payload(session: Path) -> dict[str, Any]:
    manifest, payload, _, _ = load_anonymous(session)
    attributions = payload.get("remote_speaker_attributions")
    if not isinstance(attributions, list):
        raise NamingError("anonymous_attributions_missing")
    labels: list[dict[str, Any]] = []
    for speaker in speaker_rows(payload):
        speaker_id = str(speaker["speaker_id"])
        matching = [row for row in attributions if row.get("speaker_id") == speaker_id]
        labels.append(
            {
                "speaker_id": speaker_id,
                "action": "unresolved",
                "display_label": None,
                "evidence": {
                    "utterance_count": len(matching),
                    "first_start": min((float(row.get("start") or 0.0) for row in matching), default=None),
                    "last_end": max((float(row.get("end") or 0.0) for row in matching), default=None),
                },
            }
        )
    basis = {
        "schema": DECISION_SCHEMA,
        "session_id": session.name,
        "anonymous_semantic_fingerprint": manifest["semantic_fingerprint"],
        "speaker_ids": [row["speaker_id"] for row in labels],
        "evidence": [row["evidence"] for row in labels],
    }
    return {
        "schema": DECISION_SCHEMA,
        "version": 1,
        "session_id": session.name,
        "source": {
            "anonymous_semantic_fingerprint": manifest["semantic_fingerprint"],
            "template_fingerprint": sha256_bytes(compact_bytes(basis)),
        },
        "decision_source": "explicit_session_review",
        "review_completed": False,
        "labels": labels,
        "instructions": {
            "label": "Set action to label and provide a unique display_label.",
            "keep_anonymous": "Set action to keep_anonymous and leave display_label null.",
            "finish": "Set review_completed to true only after every row is resolved.",
        },
    }


def write_template(session: Path, path: Path, force: bool) -> dict[str, Any]:
    payload = template_payload(session)
    if path.exists() and not force:
        existing = read_json(path)
        if (
            existing.get("schema") == DECISION_SCHEMA
            and (existing.get("source") or {}).get("anonymous_semantic_fingerprint")
            == payload["source"]["anonymous_semantic_fingerprint"]
        ):
            return existing
        raise NamingError("decision_file_exists_and_is_stale:use_--force_to_reset")
    atomic_write(path, canonical_bytes(payload))
    return payload


def validate_label(value: Any) -> str:
    if not isinstance(value, str):
        raise NamingError("display_label_missing")
    if value != value.strip() or not value:
        raise NamingError("display_label_not_trimmed_or_empty")
    if len(value) > 80:
        raise NamingError("display_label_too_long")
    if CONTROL_RE.search(value) or LOCAL_PATH_RE.search(value):
        raise NamingError("display_label_unsafe")
    if value.casefold() in RESERVED_LABELS:
        raise NamingError("display_label_reserved")
    return value


def validate_decisions(
    session: Path,
    decisions: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    expected = template_payload(session)
    if decisions.get("schema") != DECISION_SCHEMA or decisions.get("version") != 1:
        raise NamingError("decision_schema_mismatch")
    if decisions.get("session_id") != session.name:
        raise NamingError("decision_session_mismatch")
    if decisions.get("decision_source") != "explicit_session_review":
        raise NamingError("decision_source_not_explicit")
    if decisions.get("review_completed") is not True:
        raise NamingError("review_not_completed")
    if decisions.get("source") != expected.get("source"):
        raise NamingError("decision_source_fingerprint_stale")
    rows = decisions.get("labels")
    if not isinstance(rows, list):
        raise NamingError("decision_labels_missing")
    expected_rows = expected["labels"]
    if [row.get("speaker_id") for row in rows if isinstance(row, dict)] != [
        row["speaker_id"] for row in expected_rows
    ]:
        raise NamingError("decision_speaker_set_mismatch")
    reviewed: list[dict[str, Any]] = []
    used_labels: set[str] = set()
    for row, expected_row in zip(rows, expected_rows):
        if not isinstance(row, dict) or row.get("evidence") != expected_row["evidence"]:
            raise NamingError("decision_evidence_mismatch")
        action = row.get("action")
        if action not in ALLOWED_ACTIONS:
            raise NamingError("decision_action_unresolved")
        display_label = row.get("display_label")
        if action == "label":
            display_label = validate_label(display_label)
            folded = display_label.casefold()
            if folded in used_labels:
                raise NamingError("display_label_duplicate")
            used_labels.add(folded)
        elif display_label is not None:
            raise NamingError("keep_anonymous_label_must_be_null")
        reviewed.append(
            {
                "speaker_id": row["speaker_id"],
                "action": action,
                "display_label": display_label,
            }
        )
    return expected, reviewed


def render_markdown(payload: dict[str, Any], labels: dict[str, str]) -> str:
    attribution = {
        str(row["utterance_id"]): row
        for row in payload.get("remote_speaker_attributions") or []
    }
    lines = [
        "# Reviewed Remote Speaker Transcript",
        "",
        "Optional session-local reviewed labels. The ordinary transcript remains authoritative.",
        "",
        f"Source profile: `{payload.get('selected_profile')}`",
        "",
    ]
    for utterance in payload.get("utterances") or []:
        start = max(0, int(float(utterance.get("start") or 0)))
        minutes, seconds = divmod(start, 60)
        if utterance.get("role") == "remote":
            row = attribution[str(utterance["id"])]
            speaker_id = row.get("speaker_id")
            label = labels.get(str(speaker_id), str(speaker_id)) if speaker_id else "Colleagues"
        else:
            label = "Me"
        lines.extend(
            [
                f"## {minutes:02d}:{seconds:02d} {label}",
                "",
                str(utterance.get("text") or ""),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def baseline_identities(
    session: Path,
    anonymous_manifest: dict[str, Any],
    anonymous_json: Path,
    anonymous_markdown: Path,
    decisions: Path,
) -> dict[str, dict[str, Any]]:
    baseline = {
        "anonymous_handoff_manifest": identity(
            session / rich.DEFAULT_OUTPUT_DIR / "handoff_manifest.json", session
        ),
        "anonymous_rich_json": identity(anonymous_json, session),
        "anonymous_rich_markdown": identity(anonymous_markdown, session),
        "review_decisions": identity(decisions, session),
    }
    for key, row in ((anonymous_manifest.get("safety") or {}).get("baseline_identities") or {}).items():
        if not identity_matches(row, session):
            raise NamingError(f"anonymous_baseline_stale:{key}")
        baseline[f"ordinary_{key}"] = row
    return baseline


def output_identity(path: str, payload: bytes) -> dict[str, Any]:
    return {"scope": "session", "path": path, "bytes": len(payload), "sha256": sha256_bytes(payload)}


def build_material(session: Path, decisions_path_value: Path) -> dict[str, Any]:
    anonymous_manifest, anonymous, anonymous_json, anonymous_markdown = load_anonymous(session)
    decisions = read_json(decisions_path_value)
    _, reviewed = validate_decisions(session, decisions)
    label_map = {
        row["speaker_id"]: row["display_label"]
        for row in reviewed
        if row["action"] == "label"
    }
    output = {
        "schema": TRANSCRIPT_SCHEMA,
        "version": 1,
        "status": "optional_reviewed_session_labels",
        "session_id": session.name,
        "selected_profile": anonymous.get("selected_profile"),
        "source_anonymous_semantic_fingerprint": anonymous_manifest["semantic_fingerprint"],
        "utterances": anonymous.get("utterances"),
        "remote_speaker_attributions": anonymous.get("remote_speaker_attributions"),
        "reviewed_speaker_labels": reviewed,
        "constraints": {
            "authoritative": False,
            "decision_source": "explicit_session_review",
            "voice_identity_inference": False,
            "cross_session_identity": False,
            "notes_or_export_source": False,
        },
    }
    json_payload = canonical_bytes(output)
    markdown_payload = render_markdown(anonymous, label_map).encode("utf-8")
    baseline = baseline_identities(
        session, anonymous_manifest, anonymous_json, anonymous_markdown, decisions_path_value
    )
    return {
        "anonymous_manifest": anonymous_manifest,
        "anonymous": anonymous,
        "decisions": decisions,
        "reviewed": reviewed,
        "baseline": baseline,
        "inputs": {
            "anonymous_handoff": identity(
                session / rich.DEFAULT_OUTPUT_DIR / "handoff_manifest.json", session
            ),
            "anonymous_rich_json": identity(anonymous_json, session),
            "anonymous_rich_markdown": identity(anonymous_markdown, session),
            "review_decisions": identity(decisions_path_value, session),
        },
        "outputs": {
            "transcript_json": json_payload,
            "transcript_markdown": markdown_payload,
        },
        "summary": {
            "speaker_count": len(reviewed),
            "labeled_count": sum(row["action"] == "label" for row in reviewed),
            "kept_anonymous_count": sum(row["action"] == "keep_anonymous" for row in reviewed),
        },
    }


def semantic_basis(session: Path, material: dict[str, Any], filenames: dict[str, str]) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "source_anonymous_semantic_fingerprint": material["anonymous_manifest"]["semantic_fingerprint"],
        "inputs": material["inputs"],
        "outputs": {
            key: {
                "filename": filenames[key],
                "bytes": len(payload),
                "sha256": sha256_bytes(payload),
            }
            for key, payload in sorted(material["outputs"].items())
        },
        "summary": material["summary"],
        "scope": "optional_explicit_session_labels",
    }


def report_payload(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "version": 1,
        "session_id": manifest["session_id"],
        "state": manifest["state"],
        "semantic_fingerprint": manifest.get("semantic_fingerprint"),
        "summary": manifest.get("summary") or {},
        "reasons": manifest.get("reasons") or [],
        "privacy": {
            "display_labels_in_report": False,
            "voice_identity_inference": False,
            "cross_session_identity": False,
        },
    }


def report_markdown(manifest: dict[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    lines = [
        "# Reviewed Remote Speaker Naming v1",
        "",
        f"- State: `{manifest.get('state')}`",
        f"- Speakers reviewed: `{summary.get('speaker_count', 0)}`",
        f"- Explicit labels: `{summary.get('labeled_count', 0)}`",
        f"- Kept anonymous: `{summary.get('kept_anonymous_count', 0)}`",
        "",
        "Display labels are intentionally omitted from this report.",
    ]
    for reason in manifest.get("reasons") or []:
        lines.append(f"- Reason: `{reason}`")
    return "\n".join(lines) + "\n"


def build_handoff(
    session: Path,
    decisions: Path,
    root: Path,
    *,
    simulate_interruption_before_publish: bool = False,
) -> dict[str, Any]:
    material = build_material(session, decisions)
    filenames = {
        "transcript_json": "transcript.rich.reviewed.json",
        "transcript_markdown": "transcript.rich.reviewed.md",
    }
    basis = semantic_basis(session, material, filenames)
    fingerprint = sha256_bytes(compact_bytes(basis))
    bundle_relative = f"{session_relative(root, session)}/bundles/{fingerprint}"
    bundle = session / bundle_relative
    files = {
        key: output_identity(f"{bundle_relative}/{filenames[key]}", payload)
        for key, payload in sorted(material["outputs"].items())
    }
    unchanged = all(identity_matches(row, session) for row in material["baseline"].values())
    if not unchanged:
        raise NamingError("source_output_changed_before_publication")
    manifest = {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "ready",
        "semantic_fingerprint": fingerprint,
        "source_anonymous_semantic_fingerprint": material["anonymous_manifest"]["semantic_fingerprint"],
        "fingerprint_basis": basis,
        "inputs": material["inputs"],
        "bundle": {"path": bundle_relative, "files": files},
        "summary": material["summary"],
        "gates": {
            "publish_reviewed_labels": True,
            "explicit_review_complete": True,
            "anonymous_handoff_current": True,
            "ordinary_outputs_unchanged": True,
            "voice_identity_inference": False,
            "cross_session_identity": False,
        },
        "safety": {
            "baseline_identities": material["baseline"],
            "display_labels_in_manifest": False,
            "plain_transcript_authoritative": True,
            "anonymous_rich_fallback": True,
            "notes_or_export_mutated": False,
        },
        "reasons": [],
    }
    expected = {
        filenames[key]: payload for key, payload in material["outputs"].items()
    }
    expected["handoff_manifest.json"] = canonical_bytes(manifest)

    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging.", dir=root))
    try:
        for name, payload in expected.items():
            write_durable(staging / name, payload)
        fsync_directory(staging)
        bundles = root / "bundles"
        bundles.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            if not all((bundle / name).is_file() and (bundle / name).read_bytes() == payload for name, payload in expected.items()):
                raise NamingError("existing_immutable_bundle_invalid")
            shutil.rmtree(staging)
        else:
            os.replace(staging, bundle)
            fsync_directory(bundles)
        if not all(identity_matches(row, session) for row in material["baseline"].values()):
            raise NamingError("source_output_changed_during_publication")
        if simulate_interruption_before_publish:
            raise SimulatedInterruption("simulated interruption before reviewed naming publish")
        atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))
        atomic_write(root / "report.json", canonical_bytes(report_payload(manifest)))
        atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))
        return manifest
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def unavailable_manifest(session: Path, reason: str) -> dict[str, Any]:
    return {
        "schema": HANDOFF_SCHEMA,
        "version": 1,
        "generator": implementation(),
        "session_id": session.name,
        "state": "unavailable",
        "semantic_fingerprint": None,
        "bundle": None,
        "summary": {},
        "gates": {"publish_reviewed_labels": False},
        "safety": {
            "display_labels_in_manifest": False,
            "plain_transcript_authoritative": True,
            "anonymous_rich_fallback": True,
        },
        "reasons": [reason],
    }


def publish_unavailable(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write(root / "handoff_manifest.json", canonical_bytes(manifest))
    atomic_write(root / "report.json", canonical_bytes(report_payload(manifest)))
    atomic_write(root / "report.md", report_markdown(manifest).encode("utf-8"))


def verify_handoff(
    session: Path,
    decisions: Path,
    root: Path,
) -> tuple[dict[str, Any] | None, list[str]]:
    try:
        current = read_json(root / "handoff_manifest.json")
    except NamingError as error:
        return None, [str(error)]
    if current.get("schema") != HANDOFF_SCHEMA:
        return None, ["handoff_schema_mismatch"]
    if current.get("state") != "ready":
        return None, [str(reason) for reason in current.get("reasons") or ["reviewed_handoff_unavailable"]]
    try:
        material = build_material(session, decisions)
    except NamingError as error:
        return None, [str(error)]
    filenames = {
        "transcript_json": "transcript.rich.reviewed.json",
        "transcript_markdown": "transcript.rich.reviewed.md",
    }
    basis = semantic_basis(session, material, filenames)
    fingerprint = sha256_bytes(compact_bytes(basis))
    if current.get("generator") != implementation():
        return None, ["generator_fingerprint_mismatch"]
    if current.get("fingerprint_basis") != basis or current.get("semantic_fingerprint") != fingerprint:
        return None, ["semantic_fingerprint_mismatch"]
    if current.get("inputs") != material["inputs"] or current.get("summary") != material["summary"]:
        return None, ["manifest_material_mismatch"]
    bundle = current.get("bundle") if isinstance(current.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    try:
        bundle_path = resolve_inside(session, str(bundle.get("path") or ""))
    except NamingError:
        return None, ["bundle_path_invalid"]
    if bundle_path.name != fingerprint or set(files) != {"transcript_json", "transcript_markdown"}:
        return None, ["bundle_path_invalid"]
    for key, row in files.items():
        if not identity_matches(row, session):
            return None, [f"bundle_file_identity_mismatch:{key}"]
        if row.get("sha256") != sha256_bytes(material["outputs"][key]):
            return None, [f"bundle_semantic_mismatch:{key}"]
    bundle_manifest = bundle_path / "handoff_manifest.json"
    if not bundle_manifest.is_file() or bundle_manifest.read_bytes() != canonical_bytes(current):
        return None, ["bundle_manifest_mismatch"]
    baseline = (current.get("safety") or {}).get("baseline_identities")
    if not isinstance(baseline, dict) or not all(identity_matches(row, session) for row in baseline.values()):
        return None, ["source_output_fingerprint_mismatch"]
    return current, []


def reviewed_path(manifest: dict[str, Any], session: Path, key: str) -> Path | None:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    files = bundle.get("files") if isinstance(bundle.get("files"), dict) else {}
    row = files.get(key)
    path = resolve_identity(row, session)
    return path if path is not None and path.is_file() else None


def print_summary(manifest: dict[str, Any], root: Path, decisions: Path) -> None:
    summary = manifest.get("summary") or {}
    print("reviewed_speaker_naming:")
    print(f"  state: {manifest.get('state')}")
    print(f"  speakers: {summary.get('speaker_count', 0)}")
    print(f"  labeled: {summary.get('labeled_count', 0)}")
    print(f"  kept_anonymous: {summary.get('kept_anonymous_count', 0)}")
    if manifest.get("reasons"):
        print(f"  reason: {manifest['reasons'][0]}")
    print(f"  decisions: {decisions}")
    print(f"  manifest: {root / 'handoff_manifest.json'}")


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if not (session / "session.json").is_file():
        print(f"error: session.json not found under {session}", file=sys.stderr)
        return 2
    try:
        decisions = decisions_path(args, session)
        root = output_root(args, session)
    except NamingError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if args.command == "template":
        try:
            payload = write_template(session, decisions, args.force)
        except NamingError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        print("reviewed_speaker_template:")
        print(f"  state: {'complete' if payload.get('review_completed') else 'needs_review'}")
        print(f"  speakers: {len(payload.get('labels') or [])}")
        print(f"  path: {decisions}")
        print(f"  next: edit {decisions}, then run `murmurmark speakers apply {session}`")
        return 0

    if args.command == "apply":
        try:
            manifest = build_handoff(
                session,
                decisions,
                root,
                simulate_interruption_before_publish=args.simulate_interruption_before_publish,
            )
        except SimulatedInterruption as error:
            print(str(error))
            return 3
        except NamingError as error:
            manifest = unavailable_manifest(session, str(error))
            publish_unavailable(root, manifest)
            print_summary(manifest, root, decisions)
            return 2
        print_summary(manifest, root, decisions)
        path = reviewed_path(manifest, session, "transcript_markdown")
        if path is not None:
            print(f"  next: murmurmark transcript {session} --rich --reviewed-speakers")
        return 0

    manifest, reasons = verify_handoff(session, decisions, root)
    if manifest is None:
        if args.verify_only:
            for reason in reasons:
                print(reason)
        else:
            print_summary(unavailable_manifest(session, reasons[0]), root, decisions)
        return 2
    path = reviewed_path(manifest, session, "transcript_markdown")
    if args.print_path and path is not None:
        print(path)
    elif not args.verify_only:
        print_summary(manifest, root, decisions)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
