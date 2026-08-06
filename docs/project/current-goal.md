# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF and the promoted Speaker-Preserving Neural Echo v2 output remain immutable baselines. New
separators run in isolated profiles and may replace `mic_for_asr` only after audio, direct-ASR and
corpus-wide safety gates pass.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Pre-ASR Target-Me Isolation Limit v1

OpsKarta nearest goal: Pre-ASR Target-Me Isolation Limit v1: провести Multi-Component Residual Separator Qualification v1 поверх неизменяемого production v2 и завершённого Alignment/Echo-Path v3; до hard/sealed данных заморозить decomposition contract, dev corpus, candidate ladder и stop rules; разделять Target-Me, remote echo, other-local и unexplained residual с mixture consistency и exact fallback; доказать direct-ASR уменьшение remote без потери protected Me, nearby speech, chronology, openings и double-talk и без post-ASR cleanup credit; завершить PROMOTE_MULTI_COMPONENT_RESIDUAL_SEPARATOR, READY_FOR_STRONGER_LOCAL_SEPARATOR либо CURRENT_RESOURCE_LIMIT_REACHED с тестами, актуальной документацией, roadmap и OpsKarta, коммитом и push.

## North Star

Для поддерживаемой speaker-playback сессии канонический вход микрофона в первичный ASR должен:

- содержать всю распознаваемую речь целевого пользователя `Me`;
- не содержать распознаваемого содержания authoritative remote;
- не присваивать `Me` речь людей рядом с микрофоном;
- сохранять `other_local` и необъяснённый residual как отдельные доказательства;
- возвращаться к точному production baseline при недостатке доказательств.

Это операционный критерий по словам, ролям и порядку реплик. Нулевой residual без сохранённых слов
не считается успехом.

## Evidence So Far

Speaker-Preserving Neural Echo v2 остаётся production plateau: на sealed corpus кандидат выбран в
`5/12` сессиях, удалены `41.940s` и 90 remote-supported токенов при local-token retention `1.0`;
остальные `7/12` сессий используют exact fallback.

Residual Echo Ceiling Map v1 измерил `6869.306s` actionable material evidence. Alignment/echo-path
занимал `2443.222s` (`35.567%`), multi-component separation — `2124.220s` (`30.923%`), Target-Me
model — `1258.702s` (`18.324%`). Поэтому первым был проверен физический эхотракт.

Alignment and Echo-Path Model v3 завершён с `READY_FOR_MULTI_COMPONENT_SEPARATOR`. После единственной
разрешённой revision модель безопасно изменила 11 из 32 контролируемых remote-фрагментов при
требовании 12, дала median reduction `2.552124 dB` и сохранила все 156 protected items sample-exact.
На real dev изменения оставались только внутри remote-only окон, однако required low-leak control
не получил exact fallback. Direct ASR, hard и sealed поэтому не запускались. Production v2 не изменён.

Вывод ограничен, но полезен: time-varying FIR и нелинейные remote bases уменьшают когерентное эхо,
однако не объясняют весь остаток. Дальше нужен разделитель, который моделирует несколько источников,
а не ещё один порог или более длинный FIR.

## Objective

За один ограниченный исследовательский цикл проверить multi-component separator поверх production
v2. Он должен явно выделять `target_me`, `remote_echo`, `other_local` и `unexplained_residual`,
сохранять их происхождение и возвращать baseline для каждого сомнительного окна. Цикл заканчивается
продвижением, доказанным пределом доступной локальной реализации или точным требованием к следующему
классу модели.

## Required Work

1. Заморозить production v2, residual map, решение v3, controlled supervision, Target-Me
   Identifiability Corpus, development/hard/sealed splits и SHA-256 до обучения или настройки.
2. Зафиксировать decomposition contract: входы, четыре стема, временную сетку, mixture-consistency
   tolerance, allowable latency, exact fallback и запрет публикации до corpus decision.
3. Подготовить split-disjoint supervised mixtures из уже проверенных clean Target-Me, non-target
   local, measured echo и digital remote. Не превращать `speaker_state` или ASR-текст в ground truth.
4. Зафиксировать bounded ladder до hard data: deterministic constrained baseline; v3 echo estimate
   как дополнительный признак; reference-conditioned local model; один лицензированно и технически
   проверенный pretrained/local initialization, если он доступен offline.
5. Учить и выбирать модель только на train/dev. Target-Me query должен влиять на выход; wrong-query,
   nearby-speaker, remote-only, keyboard, silence, opening и measured double-talk являются
   обязательными отрицательными или preservation controls.
6. Публиковать candidate только целыми доказанными окнами. Любой конфликт идентичности, source
   attribution, reconstruction, chronology или local-word evidence выбирает exact production v2.
7. На dev измерить stem reconstruction, remote leakage, wrong-query margin и direct whisper.cpp.
   Hard и sealed открывать лишь после полного locked dev pass; tuning после открытия запрещён.
8. Выпустить `PROMOTE_MULTI_COMPONENT_RESIDUAL_SEPARATOR`,
   `READY_FOR_STRONGER_LOCAL_SEPARATOR` или `CURRENT_RESOURCE_LIMIT_REACHED` с воспроизводимыми
   отчётами, provenance и транзакционным publication plan.

## Acceptance Gates

- все подтверждённые слова `Me`, openings, chronology и measured double-talk не хуже production v2;
- nearby `other_local` не попадает в Target-Me stem и остаётся доступен как отдельное доказательство;
- authoritative remote audio/text не меняются, а ASR-visible remote в Target-Me уменьшается на
  заранее замороженную существенную величину в нескольких сессиях;
- correct-query результат превосходит wrong-query и query-agnostic controls на split-disjoint
  speakers; identity collapse запрещает promotion;
- сумма стемов объясняет вход в заданной tolerance, но mixture consistency не может насильно
  вернуть remote в Target-Me stem;
- no-speech, headphones/low-leak, unsupported and uncertain windows выбирают exact fallback;
- outside-selected samples совпадают с production v2, raw CAF и transcript evidence неизменны;
- повторный запуск детерминирован, runtime укладывается в замороженный локальный budget;
- post-ASR filtering, role cleanup и transcript deletion получают нулевой promotion credit;
- contracts, runbook, README, roadmap, OpsKarta и автоматические тесты отражают итоговое решение.

## Stop Rules

- не ослаблять local-word, identity, chronology, opening или double-talk gates ради suppression;
- не настраивать thresholds, architecture или splits по hard/sealed результатам;
- не считать AECMOS, SI-SDR, speaker similarity или exact remix самостоятельным разрешением;
- не заменять multi-component модель ещё одной scalar mask или post-ASR эвристикой;
- не загружать непроверенную модель во время qualification: license, hash, offline runtime и ресурсный
  budget должны пройти preflight до заморозки;
- если доступная локальная модель не проходит dev после одной bounded revision, завершить точным
  отрицательным решением, а не продолжать поиск на тех же данных.

## Safety Boundary

- capture, raw writer, authoritative remote, ordinary whisper.cpp и Live Shadow не меняются;
- production v2 и `local_fir_role_masked` остаются exact fallbacks;
- private enrollment, models and meeting audio stay local and ignored by source control;
- no cloud audio processing, external writes, automatic voice identity or cross-session roster.

## Deferred Product Step

Reviewed Speaker-Aware Meeting Memory v1 remains ready. Its explicit session-local labels and
evidence-bound export contract resume after the current audio frontier reaches a terminal decision.
