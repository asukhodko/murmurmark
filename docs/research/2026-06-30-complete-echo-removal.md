# Complete Echo Removal Research

Status: research notes and experiment plan
Date: 2026-06-30

Related:

- [Echo Guard architecture](../architecture/echo-suppression.md)
- [Echo Guard delay lab](../runbooks/echo-guard-lab.md)
- [Mic remote bleed reduction](../backlog/mic-remote-bleed-reduction.md)
- [ADR-0010](../adr/0010-use-preserve-local-fir-for-current-echo-guard.md)

## Problem

MurmurMark records two raw tracks:

```text
audio/mic/*.caf
audio/remote/*.caf
```

`remote` is the clean far-end/system audio. `mic` is the real microphone signal. When the meeting is
played through speakers, `mic` can contain:

- real local speech;
- delayed and room-colored remote speech;
- speaker/microphone nonlinear distortion;
- macOS/browser gain changes;
- background noise;
- overlap between local and remote speech.

The current `local_fir` Echo Guard reduces remote bleed and protects quiet local speech, but it does
not make the leaked remote speech disappear completely. That residue still creates real product
cost: review queues, wrong `Me` turns, order risks, local-recall doubts and export blockers.

The new research target is stronger:

```text
Remove remote-derived speech from mic-derived ASR input so completely that remote content cannot be
recognized as Me, while preserving every phrase actually spoken into the microphone.
```

Raw CAF files remain immutable. All experiments write derived artifacts only.

## What "Complete" Can Mean

Waveform-perfect removal is not a realistic invariant in all cases. During double-talk, the observed
`mic` waveform is one mixture. If local and remote speech overlap in the same time-frequency regions,
and the echo path is nonlinear or clipped, there may be no unique mathematical answer without a
speech prior.

For MurmurMark, "complete" should mean product-complete, not physics-perfect:

1. **ASR-complete remote removal.** Running ASR on the cleaned mic must not recover remote words as
   `Me`.
2. **Local speech preservation.** Local words must not disappear, especially greetings, backchannels,
   short confirmations and overlap comments.
3. **Auditable uncertainty.** Any segment that cannot satisfy both conditions must be flagged rather
   than silently muted.
4. **Derived-only processing.** Raw mic and remote CAF stay as the audit record.

This definition is stricter than current `preserve_local`, but it still admits "needs review" when
the signal is genuinely ambiguous.

## Why Current Local FIR Is Not Enough

`local_fir` models the echo as a local linear filter:

```text
mic ~= local_speech + FIR(remote)
```

That works when:

- delay is stable;
- the speaker/microphone path is approximately linear;
- there are nearby remote-only windows for fitting;
- the room tail is short enough for the chosen FIR;
- double-talk is not too dense.

It fails or leaves residue when:

- the echo path changes faster than the window cadence;
- room reverberation tail is longer than the FIR;
- speakers clip or compress;
- the laptop applies automatic gain control;
- the remote path has nonlinear coloration;
- there are too few clean remote-only windows;
- local speech overlaps the remote leak.

The current preserve-local policy is the correct default for not losing speech, but it intentionally
leaves remote residue in ambiguous regions.

## Constraint Model

Any future engine must preserve these invariants:

- no raw CAF mutation;
- no cloud dependency by default;
- no automatic deletion of uncertain local speech;
- no promotion to `mic_for_asr.wav` without gates;
- all per-segment decisions recorded;
- ASR/transcript layers still remain a safety net.

## Follow-Up Consultation Synthesis

The strongest additional conclusion is that MurmurMark should stop treating this as "make
`local_fir` a little better". The realistic target is a hybrid cascade:

```text
remote + mic
  -> linear_echo_model
  -> residual_echo_suppressor
  -> target_speaker_branch for hard overlap
  -> safety_selector
  -> ASR and transcript gates
```

The linear stage should stay explainable and auditable. It provides delay curves, echo estimates and
failure evidence. The residual stage handles the part that linear filters are bad at: speaker
nonlinearity, clipping, AGC, room coloration and fast path changes. The target-speaker branch is not
the first default path, but it is the most plausible rescue path for group calls and double-talk,
where the product question becomes "is this the local speaker?" rather than "can remote be perfectly
subtracted?".

Working rule:

