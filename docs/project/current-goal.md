# Current Goal: Remote-Forbidden Evidence Hardening v1

Status, 2026-06-30: implementation started. The first shadow evidence layer now materializes
`remote_forbidden_token_guard` results into persistent evidence rows, session readiness metrics and
a corpus report. It is not promoted and does not edit transcripts.

MurmurMark has enough evidence that remote speech leaking into the mic track is a root cause of
false `Me` utterances, wrong order, extra cleanup work and remaining review burden. The previous
Echo Guard vNext spike produced the first positive result at the ASR level:
`remote_forbidden_token_guard` reduced remote-token leakage below `local_fir` on one difficult 1x1
session without local-recall regression.

That is not complete echo removal yet. It is a useful proof that MurmurMark can judge echo cleanup
by recognized words, not only by loudness, ERLE or waveform similarity. The next goal is to harden
that mechanism into an auditable safety layer.

## Goal

Make ASR-visible remote leakage measurable, reviewable and safely reducible across real sessions
without changing capture, raw CAF files, the default `local_fir` engine or the primary ASR topology.

In plain words: when a phrase from `remote` appears in the `Me` transcript, MurmurMark should be able
to prove it, mark it, and safely remove or quarantine it only when local speech is protected.

## Why This Goal Now

Post-processing already helps, but it is compensating for a bad input signal. The latest research
showed three important facts:

- waveform/proxy echo reduction can look good while ASR still recognizes remote words in `Me`;
- audio-only candidates are not ready to replace `local_fir`;
- a token-level remote-forbidden guard can improve a difficult case without losing local words.

The shortest useful path is therefore not another broad cleanup layer. It is a stricter evidence
loop around remote-derived words:

1. find ASR-visible remote leakage;
2. compare against local speech evidence;
3. choose `keep`, `quarantine`, `suggest_drop`, or `needs_review`;
4. report the decision with audio, transcript and speaker-state evidence.

## Scope

In scope:

- expand ASR audit windows beyond the first clip-level spike;
- persist remote-forbidden evidence rows with timestamps, source tokens and confidence;
- link guard decisions to transcript/review artifacts instead of keeping them only in lab reports;
- enforce local-speech gates using speaker state, local recall and available audio-judge evidence;
- produce per-session and corpus summaries: remote-token leakage before/after, local-word recall,
  guarded seconds and review burden;
- keep all candidate outputs shadow-only until corpus gates pass.

Out of scope:

- replacing `local_fir` as default;
- writing a new neural echo canceller;
- changing capture, raw CAF tracks, primary `whisper.cpp` ASR or reviewed transcript profiles;
- silently deleting uncertain `Me` text;
- claiming waveform-perfect echo removal.

## Acceptance

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are unchanged.
- `local_fir` remains the selected production Echo Guard path.
- Every remote-forbidden action has evidence: remote tokens, mic tokens, timestamps, speaker state and
  review classification.
- Local-only word recall is no worse than baseline by more than 2 percentage points.
- At least two difficult real sessions show lower ASR-visible remote leakage, or the report explains
  why only one case is currently safely fixable.
- No candidate is promoted to default from proxy metrics alone.
- `murmurmark status`, corpus reports or review artifacts expose the remaining risk instead of hiding
  it inside a cleaned transcript.

## Working Commands

Current lab command:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION" --asr-audit --asr-max-clips 2
```

Current evidence command:

```bash
murmurmark audit remote-forbidden "$SESSION" --profile auto --asr-max-clips 6
```

Refresh evidence only:

```bash
murmurmark audit remote-forbidden "$SESSION" --skip-lab --profile auto
```

Corpus summary:

```bash
.venv/bin/python scripts/report-offline-aec-v2-corpus.py SESSION...
.venv/bin/python scripts/report-remote-forbidden-corpus.py SESSION...
```

Target user-facing shape after hardening:

```bash
murmurmark process "$SESSION"
murmurmark status "$SESSION"
murmurmark review suggested "$SESSION"
```

The hardened layer should appear as evidence and review decisions in the normal pipeline before it
is considered for any default audio change.

## Current Finding

Six-session smoke after materializing evidence:

- `reports_found = 6/6`;
- `safe_improved_sessions = 1`;
- `local_recall_regressions = 0`;
- `suggest_drop_count = 1`;
- `quarantine_count = 8`;
- `target_status = target_not_met_only_one_safe_session`.

Interpretation: the first hardening layer satisfies persistence and visibility, but not the full
two-session acceptance target. The next step is broader audit-window coverage or a better candidate
source that can produce at least one more safe ASR-visible improvement.

## Larger Goals After This

1. ASR-positive audio candidate v2: make an actual audio candidate beat `local_fir` on remote-token
   leakage without local recall loss.
2. Target-Me extraction spike: use high-confidence local speech to separate the user's voice in
   difficult double-talk and open-space noise.
3. Neural residual echo suppression spike: test a narrow local model only after the evidence gates
   are stable.
4. Transcript-level remote-forbidden reconciliation: keep a final safety net even when audio cleanup
   improves.

## References

- [Complete Echo Removal Research](../research/2026-06-30-complete-echo-removal.md)
- [Echo Guard architecture](../architecture/echo-suppression.md)
- [Mic remote bleed reduction backlog](../backlog/mic-remote-bleed-reduction.md)
- [CLI roadmap](../roadmap/murmurmark-cli-roadmap.md)

---

# Latest Completed Goal: Echo Guard Complete Removal vNext

Status, 2026-06-30: completed as a shadow research spike. Not promoted.

## Result

The vNext spike added:

- `segment_switch_remote_floor_local_fir`: a shadow audio candidate that uses `remote_floor` only in
  `remote_only` windows and keeps `local_fir` elsewhere;
- `remote_forbidden_token_guard`: a virtual ASR safety candidate for audit windows that removes
  forbidden remote-reference tokens only in `remote_only` regions;
- per-session and corpus reports comparing the result with `local_fir` and v0 candidates.

Smoke corpus:

- `sessions/2026-06-23_14-04-37`;
- `sessions/2026-06-26_15-32-02`;
- `sessions/2026-06-29_15-46-17`;
- `sessions/2026-06-29_16-31-02`;
- `sessions/2026-06-30_11-15-56`;
- `sessions/2026-06-30_17-17-20`.

Outcome:

- `remote_forbidden_token_guard` passed ASR-token gates on one difficult 1x1 session:
  `remote_token_leak_delta = -0.5`, `local_only_word_recall_delta = 0.0`;
- hardened corpus summary: `safe_improved_sessions = 1/6`,
  `remote_token_leak_improved_sessions = 1/6`, `local_recall_regressions = 0/6`;
- the other sampled sessions currently explain as `no_baseline_asr_visible_leak`: the selected
  ASR-positive windows do not contain measurable remote-token leakage in the `local_fir` baseline, so
  they cannot safely count as fixed cases yet;
- no audio candidate beat `local_fir` well enough for promotion.

Interpretation: vNext proved the right measurement direction. It did not solve complete echo
removal. The next step is broader window selection and stronger evidence, not default replacement.

---

# Previous Completed Goal: Export Bundle Quality v1

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

## Commit

`6362d12 feat: improve export bundle handoff`
