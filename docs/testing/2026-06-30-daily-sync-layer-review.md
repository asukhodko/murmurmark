# 2026-06-30 Daily Sync Layer Review

Session: `sessions/2026-06-30_11-15-56`

This note records pipeline behavior only. It intentionally avoids meeting-domain details and
transcript snippets.

## Summary

The recording layer worked as intended. The full pipeline completed, selected `audit_cleanup_v2`,
and produced notes, transcript, quality verdict and review handoff. The final verdict was `risky`,
but the main risk is not capture loss or harmful remote leakage. The blocking issue is unresolved
review around transcript order and a small possible local-recall loss.

The important product finding: local stronger-audio evidence already confirms most of the blocking
order-review rows as real `Me` plus real `Colleagues` timing/double-talk. MurmurMark should turn that
evidence into a first-class suggested review closure flow before asking the user to listen manually.

## Layer Results

- Capture: passed.
  - `status`: `completed`
  - `stop_reason`: `sigint`
  - `explicit_stop`: `true`
  - `partial`: `false`
  - `screen_capture_restart_count`: `0`
  - mic and remote tracks are non-empty and almost equal in duration.

- Echo Guard: safe fallback.
  - local FIR produced candidate outputs and role-masked audio.
  - conservative gate rejected clean mic for ASR because remote-only reduction was too low.
  - fallback stayed on raw mic for ASR, while role-masked/clean audio remained useful for audits.

- ASR and timeline repair: mostly working, still noisy in group overlap regions.
  - `utterances`: `242`
  - `unrepaired_long_mic_crossings_count`: `0`
  - `micro_reasr_success_count`: `26 / 36`
  - `local_only_island_recall`: `0.72973`
  - `needs_review_count`: `17`

- Group overlap and cleanup: conservative and safe.
  - harmful overlap seconds after cleanup: `0.0`
  - benign/timing overlap seconds: `29.88`
  - review overlap seconds: `67.95`
  - cleanup applied no drops, which is correct for this evidence shape.

- Audio review: no probable transcript errors.
  - local audio-review pack: `63 / 63` likely reliable.
  - probable errors: `0`.
  - stronger audio judge processed the selected hard cases and suggested `keep_me` for most of them.

- Readiness: correctly blocks unattended use.
  - selected profile: `audit_cleanup_v2`
  - verdict: `risky`
  - gate: `do_not_use_without_manual_review`
  - review burden: `76.28s` / `4.64%`
  - blockers: quality verdict, transcript order risk, possible lost local speech.

- Review loop: usable but not convergent enough yet.
  - `review next` selects `check_transcript_order`.
  - first lane has `12` rows.
  - suggested answer sheet contains safe `keep_me` hints for most rows.
  - dry-run with suggested answers reviews `9 / 12` rows and leaves `3` order rows todo.
  - plus local recall leaves `2` short manual checks.

## Product Diagnosis

The current CLI already knows enough to reduce the manual queue substantially, but this knowledge is
not yet reflected as a first-class readiness transition. The user sees `risky` and is sent into
manual review, while the local judge has already explained most of that risk as timing/double-talk.

The next improvement should not change capture, Echo Guard or the main ASR. It should improve the
review/readiness layer:

1. Preview suggested review closure explicitly.
2. Show how many rows stronger local audio evidence can close.
3. Show the exact manual remainder after suggested apply.
4. Apply only safe suggested `keep_me`/drop decisions through existing review gates.
5. Refresh readiness and corpus delta after suggested closure.

## Roadmap Impact

The next recommended goal is `Suggested review closure v2`, before export-bundle polish. Export
should still remain near-term, but this session shows that export polish is premature while good
local evidence is not yet fully used to reduce the review queue.

## Follow-up Check

Implemented CLI path:

```bash
murmurmark review suggested sessions/2026-06-30_11-15-56
murmurmark review suggested apply sessions/2026-06-30_11-15-56
```

Observed effect on this session:

- suggested preview closed `9` rows and left `5` manual actions;
- partial apply wrote only reviewed suggested rows;
- `reviewed_v1` readiness was refreshed through `--allow-partial-review`;
- the remaining manual queue became `3` transcript-order checks plus `2` local-recall checks;
- capture, Echo Guard, ASR cache and raw CAF tracks were not changed.
