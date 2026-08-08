# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Independent Remote Speaker Evidence v1

OpsKarta nearest goal: Independent Remote Speaker Evidence v1: квалифицировать один действительно независимый локальный speaker-embedding или diarization backend на frozen six-session Coverage v3 corpus и отдельно на `598.240s` unknown remote residue; зафиксировать модель, лицензию, SHA-256, offline runtime, enrollment и test split; сохранить selected words, timestamps, roles, Me, v2/v3 labels, 5/5 internal boundaries и exact aggregate fallback; разрешить новый профиль только при corpus-wide восстановлении не менее 20% unknown words и 20% unknown seconds без снижения attributed B-cubed F1 и pairwise precision ниже promoted v3, иначе завершить воспроизводимым `DO_NOT_PROMOTE`; не ослаблять v3 thresholds, не считать согласие двух backends truth, не менять capture, Echo Guard, primary ASR, lexical reference или optional derivatives; добавить тесты, обновить README, contracts, runbook, current-goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Lexical Accuracy Reference Corpus v1 честно закрылся `REFERENCE_INSUFFICIENT`: точный цифровой
поднабор измерен, но реальная WER требует внешней человеческой проверки. Автономно чинить слова по
согласию машин сейчас нельзя.

Самый крупный уже измеренный остаток находится в remote speaker attribution: 851 слово и
`598.240s` сохранённой remote-речи остаются aggregate `Colleagues`. Coverage v3 продвинут, а v4
исчерпал безопасное расширение на том же Resemblyzer evidence и закрылся `DO_NOT_PROMOTE`.
Продолжение имеет смысл только с действительно независимым локальным источником доказательств.

## Objective

Выбрать и воспроизводимо квалифицировать один независимый локальный backend на неизменном корпусе.
Получить новый безопасный remote-speaker профиль либо точно доказать предел выбранного backend без
изменения обычной транскрибации.

## Required Work

1. Сравнить локально доступные backends по лицензии, Apple Silicon runtime, offline-модели и
   пригодности для коротких speaker-bounded окон; выбрать один, а не строить зоопарк моделей.
2. Зафиксировать model/runtime manifest с версиями, SHA-256 и запретом неявной сети.
3. Заморозить существующие v3/v4 inputs, private references, enrollment и split до оценки.
4. Запускать candidate только на remote audio и остаточных unknown-окнах; слова и таймкоды читать из
   authoritative dialogue, а не распознавать заново.
5. Считать recovery слов/секунд, B-cubed, pairwise precision/recall, merge/split errors, boundary и
   1x1 controls отдельно от agreement с текущим backend.
6. Материализовать изолированный профиль и exact aggregate fallback; обычный default не менять до
   решения всего корпуса.
7. Выпустить `PROMOTE_INDEPENDENT_REMOTE_SPEAKER_EVIDENCE_V1` или научно полный `DO_NOT_PROMOTE`.

## Acceptance Gates

- модель и runtime локальны, pinned, лицензия совместима, implicit download запрещён;
- 6/6 frozen sessions, две 1x1, четыре group и 5/5 internal boundaries воспроизводимы;
- unknown recovery одновременно не ниже 20% слов и 20% секунд;
- attributed B-cubed F1 и pairwise precision не ниже promoted Coverage v3;
- known v2/v3 labels, words, timestamps, roles, `Me` и chronological order сохранены точно;
- false merge редкого/нового remote speaker не скрывается ростом coverage;
- missing model, conflict или stale lineage дают exact aggregate fallback;
- повторный прогон byte-stable, raw CAF и selected default неизменны;
- итог `PROMOTE` или `DO_NOT_PROMOTE`, без промежуточного неподтверждённого default.

## Safety Boundary

- no capture, Echo Guard, Target-Me, primary ASR or lexical-reference changes;
- no cloud inference, voice-derived human names or cross-session identity;
- no threshold weakening in Coverage v3;
- no promotion from backend agreement without private-reference gates;
- no notes, summaries, external writes, retention changes or UI.

## Previous Goal Result

Lexical Accuracy Reference Corpus v1 completed with `REFERENCE_INSUFFICIENT`:

- 9 graded sources: exact generated, held-out scripted and two independent-machine meetings;
- exact generated subset: 67 words, WER/CER `0`, term accuracy `1.0`;
- 1x1, group, `Me`, remote and speaker-playback diagnostics exist;
- human-reviewed real sessions: 0; required lexical repair remains blocked;
- weak references cannot become truth; public artifacts contain no speech, names or absolute paths;
- Transcript Perfection now verifies 13/13 sources and reports a bounded exact subset.

## After This Goal

1. If promoted, qualify Independent Remote Speaker Evidence as a default candidate through the same
   exact fallback contract.
2. If not promoted, keep Coverage v3 and record the independent-backend ceiling; do not iterate by
   loosening thresholds.
3. Human-Reviewed Lexical Seed remains an external-evidence prerequisite.
4. Local mic multi-speaker diarization opens only after a real labelled scenario exists.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory.
