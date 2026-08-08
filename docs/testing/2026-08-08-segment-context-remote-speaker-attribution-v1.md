# Segment-Context Remote Speaker Attribution v1 Result

Date: 2026-08-08

Decision: `DO_NOT_PROMOTE_SEGMENT_CONTEXT`.

## Setup

- Development: Controlled Truth Lab v1 + открытый hard-v2, 365 exact words.
- Untouched hard-v3: 5 scenarios, 197 words, 22 truth boundaries, 7 mixed words.
- Speakers: 4 enrolled, 2 open-set; отдельное 64-word enrollment.
- Candidate pool: три заранее объявленных segment-context topology.
- Selected topology: `conservative_dual_backend_context_fusion`.
- Hard-v3 decision openings: `1`.

## Metrics

| Split | B-cubed F1 | Pairwise precision | Known recall | Boundary recall | Open-set false |
|---|---:|---:|---:|---:|---:|
| Development | 0.577946 | 1.000000 | 0.605505 | 0.154930 | 0 |
| Hard-v3 candidate | 0.475586 | 0.966418 | 0.445087 | 0.000000 | 2 |
| Hard-v3 Coverage v3 control | 0.397590 | 0.943249 | 0.439306 | 0.150000 | 0 |

Candidate сохранил 197/197 words и 7/7 mixed fail-closed, но не прошёл B-cubed, pairwise,
known-speaker, boundary, open-set и control non-regression gates. Нулевая boundary recall означает,
что длинный context не решил главную задачу смены говорящего; две open-set ошибки не позволяют
обменять abstention на небольшое улучшение identity recall.

## Integrity

- hard-v3 заморожен до candidate implementation;
- hard-v3 не использовался для выбора topology/config;
- повторный `evaluate-hard` отвергнут;
- replay совпал по candidate predictions, control predictions и public report;
- public artifacts не содержат scripts, voices, seed, speech text или absolute paths;
- selected transcript, Coverage v3, raw CAF, primary ASR и Echo Guard не изменены.

Tracked lineage:
`docs/testing/segment-context-remote-speaker-attribution-v1-manifest.json`.

## Consequence

Duration-aware word fusion и segment-context fusion закрыты как текущий путь улучшения. Перед
квалификацией нового diarization/embedding backend нужна oracle-декомпозиция boundary, identity и
overlap/open-set ошибок на всех exact корпусах.
