# Neural Residual Echo Suppression v1

Date: 2026-07-23

Decision: **DO_NOT_PROMOTE**

Decision fingerprint:
`eff4119d7e19b90dc2b7e6f03caf1b762d29f6c46c97b3141764db76764200f3`.

## Question

Can a pretrained local remote-conditioned model remove residual speaker echo after `local_fir`
without deleting real near-end speech in double-talk and chronology boundaries?

The experiment evaluated Microsoft's ICASSP 2022 DEC baseline in two isolated modes:

- `ms_dec_local_fir_v1`: `mic_clean_local_fir` plus the canonical aligned remote;
- `ms_dec_raw_mic_control_v1`: canonical raw mic plus the same aligned remote.

Neither mode can alter production. `local_fir_role_masked` remains the exact fallback.

## Reproducible Inputs

The model source is pinned to Microsoft AEC Challenge commit
`6c633d0a9d2a143a0e364899b91b06f127315b18`.

| Purpose | File | SHA-256 |
| --- | --- | --- |
| suppression | `dec-baseline-model-icassp2022.onnx` | `4436ee4f80e5f1d0299196bd7057137a3cad7cac324409dce7540f2a113bb931` |
| secondary metric | `aecmos-16k-no-scenarios.onnx` | `b517d8d9ca2f91ea55d15f605a15917c19be5d832868fe115c7c5bc48986dae1` |

Inference is local, offline, mono 16 kHz and CPU-only through ONNX Runtime. The adapter uses the
model's 20 ms square-root Hann window, 10 ms hop, recurrent state and 161-bin mask. It adds no gain
normalization and restores the exact input sample count. Missing weights, a bad checksum, runtime
failure, non-finite output, clipping or a length mismatch select the exact baseline.

The frozen nine-session corpus and signed alignment contract come from
[Echo Suppression Promotion v1](2026-07-23-echo-suppression-promotion-v1.md). Two previously failed
speaker-playback intervals are mandatory first-stage counterexamples. The stop rule prevents full
corpus ASR after either counterexample loses local speech.

## Result

Both mandatory counterexamples failed local-preservation gates.

| Metric | Result | Required |
| --- | ---: | ---: |
| bounded ASR-visible remote-risk | `3.49s -> 0s` (`100%`) | at least `25%` reduction |
| minimum ordinary local-only recall | `100%` | at least `99%` |
| minimum protected-local recall | `45.45%` | at least `99%` |
| minimum chronology recall | `0%` | no regression |
| double-talk recall | `0%` in one counterexample | no regression |
| maximum incremental audio runtime | `52.85%` of `local_fir` | at most `25%` |

The first counterexample retained only the tail `Все задачи будут не успевать` from the protected
phrase beginning `Когда ты слишком быстро работаешь...`; chronology probes fell to zero. The second
counterexample deleted the short local acknowledgement `Да.` and retained only `54.55%` of
protected-local words.

AECMOS no-scenario produced high mean echo scores (`4.15` and `4.50`) for the primary candidate.
Those scores are secondary only: they did not detect the word-level near-end loss that blocks
MurmurMark promotion.

The raw-mic control did not rescue the safety failure. Its signal metrics also showed excessive
double-talk attenuation. This points to model/domain adaptation rather than a simple
`local_fir`-input mismatch.

## Stop Decision

The primary candidate proved that a remote-conditioned mask can remove recognizable leakage, but
the pretrained model does not distinguish MurmurMark's quiet near-end speech from transformed
remote echo reliably enough. Running full shadow transcription on the remaining corpus would only
measure downstream damage after a mandatory gate had already failed.

All nine frozen sessions still have an explicit disposition:

- two mandatory speaker-playback counterexamples were evaluated;
- three further speaker-playback sessions were not run after the mandatory local-loss stop;
- three headphones/low-leak sessions keep baseline;
- one verified no-speech session keeps baseline.

Raw CAF and frozen derived inputs retained their SHA-256 identities. Candidate audio replay and the
corpus decision are deterministic.

## Commands

```bash
.venv/bin/python scripts/bootstrap-neural-residual-echo-v1.py

.venv/bin/python scripts/neural-residual-echo-v1.py corpus \
  --run \
  --max-windows-per-class 2

jq '{decision:.promotion.decision, decision_fingerprint, determinism, metrics}' \
  sessions/_reports/neural-residual-echo-v1/neural_residual_echo_corpus_report.json
```

Per-session artifacts live under:

```text
derived/preprocess/neural-residual-echo-v1/
  model_manifest.json
  freeze_manifest.json
  inference_manifest.json
  integrity_report.json
  bounded_asr_report.json
  protected_local_report.json
  session_decision.json
  candidates/<candidate>/
```

Corpus artifacts live under:

```text
sessions/_reports/neural-residual-echo-v1/
  model_manifest.json
  frozen_corpus.json
  neural_residual_echo_corpus_report.json
  neural_residual_echo_corpus_report.md
```

## Next Hypothesis

Do not lower local-recall gates and do not try another opaque suppressor on the same evidence.
The next bounded step is **Speaker-Preserving Echo Adaptation Corpus v1**:

1. freeze reliable local-only, remote-only and double-talk examples with session-disjoint splits;
2. construct paired supervision using measured MurmurMark echo paths and preserved near-end speech;
3. define word-level protected-local and chronology acceptance before training;
4. publish `READY_FOR_ADAPTATION` or `DO_NOT_TRAIN`.

Fine-tuning or a new model belongs to a later goal and starts only if that corpus has adequate,
privacy-safe supervision.
