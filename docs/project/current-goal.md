# Current Goal

Status: current

Updated: 2026-08-06

The supported product path remains `murmurmark meeting -> first Ctrl-C -> authoritative result`.
Raw CAF, authoritative remote and Speaker-Preserving Neural Echo v2 remain immutable baselines. A
research separator may replace `mic_for_asr` only after audio, direct-ASR and corpus-wide safety
gates pass.

Roadmap status and dependencies live in `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`.
`scripts/check-planning-consistency.py` keeps the README, roadmap and OpsKarta wording aligned.

## Pre-ASR Target-Me Isolation Limit v1

OpsKarta nearest goal: Pre-ASR Target-Me Isolation Limit v1: после завершённого Stronger Offline Target-Speaker Separator Prerequisites v1 с READY_FOR_STRONGER_SEPARATOR_QUALIFICATION провести SepFormer Four-Stem Target-Me Qualification v1; материализовать замороженное расширение train/dev с 12/4 split-disjoint non-target speakers, проверить pinned offline SepFormer через WavLM paired-query assignment, scale recovery, exact four-stem reconstruction и production-v2 fallback; не открывать future-hard, sealed или direct ASR до immutable dev pass; завершить READY_FOR_STRONGER_SEPARATOR_HARD_TEST, DO_NOT_ADVANCE_STRONGER_SEPARATOR либо CURRENT_RESOURCE_LIMIT_REACHED с тестами, актуальными документами, roadmap и OpsKarta, коммитом и push.

## North Star

For a supported speaker-playback session the canonical microphone input to primary ASR must:

- retain every recognizable word spoken by the target user `Me`;
- contain no recognizable authoritative remote content;
- keep nearby non-target speech out of `Me`;
- preserve `other_local` and unexplained residual as separate evidence;
- return to the exact production baseline whenever evidence is insufficient.

This is an operational word, role and chronology criterion. Low waveform residual, SI-SDR or speaker
similarity alone is not success.

## Evidence So Far

Speaker-Preserving Neural Echo v2 is the guarded production plateau: its sealed decision selected
candidate audio in `5/12` sessions, removed `41.940s` and 90 remote-supported tokens with local-token
retention `1.0`; the other `7/12` sessions used exact fallback.

Residual Echo Ceiling Map v1 measured `6869.306s` actionable evidence. Alignment/echo-path represented
`2443.222s` (`35.567%`), multi-component separation `2124.220s` (`30.923%`) and Target-Me modeling
`1258.702s` (`18.324%`). Alignment/Echo-Path v3 then exhausted its bounded ladder with
`READY_FOR_MULTI_COMPONENT_SEPARATOR` without earning production access.

Multi-Component Residual Separator v1 preserved exact reconstruction and speaker-query identity,
but dev reached only `5.561 dB` Target-Me SNR, `4.443 dB` other-local SNR, `6.803 dB` absent-query
attenuation and `-1.545 dB` residual SNR. It completed `READY_FOR_STRONGER_LOCAL_SEPARATOR`; hard,
sealed and direct ASR remained closed.

Stronger Offline Target-Speaker Separator Prerequisites v1 is now complete with
`READY_FOR_STRONGER_SEPARATOR_QUALIFICATION`. It froze a `12/4/4` split-disjoint non-target identity
plan and selected Apache-2.0 SpeechBrain SepFormer at pinned model revision
`eb43c5bfbb2aa654630adbf849373bcec0a20ed4`. Two network-blocked probes produced the same tensor
SHA-256, used about `687 MB` peak RSS and stayed inside the four-thread background budget.

That decision proves readiness and local executability, not audio quality. SepFormer still produces
two anonymous 8-kHz speech estimates with indeterminate scale. WavLM assignment, Russian short-word
retention, quiet Target-Me, nearby speakers and exact fallback must now be qualified on train/dev.

## Objective

Run one bounded train/dev qualification of the pinned SepFormer four-stem adapter. Materialize only
the frozen train/dev expansion, keep the backbone and evidence hashes fixed, assign the Target-Me
stem using paired WavLM enrollment, restore source scale, retain an exact residual remainder and
stop before hard, sealed or direct ASR unless every immutable dev gate passes.

## Required Work

1. Freeze the prerequisite decision, production v2, model/runtime files and existing corpus hashes.
2. Materialize deterministic expanded train/dev mixtures for 12/4 non-target speakers; do not read
   future-hard audio or use ordinary meetings as inferred labels.
3. Add a frozen SepFormer inference adapter: 16 kHz local mixture to 8 kHz separator and back,
   least-squares scale recovery, exact residual remainder and sample-exact reconstruction.
4. Assign both anonymous speech stems against positive and negative WavLM enrollment. Calibrate any
   margin on train only and lock it before dev.
5. Evaluate quiet/absent Target-Me, nearby speakers, double-talk, openings, keyboard and office noise.
6. Preserve exact production v2 fallback for weak margins, missing models, non-finite output, more
   than two local speakers, reconstruction failure or unsupported duration.
7. Run one immutable dev pass. Do not tune from future-hard, sealed, transcript cleanup or ASR text.
8. Finish with `READY_FOR_STRONGER_SEPARATOR_HARD_TEST`,
   `DO_NOT_ADVANCE_STRONGER_SEPARATOR` or `CURRENT_RESOURCE_LIMIT_REACHED`.

## Acceptance Gates

- every expanded identity is split-disjoint and every rendered mixture has exact component truth;
- Target-Me SNR median is at least `8 dB` and improves at least `3 dB` over production v2;
- other-local and residual SNR medians are at least `8 dB` and `6 dB`;
- paired query margin is at least `4 dB`, query collapse at most `5%` and absent-query attenuation
  at least `12 dB`;
- quiet Target-Me, openings and ordinary double-talk meet their locked family gates;
- reconstruction error stays at most `1e-5`, with zero clipped or non-finite outputs;
- missing or uncertain evidence yields exact production fallback;
- runtime remains bounded under the background resource policy;
- future-hard, sealed, direct ASR, raw CAF, production v2 and transcripts remain untouched.

## Stop Rules

- do not fine-tune or replace the backbone before the frozen-backbone control is measured;
- do not weaken gates after observing dev;
- do not assign `Me` from separator output without independent WavLM margin;
- do not treat 8-kHz SI-SDR as evidence that Russian words survived;
- do not open future-hard, sealed or direct ASR after a failed dev gate;
- if runtime, data or model integrity fails, publish the resource-limit decision and stop.

## Safety Boundary

- capture, raw writer, authoritative remote, ordinary whisper.cpp and Live Shadow do not change;
- production v2 and `local_fir_role_masked` remain exact fallbacks;
- private enrollment, models and meeting audio stay local and ignored by source control;
- no cloud audio processing, external writes, automatic voice identity or cross-session roster;
- Reviewed Speaker-Aware Meeting Memory v1 remains deferred until this audio frontier terminates.
