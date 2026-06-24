# Evidence and Synthesis Architecture

Synthesis is the stage that turns transcript evidence into useful work artifacts. It must not be mixed into ASR.

Status, 2026-06-24: this is target architecture. The implemented CLI currently stops at transcript and audit artifacts. Evidence-backed notes, decisions, action items, risks and docs patch plans are not implemented yet.

The current `transcript-simple` outputs are close enough to support a first synthesis spike, but they are not the final evidence package:

- `clean_dialogue*.json` can provide utterance text and IDs;
- `quality_report*.json` and `overlaps*.json` can identify risky regions;
- `timeline_audit_examples*.jsonl` can provide review clips and context;
- `speaker_map.json`, chapter summaries, extracted decisions and action items are still future work.

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
