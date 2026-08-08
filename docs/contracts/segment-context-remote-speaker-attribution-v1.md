# Segment-Context Remote Speaker Attribution v1

Status: completed, `DO_NOT_PROMOTE_SEGMENT_CONTEXT`.

## Purpose

Контракт проверяет, может ли speaker evidence на длинных однородных remote-интервалах надёжнее
атрибутировать короткие слова, чем Duration-Aware v2. Это лабораторный слой: он не меняет выбранный
transcript и не переносит synthetic labels в реальные сессии.

## Frozen Inputs

- Controlled Remote Speaker Truth Lab v1 и открытый hard-v2 используются только для development.
- Новый private hard-v3 заморожен до выбора topology.
- Hard-v3 содержит 5 сценариев, 197 слов, 22 speaker boundaries и 7 mixed words.
- Четыре голоса enrolled, два голоса open-set; scripts и renderer voices не пересекаются с v1/v2.
- Source stems восстанавливают mixture без ошибки PCM samples.

Публичная lineage закреплена в
`docs/testing/segment-context-remote-speaker-attribution-v1-manifest.json`. Private audio и truth
остаются под `sessions/_reports/segment-context-remote-speaker-attribution-v1/private/`.

## Predeclared Topologies

1. `silence_bounded_context_prototypes`.
2. `embedding_change_point_context`.
3. `conservative_dual_backend_context_fusion`.

Speaker truth запрещена при поиске границ и выборе topology. Mixed words получают `mixed`, короткие
неподтверждённые сегменты и конфликт моделей получают `unknown_speaker`.

## One-Shot Decision

Development выбрал `conservative_dual_backend_context_fusion` и заморозил config и implementation
SHA-256. Только после этого hard-v3 был открыт один раз. Повторный `evaluate-hard` запрещён ledger;
`replay` может пересчитать уже принятое решение и обязан совпасть побайтно.

Promotion требовал одновременно:

- exact word conservation и direct scripted truth;
- B-cubed F1 и pairwise precision не ниже `0.98`;
- known-speaker recall не ниже `0.98`;
- boundary recall `1.0`;
- zero open-set false attribution;
- mixed fail-closed и non-regression относительно Coverage v3 control.

## Outputs

Публичные:

- `hard_v3_public_manifest.json`;
- `development_report.json`;
- `segment_context_remote_speaker_attribution_report.json`;
- `segment_context_remote_speaker_attribution_report.md`;
- `replay_report.json`.

Private:

- `private/hard-v3/frozen_manifest.json`;
- `private/candidate_freeze.json`;
- `private/hard-v3/hard_v3_opening_ledger.json`;
- exact truth, source stems, embeddings и predictions.

## Result

Hard-v3 получил B-cubed `0.475586`, pairwise precision `0.966418`, known-speaker recall `0.445087`,
boundary recall `0.0` и две open-set false attribution. Итог —
`DO_NOT_PROMOTE_SEGMENT_CONTEXT`; Coverage v3 и production остаются неизменными.

Следующий шаг — разложить boundary, identity и overlap/open-set ошибки oracle-треками до выбора
качественно нового backend.
