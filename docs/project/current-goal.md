# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Direct Truth Seed v1

OpsKarta nearest goal: Remote Speaker Direct Truth Seed v1: сохранить Coverage v3, selected
transcripts, raw CAF, primary ASR, Echo Guard, ECAPA shadow и все закрытые interval/enrollment
experiments неизменными; SHA-256 заморозить 278-item blind residual pack и
contrastive_reliability_weighted_centroid_v1 comparison; до открытия speaker labels выбрать
небольшой стратифицированный review seed, включающий все 11 newly accepted, 5 removed control
acceptances, stable controls, abstentions и доступные open-set/mixed cases минимум из трёх групповых
сессий; материализовать speaker-bounded local clips и blind review UI без model suggestion;
принимать только явный session-local anonymous speaker, unknown, mixed или unusable; проверить
повторным blinded subset consistency, exact word/timestamp conservation и provenance; выпустить
`DIRECT_TRUTH_SEED_READY`, `REFERENCE_INSUFFICIENT` либо `EVIDENCE_BOUND` без production
promotion; добавить CLI, fail-closed fixture/replay tests, portable aggregate report и Transcript
Perfection source; обновить документацию и планирование, пройти проверки, закоммитить и отправить
изменения.

## Why Now

Session-Local Remote Speaker Enrollment Hardening v1 завершён
`DO_NOT_ADVANCE_ENROLLMENT_HARDENING`. Единственный заранее объявленный centroid candidate добавил
11 items / `44.694004s`, сохранил structural precision `1.0`, поднял coarse independent precision
`0.878788 -> 0.894737` и не создал новых измеренных reference errors. Одновременно он потерял пять
control acceptances и восстановил только 4/83 items целевой очереди, то есть `4.82%` при gate `5%`.

Этот результат показывает, что enrollment влияет на решения, но существующая machine reference не
может доказать, какие изменения истинны. Новое взвешивание тех же 28 exemplars теперь запрещено.

## Objective

Получить минимальный прямой real-session speaker reference, который различает реальный прогресс,
регрессию и безопасную abstention. Seed должен быть выбран до чтения ответов и оставаться малым,
слепым, session-local и воспроизводимым.

## Required Work

1. Заморозить residual pack, ECAPA control, interval и enrollment comparison со всеми SHA-256.
2. До labels объявить точную стратификацию, размер seed и правила повторной проверки.
3. Включить все 16 изменённых candidate/control cases и сбалансированные unchanged/abstained rows.
4. Не показывать reviewer-у candidate outcome, score, speaker name или предыдущую machine reference.
5. Материализовать точные и speaker-bounded remote clips с явными word IDs и без speech text в
   public artifacts.
6. Принимать только `remote_speaker_NN`, `unknown_speaker`, `mixed`, `unusable` или `skip`.
7. Повторить вслепую заранее выбранную часть seed и измерить consistency до публикации результата.
8. Заморозить answer hashes, provenance и coverage; отсутствие ответов оставить
   `REFERENCE_INSUFFICIENT`.
9. Не переоценивать backend и не менять transcript в этой цели.
10. Добавить CLI, тесты, отчёт, Transcript Perfection source, документацию, commit и push.

## Acceptance Gates

- selection не читает candidate correctness, model reference или будущие answers;
- все 11 newly accepted и 5 removed control acceptances присутствуют ровно один раз;
- seed охватывает не менее трёх group sessions, несколько anonymous speakers и abstentions;
- direct labels покрывают заранее заданный минимум items, words и seconds;
- повторный blind subset достигает заранее заданной consistency;
- mixed/open-set/unknown доступны и не принуждаются к известному speaker ID;
- words, timestamps, selected transcripts, raw CAF и production guards неизменны;
- public artifacts не содержат speech text, имена, absolute paths или reviewer identity;
- missing clips, answers или provenance дают fail-closed outcome;
- replay детерминирован.

## Terminal Outcomes

- `DIRECT_TRUTH_SEED_READY`: достаточный слепой seed заморожен и может оценивать следующий backend.
- `REFERENCE_INSUFFICIENT`: механизм и pack валидны, но прямых labels пока недостаточно.
- `EVIDENCE_BOUND`: scope, audio, conservation, privacy или replay нельзя доказать.

## Safety Boundary

- identity остаётся session-local и anonymous;
- human names, cross-session voice linking и machine suggestions в blind review запрещены;
- capture, Echo Guard, primary ASR, selected transcript, export и live path не меняются;
- seed измеряет кандидатов, но сам ничего не продвигает в production.

## Previous Goal Result

Session-Local Remote Speaker Enrollment Hardening v1 завершён
`DO_NOT_ADVANCE_ENROLLMENT_HARDENING`. Candidate изменил 10/14 centroids, добавил 11 items /
`44.694004s`, удалил пять control acceptances и безопасно не прошёл promotion gates. Все 278 items,
851 words и item embeddings сохранены; replay byte-identical. Transcript Perfection проверяет
23/23 frozen sources.

## After This Goal

1. `DIRECT_TRUTH_SEED_READY` разблокирует disjoint qualification нового identity backend.
2. `REFERENCE_INSUFFICIENT` требует только недостающие labels, без смены алгоритма.
3. `EVIDENCE_BOUND` чинит acquisition/provenance до новых speaker experiments.
4. Production меняется только отдельной corpus-wide promotion-целью.
