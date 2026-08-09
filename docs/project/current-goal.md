# Current Goal

Updated: 2026-08-09

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Temporal End-to-End Remote Diarization Qualification v1

OpsKarta nearest goal: Temporal End-to-End Remote Diarization Qualification v1: сохранить Coverage
v3, selected transcripts, raw CAF, primary ASR, Echo Guard и 29 frozen Transcript Perfection sources
неизменными; выбрать и зафиксировать минимум один полностью локальный temporal/end-to-end
diarization backend, который моделирует speaker activity и последовательность разговора, а не только
кластеризует независимые ECAPA/WavLM/WeSpeaker windows; до чтения Coverage labels и 33-item direct
truth заморозить model/license/runtime provenance, remote-audio hashes, segmentation, overlap policy,
speaker-count strategy и candidate diarization pack; на том же шестисессионном корпусе, включая
сессию с пятью remote profiles, измерить temporal stability, boundary conservation, overlap safety,
cluster-to-profile ambiguity, open-set errors и сохранение трёх подтверждённых gains; выпустить
TEMPORAL_DIARIZATION_READY, KEEP_EXPLICIT_UNKNOWN либо EVIDENCE_BOUND без production promotion;
добавить deterministic evaluator, replay, tests и corpus report; актуализировать документацию,
Transcript Perfection и планирование; пройти проверки, закоммитить изменения и отправить их в
origin/main.

## Why Now

Три класса независимых fixed-window embeddings уже ограничены реальными данными. ECAPA/WavLM
re-clustering дал minimum agreement ARI `0.090170` и сохранил `0/3` gains. WeSpeaker ResNet34-LM
заметно улучшил blind geometry на трёх сессиях и сохранил `3/3` gains, но на четвёртой stability ARI
упал до `0.442394`; post-freeze mapping оставил шесть ambiguous clusters и добавил 12 новых false
identities. Новая настройка similarity thresholds повторит уже закрытый маршрут.

## Objective

Проверить, устраняет ли модель временной структуры разговора главный предел независимых embeddings:
нестабильные редкие speakers, короткие смены, overlap и неоднозначное cluster-to-profile mapping.
Цель сначала квалифицирует backend и его ограничения. Она не внедряет его в обычный transcript.

## Required Work

1. Проверить 29 frozen Transcript Perfection sources и 355 production guards.
2. Сравнить локальные temporal candidates: pyannote Community-1, NeMo Sortformer и NeMo MSDD либо
   равноценные backends; зафиксировать license, model size, CPU/offline runtime и speaker limits.
3. Отбросить candidate, который не поддерживает минимум пять remote speakers или требует cloud API.
4. Выбрать минимум один backend, использующий temporal speaker activity, overlap-aware segmentation
   или sequence decoding, а не прежний fixed-window clustering.
5. До labels/truth заморозить model SHA-256, runtime, audio-only preprocessing, remote input hashes,
   speaker-count source, overlap policy, output normalization и candidate pack.
6. Использовать те же шесть real sessions и 33 direct-truth items; любое изменение корпуса оформить
   отдельной версией до оценки.
7. Измерить temporal replay stability, boundary conservation, speaker collapse/fragmentation,
   cluster-to-profile purity/margin, open-set safety и поведение mixed/unusable clips.
8. Проверить три confirmed gains, control identities и отсутствие новых false identities.
9. Fail-open при missing model, unsupported speaker count, conflict или weak evidence.
10. Добавить CLI, replay, fixture tests, public aggregate report, docs and planning; commit and push.

## Acceptance Gates

- backend работает полностью локально и детерминированно на CPU либо явно фиксированном local device;
- license и model/runtime provenance достаточны для open-source проекта;
- одна frozen конфигурация поддерживает все шесть сессий, включая пять remote profiles;
- truth не выбирает model checkpoint, speaker count, segmentation, thresholds или mapping rules;
- speaker turns и word order сохраняются, mixed/overlap не превращаются в ложную identity;
- direct truth не добавляет false identity и не теряет correct control identity;
- все `3/3` confirmed gains сохранены либо candidate остаётся explicit unknown;
- production promotion запрещён в этой цели.

## Terminal Outcomes

- `TEMPORAL_DIARIZATION_READY`: temporal backend проходит frozen geometry, boundary и direct-truth
  gates; для него можно открыть отдельный monotonic shadow profile.
- `KEEP_EXPLICIT_UNKNOWN`: backend воспроизводим, но не превосходит безопасный Coverage v3 residual.
- `EVIDENCE_BOUND`: model/license/runtime, five-speaker support или frozen evidence не позволяют
  честную оценку.

## Previous Goal Result

Stronger Local Remote Speaker Representation Qualification v1 завершён `KEEP_EXPLICIT_UNKNOWN`.
WeSpeaker ResNet34-LM был заморожен до labels/direct truth на 347 окнах. Minimum silhouette
`0.263291` прошёл gate, minimum stability ARI `0.442394` не прошёл. Candidate сохранил `3/3` gains и
не потерял correct controls, но дал 17 unsafe accepts, включая 12 новых false identities, и шесть
ambiguous clusters. Coverage v3, selected transcripts, raw CAF, ASR и Echo Guard не изменены.

## After This Goal

1. `TEMPORAL_DIARIZATION_READY` открывает отдельный monotonic temporal shadow candidate.
2. `KEEP_EXPLICIT_UNKNOWN` закрывает доступные локальные diarization backends до нового model class
   или расширения direct truth.
3. `EVIDENCE_BOUND` чинит только acquisition, license, runtime, speaker-limit или provenance.