```text
Do not promote any stronger cleanup because it sounds cleaner. Promote only when remote-token leakage
falls and local-word recall does not regress.
```

This keeps the project aligned with the real failure mode: remote words becoming `Me`.

## Approach Families

### 1. Stronger Classical Offline AEC

This is the closest continuation of current Echo Guard.

Current `local_fir` is a small local FIR subtractor. A stronger offline AEC should use the fact that
MurmurMark is not real-time constrained:

- non-causal multi-pass delay estimation;
- drift-aware alignment instead of one median delay;
- long-tail partitioned frequency-domain adaptive filters;
- per-band adaptation rates;
- filter banks for multiple echo states;
- robust double-talk detection;
- residual echo suppression after linear subtraction;
- candidate selection by ASR and local-recall gates.

Promising variants:

1. **Partitioned block frequency-domain NLMS/MDF.**
   Long echo tails can be modeled without huge time-domain matrices. Offline processing can run
   multiple tail lengths, e.g. 80 ms, 160 ms, 320 ms, and choose per segment.

2. **Multi-hypothesis echo path bank.**
   Keep several echo filters trained on different acoustic states: quiet speaker, loud speaker,
   clipped speaker, early-call path, late-call path. For each segment, choose the filter that best
   explains remote-only leakage while preserving local evidence.

3. **Nonlinear echo basis.**
   Model not only `remote`, but also transformed references:

   ```text
   remote
   tanh(gain * remote)
   clipped(remote)
   remote^2 signed
   band-limited remote
   compressor(remote)
   ```

   Then fit a regularized multi-basis echo model. This approximates speaker distortion and laptop
   processing without a full neural model.

4. **Residual echo suppressor.**
   After subtracting `echo_hat`, compute a spectral mask from:

   - raw mic spectrum;
   - remote spectrum;
   - `echo_hat` spectrum;
   - residual spectrum;
   - speaker-state features.

   Remote-only frames can be suppressed aggressively; double-talk frames use a local-preserving
   mask.

5. **Offline oracle search.**
   Generate multiple candidates per segment and choose by gates:

   ```text
   candidate = subtractor(tail, delay, nonlinear_basis, strength, residual_mask)
   score = remote_asr_leak_penalty + local_loss_penalty + artifact_penalty
   ```

   This is too expensive for real-time calls, but acceptable for post-meeting processing.

Expected value: high. This is the best next engineering step because it keeps the current topology
and needs no training data.

Risk: still cannot solve hard double-talk where remote and local speech overlap strongly.

### 2. Hybrid DSP + Neural Residual Echo Suppression

The likely production shape is not "replace FIR with a neural net". A better shape is:

```text
remote + mic
  -> alignment
  -> adaptive linear / nonlinear echo estimate
  -> residual echo suppressor
  -> local-preservation gate
  -> ASR/text validation
```

The neural layer should not be asked to rediscover alignment and linear echo cancellation from
scratch. It should receive:

- mic;
- remote;
- aligned remote;
- echo estimate;
- residual after subtraction;
- local/remote/double-talk state;
- optional target-speaker embedding for `Me`.

Candidate neural tasks:

1. **Residual echo mask.**
   Predict a time-frequency mask that removes leftover far-end speech after classical AEC.

2. **Near-end speech estimator.**
   Predict local speech directly from `[mic, remote, echo_hat]`.

3. **Two-output model.**
   Predict both `local_speech` and `remote_leak`. Penalize any remote-like energy in
   `local_speech`, and penalize local-speaker loss.

4. **Confidence model.**
   Predict whether the cleaned segment is safe enough for ASR, otherwise force review.

Training data can be built from:

- Microsoft AEC Challenge datasets;
- MurmurMark remote-only windows as real echo examples;
- MurmurMark local-only windows as local speech examples;
- synthetic mixtures of real echo examples with local speech islands;
- room impulse response augmentation;
- speaker distortion / clipping / compressor augmentation.

Expected value: very high after enough data. This is the most plausible path to product-complete
remote removal.

Risk: a neural suppressor can hallucinate or damage speech. It must be gated by ASR/local-recall
checks and should initially be an experimental derived candidate.

### 3. Reference-Conditioned "Forbidden Source" Separation

