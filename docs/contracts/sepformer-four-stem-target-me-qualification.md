# SepFormer Four-Stem Target-Me Qualification v1

This contract measures one pinned offline separator on frozen train/dev evidence. It cannot select
production audio, change `mic_for_asr` or open future-hard, sealed or direct-ASR inputs.

## Policy And Runner

- policy: `policies/sepformer-four-stem-target-me-qualification-v1.json`;
- runner: `scripts/sepformer-four-stem-target-me-qualification-v1.py`;
- output: `sessions/_reports/sepformer-four-stem-target-me-qualification-v1/`;
- terminal decisions: `READY_FOR_STRONGER_SEPARATOR_HARD_TEST`,
  `DO_NOT_ADVANCE_STRONGER_SEPARATOR` or `CURRENT_RESOURCE_LIMIT_REACHED`.

The runner uses the locally pinned SpeechBrain SepFormer Libri2Mix checkpoint. It is a frozen
two-speaker 8-kHz backbone. MurmurMark supplies the four-stem accounting, speaker assignment,
scale recovery, residual remainder and production fallback around it.

## Frozen Train/Dev Corpus

The policy names exactly 12 train and 4 dev non-target speakers from the already downloaded
OpenSLR SLR31 Mini LibriSpeech corpus. No identity crosses a split. Each speaker contributes ten
four-second family rows and five identity controls:

- ordinary double-talk;
- quiet Target-Me and quiet other-local speech;
- target-absent query and nearby speaker;
- opening backchannel;
- keyboard and office background;
- remote-only and target-only speech;
- target-only, remote-only, other-speaker-only, target+remote and target+other controls.

Target-Me, remote-echo and measured local-noise sources come only from the frozen controlled
supervision publication. Enrollment files and mixture files are disjoint. Materialization writes a
SHA-256 inventory before any separator inference. The future-hard speaker IDs remain metadata;
their directory and audio cannot be opened by this runner.

## Adapter

For each item:

```text
production_v2_mic - frozen_remote_echo
    -> 16 kHz to 8 kHz
    -> pinned SepFormer
    -> two anonymous speech estimates
    -> 8 kHz to 16 kHz
    -> joint least-squares scale recovery
    -> paired WavLM assignment

target_me + remote_echo + other_local + unexplained_residual
    == production_v2_mic
```

The remote-echo stem remains the frozen known component. `unexplained_residual` is the exact
remainder after the two scaled speech estimates. The adapter records both candidate stems even
when assignment is weak, but its selectable output is the sample-exact production v2 input unless
all locked evidence gates pass.

## Train Lock And Dev Access

Train calibrates only speaker-assignment thresholds. It may not alter the SepFormer checkpoint.
The resulting `train/calibration_lock.json` binds:

- policy, source inventory, model and runtime hashes;
- separator cache and WavLM stem-embedding hashes;
- paired-assignment and Target-Me-presence thresholds;
- all train metrics and checks.

Only a valid lock creates `dev/access.json`. Dev inference is then performed once. An existing dev
result is verified, never silently regenerated. Thresholds and quality gates cannot change after
dev access.

Each separator cache item stores a stable audio fingerprint plus its own inference time. Resume
reuses only complete verified items and sums their recorded times, so interruption cannot reset the
resource budget. Train and dev apply the background resource policy independently. A resource
failure is reported as `CURRENT_RESOURCE_LIMIT_REACHED`, never as a model-quality rejection.

## Safety And Decisions

`READY_FOR_STRONGER_SEPARATOR_HARD_TEST` requires every locked dev gate to pass. It only authorizes
a separately designed hard-test stage; it does not open hard audio itself. A quality miss produces
`DO_NOT_ADVANCE_STRONGER_SEPARATOR`. Missing or changed files, non-finite tensors, failed offline
loading or an exceeded resource envelope produce `CURRENT_RESOURCE_LIMIT_REACHED`.

All outcomes preserve:

- raw CAF and authoritative remote bytes;
- Speaker-Preserving Neural Echo v2.17 and `local_fir_role_masked` fallback;
- primary whisper.cpp inputs and transcript outputs;
- zero promotion credit from transcript cleanup or post-ASR repair.

## Frozen Result

The run completed with `DO_NOT_ADVANCE_STRONGER_SEPARATOR` at train calibration. Paired assignment
was accurate, but Target-Me presence and absence scores overlapped by `0.253397`; false rejection
was `0.643939` at the minimum allowed threshold. Dev access was not created. Production v2.17,
future-hard, sealed and direct ASR stayed unchanged.
