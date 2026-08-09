# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Session-Local Homogeneous Remote Speaker Enrollment Mining v1

OpsKarta nearest goal: Session-Local Homogeneous Remote Speaker Enrollment Mining v1: сохранить Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard и все frozen reports неизменными; заменить два часто смешанных шестисекундных enrollment exemplar на воспроизводимо добытые speaker-homogeneous интервалы только внутри той же сессии; использовать remote speech activity, устойчивые turn boundaries, несколько неперекрывающихся окон и согласие ECAPA с независимым WavLM evidence, не читая target text, human names и cross-session voices; заморозить mining policy и candidate pack до проверки на существующем development truth; выпустить HOMOGENEOUS_ENROLLMENT_READY, KEEP_EXISTING_ENROLLMENT либо EVIDENCE_BOUND без production promotion; добавить deterministic evaluator, tests и corpus report, обновить документацию и планирование, пройти проверки, закоммитить и отправить изменения.

## Why Now

Enrollment Purity and Abstention Hardening v2 завершён `KEEP_COVERAGE_V3`. Строгий двухсекундный
subwindow core сохранил все 68 Coverage v3 accepts и снизил unsafe accepts с 13 до контрольных 8,
но семь из четырнадцати profiles не прошли purity, а оставшиеся не дали ни одного нового safe
accept. Проблема находится в исходных enrollment-фрагментах, а не в пороге финального решения.

## Objective

Построить один замороженный session-local mining pipeline, который находит несколько длинных,
неперекрывающихся и голосово однородных remote-интервалов для каждого уже существующего анонимного
профиля. Не менять production и не строить новый classifier до доказательства качества материала.

## Required Work

1. Проверить frozen hashes Coverage v3, ECAPA shadow, direct truth и purity v2.
2. Определить speaker-homogeneous interval contract до чтения development outcomes.
3. Искать кандидаты только внутри remote track текущей сессии и поддержанных speech turns.
4. Отсекать overlap, mixed/open-set, короткие, тихие и boundary-неустойчивые интервалы.
5. Требовать несколько независимых окон и согласие двух разных embedding families.
6. Не использовать текст, имена, межсессионное связывание голосов или ручную новую разметку.
7. Заморозить candidate pack до development-оценки.
8. Измерить profile coverage, purity, diversity и способность сохранить три подтверждённых gain.
9. Сохранить exact words/timestamps, 68 Coverage v3 accepts и production guards.
10. Обновить Transcript Perfection, документы, tests, commit и push.

## Acceptance Gates

- все Coverage v3 решения и selected transcripts byte-exact сохранены;
- для пригодного profile есть минимум три неперекрывающихся окна из разных turns;
- окна проходят speech activity, boundary, within-profile consistency и impostor separation;
- ECAPA и WavLM согласны на profile membership; разногласие означает abstention;
- mixed, unknown, unusable и silent evidence не становятся enrollment;
- нет target-text leakage, threshold grid search, human names или cross-session linking;
- candidate pack заморожен до development truth evaluation;
- replay детерминирован, public outputs не раскрывают private session data;
- production promotion и новый disjoint truth остаются заблокированы.

## Terminal Outcomes

- `HOMOGENEOUS_ENROLLMENT_READY`: mining даёт достаточно чистый и разнообразный материал для
  отдельного additive candidate experiment.
- `KEEP_EXISTING_ENROLLMENT`: новый материал не улучшает доказуемую чистоту или покрытие.
- `EVIDENCE_BOUND`: нужные интервалы, модели, provenance или replay нельзя доказать.

## Previous Goal Result

Remote Speaker Enrollment Purity and Abstention Hardening v2 завершён `KEEP_COVERAGE_V3`: 7/14
profiles qualified, 0 additions, 0/3 confirmed gains preserved, 8 unsafe accepts как у control.
Exact word/timestamp conservation, 68 Coverage v3 accepts, 355 production guards и replay сохранены.

## After This Goal

1. `HOMOGENEOUS_ENROLLMENT_READY` открывает отдельный monotonic additive candidate.
2. Только прошедший development gates candidate открывает disjoint Direct Truth v2.
3. `KEEP_EXISTING_ENROLLMENT` закрывает эту ветку и возвращает остаток в явный `unknown`.
4. `EVIDENCE_BOUND` чинит только provenance, model availability или evidence acquisition.
