# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## ECAPA Remote Speaker Shadow Qualification v1

OpsKarta nearest goal: ECAPA Remote Speaker Shadow Qualification v1: сохранить promoted lab
candidate, one-shot hard-v4, Coverage v3 и selected transcripts неизменными; встроить frozen ECAPA
backend только как fail-open shadow над существующими remote-speaker intervals реальных frozen
sessions; использовать только уже разрешённые reviewed speaker labels и anonymous within-session
evidence, не переносить synthetic voices или identity между сессиями; сравнить ECAPA с Coverage v3
по exact word conservation, attributed precision, known reviewed coverage, open-set/unknown safety,
boundary/order и runtime; публиковать shadow artifacts и corpus report с полной model/input
provenance; разрешить отдельное production promotion решение только при no-regression, нулевой
ложной уверенности на reviewed negatives и воспроизводимом улучшении real-session coverage, иначе
выпустить `DO_NOT_PROMOTE_REAL_IDENTITY`; добавить автоматические тесты и Transcript Perfection
source, обновить документацию и планирование, закоммитить и отправить изменения.

## Why Now

Stronger Remote Speaker Identity Backend Qualification v1 закрыл лабораторный вопрос. ECAPA
получил на новом one-shot hard-v4 B-cubed F1 `0.948042`, pairwise precision `1.0`, known-speaker
recall `0.947368`, ноль open-set false attribution и сохранил 154/154 words. Текущий WavLM control
на том же hard-v4 воздержался почти везде и получил known recall `0`.

Это достаточное основание проверить ECAPA на реальных встречах. Оно не доказывает production
качество: synthetic voices, clean enrollment и exact event boundaries проще реального звука,
кодеков, коротких реплик и overlap.

## Objective

Проверить, даёт ли frozen ECAPA backend воспроизводимое и безопасное улучшение anonymous
remote-speaker attribution на замороженных реальных сессиях. Весь этап работает в shadow и не
меняет выбранный transcript. Итогом должен быть отдельный аргументированный `PROMOTE`-кандидат для
production-решения либо `DO_NOT_PROMOTE_REAL_IDENTITY`.

## Required Work

1. Заморозить ECAPA model/runtime provenance, hard-v4 result и входные real-session profiles.
2. Выбрать только сессии с уже разрешёнными reviewed labels и явными negative/unknown примерами.
3. Построить session-local enrollment без human-name inference и межсессионного связывания голоса.
4. Запустить ECAPA над существующими remote intervals в отдельном fail-open shadow профиле.
5. Сравнить его с Coverage v3 по словам, точности, coverage, boundary/order, abstention и времени.
6. Сохранить каждое решение с model, input, interval, enrollment и score provenance.
7. Зафиксировать corpus-wide решение без изменения production и selected transcript.
8. Добавить CLI, тесты, portable manifest и новый Transcript Perfection source.

## Acceptance Gates

- every input transcript, interval, reviewed label, model and runtime is SHA-256 frozen;
- every selected word and timestamp is conserved exactly;
- no reviewed negative or unknown speaker receives a false confident attribution;
- reviewed attributed precision and chronology do not regress against Coverage v3;
- real-session reviewed coverage improves reproducibly by a predeclared material amount;
- missing model, weak enrollment or conflicting evidence yields `unknown` and exact fallback;
- no synthetic or cross-session identity enters real outputs;
- repeated runs are deterministic and production artifacts remain byte-exact.

## Safety Boundary

- ECAPA remains shadow-only throughout this goal;
- no human name is inferred from voice;
- no speaker identity is linked across sessions;
- reviewed labels are evidence, not training data for hidden production behavior;
- no cloud service, raw CAF mutation, primary ASR change or Echo Guard change;
- production promotion, if justified, is a separate explicit goal.

## Previous Goal Result

Stronger Remote Speaker Identity Backend Qualification v1 completed with
`PROMOTE_LAB_IDENTITY_CANDIDATE`. The disjoint hard-v4 was frozen before candidate selection and
opened once. ECAPA passed every fixed gate, replay is byte-identical, public artifacts are portable,
and Transcript Perfection verifies `19/19` frozen sources. Coverage v3 and all selected transcripts
remain unchanged.

## After This Goal

1. A passing shadow result opens a separate production integration and promotion goal.
2. A negative result records the synthetic-to-real gap and keeps Coverage v3 as the supported path.
3. Dedicated segmentation resumes only if real ECAPA evidence shows identity is no longer dominant.
