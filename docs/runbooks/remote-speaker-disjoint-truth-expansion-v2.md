# Remote Speaker Disjoint Truth Expansion v2 Runbook

Это приватная разметка корпуса, а не часть обычной обработки встречи.

## Проверка Замороженного Pack

```bash
murmurmark corpus remote-truth-seed-v2 preflight
murmurmark corpus remote-truth-seed-v2 status
murmurmark corpus remote-truth-seed-v2 replay
```

Ожидаемое начальное состояние:

```text
primary_items: 72
primary_answers: 0
repeat_answers: 0
remaining_slots: 84
decision: REFERENCE_INSUFFICIENT
```

Не запускай `prepare` или `freeze` повторно ради смены выборки. Уже существующий frozen pack
проверяется и сохраняет порядок, ответы и SHA-256.

## Короткая Интерактивная Разметка

Рекомендуемый путь:

```bash
murmurmark corpus remote-truth-seed-v2 review
```

Очередь идёт сессионными блоками, но тип конкретного слота не показывается. При входе в блок
команда проигрывает target, доступные чистые anonymous exemplars и target ещё раз; для следующих
слотов этого блока проигрывается только target. Ответы:

- номер варианта: соответствующий `remote_speaker_XX`;
- `u`: голос слышен, но среди exemplars его нет или соответствие не доказано;
- `m`: в target говорят несколько участников;
- `x`: речь нельзя надёжно разобрать или клип непригоден;
- `r`: проиграть target ещё раз;
- `e`: снова проиграть target, все exemplars и target;
- `q` или `Ctrl-D`: остановиться, сохранив прогресс.

Скрытые повторы выглядят как обычные слоты. Не пытайся вспомнить предыдущий ответ по имени файла.
При следующем запуске `review` очередь продолжится с первого неразмеченного слота.

Команда должна оставаться подключённой к stdin текущего терминала. Если сразу после приглашения
появилось `interactive review input is unavailable`, обнови и переустанови MurmurMark через
`scripts/install-local.sh`; сохранённые ответы при этом не теряются.

До первого ответа review-pack можно технически перестроить без смены frozen candidates:

```bash
murmurmark corpus remote-truth-seed-v2 refresh-review
```

После первого ответа команда fail-closed отказывается менять порядок.

## Пошаговый Режим

```bash
murmurmark corpus remote-truth-seed-v2 next --play
murmurmark corpus remote-truth-seed-v2 grade SLOT_ID --outcome remote_speaker_01
murmurmark corpus remote-truth-seed-v2 progress
```

Используй только outcome, напечатанный для текущего слота. Если конкретный голос узнаваем, но его
чистого anonymous exemplar нет, правильный ответ здесь `unknown_speaker`.

## Завершение

```bash
murmurmark corpus remote-truth-seed-v2 finalize
murmurmark corpus remote-truth-seed-v2 replay
murmurmark corpus remote-truth-seed-v2 status
```

`DIRECT_TRUTH_V2_READY` разрешает отдельную one-shot qualification следующего класса моделей.
`REFERENCE_INSUFFICIENT` фиксирует предел имеющихся записей и требует новых встреч с известными
remote speakers или отдельного controlled corpus. Ни один outcome не меняет текущую транскрибацию.

Фактический результат 2026-08-12: `DIRECT_TRUTH_V2_READY`. Закрыты `72/72` primary и `12/12`
hidden repeat slots; repeat consistency `1.0`. Primary outcomes: 21 attributed, 28 unknown,
4 mixed и 19 unusable на шести сессиях. Replay побайтно точен. Повторная разметка или перестройка
pack не требуется; следующий этап использует этот reference только для one-shot qualification.
