#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA = "murmurmark.suppressed_mic_composite_gate_lab/v1"
SCRIPT_VERSION = "0.1.0"
DEFAULT_REPORT = Path("sessions/_reports/live-pipeline/suppressed_mic_composite_gate_lab.json")
LOCAL_LABELS = {"me_dominant", "mixed"}
REMOTE_RISK_LABELS = {"remote_dominant", "none"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate composite live-accessible suppressed-mic role gates using existing audio/text, "
            "Target-Me and persistent Target-Me evidence. Batch labels are used only for offline scoring."
        )
    )
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--scope",
        choices=("capture-safe-candidate", "real", "all-live"),
        default="capture-safe-candidate",
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-examples", type=int, default=8)
    return parser.parse_args()


def load_compare_module(root: Path) -> Any:
    module_path = root / "scripts" / "compare-live-batch.py"
    spec = importlib.util.spec_from_file_location("murmurmark_compare_live_batch", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return number


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def round_float(value: float | None, digits: int = 6) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def resolve_session(root: Path, value: Path) -> Path:
    if value.is_absolute():
        return value
    if value.parts and value.parts[0] == "sessions":
        return root / value
    return root / "sessions" / value


def report_session_ids(root: Path, scope: str) -> set[str]:
    report = read_json(root / "sessions/_reports/live-pipeline/live_corpus_gates_report.json")
    if not isinstance(report, dict):
        return set()
    if scope == "capture-safe-candidate":
        candidate_scope = report.get("capture_safe_candidate_scope")
        ids = candidate_scope.get("session_ids") if isinstance(candidate_scope, dict) else None
        return {str(item) for item in ids or []}
    sessions = report.get("sessions")
    if not isinstance(sessions, list):
        return set()
    result: set[str] = set()
    for row in sessions:
        if not isinstance(row, dict):
            continue
        item_id = str(row.get("session") or "")
        if not item_id:
            continue
        if scope == "real" and row.get("evidence_scope") != "real_meeting":
            continue
        result.add(item_id)
    return result


def discover_sessions(root: Path, scope: str, explicit: list[Path]) -> list[Path]:
    if explicit:
        sessions = [resolve_session(root, item) for item in explicit]
        return [item for item in sessions if (item / "derived/live/chunks.jsonl").exists()]
    known_ids = report_session_ids(root, scope)
    sessions: list[Path] = []
    for chunks in sorted((root / "sessions").glob("*/derived/live/chunks.jsonl")):
        session = chunks.parents[2]
        if session.name.startswith("_"):
            continue
        if known_ids and session.name not in known_ids:
            continue
        sessions.append(session)
    return sessions


def segment_key(session_id: str, chunk_index: Any, start: Any, end: Any) -> tuple[str, int, float, float]:
    return (session_id, safe_int(chunk_index), round(safe_float(start), 3), round(safe_float(end), 3))


def target_me_index(session: Path) -> dict[tuple[str, int, float, float], dict[str, Any]]:
    rows = read_jsonl(session / "derived/audit/live-local-recall-target-me/live_local_recall_target_me_audit.jsonl")
    result: dict[tuple[str, int, float, float], dict[str, Any]] = {}
    for row in rows:
        interval = row.get("interval") if isinstance(row.get("interval"), dict) else {}
        key = segment_key(session.name, row.get("chunk_index"), interval.get("start"), interval.get("end"))
        result[key] = row
    return result


def persistent_index(root: Path, session: Path) -> dict[tuple[str, int, float, float], dict[str, Any]]:
    roots = [
        root / "sessions/_reports/live-pipeline/persistent_target_me_profile_lab.real/targets",
        root / "sessions/_reports/live-pipeline/persistent_target_me_profile_lab/targets",
    ]
    result: dict[tuple[str, int, float, float], dict[str, Any]] = {}
    for base in roots:
        path = base / session.name / "persistent_target_me_profile_lab_rows.json"
        rows = read_json(path)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = segment_key(session.name, row.get("chunk_index"), row.get("start"), row.get("end"))
            result[key] = row
    return result


def collect_segments(root: Path, sessions: list[Path], compare: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for session in sessions:
        chunks = compare.read_jsonl(session / "derived/live/chunks.jsonl")
        profile = compare.selected_profile(session)
        clean_dialogue = compare.selected_clean_dialogue_path(session, profile)
        batch_utterances = compare.read_utterances(clean_dialogue)
        audit = compare.read_suppressed_mic_asr_segment_audit(session, chunks, batch_utterances)
        segments = [row for row in (audit.get("segments") if isinstance(audit, dict) else []) or [] if isinstance(row, dict)]
        target_index = target_me_index(session)
        persistent_rows = persistent_index(root, session)
        for row in segments:
            item = dict(row)
            item["session"] = session.name
            item["session_path"] = rel(session, root)
            key = segment_key(session.name, item.get("chunk_index"), item.get("start"), item.get("end"))
            item["target_me"] = target_index.get(key)
            item["persistent_target_me"] = persistent_rows.get(key, {}).get("persistent_target_me")
            rows.append(item)
        summaries.append(
            {
                "session": session.name,
                "path": rel(session, root),
                "selected_profile": profile,
                "clean_dialogue": rel(clean_dialogue, root) if clean_dialogue else None,
                "suppressed_mic_segment_count": len(segments),
                "target_me_matched_count": sum(1 for row in segments if segment_key(session.name, row.get("chunk_index"), row.get("start"), row.get("end")) in target_index),
                "persistent_matched_count": sum(1 for row in segments if segment_key(session.name, row.get("chunk_index"), row.get("start"), row.get("end")) in persistent_rows),
            }
        )
    return rows, summaries


def duration(row: dict[str, Any]) -> float:
    return safe_float(row.get("duration_sec"), max(0.0, safe_float(row.get("end")) - safe_float(row.get("start"))))


def local_label(row: dict[str, Any]) -> bool:
    return row.get("batch_role_label") in LOCAL_LABELS


def remote_risk_label(row: dict[str, Any]) -> bool:
    return row.get("batch_role_label") in REMOTE_RISK_LABELS


def candidate_policies(row: dict[str, Any]) -> set[str]:
    return {str(item) for item in row.get("rescue_policy_candidates") or []}


def target_me_policies(row: dict[str, Any]) -> set[str]:
    target = row.get("target_me")
    if not isinstance(target, dict):
        return set()
    return {str(item) for item in target.get("target_me_rescue_policy_candidates") or []}


def persistent_policies(row: dict[str, Any]) -> set[str]:
    persistent = row.get("persistent_target_me")
    if not isinstance(persistent, dict):
        return set()
    return {str(item) for item in persistent.get("policy_candidates") or []}


def target_scores(row: dict[str, Any]) -> dict[str, Any]:
    target = row.get("target_me")
    if not isinstance(target, dict):
        return {}
    classification = target.get("classification") if isinstance(target.get("classification"), dict) else {}
    return classification.get("scores") if isinstance(classification.get("scores"), dict) else {}


def persistent_scores(row: dict[str, Any]) -> dict[str, Any]:
    persistent = row.get("persistent_target_me")
    if not isinstance(persistent, dict):
        return {}
    classification = persistent.get("classification") if isinstance(persistent.get("classification"), dict) else {}
    return classification.get("scores") if isinstance(classification.get("scores"), dict) else {}


def target_confirmed_remote_guard(row: dict[str, Any]) -> bool:
    return "target_me_confirmed_remote_guard_v1" in target_me_policies(row)


def persistent_confirmed_remote_guard(row: dict[str, Any]) -> bool:
    return "confirmed_remote_guard" in persistent_policies(row)


def audio_safe(row: dict[str, Any]) -> bool:
    return "audio_safe_union_v1" in candidate_policies(row)


def audio_mic_dominant(row: dict[str, Any]) -> bool:
    return "audio_mic_dominant_v1" in candidate_policies(row)


def remote_silent_text(row: dict[str, Any]) -> bool:
    return "remote_silent_text_v1" in candidate_policies(row)


def remote_forbidden_strict(row: dict[str, Any]) -> bool:
    return (
        safe_int(row.get("segment_gate_overlapping_remote_token_count")) == 0
        and safe_float(row.get("segment_gate_overlapping_remote_token_recall_in_mic")) <= 0.20
        and (
            safe_float(row.get("audio_remote_rms_db"), -120.0) <= -48.0
            or safe_float(row.get("audio_mic_minus_remote_rms_db"), -120.0) >= 8.0
        )
        and safe_float(row.get("audio_mic_remote_zero_lag_abs_corr"), 1.0) <= 0.12
    )


def remote_forbidden_balanced(row: dict[str, Any]) -> bool:
    return (
        safe_int(row.get("segment_gate_overlapping_remote_token_count")) <= 2
        and safe_float(row.get("segment_gate_overlapping_remote_token_recall_in_mic")) <= 0.35
        and safe_float(row.get("segment_gate_mic_token_recall_in_overlapping_remote")) <= 0.45
        and safe_float(row.get("audio_mic_remote_zero_lag_abs_corr"), 1.0) <= 0.08
    )


def remote_forbidden_speaker_delta(row: dict[str, Any]) -> bool:
    target = target_scores(row)
    persistent = persistent_scores(row)
    target_delta = safe_float(target.get("delta_vs_remote")) if target else 0.0
    persistent_delta = safe_float(persistent.get("delta_vs_remote")) if persistent else 0.0
    target_remote = safe_float(target.get("remote_target_similarity")) if target else 1.0
    persistent_remote = safe_float(persistent.get("remote_target_similarity")) if persistent else 1.0
    return (target_delta >= 0.20 and target_remote <= 0.62) or (persistent_delta >= 0.24 and persistent_remote <= 0.62)


def local_speaker_evidence(row: dict[str, Any]) -> bool:
    return target_confirmed_remote_guard(row) or persistent_confirmed_remote_guard(row)


PolicyFn = Callable[[dict[str, Any]], bool]


POLICIES: list[tuple[str, str, PolicyFn]] = [
    (
        "audio_safe_union_v1",
        "Existing safe audio/text union: remote_silent_text or audio_mic_dominant.",
        audio_safe,
    ),
    (
        "target_me_remote_guard_v1",
        "Existing session-local Target-Me confirmed remote guard.",
        target_confirmed_remote_guard,
    ),
    (
        "persistent_remote_guard_v1",
        "Historical persistent Target-Me confirmed remote guard.",
        persistent_confirmed_remote_guard,
    ),
    (
        "target_or_persistent_remote_guard_v1",
        "Either session-local or historical Target-Me confirmed remote guard.",
        lambda row: target_confirmed_remote_guard(row) or persistent_confirmed_remote_guard(row),
    ),
    (
        "target_me_and_audio_safe_v1",
        "Session-local Target-Me plus the existing audio-safe union.",
        lambda row: target_confirmed_remote_guard(row) and audio_safe(row),
    ),
    (
        "persistent_and_audio_safe_v1",
        "Historical Target-Me plus the existing audio-safe union.",
        lambda row: persistent_confirmed_remote_guard(row) and audio_safe(row),
    ),
    (
        "any_target_and_audio_safe_v1",
        "Any Target-Me source plus the existing audio-safe union.",
        lambda row: local_speaker_evidence(row) and audio_safe(row),
    ),
    (
        "dual_target_remote_guard_v1",
        "Both session-local and historical Target-Me agree under remote guard.",
        lambda row: target_confirmed_remote_guard(row) and persistent_confirmed_remote_guard(row),
    ),
    (
        "speaker_plus_strict_remote_forbidden_v1",
        "Any Target-Me source plus strict text/audio remote-forbidden evidence.",
        lambda row: local_speaker_evidence(row) and remote_forbidden_strict(row),
    ),
    (
        "speaker_plus_balanced_remote_forbidden_v1",
        "Any Target-Me source plus balanced text/correlation remote-forbidden evidence.",
        lambda row: local_speaker_evidence(row) and remote_forbidden_balanced(row),
    ),
    (
        "speaker_delta_plus_audio_safe_v1",
        "Target speaker delta-vs-remote evidence plus the existing audio-safe union.",
        lambda row: remote_forbidden_speaker_delta(row) and audio_safe(row),
    ),
    (
        "composite_conservative_v1",
        "Audio mic-dominant, or speaker evidence with strict remote-forbidden evidence.",
        lambda row: audio_mic_dominant(row) or (local_speaker_evidence(row) and remote_forbidden_strict(row)),
    ),
    (
        "composite_balanced_v1",
        "Audio-safe union, or speaker evidence with balanced remote-forbidden evidence.",
        lambda row: audio_safe(row) or (local_speaker_evidence(row) and remote_forbidden_balanced(row)),
    ),
    (
        "composite_dual_evidence_v1",
        "Requires at least two independent local/remote-forbidden signals.",
        lambda row: sum(
            [
                audio_safe(row),
                target_confirmed_remote_guard(row),
                persistent_confirmed_remote_guard(row),
                remote_forbidden_strict(row),
                remote_forbidden_speaker_delta(row),
            ]
        )
        >= 2,
    ),
    (
        "remote_silent_or_dual_target_v1",
        "Remote-silent text, or both Target-Me sources under remote guard.",
        lambda row: remote_silent_text(row)
        or (target_confirmed_remote_guard(row) and persistent_confirmed_remote_guard(row)),
    ),
]


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_label_count: Counter[str] = Counter()
    by_label_seconds: Counter[str] = Counter()
    for row in rows:
        label = str(row.get("batch_role_label") or "none")
        by_label_count[label] += 1
        by_label_seconds[label] += duration(row)
    return {
        "segment_count": len(rows),
        "segment_seconds": round(sum(by_label_seconds.values()), 3),
        "by_batch_role_count": dict(sorted(by_label_count.items())),
        "by_batch_role_seconds": {key: round(value, 3) for key, value in sorted(by_label_seconds.items())},
        "local_seconds": round(sum(by_label_seconds[label] for label in LOCAL_LABELS), 3),
        "remote_risk_seconds": round(sum(by_label_seconds[label] for label in REMOTE_RISK_LABELS), 3),
        "target_me_matched_count": sum(1 for row in rows if isinstance(row.get("target_me"), dict)),
        "persistent_target_me_matched_count": sum(1 for row in rows if isinstance(row.get("persistent_target_me"), dict)),
    }


def example_payload(row: dict[str, Any]) -> dict[str, Any]:
    target = row.get("target_me") if isinstance(row.get("target_me"), dict) else {}
    persistent = row.get("persistent_target_me") if isinstance(row.get("persistent_target_me"), dict) else {}
    target_classification = target.get("classification") if isinstance(target.get("classification"), dict) else {}
    persistent_classification = (
        persistent.get("classification") if isinstance(persistent.get("classification"), dict) else {}
    )
    return {
        "session": row.get("session"),
        "chunk_index": row.get("chunk_index"),
        "start": row.get("start"),
        "end": row.get("end"),
        "duration_sec": row.get("duration_sec"),
        "batch_role_label": row.get("batch_role_label"),
        "text": row.get("text"),
        "rescue_policy_candidates": row.get("rescue_policy_candidates") or [],
        "target_me_policy_candidates": list(sorted(target_me_policies(row))),
        "persistent_policy_candidates": list(sorted(persistent_policies(row))),
        "target_me_label": target_classification.get("label"),
        "target_me_confidence": target_classification.get("confidence"),
        "persistent_label": persistent_classification.get("label"),
        "persistent_confidence": persistent_classification.get("confidence"),
        "remote_token_count": row.get("segment_gate_overlapping_remote_token_count"),
        "remote_recall_in_mic": row.get("segment_gate_overlapping_remote_token_recall_in_mic"),
        "mic_recall_in_remote": row.get("segment_gate_mic_token_recall_in_overlapping_remote"),
        "audio_mic_clean_rms_db": row.get("audio_mic_clean_rms_db"),
        "audio_remote_rms_db": row.get("audio_remote_rms_db"),
        "audio_mic_minus_remote_rms_db": row.get("audio_mic_minus_remote_rms_db"),
        "audio_mic_remote_zero_lag_abs_corr": row.get("audio_mic_remote_zero_lag_abs_corr"),
    }


def evaluate_policy(policy_id: str, description: str, predicate: PolicyFn, rows: list[dict[str, Any]], max_examples: int) -> dict[str, Any]:
    selected = [row for row in rows if predicate(row)]
    total_local = sum(duration(row) for row in rows if local_label(row))
    selected_seconds = sum(duration(row) for row in selected)
    local_seconds = sum(duration(row) for row in selected if local_label(row))
    remote_seconds = sum(duration(row) for row in selected if remote_risk_label(row))
    precision = local_seconds / selected_seconds if selected_seconds else None
    recall = local_seconds / total_local if total_local else None
    payload = {
        "policy": policy_id,
        "description": description,
        "selected_segment_count": len(selected),
        "selected_seconds": round(selected_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_seconds, 3),
        "precision_proxy": round_float(precision),
        "local_recall_proxy": round_float(recall),
        "selected_by_batch_role_seconds": selected_by_label_seconds(selected),
    }
    if max_examples > 0:
        payload["local_examples"] = [example_payload(row) for row in selected if local_label(row)][:max_examples]
        payload["remote_risk_examples"] = [example_payload(row) for row in selected if remote_risk_label(row)][:max_examples]
    return payload


def selected_by_label_seconds(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: Counter[str] = Counter()
    for row in rows:
        values[str(row.get("batch_role_label") or "none")] += duration(row)
    return {key: round(value, 3) for key, value in sorted(values.items())}


def conclusion(policy_rows: list[dict[str, Any]], rows_summary: dict[str, Any]) -> dict[str, Any]:
    useful = [row for row in policy_rows if safe_float(row.get("selected_seconds")) > 0]
    zero_risk = [row for row in useful if safe_float(row.get("remote_risk_seconds")) == 0.0]
    low_risk = [row for row in useful if safe_float(row.get("remote_risk_seconds")) <= 3.0]
    best_zero = max(zero_risk, key=lambda row: safe_float(row.get("local_seconds")), default={})
    best_low = max(low_risk, key=lambda row: safe_float(row.get("local_seconds")), default={})
    best_local = max(useful, key=lambda row: safe_float(row.get("local_seconds")), default={})
    total_local = safe_float(rows_summary.get("local_seconds"))
    zero_local = safe_float(best_zero.get("local_seconds"))
    low_local = safe_float(best_low.get("local_seconds"))
    if total_local <= 0:
        status = "no_local_labels"
        next_step = "collect comparable live sessions with suppressed mic local labels"
    elif zero_local / total_local >= 0.30:
        status = "promising_zero_risk_composite_gate"
        next_step = "materialize the best zero-risk composite gate as a shadow profile and run parity gates"
    elif low_local / total_local >= 0.30:
        status = "promising_low_risk_composite_gate"
        next_step = "inspect low-risk examples and add a stricter remote-forbidden guard before materialization"
    elif zero_local > 0:
        status = "limited_safe_composite_gate"
        next_step = "use composite evidence as a small shadow rescue and design stronger local-speaker validation"
    else:
        status = "composite_gate_not_enough"
        next_step = "do not promote composite gates; improve remote-forbidden/local-speaker evidence first"
    return {
        "status": status,
        "total_local_seconds": round(total_local, 3),
        "best_zero_remote_risk_policy": best_zero.get("policy"),
        "best_zero_remote_risk_local_seconds": round(zero_local, 3),
        "best_remote_risk_lte_3s_policy": best_low.get("policy"),
        "best_remote_risk_lte_3s_local_seconds": round(low_local, 3),
        "best_local_policy": best_local.get("policy"),
        "best_local_policy_local_seconds": round(safe_float(best_local.get("local_seconds")), 3),
        "best_local_policy_remote_risk_seconds": round(safe_float(best_local.get("remote_risk_seconds")), 3),
        "recommended_next": next_step,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]["rows"]
    conclusion_payload = payload["conclusion"]
    lines = [
        "# Suppressed Mic Composite Gate Lab",
        "",
        f"Generated: `{payload['created_at']}`",
        "",
        "## Scope",
        "",
        f"- scope: `{payload['scope']}`",
        f"- sessions: `{payload['summary']['session_count']}`",
        f"- suppressed mic ASR segments: `{summary['segment_count']}` / `{summary['segment_seconds']}` sec",
        f"- local/mixed seconds by batch labels: `{summary['local_seconds']}`",
        f"- remote-risk seconds by batch labels: `{summary['remote_risk_seconds']}`",
        f"- Target-Me matched segments: `{summary['target_me_matched_count']}`",
        f"- persistent Target-Me matched segments: `{summary['persistent_target_me_matched_count']}`",
        "",
        "## Conclusion",
        "",
        f"- status: `{conclusion_payload['status']}`",
        f"- best zero-risk policy: `{conclusion_payload['best_zero_remote_risk_policy']}` "
        f"({conclusion_payload['best_zero_remote_risk_local_seconds']} local sec)",
        f"- best <=3s-risk policy: `{conclusion_payload['best_remote_risk_lte_3s_policy']}` "
        f"({conclusion_payload['best_remote_risk_lte_3s_local_seconds']} local sec)",
        f"- recommended next: {conclusion_payload['recommended_next']}",
        "",
        "## Policies",
        "",
        "| Policy | Local sec | Remote-risk sec | Precision | Recall | Selected sec |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in payload["policies"]:
        lines.append(
            f"| `{row['policy']}` | {row['local_seconds']} | {row['remote_risk_seconds']} "
            f"| {row['precision_proxy']} | {row['local_recall_proxy']} | {row['selected_seconds']} |"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    compare = load_compare_module(root)
    sessions = discover_sessions(root, args.scope, args.sessions)
    rows, session_summaries = collect_segments(root, sessions, compare)
    rows_summary = summarize_rows(rows)
    policy_rows = [
        evaluate_policy(policy_id, description, predicate, rows, args.max_examples)
        for policy_id, description, predicate in POLICIES
    ]
    policy_rows.sort(
        key=lambda row: (
            safe_float(row.get("remote_risk_seconds")) > 0,
            safe_float(row.get("remote_risk_seconds")),
            -safe_float(row.get("local_seconds")),
        )
    )
    payload = {
        "schema": SCHEMA,
        "generator": {
            "script": "scripts/report-suppressed-mic-composite-gate-lab.py",
            "version": SCRIPT_VERSION,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope if not args.sessions else "explicit",
        "input_sessions": session_summaries,
        "summary": {
            "session_count": len(session_summaries),
            "rows": rows_summary,
        },
        "conclusion": conclusion(policy_rows, rows_summary),
        "policies": policy_rows,
    }
    out = args.out if args.out.is_absolute() else root / args.out
    write_json(out, payload)
    write_markdown(out.with_suffix(".md"), markdown_report(payload))
    print(f"written: {rel(out, root)}")
    print(f"written: {rel(out.with_suffix('.md'), root)}")
    print(
        "summary: status={status} sessions={sessions} best_zero={best_zero} local={local}s remote_risk={risk}s".format(
            status=payload["conclusion"]["status"],
            sessions=len(session_summaries),
            best_zero=payload["conclusion"]["best_zero_remote_risk_policy"],
            local=payload["conclusion"]["best_zero_remote_risk_local_seconds"],
            risk=rows_summary.get("remote_risk_seconds"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
