# Current Goal

Status: current

Updated: 2026-08-07

The supported path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`. Plain
transcript, extractive notes and guarded export stay authoritative. Optional generated memory may
advance only through frozen local evidence gates and must fail open to those exact outputs.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Evidence-Guarded Local Synthesis Qualification v1

OpsKarta nearest goal: Evidence-Guarded Local Synthesis Qualification v1: квалифицировать локальный LLM как необязательный consumer current Reviewed Speaker-Aware Meeting Memory v1; заморозить model/revision, prompt и six-session corpus, требовать exact utterance/evidence IDs для каждого generated claim, отклонять unsupported claims и сохранять extractive notes как byte-identical fallback; сравнить factual support, coverage, review burden, replay и resource cost, завершить PROMOTE_OPTIONAL_LOCAL_SYNTHESIS либо DO_NOT_PROMOTE; не менять transcript selection/default notes/export, не использовать cloud, external writes, cross-session identity или UI; добавить tests/report, согласовать документацию, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Reviewed Speaker-Aware Meeting Memory v1 is complete with
`PROMOTE_OPTIONAL_REVIEWED_SPEAKER_MEMORY`: 6/6 frozen sessions, 2319 utterances, 726 exact evidence
statements and byte-identical ordinary outputs. Explicit names now have session-local decision
provenance, while stale or partial decisions return the ordinary Evidence Handoff v2 bundle.

The bounded pre-ASR frontier is also closed. SepFormer assigned present Target-Me stems correctly
on train but could not separate presence from absence, so it stopped before dev. The next useful
question is whether a local model can turn the verified evidence into better meeting memory without
inventing claims or weakening the extractive fallback.

## Objective

Qualify one pinned local language model and deterministic prompt as an optional consumer of the
current speaker-aware handoff. Every published statement must be supported by exact current-session
utterance IDs and pass an independent support check.

## Required Work

1. Inventory the available local runtime and choose one pinned model/revision under a compatible
   redistribution policy; missing model support must remain a clean fail-open state.
2. Freeze the prompt, decoding parameters, speaker-aware input manifest and six-session corpus.
3. Generate only bounded summary, decision, action, risk and open-question candidates with explicit
   evidence IDs; do not rewrite transcript truth.
4. Reject missing, stale, unknown or semantically unsupported references before publication.
5. Compare factual support, useful coverage, review burden, deterministic replay, latency, memory
   and energy cost against the current extractive notes.
6. Publish an isolated immutable bundle only after corpus-wide promotion; otherwise record a precise
   `DO_NOT_PROMOTE` and keep extractive notes unchanged.
7. Finish with tests, corpus report, documentation, roadmap, OpsKarta, commit and push.

## Acceptance Gates

- every generated claim has one or more exact current-session evidence utterance IDs;
- an independent verifier rejects unsupported or contradicted claims;
- no speaker label escapes its current reviewed session provenance;
- stale/missing model, prompt, handoff or decisions returns byte-identical extractive notes;
- repeated runs with frozen inputs and decoding parameters are deterministic;
- default transcript, notes, Evidence Handoff v2 and export bytes do not change;
- the corpus decision is explicit: `PROMOTE_OPTIONAL_LOCAL_SYNTHESIS` or `DO_NOT_PROMOTE`.

## Safety Boundary

- no cloud request or external write;
- no voice-only identity, cross-session roster or Target-Me enrollment reuse;
- no capture, Echo Guard, ASR, transcript selection, default notes/export or UI change;
- no promotion based only on fluent wording or model self-confidence;
- the closed SepFormer dev/future-hard sets remain unopened.

## Completed Checkpoint

Reviewed Speaker-Aware Meeting Memory v1 introduced
`murmurmark notes SESSION --reviewed-speakers` and
`murmurmark export SESSION --reviewed-speakers`. Its policy and frozen corpus bind every displayed
reviewed name to an anonymous speaker ID, decision row and exact statement evidence IDs. Missing,
partial or stale review falls back to ordinary Evidence Handoff v2 artifacts.
