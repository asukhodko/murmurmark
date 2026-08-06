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

OpsKarta nearest goal: Pre-ASR Target-Me Isolation Limit v1: после завершённого Multi-Component Residual Separator v1 с READY_FOR_STRONGER_LOCAL_SEPARATOR подготовить Stronger Offline Target-Speaker Separator Prerequisites v1; заморозить расширение split-disjoint supervision для Target-Me и nearby speakers, выбрать один лицензированно совместимый offline backbone с зафиксированными hash, resource preflight и four-stem adapter plan; не открывать новый hard/sealed цикл до доказанной готовности данных и модели; завершить READY_FOR_STRONGER_SEPARATOR_QUALIFICATION либо CURRENT_RESOURCE_LIMIT_REACHED с тестами, актуальной документацией, roadmap и OpsKarta, коммитом и push.

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

Multi-Component Residual Separator v1 завершён с `READY_FOR_STRONGER_LOCAL_SEPARATOR`. Небольшой
four-stem FiLM-GRU сохранил exact reconstruction и не схлопнул speaker query, но dev дал только
`5.561 dB` Target-Me SNR, `4.443 dB` other-local SNR, `6.803 dB` absent-query attenuation и
`-1.545 dB` residual SNR. Единственная разрешённая revision не улучшила предел. Hard, sealed и
direct ASR не открывались; production v2 остался неизменным.

Вывод теперь точнее: четырёхкомпонентный контракт пригоден, но имеющихся данных и небольшой модели
недостаточно для тихой/отсутствующей Target-Me и nearby speech. Следующий цикл должен сначала
доказать готовность более сильного локального разделителя, а не снова настраивать ту же архитектуру.

## Objective

Подготовить один воспроизводимый путь к более сильной локальной квалификации. До обучения нужно
закрыть два известных ограничения: увеличить split-disjoint supervision для Target-Me и nearby
speakers и выбрать один лицензированно совместимый offline speech-separation backbone, который
помещается в локальный ресурсный бюджет и допускает four-stem Target-Me adapter.

## Required Work

1. Заморозить production v2, завершённый Multi-Component v1, текущий Target-Me corpus и все SHA-256.
2. Составить точную карту нехватки данных по quiet/absent Target-Me, nearby speakers, double-talk,
   openings, клавиатуре и офисному шуму; обычные встречи не использовать как скрытые labels.
3. Спроектировать расширение train/dev/hard с непересекающимися non-target speakers и отдельными
   отрицательными query controls. Sealed corpus не открывать.
4. Проверить ограниченный список локальных pretrained separators по лицензии, воспроизводимой загрузке,
   pinned hash, macOS CPU runtime, памяти, sample rate и доступу к внутренним признакам.
5. Выбрать ровно один backbone либо доказать, что ни один кандидат не укладывается в текущие ресурсы.
6. Зафиксировать four-stem adapter plan: Target-Me query, frozen echo hint, other-local, residual,
   mixture consistency, exact production fallback и прямой ASR только после dev pass.
7. Выпустить immutable readiness manifest и ресурсный preflight без обучения на hard/sealed.
8. Завершить `READY_FOR_STRONGER_SEPARATOR_QUALIFICATION` или
   `CURRENT_RESOURCE_LIMIT_REACHED` с воспроизводимым отчётом и следующим ограниченным шагом.

## Acceptance Gates

- данные покрывают известные dev-провалы и сохраняют split-disjoint non-target identities;
- выбранный backbone имеет проверенные license, source revision, SHA-256 и offline-only загрузку;
- resource preflight на текущем Mac измеряет память, runtime и число потоков с фоновым приоритетом;
- adapter plan сохраняет четыре стема, speaker-query controls, exact fallback и post-ASR credit `0`;
- hard/sealed и production publication остаются закрыты до следующего immutable dev pass;
- raw CAF, production v2, transcript evidence и существующие frozen reports неизменны;
- повторный preflight детерминирован, а отсутствие модели или данных даёт явный ресурсный предел;
- contracts, runbook, README, roadmap, OpsKarta и тесты отражают принятое решение.

## Stop Rules

- не продолжать подстройку завершённого FiLM-GRU на тех же dev speakers;
- не выбирать backbone по hard/sealed или transcript cleanup;
- не скачивать модель без проверяемой лицензии, revision и hash;
- не выдавать большой размер модели, SI-SDR или speaker similarity за сохранение слов;
- не начинать обучение, пока данные, адаптер и ресурсный бюджет не заморожены;
- если подходящего offline backbone или supervision нет, завершить ресурсным пределом.

## Safety Boundary

- capture, raw writer, authoritative remote, ordinary whisper.cpp и Live Shadow не меняются;
- production v2 и `local_fir_role_masked` остаются exact fallbacks;
- private enrollment, models and meeting audio stay local and ignored by source control;
- no cloud audio processing, external writes, automatic voice identity or cross-session roster.

## Deferred Product Step

Reviewed Speaker-Aware Meeting Memory v1 remains ready. Its explicit session-local labels and
evidence-bound export contract resume after the current audio frontier reaches a terminal decision.
