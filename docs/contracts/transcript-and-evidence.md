# Transcript and Evidence Contracts

The transcript JSON is the source of truth. Markdown is an export.

## Current MVP Contract

The implemented CLI MVP currently writes a simpler transcript package under:

```text
derived/transcript-simple/whisper-cpp/resolved/
```

Important files:

```text
raw_segments.json
candidate_utterances.json
role_decisions.json
clean_dialogue.json
overlaps.json
quality_report.json
timeline_repair_report.json
timeline_repair_examples.jsonl
timeline_audit_examples.jsonl
corrections.jsonl
transcript.simple.json
transcript.md
```

When `--repair-profile shadow_v2` is used, the same stage writes separate candidate artifacts:

```text
clean_dialogue.shadow_v2.json
quality_report.shadow_v2.json
timeline_repair_report.shadow_v2.json
timeline_repair_examples.shadow_v2.jsonl
timeline_audit_examples.shadow_v2.jsonl
opening_repair_report.shadow_v2.json
opening_candidates.shadow_v2.jsonl
opening_micro_asr_runs.shadow_v2.jsonl
opening_patch.shadow_v2.json
transcript.simple.shadow_v2.json
transcript.shadow_v2.md
repair_comparison.json
```

Current source-of-truth order:

1. `clean_dialogue*.json` for final readable turns.
2. `role_decisions*.json` for why a candidate was kept, dropped, split or repaired.
3. `quality_report*.json` and `overlaps*.json` for risk review.
4. `transcript*.md` only as a human-readable export.

The current extractive synthesis spike writes:

```text
derived/synthesis-simple/extractive/
  synthesis_manifest.json
  quality_verdict.json
  quality_verdict.md
  notes.md
  evidence_notes.json
  review_items.jsonl
```

`quality_verdict.json` is a local quality gate:

```json
{
  "schema": "murmurmark.quality_verdict/v1",
  "verdict": "usable_with_review",
  "selected_transcript_profile": "shadow_v2",
  "inputs": {
    "clean_dialogue": "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json",
    "quality_report": "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json",
    "overlaps": "derived/transcript-simple/whisper-cpp/resolved/overlaps.shadow_v2.json",
    "repair_comparison": "derived/transcript-simple/whisper-cpp/resolved/repair_comparison.json"
  },
  "metrics": {
    "utterances": 120,
    "needs_review_count": 4,
    "cross_role_overlap_gt2_count": 2,
    "remote_duplicate_in_me_seconds": 8.4
  },
  "risk_items": [
    {
      "type": "needs_review_ratio",
      "severity": "medium",
      "reason": "some transcript regions need review"
    }
  ]
}
```

`notes.md` is not a free summary. It contains selected extractive outline items and top
decisions/actions/risks/open questions with utterance IDs. Every item that looks like a decision or
action remains `needs_review` until a human confirms it. The complete scored candidate list lives in
`evidence_notes.json`. Since generator `0.3.0`, meeting facilitation and process phrases such as
`let's move to the next block` or `let's vote` are kept as hidden candidates in JSON, but are not
shown as decisions or actions in Markdown.

`evidence_notes.json` v2:

```json
{
  "schema": "murmurmark.evidence_notes/v2",
  "source": {
    "transcript_profile": "shadow_v2",
    "clean_dialogue_path": "derived/transcript-simple/whisper-cpp/resolved/clean_dialogue.shadow_v2.json",
    "quality_report_path": "derived/transcript-simple/whisper-cpp/resolved/quality_report.shadow_v2.json"
  },
  "generator": {
    "name": "synthesize-simple-extractive",
    "version": "0.3.0",
    "mode": "deterministic",
    "config": "default_v3"
  },
  "topic_blocks": [],
  "candidates": [],
  "selected": {
    "outline_blocks": [],
    "decisions": [],
    "actions": [],
    "risks": [],
    "open_questions": []
  },
  "review": {
    "items": [],
    "summary": {}
  },
  "metrics": {}
}
```

Candidate items are extractive and auditable:

```json
{
  "id": "cand_action_0012",
  "type": "action",
  "subtype": "candidate_action",
  "status": "selected",
  "score": 68,
  "confidence": "medium",
  "display_text": "Надо проверить, почему deploy в GitLab pipeline тормозит.",
  "evidence_utterance_ids": ["utt_0123"],
  "context_utterance_ids": ["utt_0122", "utt_0124"],
  "topic_block_id": "topic_0003",
  "time": {"start": 812.42, "end": 818.77},
  "roles": ["Me"],
  "features": {
    "markers": ["надо"],
    "verbs": ["проверить"],
    "objects": ["deploy", "GitLab pipeline"],
    "domain_terms": ["deploy", "GitLab"],
    "quality_flags": []
  },
  "reasons": ["obligation marker: надо", "action verb: проверить"],
  "penalties": [],
  "needs_review": true
}
```

Topic blocks are time ranges with representative utterances chosen by salience:

```json
{
  "id": "topic_0003",
  "start": 720.12,
  "end": 1198.44,
  "title": "12:00-19:58: gitlab, deploy, pipeline",
  "utterance_range": {"first_id": "utt_0101", "last_id": "utt_0174", "count": 74},
  "keywords": ["gitlab", "deploy", "pipeline"],
  "representatives": [
    {
      "utterance_id": "utt_0123",
      "role": "Me",
      "text": "Надо проверить, почему deploy в GitLab pipeline тормозит.",
      "salience_score": 86
    }
  ]
}
```

The `selected` section is the Markdown view source. It keeps only top items:

```json
{
  "selected": {
    "actions": [
      {
        "id": "cand_action_0012",
        "subtype": "candidate_action",
        "score": 68,
        "evidence_utterance_ids": ["utt_0123"],
        "display_text": "Надо проверить, почему deploy в GitLab pipeline тормозит.",
        "needs_review": true
      }
    ],
    "decisions": [],
    "risks": [],
    "open_questions": []
  }
}
```

Review items point either to risky utterances or intervals:

```json
{
  "type": "quality_needs_review",
  "severity": "medium",
  "start": 812.42,
  "end": 818.77,
  "utterance_ids": ["utt_0123"],
  "reason": "utterance marked needs_review by transcript pipeline",
  "text": "Надо проверить, почему deploy в GitLab pipeline тормозит."
}
```

The group-call overlap audit is diagnostic and writes only under:

```text
derived/audit/group-overlaps/
  group_overlap_audit.jsonl
  group_overlap_summary.json
  group_overlap_review.md
  group_overlap_patch_suggestions.jsonl
  clips/
```

`group_overlap_summary.json` v1:

