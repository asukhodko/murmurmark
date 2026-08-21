# Current Goal

Updated: 2026-08-21

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.

## Speaker-Resolved Transcript Terminal Gate Instrumentation v1

OpsKarta nearest goal: Speaker-Resolved Transcript Terminal Gate Instrumentation v1: материализовать независимый fingerprint-bound прибор North Star на свежем шестисессионном корпусе; раздельно измерять durable capture, Target-Me preservation, lexical accuracy, chronology and conservation, remote speaker attribution, explicit unknown, mandatory review burden и speaker-resolved publication с exact aggregate fallback; запретить усреднённый quality score и ложный pass при missing/stale evidence; сохранить Human-Reviewed Lexical Seed v1 отдельным blocked evidence node; выпустить TERMINAL_GATE_INSTRUMENT_READY либо EVIDENCE_INCOMPLETE с точными блокерами, детерминированным replay и privacy-safe snapshot, не меняя production audio/transcript, затем обновить тесты, документы и OpsKarta, закоммитить и отправить результат в origin/main.

## Why Now

Предыдущие исследования доказали отдельные свойства, но не отвечали одной командой, какие части
North Star уже пройдены и что именно мешает назвать результат готовым. Human-Reviewed Lexical Seed
заморожен, но требует 28 прямых человеческих ответов. Этот внешний долг не должен мешать измерить
все остальные границы и подготовить честную карту сходимости.

## Objective

Создать воспроизводимый измерительный слой над существующими каноническими отчётами. Готовность
прибора и готовность продукта должны быть независимыми решениями: `TERMINAL_GATE_INSTRUMENT_READY`
может сопровождаться `product_decision=NOT_READY`.

## Required Work

1. Переквалифицировать текущий provisional materializer и восстановить byte-exact fresh rebaseline.
2. Заморозить схемы, SHA-256 и решения всех восьми источников в приватном manifest.
3. Выдать отдельный `pass|bounded|blocked|not_measured` для каждого измерения без общего score.
4. Fail closed при отсутствующем, изменённом или несовместимом источнике.
5. Добавить CLI `murmurmark corpus terminal-gate-v1 ...`, JSON/Markdown report и tracked snapshot.
6. Проверить privacy, stale evidence, product-ready fixture, byte-exact replay и отсутствие мутаций.
7. Согласовать README, contracts, runbook, roadmap и OpsKarta; commit и push.

## Acceptance Gates

- все восемь измерений явны и имеют собственное состояние, метрики, evidence и blocker;
- instrument readiness не зависит от того, прошёл ли продукт все quality gates;
- WER/CER остаются `null`, пока Human-Reviewed Lexical Seed не стал `REFERENCE_READY`;
- public artifacts не содержат session IDs, речь, имена, абсолютные пути или private manifest;
- stale source переводит прибор в `EVIDENCE_INCOMPLETE`, а replay сравнивает байты;
- raw CAF, Echo Guard, ASR, Coverage v3, selected transcripts и human answers неизменны;
- полный набор проверок проходит, изменения находятся в `origin/main`.

## Current Evidence

Реальный baseline уже даёт `TERMINAL_GATE_INSTRUMENT_READY` и `product_decision=NOT_READY`.
`review_burden` и `speaker_resolved_publication` проходят. Continuity, Target-Me residual,
chronology, current-corpus speaker-count truth и unknown-duration ограничены доказательствами;
lexical accuracy заблокирована очередью `0/28`. Полный прогон дополнительно выявил и устранил
устаревшую transitive-ссылку Remote Unknown Recovery; отдельный regression теперь проверяет этот
случай покомпонентно.

## Commands

```bash
murmurmark corpus terminal-gate-v1 all --refresh --write-snapshot
murmurmark corpus terminal-gate-v1 status
murmurmark corpus terminal-gate-v1 replay --write-snapshot
```

## Out Of Scope

Capture/Echo/ASR tuning, transcript mutation, forced speaker labels, cloud inference, summaries,
exports, UI and filling human lexical answers on behalf of the user.
