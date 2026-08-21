# Current Goal

Updated: 2026-08-21

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Human-Reviewed Lexical Seed v1

OpsKarta nearest goal: Human-Reviewed Lexical Seed v1: опираясь на завершённые Post-Segmentation Transcript Rebaseline v1, Capture Continuity Loss Closure v1 и Remote Unknown Evidence Recovery v1, заморозить минимум две репрезентативные реальные встречи — 1x1 и group в разных акустических режимах — материализовать короткую приватную очередь word-level review для Me и remote, получить прямую человеческую truth по словам без использования machine agreement как эталона, измерить WER/CER и domain-term accuracy текущего production transcript, выпустить REFERENCE_READY либо воспроизводимый EVIDENCE_BOUND, обновить тесты, документацию и планы, затем закоммитить и отправить результат в origin/main.

## Why Now

Remote Unknown Evidence Recovery v1 завершён `EVIDENCE_BOUND`. Все 547 strict unknown words имеют
стабильную причину и provenance. Независимый WavLM предложил 59 слов, но отдельное структурное
подтверждение осталось только у 10 слов / `4.682812s`; на untuned-сессии — 1 из 166 слов.
Ни один recovery-кандидат не пересёк 105 direct-truth items. Ослабление speaker gates закрыто,
Coverage v3 остаётся authoritative.

Следующий неизвестный размер — лексическая точность реальной речи. Цифровой 67-word subset уже
имеет WER/CER `0`, но это не доказывает качество живых созвонов. Автоматические ASR, облачная
транскрибация и согласие моделей не являются прямой truth.

## Objective

Получить небольшой, но достаточный человеческий эталон реальной речи, чтобы дальнейшие изменения
ASR-контекста оценивались по фактам, а не по впечатлению от отдельных транскриптов.

## Required Work

1. До просмотра текста заморозить две или больше сессий, выбранные профили, аудио, transcript words,
   роли, speaker labels и SHA-256 всех входов.
2. Покрыть 1x1 и group, Me и remote, наушники и/или громкую связь, обычные слова и доменные термины.
3. Построить короткую приватную review-очередь с точными audio intervals и неизменяемыми slot IDs.
4. Принимать только прямую человеческую разметку: exact text, inaudible, mixed или unusable.
5. Не показывать модельные ответы и не использовать облачную расшифровку как эталон.
6. Посчитать WER, CER, insertions/deletions/substitutions, domain-term accuracy, role/speaker
   conservation и отдельные результаты по сессиям.
7. Выпустить `REFERENCE_READY` либо точный `EVIDENCE_BOUND`; production ASR не менять в этой цели.
8. Обновить README, contracts, runbook, roadmap и OpsKarta; выполнить полный набор проверок,
   commit и push.

## Acceptance Gates

- минимум две fingerprint-bound реальные сессии: 1x1 и group;
- прямой эталон покрывает Me и remote и не содержит private text в tracked artifacts;
- все reviewed intervals имеют полный provenance и повторяемые slot IDs;
- WER/CER и domain-term accuracy считаются воспроизводимо и раздельно по режимам;
- selected transcript, Coverage v3, ASR cache, Echo Guard и raw CAF неизменны;
- повторный запуск детерминирован, public report не содержит абсолютных путей или речи;
- результат документирован, закоммичен и находится в `origin/main`.

## Out Of Scope

- tuning prompt/hotwords или замена основного ASR;
- автоматическая «truth» из согласия моделей или облака;
- изменение speaker attribution, capture и Echo Guard;
- summaries, exports, work-system updates и UI.

## First Commands

```bash
murmurmark corpus lexical-seed-v1 progress
murmurmark corpus lexical-seed-v1 review

murmurmark corpus lexical-seed-v1 evaluate \
  --write-snapshot docs/testing/human-reviewed-lexical-seed-v1-snapshot.json
murmurmark corpus lexical-seed-v1 replay \
  --write-snapshot docs/testing/human-reviewed-lexical-seed-v1-snapshot.json
```

Implementation status: the fingerprint-bound queue is frozen from two real sessions. It contains
24 primary intervals and four blind repeats across both roles, 1x1/group and low-leak/speaker
playback modes. Current decision is `REVIEW_REQUIRED` with `0/28` answers; production remains
unchanged.