```json
{
  "schema": "murmurmark.group_overlap_summary/v1",
  "profile": "shadow_v2",
  "input_metrics": {
    "cross_role_overlap_gt2_count": 25,
    "cross_role_overlap_gt2_seconds": 144.09,
    "remote_duplicate_in_me_seconds": 192.85
  },
  "classified": {
    "total_overlap_count": 93,
    "total_overlap_seconds": 201.34,
    "by_label": {
      "probable_duplicate": {"count": 6, "seconds": 11.77},
      "probable_timing_overlap": {"count": 38, "seconds": 81.55},
      "needs_human_review": {"count": 47, "seconds": 104.84}
    }
  },
  "harmful": {
    "seconds": 11.77,
    "labels": ["probable_asr_noise", "probable_duplicate", "probable_remote_leak"]
  },
  "benign_or_expected": {
    "seconds": 84.73,
    "labels": ["probable_double_talk", "probable_timing_overlap"]
  },
  "review": {"seconds": 104.84, "count": 47},
  "recommended_verdict_adjustment": {
    "old": "usable_with_review",
    "new": "usable_with_review",
    "informational_only": true
  }
}
```

`group_overlap_audit.jsonl` v1 contains one record per `Me`/`Colleagues` overlap:

```json
{
  "schema": "murmurmark.group_overlap_audit/v1",
  "id": "ov_000042",
  "profile": "shadow_v2",
  "interval": {
    "start": 812.42,
    "end": 818.77,
    "duration_sec": 6.35,
    "severity": "critical"
  },
  "utterances": {
    "me": {"id": "utt_mic_0123", "text": "Да, надо проверить deploy."},
    "remote": {"id": "utt_remote_0119", "text": "Надо проверить deploy."}
  },
  "features": {
    "speaker_state": {},
    "audio": {},
    "text": {},
    "interval": {}
  },
  "scores": {
    "local_evidence": 22,
    "audio_leak": 71,
    "text_duplicate": 84,
    "probable_duplicate": 89
  },
  "classification": {
    "label": "probable_duplicate",
    "confidence": 0.89,
    "action_suggestion": "drop_me_duplicate"
  },
  "clips": {
    "stereo_clean_left_remote_right": "derived/audit/group-overlaps/clips/ov_000042_stereo_clean_left_remote_right.wav"
  }
}
```

Allowed group-overlap labels:

- `probable_duplicate`
- `probable_remote_leak`
- `probable_double_talk`
- `probable_timing_overlap`
- `probable_asr_noise`
- `needs_human_review`

The local recall audit explains low timeline-repair `local_only_island_recall`. It is audit-only and
writes under:

```text
derived/audit/local-recall/
  local_recall_audit.json
  local_recall_items.jsonl
  local_recall_review.md
```

`local_recall_audit.json` uses `murmurmark.local_recall_audit/v1`:

```json
{
  "schema": "murmurmark.local_recall_audit/v1",
  "profile": "shadow_v2",
  "status": "ok",
  "summary": {
    "audited_missing_island_count": 5,
    "possible_lost_me_seconds": 0.0,
    "needs_review_seconds": 0.7,
    "blocking_low_local_recall": false,
    "recommended_next_step": "local_recall_risk_explained"
  }
}
```

`local_recall_items.jsonl` uses `murmurmark.local_recall_item/v1`. Labels are:

- `possible_lost_me`
- `needs_review`
- `likely_harmless_short`
- `likely_harmless_weak_audio`
- `likely_harmless_remote_guard`

The audit reads timeline-repair examples and Echo Guard `speaker_state.jsonl`; it does not read or
modify raw capture. Session quality gates may ignore low local recall only when this audit says
`blocking_low_local_recall: false`. Possible lost local speech remains a blocking risk.

The audio review pack is the local handoff format for agent-driven audio checks. It is audit-only
and writes under:

```text
derived/audit/audio-review-pack/
  review_pack_manifest.json
  review_pack_summary.json
  review_pack_items.jsonl
  review_pack.md
  audio_review_audit.jsonl
  audio_review_summary.json
  audio_review_report.md
  clips/
```

`review_pack_items.jsonl` contains suspicious regions collected from `needs_review`, transcript
overlaps, group overlap audit and audit-cleanup rejections:

```json
{
  "schema": "murmurmark.audio_review_pack_item/v1",
  "id": "arp_000042",
  "session_id": "2026-06-26_14-01-21",
  "profile": "audit_cleanup_v1",
  "source_reasons": ["group_overlap:needs_human_review", "cross_role_overlap"],
  "priority_score": 88.4,
  "interval": {
    "start": 812.42,
    "end": 818.77,
    "duration_sec": 6.35,
    "start_time": "13:32",
    "end_time": "13:38"
  },
  "utterance_ids": ["utt_0123", "utt_0124"],
  "utterances": [
    {"id": "utt_0123", "role": "Me", "text": "Да, я проверю логи.", "needs_review": true},
    {"id": "utt_0124", "role": "Colleagues", "text": "Давайте посмотрим deploy.", "needs_review": false}
  ],
  "clips": {
    "mic_raw": "derived/audit/audio-review-pack/clips/arp_000042_mic_raw.wav",
    "remote": "derived/audit/audio-review-pack/clips/arp_000042_remote.wav",
    "mic_clean": "derived/audit/audio-review-pack/clips/arp_000042_mic_clean.wav",
    "mic_role_masked": "derived/audit/audio-review-pack/clips/arp_000042_mic_role_masked.wav",
    "stereo_clean_left_remote_right": "derived/audit/audio-review-pack/clips/arp_000042_stereo_clean_left_remote_right.wav"
  }
}
```

`audio_review_summary.json` is the local metric audit over the pack:

```json
{
  "schema": "murmurmark.audio_review_summary/v1",
  "items": 37,
  "by_label": {
    "remote_duplicate": {"count": 4, "seconds": 12.8},
    "double_talk": {"count": 11, "seconds": 42.6},
    "uncertain": {"count": 7, "seconds": 31.4}
  },
  "by_verdict": {
    "likely_reliable": {"count": 18, "seconds": 68.2},
    "probable_transcript_error": {"count": 8, "seconds": 22.9},
    "needs_stronger_audio_judge": {"count": 11, "seconds": 48.7}
  },
  "recommended_next_step": "send_uncertain_clips_to_stronger_local_audio_judge"
}
```

The local audio review does not apply patches. Later stronger local audio judges should consume the
same `review_pack_items.jsonl` and write their own verdicts instead of re-cutting clips.
`likely_reliable` can be emitted with lower confidence when the best local metric class is already
`likely_reliable`, the score is at least `65`, and the nearest competing error class is at least
`10` points lower. This only lowers review priority; it is not an automatic transcript edit.
`likely_reliable` can also be emitted for benign ties when `double_talk`, `timing_overlap` and/or
local reliability are the strongest classes and all error classes stay below `60`. This avoids
escalating expected group-call timing overlap to a stronger judge.

Cross-session regression corpus is generated from audio review audits:

