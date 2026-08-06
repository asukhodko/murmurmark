# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF, plain transcript, Evidence Handoff v2, ordinary notes and guarded export remain
authoritative. Optional speaker-aware artifacts must never weaken that path.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Reviewed Speaker-Aware Meeting Memory v1

OpsKarta nearest goal: Reviewed Speaker-Aware Meeting Memory v1: поверх promoted explicit session-local speaker labels создать отдельный fingerprint-bound opt-in notes/export handoff с exact utterance и evidence IDs; не менять ordinary notes, Evidence Handoff v2, guarded export или auto-selection, сохранять aggregate Colleagues и anonymous fallback, запрещать voice identity, cross-session roster и external writes; доказать stale/fail-open/replay, privacy, referential integrity и corpus no-regression; завершить PROMOTE или воспроизводимым DO_NOT_PROMOTE, добавить тесты, актуализировать документацию, roadmap и OpsKarta, закоммитить и отправить изменения.

## Why This Is Next

Reviewed Remote Speaker Naming v1 completed with `PROMOTE_OPTIONAL_REVIEWED_NAMING`. Six frozen
sessions passed all gates: 14 anonymous IDs are available for explicit review and all 1235 remote
utterance references remain exact. Missing, partial or stale decisions fall back to anonymous rich;
voice-only identity and cross-session matching are absent.

The reviewed transcript is useful to read, but ordinary notes/export cannot yet carry those labels.
The next bounded product step is a separate opt-in meeting-memory bundle, not a change to the normal
handoff.

## Objective

Allow a user to request local notes and a Markdown export that display current explicit
session-local speaker labels while preserving exact links to the selected utterances and evidence.
The optional bundle must be independently verifiable and must never become the default source.

## Required Work

1. Define versioned speaker-aware notes/export handoff schemas over a current reviewed naming
   fingerprint and current Evidence Handoff v2.
2. Add explicit CLI read/export commands. Ordinary `notes`, `transcript`, `finish` and `export`
   behavior stays unchanged.
3. Preserve every evidence utterance ID and aggregate `Colleagues` abstention; do not invent speaker
   attribution for unlabelled evidence.
4. Publish transactionally into an immutable fingerprint bundle. Stale labels, stale source notes,
   incomplete review or missing artifacts fail open to the ordinary meeting-memory path.
5. Keep display labels out of ordinary reports, logs, frozen corpus artifacts and external systems.
6. Prove deterministic replay, interrupted publication recovery, referential integrity, path safety,
   privacy and ordinary-output non-regression on synthetic and real frozen sessions.
7. End with a reproducible `PROMOTE` or `DO_NOT_PROMOTE` corpus decision.

## Acceptance Gates

- every displayed speaker label comes from a current explicit session-local decision;
- every note/export evidence reference exists in the current selected dialogue;
- aggregate and anonymous fallbacks stay visible where evidence abstains;
- no voice identity, cross-session roster or external write exists in code or artifacts;
- stale or missing reviewed labels cannot be read as current speaker-aware memory;
- repeated runs are byte-identical and interrupted publication preserves the prior pointer;
- ordinary transcript, notes, verdict, Evidence Handoff v2, guarded export and raw audio remain
  byte-exact;
- the frozen corpus ends in reproducible `PROMOTE` or `DO_NOT_PROMOTE`;
- tests, contracts, runbook, README, roadmap and OpsKarta are current before commit and push.

## Safety Boundary

- no changes to capture, Echo Guard, ASR, transcript selection or speaker clustering;
- no automatic person naming, biometric identity store or cross-meeting voice matching;
- no calendar, contacts, network service, issue tracker or document-system write;
- no replacement of ordinary notes/export or automatic promotion into `meeting`.

## Completed Predecessor

Reviewed Remote Speaker Naming v1 completed on 2026-08-06 with
`PROMOTE_OPTIONAL_REVIEWED_NAMING`. `murmurmark speakers template|apply|status` manages explicit
session decisions, and `murmurmark transcript SESSION --rich --reviewed-speakers` verifies the
optional immutable bundle before reading it. See
`docs/testing/2026-08-06-reviewed-remote-speaker-naming-v1.md`.
