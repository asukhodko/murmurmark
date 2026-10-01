# Current Goal

Updated: 2026-10-01

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Bounded Evidence Compute v1

OpsKarta nearest goal: Bounded Evidence Compute v1: квалифицировать новый воспроизводимый корпус для изменения frozen ASR producer, подключить session-local exact-PCM micro-ASR cache без смешения provenance current/shadow, обеспечить отменяемые lock/decode и атомарную публикацию; измерить cold/warm время, новые decodes и время до атрибутированного текста при неизменном resource profile, сохранить все conservation/fallback gates, raw и основной текст; при недостатке evidence оставить production неизменным с точным DO_NOT_PROMOTE, обновить документацию и планы, завершить этап коммитом и push.

## Why Now

Первый пакет ремонта publication/review готов. Остаются повторные micro-ASR decodes и долгое
ожидание результата. Candidate cache проверен отдельно, но не подключён к production. Raw
недоступен для 11/12 прежних Echo v2.17 qualification sessions; прежний replay нельзя воспроизвести.
Основания и границы R5: [план ремонта](2026-09-30-transcript-reliability-repair-plan.md).

## Required Work

1. Зафиксировать доступный новый корпус, baseline outputs, модели, runtime и ресурсные настройки.
2. Квалифицировать изменение producer с сохранением всех действующих conservation/fallback gates.
3. Подключить общий session-local cache для точных PCM/config, сохраняя provenance потребителей.
4. Проверить cold/warm, force, concurrent requests, отмену, corrupt entries и перенос timestamps.
5. Измерить новые decodes, время до текста и полный lifecycle; выпустить результат без смены profile.

## Acceptance Gates

1. Новые policy hashes допустимы только после воспроизводимой квалификации, не замены SHA на доверии.
2. Для одного валидного ключа без force выполняется один успешный decode; replay не запускает модель.
3. Ключ проверяет PCM, модель, executable, prompt/VAD и влияющие runtime settings; stale не переиспользуется.
4. Отмена и испорченный cache не публикуют частичный результат, не теряют checkpoint и не оставляют workers.
5. Слова, роли, порядок, качество, допустимая атрибуция и raw сохраняются; R1-R4/R6 не регрессируют.
6. Есть cold/warm измерения на одинаковых входах и ресурсах, тесты, согласованные документы, commit/push.
7. Недостаточный корпус означает явный DO_NOT_PROMOTE, а не ослабление gate или обещание ускорения.

## Current Evidence

Промежуточный ремонт публикации 1 октября отделён от изменения producer. Provisional view
показывает локальные review-причины, проверяет вложенные времена и не ставит remote-текст раньше
доказанного конца цифровой тишины. Это приблизительный display bound, не word alignment и не
пересчёт overlaps/Me repair. Micro-ASR context ownership теперь проверяется в review; remote
needs_review больше не теряется из-за Me-only фильтра. Слова и frozen evidence не переписываются.
Состояние и следующий допуск: [repair checkpoint](2026-10-01-acoustic-publication-repair.md);
корпус и границы проверки: [отчёт](../testing/2026-10-01-acoustic-publication-repair.md).

Helper прошёл synthetic checks: шесть concurrent requests дают один fake decode; current/shadow
могут разделять session-local cache. Это не production benchmark. Primary transcriber и strict
selector пока не изменены. Интеграция и квалификационный корпус остаются открытой работой.

## Completed Checkpoint: Review-Safe Attributed Handoff v1

R1-R4/R6 реализованы: compatible publication, scoped review/provenance, bounded cancellation и
fingerprint-bound очередь. Три cached replay дали coverage 84.97%, 91.67%, 49.54% без нового ASR
и изменения raw/основного текста. Coverage не является accuracy; quality/export gates сохранены.
Выпускные проверки и ограничения: [отчёт](../testing/2026-09-30-review-safe-handoff.md).

## Out Of Scope

Capture, Echo Guard tuning, primary-ASR tuning, live promotion, cloud inference, voice-derived names,
семантическая правка слов по догадке, суммаризация и снятие retention pins.

## Completed Checkpoint: Word-Level Chronology Localization v1

Ниже сохранена постановка и результат завершённого этапа (`45f1785`). Это не текущая работа.

Previous goal: Word-Level Chronology Localization v1: на замороженных 14 chronology rows /
89.97s выполнить локальный второй проход faster-whisper large-v3 с word timestamps отдельно для
mic-clean и remote, привязать слова к исходным utterance IDs и определить фактические речевые
интервалы внутри широких ASR-сегментов; закрывать только доказанную последовательную границу,
реальный double-talk или доказанный перенос remote leak/segmentation, а слабое или конфликтующее
выравнивание оставить явным; не менять raw, ASR, текст, роли, опубликованные timestamps или selected
transcripts; закрыть не менее 50% строк и секунд либо выпустить точный EVIDENCE_BOUND; встроить
остаток в terminal gate, обновить тесты, документы и OpsKarta, затем закоммитить и отправить результат
в origin/main.

### Why Now

Предыдущий слой сократил chronology blocker с 52 строк / `345.94s` до 14 / `89.97s`, но использовал
таймкоды целых ASR-сегментов. Реальный пробный decode показал, что широкий overlap может содержать
последовательную речь с паузой. Все нужные клипы и локальная модель уже доступны, ручная разметка и
новая запись не требуются.

### Required Work

1. Заморозить upstream reports, residual rows, clips, model, policy и implementation по SHA-256.
2. Получить offline word timestamps отдельно для `mic_clean` и `remote` с воспроизводимым кэшем.
3. Выравнивать только содержательные слова исходных `Me`/remote utterances с независимыми дорожками.
4. Закрывать только доказанные sequential boundary, independent double-talk и remote-only transfer.
5. Оставлять missing, weak и conflicting alignment явным остатком.
6. Передать Terminal Gate полный initial/upstream/word-level/final счётчик chronology seconds.
7. Добавить CLI, fixture, stale-input, privacy, no-mutation и byte-exact replay checks.
8. Согласовать README, contracts, runbook, roadmap и OpsKarta; выполнить полный набор проверок,
   commit и push.

### Acceptance Gates

- все 14 строк имеют стабильный outcome и явную причину;
- закрыто не менее 50% строк и секунд либо опубликован точный evidence bound;
- ни один blocker не закрывается только по широкому segment timestamp или сходству дорожек;
- отсутствие модели или артефакта оставляет строку открытой;
- raw, ASR, текст, роли, timestamps и selected transcripts неизменны;
- public artifacts не содержат session IDs, речь или абсолютные пути;
- повторный запуск byte exact, Terminal Gate проверяет транзитивную provenance;
- код, тесты, документы и планы находятся в `origin/main`.

### Current Evidence

Цель достигла `PROMOTE_WORD_LEVEL_CHRONOLOGY_LOCALIZATION_V1`. Закрыты 9/14 строк и `52.83s`:
шесть последовательных границ, два double-talk и один remote-only перенос. Пять строк / `37.14s`
остались `insufficient_word_alignment`. Общая chronology closure теперь `308.8/345.94s`.
Terminal Gate читает 10 fingerprint-bound источников и остаётся `NOT_READY` с точным остатком.

### Commands

```bash
murmurmark corpus chronology-localization-v1 status
murmurmark corpus chronology-localization-v1 replay --write-snapshot
murmurmark corpus terminal-gate-v1 status
```

### Out Of Scope

Transcript mutation, retiming, role reassignment, capture/Echo/primary-ASR tuning, cloud inference,
speaker naming, filling the Human-Reviewed Lexical Seed and summaries.
