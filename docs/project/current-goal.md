# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF and the promoted Speaker-Preserving Neural Echo v2 output remain immutable baselines. Every
new separator runs in isolation and may replace `mic_for_asr` only after audio, direct-ASR and
corpus-wide safety gates pass.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Pre-ASR Target-Me Isolation Limit v1

OpsKarta nearest goal: Pre-ASR Target-Me Isolation Limit v1: провести Alignment and Echo-Path Model v3 Qualification по завершённой residual map; заморозить production v2 и discovery decision, проверить sub-window delay/drift, bounded echo-path bank, nonlinear remote bases и remote-only residual suppression в изолированном профиле; до hard/sealed данных зафиксировать dev gates и stop rules, сохранить protected Me, other-local speech, chronology, openings, double-talk и exact fallback, не засчитывать post-ASR cleanup; завершить PROMOTE_ALIGNMENT_OR_ECHO_MODEL_V3, READY_FOR_MULTI_COMPONENT_SEPARATOR либо CURRENT_RESOURCE_LIMIT_REACHED с воспроизводимым корпусным решением, тестами, актуальной документацией, roadmap и OpsKarta, коммитом и push.

## North Star

Для поддерживаемой speaker-playback сессии канонический вход микрофона в первичный ASR должен:

- содержать всю распознаваемую речь целевого пользователя `Me`;
- не содержать распознаваемого содержания authoritative remote;
- не присваивать `Me` речь людей рядом с микрофоном;
- сохранять явный `other_local` или residual для необъяснённого звука;
- возвращаться к точному production baseline при недостатке доказательств.

Это операционный North Star, а не обещание математически идеального разделения waveform. Решение
оценивается по сохранённым словам, ролям и порядку реплик. Нулевой residual signal без сохранённых
слов не считается успехом.

## Why This Is Next

Speaker-Preserving Neural Echo v2 уже доказал, что пред-ASR улучшение возможно без участия
post-ASR cleanup: на sealed corpus кандидат выбран в `5/12` сессиях, удалены `41.940s` и `90`
remote-supported токенов при local-token retention `1.0`. На остальных `7/12` сессиях сработал
точный fallback.

Это лучший безопасный production результат, но не предел подавления. Селектор принимает только
небольшую долю independently supported окон, остаточный remote всё ещё виден mic ASR, а профиль
персонализирован и применим не ко всем акустическим режимам.

Pre-ASR Residual Echo Ceiling Map v1 затем объяснил остаток на 14 real sessions. Из `6869.306s`
actionable material evidence крупнейший класс требует улучшения alignment/echo path: `2443.222s`
(`35.567%`) в 9 сессиях. Multi-component separation занимает `2124.220s` (`30.923%`), Target-Me
model — `1258.702s` (`18.324%`). Неопределённый остаток составляет `9.216%`, поэтому решение
`READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3` прошло locked evidence gate.

Это меняет порядок работы. Ещё одна Target-Me модель сейчас не является первым обоснованным
экспериментом. Сначала надо проверить, насколько далеко production можно продвинуть за счёт
локальной time-varying задержки, нескольких эхотрактов и нелинейных remote bases, сохраняя текущий
строгий local-preservation guard.

Reviewed Speaker-Aware Meeting Memory v1 остаётся полезным и уже разблокированным продуктовым
шагом, но временно уступает критический путь этому audio-first пределу.

## Objective

За один bounded research-to-production цикл квалифицировать Alignment and Echo-Path Model v3:
либо продвинуть изолированный пред-ASR профиль, который существенно уменьшает подтверждённое remote
echo поверх production v2, либо доказать, что этот класс методов исчерпан и следующий необходимый
шаг — многокомпонентное разделение. Отрицательный результат должен закрыть весь заранее
зафиксированный candidate ladder, а не одну реализацию.

## Required Work

1. Считать frozen residual map и production v2.16 неизменяемыми входами. Проверить их SHA-256 и
   запретить повторную настройку map thresholds по результатам candidate.
2. До реализации зафиксировать candidate ladder, dev sessions, hard/sealed границы, direct-ASR
   метрики, runtime budget, thresholds и ровно одну допустимую bounded revision.
