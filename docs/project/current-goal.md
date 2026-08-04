# Current Goal

Status: current

Updated: 2026-08-04

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 is now a guarded pre-ASR
capability: compatible speaker-playback sessions may use its personalized clean mic, while every
missing artifact, incompatible acoustic mode or regression returns to exact `local_fir_role_masked`.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. This file expands the one executable goal in human
terms. `scripts/check-planning-consistency.py` keeps README, roadmap and OpsKarta aligned.

## Evidence Notes And Export v2

OpsKarta nearest goal: Evidence Notes And Export v2: собрать один versioned deterministic handoff
bundle из выбранного transcript profile, quality verdict, review evidence, notes и export readiness;
каждая видимая запись ссылается на существующие evidence IDs, stale или blocked inputs fail closed,
а Markdown/Obsidian export не требует ручного склеивания артефактов.

## Starting Evidence

- durable capture, authoritative transcription, review, notes, export and retention already work;
- `murmurmark meeting` owns the normal unattended lifecycle and resume contract;
- Speaker-Preserving Neural Echo v2 is promoted: `5/12` corpus sessions used candidate audio,
  `41.940s` and `90` remote-supported tokens were removed with local retention `1.0`;
- quality verdict, extractive notes and guarded export exist, but their version/fingerprint
  relationship is spread across several artifacts;
- profile-specific aliases and late enrichment can make it harder to prove that transcript, notes,
  verdict and export all describe the same immutable result;
- unresolved review burden is visible, but the user-facing bundle is not yet one strict contract.

## Objective

Publish one local, deterministic and verifiable handoff bundle for every successfully processed
session. It either contains a coherent transcript, verdict, evidence-backed notes, review summary
and export payload, or fails closed with a precise reason and next command.

The bundle must be usable by a person or a later local/controlled synthesis layer without guessing
profile filenames or joining incompatible generations of artifacts.

## Contract

The versioned handoff must freeze:

- selected transcript profile, path, schema and SHA-256;
- quality verdict, readiness/use gate and unresolved review burden;
- evidence notes and every cited utterance/audit ID;
- export format, privacy mode and payload manifest;
- generator versions, source fingerprints and creation time;
- exact commands to review, rebuild or export when blocked.

Every claim visible in Markdown or Obsidian must resolve to an existing item in the selected
transcript/evidence generation. Missing IDs, stale hashes, incompatible profiles, blocked readiness
or incomplete review evidence reject publication. A rejection must preserve all source artifacts.

## Execution Scope

1. Freeze `murmurmark.handoff_bundle/v2` and related manifest schemas before implementation.
2. Define one resolver that selects transcript, notes, verdict, review and export evidence from the
   same compatible profile generation.
3. Validate referential integrity for utterance IDs, audit IDs, review decisions and note evidence.
4. Materialize a byte-stable local bundle with Markdown and optional Obsidian views plus structured
   JSON provenance.
5. Make `meeting`, `finish`, `status`, `outcome`, `notes`, `transcript` and `export` point to the same
   handoff decision rather than recomputing conflicting recommendations.
6. Preserve `review_first` as a useful result: export stays blocked where policy requires, but the
   user receives the exact review queue and next command.
7. Add deterministic replay, stale-input, interrupted-write, invalid-reference, empty-conversation
   and profile-fallback tests.
8. Run corpus regression over representative 1x1, group, headphones, speaker-playback, noisy-office
   and verified-no-speech sessions.
9. Update README, contracts, runbooks, current goal, roadmap and OpsKarta; commit and push the result.

## Acceptance Gates

- one selected transcript fingerprint is shared by handoff, notes, verdict and export manifest;
- every visible claim cites valid evidence from that selected generation;
- stale or missing evidence cannot produce a successful bundle;
- repeated materialization from unchanged inputs is byte-stable except explicitly volatile timing;
- interrupted publication leaves either the previous valid bundle or no bundle, never a mixed one;
- `ready_for_notes`, `review_first` and `blocked` remain distinguishable and actionable;
- no command reports export success while readiness or privacy gates block it;
- verified-no-speech sessions produce a valid empty-conversation bundle rather than fabricated text;
- selected transcript text and role order are unchanged by bundling;
- raw CAF, Echo Guard, primary ASR and transcript profile selection are unchanged;
- no network, LLM or external write is needed;
- corpus verdict, review burden and existing evidence-note selections do not regress.

## Definition Of Done

- tracked v2 schemas and one implementation own handoff resolution and publication;
- normal lifecycle produces the bundle automatically without extra user commands;
- CLI accessors resolve through the validated bundle and report one consistent next action;
- Markdown and Obsidian outputs are complete local deliverables with evidence references;
- failure and resume paths are explicit, atomic and tested;
- fixture, integration, corpus replay, planning and open-source checks pass;
- documentation and roadmap describe the implemented behavior rather than an aspiration;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- changing capture, Echo Guard, Speaker-Preserving Neural Echo or whisper.cpp;
- adding cloud models, LLM summaries or automatic external writes;
- diarizing individual `Colleagues`;
- promoting Live Shadow;
- building a UI.

## Previous Goal Result

Speaker-Preserving Neural Echo v2 completed with
`PROMOTE_SPEAKER_PRESERVING_NEURAL_ECHO_V2`. The detailed result, frozen fingerprints, safety
boundary and production contract are recorded in
`docs/research/2026-08-04-speaker-preserving-neural-echo-v2.md`.
