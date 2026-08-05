# Current Goal

Status: current

Updated: 2026-08-05

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded production
audio profile. Evidence Handoff v2 remains the only input to guarded export.

Release-quality CLI is complete. The next constraint is operational convergence: a valid recording
can still spend hours in post-processing, and a blocking review state can remain without an
executable next action. This violates the product mission before aggregate `Colleagues` becomes the
most important limitation.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Reliable Final Handoff v1

OpsKarta nearest goal: Reliable Final Handoff v1: сделать путь `murmurmark meeting -> first Ctrl-C -> final result` ограниченным по времени, возобновляемым и полностью исполнимым: не позволять тяжёлым улучшателям бесконечно задерживать первый authoritative transcript, переиспользовать ASR для неизменившихся окон и исключить блокирующий review без машинно-читаемого следующего действия, сохранив raw CAF, качество и Evidence Handoff v2.

## Why This Is Next

The frozen lifecycle snapshot contains 34 sessions: 29 ended `ready_with_review`, one `ready`, three
`failed` and one `interrupted`. Across 29 usable runs, median post-stop time is `1746.216s`, p90 is
`2982.234s`, and the maximum is `19467.679s`. The two latest long meetings spent `4.545x` and
`6.383x` their recorded duration after stop.

The latest session eventually produced a usable reviewed transcript, but the original lifecycle
spent `13274.232s` inside `process`, skipped an independently safe suggested review, and retained a
stale blocking handoff after later review. Anonymous remote speakers improve semantics; bounded and
actionable completion is required for the basic product promise.

The reproducible baseline is recorded in
`docs/testing/2026-08-05-reliable-final-handoff-baseline.md`.

## Objective

Make one `murmurmark meeting` invocation converge after the first `Ctrl-C` to a truthful final
handoff within an explicit budget. Required work may resume from checkpoints. Optional or expensive
candidates may be deferred, but they cannot silently hold the first usable authoritative transcript.
Every blocking result must contain either an executable next command or a concrete bounded human
decision item.

## Required Work

1. Add a deterministic corpus report for lifecycle latency, stage timings, completion state and
   dead-end blockers; freeze its input report hashes.
2. Separate the first authoritative handoff from optional heavy improvement work in the lifecycle
   contract. Persist an explicit degraded/deferred reason when a budget is exhausted.
3. Verify resource-profile propagation after capture. Keep capture undemoted; allow configured
   low-priority work-conserving processing after raw writers close.
4. Make Speaker-Preserving Neural Echo candidate ASR sparse and cache-aware: unchanged windows reuse
   exact baseline evidence; only affected windows may invoke whisper.cpp again.
5. Re-evaluate suggested review after enrichment using current fingerprints and apply only rows that
   pass existing safety gates. Refresh lifecycle, selected profile and handoff afterward.
6. Enforce the actionability invariant: an export or review blocker must map to an allowlisted command
   or a machine-readable manual item with interval, evidence and allowed decisions.
7. Show useful stage progress, elapsed time and bounded ETA; interruption must leave one exact resume
   command and no stale `running` state.
8. Add fixture, cache, timeout, interruption, stale-evidence, no-actionable-lane and corpus regression
   tests. Re-run the two latest outlier sessions from valid caches.
9. Reconcile README, contracts, runbooks, roadmap and OpsKarta with measured results.

## Safety Boundary

- raw CAF, selected transcript text and evidence IDs remain immutable inputs;
- no quality gate is weakened to improve latency;
- timeout or missing optional evidence fails open to a baseline transcript or explicit review;
- auto-review uses only already allowlisted answers and existing safety checks;
- no new ASR model, cloud service, diarization, live promotion or UI;
- Remote Speaker Evidence Map v1 remains the next goal and keeps its previously defined scope.

## Acceptance Gates

- every valid supported capture produces an authoritative transcript or an explicit failed reason;
- p90 `total_after_stop / capture` is at most `1.0` on the eligible frozen corpus;
- no compatible session exceeds ratio `2.0` without an explicit budget/degraded reason and resume
  action;
- unchanged candidate windows cause zero duplicate ASR work and exact cache replay;
- `open_review_lanes == 0` cannot coexist with an opaque export-blocking review state;
- safe suggested closure discovered after enrichment is applied inside the same lifecycle;
- lifecycle report, selected profile, Evidence Handoff v2 and CLI accessors agree after every action;
- local recall, chronology, remote-like `Me`, protected content, notes evidence and guarded export do
  not regress;
- repeated cached runs are deterministic and raw CAF SHA-256 values remain unchanged.

## Definition Of Done

- implementation, schemas, timing report and machine-readable budget reasons exist;
- fixture and frozen-corpus gates pass, including the two August 5 latency outliers;
- a second `Ctrl-C` and a process failure both leave a tested exact resume path;
- a normal successful lifecycle ends with the final artifact paths and no hidden required command;
- the corpus decision and remaining runtime ceiling are documented;
- README, contracts, runbooks, current goal, roadmap and OpsKarta agree;
- full static, Swift, privacy, open-source, planning and relevant corpus checks pass;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- remote-speaker diarization, naming and `transcript.rich.json`;
- another Echo or Target-Me model;
- changing the primary whisper.cpp model;
- cloud APIs, LLM synthesis, UI or live-result promotion.
