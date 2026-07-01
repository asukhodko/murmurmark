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
  "review_summary": {
    "review_item_count": 6,
    "review_item_seconds": 42.3,
    "by_type": {
      "utterance_transcript_order_review": {"count": 1, "seconds": 6.0}
    },
    "by_severity": {
      "medium": {"count": 6, "seconds": 42.3}
    }
  },
  "risk_items": [
    {
      "type": "needs_review_ratio",
      "severity": "medium",
      "reason": "some transcript regions need review"
    }
  ],
  "recommended_next": "murmurmark review next sessions/2026-06-26_15-32-02",
  "next_commands": [
    {
      "id": "review_next",
      "command": "murmurmark review next sessions/2026-06-26_15-32-02",
      "reason": "review required before export or high-confidence use"
    },
    {
      "id": "open_notes_summary",
      "command": "murmurmark notes sessions/2026-06-26_15-32-02",
      "reason": "read the selected extractive notes"
    }
  ],
  "open_commands": [
    {
      "id": "open_quality_verdict",
      "command": "less sessions/2026-06-26_15-32-02/derived/synthesis-simple/extractive/quality_verdict.md",
      "path": "sessions/2026-06-26_15-32-02/derived/synthesis-simple/extractive/quality_verdict.md"
    }
  ]
}
```

`recommended_next`, `next_commands` and `open_commands` are also copied to
`synthesis_manifest.json`. This makes synthesis a machine-readable handoff: CLI wrappers and agents
do not have to scrape terminal output to decide whether to review, inspect notes, refresh a report
or export a bundle.

`notes.md` is not a free summary. It contains selected extractive outline items and top
decisions/actions/risks/open questions with utterance IDs. Every item that looks like a decision or
action remains `needs_review` until a human confirms it. The complete scored candidate list lives in
`evidence_notes.json`. Since generator `0.3.0`, meeting facilitation and process phrases such as
`let's move to the next block` or `let's vote` are kept as hidden candidates in JSON, but are not
shown as decisions or actions in Markdown. Unresolved review sources such as
`transcript_order_review:needs_review` are also copied to candidate features and penalized before
Markdown selection, so risky evidence is easier to audit and less likely to appear as a confident
top item.

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

`quality_flags` contains boolean quality keys such as `needs_review` and nested review statuses such
as `transcript_order_review:needs_review` or `transcript_order_review:cleared`.
`review_items.jsonl` should use the more specific nested source when available; for example,
`quality.transcript_order_review.status == "needs_review"` becomes an
`utterance_transcript_order_review` item with `source_audit_ids`, instead of only a generic
`utterance_needs_review` row.

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

`murmurmark audit group-overlaps` prints a compact CLI summary after writing these files: profile,
total overlap seconds, harmful seconds, benign/expected seconds, review seconds and the report path.

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

`murmurmark audit local-recall` prints a compact CLI summary after writing these files: profile,
missing-island count, possible lost local speech, review seconds, recommendation and report path.
An existing but empty `timeline_repair_examples*.jsonl` is a valid zero-item audit result. It must
produce `status: ok`, `audited_missing_island_count: 0`, and an empty `local_recall_items.jsonl`.

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
- `likely_harmless_remote_covered`
- `likely_harmless_remote_boundary_covered`
- `likely_harmless_ack_fragment`
- `likely_harmless_boundary_fragment`

Each item also carries a `boundary` object:

```json
{
  "boundary": {
    "start_offset_from_parent_ms": 450,
    "end_offset_from_parent_ms": 3100,
    "near_parent_boundary": true,
    "nearest_child_boundary_ms": null,
    "adjacent_to_child": false,
    "nearest_remote_guard_boundary_ms": 0,
    "adjacent_to_remote_guard": true,
    "boundary_fragment": true
  }
}
```

The audit reads timeline-repair examples and Echo Guard `speaker_state.jsonl`; it does not read or
modify raw capture. Session quality gates may ignore low local recall only when this audit says
`blocking_low_local_recall: false`. Possible lost local speech remains a blocking risk.
`likely_harmless_remote_covered` means the unrecovered local island text is already covered by a
nearby remote candidate, so the audit treats it as preserved content rather than missing meeting
substance. `likely_harmless_remote_boundary_covered` is the narrower boundary case: the island is
short, sits on a parent/remote guard boundary and the nearby remote text already covers enough of
the meaningful tokens, even when the dropped parent contains work markers. `likely_harmless_ack_fragment`
means the missing island is a short acknowledgement such as "понял" or "окей" without work markers
or unique meeting content. `likely_harmless_boundary_fragment` means the missing island is short and
sits on a known timeline boundary, so it is tracked as low-risk boundary timing noise rather than a
lost local turn.

`murmurmark repair local-recall` is the first conservative repair layer for this queue. It writes a
separate profile and does not modify `shadow_v2`, cleanup, order repair or reviewed profiles.
The v1 repair may insert only whole short `Me` utterances from `possible_lost_me` items when the
local state is strong, remote state is weak and micro-ASR produces a non-empty text that is not too
similar to nearby remote text. Inserted rows are marked `quality.needs_review = true`.
For boundary islands, micro-ASR rows whose midpoint is outside the selected island can still be
used when the row overlaps or nearly touches that island; such attempts are marked with
`selection_policy = "boundary_overlap_fallback"` in the micro-runs JSONL.

Profile outputs:

```text
derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.local_recall_repair_v1.json
  transcript.local_recall_repair_v1.md
  transcript.simple.local_recall_repair_v1.json
  quality_report.local_recall_repair_v1.json
  overlaps.local_recall_repair_v1.json

derived/transcript-simple/whisper-cpp/local-recall-repair/
  local_recall_repair_report.local_recall_repair_v1.json
  local_recall_repair_patches.local_recall_repair_v1.jsonl
  local_recall_repair_rejected.local_recall_repair_v1.jsonl
  local_recall_repair_micro_runs.local_recall_repair_v1.jsonl
  local_recall_repair.local_recall_repair_v1.md
```

`local_recall_repair_report.local_recall_repair_v1.json` uses
`murmurmark.local_recall_repair_report/v1`:

```json
{
  "schema": "murmurmark.local_recall_repair_report/v1",
  "input_profile": "order_repair_v1",
  "output_profile": "local_recall_repair_v1",
  "summary": {
    "source_items": 3,
    "eligible_items": 1,
    "applied_repairs": 1,
    "inserted_me_seconds": 1.3,
    "rejected_items": 2,
    "micro_boundary_overlap_recovered_items": 1,
    "micro_boundary_overlap_recovered_attempts": 3,
    "micro_raw_transcription_rows": 3
  },
  "gates": {
    "passed": true,
    "hard_failures": [],
    "warnings": []
  },
  "recommended_next": "murmurmark synthesize sessions/example --transcript-profile local_recall_repair_v1",
  "next_commands": [
    {
      "id": "synthesize_repair_profile",
      "command": "murmurmark synthesize sessions/example --transcript-profile local_recall_repair_v1",
      "reason": "build quality verdict and notes from the repair profile"
    },
    {
      "id": "open_repair_transcript",
      "command": "murmurmark transcript sessions/example --profile local_recall_repair_v1",
      "reason": "inspect the repair transcript through the CLI"
    },
    {
      "id": "refresh_session_report",
      "command": "murmurmark report sessions/example",
      "reason": "refresh readiness after repair-derived synthesis"
    }
  ],
  "open_commands": [
    {
      "id": "open_repair_report",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair_report.local_recall_repair_v1.json",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/local-recall-repair/local_recall_repair_report.local_recall_repair_v1.json"
    },
    {
      "id": "open_repair_transcript",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/resolved/transcript.local_recall_repair_v1.md",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/resolved/transcript.local_recall_repair_v1.md"
    }
  ]
}
```

Applied patches use `murmurmark.local_recall_repair_patch/v1`; rejected items use
`murmurmark.local_recall_repair_rejection/v1` with a machine-readable reason. Eligible inputs include
strong `possible_lost_me` rows and narrow `needs_review` rows with strong `local_only` speaker-state
evidence. Synthesis accepts `--transcript-profile local_recall_repair_v1` explicitly and adds a review
risk when inserted local turns exist. Auto-promotion of the raw repair profile is intentionally left
out. The normal promotion path is `local_recall_repair_v1` as an input to `murmurmark review agent`,
which may keep high-confidence repair insertions inside the confirmed `agent_reviewed_v1` profile.
The report also carries `recommended_next`, `next_commands` and `open_commands`; the Swift CLI prints
these fields after `murmurmark repair local-recall` so terminal output and JSON handoff stay identical.
`local_recall_repair_micro_runs.local_recall_repair_v1.jsonl` keeps per-source/per-window evidence:
raw transcription text, selected rows, score, source label, window label and fallback metadata.
Operational readiness exposes applied repair turns as review queue rows with:

```json
{
  "source": "local_recall_repair",
  "label": "local_recall_repair_inserted",
  "input_profile": "local_recall_repair_v1",
  "allowed_decisions": ["drop_me", "keep_me", "needs_review", "skip"],
  "utterance_ids": ["local_recall_repair_v1_local_recall_0005"]
}
```

Unlike raw `source: "local_recall"` rows, these rows target real inserted `Me` utterances, so
`drop_me` is valid and removes the repair turn from the reviewed output profile.

The transcript order audit explains remaining chronology risk from long `Me` utterances crossing
remote turns. It is audit-only and writes under:

```text
derived/audit/order/
  transcript_order_audit.json
  transcript_order_items.jsonl
  transcript_order_review.md
```

`murmurmark audit order` prints a compact CLI summary after writing these files: profile, audited
overlaps, probable order-risk seconds, review seconds, recommendation and report path.

`transcript_order_audit.json` uses `murmurmark.transcript_order_audit/v1`:

```json
{
  "schema": "murmurmark.transcript_order_audit/v1",
  "profile": "shadow_v2",
  "status": "ok",
  "summary": {
    "audited_overlap_count": 8,
    "probable_order_risk_count": 1,
    "probable_order_risk_seconds": 2.4,
    "blocking_order_risk": true,
    "recommended_next_step": "review_transcript_order_items"
  }
}
```

`transcript_order_items.jsonl` uses `murmurmark.transcript_order_item/v1`. Labels are:

- `probable_order_risk`
- `needs_review`
- `probable_duplicate`
- `likely_timing_overlap`
- `possible_double_talk`

The audit reads `clean_dialogue*.json` and `overlaps*.json`; it does not read audio and does not
modify transcript profiles. `probable_order_risk` means a long `Me` turn wraps a `Colleagues` turn
and continues after it, which is the main known pattern for Markdown showing a local reaction before
the remote phrase that triggered it.

`report-session-quality.py` reads this audit. Blocking order risk contributes to
`review_burden_sec`, adds `risk:transcript_order_risk` to readiness blockers, and is checked by
`check-corpus-gates.py` as `transcript.no_blocking_order_risk` for the selected operational session
scope. The operational readiness report also includes such items in the review queue under lane
`check_transcript_order`.

### Transcript Order Repair

`murmurmark repair order` is an explicit structural repair step. It writes a separate
`order_repair_v1` profile and never modifies baseline, `shadow_v2`, cleanup or reviewed profiles.
The v1 repair is deliberately narrow: it may split one long `Me` utterance into before/after `Me`
utterances only when saved mic ASR source segments sit cleanly before and after the crossed remote
turn. Overlap segments are dropped only when their content is covered by the remote utterance and
does not contain protected action/decision/risk markers or unique local content.

Profile outputs:

```text
derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.order_repair_v1.json
  transcript.order_repair_v1.md
  transcript.simple.order_repair_v1.json
  quality_report.order_repair_v1.json
  overlaps.order_repair_v1.json

derived/transcript-simple/whisper-cpp/order-repair/
  transcript_order_repair_report.order_repair_v1.json
  transcript_order_repair_patches.order_repair_v1.jsonl
  transcript_order_repair_rejected.order_repair_v1.jsonl
```

`transcript_order_repair_report.order_repair_v1.json` uses
`murmurmark.transcript_order_repair_report/v1`:

```json
{
  "schema": "murmurmark.transcript_order_repair_report/v1",
  "input_profile": "shadow_v2",
  "output_profile": "order_repair_v1",
  "summary": {
    "order_risk_items": 1,
    "applied_repairs": 1,
    "split_utterances_created": 2,
    "removed_original_me_utterances": 1,
    "marked_needs_review": 0,
    "unrepaired_order_risks": 0,
    "repaired_order_risk_seconds": 2.4,
    "unrepaired_order_risk_seconds": 0.0
  },
  "gates": {
    "passed": true,
    "hard_failures": [],
    "warnings": []
  },
  "recommended_next": "murmurmark synthesize sessions/example --transcript-profile order_repair_v1",
  "next_commands": [
    {
      "id": "synthesize_repair_profile",
      "command": "murmurmark synthesize sessions/example --transcript-profile order_repair_v1",
      "reason": "build quality verdict and notes from the repair profile"
    },
    {
      "id": "open_repair_transcript",
      "command": "murmurmark transcript sessions/example --profile order_repair_v1",
      "reason": "inspect the repair transcript through the CLI"
    },
    {
      "id": "refresh_session_report",
      "command": "murmurmark report sessions/example",
      "reason": "refresh readiness after repair-derived synthesis"
    }
  ],
  "open_commands": [
    {
      "id": "open_repair_report",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/order-repair/transcript_order_repair_report.order_repair_v1.json",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/order-repair/transcript_order_repair_report.order_repair_v1.json"
    },
    {
      "id": "open_repair_transcript",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/resolved/transcript.order_repair_v1.md",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/resolved/transcript.order_repair_v1.md"
    }
  ]
}
```

Applied patches use `murmurmark.transcript_order_repair_patch/v1` and record the source audit id,
original `Me` id, remote id, created utterance ids and dropped overlap segment ids. Rejected patches
use `murmurmark.transcript_order_repair_rejection/v1` and must include a machine-readable reason.
When repair cannot be applied safely, the original utterance remains in the output profile and gets
`quality.transcript_order_repair.status = "needs_review"` plus `quality.needs_review = true`.
`gates.passed = true` means the output profile is internally safe to consume: applied repairs were
materialized, and every unrepaired order risk is still explicit as `needs_review`. It does not mean
all chronology risk disappeared. Partial repairs add `partial_order_repair_needs_review` to warnings
and keep `unrepaired_order_risk_seconds` in readiness.
The report also carries `recommended_next`, `next_commands` and `open_commands`; `murmurmark repair
order` prints those JSON commands instead of keeping a separate hard-coded terminal handoff.
Synthesis accepts `--transcript-profile order_repair_v1` explicitly and treats failed repair gates as
a high-severity selection risk. `--transcript-profile auto` may select `order_repair_v1` only when it
was built over the otherwise selected base profile, gates passed and at least one repair was applied.
When `order_repair_v1` gates pass, `report-session-quality.py` may select this profile and reports
`transcript_order_recommended_next_step = "transcript_order_repaired_clear"` only when
`unrepaired_order_risks == 0`. Otherwise it keeps `review_transcript_order_items` and counts the
remaining seconds in `transcript_order_review_seconds`.

`murmurmark corpus order` aggregates per-session order audits into:

```text
sessions/_reports/transcript-order/
  transcript_order_corpus_report.json
  transcript_order_corpus_items.jsonl
  transcript_order_corpus_report.md
