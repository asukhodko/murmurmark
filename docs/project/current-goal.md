# Current Goal

Updated: 2026-08-10

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Disjoint Truth Expansion v2

OpsKarta nearest goal: Remote Speaker Disjoint Truth Expansion v2: сохранить Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard, 30 frozen Transcript Perfection sources и прежние 33 primary + 8 repeat truth decisions неизменными; из остатка 851 unknown remote words / 598.240s и disagreement evidence ECAPA, WavLM, WeSpeaker и temporal AHC/VBx до чтения прежних labels детерминированно отобрать новый непересекающийся стратифицированный pack минимум из 72 primary и 12 hidden repeat slots на шести real sessions, включая short turns, boundaries, overlap, mixed и five-speaker session; исключить прежние интервалы и mixed exemplars, давать для сравнения только speaker-pure bounded exemplars и явные unknown/mixed/unusable outcomes; добавить короткую resumable CLI review-петлю, SHA-256 provenance, privacy-safe public aggregates, repeat-consistency и coverage отчёт; выпустить DIRECT_TRUTH_V2_READY либо REFERENCE_INSUFFICIENT без выбора модели и production promotion; обновить Transcript Perfection, документацию и планирование, пройти проверки, закоммитить изменения и отправить их в origin/main.

## Why Now

Fixed-window ECAPA/WavLM/WeSpeaker и temporal AHC/VBx проверены на одних 33 direct-truth items.
Последний backend прошёл temporal stability, но совпал по числу speakers в `0/6` сессий, сохранил
`2/3` gains, потерял control и добавил семь false identities. Настраивать следующий алгоритм на той
же маленькой truth выборке больше нельзя.

## Objective

Создать достаточный disjoint real-session reference, на котором следующий materially new speaker
model можно будет выбрать один раз без переиспользования development truth. Эта цель улучшает
доказательную базу и review UX, но не меняет production speaker attribution.

## Current State

Sampling и tooling готовы. Pack из `72` primary items / `148` words / `155.440894s` и `12` hidden
repeats заморожен на шести сессиях. Он не пересекается с 33 v1 primary intervals, содержит все
доступные model disagreements и использует 19 bounded exemplar clips без confirmed mixed
exemplars. Candidate-pack replay побайтно точен, все 355 guards проходят.

Review-pack сгруппирован в сессионные блоки: 11 переключений вместо 67, а чистые exemplars
проигрываются один раз на блок. Candidate pack при этом не изменился; после первого ответа
перестройка review-представления запрещена.

Остались blind review, terminal report, append в Transcript Perfection, переход планирования,
финальные проверки, commit и push. Разметка прерывается и продолжается одной командой:

```bash
murmurmark corpus remote-truth-seed-v2 review
```

## Required Work

1. Проверить 30 frozen Transcript Perfection sources, 355 production guards и прежний truth seed.
2. Запретить пересечение новых primary/repeat slots со всеми 33 прежними primary intervals.
3. До прежних labels заморозить sampling policy, source hashes, strata, interval bounds и pack.
4. Покрыть все шесть сессий, temporal/model disagreements, короткие turns, boundaries, overlap и
   five-speaker topology.
5. Не использовать mixed clips как speaker exemplars; каждый exemplar должен быть speaker-pure и
   bounded либо отсутствовать.
6. Дать outcomes `remote_speaker_N`, `unknown_speaker`, `mixed`, `unusable` без human names.
7. Сделать `next --play`, `grade`, `progress`, `finalize` и resume детерминированными и короткими.
8. Проверить hidden repeats, provenance, privacy и отсутствие production mutations.
9. Добавить corpus report, replay, fixtures, CLI, docs and planning; commit and push.

## Acceptance Gates

- минимум 72 disjoint primary и 12 hidden repeat slots либо точный `REFERENCE_INSUFFICIENT`;
- все шесть sessions и все заявленные strata представлены;
- нулевое пересечение с v1 primary intervals;
- ни один exemplar не содержит подтверждённую mixed speech;
- repeat consistency не ниже `0.85` для `DIRECT_TRUTH_V2_READY`;
- public artifacts не содержат speech, human names, absolute paths или private labels;
- Coverage v3, selected transcripts, raw CAF, ASR, Echo Guard и 355 guards неизменны;
- никакой candidate model не выбирается и не продвигается в этой цели.

## Terminal Outcomes

- `DIRECT_TRUTH_V2_READY`: новый disjoint reference достаточен для one-shot qualification следующего
  model class.
- `REFERENCE_INSUFFICIENT`: доступный residual не даёт достаточных чистых и проверяемых slots;
  отчёт фиксирует точный предел без ослабления требований.

## Previous Goal Result

Temporal End-to-End Remote Diarization Qualification v1 завершён `KEEP_EXPLICIT_UNKNOWN`.
Community-1-equivalent temporal backend дал stability ARI `0.814301` и activity Jaccard `0.972946`,
но speaker count совпал в `0/6` sessions, minimum duration recall составил `0.598626`, сохранились
`2/3` gains, потерян один control и добавлены семь false identities. Production остался Coverage v3.

## After This Goal

1. `DIRECT_TRUTH_V2_READY` разрешает выбрать один materially new speaker model и открыть disjoint
   one-shot qualification.
2. `REFERENCE_INSUFFICIENT` фиксирует evidence ceiling; дальнейшее улучшение потребует новых
   записей с известными remote speakers или отдельного controlled corpus.
