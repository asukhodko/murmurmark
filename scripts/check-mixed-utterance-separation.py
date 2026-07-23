#!/usr/bin/env python3
"""Focused checks for Mixed-Utterance Remote Span Separation v1."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_module():
    path = Path(__file__).with_name("mixed-utterance-span-separation.py")
    spec = importlib.util.spec_from_file_location("murmurmark_mixed_span_checks", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def remote_evidence(*, confirmed: bool = True) -> dict:
    support = {
        "supported": confirmed,
        "valid": True,
        "text": "remote phrase",
        "similarity": 0.95 if confirmed else 0.1,
        "containment": 0.95 if confirmed else 0.1,
    }
    return {
        "mic_support": {
            "mic_clean": dict(support),
            "mic_raw": dict(support),
        },
        "remote_support": dict(support),
        "cross_mic_similarity": 0.95 if confirmed else 0.1,
        "authoritative_similarity": 0.98,
    }


def local_island(*, confirmed: bool = True) -> dict:
    return {
        "confirmed_local": confirmed,
        "voice": {"best_mic": 0.92 if confirmed else 0.4},
        "mic_support": {
            "mic_clean": {"similarity": 0.95 if confirmed else 0.2},
            "mic_raw": {"similarity": 0.94 if confirmed else 0.2},
        },
    }


def queue(source_label: str = "remote_duplicate", **protection: bool) -> dict:
    return {"source_label": source_label, "protection": protection}


def check_span_plans(module) -> None:
    middle = module.span_plan(
        "локальное начало удаленная фраза локальный хвост",
        "удаленная фраза",
    )
    assert middle is not None, middle
    assert middle["local_prefix_text"] == "локальное начало", middle
    assert middle["local_tail_text"] == "локальный хвост", middle

    prefix = module.span_plan("удаленная фраза локальный хвост", "удаленная фраза")
    assert prefix is not None and not prefix["local_prefix_text"], prefix
    assert prefix["local_tail_text"] == "локальный хвост", prefix

    tail = module.span_plan("локальное начало удаленная фраза", "удаленная фраза")
    assert tail is not None and tail["local_prefix_text"] == "локальное начало", tail
    assert not tail["local_tail_text"], tail

    assert module.span_plan("полный удаленный дубль", "полный удаленный дубль") is None


def check_decisions(module) -> None:
    quiet_state = {"local_only_ratio": 0.0, "double_talk_ratio": 0.0, "remote_active_ratio": 1.0}
    span_voice = {"confirmed": False, "weak": True, "best_mic": 0.2}

    both = module.classify_evidence(
        queue(),
        remote_evidence(),
        local_island(),
        local_island(),
        span_voice,
        quiet_state,
    )
    assert both["outcome"] == "confirmed_remote_duplicate", both
    assert both["action"] == "split_keep_both_local_islands", both

    prefix = module.classify_evidence(
        queue(),
        remote_evidence(),
        local_island(),
        None,
        span_voice,
        quiet_state,
    )
    assert prefix["action"] == "split_keep_local_prefix", prefix

    tail = module.classify_evidence(
        queue("remote_leak"),
        remote_evidence(),
        None,
        local_island(),
        span_voice,
        quiet_state,
    )
    assert tail["outcome"] == "confirmed_remote_leak", tail
    assert tail["action"] == "split_keep_local_tail", tail

    double_talk = module.classify_evidence(
        queue(),
        remote_evidence(),
        local_island(),
        None,
        {"confirmed": True, "weak": False, "best_mic": 0.94},
        {"local_only_ratio": 0.0, "double_talk_ratio": 0.5, "remote_active_ratio": 0.8},
    )
    assert double_talk["outcome"] == "confirmed_double_talk", double_talk
    assert double_talk["action"] == "keep_unchanged", double_talk

    local = module.classify_evidence(
        queue(),
        remote_evidence(confirmed=False),
        local_island(),
        None,
        {"confirmed": True, "weak": False, "best_mic": 0.94},
        {"local_only_ratio": 0.8, "double_talk_ratio": 0.0, "remote_active_ratio": 0.0},
    )
    assert local["outcome"] == "confirmed_local", local
    assert local["action"] == "keep_unchanged", local

    protected = module.classify_evidence(
        queue(protected_work_marker_in_remote_span=True),
        remote_evidence(),
        local_island(),
        None,
        span_voice,
        quiet_state,
    )
    assert protected["outcome"] == "ambiguous", protected
    assert protected["action"] == "needs_review", protected

    weak_target = module.classify_evidence(
        queue(),
        remote_evidence(),
        local_island(confirmed=False),
        None,
        {"confirmed": False, "weak": True, "best_mic": 0.3},
        quiet_state,
    )
    assert weak_target["outcome"] == "probable_asr_noise", weak_target
    assert weak_target["action"] == "needs_review", weak_target

    missing_model_or_evidence = module.classify_evidence(
        queue(),
        {},
        None,
        None,
        {"confirmed": False, "weak": True, "best_mic": 0.0},
        {"local_only_ratio": 0.0, "double_talk_ratio": 0.0, "remote_active_ratio": 0.0},
    )
    assert missing_model_or_evidence["outcome"] == "ambiguous", missing_model_or_evidence
    assert missing_model_or_evidence["action"] == "needs_review", missing_model_or_evidence

    conflicting_mic_views = remote_evidence()
    conflicting_mic_views["mic_support"]["mic_raw"] = {
        "supported": False,
        "valid": True,
        "text": "local continuation",
        "similarity": 0.1,
        "containment": 0.1,
    }
    conflict = module.classify_evidence(
        queue(),
        conflicting_mic_views,
        local_island(),
        None,
        span_voice,
        quiet_state,
    )
    assert conflict["outcome"] == "ambiguous", conflict
    assert conflict["action"] == "needs_review", conflict


def check_lossless_patch(module) -> None:
    original = {
        "id": "me_1",
        "role": "me",
        "source_track": "mic",
        "start": 1.0,
        "end": 8.0,
        "text": "локальное начало удаленная фраза локальный хвост",
        "quality": {"needs_review": True},
    }
    plan = module.span_plan(original["text"], "удаленная фраза")
    assert plan is not None
    queue_row = {
        "queue_id": "mix_test",
        "interval": {"start": 3.0, "end": 6.0},
        "span_plan": plan,
    }
    evidence = {
        "provenance_sha256": "evidence",
        "decision": {"action": "split_keep_both_local_islands"},
    }
    rows = module.make_patch_rows(original, queue_row, evidence)
    assert [row["text"] for row in rows] == ["локальное начало", "локальный хвост"], rows
    assert rows[0]["id"] == "me_1", rows
    assert rows[1]["id"].startswith("me_1__mixed_post_"), rows
    assert rows[0]["end"] == 3.0 and rows[1]["start"] == 6.0, rows
    assert module.fingerprint(rows) == module.fingerprint(rows)


def check_generated_corpus(module, root: Path) -> None:
    sessions_root = root / "sessions"
    corpus_dir = sessions_root / "_reports/mixed-utterance-separation-v1"
    report = module.read_json(corpus_dir / "mixed_utterance_separation_corpus_report.json")
    if not report:
        return
    baseline = module.read_json(corpus_dir / "baseline_manifest.json")
    assert baseline and baseline["gates"]["passed"] is True, baseline
    assert report["decision"] in {
        module.PROMOTE_DECISION,
        "DO_NOT_PROMOTE",
    }, report["decision"]
    assert report["summary"]["queue_items"] > 0, report["summary"]
    assert report["summary"]["additional_runtime_ratio"] <= module.MAX_RUNTIME_RATIO, report["summary"]
    assert not any(
        "raw_capture" in value or "remote_content_changed" in value
        for value in (report.get("gates") or {}).get("hard_failures") or []
    ), report["gates"]
    expected_value_failures = {
        "duplicate_leak_reduction_below_25_percent",
        "mandatory_review_reduction_below_15_percent",
    }
    if report["decision"] == "DO_NOT_PROMOTE":
        assert {
            value.split(":", 1)[0]
            for value in (report.get("gates") or {}).get("hard_failures") or []
        } <= expected_value_failures, report["gates"]

    baseline_by_session = {
        row["session_id"]: row
        for row in baseline.get("sessions") or []
        if isinstance(row, dict)
    }
    for session_report in report.get("sessions") or []:
        session_id = session_report["session_id"]
        session = sessions_root / session_id
        baseline_row = baseline_by_session[session_id]
        assert (
            module.fingerprint(
                module.frozen_artifacts(session, baseline_row["input_profile"])
            )
            == module.fingerprint(baseline_row["artifacts"])
        ), session_id
        input_dialogue = module.read_json(
            module.profile_paths(session, baseline_row["input_profile"])["dialogue"]
        )
        output_dialogue = module.read_json(
            module.profile_paths(session, module.PROFILE)["dialogue"]
        )
        assert input_dialogue and output_dialogue, session_id
        assert [
            row
            for row in input_dialogue["utterances"]
            if module.role_name(row) == "Colleagues"
        ] == [
            row
            for row in output_dialogue["utterances"]
            if module.role_name(row) == "Colleagues"
        ], session_id
        output_ids = {str(row.get("id") or "") for row in output_dialogue["utterances"]}
        evidence_notes = module.read_json(
            session
            / "derived/synthesis-simple/extractive"
            / f"evidence_notes.{module.PROFILE}.json"
        )
        assert evidence_notes, session_id
        assert module.evidence_ids(evidence_notes) <= output_ids, session_id
        assert session_report["deterministic"] is True, session_id


def main() -> int:
    module = load_module()
    check_span_plans(module)
    check_decisions(module)
    check_lossless_patch(module)
    check_generated_corpus(module, Path(__file__).resolve().parents[1])
    print("mixed utterance separation checks ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
