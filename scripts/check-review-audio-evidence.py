#!/usr/bin/env python3
"""Cross-module review evidence regressions, without models or real recordings."""
from __future__ import annotations

import contextlib
import copy
import importlib.util
import io
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import review_audio_evidence as evidence


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def main():
    lane = load("build-review-lane-pack.py")
    apply = load("apply-review-workspace-decisions.py")
    apply_single = load("apply-review-lane-pack-decisions.py")
    judge = load("audit-stronger-audio-judge.py")
    target_me = load("audit-target-me.py")
    outcome = load("evaluate-outcome.py")
    decision_audit = load("audit-review-decision-evidence.py")
    quality = load("report-session-quality.py")
    materializer = load("apply-review-decisions.py")
    audio_audit = load("audit-audio-review-pack.py")
    readiness = load("report-operational-readiness.py")
    cli_source = (Path(__file__).resolve().parents[1] / "Sources/MurmurMarkCLI/MurmurMarkCLI.swift").read_text()
    targeted_args = cli_source.split('var judgeArgs = [', 1)[1].split(']', 1)[0]
    assert '"--word-timestamps"' in targeted_args
    with tempfile.TemporaryDirectory(prefix="murmurmark-review-evidence-") as directory:
        session = Path(directory) / "fixture"
        source = session / "clip.wav"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"audio-one")
        remote_source = session / "remote.wav"
        remote_source.write_bytes(b"remote-audio")
        policy = session / "policy.json"
        write(policy, {"version": 1})
        utterance = {"id": "utt_new", "role": "me", "source_track": "mic", "text": "current local phrase", "start": 10.0, "end": 12.0}
        row = {"session_id": "fixture", "session": str(session), "input_profile": "reviewed_v1",
               "source": "audio_review", "source_audit_id": "arp_1", "label": "uncertain",
               "review_lane": "classify_audio", "interval": {"start": 10.0, "end": 12.0},
               "text": [utterance], "utterance_ids": ["utt_new"], "me_utterance_ids": ["utt_new"],
               "allowed_decisions": ["keep_me", "drop_me", "needs_review"], "decision": "todo", "status": "todo"}
        manual_template = session / "manual.template.jsonl"
        manual_output = session / "manual.jsonl"
        manual_template.write_text(json.dumps(row) + "\n", encoding="utf-8")
        manual_command = [sys.executable, str(Path(__file__).with_name("review-decisions-cli.py")),
                          "--template", str(manual_template), "--out", str(manual_output), "--no-play"]
        subprocess.run(manual_command, input="k\n", text=True, capture_output=True, check=True, timeout=20)
        manual_row = json.loads(manual_output.read_text())
        assert evidence.decision_origin(manual_row) == "human"
        assert evidence.effective_decision(manual_row) == "keep_me"
        # Opening an old completed review must not invent human provenance.
        del manual_row["review_source"]
        manual_output.write_text(json.dumps(manual_row) + "\n", encoding="utf-8")
        subprocess.run(manual_command, input="", text=True, capture_output=True, check=True, timeout=20)
        assert evidence.decision_origin(json.loads(manual_output.read_text())) == "legacy_unknown"
        dialogue = evidence.dialogue_path(row)
        write(dialogue, {"utterances": [utterance]})
        candidate = {"schema": judge.SCHEMA_ROW, "id": "fwj_1", "session_id": "fixture", "profile": "audit_cleanup_v2",
                     "source_pack_item_id": "arp_old", "interval": row["interval"],
                     "utterances": [{**utterance, "id": "utt_old"}], "utterance_ids": ["utt_old"],
                     "classification": {"label": "confirm_remote_duplicate", "confidence": 0.99},
                     "sources": ["mic_clean", "remote"], "clips": {"mic_clean": str(source), "remote": str(remote_source)},
                     "classification_scope": {"mic_clean": "word_bounded", "remote": "word_bounded"}}
        candidate["source_pack_item_fingerprint"] = evidence.item_fingerprint(candidate)
        evidence.seal_evidence(candidate, policy)
        assert evidence.evidence_matches_review_row(row, candidate)
        local = {**candidate, "classification": {"label": "confirm_me", "confidence": 0.95}}
        assert evidence.automatic_keep_supported([row], [local])
        assert not evidence.automatic_keep_supported([row], [local, candidate])
        for invalid_confidence in (True, False, None, "0.99", float("nan"), float("inf"), -1, 2):
            for label, check in (("confirm_me", evidence.automatic_keep_supported),
                                 ("confirm_remote_duplicate", evidence.automatic_drop_supported)):
                invalid = {**candidate, "classification": {"label": label, "confidence": invalid_confidence}}
                assert not check([row], [invalid]), (label, invalid_confidence)
        assert not evidence.automatic_keep_supported([{**row, "source": "transcript_order"}], [local])
        assert not evidence.automatic_keep_supported([row], [{**local, "clips": {}}])
        assert not evidence.automatic_keep_supported([row], [{**local, "classification_scope": {}}])
        keep_receipt = evidence.suggestion_receipt([row], [local], "keep_me")
        assert keep_receipt["resolved_scope"] == "local_voice"
        automatic = {**row, "decision": "keep_me", "review_source": "workspace_suggested_answers",
                     "review_evidence": {"suggestion_receipt": keep_receipt}}
        assert evidence.effective_decision(automatic) == "keep_me"
        for facet in ({"source": "transcript_text"}, {"review_lane": "check_transcript_text"},
                      {"review_features": {"interval_ownership_review": {"status": "needs_review"}}}):
            assert not evidence.automatic_keep_supported([{**row, **facet}], [local])
            assert evidence.effective_decision({**automatic, **facet}) == "needs_review"
        assert not evidence.automatic_drop_supported([
            {**row, "review_features": {"interval_ownership_review": {"status": "needs_review"}}}], [candidate])
        for unproven in ({**automatic, "review_evidence": {}}, {**automatic, "review_source": ""}):
            assert evidence.effective_decision(unproven) == "needs_review"
        assert evidence.effective_decision({**row, "decision": "keep_me", "review_source": "manual"}) == "keep_me"
        annotated = {"needs_review": True, "transcript_integrity": {"status": "needs_review"}}
        materializer.annotate_voice_review(annotated, [automatic], "reviewed_v1")
        assert "human_review" not in annotated and annotated["agent_review"]["status"] == "cleared"
        assert annotated["needs_review"] is True
        integrity_list = {"needs_review": True, "transcript_integrity": [
            {"status": "applied"}, {"status": "needs_review", "outcome": "needs_review"}]}
        materializer.annotate_voice_review(integrity_list, [automatic], "reviewed_v1")
        assert integrity_list["needs_review"] is True
        clean = {"needs_review": True}
        materializer.annotate_voice_review(clean, [automatic], "reviewed_v1")
        assert clean["needs_review"] is False
        materializer.annotate_voice_review(clean, [{**automatic, "review_source": ""}], "reviewed_v1")
        assert clean["needs_review"] is True and clean["review_evidence"]["origins"] == ["legacy_unknown"]
        order_target = {"quality": {"needs_review": True, "transcript_integrity": {"status": "needs_review"}}}
        materializer.add_review_quality(order_target, "transcript_order_review", [automatic], "reviewed_v1")
        assert order_target["quality"]["needs_review"] is True
        # Adjacent remote turns explain a phrase that a single-pair guard called unique.
        unique_row = {**row, "label": "remote_duplicate", "verdict": "probable_transcript_error",
                      "review_lane": "check_unique_me_content", "review_features": {"me_overlap_coverage": 0.2},
                      "text": [{**utterance, "text": "several distinctive technical words remain"}]}
        assert lane.text_guard_keep_decision([unique_row], None)[0] == "keep_me"
        assert lane.suggested_decision_for_group([unique_row], {}, {})[0] == "needs_review"
        write(dialogue, {"utterances": [utterance,
              {"role": "remote", "text": "several distinctive technical", "start": 8, "end": 10},
              {"role": "remote", "text": "words remain", "start": 12, "end": 14},
              {"role": "remote", "text": "unrelated distant words", "start": 100, "end": 102}]})
        assert lane.text_guard_keep_decision([unique_row], None)[0] is None
        assert "distant" not in evidence.neighboring_remote_text(unique_row)
        write(dialogue, {"utterances": [utterance]})
        target_item = {**candidate, "id": "arp_old"}
        assert target_me.evidence_rows_by_item_id([target_item], [candidate])
        extra_me = copy.deepcopy(candidate)
        extra_me["utterances"].append({**utterance, "id": "other", "start": 8, "end": 9})
        extra_me["source_pack_item_fingerprint"] = evidence.item_fingerprint(extra_me)
        assert not evidence.evidence_matches_review_row(row, extra_me)
        assert lane.suggested_decision_for_group([row], {"fixture": [candidate]}, {})[0] == "drop_me"
        # Heuristic noise hints and even freshly sealed empty receipts are not
        # independent audio evidence. This reproduces the 2026-09-24 failure.
        noise = {**row, "label": "asr_noise", "confidence": 0.78, "suggested_decision": "drop_me",
                 "suggested_decision_confidence": "medium", "suggested_decision_reason": "confirm by listening"}
        assert lane.suggested_decision_for_group([noise], {}, {})[0] == "needs_review"
        empty_receipt = evidence.suggestion_receipt([noise], [], "drop_me")
        assert not evidence.suggestion_receipt_current(empty_receipt, [noise], [], "drop_me")
        assert not evidence.automatic_drop_supported([row], [{**candidate, "classification_scope": {}}])
        assert not evidence.automatic_drop_supported([row], [{**candidate, "sources": ["mic_clean"]}])
        assert not evidence.automatic_drop_supported([row], [{**candidate, "classification": {
            "label": "confirm_asr_noise", "confidence": 0.99}}])
        assert not evidence.automatic_drop_supported([row], [candidate, {**candidate, "classification": {
            "label": "target_me_confirmed", "confidence": 0.94}}])
        weak_duplicate = {**candidate, "classification": {"label": "confirm_remote_duplicate", "confidence": 0.80}}
        voice_absent = {**candidate, "classification": {"label": "target_me_absent_remote_like", "confidence": 0.90}}
        assert not evidence.automatic_drop_supported([row], [weak_duplicate])
        assert evidence.automatic_drop_supported([row], [weak_duplicate, voice_absent])
        instability = {"unstable_micro_asr_success": True,
                       "micro_asr_selection_review_reasons": ["baseline_only_selection_without_canonical_support"]}
        audited = audio_audit.audit_item({**candidate, "clips": {}, "review_features": instability})
        compact = readiness.compact_review_item({"session_id": "fixture"}, audited)
        assert all(compact["review_features"].get(key) == value for key, value in instability.items())
        for field, value in (("text", "old unrelated phrase"), ("role", "remote"), ("start", 10.1), ("end", 12.1)):
            stale = copy.deepcopy(candidate)
            stale["utterances"][0][field] = value
            stale["source_pack_item_id"] = "arp_1"
            stale["source_pack_item_fingerprint"] = evidence.item_fingerprint(stale)
            assert not lane.stronger_matches_for_row(row, {"fixture": [stale]}), field
        invalid = {**candidate, "source_pack_item_fingerprint": "stale"}
        assert not evidence.evidence_matches_review_row(row, invalid)
        receipt = evidence.suggestion_receipt([row], [candidate], "drop_me")
        assert evidence.suggestion_receipt_current(receipt, [row], [candidate], "drop_me")
        assert not evidence.suggestion_receipt_current(receipt, [row], [candidate], "keep_me")
        write(dialogue, {"utterances": [{**utterance, "text": "changed after preview"}]})
        assert not evidence.suggestion_receipt_current(receipt, [row], [candidate], "drop_me")
        assert not evidence.evidence_matches_review_row(row, candidate)
        write(dialogue, {"utterances": [utterance]})
        source.write_bytes(b"audio-two")
        assert not evidence.evidence_matches_review_row(row, candidate)
        assert not target_me.evidence_rows_by_item_id([target_item], [candidate])
        source.write_bytes(b"audio-one")
        write(policy, {"version": 2})
        assert not evidence.evidence_matches_review_row(row, candidate)
        write(policy, {"version": 1})
        assert evidence.evidence_matches_review_row(row, candidate)

        # A split is not a renumbering, even when the old ID survives.
        split = [{**utterance, "end": 11.0}, {**utterance, "id": "utt_next", "start": 11.0}]
        write(dialogue, {"utterances": split})
        assert not evidence.evidence_matches_review_row(row, candidate)
        write(dialogue, {"utterances": [utterance]})
        assert not lane.requires_materialized_local_recall([{**row, "label": "lost_me"}])
        assert lane.requires_materialized_local_recall([{**row, "source": "local_recall", "label": "lost_me"}])

        second = {**utterance, "id": "utt_next", "start": 15, "end": 17}
        write(dialogue, {"utterances": [utterance, second]})
        next_row = {**row, "text": [second], "source_audit_id": "arp_2", "interval": {"start": 15, "end": 17},
                    "utterance_ids": ["utt_next"], "me_utterance_ids": ["utt_next"]}
        weak = {**candidate, "id": "fwj_2", "utterances": [second], "interval": next_row["interval"],
                "classification": {"label": "confirm_remote_duplicate", "confidence": 0.79}}
        weak["source_pack_item_fingerprint"] = evidence.item_fingerprint(weak)
        assert lane.stronger_suggested_decision([row, next_row], {"fixture": [candidate, weak]})[0] is None
        write(dialogue, {"utterances": [utterance]})

        manifest = session / "lane.json"
        answers = session / "answers.txt"
        answers.write_text("d\n")
        item = {"source_audit_id": "arp_1", "review_row_key": apply.review_row_key(row),
                "suggested_decision": "drop_me", "stronger_audio_judge": {"matches": [candidate]},
                "suggestion_receipt": evidence.suggestion_receipt([row], [candidate], "drop_me")}
        write(manifest, {"items": [item]})
        lane_input = {"lane": "classify_audio", "manifest": str(manifest), "answer_sheet": str(answers)}
        unproven = {**item, "stronger_audio_judge": {"matches": []},
                    "suggestion_receipt": evidence.suggestion_receipt([row], [], "drop_me")}
        write(manifest, {"items": [unproven]})
        rows = [copy.deepcopy(row)]
        result = apply.apply_lane(lane_input, rows, apply.row_lookup(rows), "fixture", "test", "suggested")
        assert result["rejected"] and rows[0]["decision"] == "todo", result
        write(manifest, {"items": [item]})
        rows = [copy.deepcopy(row)]
        result = apply.apply_lane(lane_input, rows, apply.row_lookup(rows), "fixture", "test", "suggested")
        assert not result["rejected"], result
        assert rows[0]["decision"] == "drop_me"
        decisions_path = session / "derived/readiness/review-plan/review_decisions.jsonl"
        judge.write_jsonl(decisions_path, rows)
        judge.write_jsonl(session / "derived/audit/audio-review-pack/faster_whisper_judge.jsonl", [candidate])
        before = evidence.file_identity(decisions_path)
        audit_result = decision_audit.audit(session)
        assert audit_result["decisions"] == 1 and audit_result["missing_semantic_evidence"] == 0
        assert evidence.file_identity(decisions_path) == before
        source.write_bytes(b"audio-two")
        rows = [copy.deepcopy(row)]
        result = apply.apply_lane(lane_input, rows, apply.row_lookup(rows), "fixture", "test", "suggested")
        assert result["rejected"][0]["reason"] == "stale_or_missing_suggestion_receipt", result
        assert rows[0]["decision"] == "todo"
        source.write_bytes(b"audio-one")

        # The direct lane command uses the same receipt rules as workspace apply.
        template_path = session / "single-template.jsonl"
        single_output = session / "single-decisions.jsonl"
        judge.write_jsonl(template_path, [row])
        old_argv = sys.argv
        try:
            for mutate, code in ((False, 0), (True, 1)):
                if mutate:
                    source.write_bytes(b"audio-two")
                sys.argv = ["apply-lane", str(manifest), "--template", str(template_path), "--out", str(single_output),
                            "--answers", "d", "--answers-source", "suggested", "--dry-run"]
                with contextlib.redirect_stdout(io.StringIO()):
                    assert apply_single.main() == code
                assert not single_output.exists()
                single_report = judge.read_json(session / "review_lane_pack_apply_report.json")
                assert single_report["summary"]["rejected_count"] == int(mutate), single_report
                if mutate:
                    assert single_report["recommended_next"].startswith("$EDITOR ")
                    assert ".suggested.txt" not in single_report["recommended_next"]
                    assert "--answers-source suggested" not in single_report["next_commands"][-1]["command"]
                else:
                    assert "--answers-source suggested" in single_report["recommended_next"]
        finally:
            sys.argv = old_argv
            source.write_bytes(b"audio-one")

        voice = {**candidate, "classification": {"label": "target_me_confirmed", "confidence": 0.94},
                 "impact": {"category": "new_keep_evidence"}}
        lexical_row = {**row, "review_lane": "check_transcript_text"}
        assert lane.suggested_decision_for_group([lexical_row], {}, {"fixture": [voice]})[0] == "needs_review"
        impact = target_me.classify_target_me_impact({"label": "target_me_absent_remote_like", "existing_evidence": {
            "audio_review_label": "uncertain", "stronger_audio_judge_label": "confirm_asr_noise", "stronger_audio_judge_confidence": 0.82}})
        assert impact["category"] == "new_drop_evidence", impact

        aligned = judge.scoped_transcripts({"clip_interval": {"start": 7}, "utterances": [utterance]}, {
            "mic_clean": {"text": "previous current after", "segments": [{"words": [
                {"start": 0, "end": 1, "word": "previous"}, {"start": 3.2, "end": 4, "word": "current"},
                {"start": 6, "end": 7, "word": "after"}]}]}})
        assert aligned["mic_clean"]["text"] == "current", aligned
        boundary = judge.scoped_transcripts({"clip_interval": {"start": 7}, "utterances": [utterance]}, {
            "mic_clean": {"text": "previous and current", "segments": [{"start": 1, "end": 5, "text": "previous and current"}]}})
        assert boundary["mic_clean"]["scope_status"] == "ambiguous_segment_boundary"
        classified = judge.classify_item({"utterances": [utterance]}, {}, boundary,
                                        judge.source_metrics(boundary, utterance["text"], ""))
        assert classified["label"] == "uncertain", classified
        # A padded decode with words is usable even when its ASR segment spans
        # neighboring phrases; neither neighbor may authorize this Me row.
        assert aligned["mic_clean"]["scope_status"] == "word_bounded"
        adjacent = judge.scoped_transcripts({"clip_interval": {"start": 7}, "utterances": [utterance]}, {
            "mic_clean": {"text": "previous after", "segments": [{"words": [
                {"start": 1, "end": 3, "word": "previous"}, {"start": 5, "end": 7, "word": "after"}]}]}})
        assert adjacent["mic_clean"]["text"] == ""
        # The same decoded phrase in both streams is not independent Me evidence,
        # even when speaker-state claims full double-talk activity.
        spoken = "several clear words in both audio streams"
        duplicate_transcripts = {name: {"text": spoken, "scope_status": "word_bounded"}
                                for name in ("mic_raw", "mic_clean", "mic_role_masked", "remote")}
        duplicate_item = {"utterances": [{**utterance, "text": spoken},
                          {**utterance, "id": "remote", "role": "remote", "source_track": "remote", "text": spoken}]}
        classification = judge.classify_item(
            duplicate_item, {}, duplicate_transcripts, judge.source_metrics(duplicate_transcripts, spoken, spoken),
            {"coverage_ratio": 1.0, "local_active_ratio": 1.0, "double_talk_ratio": 1.0},
        )
        assert classification["label"] == "uncertain", classification

        # Drive the actual judge orchestrator through cold, warm and cached-only
        # runs with a fake decoder, retaining real content identities on disk.
        model = session / "model"
        model.mkdir()
        (model / "model.bin").write_bytes(b"model-one")
        pack = session / "pack"
        pack.mkdir()
        pack_item = {**candidate, "id": "arp_1", "profile": "reviewed_v1", "utterances": [utterance],
                     "utterance_ids": ["utt_new"], "source_reasons": ["review_plan:check_transcript_text"],
                     "clip_interval": {"start": 10, "end": 12},
                     "clips": {"mic_clean": str(source), "remote": str(remote_source)}}
        pack_item.pop("source_pack_item_fingerprint", None)
        judge.write_jsonl(pack / "review_pack_items.jsonl", [pack_item])
        audit = {**pack_item, "classification": {"label": "uncertain", "verdict": "needs_stronger_audio_judge"}}
        judge.write_jsonl(pack / "audio_review_audit.jsonl", [audit])
        loads, decodes = [], []
        interrupted_paths = set()

        class Model:
            def transcribe(self, path, **kwargs):
                if path in interrupted_paths:
                    interrupted_paths.remove(path)
                    raise KeyboardInterrupt
                decodes.append(path)
                segment = SimpleNamespace(start=0, end=2, text="current local phrase", avg_logprob=-0.1, no_speech_prob=0.0, words=[])
                return iter([] if path == str(remote_source) else [segment]), SimpleNamespace(language="en", language_probability=1.0)

        original_load = judge.load_model
        judge.load_model = lambda *_: loads.append(True) or Model()
        old_argv = sys.argv
        try:
            def run(*flags, expected_code=0):
                sys.argv = ["judge", str(session), "--pack-dir", str(pack), "--model", str(model), "--no-progress", *flags]
                with contextlib.redirect_stdout(io.StringIO()):
                    assert judge.main() == expected_code
                return judge.read_json(pack / "faster_whisper_judge_summary.json")

            cold = run("--quick")
            assert cold["status"] == "completed" and len(loads) == 1 and len(decodes) == 2, cold
            current_evidence = judge.read_jsonl(pack / "faster_whisper_judge.jsonl")[0]
            assert evidence.evidence_files_current(current_evidence)
            state_path = session / "derived/preprocess/echo/speaker_state.jsonl"
            judge.write_jsonl(state_path, [{"start": 10, "end": 12, "state": "local_only"}])
            assert not evidence.evidence_files_current(current_evidence)
            warm = run("--quick", "--cached-only")
            assert warm["cached_items"] == 1 and warm["computed_items"] == 0 and len(decodes) == 2, warm
            current_evidence = judge.read_jsonl(pack / "faster_whisper_judge.jsonl")[0]
            assert evidence.evidence_files_current(current_evidence)
            judge.write_jsonl(pack / "audio_review_audit.jsonl", [{**audit, "scores": {"test_changed": True}}])
            assert not evidence.evidence_files_current(current_evidence)
            adaptive = run("--adaptive-sources", "--cached-only")
            assert adaptive["status"] == "completed" and adaptive["selected_coverage"][0]["required_sources"] == list(judge.QUICK_SOURCES), adaptive
            full = run("--cached-only")
            assert full["status"] == "completed_partial", full
            assert full["items"] == 1 and full["computed_items"] == 0, full
            assert len(loads) == 1 and len(decodes) == 2
            (model / "model.bin").write_bytes(b"model-two")
            stale = run("--quick", "--cached-only")
            assert stale["status"] == "completed_partial" and stale["items"] == 0 and stale["historical_items"] >= 1, stale
            (model / "model.bin").unlink()
            unavailable = run("--quick", "--cached-only")
            assert unavailable["status"] == "skipped" and unavailable["items"] == 0, unavailable
            assert len(loads) == 1 and len(decodes) == 2

            # An interruption preserves completed items and resume only decodes
            # the unfinished clip, even after stale classification was archived.
            (model / "model.bin").write_bytes(b"model-one")
            extra_clip = session / "extra.wav"
            extra_clip.write_bytes(b"different-audio")
            extra_item = {**pack_item, "id": "arp_2", "clips": {**pack_item["clips"], "mic_clean": str(extra_clip)}}
            judge.write_jsonl(pack / "review_pack_items.jsonl", [pack_item, extra_item])
            judge.write_jsonl(pack / "audio_review_audit.jsonl", [audit, {**audit, "id": "arp_2"}])
            interrupted_paths.add(str(extra_clip))
            interrupted = run("--quick", "--word-timestamps", expected_code=130)
            assert interrupted["status"] == "interrupted_checkpointed" and interrupted["items"] == 1, interrupted
            assert len(decodes) == 4, "unaligned cache must not stand in for a word-timestamp decode"
            resume = shlex.split(interrupted["resume_command"])
            assert resume[:3] == ["murmurmark", "audit", "stronger-audio-judge"]
            assert resume[resume.index("--pack-dir") + 1] == str(pack)
            assert resume[resume.index("--model") + 1] == str(model)
            assert "--quick" in resume
            assert "--word-timestamps" in resume
            resumed = run("--quick", "--word-timestamps")
            assert resumed["cached_items"] == 1 and resumed["computed_items"] == 1 and resumed["items"] == 2, resumed
            count = len(decodes)
            warm = run("--quick", "--word-timestamps", "--cached-only")
            assert warm["cached_items"] == 2 and warm["computed_items"] == 0 and len(decodes) == count, warm

            # A quick pass cannot reuse stale extra sources from an older full pass.
            saved = judge.read_jsonl(pack / "faster_whisper_judge.jsonl")
            expanded = {**saved[0], "sources": [*judge.QUICK_SOURCES, "mic_raw"],
                        "transcripts": {**saved[0]["transcripts"], "mic_raw": saved[0]["transcripts"]["mic_clean"]}}
            cache = judge.DecodeCache(pack, model, judge.parse_args())
            expanded_item = {**pack_item, "clips": {**pack_item["clips"], "mic_raw": str(remote_source)}}
            assert not judge.cached_row_matches_item(expanded, expanded_item, judge.QUICK_SOURCES, cache)

            # An interrupted run must checkpoint refreshed verdicts, not copy
            # old classifications and give them current provenance.
            saved[0]["classification"] = {"label": "obsolete_verdict", "confidence": 1.0}
            judge.write_jsonl(pack / "faster_whisper_judge.jsonl", saved)
            pending_clip = session / "pending.wav"
            pending_clip.write_bytes(b"pending-audio")
            pending_item = {**pack_item, "id": "arp_3", "clips": {**pack_item["clips"], "mic_clean": str(pending_clip)}}
            judge.write_jsonl(pack / "review_pack_items.jsonl", [pack_item, extra_item, pending_item])
            judge.write_jsonl(pack / "audio_review_audit.jsonl", [audit, {**audit, "id": "arp_2"}, {**audit, "id": "arp_3"}])
            interrupted_paths.add(str(pending_clip))
            interrupted = run("--quick", "--word-timestamps", expected_code=130)
            assert interrupted["cached_items"] == 2 and interrupted["computed_items"] == 0, interrupted
            checkpoint = judge.read_jsonl(pack / "faster_whisper_judge.jsonl")
            assert all(row["classification"]["label"] != "obsolete_verdict" for row in checkpoint)
            assert all(evidence.evidence_files_current(row) for row in checkpoint)
            resumed = run("--quick", "--word-timestamps")
            assert resumed["cached_items"] == 2 and resumed["computed_items"] == 1, resumed
            judge.write_jsonl(pack / "review_pack_items.jsonl", [])
            empty = run("--quick", "--cached-only")
            assert empty["items"] == 0 and empty["historical_items"] >= 2, empty
        finally:
            sys.argv = old_argv
            judge.load_model = original_load

        audit_rows = [
            {"id": f"error_{index}", "interval": {"start": start, "end": end}, "utterances": [utterance],
             "classification": {"label": label, "verdict": "probable_transcript_error"}}
            for index, (label, start, end) in enumerate((
                ("lost_me", 10, 30), ("remote_leak", 40, 43), ("remote_duplicate", 45, 48),
                ("remote_duplicate", 41, 42),
            ))
        ]
        judge.write_jsonl(session / "derived/audit/audio-review-pack/audio_review_audit.jsonl", audit_rows)
        typed = quality.audio_review_metrics({}, session, "reviewed_v1", {})
        assert typed["audio_review_probable_error_seconds"] == 26, typed
        assert typed["audio_review_remote_error_seconds"] == 6, typed
        assert outcome.harmful_remote_evidence({**typed, "remote_forbidden_status": "ok"})["status"] == "review"
        existing_plan = {"use_gate": "review_first", "audio_review_remote_leak_probable_error_count": 1,
                         "remote_leak_segment_plan_status": "ok"}
        assert not any(row["id"] == "plan_remote_leak_segment_repair" for row in quality.readiness_next_commands(session, existing_plan))

    metrics = {"audio_review_probable_error_seconds": 50, "audio_review_remote_leak_probable_error_seconds": 1.79,
               "remote_forbidden_status": "skipped"}
    assert outcome.harmful_remote_evidence(metrics)["seconds"] == 1.79
    assert outcome.harmful_remote_evidence({"audio_review_probable_error_seconds": 50})["status"] == "unknown"
    print("review audio evidence cross-module checks passed")


if __name__ == "__main__":
    main()
