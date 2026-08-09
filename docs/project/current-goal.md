# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Session-Local Remote Speaker Re-Clustering Feasibility v1

OpsKarta nearest goal: Session-Local Remote Speaker Re-Clustering Feasibility v1: сохранить Coverage
v3, selected transcripts, raw CAF, primary ASR, Echo Guard и все frozen reports неизменными;
построить внутри каждой сессии label-independent clustering remote speech windows отдельно в ECAPA
и WavLM; заморозить unlabeled windows, embeddings, cluster count и consensus до чтения Coverage
labels и direct truth; после freeze измерить cluster stability, model agreement, mapping ambiguity
и direct-truth outcomes; выпустить RECLUSTERING_ROUTE_READY, LABEL_MAPPING_BOUND,
EMBEDDING_GEOMETRY_BOUND либо EVIDENCE_BOUND без production promotion; добавить deterministic
evaluator, tests и corpus report, обновить документацию и планирование, пройти проверки,
закоммитить и отправить изменения.

## Why Now

Homogeneous Enrollment Mining v1 завершён `KEEP_EXISTING_ENROLLMENT`. Он нашёл 39 чистых окон и
квалифицировал 9/14 профилей, но сохранил 0/3 подтверждённых gains, потерял три верных control
identity и добавил четыре ложных. Ещё один label-conditioned enrollment повторит ту же скрытую
предпосылку: что Coverage v3 уже правильно сопоставил каждый turn с профилем.

## Objective

Проверить эту предпосылку отдельно. Сначала кластеризовать remote-речь без speaker IDs, затем
заморозить результат и только после этого сравнить кластеры с Coverage labels и прямой разметкой.
Эксперимент должен локализовать предел: сами embedding не разделяют голоса или разделяют, но
существующее сопоставление cluster -> anonymous profile загрязнено.

## Required Work

1. Проверить frozen hashes 27 Transcript Perfection sources и всех production guards.
2. Построить обезличенный inventory remote speech windows без текста и speaker labels.
3. Зафиксировать session-local cluster count, sampling и clustering rule до оценки labels.
4. Получить независимые ECAPA и WavLM co-assignment matrices.
5. Заморозить windows, embeddings и clusters до чтения Coverage labels и direct truth.
6. Измерить устойчивость кластеров, согласие моделей и возможное дробление/слияние голосов.
7. После freeze выровнять clusters с anonymous profiles только для оценки, не для production.
8. Измерить 33 direct-truth items и три подтверждённых gains без threshold search.
9. Сохранить exact words/timestamps, 68 Coverage v3 accepts и 355 production guards.
10. Обновить Transcript Perfection, документы, tests, commit и push.

## Acceptance Gates

- unlabeled pack не содержит target text, speaker IDs, names или cross-session voices;
- каждый model cluster воспроизводим и session-local;
- ECAPA/WavLM agreement измеряется до просмотра labels;
- cluster count и algorithm не подбираются по direct truth;
- mapping ambiguity остаётся явной и не превращается в forced identity;
- candidate pack заморожен до Coverage-label и development-truth evaluation;
- replay детерминирован, public outputs не раскрывают private session data;
- production promotion, selected transcript mutation и disjoint truth запрещены.

## Terminal Outcomes

- `RECLUSTERING_ROUTE_READY`: независимые кластеры устойчивы и дают безопасный следующий путь.
- `LABEL_MAPPING_BOUND`: кластеры устойчивы, но cluster-to-profile mapping нельзя доказать.
- `EMBEDDING_GEOMETRY_BOUND`: ECAPA/WavLM не дают согласованной структуры голосов.
- `EVIDENCE_BOUND`: модели, provenance, интервалы или replay нельзя проверить.

## Previous Goal Result

Session-Local Homogeneous Remote Speaker Enrollment Mining v1 завершён
`KEEP_EXISTING_ENROLLMENT`: 9/14 qualified profiles, 39 selected windows, 0/3 preserved gains,
5 unsafe accepts, 4 new false identities и 3 lost correct controls. Candidate pack был заморожен
до truth, replay прошёл, production остался Coverage v3.

## After This Goal

1. `RECLUSTERING_ROUTE_READY` открывает отдельный monotonic re-clustered shadow candidate.
2. `LABEL_MAPPING_BOUND` требует нового независимого mapping evidence, а не новых thresholds.
3. `EMBEDDING_GEOMETRY_BOUND` закрывает текущие ECAPA/WavLM и оставляет residual явным `unknown`.
4. `EVIDENCE_BOUND` чинит только provenance, model availability или evidence acquisition.
