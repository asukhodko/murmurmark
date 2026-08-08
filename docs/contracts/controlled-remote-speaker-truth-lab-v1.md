# Controlled Remote Speaker Truth Lab v1 Contract

Updated: 2026-08-08

## Purpose

This lab measures anonymous remote-speaker attribution against direct local truth. It exists because
agreement between Resemblyzer, WavLM and the selected transcript is not reference evidence.

The lab is audit-only. A successful synthetic result cannot change a real transcript, real speaker
label, Coverage v3 artifact, Echo Guard output or primary ASR input.

## Corpus Boundary

The corpus contains disjoint scripted sessions in `train`, `dev` and `hard` splits. It has at least
four enrolled anonymous voices plus separate unenrolled voices for development and hard open-set
checks. The hard open-set voice must not occur in train or dev.

Each generated session contains:

- one canonical mono PCM mixture;
- one full-duration source stem per active speaker;
- exact word IDs, text, speaker IDs and sample-bounded timestamps;
- exact turn and speaker-boundary truth;
- explicit overlap, silence, short-turn and open-set annotations;
- SHA-256 for every source, mixture and truth artifact.

Source stems must reconstruct the canonical mixture exactly in signed PCM sample space. No ASR or
speaker model creates truth.

## Private And Public Artifacts

Generated speech, exact scripts, voice renderer names, word-level truth and machine predictions live
under the ignored private directory:

```text
sessions/_reports/controlled-remote-speaker-truth-lab-v1/private/
```

Tracked or otherwise public artifacts may contain only aggregate counts, metrics, portable paths,
hashes, implementation versions and gate outcomes. They must contain no synthesized text, voice
renderer name, participant name or absolute path.

The public report schema is:

```text
murmurmark.controlled_remote_speaker_truth_lab_report/v1
```

## Evaluation

Training source turns create anonymous enrollment centroids. Development sessions may select WavLM
similarity and margin thresholds. Hard sessions are read only after threshold selection and never
participate in tuning.

Two audit tracks and separate track decisions are reported:

1. `coverage_v3_topology`: the promoted seeded-centroid decision shape with frozen v3 thresholds.
2. `wavlm_open_set_candidate`: local WavLM XVector enrollment with thresholds chosen only on dev.

Words with timestamp overlap are marked `mixed` before speaker classification. Unenrolled voices
must remain `unknown_speaker`; a model must not force them into a known anonymous speaker.

All machine outputs remain predictions. Exact scripted metadata remains the only truth source. The
Coverage v3 track is a frozen control. The overall `LAB_READY` decision follows the new WavLM
candidate gates; a passing control cannot hide a failed candidate.

## Metrics And Decision

`LAB_READY` requires all of the following on the frozen hard split:

- exact word conservation and 100% direct truth coverage;
- byte-stable deterministic replay;
- source-stem reconstruction error of zero PCM samples;
- session-disjoint train/dev/hard inputs and no hard tuning;
- B-cubed F1 `>= 0.98` on known single-speaker words;
- pairwise precision `>= 0.98` on known single-speaker words;
- speaker-boundary recall `== 1.0`;
- open-set false attribution count `== 0`;
- public artifact privacy checks pass.

Any failed gate produces `DO_NOT_ADVANCE`. `LAB_READY` permits only a later bounded real-session
candidate; it does not promote synthetic labels into production.

## Fail-Open Rules

Missing voices, renderer, runtime, local model, private corpus or stale hashes stop evaluation with an
explicit blocker. Existing transcripts and Coverage v3 remain untouched.
