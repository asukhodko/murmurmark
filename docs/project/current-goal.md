# Current Goal

Updated: 2026-08-08

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Segment-Context Remote Speaker Attribution v1

OpsKarta nearest goal: Segment-Context Remote Speaker Attribution v1: до изменений topology
заморозить новый private exact-scripted hard-v3 с новыми scripts, renderer voices, отдельным known
enrollment, source stems, word/speaker/timestamp truth и SHA-256; считать Truth Lab v1 и открытый
hard-v2 только development evidence; сравнить не более трёх заранее объявленных segment-context
topology, которые независимо строят silence/embedding change points, получают speaker evidence на
длинных однородных интервалах и лишь затем консервативно проецируют anonymous ID на слова; mixed,
short unsupported и конфликтующие интервалы оставлять `unknown`/`mixed`; выбрать candidate только
на development, заморозить config и открыть hard-v3 ровно один раз; завершить
`PROMOTE_LAB_CANDIDATE` только при exact word conservation, deterministic replay, B-cubed F1 и
pairwise precision >=0.98, known-speaker recall >=0.98, boundary recall 100%, zero open-set false
attribution и non-regression Coverage v3, иначе выпустить `DO_NOT_PROMOTE_SEGMENT_CONTEXT`; не
менять selected transcript, Coverage v3, raw CAF, primary ASR или Echo Guard и не переносить
synthetic labels в real sessions; добавить CLI, тесты и corpus report, обновить README, contracts,
runbook, current-goal, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Duration-Aware v2 established a clean boundary: conservative word-level fusion can keep measured
precision at `1.0` and abstain on every unseen open-set voice, but it retained only `55.1402%` of
known hard-v2 words and recovered `32.1429%` of speaker boundaries. Individual short-word embeddings
do not contain stable enough identity evidence.

Longer context is the shortest plausible route forward. Speaker evidence should be measured over a
homogeneous span, while a separate change-point detector decides where that evidence may be shared.
Word timestamps then receive a label only from a supported containing span.

## Objective

Determine whether segment-context evidence can recover short remote words and internal speaker
changes without weakening open-set precision. End with `PROMOTE_LAB_CANDIDATE` or reproducible
`DO_NOT_PROMOTE_SEGMENT_CONTEXT`.

## Required Work

1. Freeze hard-v3 before candidate implementation, with new voices/scripts and disjoint enrollment.
2. Treat all v1 and hard-v2 truth as development; never present them as fresh held-out evidence.
3. Predeclare no more than three segment-context candidates.
4. Detect candidate boundaries from silence and embedding changes only, without speaker truth.
5. Embed context windows long enough for stable identity, then assign words by temporal containment.
6. Keep overlap, unsupported short spans, open-set and backend disagreement fail-open.
7. Select on development, freeze one candidate, and consume hard-v3 once.
8. Add deterministic replay, privacy checks, CLI, tests and aggregate corpus reporting.

## Acceptance Gates

- exact word conservation, direct truth coverage and zero-sample stem reconstruction error;
- hard-v3 was frozen before candidate code and not used for tuning;
- B-cubed F1 and pairwise precision at least `0.98`;
- known-speaker recall at least `0.98`;
- every evaluated speaker boundary recovered;
- zero open-set false attribution and every mixed word fails closed;
- Coverage v3 control and production hashes do not regress;
- deterministic replay and private-safe public artifacts.

Any failed gate yields `DO_NOT_PROMOTE_SEGMENT_CONTEXT`. Post-hard threshold relaxation is forbidden.

## Safety Boundary

- selected transcript, Coverage v3, raw CAF, primary ASR and Echo Guard remain unchanged;
- synthetic truth cannot label real sessions;
- no cloud speech service or cross-session human identity;
- longer context cannot overwrite mixed or unsupported words;
- real residual review remains blocked without direct blind truth.

## Previous Goal Result

Duration-Aware Remote Speaker Attribution v2 completed with `DO_NOT_PROMOTE_TOPOLOGY`:

- 4 unopened-then-one-shot hard-v2 scenarios, 125 words, 4 enrolled and 2 open-set voices;
- three candidates selected only on v1 development evidence;
- conservative fusion development: B-cubed `0.912728`, known recall `0.913043`;
- hard-v2: B-cubed `0.499381`, pairwise precision `1.0`, known recall `0.551402`, boundaries
  `9/28`, open-set false attribution `0`;
- Coverage v3 control was weaker on hard-v2, but both tracks remained far below recall gates;
- exact word/stem conservation, one-shot ledger, privacy and replay gates passed;
- production remained unchanged.

## After This Goal

1. A passing lab candidate may proceed only to bounded real-session audit with direct reference.
2. A failed candidate closes embedding-based segment projection at the current local model ceiling.
3. Per-speaker names remain an explicit review layer over anonymous IDs.
4. Local-mic multi-speaker attribution waits for a real consented scenario.