```

`transcript_order_corpus_report.json` uses `murmurmark.transcript_order_corpus_report/v1`:

```json
{
  "schema": "murmurmark.transcript_order_corpus_report/v1",
  "summary": {
    "session_count": 12,
    "audited_session_count": 12,
    "missing_order_audit_count": 0,
    "blocking_session_count": 2,
    "complete_blocking_session_count": 0,
    "probable_order_risk_count": 0,
    "probable_order_risk_seconds": 0.0,
    "needs_review_count": 23,
    "needs_review_seconds": 173.42,
    "order_repair": {
      "sessions_with_repair": 1,
      "cleared_session_count": 1,
      "partial_session_count": 0,
      "applied_repairs": 1,
      "unrepaired_order_risks": 0,
      "audit_probable_order_risk_count": 1,
      "effective_probable_order_risk_count": 0,
      "resolved_order_risk_count": 1,
      "resolved_order_risk_seconds": 2.0
    },
    "recommended_next_step": "review_incomplete_order_candidates"
  },
  "next_commands": [
    {
      "id": "review_transcript_order_2026-06-26_17-31-17",
      "label": "Review transcript-order risks for 2026-06-26_17-31-17.",
      "command": "murmurmark review lane check_transcript_order --session sessions/2026-06-26_17-31-17",
      "session_id": "2026-06-26_17-31-17",
      "session": "sessions/2026-06-26_17-31-17"
    }
  ]
}
```

`transcript_order_corpus_items.jsonl` uses `murmurmark.transcript_order_corpus_item/v1` and keeps
the session id, selected profile, label, interval, utterance ids, short `Me`/`Colleagues` texts and
the path to the per-session review Markdown. `summary.order_repair` compares the original order audit
with the effective metrics after `order_repair_v1` and records applied repairs, cleared sessions and
remaining unrepaired risks. This report is read-only: it does not change review decisions or
transcript profiles. Its purpose is to keep chronology-risk examples visible as a corpus regression
queue. `next_commands` points to `check_transcript_order` for the first complete blocking session,
or to `murmurmark process ...` when only incomplete blocking sessions remain.

`check-corpus-gates.py` reads this aggregate report via `--transcript-order` (default:
`sessions/_reports/transcript-order/transcript_order_corpus_report.json`). It warns when the report
has missing order audits and fails `transcript_order.no_complete_blocking_sessions` when a selected
operational session still has blocking chronology risk. Blocking rows from diagnostic sessions or
profiles superseded by the selected transcript profile are reported as
`transcript_order.raw_complete_blocking_sessions` warnings. `murmurmark corpus process` builds the
aggregate order report before running corpus gates.

`murmurmark corpus local-recall` aggregates per-session local-recall audits into:

```text
sessions/_reports/local-recall/
  local_recall_corpus_report.json
  local_recall_corpus_items.jsonl
  local_recall_corpus_report.md
```

`local_recall_corpus_report.json` uses `murmurmark.local_recall_corpus_report/v1`:

```json
{
  "schema": "murmurmark.local_recall_corpus_report/v1",
  "summary": {
    "session_count": 10,
    "audited_session_count": 10,
    "missing_local_recall_audit_count": 0,
    "blocking_session_count": 1,
    "complete_blocking_session_count": 1,
    "possible_lost_me_count": 2,
    "possible_lost_me_seconds": 4.2,
    "needs_review_count": 3,
    "needs_review_seconds": 6.8,
    "likely_harmless_seconds": 12.4,
    "recommended_next_step": "review_complete_local_recall_items"
  },
  "next_commands": [
    {
      "id": "review_local_recall_2026-06-26_17-31-17",
      "label": "Review local-recall items for 2026-06-26_17-31-17.",
      "command": "murmurmark review lane check_local_recall --session sessions/2026-06-26_17-31-17",
      "session_id": "2026-06-26_17-31-17",
      "session": "sessions/2026-06-26_17-31-17"
    }
  ]
}
```

`local_recall_corpus_items.jsonl` uses `murmurmark.local_recall_corpus_item/v1` and keeps session id,
selected profile, label, interval, parent candidate/text, compact state/boundary evidence and the
path to `local_recall_review.md`. This report is read-only: it does not insert missing speech into
the transcript. `summary.audit_by_label` keeps the raw local-recall audit labels; the top-level
`possible_lost_me_*` and `needs_review_*` counters follow the effective session-quality metrics after
review decisions. Its purpose is to keep possible lost-`Me` examples visible as a corpus regression
queue without reopening already-reviewed items. `next_commands` points to `check_local_recall` for
the first complete blocking session, or to `murmurmark process ...` when only incomplete blocking
sessions remain.

`murmurmark corpus local-recall-repair` aggregates `local_recall_repair_v1` reports into:

```text
sessions/_reports/local-recall-repair/
  local_recall_repair_corpus_report.json
  local_recall_repair_corpus_items.jsonl
  local_recall_repair_corpus_report.md
```

`local_recall_repair_corpus_report.json` uses
`murmurmark.local_recall_repair_corpus_report/v1`:

```json
{
  "schema": "murmurmark.local_recall_repair_corpus_report/v1",
  "summary": {
    "session_count": 10,
    "repaired_session_count": 10,
    "missing_repair_report_count": 0,
    "missing_input_session_count": 0,
    "sessions_with_repairs": 1,
    "reviewable_sessions_with_repairs": 1,
    "incomplete_sessions_with_repairs": 0,
    "eligible_items": 2,
    "applied_repairs": 1,
    "reviewable_applied_repairs": 1,
    "incomplete_applied_repairs": 0,
    "inserted_me_seconds": 1.3,
    "reviewable_inserted_me_seconds": 1.3,
    "incomplete_inserted_me_seconds": 0.0,
    "rejected_items": 8,
    "recommended_next_step": "review_inserted_local_recall_repairs"
  },
  "policy": {
    "mode": "explicit_profile",
    "auto_promotion": false,
    "inserted_me_turns_need_review": true
  },
  "next_commands": [
    {
      "id": "review_local_recall_repair_2026-06-26_17-31-17",
      "label": "Review inserted local-recall repairs for 2026-06-26_17-31-17.",
      "command": "murmurmark review lane check_local_recall --session sessions/2026-06-26_17-31-17",
      "session_id": "2026-06-26_17-31-17",
      "session": "sessions/2026-06-26_17-31-17"
    }
  ]
}
```

`local_recall_repair_corpus_items.jsonl` uses
`murmurmark.local_recall_repair_corpus_item/v1` and keeps inserted `Me` rows plus rejected
local-recall repair items. This report is a promotion checkpoint: it shows whether
`local_recall_repair_v1` produced useful candidates, but does not make the profile automatic.
Inserted repair rows include `ready_for_review`; corpus `next_commands` point to
`check_local_recall` only for complete sessions. If inserted repairs exist only in incomplete
sessions, the next command points back to `murmurmark process ...` first.

### Remote Leak Segment Repair Plan

`murmurmark repair remote-leak` is an audit-only planning step. It reads
`derived/audit/audio-review-pack/audio_review_audit.jsonl`, selects rows where
`classification.verdict == "probable_transcript_error"` and the label is either `remote_leak` or a
partial `remote_duplicate` that is unsafe for whole-utterance deletion, and writes:

```text
derived/transcript-simple/whisper-cpp/remote-leak-repair/
  remote_leak_segment_repair_plan.json
  remote_leak_segment_repair_items.jsonl
  remote_leak_segment_repair.md
```

`remote_leak_segment_repair_plan.json` uses
`murmurmark.remote_leak_segment_repair_plan/v1`:

```json
{
  "schema": "murmurmark.remote_leak_segment_repair_plan/v1",
  "summary": {
    "items": 15,
    "seconds": 63.46,
    "protect_local_content_items": 15,
    "protect_local_content_seconds": 63.46,
    "by_diagnostic": {
      "remote_leak_with_local_content_risk": 15
    }
  },
  "action_plan": [
    {
      "next_work": "implement_segment_level_remote_overlap_repair",
      "diagnostic": "protected_local_content_risk"
    }
  ],
  "policy": {
    "mode": "audit_only",
    "may_modify_transcript": false,
    "may_modify_raw_audio": false,
    "whole_me_drop_allowed": false
  },
  "recommended_next": "less sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md",
  "next_commands": [
    {
      "id": "open_remote_leak_segment_report",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md",
      "reason": "inspect the audit-only remote-leak segment plan"
    }
  ],
  "open_commands": [
    {
      "id": "open_remote_leak_segment_report",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair.md"
    },
    {
      "id": "open_remote_leak_segment_plan",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_plan.json"
    },
    {
      "id": "open_remote_leak_segment_items",
      "command": "less sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_items.jsonl",
      "path": "sessions/example/derived/transcript-simple/whisper-cpp/remote-leak-repair/remote_leak_segment_repair_items.jsonl"
    }
  ]
}
```

`remote_leak_segment_repair_items.jsonl` uses
`murmurmark.remote_leak_segment_repair_item/v1`. Each item keeps the source audit id, interval,
utterance ids, compact utterances, diagnostic label, proposed future patch type, scores, text/audio
evidence and ready clip commands from the source audit. v1 has five diagnostics:

- `remote_leak_with_local_content_risk`: local support or unique `Me` text makes whole-utterance
  deletion unsafe; future work should split or re-ASR local islands.
- `remote_leak_duplicate_like`: leak also looks textually similar to remote, but remains review or
  mark-only until stronger evidence exists.
- `remote_leak_plain`: likely leak evidence without enough local content; keep explicit mark-only
  evidence.
- `remote_duplicate_with_local_content_risk`: the duplicate is only part of a `Me` utterance, or the
  `Me` utterance has unique/protected local content; future work should preserve the unique local
  prefix/suffix and remove only verified duplicate segments.
- `remote_duplicate_whole_drop_candidate`: the duplicate likely belongs to the existing
  whole-utterance cleanup/review path, not the segment repair queue.

The top-level `recommended_next`, `next_commands` and `open_commands` make the audit-only plan
self-guiding. `murmurmark repair remote-leak` prints the same JSON commands after writing the plan.

The planner never writes a transcript profile and never changes raw CAF files. Its job is to turn a
wide `remote_leak`/partial-duplicate bucket into the next safe repair queue.

`murmurmark corpus remote-leak` aggregates those per-session plans into:

```text
sessions/_reports/remote-leak-segment/
  remote_leak_segment_corpus_report.json
  remote_leak_segment_corpus_items.jsonl
  remote_leak_segment_corpus_report.md
```

`remote_leak_segment_corpus_report.json` uses
`murmurmark.remote_leak_segment_corpus_report/v1`:

```json
{
  "schema": "murmurmark.remote_leak_segment_corpus_report/v1",
  "summary": {
    "session_count": 10,
    "planned_session_count": 10,
    "missing_plan_count": 0,
    "item_count": 15,
    "seconds": 63.46,
    "protect_local_content_items": 15,
    "protect_local_content_seconds": 63.46,
    "sessions_with_protect_local_content": 3,
    "reviewable_protect_local_content_items": 15,
    "reviewable_protect_local_content_seconds": 63.46,
    "reviewable_sessions_with_protect_local_content": 3,
    "incomplete_protect_local_content_items": 0,
    "incomplete_protect_local_content_seconds": 0.0,
    "incomplete_sessions_with_protect_local_content": 0,
    "recommended_next_step": "review_segment_level_remote_leak_risks"
  },
  "policy": {
    "mode": "audit_only",
    "may_modify_transcript": false,
    "may_modify_raw_audio": false
  },
  "next_commands": [
    {
      "id": "review_remote_leak_segment_2026-06-26_11-15-50",
      "label": "Review unique local content around protected remote leak/duplicate segments for 2026-06-26_11-15-50.",
      "command": "murmurmark review lane check_unique_me_content --session sessions/2026-06-26_11-15-50",
      "session_id": "2026-06-26_11-15-50",
      "session": "sessions/2026-06-26_11-15-50"
    }
  ]
}
```

`remote_leak_segment_corpus_items.jsonl` uses
`murmurmark.remote_leak_segment_corpus_item/v1` and keeps session id, source audit id, diagnostic,
proposal, interval, utterance ids, compact `Me`/`Colleagues` texts and `ready_for_review`. This
corpus report is the queue for future segment-level remote-leak repair work; it does not apply
repairs. `next_commands` points to `murmurmark corpus remote-leak --plan` when plans are missing,
to `murmurmark review lane check_unique_me_content --session ...` for complete sessions with
protected local content, or to `murmurmark process ...` when protected segments exist only in
incomplete sessions.

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

`murmurmark audit audio-review` prints a compact CLI summary after writing these files: profile,
item count, likely reliable seconds, probable transcript error seconds, stronger-audio-judge demand
and report path.

Session-quality reports may count an `uncertain / needs_stronger_audio_judge` row as
`audio_review_explained_by_reliable_*` instead of review burden when all selected `Me` utterances in
that row are covered by high-confidence `likely_reliable` audio-review intervals. This is a reporting
deduplication only: it does not change transcript profiles, does not delete utterances, and does not
apply to probable transcript errors or possible lost `Me` speech.

Session-quality reports may also count a very narrow `remote_leak / probable_transcript_error` row as
`audio_review_explained_by_strong_local_*` when local support is strong, remote similarity and text
overlap are low, and the row is better explained as real local speech with a boundary overlap. This
also affects only review accounting and queue selection; it does not edit transcript text.

An empty `review_pack_items.jsonl` is valid. In that case `audio_review_audit.jsonl` is empty,
`audio_review_summary.json.items` is `0`, `recommended_next_step` is
`no_extra_audio_judge_needed_for_current_pack`, and the pipeline should continue.

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

`murmurmark.faster_whisper_judge/v1` is the optional stronger local judge over the same review pack.
It uses a local CTranslate2 `faster-whisper` model on the existing clips and writes:

```text
derived/audit/audio-review-pack/
  faster_whisper_judge.jsonl
  faster_whisper_judge_summary.json
  faster_whisper_judge_report.md
```

Each JSONL row keeps the source pack item, copied utterance evidence, per-source transcripts and a
single classification:

```json
{
  "schema": "murmurmark.faster_whisper_judge/v1",
  "id": "fwj_000042",
  "source_pack_item_id": "arp_000042",
  "profile": "audit_cleanup_v2",
  "utterance_ids": ["utt_0123", "utt_0124"],
  "transcripts": {
    "mic_clean": {"text": "Да, я проверю логи.", "avg_logprob": -0.19},
    "remote": {"text": "Давайте посмотрим deploy.", "avg_logprob": -0.22}
  },
  "classification": {
    "label": "confirm_timing_or_doubletalk",
    "suggested_decision": "keep_me",
    "confidence": 0.84,
    "reason": "mic confirms Me; remote track confirms Colleagues"
  }
}
```

Valid labels are `confirm_me`, `confirm_remote_duplicate`, `confirm_asr_noise`,
`confirm_timing_or_doubletalk` and `uncertain`. The summary schema
`murmurmark.faster_whisper_judge_summary/v1` reports item counts, label buckets,
`suggested_keep_me_seconds`, `suggested_drop_me_seconds`, `skipped_reason` when the optional model is
missing, and `recommended_next_step`. These outputs are audit evidence only. They may improve
`review_lane_answers.<lane>.suggested.txt`, but they do not edit transcript profiles, Echo Guard
outputs or raw capture.
`likely_reliable` can also be emitted for benign ties when `double_talk`, `timing_overlap` and/or
local reliability are the strongest classes and all error classes stay below `60`. This avoids
escalating expected group-call timing overlap to a stronger judge.

`murmurmark.target_me_*` is the shadow Target-Me evidence layer. It reads the selected transcript
profile, `speaker_state.jsonl` and the existing audio review pack. It does not edit audio,
transcripts, cleanup profiles or raw capture.

Current outputs:

```text
derived/audit/target-me/
  target_me_enrollment.json
  target_me_audit.jsonl
  target_me_summary.json
  target_me_report.md

sessions/_reports/target-me/
  target_me_corpus_report.json
  target_me_corpus_report.md
