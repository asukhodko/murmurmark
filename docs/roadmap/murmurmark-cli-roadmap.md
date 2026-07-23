# MurmurMark CLI Roadmap

Updated: 2026-07-23

This is the readable view of the active OpsKarta v3 plan:

- `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`

The YAML plan owns statuses and dependencies. `docs/project/current-goal.md` expands the one
executable goal. Historical experiment detail is preserved under `docs/history/` and does not
redefine current priorities.

## Planning Rules

- `done`: implemented and evidenced capability;
- `current`: work being executed now;
- `next`: unlocked goal that follows the current one;
- `later`: dependent stage whose prerequisites are not complete;
- `idea`: research hypothesis outside the committed path;
- `optional`: useful but nonessential capability;
- `blocked`: work with an explicit unsatisfied gate.

Evergreen capabilities such as corpus regression are `done`, not permanently `current`. A completed
experiment ends in `PROMOTE` or `DO_NOT_PROMOTE`; either outcome closes its hypothesis.

## What Works Now

```mermaid
flowchart LR
    C["Durable two-track capture"]
    E["Echo Guard preprocessing"]
    T["Authoritative batch transcript"]
    R["Audit and review loop"]
    N["Evidence notes and verdict"]
    X["Guarded export and retention"]

    C --> E --> T --> R --> N --> X
```

The supported product path is:

```text
murmurmark meeting -> first Ctrl-C -> bounded authoritative lifecycle -> honest result
```

Raw CAF files and batch output are authoritative. Committed-PCM Live Shadow is capture-safe and
advisory; its promotion remains blocked by quality and runtime evidence.

## Current Goal

**Speaker-Preserving Echo Adaptation Corpus v1** is the current goal. It does not train or apply a
model. It must first prove that local MurmurMark sessions contain enough privacy-safe,
session-disjoint supervision for remote-only, local-only and double-talk. The result is
`READY_FOR_ADAPTATION` or an exact `DO_NOT_TRAIN`.

Two suppression families established the same safety ceiling. Classical state-level suppression
removed `68.2845%` of bounded remote-risk, but lost protected `Me` in two speaker sessions. The
pinned Microsoft DEC model removed all `3.49s` of bounded remote-risk in those hard sessions, yet
protected-local recall fell to `45.45%`, chronology and double-talk recall to `0%`, and incremental
runtime reached `52.85%` of `local_fir`. AECMOS still rated the audio well, so it remains a secondary
metric rather than a word-preservation gate. `local_fir_role_masked` remains production.

**One-Command Meeting Lifecycle v1** is complete. `murmurmark meeting` owns durable capture,
authoritative processing, evidence enrichment, conservative review and guarded export. It uses
machine-readable readiness, checkpoints every action and gives a precise resume command after
interruption. Capture now runs in a short-lived child: ScreenCaptureKit/ReplayKit is gone before
post-processing, so the next meeting can start while an older one is still being recognized.
Startup and shutdown are bounded, and a failed startup releases the recording lock. Automated
checks, real-artifact interrupt/resume, a fresh permission-capable capture soak and strict lifecycle
acceptance all pass.

Speaker-Mode Transcript Quality Hardening v1 completed with `DO_NOT_PROMOTE`. The frozen corpus
proved three lossless retimes, one real double-talk interval and one genuine `Me` row, but no whole
`Me` deletion. Duplicate reduction was `2.7%` and review reduction `7.9%`, below the `25%` and `15%`
promotion gates.

The immediate Evidence-Backed Me Completion v2 predecessor is now complete and promoted for its
frozen two-session scope. It closed `3/6` residual local-recall rows and `22.4/35.85s`, repaired one
duplicate text tail, preserved raw/remote/chronology/notes evidence, and exposed the remaining
`13.45s` plus unresolved transcript text through concrete review lanes. Outside that frozen scope,
`residual_local_recall_v1` remains the fallback.

Mixed-Utterance Remote Span Separation v1 completed with `DO_NOT_PROMOTE`. It froze `12` mixed
`Me` rows / `54.940s` across `7` sessions and produced deterministic evidence for all of them.
Seven rows are probable ASR noise and five remain ambiguous, but no row proved both the removable
remote span and the identity of every retained local edge. It applied no text changes and introduced
no raw, remote, local-recall, chronology, notes or verdict regression.

Echo Suppression Promotion v1 then aligned Offline AEC, WebRTC AEC3 and SpeexDSP under one signed
timeline and automatic fail-open policy. The nine-session corpus kept `local_fir` in production and
froze the exact overlap intervals that classical state-level suppression could not handle safely.

Neural Residual Echo Suppression v1 then tested a pinned, offline Microsoft DEC ONNX model against
those failures. It completed with deterministic `DO_NOT_PROMOTE`; full shadow was intentionally
skipped after the mandatory local-loss stop rule fired. This rules out another blind engine swap
and motivates proving supervision quality before any adaptation.

## Critical Path