```text
sessions/_reports/regression-corpus/
  regression_corpus_manifest.json
  regression_corpus_summary.json
  regression_corpus_items.jsonl
  regression_corpus.md
  regression_corpus_evaluation.json
  regression_corpus_evaluation_items.jsonl
  regression_corpus_evaluation.md
  clips/
```

`regression_corpus_items.jsonl` rows use `murmurmark.regression_corpus_item/v1`:

```json
{
  "schema": "murmurmark.regression_corpus_item/v1",
  "id": "rc_000001",
  "session_id": "2026-06-26_11-15-50",
  "source_audit_id": "arp_000042",
  "profile": "audit_cleanup_v2",
  "label": "remote_duplicate",
  "audio_review_label": "remote_duplicate",
  "label_evidence": ["audio_review:remote_duplicate"],
  "verdict": "probable_transcript_error",
  "confidence": 0.94,
  "target_use": ["cleanup_regression", "auto_drop_gate"],
  "utterance_ids": ["utt_000123", "utt_000124"],
  "clips": {
    "mic_raw": "sessions/_reports/regression-corpus/clips/2026-06-26_11-15-50/rc_000001_mic_raw.wav"
  }
}
```

The corpus is private generated data. It is for regression tests, agent review and future local
audio-judge development. It must not become a tracked fixture when it contains real meeting audio.

`regression_corpus_evaluation.json` uses `murmurmark.regression_corpus_evaluation/v1`. It reports
readiness buckets:

```text
silver_cleanup_positive
silver_keep_negative
weak_cleanup_positive
mark_only_regression
needs_audio_judge
```

`label` is the corpus class used for regression selection. `audio_review_label` preserves the raw
local audio-review classifier output. They can differ: for example a row that audio review considers
`likely_reliable` can still be a useful `timing_overlap` false-positive guard when the source group
overlap audit marked it as `probable_timing_overlap`.

Readiness buckets are not human truth labels. They are silver labels derived from current local
metrics and are suitable for no-regression checks and prioritising future audio-judge work.

Audio judge v0 is a local shadow classifier trained on the regression corpus:

```text
sessions/_reports/audio-judge-v0/
  audio_judge_v0_report.json
  audio_judge_v0_predictions.jsonl
  audio_judge_v0_report.md
```

`audio_judge_v0_report.json` uses `murmurmark.audio_judge_v0_report/v1`:

```json
{
  "schema": "murmurmark.audio_judge_v0_report/v1",
  "readiness": "cleanup_shadow_candidate",
  "training": {
    "rows": 102,
    "sessions": 10,
    "label_counts": {
      "drop_error": 18,
      "keep": 18,
      "mark_only_error": 16,
      "uncertain": 50
    }
  },
  "evaluation": {
    "method": "leave_one_session_out",
    "cv_accuracy": 0.901961
  },
  "policy": {
    "mode": "shadow_only",
    "may_modify_transcript": false
  },
  "review_queue": {
    "items": 40,
    "candidate_future_cleanup_items": 27,
    "candidate_mark_only_items": 13,
    "remaining_human_review_items": 40
  }
}
```

The model uses only numeric audio/text metrics, not `label`, `verdict`, readiness bucket, or free-text
content as features. The labels are still silver labels derived from current local metrics, so v0 is a
shadow judge for prioritisation and future cleanup experiments, not a human-quality audio oracle.
`remaining_human_review_items` does not decrease for `drop_error` or `mark_only_error`; those classes
remain review items until a separate cleanup profile applies stricter gates.

When an operational readiness report exists, `train-audio-judge-v0.py` may also write
`audio_judge_v0_queue_predictions.jsonl`:

```json
{
  "schema": "murmurmark.audio_judge_v0_queue_prediction/v1",
  "session_id": "2026-06-26_11-15-50",
  "source_audit_id": "arp_000068",
  "audio_review_label": "remote_duplicate",
  "judge_label": "drop_error",
  "judge_confidence": 0.997627,
  "shadow_action": "candidate_future_cleanup_review"
}
```

These rows are shadow evidence. They do not modify a transcript by themselves.

The post-recording runner writes a per-session execution manifest:

```text
derived/pipeline-run/
  pipeline_run_report.json
derived/readiness/
  session_readiness.json
  session_readiness.md
```

`pipeline_run_report.json` uses `murmurmark.session_pipeline_run/v1`:

```json
{
  "schema": "murmurmark.session_pipeline_run/v1",
  "session": "sessions/2026-06-26_15-32-02",
  "status": "passed",
  "inputs": {
    "model": "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin",
    "language": "ru",
    "prompt_file": "examples/domain-packs/backend-platform/whisper-prompt.ru.txt"
  },
  "outputs": {
    "quality_verdict": "derived/synthesis-simple/extractive/quality_verdict.json",
    "session_readiness": "derived/readiness/session_readiness.json",
    "selected_transcript_profile": "audit_cleanup_v4",
    "verdict": "usable_with_review",
    "use_gate": "review_first"
  },
  "steps": [
    {
      "name": "transcribe_current",
      "status": "passed",
      "started_at": "2026-06-26T22:18:05.434622+00:00",
      "finished_at": "2026-06-26T22:18:42.120000+00:00",
      "duration_sec": 36.685,
      "command": ["python", "scripts/transcribe-simple-whispercpp.py", "sessions/..."]
    }
  ]
}
```

The report is a reproducibility/audit artifact. It does not replace the per-stage reports; it only
records which existing stage commands were run and what final synthesis profile was selected.

`session_readiness.json` uses `murmurmark.session_readiness/v1`:

```json
{
  "schema": "murmurmark.session_readiness/v1",
  "session": "sessions/2026-06-26_15-32-02",
  "use_gate": "review_first",
  "recommendation": "review_flagged_audio_before_using_for_medium_risk_work",
  "selected_profile": "audit_cleanup_v4",
  "verdict": "usable_with_review",
  "metrics": {
    "review_burden_sec": 42.5,
    "review_burden_ratio": 0.031,
    "audio_review_probable_error_count": 2,
    "audio_review_stronger_judge_count": 6,
    "local_only_island_recall": 0.875,
    "local_recall_recommended_next_step": "local_recall_risk_explained"
  },
  "outputs": {
    "transcript": {
      "path": "derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v4.md",
      "exists": true
    },
    "notes": {
      "path": "derived/synthesis-simple/extractive/notes.audit_cleanup_v4.md",
      "exists": true
    }
  }
}
```

`use_gate` is the short per-session answer for practical use:

- `ready_for_notes`: use the notes with normal caution;
- `review_first`: review flagged regions before using the result for medium-risk work;
- `do_not_use_without_manual_review`: do not rely on the transcript without manual checking;
- `pipeline_incomplete`: rerun the full post-recording pipeline first;
- `pipeline_incomplete_review_first`: cleanup/synthesis profiles are missing or not selected yet.

