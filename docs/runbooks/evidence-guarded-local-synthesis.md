# Evidence-Guarded Local Synthesis Qualification v1

Status: completed with `DO_NOT_PROMOTE`

This track tests whether one pinned local language model can improve meeting notes without changing
transcript truth or weakening the extractive fallback.

## Frozen Runtime

- runtime: local loopback Ollama only;
- model: `deepseek-r1:14b`;
- architecture: Qwen2, 14.8B, `Q4_K_M`;
- license: MIT;
- model blob: `sha256:6e9f90f02bb3b39b59e81916e8cfce9deb45aeaeb9a54a5be4414486b907dc1e`;
- decoding: temperature `0`, seed `424242`, fixed context/output bounds;
- oversized evidence is split into deterministic, speaker-bounded prompt chunks;
- network: loopback request only; the runner never pulls a model.

The exact model, prompt, policy and implementation hashes are qualification inputs. A missing or
different runtime is a fail-open state, not permission to download or substitute another model.

## Evidence Boundary

The model reads the current Reviewed Speaker-Aware Meeting Memory v1 plus its exact Evidence
Handoff v2 statements and utterances. It may rank and minimally compress those statements. It may
not rewrite transcript truth, infer participant identity, use cross-session memory or create a fact
without exact source statement and utterance IDs.

Every proposed claim passes a separate deterministic verifier. It checks:

- known statement and utterance IDs;
- exact statement-to-utterance membership;
- permitted category and output limit;
- content-token support, protected names and numbers;
- negation and commitment markers;
- reviewed speaker-label provenance;
- duplicate and contradictory output.

Rejected candidates remain in machine-readable audit output and never enter published Markdown.
Top-N overflow and duplicates are recorded separately as `selection_hidden`; only unsupported
proposals count toward the safety-rejection gate.

## Decision

The six-session corpus completed with `DO_NOT_PROMOTE`. The model produced 142 candidates: 49
passed, 69 failed independent support checks and 24 were hidden as duplicates or output overflow.
The stable safety-rejection ratio was `0.485915`, above the frozen maximum `0.35`; observed model
RSS also reached `13978.922 MB`, above the `13312 MB` limit.

No `murmurmark ... --local-synthesis` CLI surface was activated. Default and reviewed-speaker
`notes` and `export` continue to use the existing extractive handoff. Qualification artifacts are
diagnostic only.

Corpus qualification:

```bash
.venv/bin/python scripts/report-evidence-guarded-local-synthesis-corpus.py \
  --strict \
  --frozen-manifest docs/testing/evidence-guarded-local-synthesis-v1-manifest.json
```

The report records factual support, accepted/rejected claims, evidence coverage, replay equality,
wall time, model evaluation time and observed memory. The result must be an explicit
`PROMOTE_OPTIONAL_LOCAL_SYNTHESIS` or `DO_NOT_PROMOTE`.

The ordinary repository test validates the frozen result without loading Ollama:

```bash
.venv/bin/python scripts/report-evidence-guarded-local-synthesis-corpus.py \
  --verify-frozen-only \
  --frozen-manifest docs/testing/evidence-guarded-local-synthesis-v1-manifest.json
```

## Fail-Open Rules

Missing or stale speaker review, model, prompt, policy, implementation, source handoff or bundle
returns the ordinary extractive notes/export. Malformed JSON, unknown IDs, unsupported claims and
nondeterministic replay cannot publish a current local-synthesis bundle.

Capture, Echo Guard, ASR, transcript selection, default notes/export, retention and external systems
remain unchanged.

The full result and evidence ceiling are recorded in
`docs/research/2026-08-07-evidence-guarded-local-synthesis-v1.md`.
