#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
from collections import Counter
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Callable


SCHEMA = "murmurmark.suppressed_mic_policy_lab/v1"
SCRIPT_VERSION = "0.1.0"
LOCAL_LABELS = {"me_dominant", "mixed"}
REMOTE_RISK_LABELS = {"remote_dominant", "none"}
DEFAULT_REPORT = Path("sessions/_reports/live-pipeline/suppressed_mic_policy_lab.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Search live-accessible suppressed-mic rescue policies against the current "
            "live/batch corpus. The batch transcript is used only as lab labels."
        )
    )
    parser.add_argument("sessions", nargs="*", type=Path)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument(
        "--scope",
        choices=("capture-safe-candidate", "real", "all-live"),
        default="capture-safe-candidate",
        help="Session scope to evaluate when sessions are not passed explicitly.",
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
        return float(value)
    except (TypeError, ValueError):
        return default


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
    candidates = []
    for chunks in sorted((root / "sessions").glob("*/derived/live/chunks.jsonl")):
        session = chunks.parents[2]
        if session.name.startswith("_"):
            continue
        if known_ids and session.name not in known_ids:
            continue
        candidates.append(session)
    return candidates


def collect_segments(root: Path, sessions: list[Path], compare: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    session_summaries: list[dict[str, Any]] = []
    for session in sessions:
        chunks_path = session / "derived/live/chunks.jsonl"
        chunks = compare.read_jsonl(chunks_path)
        profile = compare.selected_profile(session)
        clean_dialogue = compare.selected_clean_dialogue_path(session, profile)
        batch_utterances = compare.read_utterances(clean_dialogue)
        audit = compare.read_suppressed_mic_asr_segment_audit(session, chunks, batch_utterances)
        segments = audit.get("segments") if isinstance(audit, dict) else []
        usable_segments = [row for row in segments if isinstance(row, dict)]
        for row in usable_segments:
            item = dict(row)
            item["session"] = session.name
            item["session_path"] = rel(session, root)
            rows.append(item)
        session_summaries.append(
            {
                "session": session.name,
                "path": rel(session, root),
                "selected_profile": profile,
                "clean_dialogue": rel(clean_dialogue, root) if clean_dialogue else None,
                "chunk_count": len(chunks),
                "suppressed_mic_segment_count": len(usable_segments),
                "suppressed_mic_segment_seconds": round(
                    sum(safe_float(row.get("duration_sec")) for row in usable_segments),
                    3,
                ),
            }
        )
    return rows, session_summaries


def value(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    return safe_float(row.get(key), default)


def int_value(row: dict[str, Any], key: str, default: int = 0) -> int:
    return safe_int(row.get(key), default)


Rule = dict[str, Any]


def matches_rule(row: dict[str, Any], rule: Rule) -> bool:
    token_count = int_value(row, "token_count")
    unique_count = int_value(row, "segment_gate_unique_token_count")
    remote_token_count = int_value(row, "segment_gate_overlapping_remote_token_count")
    mic_in_remote = value(row, "segment_gate_mic_token_recall_in_overlapping_remote")
    remote_in_mic = value(row, "segment_gate_overlapping_remote_token_recall_in_mic")
    mic_db = value(row, "audio_mic_clean_rms_db", -120.0)
    remote_db = value(row, "audio_remote_rms_db", -120.0)
    mic_minus_remote = value(row, "audio_mic_minus_remote_rms_db", -120.0)
    corr = value(row, "audio_mic_remote_zero_lag_abs_corr", 1.0)
    if token_count < rule.get("token_min", 0):
        return False
    if unique_count < rule.get("unique_min", 0):
        return False
    if mic_in_remote > rule.get("mic_in_remote_max", 1.0):
        return False
    if remote_in_mic > rule.get("remote_in_mic_max", 1.0):
        return False
    if corr > rule.get("corr_max", 1.0):
        return False
    if mic_db < rule.get("mic_db_min", -120.0):
        return False
    remote_db_max = rule.get("remote_db_max")
    if remote_db_max is not None and remote_db > remote_db_max:
        return False
    mic_minus_remote_min = rule.get("mic_minus_remote_min")
    if mic_minus_remote_min is not None and mic_minus_remote < mic_minus_remote_min:
        return False
    remote_token_max = rule.get("remote_token_count_max")
    if remote_token_max is not None and remote_token_count > remote_token_max:
        return False
    return True


def generated_rules() -> list[Rule]:
    rules: list[Rule] = []

    def add_family(family: str, keys: list[str], grid: list[tuple[Any, ...]]) -> None:
        for index, values in enumerate(grid, start=1):
            rule = {"id": f"{family}_{index:04d}", "family": family}
            rule.update(dict(zip(keys, values)))
            rules.append(rule)

    add_family(
        "text_unique",
        ["token_min", "unique_min", "remote_in_mic_max", "mic_in_remote_max", "remote_token_count_max"],
        list(
            product(
                [2, 3, 4, 5],
                [1, 2, 3, 4],
                [0.20, 0.35, 0.45, 0.60],
                [0.35, 0.55, 0.75, 1.01],
                [None, 0, 2, 5],
            )
        ),
    )
    add_family(
        "audio_low_corr",
        ["token_min", "unique_min", "corr_max", "remote_in_mic_max", "mic_db_min"],
        list(product([2, 3, 4, 5], [0, 1, 2], [0.03, 0.05, 0.08, 0.12, 0.20], [0.25, 0.35, 0.45, 0.60], [-62, -58, -56, -52])),
    )
    add_family(
        "remote_quiet",
        ["token_min", "unique_min", "remote_db_max", "mic_db_min", "remote_token_count_max"],
        list(product([2, 3, 4], [0, 1, 2], [-55, -50, -45, -40], [-64, -60, -58, -56], [None, 0, 2, 5])),
    )
    add_family(
        "mic_dominant",
        ["token_min", "unique_min", "mic_minus_remote_min", "mic_db_min", "remote_in_mic_max"],
        list(product([2, 3, 4], [0, 1, 2], [-10, 0, 4, 8, 12], [-60, -56, -52], [0.35, 0.55, 0.75])),
    )
    add_family(
        "hybrid_safe",
        [
            "token_min",
            "unique_min",
            "corr_max",
            "remote_in_mic_max",
            "mic_in_remote_max",
            "mic_db_min",
            "mic_minus_remote_min",
        ],
        list(
            product(
                [2, 3, 4],
                [1, 2, 3],
                [0.05, 0.08, 0.12, 0.20],
                [0.25, 0.35, 0.45],
                [0.55, 0.75, 1.01],
                [-60, -58, -56],
                [None, -10, 0, 4, 8],
            )
        ),
    )
    return rules


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    seconds_by_label: Counter[str] = Counter()
    count_by_label: Counter[str] = Counter()
    seconds_by_session: Counter[str] = Counter()
    for row in rows:
        label = str(row.get("batch_role_label") or "none")
        duration = value(row, "duration_sec")
        seconds_by_label[label] += duration
        count_by_label[label] += 1
        seconds_by_session[str(row.get("session") or "unknown")] += duration
    return {
        "segment_count": len(rows),
        "segment_seconds": round(sum(seconds_by_label.values()), 3),
        "by_batch_role_count": dict(sorted(count_by_label.items())),
        "by_batch_role_seconds": {key: round(val, 3) for key, val in sorted(seconds_by_label.items())},
        "local_seconds": round(sum(seconds_by_label[label] for label in LOCAL_LABELS), 3),
        "remote_risk_seconds": round(sum(seconds_by_label[label] for label in REMOTE_RISK_LABELS), 3),
        "by_session_seconds": {key: round(val, 3) for key, val in sorted(seconds_by_session.items())},
    }


def example_payload(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "session",
        "chunk_index",
        "start",
        "end",
        "duration_sec",
        "text",
        "batch_role_label",
        "segment_gate_unique_token_count",
        "segment_gate_mic_token_recall_in_overlapping_remote",
        "segment_gate_overlapping_remote_token_recall_in_mic",
        "segment_gate_overlapping_remote_token_count",
        "audio_mic_clean_rms_db",
        "audio_remote_rms_db",
        "audio_mic_minus_remote_rms_db",
        "audio_mic_remote_zero_lag_abs_corr",
    )
    return {key: row.get(key) for key in keys if key in row}


def evaluate_rule(rule: Rule, rows: list[dict[str, Any]], max_examples: int) -> dict[str, Any]:
    selected = [row for row in rows if matches_rule(row, rule)]
    seconds_by_label: Counter[str] = Counter()
    selected_by_session: Counter[str] = Counter()
    for row in selected:
        duration = value(row, "duration_sec")
        seconds_by_label[str(row.get("batch_role_label") or "none")] += duration
        selected_by_session[str(row.get("session") or "unknown")] += 1
    selected_seconds = sum(seconds_by_label.values())
    local_seconds = sum(seconds_by_label[label] for label in LOCAL_LABELS)
    remote_risk_seconds = sum(seconds_by_label[label] for label in REMOTE_RISK_LABELS)
    precision = local_seconds / selected_seconds if selected_seconds > 0 else None
    total_local = sum(value(row, "duration_sec") for row in rows if row.get("batch_role_label") in LOCAL_LABELS)
    recall = local_seconds / total_local if total_local > 0 else None
    f05 = None
    if precision is not None and recall is not None and precision > 0 and recall > 0:
        beta2 = 0.25
        f05 = (1 + beta2) * precision * recall / (beta2 * precision + recall)
    remote_examples = [example_payload(row) for row in selected if row.get("batch_role_label") in REMOTE_RISK_LABELS]
    local_examples = [example_payload(row) for row in selected if row.get("batch_role_label") in LOCAL_LABELS]
    payload = {
        "_selection_signature": [
            f"{row.get('session')}:{row.get('start')}:{row.get('end')}:{row.get('text')}" for row in selected
        ],
        "rule": rule,
        "selected_segment_count": len(selected),
        "selected_seconds": round(selected_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_risk_seconds, 3),
        "precision_proxy": round_float(precision),
        "local_recall_proxy": round_float(recall),
        "f0_5_proxy": round_float(f05),
        "by_batch_role_seconds": {key: round(val, 3) for key, val in sorted(seconds_by_label.items())},
        "selected_by_session_count": dict(sorted(selected_by_session.items())),
    }
    if max_examples > 0:
        payload["local_examples"] = local_examples[:max_examples]
        payload["remote_risk_examples"] = remote_examples[:max_examples]
    return payload


def evaluate_named_policies(rows: list[dict[str, Any]], max_examples: int) -> list[dict[str, Any]]:
    policies = sorted({label for row in rows for label in (row.get("rescue_policy_candidates") or [])})
    result = []
    for policy in policies:
        selected = [row for row in rows if policy in (row.get("rescue_policy_candidates") or [])]
        result.append(evaluate_named_policy(policy, selected, rows, max_examples))
    return sorted(result, key=lambda row: (-safe_float(row.get("local_seconds")), safe_float(row.get("remote_risk_seconds"))))


def evaluate_named_policy(policy: str, selected: list[dict[str, Any]], all_rows: list[dict[str, Any]], max_examples: int) -> dict[str, Any]:
    seconds_by_label: Counter[str] = Counter()
    for row in selected:
        seconds_by_label[str(row.get("batch_role_label") or "none")] += value(row, "duration_sec")
    selected_seconds = sum(seconds_by_label.values())
    local_seconds = sum(seconds_by_label[label] for label in LOCAL_LABELS)
    remote_risk_seconds = sum(seconds_by_label[label] for label in REMOTE_RISK_LABELS)
    precision = local_seconds / selected_seconds if selected_seconds > 0 else None
    total_local = sum(value(row, "duration_sec") for row in all_rows if row.get("batch_role_label") in LOCAL_LABELS)
    recall = local_seconds / total_local if total_local > 0 else None
    payload = {
        "policy": policy,
        "selected_segment_count": len(selected),
        "selected_seconds": round(selected_seconds, 3),
        "local_seconds": round(local_seconds, 3),
        "remote_risk_seconds": round(remote_risk_seconds, 3),
        "precision_proxy": round_float(precision),
        "local_recall_proxy": round_float(recall),
        "by_batch_role_seconds": {key: round(val, 3) for key, val in sorted(seconds_by_label.items())},
    }
    if max_examples > 0:
        payload["remote_risk_examples"] = [
            example_payload(row) for row in selected if row.get("batch_role_label") in REMOTE_RISK_LABELS
        ][:max_examples]
        payload["local_examples"] = [
            example_payload(row) for row in selected if row.get("batch_role_label") in LOCAL_LABELS
        ][:max_examples]
    return payload


def prune_candidates(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    useful = [row for row in candidates if safe_float(row.get("selected_seconds")) > 0]
    zero_risk = [row for row in useful if safe_float(row.get("remote_risk_seconds")) == 0.0]
    low_risk_3 = [row for row in useful if safe_float(row.get("remote_risk_seconds")) <= 3.0]
    low_risk_10 = [row for row in useful if safe_float(row.get("remote_risk_seconds")) <= 10.0]
    high_precision = [row for row in useful if safe_float(row.get("precision_proxy")) >= 0.90]
    frontier = pareto_frontier(useful)
    return {
        "evaluated_rule_count": len(candidates),
        "useful_rule_count": len(useful),
        "best_zero_remote_risk": top_rules(zero_risk, lambda row: (safe_float(row.get("local_seconds")), -safe_float(row.get("selected_segment_count"))), 10),
        "best_remote_risk_lte_3s": top_rules(low_risk_3, lambda row: (safe_float(row.get("local_seconds")), safe_float(row.get("precision_proxy"))), 10),
        "best_remote_risk_lte_10s": top_rules(low_risk_10, lambda row: (safe_float(row.get("local_seconds")), safe_float(row.get("precision_proxy"))), 10),
        "best_precision_gte_90": top_rules(high_precision, lambda row: (safe_float(row.get("local_seconds")), -safe_float(row.get("remote_risk_seconds"))), 10),
        "best_f0_5": top_rules(useful, lambda row: (safe_float(row.get("f0_5_proxy")), -safe_float(row.get("remote_risk_seconds"))), 10),
        "pareto_frontier": [strip_private(row) for row in frontier[:50]],
    }


def top_rules(rows: list[dict[str, Any]], key: Callable[[dict[str, Any]], tuple[float, float]], limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for row in sorted(rows, key=key, reverse=True):
        signature = tuple(row.get("_selection_signature") or [])
        if signature in seen:
            continue
        seen.add(signature)
        result.append(strip_private(row))
        if len(result) >= limit:
            break
    return result


def strip_private(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    for key in list(payload):
        if key.startswith("_"):
            payload.pop(key, None)
    return payload


def pareto_frontier(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = sorted(
        rows,
        key=lambda row: (
            safe_float(row.get("remote_risk_seconds")),
            -safe_float(row.get("local_seconds")),
            -safe_float(row.get("precision_proxy")),
        ),
    )
    frontier: list[dict[str, Any]] = []
    best_local = -1.0
    best_precision = -1.0
    for row in compact:
        local = safe_float(row.get("local_seconds"))
        precision = safe_float(row.get("precision_proxy"))
        if local > best_local or precision > best_precision:
            frontier.append(row)
            best_local = max(best_local, local)
            best_precision = max(best_precision, precision)
    return frontier


def conclusion(pruned: dict[str, Any], rows_summary: dict[str, Any]) -> dict[str, Any]:
    best_zero = (pruned.get("best_zero_remote_risk") or [{}])[0]
    best_lte_3 = (pruned.get("best_remote_risk_lte_3s") or [{}])[0]
    best_f05 = (pruned.get("best_f0_5") or [{}])[0]
    total_local = safe_float(rows_summary.get("local_seconds"))
    zero_local = safe_float(best_zero.get("local_seconds"))
    lte_3_local = safe_float(best_lte_3.get("local_seconds"))
    f05_risk = safe_float(best_f05.get("remote_risk_seconds"))
    if total_local <= 0:
        status = "no_local_labels"
        next_step = "collect comparable live sessions with suppressed mic local labels"
    elif zero_local / total_local >= 0.5:
        status = "promising_zero_risk_threshold_region"
        next_step = "materialize the best zero-risk rule as a shadow profile and run parity gates"
    elif lte_3_local / total_local >= 0.5:
        status = "promising_low_risk_threshold_region"
        next_step = "inspect low-risk examples, then add stronger remote-forbidden gates before materialization"
    elif f05_risk > 10.0:
        status = "thresholds_not_enough"
        next_step = "add stronger local-speaker evidence or a local judge; simple audio/text thresholds leak remote"
    else:
        status = "limited_safe_recovery"
        next_step = "use the safe rule only as a small shadow rescue and continue with local-speaker evidence"
    return {
        "status": status,
        "total_local_seconds": round(total_local, 3),
        "best_zero_remote_risk_local_seconds": round(zero_local, 3),
        "best_remote_risk_lte_3s_local_seconds": round(lte_3_local, 3),
        "best_f0_5_remote_risk_seconds": round(f05_risk, 3),
        "recommended_next": next_step,
    }


def markdown_report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    conclusion_payload = payload["conclusion"]
    lines = [
        "# Suppressed Mic Policy Lab",
        "",
        f"Generated: `{payload['created_at']}`",
        "",
        "## Scope",
        "",
        f"- scope: `{payload['scope']}`",
        f"- sessions: `{summary['session_count']}`",
        f"- suppressed mic ASR segments: `{summary['rows']['segment_count']}`",
        f"- local seconds by batch labels: `{summary['rows']['local_seconds']}`",
        f"- remote-risk seconds by batch labels: `{summary['rows']['remote_risk_seconds']}`",
        "",
        "## Conclusion",
        "",
        f"- status: `{conclusion_payload['status']}`",
        f"- best zero-risk local seconds: `{conclusion_payload['best_zero_remote_risk_local_seconds']}`",
        f"- best <=3s-risk local seconds: `{conclusion_payload['best_remote_risk_lte_3s_local_seconds']}`",
        f"- recommended next: {conclusion_payload['recommended_next']}",
        "",
        "## Current Named Policies",
        "",
    ]
    for policy in payload["named_policy_summary"][:12]:
        lines.append(
            "- `{policy}`: local `{local}` sec, remote-risk `{risk}` sec, precision `{precision}`, recall `{recall}`".format(
                policy=policy.get("policy"),
                local=policy.get("local_seconds"),
                risk=policy.get("remote_risk_seconds"),
                precision=policy.get("precision_proxy"),
                recall=policy.get("local_recall_proxy"),
            )
        )
    lines += [
        "",
        "## Best Zero Remote-Risk Rules",
        "",
    ]
    for row in payload["generated_policy_search"]["best_zero_remote_risk"][:10]:
        lines.append(format_rule_line(row))
    lines += [
        "",
        "## Best <=3s Remote-Risk Rules",
        "",
    ]
    for row in payload["generated_policy_search"]["best_remote_risk_lte_3s"][:10]:
        lines.append(format_rule_line(row))
    lines += [
        "",
        "## Best F0.5 Rules",
        "",
    ]
    for row in payload["generated_policy_search"]["best_f0_5"][:10]:
        lines.append(format_rule_line(row))
    lines.append("")
    return "\n".join(lines)


def format_rule_line(row: dict[str, Any]) -> str:
    rule = row.get("rule") if isinstance(row.get("rule"), dict) else {}
    return (
        "- `{id}` ({family}): local `{local}` sec, remote-risk `{risk}` sec, "
        "precision `{precision}`, recall `{recall}`, selected `{selected}` sec"
    ).format(
        id=rule.get("id"),
        family=rule.get("family"),
        local=row.get("local_seconds"),
        risk=row.get("remote_risk_seconds"),
        precision=row.get("precision_proxy"),
        recall=row.get("local_recall_proxy"),
        selected=row.get("selected_seconds"),
    )


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    compare = load_compare_module(root)
    sessions = discover_sessions(root, args.scope, args.sessions)
    rows, session_summaries = collect_segments(root, sessions, compare)
    rows_summary = summarize_rows(rows)
    rules = generated_rules()
    candidates = [evaluate_rule(rule, rows, args.max_examples) for rule in rules]
    pruned = prune_candidates(candidates)
    named_policy_summary = evaluate_named_policies(rows, args.max_examples)
    payload = {
        "schema": SCHEMA,
        "generator": {
            "script": "scripts/report-suppressed-mic-policy-lab.py",
            "version": SCRIPT_VERSION,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": args.scope if not args.sessions else "explicit",
        "input_sessions": session_summaries,
        "summary": {
            "session_count": len(session_summaries),
            "rows": rows_summary,
        },
        "conclusion": conclusion(pruned, rows_summary),
        "named_policy_summary": named_policy_summary,
        "generated_policy_search": pruned,
    }
    out = args.out if args.out.is_absolute() else root / args.out
    write_json(out, payload)
    write_markdown(out.with_suffix(".md"), markdown_report(payload))
    print(f"written: {rel(out, root)}")
    print(f"written: {rel(out.with_suffix('.md'), root)}")
    print(
        "summary: status={status} sessions={sessions} local={local}s remote_risk={risk}s".format(
            status=payload["conclusion"]["status"],
            sessions=len(session_summaries),
            local=rows_summary.get("local_seconds"),
            risk=rows_summary.get("remote_risk_seconds"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