`session_readiness.md` is the human-readable view of the same object and should be opened before
the transcript or notes.

Operational readiness combines the private session quality report and regression corpus evaluation:

```text
sessions/_reports/operational-readiness/
  operational_readiness_report.json
  operational_readiness_report.md
```

`operational_readiness_report.json` uses `murmurmark.operational_readiness_report/v1`:

```json
{
  "schema": "murmurmark.operational_readiness_report/v1",
  "operational_verdict": "pilot_ready_with_review",
  "scope": "local tool for medium-risk working meetings",
  "blockers": [],
  "warnings": ["some_sessions_need_manual_review_before_use"],
  "summary": {
    "session_count": 10,
    "use_gates": {
      "ready_for_notes": 4,
      "review_first": 5,
      "pipeline_incomplete_review_first": 1
    },
    "total_review_burden_ratio": 0.029619,
    "corpus_readiness": "useful_for_audio_judge_v0",
    "audio_judge_readiness": "cleanup_shadow_candidate",
    "audio_judge_cv_accuracy": 0.901961,
    "audio_judge_review_queue": {
      "items": 34,
      "resolved_by_selected_profile_items": 6,
      "remaining_human_review_items": 34
    },
    "review_queue_items": 40
  },
  "promotion_plan": {
    "target": "medium_risk_ready",
    "current_verdict": "pilot_ready_with_review",
    "status": "manual_review_or_algorithmic_cleanup_needed",
    "outstanding_conditions": {
      "sessions_not_ready_for_notes": 6,
      "review_queue_items": 40,
      "review_queue_raw_audio_minutes": 1.81
    },
    "review_queue_strategy": {
      "first_recommended_lane": "fast_confirm_drop",
      "after_first_lane_estimate": {
        "remaining_items": 30,
        "remaining_minutes": 1.28
      },
      "by_lane": [
        {
          "lane": "fast_confirm_drop",
          "items": 10,
          "seconds": 27.87,
          "labels": {
            "remote_duplicate": 9,
            "asr_noise": 1
          }
        }
      ]
    },
    "session_targets": [
      {
        "session_id": "2026-06-26_12-04-04",
        "use_gate": "review_first",
        "review_burden_min": 1.95,
        "recommended_action": "close_review_decisions_or_improve_cleanup"
      }
    ]
  },
  "review_queue": [
    {
      "session_id": "2026-06-26_11-15-50",
      "source_audit_id": "arp_000020",
      "label": "remote_duplicate",
      "verdict": "probable_transcript_error",
      "commands": {
        "stereo_clean_left_remote_right": "afplay sessions/.../clip.wav"
      }
    }
  ]
}
```

The operational verdict is not a transcript correctness proof. It is a use-readiness summary for
piloting MurmurMark on medium-risk meetings with explicit review burden, per-session use gates and a
prioritised review queue.

`promotion_plan` is the bridge from current pilot status to the target state. It names remaining
conditions, sessions that are not yet `ready_for_notes`, review minutes and the next actions needed
to reduce uncertainty. It is report-only: it never edits transcripts or cleanup profiles.
`review_queue_strategy` is also report-only. It groups the remaining review queue into workflow
lanes, recommends the first lane to close, and estimates the remaining queue after that first lane is
reviewed. The estimate is not a substitute for rerunning `apply-review-decisions-batch.py`; it is a
planning aid for reaching `medium_risk_ready`.

`build-review-plan.py` converts the operational review queue into a short working checklist under:

```text
sessions/_reports/review-plan/
  review_plan.json
  review_plan.md
  review_plan_clusters.jsonl
  review_decisions.template.jsonl
```

`review_plan.json` uses `murmurmark.review_plan/v1`:

```json
{
  "schema": "murmurmark.review_plan/v1",
  "summary": {
    "raw_item_count": 40,
    "cluster_count": 29,
    "sessions_with_review": 6,
    "estimated_listen_seconds": 215.52,
    "by_review_action": {
      "confirm_drop_or_keep_me": 21,
      "check_unique_me_content": 13
    },
    "by_review_lane": {
      "fast_confirm_drop": 10,
      "check_unique_me_content": 20
    }
  },
  "review_lanes": {
    "fast_confirm_drop": {
      "title": "Fast confirm drop",
      "item_count": 10,
      "raw_item_seconds": 24.3,
      "description": "Likely leaked remote/ASR noise. Listen once; if it is only non-local speech, accept drop_me."
    }
  },
  "clusters": [
    {
      "id": "review_cluster_0001",
      "session_id": "2026-06-26_11-15-50",
      "severity": "high",
      "start_time": "24:15",
      "end_time": "24:20",
      "estimated_listen_sec": 9.62,
      "primary_command": "afplay sessions/.../arp_000020_stereo_clean_left_remote_right.wav",
      "items": [
        {
          "source_audit_id": "arp_000020",
          "label": "remote_duplicate",
          "review_lane": "fast_confirm_drop",
          "review_action": "confirm_drop_or_keep_me",
          "suggested_decision": "drop_me",
          "utterance_ids": ["utt_000269", "utt_000271"]
        }
      ]
    }
  ]
}
```

The review plan is audit-only. It does not create new audio clips and does not modify transcripts.
It groups nearby suspicious intervals, keeps the original `afplay` commands, and gives a small
decision protocol: drop leaked `Me`, keep real local speech, or leave unclear cases as
`needs_review`.
`review_lane` is a workflow hint, not a decision. `fast_confirm_drop` rows are the quickest duplicate
or ASR-noise checks, `check_unique_me_content` rows need a content check before any drop,
`check_local_recall` rows are audit-only possible missing-local-speech checks, `confirm_benign` rows
usually clear harmless overlap, and `classify_audio` rows have no safe shortcut.
`remote_duplicate` suggestions are coverage-aware. `drop_me` is suggested only when the suspicious
interval covers enough of the whole `Me` utterance. Partial duplicates keep
`allowed_decisions: ["drop_me", "keep_me", "needs_review", "skip"]`, but the hint becomes
`needs_review` with `review_action: "check_unique_me_content"` and `review_features` records the
coverage and text-similarity signals.

`review_decisions.template.jsonl` contains editable `murmurmark.review_decision/v1` rows:

```json
{
  "schema": "murmurmark.review_decision/v1",
  "status": "todo",
  "decision": "todo",
  "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
  "session_id": "2026-06-26_11-15-50",
  "input_profile": "audit_cleanup_v3",
  "source_audit_id": "arp_000020",
  "review_lane": "fast_confirm_drop",
  "review_action": "confirm_drop_or_keep_me",
  "suggested_decision": "drop_me",
  "suggested_decision_confidence": "high",
  "suggested_decision_reason": "probable leaked remote duplicate; confirm by listening before changing decision",
  "review_features": {
    "me_overlap_coverage": 0.91,
    "text_similarity": 0.88,
    "token_containment": 1.0,
    "likely_partial_me_utterance": false
  },
  "me_utterance_ids": ["utt_000271"],
  "remote_utterance_ids": ["utt_000269"],
  "commands": {
    "stereo_clean_left_remote_right": "afplay sessions/.../clip.wav"
  },
  "reviewer": "",
  "notes": ""
}
```

