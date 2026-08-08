# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Bounded Remote Speaker Interval Purification v1

OpsKarta nearest goal: Bounded Remote Speaker Interval Purification v1: сохранить Coverage v3, ECAPA shadow, enrollment centroids, selected transcripts, raw CAF, primary ASR и Echo Guard неизменными; SHA-256 заморозить decomposition inputs и 93 interval-axis failure items / 201.273504s; до открытия результата объявить ровно один deterministic speaker-bounded interval candidate, который удаляет padding/соседнюю и смешанную речь без изменения word IDs, text или timestamps; пересчитать только item embeddings тем же pinned ECAPA, thresholds 0.50/0.30 и неизменным enrollment; сравнить с frozen control по acceptance, independent/structural evidence, silent fail-open, chronology и per-item provenance; выпустить `ADVANCE_PURIFIED_SHADOW_CANDIDATE`, `DO_NOT_ADVANCE_INTERVAL_PURIFICATION` либо `EVIDENCE_BOUND` без production promotion; добавить CLI, fail-closed fixture/replay tests, portable report и Transcript Perfection source; обновить документацию и планирование, пройти проверки, закоммитить и отправить изменения.

## Why Now

Remote Speaker Shadow Error Decomposition v1 учёл все 278 items и выбрал
`ADVANCE_INTERVAL_PURIFICATION`. Из 214 failure items интервал объясняет 93 (`43.4579%`) и
201.273504 из 392.415726 секунд (`51.2909%`). Material score `0.434579` опережает enrollment на
`0.128982`, что выше заранее объявленного dominance gate `0.10`.

Это не доказывает правильность speaker identity: для `68.7050%` items нет contextual truth. Оно
доказывает более узкую вещь: следующий контролируемый эксперимент должен сначала улучшить аудио,
которое получает уже квалифицированный ECAPA backend, а не менять модель или пороги.

## Objective

Проверить одну фиксированную speaker-bounded обрезку real-session residual clips. Candidate должен
удалять соседнюю/смешанную речь и лишний padding, сохранять минимально достаточное речевое ядро и
fail-open на коротких или немых интервалах. Изменяется только item audio и его embedding.

## Required Work

1. Заморозить decomposition report, 278 control items, 93 interval-axis failures, clips и hashes.
2. До оценки описать единственный interval candidate и его точные sample-level правила.
3. Не менять 28 enrollment exemplars, centroids, ECAPA model/revision и thresholds `0.50/0.30`.
4. Материализовать candidate clips отдельно; control clips и session artifacts не перезаписывать.
5. Пересчитать item embeddings и воспроизвести control/candidate решения с общей provenance.
6. Сравнить acceptance, recovered words/seconds, structural 1x1 и independent machine evidence.
7. Проверить boundary/chronology, silent fail-open, exact word/timestamp conservation и privacy.
8. Выпустить один terminal outcome без production promotion.
9. Добавить CLI, синтетическую фикстуру, tamper test, byte-identical replay и public report.
10. Обновить Transcript Perfection, документацию и планы; пройти проверки, commit и push.

## Acceptance Gates

- все 278 items и 851 words сохранены, primary analysis отдельно показывает 93 interval failures;
- один candidate объявлен до результата, parameter sweep и post-hoc threshold tuning запрещены;
- control decisions и scores воспроизводятся byte-identical;
- original clips, enrollment, selected transcripts, Coverage v3 и raw CAF неизменны;
- candidate не ухудшает known structural precision, available independent precision, chronology,
  silent fail-open или existing-label conservation;
- improvement считается только на frozen interval scope и не маскирует enrollment/identity limits;
- отсутствующий/нечитаемый audio или embedding остаётся `unknown`;
- public artifacts не содержат speech text, имена, absolute paths или embeddings;
- repeated evaluation и replay детерминированы.

## Terminal Outcomes

- `ADVANCE_PURIFIED_SHADOW_CANDIDATE`: candidate даёт материальное техническое улучшение без
  evidence regression; он всё ещё остаётся shadow и требует отдельной qualification.
- `DO_NOT_ADVANCE_INTERVAL_PURIFICATION`: fixed candidate не улучшает interval scope безопасно;
  ветку не перенастраивать на этом же evidence.
- `EVIDENCE_BOUND`: входы, reference или conservation не позволяют надёжно решить эксперимент.

## Safety Boundary

- Coverage v3 и ordinary speaker-resolved transcript остаются authoritative;
- human names и cross-session voice identity запрещены;
- coarse independent machine reference не превращается в human truth;
- capture, Echo Guard, primary ASR, selected transcript, export и live path не меняются;
- новый candidate не применяется к production даже при положительном результате этой цели.

## Previous Goal Result

Remote Speaker Shadow Error Decomposition v1 завершён `ADVANCE_INTERVAL_PURIFICATION`. Все 214
failure items объяснены, четыре mismatch words разделены на три unmapped coarse-reference cases и
один identity conflict, два embedding failures подтверждены как цифровая тишина. Frozen thresholds,
68 accepted proposals, 210 abstentions и production guards не менялись; replay byte-identical.
Transcript Perfection теперь проверяет 21/21 frozen sources.

## After This Goal

1. Положительный shadow candidate проходит отдельную real-session/reference qualification.
2. Отрицательный результат возвращает маршрут к enrollment hardening, следующему измеренному axis.
3. `EVIDENCE_BOUND` открывает только acquisition прямой truth, а не ослабление thresholds.
4. Новый identity backend допускается лишь после закрытия interval и enrollment evidence.
