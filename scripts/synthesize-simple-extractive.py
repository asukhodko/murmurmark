#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VERDICTS = ("good", "usable_with_review", "risky", "failed")
DECISION_MARKERS = ("решили", "договорились", "согласовали", "окей", "тогда делаем")
ACTION_MARKERS = ("надо", "нужно", "я сделаю", "давай", "проверь", "посмотрю", "сделаем")
RISK_MARKERS = ("риск", "проблема", "непонятно", "вопрос", "блокер", "сомневаюсь")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build local extractive MurmurMark notes from transcript artifacts.")
    parser.add_argument("session", type=Path)
    parser.add_argument(
        "--transcript-profile",
        choices=("auto", "current", "shadow_v2"),
        default="auto",
        help="Transcript artifact profile to synthesize from.",
    )
    return parser.parse_args()


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, f"missing file: {path}"
    except json.JSONDecodeError as error:
        return None, f"invalid json: {path}: {error}"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def format_time(seconds: float | int | None) -> str:
    if seconds is None:
        return "??:??"
    total = max(0, int(float(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def clean_text(text: Any, limit: int = 280) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def utterance_id(row: dict[str, Any], index: int) -> str:
    return str(row.get("id") or f"utt_{index + 1:06d}")


def source_profile_paths(resolved_dir: Path, requested_profile: str) -> dict[str, Path]:
    suffix = ".shadow_v2" if requested_profile == "shadow_v2" else ""
    return {
        "clean_dialogue": resolved_dir / f"clean_dialogue{suffix}.json",
        "quality_report": resolved_dir / f"quality_report{suffix}.json",
        "overlaps": resolved_dir / f"overlaps{suffix}.json",
        "repair_comparison": resolved_dir / "repair_comparison.json",
    }


def choose_profile(resolved_dir: Path, requested_profile: str) -> tuple[str, dict[str, Path], dict[str, Any] | None, list[dict[str, Any]]]:
    risk_items: list[dict[str, Any]] = []
    repair_comparison: dict[str, Any] | None = None

    if requested_profile == "auto":
        comparison_path = resolved_dir / "repair_comparison.json"
        comparison, error = read_json(comparison_path)
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        shadow_paths = source_profile_paths(resolved_dir, "shadow_v2")
        if shadow_paths["clean_dialogue"].exists() and repair_comparison and repair_comparison.get("passed") is True:
            return "shadow_v2", shadow_paths, repair_comparison, risk_items
        return "current", source_profile_paths(resolved_dir, "current"), repair_comparison, risk_items

    if requested_profile == "shadow_v2":
        paths = source_profile_paths(resolved_dir, "shadow_v2")
        comparison, error = read_json(paths["repair_comparison"])
        if error is None and isinstance(comparison, dict):
            repair_comparison = comparison
        if not repair_comparison or repair_comparison.get("passed") is not True:
            risk_items.append(
                {
                    "type": "shadow_profile_without_passing_comparison",
                    "severity": "high",
                    "reason": "shadow_v2 was requested but repair_comparison.json did not pass",
                }
            )
        return "shadow_v2", paths, repair_comparison, risk_items

    return "current", source_profile_paths(resolved_dir, "current"), None, risk_items


def load_inputs(session: Path, selected_profile: str, paths: dict[str, Path]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    dialogue, dialogue_error = read_json(paths["clean_dialogue"])
    quality, quality_error = read_json(paths["quality_report"])
    overlaps, overlaps_error = read_json(paths["overlaps"])

    risk_items: list[dict[str, Any]] = []
    if dialogue_error:
        risk_items.append({"type": "missing_clean_dialogue", "severity": "fatal", "reason": dialogue_error})
        dialogue = {"schema": None, "utterances": []}
    if quality_error:
        risk_items.append({"type": "missing_quality_report", "severity": "medium", "reason": quality_error})
        quality = {}
    if overlaps_error:
        overlaps = {"schema": None, "overlaps": []}

    if not isinstance(dialogue, dict) or dialogue.get("schema") != "murmurmark.clean_dialogue/v1":
        risk_items.append(
            {
                "type": "invalid_clean_dialogue_schema",
                "severity": "fatal",
                "reason": f"expected murmurmark.clean_dialogue/v1 for profile {selected_profile}",
            }
        )
        dialogue = {"schema": None, "utterances": []}

    utterances = dialogue.get("utterances") if isinstance(dialogue, dict) else []
    if not isinstance(utterances, list):
        risk_items.append({"type": "invalid_utterances", "severity": "fatal", "reason": "clean_dialogue.utterances is not an array"})
        utterances = []

    overlap_rows = overlaps.get("overlaps") if isinstance(overlaps, dict) else []
    if not isinstance(overlap_rows, list):
        overlap_rows = []

    for index, row in enumerate(utterances):
        if isinstance(row, dict):
            row.setdefault("id", utterance_id(row, index))

    return quality if isinstance(quality, dict) else {}, utterances, overlap_rows + risk_items


def metrics_from_quality(quality: dict[str, Any], utterances: list[dict[str, Any]], overlap_rows: list[dict[str, Any]]) -> dict[str, Any]:
    needs_review = int(quality.get("needs_review_count", sum(1 for row in utterances if row.get("quality", {}).get("needs_review"))))
    utterance_count = int(quality.get("utterances", len(utterances)))
    cross_gt2 = int(quality.get("cross_role_overlap_gt2_count", sum(1 for row in overlap_rows if float(row.get("duration_sec", 0) or 0) > 2.0)))
    remote_duplicate_seconds = float(quality.get("remote_duplicate_in_me_seconds", 0.0) or 0.0)
    return {
        "utterances": utterance_count,
        "needs_review_count": needs_review,
        "needs_review_ratio": round(needs_review / utterance_count, 6) if utterance_count > 0 else None,
        "cross_role_overlap_gt2_count": cross_gt2,
        "cross_role_overlap_gt2_seconds": float(quality.get("cross_role_overlap_gt2_seconds", 0.0) or 0.0),
        "remote_duplicate_in_me_seconds": round(remote_duplicate_seconds, 3),
        "unrepaired_long_mic_crossings_count": int(quality.get("unrepaired_long_mic_crossings_count", 0) or 0),
        "golden_phrase_fail_count": int(quality.get("golden_phrase_fail_count", 0) or 0),
    }


def verdict_from_metrics(
    selected_profile: str,
    metrics: dict[str, Any],
    risk_items: list[dict[str, Any]],
    repair_comparison: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]]]:
    items = list(risk_items)
    utterances = int(metrics.get("utterances", 0) or 0)
    needs_ratio = metrics.get("needs_review_ratio")

    if any(item.get("severity") == "fatal" for item in items) or utterances <= 0:
        if utterances <= 0:
            items.append({"type": "empty_transcript", "severity": "fatal", "reason": "selected clean_dialogue has no utterances"})
        return "failed", items

    if selected_profile == "shadow_v2" and (not repair_comparison or repair_comparison.get("passed") is not True):
        items.append(
            {
                "type": "shadow_profile_without_passing_comparison",
                "severity": "high",
                "reason": "selected shadow transcript did not pass repair comparison gates",
            }
        )

    if int(metrics["unrepaired_long_mic_crossings_count"]) > 0:
        items.append(
            {
                "type": "unrepaired_long_mic_crossings",
                "severity": "high",
                "reason": "long mic segments still cross authoritative remote intervals",
            }
        )
    if int(metrics["golden_phrase_fail_count"]) > 0:
        items.append({"type": "golden_phrase_failures", "severity": "high", "reason": "configured golden phrase checks failed"})
    if needs_ratio is not None and float(needs_ratio) > 0.12:
        items.append(
            {
                "type": "needs_review_ratio",
                "severity": "high",
                "reason": "more than 12% of utterances need review",
            }
        )
    if float(metrics["remote_duplicate_in_me_seconds"]) > 180.0:
        items.append(
            {
                "type": "remote_duplicate_in_me_seconds",
                "severity": "high",
                "reason": "too much remote speech may remain in Me utterances",
            }
        )

    if any(item.get("severity") == "high" for item in items):
        return "risky", items

    if int(metrics["needs_review_count"]) > 0:
        items.append({"type": "needs_review_utterances", "severity": "medium", "reason": "some utterances need review"})
    if int(metrics["cross_role_overlap_gt2_count"]) > 0:
        items.append({"type": "long_cross_role_overlaps", "severity": "medium", "reason": "some role overlaps are longer than 2 seconds"})
    if float(metrics["remote_duplicate_in_me_seconds"]) > 0.0:
        items.append({"type": "remote_duplicate_in_me_seconds", "severity": "medium", "reason": "some remote overlap remains in Me utterances"})

    if any(item.get("severity") == "medium" for item in items):
        return "usable_with_review", items
    return "good", items


def role(row: dict[str, Any]) -> str:
    return str(row.get("speaker_label") or row.get("role") or row.get("source_track") or "Unknown")


def contains_marker(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def evidence_item(row: dict[str, Any], index: int, category: str, markers: tuple[str, ...]) -> dict[str, Any] | None:
    text = clean_text(row.get("text"), limit=420)
    if not text or not contains_marker(text, markers):
        return None
    return {
        "id": f"{category}_{index + 1:04d}",
        "category": category,
        "status": "needs_review",
        "utterance_ids": [utterance_id(row, index)],
        "start": row.get("start"),
        "end": row.get("end"),
        "role": role(row),
        "text": text,
    }


def build_outline(utterances: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not utterances:
        return []

    blocks: list[list[tuple[int, dict[str, Any]]]] = []
    current: list[tuple[int, dict[str, Any]]] = []
    block_start = float(utterances[0].get("start", 0.0) or 0.0)
    previous_end = block_start

    for index, row in enumerate(utterances):
        start = float(row.get("start", previous_end) or previous_end)
        gap = start - previous_end
        span = start - block_start
        if current and (gap > 90.0 or span > 600.0):
            blocks.append(current)
            current = []
            block_start = start
        current.append((index, row))
        previous_end = float(row.get("end", start) or start)

    if current:
        blocks.append(current)

    outline: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks, start=1):
        first_index, first = block[0]
        last_index, last = block[-1]
        samples = []
        for sample_index, sample in block[:3]:
            text = clean_text(sample.get("text"), limit=180)
            if text:
                samples.append(
                    {
                        "utterance_id": utterance_id(sample, sample_index),
                        "role": role(sample),
                        "text": text,
                    }
                )
        outline.append(
            {
                "id": f"outline_{block_index:03d}",
                "start": first.get("start"),
                "end": last.get("end"),
                "utterance_ids": [utterance_id(first, first_index), utterance_id(last, last_index)],
                "utterance_count": len(block),
                "samples": samples,
            }
        )
    return outline


def build_evidence_notes(utterances: list[dict[str, Any]]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    for index, row in enumerate(utterances):
        if not isinstance(row, dict):
            continue
        decision = evidence_item(row, index, "decision", DECISION_MARKERS)
        action = evidence_item(row, index, "action", ACTION_MARKERS)
        risk = evidence_item(row, index, "risk_or_open_question", RISK_MARKERS)
        if decision:
            decisions.append(decision)
        if action:
            actions.append(action)
        if risk:
            risks.append(risk)

    return {
        "schema": "murmurmark.evidence_notes/v1",
        "outline": build_outline(utterances),
        "potential_decisions": decisions,
        "potential_actions": actions,
        "risks_and_open_questions": risks,
    }


def build_review_items(
    utterances: list[dict[str, Any]],
    overlaps: list[dict[str, Any]],
    risk_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(utterances):
        quality = row.get("quality") if isinstance(row, dict) else {}
        if isinstance(quality, dict) and quality.get("needs_review"):
            rows.append(
                {
                    "type": "utterance_needs_review",
                    "severity": "medium",
                    "start": row.get("start"),
                    "end": row.get("end"),
                    "utterance_ids": [utterance_id(row, index)],
                    "reason": "utterance quality.needs_review is true",
                    "text": clean_text(row.get("text"), limit=360),
                }
            )

    for index, overlap in enumerate(overlaps):
        if not isinstance(overlap, dict) or "duration_sec" not in overlap:
            continue
        severity = "medium" if float(overlap.get("duration_sec", 0.0) or 0.0) > 2.0 else "low"
        rows.append(
            {
                "type": "cross_role_overlap",
                "severity": severity,
                "start": overlap.get("start"),
                "end": overlap.get("end"),
                "utterance_ids": [
                    value
                    for value in (
                        overlap.get("left_utterance_id"),
                        overlap.get("right_utterance_id"),
                        overlap.get("left_id"),
                        overlap.get("right_id"),
                    )
                    if value
                ],
                "reason": overlap.get("type", "overlap"),
                "text": clean_text(overlap.get("text") or overlap.get("left_text") or overlap.get("right_text"), limit=360),
            }
        )

    for item in risk_items:
        rows.append(
            {
                "type": item.get("type", "verdict_risk"),
                "severity": item.get("severity", "medium"),
                "start": item.get("start"),
                "end": item.get("end"),
                "utterance_ids": item.get("utterance_ids", []),
                "reason": item.get("reason", ""),
                "text": clean_text(item.get("text"), limit=360),
            }
        )
    return rows


def write_quality_markdown(path: Path, verdict_payload: dict[str, Any]) -> None:
    lines = [
        "# Quality Verdict",
        "",
        f"Verdict: `{verdict_payload['verdict']}`",
        f"Transcript profile: `{verdict_payload['selected_transcript_profile']}`",
        "",
        "## Metrics",
        "",
    ]
    for key, value in verdict_payload["metrics"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Risk Items", ""])
    if verdict_payload["risk_items"]:
        for item in verdict_payload["risk_items"]:
            lines.append(f"- `{item.get('severity', 'medium')}` `{item.get('type', 'risk')}`: {item.get('reason', '')}")
    else:
        lines.append("- none")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_notes_markdown(path: Path, verdict: dict[str, Any], evidence: dict[str, Any]) -> None:
    lines = [
        "# Extractive Notes",
        "",
        f"Verdict: `{verdict['verdict']}`  ",
        f"Transcript profile: `{verdict['selected_transcript_profile']}`",
        "",
        "These notes are extractive. Treat potential decisions and actions as review candidates until confirmed.",
        "",
        "## Conversation Outline",
        "",
    ]
    for block in evidence["outline"]:
        start = format_time(block.get("start"))
        end = format_time(block.get("end"))
        ids = block.get("utterance_ids", [])
        lines.append(f"- `{start}-{end}` utterances `{ids[0]}`..`{ids[-1]}` ({block.get('utterance_count', 0)} turns)")
        for sample in block.get("samples", []):
            lines.append(f"  - `{sample['utterance_id']}` {sample['role']}: {sample['text']}")
    if not evidence["outline"]:
        lines.append("- no utterances")

    section_titles = (
        ("Potential Decisions", "potential_decisions"),
        ("Potential Actions", "potential_actions"),
        ("Risks / Open Questions", "risks_and_open_questions"),
    )
    for title, key in section_titles:
        lines.extend(["", f"## {title}", ""])
        rows = evidence.get(key, [])
        if not rows:
            lines.append("- none detected")
            continue
        for item in rows:
            ids = ", ".join(f"`{value}`" for value in item["utterance_ids"])
            lines.append(
                f"- `needs_review` {format_time(item.get('start'))}-{format_time(item.get('end'))} "
                f"{ids} {item['role']}: {item['text']}"
            )
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_failed_outputs(out_dir: Path, session: Path, requested_profile: str, risk_items: list[dict[str, Any]]) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    metrics = {
        "utterances": 0,
        "needs_review_count": 0,
        "needs_review_ratio": None,
        "cross_role_overlap_gt2_count": 0,
        "cross_role_overlap_gt2_seconds": 0.0,
        "remote_duplicate_in_me_seconds": 0.0,
        "unrepaired_long_mic_crossings_count": 0,
        "golden_phrase_fail_count": 0,
    }
    payload = {
        "schema": "murmurmark.quality_verdict/v1",
        "verdict": "failed",
        "selected_transcript_profile": requested_profile,
        "inputs": {},
        "metrics": metrics,
        "risk_items": risk_items,
    }
    write_json(out_dir / "quality_verdict.json", payload)
    write_quality_markdown(out_dir / "quality_verdict.md", payload)
    write_json(out_dir / "evidence_notes.json", {"schema": "murmurmark.evidence_notes/v1", "outline": [], "potential_decisions": [], "potential_actions": [], "risks_and_open_questions": []})
    (out_dir / "notes.md").write_text("# Extractive Notes\n\nNo transcript evidence was available.\n", encoding="utf-8")
    write_jsonl(out_dir / "review_items.jsonl", build_review_items([], [], risk_items))
    write_json(
        out_dir / "synthesis_manifest.json",
        {
            "schema": "murmurmark.synthesis_manifest/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "session": str(session),
            "mode": "extractive",
            "outputs": {
                "quality_verdict": "quality_verdict.json",
                "quality_verdict_markdown": "quality_verdict.md",
                "notes_markdown": "notes.md",
                "evidence_notes": "evidence_notes.json",
                "review_items": "review_items.jsonl",
            },
        },
    )
    print("verdict: failed")
    print(f"selected_transcript_profile: {requested_profile}")
    print(f"quality_verdict: {out_dir / 'quality_verdict.json'}")
    print(f"notes: {out_dir / 'notes.md'}")
    return 0


def main() -> int:
    args = parse_args()
    session = args.session.expanduser().resolve()
    resolved_dir = session / "derived" / "transcript-simple" / "whisper-cpp" / "resolved"
    out_dir = session / "derived" / "synthesis-simple" / "extractive"
    out_dir.mkdir(parents=True, exist_ok=True)

    selected_profile, paths, repair_comparison, selection_risks = choose_profile(resolved_dir, args.transcript_profile)
    quality, utterances, overlaps_and_input_risks = load_inputs(session, selected_profile, paths)

    input_risks = [item for item in overlaps_and_input_risks if "duration_sec" not in item]
    overlap_rows = [item for item in overlaps_and_input_risks if "duration_sec" in item]
    metrics = metrics_from_quality(quality, utterances, overlap_rows)
    verdict, risk_items = verdict_from_metrics(selected_profile, metrics, selection_risks + input_risks, repair_comparison)

    inputs = {
        "clean_dialogue": rel(paths["clean_dialogue"], session),
        "quality_report": rel(paths["quality_report"], session),
        "overlaps": rel(paths["overlaps"], session),
    }
    if repair_comparison is not None or paths["repair_comparison"].exists():
        inputs["repair_comparison"] = rel(paths["repair_comparison"], session)

    verdict_payload = {
        "schema": "murmurmark.quality_verdict/v1",
        "verdict": verdict,
        "selected_transcript_profile": selected_profile,
        "requested_transcript_profile": args.transcript_profile,
        "inputs": inputs,
        "metrics": metrics,
        "risk_items": risk_items,
    }

    if verdict == "failed":
        return write_failed_outputs(out_dir, session, selected_profile, risk_items)

    evidence = build_evidence_notes(utterances)
    review_items = build_review_items(utterances, overlap_rows, risk_items)

    write_json(out_dir / "quality_verdict.json", verdict_payload)
    write_quality_markdown(out_dir / "quality_verdict.md", verdict_payload)
    write_json(out_dir / "evidence_notes.json", evidence)
    write_notes_markdown(out_dir / "notes.md", verdict_payload, evidence)
    write_jsonl(out_dir / "review_items.jsonl", review_items)
    write_json(
        out_dir / "synthesis_manifest.json",
        {
            "schema": "murmurmark.synthesis_manifest/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "session": str(session),
            "mode": "extractive",
            "selected_transcript_profile": selected_profile,
            "requested_transcript_profile": args.transcript_profile,
            "inputs": inputs,
            "outputs": {
                "quality_verdict": "quality_verdict.json",
                "quality_verdict_markdown": "quality_verdict.md",
                "notes_markdown": "notes.md",
                "evidence_notes": "evidence_notes.json",
                "review_items": "review_items.jsonl",
            },
        },
    )

    print(f"verdict: {verdict}")
    print(f"selected_transcript_profile: {selected_profile}")
    print(f"quality_verdict: {out_dir / 'quality_verdict.json'}")
    print(f"notes: {out_dir / 'notes.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