`review-decisions-cli.py` fills this template into `review_decisions.jsonl`. It preserves the same
schema, updates `decision`, `status`, optional `reviewer`, and `reviewed_at`, and writes after every
answered row so the review can be resumed. `--session` limits the walk to one or more sessions.
`--lane` limits it to one or more `review_lane` values while still writing the complete output file,
so a reviewer can close `fast_confirm_drop` first and return later for `check_unique_me_content` or
`check_local_recall`.

`build-review-lane-pack.py` can turn one lane into a single listening WAV and index:

```text
sessions/_reports/review-plan/lane-packs/
  review_lane_pack.fast_confirm_drop.wav
  review_lane_pack.fast_confirm_drop.json
  review_lane_pack.fast_confirm_drop.md
  review_lane_answers.fast_confirm_drop.txt
  review_lane_answers.fast_confirm_drop.suggested.txt
```

The JSON uses `murmurmark.review_lane_pack/v1`:

```json
{
  "schema": "murmurmark.review_lane_pack/v1",
  "lane": "fast_confirm_drop",
  "outputs": {
    "audio": "sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
    "manifest": "sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json",
    "markdown": "sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.md",
    "answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt",
    "suggested_answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.suggested.txt"
  },
  "summary": {
    "selected_rows": 10,
    "item_count": 10,
    "skipped_count": 0,
    "duration_sec": 93.42
  },
  "items": [
    {
      "index": 1,
      "review_row_key": "review:arp_000020:2026-06-26_11-15-50:review_cluster_0015:utt_000269,utt_000271:1455.04:1460.66:remote_duplicate",
      "source_audit_id": "arp_000020",
      "session_id": "2026-06-26_11-15-50",
      "pack_start": 0.0,
      "pack_end": 9.62,
      "pack_start_time": "00:00.000",
      "pack_end_time": "00:09.620",
      "suggested_decision": "drop_me"
    }
  ]
}
```

`review_row_key` is the stable row identity for applying answers. `source_audit_id` is useful for
display but is not guaranteed to be unique across clustered review rows.

Lane packs are listening aids only. The generated answer sheet starts with `answers=...`, where `.`
means `todo`; it is not applied until `apply-review-lane-pack-decisions.py` is run. The generated
`.suggested.txt` sheet mirrors existing `suggested_decision` values that are allowed for each row.
It is a review aid, not a transcript edit by itself.
Lane packs do not modify transcript profiles.
`build-review-workspace.py` builds all currently-open lane packs and writes:

```text
sessions/_reports/review-plan/
  review_workspace.json
  review_workspace.md
  lane-packs/review_lane_answers.<lane>.txt
```

The JSON uses `murmurmark.review_workspace/v1`:

```json
{
  "schema": "murmurmark.review_workspace/v1",
  "lane_counts": [
    {
      "lane": "fast_confirm_drop",
      "template_rows": 10
    }
  ],
  "lanes": [
    {
      "lane": "fast_confirm_drop",
      "status": "ok",
      "items": 10,
      "audio": "sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
      "answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt",
      "suggested_answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.suggested.txt"
    }
  ]
}
```

The workspace is a reviewer index only. It writes lane answer sheets with `answers=...` placeholders,
where `.` means `todo`. It does not write decisions; use
`apply-review-lane-pack-decisions.py` or `review-decisions-cli.py` for that.
`apply-review-lane-pack-decisions.py` applies explicit reviewer answers for a lane pack back into
the complete `review_decisions.jsonl`. It accepts either `--answers` with a compact answer string in
pack order, or `--answers-file` pointing to a text file with an `answers=...` line:
`d=drop_me`, `k=keep_me`, `r` or `?=needs_review`, `s=skip`, and `.`/`n`/`t=todo`. The script validates
each answer against `allowed_decisions`, writes `review_source: "lane_pack"`, and emits
`review_lane_pack_apply_report.json`:

```json
{
  "schema": "murmurmark.review_lane_pack_apply_report/v1",
  "lane": "fast_confirm_drop",
  "summary": {
    "manifest_items": 10,
    "answer_count": 10,
    "applied_count": 10,
    "rejected_count": 0,
    "reviewed_count": 10,
    "todo_count": 0
  }
}
```

`apply-review-workspace-decisions.py` applies every lane answer sheet referenced by
`review_workspace.json` into one `review_decisions.jsonl`. It validates item counts, answer
shortcuts and `allowed_decisions`; with `--answers-source suggested` it reads
`suggested_answer_sheet` instead of the manual `answer_sheet`, and with `--require-complete` it fails
if any selected workspace answer is still `todo`. It writes:

```text
sessions/_reports/review-plan/
  review_workspace_apply_report.json
```

The JSON uses `murmurmark.review_workspace_apply_report/v1`:

```json
{
  "schema": "murmurmark.review_workspace_apply_report/v1",
  "answers_source": "review",
  "summary": {
    "lane_count": 3,
    "reviewed_count": 10,
    "workspace_todo_count": 30,
    "skipped_count": 0,
    "rejected_count": 0,
    "remaining_rows": 30,
    "ready_for_batch_apply": false
  }
}
```

`report-review-decisions-progress.py` summarizes the current edited review file before applying it:

```text
sessions/_reports/review-plan/
  review_decisions_progress.json
  review_decisions_progress.md
```

The JSON uses `murmurmark.review_decisions_progress/v1` and reports totals, remaining raw-audio
seconds, grouped progress by `review_lane` and `session_id`, and validation errors:

```json
{
  "schema": "murmurmark.review_decisions_progress/v1",
  "summary": {
    "total": 40,
    "reviewed": 10,
    "remaining": 30,
    "remaining_minutes": 1.28,
    "invalid_rows": 0,
    "ready_for_batch_apply": false
  }
}
```

After review, `apply-review-decisions.py` consumes the edited decision file and writes a separate
`reviewed_v1` transcript profile:

```text
derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.reviewed_v1.json
  transcript.reviewed_v1.md
  transcript.simple.reviewed_v1.json
  quality_report.reviewed_v1.json
  overlaps.reviewed_v1.json

derived/transcript-simple/whisper-cpp/review-decisions/
  review_decisions_report.reviewed_v1.json
  review_decisions_applied.reviewed_v1.jsonl
  review_decisions_rejected.reviewed_v1.jsonl
  review_decisions_conflicts.reviewed_v1.jsonl

sessions/_reports/review-plan/
  review_decisions_apply_report.json
```

