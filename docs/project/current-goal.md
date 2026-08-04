# Current Goal

Status: current

Updated: 2026-08-05

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded production
audio profile; Reference-Conditioned Target-Me Separation v2 completed with `DO_NOT_PROMOTE` and
did not change production.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Evidence Notes And Export v2

OpsKarta nearest goal: Evidence Notes And Export v2: собрать один versioned deterministic handoff bundle из выбранного transcript profile, quality verdict, unresolved review burden и evidence-backed notes; сделать guarded Markdown/Obsidian export прямым результатом lifecycle, запретить stale evidence и unsupported claims, завершить документацией, corpus regression и release-ready CLI contract.

## Why This Is Next

The audio ladder has reached a useful stable boundary:

- Speaker-Preserving Neural Echo v2 is promoted with exact fallback;
- Reference-Conditioned v1 identified missing speaker supervision;
- Target-Me Identifiability Corpus v1 closed that data gap;
- Reference-Conditioned v2 proved query adherence but rejected its small separator on dev before
  hard access.

The remaining immediate user friction is no longer another safe audio candidate. A completed
meeting still exposes several profile-specific transcript, verdict, notes, review and export files.
The user should receive one trustworthy result with its quality and evidence attached, without
knowing which internal profile won.

## Objective

Create one deterministic handoff artifact for every completed meeting. It must identify the exact
selected transcript, state whether review remains, include evidence-backed notes, and expose the
only guarded export that may leave the session directory.

The handoff becomes the stable product boundary for `meeting`, `finish`, `status`, `notes`,
`transcript` and `export`. Existing profile artifacts remain available for audit and recovery.

## Intended Contract

```text
selected transcript profile
  + quality verdict
  + review progress and unresolved queue
  + evidence_notes.json
  + notes.md
  + export readiness
  -> immutable handoff manifest
  -> meeting.md + evidence bundle
  -> guarded Markdown | Obsidian export
```

Every visible claim or extracted item must cite existing utterance/evidence IDs. Missing, stale or
incompatible inputs block publication instead of silently falling back to uncited prose.

## Required Work

1. Define versioned `handoff_manifest/v2` and `handoff_evidence/v2` schemas.
2. Resolve one selected transcript profile through the existing readiness and profile gates.
3. Bind all input paths, schemas and SHA-256 values before rendering output.
4. Produce one readable `meeting.md` with verdict, transcript link, curated evidence notes and
   explicit unresolved review burden.
5. Keep full candidate/audit material in structured JSON while Markdown remains a concise view.
6. Make `murmurmark finish` build or resume the handoff transactionally.
7. Make `murmurmark export --format markdown|obsidian` consume only a ready handoff.
8. Preserve the existing no-speech outcome and do not invent an empty-meeting summary.
9. Add corpus regression for stale evidence, missing IDs, changed profile inputs, interrupted
   publication and repeated deterministic runs.
10. Update CLI help, README, contracts, runbooks, roadmap and OpsKarta.

## Safety Boundary

- no claim without an existing evidence or utterance ID;
- no export while mandatory review or readiness blockers remain;
- no automatic write to Jira, issue trackers or external document systems;
- no cloud upload and no raw-audio payload;
- selected transcript and notes inputs are immutable for one handoff fingerprint;
- interrupted publication either resumes the same transaction or rolls back cleanly;
- a stale handoff is never shown as the current result;
- deterministic extractive notes remain the default; LLM synthesis is outside this goal.

## Acceptance Gates

- every processed corpus session gets exactly one of `ready`, `review_required`, `blocked` or
  `no_speech` handoff states;
- every Markdown note item resolves to an existing evidence ID in the selected profile;
- selected transcript, verdict, notes and review counters agree across `status`, `next`, `finish`
  and `export`;
- a repeated run over unchanged inputs yields the same semantic fingerprint and byte-identical
  payload files;
- stale hashes, missing evidence, unresolved mandatory review and unsupported schemas fail closed;
- guarded Markdown and Obsidian exports contain no absolute local paths or private debug payloads;
- no-speech, interrupted-run and profile-fallback fixtures pass;
- current meeting, transcript, review, retention and export regressions remain green.

## Definition Of Done

- schemas, CLI integration, transactional writer and recovery behavior are implemented;
- fixture and real-corpus reports prove deterministic output and evidence referential integrity;
- `murmurmark meeting` and `murmurmark finish` converge on the same handoff without extra user
  commands when no manual review is required;
- README, architecture, contracts, runbooks, current goal, roadmap and OpsKarta describe measured
  behavior rather than planned behavior;
- full static, Swift, privacy, open-source, planning and corpus checks pass;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- another echo model or target-speaker separator;
- remote diarization inside `Colleagues`;
- cloud or local generative LLM synthesis;
- direct Jira/docs mutation;
- UI or menu-bar application;
- changing capture, Echo Guard or whisper.cpp.

## Deferred Audio Research

A future Target-Me attempt requires a pretrained target-speaker extraction representation or a
larger multilingual speaker-query corpus. The completed v2 result rules out repeating the same
small spectral mask on the existing five train identities.