Standard speech enhancement removes generic noise. MurmurMark has a stronger clue: the unwanted
source is known exactly as `remote`.

This suggests a model trained to say:

```text
Given mic mixture and forbidden remote reference, output everything in mic that is not remote-derived speech.
```

This differs from ordinary denoising:

- remote speech is not "noise-like";
- remote may be clearer than local speech;
- remote and local are both speech;
- the model must suppress semantic content that matches the reference even when timbre, delay and
  room response differ.

Possible architectures:

- dual-encoder model: one encoder for `mic`, one for `remote`, cross-attention between them;
- complex spectrogram U-Net with remote-conditioned masks;
- Conv-TasNet/SepFormer-style separator conditioned by remote reference;
- neural Kalman/adaptive filter with learned residual suppression;
- contrastive objective: cleaned mic must be dissimilar to remote and similar to local references.

Expected value: very high, but requires training and evaluation.

Risk: no ready-made model is likely to solve MurmurMark's exact local/offline/Russian-ASR objective
without adaptation.

### 4. Target-Speaker Extraction for `Me`

Instead of removing remote, extract the local speaker.

MurmurMark sessions usually contain local-only fragments. Those can become an enrollment sample for
the local user. A target-speaker extractor can then preserve speech matching the local speaker and
suppress everything else, including remote leakage.

Pipeline idea:

```text
local-only islands
  -> local speaker embedding
mic mixture + local embedding + remote reference
  -> target Me extractor
  -> local-preserving ASR candidate
```

This is related to VoiceFilter-style target-speaker extraction. It is promising because "Me" is a
stable identity across the whole session, while `Colleagues` may contain many voices.

Expected value: high for 1x1 and group meetings where the local user's voice has enough enrollment
material.

Risks:

- quiet/short sessions may not provide enough local enrollment;
- overlapped speech can still be hard;
- model may suppress local speech when the user's voice is altered by microphone position or
  emotion;
- diarization-style identity errors would be dangerous if used without gates.

### 5. ASR-Level Remote-Forbidden Decoding

Even if waveform cleaning is imperfect, transcript construction can be stricter:

1. Transcribe `remote`.
2. Transcribe multiple mic candidates:
   - raw;
   - local FIR;
   - stronger AEC candidate;
   - target-speaker candidate.
3. Align mic tokens to remote tokens.
4. Drop or mark mic tokens that are recoverable from remote unless local evidence is strong.

This is not echo removal. It is role-safe transcript construction. But it can deliver the user-facing
goal: remote content must not appear as `Me`.

Current MurmurMark already does parts of this. The next level would be token-level, not just
utterance-level:

- forced alignment for remote and mic;
- token overlap with time/delay uncertainty;
- confidence that a mic token is local-only;
- local-speaker embedding score per token window;
- review item only for tokens where sources disagree.

Expected value: high as a safety net.

Risk: it cannot produce a clean mic waveform and may hide true local repetitions if not careful.

### 6. Generative Speech Restoration

After removing remote-dominant time-frequency regions, a model could reconstruct local speech in
damaged overlap regions.

This is an invention-track idea, not a default product path:

- use only when a local speaker model confirms local speech is present;
- mark output as reconstructed;
- never use reconstructed content as factual evidence without ASR cross-checks;
- keep original clips for audit.

Expected value: unknown.

Risk: hallucination. This is dangerous for meeting memory unless every generated token is marked as
uncertain.

### 7. Capture and Physical Mitigations

These are not algorithmic echo removal, but they change the problem:

- headphones;
- lower speaker volume;
- directional microphone;
- external headset mic;
- macOS voice-processing path when available;
- recording app-owned processed mic if the meeting app exposes it;
- calibration before meeting with a known remote test signal.

They can reduce the amount of required cleanup, but MurmurMark should not depend on them for the
normal path.

## Most Promising MurmurMark Plan

### Phase A: Define Product-Level Complete Removal

Before another engine, define gates that measure the actual product failure:

```text
remote_token_leak_rate_in_me == 0 for high-confidence remote-only regions
local_word_recall >= current preserve-local baseline
no increase in local-recall blockers
no new transcript-order blockers
no new clipping/dropout/artifact flags
```

Add segment categories:

```text
remote_only
local_only
double_talk_light
double_talk_heavy
unknown
```