`drop_me` removes whole reviewed `Me` utterances. `keep_me` keeps the utterance and can clear its
review flag. `needs_review` keeps the utterance marked for review. Conflicting decisions fail the
`reviewed_v1` gates. Coverage is also a hard gate: every template row for that session must be
closed with `drop_me`, `keep_me`, `needs_review`, or `skip`. If a row is missing or still `todo`,
the script still writes audit artifacts, but `review_decisions_report.reviewed_v1.json.gates.passed`
is `false` and `--transcript-profile auto` must not select `reviewed_v1`.
For `source: "local_recall"` rows, `drop_me` is invalid because the row points to a timeline-repair
island, not a transcript utterance. `keep_me` and `skip` close that local-recall risk as checked;
`needs_review` keeps it in the readiness burden. These rows are recorded in
`review_decisions_applied.reviewed_v1.jsonl` with `review_effect: "audit_only_local_recall"`.
`review-decisions-cli.py` and `apply-review-decisions.py` must honor `allowed_decisions`; for
example, they must not accept `drop_me` on `source: "local_recall"` rows even though `drop_me` is
valid for ordinary audio-review rows.
The CLI may also render nearby transcript context from `clean_dialogue.<input_profile>.json`. This
context is display-only and must not change the decision schema or coverage accounting.
It may expose numbered playback shortcuts for commands already present in the review row; those
shortcuts are UI-only and must not alter the row schema.
It may print progress and suggested next shell commands after writing `review_decisions.jsonl`; this
is also UI-only and does not count as review coverage.
`suggested_decision` is only a review hint. It never changes transcript output by itself and does not
count as coverage. The reviewer must still copy the intended value into `decision`.

The same hint stream can be materialized for measurement as `suggested_review_v1`:

```text
derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.suggested_review_v1.json
  transcript.suggested_review_v1.md
  transcript.simple.suggested_review_v1.json
  quality_report.suggested_review_v1.json
  overlaps.suggested_review_v1.json

derived/transcript-simple/whisper-cpp/review-decisions/
  review_decisions_report.suggested_review_v1.json
  review_decisions_applied.suggested_review_v1.jsonl
  review_decisions_rejected.suggested_review_v1.jsonl
  review_decisions_conflicts.suggested_review_v1.jsonl

derived/synthesis-simple/extractive/
  quality_verdict.suggested_review_v1.json
  quality_verdict.suggested_review_v1.md
  notes.suggested_review_v1.md
  evidence_notes.suggested_review_v1.json
  review_items.suggested_review_v1.jsonl

sessions/_reports/review-plan/
  review_decisions_apply.suggested_review_v1.json

sessions/_reports/suggested-review-shadow/
  suggested_review_shadow_report.json
  suggested_review_shadow_report.md
  suggested_cleanup_apply_report.json
```

`suggested_review_v1` is explicit-only. `--transcript-profile auto` must never select it, and
session readiness must not count it as a human-reviewed profile. It exists to compare machine
suggestions against the trusted cleanup and manual-review paths. The shadow report compares core
quality metrics against the selected profile and may classify a session as `do_not_promote` even
when the suggested profile gates pass.

`apply-suggested-cleanup.py` consumes this report and materializes only safe `drop_me` candidates as
`audit_cleanup_v5`. It must not select `suggested_review_v1` as input wholesale and must not copy
machine-generated review marks as if they were human review.

`apply-review-decisions-batch.py --refresh-reports` extends the batch report with
`refresh_reports[]`. Each row stores the command, return code, and output tails for the refreshed
`session-quality`, `operational-readiness`, and `review-plan` commands. The batch command must fail
if any refreshed report command fails, so stale readiness reports are not silently treated as current.

The report includes coverage evidence:

```json
{
  "schema": "murmurmark.review_decisions_report/v1",
  "coverage": {
    "schema": "murmurmark.review_coverage/v1",
    "status": "complete",
    "complete": true,
    "template_path": "sessions/_reports/review-plan/review_decisions.template.jsonl",
    "required_rows": 6,
    "closed_rows": 6,
    "coverage_ratio": 1.0,
    "missing_rows": 0,
    "pending_rows": 0
  },
  "gates": {
    "passed": true,
    "hard_failures": [],
    "warnings": []
  }
}
```

Automatic cleanup profiles remain unchanged.

`apply-review-decisions-batch.py` wraps the per-session command and writes
`murmurmark.review_decisions_batch_report/v1`:

```json
{
  "schema": "murmurmark.review_decisions_batch_report/v1",
  "summary": {
    "session_count": 6,
    "passed_sessions": 6,
    "failed_sessions": 0
  },
  "sessions": [
    {
      "session_id": "2026-06-26_11-15-50",
      "apply": {
        "returncode": 0,
        "gates_passed": true,
        "coverage_complete": true,
        "coverage_ratio": 1.0
      }
    }
  ]
}
```

Session quality and operational readiness are profile-aware. They compare audio-review items with
the selected `clean_dialogue*.json` profile and treat items whose `Me` utterance was removed by
cleanup as resolved. Raw audio-review summaries are still available inside each session for audit,
but operational `review_burden` and review queues should reflect the remaining selected transcript,
not the pre-cleanup audit file.

They also consume `derived/audit/local-recall/local_recall_audit.json`. A raw
`local_only_island_recall < 0.9` is blocking only when the audit is missing or reports possible lost
local speech. Explained short/weak islands stay visible in readiness metrics but do not by
themselves force `review_first`.
Blocking local-recall items are emitted into the operational review queue as `lost_me` or
`local_recall_needs_review` items. These rows may not have transcript utterance IDs yet; they carry
the timeline-repair parent candidate and a short `ffplay` command for the mic capture.

`audit_cleanup_v2` consumes the audio review audit as an extra evidence layer. It writes the same
profile-shaped transcript artifacts as v1, but with the `audit_cleanup_v2` suffix:

```text
derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.audit_cleanup_v2.json
  transcript.audit_cleanup_v2.md
  transcript.simple.audit_cleanup_v2.json
  quality_report.audit_cleanup_v2.json
  overlaps.audit_cleanup_v2.json

derived/transcript-simple/whisper-cpp/audit-cleanup/
  audit_cleanup_report.audit_cleanup_v2.json
  audit_cleanup_patches.audit_cleanup_v2.jsonl
  audit_cleanup_rejected_patches.audit_cleanup_v2.jsonl
  audit_cleanup_diff.audit_cleanup_v2.json
```

v2 usually uses `audit_cleanup_v1` as input and may drop only whole `Me` utterances when
`audio_review_audit.jsonl` classifies them as high-confidence `remote_duplicate` or short
`asr_noise` with verdict `probable_transcript_error`. Other audio-review labels are mark-only:
`remote_leak`, `lost_me`, `uncertain`, `double_talk` and `timing_overlap`.

