# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Lexical Accuracy Reference Corpus v1

OpsKarta nearest goal: Lexical Accuracy Reference Corpus v1: превратить `recognized_words.lexical_correctness_not_measured` в воспроизводимое локальное измерение, не меняя основной ASR: создать приватный reference contract с уровнями доверия, импортировать точные scripted Echo Lab references и доступные external transcripts только как явно graded evidence, выровнять их с authoritative utterance/word timestamps, считать WER/CER, omissions, insertions, substitutions, domain-term и role-specific accuracy; tracked manifest должен содержать только пути/sha/метрики без текста и имён, weak machine references не могут считаться ground truth; проверить deterministic replay, reference leakage, 1x1/group/acoustic modes и неизменность selected transcript/speaker default; обновить Transcript Perfection ranking и выбрать один крупнейший доказанный lexical defect либо честно завершить `REFERENCE_INSUFFICIENT`; capture, Echo Guard, primary ASR, speaker profiles и optional derivatives не менять; актуализировать README, contracts, runbook, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Speaker-Resolved Transcript Default v1 уже продвинут: обычный transcript/handoff/export выбирает
Coverage v3 на совместимых сессиях и возвращает exact aggregate Markdown при несовместимости.
Transcript Perfection после этого всё ещё показывает одну принципиальную слепую зону:
`recognized_words.lexical_correctness_not_measured`.

Сохранность выбранных слов доказана, но она не отвечает на вопрос, верно ли Whisper распознал
произнесённое. Без эталона нельзя объективно выбирать между заменами, вставками, пропусками,
ошибками терминов и новыми ASR-кандидатами.

## Objective

Создать приватный и воспроизводимый эталон лексической точности, который различает настоящую truth,
контролируемый scripted reference и слабую независимую machine reference. Получить WER/CER и
разложение ошибок либо точно доказать, какого reference evidence пока не хватает.

## Required Work

1. Зафиксировать private reference schema, trust grades и запрет считать machine agreement truth.
2. Импортировать точные scripted Echo Lab фразы и доступные внешние транскрипты с явным grade.
3. Выровнять reference с authoritative utterance/word timestamps без изменения selected transcript.
4. Считать WER/CER, substitutions, omissions, insertions, domain terms и показатели по ролям.
5. Хранить речь и имена только в ignored private data; tracked manifest содержит SHA-256 и метрики.
6. Проверить 1x1/group/acoustic modes, replay, leakage и Speaker-Resolved Default non-regression.
7. Обновить Transcript Perfection ranking и выбрать один измеренный ASR defect или выпустить
   `REFERENCE_INSUFFICIENT` с точным пределом.

## Acceptance Gates

- reference rows имеют source grade, exact provenance и неизменяемые SHA-256;
- scripted truth отделена от human-reviewed и independent-machine evidence;
- weak references не повышают lexical correctness до `passed`;
- WER/CER и error classes детерминированы и проверяются повторным прогоном;
- tracked файлы не содержат transcript text, имена или абсолютные пользовательские пути;
- selected transcript, speaker selection, raw CAF и текущие Perfection dimensions не изменены;
- итогом является измеренный lexical baseline или воспроизводимый `REFERENCE_INSUFFICIENT`.

## Safety Boundary

- no capture, Echo Guard, primary ASR or speaker-profile changes;
- no cloud runtime, implicit upload or model download;
- no tuning on test/hard references before a frozen split exists;
- no synthetic aggregate quality score;
- no optional notes, summaries, external writes or UI work.

## Previous Goal Result

Speaker-Resolved Transcript Default v1 completed with `PROMOTE`:

- 6/6 frozen sessions: two 1x1 and four group calls;
- 14 expected anonymous session-local speakers and 5/5 internal boundaries;
- exact selected words, text, roles, `Me`, timestamps, order, raw preservation and replay;
- ordinary transcript, Evidence Handoff v2 and guarded export carry the selected speaker profile;
- stale, missing and failed local evidence returns exact aggregate Markdown;
- human names remain complete fingerprint-bound review only.

## After This Goal

1. Fix the largest measured lexical defect without changing unrelated pipeline stages.
2. Revisit unknown remote speakers only with a genuinely independent pinned backend or stronger
   enrollment evidence; v4 already closed the current Resemblyzer continuation.
3. Open local mic multi-speaker diarization only after a real labelled scenario exists.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory.
