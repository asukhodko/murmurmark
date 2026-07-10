#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "murmurmark.live_session_evidence/v1"
TERMINAL_WORKER_STATUSES = {
    "completed",
    "completed_partial_draft",
    "disabled_backpressure",
    "disabled_pcm_copy",
    "failed",
    "stopped_by_limit",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report whether one live-shadow session is valid parity evidence.")
    parser.add_argument("session", type=Path)
    parser.add_argument("--refresh", action="store_true", help="Refresh live-vs-batch comparison before reporting.")
    parser.add_argument("--strict", action="store_true", help="Exit 2 unless all parity gates pass.")
    parser.add_argument("--max-final-lag-sec", type=float, default=60.0)
    parser.add_argument("--max-first-chunk-latency-sec", type=float, default=120.0)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def optional_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def gate_map(comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    parity = comparison.get("parity_gates") if isinstance(comparison.get("parity_gates"), dict) else {}
    gates = parity.get("gates") if isinstance(parity.get("gates"), list) else []
    return {
        str(row.get("name")): row
        for row in gates
        if isinstance(row, dict) and row.get("name")
    }


def check(check_id: str, passed: bool, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "passed" if passed else "failed",
        "reason": reason,
        "evidence": evidence,
    }


def refresh_comparison(session: Path) -> int:
    script = Path(__file__).with_name("compare-live-batch.py")
    return subprocess.run([sys.executable, str(script), str(session)], check=False).returncode


def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Live Session Evidence",
        "",
        f"- Status: `{payload['status']}`",
        f"- Transport evidence: `{str(payload['transport_evidence_passed']).lower()}`",
        f"- Meaningful comparison: `{str(payload['meaningful_comparison']).lower()}`",
        f"- All parity gates passed: `{str(payload['all_parity_gates_passed']).lower()}`",
        f"- Batch authoritative: `{str(payload['batch_authoritative']).lower()}`",
        f"- Promotion allowed: `false`",
        "",
        "## Checks",
        "",
    ]
    for row in payload["checks"]:
        lines.append(f"- `{row['status']}` `{row['id']}`: {row['reason']}")
    lines.extend(
        [
            "",
            "## Remaining Parity Gates",
            "",
        ]
    )
    remaining = payload.get("remaining_parity_gates") or []
    if remaining:
        for row in remaining:
            lines.append(f"- `{row.get('status')}` `{row.get('name')}`: {row.get('reason')}")
    else:
        lines.append("- None.")
    lines.extend(["", f"Next: `{payload['recommended_next']}`", ""])
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    if args.refresh:
        refresh_status = refresh_comparison(session)
        if refresh_status != 0:
            print(f"live evidence: comparison refresh failed with {refresh_status}", file=sys.stderr)
            return refresh_status

    live_dir = session / "derived/live"
    comparison_path = live_dir / "live_batch_comparison.json"
    comparison = read_json(comparison_path)
    session_manifest = read_json(session / "session.json")
    worker_state = read_json(live_dir / "live_pipeline_state.json")
    worker_report = read_json(live_dir / "live_pipeline_report.json")
    chunks = read_jsonl(live_dir / "chunks.jsonl")
    segments = read_jsonl(live_dir / "segments.jsonl")
    temporal = comparison.get("temporal_provenance") if isinstance(comparison.get("temporal_provenance"), dict) else {}
    metrics = comparison.get("metrics") if isinstance(comparison.get("metrics"), dict) else {}
    gates = gate_map(comparison)

    capture_gate = gates.get("capture_safety") or {}
    artifact_gate = gates.get("required_artifacts") or {}
    authoritative_gate = gates.get("raw_batch_authoritative") or {}
    pre_stop_gate = gates.get("pre_stop_live_artifacts") or {}
    capture_ok = capture_gate.get("status") == "passed"
    artifacts_ok = artifact_gate.get("status") == "passed"
    batch_authoritative = authoritative_gate.get("status") == "passed" and bool(
        metrics.get("batch_authoritative", comparison.get("batch_authoritative", True))
    )
    pre_stop_chunks = int(temporal.get("live_pre_stop_chunk_count") or 0)
    pre_stop_ok = pre_stop_gate.get("status") == "passed" and pre_stop_chunks > 0
    meaningful = bool(metrics.get("meaningful_live_comparison"))
    all_parity_passed = bool(metrics.get("all_parity_gates_passed"))

    worker_status = str(worker_state.get("status") or worker_report.get("status") or "missing")
    progress = worker_report.get("progress") if isinstance(worker_report.get("progress"), dict) else {}
    report_final_lag = optional_float(progress.get("live_lag_sec"))
    captured_from_rows = max((optional_float(row.get("end_sec")) or 0.0 for row in segments), default=0.0)
    processed_from_rows = max((optional_float(row.get("end_sec")) or 0.0 for row in chunks), default=0.0)
    row_final_lag = max(0.0, captured_from_rows - processed_from_rows) if captured_from_rows > 0 else None
    final_lag = row_final_lag if row_final_lag is not None else report_final_lag
    termination_reason = str(
        worker_state.get("termination_reason") or worker_report.get("termination_reason") or ""
    )
    effective_worker_status = (
        "completed_partial_draft"
        if termination_reason and final_lag is not None and final_lag > 0.5
        else worker_status
    )
    worker_terminal = effective_worker_status in TERMINAL_WORKER_STATUSES
    final_lag_ok = final_lag is not None and final_lag <= max(0.0, args.max_final_lag_sec)
    first_chunk_latency = optional_float(temporal.get("first_live_chunk_latency_sec"))
    latency_ok = first_chunk_latency is not None and first_chunk_latency <= max(0.0, args.max_first_chunk_latency_sec)
    draft_exists = (live_dir / "transcript.draft.md").exists()

    realtime_rows = chunks + segments
    provenance_values = {str(row.get("provenance") or "") for row in realtime_rows}
    provenance_ok = bool(realtime_rows) and provenance_values == {"recording_time_committed_pcm"}
    fallback_contamination = any(
        "fallback/" in str(row.get("path") or "")
        or str(row.get("provenance") or "") == "post_stop_raw_commit_recovery"
        for row in realtime_rows
    )

    checks = [
        check("capture_safety", capture_ok, "raw capture must be complete", {"gate": capture_gate}),
        check("required_artifacts", artifacts_ok, "live and batch artifacts must exist", {"gate": artifact_gate}),
        check("batch_authoritative", batch_authoritative, "batch output remains source of truth", {"gate": authoritative_gate}),
        check(
            "pre_stop_live_artifacts",
            pre_stop_ok,
            "at least one timestamped live chunk must be produced before stop",
            {"gate": pre_stop_gate, "temporal": temporal},
        ),
        check(
            "worker_terminal",
            worker_terminal,
            "worker must not remain stale running",
            {
                "worker_status": worker_status,
                "effective_worker_status": effective_worker_status,
                "current_stage": worker_state.get("current_stage"),
                "termination_reason": termination_reason or None,
            },
        ),
        check(
            "bounded_final_lag",
            final_lag_ok,
            f"final live lag must be <= {args.max_final_lag_sec:.1f}s",
            {
                "final_lag_sec": final_lag,
                "report_final_lag_sec": report_final_lag,
                "captured_from_segment_rows_sec": round(captured_from_rows, 3),
                "processed_from_chunk_rows_sec": round(processed_from_rows, 3),
            },
        ),
        check(
            "bounded_first_chunk_latency",
            latency_ok,
            f"first chunk latency must be <= {args.max_first_chunk_latency_sec:.1f}s",
            {"first_chunk_latency_sec": first_chunk_latency},
        ),
        check("draft_present", draft_exists, "recording-time draft must exist", {"path": "derived/live/transcript.draft.md"}),
        check(
            "realtime_provenance",
            provenance_ok,
            "all realtime rows must come from committed PCM",
            {"row_count": len(realtime_rows), "values": sorted(provenance_values)},
        ),
        check(
            "fallback_isolation",
            not fallback_contamination,
            "post-stop recovery must not contaminate realtime rows",
            {"fallback_contamination": fallback_contamination},
        ),
        check(
            "meaningful_comparison",
            meaningful,
            "live draft must be meaningfully comparable with batch output",
            {"meaningful_live_comparison": meaningful},
        ),
    ]
    transport_passed = all(row["status"] == "passed" for row in checks)
    if transport_passed and all_parity_passed:
        status = "parity_passed"
    elif transport_passed:
        status = "evidence_ready_non_passing"
    else:
        status = "incomplete"

    parity_rows = list(gates.values())
    remaining = [row for row in parity_rows if row.get("status") != "passed"]
    session_display = str(args.session)
    if not comparison:
        recommended_next = f"murmurmark process {session_display}"
    elif status == "incomplete":
        recommended_next = f"murmurmark status {session_display}"
    else:
        recommended_next = "murmurmark corpus live all --refresh"

    payload = {
        "schema": SCHEMA,
        "generated_at": utc_now(),
        "session": session_display,
        "status": status,
        "transport_evidence_passed": transport_passed,
        "meaningful_comparison": meaningful,
        "all_parity_gates_passed": all_parity_passed,
        "batch_authoritative": batch_authoritative,
        "promotion_allowed": False,
        "checks": checks,
        "remaining_parity_gates": remaining,
        "metrics": {
            "live_pre_stop_chunk_count": pre_stop_chunks,
            "first_live_chunk_latency_sec": first_chunk_latency,
            "final_live_lag_sec": final_lag,
            "report_final_live_lag_sec": report_final_lag,
            "captured_from_segment_rows_sec": round(captured_from_rows, 3),
            "processed_from_chunk_rows_sec": round(processed_from_rows, 3),
            "live_missing_me_seconds": metrics.get("live_missing_me_seconds"),
            "live_remote_in_me_seconds": metrics.get("live_suspected_remote_leak_in_me_seconds"),
            "live_order_mismatch_count": metrics.get("live_blocking_contentful_role_constrained_order_mismatch_count"),
            "adjacent_duplicate_chunk_count": metrics.get("adjacent_duplicate_chunk_count"),
        },
        "inputs": {
            "session_manifest": "session.json" if session_manifest else None,
            "worker_state": "derived/live/live_pipeline_state.json" if worker_state else None,
            "worker_report": "derived/live/live_pipeline_report.json" if worker_report else None,
            "comparison": "derived/live/live_batch_comparison.json" if comparison else None,
        },
        "recommended_next": recommended_next,
    }
    json_path = live_dir / "live_session_evidence.json"
    markdown_path = live_dir / "live_session_evidence.md"
    write_json(json_path, payload)
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")

    print(f"live_session_evidence: {json_path}")
    print(f"status: {status}")
    print(f"transport_evidence_passed: {str(transport_passed).lower()}")
    print(f"meaningful_comparison: {str(meaningful).lower()}")
    print(f"all_parity_gates_passed: {str(all_parity_passed).lower()}")
    print(f"live_pre_stop_chunk_count: {pre_stop_chunks}")
    print(f"first_live_chunk_latency_sec: {first_chunk_latency}")
    print(f"final_live_lag_sec: {final_lag}")
    print(f"remaining_parity_gates: {len(remaining)}")
    print(f"next: {recommended_next}")
    if args.strict and not all_parity_passed:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
