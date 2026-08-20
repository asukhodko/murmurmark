# Current Goal

Updated: 2026-08-20

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Unknown Evidence Recovery v1

OpsKarta nearest goal: Remote Unknown Evidence Recovery v1: опираясь на завершённые Post-Segmentation Transcript Rebaseline v1 и Capture Continuity Loss Closure v1, заморозить 397.543570s / 547 слов explicit unknown из strict Coverage v3, разложить их по причинам отсутствия embedding, конфликтующим frame-speakers, слабому margin, overlap и boundary context, восстанавливать session-local speaker label только при независимом воспроизводимом evidence и без ослабления production abstention gates, сохранить точные words/roles/timestamps/aggregate fallback, выпустить PROMOTE либо точный EVIDENCE_BOUND, обновить тесты, документацию и планы, затем закоммитить и отправить результат в origin/main.

## Why Now

Capture Continuity Loss Closure v1 завершён `EVIDENCE_BOUND`. Удалены фиксированная `500ms` пауза
и повторный `stopCapture`; новый controlled restart сократил разрыв до `0.468729s`, программный
простой до `2.362ms`. Остаток принадлежит ScreenCaptureKit/start delivery, точно записывается как
`captured_audio=false` и блокирует terminal completeness. Обычный `600.434s` soak дал zero gaps.

Fresh rebaseline снова byte-exact на той же шестёрке: strict unknown составляет `397.543570s` и
547 слов. Это крупнейший следующий измеренный остаток на пути к надёжной атрибутированной
транскрибации.

## Objective

Понять, какую часть explicit unknown можно доказательно вернуть существующим участникам сессии.
Неизвестная речь остаётся корректным результатом, если evidence слабое или конфликтующее.

## Required Work

1. Заморозить шесть rebaseline-сессий, Coverage v3 inputs, unknown words/intervals и SHA-256 всех
   используемых speaker artifacts.
2. Для каждого unknown слова сохранить одну причину верхнего уровня и полную диагностическую
   provenance: embedding availability, similarity/margin, frame conflicts, overlap, boundary и
   соседний однородный контекст.
3. Проверить recovery-кандидаты независимым локальным speaker evidence, не переиспользуя тот же
   порог как собственное подтверждение.
4. Разрешать label только при согласии evidence и сохранении speaker purity; mixed/weak/short rows
   остаются `remote_speaker_unknown`.
5. Материализовать отдельный shadow profile. Coverage v3 и aggregate transcript остаются точным
   fallback и не переписываются до corpus-wide promotion.
6. Сравнить words, roles, timestamps, order, speaker topology, unknown seconds, false identity,
   review burden и exact fallback на всей замороженной шестёрке.
7. Выпустить `PROMOTE_REMOTE_UNKNOWN_RECOVERY` либо воспроизводимый `EVIDENCE_BOUND` с точным
   безопасным потолком.
8. Обновить README, contracts, runbook, roadmap и OpsKarta; выполнить полный набор проверок,
   commit и push.

## Acceptance Gates

- frozen capture, ASR, Echo Guard, Coverage v3, selected transcripts и raw CAF неизменны;
- все 547 unknown слов имеют стабильную cause/provenance;
- ни один label не назначен только соседством или одним similarity score;
- direct controls и known-speaker precision не регрессируют;
- words, roles, timestamps, chronology и aggregate fallback сохраняются точно;
- false identity не растёт; unsupported speech остаётся explicit unknown;
- повторный запуск детерминирован и corpus report byte-exact;
- результат документирован, закоммичен и находится в `origin/main`.

## Out Of Scope

- изменение capture, Echo Guard, основного ASR и restart policy;
- cross-session identity или имена людей без review;
- принудительное назначение всех слов известным speakers;
- local mic multi-speaker diarization, cloud inference, summaries и UI.

## First Commands

```bash
murmurmark corpus post-segmentation-rebaseline all --verify-existing
jq '.dimensions.explicit_unknown.causes, .summary.unknown_remote_words_coverage_v3' \
  sessions/_reports/post-segmentation-transcript-rebaseline-v1/post_segmentation_rebaseline_report.json
scripts/check-remote-speaker-coverage-v3.py
scripts/check.sh
```
