# Current Goal

Updated: 2026-08-20

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Capture Continuity Loss Closure v1

OpsKarta nearest goal: Capture Continuity Loss Closure v1: зафиксировав завершённый Post-Segmentation Transcript Rebaseline v1 и неизменные production transcript profiles, воспроизвести три restart-bounded PCM gap общей длиной 2.268542s, измерить stop-to-first-committed-PCM latency, устранить доказанную программную задержку либо выпустить точный EVIDENCE_BOUND для macOS outage, сделать loss явным в lifecycle/readiness, добавить fault-injection, soak и no-regression тесты, синхронизировать документацию и планы, затем закоммитить и отправить изменения в origin/main.

## Why Now

Post-Segmentation Transcript Rebaseline v1 завершён с `REBASELINE_ESTABLISHED`. Шесть свежих
fingerprint-bound сессий дали пять strict rich transcript и одну disclaimer-bearing provisional
surface. Coverage v3, selected words, roles, timestamps и aggregate fallback остались неизменными.
Strict Coverage v3 оставил explicit unknown на `5.0562%` remote seconds против замороженных
`6.0688%`.

Корпус одновременно обнаружил более ранний дефект: одна сессия пережила три
`stream_stopped`/restart события и потеряла `2.268542s` PCM. Интервалы имеют high-confidence
mic+remote evidence, каждый длится `0.724646..0.785979s`. Readiness показывает warning, но
`partial_recommended=false`. Потерянный источник нельзя восстановить последующей атрибуцией или
ASR, поэтому этот hard blocker идёт раньше Remote Unknown Evidence Recovery v1.

## Objective

Разобрать restart path от события остановки ScreenCaptureKit до первого committed PCM и закрыть
всю устранимую задержку. Если часть outage контролируется macOS и неустранима, MurmurMark должен
точно показать потерянные интервалы и никогда не выдавать такую сессию за полную.

## Required Work

1. Заморозить rebaseline report, affected private session input, `session.json`, capture events,
   continuity report и SHA-256 raw tracks. Production transcript artifacts не перегенерировать.
2. Добавить монотонные timestamps для stream stop, restart request, capture start completion,
   первого mic/remote callback и первого committed frame после restart.
3. Построить детерминированный fault-injection harness для restart state machine: повторный stop,
   stop во время restart, отмена, timeout и завершение meeting не должны давать continuation leak,
   deadlock, двойной writer или stale recording lock.
4. Убрать доказанные последовательные ожидания на первом restart. Разрешён только один in-flight
   restart; sidecar и post-processing не участвуют в capture recovery.
5. Сохранять session clock и точные gap intervals. Вставленная тишина может поддерживать timeline,
   но не считается captured audio и не скрывает потерю речи.
6. Протянуть capture completeness в lifecycle, readiness, outcome и transcript header. Сессия с
   gap остаётся usable partial, если raw валиден, но не проходит terminal completeness gate.
7. Выпустить automated restart tests, короткий controlled capture и 10-15 minute soak. Обычный
   no-restart capture и live sidecar обязаны сохранить прежнее поведение.
8. Пересобрать post-segmentation rebaseline и зафиксировать один из исходов:
   `PROMOTE_RESTART_HARDENING`, если software latency устранена и gates пройдены; либо
   `EVIDENCE_BOUND`, если остаток доказан как external outage и корректно отражается пользователю.
9. Обновить README, contracts, runbook, roadmap, OpsKarta и текущую цель; выполнить полный набор
   проверок, commit и push.

## Acceptance Gates

- frozen rebaseline, Coverage v3, ASR, Echo Guard, selected transcripts и существующие raw CAF не
  изменены;
- все restart transitions имеют монотонную provenance и ровно один terminal outcome;
- injected stop не создаёт continuation misuse, deadlock, double writer или stale lock;
- измерена отдельно OS outage и MurmurMark restart latency;
- устранимая программная пауза удалена; остаточная потеря имеет точные start/end/duration;
- no-restart capture имеет zero regression по длительности, sparse/silent detection и stop path;
- batch processing успешно завершается после каждого fault case;
- lifecycle/readiness/transcript не называют gap-сессию полной;
- post-segmentation corpus replay остаётся byte-exact после осознанного refresh только нового
  capture evidence;
- результат детерминирован, документирован, закоммичен и отправлен в `origin/main`.

## Out Of Scope

- изменение основного ASR, Echo Guard, Coverage v3 или remote-speaker thresholds;
- попытка угадать речь внутри уже потерянного PCM;
- второй независимый capture process без доказательства, что single-stream recovery недостаточен;
- cloud inference, UI, notes, summaries и lexical tuning.

## First Commands

```bash
murmurmark corpus post-segmentation-rebaseline all --verify-existing
jq '.dimensions.capture_completeness, .next_priority' \
  sessions/_reports/post-segmentation-transcript-rebaseline-v1/post_segmentation_rebaseline_report.json
scripts/check-capture-regressions.sh
scripts/check.sh
```
