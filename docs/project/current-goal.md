# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Stronger Remote Speaker Identity Backend Qualification v1

OpsKarta nearest goal: Stronger Remote Speaker Identity Backend Qualification v1: сохранить frozen
Error Decomposition v1, Truth Lab v1, once-opened hard-v2/hard-v3, Coverage v3 и production
неизменными; до сравнения зафиксировать не более трёх действительно разных локальных
speaker-verification backend, включая текущий control и минимум один независимо обученный
ECAPA-class или сопоставимый backend, вместе с model, license, runtime и SHA-256 provenance;
использовать существующие exact corpora только как development truth с заранее замороженными
enrollment, segment partition, scoring, calibration и open-set abstention; до выбора кандидата
создать и запечатать новый disjoint hard-v4 с новыми voices, scripts, durations, transitions,
overlap и open-set cases, затем выбрать не более одного кандидата на development по fixed gates и
открыть hard-v4 ровно один раз; выпустить `PROMOTE_LAB_IDENTITY_CANDIDATE` только при exact word
conservation, B-cubed F1 не ниже `0.85`, pairwise precision не ниже `0.99`, known-speaker recall не
ниже `0.80`, zero open-set false attribution, mixed fail-closed и boundary no-regression, иначе
выпустить воспроизводимый `DO_NOT_PROMOTE_IDENTITY_BACKEND`; не менять selected transcript,
Coverage v3, raw CAF, primary ASR, Echo Guard и synthetic-to-real boundary; добавить CLI,
автоматические тесты и Transcript Perfection source, обновить README, contracts, runbook,
current-goal, roadmap и OpsKarta, закоммитить и отправить изменения в origin/main.

## Why Now

Error Decomposition v1 учёл 393 exact words и 64 boundaries на трёх корпусах. Текущий primary track
получил known-speaker recall `0.571006` и boundary recall `0.421875`. Oracle-матрица дала приросты:

- speaker identity: `0.351382`;
- segmentation: `0.063882`;
- overlap/open-set: `0.036364`.

Identity oracle при текущих границах достигает known-speaker recall `0.934911` и B-cubed F1
`0.886021`. Это существенно больший резерв, чем повторная настройка boundary или overlap эвристик.

## Objective

Проверить, существует ли доступный локальный speaker-verification backend, который реализует
существенную часть измеренного identity ceiling и сохраняет консервативное abstention. Этап
квалифицирует только лабораторного кандидата; production меняется лишь отдельной будущей целью.

## Required Work

1. Заморозить Error Decomposition result и все upstream hashes как неизменяемый control.
2. До метрик объявить максимум три backend, model/license/runtime provenance и fixed selection rule.
3. Нормализовать enrollment и score calibration без утечки hard-v4 voices или scripts.
4. Построить disjoint hard-v4 до выбора кандидата и запечатать one-shot opening ledger.
5. На development truth выбрать максимум один backend, не перенастраивая rejected topology.
6. Открыть hard-v4 ровно один раз и применить фиксированные promotion gates.
7. Сохранить word-level provenance, model outputs, replay и portable public report.
8. Добавить CLI, тесты и новый Transcript Perfection source.

## Acceptance Gates

- model artifacts, licenses, runtimes, truth, predictions and ledgers are SHA-256 frozen;
- hard-v4 speakers, scripts and enrollment are disjoint from development data;
- every exact word and timestamp is conserved and accounted once;
- hard-v4 B-cubed F1 is at least `0.85`, pairwise precision at least `0.99` and known recall at least
  `0.80`;
- open-set false attribution is zero, mixed speech fails closed and boundaries do not regress;
- repeated runs are byte-identical;
- missing model or incompatible runtime fails closed without changing production;
- exactly one terminal decision is published.

## Safety Boundary

- v1, hard-v2 and hard-v3 are development evidence and cannot be called blind again;
- hard-v4 remains unopened until backend and gates are frozen;
- synthetic labels never enter real sessions;
- no cloud service, inferred human name or cross-session production identity;
- no selected transcript, Coverage v3, Echo Guard, ASR or raw CAF mutation.

## Previous Goal Result

Remote Speaker Attribution Error Decomposition v1 completed with
`ADVANCE_STRONGER_SPEAKER_IDENTITY`: identity is the dominant measured bottleneck, replay is
byte-exact, public artifacts contain no private speech or machine path, and Transcript Perfection
verifies `18/18` frozen sources.

## After This Goal

1. A passing lab candidate opens a separate real-session, fail-open production qualification.
2. A negative decision establishes the current local identity-model limit and moves the route to a
   new model family or direct reviewed evidence.
3. Dedicated segmentation remains deferred until identity is no longer the dominant measured axis.
