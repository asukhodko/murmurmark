# Current Goal

Updated: 2026-08-21

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Word-Level Chronology Localization v1

OpsKarta nearest goal: Word-Level Chronology Localization v1: на замороженных 14 chronology rows /
89.97s выполнить локальный второй проход faster-whisper large-v3 с word timestamps отдельно для
mic-clean и remote, привязать слова к исходным utterance IDs и определить фактические речевые
интервалы внутри широких ASR-сегментов; закрывать только доказанную последовательную границу,
реальный double-talk или доказанный перенос remote leak/segmentation, а слабое или конфликтующее
выравнивание оставить явным; не менять raw, ASR, текст, роли, опубликованные timestamps или selected
transcripts; закрыть не менее 50% строк и секунд либо выпустить точный EVIDENCE_BOUND; встроить
остаток в terminal gate, обновить тесты, документы и OpsKarta, затем закоммитить и отправить результат
в origin/main.

## Why Now

Предыдущий слой сократил chronology blocker с 52 строк / `345.94s` до 14 / `89.97s`, но использовал
таймкоды целых ASR-сегментов. Реальный пробный decode показал, что широкий overlap может содержать
последовательную речь с паузой. Все нужные клипы и локальная модель уже доступны, ручная разметка и
новая запись не требуются.

## Required Work

1. Заморозить upstream reports, residual rows, clips, model, policy и implementation по SHA-256.
2. Получить offline word timestamps отдельно для `mic_clean` и `remote` с воспроизводимым кэшем.
3. Выравнивать только содержательные слова исходных `Me`/remote utterances с независимыми дорожками.
4. Закрывать только доказанные sequential boundary, independent double-talk и remote-only transfer.
5. Оставлять missing, weak и conflicting alignment явным остатком.
6. Передать Terminal Gate полный initial/upstream/word-level/final счётчик chronology seconds.
7. Добавить CLI, fixture, stale-input, privacy, no-mutation и byte-exact replay checks.
8. Согласовать README, contracts, runbook, roadmap и OpsKarta; выполнить полный набор проверок,
   commit и push.

## Acceptance Gates

- все 14 строк имеют стабильный outcome и явную причину;
- закрыто не менее 50% строк и секунд либо опубликован точный evidence bound;
- ни один blocker не закрывается только по широкому segment timestamp или сходству дорожек;
- отсутствие модели или артефакта оставляет строку открытой;
- raw, ASR, текст, роли, timestamps и selected transcripts неизменны;
- public artifacts не содержат session IDs, речь или абсолютные пути;
- повторный запуск byte exact, Terminal Gate проверяет транзитивную provenance;
- код, тесты, документы и планы находятся в `origin/main`.

## Current Evidence

Цель достигла `PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1`. Закрыты 9/14 строк и `52.83s`:
шесть последовательных границ, два double-talk и один remote-only перенос. Пять строк / `37.14s`
остались `insufficient_word_alignment`. Общая chronology closure теперь `308.8/345.94s`.
Terminal Gate читает 10 fingerprint-bound источников и остаётся `NOT_READY` с точным остатком.

## Commands

```bash
murmurmark corpus chronology-localization-v1 status
murmurmark corpus chronology-localization-v1 replay --write-snapshot
murmurmark corpus terminal-gate-v1 status
```

## Out Of Scope

Transcript mutation, retiming, role reassignment, capture/Echo/primary-ASR tuning, cloud inference,
speaker naming, filling the Human-Reviewed Lexical Seed and summaries.
