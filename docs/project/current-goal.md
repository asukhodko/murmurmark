# Current Goal

Updated: 2026-08-20

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Post-Segmentation Transcript Rebaseline v1

OpsKarta nearest goal: Post-Segmentation Transcript Rebaseline v1: зафиксировав terminal outcome KEEP_COVERAGE_V3 и неизменный production Coverage v3, пересобрать свежий корпус speaker-resolved транскрибаций, проверить selected/provisional/aggregate surfaces и ранжировать остаточные topology, unknown, overlap, chronology и lexical defects; выпустить один evidence-backed приоритет следующего улучшения с frozen inputs, exact replay, no-regression gates, privacy-safe отчётом, тестами, документацией, коммитом и push.

## Why Now

Remote Speaker Boundary and Minority-Voice Segmentation v1 закрыт честным `KEEP_COVERAGE_V3`.
Замороженный candidate сохранил все слова, но на реальном diagnostic reference получил boundary
precision `0.044688`, speaker-count ratio `0.5`, minority-speaker recall `0.017161` и timing-shift
partition ARI `0.289387`. Простые спектральные границы и текущая кластеризация не готовы заменить
Coverage v3. Подстраивать их по уже открытому terminal reference нельзя.

За длинной серией speaker experiments накопились результаты на разных корпусах и профилях. Прежде
чем выбирать ещё одну модель или эвристику, нужно пересчитать текущий продуктовый результат на
свежих сессиях единым способом и сравнить остатки между собой. Иначе следующая цель снова будет
оптимизировать отдельный симптом.

Перед rebaseline закрыт обнаруженный операционный долг **Enrichment Transaction and Cache
Coherence v1**. Отложенный Echo/review путь теперь повторно строит зависимые слои, переносит только
совместимые review-решения, сохраняет их историю и сводит progress, readiness, speaker selection и
outcome к одному профилю и одной очереди. На сессии `2026-08-20_11-31-56` повторный прогон сохранил
SHA-256 `reviewed_v1`, raw CAF и 47 исторических решений; во всех пользовательских отчётах осталось
20 строк / `111.35s`. Этот prerequisite больше не смешивает устаревшие метрики с fresh rebaseline.

## Objective

Создать один воспроизводимый rebaseline текущего speaker-resolved pipeline без изменения production.
Он должен показать, где теперь находится крупнейший доказанный вред: в смешанных remote-кластерах,
explicit unknown, overlap/chronology, словах или выборе пользовательской read surface.

## Required Work

1. Заморозить версии Coverage v3, Transcript Integrity, provisional read policy, выбранные профили,
   список свежих сессий и SHA-256 всех входов.
2. Пересобрать или проверить с кэшем speaker-resolved, aggregate и provisional outputs на одинаковом
   корпусе 1x1/group и speaker/headphones/office режимов.
3. Проверить, что `murmurmark transcript` выбирает ожидаемый rich output, disclaimer виден при слабой
   атрибуции, а `--aggregate` остаётся точным fallback.
4. Раздельно измерить capture completeness, word/order/role conservation, attributed/unknown
   coverage, cluster topology, mixed intervals, chronology, lexical evidence и review burden.
5. Не объединять эти оси в один средний score. Для каждой ошибки хранить source profile, lineage и
   доказательство.
6. Сравнить fresh corpus с замороженным baseline и зарегистрировать все регрессии, stale artifacts
   и несовместимые сессии.
7. Выбрать ровно один следующий технический приоритет по величине доказанного вреда, доступности
   truth и ожидаемой проверяемости решения.
8. Выпустить privacy-safe corpus report, exact replay, автоматические тесты и синхронизировать CLI,
   README, roadmap, OpsKarta и документацию.

## Acceptance Gates

- boundary/minority v1 остаётся `KEEP_COVERAGE_V3`; его policy, freeze и terminal report неизменны;
- production Coverage v3, ASR, Echo Guard, raw CAF и selected transcript inputs не изменены;
- все входные сессии fingerprint-bound, missing/stale evidence явно исключено с причиной;
- слова, порядок, роли и timestamps проверены отдельно от speaker attribution;
- rich, provisional и aggregate surfaces проверены для каждой пригодной сессии;
- итог содержит ранжированный список residual axes и один ближайший рекомендуемый axis;
- public report не содержит имён, речи, session IDs или absolute paths;
- повтор byte-exact, а failures не переписывают production artifacts.

## Out Of Scope

- новая diarization или embedding model;
- настройка boundary/minority v1 после terminal evaluation;
- изменение Capture, Echo Guard, основного ASR или pre-ASR separation;
- forced identity, cloud inference, UI, notes и summaries;
- ручная lexical truth, если её нет в уже подготовленных frozen sources.

## First Commands

```bash
murmurmark corpus remote-boundary-minority-v1 status
murmurmark corpus remote-boundary-minority-v1 replay
murmurmark corpus remote-coverage all --verify-existing
murmurmark corpus speaker-default all --verify-existing
murmurmark corpus perfection all --verify-existing
scripts/check.sh
```
