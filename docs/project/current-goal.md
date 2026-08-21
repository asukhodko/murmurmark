# Current Goal

Updated: 2026-08-21

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Speaker-Bounded Chronology Evidence Arbitration v1

OpsKarta nearest goal: Speaker-Bounded Chronology Evidence Arbitration v1: доказательно разобрать 52 blocking chronology rows / 345.94s свежего шестисессионного корпуса по уже сохранённым локальным audio, speaker-state, group-overlap и stronger-audio evidence; заморозить входы и SHA-256, присвоить каждой строке стабильный outcome, закрывать только независимо подтверждённые benign turn boundaries и real double-talk, сохранить remote leak, ASR segmentation, true chronology risk и insufficient evidence явным остатком; не менять raw, ASR, текст, роли, timestamps или selected transcripts; закрыть не менее 50% строк и секунд либо выпустить точный EVIDENCE_BOUND; встроить остаток в terminal gate, обновить тесты, документы и OpsKarta, затем закоммитить и отправить результат в origin/main.

## Why Now

Speaker-Resolved Transcript Terminal Gate Instrumentation v1 показал `345.94s` хронологического
review, но эта сумма смешивала реальные риски порядка с обычными границами соседних реплик и
настоящим double-talk. Все 52 строки уже имеют group-overlap evidence, а 44 строки дополнительно
имеют локальное faster-whisper evidence. Новая запись и ручная разметка для первого безопасного
разделения не нужны.

## Objective

Превратить грубый chronology blocker в воспроизводимый остаток. Автоматический слой может закрыть
только строки, для которых временные, акустические и speaker-state доказательства независимо
показывают нормальную границу реплик или реальный double-talk. Всё остальное остаётся видимым.

## Required Work

1. Заморозить rebaseline, очередь, policy, реализацию и все входные артефакты по SHA-256.
2. Сопоставить order rows с group-overlap и stronger-audio evidence без чтения облачных данных.
3. Выдать для каждой строки один outcome: `benign_turn_boundary`, `confirmed_double_talk`,
   `remote_leak_or_asr_segmentation`, `true_chronology_risk` или `insufficient_evidence`.
4. Считать закрытыми только первые два outcome; отсутствие optional judge должно давать
   `insufficient_evidence`, а не ошибочный pass.
5. Выпустить privacy-safe JSON/Markdown report, private provenance и byte-exact replay.
6. Передать initial, closed и remaining chronology seconds в terminal gate как отдельный источник.
7. Добавить CLI, fixture, stale-input, privacy, no-mutation и replay tests.
8. Согласовать README, contracts, runbook, roadmap и OpsKarta; выполнить полный набор проверок,
   commit и push.

## Acceptance Gates

- все 52 строки имеют стабильный outcome и явную причину;
- безопасно закрыты не менее 50% строк и 50% секунд либо выпущен воспроизводимый evidence bound;
- ни одна строка не закрывается по одному similarity score или одному текстовому совпадению;
- raw CAF, Echo Guard, primary ASR, текст, роли, timestamps и selected transcripts неизменны;
- public artifacts не содержат session IDs, речь, имена или абсолютные пути;
- missing/stale evidence fail closed, повторный запуск byte-exact;
- terminal gate показывает исходные, закрытые и оставшиеся chronology seconds;
- код, тесты, документы и планы находятся в `origin/main`.

## Current Evidence

Реальный frozen run завершён с `PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1`. Закрыты 38 из 52
строк (`73.08%`) и `255.97s` из `345.94s` (`73.99%`): 34 benign turn boundaries и четыре
подтверждённых double-talk. Явный остаток составляет 14 строк / `89.97s`: 10 insufficient,
два remote leak или ASR segmentation и два настоящих chronology risks. Terminal gate остаётся
`NOT_READY`, но его chronology blocker теперь измеряет этот остаток, а не всю исходную очередь.

## Commands

```bash
murmurmark corpus chronology-arbitration-v1 all --refresh --write-snapshot
murmurmark corpus chronology-arbitration-v1 status
murmurmark corpus chronology-arbitration-v1 replay --write-snapshot
murmurmark corpus terminal-gate-v1 all --refresh --write-snapshot
```

## Out Of Scope

Transcript mutation, retiming, role reassignment, capture/Echo/ASR tuning, cloud inference,
speaker naming, filling the Human-Reviewed Lexical Seed and summaries.
