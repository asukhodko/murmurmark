# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF, batch transcript and Evidence Handoff v2 remain authoritative. Optional speaker views must
never weaken that path.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Reviewed Remote Speaker Naming v1

OpsKarta nearest goal: Reviewed Remote Speaker Naming v1: поверх promoted Anonymous Rich Transcript Handoff v1 создать optional review-only label overlay, который принимает имена или метки только из явного session-local decision file с fingerprint-bound provenance, генерирует шаблон и проверяемый CLI read path, никогда не выводит имя из голоса и не связывает людей между сессиями, не меняет plain transcript, notes, Evidence Handoff v2, guarded export или auto-selection; доказать referential integrity, stale/fail-open/replay, privacy и corpus no-regression; завершить PROMOTE или воспроизводимым DO_NOT_PROMOTE, добавить тесты, актуализировать документацию, roadmap и OpsKarta, закоммитить и отправить изменения.

## Why This Is Next

Anonymous Rich Transcript Handoff v1 completed with `PROMOTE_OPTIONAL_RICH`. Six frozen sessions
passed all gates: all `1235` remote utterances have exact references, `629` received one of `14`
session-local anonymous speaker IDs and `606` remained aggregate `Colleagues`.

The optional view can now distinguish stable speakers, but `remote_speaker_02` is not useful meeting
memory by itself. The safe next step is explicit reviewed labeling. Acoustic similarity remains
evidence for grouping, never evidence for a person's identity.

## Objective

Provide a local, review-only overlay that lets a user assign a display label to a current anonymous
speaker ID and read a separately versioned reviewed rich transcript. Every assignment must be an
explicit session-local decision bound to the current anonymous handoff fingerprint.

## Required Work

1. Define versioned template, decision and reviewed-handoff schemas keyed by the current
   `remote_speaker_NN` IDs.
2. Add CLI commands to generate a review template, validate/apply completed decisions and read the
   optional reviewed speaker view.
3. Accept only explicit labels from the decision file. Do not infer names from voice, transcript,
   calendar, contacts or previous meetings.
4. Bind decisions to the anonymous rich semantic fingerprint, speaker set and exact utterance
   references. Stale, partial or malformed decisions fail open to the anonymous rich view.
5. Keep aggregate `Colleagues` untouched. Never force a label onto abstained evidence.
6. Prove deterministic replay, interrupted publication recovery, path/privacy safety and ordinary
   transcript, notes, verdict, Evidence Handoff v2, export and raw-audio non-regression.
7. Freeze a synthetic and real-session corpus decision. Promotion covers only the explicit reviewed
   read surface; downstream notes/export require a separate goal.

## Acceptance Gates

- every reviewed label was explicitly supplied for an existing current anonymous ID;
- no voice-only or cross-session identity assignment exists in code or artifacts;
- stale anonymous evidence or stale decisions cannot be read as current;
- duplicate, empty, unsafe or unmapped labels produce explicit validation errors;
- anonymous and ordinary views remain available when reviewed naming is unavailable;
- repeated runs are byte-identical and interrupted publication preserves the prior pointer;
- no private labels enter tracked fixtures, logs, notes or guarded export;
- the frozen corpus ends in reproducible `PROMOTE` or `DO_NOT_PROMOTE`;
- tests, contracts, runbook, README, roadmap and OpsKarta are current before commit and push.

## Safety Boundary

- no changes to capture, Echo Guard, ASR, transcript selection or anonymous clustering;
- no face/contact/calendar lookup and no network service;
- no automatic person naming, biometric identity store or cross-meeting voice matching;
- no notes, export, retention or UI integration in this stage.

## Completed Predecessor

Anonymous Rich Transcript Handoff v1 completed on 2026-08-06 with `PROMOTE_OPTIONAL_RICH`.
`murmurmark transcript SESSION --rich` verifies current fingerprints before reading the immutable
optional bundle. Missing or stale evidence leaves the ordinary transcript available and unchanged.
See `docs/testing/2026-08-06-anonymous-rich-transcript-handoff-v1.md`.
