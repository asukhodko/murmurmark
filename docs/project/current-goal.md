# Current Goal

Status: current

Updated: 2026-08-05

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded production
audio profile. Evidence Handoff v2 remains the only input to guarded export.

Release-quality CLI is complete. MurmurMark now has a deterministic versioned archive, complete
file checksums, compatibility and license contracts, transactional install/upgrade, rollback on
failure and packaged offline acceptance through Evidence Handoff v2. Runtime files are immutable;
config, sessions and exports remain in an external workspace.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Remote Speaker Evidence Map v1

OpsKarta nearest goal: Remote Speaker Evidence Map v1: разделить authoritative remote-речь на локально вычисленные стабильные анонимные speaker intervals, создать audit-only rich transcript с полной provenance и corpus gates, не присваивая имён и не меняя selected transcript, Evidence Handoff v2 или guarded export до отдельного решения PROMOTE.

## Why This Is Next

The release boundary is now reproducible and usable without a source checkout. The largest visible
product limitation is semantic: every remote participant is still rendered as aggregate
`Colleagues`. This is acceptable for a 1x1, but weakens group-meeting chronology, evidence notes and
later action ownership.

The authoritative remote track is already cleanly separated from `Me`, so anonymous remote speaker
segmentation does not require another Echo model. A bounded audit-only map can establish whether
local diarization is stable enough before names, transcript promotion or synthesis depend on it.

## Objective

Build a deterministic local evidence layer that assigns each speech interval on the authoritative
remote track to an anonymous stable ID such as `Remote-1`, `Remote-2` or `unknown`. Publish a shadow
rich transcript and a corpus decision without changing existing transcript text, timestamps,
selected profile or export behavior.

## Intended Contract

```text
authoritative remote audio + selected transcript + frozen corpus
  -> speech regions and embeddings
  -> constrained anonymous clustering
  -> speaker intervals with confidence and provenance
  -> transcript.rich.shadow_v1.json
  -> corpus PROMOTE or DO_NOT_PROMOTE
```

## Required Work

1. Freeze representative 1x1, group, overlap, short-speaker and noisy-office sessions with input
   SHA-256 and known structural expectations.
2. Define versioned schemas for anonymous speaker intervals, per-session evidence and corpus report.
3. Benchmark local offline speech segmentation and speaker embeddings already available on the
   machine; record exact model/version fingerprints and fail open when unavailable.
4. Cluster only authoritative remote speech. Keep `Me`, transcript text and chronology immutable.
5. Make speaker count conservative: merge uncertain fragments into `unknown` instead of inventing
   identities; never derive a person's name from transcript text.
6. Measure single-speaker false splits, multi-speaker merges, speaker-change boundaries,
   cross-session instability, overlap coverage and deterministic replay.
7. Create an audit-only `transcript.rich.shadow_v1.json` referencing existing utterance IDs and
   interval evidence.
8. Add fixture, negative, missing-model, repeatability, privacy and corpus tests.
9. Keep Evidence Handoff v2 and guarded export unchanged unless a later explicit promotion goal
   proves the new schema safe.
10. Reconcile README, contracts, runbooks, roadmap and OpsKarta with the measured decision.

## Safety Boundary

- local and offline only;
- anonymous IDs only; names require later evidence or review;
- no rewrite of raw CAF, selected transcript or existing timestamps;
- no diarization from the microphone track;
- uncertain speech remains `unknown` or explicit review evidence;
- a missing model or weak corpus result yields `DO_NOT_PROMOTE`, not a weaker transcript;
- no LLM, cloud service, UI or external-system write.

## Acceptance Gates

- every output interval points to immutable audio and transcript evidence;
- one-remote-speaker fixtures do not split into multiple confident identities;
- multi-speaker fixtures preserve known speaker changes without collapsing all speech into one ID;
- repeated runs produce byte-identical structured outputs;
- no concrete participant name is generated automatically;
- selected transcript, Evidence Handoff v2, notes, export readiness and raw CAF remain byte-exact;
- missing optional diarization assets fail open with an actionable report;
- corpus report records a reproducible `PROMOTE_REMOTE_SPEAKER_EVIDENCE_MAP_V1` or
  `DO_NOT_PROMOTE_REMOTE_SPEAKER_EVIDENCE_MAP_V1` decision.

## Definition Of Done

- schemas, implementation and audit artifacts exist;
- frozen fixtures and real-session corpus cover 1x1 and group meetings;
- local repeatability and no-regression gates pass;
- the shadow rich transcript is inspectable but cannot become authoritative accidentally;
- the measured corpus decision and evidence ceiling are documented;
- README, contracts, runbooks, current goal, roadmap and OpsKarta agree;
- full static, Swift, privacy, open-source, planning and relevant corpus checks pass;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- assigning real names to anonymous speakers;
- cross-meeting identity tracking;
- changing `Colleagues` in the selected Markdown transcript;
- promoting `transcript.rich.json` into Evidence Handoff v2;
- another Echo or Target-Me separation model;
- LLM synthesis, cloud APIs, UI or live-result promotion.
