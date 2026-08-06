# MurmurMark CLI Roadmap

Updated: 2026-08-07

This is the readable view of the active OpsKarta v3 plan:

- `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`

The YAML plan owns statuses and dependencies. `docs/project/current-goal.md` expands the one
executable goal. Historical experiment detail is preserved under `docs/history/` and does not
redefine current priorities.

## Planning Rules

Statuses are `done`, `current`, `next`, `later`, `idea`, `optional` and `blocked`; only `current`
denotes active execution, while every other unfinished status retains an explicit dependency role.

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

## North Star And Current Goal
The technical North Star is an ASR input that retains every confirmed `Me` word, contains no recognizable authoritative remote and keeps nearby non-target speech out of `Me`, with exact fallback.
Speaker-Preserving Neural Echo v2 is the safe production plateau: `5/12` candidate sessions,
`41.940s` and 90 tokens removed, local retention `1.0`; v2.17 requalified it on current ASR with
hard `3/3`, corpus `12/12` and unchanged utility. **Pre-ASR Target-Me Isolation Limit v1** is done.
SepFormer reached perfect paired train assignment but failed Target-Me presence separation and
stopped before dev with `DO_NOT_ADVANCE_STRONGER_SEPARATOR`. **Reviewed Speaker-Aware Meeting
Memory v1** is current: reviewed session-local labels enter an opt-in notes/export handoff.
## Critical Path
```mermaid
flowchart LR
    P["Done<br/>Me Completion v2"]
    L["Done<br/>One-Command Lifecycle"]
    A["Done<br/>Mixed-Utterance Separation"]
    B["Done<br/>Echo Suppression Promotion v1"]
    N["Done<br/>Neural Residual Echo v1"]
    S["Done: DO_NOT_TRAIN<br/>Speaker-Preserving<br/>Adaptation Corpus v1"]
    C["Done: READY<br/>Controlled Echo<br/>Supervision Lab v1"]
    E["Done: PROMOTE<br/>Speaker-Preserving<br/>Neural Echo v2"]
    X["Done: DO_NOT_PROMOTE<br/>Reference-Conditioned<br/>Target-Me Separation v1"]
    I["Done: READY<br/>Target-Me Identifiability<br/>Corpus v1"]
    V["Done: DO_NOT_PROMOTE<br/>Reference-Conditioned<br/>Target-Me Separation v2"]
    H["Done<br/>Evidence Notes And Export v2"]
    D["Done<br/>Release-quality CLI"]
    F["Done<br/>Reliable Final<br/>Handoff v1"]
    G["Done: split decision<br/>Authoritative<br/>Incremental ASR v1"]
    C2["Done: DO_NOT_PROMOTE<br/>Canonical Live<br/>ASR Producer v1"]
    M["Done: DO_NOT_PROMOTE<br/>Causal Canonical<br/>Mic ASR v1"]
    R["Done: PROMOTE_AUDIT_ONLY<br/>Remote Speaker<br/>Evidence Map v1"]
    T["Done: PROMOTE<br/>Anonymous Rich<br/>Transcript Handoff v1"]
    Q["Done: PROMOTE<br/>Reviewed Remote<br/>Speaker Naming v1"]
    Z["Done: READY<br/>Residual Map +<br/>Alignment/Echo v3"]
    Y["Done: DO_NOT_ADVANCE<br/>Pre-ASR Target-Me<br/>Isolation Limit v1"]
    W["Current<br/>Reviewed Speaker-Aware<br/>Meeting Memory v1"]

    P --> L --> A --> B --> N --> S --> C --> E --> X --> I --> V --> H --> D --> F --> G --> C2 --> M --> R --> T --> Q --> Z --> Y --> W
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

Completed with reproducible `DO_NOT_TRAIN`. Provenance, session-disjoint train/dev/hard-test splits,
immutable counterexamples, privacy checks and byte-stable replay are available. The frozen corpus
has enough local-only targets, but no remote-only examples pass the independent confidence gate;
synthetic pairing is therefore forbidden. No training was run and production remained unchanged.

### 6. Controlled Echo Supervision Lab v1

Done with `READY_FOR_ADAPTATION`. The durable recorder ran a frozen phase schedule across five
train, one dev and one controlled hard-test speaker-mode sessions. Accept measured echo and local
targets only when schedule, signal, local ASR and Target-Me evidence agree. Build synthetic mixtures
inside a split, preserve existing real counterexamples as hard-test only, and issue a deterministic
adaptation decision. Final replay is `1465/1465`: train has `620s` local, `640s` remote and `1804s`
synthetic; dev has `124s` local, `128s` remote and `352s` synthetic; hard-test has `68s` measured
double-talk. Local-FIR residual Target-Me removes raw echo false positives while retaining their
diagnostic evidence. No gate failed. The lab itself did not alter production; the separately gated
v2.16 corpus later promoted the personalized hybrid.

### 7. Speaker-Preserving Neural Echo v2

Completed with guarded `PROMOTE`. Small residual-mask, complex-spectral, echo-mapper and pretrained
DEC candidates established the local-preservation ceiling. The winning hybrid uses controlled
Target-Me enrollment, WavLM/Resemblyzer, authoritative remote evidence, bounded attenuation and
direct whisper.cpp checks with per-window rollback. The immutable hard test proved fail-open safety;
the sealed corpus proved utility. Candidate publication is transactional and every incompatibility,
headphones session or regression restores the exact `local_fir` fallback.

Production Requalification v2.17 replayed the same membership on current incremental ASR. Hard
`3/3`, corpus `12/12` and unchanged utility passed; production now pins v2.17.

### 8. Reference-Conditioned Target-Me Separation v1

Completed with fingerprinted `DO_NOT_PROMOTE`. All `1456` controlled artifacts, the train/dev
oracle and bounded overfit passed. Two deterministic candidates then passed seven of nine locked
dev gates; the best reached `11.470 dB` Target-Me SNR and `7.788 dB` echo SNR against `12/8 dB`
requirements. The corpus had no independently labelled non-target local speech and only one fixed
Target-Me enrollment, so correct `other_local speech` attribution was unidentifiable. Hard-test and
the sealed twelve-session corpus stayed unopened. Production v2 remained byte-exact.

### 9. Target-Me Identifiability Corpus v1

Completed with `READY_FOR_TARGET_CONDITIONED_TRAINING`. The private deterministic publication has
known Target-Me, remote echo, non-target local speech and local noise stems. Every speaker-bearing
mixture has paired correct/wrong enrollment queries over identical bytes. Split-local Target-Me
sources and enrollment audio never cross train/dev/hard; all non-target identities are fully
speaker-disjoint. Exact source replay, privacy/licensing checks and byte-level publication
verification pass. No model was trained and production remained byte-exact.

### 10. Reference-Conditioned Target-Me Separation v2

Completed with fingerprinted `DO_NOT_PROMOTE`. The v1 baseline replayed exactly. One frozen
FiLM+GRU paired-query candidate was trained three times with identical checkpoint, model-state and
report fingerprints. It learned query adherence (`4.991 dB` correct-vs-wrong margin, `0%`
collapse), but missed Target-Me, non-target and absent-query dev gates. Hard-test and sealed meeting
targets remained unopened, and production audio stayed byte-exact.

### 11. Evidence Notes And Export v2

Completed. `murmurmark.handoff_manifest/v2` and `murmurmark.handoff_evidence/v2` bind input schemas,
paths, SHA-256 values, selected profile, verdict, review burden and evidence IDs. Publication is
transactional and deterministic. Export consumes only a current `ready` or verified `no_speech`
handoff; `--force` cannot bypass review or integrity gates. The 110-session corpus passes with
`110/110` valid manifests and zero referential-integrity, stale or replay failures.

### 12. Release-quality CLI

Completed. The versioned archive has a complete SHA-256 inventory, compatibility and license
contracts, deterministic assembly and transactional install/upgrade with rollback. Packaged offline
acceptance covers strict doctor, self-test, Evidence Handoff v2 and guarded export while preserving
the external workspace and existing fingerprints.

### 13. Reliable Final Handoff v1

Completed. The first handoff excludes optional heavy candidates, enrichment has a recorded budget,
safe review uses fresh evidence, interruption has exact resume, and terminal review is a bounded
decision list. The frozen cache/resume verification passes `3/3` with p90 ratio `0.059041`, no dead
ends or stale handoffs, and exact candidate-window ASR reuse on `2/2` applicable sessions. Cold
first-pass ASR remains a separately measured ceiling.

### 14. Authoritative Incremental ASR v1

Completed with a split decision. Strict v2 identity, atomic completion, integrity checks and
byte-identical interrupted-batch replay are `PROMOTE`. Historical checkpoint/cache process reduction
is median `98.94%`. Live-origin reuse is `DO_NOT_PROMOTE`: three real frozen sessions have `0/30`
required `authoritative_live_asr_chunk/v1` proofs. Every mismatch falls back to ordinary batch ASR.

### 15. Canonical Live ASR Producer v1

Completed with `DO_NOT_PROMOTE`. A capture-safe producer reconstructs canonical remote PCM from
closed committed-PCM segments and emits exact `authoritative_live_asr_chunk/v1` proof. Strict remote
parity passes `3/3`, but evidence is historical replay and the modeled wall reduction is only
`2.8651%..4.1040%`. Ordinary Live Shadow does not start it; the evidence flag and cache promotion
gate keep the extra work quarantined.

### 16. Causal Canonical Mic ASR v1

Completed with `DO_NOT_PROMOTE`. The isolated producer formalized committed PCM, resampling and
speech-band work as causal or delayed-commit operations, then proved the current local-FIR
statistics, delay/fit choice, policy and Speaker-Preserving selection are whole-session operations.
Across three frozen real sessions, `0/147` raw-fallback windows matched final canonical mic PCM and
bounded `5/30/120s` prefix probes all failed exact parity. The strict consumer, raw capture and
selected output remained unchanged. A future latency attempt first needs a separately quality-gated
causal Echo architecture; approximate precomputation is closed.

### 17. Remote Speaker Evidence Map v1

Completed with `PROMOTE_AUDIT_ONLY`. Resemblyzer evidence, conservative major-cluster gates,
reverse-order replay and chunk replay produce stable session-local IDs while every weak interval
remains aggregate. Both 1x1 controls publish exactly one remote speaker; four group controls publish
`5`, `2`, `3` and `2`. Selected dialogue, raw remote, Evidence Handoff v2 and export remain exact.

### 18. Anonymous Rich Transcript Handoff v1

Completed with `PROMOTE_OPTIONAL_RICH`. A transactional immutable bundle binds current Evidence
Handoff v2 utterances to passing anonymous evidence. `murmurmark transcript SESSION --rich` verifies
all fingerprints before reading it. Replay, stale/fail-open, exact references and plain-output
non-regression pass on 6/6 frozen sessions.

### 19. Reviewed Remote Speaker Naming v1

Completed with `PROMOTE_OPTIONAL_REVIEWED_NAMING`. `speakers template|apply|status` accepts only
explicit fingerprint-bound session-local decisions. The optional reviewed read path preserves exact
utterances and anonymous attributions; stale or missing decisions fall back to anonymous rich.
Voice-only identity, cross-session matching and ordinary notes/export remain untouched.

### 20. Pre-ASR Target-Me Isolation Limit v1
Completed as the bounded local audio frontier. SepFormer preserved exact accounting and assigned
present Target-Me stems with `1.0` train accuracy, but presence separation was `-0.253397` and false
rejection `0.643939`. It stopped before dev with `DO_NOT_ADVANCE_STRONGER_SEPARATOR`; future-hard,
sealed, direct ASR and production remained unchanged. Reopening requires a separately qualified
abstaining Target-Me presence detector and stronger residual preservation.

### 21. Reviewed Speaker-Aware Meeting Memory v1

Current. Build a separate opt-in notes/export handoff over explicit session-local labels and exact
evidence IDs. Missing, stale or partial review returns the anonymous/aggregate fallback; ordinary
notes/export, voice identity, cross-session roster and external writes remain unchanged.

## Dependent And Parallel Research

Speaker-Preserving Neural Echo v2 remains the exact production plateau after the closed audio
frontier. Reviewed naming is promoted optional; speaker-aware memory is current. Cross-session
mapping, external integrations, heavy validators and LLM synthesis wait behind separate gates.

## Parking Lot

- Live result promotion: blocked by reproducible `DO_NOT_PROMOTE` evidence;
- docs and issue-tracker proposals: optional and reviewed before external writes;
- UI/Menu Bar: optional after release-quality CLI.

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
PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli validate docs/roadmap/murmurmark-cli-roadmap.plan.yaml
PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli render tree docs/roadmap/murmurmark-cli-roadmap.plan.yaml
```
