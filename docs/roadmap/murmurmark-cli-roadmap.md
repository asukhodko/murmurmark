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
3. `murmurmark status SESSION`, `murmurmark report SESSION` и `murmurmark report corpus` — готово.
4. `murmurmark audit local-recall|group-overlaps|audio-review` — готов CLI-вход к audit-слоям со сводкой.
5. `murmurmark cleanup` и `murmurmark synthesize` — готовы CLI-входы к cleanup-профилям и extractive notes.
6. `murmurmark review SESSION` — готов базовый CLI-контур.
7. `murmurmark corpus process all` — готов базовый контур качества по корпусу.
8. `murmurmark corpus taxonomy` — готова сводная таксономия аудио-ошибок для следующей итерации качества.
9. `murmurmark corpus gate` — готов no-regression gate с локальным baseline-сравнением, local-recall blockers и warnings по remote-leak очереди.
10. `murmurmark corpus local-recall` — готова корпусная очередь возможных пропусков `Me`.
11. `murmurmark corpus local-recall-repair` — готова сводка эффекта `local_recall_repair_v1` перед auto-promotion.
12. `murmurmark export SESSION --format markdown|obsidian` — готов базовый пользовательский output-блок.
13. `scripts/install-local.sh` — готов минимальный локальный install wrapper для команды `murmurmark`.
14. `murmurmark doctor` — готов расширенный health check локальной установки и pipeline-зависимостей.
15. `scripts/build-release-bundle.sh` — готов локальный release layout с manifest и без приватных данных.
16. `murmurmark retention plan SESSION` — готов локальный retention plan; raw deletion защищён отдельным `apply`.
17. `murmurmark retention payload SESSION` — готов provider payload manifest; default policy блокирует внешние payload’ы.
18. `scripts/check-open-source-readiness.sh` — готов public-readiness gate; MIT LICENSE добавлена.

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
- `retention-policy` и `packaging` — приватность, хранение raw audio, release layout, provider payload manifest и readiness gate; перед публикацией нужен публичный security contact.
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
