# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Direct-Truth Candidate Adjudication v1

OpsKarta nearest goal: Remote Speaker Direct-Truth Candidate Adjudication v1: сохранить Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard, ECAPA shadow, interval/enrollment results и frozen Direct Truth Seed v1 неизменными; SHA-256 проверить 278-item source pack, 33 primary / 8 repeat direct answers, 355 inherited guards и byte-exact replay; только после закрытого blind review открыть sealed item mapping и один раз оценить неизменённые Coverage v3/control и contrastive_reliability_weighted_centroid_v1 enrollment candidate против direct anonymous outcomes; считать `unknown_speaker`, `mixed` и `unusable` только fail-closed abstention evidence, не положительной identity truth; отдельно измерить 11 newly accepted, 5 removed controls, stable controls/abstentions, repeat mismatch и exemplar-purity limitation; выпустить `ADVANCE_DIRECT_TRUTH_IDENTITY`, `KEEP_COVERAGE_V3` либо `EVIDENCE_BOUND` без production promotion или threshold tuning; добавить deterministic evaluator, portable aggregate report и Transcript Perfection source; обновить документацию и планирование, пройти проверки, закоммитить и отправить изменения.

## Why Now

Blind Review Completion v1 закрыл все 33 primary и 8 repeat slots. Получено 8 прямых анонимных
speaker labels, 11 `unknown_speaker`, 4 `mixed` и 10 `unusable`; 7/8 повторов совпали. Это первый
bounded real-session reference, позволяющий сравнить Coverage v3 и enrollment candidate не через
согласие моделей.

Запас evidence невелик: consistency ровно `0.875`, а часть exemplars смешанная или тихая. Поэтому
следующий шаг обязан быть одноразовой оценкой уже замороженных вариантов, без настройки порогов.

## Objective

После завершённого blind review открыть sealed mapping только evaluator-у и измерить, какой из двух
неизменённых вариантов лучше согласуется с прямыми session-local labels. Не менять production и не
считать отсутствие положительной identity label ошибкой кандидата, если truth остаётся unknown.

## Required Work

1. Проверить hashes pack, answers, repeats, clips и 355 production guards.
2. Заморозить evaluator policy до чтения sealed mapping.
3. Сопоставить direct primary labels с Coverage v3/control и enrollment candidate.
4. Отдельно посчитать changed gains, removed controls, stable controls и abstentions.
5. Не использовать `unknown_speaker`, `mixed` и `unusable` как положительную identity truth.
6. Учесть один repeat mismatch и ограничения exemplar purity в confidence/result boundary.
7. Проверить exact words, timestamps, source clips и deterministic replay.
8. Не менять threshold, enrollment, transcript или production selection.
9. Добавить portable aggregate report в Transcript Perfection Corpus.
10. Обновить документацию, планирование, commit и push.

## Acceptance Gates

- 33 primary outcomes учтены ровно один раз;
- 8 repeats используются только для consistency, не удваивают метрики;
- все 16 changed cases и объявленные controls представлены отдельно;
- positive identity correctness считается только по 8 attributed primary labels;
- unknown/mixed/unusable остаются fail-closed и не принуждаются к speaker ID;
- direct-truth seed, Coverage v3, enrollment result и 355 guards неизменны;
- evaluator и replay детерминированы;
- public report не содержит речь, session IDs, имена, absolute paths или reviewer identity;
- production profile и selected transcript не меняются.

## Terminal Outcomes

- `ADVANCE_DIRECT_TRUTH_IDENTITY`: candidate даёт прямой безопасный выигрыш, достаточный для
  отдельной corpus-wide qualification.
- `KEEP_COVERAGE_V3`: control надёжнее либо выигрыш candidate недостаточен.
- `EVIDENCE_BOUND`: integrity, conservation или прямой evidence нельзя доказать.

## Previous Goal Result

Remote Speaker Blind Review Completion v1 завершён `DIRECT_TRUTH_SEED_READY`. Закрыты 33/33
primary и 8/8 repeat slots; consistency `7/8 = 0.875`. Primary outcomes: 8 anonymous speaker,
11 unknown, 4 mixed, 10 unusable. Все hashes, conservation gates, privacy gates, 355 inherited
production guards и byte-exact replay проходят. Production остаётся Coverage v3.

## After This Goal

1. `ADVANCE_DIRECT_TRUTH_IDENTITY` открывает отдельную qualification без автоматического promotion.
2. `KEEP_COVERAGE_V3` закрывает enrollment candidate и направляет работу на следующий measured axis.
3. `EVIDENCE_BOUND` чинит только provenance/acquisition, не алгоритм speaker attribution.
