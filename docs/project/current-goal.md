# Current Goal: Echo Guard Complete Removal vNext

Status, 2026-06-30: the first vNext shadow mechanism is implemented and checked on a six-session
smoke corpus. It produced the first ASR-positive result: `remote_forbidden_token_guard` reduced
remote-token leakage below `local_fir` on `sessions/2026-06-23_14-04-37` without local-recall
regression. Promotion is still blocked because this is a token-level safety layer, not a production
audio engine.

MurmurMark already proves the product pain: when remote speech leaks into `mic`, later transcript
repair, cleanup, audio review and manual review can reduce the damage, but they do not fully remove
the cause. The next meaningful quality goal is to attack the source again: build a stronger offline
Echo Guard lab that tries to remove remote-derived speech from the mic-derived ASR input while
preserving every phrase actually spoken into the microphone.

This is an experimental goal, not a promise to replace `local_fir` immediately.

## Goal

Build on the `offline_aec_v2_v0` lab and test mechanisms that reduce ASR-visible remote speech, not
only waveform/proxy energy. vNext currently adds:

- `segment_switch_remote_floor_local_fir`: a shadow audio candidate that uses `remote_floor` only in
  `remote_only` windows and keeps `local_fir` elsewhere;
- `remote_forbidden_token_guard`: a token-level safety candidate for ASR audit windows that removes
  tokens matching the forbidden remote reference only in `remote_only` regions;
- per-session and corpus reports comparing the result with `local_fir` and v0 candidates.

The first useful command shape should be close to:

```bash
murmurmark preprocess "$SESSION" --echo clean --echo-engine offline_aec_v2
```

The engine may stay behind scripts or hidden CLI flags until the contracts settle.

Current command:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION" --asr-audit --asr-max-clips 2
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

- segment-local audio candidate switching based on speaker-state windows;
- token-level remote-forbidden guard for `remote_only` ASR audit windows;
- ASR-token leakage and local-word recall reports for audio and token-guard candidates;
- corpus reports comparing `local_fir`, `offline_aec_v2_v0`, segment switch and token guard.

Out of scope for vNext:

- replacing `local_fir` as default;
- training a neural model;
- changing capture, raw CAF, primary `whisper.cpp` ASR or reviewed transcript profiles;
- claiming waveform-perfect echo removal;
- auto-promoting a candidate because ERLE, subjective loudness or token-guard success improved.

## Acceptance

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are unchanged.
- `local_fir` remains the default selected engine.
- New artifacts are written separately under `derived/preprocess/echo/offline_aec_v2*` and
  `derived/preprocess/audio/*offline_aec_v2*`.
- At least one difficult real session shows lower remote-token leakage than `local_fir`.
- Local-only word recall is no worse than baseline by more than 2 percentage points.
- Opening/backchannel recall is reported and any regression blocks audio promotion.
- If no candidate wins, the report clearly says which gate failed.

## Current vNext Finding

Smoke corpus:

- `sessions/2026-06-23_14-04-37`;
- `sessions/2026-06-26_15-32-02`;
- `sessions/2026-06-29_15-46-17`;
- `sessions/2026-06-29_16-31-02`;
- `sessions/2026-06-30_11-15-56`;
- `sessions/2026-06-30_17-17-20`.

Current result:

- best proxy candidate is usually `nonlinear_tail160_remote_floor`;
- `segment_switch_remote_floor_local_fir` became the best proxy candidate on the mostly-silent
  session because it preserves local regions better;
- `remote_forbidden_token_guard` passed ASR-token gates on one difficult 1x1 session:
  `remote_token_leak_delta = -0.5`, `local_only_word_recall_delta = 0.0`;
- corpus summary: `asr_candidate_gate_passed = 1/6`,
  `asr_remote_token_leak_improved = 1/6`, `asr_local_word_recall_regressions = 0/6`;
- no candidate is promoted.

Interpretation: vNext has the first ASR-positive safety mechanism, but it is not complete echo
removal. The audio candidate alone still does not beat `local_fir` on ASR-visible leakage. The next
work is to make the token guard less clip-specific and connect it to transcript/review evidence
without hiding uncertainty.

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
