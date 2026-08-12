# Current Goal

Updated: 2026-08-12

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Disjoint Remote Speaker Model Qualification v1

OpsKarta nearest goal: Disjoint Remote Speaker Model Qualification v1: сохранив Coverage v3 и все frozen inputs неизменными, выбрать ровно один существенно новый локальный класс speaker-модели по лицензии, доступности и совместимости с Apple Silicon; до открытия item-level truth v2 заморозить веса, SHA-256, preprocessing, segmentation, clustering, thresholds и candidate outputs; один раз оценить candidate на независимых 72 primary + 12 repeat Disjoint Truth v2 slots, прежних direct-truth controls и controlled corpus; измерить attributed precision/recall, B-cubed, abstention на unknown/mixed/unusable, speaker-count, boundaries, repeat determinism и conservation; запретить tuning после unseal и выпустить PROMOTE_SHADOW, KEEP_COVERAGE_V3 либо MODEL_UNAVAILABLE с byte-exact replay, privacy-safe отчётом, тестами, актуальными документами, коммитом и push.

## Why Now

Disjoint Truth v2 завершён `DIRECT_TRUTH_V2_READY`: размечены 72 primary и 12 hidden repeat slots,
repeat consistency равна `1.0`, а candidate/review pack воспроизводится побайтно. Теперь можно
проверить новый класс модели без повторной подгонки к прежним 33 direct-truth items.

## Objective

Провести одну честную квалификацию нового локального speaker backend на независимой real-session
truth. Candidate либо докажет право на отдельный shadow-профиль, либо закроет ещё одну ветвь без
изменения production.

## Current State

Truth v2 содержит 21 attributed, 28 unknown, 4 mixed и 19 unusable primary outcomes на шести
сессиях; attributed evidence представлено в четырёх сессиях. Все 12 скрытых повторов совпали.
Public report не содержит речь, имена, абсолютные пути или private labels. Coverage v3, selected
transcripts, raw CAF, ASR, Echo Guard и truth v1 не изменились.

Следующий candidate ещё не выбран. ECAPA, WavLM, WeSpeaker fixed-window и Community-1-equivalent
temporal AHC/VBx уже исчерпаны; повторная настройка этих же ветвей не считается новым классом.

## Required Work

1. Сделать короткий feasibility preflight доступных materially new local backends и выбрать один.
2. Записать лицензию, источник, версию, model SHA-256, runtime и hardware/resource contract.
3. Заморозить preprocessing, segmentation, embeddings, clustering, thresholds и candidate outputs
   до чтения item-level truth v2 evaluator-ом.
4. Выполнить ровно один unseal/evaluation на truth v2; после него не менять candidate.
5. Сопоставить результат с Coverage v3, v1 controls и controlled truth без потери слов/таймкодов.
6. Проверить unknown/mixed/unusable abstention, boundaries, speaker count и hidden repeats.
7. Выпустить privacy-safe aggregate report и byte-exact replay без речи, имён и private labels.
8. Добавить CLI, fixtures, guards, docs and planning; commit and push.

## Acceptance Gates

- candidate и evaluator fingerprints заморожены до unseal;
- truth v2 используется только для one-shot terminal evaluation, а не для tuning;
- attributed precision не хуже Coverage v3 control и нет новых unsafe accepts;
- unknown/mixed/unusable не получают принудительную identity;
- words, timestamps, roles, `Me`, boundaries и aggregate fallback сохраняются;
- replay byte-exact, public artifacts privacy-safe, все production guards проходят;
- production не меняется в этой цели: положительный исход разрешает только отдельный shadow.

## Terminal Outcomes

- `PROMOTE_SHADOW`: candidate проходит material и safety gates и допускается к отдельному shadow.
- `KEEP_COVERAGE_V3`: candidate доступен, но не даёт безопасного материального улучшения.
- `MODEL_UNAVAILABLE`: materially new candidate нельзя локально и воспроизводимо запустить; отчёт
  фиксирует точный blocker без ослабления ворот.

## Previous Goal Result

Remote Speaker Disjoint Truth Expansion v2 завершён `DIRECT_TRUTH_V2_READY`: 72 primary / 12 repeat,
21 attributed primary на четырёх сессиях, repeat consistency `1.0`, zero v1 overlap и byte-exact
replay. Production остался Coverage v3.

## After This Goal

1. `PROMOTE_SHADOW` откроет отдельную corpus-wide shadow qualification без автоматического выбора.
2. `KEEP_COVERAGE_V3` или `MODEL_UNAVAILABLE` зафиксирует текущий локальный предел и направит работу
   к новым controlled recordings либо принципиально другому representation class.
