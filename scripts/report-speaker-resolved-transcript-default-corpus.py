#!/usr/bin/env python3
"""Verify the default speaker-resolved transcript selector on the frozen six-session corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
SCHEMA = "murmurmark.speaker_resolved_transcript_default_corpus/v1"
MANIFEST_SCHEMA = "murmurmark.speaker_resolved_transcript_default_frozen_manifest/v1"
SELECTOR = ROOT / "scripts/select-speaker-resolved-transcript.py"
DEFAULT_BASELINE_MANIFEST = ROOT / "docs/testing/remote-speaker-coverage-v3-manifest.json"
DEFAULT_BASELINE_REPORT = (
    ROOT / "sessions/_reports/remote-speaker-coverage-v3/remote_speaker_coverage_corpus_report.json"
)
DEFAULT_OUT_DIR = ROOT / "sessions/_reports/speaker-resolved-transcript-default-v1"
DEFAULT_MANIFEST = ROOT / "docs/testing/speaker-resolved-transcript-default-v1-manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify promoted v3 as the ordinary transcript read/handoff surface."
    )
    parser.add_argument("scope", nargs="?", choices=("all",), default="all")
    parser.add_argument("--sessions-root", type=Path, default=ROOT / "sessions")
    parser.add_argument("--baseline-manifest", type=Path, default=DEFAULT_BASELINE_MANIFEST)
    parser.add_argument("--baseline-report", type=Path, default=DEFAULT_BASELINE_REPORT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument(
        "--frozen-manifest", "--manifest", dest="manifest", type=Path, default=DEFAULT_MANIFEST
    )
    parser.add_argument("--refresh-evidence", action="store_true")
    parser.add_argument("--verify-existing", action="store_true")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--require-promoted", action="store_true")
    return parser.parse_args()


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def portable(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def artifact(path: Path) -> dict[str, Any]:
    return {
        "path": portable(path),
        "exists": path.is_file(),
        **({"bytes": path.stat().st_size, "sha256": sha256(path)} if path.is_file() else {}),
    }


def resolve_session_path(session: Path, row: Any) -> Path:
    if not isinstance(row, dict) or not isinstance(row.get("path"), str):
        raise RuntimeError("artifact path missing")
    raw = Path(row["path"])
    return raw if raw.is_absolute() else session / raw


def run_selector(session: Path, refresh: bool) -> bytes:
    command = [sys.executable, str(SELECTOR), str(session), "--require-speaker-resolved"]
    if refresh:
        command.append("--refresh-evidence")
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"selector failed for {session.name}: {completed.stdout.strip()} {completed.stderr.strip()}"
        )
    path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    first = path.read_bytes()
    replay = subprocess.run(
        [sys.executable, str(SELECTOR), str(session), "--require-speaker-resolved"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if replay.returncode != 0 or path.read_bytes() != first:
        raise RuntimeError(f"selector replay is not deterministic for {session.name}")
    return first


def dialogue_exact(dialogue: dict[str, Any], rich: dict[str, Any]) -> bool:
    source_rows = dialogue.get("utterances") if isinstance(dialogue.get("utterances"), list) else []
    rich_rows = rich.get("utterances") if isinstance(rich.get("utterances"), list) else []
    if len(source_rows) != len(rich_rows):
        return False
    keys = ("id", "role", "speaker_label", "start", "end", "text")
    return all(
        all(source.get(key) == selected.get(key) for key in keys)
        for source, selected in zip(source_rows, rich_rows)
        if isinstance(source, dict) and isinstance(selected, dict)
    ) and all(isinstance(row, dict) for row in source_rows + rich_rows)


def session_row(
    session_id: str,
    expected: dict[str, Any],
    refresh: bool,
    sessions_root: Path,
) -> dict[str, Any]:
    session = sessions_root / session_id
    run_selector(session, refresh)
    selection_path = session / "derived/transcript-rich/speaker-resolved-default-v1/selection.json"
    selection = read_json(selection_path)
    coverage_report_path = resolve_session_path(session, selection.get("coverage_report"))
    coverage_dir = coverage_report_path.parent
    report = read_json(coverage_report_path)
    rich_path = coverage_dir / "transcript.rich.shadow.json"
    rich = read_json(rich_path)
    dialogue_path = resolve_session_path(session, (report.get("source") or {}).get("dialogue"))
    dialogue = read_json(dialogue_path)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    gates = report.get("gates") if isinstance(report.get("gates"), dict) else {}
    safety = report.get("safety") if isinstance(report.get("safety"), dict) else {}
    expected_speakers = expected.get("expected_speakers") if isinstance(expected, dict) else {}
    minimum = int((expected_speakers or {}).get("min") or 0)
    maximum = int((expected_speakers or {}).get("max") or 999)
    published = int(summary.get("published_speakers") or 0)
    selected_path = resolve_session_path(session, selection.get("selected_transcript"))
    return {
        "session_id": session_id,
        "selected_profile": selection.get("selected_profile"),
        "selected_speaker_profile": selection.get("selected_speaker_profile"),
        "state": selection.get("state"),
        "fallback_reason": selection.get("fallback_reason"),
        "published_speakers": published,
        "expected_speakers": {"min": minimum, "max": maximum},
        "attributable_remote_speech_ratio": summary.get("attributable_remote_speech_ratio"),
        "remaining_unknown_words": summary.get("remaining_unknown_words"),
        "remaining_unknown_seconds": summary.get("remaining_unknown_seconds"),
        "gates": {
            "speaker_resolved_selected": selection.get("state") == "selected",
            "speaker_count_expected": minimum <= published <= maximum,
            "selected_dialogue_exact": dialogue_exact(dialogue, rich),
            "selected_text_unchanged": gates.get("selected_text_unchanged") is True,
            "word_conservation": gates.get("word_conservation") is True,
            "word_timestamps_unchanged": gates.get("word_timestamps_unchanged") is True,
            "me_unchanged": gates.get("me_unchanged") is True,
            "timestamp_order": gates.get("timestamp_order") is True,
            "raw_audio_unchanged": safety.get("raw_audio_unchanged") is True,
            "session_local_anonymous_only": safety.get("session_local_anonymous_only") is True,
            "selected_markdown_current": (
                selected_path.is_file()
                and selection["selected_transcript"].get("sha256") == sha256(selected_path)
            ),
        },
        "artifacts": {
            "coverage_manifest": artifact(coverage_dir / "artifact_manifest.json"),
            "coverage_report": artifact(coverage_report_path),
            "coverage_rich": artifact(rich_path),
        },
    }


def build_manifest(rows: list[dict[str, Any]], decision: str) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "decision": decision,
        "implementation": {
            "selector": artifact(SELECTOR),
            "corpus_report": artifact(Path(__file__).resolve()),
        },
        "baseline": {
            "coverage_policy": artifact(ROOT / "policies/remote-speaker-coverage-v3.json"),
            "coverage_manifest": artifact(DEFAULT_BASELINE_MANIFEST),
            "boundary_cases": artifact(
                ROOT / "docs/testing/remote-speaker-diarization-v2-boundaries.json"
            ),
        },
        "sessions": [
            {
                "session_id": row["session_id"],
                "selected_profile": row["selected_profile"],
                "expected_speakers": row["expected_speakers"],
                **row["artifacts"],
            }
            for row in rows
        ],
    }


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Speaker-Resolved Transcript Default v1 Corpus",
        "",
        f"Decision: `{report['decision']}`",
        "",
        "| Session | Profile | Speakers | Expected | Coverage | State |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in report["sessions"]:
        expected = row["expected_speakers"]
        lines.append(
            f"| `{row['session_id']}` | `{row['selected_profile']}` | "
            f"{row['published_speakers']} | {expected['min']}..{expected['max']} | "
            f"{float(row.get('attributable_remote_speech_ratio') or 0):.4f} | `{row['state']}` |"
        )
    lines += ["", "## Gates", ""]
    lines.extend(f"- `{key}`: `{value}`" for key, value in report["gates"].items())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    baseline_manifest = read_json(args.baseline_manifest)
    baseline_report = read_json(args.baseline_report)
    expected_by_id = {
        str(row.get("session_id")): row
        for row in baseline_report.get("sessions") or []
        if isinstance(row, dict)
    }
    session_ids = [
        str(row.get("session_id"))
        for row in baseline_manifest.get("sessions") or []
        if isinstance(row, dict) and row.get("session_id")
    ]
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for session_id in session_ids:
        print(f"speaker_default_corpus: {session_id}", flush=True)
        try:
            rows.append(
                session_row(
                    session_id,
                    expected_by_id.get(session_id, {}),
                    args.refresh_evidence,
                    args.sessions_root.resolve(),
                )
            )
        except Exception as error:
            errors.append(f"{session_id}:{error}")
    all_session_gates = all(all(row["gates"].values()) for row in rows)
    one_to_one = [row for row in rows if row["expected_speakers"]["max"] == 1]
    groups = [row for row in rows if row["expected_speakers"]["min"] >= 2]
    baseline_gates = baseline_report.get("gates") if isinstance(baseline_report.get("gates"), dict) else {}
    gates = {
        "six_sessions": len(rows) == 6 and len(session_ids) == 6,
        "all_session_gates": all_session_gates,
        "all_one_to_one_controls": bool(one_to_one)
        and all(row["gates"]["speaker_count_expected"] for row in one_to_one),
        "all_group_controls": bool(groups)
        and all(row["gates"]["speaker_count_expected"] for row in groups),
        "five_boundary_cases": baseline_gates.get("boundary_cases") is True,
        "deterministic_replay": not errors,
        "no_errors": not errors,
    }
    decision = "PROMOTE" if all(gates.values()) else "DO_NOT_PROMOTE"
    report = {
        "schema": SCHEMA,
        "version": VERSION,
        "decision": decision,
        "sessions": rows,
        "summary": {
            "sessions": len(rows),
            "one_to_one_sessions": len(one_to_one),
            "group_sessions": len(groups),
            "published_speakers": sum(int(row["published_speakers"]) for row in rows),
            "remaining_unknown_words": sum(int(row.get("remaining_unknown_words") or 0) for row in rows),
            "remaining_unknown_seconds": round(
                sum(float(row.get("remaining_unknown_seconds") or 0) for row in rows), 6
            ),
        },
        "gates": gates,
        "errors": errors,
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "report.json").write_bytes(canonical(report))
    (args.out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    if args.write_manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_bytes(canonical(build_manifest(rows, decision)))
    elif args.manifest.is_file():
        frozen = read_json(args.manifest)
        if frozen.get("schema") != MANIFEST_SCHEMA or frozen.get("decision") != "PROMOTE":
            report["gates"]["frozen_manifest_promoted"] = False
            decision = "DO_NOT_PROMOTE"
        else:
            expected = {
                row["session_id"]: row for row in frozen.get("sessions") or [] if isinstance(row, dict)
            }
            frozen_match = all(
                session_id in expected
                and expected[session_id].get("selected_profile") == row["selected_profile"]
                and all(
                    expected[session_id].get(key, {}).get("sha256")
                    == row["artifacts"][key].get("sha256")
                    for key in ("coverage_manifest", "coverage_report", "coverage_rich")
                )
                for session_id, row in ((row["session_id"], row) for row in rows)
            )
            report["gates"]["frozen_manifest_promoted"] = frozen_match
            if not frozen_match:
                decision = "DO_NOT_PROMOTE"
        report["decision"] = decision
        (args.out_dir / "report.json").write_bytes(canonical(report))
        (args.out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    print(f"speaker_default_corpus: decision={decision} sessions={len(rows)}")
    return 0 if not args.require_promoted or decision == "PROMOTE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