`remote_only` should be aggressively removable. `local_only` must remain untouched. `double_talk`
is where new methods must prove value.

### Phase B: Stronger Offline AEC v2

Implement an experimental engine, not a default:

```bash
murmurmark preprocess "$SESSION" --echo clean --echo-engine offline_aec_v2
```

Candidate internals:

- drift-aware delay curve;
- 160 ms and 320 ms partitioned filters;
- nonlinear remote basis;
- multi-hypothesis echo path bank;
- residual echo spectral mask;
- per-segment candidate scoring;
- ASR leak audit before promotion.

Outputs:

```text
derived/preprocess/audio/mic_clean_offline_aec_v2.wav
derived/preprocess/audio/echo_hat_offline_aec_v2.wav
derived/preprocess/audio/mic_for_asr_offline_aec_v2.wav
derived/preprocess/echo/offline_aec_v2_report.json
derived/preprocess/echo/offline_aec_v2_segments.jsonl
derived/preprocess/echo/offline_aec_v2_candidates.jsonl
```

Promotion rule:

```text
Never promote only on ERLE. Promote only when remote ASR leak falls and local recall does not regress.
```

#### Minimal v0 Shape

The first implementation should be a diagnostic lab, not a new default engine.

1. **Delay trajectory.**
   Estimate a piecewise-smoothed delay curve rather than a single median delay:

   ```text
   analysis window: 250..500 ms
   hop: 50..100 ms
   delay candidates: around the current session delay, with negative delay allowed
   smoothing: Viterbi-style transition penalty or simpler dynamic programming
   ```

   If this stage is unstable, later AEC stages are measuring the wrong problem.

2. **Long-tail partitioned linear stage.**
   Start with an MDF/PF-NLMS-style block frequency-domain filter. It is easier to debug than
   Kalman/RLS and is the natural next step beyond the current short local FIR. Test at least:

   ```text
   tail_ms: 80, 160, 320
   ```

3. **Nonlinear auxiliary references.**
   Fit not only `remote`, but a small bank of transformed references:

   ```text
   remote
   band-limited remote
   clipped(remote)
   tanh(remote)
   soft-compressed(remote)
   remote * abs(remote)
   ```

   This is a cheap way to test whether the remaining leak is caused by nonlinear speaker/system
   processing before training a neural model.

4. **Reference-aware residual mask.**
   Add a conservative spectral suppressor after the linear estimate. The first version can be
   rule-based:

   ```text
   inputs: |mic|, |remote|, |echo_hat|, |residual|, coherence, delay confidence, speaker_state
   output: suppression gain per time-frequency region
   policy: strong only in remote-only; mild or no suppression in double-talk/local-only
   ```

5. **Candidate ranking.**
   Generate several derived candidates per segment and rank them by product metrics:

   ```text
   score = remote_token_leak_penalty + local_word_loss_penalty + artifact_penalty
   ```

Artifacts for `offline_aec_v2_v0`:

```text
derived/preprocess/audio/mic_clean_linear_v2.wav
derived/preprocess/audio/mic_clean_residual_v2.wav
derived/preprocess/audio/echo_hat_linear_v2.wav
derived/preprocess/audio/echo_hat_aux_basis_v2.wav
derived/preprocess/echo/offline_aec_v2_delay_curve.jsonl
derived/preprocess/echo/offline_aec_v2_window_metrics.jsonl
derived/preprocess/echo/offline_aec_v2_asr_leak_report.json
derived/preprocess/echo/offline_aec_v2_near_end_preservation_report.json
```

### Phase C: Neural Residual Echo Suppression Spike

Create a separate helper:

```bash
scripts/train-echo-residual-suppressor.py
scripts/apply-echo-residual-suppressor.py "$SESSION"
```

First model:

- input features: mic, remote, aligned remote, echo_hat, residual, speaker_state;
- target: local speech mask or near-end speech waveform;
- initial training: AEC Challenge + synthetic MurmurMark mixtures;
- first use: shadow candidate only;
- output never becomes default until corpus gates prove it.

The first neural model should be narrow: estimate echo posterior and preserve-near-end confidence,
not hallucinate a new clean speech signal. A useful output shape is two confidence maps:

```text
g_echo(t, f)
g_local(t, f)
```

