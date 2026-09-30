#!/usr/bin/env python3
"""Read-only provenance audit of saved review decisions; never applies them."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from review_audio_evidence import (
    dialogue_path, evidence_matches_review_row, file_identity, review_targets, signature,
)


def read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audit(session: Path) -> dict:
    session = session.resolve()
    plan = session / "derived/readiness/review-plan"
    pack = session / "derived/audit/audio-review-pack"
    decisions = read_rows(plan / "review_decisions.jsonl")
    candidates = read_rows(pack / "faster_whisper_judge.jsonl")
    candidates += read_rows(pack / "faster_whisper_judge_history.jsonl")
    rows = []
    inputs = {plan / "review_decisions.jsonl", pack / "faster_whisper_judge.jsonl",
              pack / "faster_whisper_judge_history.jsonl"}
    for decision in decisions:
        if decision.get("decision") not in {"keep_me", "drop_me", "drop_remote", "skip"}:
            continue
        row = {**decision, "session": str(session)}
        source = dialogue_path(row)
        if source:
            inputs.add(source)
        targets = review_targets(row)
        semantic = []
        for candidate in candidates:
            # Legacy rows have no sealed provenance. Check semantic identity
            # separately, explicitly without certifying the historical verdict.
            legacy = {key: value for key, value in candidate.items()
                      if key not in {"evidence_files", "classification_inputs"}}
            legacy["schema"] = ""
            if evidence_matches_review_row(row, legacy):
                semantic.append(candidate)
        sealed = [candidate for candidate in semantic if evidence_matches_review_row(row, candidate)]
        rows.append({
            "source_audit_id": row.get("source_audit_id"), "decision": row.get("decision"),
            "input_profile": row.get("input_profile"), "interval": row.get("interval"),
            "target_present_in_input": bool(targets),
            "legacy_semantic_matches": len(semantic), "current_sealed_matches": len(sealed),
            "labels": sorted({str((candidate.get("classification") or {}).get("label")) for candidate in semantic}),
            "receipt_present": bool((row.get("review_evidence") or {}).get("suggestion_receipt")),
            "status": "needs_provenance_refresh" if not sealed else "current_evidence_available",
        })

    output = session / "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.reviewed_v1.json"
    inputs.add(output)
    output_rows = json.loads(output.read_text(encoding="utf-8")).get("utterances", []) if output.is_file() else []
    comparisons = []
    profiles = sorted({str(row["input_profile"]) for row in rows if row.get("input_profile")})
    for profile in profiles:
        source = dialogue_path({"session": str(session), "input_profile": profile})
        if not source or not source.is_file() or not output.is_file() or source == output:
            continue
        before = Counter(signature(row) for row in json.loads(source.read_text(encoding="utf-8")).get("utterances", []))
        after = Counter(signature(row) for row in output_rows)
        removed, added = before - after, after - before
        comparisons.append({
            "input_profile": profile, "output_profile": "reviewed_v1",
            "removed_me": sum(count for key, count in removed.items() if key[0] == "me"),
            "removed_remote": sum(count for key, count in removed.items() if key[0] == "remote"),
            "added_or_changed_utterances": sum(added.values()),
        })
    return {
        "schema": "murmurmark.review_decision_evidence_audit/v1", "session": str(session),
        "read_only": True, "decisions": len(rows), "rows": rows, "profile_comparisons": comparisons,
        "by_decision": dict(Counter(row["decision"] for row in rows)),
        "missing_semantic_evidence": sum(not row["legacy_semantic_matches"] for row in rows),
        "missing_current_sealed_evidence": sum(not row["current_sealed_matches"] for row in rows),
        "inputs": [file_identity(path) for path in sorted(inputs)],
        "limitation": "Matching historical text/times does not prove audible speech or validate a deletion. No decisions are changed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report = audit(args.session)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        destination = args.out.resolve()
        if destination.is_relative_to(args.session.resolve()):
            parser.error("--out must be outside the session being audited")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        print(json.dumps({key: report[key] for key in (
            "decisions", "by_decision", "missing_semantic_evidence", "missing_current_sealed_evidence", "profile_comparisons",
        )}, ensure_ascii=False, indent=2))
        print(f"report: {destination}")
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
