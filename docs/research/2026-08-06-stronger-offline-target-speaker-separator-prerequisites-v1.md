# Stronger Offline Target-Speaker Separator Prerequisites v1

Date: 2026-08-06

Decision: `READY_FOR_STRONGER_SEPARATOR_QUALIFICATION`

## Why This Stage Existed

Multi-Component Residual Separator v1 proved that four-stem accounting and speaker queries are
structurally useful, but its small FiLM-GRU stopped at `5.561 dB` Target-Me SNR, `4.443 dB`
other-local SNR, `6.803 dB` absent-query attenuation and `-1.545 dB` residual SNR. Repeating the
same architecture on the same four train and two dev non-target speakers would not be useful.

This stage therefore selected and resource-qualified one stronger backbone before any new training,
hard evaluation or direct ASR.

## Data Gap Map

The expansion manifest increases public non-target identities from `4/2/2` to `12/4/4` across
train/dev/future-hard while keeping every identity split-disjoint. It retains the frozen controlled
Target-Me sessions and explicitly covers quiet and absent Target-Me, nearby speakers, ordinary
double-talk, openings, keyboard and office noise. Future-hard speaker IDs are frozen as metadata;
their audio was not opened in this stage.

Ordinary meetings remain evaluation evidence. They are not converted into guessed training labels.

## Backbone Review

Three paths were checked against primary project/model sources:

1. [SpeechBrain SepFormer Libri2Mix](https://huggingface.co/speechbrain/sepformer-libri2mix/tree/eb43c5bfbb2aa654630adbf849373bcec0a20ed4)
   was selected. The official checkpoint is Apache-2.0, deterministic offline on CPU and bounded on
   this Mac. It is a two-source 8-kHz separator, so Target-Me assignment stays external.
2. [WeSep](https://github.com/wenet-e2e/wesep/tree/99eca54b60300d39b9353d93cf285a14bba37854)
   is architecturally closest to target-speaker extraction, but the pinned repository has no
   top-level license and documents a Python 3.9 / PyTorch 1.12.1 / CUDA baseline. It was rejected
   for this prerequisite, not declared useless.
3. [Asteroid Conv-TasNet Libri2Mix 16k](https://huggingface.co/JorisCos/ConvTasNet_Libri2Mix_sepclean_16k/tree/188631e8337c9bee6517bfe115956f475d94f523)
   is small and CPU-friendly, but the current local runtime produced physically invalid gain even
   though tensors were finite. The new preflight correctly rejects this class of false success.

SpeechBrain source is pinned at `36c180c7bfad3bf5c48bd76a24799812952c4565` (`v1.1.0`). The
[source license](https://github.com/speechbrain/speechbrain/blob/36c180c7bfad3bf5c48bd76a24799812952c4565/LICENSE),
model revision, every checkpoint and every private runtime wheel are recorded by SHA-256.

## Resource Result

Two independent child processes ran with blocked network access, four Torch threads and background
priority. The frozen probe produced the same output SHA-256 both times:

```text
c2cc4274a5db0b20d155045d5ab3a5ad896540d19dd25ede45d8e090335f0e64
```

Observed envelope:

- model files: `113,153,233` bytes;
- peak RSS: about `687 MB`;
- model load: below `0.6s`;
- one-second probe inference: below `3s` under the low-impact policy;
- output: finite, non-zero, shape `[1, 8000, 2]`;
- network attempts: `0`;
- exact reconstruction after adapter remainder: passed.

The resource probe is intentionally not a quality claim. SepFormer output is anonymous and
scale-indeterminate; SI-SDR or a successful tensor pass does not prove preservation of Russian words.

## Frozen Adapter

The next qualification operates on the local mixture after the frozen echo hint. SepFormer yields
two anonymous speech estimates. Existing WavLM enrollment assigns the target only when the target
stem beats the alternative by a locked paired margin. Least-squares coefficients restore stem
scale, and `unexplained_residual` is the exact remainder. Together with the frozen `remote_echo`
stem this preserves sample-exact reconstruction.

Weak query evidence, missing local files, more than two local speakers, non-finite output or failed
mixture consistency returns the exact Speaker-Preserving Neural Echo v2 production fallback.

## What Is Now Unblocked

One bounded train/dev qualification may now:

- render the frozen expanded supervision without touching future-hard audio;
- run the pinned SepFormer backbone offline;
- calibrate WavLM stem assignment and the four-stem adapter on train/dev only;
- stop at dev with either a locked candidate or a reproducible rejection.

Hard/sealed evaluation, direct ASR and production publication remain closed until that dev gate
passes. Raw CAF, authoritative remote, production v2, transcripts and Live Shadow were unchanged.
