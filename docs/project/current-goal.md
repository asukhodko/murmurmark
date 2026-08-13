# Current Goal

Updated: 2026-08-13

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Usability Gate Error Decomposition v1

OpsKarta nearest goal: Remote Speaker Usability Gate Error Decomposition v1: сохранив Coverage v3 и frozen ERes2NetV2 qualification неизменными, доказательно разложить 7 unsafe special accepts, 9 missed positive items и truth-v1 regressions на speech usability, single-speaker purity, interval boundaries, enrollment и identity causes; определить доступные до identity label-independent observables; не выбирать candidate и не считать уже открытую Truth v2 новым terminal set; выпустить ADVANCE_USABILITY_GATE, ADVANCE_SEGMENTATION либо EVIDENCE_BOUND с replay, privacy-safe aggregate report, тестами, актуальными документами, коммитом и push.

## Why Now

Disjoint Model Qualification v1 завершён `KEEP_COVERAGE_V3`. ERes2NetV2 идеален на controlled
hard, правильно узнаёт 12/21 real-session identities и ни разу не заменяет известного участника
другим. При этом он принудительно атрибутирует семь `unknown/unusable` intervals и теряет два
truth-v1 correct controls. Следующая неизвестная находится перед identity decision: пригоден ли
сам аудиоинтервал и говорит ли в нём один поддержанный человек.

## Objective

Не строить очередную модель вслепую. Сначала получить воспроизводимую карту причин для каждого
ошибочного или пропущенного решения ERes2NetV2 и выяснить, существует ли наблюдаемый до разметки
аудиопризнак, который отделяет безопасные identity accepts от special/impure intervals.

Truth v2 теперь разрешена только как development evidence этой диагностики. Она не может повторно
служить terminal promotion set. Coverage v3, candidate ERes2NetV2, его пороги и predictions
остаются байт-в-байт неизменными.

## Required Work

1. Зафиксировать item-level analysis ledger для 72 Truth v2 primary, 12 repeats и truth-v1 controls.
2. Для каждой ошибки записать одну первичную причину и все supporting observables.
3. Считать только label-independent признаки, доступные до identity assignment: duration, speech
   activity, silence/noise, SNR, full/subwindow consensus, similarity/margin shape, embedding drift,
   model disagreement, overlap and boundary evidence.
4. Отделить `unusable`, `mixed`, unsupported/open-set voice, boundary contamination, impure
   enrollment и настоящую identity geometry ошибку.
5. Проверить устойчивость любой разделяющей гипотезы leave-one-session-out, не объявляя promotion.
6. Измерить, сколько unsafe accepts можно было бы reject и сколько correct identities при этом
   потерялось бы; не выбирать production threshold.
7. Выпустить один terminal route decision и точный контракт следующего независимого hard set.
8. Добавить CLI/status, tests, privacy-safe aggregate report, replay, docs, commit and push.

## Acceptance Gates

- все 7 Truth v2 unsafe accepts, 9 missed positives и truth-v1 regressions имеют стабильную причину;
- причины воспроизводятся из frozen audio/model evidence и не зависят от речи, имён или session IDs
  в public report;
- label-independent observables вычислены до присоединения truth outcome;
- leave-one-session-out analysis явно отделён от terminal qualification;
- words, timestamps, Coverage v3, raw CAF, selected transcripts, ASR and Echo Guard неизменны;
- replay byte-exact; отсутствующие evidence fail open to `unclassified`;
- следующий candidate не выбран и production gate не добавлен в этой цели.

## Terminal Outcomes

- `ADVANCE_USABILITY_GATE`: unsafe accepts имеют устойчивый до-identity audio signature; следующий
  этап строит rejector и новый disjoint terminal set.
- `ADVANCE_SEGMENTATION`: основной источник ошибок — смешанные или неверно ограниченные интервалы;
  следующий этап меняет segmentation before identity.
- `EVIDENCE_BOUND`: доступные observables не разделяют ошибки без неприемлемой потери correct
  identities; ветка закрывается до появления нового evidence source.

## Previous Goal Result

Disjoint Remote Speaker Model Qualification v1 завершён `KEEP_COVERAGE_V3`: frozen ERes2NetV2
получил Truth v2 precision `0.631579`, recall `0.571429`, 12 correct identities, 7 unsafe special
accepts и repeat determinism `1.0`. Controlled hard был perfect, но real-session safety не прошла.
Production и все 355 guards не изменились.
