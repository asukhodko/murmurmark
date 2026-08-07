# Evidence-Guarded Local Synthesis Qualification v1

Status: completed with `DO_NOT_PROMOTE`

Date: 2026-08-07

## Question

Can one pinned local language model turn current Reviewed Speaker-Aware Meeting Memory v1 into a
shorter summary, decisions, actions, risks and open questions while every published claim remains
independently supported by exact current-session evidence IDs?

## Frozen Setup

- runtime: Ollama `0.32.1`, loopback only;
- model: `deepseek-r1:14b`, Qwen2 14.8B, `Q4_K_M`, MIT;
- model blob SHA-256:
  `6e9f90f02bb3b39b59e81916e8cfce9deb45aeaeb9a54a5be4414486b907dc1e`;
- temperature `0`, seed `424242`, context `6144`, output bound `2048`;
- deterministic prompt chunks: at most 12 statements and 28000 rendered bytes;
- source: the frozen six-session Reviewed Speaker-Aware Meeting Memory corpus;
- published text required exact statement and utterance references, token support and preserved
  names, numbers, negation, commitment markers and speaker provenance.

The model ran twice per session. Capture, Echo Guard, ASR, selected transcript, ordinary notes and
export were outside the experiment and remained byte-identical.

## Result

| Metric | Result |
|---|---:|
| Sessions completed | 6/6 |
| Proposed claims | 142 |
| Accepted claims | 49 |
| Safety-rejected claims | 69 |
| Selection-hidden duplicates/overflow | 24 |
| Safety-rejected ratio | 0.485915 |
| Category coverage | 19/25, or 0.76 |
| Accepted/available evidence utterances | 50/176 |
| Accepted/source review-marked statements | 22/47 |
| Published unsupported claims | 0 |
| Maximum first-session wall time | 154.165211s |
| Maximum observed model RSS | 13978.922 MB |

All six replays were deterministic. Every published reference resolved, the synthetic adversarial
checker passed, and ordinary outputs remained unchanged. The decision is nevertheless
`DO_NOT_PROMOTE` because two frozen promotion gates failed:

1. safety rejection was `0.485915`, above the maximum `0.35`;
2. observed model RSS was `13978.922 MB`, above the maximum `13312 MB`.

The largest failure concentration was session `2026-06-26_11-15-50`: only 6 proposals passed while
36 were safety-rejected. Frequent reasons were insufficient source-text support and selecting a
statement under the wrong output category. Weakening these checks would publish fluent but
unsupported meeting claims, so the gates remain unchanged.

## Decision

No `--local-synthesis` CLI read or export mode is activated. The qualification scripts and audit
artifacts remain available for reproduction, but normal `notes` and `export` continue to use the
existing extractive Evidence Handoff v2 path.

The useful next hypothesis is narrower: let a local model choose and rank only existing statement
IDs. Published wording, speaker provenance and utterance IDs would then be copied byte-for-byte
from verified evidence rather than authored by the model.

## Reproduction

The full model run is intentionally expensive:

```bash
nice -n 20 .venv/bin/python scripts/report-evidence-guarded-local-synthesis-corpus.py \
  --strict \
  --frozen-manifest docs/testing/evidence-guarded-local-synthesis-v1-manifest.json
```

Normal repository checks use the frozen offline verifier and do not load the model:

```bash
.venv/bin/python scripts/report-evidence-guarded-local-synthesis-corpus.py \
  --verify-frozen-only \
  --frozen-manifest docs/testing/evidence-guarded-local-synthesis-v1-manifest.json
```
