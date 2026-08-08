# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Session-Local Remote Speaker Enrollment Hardening v1

OpsKarta nearest goal: Session-Local Remote Speaker Enrollment Hardening v1: сохранить Coverage v3,
ECAPA backend, selected transcripts, raw CAF, primary ASR и Echo Guard неизменными; SHA-256
заморозить 278-item shadow control, 83 enrollment-axis failure items / 119.920926s, 28 session-local
exemplars, их embeddings и independent/structural evidence; до открытия результата объявить ровно
один exemplar-only robust-centroid candidate, который использует только intra-session enrollment
similarity, leave-one-out stability и impostor margin, но не target-item outcomes; пересчитать
centroids и те же frozen item embeddings при thresholds 0.50/0.30; сравнить с control по acceptance,
precision, open-set abstention, silent fail-open, chronology и provenance; выпустить
`ADVANCE_HARDENED_ENROLLMENT_SHADOW`, `DO_NOT_ADVANCE_ENROLLMENT_HARDENING` либо `EVIDENCE_BOUND`
без production promotion; добавить CLI, fail-closed fixture/replay tests, portable report и Transcript
Perfection source; обновить документацию и планирование, пройти проверки, закоммитить и отправить
изменения.

## Why Now

Bounded Remote Speaker Interval Purification v1 завершён
`DO_NOT_ADVANCE_INTERVAL_PURIFICATION`. Фиксированный crop поднял coarse independent precision с
`0.878788` до `0.967742`, но восстановил только 2 слова / `4.154556s`, не прошёл material recovery
и создал одну новую reference-ошибку. Настройка границ на том же evidence запрещена.

Следующий измеренный предел из frozen decomposition: enrollment instability, 83 failure items /
`119.920926s`. Девять из 14 session-local speaker profiles нестабильны при leave-one-out.

## Objective

Проверить один заранее зафиксированный способ построения session-local enrollment centroid. Кандидат
может менять только состав или вес уже замороженных exemplar embeddings. Модель, item audio,
item embeddings, speaker choices и thresholds остаются прежними.

## Required Work

1. Заморозить shadow/decomposition/interval outputs, 28 exemplars и 83 enrollment failures.
2. До оценки описать один exemplar-only robust-centroid candidate без target-item supervision.
3. Проверить intra-speaker similarity, nearest-impostor margin и leave-one-out stability каждого
   exemplar; недостаточное enrollment оставлять fail-open.
4. Материализовать candidate centroids отдельно; control embeddings и clips не перезаписывать.
5. Пересчитать решения для тех же 278 item embeddings при thresholds `0.50/0.30`.
6. Сравнить recovered words/seconds, structural и independent precision, open-set и silent cases.
7. Проверить exact word/timestamp conservation, production guards, privacy и per-item provenance.
8. Выпустить один terminal outcome без production promotion и без tuning по результату.
9. Добавить CLI, синтетическую фикстуру, tamper test, byte-identical replay и public report.
10. Обновить Transcript Perfection, документацию и планы; пройти проверки, commit и push.

## Acceptance Gates

- все 278 items, 851 words, 28 exemplars и 83 enrollment failures учтены;
- кандидат объявлен до результата и не читает target-item truth/outcomes;
- ECAPA model/revision, item embeddings, speaker choices и thresholds не меняются;
- missing/weak enrollment приводит к `unknown`, а не forced identity;
- structural и independent precision, open-set behavior и silent fail-open не ухудшаются;
- candidate даёт заранее заданный материальный gain на enrollment scope;
- Coverage v3 labels, chronology, words, timestamps и selected transcripts сохранены;
- public artifacts не содержат speech text, имена, absolute paths или embeddings;
- repeated evaluation и replay детерминированы.

## Terminal Outcomes

- `ADVANCE_HARDENED_ENROLLMENT_SHADOW`: один candidate materially improves frozen enrollment scope
  без evidence regression; production qualification остаётся отдельной целью.
- `DO_NOT_ADVANCE_ENROLLMENT_HARDENING`: candidate не даёт безопасного материального улучшения;
  ветка не перенастраивается на том же evidence.
- `EVIDENCE_BOUND`: enrollment или reference недостаточны для надёжного сравнения.

## Safety Boundary

- Coverage v3 и ordinary speaker-resolved transcript остаются authoritative;
- human names, cross-session voice linking и target-item-supervised enrollment запрещены;
- capture, Echo Guard, primary ASR, item audio, selected transcript, export и live path не меняются;
- даже положительный enrollment candidate остаётся shadow до отдельной qualification.

## Previous Goal Result

Bounded Remote Speaker Interval Purification v1 завершён `DO_NOT_ADVANCE_INTERVAL_PURIFICATION`.
50 clips пересчитаны одним fixed crop; 43 items остались fail-open. Candidate сохранил structural
precision `1.0`, улучшил coarse independent precision до `0.967742`, но дал только 2 новых слова и
одну новую reference-ошибку. Replay byte-identical; production не менялся. Transcript Perfection
теперь проверяет 22/22 frozen sources.

## After This Goal

1. Положительный candidate проходит отдельную real-session/reference qualification.
2. Отрицательный результат закрывает enrollment и возвращает маршрут к identity backend или truth.
3. `EVIDENCE_BOUND` открывает только acquisition прямой truth, а не ослабление thresholds.
4. Production меняется только отдельной promotion-целью после corpus-wide no-regression.