```

`target_me_enrollment.json` v1 records how the local `Me` evidence model was built:

```json
{
  "schema": "murmurmark.target_me_enrollment/v1",
  "method": "mfcc_contrastive_v0",
  "profile": "reviewed_v1",
  "status": "ready",
  "accepted_count": 12,
  "accepted_total_sec": 63.75,
  "negative_accepted_count": 40,
  "negative_accepted_total_sec": 374.63,
  "calibration": {
    "target_threshold": 0.001032,
    "weak_target_threshold": -0.000595
  },
  "segments": [
    {
      "utterance_id": "utt_000064",
      "start": 556.32,
      "end": 565.46,
      "state": {"local_only_ratio": 1.0, "remote_active_ratio": 0.0}
    }
  ]
}
```

`target_me_audit.jsonl` v1 contains one row per risky review clip:

```json
{
  "schema": "murmurmark.target_me_audit/v1",
  "id": "tme_000004",
  "source_pack_item_id": "arp_000004",
  "utterance_ids": ["utt_000026", "utt_000027"],
  "state": {"local_only_ratio": 1.0, "remote_active_ratio": 0.0},
  "source_scores": {
    "mic_clean": {"target_similarity": 0.91},
    "remote": {"target_similarity": 0.42}
  },
  "classification": {
    "label": "target_me_confirmed",
    "suggested_decision": "keep_me",
    "confidence": 0.84,
    "reason": "mic_clean matches Target-Me voiceprint"
  }
}
```

Valid labels are:

- `target_me_confirmed`;
- `target_me_possible`;
- `target_me_absent_remote_like`;
- `target_me_absent`;
- `target_me_ambiguous`.

`target_me_confirmed` and `target_me_absent_remote_like` are evidence labels, not automatic edits.
They may become review suggestions only after the normal safety gates also agree. `mfcc_voiceprint_v0`
is the first baseline method. `mfcc_contrastive_v0` is the deterministic fallback when WavLM and
`resemblyzer` are not available: it uses clean remote speech as a negative centroid. The current
six-session contrastive
smoke produced ready enrollment on all six sessions, `102` audited clips, `0` helpful rows, `0`
corroborating rows and `0` review-burden reductions. That is a useful negative baseline: future
Target-Me work should compare against it with a real local speaker-embedding or target-speaker
separation model.

`resemblyzer_dvector_v0` is the first tested local speaker-embedding backend. It uses
`resemblyzer.VoiceEncoder` and writes the same v1 artifacts. Six-session smoke:

- `102` audited clips;
- `67` `target_me_confirmed` rows / `355.77s`;
- `13` `new_keep_evidence` rows / `48.82s`;
- `54` `corroborates_existing_evidence` rows / `306.95s`;
- readiness impact: `shadow_only_not_applied`, so actual session readiness counts are unchanged;
- research decision: `promising_shadow_evidence_continue`;
- promotion decision: `shadow_only_do_not_promote`.

This makes d-vector evidence promising as a review layer, especially for protecting true `Me`
utterances from older remote-duplicate heuristics. It is not an automatic transcript edit.

The first optional speaker-embedding backend is `wavlm_xvector_v0`. It uses a local
`microsoft/wavlm-base-plus-sv` directory through `transformers` `AutoModelForAudioXVector`.
Model files are never downloaded by the normal pipeline. If the local directory is missing,
`target_me_summary.json.status` is `missing_embedding_model` and no fallback is hidden when
`--method wavlm_xvector` was requested.

`target_me_summary.json.local_backend_probe` records which optional local backends were available
when the audit ran. The current probe distinguishes installed ASR utilities from actual speaker or
separation capability:

```json
{
  "speaker_embedding_ready": true,
  "wavlm_ready": false,
  "resemblyzer_ready": true,
  "separation_candidate_available": false,
  "modules": {
    "torch": {"available": true},
    "transformers": {"available": true},
    "faster_whisper": {"available": true},
    "resemblyzer": {"available": true},
    "speechbrain": {"available": false},
    "asteroid": {"available": false}
  }
}
```

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
`regression_corpus_summary.json` includes `recommended_next`, `next_commands` and `open_commands`:
the primary next command is `murmurmark corpus evaluate --corpus-dir ...`, while `open_commands`
points to `regression_corpus.md`.

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
The evaluation report also includes `recommended_next`, `next_commands` and `open_commands`: the
primary next command is `murmurmark corpus train-audio-judge --corpus-dir ...`, while
`open_commands` points to `regression_corpus_evaluation.md`.

Audio judge v0 is a local shadow classifier trained on the regression corpus:

```text
sessions/_reports/audio-judge-v0/
  audio_judge_v0_report.json
  audio_judge_v0_predictions.jsonl
  audio_judge_v0_cv_predictions.jsonl
  audio_judge_v0_queue_predictions.jsonl
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
    "cv_accuracy": 0.970297,
    "policy_accuracy": 0.990099
  },
  "evaluation_detail": {
    "confidence_buckets": [
      {"bucket": "0.90-1.00", "items": 78, "cv_accuracy": 1.0}
    ],
    "cleanup_precision_by_threshold": [
      {
        "predicted_label": "drop_error",
        "expected_label": "drop_error",
        "confidence_threshold": 0.93,
        "precision": 1.0,
        "recall": 0.611111
      }
    ],
    "high_confidence_errors": []
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

The audio-judge report includes `recommended_next`, `next_commands` and `open_commands`: the
primary next command is `murmurmark corpus taxonomy --corpus-dir ... --audio-judge-dir ...`, while
`open_commands` points to `audio_judge_v0_report.md`.

The model uses only numeric audio/text metrics, not `label`, `verdict`, readiness bucket, or free-text
content as features. The labels are still silver labels derived from current local metrics, so v0 is a
shadow judge for prioritisation and future cleanup experiments, not a human-quality audio oracle.
`audio_judge_v0_cv_predictions.jsonl` contains out-of-fold predictions for the same training rows,
so the report can show per-session weakness, confidence buckets and high-confidence mistakes without
training on the row being evaluated.
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

Audio error taxonomy is the action map over the same private reports:

```text
sessions/_reports/audio-error-taxonomy/
  audio_error_taxonomy_report.json
  audio_error_taxonomy_items.jsonl
  audio_error_taxonomy_report.md
```

`audio_error_taxonomy_report.json` uses `murmurmark.audio_error_taxonomy_report/v1`:

```json
{
  "schema": "murmurmark.audio_error_taxonomy_report/v1",
  "summary": {
    "items": 102,
    "sessions": 10,
    "total_seconds": 312.4
  },
  "by_class": {
    "remote_duplicate": {
      "items": 18,
      "seconds": 44.2,
      "recommended_action": "safe_cleanup_regression"
    },
    "lost_me": {
      "items": 9,
      "seconds": 21.8,
      "recommended_action": "local_recall_repair"
    }
  },
  "by_diagnostic": {
    "uncertain_duplicate_vs_leak": {
      "items": 7,
      "seconds": 18.6,
      "attention_seconds": 18.6,
      "suggested_actions": {
        "tighten_duplicate_vs_leak_rules": 7
      }
    }
  },
  "action_plan": [
    {
      "diagnostic": "remote_leak_with_local_content_risk",
      "next_work": "segment_level_remote_leak_repair",
      "deliverable": "audit-only segment suggestions for leak regions that preserve unique Me content"
    }
  ],
  "focus_areas": [
    {
      "class": "uncertain",
      "reason": "many items still need stronger audio judgement"
    }
  ]
}
```

The taxonomy report is read-only. It does not create transcript profiles and does not decide review
items. It exists to choose the next quality-hardening task: safer duplicate cleanup, local-recall
repair, boundary repair, stronger labels, or collecting more corpus examples.
Each `audio_error_taxonomy_items.jsonl` row keeps the original class plus a deterministic
`diagnostic.label`, for example `uncertain_duplicate_vs_leak`, `uncertain_remote_dominant`,
`remote_leak_with_local_content_risk`, or `timing_overlap_guard`. These labels are not human truth;
they are a routing layer for the next agent or reviewer.
`action_plan` is also routing metadata. It names the next narrow engineering work and expected
deliverable, but it does not apply patches.

Corpus gates are generated after session quality, corpus evaluation, audio judge, transcript-order,
local-recall, remote-leak segment corpus and operational readiness reports:

```text
sessions/_reports/corpus-gates/
  corpus_gates_report.json
  corpus_gates_report.md
  baseline.local.json
```

`corpus_gates_report.json` uses `murmurmark.corpus_gates_report/v1`:

```json
{
  "schema": "murmurmark.corpus_gates_report/v1",
  "status": "passed_with_warnings",
  "failed_gate_count": 0,
  "warning_count": 3,
  "recommended_next": "less sessions/_reports/corpus-gates/corpus_gates_report.md",
  "next_commands": [
    {
      "id": "open_corpus_gates_report",
      "command": "less sessions/_reports/corpus-gates/corpus_gates_report.md",
      "reason": "inspect corpus gate failures and warnings"
    }
  ],
  "open_commands": [
    {
      "id": "open_corpus_gates_report",
      "command": "less sessions/_reports/corpus-gates/corpus_gates_report.md",
      "path": "sessions/_reports/corpus-gates/corpus_gates_report.md"
    }
  ],
  "thresholds": {
    "min_complete_sessions": 3,
    "max_total_review_burden_ratio": 0.03,
    "max_audio_judge_cv_accuracy_drop": 0.03
  },
  "baseline": {
    "input": "sessions/_reports/corpus-gates/baseline.local.json",
    "write_path": null
  },
  "summary": {
    "complete_pipeline_count": 10,
    "selected_operational_complete_pipeline_count": 8,
    "excluded_diagnostic_session_count": 2,
    "ready_for_notes": 6,
    "audio_judge_cv_accuracy": 0.970297,
    "total_review_burden_ratio": 0.007082,
    "local_recall_complete_blocking_sessions": 0,
    "local_recall_selected_blocking_sessions": 0,
    "local_recall_selected_profile_blocking_sessions": 0,
    "local_recall_selected_profile_review_sessions": 1,
    "local_recall_possible_lost_me_seconds": 0.0,
    "remote_leak_segment_item_count": 15,
    "remote_leak_segment_protect_local_content_items": 15
  },
  "checks": [
    {
      "id": "baseline.ready_for_notes_not_lower",
      "status": "pass",
      "severity": "fail",
      "observed": 6,
      "threshold": ">= baseline 6 - 0"
    },
    {
      "id": "transcript.no_blocking_order_risk",
      "status": "pass",
      "severity": "fail",
      "observed": 0,
      "threshold": "0 sessions"
    },
    {
      "id": "local_recall.no_complete_blocking_sessions",
      "status": "pass",
      "severity": "fail",
      "observed": 0,
      "threshold": "0 selected operational sessions"
    },
    {
      "id": "local_recall.raw_complete_blocking_sessions",
      "status": "warn",
      "severity": "warn",
      "observed": 2,
      "threshold": "0 complete sessions in full historical corpus"
    },
    {
      "id": "remote_leak_segment.no_protected_local_content",
      "status": "warn",
      "severity": "warn",
      "observed": {
        "items": 15,
        "seconds": 63.46,
        "sessions": 3
      },
      "threshold": "0 protected-local-content items"
    }
  ]
}
```

`baseline.local.json` uses `murmurmark.corpus_gates_baseline/v1`. It is a private generated snapshot
of aggregate metrics and per-session gates used by `check-corpus-gates.py --baseline`. A baseline
built from real meeting sessions must stay under ignored `sessions/_reports/`. Baseline comparison
covers complete/ready session counts, review burden, audio judge metrics, per-session use/local
recall gates, selected-profile local-recall blockers, possible lost-`Me` seconds and protected
remote-leak queue growth. Current summaries and baselines also carry suggested review closure
metrics: auto-closed rows/seconds and remaining manual rows/seconds. Those metrics are informational
in v2; hard gates still come from use gate, review burden, local recall, transcript order and audio
judge checks.

Remote-leak segment corpus gates are intentionally warning-level. A pending queue means some `Me`
regions may still contain unique local content mixed with remote leak and need segment-level repair
or review. It must not be treated as permission for whole-utterance deletion.

Local-recall corpus gates are profile-aware. A selected operational session with remaining possible
lost-`Me` blockers fails `local_recall.no_complete_blocking_sessions`; a short non-blocking
`needs_review` island is a warning and stays in the review queue. The full historical local-recall
report is still surfaced through `local_recall.raw_complete_blocking_sessions`, but diagnostic
sessions and blockers already resolved by the selected transcript profile do not fail the operational
gate. Missing local-recall corpus reports or missing per-session audits remain warnings for backwards
compatibility.

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
    "prompt_file": "domain-packs/example-domain/whisper-prompt.ru.txt"
  },
  "outputs": {
    "quality_verdict": "derived/synthesis-simple/extractive/quality_verdict.json",
    "session_readiness": "derived/readiness/session_readiness.json",
    "selected_transcript_profile": "audit_cleanup_v4",
    "synthesis_selected_transcript_profile": "audit_cleanup_v4",
    "readiness_selected_profile": "audit_cleanup_v4",
    "verdict": "usable_with_review",
    "synthesis_verdict": "usable_with_review",
    "readiness_verdict": "usable_with_review",
    "use_gate": "review_first"
  },
  "recommended_next": "murmurmark review next sessions/2026-06-26_15-32-02",
  "next_commands": [
    {
      "id": "readiness_next",
      "command": "murmurmark review next sessions/2026-06-26_15-32-02",
      "reason": "continue from the refreshed session readiness state"
    },
    {
      "id": "refresh_report",
      "command": "murmurmark report sessions/2026-06-26_15-32-02",
      "reason": "refresh and inspect the post-process readiness summary"
    }
  ],
  "open_commands": [
    {
      "id": "open_pipeline_run_report",
      "command": "less sessions/2026-06-26_15-32-02/derived/pipeline-run/pipeline_run_report.json",
      "path": "sessions/2026-06-26_15-32-02/derived/pipeline-run/pipeline_run_report.json"
    }
  ],
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
records which existing stage commands were run and what final synthesis profile was selected. It is
also a machine-readable handoff. For `status: "planned"`, `recommended_next` is the matching
`murmurmark process SESSION` command and `next_commands` also includes `murmurmark next SESSION` for
inspecting current readiness. For a failed run, `recommended_next` opens the pipeline report so the
failing step and command tails are visible before rerunning.
The runner also prints the same stage names to stdout as live progress lines (`[run]`, `[passed]`,
`[failed]`, `[skip]`). Long-running stages emit heartbeat lines at the configured interval, for
example `[run] transcribe_current still running (120.4s)`, so a normal `murmurmark process` run does
not look stuck while Whisper is still working. Stage subprocesses run with stdin detached from the
terminal. This prevents `ffmpeg`, `whisper-cli` or nested scripts from being stopped by terminal
job-control when they attempt to read from stdin.

## Export Bundle

`murmurmark export SESSION` creates a local handoff bundle outside the session directory, under
`exports/private/<session-dir-name>/` by default. Raw audio is never copied.

Markdown format:

```text
index.md
quality_verdict.md
notes.md
transcript.md
export_manifest.json
```

Export Bundle Quality v1 makes those Markdown files user-facing, not raw copies of derived
debugging artifacts:

- `index.md` is the start page. It answers "Can I use this?", shows selected transcript profile,
  verdict, review burden, links to notes/transcript/evidence, review-needed regions, retention and
  privacy summary, and the next command.
- `quality_verdict.md` is a human trust report. It explains the verdict, review burden, main risk
  reasons and source-file presence.
- `notes.md` is an extractive working summary. Conversation outline, potential decisions, actions,
  risks and open questions are shown only with utterance IDs; review candidates remain marked
  `needs_review`.
- `transcript.md` is the full selected transcript rendered from `clean_dialogue*.json` when
  available. It keeps every utterance, role, timestamp, utterance ID, source track and review flag.
- `export_manifest.json` stays the machine-readable source for selected profile, files, warnings,
  blockers and next commands.

If `clean_dialogue*.json` or `evidence_notes*.json` is missing, export falls back to the existing
Markdown artifacts, but the manifest still records which source files were missing. Raw audio is
never copied.

With `--include-json`, the bundle also includes evidence/source JSON such as
`evidence_notes.<profile>.json`, `clean_dialogue.<profile>.json`, `quality_report.<profile>.json`
and `transcript.simple.<profile>.json`.

Obsidian format writes one self-contained frontmatter Markdown note plus `export_manifest.json`.
The note includes verdict, notes, review items and transcript content. It must not contain absolute
private paths or raw audio references.

`export_manifest.json` uses `murmurmark.export_manifest/v1`:

```json
{
  "schema": "murmurmark.export_manifest/v1",
  "bundle_quality": "v1",
  "status": "exported_with_warnings",
  "session_id": "2026-06-23T11-04-37Z_87bac5",
  "requested_profile": "auto",
  "selected_profile": "audit_cleanup_v2",
  "format": "markdown",
  "verdict": "usable_with_review",
  "use_gate": "ready_for_notes",
  "blockers": [],
  "warnings": ["quality_verdict:usable_with_review"],
  "readiness": {
    "use_gate": "ready_for_notes",
    "export_blockers": []
  },
  "files": {
    "transcript_md": {"path": "exports/private/.../transcript.md", "bytes": 12345}
  },
  "next": "murmurmark retention plan sessions/... --export-manifest exports/private/.../export_manifest.json",
  "next_commands": [
    {
      "id": "retention_plan",
      "label": "Plan local retention actions after this export.",
      "command": "murmurmark retention plan sessions/... --export-manifest exports/private/.../export_manifest.json"
    },
    {
      "id": "retention_payload",
      "label": "Inventory any external-provider payload before handoff.",
      "command": "murmurmark retention payload sessions/... --export-manifest exports/private/.../export_manifest.json"
    }
  ],
  "open_commands": [
    {
      "id": "open_notes_md",
      "label": "Read exported notes.",
      "command": "less exports/private/.../notes.md"
    }
  ],
  "export_commands": {
    "rerun": "murmurmark export sessions/... --format markdown --profile auto --out-dir exports/private",
    "debug_force": "murmurmark export sessions/... --format markdown --profile auto --out-dir exports/private --force"
  }
}
```

