# Evidence and Synthesis Architecture

Synthesis is an optional stage that turns transcript evidence into useful work artifacts. It must
not be mixed into ASR or compete with speaker-resolved transcript quality.

Status, 2026-08-07: MurmurMark has a production local extractive synthesis path over the current
transcript and Evidence Handoff v2 artifacts. A pinned local free-text synthesis qualification was
completed with `DO_NOT_PROMOTE`; Evidence-Only Local Note Selection v1 is an isolated opt-in view.
Further synthesis, retrieval and proposal work is parked until the transcript-quality program
reaches its speaker and corpus gates.

The current `transcript-simple` outputs are useful enough for evidence-backed extractive notes, but they are not the final evidence package:

- `clean_dialogue*.json` can provide utterance text and IDs;
- `quality_report*.json` and `overlaps*.json` can identify risky regions;
- `timeline_audit_examples*.jsonl` can provide review clips and context;
- anonymous/reviewed speaker handoffs and extractive decisions/actions are available;
- generated chapter summaries and direct external writes remain future work.

## Current Extractive Spike

Implemented command:

```bash
scripts/synthesize-simple-extractive.py "$SESSION" --transcript-profile auto
```

It writes:

```text
derived/synthesis-simple/extractive/
  synthesis_manifest.json
  quality_verdict.json
  quality_verdict.md
  notes.md
  evidence_notes.json
  review_items.jsonl
```

The `auto` profile first checks `residual_audio_arbitration_v1`, but selects it only after a
corpus-wide `PROMOTE_RESIDUAL_AUDIO_ARBITRATION_V1`. The current arbitration decision is
`DO_NOT_PROMOTE`, so `auto` prefers `residual_me_evidence_v1` when its session gates, frozen source
hashes, promoted corpus report and output fingerprints all pass. It then falls back to promoted
`authoritative_boundary_v1`. Otherwise it follows the existing order: a material passing
`audit_cleanup_v7`, `reviewed_v1`, `agent_reviewed_v1`, the remaining passing
`audit_cleanup_v6..v1`, a compatible passing `order_repair_v1`, passing `shadow_v2`, then baseline
`clean_dialogue.json`.
The script reads only derived transcript and audit JSON; it never reads raw audio.

The output is intentionally extractive:

- topic blocks choose salient utterances instead of the first utterances in a time window;
- potential decisions, actions, risks and open questions are scored rule candidates with evidence IDs;
- Markdown shows only selected top items, while `evidence_notes.json` keeps the full candidate audit;
- meeting facilitation and process phrases are hidden from Markdown but kept as candidates for audit;
- unresolved review sources such as transcript-order checks are copied into candidate features and
  penalized before Markdown selection;
- every selected or candidate item is marked `needs_review`;
- unsupported claims are not generated.

Current data flow:

```text
clean_dialogue.json
  -> optional audit_cleanup_v* profile
  -> optional corpus-gated authoritative_boundary_v1
  -> optional corpus-gated residual_me_evidence_v1
  -> optional corpus-gated residual_audio_arbitration_v1
  -> topic_blocks
  -> candidate_items
  -> scored_items
  -> selected_notes
  -> notes.md
  -> evidence_notes.json
```

`quality_verdict.json` is the first gate a user should read. It reports `good`, `usable_with_review`,
`risky` or `failed` from transcript quality counters and risky intervals.

## Local Model Qualification

Evidence-Guarded Local Synthesis Qualification v1 tested a pinned `deepseek-r1:14b` over six
speaker-aware sessions. All replays and references were deterministic, unsupported published claims
stayed at zero and ordinary outputs remained byte-identical. The independent verifier rejected
69/142 proposals for evidence or provenance failures, above the frozen `0.35` rejection limit; peak
RSS also exceeded 13 GB. The result is `DO_NOT_PROMOTE`, so no generated-text CLI surface exists.

Evidence-Only Local Note Selection v1 tested the safer bounded alternative. The same pinned model
may choose or rank only known statement IDs under a dynamic JSON Schema. Displayed wording, speaker
provenance and utterance IDs are copied exactly from the source handoff. The frozen result is
`PROMOTE_OPTIONAL_EVIDENCE_SELECTION`: 6/6 sessions, 47 review-marked candidates reduced to 28,
category coverage `1.0`, speaker coverage `0.8`, deterministic replay and zero model-authored
published claims. The 14.8B runtime used up to about 12.5 GB RAM, so it remains an explicit optional
view and is not part of the ordinary meeting path.

The frozen corpus contains no baseline high-confidence decision/action/risk/question items. Its
retention ratio is therefore vacuous and cannot justify dropping such items later. Reviewed Meeting
Artifacts v1 must preserve that boundary while asking the user to confirm, reject or leave exact
candidates unresolved.