`audit_cleanup_v3` has the same artifact shape with the `audit_cleanup_v3` suffix. It usually uses
`audit_cleanup_v2` as input and additionally reads
`sessions/_reports/audio-judge-v0/audio_judge_v0_queue_predictions.jsonl`. v3 may drop a whole `Me`
utterance only when:

- the corresponding audio-review item is still active in the input profile;
- the audio judge predicts `drop_error` with high confidence;
- the audio-review label is a duplicate/noise class;
- the existing text/local-support/marker/intentional-repeat safety gates pass.

If v3 applies no patches, automatic synthesis and session-quality reporting should keep using v2 or
v1 instead of promoting an empty v3 profile.

`audit_cleanup_v4` has the same artifact shape with the `audit_cleanup_v4` suffix. It usually uses
`audit_cleanup_v3` as input and reads the same audio-judge queue. v4 adds one expanded gate for
strong duplicate evidence:

- audio judge label is `drop_error`;
- judge confidence is at least `0.93`;
- audio-review label is `remote_duplicate`;
- duplicate score is at least `82`;
- text similarity and token containment are at least `0.75`;
- local support is at most `55`;
- `Me` overlap coverage is at least `0.60`;
- unique `Me` content tokens are at most `2`;
- no protected action/decision/risk marker, intentional repeat or notes impact is present.

All non-duplicate audio-review classes remain mark-only in v4.

`audit_cleanup_v5` has the same artifact shape with the `audit_cleanup_v5` suffix. It usually uses
the currently selected cleanup profile from `suggested_review_shadow_report.json` as input and reads
the `suggested_review_v1` applied decisions only as evidence for whole-utterance `drop_me` patches.
It may apply patches only for sessions classified as `promising_shadow_candidate` or
`promising_cleanup_candidate_with_residual_review`, and it must skip sessions where the shadow
report added new `needs_review` items. v5 is a cleanup profile, not a reviewed profile:
`suggested_review_v1` remains explicit-only and is never selected by `auto`.

Patch suggestions are dry-run only:

```json
{
  "schema": "murmurmark.group_overlap_patch_suggestion/v1",
  "overlap_id": "ov_000042",
  "action": "drop_me_duplicate",
  "apply_automatically": false
}
```

Audit-informed cleanup consumes the group overlap audit and writes a separate profile. It never edits
`current`, `shadow_v2`, raw audio, Echo Guard outputs, or ASR raw segments.

```text
derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.audit_cleanup_v1.json
  transcript.audit_cleanup_v1.md
  transcript.simple.audit_cleanup_v1.json
  quality_report.audit_cleanup_v1.json
  overlaps.audit_cleanup_v1.json

derived/transcript-simple/whisper-cpp/audit-cleanup/
  audit_cleanup_report.audit_cleanup_v1.json
  audit_cleanup_patches.audit_cleanup_v1.jsonl
  audit_cleanup_rejected_patches.audit_cleanup_v1.jsonl
  audit_cleanup_diff.audit_cleanup_v1.json
```

`audit_cleanup_report.audit_cleanup_v1.json` v1:

```json
{
  "schema": "murmurmark.audit_cleanup_report/v1",
  "input_profile": "shadow_v2",
  "output_profile": "audit_cleanup_v1",
  "mode": "conservative",
  "summary": {
    "input_utterances": 497,
    "output_utterances": 486,
    "applied_patches": 11,
    "rejected_patches": 82,
    "dropped_me_duplicate_seconds": 40.82,
    "dropped_me_noise_seconds": 0,
    "protected_intentional_repeat_count": 3,
    "audit_harmful_seconds_before": 44.28,
    "audit_harmful_seconds_after": 3.46
  },
  "gates": {
    "passed": true,
    "hard_failures": [],
    "warnings": []
  }
}
```

Patch JSONL records are explicit and auditable:

```json
{
  "schema": "murmurmark.audit_cleanup_patch/v1",
  "patch_id": "patch_000011",
  "action": "drop_me_duplicate",
  "status": "applied",
  "reason": "conservative_gate_passed",
  "input_profile": "shadow_v2",
  "output_profile": "audit_cleanup_v1",
  "target": {
    "utterance_id": "utt_mic_0420",
    "role": "Me",
    "start": 1801.12,
    "end": 1805.44,
    "text": "Да, проверим deploy."
  },
  "matched_remote": {
    "utterance_id": "utt_remote_0417",
    "start": 1800.92,
    "end": 1805.68,
    "text": "Да, проверим deploy."
  },
  "audit_overlap_ids": ["ov_000087"],
  "evidence": {
    "label": "probable_duplicate",
    "classification_confidence": 0.93,
    "scores": {},
    "text": {},
    "speaker_state": {},
    "interval": {}
  },
  "safety_checks": {
    "unique_me_content_token_count": 0,
    "notes_impact": false,
    "has_protected_action_decision_risk_marker": false,
    "intentional_repeat_candidate": false,
    "safe_to_drop_entire_utterance": true
  }
}
```

Conservative cleanup rules:

- `probable_duplicate`: may drop only a whole `Me` utterance when confidence, text duplicate
  evidence, weak local evidence, utterance coverage, unique-content, notes-impact and protected-marker
  checks all pass.
- `probable_asr_noise`: may drop only a short whole `Me` utterance with weak local support, few
  content tokens, no domain terms, and no action/decision/risk marker.
- `probable_double_talk`, `probable_timing_overlap`, `probable_remote_leak`, and
  `needs_human_review`: kept in v1 and written as `quality.audit_cleanup` flags on the utterance.
- Audio-only remote leak is mark-only unless it also passes duplicate/noise gates.

`quality_report.audit_cleanup_v1.json` keeps the simple quality schema and adds audit-informed
metrics both at top level and under `audit_cleanup`:

```json
{
  "schema": "murmurmark.simple_transcript_quality/v1",
  "utterances": 486,
  "remote_duplicate_in_me_seconds": 125.83,
  "meeting_duration_sec": 3102.4,
  "audit_cleanup": {
    "profile": "audit_cleanup_v1",
    "applied_patches": 11,
    "rejected_patches": 82,
    "dropped_me_duplicate_seconds": 40.82,
    "dropped_me_noise_seconds": 0,
    "audit_harmful_seconds_before": 44.28,
    "audit_harmful_seconds_after": 3.46,
    "audit_benign_seconds": 210.15,
    "audit_review_seconds": 98.7,
    "protected_intentional_repeat_count": 3
  }
}
```

When synthesis is run with `--transcript-profile audit_cleanup_v1`, it writes the normal extractive
outputs plus profile aliases:

```text
quality_verdict.audit_cleanup_v1.json
quality_verdict.audit_cleanup_v1.md
notes.audit_cleanup_v1.md
evidence_notes.audit_cleanup_v1.json
```

