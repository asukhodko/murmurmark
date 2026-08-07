# Evidence-Only Local Note Selection v1

Status: complete

Decision: **PROMOTE_OPTIONAL_EVIDENCE_SELECTION**

## Question

Can a local model make evidence-backed meeting notes shorter and easier to scan without receiving
permission to create, rewrite or paraphrase factual claims?

## Frozen Design

- Source: current Reviewed Speaker-Aware Meeting Memory v1 on the same six sessions used by the
  failed free-text synthesis qualification.
- Model: local `deepseek-r1:14b`, pinned blob `6e9f90f02bb3...b907dc1e`, Q4_K_M, MIT license.
- Runtime: loopback-only Ollama `0.32.1`, temperature `0`, seed `424242`.
- Output: five bounded arrays containing only statement IDs allowed by a dynamic JSON Schema.
- Publication: model output selects IDs; all visible text, speaker provenance and evidence IDs are
  copied exactly from the verified source catalog.
- Failure: unknown or malformed IDs, stale inputs, runtime mismatch or publication interruption
  fail open to exact extractive notes inside the isolated bundle.
- Ordinary transcript, notes, export and Evidence Handoff v2 bytes are protected and unchanged.

The frozen policy is `policies/evidence-only-local-note-selection-v1.json`. The corpus evidence is
`docs/testing/evidence-only-local-note-selection-v1-manifest.json`.

## Result

| Measure | Result |
|---|---:|
| Sessions passed | `6/6` |
| Source candidates | `189` |
| Selected statements | `56` |
| Review-marked source candidates | `47` |
| Selected review-marked candidates | `28` |
| Review compression | `0.404255` |
| Category coverage | `1.0` |
| Speaker coverage | `0.8` |
| Selection contract errors | `0` |
| Model-authored published claims | `0` |
| Maximum first-run wall time | `25.760476s` |
| Maximum model RSS | `12474.344 MB` |

All six repeated runs produced the same semantic selection fingerprint. Every selected statement
retained exact source text and provenance. Ordinary outputs remained byte-identical. The malformed
response fixture produced an exact extractive fallback.

## Limitation

The frozen corpus has `0` baseline high-confidence decision/action/risk/question items. Therefore
`baseline_high_confidence_retention_ratio = 1.0` is a vacuous result. Promotion is still safe in its
narrow optional scope because the selector cannot author text, default outputs do not consume it,
and every selected item remains review-marked. This experiment does not prove that future
high-confidence artifacts may be dropped safely.

## Decision Boundary

Promotion means only that a user or the next reviewed-artifact stage may explicitly consume this
verified selection bundle. It does not activate a generated-notes mode, change `auto`, alter
ordinary `notes` or `export`, or authorize external writes.

The next product stage is Reviewed Meeting Artifacts v1: turn exact decisions, actions, risks and
open questions into a short fingerprint-bound `confirmed`, `rejected` or `unresolved` queue. It may
use this selector only when its handoff verifies; otherwise it must use the deterministic exact
source catalog.
