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
