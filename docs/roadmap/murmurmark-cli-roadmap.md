# MurmurMark CLI Roadmap

Roadmap лежит в формате opskarta v3:

- `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`
- no-schedule: без календарных дат, только структура, зависимости, статусы и effort
- основной путь: CLI-first, local-first, evidence-backed

## Смысл карты

MurmurMark уже прошёл стадию proof of concept: запись, подавление эха, локальная транскрибация, timeline repair, audit cleanup, audio review, agent-reviewed слой, extractive notes и quality verdict уже работают.

Ближайшая цель — закончить превращение набора отдельных скриптов в нормальный CLI-пайплайн:

1. `murmurmark process SESSION` — готово.
2. `murmurmark process latest` — готово.
3. `murmurmark sessions` — готов короткий список последних записей с временем, длительностью, review burden, readiness-статусом, фильтром, JSON-выводом и next-командой.
4. `murmurmark status SESSION`, `murmurmark report SESSION`, `murmurmark report corpus` и `murmurmark next corpus` — готово.
5. `murmurmark open SESSION` — готов короткий CLI-вход к выбранным notes/transcript/verdict/readiness/audit артефактам.
6. `murmurmark audit local-recall|group-overlaps|audio-review` — готов CLI-вход к audit-слоям со сводкой.
7. `murmurmark cleanup` и `murmurmark synthesize` — готовы CLI-входы к cleanup-профилям и extractive notes.
8. `murmurmark review SESSION` — готов базовый CLI-контур.
9. `murmurmark corpus process all` — готов базовый контур качества по корпусу.
10. `murmurmark corpus taxonomy` — готова сводная таксономия аудио-ошибок для следующей итерации качества.
11. `murmurmark corpus gate` — готов no-regression gate с локальным baseline-сравнением, local-recall blockers и warnings по remote-leak очереди.
12. `murmurmark corpus local-recall` — готова корпусная очередь возможных пропусков `Me`.
13. `murmurmark corpus local-recall-repair` — готова сводка эффекта `local_recall_repair_v1` перед auto-promotion.
14. `murmurmark export SESSION --format markdown|obsidian` — готов базовый пользовательский output-блок.
15. `scripts/install-local.sh` — готов минимальный локальный install wrapper для команды `murmurmark`.
16. `murmurmark doctor` — готов расширенный health check локальной установки и pipeline-зависимостей.
17. `scripts/build-release-bundle.sh` — готов локальный release layout с manifest и без приватных данных.
18. `murmurmark retention plan SESSION` — готов локальный retention plan; raw deletion защищён отдельным `apply`.
19. `murmurmark retention payload SESSION` — готов provider payload manifest; default policy блокирует внешние payload’ы.
20. `scripts/check-open-source-readiness.sh` — готов public-readiness gate; MIT LICENSE добавлена.
21. `murmurmark self-test` — готов быстрый CLI smoke через сам инструмент.
22. `scripts/acceptance-cli-mvp.sh` — готов единый автоматический gate для CLI MVP.

UI App не является обязательной частью roadmap. Он остаётся optional tail после зрелого CLI, review loop, export и retention policy.

## Крупные направления

- `foundation-done` — уже готовая основа: capture, Echo Guard, whisper.cpp, repair/audit, agent_reviewed_v1, notes, readiness.
- `cli-orchestration` — текущий фокус: единые команды process/report/audit/review/corpus/export/config; минимальная локальная установка уже готова.
- `corpus-regression` — текущий контур: корпус сессий, пересборка, baseline thresholds,
  out-of-fold оценка audio judge, local-recall blockers, remote-leak queue и явные review/export
  blockers.
- `review-loop` — ближайший этап: удобный CLI-review спорных участков; ручный workspace review и агентный `review agent` уже есть.
- `quality-hardening` — ближайший этап: улучшение качества transcript без смены топологии; первый
  явный `order_repair_v1` уже чинит только те order-risk регионы, которые безопасно режутся по
  сохранённым source ASR segments. `local_recall_repair_v1` уже восстанавливает короткие
  boundary-сдвинутые `Me`-фразы через micro-ASR, а вставки проходят через обычный review loop;
  следующий короткий шаг — расширение boundary repair только по доказанным случаям.
- `evidence-notes` и `export-workflows` — пользовательские артефакты; базовый export готов, дальше нужны vault/docs/Jira proposals.
- `retention-policy` и `packaging` — приватность, хранение raw audio, release layout, provider payload manifest, self-test, CLI MVP acceptance и readiness gate; перед публикацией нужен публичный security contact.
- `future-heavy-local`, `future-llm-synthesis`, `future-ui-app` — дальние ветки.

## Проверка

```bash
OPSKARTA_REPO="${OPSKARTA_REPO:-../opskarta}"
PLAN="docs/roadmap/murmurmark-cli-roadmap.plan.yaml"

PYTHONPATH="$OPSKARTA_REPO" python3 -m specs.v3.tools.cli validate "$PLAN"
PYTHONPATH="$OPSKARTA_REPO" python3 -m specs.v3.tools.cli render tree "$PLAN"
PYTHONPATH="$OPSKARTA_REPO" python3 -m specs.v3.tools.cli render deps "$PLAN" --mode hierarchical
PYTHONPATH="$OPSKARTA_REPO" python3 -m specs.v3.tools.cli render executive "$PLAN" --view exec-top
PYTHONPATH="$OPSKARTA_REPO" python3 -m specs.v3.tools.cli render executive-report "$PLAN" --section status --lang ru
```

## Ближайшая дуга

```mermaid
flowchart LR
    foundation["foundation-done<br/>готовая основа"]
    cli["cli-orchestration<br/>process/report/review/export"]
    corpus["corpus-regression<br/>no-regression gates"]
    review["review-loop<br/>человеческие и агентные решения"]
    quality["quality-hardening<br/>меньше дублей и пропусков"]
    notes["evidence-notes<br/>проверяемые итоги"]
    export["export-workflows<br/>Markdown/Obsidian/docs/Jira proposals"]
    tail["future-ui-app<br/>optional tail"]

    foundation --> cli
    foundation --> corpus
    cli --> review
    corpus --> review
    review --> quality
    corpus --> quality
    quality --> notes
    notes --> export
    export --> tail
```