The final gain should still be selected by explicit rules and corpus gates.

### Phase D: Target-Me Extractor Spike

Build a local speaker enrollment from high-confidence local-only islands:

```text
speaker_embedding_me
  <- local_only islands from mic_raw/mic_clean
```

Then run a target-speaker extractor candidate:

```text
input: mic, speaker_embedding_me, optional remote reference
output: mic_target_me.wav
```

This is especially promising for group calls because `remote` may contain many speakers while `Me`
is one stable speaker.

### Phase E: Token-Level Remote-Forbidden Transcript

Even with better audio, keep transcript safety:

- run ASR on all candidates;
- align remote and mic tokens;
- mark any mic token explainable by remote;
- keep local-only token islands;
- route ambiguous double-talk to review.

This protects the product if audio cleaning leaves faint but recognizable remote speech.

## Experiment Matrix

Use the existing real corpus plus synthetic fixtures.

Required session classes:

- 1x1 with strong remote bleed;
- group sync with many remote speakers;
- local speaker mostly silent;
- local speaker frequently overlapping;
- clean/exportable session as no-regression;
- intentionally bad speaker volume session.

For each candidate engine, write:

```text
echo_reduction_db
remote_only_asr_tokens_before/after
remote_token_leak_rate_in_me
local_only_word_recall
double_talk_local_recall
new_needs_review_count
new_order_risk_count
artifact_flags
promotion_decision
```

Do not trust only ERLE/PESQ-style metrics. AEC Challenge papers explicitly warn that common
objective metrics can correlate poorly with subjective quality in realistic noisy/reverberant
conditions.

Metric groups:

1. **Echo-risk metrics**
   - `remote_token_leak_rate`;
   - `remote_phrase_leak_rate`;
   - `harmful_remote_seconds_in_me`;
   - residual coherence between cleaned mic and remote.

2. **Local-preservation metrics**
   - `local_only_word_recall`;
   - `opening_ack_recall`;
   - `double_talk_local_recall`;
   - `dropout_rate`;
   - `artifact_rate`.

3. **Acoustic diagnostics**
   - ERLE on remote-only windows only;
   - delay consistency error;
   - AECMOS or equivalent non-intrusive quality proxy if available;
   - separate residual-echo and desired-speech preservation scores on curated clips.

Hard gates:

```text
local_only_word_recall >= baseline - 0.02
opening_ack_recall >= baseline
remote_token_leak_rate < baseline
harmful_remote_seconds_in_me < baseline
no growth in action/decision review blockers
no severe artifact/dropout increase
```

Soft gates:

```text
ERLE improves on remote-only windows
residual coherence with remote decreases
manual/auditor clips show fewer remote duplicates
review burden decreases
```

## Hypotheses

### H1: Longer partitioned filters will reduce residual remote more than current 80 ms FIR.

Reason: laptop speaker/room tail may exceed current tail. Speex documentation gives a small-room
example where a 100 ms tail corresponds to roughly one third of a 300 ms reverberation time.

Test:

- run 80/160/320 ms tails;
- compare remote-only ASR leak and local recall;
- reject if double-talk damage rises.

### H2: Nonlinear remote basis explains the residue current FIR cannot subtract.

Reason: loudspeakers, AGC and clipping create echo components not representable as a linear filtered
remote signal.

Test:

- fit basis-expanded echo model on remote-only windows;
- compare residual coherence and ASR leak with linear FIR.

### H3: Residual echo suppression after linear AEC is more useful than stronger subtraction alone.

Reason: adaptive filters leave residual echo; speech-aware masks can suppress leftover remote in
remote-only and light double-talk regions.

Test:

- classical AEC candidate;
- AEC + spectral residual mask;
- AEC + neural residual mask;
- evaluate local loss.

### H4: A local target-speaker extractor can preserve `Me` better than remote subtraction in double-talk.

Reason: double-talk is underdetermined for pure subtraction, but `Me` has a stable speaker identity.

Test:

- enroll from local-only islands;
- run target-speaker extraction on overlap clips;
- compare local word recall and remote token leakage.

### H5: A remote-conditioned neural model can learn "this exact remote content is forbidden".

Reason: remote is known, not a generic noise class. Cross-attention over `remote` and `mic` should
help suppress remote-derived speech even when colored by room acoustics.

