# Speaker-Resolved Transcript Terminal Gate Instrument v1

Status: active fingerprint-bound measurement contract.

## Purpose

The instrument answers two different questions:

1. Is every North Star dimension measurable from current, frozen evidence?
2. Does the current product pass every dimension?

The first answer is `decision`; the second is `product_decision`. A ready instrument may correctly
report a product that is not ready. No aggregate quality score is allowed.

## Inputs

`policies/speaker-resolved-terminal-gate-instrument-v1.json` names eight canonical reports:

- fresh post-segmentation rebaseline;
- capture continuity closure;
- promoted speaker-preserving pre-ASR Echo profile;
- residual local-recall report;
- human-reviewed lexical seed;
- remote direct truth v2;
- remote unknown recovery;
- speaker-resolved publication corpus.

`freeze` records the policy, implementation and each report's schema, size and SHA-256 under
`sessions/_reports/speaker-resolved-terminal-gate-instrument-v1/private/input_manifest.json`.
Evaluation never refreshes this manifest implicitly. Missing, changed or incompatible evidence
produces `EVIDENCE_INCOMPLETE`. Only dimensions depending on the stale source become
`not_measured`; independent dimensions retain their measured state. For Remote Unknown Recovery,
the instrument also verifies the report-to-private-manifest SHA and that manifest's frozen
rebaseline fingerprint, so a current report file cannot mask stale transitive evidence.

## Outputs

```text
sessions/_reports/speaker-resolved-terminal-gate-instrument-v1/
  private/input_manifest.json
  speaker_resolved_terminal_gate_report.json
  speaker_resolved_terminal_gate_report.md

docs/testing/speaker-resolved-terminal-gate-instrument-v1-snapshot.json
```

The JSON schema is `murmurmark.speaker_resolved_terminal_gate_report/v1`. It contains:

- `decision`: `TERMINAL_GATE_INSTRUMENT_READY` or `EVIDENCE_INCOMPLETE`;
- `product_decision`: `READY` or `NOT_READY`;
- eight independent dimension rows with `pass`, `bounded`, `blocked` or `not_measured`;
- source fingerprints without paths;
- exact blockers and available next commands;
- privacy, provenance and no-mutation declarations;
- `aggregate_quality_score: null`.

## Product Gates

The eight required dimensions are:

1. `durable_capture`: no-restart soak, controlled restart and current corpus have zero lost PCM.
2. `target_me_preservation`: promoted local preservation and exact fallback with no recall residual.
3. `lexical_accuracy`: direct human reference is ready, WER <= 15%, CER <= 8% and domain-term
   accuracy >= 90%.
4. `chronology_and_conservation`: words, roles and order survive; chronology review is zero.
5. `remote_speaker_attribution`: direct truth is ready and current topology has direct count truth.
6. `explicit_unknown`: word and duration ratios stay within explicit bounds.
7. `review_burden`: unresolved seconds stay below the existing 3% corpus bound.
8. `speaker_resolved_publication`: at least six fresh strict/provisional read surfaces and exact
   aggregate fallback pass.

Every row must pass before `product_decision=READY`. A bounded or blocked row cannot be offset by a
stronger result elsewhere.

## Commands

```bash
murmurmark corpus terminal-gate-v1 preflight
murmurmark corpus terminal-gate-v1 freeze
murmurmark corpus terminal-gate-v1 evaluate --write-snapshot
murmurmark corpus terminal-gate-v1 status
murmurmark corpus terminal-gate-v1 replay --write-snapshot
```

Use `all --refresh` only for an intentional new evidence freeze. Ordinary verification uses
`status` and `replay`.

## Safety And Privacy

The instrument reads reports only. It cannot write raw audio, ASR cache, selected transcripts,
Coverage v3, Echo profiles or human answers. Public and tracked artifacts contain no speech text,
session IDs, human names or absolute paths. The private manifest remains under ignored `sessions/`.