`--transcript-profile auto` may choose `audit_cleanup_v1` only when the cleanup report exists and
`gates.passed == true`; otherwise it falls back to the previous `shadow_v2` selection logic.
For `audit_cleanup_v1`, verdict thresholds prefer audit-informed group metrics:

- `good`: harmful overlap ratio is at most 1% and review ratio is at most 3%.
- `usable_with_review`: harmful ratio is at most 3% and review ratio is at most 12%.
- `risky`: above those limits or local-only recall is below 0.80.
- `failed`: unrepaired long mic crossings, golden phrase failures, local-only recall below 0.70, or
  severe harmful/review ratios.

Current role model:

- `Me` from the local microphone track;
- `Colleagues` from the remote track;
- no per-person diarization inside `Colleagues`.

The richer contract below remains the target for the future heavy-local transcription pipeline.

## `transcript.rich.json`

```json
{
  "schema": "murmurmark.transcript/v1",
  "session_id": "2026-06-20T14-30-00Z_7f3a",
  "language_profile": ["ru", "en"],
  "utterances": [
    {
      "id": "utt_000142",
      "start": 812.42,
      "end": 818.77,
      "source_track": "remote",
      "speaker_cluster": "remote_speaker_02",
      "speaker_identity": {
        "name": "Anna",
        "confidence": 0.84,
        "method": "manual_confirmed"
      },
      "role": "teammate",
      "raw_text": "я думаю что сло надо считать отдельно для гейтвея",
      "corrected_text": "Я думаю, что SLO надо считать отдельно для gateway.",
      "asr_evidence": [
        {
          "engine": "vibevoice-asr",
          "text": "я думаю что сло надо считать отдельно для гейтвея",
          "confidence": null,
          "window": "w001"
        },
        {
          "engine": "qwen3-asr",
          "text": "я думаю что SLO надо считать отдельно для gateway",
          "confidence": null,
          "window": "validator_023"
        }
      ],
      "domain_corrections": [
        {
          "from": "сло",
          "to": "SLO",
          "reason": "term glossary: Service Level Objective",
          "confidence": 0.93
        }
      ],
      "quality": {
        "speaker_assignment": "verified",
        "diarization_disagreement": false,
        "possible_mic_leakage": false,
        "excluded_from_me_role": false,
        "matched_remote_utterance_id": null,
        "needs_review": false
      }
    }
  ]
}
```

Allowed `source_track` values:

- `mic`
- `remote`
- `mixed`
- `unknown`

Allowed speaker assignment states:

- `verified`
- `probable`
- `unresolved`
- `conflict`

Mic utterances that match remote audio should remain in the transcript as evidence, but must not be treated as the user's speech when leakage is likely:

```json
{
  "id": "utt_000302",
  "source_track": "mic",
  "speaker_cluster": "me",
  "raw_text": "давайте посмотрим сло",
  "quality": {
    "possible_mic_leakage": true,
    "excluded_from_me_role": true,
    "matched_remote_utterance_id": "utt_000298",
    "needs_review": false
  }
}
```

`murmurmark reconcile-transcript ./session` applies this marking after Echo Guard diagnostics exist. It writes the matched remote utterance id and an `echo_guard` evidence object into the mic utterance quality block.

Default paths:

```text
input/output: derived/transcript/resolved/transcript.rich.json
quality:      derived/transcript/resolved/quality_report.json
report:       derived/transcript/resolved/echo_reconciliation_report.json
```

## `speaker_map.json`

```json
{
  "schema": "murmurmark.speaker_map/v1",
  "session_id": "2026-06-20T14-30-00Z_7f3a",
  "speakers": [
    {
      "cluster": "me",
      "source_track": "mic",
      "identity": {
        "name": "Local User",
        "confidence": 1.0,
        "method": "track_hint"
      }
    },
    {
      "cluster": "remote_speaker_02",
      "source_track": "remote",
      "identity": {
        "name": null,
        "label": "Speaker 2",
        "confidence": 0.48,
        "method": "unresolved"
      }
    }
  ]
}
```

## `corrections.jsonl`

```jsonl
{"utterance_id":"utt_000142","from":"сло","to":"SLO","reason":"known glossary abbreviation","confidence":0.93,"meaning_changed":false}
{"utterance_id":"utt_000142","from":"гейтвея","to":"gateway","reason":"domain spelling preference","confidence":0.81,"meaning_changed":false}
```

Correction records are audit evidence. Do not overwrite them silently.

## `quality_report.json`

```json
{
  "schema": "murmurmark.quality_report/v1",
  "session_id": "2026-06-20T14-30-00Z_7f3a",
  "summary": {
    "duration_sec": 3580,
    "remote_speakers_detected": 3,
    "speaker_identity_resolved": 2,
    "utterances_total": 412,
    "utterances_needing_review": 19,
    "domain_corrections_total": 86,
    "echo": {
      "mode": "conservative",
      "bleed_detected": true,
      "median_delay_ms": 182,
      "suppression_attempted": true,
      "clean_mic_accepted_for_asr": true,
      "segments_with_probable_bleed": 34,
      "segments_excluded_from_me_role": 12
    }
  },
  "risks": [
    {
      "type": "speaker_identity_low_confidence",
      "speaker": "remote_speaker_02",
      "confidence": 0.48
    },
    {
      "type": "diarization_disagreement",
      "start": 1782.2,
      "end": 1791.4
    },
    {
      "type": "possible_mic_leakage",
      "start": 923.1,
      "end": 928.5
    },
    {
      "type": "probable_remote_bleed_in_mic",
      "start": 923.1,
      "end": 928.5,
      "delay_ms": 184,
      "confidence": 0.88
    },
    {
      "type": "echo_suppression_rejected",
      "reason": "near_end_speech_damage_detected"
    }
  ]
}
```

Synthesis must read this report before producing notes.

## `synthesis_policy.yaml`

```yaml
schema: murmurmark.synthesis_policy/v1

privacy_mode: local_only

allowed_external_payload:
  raw_audio: false
  transcript: false
  speaker_names: false
  project_codenames: false
  ticket_ids: false
  service_names: false

outputs:
  require_utterance_citations: true
  require_human_review_before_docs_update: true
  reject_uncited_facts: true
```

For sanitized external synthesis:

```yaml
schema: murmurmark.synthesis_policy/v1

privacy_mode: sanitized_frontier

allowed_external_payload:
  raw_audio: false
  transcript: true
  speaker_names: false
  project_codenames: false
  ticket_ids: true
  service_names: true

provider:
  type: openai_responses
  model: gpt-5.5
  require_zero_data_retention: true
  store: false

outputs:
  require_utterance_citations: true
  require_human_review_before_docs_update: true
  reject_uncited_facts: true
```