3. Реализовать локальный sub-window delay/drift estimator с устойчивостью к паузам, boundary и
   смене громкости. Сравнить его с whole-session delay без изменения authoritative remote.
4. Проверить bounded echo-path bank: несколько FIR/transfer hypotheses для изменяющегося положения,
   комнаты и усиления. Выбор должен опираться только на causal audio evidence внутри окна.
5. Добавить ограниченные nonlinear remote bases для coloration и мягкого clipping/distortion.
   Любая энергия, не объяснённая remote, должна сохраняться.
6. Разрешать residual suppression только на confirmed-remote/weak-local окнах. Double-talk,
   Target-Me uncertainty, other-local и boundary uncertainty должны выбирать production v2.
7. На locked dev сравнить каждый rung с v2 по direct whisper.cpp remote-forbidden токенам,
   protected Me, chronology, openings, double-talk, no-speech, runtime и determinism. Hard/sealed
   открывать только после полного dev pass.
8. Выпустить одно воспроизводимое решение: `PROMOTE_ALIGNMENT_OR_ECHO_MODEL_V3`,
   `READY_FOR_MULTI_COMPONENT_SEPARATOR` либо `CURRENT_RESOURCE_LIMIT_REACHED`. Публикация должна
   быть транзакционной и сохранять exact per-window или whole-session fallback.

## Acceptance Gates

- protected-local, opening acknowledgement, chronology и measured double-talk не хуже production
  v2; потеря подтверждённого слова `Me` запрещает promotion;
- candidate сокращает `alignment_or_echo_model_v3` residual относительно frozen map на заранее
  зафиксированную существенную величину в нескольких сессиях;
- whole-session drift, boundary transitions, gain changes и nonlinear remote coloration покрыты
  отдельными воспроизводимыми тестами;
- nearby `other_local` и Target-Me uncertainty никогда не разрешают агрессивный echo-only output;
- authoritative remote audio и text остаются неизменными;
- candidate даёт заранее зафиксированное существенное уменьшение остаточного ASR-visible remote
  относительно v2 на applicable scope;
- no-speech, headphones/low-leak и unsupported sessions выбирают безопасный результат;
- повторный запуск детерминирован, publication восстановима после прерывания;
- raw CAF, production v2 baseline, transcript evidence, notes и guarded export не меняются до
  полного promotion decision;
- итоговое решение покрывает delay/drift, echo-path bank, nonlinear bases и bounded residual
  suppression и не оставляет молча непроверенную доступную гипотезу этого класса;
- тесты, contracts, runbook, README, roadmap и OpsKarta актуальны перед commit и push.

## Stop Rules

- не ослаблять local-word, chronology, double-talk или speaker-attribution gates ради suppression;
- не менять residual-map classification или capability ordering по результатам candidate;
- не продолжать настройку на том же dev после одной заранее разрешённой bounded revision;
- не открывать hard/sealed corpus кандидату, который не прошёл supervised dev;
- не возвращаться к ещё одному маленькому Target-Me spectral mask, пока echo-path ladder не закрыт;
- не считать SI-SDR, AECMOS, exact remix или speaker similarity самостоятельным разрешением;
- не использовать cloud audio processing или непроверенный remote model download во время run;
- если ни одна доступная pretrained representation не проходит license/runtime/preflight, завершить
  `CURRENT_RESOURCE_LIMIT_REACHED`, а не подменять цель более слабой эвристикой.

## Safety Boundary

- no changes to capture, raw writer, authoritative remote, ordinary ASR model or Live Shadow;
- production v2 and `local_fir_role_masked` remain exact fallbacks;
- no post-ASR transcript mutation may justify audio promotion;
- no automatic voice identity, cross-session roster or external write;
- private enrollment and meeting audio remain local and ignored by source control.

## Deferred Product Step

Reviewed Speaker-Aware Meeting Memory v1 remains ready after this goal. Its explicit session-local
labels, anonymous fallback and evidence-bound export contract are preserved unchanged while the
audio frontier is active.