Test:

- train synthetic reference-conditioned model;
- include real remote-only echo examples from MurmurMark;
- validate on sessions not used in training.

### H6: Transcript-level token suppression remains necessary even after stronger audio cleaning.

Reason: faint residual speech can still be recognized by ASR; waveform quality and transcript role
safety are different goals.

Test:

- run ASR on cleaned candidates;
- count remote tokens that appear in `Me`;
- keep token-level remote-forbidden reconciliation as final gate.

## Recommended Next Goal

The next implementation goal should not be "install another denoiser". It should be:

```text
Echo Guard Complete Removal v0:
Build an offline experimental AEC v2 lab that creates multiple stronger derived mic candidates
using long-tail adaptive filtering, nonlinear remote bases and residual echo masking, then ranks
them by ASR-level remote leakage and local-speech preservation on the real MurmurMark corpus.
```

Acceptance for the first version:

- no raw CAF changes;
- `local_fir` remains default;
- new engine is shadow-only;
- candidate reports include remote-token leakage and local-recall metrics;
- at least one difficult session shows lower remote leakage than `local_fir` without increasing
  lost-`Me` review items;
- if no candidate passes, the report says why.

v0 result on 2026-06-30:

- the lab exists as `scripts/echo-guard-offline-aec-v2-lab.py`;
- `nonlinear_tail160_remote_floor` improved proxy harmful-remote seconds and remote-only dB on the
  six-session smoke corpus;
- ASR clip audit across all candidates did not find a candidate with lower remote-token leakage than
  `local_fir` without local-recall regression;
- promotion is blocked by
  `no_candidate_reduced_remote_tokens_without_local_recall_regression`;
- the next hypothesis should target ASR-visible speech leakage directly, not only residual waveform
  energy.

## Library and Dataset Watchlist

Priority references for the first implementation:

- **SpeexDSP MDF**: practical baseline for long-tail block adaptive filtering.
- **WebRTC AEC3**: architecture reference for staged linear AEC, residual estimation and
  suppression; not assumed to solve MurmurMark as a black box.
- **Microsoft AEC Challenge data and AECMOS**: useful for training/evaluation and for avoiding
  overfitting to one laptop/room.
- **NKF-AEC, NeuralKalman and MetaAF**: candidates after the MDF baseline if delay/path changes
  remain the bottleneck.
- **DTLN-aec and other AEC Challenge baselines**: sanity-check candidates, but not first default
  paths because they are harder to explain and gate.
- **SpeechBrain ECAPA-TDNN, SpeakerBeam, Asteroid/ClearerVoice-style extraction tools**: candidates
  for the later `target_speaker_branch`.

Useful synthetic data recipe:

```text
local target: local-only MurmurMark islands
forbidden source: remote MurmurMark windows
echo transform: delay jitter + RIR + EQ + clipping + compression + soft saturation
mixture: local target + transformed remote + room noise
holdout: real sessions not used for transform fitting
```

## Sources

- SpeexDSP echo cancellation API and timing/tail guidance:
  <https://www.speex.org/docs/manual/speex-manual/node7.html>
- WebRTC AEC3 source interface:
  <https://chromium.googlesource.com/external/webrtc/+/master/modules/audio_processing/aec3/echo_canceller3.h>
- Microsoft AEC Challenge repository:
  <https://github.com/microsoft/aec-challenge>
- ICASSP 2021 Acoustic Echo Cancellation Challenge:
  <https://ar5iv.labs.arxiv.org/html/2009.04972>
- INTERSPEECH 2021 Acoustic Echo Cancellation Challenge:
  <https://www.isca-archive.org/interspeech_2021/cutler21_interspeech.pdf>
- Hybrid deep learning and adaptive AEC:
  <https://minjekim.com/wp-content/uploads/icassp2022_hzhang.pdf>
- DeepFilterNet:
  <https://github.com/Rikorose/DeepFilterNet>
- RNNoise:
  <https://github.com/xiph/rnnoise>
- VoiceFilter target-speaker extraction:
  <https://google.github.io/speaker-id/publications/VoiceFilter/>
- Asteroid source separation toolkit:
  <https://github.com/asteroid-team/asteroid>
