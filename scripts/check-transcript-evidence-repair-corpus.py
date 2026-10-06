#!/usr/bin/env python3
"""Freeze private inputs and replay publication checks without changing a session."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import transcript_publication as publication
import transcript_interval_evidence as intervals
from micro_asr_evidence import assess_utterance

ROOT = Path(__file__).resolve().parents[1]


def qualification_bound() -> dict:
    policy = read(ROOT / "policies/speaker-preserving-neural-echo-v2-17.json")
    corpus = read(ROOT / policy["corpus_set"])
    selector = read(ROOT / "policies/speaker-resolved-transcript-default-v1.json")
    selector = selector["required_evidence"]["default_selector_implementation"]
    unavailable = [row["id"] for row in corpus["sessions"]
                   if not all((ROOT / "sessions" / row["id"] / f"audio/{role}/000001.caf").is_file()
                              for role in ("mic", "remote"))]
    checks = [{"path": policy["transcriber_runtime"], "expected": policy["transcriber_runtime_sha256"]},
              {"path": selector["path"], "expected": selector["sha256"]}]
    for row in checks:
        row.update(actual=digest(ROOT / row["path"]))
        row["unchanged"] = row["actual"] == row["expected"]
    return {"decision": "DO_NOT_PROMOTE", "frozen_producers": checks,
            "old_corpus_sessions": len(corpus["sessions"]), "old_corpus_raw_unavailable": unavailable,
            "required_next": ["replacement_corpus_with_all_existing_echo_safety_controls",
                              "full_producer_cold_warm_conservation_replay",
                              "independent_word_truth_before_selection_changes",
                              "independent_voice_truth_before_cluster_gate_changes"]}


def read(path: Path):
    return json.loads(path.read_text())


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    tmp.replace(path)


def freeze(session: Path) -> dict:
    readiness = read(session / "derived/readiness/session_readiness.json")
    dialogue = Path(readiness["outputs"]["clean_dialogue"]["path"])
    if not dialogue.is_absolute():
        dialogue = session / dialogue
    paths = [session / "session.json", dialogue, *sorted((session / "audio").glob("*/*.caf"))]
    paths += list((session / "derived/transcript-simple/whisper-cpp/resolved").glob("*.json"))
    paths += list((session / "derived/transcript-simple/whisper-cpp/raw").rglob("*.json"))
    files = {str(p.relative_to(session)): digest(p) for p in sorted(set(paths)) if p.is_file()}
    rich = session / "derived/transcript-rich/speaker-resolved-default-v1/provisional/transcript.provisional.json"
    return {"session": str(session), "dialogue": str(dialogue.relative_to(session)), "inputs": files,
            "raw_available": all((session / f"audio/{role}/000001.caf").is_file() for role in ("mic", "remote")),
            "baseline_rich": read(rich) if rich.is_file() else None}


def replay(row: dict) -> dict:
    session = Path(row["session"])
    utterances = read(session / row["dialogue"])["utterances"]
    baseline = row.get("baseline_rich") or {}
    source = baseline.get("utterances") or utterances
    before = json.dumps(source, sort_keys=True)
    start = time.monotonic()
    turns = publication.display_turns(source, baseline.get("remote_utterance_attributions"),
                                      baseline.get("acoustic_timing_evidence"))
    conserved = all("".join(t["text"] for t in sorted(turns, key=lambda t: (t["parent_position"], t["turn_index"]))
                            if t["utterance_id"] == u["id"]) == u["text"] for u in source)
    new_flags = [{"id": u["id"], "role": u["role"], **flag}
                 for u in source if (flag := intervals.assess_utterance(u))]
    risks = [u for u in source if assess_utterance(u)]
    reasons = Counter(reason for u in risks for reason in assess_utterance(u)["reasons"])
    remaining = Counter((u.get("speaker_attribution") or {}).get("reason", "unspecified") for u in source
                        if u.get("role") == "remote" and u.get("speaker_label") == "remote_speaker_unknown")
    return {"session": str(session), "utterances": len(source), "turns": len(turns),
            "publication_sec": round(time.monotonic() - start, 6),
            "conserved_text": conserved, "source_unchanged": before == json.dumps(source, sort_keys=True),
            "new_interval_reviews": new_flags, "micro_review_count": len(risks),
            "micro_review_reasons": dict(reasons),
            "remaining_speaker_reasons": dict(remaining),
            "unavailable_reference": "independent_word_and_voice_truth_not_established",
            "producer_decision": "DO_NOT_PROMOTE",
            "inputs_unchanged": all(p.is_file() and digest(p) == sha
                                    for name, sha in row["inputs"].items() for p in [session / name])}


def check_published(row: dict) -> dict:
    session = Path(row["session"])
    baseline = row.get("baseline_rich")
    if not baseline:
        return {"passed": False, "reason": "baseline_rich_missing"}
    directory = session / "derived/transcript-rich/speaker-resolved-default-v1/provisional"
    selection = read(directory / "selection.json")
    rich_path = session / selection["rich_transcript"]["path"]
    markdown_path = session / selection["selected_transcript"]["path"]
    current = read(rich_path)
    expected_turns = publication.display_turns(current["utterances"], current.get("remote_utterance_attributions"),
                                               current.get("acoustic_timing_evidence"))
    checks = {
        "utterances_and_labels_conserved": current["utterances"] == baseline["utterances"],
        "coverage_conserved": current["summary"] == baseline["summary"],
        "display_turns_current": current["display_turns"] == expected_turns,
        "markdown_matches_json": "\n".join(publication.render_body(expected_turns)) in markdown_path.read_text(),
        "rich_identity_current": digest(rich_path) == selection["rich_transcript"]["sha256"],
        "markdown_identity_current": digest(markdown_path) == selection["selected_transcript"]["sha256"],
    }
    return {"passed": all(checks.values()), "checks": checks, "summary": current["summary"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", action="append", type=Path, default=[])
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--check-published", action="store_true",
                        help="After an explicit refresh, verify the selected immutable publication too.")
    args = parser.parse_args()
    if args.baseline.resolve() == args.out.resolve():
        parser.error("the baseline is immutable; use a different output path")
    sessions = [s.expanduser().resolve() for s in args.session]
    for destination in (args.baseline, args.out):
        if any(destination.resolve().is_relative_to(s) for s in sessions):
            parser.error("reports must be outside input sessions")
    if not args.baseline.exists():
        if not sessions:
            parser.error("provide sessions to freeze a baseline")
        write(args.baseline, {"schema": "murmurmark.transcript_repair_baseline/v1",
                              "sessions": [freeze(s) for s in sessions]})
    baseline = read(args.baseline)
    if any(args.out.resolve().is_relative_to(Path(row["session"])) for row in baseline["sessions"]):
        parser.error("report must be outside frozen sessions")
    results = [replay(row) for row in baseline["sessions"]]
    if args.check_published:
        for original, result in zip(baseline["sessions"], results):
            result["published"] = check_published(original)
    qualification = qualification_bound()
    passed = (all(r["conserved_text"] and r["source_unchanged"] and r["inputs_unchanged"] for r in results)
              and all(row["unchanged"] for row in qualification["frozen_producers"])
              and all(row.get("published", {}).get("passed", True) for row in results))
    report = {"schema": "murmurmark.transcript_repair_replay/v1", "passed": passed,
              "baseline_sha256": digest(args.baseline), "sessions": results,
              "acoustic_accuracy_measured": False, "qualification": qualification}
    write(args.out, report)
    print(json.dumps({"passed": passed, "sessions": len(results), "report": str(args.out)}))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
