# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Duration-Aware Remote Speaker Attribution v2

OpsKarta nearest goal: Duration-Aware Remote Speaker Attribution v2: до любых изменений алгоритма заморозить новый private exact-scripted hard-v2 с ранее не использованными scripts, renderer voices, source stems, word/speaker/timestamp truth и SHA-256; считать Truth Lab v1 train/dev/hard только development evidence и не использовать hard-v2 до окончательного freeze кандидата; реализовать и сравнить не более трёх заранее объявленных topology — duration-binned prototype bank, cohort-normalized WavLM и conservative Resemblyzer/WavLM fusion — с timestamp-only mixed detection и fail-open unknown; выбрать кандидата только по development, запустить hard-v2 ровно один раз и завершить PROMOTE_LAB_CANDIDATE лишь при exact word conservation, deterministic replay, B-cubed F1 и pairwise precision >=0.98, known-speaker recall >=0.98, boundary recall 100%, zero open-set false attribution и полном non-regression Coverage v3 control, иначе выпустить DO_NOT_PROMOTE_TOPOLOGY; не менять selected transcript, Coverage v3, raw CAF, primary ASR или Echo Guard и не переносить synthetic labels в real sessions; хранить generated speech и truth в private ignored artifacts, tracked outputs ограничить агрегатами, portable paths и hashes; добавить CLI, тесты и corpus report, обновить README, contracts, runbook, current-goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Truth Lab v1 supplied direct exact evidence instead of model agreement. Its clean held-out control
qualified the frozen Coverage v3 decision shape, while the independent WavLM candidate failed on
short words and a previously unseen open-set voice.

The v1 hard split has now been observed and must never become the final test for another candidate.
The next honest step is to freeze a new hard-v2 before topology development, use v1 only as
development evidence, and run hard-v2 once after the candidate is sealed.

## Objective

Determine whether duration-matched prototypes, cohort score normalization or conservative backend
fusion can retain the Coverage v3 control quality while adding genuinely independent open-set
evidence. End with `PROMOTE_LAB_CANDIDATE` or reproducible `DO_NOT_PROMOTE_TOPOLOGY`.

## Required Work

1. Freeze a private hard-v2 with new scripts, source hashes and renderer voices not present in v1.
2. Record the freeze before implementing or selecting any candidate topology.
3. Treat all v1 splits as development; do not report v1 hard as fresh held-out evidence again.
4. Predeclare at most three candidates: duration-binned prototypes, cohort-normalized WavLM and
   conservative Resemblyzer/WavLM fusion.
5. Use only timestamps and audio for mixed/open-set decisions; truth remains evaluation-only.
6. Select one candidate on development, seal its configuration, then open hard-v2 once.
7. Add deterministic replay, privacy checks, CLI, tests and aggregate corpus reporting.

## Acceptance Gates

- exact hard-v2 word conservation and direct scripted truth coverage;
- source stems reconstruct every mixture with zero PCM sample error;
- hard-v2 inputs predate candidate freeze and are not used for tuning;
- B-cubed F1 and pairwise precision are at least `0.98`;
- known-speaker attribution recall is at least `0.98`;
- all scripted boundaries are recovered;
- no unseen open-set word is assigned to an enrolled speaker;
- Coverage v3 control metrics and safety behavior do not regress;
- replay is deterministic and public artifacts contain no speech, names or absolute paths.

Any failed gate produces `DO_NOT_PROMOTE_TOPOLOGY`. Threshold relaxation after hard-v2 is opened is
forbidden.

## Safety Boundary

- lab promotion is not real-session promotion;
- selected transcript, Coverage v3, raw CAF, primary ASR and Echo Guard remain unchanged;
- no synthetic label enters a real session or reviewed speaker memory;
- no cloud speech service or cross-session human identity;
- real residual proposals stay blocked without direct blind truth.

## Previous Goal Result

Controlled Remote Speaker Truth Lab v1 completed with a split result:

- 8 sessions, 6 anonymous local voices and 240 exact words;
- exact stems/mixtures/truth, zero-sample reconstruction error and deterministic replay;
- Coverage v3 control: B-cubed `0.983505`, pairwise precision `1.0`, boundaries `16/16`, open-set
  false attribution `0`;
- WavLM candidate: B-cubed `0.834325`, pairwise precision `0.950920`, boundaries `10/16`, open-set
  false attribution `2`;
- overall candidate decision: `DO_NOT_ADVANCE`;
- production and the six-session real residual remain unchanged.

## After This Goal

1. `PROMOTE_LAB_CANDIDATE` permits only a bounded candidate against direct real reference.
2. `DO_NOT_PROMOTE_TOPOLOGY` closes this model family and keeps Coverage v3 as the supported source.
3. Real blind residual review and real lexical reference remain external evidence prerequisites.
4. Local mic multi-speaker diarization remains conditional on a real consented scenario.
