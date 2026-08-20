# Remote Speaker Boundary and Minority-Voice Segmentation v1 Runbook

Работа завершена. Команды ниже воспроизводят замороженный результат, но не меняют транскрибации.

## Проверка

```bash
murmurmark corpus remote-boundary-minority-v1 status
murmurmark corpus remote-boundary-minority-v1 replay
```

Ожидается:

```text
decision: KEEP_COVERAGE_V3
replay: byte-exact
```

Отчёт:

```bash
less sessions/_reports/remote-speaker-boundary-minority-v1/report.md
jq '{decision, gates, safety}' \
  sessions/_reports/remote-speaker-boundary-minority-v1/report.json
```

## Полная Лабораторная Последовательность

Эта последовательность удаляет прежний локальный отчёт и создаёт новый freeze. Используй её только
при намеренном повторе исследования на тех же неизменных входах:

```bash
murmurmark corpus remote-boundary-minority-v1 prepare
murmurmark corpus remote-boundary-minority-v1 freeze
murmurmark corpus remote-boundary-minority-v1 evaluate \
  --write-manifest docs/testing/remote-speaker-boundary-minority-v1-manifest.json
murmurmark corpus remote-boundary-minority-v1 replay
```

После `freeze` нельзя менять policy или evaluator. Для нового candidate нужен новый versioned
контракт и новый disjoint terminal reference. Подстраивать v1 по открытому terminal result нельзя.

## Интерпретация

Candidate сохранил все слова и хорошо находил многие реальные смены голоса, но принял обычные
внутриспикерные паузы за границы и затем нестабильно слил разделы обратно. Поэтому он остаётся
диагностикой. Рабочий transcript продолжает использовать Coverage v3 и explicit unknown.
