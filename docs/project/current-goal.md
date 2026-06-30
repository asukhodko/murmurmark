# Current Goal: Echo Guard Complete Removal v0

Status, 2026-06-30: v0 shadow lab is implemented and checked on a six-session smoke corpus.
Promotion is blocked: proxy metrics improved, but ASR-token gates did not show an improvement over
`local_fir`.

MurmurMark already proves the product pain: when remote speech leaks into `mic`, later transcript
repair, cleanup, audio review and manual review can reduce the damage, but they do not fully remove
the cause. The next meaningful quality goal is to attack the source again: build a stronger offline
Echo Guard lab that tries to remove remote-derived speech from the mic-derived ASR input while
preserving every phrase actually spoken into the microphone.

This is an experimental goal, not a promise to replace `local_fir` immediately.

## Goal

Create a shadow-only `offline_aec_v2_v0` lab for already recorded sessions. It generates several
cleaned mic candidates and ranks them by product metrics:

- fewer remote words recoverable as `Me`;
- no loss of real local speech, including greetings, backchannels and overlap comments;
- no new transcript-order or local-recall blockers;
- no severe clipping, dropout or artifact flags.

The first useful command shape should be close to:

```bash
murmurmark preprocess "$SESSION" --echo clean --echo-engine offline_aec_v2
```

The engine may stay behind scripts or hidden CLI flags until the contracts settle.

Current command:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION"
```

Corpus summary:

```bash
.venv/bin/python scripts/report-offline-aec-v2-corpus.py SESSION...
```

ASR clip audit for a candidate session:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION" --asr-audit --asr-max-clips 2
```

## Why This Goal Now

Current evidence across real sessions shows that residual remote bleed is a root cause of:

- false `Me` utterances;
- long mixed `Me` blocks that require timeline repair;
- review queues around order, duplicate and local-recall risks;
- imperfect final transcript even after cleanup.

Post-processing remains necessary, but it is compensating for a bad mic signal. A stronger Echo
Guard candidate can reduce the burden before ASR and make every later layer simpler.

## Scope

In scope:

- delay trajectory instead of a single median delay;
- long-tail partitioned adaptive filtering, starting with 80/160/320 ms tails;
- nonlinear remote basis experiments: clipped, compressed, band-limited and signed-power remote;
- conservative residual echo spectral mask;
- per-segment candidate scoring by ASR-level remote leakage and local-word preservation;
- corpus reports comparing `local_fir` and `offline_aec_v2_v0`.

Out of scope for v0:

- replacing `local_fir` as default;
- training a neural model;
- changing capture, raw CAF, primary `whisper.cpp` ASR or reviewed transcript profiles;
- claiming waveform-perfect echo removal;
- auto-promoting a candidate because ERLE or subjective loudness improved.

## Acceptance

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are unchanged.
- `local_fir` remains the default selected engine.
- New artifacts are written separately under `derived/preprocess/echo/offline_aec_v2*` and
  `derived/preprocess/audio/*offline_aec_v2*`.
- At least one difficult real session shows lower remote-token leakage than `local_fir`.
- Local-only word recall is no worse than baseline by more than 2 percentage points.
- Opening/backchannel recall does not regress.
- If no candidate wins, the report clearly says which gate failed.

## Current v0 Finding

Smoke corpus:

- `sessions/2026-06-23_14-04-37`;
- `sessions/2026-06-26_15-32-02`;
- `sessions/2026-06-29_15-46-17`;
- `sessions/2026-06-29_16-31-02`;
- `sessions/2026-06-30_11-15-56`;
- `sessions/2026-06-30_17-17-20`.

Current result:

- best proxy candidate is now `nonlinear_tail160_remote_floor`;
- proxy harmful-remote seconds improved on all six sessions;
- remote-only reduction dB improved on all six sessions;
- local-only recall proxy regressed on one mostly-silent session;
- opening/backchannel recall proxy improved on four sessions and regressed on two;
- proxy gates passed on three sessions and failed on three;
- all checked ASR-token audits are blocked with
  `no_candidate_reduced_remote_tokens_without_local_recall_regression`;
- no candidate is promoted.

Interpretation: the residual mask and `remote_only` floor hypotheses are useful for diagnostics and
can match `local_fir` on sampled ASR leakage, but v0 did not prove a better production signal. The
next work should not tune dB-only metrics. It should test mechanisms that can reduce ASR-visible
remote tokens below `local_fir`: better speaker-state segmentation, segment-local candidate
switching, target-speaker extraction, or token-level remote-forbidden decoding.

## References

- [Complete Echo Removal Research](../research/2026-06-30-complete-echo-removal.md)
- [Echo Guard architecture](../architecture/echo-suppression.md)
- [Mic remote bleed reduction backlog](../backlog/mic-remote-bleed-reduction.md)

---

# Latest Completed Goal: Export Bundle Quality v1

Status, 2026-06-30: completed.

MurmurMark now ends a successful meeting pipeline with a local handoff bundle that can be read as
the user-facing result, not as a directory of internal derived artifacts.

## Goal

After `record`, `process` and the minimal required review, the user should be able to run:

```bash
murmurmark finish SESSION
```

The resulting Markdown or Obsidian bundle should answer:

- can this result be used;
- which transcript profile was selected;
- what the verdict and review burden are;
- which notes, decisions, actions, risks and open questions were extracted;
- which utterance IDs support the notes;
- which regions still need review;
- where the full transcript is;
- what the retention/privacy next step is.

## Completed Scope

- `index.md` is now the start page with "Can I use this?", selected profile, verdict, review burden,
  review-needed items, retention/privacy summary and the next command.
- `quality_verdict.md` is rendered as a human trust report rather than a raw metrics dump.
- `notes.md` is rendered as a readable extractive working summary with evidence IDs and review
  markers.
- `transcript.md` is rendered from `clean_dialogue*.json` when available and keeps roles, timestamps,
  utterance IDs, source track and review flags.
- Obsidian export writes one self-contained frontmatter note with verdict, notes, review items and
  transcript content.
- `export_manifest.json` records `bundle_quality: "v1"`.
- Export still blocks honestly when readiness has blockers. Forced exports remain debug artifacts and
  say "Do not use yet".
- Raw audio is not copied into export bundles.

## Out Of Scope

- Changing capture, Echo Guard or the main ASR path.
- Making risky sessions look exportable.
- Auto-deleting raw audio.
- Writing directly to Obsidian, Jira, docs repositories or external providers.
- Generating unsupported summaries without evidence IDs.

## Verification

- `swift build`
- `.venv/bin/python -m py_compile scripts/*.py`
- `git diff --check`
- `scripts/smoke-cli-handoff.sh`
- `scripts/smoke-fixture.sh`
- `scripts/check-open-source-readiness.sh`
- `scripts/check.sh`
- opskarta v3 validation for `docs/roadmap/murmurmark-cli-roadmap.plan.yaml`
- Manual export checks on:
  - an exportable session;
  - a real forced/debug 1x1 session;
  - a real blocked/forced group session;
  - an Obsidian single-note export.

## Commit

`6362d12 feat: improve export bundle handoff`

## Next Direction

The next large goal moves back to the audio root cause: reduce the remote leak before ASR with a
shadow offline Echo Guard lab, while keeping the existing capture, raw files, `local_fir` default and
review gates intact.
