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
`evidence_notes.json`.

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
    "version": "0.2.0",
    "mode": "deterministic",
    "config": "default_v2"
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
