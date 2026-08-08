# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Attribution Error Decomposition v1

OpsKarta nearest goal: Remote Speaker Attribution Error Decomposition v1: заморозить SHA-256
существующих Truth Lab v1, hard-v2, once-opened hard-v3, их truth, predictions, decisions и ledgers
без повторного выбора кандидата; построить детерминированную oracle-матрицу `oracle boundaries +
current identity`, `current boundaries + oracle identity` и отдельный `overlap/open-set oracle`,
чтобы независимо измерить потолок boundary detection, speaker identity и mixed/open-set abstention
на exact word/speaker/timestamp truth; разложить ошибки по corpus, speaker, duration, gap, transition и
overlap strata, сохранив все слова и provenance; по заранее заданным правилам выпустить ровно один
итог `ADVANCE_DEDICATED_SEGMENTATION`, `ADVANCE_STRONGER_SPEAKER_IDENTITY`,
`ADVANCE_OVERLAP_OPEN_SET_MODEL` либо `CURRENT_LOCAL_ATTRIBUTION_LIMIT`, не подбирать новый
production candidate и не ослаблять gates после просмотра truth; не менять selected transcript,
Coverage v3, raw CAF, primary ASR, Echo Guard, hard-v2/v3 decisions и не переносить synthetic labels
в real sessions; добавить CLI, автоматические тесты и Transcript Perfection source, обновить README,
contracts, runbook, current-goal, roadmap и OpsKarta, закоммитить и отправить изменения в origin/main.

## Why Now

Две независимо замороженные проверки закрыли текущий класс embedding-эвристик:

- Duration-Aware v2: hard-v2 B-cubed `0.499381`, known recall `0.551402`, boundaries `9/28`;
- Segment-Context v1: hard-v3 B-cubed `0.475586`, known recall `0.445087`, boundaries `0/20`,
  две ложные open-set attribution.

Новый segment-context слой местами улучшил метрики относительно Coverage v3 control, но одновременно
ухудшил границы и open-set safety. По итоговому числу нельзя понять, что именно ограничивает систему:
поиск смены говорящего, устойчивость speaker embedding или обработка overlap/open-set. Ещё один набор
порогов без такого разложения только повторит уже закрытые попытки.

## Objective

Получить точный error budget для remote speaker attribution и выбрать один качественно новый трек
развития. Эта цель диагностическая: она не создаёт ещё один production candidate.

## Required Work

1. Заморозить входы и хэши трёх exact корпусов, решений hard-v2/v3 и всех используемых prediction.
2. Посчитать current system на общей нормализованной word/boundary схеме без повторного tuning.
3. Подставить oracle boundaries при неизменном current identity backend.
4. Подставить oracle speaker identity при неизменных current candidate boundaries.
5. Отдельно измерить mixed speech, overlap и unseen open-set abstention.
6. Разложить ошибки по corpus, speaker, duration, gap, transition type и overlap state.
7. Применить заранее зафиксированное дерево решения и выбрать ровно один следующий backend track.
8. Добавить deterministic replay, portable public report, CLI, тесты и Transcript Perfection source.

## Acceptance Gates

- все exact слова, timestamps, speaker truth и evaluated boundaries учтены ровно один раз;
- oracle-треки меняют только одну ось за раз;
- hard-v2/v3 decisions и opening ledgers остаются byte-exact;
- результаты повторного запуска совпадают побайтно;
- каждый вывод о bottleneck имеет corpus и stratum provenance;
- выбран ровно один из четырёх заранее объявленных итогов;
- production, Coverage v3 и ordinary transcript не меняются.

## Safety Boundary

- hard-v3 уже открыт один раз и больше не является blind selection set;
- truth разрешена только для error decomposition, но не для настройки нового кандидата;
- synthetic speaker labels не переходят в реальные сессии;
- никакие пороги Attribution v2/Segment-Context v1 не пересматриваются;
- имена людей и cross-session voice identity остаются запрещены.

## Previous Goal Result

Segment-Context Remote Speaker Attribution v1 завершён с `DO_NOT_PROMOTE_SEGMENT_CONTEXT`:

- hard-v3 заморожен до разработки: 5 scenarios, 197 words, 22 boundaries, 7 mixed words;
- 4 enrolled и 2 open-set voices, новые scripts/voices и отдельное enrollment;
- выбран `conservative_dual_backend_context_fusion` только на v1 + open hard-v2;
- hard-v3: B-cubed `0.475586`, pairwise precision `0.966418`, known recall `0.445087`;
- boundaries `0/20`, open-set false attribution `2`, mixed fail-closed `7/7`;
- exact words/stems, one-shot opening, deterministic replay и production boundary сохранены;
- Transcript Perfection теперь проверяет `17/17` frozen sources.

## After This Goal

1. Boundary bottleneck открывает квалификацию специализированного локального diarization engine.
2. Identity bottleneck открывает новый speaker embedding/enrollment backend на новом blind corpus.
3. Overlap/open-set bottleneck открывает отдельный abstaining detector.
4. Если ни один oracle ceiling не достигает gates, фиксируется текущий локальный предел и критический
   путь переходит к внешнему human-reviewed reference либо новому классу моделей.