Default export blocks sessions whose `session_readiness.json` contains `export_blockers`, including
review-required sessions, incomplete pipeline and hard quality failures. `--force` may create an
export for debugging, but the manifest still records blockers, warnings and the readiness payload
used.

Blocked export writes `<session>.export_blocked.json` with the same schema, `status: "blocked"`,
the blockers, the readiness payload, legacy text `next`, structured `next_commands`, and
`export_commands`:

```json
{
  "schema": "murmurmark.export_manifest/v1",
  "status": "blocked",
  "blockers": ["risk:local_recall_possible_lost_me"],
  "next": "`murmurmark review next sessions/...`; rerun export after blockers are closed...",
  "next_commands": [
    {
      "id": "review_next",
      "label": "Refresh this session's review handoff.",
      "command": "murmurmark review next sessions/..."
    }
  ],
  "export_commands": {
    "rerun": "murmurmark export sessions/... --format markdown --profile auto --out-dir exports/private",
    "debug_force": "murmurmark export sessions/... --format markdown --profile auto --out-dir exports/private --force"
  }
}
```

The Swift CLI reads successful `export_manifest.json` files and blocked `*.export_blocked.json`
files, then prints a short handoff summary with output files, retention commands, or structured next
commands. When an export was forced while blockers remain, `recommended_next` follows readiness back
to `murmurmark process` or `murmurmark review next`; retention commands are printed only under
`debug_retention`.

Successful manifests are self-contained handoff artifacts. `next_commands` is the executable
post-export chain, usually retention planning and provider-payload inventory. `open_commands` is the
read-only bundle inspection chain. Forced exports with blockers keep the retention commands under
`debug_retention_commands` and keep readiness repair/review commands in `next_commands`.

## Finish Handoff

`murmurmark finish SESSION` is a CLI orchestration command over existing artifacts. It does not define
a new data schema and does not change capture, Echo Guard, ASR, transcript repair or synthesis.

The command:

1. refreshes `SESSION/derived/readiness/session_readiness.json`;
2. attempts `murmurmark export` semantics through `export-session-bundle.py`;
3. includes JSON evidence by default unless `--no-json` is passed;
4. writes `retention_plan.json` and `provider_payload_manifest.json` after a successful export;
5. prints one final `next: ...` line, usually a read-only `less ...` command for the exported bundle.

If export is blocked, `finish` writes the same `<session>.export_blocked.json` artifact as the export
command and points back to the review or processing command from readiness. It never deletes raw
audio; raw deletion remains possible only through explicit `murmurmark retention apply ...`.

## Retention Plan

`murmurmark retention plan SESSION` records how the current retention policy treats raw audio,
exports and external providers.

Output:

```text
SESSION/derived/retention/retention_plan.json
```

The plan uses `murmurmark.retention_plan/v1`. See
[retention-policy.md](retention-policy.md) for policy, plan and audit-event schemas.
The CLI prints a compact handoff summary after writing the plan, but the JSON file remains the
source of truth. When the session readiness gate is not exportable and no successful export manifest
exists yet, the summary's `recommended_next` follows readiness back to `murmurmark process` or
`murmurmark review next` rather than pointing at a blocked export.
The summary also prints a derived `status`: `waiting_for_export`,
`waiting_for_successful_export`, `ready_no_raw_deletion`, `ready_to_apply`, `applied` or an invalid
manifest blocker. When a manifest file exists but was produced by forced export with blockers, the
summary prints `export_successful: false`, the manifest status/reason and keeps the next command on
the earlier safe step.

`murmurmark retention payload SESSION` writes
`SESSION/derived/retention/provider_payload_manifest.json` using
`murmurmark.provider_payload_manifest/v1`. It inventories export files for possible external
handoff, records policy blockers and never sends data by itself. Its CLI summary repeats the
payload status, blockers and `raw_audio_included`/`sends_data` flags for quick review.

## Local CLI Config

`murmurmark.config.json` uses `murmurmark.config/v1`. It is local and ignored by git. The tracked
`murmurmark.config.example.json` documents safe defaults. Create the local file with
`murmurmark config init`; use `--force` only when you intentionally want to overwrite it from the
tracked example.

```json
{
  "schema": "murmurmark.config/v1",
  "transcription": {
    "model": "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin",
    "language": "ru",
    "prompt_file": null
  },
  "export": {
    "format": "markdown",
    "profile": "auto",
    "out_dir": "exports/private",
    "include_json": true,
    "force": false
  }
}
```

`murmurmark process` reads `transcription.*`. `murmurmark export` reads `export.*`.
Explicit command-line flags override config values.

`session_readiness.json` uses `murmurmark.session_readiness/v1`:

```json
{
  "schema": "murmurmark.session_readiness/v1",
  "session": "sessions/2026-06-26_15-32-02",
  "use_gate": "review_first",
  "recommendation": "review_flagged_audio_before_using_for_medium_risk_work",
  "selected_profile": "audit_cleanup_v4",
  "verdict": "usable_with_review",
  "risk_flags": ["audio_review_probable_errors"],
  "use_gate_reasons": [
    {
      "id": "risk:audio_review_probable_errors",
      "severity": "review",
      "message": "Session quality report raised a risk flag.",
      "value": "audio_review_probable_errors"
    }
  ],
  "review_blockers": ["risk:audio_review_probable_errors"],
  "export_blockers": ["risk:audio_review_probable_errors"],
  "warnings": [],
  "non_actionable_blockers": [],
  "recommended_next": "murmurmark review next sessions/2026-06-26_15-32-02",
  "next_commands": [
    {
      "id": "review_next",
      "label": "Refresh this session's review handoff and recommended first lane.",
      "command": "murmurmark review next sessions/2026-06-26_15-32-02"
    },
    {
      "id": "review_first_lane",
      "label": "Build the recommended first review lane pack.",
      "command": "murmurmark review first-lane --session sessions/2026-06-26_15-32-02"
    },
    {
      "id": "review_workspace",
      "label": "Build lane packs and answer sheets for this session.",
      "command": "murmurmark review workspace --session sessions/2026-06-26_15-32-02"
    },
    {
      "id": "review_workspace_apply",
      "label": "Apply edited review workspace answers.",
      "command": "murmurmark review workspace apply"
    },
    {
      "id": "review_apply",
      "label": "Apply closed review decisions and refresh reports when progress is ready.",
      "command": "murmurmark review apply"
    }
  ],
  "open_commands": [
    {
      "id": "open_quality_verdict",
      "label": "Read the quality verdict first.",
      "command": "less sessions/2026-06-26_15-32-02/derived/synthesis-simple/extractive/quality_verdict.audit_cleanup_v4.md"
    },
    {
      "id": "open_notes",
      "label": "Read selected evidence-backed notes.",
      "command": "less sessions/2026-06-26_15-32-02/derived/synthesis-simple/extractive/notes.audit_cleanup_v4.md"
    }
  ],
  "metrics": {
    "review_burden_sec": 42.5,
    "review_burden_ratio": 0.031,
    "notes_review_burden_sec": 42.5,
    "notes_review_burden_ratio": 0.031,
    "transcript_review_burden_sec": 74.2,
    "transcript_review_burden_ratio": 0.054,
    "notes_evidence_utterance_count": 24,
    "notes_needs_review_count": 2,
    "notes_needs_review_ratio": 0.083333,
    "needs_review_count": 49,
    "needs_review_ratio": 0.198,
    "audio_review_probable_error_count": 2,
    "audio_review_notes_probable_error_count": 1,
    "audio_review_stronger_judge_count": 6,
    "audio_review_notes_stronger_judge_count": 2,
    "synthesis_review_item_count": 12,
    "synthesis_review_item_seconds": 74.2,
    "synthesis_review_top_types": [
      {"type": "utterance_transcript_order_review", "count": 2, "seconds": 13.4}
    ],
    "local_only_island_recall": 0.875,
    "local_recall_recommended_next_step": "local_recall_risk_explained",
    "transcript_order_probable_order_risk_count": 0,
    "transcript_order_review_seconds": 0.0
  },
  "outputs": {
    "transcript": {
      "path": "derived/transcript-simple/whisper-cpp/resolved/transcript.audit_cleanup_v4.md",
      "exists": true
    },
    "notes": {
      "path": "derived/synthesis-simple/extractive/notes.audit_cleanup_v4.md",
      "exists": true
    },
    "transcript_order_review": {
      "path": "derived/audit/order/transcript_order_review.md",
      "exists": true
    }
  }
}
```

When `review_scope_complete` is true and `review_scope_remaining_seconds` is zero, a session may
still have residual risk flags without any actionable review rows. In that case
`non_actionable_blockers` contains `review_queue_exhausted`, `recommended_next` points to
`murmurmark status SESSION` or a linked evidence document, and `next_commands` must not include
`review_first_lane`. This is a documented blocker, not a hidden lane-pack task.

`use_gate` is the short per-session answer for practical use:

- `ready_for_notes`: use the notes with normal caution;
- `review_first`: review flagged regions before using the result for medium-risk work;
- `do_not_use_without_manual_review`: do not rely on the transcript without manual checking;
- `pipeline_incomplete`: rerun the full post-recording pipeline first;
- `pipeline_incomplete_review_first`: cleanup/synthesis profiles are missing or not selected yet.

`session_readiness.md` is the human-readable view of the same object and should be opened before
the transcript or notes. `export_blockers` is the machine-readable default export gate. `review_blockers`
is the list a review workflow should close before a medium-risk handoff. `recommended_next` is the
single primary executable next step, using the same action-first preference as the Swift CLI.
`next_commands` is the full executable command chain for the current state. `open_commands` is the
read-only inspection chain for selected notes, transcript, verdict and audit reports. Gates and
blockers remain the source of truth.

`review_burden_sec` and `review_burden_ratio` are the operational notes-review burden. They count
only review regions that can affect selected evidence-backed notes, plus blocking local-recall and
transcript-order risks. They are intentionally equal to `notes_review_burden_*` for compatibility
with older reports. Full transcript/export risk is kept separately in
`transcript_review_burden_sec`, `transcript_review_burden_ratio` and `export_blockers`.
`notes_needs_review_*` is computed only over utterances referenced by selected evidence-backed notes;
the older `needs_review_*` fields still describe the full selected transcript. A session may
therefore be `ready_for_notes` while still refusing default export with
`full_transcript_review_required` or `full_transcript_needs_review_required`.

The Swift CLI additionally prints a derived terminal-only `status`:

- `exported`: `ready_for_notes`, no export blockers and a successful default
  `exports/private/<session>/export_manifest.json`;
- `exportable`: `ready_for_notes` and no export blockers;
- `notes_ready_export_blocked`: `ready_for_notes`, no notes review blockers, but full
  transcript/export blockers remain; `next_commands` should point to export-review workspace or lane
  commands rather than asking the user to reread notes;
- `review_required`: review blockers or `review_first`;
- `incomplete`: pipeline-incomplete gate or blocker;
- `blocked`: hard no-use gate or export blockers not already classified as review/incomplete;
- `check_required`: fallback for unusual states.

