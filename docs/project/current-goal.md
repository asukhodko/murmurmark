# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Blind Review Completion v1

OpsKarta nearest goal: Remote Speaker Blind Review Completion v1: сохранить Coverage v3, selected
transcripts, raw CAF, primary ASR, Echo Guard, ECAPA shadow, закрытые interval/enrollment
experiments и frozen Direct Truth Seed v1 неизменными; SHA-256 проверить 278-item source pack,
33-item / 116-word / 90.100820s seed, 8 hidden repeats, 41 opaque review slots и 355 inherited
production guards; проводить review только через `murmurmark corpus remote-truth-seed-v1
next|grade`, не открывая seed selection, slot map, enrollment comparison, model references или
suggestions; для каждого слота принять только session-local anonymous speaker, `unknown_speaker`,
`mixed` или `unusable`; закрыть все 33 primary и 8 repeat slots, получить consistency не ниже
0.875, проверить exact word/timestamp/clip conservation и deterministic replay; выпустить
`DIRECT_TRUTH_SEED_READY`, `REFERENCE_INSUFFICIENT` либо `EVIDENCE_BOUND` без production
promotion; обновить Transcript Perfection, документацию и планирование, пройти проверки,
закоммитить и отправить изменения.

## Why Now

Session-Local Remote Speaker Enrollment Hardening v1 завершён
`DO_NOT_ADVANCE_ENROLLMENT_HARDENING`. Новый Direct Truth Seed v1 затем заморозил ровно те случаи,
которые отличают полезное изменение от регрессии: все 11 новых принятий, 5 потерянных control
acceptances, стабильные controls, abstentions и специальные случаи.

Механизм готов и воспроизводим, но прямых ответов пока нет. Ещё один backend или threshold без этой
разметки снова будет измеряться через machine agreement и не сможет честно продвинуть production.

## Objective

Заполнить только замороженные 41 blind slots и получить минимальный прямой real-session speaker
reference с измеренной повторяемостью. Не расширять scope, не менять алгоритмы и не использовать
model suggestion.

## Required Work

1. Проверить policy, pack, report, replay и все frozen source hashes.
2. Получать следующий слот только командой `remote-truth-seed-v1 next`.
3. Слушать target clip и anonymous exemplars, не открывая sealed mapping или model results.
4. Записывать только один из показанных `remote_speaker_XX`, `unknown_speaker`, `mixed`, `unusable`.
5. Не пытаться связывать anonymous speaker IDs между сессиями или выводить человеческие имена.
6. Закрыть 33 primary и 8 скрытых repeat slots.
7. Проверить consistency, answer provenance, exact source conservation и replay.
8. Не переоценивать новый backend и не менять transcript в этой цели.
9. Обновить Transcript Perfection source, документацию, планы, commit и push.

## Acceptance Gates

- 33/33 primary и 8/8 repeat slots имеют `human_reviewed` outcome;
- все 16 changed cases покрыты прямым ответом;
- не менее 8 primary outcomes указывают на session-local anonymous speaker;
- repeat consistency не ниже `0.875`;
- blind queue не содержит suggestion, stratum, score, reference или speech text;
- source items, words, timestamps, clips и 355 inherited guards неизменны;
- public artifacts не содержат речь, session IDs, имена, absolute paths или reviewer identity;
- missing, stale или conflicting evidence даёт fail-closed outcome;
- replay детерминирован.

## Terminal Outcomes

- `DIRECT_TRUTH_SEED_READY`: seed достаточен для отдельной квалификации следующего identity backend.
- `REFERENCE_INSUFFICIENT`: механизм валиден, но часть blind slots не заполнена.
- `EVIDENCE_BOUND`: source, pack, privacy, conservation или replay нельзя доказать.

## Safety Boundary

- review ничего не исправляет и не продвигает в production;
- human names, cross-session voice linking и model suggestions запрещены;
- capture, Echo Guard, primary ASR, selected transcript, export и live path не меняются;
- новый backend открывается только отдельной целью после `DIRECT_TRUTH_SEED_READY`.

## Previous Goal Result

Remote Speaker Direct Truth Seed v1 завершён `REFERENCE_INSUFFICIENT`. Заморожены 33 items / 116
words / `90.100820s`, 8 hidden repeats и 41 blind slot по шести сессиям. Все 16 changed cases
включены; direct answers 0/41; replay byte-exact. Transcript Perfection проверяет 24/24 frozen
sources. Production остаётся Coverage v3.

## After This Goal

1. `DIRECT_TRUTH_SEED_READY` разблокирует disjoint qualification нового identity backend.
2. `REFERENCE_INSUFFICIENT` сохраняет ровно оставшиеся slots без смены алгоритма или scope.
3. `EVIDENCE_BOUND` чинит acquisition/provenance до новых speaker experiments.
4. Production меняется только отдельной corpus-wide promotion-целью.
