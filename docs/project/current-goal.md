# Current Goal

Status: current

Updated: 2026-08-07

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Plain transcript, extractive notes and guarded export stay authoritative. Optional model-assisted
views may advance only through frozen local evidence gates and must fail open to those exact
outputs.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Evidence-Only Local Note Selection v1

OpsKarta nearest goal: Evidence-Only Local Note Selection v1: заменить свободную генерацию безопасным выбором и ранжированием только существующих statement IDs из current Reviewed Speaker-Aware Meeting Memory v1; публиковать текст, speaker provenance и evidence utterance IDs byte-for-byte из проверенного source, не разрешая модели создавать или редактировать claims; заморозить модель, ID-only prompt, selection policy и тот же six-session corpus, измерить сохранение baseline high-confidence items, полезное сжатие review-marked candidates, category/speaker coverage, deterministic replay, latency и memory, завершить PROMOTE_OPTIONAL_EVIDENCE_SELECTION либо DO_NOT_PROMOTE; default transcript/notes/export и existing handoffs оставить неизменными, fail open при любой stale/missing/malformed evidence, не использовать cloud, external writes, cross-session identity или UI; добавить tests/report, согласовать документацию, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Evidence-Guarded Local Synthesis Qualification v1 completed reproducibly with `DO_NOT_PROMOTE`.
The pinned 14.8B model produced 142 claims; 49 passed, 69 failed independent support checks and 24
were hidden as duplicates or top-N overflow. Safety rejection reached `0.485915`, and peak model
RSS reached `13978.922 MB`. All six replays were deterministic, references were exact, unsupported
published claims stayed at zero and ordinary outputs were byte-identical.

The failure is concentrated in authored wording and category drift. The evidence source itself is
sound. Removing text authorship from the model preserves its possible ranking value while deleting
the main unsupported-claim surface.

## Objective

Qualify a local model as an optional selector over existing evidence statements. The model may
return only known statement IDs and ordering metadata. User-visible text, speaker labels and
utterance IDs must be copied exactly from the verified source bundle.

## Required Work

1. Freeze one local model/revision, an ID-only prompt, selection limits and the same six-session
   corpus used by the failed free-generation qualification.
2. Build a compact candidate catalog with immutable statement IDs, categories, scores, review flags,
   speakers and evidence utterance IDs.
3. Accept only known IDs in permitted categories; reject duplicates, unknown IDs, category drift,
   malformed output and stale source fingerprints before publication.
4. Preserve every baseline high-confidence selected item unless a deterministic duplicate rule
   proves it redundant. The model cannot edit wording or create facts.
5. Measure category and speaker coverage, review-marked candidate reduction, deterministic replay,
   latency, memory and exact fallback against the current extractive notes.
6. Publish an isolated optional bundle only after corpus-wide promotion; otherwise record a precise
   `DO_NOT_PROMOTE` and leave all ordinary outputs unchanged.
7. Finish with fixture and corpus tests, policy/report, synchronized documentation, commit and push.

## Acceptance Gates

- model output contains only known source statement IDs and bounded rank/order fields;
- every displayed byte of claim text, speaker provenance and evidence IDs comes from current source;
- baseline high-confidence decisions/actions/risks/questions are retained or deterministically
  de-duplicated;
- unknown, stale, malformed or conflicting selection fails open to byte-identical extractive notes;
- repeated runs with frozen inputs and decoding parameters are deterministic;
- default transcript, notes, Evidence Handoff v2, speaker-aware memory and export bytes do not change;
- the corpus decision is explicit: `PROMOTE_OPTIONAL_EVIDENCE_SELECTION` or `DO_NOT_PROMOTE`.

## Safety Boundary

- no generated or edited factual wording;
- no cloud request, model pull or external write;
- no voice-only identity or cross-session participant roster;
- no capture, Echo Guard, ASR, transcript selection, default notes/export, retention or UI change;
- no promotion based only on fluent ordering or model self-confidence.

## Completed Checkpoint

Evidence-Guarded Local Synthesis Qualification v1 is closed with a frozen `DO_NOT_PROMOTE`.
Its exact result, model identity and resource ceiling are recorded in
`docs/research/2026-08-07-evidence-guarded-local-synthesis-v1.md`. No user-facing local-synthesis
mode was activated.