`recommended_next` is an action-first view over `next_commands`: readiness generation and the CLI
prefer the first command that starts with `murmurmark process`, `murmurmark review`,
`murmurmark export`, `murmurmark retention`, or `murmurmark report`. If no such command exists, they
fall back to the first command from `next_commands`. This keeps report-reading commands such as
`less ...` visible while making the headline next step executable.
`murmurmark status SESSION` and `murmurmark report SESSION` use the default export manifest check
for their terminal summary: after a successful default export they print status `exported`,
`export_manifest` and retention as `recommended_next`.
`murmurmark next SESSION` is the compact view over this same object. It prints one primary
`command`, status, gate, selected profile, verdict and the first read-only `open_commands` item. With
`--refresh`, it regenerates session readiness through `report-session-quality.py` before reading the
command. If the session is exportable and a successful `export_manifest.json` exists, `next` follows
the manifest's post-export `next_commands` instead, usually retention planning. Forced exports or
manifests with blockers do not override readiness. `--export-manifest` points `next` at a non-default
export bundle.
`murmurmark sessions` uses the same default export manifest check for queue status: exported
sessions move from `exportable` to `exported`, and their `next` field points to retention instead of
repeating export.
`murmurmark next corpus` is the compact operational-readiness view for the whole sessions root. It
prints `corpus_next.command`, operational verdict, review burden, focus metadata and alternatives
from `operational_readiness_report.json`. With `--refresh`, it first regenerates session-quality and
operational-readiness reports; without `--refresh`, it only reads the existing report and points to
`murmurmark report corpus` when the report is missing. If the report's focus session/lane already
has `review_lane_pack.<lane>.json`, the compact handoff may use that pack as source
`review_lane_pack` and promote the prepared `afplay`/`less`/answer-sheet flow over rebuilding the
same lane pack. It also prints `answer_sheet_status`; once the answer sheet contains reviewed
answers, `command` switches to lane apply `--dry-run` and the non-dry-run apply command remains
visible as the follow-up. In this mode, `focus_pack_items`, `focus_pack_rows`,
`focus_pack_minutes`, `after_focus_pack_actions` and `after_focus_pack_rows` describe the already
prepared next review pack; the corpus-wide `review_actions` counter remains unchanged until the
answers are applied. If the first corpus command is export and the default successful
`exports/private/<session>/export_manifest.json` already exists, `next corpus` uses source
`export_manifest` and points to retention instead of repeating export.
The same terminal summary prints `handoff` commands for opening selected notes, transcript and
quality verdict. When the derived status is `exportable`, it also prints export and retention
commands. This is CLI presentation only; `outputs`, `next_commands`, gates and blockers remain the
machine-readable contract.

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
  "warnings": [
    "some_sessions_need_manual_review_before_use",
    "irreducible_manual_review_queue_present"
  ],
  "summary": {
    "session_count": 19,
    "all_session_count": 45,
    "excluded_diagnostic_session_count": 26,
    "excluded_diagnostic_sessions": ["audio-input-smoke", "test"],
    "use_gates": {
      "do_not_use_without_manual_review": 1,
      "ready_for_notes": 14,
      "review_first": 4
    },
    "total_review_burden_ratio": 0.001134,
    "total_notes_review_burden_sec": 51.65,
    "total_notes_review_burden_ratio": 0.001134,
    "total_transcript_review_burden_sec": 211.23,
    "total_transcript_review_burden_ratio": 0.004639,
    "corpus_readiness": "useful_for_audio_judge_v0",
    "audio_judge_readiness": "cleanup_shadow_candidate",
    "audio_judge_cv_accuracy": 0.901961,
    "audio_judge_review_queue": {
      "items": 34,
      "resolved_by_selected_profile_items": 6,
      "remaining_human_review_items": 34
    },
    "review_queue_items": 7,
    "review_queue_low_materiality_excluded": {
      "items": 25,
      "seconds": 44.44,
      "minutes": 0.74,
      "by_label": {
        "needs_review": 3,
        "remote_leak": 5,
        "uncertain": 17
      }
    },
    "review_action_count": 7,
    "grouped_review_row_count": 0,
    "irreducible_review": {
      "schema": "murmurmark.operational_irreducible_review/v1",
      "passed": true,
      "status": "pilot_ready_with_irreducible_review",
      "reasons": ["short_irreducible_review_queue"],
      "metrics": {
        "not_ready_sessions": 5,
        "review_queue_items": 7,
        "review_action_count": 7,
        "review_queue_seconds": 10.85,
        "review_queue_minutes": 0.18,
        "notes_review_burden_seconds": 51.65,
        "notes_review_burden_ratio": 0.001134,
        "failed_sessions": 0,
        "risky_sessions": 1,
        "not_ready_without_queue": [],
        "pending_safe_suggestions": []
      }
    },
    "by_review_action": {
      "check_local_recall_island": 3,
      "check_lost_local_speech": 3,
      "classify_audio": 1
    },
    "by_review_lane_actions": {
      "check_local_recall": 6,
      "classify_audio": 1
    },
    "by_review_lane_grouped_rows": {
      "check_local_recall": 0,
      "classify_audio": 0
    }
  },
  "promotion_plan": {
    "target": "medium_risk_ready",
    "current_verdict": "pilot_ready_with_review",
    "status": "manual_review_or_algorithmic_cleanup_needed",
    "outstanding_conditions": {
      "sessions_not_ready_for_notes": 5,
      "review_queue_items": 7,
      "review_action_count": 7,
      "grouped_review_row_count": 0,
      "review_queue_raw_audio_minutes": 0.18
    },
    "review_queue_strategy": {
      "first_recommended_lane": "check_unique_me_content",
      "quick_recommended_lane": "fast_confirm_drop",
      "first_recommended_reason": "reduce_largest_blocking_review_lane",
      "after_first_lane_estimate": {
        "remaining_items": 13,
        "remaining_actions": 13,
        "remaining_minutes": 0.82
      },
      "by_lane": [
        {
          "lane": "fast_confirm_drop",
          "items": 10,
          "actions": 10,
          "grouped_rows": 0,
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
  ],
  "next_commands": [
    {
      "id": "review_first_lane",
      "label": "Build the first review lane pack (fast_confirm_drop).",
      "command": "murmurmark review first-lane --session sessions/2026-06-26_11-15-50"
    },
    {
      "id": "review_workspace",
      "label": "Build all review lane packs and answer sheets.",
      "command": "murmurmark review workspace --session sessions/2026-06-26_11-15-50"
    }
  ]
}
```

`session_count` is the operational scope for working meetings, not necessarily every directory under
`sessions/`. Obvious diagnostic/smoke sessions whose ids contain markers such as `audio-input`,
`talk-routed`, `talk-audio-input`, or exact ids such as `smoke`, `test`, `talk-solo`,
`voice-processing-smoke`, are excluded from operational readiness and listed in
`excluded_diagnostic_sessions`. They remain valid debug sessions and are not deleted.

The operational verdict is not a transcript correctness proof. It is a use-readiness summary for
piloting MurmurMark on medium-risk meeting notes with explicit notes review burden, per-session use
gates and a prioritised review queue. Transcript/export review is reported separately and can still
block `murmurmark export` after notes are ready.

`pilot_ready_with_review` means the working corpus no longer has a hidden structural blocker, but
still has a short explicit operator queue. The report may reach this state from `not_ready` only when
`summary.irreducible_review.passed` is true: there are no failed working sessions, no pending safe
suggestions, no not-ready sessions without a queue or non-actionable explanation, and the remaining
mandatory review queue stays below the configured small-queue limits. This is a convergence gate,
not an auto-approval gate: the listed rows must remain visible until a human or a stronger local
evidence layer closes them.

`promotion_plan` is the bridge from current pilot status to the target state. It names remaining
conditions, sessions that are not yet `ready_for_notes`, notes review minutes and the next actions
needed to reduce uncertainty. It is report-only: it never edits transcripts or cleanup profiles.
`summary.review_queue_low_materiality_excluded` is also report-only. It counts short low-content
`remote_leak` / `uncertain` review rows, short low-content transcript-order overlaps and short
remote backchannel overlaps that were part of the selected top queue but were kept outside the
mandatory operator queue. These rows are not deleted, marked resolved or removed from the underlying
session-quality metrics; they only keep `review_queue`, `review_action_count`,
`murmurmark report corpus` and `murmurmark next corpus` focused on content-bearing review work.
`review_queue_strategy` is also report-only. It groups the remaining review queue into workflow
lanes, recommends the first lane to close, and estimates the remaining queue after that first lane is
reviewed. It reports both raw `items` and packed `actions`; `grouped_rows` is the number of raw rows
that can share one answer because they have the same lane, action, allowed decisions and first `Me`
utterance. The estimate is not a substitute for rerunning `apply-review-decisions-batch.py`; it is a
planning aid for reaching `medium_risk_ready`.
`murmurmark review next SESSION` prints the same strategy as a terminal handoff: `first_lane`,
`quick_lane`, `first_lane_reason`, `after_first_lane`, `quick_lane_flow` and `workspace_flow`.
When the review plan carries packed-action metrics, it also prints `review_actions`,
`grouped_review_rows` and `remaining_actions` in `after_first_lane`. These fields explain the review
order; transcript changes still require explicit review answers.
If the refreshed session gate no longer requires review, `review next` ignores any stale
`SESSION/derived/readiness/review-plan/review_plan.json` and prints `reason: no_review_required`
with `recommended_next: murmurmark next SESSION`. The regular `next` command remains the source of
truth for exportable, exported, blocked and incomplete sessions.
If the gate still requires review but the local plan has `review_action_count: 0`, `review next`
prints `review_handoff: no_actionable_review_rows` and points to `status`/`report`/readiness
documents. It must not recommend `review first-lane` unless the pack is non-empty.
Top-level `next_commands` is the executable handoff: structural blockers such as too few complete
pipelines point to the first concrete `murmurmark process sessions/<id>` target only when that target
is still pipeline-incomplete, and fall back to `murmurmark corpus process all` when no incomplete
target is known. Complete but risky sessions stay in review-oriented commands instead of being
reported as process targets. When a concrete review focus is known, review queues point to
`murmurmark review first-lane --session sessions/<id>` and
`murmurmark review workspace --session sessions/<id>`. If no focus session can be inferred, they
fall back to corpus-wide review commands.
`murmurmark review first-lane` reads this strategy from `review_plan.json` and builds the matching
lane pack through `build-review-lane-pack.py`.

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
    "review_action_count": 32,
    "grouped_review_row_count": 8,
    "cluster_count": 29,
    "sessions_with_review": 6,
    "estimated_listen_seconds": 215.52,
    "by_review_action": {
      "confirm_drop_or_keep_me": 21,
      "check_unique_me_content": 13
    },
    "by_review_lane": {
      "fast_confirm_drop": 10,
      "check_unique_me_content": 20,
      "check_transcript_order": 2
    }
  },
  "review_queue_strategy": {
    "first_recommended_lane": "check_transcript_order",
    "quick_recommended_lane": "fast_confirm_drop",
    "first_recommended_reason": "reduce_largest_blocking_review_lane",
    "after_first_lane_estimate": {
      "remaining_items": 38,
      "remaining_minutes": 1.93
    },
    "commands": {
      "build_first_lane_pack": ".venv/bin/python scripts/build-review-lane-pack.py --lane check_transcript_order"
    }
  },
  "review_lanes": {
    "fast_confirm_drop": {
      "title": "Fast confirm drop",
      "item_count": 10,
      "action_count": 10,
      "grouped_row_count": 0,
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
`review_lane` is a workflow hint, not a decision. `fast_confirm_drop` rows are the quickest complete
duplicate or ASR-noise checks, `check_unique_me_content` rows need a content check before any drop,
`check_local_recall` rows are audit-only possible missing-local-speech checks,
`check_transcript_order` rows are audit-only chronology checks, `confirm_benign` rows usually clear
harmless overlap, and `classify_audio` rows have no safe shortcut.
Transcript-order rows allow only `keep_me`, `needs_review` and `skip`; they never allow `drop_me`,
never move utterances and never edit transcript text. Their review rows include `mic_raw` and
`remote` playback commands around the crossed utterances, plus the full `transcript_order_review.md`
link.
`remote_duplicate` suggestions are coverage-aware. `drop_me` is suggested only when the suspicious
interval covers almost all of the whole `Me` utterance and the text match is strong. Partial duplicates keep
`drop_remote` in `allowed_decisions` when a reviewed remote utterance exists, but the hint becomes
`needs_review` with `review_action: "check_unique_me_content"` and `review_features` records the
coverage and text-similarity signals. Reviewers use `drop_remote` only when listening confirms that
the remote row is a duplicate of local speech.

`review_decisions.template.jsonl` contains editable `murmurmark.review_decision/v1` rows:

```json
{
  "schema": "murmurmark.review_decision/v1",
  "status": "todo",
  "decision": "todo",
  "allowed_decisions": ["drop_me", "drop_remote", "keep_me", "needs_review", "skip"],
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
  review_lane_probe.fast_confirm_drop.json
  review_lane_probe.fast_confirm_drop.md
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
  "inputs": {
    "template": "sessions/_reports/review-plan/review_decisions.template.jsonl",
    "decisions": "sessions/_reports/review-plan/review_decisions.jsonl",
    "fingerprints": {
      "template": {
        "path": "sessions/_reports/review-plan/review_decisions.template.jsonl",
        "exists": true,
        "size": 12345,
        "sha256": "..."
      },
      "decisions": {
        "path": "sessions/_reports/review-plan/review_decisions.jsonl",
        "exists": true,
        "size": 678,
        "sha256": "..."
      }
    }
  },
  "summary": {
    "selected_rows": 10,
    "item_count": 10,
    "skipped_count": 0,
    "duration_sec": 93.42
  },
  "recommended_next": "afplay sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
  "next_commands": [
    {
      "id": "listen_review_lane_pack",
      "command": "afplay sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
      "reason": "listen to the review lane audio pack"
    },
    {
      "id": "dry_run_review_lane_answers",
      "command": "murmurmark review lane apply fast_confirm_drop --manifest sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --decisions-out sessions/_reports/review-plan/review_decisions.jsonl --answers-file sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt --dry-run",
      "reason": "validate manual decisions before applying"
    }
  ],
  "open_commands": [
    {
      "id": "open_review_lane_pack",
      "command": "less sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.md",
      "reason": "inspect review lane evidence"
    },
    {
      "id": "edit_review_lane_answers",
      "command": "$EDITOR sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt",
      "reason": "fill manual review decisions"
    }
  ],
  "manual_flow": {
    "dry_run": "murmurmark review lane apply fast_confirm_drop --manifest sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --decisions-out sessions/_reports/review-plan/review_decisions.jsonl --answers-file sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt --dry-run",
    "apply": "murmurmark review lane apply fast_confirm_drop --manifest sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --decisions-out sessions/_reports/review-plan/review_decisions.jsonl --answers-file sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt"
  },
  "items": [
    {
      "index": 1,
      "review_row_key": "review:arp_000020:2026-06-26_11-15-50:review_cluster_0015:utt_000269,utt_000271:1455.04:1460.66:remote_duplicate",
      "review_row_keys": [
        "review:arp_000020:2026-06-26_11-15-50:review_cluster_0015:utt_000269,utt_000271:1455.04:1460.66:remote_duplicate"
      ],
      "source": "audio_review",
      "source_audit_id": "arp_000020",
      "source_audit_ids": ["arp_000020"],
      "grouped": false,
      "group_size": 1,
      "session_id": "2026-06-26_11-15-50",
      "input_profile": "audit_cleanup_v2",
      "utterance_ids": ["utt_000269", "utt_000271"],
      "me_utterance_ids": ["utt_000269"],
      "remote_utterance_ids": ["utt_000271"],
      "pack_start": 0.0,
      "pack_end": 9.62,
      "pack_start_time": "00:00.000",
      "pack_end_time": "00:09.620",
      "suggested_decision": "drop_me",
      "suggested_decision_reason": "whole_me_duplicate",
      "allowed_decisions": ["drop_me", "drop_remote", "keep_me", "needs_review", "skip"],
      "command_key": "stereo_clean_left_remote_right",
      "command": "ffplay -hide_banner -loglevel error ...",
      "text": "me: ... | remote: ...",
      "evidence_text": [
        {"role": "me", "text": "..."},
        {"role": "remote", "text": "..."}
      ],
      "review_hint": {
        "focus": "Check whether the Me utterance contains unique local speech outside the remote overlap.",
        "short_focus": "unique Me content outside remote overlap",
        "why_review_required": "Dropping the whole Me utterance may remove real local speech.",
        "risk_factors": [
          "remote_duplicate evidence may cover only part of Me",
          "partial Me overlap; a whole-utterance drop is risky"
        ],
        "decision_guide": [
          {"decision": "keep_me", "when": "Me contains real local speech or a unique continuation."},
          {"decision": "drop_me", "when": "Me is only remote duplicate/noise and has no unique local content."},
          {"decision": "needs_review", "when": "Double-talk, garbled ASR or partial overlap makes the content ambiguous."}
        ],
        "evidence_features": {
          "labels": ["remote_duplicate"],
          "mean_me_overlap_coverage": 0.33,
          "mean_text_similarity": 1.0
        }
      }
    }
  ]
}
```

`inputs.fingerprints` stores SHA-256 fingerprints for the template and decisions files used to build
the pack. `murmurmark next corpus` uses those fingerprints to keep an already prepared pack active
after a harmless report refresh, while still rejecting it after real review-template or answer-file
changes.
`review_row_key` is the stable row identity for applying answers. `source_audit_id` is useful for
display but is not guaranteed to be unique across clustered review rows. `source`, `input_profile`
and utterance id arrays are copied from the review template so a lane pack remains auditable even
when it is opened outside the full `review_plan.json`.
For lanes such as `check_transcript_order`, `check_unique_me_content` and `classify_audio`,
`build-review-lane-pack.py` may group several rows that point to the same exact set of `Me`
utterance ids. In that case `grouped` is `true`, `group_size` is greater than `1`, and
`review_row_keys` / `source_audit_ids` contain every underlying row. The grouped pack item exposes
only the intersection of allowed decisions across those rows, so mixed rows do not accidentally
offer `drop_remote` when one of the rows cannot accept it. Applying one answer to that pack item
writes the same decision to each listed review row; no transcript profile is edited until the normal
review batch apply step runs. Single-lane CLI packs can also include same-`Me` rows from the paired
`check_unique_me_content` / `classify_audio` lane; workspace packs keep lanes separate to avoid
duplicating open rows across answer sheets.

The Swift CLI prints a compact handoff for the same manifest: selected lane, audio, Markdown, answer
sheet, suggested answer sheet, the first `answers=...` line from the suggested sheet, and ready-to-run
`afplay`, `less`, `$EDITOR`, `dry_run` and `apply` commands.

`probe-review-lane-pack-audio.py` is an optional review aid for hard lanes. It reads
`review_lane_pack.<lane>.json`, reruns whisper.cpp on per-track clips referenced by lane items
(`mic_clean`, `mic_role_masked`, `remote` by default), and writes:

```text
review_lane_probe.<lane>.json
review_lane_probe.<lane>.md
```

The JSON uses `murmurmark.review_lane_audio_probe/v1`:

```json
{
  "schema": "murmurmark.review_lane_audio_probe/v1",
  "input": {
    "manifest": "sessions/.../review_lane_pack.check_unique_me_content.json",
    "lane": "check_unique_me_content"
  },
  "parameters": {
    "language": "ru",
    "model": "~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin",
    "tracks": ["mic_clean", "mic_role_masked", "remote"],
    "dry_run": false
  },
  "summary": {
    "items": 9,
    "source_clips": 13,
    "track_probes": 39,
    "missing_clips": 0
  },
  "items": [
    {
      "index": 1,
      "source_audit_ids": ["arp_000098"],
      "label": "remote_duplicate",
      "suggested_decision": "needs_review",
      "evidence": {
        "me_text": "На что завязаться...",
        "remote_text": "..."
      },
      "sources": [
        {
          "source_audit_id": "arp_000098",
          "tracks": [
            {
              "track": "mic_clean",
              "clip": "sessions/.../arp_000098_mic_clean.wav",
              "exists": true,
              "text": "Окей, да, пока непонятно...",
              "scores": {
                "token_overlap_to_me": 0.71,
                "token_overlap_to_remote": 0.27
              }
            }
          ]
        }
      ]
    }
  ]
}
```

The probe is not a decision source and does not edit answer sheets. It exists to make AI-agent or
human review faster and more auditable, especially when `mic_clean` and `remote` decode to similar
phrases and automatic cleanup should remain conservative.
The manifest also stores that handoff as `recommended_next`, `next_commands`, `open_commands`,
`manual_flow`, `suggested_flow` and `after_apply`, so agents can continue the review loop without
scraping terminal text.

The Markdown index is intentionally self-contained for human review. It starts with the compact
shortcut protocol, then lists each item with allowed decisions, suggested decision reason, utterance
ids, selected audio command, role-separated evidence text and `Review focus` / decision-guide hints.
The answer sheet repeats the short hint as `focus=...` in each item comment. Tooling should still
read the JSON manifest; the Markdown and answer sheet are the reviewer-facing views.

Lane packs are listening aids only. The generated answer sheet starts with `answers=...`, where `.`
means `todo`; it is not applied until `murmurmark review lane apply <lane>` or the lower-level
`apply-review-lane-pack-decisions.py` script is run. The generated `.suggested.txt` sheet mirrors
existing `suggested_decision` values that are allowed for each row. It is a review aid, not a
transcript edit by itself.
Lane packs do not modify transcript profiles.
`murmurmark review workspace` builds all currently-open lane packs and writes:

```text
sessions/_reports/review-plan/
  review_workspace.json
  review_workspace.md
  lane-packs/review_lane_answers.<lane>.txt
```

The Swift CLI also prints a per-lane handoff from `review_workspace.json`: source row count, packed
item count, grouped rows saved, estimated minutes, suggested `answers=...`, and ready
`afplay`/`$EDITOR` commands for each lane pack.
The workspace JSON also stores top-level `recommended_next`, `next_commands`, `open_commands`,
`manual_flow`, `suggested_flow` and `after_apply`. The first commands come from the first generated
lane pack, then continue through workspace dry-run/apply/progress.

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
  "recommended_next": "afplay sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
  "next_commands": [
    {
      "id": "first_lane_1",
      "command": "afplay sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
      "reason": "continue with the first review lane"
    },
    {
      "id": "dry_run_review_workspace",
      "command": "murmurmark review workspace apply --workspace sessions/_reports/review-plan/review_workspace.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --out sessions/_reports/review-plan/review_decisions.jsonl --report sessions/_reports/review-plan/review_workspace_apply_report.json --dry-run",
      "reason": "validate all lane answer sheets before applying"
    }
  ],
  "open_commands": [
    {
      "id": "open_review_workspace",
      "command": "less sessions/_reports/review-plan/review_workspace.md",
      "reason": "inspect the review workspace index"
    }
  ],
  "manual_flow": {
    "dry_run": "murmurmark review workspace apply --workspace sessions/_reports/review-plan/review_workspace.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --out sessions/_reports/review-plan/review_decisions.jsonl --report sessions/_reports/review-plan/review_workspace_apply_report.json --dry-run",
    "apply": "murmurmark review workspace apply --workspace sessions/_reports/review-plan/review_workspace.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --out sessions/_reports/review-plan/review_decisions.jsonl --report sessions/_reports/review-plan/review_workspace_apply_report.json"
  },
  "lanes": [
    {
      "lane": "fast_confirm_drop",
      "status": "ok",
      "selected_rows": 10,
      "items": 10,
      "grouped_item_count": 0,
      "grouped_row_count": 0,
      "audio": "sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.wav",
      "answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt",
      "suggested_answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.suggested.txt"
    }
  ]
}
```

The workspace is a reviewer index only. It writes lane answer sheets with `answers=...` placeholders,
where `.` means `todo`. It does not write decisions; use
`murmurmark review workspace apply`, `murmurmark review lane apply <lane>`,
`apply-review-lane-pack-decisions.py`, or
`review-decisions-cli.py` for that.
`murmurmark review lane apply <lane>` applies the lane's generated answer sheet using the normal
session-local or corpus-local paths, and prints the next review command.
`murmurmark review lane apply first` resolves `first` through
`review_queue_strategy.first_recommended_lane` in the active `review_plan.json`. After a non-dry run,
the command also writes `review_decisions_progress.json` and only recommends `review apply` when
`ready_for_batch_apply` is true. If `murmurmark review progress` finds an existing lane pack for the
first remaining lane, it may expose `prepared_lane_pack`, `afplay`, `less`, `$EDITOR`, dry-run and
apply commands as the next handoff instead of recommending another lane-pack build. It includes
`answer_sheet_status`; when reviewed answers are present, `recommended_next` becomes the dry-run
apply command for that lane.
`murmurmark review lane apply <lane> --answers-source suggested` reads
`review_lane_answers.<lane>.suggested.txt` instead of the manual sheet. This mode is explicit,
prints `answers_source: suggested`, and is mutually exclusive with `--answers` and `--answers-file`;
use it with `--dry-run` before writing decisions.
`apply-review-lane-pack-decisions.py` is the same operation as a lower-level script: it applies
explicit reviewer answers for a lane pack back into the complete `review_decisions.jsonl`. It accepts
either `--answers` with a compact answer string in pack order, or `--answers-file` pointing to a text
file with an `answers=...` line:
`d=drop_me`, `c=drop_remote`, `k=keep_me`, `r` or `?=needs_review`, `s=skip`, and
`.`/`n`/`t=todo`. The script validates each answer against `allowed_decisions`, writes
`review_source: "lane_pack"`, and emits
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
  },
  "recommended_next": "murmurmark review progress",
  "next_commands": [
    {
      "id": "refresh_review_progress",
      "command": "murmurmark review progress",
      "reason": "refresh review progress"
    },
    {
      "id": "apply_review_decisions",
      "command": "murmurmark review apply --decisions sessions/_reports/review-plan/review_decisions.jsonl --review-template sessions/_reports/review-plan/review_decisions.template.jsonl",
      "reason": "materialize reviewed decisions into transcript profile"
    }
  ],
  "open_commands": [
    {
      "id": "open_review_lane_apply_report",
      "command": "less sessions/_reports/review-plan/review_lane_pack_apply_report.json",
      "reason": "inspect lane apply report"
    }
  ]
}
```

`--dry-run` writes the same report without changing `review_decisions.jsonl`. The report always
includes `recommended_next`, `next_commands` and `open_commands`, so the Swift CLI and agents can
continue without scraping terminal output. The Swift CLI prints that report as `lane_items` and
`lane_result`. If rows remain `todo`, it points back to the lane Markdown and answer sheet; otherwise
it shows the next non-dry-run command.

`murmurmark review workspace apply` applies every lane answer sheet referenced by
`review_workspace.json` into one `review_decisions.jsonl`. It validates item counts, answer
shortcuts and `allowed_decisions`; with `--answers-source suggested` it reads
`suggested_answer_sheet` instead of the manual `answer_sheet`, and with `--require-complete` it fails
if any selected workspace answer is still `todo`. With `--allow-partial`, suggested or manual
workspace apply may write the reviewed rows while preserving `todo` rows as the remaining manual
queue. This is the path used by `murmurmark review suggested apply SESSION`: it closes only generated
answers that are already actionable `keep_me`/`drop_me`, keeps dotted rows as manual review, then
`review apply --allow-partial-review` materializes a reviewed profile only when at least one safe row
was closed. If generated suggested sheets contain only dots, no decisions are written and the next
command points to the first remaining manual lane. The Swift workspace handoff prints
`suggested_dry_run` and `suggested_apply` commands whenever the workspace has suggested sheets. It
writes decisions cumulatively: already reviewed rows that are no longer present in a regenerated
template remain in `review_decisions.jsonl`, and `suggested_closure.closed_by_suggestions` is computed
by stable review-row keys rather than list positions. This keeps `review progress`, `status`,
`report`, session-quality and `suggested_closure.remaining_manual_queue` aligned on the same
remaining rows and seconds.

`review suggested` is cached-first for expensive local model evidence. It consumes existing
`faster_whisper_judge.jsonl` and Target-Me rows when building lane suggestions. New faster-whisper
decodes during suggested review are opt-in through `MURMURMARK_TARGETED_JUDGE_COMPUTE=1`; Target-Me
refresh during suggested review is opt-in through `MURMURMARK_REVIEW_TARGET_ME_REFRESH=1`.

The workspace apply report is written to:

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
    "ready_for_batch_apply": false,
    "ready_for_partial_apply": true,
    "partial_apply_allowed": true
  },
  "suggested_closure": {
    "schema": "murmurmark.suggested_review_closure/v1",
    "answers_source": "suggested",
    "status": "partial_apply_ready",
    "before": {
      "manual_rows": 12,
      "manual_seconds": 42.2
    },
    "after": {
      "manual_rows": 5,
      "manual_seconds": 8.84
    },
    "readiness_projection": {
      "before_state": "review_required",
      "after_state": "review_required",
      "effect": "manual_review_reduced",
      "manual_rows_delta": -7,
      "manual_seconds_delta": -33.36,
      "requires_review_apply": false,
      "requires_manual_review": true
    },
    "generated_suggestions": {
      "rows": 12,
      "seconds": 42.2,
      "actionable_rows": 7,
      "actionable_seconds": 33.36,
      "needs_review_rows": 3,
      "needs_review_seconds": 6.35,
      "todo_rows": 2,
      "todo_seconds": 2.49,
      "by_decision": [
        {"key": "keep_me", "count": 7, "seconds": 33.36},
        {"key": "needs_review", "count": 3, "seconds": 6.35},
        {"key": "todo", "count": 2, "seconds": 2.49}
      ]
    },
    "closed_by_suggestions": {
      "rows": 7,
      "seconds": 33.36,
      "by_decision": [
        {"key": "keep_me", "count": 7, "seconds": 33.36}
      ],
      "items": [
        {
          "source_audit_id": "arp_000001",
          "review_lane": "classify_audio",
          "label": "timing_overlap",
          "decision": "keep_me",
          "reason": "stronger_audio_judge: confirm_timing_or_doubletalk",
          "evidence": {
            "stronger_audio_judge": {
              "labels": ["confirm_timing_or_doubletalk"],
              "max_confidence": 0.92
            }
          }
        }
      ]
    },
    "remaining_manual_queue": {
      "rows": 5,
      "seconds": 8.84,
      "by_lane": [
        {"key": "check_local_recall", "count": 2, "seconds": 2.49},
        {"key": "check_transcript_order", "count": 3, "seconds": 6.35}
      ]
    },
    "safe_decision_classes": {
      "keep_me": ["confirmed local Me speech", "confirmed timing/double-talk where both sides are real"],
      "drop_me": ["confirmed remote duplicate after safety gates", "confirmed short ASR noise after safety gates"],
      "needs_review": ["uncertain audio evidence", "local recall risk", "transcript order risk without matching high-confidence judge evidence"]
    }
  },
  "lanes": [
    {
      "lane": "fast_confirm_drop",
      "status": "ok",
      "markdown": "sessions/_reports/review-plan/lane-packs/review_lane_pack.fast_confirm_drop.md",
      "answer_sheet": "sessions/_reports/review-plan/lane-packs/review_lane_answers.fast_confirm_drop.txt",
      "summary": {
        "reviewed_count": 2,
        "todo_count": 0,
        "rejected_count": 0
      }
    }
  ],
  "recommended_next": "$EDITOR sessions/_reports/review-plan/lane-packs/review_lane_answers.check_local_recall.txt",
  "next_commands": [
    {
      "id": "edit_workspace_lane_answers",
      "command": "$EDITOR sessions/_reports/review-plan/lane-packs/review_lane_answers.check_local_recall.txt",
      "reason": "finish the first incomplete lane answer sheet"
    },
    {
      "id": "retry_review_workspace_dry_run",
      "command": "murmurmark review workspace apply --workspace sessions/_reports/review-plan/review_workspace.json --template sessions/_reports/review-plan/review_decisions.template.jsonl --out sessions/_reports/review-plan/review_decisions.jsonl --report sessions/_reports/review-plan/review_workspace_apply_report.json --dry-run",
      "reason": "rerun workspace validation"
    }
  ],
  "open_commands": [
    {
      "id": "open_review_workspace_apply_report",
      "command": "less sessions/_reports/review-plan/review_workspace_apply_report.json",
      "reason": "inspect workspace apply report"
    }
  ]
}
```

