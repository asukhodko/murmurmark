#!/usr/bin/env python3
"""Recover strict remote unknown words with independent and structural evidence."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.1.0"
PROFILE = "remote_unknown_evidence_recovery_v1"
POLICY = ROOT / "policies/remote-unknown-evidence-recovery-v1.json"
V3_SCRIPT = ROOT / "scripts/audit-remote-speaker-coverage-v3.py"
DEFAULT_INPUT = Path("derived/audit/remote-speaker-coverage-v3")
DEFAULT_INDEPENDENT = Path("derived/audit/independent-remote-speaker-evidence-v1")
DEFAULT_OUTPUT = Path("derived/audit/remote-unknown-evidence-recovery-v1")

REPORT_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_report/v1"
DECISION_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_decision/v1"
CAUSE_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_cause_map/v1"
WORD_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_word/v1"
UTTERANCE_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_utterance/v1"
MAP_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_speaker_map/v1"
RICH_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_rich_transcript/v1"
MANIFEST_SCHEMA = "murmurmark.remote_unknown_evidence_recovery_manifest/v1"

OUTPUT_NAMES = (
    "recovery_decisions.jsonl",
    "unknown_cause_map.json",
    "word_attribution.jsonl",
    "utterance_attribution.jsonl",
    "speaker_map.json",
    "transcript.rich.shadow.json",
    "transcript.rich.shadow.md",
    "report.json",
    "report.md",
)


class RecoveryError(RuntimeError):
    pass


def load_v3() -> Any:
    spec = importlib.util.spec_from_file_location("murmurmark_unknown_recovery_v3", V3_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot_load_coverage_v3")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V3 = load_v3()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover Coverage v3 unknown words with WavLM plus structural evidence."
    )
    parser.add_argument("session", type=Path)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--independent-dir", type=Path, default=DEFAULT_INDEPENDENT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--policy", type=Path, default=POLICY)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RecoveryError(f"json_object_required:{path.name}")
    return payload


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(payload), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path: Path, session: Path | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"exists": path.is_file()}
    if session is not None:
        try:
            row["path"] = str(path.relative_to(session))
        except ValueError:
            row["path"] = str(path)
    else:
        row["path"] = str(path)
    if path.is_file():
        row.update({"bytes": path.stat().st_size, "sha256": sha256(path)})
    return row


def resolve_dir(session: Path, value: Path) -> Path:
    return value.expanduser().resolve() if value.is_absolute() else session / value


def input_paths(root: Path) -> dict[str, Path]:
    return {
        "report": root / "report.json",
        "manifest": root / "artifact_manifest.json",
        "decisions": root / "recovery_decisions.jsonl",
        "cause_map": root / "unknown_cause_map.json",
        "words": root / "word_attribution.jsonl",
        "utterances": root / "utterance_attribution.jsonl",
        "speaker_map": root / "speaker_map.json",
        "rich": root / "transcript.rich.shadow.json",
        "rich_markdown": root / "transcript.rich.shadow.md",
    }


def independent_paths(root: Path) -> dict[str, Path]:
    return {
        "report": root / "report.json",
        "manifest": root / "artifact_manifest.json",
        "decisions": root / "residual_decisions.jsonl",
        "units": root / "residual_units.jsonl",
    }


def verify_manifest(root: Path, path: Path) -> None:
    payload = read_json(path)
    artifacts = payload.get("artifacts") or {}
    if not artifacts:
        raise RecoveryError(f"artifact_manifest_empty:{root.name}")
    for name, expected in artifacts.items():
        artifact = root / str(name)
        if not artifact.is_file() or sha256(artifact) != str(expected):
            raise RecoveryError(f"artifact_stale:{root.name}:{name}")


def verify_coverage(session: Path, coverage_dir: Path) -> dict[str, Any]:
    coverage = input_paths(coverage_dir)
    missing = [str(path) for path in coverage.values() if not path.is_file()]
    if missing:
        raise RecoveryError("input_missing:" + ",".join(missing))
    coverage_report = read_json(coverage["report"])
    if (
        coverage_report.get("schema") != V3.REPORT_SCHEMA
        or coverage_report.get("decision") != "PUBLISH_EVIDENCE"
        or not V3.verify_v3_promotion(coverage_report)
    ):
        raise RecoveryError("coverage_v3_not_publishable")
    verify_manifest(coverage_dir, coverage["manifest"])
    if str((coverage_report.get("source") or {}).get("session_id")) != session.name:
        raise RecoveryError("coverage_session_mismatch")
    return coverage_report


def verify_independent(
    coverage_dir: Path,
    independent_dir: Path,
) -> dict[str, Any]:
    coverage = input_paths(coverage_dir)
    independent = independent_paths(independent_dir)
    missing = [str(path) for path in independent.values() if not path.is_file()]
    if missing:
        raise RecoveryError("input_missing:" + ",".join(missing))
    independent_report = read_json(independent["report"])
    if independent_report.get("decision") != "PUBLISH_EVIDENCE":
        raise RecoveryError("independent_evidence_not_publishable")
    required_gates = (
        "baseline_attributions_preserved",
        "protected_causes_preserved",
        "aggregate_fallback_exact",
    )
    if not all(bool((independent_report.get("gates") or {}).get(name)) for name in required_gates):
        raise RecoveryError("independent_evidence_safety_gate_failed")
    verify_manifest(independent_dir, independent["manifest"])
    source_v3 = (independent_report.get("source") or {}).get("v3_artifacts") or {}
    for name, path in coverage.items():
        row = source_v3.get(name)
        if not isinstance(row, dict) or row.get("sha256") != sha256(path):
            raise RecoveryError(f"independent_source_mismatch:{name}")
    return independent_report


def source_path(session: Path, row: Any) -> Path | None:
    if not isinstance(row, dict) or not row.get("path"):
        return None
    path = Path(str(row["path"])).expanduser()
    return path.resolve() if path.is_absolute() else session / path


def structural_evidence(
    unit: dict[str, Any],
    words: list[dict[str, Any]],
    words_by_utterance: dict[str, list[dict[str, Any]]],
    v1_by_utterance: dict[str, dict[str, Any]],
    policy: dict[str, Any],
) -> dict[str, Any]:
    candidate = str(unit.get("speaker_id") or "")
    uid = str(unit["utterance_id"])
    ids = set(str(value) for value in unit.get("word_ids") or [])
    unit_words = [row for row in words_by_utterance.get(uid, []) if str(row["word_id"]) in ids]
    if not unit_words:
        return {"supports": [], "conflicts": ["unit_words_missing"], "anchors": {}}
    start = min(float(row["start"]) for row in unit_words)
    end = max(float(row["end"]) for row in unit_words)
    decision = policy["decision"]
    same_limit = float(decision["maximum_same_utterance_anchor_gap_sec"])
    boundary_limit = float(decision["maximum_boundary_anchor_gap_sec"])

    same_rows = [
        row
        for row in words_by_utterance.get(uid, [])
        if row.get("speaker_id") and str(row["word_id"]) not in ids
    ]
    same_anchors: list[dict[str, Any]] = []
    for row in same_rows:
        distance = start - float(row["end"]) if float(row["end"]) <= start else float(row["start"]) - end
        distance = max(0.0, distance)
        if distance <= same_limit:
            same_anchors.append(
                {
                    "word_id": row["word_id"],
                    "speaker_id": row["speaker_id"],
                    "gap_sec": round(distance, 6),
                }
            )

    ordered = sorted(words, key=lambda row: (float(row["start"]), float(row["end"]), str(row["word_id"])))
    before = [row for row in ordered if row.get("speaker_id") and float(row["end"]) <= start]
    after = [row for row in ordered if row.get("speaker_id") and float(row["start"]) >= end]
    left = before[-1] if before else None
    right = after[0] if after else None
    boundary_anchors = []
    if left is not None and start - float(left["end"]) <= boundary_limit:
        boundary_anchors.append(
            {
                "side": "left",
                "word_id": left["word_id"],
                "speaker_id": left["speaker_id"],
                "gap_sec": round(start - float(left["end"]), 6),
            }
        )
    if right is not None and float(right["start"]) - end <= boundary_limit:
        boundary_anchors.append(
            {
                "side": "right",
                "word_id": right["word_id"],
                "speaker_id": right["speaker_id"],
                "gap_sec": round(float(right["start"]) - end, 6),
            }
        )

    supports: list[str] = []
    conflicts: list[str] = []
    v1 = v1_by_utterance.get(uid) or {}
    v1_speaker = v1.get("speaker_id")
    if v1_speaker:
        if (
            str(v1_speaker) == candidate
            and v1.get("status") == "attributed"
            and not (v1.get("overlap_utterance_ids") or [])
        ):
            supports.append("v1_utterance_attribution")
        elif str(v1_speaker) != candidate:
            conflicts.append("v1_speaker_disagreement")

    same_speakers = {str(row["speaker_id"]) for row in same_anchors}
    if candidate in same_speakers and same_speakers == {candidate}:
        supports.append("same_utterance_anchor")
    elif same_speakers - {candidate}:
        conflicts.append("same_utterance_anchor_disagreement")

    boundary_speakers = [str(row["speaker_id"]) for row in boundary_anchors]
    if (
        len(boundary_speakers) >= int(decision["minimum_boundary_anchor_count"])
        and set(boundary_speakers) == {candidate}
    ):
        supports.append("two_sided_boundary_anchors")
    elif set(boundary_speakers) - {candidate}:
        conflicts.append("boundary_anchor_disagreement")

    return {
        "supports": sorted(set(supports)),
        "conflicts": sorted(set(conflicts)),
        "anchors": {
            "same_utterance": same_anchors,
            "boundary": boundary_anchors,
            "v1": {
                "speaker_id": v1_speaker,
                "status": v1.get("status"),
                "reason": v1.get("reason"),
                "overlap_utterance_ids": v1.get("overlap_utterance_ids") or [],
            },
        },
    }


def utterance_outputs(
    rich: dict[str, Any],
    words_by_utterance: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str], int]:
    utterances = deepcopy(rich.get("utterances") or [])
    rows: list[dict[str, Any]] = []
    speaker_weights: Counter[str] = Counter()
    internal_changes = 0
    for utterance in utterances:
        if utterance.get("role") != "remote" or not utterance.get("id"):
            continue
        uid = str(utterance["id"])
        words = words_by_utterance.get(uid, [])
        turns = V3.build_turns(str(utterance.get("text") or ""), words)
        utterance["speaker_turns"] = turns
        distinct = list(dict.fromkeys(str(row["speaker_id"]) for row in turns if row.get("speaker_id")))
        internal_changes += int(len(distinct) > 1)
        weights: Counter[str] = Counter()
        for word in words:
            if word.get("speaker_id"):
                weight = float(word.get("coverage_weight_sec") or 0)
                weights[str(word["speaker_id"])] += weight
                speaker_weights[str(word["speaker_id"])] += weight
        total = sum(float(word.get("coverage_weight_sec") or 0) for word in words)
        attributed = sum(weights.values())
        dominant = weights.most_common(1)[0][0] if len(weights) == 1 else None
        if dominant and total and attributed / total < 0.80:
            dominant = None
            status = "partial"
        elif len(weights) > 1:
            status = "mixed"
        elif dominant:
            status = "attributed"
        else:
            status = "aggregate"
        rows.append(
            {
                "schema": UTTERANCE_SCHEMA,
                "utterance_id": uid,
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or 0),
                "speaker_id": dominant,
                "speaker_label": dominant or "Colleagues",
                "status": status,
                "reason": "word_level_unknown_recovery_evidence" if dominant else "explicit_unknown_preserved",
                "speaker_turns": turns,
                "attributed_weight_sec": round(attributed, 9),
                "total_weight_sec": round(total, 9),
            }
        )
    return utterances, rows, speaker_weights, internal_changes


def transcript_markdown(utterances: list[dict[str, Any]], profile: str) -> str:
    lines = [
        "# Remote Unknown Evidence Recovery v1",
        "",
        "> Shadow evidence only. Coverage v3 and the aggregate transcript remain authoritative.",
        "> Recovered labels require independent WavLM plus session-local structural evidence.",
        "",
        f"Selected profile: `{profile}`  ",
        "",
    ]
    for utterance in utterances:
        start = float(utterance.get("start") or 0)
        timestamp = f"{int(start // 60):02d}:{int(start % 60):02d}"
        if utterance.get("role") == "remote":
            for turn in utterance.get("speaker_turns") or []:
                label = str(turn.get("speaker_label") or "Colleagues")
                suffix = "" if turn.get("speaker_id") else " [unknown]"
                lines.extend([f"## {timestamp} {label}{suffix}", "", str(turn.get("text") or "").strip(), ""])
        else:
            lines.extend([f"## {timestamp} Me", "", str(utterance.get("text") or "").strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def report_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Remote Unknown Evidence Recovery v1",
        "",
        f"Decision: `{report['decision']}`",
        f"Recovered: `{summary['recovered_words']}` words / `{summary['recovered_seconds']:.3f}s`",
        f"Remaining: `{summary['remaining_unknown_words']}` words / `{summary['remaining_unknown_seconds']:.3f}s`",
        "",
        "## Reasons",
        "",
    ]
    for row in report.get("outcomes") or []:
        lines.append(f"- `{row['reason']}`: {row['words']} words / {row['seconds']:.3f}s")
    lines.extend(["", "## Gates", ""])
    lines.extend(f"- `{name}`: `{str(value).lower()}`" for name, value in report["gates"].items())
    return "\n".join(lines) + "\n"


def materialize_fallback(
    session: Path,
    coverage_dir: Path,
    independent_dir: Path,
    out_dir: Path,
    policy_path: Path,
    coverage_report: dict[str, Any],
    reason: str,
) -> int:
    coverage = input_paths(coverage_dir)
    words = read_jsonl(coverage["words"])
    unknown = [row for row in words if not row.get("speaker_id")]
    unknown_seconds = sum(float(row.get("coverage_weight_sec") or 0) for row in unknown)
    decisions = [
        {
            "schema": DECISION_SCHEMA,
            "word_id": row["word_id"],
            "utterance_id": row["utterance_id"],
            "start": row["start"],
            "end": row["end"],
            "coverage_weight_sec": float(row.get("coverage_weight_sec") or 0),
            "baseline_cause": row.get("v3_reason") or row.get("reason"),
            "outcome": "unknown",
            "speaker_id": None,
            "reason": "independent_evidence_unavailable",
            "evidence": {"failure": reason},
        }
        for row in unknown
    ]
    source = {
        "session_id": session.name,
        "coverage_v3": {name: fingerprint(path, session) for name, path in coverage.items()},
        "independent_wavlm": {
            name: fingerprint(path, session)
            for name, path in independent_paths(independent_dir).items()
        },
        "policy": fingerprint(policy_path),
    }
    summary = {
        "remote_words": len(words),
        "baseline_unknown_words": len(unknown),
        "baseline_unknown_seconds": round(unknown_seconds, 6),
        "recovered_words": 0,
        "recovered_seconds": 0.0,
        "remaining_unknown_words": len(unknown),
        "remaining_unknown_seconds": round(unknown_seconds, 6),
        "unknown_words_reduction_ratio": 0.0,
        "unknown_seconds_reduction_ratio": 0.0,
        "remote_speech_sec": float((coverage_report.get("summary") or {}).get("remote_speech_sec") or 0),
        "attributed_speech_sec": float(
            (coverage_report.get("summary") or {}).get("attributed_speech_sec") or 0
        ),
        "published_speakers": int(
            (coverage_report.get("summary") or {}).get("published_speakers") or 0
        ),
        "internal_change_utterances": int(
            (coverage_report.get("summary") or {}).get("internal_change_utterances") or 0
        ),
    }
    outcomes = [
        {
            "reason": "independent_evidence_unavailable",
            "words": len(unknown),
            "seconds": round(unknown_seconds, 6),
        }
    ]
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "profile": PROFILE,
        "implementation": {
            "script": fingerprint(Path(__file__).resolve()),
            "version": VERSION,
        },
        "status": "fallback",
        "decision": "FALLBACK_COVERAGE_V3",
        "reasons": [reason],
        "source": source,
        "summary": summary,
        "outcomes": outcomes,
        "gates": {
            "coverage_v3_current": True,
            "independent_evidence_current": False,
            "coverage_v3_fallback_exact": True,
        },
        "safety": {
            "production_selection": False,
            "coverage_v3_unchanged": True,
            "aggregate_transcript_unchanged": True,
            "raw_audio_unchanged": True,
            "fallback": "remote_speaker_coverage_v3",
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "recovery_decisions.jsonl", decisions)
    write_json(
        out_dir / "unknown_cause_map.json",
        {
            "schema": CAUSE_SCHEMA,
            "session_id": session.name,
            "baseline_unknown_words": len(unknown),
            "baseline_unknown_seconds": round(unknown_seconds, 6),
            "outcomes": outcomes,
        },
    )
    for source_name, target_name in (
        ("words", "word_attribution.jsonl"),
        ("utterances", "utterance_attribution.jsonl"),
        ("speaker_map", "speaker_map.json"),
        ("rich", "transcript.rich.shadow.json"),
        ("rich_markdown", "transcript.rich.shadow.md"),
    ):
        (out_dir / target_name).write_bytes(coverage[source_name].read_bytes())
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(out_dir / "artifact_manifest.json", build_manifest(out_dir, session, source))
    print(
        f"remote_unknown_recovery_v1: decision=FALLBACK_COVERAGE_V3 "
        f"reason={reason} remaining={len(unknown)}w/{unknown_seconds:.3f}s"
    )
    return 0


def build_manifest(out_dir: Path, session: Path, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "version": VERSION,
        "session_id": session.name,
        "profile": PROFILE,
        "source": source,
        "artifacts": {name: sha256(out_dir / name) for name in OUTPUT_NAMES},
    }


def fingerprint_current(session: Path, row: Any) -> bool:
    if not isinstance(row, dict) or not row.get("path") or not row.get("sha256"):
        return False
    path = Path(str(row["path"])).expanduser()
    path = path.resolve() if path.is_absolute() else session / path
    return path.is_file() and row.get("bytes") == path.stat().st_size and row.get("sha256") == sha256(path)


def verify_existing(session: Path, out_dir: Path) -> int:
    manifest_path = out_dir / "artifact_manifest.json"
    if not manifest_path.is_file():
        print("remote unknown recovery manifest missing", file=sys.stderr)
        return 2
    try:
        manifest = read_json(manifest_path)
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise RecoveryError("manifest_schema_invalid")
        verify_manifest(out_dir, manifest_path)
        report = read_json(out_dir / "report.json")
        source = report.get("source") or {}
        if not all(fingerprint_current(session, row) for row in (source.get("coverage_v3") or {}).values()):
            raise RecoveryError("coverage_source_stale")
        if not fingerprint_current(session, source.get("policy")):
            raise RecoveryError("policy_source_stale")
        if not fingerprint_current(session, (report.get("implementation") or {}).get("script")):
            raise RecoveryError("implementation_stale")
        if report.get("decision") == "PUBLISH_SHADOW_EVIDENCE" and not all(
            fingerprint_current(session, row)
            for row in (source.get("independent_wavlm") or {}).values()
        ):
            raise RecoveryError("independent_source_stale")
    except (OSError, ValueError, json.JSONDecodeError, RecoveryError) as error:
        print(f"remote unknown recovery artifacts are stale: {error}", file=sys.stderr)
        return 2
    print("remote unknown evidence recovery v1 artifacts verified")
    return 0


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    coverage_dir = resolve_dir(session, args.input_dir)
    independent_dir = resolve_dir(session, args.independent_dir)
    out_dir = resolve_dir(session, args.out_dir)
    if args.verify_only:
        return verify_existing(session, out_dir)
    policy_path = args.policy.expanduser().resolve()
    policy = read_json(policy_path)
    if policy.get("schema") != "murmurmark.remote_unknown_evidence_recovery_policy/v1":
        raise RecoveryError("policy_schema_invalid")
    coverage_report = verify_coverage(session, coverage_dir)
    try:
        independent_report = verify_independent(coverage_dir, independent_dir)
    except RecoveryError as error:
        return materialize_fallback(
            session,
            coverage_dir,
            independent_dir,
            out_dir,
            policy_path,
            coverage_report,
            str(error),
        )
    coverage = input_paths(coverage_dir)
    independent = independent_paths(independent_dir)
    words = read_jsonl(coverage["words"])
    rich = read_json(coverage["rich"])
    speaker_map = read_json(coverage["speaker_map"])
    independent_decisions = {str(row["word_id"]): row for row in read_jsonl(independent["decisions"])}
    independent_units = {str(row["unit_id"]): row for row in read_jsonl(independent["units"])}
    v1_path = source_path(session, (coverage_report.get("source") or {}).get("v1_attribution"))
    if v1_path is None or not v1_path.is_file():
        raise RecoveryError("v1_attribution_missing")
    v1_rows = read_jsonl(v1_path)
    v1_by_utterance = {str(row["utterance_id"]): row for row in v1_rows}
    speakers = {
        str(row["speaker_id"])
        for row in speaker_map.get("speakers") or []
        if row.get("speaker_id") and int(row.get("seed_units") or 0) > 0
    }
    if not speakers:
        raise RecoveryError("seeded_speakers_empty")

    baseline_by_id = {str(row["word_id"]): row for row in words}
    baseline_assignments = {word_id: row.get("speaker_id") for word_id, row in baseline_by_id.items()}
    baseline_unknown = [row for row in words if not row.get("speaker_id")]
    if set(independent_decisions) != {str(row["word_id"]) for row in baseline_unknown}:
        raise RecoveryError("independent_unknown_word_set_mismatch")
    baseline_words_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in words:
        baseline_words_by_utterance[str(row["utterance_id"])].append(row)
    for rows in baseline_words_by_utterance.values():
        rows.sort(key=lambda row: (float(row["start"]), float(row["end"]), str(row["word_id"])))

    unit_structural: dict[str, dict[str, Any]] = {}
    for unit_id, unit in independent_units.items():
        if unit.get("outcome") == "attributed" and unit.get("speaker_id"):
            unit_structural[unit_id] = structural_evidence(
                unit, words, baseline_words_by_utterance, v1_by_utterance, policy
            )

    protected = set(str(value) for value in policy["decision"]["protected_causes"])
    decisions: list[dict[str, Any]] = []
    output_words: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, float | int]] = defaultdict(lambda: {"words": 0, "seconds": 0.0})
    recovered_words = 0
    recovered_seconds = 0.0
    for baseline in words:
        if baseline.get("speaker_id"):
            output_words.append(deepcopy(baseline))
            continue
        word_id = str(baseline["word_id"])
        independent_row = independent_decisions[word_id]
        unit_id = str(independent_row.get("residual_unit_id") or "")
        unit = independent_units.get(unit_id)
        structural = unit_structural.get(unit_id, {"supports": [], "conflicts": [], "anchors": {}})
        cause = str(baseline.get("v3_reason") or independent_row.get("baseline_cause") or "unknown")
        candidate = independent_row.get("speaker_id") if independent_row.get("outcome") == "attributed" else None
        accepted = bool(
            candidate
            and str(candidate) in speakers
            and cause not in protected
            and structural["supports"]
            and not structural["conflicts"]
        )
        if cause in protected:
            reason = "protected_unknown"
        elif not candidate:
            reason = "independent_wavlm_abstained"
        elif structural["conflicts"]:
            reason = "structural_evidence_conflict"
        elif not structural["supports"]:
            reason = "independent_without_structural_confirmation"
        elif accepted:
            reason = "independent_wavlm_structural_consensus"
        else:
            reason = "unsupported_candidate"
        weight = float(baseline.get("coverage_weight_sec") or 0)
        word = deepcopy(baseline)
        if accepted:
            word.update(
                {
                    "schema": WORD_SCHEMA,
                    "speaker_id": str(candidate),
                    "speaker_label": str(candidate),
                    "status": "attributed",
                    "reason": reason,
                    "unknown_recovery_v1": {
                        "baseline_reason": baseline.get("reason"),
                        "baseline_v3_reason": cause,
                        "independent_unit_id": unit_id,
                        "structural_support": structural["supports"],
                    },
                }
            )
            recovered_words += 1
            recovered_seconds += weight
        output_words.append(word)
        outcomes[reason]["words"] = int(outcomes[reason]["words"]) + 1
        outcomes[reason]["seconds"] = float(outcomes[reason]["seconds"]) + weight
        decisions.append(
            {
                "schema": DECISION_SCHEMA,
                "word_id": word_id,
                "utterance_id": baseline["utterance_id"],
                "start": baseline["start"],
                "end": baseline["end"],
                "coverage_weight_sec": weight,
                "baseline_cause": cause,
                "outcome": "attributed" if accepted else "unknown",
                "speaker_id": str(candidate) if accepted else None,
                "reason": reason,
                "evidence": {
                    "independent_wavlm": {
                        "decision": independent_row,
                        "unit": unit,
                    },
                    "structural": structural,
                },
            }
        )

    words_by_utterance: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in output_words:
        words_by_utterance[str(row["utterance_id"])].append(row)
    utterances, attributions, speaker_weights, internal_changes = utterance_outputs(
        rich, words_by_utterance
    )
    output_by_id = {str(row["word_id"]): row for row in output_words}
    exact_fields = ("word_id", "utterance_id", "text", "normalized", "start", "end", "start_char", "end_char")
    word_identity_exact = all(
        all(output_by_id[word_id].get(field) == baseline.get(field) for field in exact_fields)
        for word_id, baseline in baseline_by_id.items()
    ) and list(output_by_id) == list(baseline_by_id)
    baseline_labels_preserved = all(
        baseline_assignments[word_id] in {None, output_by_id[word_id].get("speaker_id")}
        for word_id in baseline_by_id
    )
    protected_preserved = all(
        output_by_id[str(row["word_id"])].get("speaker_id") is None
        for row in baseline_unknown
        if str(row.get("v3_reason") or "") in protected
    )
    seeded_speakers_only = all(
        row.get("speaker_id") is None or str(row["speaker_id"]) in speakers for row in output_words
    )
    selected_text_unchanged = all(
        "".join(str(turn.get("text") or "") for turn in utterance.get("speaker_turns") or [])
        == str(utterance.get("text") or "")
        for utterance in utterances
        if utterance.get("role") == "remote"
    )
    baseline_utterances = rich.get("utterances") or []
    utterance_identity_fields = ("id", "role", "start", "end", "text")
    utterance_identity_exact = len(utterances) == len(baseline_utterances) and all(
        all(output.get(field) == baseline.get(field) for field in utterance_identity_fields)
        for output, baseline in zip(utterances, baseline_utterances, strict=True)
    )
    baseline_me = [row for row in rich.get("utterances") or [] if row.get("role") != "remote"]
    output_me = [row for row in utterances if row.get("role") != "remote"]
    me_unchanged = baseline_me == output_me
    gates = {
        "coverage_v3_current": True,
        "independent_evidence_current": True,
        "word_identity_exact": word_identity_exact,
        "baseline_labels_preserved": baseline_labels_preserved,
        "protected_unknown_preserved": protected_preserved,
        "seeded_speakers_only": seeded_speakers_only,
        "selected_text_unchanged": selected_text_unchanged,
        "utterance_identity_exact": utterance_identity_exact,
        "me_unchanged": me_unchanged,
        "neighbor_only_assignment_forbidden": all(
            row["outcome"] != "attributed"
            or bool((row["evidence"]["independent_wavlm"]["decision"] or {}).get("speaker_id"))
            for row in decisions
        ),
        "structural_confirmation_required": all(
            row["outcome"] != "attributed" or bool(row["evidence"]["structural"]["supports"])
            for row in decisions
        ),
    }
    publish = all(gates.values())
    baseline_unknown_seconds = sum(float(row.get("coverage_weight_sec") or 0) for row in baseline_unknown)
    remaining_words = len(baseline_unknown) - recovered_words
    remaining_seconds = max(0.0, baseline_unknown_seconds - recovered_seconds)
    remote_speech_sec = float((coverage_report.get("summary") or {}).get("remote_speech_sec") or 0)
    baseline_attributed_sec = float((coverage_report.get("summary") or {}).get("attributed_speech_sec") or 0)
    source = {
        "session_id": session.name,
        "selected_profile": (coverage_report.get("source") or {}).get("profile"),
        "coverage_v3": {name: fingerprint(path, session) for name, path in coverage.items()},
        "independent_wavlm": {name: fingerprint(path, session) for name, path in independent.items()},
        "v1_attribution": fingerprint(v1_path, session),
        "policy": fingerprint(policy_path),
    }
    outcome_rows = [
        {
            "reason": reason,
            "words": int(values["words"]),
            "seconds": round(float(values["seconds"]), 6),
        }
        for reason, values in sorted(outcomes.items())
    ]
    report = {
        "schema": REPORT_SCHEMA,
        "version": VERSION,
        "profile": PROFILE,
        "implementation": {
            "script": fingerprint(Path(__file__).resolve()),
            "version": VERSION,
        },
        "status": "completed" if publish else "fallback",
        "decision": "PUBLISH_SHADOW_EVIDENCE" if publish else "FALLBACK_COVERAGE_V3",
        "source": source,
        "parameters": policy["decision"],
        "summary": {
            "remote_words": len(output_words),
            "baseline_unknown_words": len(baseline_unknown),
            "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
            "recovered_words": recovered_words,
            "recovered_seconds": round(recovered_seconds, 6),
            "remaining_unknown_words": remaining_words,
            "remaining_unknown_seconds": round(remaining_seconds, 6),
            "unknown_words_reduction_ratio": round(recovered_words / len(baseline_unknown), 6)
            if baseline_unknown
            else 0.0,
            "unknown_seconds_reduction_ratio": round(recovered_seconds / baseline_unknown_seconds, 6)
            if baseline_unknown_seconds
            else 0.0,
            "remote_speech_sec": round(remote_speech_sec, 6),
            "attributed_speech_sec": round(baseline_attributed_sec + recovered_seconds, 6),
            "published_speakers": len(speakers),
            "internal_change_utterances": internal_changes,
        },
        "outcomes": outcome_rows,
        "gates": gates,
        "safety": {
            "production_selection": False,
            "coverage_v3_unchanged": True,
            "aggregate_transcript_unchanged": True,
            "raw_audio_unchanged": True,
            "identity_scope": "session_local_anonymous",
            "fallback": "remote_speaker_coverage_v3",
        },
    }
    if not publish:
        raise RecoveryError("output_invariant_failed:" + ",".join(name for name, value in gates.items() if not value))

    baseline_speakers = {str(row["speaker_id"]): row for row in speaker_map.get("speakers") or []}
    output_speakers = []
    for speaker in sorted(speakers):
        row = deepcopy(baseline_speakers[speaker])
        row["attributed_speech_sec"] = round(float(speaker_weights[speaker]), 6)
        output_speakers.append(row)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "recovery_decisions.jsonl", decisions)
    write_json(
        out_dir / "unknown_cause_map.json",
        {
            "schema": CAUSE_SCHEMA,
            "session_id": session.name,
            "baseline_unknown_words": len(baseline_unknown),
            "baseline_unknown_seconds": round(baseline_unknown_seconds, 6),
            "outcomes": outcome_rows,
        },
    )
    write_jsonl(out_dir / "word_attribution.jsonl", output_words)
    write_jsonl(out_dir / "utterance_attribution.jsonl", attributions)
    write_json(
        out_dir / "speaker_map.json",
        {
            "schema": MAP_SCHEMA,
            "session_id": session.name,
            "profile": PROFILE,
            "decision": report["decision"],
            "speakers": output_speakers,
        },
    )
    write_json(
        out_dir / "transcript.rich.shadow.json",
        {
            "schema": RICH_SCHEMA,
            "session_id": session.name,
            "selected_profile": source["selected_profile"],
            "speaker_profile": PROFILE,
            "decision": report["decision"],
            "source": source,
            "utterances": utterances,
            "remote_speaker_attributions": attributions,
            "remote_word_attributions": output_words,
            "speaker_map": output_speakers,
            "safety": report["safety"],
        },
    )
    (out_dir / "transcript.rich.shadow.md").write_text(
        transcript_markdown(utterances, str(source["selected_profile"] or "auto")), encoding="utf-8"
    )
    write_json(out_dir / "report.json", report)
    (out_dir / "report.md").write_text(report_markdown(report), encoding="utf-8")
    write_json(out_dir / "artifact_manifest.json", build_manifest(out_dir, session, source))
    print(
        f"remote_unknown_recovery_v1: decision={report['decision']} "
        f"recovered={recovered_words}w/{recovered_seconds:.3f}s "
        f"remaining={remaining_words}w/{remaining_seconds:.3f}s"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, RecoveryError) as error:
        print(f"remote_unknown_recovery_v1: error={error}", file=sys.stderr)
        raise SystemExit(2)
