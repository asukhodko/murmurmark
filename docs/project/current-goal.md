# Current Goal: Remote-Forbidden Evidence Coverage v2

Status, 2026-07-01: selected as the recommended next goal.

Remote-Forbidden Evidence Hardening v1 is complete: MurmurMark now persists
`remote_forbidden_token_guard` evidence rows, exposes them through session readiness/status, and
writes a corpus report. The first six-session corpus has one safe ASR-visible improvement and zero
local-recall regressions.

The blocker is coverage. For five sampled sessions the report says
`no_baseline_asr_visible_leak`: the current ASR clip audit did not pick windows where `local_fir`
already contains recognizable remote words. That means the evidence layer is structurally sound, but
too narrow. The next goal is to make it search the right risky windows before any attempt to promote
audio or transcript changes.

## Goal

Increase the number of real sessions where MurmurMark can measure ASR-visible remote leakage in
`Me`, or explicitly prove that the sampled session has no safe remote-forbidden correction to make.

In plain words: v1 can write evidence once a suspicious window is found. v2 should find better
windows from existing artifacts: audio-review rows, transcript overlaps, group-overlap audit,
speaker state, local-recall risk, order risk and remote-only ASR candidates.

## Why This Goal Now

Post-processing already helps, but the cost remains high when remote speech reaches the `Me`
transcript. The current v1 evidence layer proved the right contract, but its own corpus report says
why it cannot yet converge:

- `safe_improved_sessions = 1/6`;
- `local_recall_regressions = 0/6`;
- the other five sessions were not negative proof against the idea; they were mostly missed-window
  cases where the selected clip did not contain baseline ASR-visible remote leakage.

So the shortest useful path is not a new neural model yet. It is a better deterministic window
selector and corpus gate around the evidence layer that already exists.

## Scope

In scope:

- build a broader remote-forbidden audit-window selector from existing derived artifacts;
- include windows around active `Me` rows with remote text similarity, audio-review `remote_leak` /
  `remote_duplicate`, group-overlap risky intervals, order-repair crossings and local-recall risk;
- cap the number of ASR windows per session so the audit remains usable;
- keep the same evidence row contract from v1;
- report why each candidate window was selected and whether it was evaluable;
- update corpus reports so `no_baseline_asr_visible_leak` is separated from `window_not_selected`,
  `local_recall_risk`, `quarantine_only` and `safe_improved`;
- keep everything shadow/review-only.

Out of scope:

- replacing `local_fir` as default;
- writing a new neural echo canceller;
- changing capture, raw CAF tracks, primary `whisper.cpp` ASR or reviewed transcript profiles;
- silently deleting uncertain `Me` text;
- automatically applying `suggest_drop`;
- claiming waveform-perfect echo removal.

## Acceptance

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are unchanged.
- `local_fir` remains the selected production Echo Guard path.
- V1 evidence row contract remains intact: remote/mic tokens, timestamps, speaker state, confidence
  and decision reason.
- Every ASR audit window has a `selection_reason` and a link to source artifacts when available.
- The six-session smoke corpus is rerun and no session loses local-only word recall by more than
  2 percentage points.
- At least two sessions either become `safe_improved` or get a stronger explicit explanation:
  `no_suspicious_windows`, `no_baseline_asr_visible_leak`, `local_recall_risk` or
  `quarantine_only`.
- `murmurmark status/report` and corpus reports expose the residual risk and window-selection
  coverage.
- No audio or transcript candidate is promoted to default.

## Working Commands

Existing v1 command:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION" --asr-audit --asr-max-clips 6
```

Evidence materialization:

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

Target user-facing shape after coverage:

```bash
murmurmark process "$SESSION"
murmurmark status "$SESSION"
murmurmark review suggested "$SESSION"
```

The coverage layer should stay evidence-only, but it should make the corpus report more decisive:
either more safe improvements, or a clear reason why a session has no safe remote-forbidden action.

## Current Finding

Six-session smoke after v1:

- `reports_found = 6/6`;
- `safe_improved_sessions = 1`;
- `local_recall_regressions = 0`;
- `suggest_drop_count = 1`;
- `quarantine_count = 8`;
- `target_status = target_not_met_only_one_safe_session`.

Interpretation: v1 satisfies persistence and visibility. v2 should improve coverage by looking at
more suspicious windows, not by relaxing local-speech safety gates.

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

# Latest Completed Goal: Remote-Forbidden Evidence Hardening v1

Status, 2026-06-30: completed as a shadow/review evidence layer. Not promoted.

## Result

The v1 hardening layer turned the first ASR-positive Echo Guard vNext result into normal pipeline
evidence:

- `remote_forbidden_evidence.jsonl` stores persistent rows with timestamps, remote/mic tokens,
  speaker state, transcript links, confidence and decision reason;
- `remote_forbidden_summary.json` stores per-session leakage before/after, local recall, guarded
  seconds, review-burden seconds and gate state;
- `remote_forbidden_review.md` gives a readable review artifact;
- `murmurmark audit remote-forbidden` materializes the layer from the CLI;
- `murmurmark status/report` exposes the remaining risk and links the review artifact;
- `report-remote-forbidden-corpus.py` writes the corpus summary and explicitly explains why fewer
  than two sessions are safely improved.

Six-session corpus:

- `reports_found = 6/6`;
- `safe_improved_sessions = 1/6`;
- `local_recall_regressions = 0/6`;
- `guarded_seconds = 28.0`;
- `review_burden_seconds = 18.0`;
- `promotion_decision = shadow_review_only_do_not_promote`.

Interpretation: the evidence contract is now real. The next weakness is coverage, not persistence.

---

# Previous Completed Goal: Echo Guard Complete Removal vNext

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
