# Current Goal

Status: current

Updated: 2026-08-07

The supported path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`. Plain
transcript, notes and export stay authoritative. Optional speaker-aware artifacts may use only
explicit session-local review decisions and must fail open to anonymous or aggregate evidence.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Reviewed Speaker-Aware Meeting Memory v1

OpsKarta nearest goal: Reviewed Speaker-Aware Meeting Memory v1: после закрытия bounded pre-ASR audio frontier с DO_NOT_ADVANCE_STRONGER_SEPARATOR связать explicit session-local reviewed remote labels с evidence-backed notes и guarded export через отдельный opt-in fingerprinted handoff; сохранять exact utterance/evidence IDs, aggregate/anonymous fallback при stale или partial review и обычные notes/export без изменений; не добавлять voice identity, cross-session roster, LLM claims или external writes; завершить corpus-wide deterministic/stale/fallback gates, актуализировать документацию, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

The remote-speaker evidence map, anonymous rich transcript and explicit reviewed naming are already
promoted optional. The bounded pre-ASR audio frontier is now closed: SepFormer assigned a present
Target-Me stem accurately on train, but presence and absence distributions overlapped by `0.253397`,
so it stopped before dev with `DO_NOT_ADVANCE_STRONGER_SEPARATOR`. Production v2.17 remains the exact
audio plateau.

The next user-visible value is to let reviewed names flow into meeting memory without weakening the
evidence contract or pretending that a voice embedding proves identity.

## Objective

Create an opt-in, fingerprinted notes/export handoff that renders explicit session-local reviewed
remote labels while preserving exact utterance and evidence references. Missing, stale or partial
review must return the existing anonymous or aggregate result.

## Required Work

1. Freeze selected dialogue, anonymous rich references, reviewed label decisions, notes and export
   inputs for the existing six-session remote-speaker corpus.
2. Define a versioned speaker-aware memory manifest binding every rendered label to its session-local
   anonymous speaker ID, decision row and exact utterance/evidence IDs.
3. Add explicit CLI read/export options without changing default `notes`, transcript or export.
4. Preserve aggregate `Colleagues` where anonymous attribution is absent and anonymous IDs where a
   reviewed label is missing.
5. Reject stale fingerprints, partial review, unknown anonymous IDs and unsupported labels before
   publication; publish the ordinary handoff as exact fallback.
6. Prove deterministic replay, referential integrity, no text mutation and no unsupported
   attribution across the frozen corpus.
7. Document privacy boundaries and finish with a corpus decision, tests, documentation, roadmap,
   OpsKarta, commit and push.

## Acceptance Gates

- every displayed name has an explicit current-session review decision;
- every note/export statement retains exact source utterance and evidence IDs;
- selected text, role, order and timestamps are unchanged;
- stale, missing or partial review produces exact anonymous/aggregate fallback;
- default notes/export bytes do not change;
- replay is deterministic and all manifest/input hashes verify;
- no cross-session identity, voice-only naming, external write or unsupported LLM claim appears.

## Safety Boundary

- capture, raw CAF, Echo Guard, production v2.17, ASR and transcript selection do not change;
- Target-Me enrollment is never reused as a remote participant identity system;
- reviewed names remain local to one session unless a future explicit roster contract is approved;
- the closed SepFormer dev/future-hard sets remain unopened;
- UI and automatic external integrations remain outside this goal.