An empty selected dialogue is not always a pipeline failure. For a complete no-show or silent call,
the synthesis layer may emit `session_classification: verified_no_speech` and a `good` verdict. This
requires a separate `murmurmark.no_speech_evidence/v1` artifact proving raw-track coverage,
microphone acoustic liveness, remote silence, successful chunk reconstruction, ASR output limited
to known hallucinations, and no missing-local-speech evidence. Missing or conflicting evidence keeps
the empty result at `failed`.

## Boundary

Transcription answers:

```text
What was said?
Who probably said it?
When was it said?
How reliable is this segment?
```

Synthesis answers:

```text
What did the meeting decide?
What actions were assigned?
What risks or open questions remain?
What documentation should be updated?
```

## Evidence Package

Synthesis consumes an evidence package, not raw audio:

```text
evidence_package/
  transcript.rich.json
  transcript.corrected.md
  quality_report.json
  speaker_map.json
  corrections.jsonl

  meeting_context/
    calendar_event.md
    participants.yaml
    agenda.md
    previous_meeting_notes.md

  domain_context/
    glossary.yaml
    domain.md
    architecture_summary.md
    services.yaml
    known_projects.yaml

  retrieved_context/
    tickets.md
    docs.md
    repo_summaries.md
    incident_reports.md

  synthesis_policy.yaml
```

Raw audio should not be sent to external providers by default.

## Pipeline

```text
MurmurMark Synthesis
  |
  +-- 01_context_build
  |     domain, tickets, previous notes, architecture docs
  |
  +-- 02_transcript_index
  |     topic segments, utterance embeddings, speaker turns
  |
  +-- 03_chapter_summaries
  |     10-20 minute summaries with utterance citations
  |
  +-- 04_candidate_extraction
  |     decisions, action items, risks, open questions
  |
  +-- 05_global_synthesis
  |     local/frontier model under policy
  |
  +-- 06_consistency_check
  |     every factual item must cite transcript evidence
  |
  +-- 07_export_plan
  |     Markdown, Obsidian, docs patch plan
  |
  +-- 08_human_review
        approve notes and patches
```

Current CLI status:

- Evidence Handoff v2 transactionally publishes one immutable bundle under
  `derived/handoff-v2/bundles/<semantic-fingerprint>/` and updates the current manifest only after
  all payloads and hashes validate.
- Markdown and Obsidian export are available through `murmurmark export SESSION --format ...` and
  consume only a current `ready` or verified `no_speech` handoff.
- Export writes only local files under `exports/private/`. Missing IDs, stale input hashes,
  incompatible schemas and mandatory review fail closed. `--force` is retained for compatibility
  and cannot bypass handoff integrity or review gates.
- The full evidence candidate audit remains structured JSON; user-facing Markdown contains only
  selected items with resolvable utterance/evidence IDs.
- Docs/Jira patch proposals and direct vault handoff remain future layers.

## Evidence Guard

Rules:

- Every decision must cite one or more utterance IDs.
- Every action item must cite one or more utterance IDs.
- If speaker identity is uncertain, do not assign an owner automatically.
- If a transcript segment is marked uncertain, notes must show or respect that uncertainty.
- Unsupported facts are rejected or marked for review.
- External docs updates are patch proposals, not automatic writes.

Rejected example:

```json
{
  "text": "The team decided to migrate from PostgreSQL to ClickHouse.",
  "status": "rejected",
  "reason": "No supporting utterance IDs; transcript only mentions ClickHouse as analytics storage."
}
```

## Privacy Modes

### Local Only

```text
raw audio: local
transcript: local
synthesis: local model
docs export: local
```

Default for sensitive meetings.

### Sanitized Frontier

```text
raw audio: local only
transcript: local
redaction: local
frontier API receives sanitized transcript and selected context
```

Redaction must be configurable. For engineering meetings, service names and ticket IDs may be necessary for useful notes.

### Full Frontier with Approval

```text
raw audio: never by default
full transcript/context: may be sent
requires explicit approval and payload manifest
```

Provider retention requirements must be represented in policy.

## Docs Integration

Do not let a model write directly to external docs.

Use a two-phase flow:

```text
Phase 1:
  generate proposed notes and patches

Phase 2:
  human review and apply
```

Potential outputs:

```text
notes/
  2026-06-20-retro.md

exports/
  obsidian.md
  confluence.md
  jira_comments.json
  adr_patch.diff
  docs_pr_plan.md
```

CLI agents such as Codex or Claude Code are appropriate for docs integration only inside an explicit worktree/sandbox and only after synthesis has produced evidence-backed instructions.
