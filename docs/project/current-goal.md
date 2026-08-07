# Current Goal

Updated: 2026-08-07

This document expands the single executable goal from
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Remote Speaker Residual Evidence v4

OpsKarta nearest goal: Remote Speaker Residual Evidence v4: сократить оставшиеся 851 remote words / 598.240 секунды explicit `unknown` после promoted Coverage v3, не ослабляя его similarity/margin gates и не меняя существующие v2/v3 speaker labels, selected words, timestamps, `Me`, роли, порядок или aggregate fallback; разбирать причины отдельно, начиная с `similarity_below_threshold` и `embedding_unavailable`, проверять bounded speech-aware окна и независимое enrollment agreement, а тяжёлый локальный backend допускать только как pinned offline-кандидат с явной лицензией, моделью и воспроизводимостью; conflicting frames и protected overlap оставлять unknown без нового независимого доказательства; продвинуть изолированный профиль только при снижении оставшихся unknown words и seconds минимум на 20%, attributed-only B-cubed F1 и pairwise precision не ниже 0.95, полном conservation и прохождении 1x1/group/boundary/fallback и Transcript Perfection gates; иначе выпустить воспроизводимый DO_NOT_PROMOTE с cause-specific evidence ceiling; capture, Echo Guard, основной ASR, local mic diarization, export и optional derivatives не менять; добавить tests/report, актуализировать README, contracts, runbook, roadmap и OpsKarta, закоммитить и отправить в origin/main.

## Why Now

Remote Speaker Coverage v3 reached `PROMOTE`: it recovered 368 words / `199.533s`, raised
attributable remote speech from `0.919071` to `0.939312`, and kept B-cubed F1 `0.962171` and
pairwise precision `0.961675`. The unified scorecard now ranks the remaining 851 words /
`598.240s` as the largest transcript residual.

The residue is no longer homogeneous:

- `similarity_below_threshold`: 113 words / `191.081s`;
- `conflicting_frame_speakers`: 233 words / `131.497s`;
- `embedding_unavailable`: 211 words / `130.804s`;
- `margin_below_threshold`: 278 words / `110.882s`;
- `protected_remote_overlap`: 16 words / `33.536s`.

## Objective

Add independent evidence for the causes that can be resolved safely. Do not turn uncertainty into a
speaker label merely by lowering v3 thresholds. The goal ends with either a promoted isolated v4
profile or a measured `DO_NOT_PROMOTE` that states which causes remain unsupported locally.

## Required Work

1. Freeze the v3 queue, cause map and all source hashes; keep private text and names out of tracked
   artifacts.
2. Build cause-specific fixtures and ceilings. Evaluate speech-aware bounded windows first for
   similarity and missing embeddings.
3. Require agreement with existing session-local enrollment. Never create identity from a single
   weak embedding or infer a human name.
4. Evaluate a heavier local diarization backend only if the bounded path cannot meet the target and
   runtime, model, license and offline installation are pinned.
5. Replay the six-session speaker corpus, private reference, five internal boundaries and all
   Transcript Perfection dimensions.
6. Publish one decision, testing snapshot and refreshed residual ranking; update active planning,
   commit and push.

## Acceptance Gates

- remaining unknown words and seconds each fall by at least `20%`, or decision is `DO_NOT_PROMOTE`;
- attributed-only B-cubed F1 and pairwise precision stay `>= 0.95`;
- every v2/v3 attributed word keeps its speaker;
- selected words, text, timestamps, `Me`, roles, overlap and raw audio remain exact;
- 1x1, group and 5/5 boundary controls pass;
- conflicting frames and protected overlap remain unknown without independent evidence;
- missing model, stale lineage or failed gate returns exact promoted v3 fallback;
- repeated offline runs are deterministic and Transcript Perfection stays green.

## Safety Boundary

- no capture, Echo Guard, primary ASR, retention or export change;
- no cross-session voice identity or voice-derived human names;
- no local mic multi-speaker work without a real labeled scenario;
- no cloud service or implicit model download;
- no promotion of notes, summaries or other derivative work.

## Previous Goal Result

Remote Speaker Coverage v3 completed with `PROMOTE`:

- recovered 368 words / `199.533s` from the v2 unknown queue;
- unknown reductions: words `30.1887%`, seconds `25.0113%`;
- attributable speech `0.939312`, B-cubed F1 `0.962171`, pairwise precision `0.961675`;
- all conservation, 1x1, group, boundary, fallback and deterministic replay gates passed;
- Transcript Perfection Corpus remains 12/12 valid and now ranks `598.240s` first.

## After This Goal

1. Re-run Transcript Perfection Corpus and take its highest remaining release blocker.
2. Qualify Speaker-Resolved Transcript Default only when residual gates make the standard view
   honest and useful with exact aggregate fallback.
3. Open local mic multi-speaker diarization only after a real labeled scenario exists.

Raw CAF and batch output remain authoritative. Live Shadow remains advisory.
