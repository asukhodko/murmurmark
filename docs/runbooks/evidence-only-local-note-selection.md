# Evidence-Only Local Note Selection v1

Status: promoted as an optional local read artifact

Decision: `PROMOTE_OPTIONAL_EVIDENCE_SELECTION`

This stage shortens Reviewed Speaker-Aware Meeting Memory without letting a model write or edit a
claim. The local model returns only known statement IDs. Text, speaker provenance and evidence
utterance IDs are copied byte-for-byte from the verified source bundle.

It is deliberately absent from the normal `murmurmark meeting`, `notes`, `transcript` and `export`
path. The 14.8B model is useful for an opt-in reading view, but is too heavy to put on the critical
path of every meeting.

## Prerequisites

- a current Reviewed Speaker-Aware Meeting Memory v1 bundle, normally produced by
  `murmurmark speakers apply "$SESSION"` after explicit speaker-label review;
- local Ollama `0.32.1`;
- the pinned local `deepseek-r1:14b` blob from
  `policies/evidence-only-local-note-selection-v1.json`;
- no network access is needed and the runner never pulls a model.

## Build The Optional View

```bash
SESSION="sessions/<session-id>"

nice -n 20 .venv/bin/python \
  scripts/materialize-evidence-only-local-note-selection.py \
  "$SESSION"
```

The command writes only under:

```text
derived/meeting-memory/evidence-only-selection-v1/
```

Open the verified notes:

```bash
less "$(.venv/bin/python \
  scripts/materialize-evidence-only-local-note-selection.py \
  "$SESSION" \
  --verify-only \
  --print-path notes)"
```

Inspect the audit data:

```bash
jq '{state, summary, gates, reasons, recommended_next}' \
  "$SESSION/derived/meeting-memory/evidence-only-selection-v1/handoff_manifest.json"

SELECTION_PATH="$(.venv/bin/python \
  scripts/materialize-evidence-only-local-note-selection.py \
  "$SESSION" \
  --verify-only \
  --print-path selection_json)"

jq '{selected, metrics, selection_trace}' \
  "$SELECTION_PATH"
```

## Fail-Open Behaviour

Unknown IDs, malformed JSON, stale source fingerprints, missing model/runtime, changed ordinary
outputs or any evidence mismatch produce `failed_open` or `unavailable`. In that state the isolated
bundle contains the exact Reviewed Speaker-Aware Meeting Memory notes as fallback. It never changes
ordinary transcript, notes or export files.

## Reproduce The Frozen Qualification

This is a development check, not a normal meeting command. It runs the pinned model twice per
session and can use about 12.5 GB RAM.

```bash
nice -n 20 .venv/bin/python \
  scripts/report-evidence-only-local-note-selection-corpus.py \
  --out-dir sessions/_reports/evidence-only-local-note-selection-v1 \
  --frozen-manifest docs/testing/evidence-only-local-note-selection-v1-manifest.json \
  --strict

.venv/bin/python \
  scripts/report-evidence-only-local-note-selection-corpus.py \
  --verify-frozen-only \
  --frozen-manifest docs/testing/evidence-only-local-note-selection-v1-manifest.json
```

The frozen result is 6/6 passing sessions, 47 review-marked source candidates reduced to 28,
category coverage `1.0`, speaker coverage `0.8`, deterministic replay and zero model-authored
published claims. The corpus contains no baseline high-confidence decision/action/risk/question
items, so its reported retention `1.0` is vacuous and is not evidence for that population.