`suggested_closure` is present when `answers_source == "suggested"`. Its `status` is one of:
`ready_for_review_apply`, `partial_apply_ready`, `manual_review_required`, or `already_closed`.
`generated_suggestions` reports what the generated answer sheets proposed before applying them:
safe actionable decisions, explicit `needs_review` suggestions and untouched `todo` rows are counted
separately.
`readiness_projection` is a conservative pre-batch projection, not a claim that session readiness has
already changed. If `after_state` is `review_apply_ready`, the next required step is still
`murmurmark review apply ...` or the wrapper command that materializes the reviewed profile and
refreshes readiness.
`closed_by_suggestions.items[*].reason` and `evidence` explain every generated decision that was
converted into a reviewed row. `remaining_manual_queue` is intentionally separate from closed rows;
uncertain rows must remain there and must not disappear silently.

Workspace apply reports use the same handoff fields as lane apply reports:
`recommended_next`, `next_commands` and `open_commands`. Dry runs usually point to the first
incomplete lane answer sheet and a retry command. Non-dry runs point to progress refresh or the final
`murmurmark review apply` batch step when every selected row is reviewed. The Swift CLI prints these
fields directly when they are present, so the JSON report is the authoritative handoff for
workspace-apply output.

The Swift CLI prints `lane_progress` from the `lanes` array. For each lane it shows status,
reviewed/todo/rejected counts, then the remaining lane Markdown and answer sheet when
`todo_count > 0`.

`report-review-decisions-progress.py` summarizes the current edited review file before applying it:

```text
sessions/_reports/review-plan/
  review_decisions_progress.json
  review_decisions_progress.md
```

The JSON uses `murmurmark.review_decisions_progress/v1` and reports totals, remaining raw-audio
seconds, packed review-action progress, grouped progress by `review_lane` and `session_id`, and
validation errors:

```json
{
  "schema": "murmurmark.review_decisions_progress/v1",
  "summary": {
    "total": 40,
    "reviewed": 0,
    "remaining": 40,
    "action_count": 30,
    "reviewed_actions": 0,
    "remaining_actions": 30,
    "grouped_review_row_count": 10,
    "remaining_grouped_review_row_count": 10,
    "remaining_minutes": 1.73,
    "invalid_rows": 0,
    "ready_for_batch_apply": false
  }
}
```

The Swift CLI prints the same lane breakdown as `by_lane`, with reviewed/total, remaining row count,
remaining action count and remaining minutes per lane, then prints the next safe command.

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

`drop_me` removes whole reviewed `Me` utterances. `drop_remote` removes whole reviewed `Colleagues`
utterances when the remote row is a duplicate of local speech. `keep_me` keeps the utterance and can
clear its review flag. `needs_review` keeps the utterance marked for review. Conflicting decisions fail the
`reviewed_v1` gates. Coverage is also a hard gate by default: every template row for that session must be
closed with `drop_me`, `drop_remote`, `keep_me`, `needs_review`, or `skip`. If a row is missing or still `todo`,
the script still writes audit artifacts, but `review_decisions_report.reviewed_v1.json.gates.passed`
is `false` and `--transcript-profile auto` must not select `reviewed_v1`.

`--allow-partial-review` makes this gate explicit rather than silent. The report may pass with
`coverage.allowed: true`, `coverage.complete: false` and warning `partial_review_scope_allowed` when
at least one row was closed and the remaining rows are intentionally left for later. `coverage` must
also expose `missing_review_seconds`, `pending_review_seconds` and `remaining_review_seconds`.
Readiness must count those remaining seconds as review burden and must not treat partial review as a
fully checked transcript.
For `source: "local_recall"` rows, `drop_me` and `drop_remote` are invalid because the row points to a timeline-repair
island, not a transcript utterance. `keep_me` and `skip` close that local-recall risk as checked;
`needs_review` keeps it in the readiness burden. These rows are recorded in
`review_decisions_applied.reviewed_v1.jsonl` with `review_effect: "audit_only_local_recall"`.
For `source: "transcript_order"` rows, `drop_me` and `drop_remote` are also invalid. `keep_me` and `skip` close the
chronology risk as checked; `needs_review` keeps it in the readiness burden. These rows are recorded
with `review_effect: "audit_only_transcript_order"` and are mirrored into
`quality.transcript_order_review` on affected utterances. This metadata may mark the utterance as
still needing review, but it must not move utterances, split text, or edit timestamps.
`review-decisions-cli.py` and `apply-review-decisions.py` must honor `allowed_decisions`; for
example, they must not accept `drop_me` or `drop_remote` on `source: "local_recall"` rows even though
both decisions can be valid for ordinary audio-review rows.
The CLI may also render nearby transcript context from `clean_dialogue.<input_profile>.json`. This
context is display-only and must not change the decision schema or coverage accounting.
It may expose numbered playback shortcuts for commands already present in the review row; those
shortcuts are UI-only and must not alter the row schema.
It may print progress and suggested next shell commands after writing `review_decisions.jsonl`; this
is also UI-only and does not count as review coverage.
`suggested_decision` is only a review hint. It never changes transcript output by itself and does not
count as coverage. The reviewer must still copy the intended value into `decision`.

`agent_reviewed_v1` uses the same review-decision artifact shape, but the decision file is generated
by `build-agent-review-decisions.py` from audio-review audit rows and the audio-judge queue:

