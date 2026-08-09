# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Stronger Local Remote Speaker Representation Qualification v1

OpsKarta nearest goal: Stronger Local Remote Speaker Representation Qualification v1: сохранить
Coverage v3, selected transcripts, raw CAF, primary ASR, Echo Guard и 28 frozen Transcript
Perfection sources неизменными; выбрать и зафиксировать минимум один существенно иной локальный
diarization или speaker-representation backend, который не сводится к новым порогам ECAPA/WavLM;
до чтения direct truth заморозить model provenance, license, offline runtime, audio-only segmentation,
embeddings, clustering rule и candidate pack; на том же шестисессионном корпусе и 33 direct-truth
items измерить geometry stability, open-set safety, mapping ambiguity и сохранение трёх подтверждённых
gains; выпустить STRONGER_REPRESENTATION_READY, KEEP_EXPLICIT_UNKNOWN либо EVIDENCE_BOUND без
production promotion; добавить deterministic evaluator, tests и corpus report, обновить документацию
и планирование, пройти проверки, закоммитить и отправить изменения.

## Why Now

Label-conditioned подходы уже исчерпаны. Последний label-independent эксперимент тоже дал строгий
ответ: ECAPA и WavLM разошлись до ARI `0.090170`, стабильность падала до `0.465715`, consensus
дробился до `1.8x`, а direct truth сохранил `0/3` подтверждённых gains. Перестановка labels и новые
thresholds не исправляют отсутствующую общую speaker geometry.

## Objective

Проверить один действительно новый локальный путь представления remote-голоса. До установки тяжёлой
production-интеграции надо доказать, что новый backend на тех же frozen real-session данных устойчивее
текущих ECAPA/WavLM и не покупает recall ложными identity assignments.

## Required Work

1. Проверить 28 frozen Transcript Perfection sources и 355 production guards.
2. Составить короткий список локальных backend-кандидатов с лицензией, размером, runtime и offline mode.
3. Выбрать минимум один кандидат, который обучен иначе и не является обёрткой над текущими моделями.
4. Зафиксировать SHA-256 модели, runtime, preprocessing, segmentation, clustering и thresholds до truth.
5. Использовать тот же шестисессионный корпус, 347 blind windows и 33 direct-truth items либо заранее
   объяснить и заморозить audio-only segmentation replacement.
6. Измерить stability, agreement с независимым control, cluster collapse/fragmentation и open-set safety.
7. Проверить три confirmed gains, восемь control unsafe accepts и отсутствие новых false identities.
8. Не менять Coverage v3, selected transcript, raw CAF, primary ASR, Echo Guard и обычный CLI output.
9. Добавить replay, fixture tests, public aggregate report и private provenance.
10. Обновить Transcript Perfection, README, contracts, runbook, roadmap и OpsKarta; commit и push.

## Acceptance Gates

- backend работает полностью локально и воспроизводимо;
- model/license/runtime provenance заморожены до direct truth;
- candidate materially independent от текущих ECAPA/WavLM;
- truth не участвует в выборе K, segmentation, thresholds или model checkpoint;
- geometry не схлопывает редких speakers и не дробит доминирующего speaker;
- direct truth не добавляет false identity и не теряет correct controls;
- отсутствующая модель или конфликт evidence дают fail-open explicit unknown;
- production promotion запрещён в этой цели.

## Terminal Outcomes

- `STRONGER_REPRESENTATION_READY`: новый backend проходит frozen geometry и direct-truth gates.
- `KEEP_EXPLICIT_UNKNOWN`: backend доступен, но не превосходит безопасный Coverage v3 residual.
- `EVIDENCE_BOUND`: модель, лицензия, offline runtime или provenance не позволяют честную проверку.

## Previous Goal Result

Session-Local Remote Speaker Re-Clustering Feasibility v1 завершён
`EMBEDDING_GEOMETRY_BOUND`: 347 blind windows были заморожены до labels; minimum ARI `0.090170`,
minimum NMI `0.231989`, minimum stability ARI `0.465715`, maximum fragmentation `1.8`, preserved
gains `0/3`, new false identities `4`, lost controls `3`. Coverage v3 и production не изменены.

## After This Goal

1. `STRONGER_REPRESENTATION_READY` открывает отдельный monotonic shadow candidate.
2. `KEEP_EXPLICIT_UNKNOWN` закрывает доступные локальные representation backends до появления новой модели.
3. `EVIDENCE_BOUND` чинит только model acquisition, license, offline runtime или provenance.
