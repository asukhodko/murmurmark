# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF and batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded
production audio profile. Evidence Handoff v2 remains the only input to guarded export.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Remote Speaker Evidence Map v1

OpsKarta nearest goal: Remote Speaker Evidence Map v1: разделить authoritative remote на стабильные анонимные speaker intervals на замороженном корпусе реальных 1x1 и групповых встреч; построить audit-only speaker map и shadow rich transcript с полной provenance, измерить boundary и cluster consistency и сохранить весь исходный текст, не присваивать имена и не менять selected transcript, Evidence Handoff v2 или guarded export без отдельного corpus-wide PROMOTE; missing model, слабая кластеризация и конфликт evidence должны давать fail-open aggregate Colleagues; завершить PROMOTE или воспроизводимым DO_NOT_PROMOTE, добавить тесты и актуализировать документацию, roadmap и OpsKarta.

## Why This Is Next

The stable pipeline already distinguishes `Me` from aggregate `Colleagues`, but a group meeting
still collapses every remote participant into one role. This weakens chronology review, decisions,
actions and future rich notes even when the words themselves are correct.

The prerequisite audio boundary is now clear. Causal Canonical Mic ASR v1 closed with
`DO_NOT_PROMOTE`: current Echo preparation is session-end causal and should not be weakened for
latency. Remote diarization is independent of that boundary because it consumes the already
authoritative remote track. It can add useful structure without touching capture, Echo Guard or the
selected transcript.

## Objective

Produce a deterministic, local and evidence-backed map of anonymous remote speakers. The first
version is audit-only: it annotates existing remote utterances and emits a shadow rich transcript,
while the ordinary `Colleagues` transcript remains authoritative.

## Required Work

1. Freeze a corpus containing known 1x1, group, noisy-office, overlap and long-session examples,
   including raw remote, selected dialogue and all evaluation metadata by SHA-256.
2. Define versioned contracts for diarization segments, anonymous session-local speaker IDs,
   utterance attribution, uncertainty and provenance.
3. Add a local model adapter with pinned model/runtime fingerprints and fail-open behavior. Missing
   model or incompatible runtime must produce an explicit unavailable report, not break processing.
4. Normalize model output into non-overlapping and overlap-aware remote intervals. Keep timing in
   the authoritative session clock and preserve every selected remote utterance and character.
5. Attribute existing remote utterances to anonymous speakers using bounded overlap evidence.
   Ambiguous or multi-speaker utterances remain aggregate `Colleagues` with explicit review flags.
6. Publish an isolated `remote_speaker_evidence_v1` report and a shadow rich transcript. Do not
   assign names, infer identity across meetings or mutate the selected transcript.
7. Measure deterministic replay, split/rejoin consistency, boundary stability, speaker-count
   plausibility, single-speaker false splits, overlap handling and text/timestamp preservation.
8. Decide `PROMOTE` or `DO_NOT_PROMOTE` on the frozen corpus. Promotion may expose the map as
   optional evidence, but cannot make it authoritative or alter export without a separate goal.

## Acceptance Gates

- every published speaker interval has source-audio, model and parameter provenance;
- repeated runs over unchanged inputs are byte-identical after timestamp normalization;
- selected remote text, utterance IDs, order and timestamps remain lossless;
- known 1x1 controls do not fragment one remote speaker without explicit uncertainty;
- group sessions produce stable session-local clusters under chunked and whole-file replay;
- overlap and low-confidence regions remain explicit instead of forcing a speaker assignment;
- raw CAF, Echo outputs, selected transcript, notes, verdict, review burden and guarded export do
  not change;
- missing model, model failure, stale artifacts or weak consistency return to aggregate
  `Colleagues` without failing the normal meeting pipeline;
- the frozen corpus ends in a reproducible `PROMOTE` or `DO_NOT_PROMOTE` decision with a measured
  evidence ceiling.

## Safety Boundary

- no names, personal identity inference or cross-session voice linking;
- no cloud audio upload and no second capture or primary ASR;
- no text correction, timestamp movement or selected-profile mutation;
- no Evidence Handoff v2, notes, export or retention changes in v1;
- no UI work and no promotion based only on a visually plausible diarization.

## Completed Predecessor

Causal Canonical Mic ASR v1 completed on 2026-08-06 with `DO_NOT_PROMOTE`. The frozen corpus had
`0/147` exact candidate windows and `0/8743.1315s` exact hard audio; `5/30/120s` prefix probes all
differed from final local-FIR PCM. Raw capture integrity and frozen-input replay passed. See
`docs/testing/2026-08-06-causal-canonical-mic-asr-v1.md`.