```text
sessions/_reports/review-plan/
  review_decisions.agent_reviewed_v1.jsonl
  review_decisions.agent_reviewed_v1.template.jsonl
  agent_review_report.agent_reviewed_v1.json
  review_decisions_apply.agent_reviewed_v1.json

derived/transcript-simple/whisper-cpp/resolved/
  clean_dialogue.agent_reviewed_v1.json
  transcript.agent_reviewed_v1.md
  transcript.simple.agent_reviewed_v1.json
  quality_report.agent_reviewed_v1.json
  overlaps.agent_reviewed_v1.json

derived/transcript-simple/whisper-cpp/review-decisions/
  review_decisions_report.agent_reviewed_v1.json
  review_decisions_applied.agent_reviewed_v1.jsonl
  review_decisions_rejected.agent_reviewed_v1.jsonl
  review_decisions_conflicts.agent_reviewed_v1.jsonl
```

`agent_review_report.agent_reviewed_v1.json.summary` includes both applied-decision counters and
rejected-candidate counters:

```json
{
  "decision_rows": 172,
  "rejected_candidate_rows": 1028,
  "by_decision": {"drop_me": 3, "keep_me": 169},
  "rejected_by_reason": {"remote_overlap_too_large": 45},
  "rejected_by_label": {"remote_leak": 511},
  "rejected_by_verdict": {"probable_transcript_error": 240},
  "rejected_by_reason_and_label": {"remote_overlap_too_large|remote_leak": 45},
  "top_rejected_reasons": [{"reason": "remote_overlap_too_large", "count": 45}]
}
```

These rejected aggregates are diagnostic only. They do not apply decisions and do not reduce review
coverage by themselves. Their purpose is to show which narrow evidence pattern is worth automating
next without widening existing cleanup gates.

The agent scope is deliberately smaller than the human review template. It may contain only rows that
the rules can close without listening. `drop_me` is allowed only for clear whole-utterance remote
duplicates or ASR noise with weak local support and no protected action/decision/risk markers.
`keep_me` can clear review burden for strong local-support rows, high-confidence audio-judge keep
rows, or bounded short `remote_leak` rows with unique local text, low remote similarity, no
duplicate/noise signal and no protected action/decision/risk marker. It can also clear short
`remote_leak` rows when `speaker_state.jsonl` covers the interval as near-pure `local_only`.
No-remote rows are the lowest-risk form; rows with remote context are eligible only when the remote
utterance overlap coverage is tiny, duplicate/noise signals are absent and the `Me` text has unique
local content. Protected markers require the strongest local-only evidence because the decision
keeps local speech rather than deleting it. Short `remote_duplicate` rows may also be cleared as
`keep_me` when the overlapping slice is locally confirmed, remote overlap coverage is tiny and the
`Me` text has a unique local token or continuation. The agent may also propagate `keep_me` to
additional `remote_leak` or `remote_duplicate` rows for the same exact `Me` utterance when another
row has already confirmed that utterance as local speech and the propagated row has no ASR-noise
signal. The agent can also keep short local-only rows labelled `asr_noise` when `speaker_state`
contradicts that label, and short adjacent `Me` continuations when the current utterance starts
immediately after another `Me` turn. It can also close the narrow transcript-order audit case where
a short remote backchannel such as `Спасибо` is fully inside a long confirmed `Me` turn, has no text
overlap with it, and only needs `keep_me` marking rather than text or timestamp repair.
If `faster_whisper_judge.jsonl` exists, the agent may use its high-confidence classes as extra
evidence: `confirm_me` and `confirm_timing_or_doubletalk` can produce `keep_me` for `remote_leak`,
while `confirm_remote_duplicate` and `confirm_asr_noise` can produce `drop_me` only when whole-row
duplicate/noise safety gates still pass. A confirmed local `Me` decision may propagate to sibling
`uncertain` rows for the same exact `Me` utterance. Rows not
present in the agent template remain unresolved and continue to contribute to review burden.
`agent_reviewed_v1` is
eligible for `auto` only when its own coverage gates pass; it ranks below `reviewed_v1` and above
automatic cleanup profiles.
`uncertain` rows may be cleared as `keep_me` only in the narrow no-error case: no remote duplicate,
remote leak or ASR-noise score, no remote utterance in the review row, near-full `Me` coverage, and a
mostly `local_only` `speaker_state` interval.

Operational readiness may still expose review rows after `agent_reviewed_v1` is selected. Those
rows are the remaining transcript/export surface, not a sign that the automatic layer was skipped.
As of the 2026-06-30 corpus baseline, this queue is tracked separately from notes readiness:
`14/15` working sessions are `ready_for_notes`, one session is `review_first`, selected notes review
is about `0.55 min`, remaining transcript/export review is about `3.05 min`, and actionable
`review_actions` is `0`.
Readiness inherits applied `local_recall` and `local_recall_repair` review decisions as well as
audio-review decisions. Closed local-recall rows with `keep_me`, `drop_me` or `skip` do not re-enter
`murmurmark next corpus`; unresolved possible lost speech remains visible in `check_local_recall`.
In the current corpus snapshot, the raw local-recall queue is empty because the remaining islands are
explained as harmless short/boundary/remote-covered cases.
Future cleanup or repair layers may reduce that queue only through explicit audit evidence and must
keep possible lost `Me` speech or semantic uncertainty visible to export gates.

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
When reports are refreshed, each applied session row also gets `post_apply_readiness` copied from
`SESSION/derived/readiness/session_readiness.json`: `use_gate`, `selected_profile`, `verdict`,
`recommendation`, `recommended_next` and `next_commands`. The top-level report also writes
`next_commands`; for one session they come from the refreshed readiness handoff, and for failed or
multi-session runs they point to the apply report or corpus report. This makes
`review_decisions_apply_report.json` a self-contained handoff for CLI wrappers and agents.
The Swift `murmurmark review apply` wrapper performs a preflight before running the batch command.
If the decisions file or review template is missing, it prints `review_apply: status: not_ready`,
the missing path kind and the next `review workspace` / `review progress` commands instead of
surfacing the lower-level Python failure.
If the files exist but `review_decisions_progress.json` is not `ready_for_batch_apply`, the wrapper
prints the progress summary, lane breakdown, and the next workspace/workspace-apply/progress command
chain, then exits without running the batch command.
After a successful single-session batch apply, the Swift wrapper reads the refreshed
`session_readiness.json`: `next` is the first readiness `next_commands[].command`, while
`report_next` keeps the explicit `murmurmark report SESSION` refresh command. If readiness is
missing, `next` falls back to `murmurmark report SESSION`.

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
cleanup as resolved. Raw audio-review summaries are still available inside each session for audit.
Operational `review_burden` is notes-scoped and follows the selected evidence utterance IDs; full
transcript/export risk remains visible through `transcript_review_burden_*` and `export_blockers`.

They also consume `derived/audit/local-recall/local_recall_audit.json`. A raw
`local_only_island_recall < 0.9` is blocking only when the audit is missing or reports possible lost
local speech. Explained short/weak islands stay visible in readiness metrics but do not by
themselves force `review_first`.
Blocking local-recall items are emitted into the operational review queue as `lost_me` or
`local_recall_needs_review` items. These rows may not have transcript utterance IDs yet; they carry
the timeline-repair parent candidate and a short `ffplay` command for the mic capture.

`murmurmark cleanup SESSION` is the normal CLI entry point for audit-informed cleanup profiles. It
wraps `scripts/apply-audit-cleanup.py`, forwards profile and mode options, writes the same JSON
artifacts as the script, then prints a compact summary with applied/rejected patches, dropped
seconds, harmful seconds after cleanup, gates and next commands. If cleanup gates fail, the JSON
artifacts are still authoritative, but the command exits non-zero after printing the summary.

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

`audit_cleanup_v6` has the same artifact shape with the `audit_cleanup_v6` suffix. It usually uses
`audit_cleanup_v5` as input after rebuilding `audio_review_audit.jsonl` for that profile. v6 reuses
the normal audio-review cleanup gates: only high-confidence `remote_duplicate` and short `asr_noise`
whole `Me` utterances can be dropped, while `remote_leak`, `lost_me`, `uncertain`, double-talk and
timing overlap stay mark-only. v6 must not consume `suggested_review_v1` directly and must not use
audio-judge queue predictions.

`audit_cleanup_v7` has the same profile-shaped artifact set with the `audit_cleanup_v7` suffix. It is
the first segment-level cleanup profile. It usually uses `agent_reviewed_v1` as input and consumes the
current `audio_review_audit.jsonl`.

v7 may edit only `Me` utterances where audio-review classifies a row as `remote_duplicate` with
verdict `probable_transcript_error`, low enough local support and either high remote similarity or a
text-proven duplicate match. Instead of dropping the whole utterance, it removes matched remote token
spans from the `Me` text. If the duplicate starts at the beginning of `Me`, v7 may also remove a short
ASR-glue tail before an obvious local continuation marker such as `Тогда`. The original mixed `Me`
utterance id is removed from `clean_dialogue.audit_cleanup_v7.json`; kept local fragments are written
as new `Me` utterances with ids like `<source_id>_seg01`. The patch JSONL uses action
`segment_remove_remote_duplicate` or `drop_me_after_segment_remote_duplicate_repair` and records:

```json
{
  "evidence": {
    "source": "audio_review_segment",
    "segment_repair": {
      "removed_blocks": [],
      "kept_segments": []
    }
  },
  "safety_checks": {
    "removed_token_count": 12,
    "removed_token_ratio": 0.54,
    "kept_segment_count": 1
  }
}
```

