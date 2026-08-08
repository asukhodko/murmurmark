# Segment-Context Remote Speaker Attribution v1 Runbook

Этап завершён. Обычная команда нужна для просмотра и воспроизведения уже принятого решения.

## Status

```bash
murmurmark corpus remote-segment-context status
murmurmark corpus remote-segment-context hard-status
```

Ожидаемый итог:

```text
decision: DO_NOT_PROMOTE_SEGMENT_CONTEXT
selected: conservative_dual_backend_context_fusion
```

## Deterministic Replay

```bash
murmurmark corpus remote-segment-context hard-replay
murmurmark corpus remote-segment-context replay
```

`hard-replay` проверяет неизменность замороженного корпуса. `replay` повторно считает уже открытый
candidate и сравнивает predictions/report с сохранёнными SHA-256. Это не второе открытие hard-v3.

## Contract Check

```bash
.venv/bin/python scripts/check-segment-context-remote-speaker-attribution-v1.py
```

Проверка строит независимую fixture-копию, подтверждает:

- disjoint hard-v3 и exact stem reconstruction;
- выбор ровно из трёх topology без hard-v3 truth;
- отсутствие ledger до development freeze;
- одноразовое открытие и отказ второго `evaluate-hard`;
- защиту от подмены candidate;
- deterministic replay и private-safe public outputs;
- точные хэши локального реального решения, если private corpus присутствует.

## Freeze And Evaluation

Эти команды приведены для воспроизводимости нового изолированного `--out-dir`. Не запускай
`freeze`, `develop` или `evaluate-hard` поверх основного отчёта: его one-shot решение завершено.

```bash
murmurmark corpus remote-segment-context freeze --out-dir sessions/_reports/segment-context-scratch
murmurmark corpus remote-segment-context develop --out-dir sessions/_reports/segment-context-scratch
murmurmark corpus remote-segment-context evaluate-hard --out-dir sessions/_reports/segment-context-scratch
```

Новая scratch-оценка не является продолжением hard-v3 и не может заменить tracked decision.

## Safety

- не редактировать policy/freezer/evaluator, закреплённые tracked manifest;
- не удалять private hard-v3 и opening ledger, пока они являются corpus evidence;
- не применять synthetic predictions к real sessions;
- не менять Coverage v3 или ordinary transcript по результату этого эксперимента.
