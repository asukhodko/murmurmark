# Current Goal

Updated: 2026-08-19

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Boundary and Minority-Voice Segmentation v1

OpsKarta nearest goal: Remote Speaker Boundary and Minority-Voice Segmentation v1: сохранив Coverage v3, selected transcript artifacts и диагностический Cluster Purity Reference v1 неизменными, построить session-local boundary detector для разделения смешанных remote-интервалов и восстановления редких голосов; заморозить candidate до нового disjoint terminal set, запрещать forced identity и выпускать только PROMOTE_SEGMENTATION, KEEP_COVERAGE_V3 либо EVIDENCE_BOUND с exact replay, privacy-safe отчётом, тестами, документацией, коммитом и push.

## Why Now

Residual Transcript Integrity Hardening v1 has already closed the intervening mechanical text debt:
10 of 19 duplicate/repetition candidates were safely repaired across three sessions, nine remained
explicit review, and the promoted profile preserves roles, timestamps, raw capture and speaker
evidence. Boundary work can therefore use a cleaner aggregate transcript without absorbing an
unrelated text-repair problem.

Cluster Purity Reference v1 сопоставил приватную машинную расшифровку групповой встречи с текущим
speaker-resolved transcript. При `92.8157%` lexical alignment референс содержит 10 remote-голосов,
а MurmurMark публикует 4 кластера. Weighted purity составляет `89.8106%`, девять reference speakers
попадают в collision clusters, а recall шести редких голосов равен `0`.

Это диагностический, а не identity reference. Однако расхождение топологии слишком велико, чтобы
следующий шаг снова сводился к выбору embedding-модели или порога. Сначала нужны более точные
границы речи и явное сохранение коротких/редких голосов.

Fresh operational evidence agrees with that ranking. A complete recent group session passed durable
capture and review materialization, but still left about 15% of remote speech explicit unknown and
dense-overlap words damaged. The same run exposed stale speaker-selection, cleanup-metric and
deferred-status refresh defects; those lifecycle defects are fixed and regression-covered, so they
do not justify diverting the current speaker-boundary goal.

## Objective

Построить локальный детерминированный candidate, который разбивает remote speech на speaker-bounded
интервалы до identity assignment, не теряет слова и оставляет сомнительные интервалы `unknown`.
Проверить его один раз на новом disjoint terminal set и либо продвинуть только segmentation layer,
либо оставить Coverage v3 неизменным.

## Required Work

1. Заморозить текущие Coverage v3 outputs, selected rich/aggregate transcripts, policy и corpus
   hashes до разработки candidate.
2. Построить boundary evidence из VAD, пауз, word timestamps, embedding change points и локальной
   стабильности соседних окон; identity labels при поиске границ не использовать.
3. Отдельно учитывать короткие minority turns и не сливать их с доминирующим голосом только из-за
   недостатка enrollment.
4. Сохранить точный текст, порядок и временные границы utterance; candidate меняет только
   speaker-turn segmentation и anonymous assignment evidence.
5. Заморозить candidate и пороги на development evidence до открытия нового disjoint terminal set.
6. Считать boundary precision/recall, speaker-count agreement, B-cubed F1, pairwise precision,
   minority-speaker recall, explicit unknown и word conservation.
7. Проверить session-shift stability, open-set safety, exact replay и aggregate fallback.
8. Выпустить ровно один terminal outcome и синхронизировать CLI status, документы, roadmap,
   OpsKarta, тесты, commit и push.

## Acceptance Gates

- Coverage v3, его пороги, основной ASR, Echo Guard, raw CAF и текущие selected artifacts остались
  byte-identical;
- boundary detector не использует truth identity при вычислении признаков;
- 100% входных слов и их порядок сохранены;
- candidate не присваивает identity при слабом, смешанном или open-set evidence;
- minority-speaker recall измерен отдельно и не маскируется общей weighted accuracy;
- новый terminal set не пересекается с material, использованным для выбора candidate;
- replay byte-exact, missing model/evidence fail open to current Coverage v3;
- public artifacts не содержат имён, текста встреч, session IDs или absolute paths.

## Terminal Outcomes

- `PROMOTE_SEGMENTATION`: новый слой проходит все safety, conservation, boundary, minority and
  no-regression gates и может стать входом существующей anonymous identity attribution.
- `KEEP_COVERAGE_V3`: candidate измерен, но не даёт безопасного материального улучшения.
- `EVIDENCE_BOUND`: доступного disjoint reference недостаточно для честного вывода.

## Out Of Scope

- имена и cross-session identity;
- forced attribution неизвестных голосов;
- capture, Echo Guard, основной ASR и pre-ASR separation;
- cloud inference, UI, notes, summaries and export automation.

## First Commands

```bash
murmurmark corpus remote-cluster-purity-v1 status
murmurmark corpus remote-cluster-purity-v1 replay
murmurmark transcript sessions/<session-id> --aggregate --path-only
scripts/check.sh
```
