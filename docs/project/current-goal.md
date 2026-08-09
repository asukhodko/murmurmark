# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Enrollment Purity and Abstention Hardening v2

OpsKarta nearest goal: Remote Speaker Enrollment Purity and Abstention Hardening v2: сохранить Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard и все frozen v1 reports неизменными; использовать 33 primary direct-truth items только как development evidence; построить один monotonic session-local identity candidate, который никогда не удаляет и не меняет accepted Coverage v3 labels, очищает enrollment от слабых или смешанных exemplars и добавляет speaker ID только при независимом согласии purity, duration, margin и open-set evidence; отдельно измерить три подтверждённых v1 gains, восемь fail-closed false accepts, две lost controls и exemplar-purity limitation; выпустить CANDIDATE_READY_FOR_DISJOINT_TRUTH_V2, KEEP_COVERAGE_V3 либо EVIDENCE_BOUND без production promotion или tuning на будущем held-out; добавить deterministic evaluator, corpus report и tests; обновить документацию и планирование, пройти проверки, закоммитить и отправить изменения.

## Why Now

Direct-Truth Candidate Adjudication v1 закрыл прежнюю гипотезу `KEEP_COVERAGE_V3`. Frozen
weighted-centroid candidate нашёл три правильные identity, но потерял две верные control-метки и
увеличил fail-closed unsafe accepts с 8 до 13. Чистый итог составил лишь одну дополнительную
правильную identity (`0.125`).

Проблема теперь локализована: замена centroid может улучшать одни интервалы и портить другие, а
непроверенная чистота exemplars делает смелые accept опасными. Следующий кандидат должен быть
монотонным относительно Coverage v3 и жёстче воздерживаться.

## Objective

Построить один заранее описанный v2 candidate, который сохраняет все принятые Coverage v3 решения
и рассматривает только control abstentions. Direct Truth Seed v1 используется как development set
для проверки устройства правил, но не как held-out доказательство продвижения.

## Required Work

1. Проверить frozen hashes Coverage v3, enrollment v1, direct truth и adjudication v1.
2. Разложить ошибки v1 по exemplar purity, duration, margin, mixed/open-set и missing evidence.
3. Оценить чистоту каждого session-local exemplar без cross-session identity и human names.
4. Зафиксировать один порядок правил до итоговой development-оценки.
5. Никогда не удалять и не менять accepted Coverage v3 speaker ID.
6. Добавлять identity только для control abstention при согласии независимых evidence families.
7. Слабое, смешанное, короткое или конфликтующее evidence оставлять `unknown`.
8. Проверить exact word/timestamp conservation и production guards.
9. Выпустить frozen candidate и план disjoint Direct Truth v2, но не production profile.
10. Обновить Transcript Perfection, документацию, tests, commit и push.

## Acceptance Gates

- все 68 принятых Coverage v3 items byte-exact сохранены;
- ни одна существующая identity не заменена другой;
- на v1 development truth нет lost correct control и новых false identity;
- fail-closed unsafe accepts ниже 13 и не выше control 8;
- как минимум две из трёх подтверждённых additive gains сохранены либо честно выпущен
  `KEEP_COVERAGE_V3`;
- `unknown_speaker`, `mixed` и `unusable` не становятся положительной identity truth;
- нет threshold grid search, target-text leakage или cross-session voice linking;
- replay детерминирован, public outputs не содержат private speech/session/reviewer data;
- promotion заблокирован до disjoint held-out Direct Truth v2.

## Terminal Outcomes

- `CANDIDATE_READY_FOR_DISJOINT_TRUTH_V2`: monotonic candidate проходит development gates и может
  быть заморожен до нового blind held-out.
- `KEEP_COVERAGE_V3`: безопасный additive candidate на текущих evidence построить не удалось.
- `EVIDENCE_BOUND`: input integrity, purity evidence или deterministic replay нельзя доказать.

## Previous Goal Result

Remote Speaker Direct-Truth Candidate Adjudication v1 завершён `KEEP_COVERAGE_V3`. Все 33 primary
и 8 repeat slots учтены, 65 review files и 355 production guards проверены, replay совпадает.
Control дал 3, candidate 4 прямых правильных identity; candidate приобрёл 3, потерял 2 и поднял
fail-closed unsafe accepts с 8 до 13. Production не изменён.

## After This Goal

1. Положительный результат открывает только disjoint Direct Truth v2 и corpus qualification.
2. `KEEP_COVERAGE_V3` закрывает centroid/enrollment ветку и запускает новый residual rerank.
3. `EVIDENCE_BOUND` чинит только provenance или purity acquisition.
