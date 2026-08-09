# Temporal End-to-End Remote Diarization Qualification v1

## Purpose

This contract qualifies a local sequence-aware remote-speaker diarization backend after three
independent fixed-window representation families reached their evidence bounds. It does not change
production transcripts, Coverage v3 or selected speaker labels.

## Candidate

- backend: `diarization` `0.1.0` at revision
  `cc50bd27e9373b56bdf325eab8fa390a1d006609`;
- architecture: pyannote segmentation powerset activity, overlap-excluded masked WeSpeaker
  embeddings, PLDA, AHC and VBx temporal clustering;
- model: `FinDIT-Studio/dia-models` WeSpeaker ResNet34-LM at revision
  `6eef479c954ec180e79cee316af2f16d5f7720bd`;
- model SHA-256: `f23f04aa9d0f6b8b0a28de016d226dcbe92d7461a6e58045401acfbed623838a`;
- licenses: runtime MIT or Apache-2.0, segmentation MIT, embedding Apache-2.0, PLDA CC-BY-4.0;
- runtime: pinned Rust worker, local CPU, offline, `nice=20`;
- speaker count: inferred by AHC/VBx without reading expected speaker counts or truth.

The model and its provenance files stay outside the repository. Missing or changed model, license,
runtime or source evidence fails open as `EVIDENCE_BOUND`.

## Frozen Order

1. Verify the six raw remote tracks, 347 blind windows, previous freeze and all Transcript
   Perfection sources.
2. Normalize each remote track to mono 16 kHz PCM and run canonical plus fixed 500 ms trim-start
   variants without labels, text, human names or direct truth.
3. Freeze policy, runtime, model, raw hashes, candidate spans, inferred clusters and window
   assignments.
4. Only after freeze, read Coverage labels and all 33 direct-truth items.
5. Evaluate temporal stability, activity, boundaries, speaker-count conservation, profile mapping
   and direct-truth safety without tuning.

Post-hoc threshold, speaker-count and model selection are forbidden. A changed candidate requires a
new version and a new pre-truth freeze.

## Outputs

Generated artifacts live under
`sessions/_reports/temporal-end-to-end-remote-diarization-qualification-v1/`:

- `candidate_pack.public.json`;
- `freeze_manifest.json`;
- `temporal_remote_diarization_report.json`;
- `temporal_remote_diarization_report.md`;
- `replay_report.json`;
- `artifact_manifest.json`;
- byte-exact pre-temporal Transcript Perfection snapshots under `private/frozen_inputs/`;
- private normalized audio, complete candidate spans, mappings and direct-truth decisions under
  `private/`.

The public report schema is `murmurmark.temporal_remote_diarization_report/v1`. Public artifacts do
not contain speech, human names or private interval text.

## Terminal Outcomes

- `TEMPORAL_DIARIZATION_READY`: the frozen candidate passes temporal, boundary, mapping and
  direct-truth gates and may proceed to a separate monotonic shadow profile.
- `KEEP_EXPLICIT_UNKNOWN`: the candidate is reproducible but cannot reduce unknown speakers safely.
- `EVIDENCE_BOUND`: model, license, runtime, five-speaker support, freeze or provenance is
  insufficient for a valid qualification.

Production promotion is forbidden for all three outcomes in this version.

The one-shot result is `KEEP_EXPLICIT_UNKNOWN`. Temporal replay was stable, but inferred speaker
counts matched `0/6` sessions, minimum remote-interval duration recall was `0.598626`, only `2/3`
confirmed gains survived, one correct control was lost and seven new false identities appeared.
The available local diarization route is therefore closed until materially new evidence or a new
model class exists.

## Invariants

- Coverage v3 retains all 68 accepted assignments;
- all 355 production guards remain unchanged;
- selected transcripts, raw CAF, primary ASR and Echo Guard remain byte-identical;
- all 33 direct-truth items and Coverage labels stay unavailable before freeze;
- mixed, overlap and open-set evidence cannot become a speaker identity on weak evidence;
- deterministic replay cannot mutate the report.
