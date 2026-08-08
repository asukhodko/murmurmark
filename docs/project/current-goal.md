# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Shadow Error Decomposition v1

OpsKarta nearest goal: Remote Speaker Shadow Error Decomposition v1: сохранить Coverage v3,
frozen ECAPA shadow, selected transcripts, raw CAF, primary ASR и Echo Guard неизменными; SHA-256
заморозить 278 residual items, 68 accepted ECAPA proposals, 210 abstentions, 28 enrollment
exemplars, truth grades и independent reference; для каждого item воспроизводимо оценить interval
purity, duration/silence, speech support, enrollment leave-one-out stability, boundary/mixed-speech
risk, score/margin и reference granularity; классифицировать основной и вторичный предел
доказательств без human-name inference, cross-session linking или подстройки frozen thresholds;
объяснить все 4 independent-reference wrong words и оба embedding failures либо явно отметить
insufficient truth; выпустить ровно один terminal outcome `ADVANCE_INTERVAL_PURIFICATION`,
`ADVANCE_ENROLLMENT_HARDENING`, `ADVANCE_REFERENCE_ACQUISITION`, `ADVANCE_IDENTITY_BACKEND` либо
`EVIDENCE_BOUND` только при predeclared concentration и no-regression gates; не применять shadow
labels и не менять production; добавить CLI, fail-closed tests, portable report и Transcript
Perfection source, обновить документацию и планирование, закоммитить и отправить изменения.

## Why Now

ECAPA прошёл one-shot synthetic hard-v4, но не прошёл frozen real-session shadow. Он восстановил
211.099681 секунды, однако только 156/851 слов (`0.183314`) при gate `0.20`. На доступном независимом
машинном reference precision составил `0.878788` вместо `0.99`. Два полностью немых клипа корректно
дали fail-open `unknown`.

Слепая замена модели теперь не обоснована. Эти числа могут объясняться не только voice identity:
короткие или смешанные интервалы, загрязнённый enrollment, speaker change внутри ASR-реплики и более
грубая гранулярность reference способны дать тот же симптом.

## Objective

Разложить реальный ECAPA shadow failure на измеримые причины и выбрать максимум одно следующее
инженерное направление. Если имеющихся данных недостаточно для такого выбора, результатом должен
быть честный `EVIDENCE_BOUND`, а не новая эвристика.

## Required Work

1. Заморозить завершённый shadow report, item/word decisions, embeddings, enrollment и reference.
2. Проверить точную связь всех 278 items с Coverage v3 words и исходными bounded clips.
3. Измерить duration, silence/RMS, speech support, score, margin и per-session distributions.
4. Проверить leave-one-out стабильность 28 enrollment exemplars и расстояния между centroids.
5. Выделить boundary/mixed intervals и конфликты word-level shadow с utterance-level reference.
6. Дать каждому item primary cause, secondary causes, confidence и evidence provenance.
7. Объяснить четыре reference-mismatch words и два embedding failures без прослушивания человеком.
8. Выпустить один terminal outcome по заранее зафиксированным concentration gates.
9. Добавить CLI, фикстуры, replay, portable manifest и Transcript Perfection source.
10. Синхронизировать документацию и планирование, пройти все проверки, commit и push.

## Acceptance Gates

- все 278 items и 851 words учтены ровно один раз;
- все 68 accepted proposals и 210 abstentions имеют стабильную классификацию;
- model, embeddings, enrollment, clips, reference и production guards SHA-256 frozen;
- четыре independent-reference wrong words и два silent failures имеют явное объяснение либо
  `insufficient_truth`;
- следующий axis выбирается только при заранее заданной материальной концентрации причины;
- threshold tuning по результату, применение shadow labels и production mutation запрещены;
- повторный запуск byte-identical;
- public artifacts не содержат speech text, имена, absolute paths или private embeddings.

## Safety Boundary

- Coverage v3 остаётся authoritative;
- ECAPA decisions остаются private shadow evidence;
- reference является independent machine evidence, а не human truth;
- имена и межсессионная identity не выводятся;
- capture, Echo Guard, основной ASR, selected transcript и export не меняются;
- облачные модели и ручное прослушивание не требуются.

## Previous Goal Result

ECAPA Remote Speaker Shadow Qualification v1 завершён `DO_NOT_PROMOTE_REAL_IDENTITY`. Заморозка
предшествовала inference; 304/306 embeddings рассчитаны, два silent clips дали fail-open. Word и
timestamp conservation, chronology, selected transcripts, Coverage v3 и production guards
сохранены. Replay byte-identical. Transcript Perfection проверяет 20/20 sources.

## Current Pipeline Validation Checkpoint

Перед продолжением этой цели завершён Three-Session Current Pipeline Quality Debug v1. На
анонимизированном срезе group/1x1/noisy-headset подтверждены неизменные raw CAF, нулевые capture
gaps, authoritative v2 byte replay, fail-open Echo selection и exact word conservation у
speaker-resolved view. Исправлен общий поздний отказ `--force-asr --reuse-asr-cache`: legacy или
повреждённый cache теперь выявляется до timeline repair и безопасно перестраивается. Production
speaker attribution и текущая цель не менялись. Подробности:
`docs/testing/2026-08-08-three-session-current-pipeline-quality-debug-v1.md`.

## After This Goal

1. Материальный interval-purity предел открывает bounded interval purification.
2. Материальный enrollment предел открывает enrollment hardening.
3. Недостаток truth открывает только acquisition/qualification reference, не tuning.
4. Чистый identity предел допускает один новый backend comparison.
5. Без доминирующей причины track фиксируется `EVIDENCE_BOUND`, Coverage v3 остаётся пределом.
