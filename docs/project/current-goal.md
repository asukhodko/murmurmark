# Current Goal

Status: current

Updated: 2026-08-04

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the exact production
baseline; every missing artifact, incompatible acoustic mode or regression returns to its byte-exact
fallback.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Reference-Conditioned Target-Me Separation v2

OpsKarta nearest goal: Reference-Conditioned Target-Me Separation v2: обучить bounded speaker-query separator только на READY Target-Me Identifiability Corpus v1, выбрать candidate на dev, открыть hard только после immutable lock и проверить sealed meeting corpus; завершить PROMOTE либо точным DO_NOT_PROMOTE без ослабления Speaker-Preserving Neural Echo v2.

## Why This Is Next

Reference-Conditioned Target-Me Separation v1 proved a wide ideal-mask ceiling and bounded overfit,
but ended in `DO_NOT_PROMOTE`: one fixed enrollment and no independently labelled nearby speaker
made speaker attribution unidentifiable.

Target-Me Identifiability Corpus v1 has now closed that exact prerequisite with
`READY_FOR_TARGET_CONDITIONED_TRAINING`, fingerprint
`530cb0fd23503884d438bc24be10fff45610da1fb8fe710aad1b6b6cd992b2ce`:

- `4/2/2` split-disjoint non-target speakers;
- `1200/300/300s` full train/dev/hard mixtures;
- `490` rendered items, `980` paired query controls and `11` split-local enrollments;
- zero identity, source, enrollment and rendering-seed contamination;
- enrollment similarity margin median `0.433614221`;
- exact replay `2470/2470` and publication verification `2504/2504`.

The next uncertainty is therefore the model and promotion ladder, not missing supervision.

## Objective

Train and evaluate the smallest target-conditioned separator that must follow an enrollment query.
For one fixed mixture it must recover Target-Me under the correct private enrollment and recover the
known other-local speaker under the swapped enrollment, while accounting separately for measured
remote echo and the remaining local component.

The experiment remains isolated until every gate passes. No train/dev result may replace production
audio.

## Experiment Ladder

1. Freeze the READY corpus publication, policy, code revision, runtime and all model dependencies.
2. Reproduce the v1 architecture and old train/dev metrics without opening the new hard split.
3. Train paired correct/swap query rows from the new `train` split under deterministic seeds.
4. Select one bounded candidate using only `dev` speaker attribution, Target-Me, echo and remix
   gates.
5. Publish an immutable candidate lock before reading any `hard` target.
6. Open `hard` once and require unseen-speaker query adherence, quiet speech, double-talk,
   opening/backchannel and keyboard/background preservation.
7. Only after hard success, evaluate the sealed ordinary-meeting corpus against the byte-exact
   Speaker-Preserving Neural Echo v2 baseline.
8. Finish with one fingerprinted `PROMOTE` or `DO_NOT_PROMOTE` decision.

## Locked Safety Boundary

- train uses only `train`; candidate selection uses only `dev`;
- hard data is unavailable before candidate lock;
- sealed meetings cannot tune thresholds or model weights;
- the same mixture bytes must produce the correct source for both enrollment queries;
- mixture conservation alone receives no speaker-attribution credit;
- no local or remote transcript text may be deleted after ASR to claim echo improvement;
- missing model, enrollment, corpus or hash fails open to exact production fallback;
- post-ASR cleanup receives zero promotion credit.

## Acceptance Gates

Before hard unlock, dev must show:

- paired enrollment-swap attribution for every required family;
- Target-Me SNR median at least `12 dB` and improvement at least `3 dB`;
- remote-echo SNR median at least `8 dB` and remote-only attenuation at least `15 dB`;
- correct-vs-wrong query target quality margin with no speaker-swap collapse;
- exact reconstruction within `1e-5`, no clipping and no non-finite output;
- unchanged exact-target and exact-other controls.

Hard and sealed evaluation must then preserve:

- all protected local tokens, opening phrases and double-talk Target-Me speech;
- chronology, remote text, no-speech outcomes, notes evidence and guarded export;
- non-target local speech attribution under swapped enrollment;
- zero local-recall, order and speaker-attribution regressions;
- measurable pre-ASR remote reduction beyond or equal to production v2 with positive utility, not
  merely a different waveform.

## Decision

The goal ends in exactly one immutable result:

- `PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2`; or
- `DO_NOT_PROMOTE_REFERENCE_CONDITIONED_TARGET_ME_SEPARATION_V2` with the precise model, data or
  runtime ceiling.

Promotion may add a guarded pre-ASR candidate only for proven compatible speaker-playback sessions.
Every unsafe or inapplicable session keeps exact Speaker-Preserving Neural Echo v2 output.

## Definition Of Done

- frozen experiment policy, train/dev report, candidate lock, hard decision and sealed-corpus report
  exist with stable schemas and fingerprints;
- repeated deterministic runs match before hard access;
- negative fixtures cover wrong enrollment, absent target, other-local-only speech, remote-only,
  silence, clipping, missing artifacts and publication interruption;
- raw CAF and the Target-Me Identifiability corpus publication remain unchanged;
- full regression, planning, privacy and open-source checks pass;
- README, architecture, contracts, runbook, current goal, roadmap and OpsKarta record the measured
  outcome and next dependency;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- capture, Echo Guard topology or primary whisper.cpp replacement;
- cloud inference or upload of private audio;
- post-ASR duplicate deletion as promotion evidence;
- remote diarization, LLM synthesis and UI.

## Deferred Product Goal

Evidence Notes And Export v2 remains the next product handoff after this bounded audio experiment.
It continues to use whichever transcript profile is selected by the production gates.