```mermaid
flowchart LR
    P["Done<br/>Me Completion v2"]
    L["Done<br/>One-Command Lifecycle"]
    A["Done<br/>Mixed-Utterance Separation"]
    B["Done<br/>Echo Suppression Promotion v1"]
    N["Done<br/>Neural Residual Echo v1"]
    S["Current<br/>Speaker-Preserving<br/>Adaptation Corpus v1"]
    C["Evidence Notes And Export v2"]
    D["Release-quality CLI"]

    P --> L --> A --> B --> N --> S --> C --> D
```

### 0. Evidence-Backed Me Completion v2

Completed with a scoped `PROMOTE`. Independent mic ASR, word timestamps, speaker state, calibrated
Target-Me and remote-forbidden evidence may materialize bounded local speech. Weak or conflicting
evidence stays unchanged and reviewable. Auto-selection requires exact frozen-input and output
fingerprints plus corpus membership.

### 1. One-Command Meeting Lifecycle

Completed. One command now runs durable capture and plain authoritative processing, applies only
allowlisted enrichment and suggested-review actions, guards export from structured outcome state,
verifies raw SHA-256 identities, isolates capture from post-processing in a short-lived process and
supports lock-safe resume after a second `Ctrl-C`.

### 2. Mixed-Utterance Remote Span Separation

Completed with `DO_NOT_PROMOTE`. Clean/raw/role-masked word timestamps, authoritative remote timing,
speaker state and Target-Me evidence were sufficient to identify suspicious remote-supported spans,
but not to prove safe local prefixes or tails. The isolated profile remains audit evidence and is
never selected automatically.

### 3. Echo Suppression Promotion

Completed with `DO_NOT_PROMOTE`. The exact role-aware `local_fir` baseline, signed delay contract,
candidate matrix, bounded ASR probes and policy are reproducible. Coverage passed `3/5` applicable
speaker sessions; the failed protected-local and chronology gates keep production on `local_fir`.

### 4. Neural Residual Echo Suppression v1

Completed with deterministic `DO_NOT_PROMOTE`. The model-neutral adapter, pinned DEC and AECMOS
models, exact-duration inference, fail-open checks and frozen corpus are reproducible. The candidate
removed bounded remote-risk but failed protected-word, chronology, double-talk and runtime gates.
Production output was never changed.

### 5. Speaker-Preserving Echo Adaptation Corpus v1

Current. Freeze provenance-rich remote-only, local-only and double-talk examples; create
session-disjoint train/dev/hard-test splits; reserve both known counterexamples for immutable test;
and run duration, leakage, protected-word, privacy and licensing oracle checks. Publish
`READY_FOR_ADAPTATION` only when the data can support a meaningful experiment. Otherwise publish
`DO_NOT_TRAIN` and move on without spending compute on training.

### 6. Evidence Notes And Export v2

Improve the already working notes/export handoff over the selected transcript. Generated or
extractive claims remain traceable to evidence IDs.

### 7. Release-quality CLI

Finalize the supported environment, installation, model/config handling, acceptance, release notes
and public operational contract. UI is not required.

## Parallel Research

```mermaid
flowchart LR
    Q["Adaptation corpus decision"]
    E["Speaker-Preserving Neural Echo v2"]
    D["Remote diarization"]
    S["Speaker map"]
    T["transcript.rich.json"]
    V["Heavy local validators"]
    L["Evidence-guarded LLM"]

    Q -.-> E
    Q -.-> D --> S --> T --> L
    Q -.-> V
```

Speaker-Preserving Neural Echo v2 is conditional on `READY_FOR_ADAPTATION`. A `DO_NOT_TRAIN`
decision skips that branch and continues the product path.

Remote diarization works on authoritative `remote` and does not require complete Echo suppression.
It starts after base quality closure, first produces anonymous stable speaker IDs, then an
evidence-backed speaker map and rich transcript.

Heavy local models begin as bounded validators. They do not replace the primary ASR without their
own corpus gates.

## Parking Lot

- Live result promotion: blocked by reproducible `DO_NOT_PROMOTE` evidence;
- docs and issue-tracker proposals: optional and reviewed before external writes;
- UI/Menu Bar: optional after release-quality CLI.

These branches do not block the critical path.

## Promotion Gate

```mermaid
flowchart LR
    H["Bounded hypothesis"]
    I["Frozen inputs"]
    P["Isolated profile"]
    G["Per-session and corpus gates"]
    D{"Decision"}
    Y["PROMOTE"]
    N["DO_NOT_PROMOTE"]

    H --> I --> P --> G --> D
    D --> Y
    D --> N
```

No candidate may mutate raw capture or silently replace the selected profile. A negative result must
record the evidence ceiling and leave the authoritative output unchanged.

## Validation

```bash
scripts/check-planning-consistency.py

PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  validate docs/roadmap/murmurmark-cli-roadmap.plan.yaml

PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  render tree docs/roadmap/murmurmark-cli-roadmap.plan.yaml

PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  render executive docs/roadmap/murmurmark-cli-roadmap.plan.yaml --view exec-top
```

Detailed planning and experiment history through 2026-07-19 is archived in
`docs/history/README.md`.