`quality_report.audit_cleanup_v7.json` includes
`segment_repaired_remote_duplicate_seconds`. For v7, cumulative cleanup metrics start from the input
profile's `audit_harmful_seconds_after`, and readiness reporting inherits review decisions from the
input profile, usually `agent_reviewed_v1`. Active audio-review rows are then recalculated after the
replacement, so rows pointing only to removed source `Me` ids no longer inflate the verdict or review
burden.

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
  },
  "recommended_next": "murmurmark synthesize sessions/2026-06-26_15-32-02 --transcript-profile audit_cleanup_v1",
  "next_commands": [
    {
      "id": "synthesize_cleanup_profile",
      "command": "murmurmark synthesize sessions/2026-06-26_15-32-02 --transcript-profile audit_cleanup_v1",
      "reason": "build quality verdict and notes from the cleanup profile"
    },
    {
      "id": "refresh_session_report",
      "command": "murmurmark report sessions/2026-06-26_15-32-02",
      "reason": "refresh readiness after cleanup-derived synthesis"
    }
  ],
  "open_commands": [
    {
      "id": "open_audit_cleanup_report",
      "command": "less sessions/2026-06-26_15-32-02/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v1.json",
      "path": "sessions/2026-06-26_15-32-02/derived/transcript-simple/whisper-cpp/audit-cleanup/audit_cleanup_report.audit_cleanup_v1.json"
    }
  ]
}
```

`recommended_next` is the profile-specific synthesis command. `next_commands` is the executable
post-cleanup chain; `open_commands` contains read-only inspection commands for the cleanup report
and generated cleanup transcript.

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

Synthesis must read this report before producing notes. `murmurmark synthesize SESSION` is the normal
CLI entry point for the deterministic extractive synthesis layer. It wraps
`scripts/synthesize-simple-extractive.py`, writes the same `quality_verdict`, `notes`,
`evidence_notes` and `review_items` artifacts, then prints the selected profile, verdict, risk count
and next commands. When the verdict still has review items, risk items or `usable_with_review`, the
Swift summary uses `murmurmark review next SESSION` as the primary handoff and omits export from
`next`. Export is suggested from synthesis only for a `good` verdict without review work.
The read-only `murmurmark notes` and `murmurmark transcript` summaries use the same review-aware
handoff while still listing the `less ...` command for the selected artifact.

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

## `offline_aec_v2_report.json`

`offline_aec_v2` is a shadow Echo Guard lab. These artifacts are diagnostic evidence, not selected
ASR input.

Files:

```text
derived/preprocess/audio/mic_clean_offline_aec_v2.wav
derived/preprocess/audio/echo_hat_offline_aec_v2.wav
derived/preprocess/audio/mic_clean_offline_aec_v2_<candidate>.wav
derived/preprocess/audio/echo_hat_offline_aec_v2_<candidate>.wav
derived/preprocess/echo/offline_aec_v2_report.json
derived/preprocess/echo/offline_aec_v2_candidates.jsonl
derived/preprocess/echo/offline_aec_v2_segments.jsonl
derived/preprocess/echo/offline_aec_v2_segment_switch_plan.jsonl
derived/preprocess/echo/offline_aec_v2_coverage_gate_plan.jsonl
derived/preprocess/echo/offline_aec_v2_delay_curve.jsonl
derived/preprocess/echo/offline_aec_v2_window_metrics.jsonl
derived/preprocess/echo/offline_aec_v2_asr_leak_report.json
derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json
```

Report schema:

```json
{
  "schema": "murmurmark.echo.offline_aec_v2_report/v1",
  "engine": "offline_aec_v2_v0",
  "mode": "shadow_only",
  "summary": {
    "selected_candidate": "nonlinear_tail160_remote_floor",
    "promotion_decision": "shadow_only_not_promoted",
    "candidate_gate_passed": true,
    "candidate_gate_reason": "shadow_candidate_passed_gates",
    "local_fir_remains_default": true,
    "asr_audit_mode": "faster_whisper_clip_audit",
    "asr_selected_candidate": "coverage_v2_remote_gate_local_fir",
    "asr_candidate_gate_passed": true,
    "asr_candidate_gate_reason": "remote_token_leak_reduced_without_local_recall_regression",
    "asr_selected_audio_candidate": "coverage_v2_remote_gate_local_fir",
    "asr_audio_candidate_gate_passed": true,
    "asr_audio_candidate_gate_reason": "remote_token_leak_reduced_without_local_recall_regression"
  },
  "baseline": {
    "local_fir": {
      "remote_only_median_reduction_db": 17.369,
      "harmful_remote_seconds_in_me_proxy": 121.0,
      "local_only_word_recall_proxy": 0.988
    }
  },
  "selected_candidate": {
    "candidate": "nonlinear_tail160_remote_floor",
    "score": 115.0,
    "promotion_decision": "shadow_candidate_passed_gates",
    "metrics": {
      "remote_only_median_reduction_db": 54.522,
      "harmful_remote_seconds_in_me_proxy": 1.0,
      "local_only_word_recall_proxy": 1.0,
      "opening_ack_recall_proxy": 1.0,
      "double_talk_local_recall_proxy": 0.954
    }
  }
}
```

Required invariants:

- `mode` is `shadow_only`;
- `summary.promotion_decision` is not `accepted_for_asr`;
- raw CAF files are not listed as outputs and are not modified;
- `local_fir_remains_default` stays true until a separate promotion decision exists;
- ASR token leakage fields may be null when `--asr-audit` is not run;
- if ASR audit is run, `asr_candidate_gate_reason` must be explicit even when proxy gates pass;
- a proxy pass never means default promotion.
- `remote_forbidden_token_guard` is a virtual token-level candidate. It has no standalone WAV output
  and must declare `candidate_kind: "token_guard"` and `base_candidate`.

`offline_aec_v2_segments.jsonl` contains one row per candidate per speaker-state window. Each row
must include:

```json
{
  "candidate": "nonlinear_tail160_remote_floor",
  "index": 42,
  "start_sec": 84.0,
  "end_sec": 86.0,
  "state": "remote_only",
  "segment_candidate_score": 142.3,
  "segment_candidate_rank": 1,
  "segment_candidate_selected": true
}
```

The segment rank is diagnostic. It is meant for future segment-local switching experiments and must
not be used to rewrite `mic_for_asr.wav` in v0.

`offline_aec_v2_segment_switch_plan.jsonl` explains the shadow switched audio candidate:

```jsonl
{"index":42,"start_sec":84.0,"end_sec":86.0,"state":"remote_only","selected_source":"nonlinear_tail160_remote_floor","reason":"remote_only_use_remote_floor"}
{"index":43,"start_sec":86.0,"end_sec":88.0,"state":"local_only","selected_source":"local_fir","reason":"preserve_local_or_uncertain"}
```

`offline_aec_v2_coverage_gate_plan.jsonl` explains the Coverage v2 audio candidate:

```jsonl
{"index":1,"start_sec":938.0,"end_sec":940.0,"selection_reason":"audio_review:remote_duplicate","expected_risk_type":"remote_duplicate","selected_source":"nonlinear_tail160_remote_floor","applied":true,"decision_reason":"coverage_risk_gate:remote_duplicate","state_mix":{"local_speech_ratio":0.0,"remote_only_ratio":1.0}}
{"index":2,"start_sec":120.0,"end_sec":124.0,"selection_reason":"local_recall:risk_item","expected_risk_type":"local_recall_risk","selected_source":"segment_switch_or_local_fir","applied":false,"decision_reason":"protected_local_or_order_risk","state_mix":{"local_speech_ratio":0.75,"remote_only_ratio":0.25}}
```

The Coverage gate candidate must stay shadow-only. It may use Coverage v2 audit windows and
speaker-state evidence to build a WAV, but it must not write `mic_for_asr.wav`.

Token-guard rows inside `offline_aec_v2_asr_leak_report.json` must preserve the base text and the
removed-token evidence:

```json
{
  "candidate_kind": "token_guard",
  "base_candidate": "segment_switch_remote_floor_local_fir",
  "text": "",
  "guard": {
    "removed_reason": "remote_forbidden_overlap",
    "removed_tokens": ["Да", "реально"]
  }
}
```

## `remote_forbidden_evidence.jsonl`

`remote_forbidden` is the hardened evidence layer built on top of the `offline_aec_v2` ASR clip
audit. It is review/status evidence only. It does not edit `clean_dialogue*.json`, does not replace
`mic_for_asr.wav`, and does not promote any Echo Guard candidate.

Files:

```text
derived/audit/remote-forbidden/remote_forbidden_evidence.jsonl
derived/audit/remote-forbidden/remote_forbidden_summary.json
derived/audit/remote-forbidden/remote_forbidden_review.md
sessions/_reports/remote-forbidden/remote_forbidden_corpus_report.json
sessions/_reports/remote-forbidden/remote_forbidden_corpus_report.md
```

Evidence row schema:

```json
{
  "schema": "murmurmark.echo.remote_forbidden_evidence/v1",
  "id": "rfg_remote_0002",
  "kind": "remote_forbidden_token",
  "source": "offline_aec_v2_asr_clip_audit",
  "interval": {"start": 938.0, "end": 940.0, "duration_sec": 2.0},
  "transcript_links": {
    "me_utterance_ids": [],
    "remote_utterance_ids": ["utt_000153"]
  },
  "selection": {
    "profile": "coverage_v2",
    "selection_reason": "audio_review:remote_duplicate",
    "expected_risk_type": "remote_duplicate",
    "priority": 100,
    "source_artifacts": ["derived/audit/audio-review-pack/audio_review_audit.jsonl"],
    "source_row_ids": ["arp_000034"]
  },
  "speaker_state": {
    "dominant_state": "remote_only",
    "remote_active_ratio": 1.0,
    "local_active_ratio": 0.0
  },
  "texts": {
    "remote_reference": "Да, реально, вот он пришел, такой господин.",
    "local_fir": "Да, реально, он пришел, такой господин.",
    "base_candidate": "Да, реально, вот пришел...",
    "guarded_candidate": ""
  },
  "tokens": {
    "remote": ["Да", "реально", "вот", "он", "пришел"],
    "mic_candidate": ["Да", "реально", "вот", "пришел"],
    "mic_guarded_candidate": [],
    "removed": ["Да", "реально", "вот", "пришел"],
    "kept": []
  },
  "metrics": {
    "local_fir_remote_token_overlap": 1.0,
    "base_candidate_remote_token_overlap": 1.0,
    "guarded_remote_token_overlap": 0.0,
    "leak_delta_vs_local_fir": -1.0
  },
  "decision": {
    "action": "suggest_drop",
    "confidence": 0.93,
    "reason": "remote_only_window_all_candidate_tokens_are_remote_explainable",
    "safe_to_apply": false
  }
}
```

Allowed `decision.action` values:

- `keep`: no remote-token action is needed for this row;
- `quarantine`: evidence is useful but not safe enough for a direct suggestion;
- `suggest_drop`: all candidate tokens in a remote-only ASR window are remote-explainable;
- `needs_review`: the guard found risk or insufficient evidence.

`suggest_drop` is still review-only. A later repair layer may consume it only with separate safety
gates and must preserve real local speech.

Summary schema:

```json
{
  "schema": "murmurmark.echo.remote_forbidden_summary/v1",
  "mode": "shadow_review_only",
  "status": "ok",
  "metrics": {
    "remote_token_leak_rate_before": 0.5,
    "remote_token_leak_rate_after": 0.0,
    "remote_token_leak_delta": -0.5,
    "local_word_recall_before": 0.916667,
    "local_word_recall_after": 0.916667,
    "local_word_recall_delta": 0.0,
    "guarded_seconds": 8.0,
    "review_burden_seconds": 6.0,
    "asr_windows_selected": 4,
    "asr_windows_evaluable": 4,
    "asr_windows_skipped": 78,
    "asr_selected_audio_candidate": "coverage_v2_remote_gate_local_fir",
    "asr_audio_candidate_gate_passed": true,
    "asr_audio_candidate_gate_reason": "remote_token_leak_reduced_without_local_recall_regression",
    "audio_candidate_remote_token_leak_delta": -0.15,
    "audio_candidate_local_word_recall_delta": 0.0,
    "asr_windows_selected_by_reason": {
      "audio_review:remote_duplicate": 2,
      "speaker_state_remote_only_top_remote_db": 2
    },
    "actions": {"suggest_drop": 1, "quarantine": 2, "keep": 1}
  },
  "gates": {
    "passed": true,
    "reason": "remote_tokens_reduced_without_local_recall_regression",
    "remote_token_leak_improved": true,
    "local_recall_gate_passed": true,
    "no_default_promotion": true
  }
}
```

Invariants:

- raw `audio/*.caf` files are never read as writable outputs;
- `mic_for_asr.wav` is not changed;
- `local_fir` remains the selected production Echo Guard path;
- corpus reports must count local-recall regressions separately from remote-token improvements;
- `asr_audio_candidate_gate_passed` means a shadow audio candidate beat `local_fir` on selected ASR
  windows; it is not a default-promotion decision;
- every selected ASR audit window must carry `selection.selection_reason`; rows from derived
  artifacts should also carry `selection.source_artifacts` and `selection.source_row_ids`;
- corpus reports must include selected/evaluable/skipped ASR window counts and reason buckets;
- corpus reports must include `acceptance.why_not_more_safe_sessions` when some sessions are not
  safely improved;
- a corpus target of one safe improved session is not enough for default promotion.

Corpus summary:

```json
{
  "schema": "murmurmark.echo.remote_forbidden_corpus_report/v1",
  "summary": {
    "safe_improved_sessions": 4,
    "assessment_classes": {
      "no_baseline_asr_visible_leak": 2,
      "safe_improved": 4
    },
    "asr_windows_evaluable": 24,
    "asr_windows_skipped": 578,
    "guarded_seconds": 83.71,
    "review_burden_seconds": 47.29,
    "target_status": "target_met_two_sessions",
    "promotion_decision": "shadow_review_only_do_not_promote"
  },
  "acceptance": {
    "two_session_target_met": true,
    "explanation": "At least two sessions reduce ASR-visible remote leakage while preserving local-word recall.",
    "why_not_more_safe_sessions": [
      {
        "session": "sessions/2026-06-30_11-15-56",
        "class": "no_baseline_asr_visible_leak",
        "reason": "local_fir_leak_rate_before_is_zero"
      }
    ]
  }
}
```

`no_baseline_asr_visible_leak` means the selected ASR audit windows did not reproduce the harmful
condition in the selected baseline transcript: there were no remote tokens visible in the `local_fir`
candidate for those windows. Such a session cannot count as safely improved, because the evidence
layer has nothing measurable to remove without inventing a fix.

## Near-Realtime Shadow Artifacts

Near-realtime processing is a shadow branch. It may run during `murmurmark record --live-pipeline`,
but it does not replace the batch transcript. The authoritative path is still:

```text
raw CAF session -> murmurmark process -> reviewed/readiness transcript
```

The live branch writes only under `derived/live/`.

### Segment Manifest

`derived/live/segments.jsonl` contains one row per closed mic or remote segment copy:

```json
{
  "schema": "murmurmark.live_segment/v1",
  "source": "mic",
  "index": 1,
  "path": "derived/live/audio/mic/000001.caf",
  "start_sec": 0.0,
  "end_sec": 60.0,
  "duration_sec": 60.0,
  "clip_start_sec": 0.0,
  "clip_end_sec": 65.0,
  "clip_duration_sec": 65.0,
  "overlap_before_sec": 0.0,
  "overlap_after_sec": 5.0,
  "frames": 2880000,
  "clip_frames": 3120000,
  "sample_rate": 48000,
  "closed": true,
  "final": false,
  "after_overlap_complete": true
}
```

Invariants:

- raw `audio/mic/*.caf` and `audio/remote/*.caf` remain the durable capture source;
- live segment files are derived copies and may be deleted or regenerated;
- `start_sec..end_sec` is the non-overlapping hard window for publishing text;
- `clip_start_sec..clip_end_sec` is the actual audio clip sent to ASR and may include copied
  overlap before or after the hard window;
- live workers must transcribe the clip but publish only text whose timestamp center falls inside
  the hard window;
- a worker may process an index only after both `mic` and `remote` rows for that index are closed;
- if the live worker or derived live segment writer fails, the session must remain
  batch-processable from raw CAF.

### Worker State And Report

`derived/live/live_pipeline_state.json` is the small mutable progress file. It is allowed to change
while recording.

`derived/live/live_pipeline_report.json` is the durable summary:

```json
{
  "schema": "murmurmark.live_pipeline_report/v1",
  "mode": "near_realtime_shadow",
  "status": "completed",
  "batch_authoritative": true,
  "promotion_allowed": false,
  "progress": {
    "captured_sec": 600.0,
    "processed_sec": 540.0,
    "live_lag_sec": 60.0,
    "chunks_processed": 9,
    "segments_seen": 18
  },
  "outputs": {
    "draft_transcript": "derived/live/transcript.draft.md",
    "chunks_jsonl": "derived/live/chunks.jsonl"
  }
}
```

`batch_authoritative: true` and `promotion_allowed: false` are mandatory in v1.

### Live Chunks And Draft Transcript

Each processed index writes:

```text
derived/live/chunks/<index>/chunk.json
derived/live/chunks/<index>/mic.wav
derived/live/chunks/<index>/remote.wav
```

`derived/live/chunks.jsonl` repeats the chunk summaries for cheap streaming reads. A chunk row may
contain `provisional: true` when it is inside the delayed commit window and may be rewritten by a
later worker version.

`derived/live/transcript.draft.md` is a read-only draft for early orientation. It must not be used as
the final transcript, synthesis input or export source until a future corpus gate explicitly promotes
the live path.

### Final Reconcile Report

After a live recording stops, MurmurMark may run the existing batch-grade pipeline and write:

```text
derived/live/final_reconcile_report.json
```

Schema:

```json
{
  "schema": "murmurmark.live_final_reconcile_report/v1",
  "mode": "near_realtime_shadow",
  "status": "passed",
  "batch_authoritative": true,
  "promotion_allowed": false,
  "source_of_truth": "batch_pipeline",
  "live_cache_reuse": "materialized_raw_whisper_cache",
  "speedup_status": "live_asr_cache_reused",
  "fallback_reason": [],
  "outputs": {
    "pipeline_run_report": "derived/pipeline-run/pipeline_run_report.json",
    "session_readiness": "derived/readiness/session_readiness.json",
    "live_batch_comparison": "derived/live/live_batch_comparison.json"
  }
}
```

In v1, both outcomes are valid:

- `speedup_status: live_asr_cache_reused` means live chunk ASR was strict-compatible and materialized
  into the normal whisper.cpp raw cache before batch transcript assembly.
- `speedup_status: fallback_batch_asr` means the final transcript went through the same batch-grade
  timeline repair, cleanup, review/readiness and synthesis layers as a normal `murmurmark process`
  run. It does not claim post-meeting speedup.

Promotion beyond shadow still requires corpus gates; cache reuse only saves ASR time and does not
make the live draft authoritative.

### Live ASR Cache Report

Before expensive batch ASR, `scripts/materialize-live-asr-cache.py` may try to turn live chunk ASR
into the normal whisper.cpp raw cache:

```text
derived/live/live_asr_cache_report.json
```

Schema:

```json
{
  "schema": "murmurmark.live_asr_cache_report/v1",
  "status": "not_eligible",
  "materialized": false,
  "reasons": [
    "window_duration_mismatch:1",
    "asr_json_missing:remote:1"
  ],
  "parameters": {
    "language": "ru",
    "asr_mode": "windowed",
    "asr_window_sec": 60,
    "asr_overlap_sec": 5,
    "mic_audio_prep": "speech",
    "remote_audio_prep": "loudnorm"
  }
}
```

When `materialized: true`, the script writes:

```text
derived/transcript-simple/whisper-cpp/raw/mic.json
derived/transcript-simple/whisper-cpp/raw/mic.meta.json
derived/transcript-simple/whisper-cpp/raw/remote.json
derived/transcript-simple/whisper-cpp/raw/remote.meta.json
```

The generated `.meta.json` must match `transcribe-simple-whispercpp.py` cache metadata exactly.
Otherwise `transcribe_current` reruns whisper.cpp. A `not_eligible` report is an expected safe
fallback, not an error.

Compatibility gates include same whisper.cpp model and language, source-specific audio prep
(`mic=speech`, `remote=loudnorm`), hard-window duration, clip overlap, matching mic/remote indices
and usable whisper.cpp JSON for every included live chunk.

Common `not_eligible` reasons:

- `live_report_missing`;
- `raw_cache_already_exists`;
- `segment_count_mismatch`;
- `asr_json_missing:<source>:<index>`;
- `audio_prep_mismatch:<source>:<index>`;
- `window_start_mismatch:<index>`;
- `window_duration_mismatch:<index>`;
- `overlap_before_mismatch:<index>`;
- `overlap_after_mismatch:<index>`.

### Live-vs-Batch Comparison

After the normal batch pipeline runs, `scripts/compare-live-batch.py` writes:

```text
derived/live/live_batch_comparison.json
```

Schema:

```json
{
  "schema": "murmurmark.live_batch_comparison/v1",
  "status": "shadow_compared",
  "promotion_allowed": false,
  "promotion_reason": "near_realtime_shadow_v1_never_promotes_by_default",
  "promotion_blockers": [
    "shadow_v1_never_promotes_by_default",
    "order_risk",
    "local_recall",
    "remote_duplicate_leak",
    "review_burden",
    "missing_boundary_speech"
  ],
  "blockers": [],
  "warnings": [],
  "metrics": {
    "live_chunks": 9,
    "live_token_count": 1200,
    "batch_token_count": 1350,
    "live_token_recall_in_batch": 0.82,
    "adjacent_duplicate_chunk_count": 0,
    "batch_authoritative": true
  },
  "parity_gates": {
    "status": "not_promotable",
    "gates": [
      {
        "name": "duplicate_chunks",
        "status": "passed",
        "reason": "adjacent live chunks should not repeat the same decoded text"
      },
      {
        "name": "local_recall",
        "status": "not_evaluated",
        "reason": "near-realtime shadow v1 does not yet produce batch-grade profile metrics for this gate"
      }
    ]
  }
}
```

This comparison is advisory. Missing live or batch artifacts are written as `blockers`, but they must
not fail the normal batch pipeline. `not_evaluated` gates are promotion blockers, not failures of the
recording.

### Live Corpus Gates

`scripts/report-live-corpus-gates.py` aggregates live comparisons:

```text
sessions/_reports/live-pipeline/live_corpus_gates_report.json
sessions/_reports/live-pipeline/live_corpus_gates_report.md
```

Schema:

```json
{
  "schema": "murmurmark.live_corpus_gates_report/v1",
  "summary": {
    "sessions_total": 10,
    "live_sessions": 3,
    "compared_sessions": 2,
    "blocked_sessions": 1,
    "promotion_allowed_sessions": 0,
    "target_status": "shadow_only_not_promotable",
    "promotion_decision": "shadow_only_do_not_promote",
    "speedup_supported_sessions": 0
  },
  "gate_counts": {
    "duplicate_chunks": {"passed": 2},
    "local_recall": {"not_evaluated": 2}
  }
}
```

The corpus gate is deliberately conservative. Any `not_evaluated`, `blocked`, `failed` or `warning`
gate prevents promotion. v1 is expected to remain `shadow_only_do_not_promote`.

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
