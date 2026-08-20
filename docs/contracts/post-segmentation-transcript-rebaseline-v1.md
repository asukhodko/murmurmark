# Post-Segmentation Transcript Rebaseline v1

## Purpose

This contract compares the current transcript product surfaces after the terminal
`KEEP_COVERAGE_V3` boundary/minority result. It is a read-only measurement layer. It does not run
ASR, rebuild speaker evidence or select a production profile.

## Command

```bash
murmurmark corpus post-segmentation-rebaseline all --refresh --write-snapshot
murmurmark corpus post-segmentation-rebaseline all --verify-existing
```

The first command freezes the current input set. Normal and verification runs reuse that private
manifest until `--refresh` is requested explicitly.

## Artifacts

The private input registry lives under:

```text
sessions/_reports/post-segmentation-transcript-rebaseline-v1/private/input_manifest.json
```

It contains local session paths, session identifiers and SHA-256 identities. It must not be
published.

Public, content-free artifacts live beside it:

```text
post_segmentation_rebaseline_report.json
post_segmentation_rebaseline_report.md
residual_axes.jsonl
public_manifest.json
replay_report.json
artifact_manifest.json
```

The portable snapshot is
`docs/testing/post-segmentation-transcript-rebaseline-v1-snapshot.json`.

## Inputs And Surfaces

Each frozen session binds `session.json`, readiness, strict selection, selected and aggregate
transcripts, selected dialogue, Coverage v3 evidence when present, provisional selection, capture
continuity, order audit and review state. A changed or missing identity is excluded with an explicit
reason.

The ordinary read surface is evaluated in the same precedence order as the CLI:

1. current strict rich transcript;
2. current disclaimer-bearing provisional transcript when strict publication falls back;
3. exact aggregate transcript fallback.

A dormant provisional artifact cannot invalidate a current strict rich selection. A selected weak
surface must contain a warning disclaimer.

## Dimensions

The report keeps these dimensions separate:

- capture completeness;
- selected text and word conservation;
- timestamp, role and remote-overlap conservation;
- strict/provisional/aggregate surface coherence;
- observed session-local speaker topology;
- explicit unknown speech and cause classes;
- overlap and chronology risk;
- lexical evidence level;
- remaining review burden.

There is no aggregate quality score. Explicit unknown is measured abstention, not correctness.
Machine review rows are not lexical truth.

## Decisions

`REBASELINE_ESTABLISHED` means the frozen evidence is complete, current and reproducible. Product
no-regression gates may still fail and become the next technical priority. `EVIDENCE_INCOMPLETE`
means the rebaseline itself cannot support a decision.

Hard source loss ranks before downstream residuals. Otherwise actionable residuals are ordered by
measured affected seconds. Missing truth cannot be promoted to correctness.

## Safety And Privacy

- Session artifacts and raw CAF are read-only.
- Coverage v3, Echo Guard, ASR and selected transcript inputs are unchanged.
- The public report contains aliases only: no session IDs, absolute paths, speech text or names.
- `--verify-existing` rebuilds in memory and requires byte-identical public artifacts.
