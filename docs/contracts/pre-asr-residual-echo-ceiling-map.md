# Pre-ASR Residual Echo Ceiling Map

Status: implemented audit contract

The `Pre-ASR Residual Echo Ceiling Map v1` explains the remote-supported content that remains in
the microphone ASR input after the promoted Speaker-Preserving Neural Echo v2 decision. It is an
audit-only stage. It does not rewrite audio, change a transcript, train a model or authorize a new
production profile.

## Safety Boundary

- Speaker-Preserving Neural Echo v2.16 and its exact fallback stay immutable.
- Raw CAF, authoritative remote, selected `mic_for_asr`, transcript and notes are read-only.
- The discovery corpus may define capability requirements, but may not train a separator or provide
  final promotion evidence.
- Unopened hard and holdout data remain unavailable until a later candidate and its gates are
  locked.
- Missing or conflicting evidence produces an explicit `unknown`, never an inferred deletion.

## Frozen Scope

The tracked policy is
`policies/pre-asr-residual-echo-ceiling-map-v1.json`. `freeze` expands its production corpus and
additional discovery sessions, fingerprints every required artifact and writes
`frozen_inputs.json`. Analysis refuses changed or missing frozen inputs.

The corpus is a discovery set. Reusing the already opened production corpus is acceptable for
diagnosis, but its measurements cannot be counted again as independent promotion proof.

## Residual Event

The fixed reference unit is an authoritative remote ASR segment whose words are also present in the
temporally nearby production mic ASR. This avoids depending on mic segmentation, which may change
after filtering. Each row records the complete remote segment and a weighted
`remote_supported_seconds` value:

```text
remote duration * matched remote tokens / reference remote tokens
```

All supported rows are retained. A row is `material` only when it passes the locked minimum matched
token and content-token gates. Capability decisions use material rows; reconciliation uses all rows.

## Two-Axis Classification

`signal_truth` answers what the evidence says is present:

- `confirmed_remote_echo`;
- `target_me`;
- `mixed_double_talk`;
- `other_local`;
- `asr_instability`;
- `unknown`.

`production_blocker` separately explains why production v2 did not remove it:

- `alignment_uncertainty`;
- `echo_path_mismatch`;
- `local_preservation_guard`;
- `target_identity_uncertainty`;
- `boundary_guard`;
- `unsupported_mode`;
- `metric_artifact`;
- `insufficient_evidence`.

The two fields must not be collapsed. Confirmed remote can remain because the echo model missed it,
or because removing it would endanger simultaneous local speech.

## Evidence

Each event carries:

- baseline, candidate and selected direct-ASR support;
- authoritative remote token support and text hashes;
- selected/baseline mic versus remote RMS, normalized cross-correlation, lag, speech-band coherence
  and spectral cosine;
- session-calibrated remote-only, local-only and silence baselines;
- speaker-state ratios and local FIR delay;
- intersecting proposed, selected, rejected and rollback windows;
- Target-Me WavLM and Resemblyzer evidence when production v2 computed it;
- SHA-256 provenance for every frozen input.

Audio and text evidence are independent. Text similarity alone cannot classify physical echo, and
audio similarity alone cannot identify a semantic duplicate.

## Outputs

The default report root is:

```text
sessions/_reports/pre-asr-residual-echo-map-v1/
  frozen_inputs.json
  policy_snapshot.json
  residual_events.jsonl
  session_reports/<session>.json
  corpus_summary.json
  corpus_summary.md
  capability_requirements.json
  replay_report.json
  decision.json
```

`residual_events.jsonl` uses `murmurmark.pre_asr_residual_event/v1`.
`corpus_summary.json` uses `murmurmark.pre_asr_residual_echo_corpus/v1`.
`decision.json` uses `murmurmark.pre_asr_residual_echo_decision/v1`.

## Decision

A capability family is material when it accounts for at least the locked share of confirmed
material seconds in the required number of sessions. The result is exactly one of:

- `READY_FOR_TARGET_SPEAKER_MODEL_QUALIFICATION`;
- `READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3`;
- `READY_FOR_MULTI_COMPONENT_SEPARATOR`;
- `REMOTE_METRIC_REPAIR_REQUIRED`;
- `NEEDS_MORE_SUPERVISION`;
- `CURRENT_BASELINE_AT_MEASURED_CEILING`.

The report chooses the strongest supported next capability. It does not promote that capability.

The completed 2026-08-06 run produced `READY_FOR_ALIGNMENT_OR_ECHO_MODEL_V3`. Its frozen evidence,
measurements and interpretation are recorded in
[the result report](../research/2026-08-06-pre-asr-residual-echo-ceiling-map-v1.md).

## Commands

```bash
.venv/bin/python scripts/pre-asr-residual-echo-ceiling-map-v1.py freeze
.venv/bin/python scripts/pre-asr-residual-echo-ceiling-map-v1.py run
.venv/bin/python scripts/pre-asr-residual-echo-ceiling-map-v1.py verify
```

`verify` checks frozen inputs, schemas, reconciliation, coverage and deterministic output
fingerprints without touching session artifacts.
