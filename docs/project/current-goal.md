# Current Goal Context

This file keeps the latest goal context and the most relevant completed goals. The stable
production path remains non-live `record -> process`. The current live work is evidence gathering
and gate hardening only: live output must stay shadow-only and batch transcript remains
authoritative.

## Current Live Evidence Status, 2026-07-10

`record --experiment live-shadow-v1` preserved raw capture on a real daily sync even when
ScreenCaptureKit restarted once. The session completed with warning, but `mic` and `remote` CAF
covered the full meeting and batch processing produced the authoritative transcript.

Important correction: the safe sidecar must not read still-open CAF files. The current direction is
segment-level realtime through committed PCM: after raw write succeeds, a bounded nonblocking queue
writes closed experiment segments and the live worker drafts from those files. `raw_segment_commits`
remain evidence and fallback, while batch remains authoritative.

The current live-parity blocker is profile quality, not raw capture or lack of more recordings. The
fresh corpus report still keeps promotion blocked and says no additional recording is required for
the current implementation step. Materializing Target-Me/local-recall candidates exposed a previous
measurement gap: the active `capture_safe_candidate` order slice was not advisory-only. The current
best live-implementable profile is
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_voice_activity_token_density_target_me_remote_gap_trim_micro_asr_v1`.
It corrects coarse Whisper starts from committed chunk evidence, then shifts long remote starts past
low-confidence prefixes only after a dense reliable token cluster. It also uses a temporal prior for
short generic matches. Profile-level contentful order mismatches are now `2`: `0` blocking and `2`
advisory in the active capture-safe unlock slice. Across all 14 refreshed real sessions the profile
has `5` advisory gate rows and full historical triage retains one blocking row outside the active
slice. The remote-gap trim layer keeps token-timestamped pieces of strongly confirmed Target-Me
segments only between guarded remote intervals. It closes two live-visible rows / `15.38s` without
increasing remote leakage or order risk. A live-only short-window micro-ASR follow-up accepts `3`
pieces / `10.74s`, rejects `3` unsafe or already covered candidates, and closes the final known
remote-dominant Target-Me row / `4.68s`. Full-profile missing Me is now `714.81s`; the classified
remaining-gap set is `81` rows / `268.01s`, and remote-like Me is `40.29s`. The next implementation
action returns to the broader `fix_live_local_recall_gap`. Batch remains authoritative.

## Latest Completed Goal: Experimental Sidecar Contract v1

Status, 2026-07-07: complete.

Goal:

```text
MurmurMark Experimental Sidecar Contract v1: реализовать безопасный single-capture
experimental sidecar-контур, где raw CAF остаётся единственным источником истины, а live/near-
realtime артефакты пишутся отдельно, best-effort, с машинно-проверяемым доказательством, что
эксперимент не повлиял на raw capture и batch pipeline.
```

Plainly: live work must have a passport. It may produce chunks, draft text and comparison reports,
but it must also prove where those files are, whether it fell behind, whether it disabled itself,
whether raw capture was affected, and how to recover batch processing from raw CAF without starting
a new recording.

Current implementation target:

- `derived/experiments/live-shadow-v1/experiment_manifest.json`;
- `derived/experiments/live-shadow-v1/state.json`;
- `derived/experiments/live-shadow-v1/events.jsonl`;
- `derived/experiments/live-shadow-v1/report.json`;
- `derived/experiments/live-shadow-v1/report.md`;
- `murmurmark experiment status|report|compare SESSION|latest`;
- fail-open smoke: artificial sidecar backpressure disables only sidecar artifacts while raw CAF and
  batch recovery remain valid.

Batch transcript remains authoritative. Live promotion remains blocked.

Definition of done:

- controlled live pilot writes the experiment contract;
- `state.json` answers raw seconds, sidecar seconds, backpressure/disabled state,
  `raw_capture_affected` and batch reproducibility from raw CAF;
- `murmurmark process SESSION` works from raw files even when sidecar fails;
- smoke tests cover sidecar backpressure and contract schema;
- docs/contracts/roadmap describe the experiment namespace and keep `derived/live` as compatibility.

Completion evidence:

- `murmurmark live pilot --duration 8 --segment-sec 5 --overlap-sec 1 --skip-safety-gate --out sessions/_sidecar-contract-pilot-smoke`
  wrote `derived/experiments/live-shadow-v1/experiment_manifest.json`, `state.json`,
  `events.jsonl`, `report.json` and `report.md`;
- `murmurmark experiment compare sessions/_sidecar-contract-pilot-smoke --experiment live-shadow-v1`
  refreshed comparison and contract with `raw_capture_affected: false`,
  `batch_authoritative: true`, `promotion_allowed: false`;
- `scripts/smoke-experimental-sidecar-contract.sh` covers normal and backpressure contract fixtures;
- `scripts/smoke-live-segment-fail-open.sh` verifies raw capture survives overloaded sidecar queue
  and writes fail-open contract evidence;
- `MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh` passed;
- `scripts/check.sh` passed.

## Current Goal: Near-Realtime Live Parity Coverage v1

Status, 2026-07-10: active. Blocking live order risk is cleared in the current best profile; local
recall, remote leakage, review burden and notes readiness still block promotion. Capture loss and
lack of recordings are not the current blockers.

Goal:

```text
Near-Realtime Live Parity Coverage v1: получить реальные live-pipeline сессии, сравнить live
chunks/drafts с batch output и держать live promotion заблокированным, пока parity gates не докажут
безопасность по order risk, local recall, remote leakage, review burden, selected notes readiness и
chunk-boundary risks. Batch transcript остаётся authoritative.
```

Plainly: live can be studied only as a shadow speed-up candidate. The user-facing product path stays
batch-first until live proves that it does not damage raw capture, preserves local speech, does not
introduce remote leakage or ordering errors, keeps review burden acceptable, produces notes-ready
batch output and does not break on chunk boundaries.

Current state:

- live sessions in the corpus: `27`;
- real live sessions in the corpus: `14`;
- diagnostic/lab live sessions kept out of promotion scope: `13`;
- real live-vs-batch compared sessions: `11`;
- meaningful real comparisons: `7`;
- real passing comparisons: `1`;
- capture-safe candidate sessions: `4`;
- capture-safe candidate passing sessions: `1`;
- promotion decision: `shadow_only_do_not_promote`;
- new real live collection allowed: `false`;
- controlled real live pilot collection is allowed as evidence collection; batch remains
  authoritative and promotion remains blocked;
- current blocking dimensions: `capture_safety`, `order_risk`, `local_recall`, `remote_leakage`,
  `review_burden`, `selected_notes_readiness`, `chunk_boundary_risks`, `draft_text_recall`,
  `required_artifacts`.
- capture-safe candidate blocking dimensions: `local_recall`, `remote_leakage`, `review_burden`,
  `selected_notes_readiness`;
- current best live-implementable profile: voice-activity plus token-density boundary retime,
  Target-Me remote-gap trim and focused live-only micro-ASR over the existing
  local-speaker/split-retime shadow;
- active capture-safe order-risk triage: `2` advisory timing/match ambiguities and `0` blocking rows;
- historical full-corpus triage: `4` advisory rows and `1` blocking row outside the active slice;
- best-profile full-real gaps: `714.81 sec` missing Me and `40.29 sec` remote-like Me;
- classified remaining-gap set: `81` rows / `268.01 sec` missing Me;
- current objective next focus: `fix_live_local_recall_gap`.

Safety constraint:

- do not use `--live-pipeline` as the normal production recording path while live is quarantined;
- use `record --experiment live-shadow-v1` only as controlled Live Evidence, not as a production
  transcript source;
- historical unsafe live sessions remain negative evidence and must not be treated as promotion
  candidates;
- batch transcript remains authoritative and promotion remains blocked.

Definition of done:

- existing real live sessions are classified as real-vs-diagnostic in corpus reports;
- every real live session either has a live-vs-batch comparison or a precise blocker explaining why
  comparison is impossible;
- promotion dimensions are separated and machine-readable: capture safety, order risk, local
  recall, remote leakage, review burden, selected notes readiness, chunk-boundary risks, draft text
  recall and required artifacts;
- strict gates fail while any required dimension is warning/failed/blocked/not evaluated;
- `promotion_policy` keeps `batch_authoritative: true`, `live_quarantined: true`,
  `new_real_live_collection_allowed: false` and `promotion_allowed_sessions: 0`;
- after full fail-open proof, `controlled_real_live_pilot_allowed: true` is not enough to record a
  valuable meeting; the runner still blocks new real live capture without
  `--allow-unsafe-controlled-real-recording`;
- README/runbooks/contracts/roadmap make clear that live is quarantined and that corpus reports are
  evidence, not a command to collect new unsafe live meetings.

Latest verification:

```bash
POLICY=online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_voice_activity_token_density_target_me_remote_gap_trim_micro_asr_v1
.venv/bin/python scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source target-me-remote-gap \
  --source-scope live
.venv/bin/python scripts/report-live-corpus-gates.py all \
  --refresh \
  --refresh-lab-policy "$POLICY"
```

Current result:

- `status = shadow_only_not_promotable`;
- `promotion_decision = shadow_only_do_not_promote`;
- `promotion_allowed_sessions = 0`;
- live/batch comparison granularity: ASR segment when available, chunk fallback otherwise;
- current best profile evaluates all `14` real live sessions and passes all parity gates on `1`;
- the profile has `5` advisory gate-level contentful order mismatches and `0` blocking ones across
  the refreshed real corpus;
- active capture-safe order triage: `2` advisory timing/match rows and `0` blocking rows;
- historical full-corpus triage: `4` advisory rows and `1` blocking row outside the active slice;
- remote-gap trim materializes `42` pieces / `176.262 sec` and closes `15.38 sec` of missing Me;
- focused live-only micro-ASR adds `3` non-duplicate pieces / `10.74 sec`, rejects `3`, and closes
  another `4.68 sec` of missing Me;
- best-profile full-real gaps: `714.81 sec` missing Me and `40.29 sec` remote-like Me;
- classified remaining-gap set: `81` rows / `268.01 sec` missing Me;
- `coverage_path = resolve_capture_safe_candidate_blockers`;
- `objective_next_focus = fix_live_local_recall_gap`;
- `live_next_unlock.next_actions[0] = fix_live_local_recall_gap`;
- first action scope: `81` rows / `268.01 sec`; broad rescue remains forbidden without stronger
  local-speaker evidence.

`--refresh-lab-policy` evaluates one selected shadow profile. Use `--with-labs` only for deliberate
full laboratory sweeps: it materializes every exploratory policy and is too expensive for routine
corpus refreshes.

Latest suppressed-mic threshold lab, 2026-07-09:

```bash
.venv/bin/python scripts/report-suppressed-mic-policy-lab.py
.venv/bin/python scripts/report-suppressed-mic-policy-lab.py \
  --scope real \
  --out sessions/_reports/live-pipeline/suppressed_mic_policy_lab.real.json
```

Current result:

- capture-safe candidate scope: `2` sessions, `48` suppressed mic ASR segments / `250.28s`;
- candidate scope batch labels: `13.06s` local/mixed, `237.22s` remote-risk;
- best zero-risk generated threshold rule: `1.80s` local recovered;
- best <=3s remote-risk threshold rule: `1.80s` local recovered;
- full real scope: `10` sessions, `317` suppressed mic ASR segments / `3656.50s`;
- full real scope batch labels: `409.50s` local/mixed, `3247.00s` remote-risk;
- best zero-risk generated threshold rule: `27.78s` local recovered;
- best <=3s remote-risk generated threshold rule: `60.16s` local / `2.58s` remote-risk;
- best high-recall threshold family quickly becomes unsafe: `249.56s` local / `213.94s`
  remote-risk.

Conclusion: simple live-accessible thresholds over text overlap, RMS, mic-minus-remote energy and
zero-lag correlation are not enough as the main local-recall fix. They can supply a small safe
shadow rescue, but closing the current goal needs stronger local-speaker evidence, remote-forbidden
evidence, or a local judge for suppressed mic regions. New recordings are not the blocker right now;
the current corpus is sufficient to prove this design direction.

Latest live Target-Me enrollment lab, 2026-07-09:

```bash
.venv/bin/python scripts/report-live-target-me-enrollment-lab.py --method resemblyzer_dvector
.venv/bin/python scripts/report-live-target-me-enrollment-lab.py \
  --method resemblyzer_dvector \
  --scope real \
  --out sessions/_reports/live-pipeline/live_target_me_enrollment_lab.real.json
```

Current result:

- capture-safe candidate scope: `0` positive live `Me` enrollment segments; prefix and full-live
  enrollment both recover `0.00s`;
- full real scope: `12` positive live `Me` enrollment segments / `96.26s`, but they are concentrated
  in one useful session for local-recall rescue;
- prefix-live enrollment: `73.76s` local/mixed are ready for causal evaluation, but
  `confirmed_remote_guard` recovers only `9.24s` local / `0.00s` remote-risk;
- full-session live enrollment ceiling: `confirmed_remote_guard` recovers `56.94s` local /
  `0.00s` remote-risk;
- broader prefix policies become unsafe: `confirmed` gives `27.64s` local / `11.68s` remote-risk,
  and `possible` gives `55.56s` local / `20.32s` remote-risk.

Conclusion: same-session live-published `Me` is not enough as a causal enrollment source yet. A
Target-Me-based live rescue needs an enrollment fallback, warmup/calibration step, persistent local
speaker profile, or another online speaker signal. Without that, the live path cannot recover enough
missing `Me` speech to satisfy parity gates.

Latest persistent Target-Me profile lab, 2026-07-09:

```bash
.venv/bin/python scripts/report-persistent-target-me-profile-lab.py \
  --method resemblyzer_dvector \
  --max-enrollment-segments 40 \
  --max-negative-enrollment-segments 40
.venv/bin/python scripts/report-persistent-target-me-profile-lab.py \
  --method resemblyzer_dvector \
  --scope real \
  --max-enrollment-segments 40 \
  --max-negative-enrollment-segments 40 \
  --out sessions/_reports/live-pipeline/persistent_target_me_profile_lab.real.json
```

Current result:

- capture-safe candidate scope: `2` sessions, `48` suppressed mic ASR segments / `250.28s`;
- capture-safe candidate labels: `13.06s` local/mixed, `237.22s` remote-risk;
- capture-safe `confirmed_remote_guard`: `0.00s` local / `0.00s` remote-risk;
- full real scope: `10` sessions, `317` suppressed mic ASR segments / `3656.50s`;
- full real labels: `409.50s` local/mixed, `3247.00s` remote-risk;
- full real `confirmed_remote_guard`: `75.72s` local / `8.64s` remote-risk;
- broader full real `possible`: `127.32s` local / `112.28s` remote-risk.

Conclusion: historical persistent Target-Me evidence can help as a supporting signal, but it is not
safe enough as the main suppressed-mic rescue path. The stricter capture-safe candidate scope gets no
material recovery, while the full real scope already leaks remote-risk speech under the conservative
guard. More new recordings are not the blocker for the current objective; the existing corpus already
proves that the next implementation needs stronger remote-forbidden evidence, stricter online role
gating, or a better calibrated local-speaker judge before live promotion can be considered.

Latest suppressed mic composite gate lab, 2026-07-09:

```bash
.venv/bin/python scripts/report-suppressed-mic-composite-gate-lab.py
.venv/bin/python scripts/report-suppressed-mic-composite-gate-lab.py \
  --scope real \
  --out sessions/_reports/live-pipeline/suppressed_mic_composite_gate_lab.real.json
```

Current result:

- capture-safe candidate scope: `2` sessions, `48` suppressed mic ASR segments / `250.28s`;
- capture-safe labels: `13.06s` local/mixed, `237.22s` remote-risk;
- capture-safe best composite policy: none, `0.00s` local / `0.00s` remote-risk;
- full real scope: `10` sessions, `317` suppressed mic ASR segments / `3656.50s`;
- full real labels: `409.50s` local/mixed, `3247.00s` remote-risk;
- full real best zero-risk composite: `dual_target_remote_guard_v1`, `47.70s` local /
  `0.00s` remote-risk;
- full real best <=3s-risk composite: `target_me_remote_guard_v1`, `116.10s` local /
  `2.44s` remote-risk;
- broadest composite with useful recall, `target_or_persistent_remote_guard_v1`, recovers `144.12s`
  local but leaks `11.08s` remote-risk.

Historical conclusion: combining existing online evidence improved precision, but not enough
recall. The later focused profile materialization invalidated the advisory-only order assumption;
see the current status at the top of this file.

Materialized composite shadow profile, 2026-07-09:

- `compare-live-batch.py` now writes
  `derived/live/target-me-shadow/online_suppressed_mic_dual_target_remote_guard_v1/draft.json`
  and `.md`;
- the profile uses only suppressed mic segments where session-local Target-Me and persistent
  Target-Me both pass their remote guard;
- current real corpus result: `4` added turns / `47.70s`;
- profile missing-Me: `380.17s`, down from ordinary live `395.72s`;
- profile remote leak remains `15.96s` because this rescue-only profile does not remove already
  published bad live `Me` turns;
- profile contentful role-constrained order mismatches remain `4`;
- profile non-passing gates: `42`, so promotion remains blocked.

Conclusion: the zero-risk composite is now a real materialized shadow draft, not only a lab number.
It proves the mechanism for publishing a tiny safe suppressed-mic subset, but it is far too small to
close parity by itself.

Online remote-overlap shadow filter, 2026-07-09:

- `compare-live-batch.py` now also writes
  `derived/live/target-me-shadow/online_live_me_remote_overlap_filter_v1/draft.json` and
  `derived/live/target-me-shadow/online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1/draft.json`;
- it also writes the stronger live-implementable variants
  `online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_v1` and
  `online_live_me_remote_overlap_filter_plus_target_me_timeline_safe_audio_safe_union_v1`;
- the filter removes only live `Me` turns whose text strongly overlaps contemporary live
  `Colleagues` text: at least `3s`, at least `5` mic tokens, at least `5` overlapping remote tokens,
  and either `mic_token_recall_in_overlapping_remote >= 0.70` or
  `overlapping_remote_token_recall_in_mic >= 0.75`;
- current real corpus result for `online_live_me_remote_overlap_filter_plus_dual_target_remote_guard_v1`:
  - removed live `Me`: `15.96s`;
  - remaining measured remote leak: `0.00s`;
  - restored suppressed mic `Me`: `47.70s`;
  - missing-Me: `380.17s`;
  - contentful role-constrained order mismatches: `4`;
  - non-passing gates: `41`.
- current best live-implementable profile is
  `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_live_boundary_split_retime_v1`:
  - remaining measured remote leak: `0.00s`;
  - missing-Me on real live sessions: `51.50s`;
  - contentful role-constrained order mismatches: `2`;
  - non-passing gates: `41`;
  - live-only boundary split/retime retimed `4` turns, trimmed `51.916s`, split `3` turns and
    preserved `2` local prefixes / `7.832s`.
- previous local-speaker boundary profile remains the pre-split/retime baseline:
  `86.85s` missing-Me, `0.00s` remote leak, `4` contentful order mismatches.
- best diagnostic oracle profiles are the local-island split/retime oracle family:
  - missing-Me: `85.69s`;
  - remaining measured remote leak: `0.00s`;
  - contentful role-constrained order mismatches: `4`;
  - best live-implementable to oracle gap: `1.16s`.
- remaining missing-Me decomposition for that best live-implementable profile:
  - visible in suppressed mic without Target-Me evidence: `73.08s`;
  - not visible in suppressed mic without Target-Me evidence: `13.77s`;
  - visible/not-visible with Target-Me evidence: `0.00s`.
- `live_target_me_shadow_profile_best_live_implementable_remaining_gap` exposes the same residual
  queue by bucket, policy set and session; its largest policy set is now no Target-Me policy
  (`86.85s`).
- The same block now exposes overlapping suppressed-mic evidence. Current top groups:
  `(none)` (`17.83s`) by suppressed policy set,
  `(none)` (`34.45s`) by gate reason, and
  `remote_dominant` (`32.90s`) by suppressed batch-role label. Known live ASR hallucinations are
  split out as `known_hallucination` (`12.42s`) and cannot become rescue candidates.
- Remaining-gap actionability on real live sessions after the diagnostic remote-guarded boundary
  materialization:
  - `mixed_needs_segmentation_or_speaker_evidence`: `25.00s`;
  - `remote_dominant_not_rescuable_without_new_evidence`: `12.41s`;
  - `asr_hallucination_not_rescuable`: `12.42s`;
  - the previous `0.32s` speaker-confirmation candidate is now materialized in the diagnostic
    `remote_guarded_voice_boundary` shadow profile and remains non-promotable.
- Mixed-region segmentability:
  - `local_island_split_candidate`: `10.58s`, including `5.10s` of local-looking islands;
  - `duplicate_heavy_needs_speaker_evidence`: `22.28s`;
  - `needs_speaker_evidence`: `5.36s`;
  - `remote_dominant_mixed_not_rescuable`: `3.22s`;
  - `short_low_value_tail`: `1.64s`.
- `live_next_unlock`:
  - schema: `murmurmark.live_next_unlock/v1`;
  - full-corpus diagnostics still include historical unsafe/debug blockers;
  - historical objective at this checkpoint: `fix_live_local_recall_gap`;
  - active order-risk scope: `capture_safe_candidate`;
  - first implementation action: `materialize_partial_safe_tail_shadow_and_recheck_recall`;
  - later correction: focused profile materialization found blocking boundary rows, so this
    advisory-only conclusion is no longer current;
  - boundary-order split/retime oracle:
    - profile:
      `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_batch_order_boundary_split_retime_oracle_v1`;
    - retimed turns: `2`;
    - preserved local prefixes: `1 / 6.62s`;
    - contentful order mismatches after split/retime: `2`;
    - missing-Me after split/retime: `86.85s`;
    - delta vs best live-implementable: `0.00s`;
    - conclusion: split/retime is the safer target shape; the remaining `2` order rows are advisory
      weak/short/generic matches, not boundary-retime blockers;
  - blocked buckets: `remote_dominant_without_new_evidence` (`29.68s`) and
    `known_hallucination` (`12.42s`).
- `live_speaker_boundary_evidence_lab`:
  - schema: `murmurmark.live_speaker_boundary_evidence_lab/v1`;
  - remaining rows: `21 / 86.85s`;
  - future shadow-probe candidates: `5 / 17.90s`;
  - publication-ready seconds: `0.00s`;
  - blocked rows: `16 / 68.95s`;
- `live_soft_local_speaker_boundary_shadow_lab`:
  - schema: `murmurmark.live_soft_local_speaker_boundary_shadow_lab/v1`;
  - status: `no_incremental_gain`;
  - tested profile:
    `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_soft_local_speaker_boundary_shadow_live_boundary_split_retime_v1`;
  - missing-Me delta vs best live-implementable: `0.00s`;
  - remote-leak delta vs best live-implementable: `0.00s`;
  - conclusion: a softer loudness boundary does not unlock the remaining mixed rows.
  - largest candidate class: `local_island_boundary_probe_candidate` (`10.58s`);
  - largest blocked class: `blocked_remote_dominant` (`29.68s`).
- Local-island split lab:
  - candidate batch rows: `1 / 10.58s`;
  - candidate local-island audio/text: `5.10s`;
  - accepted by token recall >= `0.35`: `0 / 0.00s` batch rows;
  - accepted local-island evidence: `0.00s`;
  - promotion allowed: `false`.
- Profile-level local-island oracle:
  - policy:
    `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_batch_remote_forbidden_local_island_split_oracle_v1`;
  - missing-Me: `85.69s`;
  - delta vs best live-implementable: `1.16s`;
  - remote leak: `0.00s`;
  - contentful order mismatches: `4`;
  - added suppressed-mic turn seconds: `62.30s`;
  - rejected supplemental turns: `2`.
- Profile-level local-island retime oracle:
  - policy:
    `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_batch_remote_forbidden_local_island_retime_oracle_v1`;
  - missing-Me: `85.69s`;
  - delta vs best live-implementable: `1.16s`;
  - delta vs split oracle: `0.00s`;
  - remote leak: `0.00s`;
  - contentful order mismatches: `4`;
  - added suppressed-mic turn seconds: `71.26s`;
  - rejected supplemental turns: `2`.
- `target_me_possible_timeline_safe_v1` recovers `251.37s`, rejects `47.38s` of candidates
  (`31.08s` contentful order risk, `16.30s` suspected remote leak), and keeps measured remote leak at
  `0.00s`.
- less strict remote-guard variants prove the recall/order trade-off:
  `online_live_me_remote_overlap_filter_plus_target_me_remote_guard_audio_safe_union_v1` reduces
  missing-Me to `276.93s` and keeps measured remote leak at `0.00s`, but raises contentful
  role-constrained order mismatches to `7`.
- timeline-safe Target-Me rejection audit:
  - rejected candidate seconds: `18.30s`;
  - rejected because of contentful order mismatch: `18.30s`;
  - rejected because of suspected remote leak: `0.00s`.

Conclusion: the online filter closes the current measured remote-leak symptom without batch truth,
and the local-speaker boundary / split-retime family lowers the best live-implementable missing-Me
to `51.50s` on real live sessions.
The goal remains blocked by local recall, order risk, review burden and draft readiness, not by lack
of another raw recording. The best local-island oracle no longer proves an extra real-live
missing-Me gain, so the next useful work is not another broad threshold: it is targeted
voice/remote-guard evidence for mixed rows without using batch labels. The
boundary-order retime oracle confirmed the direction but also showed the trap: it fixed two timing
rows while increasing missing-Me by `6.08s`. The live-only split/retime shadow now preserves that
local speech and lowers contentful order mismatches to `2`, so boundary-retime is no longer the
first unlock. A softer local-speaker boundary threshold was also tested and produced
`no_incremental_gain`; the next non-oracle version needs new speaker/boundary evidence, not weaker
loudness thresholds.
Duplicate-heavy or remote-dominant mixed rows should remain blocked until stronger speaker evidence
exists.
The corpus report records this as `live_local_island_timing_gap/v1`, including the current
`1.16s` oracle gap and the online evidence still missing before any live publication.
It also records `live_local_island_audio_anchor_lab/v1`, which currently has
`no_accepted_local_island_rows`; the next blocker is online candidate selection and speaker/boundary
evidence, not raw audio availability alone.
The new `live_online_speaker_boundary_evidence_design_lab/v1` narrows that blocker further:
`40.18s` of mixed/speaker rows are actionable, but only `14.132s` are plausible publish candidates
after new evidence, and `0.0s` are publish-ready now. Its top implementation unit is
`boundary_island_micro_asr` (`10.58s` row scope, `5.10s` potential publishable local island). This is
now the smallest useful next target: decode and align local-island spans, keep duplicate-heavy rows
blocked unless voice evidence proves unique local speech, and preserve `remote_leakage == 0`.
The first diagnostic micro-ASR lab for this target is now implemented as
`scripts/report-live-boundary-island-micro-asr-lab.py`. It writes
`sessions/_reports/live-pipeline/live_boundary_island_micro_asr_lab.json`,
`live_boundary_island_micro_asr_lab_attempts.jsonl` and a Markdown report. Current evidence:
`1` local island / `5.10s`, `12` successful attempts, `1` live alignment candidate. The best live
chunk attempt improves batch-token recall from `0.154` to `0.385` while keeping remote similarity
at `0.236`; the best batch-reference attempt reaches `0.462`. The lab is diagnostic only:
`publication_ready_seconds = 0.0`, `promotion_allowed = false`, and batch remains authoritative.
The accepted live attempt is now materialized into a separate lab-shadow profile:
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_live_boundary_micro_asr_lab_shadow_v1`.
Corpus result: `1` micro-ASR turn / `5.10s` added, missing-Me falls from `86.85s` to `76.27s`,
measured remote leak stays `0.00s`, contentful order mismatches stay `2`, and the profile remains
`lab_shadow`, not live-implementable.
The same script now has a live-only candidate mode:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source live-only \
  --max-candidates 10 \
  --source-scope live
```

It writes `live_boundary_micro_asr_live_candidates_lab.*` and feeds the live-implementable shadow
profile
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_live_boundary_micro_asr_live_only_shadow_v1`.
Current corpus result: the live-only lab finds `3` alignment candidates / `13.76s`, but the
materialized profile adds `0.00s` after deduplication and timeline safety; missing-Me remains
`86.85s`, measured remote leak remains `0.00s`, and contentful order mismatches remain `2`.
This proves that the current blocker is not lack of recordings or lack of a micro-ASR hook. The
blocker is still live-only speaker/boundary evidence precise enough to publish short `Me` islands
without opening a remote leak path.

The same lab now has a `blocker-analysis` candidate source for the capture-safe local-recall blocker:

```bash
scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source live-duplicate-heavy \
  --source-scope live

scripts/report-live-boundary-island-micro-asr-lab.py \
  --candidate-source blocker-analysis \
  --source-scope live
```

`live-duplicate-heavy` is the first live-only selector for this shape. It reads suppressed live mic
segments with `segment_duplicates_overlapping_remote` plus low-correlation audio evidence, without
batch labels. Current result: `4` selected rows, `3` micro-ASR split candidates / `12.00s`,
`promotion_allowed = false`. The selected rows include the key `2026-07-08_16-22-42` duplicate-heavy
case where micro-ASR recovers "Я бы, наверное, Алексею хотел тоже добавить..." from live chunk mic
audio.

`blocker-analysis` reads `capture_safe_candidate_local_recall_blocker_analysis`, selects only
`duplicate_heavy_mixed_needs_token_split` rows, and probes the overlapping suppressed-mic local
islands from live chunk audio. Current result: `2` duplicate-heavy rows yield `4` probes; `3`
probes / `9.16s` are micro-ASR split candidates. One example recovers the local phrase beginning
with "Я бы, наверное, Алексею хотел тоже добавить..." from inside a duplicate-heavy batch `Me`
block. This is still batch-informed and diagnostic-only: `promotion_allowed = false`, no live draft
is modified, and the next implementation task is to turn the live-only selector into a materialized
shadow split profile that still passes remote-forbidden parity gates.

The corpus report now adds `live_mixed_speaker_boundary_voice_coverage_lab/v1` to check whether
the current mixed/speaker blocker is already covered by Target-Me voice evidence. The Target-Me
audit now has `--include-remaining-gap`, so it can feed the current best-live-implementable
remaining mixed intervals into the same voice backend. It also has
`--fallback-persistent-profile`, which uses historical persistent Target-Me rows when same-session
enrollment is not ready. This fallback is diagnostic-only: it never creates rescue policy
candidates. Current result after both extensions:

- rows in scope: `5` / `25.32s`;
- Target-Me voice evidence exists for the whole set;
- remote-guard boundary materialized in diagnostic shadow: `0.32s`;
- weak or ambiguous voice evidence: `25.00s`;
- Target-Me enrollment-not-ready: `0.00s`;
- no-overlap Target-Me coverage: `0.00s`;
- historical objective at this checkpoint:
  `fix_live_local_recall_gap`.

Later focused materialization retained the conclusion that new recordings are unnecessary, but
changed the first blocker to order risk. The local-recall evidence remains important and follows
after blocking boundary rows are closed without weakening remote-forbidden gates.

The follow-up `live_same_session_voice_disambiguation_lab/v1` now makes the next blocker explicit:
all `25.00s` are `needs_same_session_local_only_enrollment_probe`. The affected sessions have `0`
positive same-session live `Me` enrollment examples, so the next useful step is to build a causal
local-only Target-Me enrollment probe from high-confidence mic evidence. Mixed rows must remain
review-only until that probe proves safe under the same remote-leak and order gates.

`report-live-local-only-enrollment-probe.py` now performs that first probe. On the current affected
sessions it finds `local_only_enrollment_probe_ready` in all `3` sessions, with `144.00s` of
accepted positive local-only seed audio and remote-negative separation. It supports `24.52s` of the
`25.00s` blocked mixed rows; only the `0.48s` low-value tail remains unsupported. This does not
unlock promotion yet; it changes the next work to “materialize a diagnostic local-only-seed
mixed-row shadow and run parity gates”.
The paired `live_local_island_retime_anchor_lab/v1` makes that blocker concrete:

- accepted rows: `0`;
- batch Me interval: `0.00s`;
- live-available local-island audio: `0.00s`;
- anchor span: `0.00s`;
- context expansion needed to match batch timing: `0.00s`;
- max leading gap before the first anchor: `0.00s`;
- max trailing gap after the last anchor: `0.00s`;
- max inter-island gap: `0.00s`.

This means additional recordings are not the current unlock. The corpus already proves the shape of
the missing work: replace lab/batch-selected micro-ASR candidates with live-only candidate selection
and strict remote-forbidden gates, then run the normal parity gates before any publication path is
considered. Until that exists, live promotion stays blocked even if more similar sessions are added.

The corpus report now also records `live_only_local_island_candidate_lab/v1`. It selects suppressed
mic segments using only live-available text/audio gates and uses batch labels only to estimate
precision. Current result:

- selected candidates: `15` segments / `99.40s`;
- local or mixed: `83.04s`;
- remote-risk: `16.36s`;
- precision proxy: `0.835412`;
- excluded because audio metrics were missing: `6` segments;
- largest source session: `2026-07-03_10-15-18` with `92.52s` selected;
- observed failure mode: live-only gates still select `remote_dominant` segments when text looks
  locally novel but audio is weakly correlated with remote.

The same report now evaluates a stricter live-only profile:
`strict_zero_remote_risk_text_audio_v1`.

- selected candidates: `3` segments / `36.12s`;
- local or mixed: `36.12s`;
- remote-risk: `0.00s`;
- precision proxy: `1.0`;
- current source session: `2026-07-03_10-15-18`;
- status: diagnostic only, not promoted.

The strict profile is now also materialized into ordinary live-shadow drafts:

- strict policy:
  `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_strict_live_only_local_island_v1`;
- strict profile result: `0.00s` added after deduplication against existing live/Target-Me turns,
  `117.57s` missing-Me, `0.00s` remote leak, `4` contentful order mismatches, `41` non-passing
  gates;
- combined policy:
  `online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_strict_live_only_local_island_v1`;
- combined profile result: `52.76s` added, `104.19s` missing-Me, `0.00s` remote leak, `4`
  contentful order mismatches, `41` non-passing gates. This is now a baseline, not the best
  live-implementable profile;
- strict shadow delta lab: `0.00s` incremental strict turns and `0.00s` closed missing-Me.

Conclusion: no additional recordings are required to unblock the current design question. The
corpus already proves both sides of the trade-off: live-only gates can recover meaningful local
speech, but the broader gates are not precise enough for publication. The stricter profile shows a
small zero-risk candidate set, but the materialized strict turns are already covered by existing
Target-Me/audio-safe live-shadow materialization and do not close additional missing-Me. The next
implementation should add an online timing anchor / remote-forbidden guard for still-uncovered local
islands, not another broad threshold and not more recordings. Live promotion remains blocked until
ordinary parity gates pass.

The report now adds `live_only_retime_boundary_candidate_lab/v1`, which evaluates whether
live-visible anchors can explain the current best-live-implementable remaining gap without using
batch timing for publication. It uses batch labels only for evaluation and keeps
`promotion_allowed: false`.

Current result:

- strict zero-remote anchors: `36.12s` anchor evidence, but `0.00s` overlap with the current
  remaining gap;
- recommended live-only boundary classifier:
  `relaxed_audio_text_anchor_remote_forbidden_boundary_classifier_v1`;
- classifier result: `13.44s` missing-Me overlap, `0.00s` remote-risk, `23.50s` candidate span;
- best recall probe: `relaxed_audio_text_anchor_oracle_gap_probe_v1`;
- best recall probe overlaps `18.69s` of missing `Me`, but also `27.20s` remote-risk;
- evaluation-only zero-remote ceiling:
  `relaxed_audio_text_anchor_remote_forbidden_trimmed_zero_remote_evaluated_gate_v1`;
- that ceiling keeps `2` groups / `31.28s` candidate span, overlaps `14.79s` missing `Me` and has
  `0.00s` evaluated remote-risk;
- candidate pool combines top-level suppressed-mic segments with remaining-gap evidence because the
  top-level risk examples are intentionally truncated.

Conclusion: the oracle-sized recovery is reachable only through relaxed anchors plus a stronger
remote-forbidden boundary/context gate. The first conservative live-only classifier recovers most of
the evaluation-only ceiling (`13.44s` of `14.79s`) without evaluated remote-risk. Relaxed anchors must
not be promoted as-is.

That materialization is now present as
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_remote_forbidden_boundary_classifier_v1`.
The ordinary parity gates show that it is not yet a promotion candidate:

- guarded boundary turns added: `1.48s`;
- missing-Me remains worse than the previous best live-implementable `110.13s`;
- measured remote leak stays `0.00s`;
- contentful order mismatches stay at `4`;
- non-passing gates remain `41`;
- rejected boundary turns: `12`.

The next materialized variant,
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_remote_forbidden_relaxed_boundary_classifier_v1`,
keeps the remote-forbidden multi-cut classifier but accepts anchor pieces down to `-6dB`
mic-minus-remote when the surrounding remote-forbidden cuts pass:

- boundary turns added: `4.10s`;
- missing-Me: `100.23s`;
- measured remote leak: `0.00s`;
- contentful order mismatches: `4`;
- non-passing gates remain `41`.

The current best live-implementable shadow is now
`online_live_me_remote_overlap_filter_plus_target_me_possible_timeline_safe_audio_safe_union_local_speaker_boundary_shadow_v1`.
It combines `audio_safe_union_v1`, relaxed remote-forbidden boundary evidence and a stricter
local-speaker boundary candidate that requires live speaker evidence plus zero token overlap with
overlapping remote text:

- missing-Me: `86.85s`;
- measured remote leak: `0.00s`;
- contentful order mismatches: `4`;
- non-passing gates remain `41`;
- live-implementable-to-oracle gap: `1.16s`.

This closes most of the previous `100.23s -> 85.69s` oracle gap without weakening promotion policy.
It does not finish the goal: live remains blocked by order/readiness and other parity gates.

The first unguarded materialization is still useful as a negative test: it added `12.68s` and lowered
missing-Me to `127.01s`, but it increased contentful order mismatches from `4` to `5`. The current
guarded version avoids that order regression by publishing only anchor-bounded pieces whose anchors
are not remote-dominant, but this also removes the useful recovery. Conclusion: the next
implementation work is stronger online timing/local-speaker evidence for boundary turns. More
recordings still are not the current unlock.

The report now keeps concrete missing-Me rows under
`capture_safe_evaluable_local_recall_gap_examples`. This includes capture-safe runs that are not
`meaningful_live_comparison` because the live draft lost all `Me` turns; those sessions must remain
visible for the local-recall fix.

The comparison now evaluates normal live turns and rescue-shadow candidates at ASR-segment
granularity when the source ASR JSON is present, with chunk-level fallback for older artifacts. This
exposes order and remote-leakage risks that the earlier chunk-level comparison could hide.
Order risk remains visible around live chunk boundaries and mic/remote reconciliation, but the
current token-density profile classifies the active capture-safe residual as `2` advisory
timing/match rows and `0` blocking rows. Historical full-corpus triage retains one blocking row
outside that active slice. Raw capture and sidecar materialization are not the next bottleneck. The current
objective is `fix_live_local_recall_gap`; remote leakage remains a parallel hard gate.
Most missing `Me` seconds are still visible in `raw_text_before_role_gate` / suppressed mic chunks,
but the live branch also has segment-level ordering drift and `15.96s` suspected remote leakage in
published live `Me`. The current live blockers are therefore: live timeline ordering, remote leakage
and the coarse `live_role_gate`, which suppresses an entire mic chunk when the chunk looks like
remote duplicate even if it also contains real local speech. Segment-level batch comparison now
shows `47` Me-dominant suppressed mic ASR segments
(`149.62s`) and `44` mixed suppressed mic ASR segments (`199.92s`) in real live runs. A first
text-only segment rescue found `9` real live candidate chunks / `54` kept candidate segments, but it
stays diagnostic-only because policy-lab metrics show it would recover `152.60s` local speech while
risking `73.62s` remote leakage. A stricter unique-token text rule is not better enough:
`143.52s` local / `118.74s` remote-risk. `remote_silent_text_v1` is much safer
(`34.16s` local / `2.58s` remote-risk), but covers only a small slice of the `349.54s` batch-oracle
local ceiling. The next implementation step should therefore add audio/evidence gates, not only text
thresholds, to split or rescue local evidence inside suppressed mic chunks without publishing remote
leak. The first audio policy lab narrows that path: `audio_mic_dominant_v1` is clean but small
(`24.00s` local / `0.00s` remote-risk), `audio_low_coherence_v1` is unsafe
(`176.98s` local / `193.18s` remote-risk), and `audio_safe_union_v1` is the best current shadow
candidate (`50.18s` local / `2.58s` remote-risk, `68.42s` missing-Me recovered). Fresh live chunks
now expose that candidate separately as `live_rescue_shadow`; in the current corpus this appears in
`2` real-live chunks / `9` segments and recovers `45.36s` missing-Me in actual shadow artifacts.
The shadow itself does not add measured remote-risk (`0.00s`), but it still leaves `350.36s`
missing-Me and does not reduce the `34` segment-level order mismatches when evaluated as a combined
draft. It remains
shadow-only evidence, not a promotable live `Me` path. The scoped candidate diagnostic is stricter:
existing live-implementable policies do not recover a material amount of local speech in
promotion-candidate sessions, even though the batch oracle shows recoverable `Me` content in the
same suppressed regions.

New Target-Me diagnostic, 2026-07-08:

- `scripts/audit-live-local-recall-target-me.py` audits suppressed live mic ASR segments with the
  local Target-Me speaker evidence backend;
- current corpus run: `6` sessions, `77` audited items / `334.12s`;
- `4` sessions had enough Target-Me enrollment, `2` reported `insufficient_enrollment`;
- audited local/mixed seconds: `295.34s`;
- Target-Me confirmed local seconds: `142.34s`;
- Target-Me possible or confirmed local seconds: `287.98s`;
- remote-risk false-positive seconds in the audited set: `38.78s`;
- Target-Me rejected remote-risk seconds: `0.00s`.
- recommended shadow policy: `target_me_confirmed_remote_guard_v1`;
- `target_me_confirmed_remote_guard_v1` selected `118.54s`, covered `116.10s` local speech,
  introduced `2.44s` remote-risk, and would recover `94.68s` current missing-Me by interval overlap;
- broader `target_me_possible_v1` would recover `247.24s`, but with `37.82s` remote-risk, so it is
  too unsafe for promotion.
- `compare-live-batch.py` now evaluates these Target-Me rows as actual counterfactual live-shadow
  turns. In the real live corpus, `target_me_confirmed_remote_guard_v1` selects `118.54s`, recovers
  `128.85s` missing-Me and adds `0.00s` measured remote leak, but also adds `3` contentful
  role-constrained order mismatches. `target_me_confirmed_v1` recovers `169.24s` but adds `12.40s`
  remote leak; `target_me_possible_v1` recovers `278.15s` but adds `16.30s` remote leak and `5`
  contentful order mismatches.
- `target_me_confirmed_remote_guard_timeline_safe_v1` is the first conservative Target-Me shadow
  subset: it accepts only candidates that do not increase contentful role-constrained order
  mismatches and do not add measured remote leak. Current real corpus result: `103.82s` missing-Me
  recovered, `0.00s` measured remote leak, `0` contentful order-mismatch delta.
- `target_me_possible_timeline_safe_v1` applies the same conservative checks to the broader possible
  Target-Me set. Current real corpus result: `251.37s` missing-Me recovered, `0.00s` measured remote
  leak, `0` contentful order-mismatch delta, with `47.38s` rejected before publication.
- `compare-live-batch.py` now materializes that subset as diagnostic-only
  `derived/live/target-me-shadow/target_me_confirmed_remote_guard_timeline_safe_v1/draft.json` and
  `draft.md`. Inserted turns are marked `shadow_added: true`; promotion remains false and batch
  remains authoritative.
- The same `parity_gates` now run against the materialized Target-Me shadow profile. Current real
  corpus result: `1` session passes all gates under that profile, total profile missing-Me is
  `315.34s`, and existing live remote leak remains `15.96s`. This proves that Target-Me rescue
  helps local recall, but it does not solve the live role/remote-leak problem by itself.
- `target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_oracle_v1` is now available
  as a diagnostic-only profile. It starts from the timeline-safe Target-Me shadow and removes live
  `Me` turns that the authoritative batch transcript classifies as remote-like. Current real corpus
  result: `15.96s` of remote leak are removed (`0.00s` remaining), `315.34s` missing-Me remain, and
  non-passing profile gates only drop from `42` to `41`. This profile is not promotable because it
  uses batch truth, but it proves that remote leak is bounded and that the next dominant blocker is
  live local recall / draft readiness.
- Profile diagnostics now decompose the remaining local-recall gap. In the same real corpus,
  `278.13s` of the `315.34s` missing-Me are already visible in suppressed mic ASR, `37.21s` are not
  visible there, `174.33s` have a broader Target-Me candidate, and `141.01s` do not. That points to a
  stricter live role gate / Target-Me fallback problem rather than missing raw audio.
- `target_me_confirmed_remote_guard_timeline_safe_batch_remote_forbidden_visible_suppressed_mic_oracle_v1`
  is now the stronger diagnostic ceiling. It starts from the remote-forbidden oracle and greedily adds
  only suppressed mic ASR segments that batch labels as `Me`/`mixed`, reduce missing-Me, and do not add
  measured remote leak or contentful order mismatch. Current real corpus result: `145.54s` added,
  missing-Me reduced from `315.34s` to `140.41s`, remote leak stays `0.00s`, and contentful order
  mismatches stay `4`. It is still not promotable because it uses batch labels, but it proves the next
  online work should target safe suppressed-mic segment publishing.
- Two live-accessible suppressed-mic profiles were evaluated against the same gates. `audio_safe_union_v1`
  is safe but too weak (`278.52s` profile missing-Me remains). `audio_low_corr_text_guard_v1` recovers
  much more (`117.52s` missing-Me remains), but leaks `210.10s` of remote-like text, so it is not a
  viable promotion path. Combining `target_me_possible_timeline_safe_v1`, online remote-overlap
  cleanup and `audio_safe_union_v1` gave an earlier live-implementable shadow at `110.13s`
  missing-Me. The relaxed remote-forbidden boundary classifier improved that to `100.23s`, and the
  local-speaker boundary profile improved that to `86.85s` while keeping measured remote leak at
  `0.00s`. The current live-only split/retime variant keeps those numbers and reduces contentful
  order mismatches from `4` to `2`. The next online design therefore needs to reduce the remaining
  local-recall/readiness gap, especially mixed regions that still need speaker/boundary evidence,
  while keeping order risk and remote leak from regressing.

Interpretation: more new recordings are not needed to unblock the next implementation step. The
existing corpus already contains enough suppressed live mic material and enough Target-Me evidence.
The next work is to fix the remaining gates exposed by this materialized shadow draft: especially
remaining missing-Me, order risk, batch readiness/review gates and capture-safe live evidence.
Sessions with
`insufficient_enrollment` point to a later calibration problem, not to a need for more ad-hoc live
meetings now.

## Latest Completed Goal: Current Pipeline Stabilization v1

Status, 2026-07-06: complete.

Current baseline:

- [2026-07-06 Current Pipeline Stabilization Baseline](../testing/2026-07-06-current-pipeline-stabilization-baseline.md)

Goal:

```text
Полная стабилизация текущего MurmurMark pipeline без новых продуктовых доработок. Текущая версия
должна надёжно выполнять основной пользовательский сценарий: записал созвон -> получил понятный
итог, то есть транскрибацию/заметки или явный отказ с причиной. На время этой цели не добавлять
новые модели, repair/synthesis/audit-слои, live promotion, Echo Guard experiments или UI.
```

Plainly: MurmurMark must become boring before it becomes smarter. The supported path is:

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
murmurmark record --out "$SESSION" --target-bundle system
murmurmark process "$SESSION"
murmurmark next "$SESSION"
murmurmark status "$SESSION"
murmurmark finish "$SESSION"
```

Current scope:

- keep `--live-pipeline` disabled by default and out of the normal meeting command until its segment
  writer no longer interferes with raw ScreenCaptureKit capture;
- prove normal capture without live writes usable mic/remote evidence or blocks early;
- block silent, partial and interrupted captures before ASR unless `--allow-partial` is explicit;
- make repeated `process` runs resume or explain the next command without stale readiness/outcome;
- make `status` and `next` agree for successful, review-first, blocked and failed-capture sessions;
- keep existing quality/review/export layers stable, but do not improve them during this goal;
- classify the current real-session corpus as usable, review_first, blocked or broken_capture;
- update README, runbooks, roadmap and opskarta around this supported route.

Definition of done:

- working tree is clean, committed and pushed;
- `caffeinate -dimsu scripts/check.sh` passes from an awake desktop session;
- live/capture regression smoke passes, but live remains diagnostic;
- at least one fresh short non-live recording with audible content produces a non-empty transcript;
- at least one fresh failed/silent capture fixture blocks before ASR;
- `murmurmark status "$SESSION"` and `murmurmark next "$SESSION"` give one non-conflicting next step;
- README and runbooks describe the supported production path without stale live-first guidance.
- `doctor --strict` blocks recording when ScreenCaptureKit sees no shareable display, including the
  case where macOS permissions exist but the display/session is asleep.

Explicit non-goals:

- full echo removal, new ASR models, diarization, new synthesis, new review profiles, Core Audio
  Process Tap migration, UI App or any quality tuning beyond stabilizing the existing pipeline.

## Latest Completed Goal: Reliable Processing UX v1

Status, 2026-07-02: complete.

Goal:

```text
Reliable Processing UX v1: сделать post-recording путь MurmurMark надёжным и понятным для
ежедневного использования. После записи пользователь запускает `murmurmark process "$SESSION"` и
получает не поток догадок, а ясный результат: `ready_for_notes`, `review_first` или `blocked`,
путь к transcript/notes, точный review burden, причину блокировки экспорта и одну следующую
команду. Пайплайн должен нормально возобновляться после прерывания, показывать понятный прогресс
долгого ASR, не пересчитывать лишнее без `--force` и не оставлять пользователя гадать, завис процесс
или работает.
```

Plainly: MurmurMark should feel less like a lab and more like a tool. The main user question after
recording is simple: "Can I use this, where is the transcript, and what is the one next safe action?"

Why this is the right next goal:

- the core transcription/review pipeline is pilot-ready, but the terminal handoff is still too noisy;
- long ASR and judge stages can look like a hang unless progress explains what is happening;
- after interruption, the user needs a resume path, not a pile of derived files;
- `outcome.json` already exists, so the shortest path is to make it the stable UX contract.

Definition of done:

- `murmurmark outcome SESSION` prints a compact human summary: can read notes, can export, review
  burden, first review lane, next command and key file paths;
- `derived/outcome/outcome.json` contains the same compact `summary` for tools and agents;
- long-running `murmurmark process` steps print useful heartbeats with step reason, checkpoints and
  resume command;
- rerunning `murmurmark process SESSION` remains the normal recovery path after interruption;
- docs describe the simple post-recording route and where deeper diagnostics live;
- no change to capture, default `local_fir` or main `whisper.cpp` ASR.

Follow-up hardening:

- add ASR window/chunk-level resume instead of whole-stage rerun;
- add duration estimates from historical corpus timings;
- make `status` default to the compact outcome view, with diagnostics behind an explicit flag;
- keep reducing mandatory review burden at the audio source.

Completion evidence, 2026-07-02:

- `murmurmark outcome SESSION` prints `can_read_notes`, `can_export`, `export_blockers`, review
  burden, first review lane, key file paths and the next command;
- `outcome.json` carries the same compact `summary`;
- long `murmurmark process` stages print heartbeat lines with step reason, checkpoint count and
  resume command;
- `process --plan-only` writes `pipeline_plan_report.json` and does not overwrite the last real
  `pipeline_run_report.json` or refresh `outcome`;
- `Ctrl-C` during `process` terminates the current child step, writes `status: interrupted`,
  refreshes `outcome` and points back to `murmurmark process SESSION`;
- checks passed: `py_compile`, `git diff --check`, `swift build`, `scripts/smoke-cli-handoff.sh`,
  `scripts/smoke-fixture.sh`, `scripts/check-corpus-gates.py --no-fail`.

## Latest Completed Goal: Chunked/Resumable Processing v1

Status, 2026-07-03: complete.

Goal:

```text
Chunked/Resumable Processing v1: превратить тяжёлую post-recording обработку MurmurMark из
монолитных стадий в кусочный, кэшируемый и безопасно возобновляемый pipeline. После прерывания или
сбоя `murmurmark process SESSION` должен продолжать с последнего проверенного chunk/checkpoint, не
пересчитывать весь ASR без необходимости, показывать реальный прогресс по секундам/окнам и
сохранять batch-quality результат. Live/near-realtime chunks могут использоваться как кэш только
через строгие parity gates; batch transcript остаётся authoritative.
```

Why this was the right significant goal:

- Reliable Processing UX now explains what happens, but the expensive ASR stage can still rerun too
  much work;
- near-realtime already exists as a shadow branch, but its chunks are not yet trusted as batch-grade
  cache;
- true resumability reduces waiting time, makes interruptions cheap and gives a concrete path from
  batch-first to near-realtime without weakening transcript quality;
- this goal does not require changing capture, default Echo Guard or main `whisper.cpp` ASR.

Definition of done:

- ASR/cache artifacts are indexed by stable chunk identity: source audio, model, language, prompt,
  audio prep, window boundaries and tool version;
- `murmurmark process SESSION` can skip already valid ASR chunks and resume after interruption;
- run reports show chunk totals, completed seconds, missing chunks, reused chunks and estimated
  remaining heavy work;
- live/near-realtime ASR cache is reused only when strict metadata compatibility and parity checks
  pass; otherwise the batch path recomputes safely;
- corpus gates compare chunked/resumed output with current batch baseline for order risk, local
  recall, remote leakage, review burden and selected notes readiness;
- docs describe when to use `--force-asr`, when cache is reused, and how to inspect a stuck chunk.

Completion evidence:

- default `windowed` whisper.cpp ASR writes per-window chunk cache metadata and
  `raw/chunks/<track>/chunk_cache_report.json`;
- if the combined raw ASR JSON is missing but compatible chunk JSON remains, the bridge rebuilds the
  combined raw JSON from chunks instead of rerunning all windows;
- chunk identity includes source audio fingerprint, model, language, prompt, audio prep, window
  boundaries and tool version;
- `murmurmark process` heartbeat reads chunk cache reports and can show completed/missing/reused/
  transcribed ASR windows, completed/remaining audio seconds and a current-run ETA when enough
  progress exists;
- `murmurmark process` runs `check-asr-chunk-cache.py --require-chunks` after `transcribe_current`;
  `raw/chunk_rebuild_check.json` is a hard gate proving current raw ASR JSON can be rebuilt from
  cached chunks;
- `report-asr-chunk-cache-corpus.py` aggregates chunk rebuild checks across sessions, and
  `check-corpus-gates.py` now treats failed ASR chunk-cache corpus reports as hard failures;
- `materialize-live-asr-cache.py` now writes materialized `raw/chunks/<track>/` reports from eligible
  live ASR chunks, so live-cache reuse is also covered by the same rebuild gate;
- verified on a temporary 4.5s two-track session: after deleting only top-level `mic.json` and
  `remote.json`, the second run reused `6/6` chunks, rebuilt the combined raw JSON, and the rebuild
  verifier passed.
- verified on a synthetic live-cache session: live chunks were materialized into raw chunk reports,
  and `check-asr-chunk-cache.py --require-chunks` rebuilt `mic` and `remote` with `2/2` chunks.
- verified on fourteen real sessions, including a real interrupted 29-minute `murmurmark process`
  run: `report-asr-chunk-cache-corpus.py --refresh` reports `passed: 14`, `failed: 0`,
  `coverage_ratio: 0.28`, `chunks_completed: 146/146`;
- `check-corpus-gates.py --no-fail` now reports `asr_chunk_cache_status: passed` with
  `asr_chunk_cache_passed_sessions: 14` and `asr_chunk_cache_coverage_ratio: 0.28`;
- `check-corpus-gates.py --no-fail` still reports live-cache parity state, with promotion blocked.
  Diagnostic live recordings showed that inline segment writing can starve raw ScreenCaptureKit
  audio delivery. The async bounded segment queue now has full fail-open proof, but the target
  coverage gate remains red until enough controlled Live Evidence live-vs-batch runs pass;
- ASR chunk-cache corpus report now separates the next coverage work: `22` sessions have old raw
  ASR without chunk reports, and `14` sessions have no raw ASR;
- legacy top-level raw ASR caches without `raw/chunks/<track>/chunk_cache_report.json` no longer
  satisfy `windowed` cache reuse by themselves; `murmurmark process` now rebuilds per-window chunks
  first, then requires `check-asr-chunk-cache.py --require-chunks` to pass;
- `pipeline_run_report.json` now carries `progress.asr_chunks.remaining_sec`,
  `progress.asr_chunks.completed_ratio` and `progress.asr_remaining_estimate`;
- `scripts/smoke-cli-handoff.sh` now includes a controlled fake-whisper failure/resume case: the
  first run fails after one cached mic chunk, the second run reuses that chunk, transcribes the
  remaining windows and passes `check-asr-chunk-cache.py --require-chunks`.
- `scripts/smoke-process-chunk-resume.sh` now proves the same recovery through the actual
  `run-session-pipeline.py` process path: first run fails in `transcribe_current`, rerun resumes,
  reuses a cached mic chunk, passes `check_asr_chunk_cache` and writes zero remaining ASR chunks.
- the same smoke now proves controlled `Ctrl-C` behavior in the actual process path: the interrupted
  run writes `pipeline_run_report.json` with `status: interrupted`, leaves one verified mic chunk in
  cache, and the next run reuses it instead of starting ASR from zero.
- the same smoke also covers legacy raw-cache rollout: a pre-chunk `raw/mic.json`/`raw/remote.json`
  pair without chunk reports is rebuilt into per-window chunks instead of being accepted as complete.
- verified with the user-facing CLI on `sessions/2026-07-02_13-33-49`: the first real interrupted
  long run wrote `pipeline_run_report.json` with `status: interrupted`, `transcribe_current`, ASR
  progress `10/30` mic chunks and a `murmurmark process SESSION` resume command; the next run reused
  `14` cached mic chunks, finished `60/60` total chunks and passed `check_asr_chunk_cache`.

Follow-up hardening:

- broader corpus no-regression comparison for chunked rebuild vs existing batch baseline;
- corpus parity coverage for live-cache reuse is paused. Do not collect more real
  `record --live-pipeline` sessions until the live segment writer is redesigned and no longer runs
  risky work on the ScreenCaptureKit callback path.

Primary design document:

- [Reliable Transcription Route](reliable-transcription-route.md)

Consultation prompt:

- [Reliable Transcription Route / Consultation Prompt](reliable-transcription-route.md#consultation-prompt)

## Completed Base Goal: Reliable Transcription Route v1

Status, 2026-07-02: completed as v1. Outcome Contract v1, Gate Evaluator, Review Plan,
Next Command, session-level Run Manifest and outcome-aware guarded export are implemented.
Review Burden Reduction v1 is also complete: the operational corpus now has a short explicit manual
tail and no pending safe suggestions.

Completion evidence:

- every processed session writes `derived/outcome/outcome.json`, `outcome.md`,
  `review_plan.json` and `next_command.txt`;
- `murmurmark status`, `next`, `outcome`, `finish`, `report session` and `report corpus --refresh`
  expose or refresh the same outcome next action;
- interrupted processing can be resumed at session-step/output-checkpoint level;
- corpus reports separate `ready_for_notes`, `review_first`, `blocked`, partial and diagnostic
  sessions without hidden exceptions;
- exported bundles are blocked unless the chosen outcome allows export;
- `local_fir` remains the default Echo Guard path.

## Latest Completed Follow-up: Review Burden Reduction v1

Status, 2026-07-02: complete.

Goal:

```text
Review Burden Reduction v1: используя уже существующие outcome/review_plan/audio-review/
stronger-judge/Target-Me evidence, уменьшить обязательную review-очередь на реальных сессиях без
смены capture, default local_fir и основного whisper.cpp ASR. Сделать review lanes измеримыми,
безопасно автозакрывать только доказанные строки, калибровать thresholds на корпусе и добиться,
чтобы больше сессий переходили из review_first в ready_for_notes либо имели короткий, объяснимый
manual tail.
```

Completion evidence:

- operational corpus: `24` working sessions;
- readiness split: `15/24 ready_for_notes`, `9/24 review_first`,
  `0/24 do_not_use_without_manual_review`;
- mandatory review queue: `9` actions / `12` rows / `25.31s`;
- low-materiality rows excluded from mandatory review: `28` rows / `70.95s`;
- corpus gates: `passed_with_warnings`, failed gates `0`;
- calibrated hard limits: `15` review actions / `25` queue rows;
- irreducible review gate: passed with limits `15` actions / `60s`;
- pending safe suggestions: `0`.

What changed:

- `manual_tail_explanation` groups the remaining queue by reason, row count, lane, label and seconds;
- stronger-audio-judge can close a narrow timing/double-talk overlap as safe `keep_me` when local
  evidence is strong and remote/leak evidence is weak;
- suggested review now leaves ambiguous rows open instead of turning uncertainty into transcript
  edits;
- corpus gates now treat review action count as a product metric, not as a loose diagnostic;
- low-materiality single-word tails and short exact partial duplicates no longer inflate the
  mandatory review queue.

Verification:

- `py_compile` over the changed Python entry points;
- `git diff --check`;
- `scripts/check-corpus-gates.py --no-fail`;
- `scripts/smoke-fixture.sh`;
- `swift build`;
- `scripts/smoke-cli-handoff.sh`.

The goal is complete because all remaining review rows are either weak/conflicting audio evidence,
ambiguous chronology, possible unique `Me` content inside remote leak, or local-recall evidence that
is not strong enough for an automatic decision. Those rows are explicit manual tail, not hidden
automation debt.

## Latest Completed Goal: ASR-Positive Echo Candidate Hardening v1

Status, 2026-07-01: implemented as an explicit shadow/experimental profile. Target-Me Evidence
Hardening v1 is complete and pushed in `a6e9f48`: the mandatory operational review queue fell from
`7` actions / `11.19s` to `5` actions / `8.78s`, and corpus gate passes as `passed_with_warnings`.

Recommended follow-up inside the new reliability route: **ASR-positive Echo promotion readiness**.
Keep `local_fir` as default, keep `coverage_v2_remote_gate_local_fir` shadow-only, widen validation
beyond the current six-session candidate corpus, and define the future default-promotion bar with
rollback and inspection rules.

Goal:

```text
ASR-Positive Echo Candidate Hardening v1: превратить `coverage_v2_remote_gate_local_fir`
из успешного shadow audio candidate в строго проверяемый экспериментальный Echo Guard профиль,
который может быть запущен на всём рабочем корпусе, снижает ASR-visible remote leakage, не
ухудшает local recall, не меняет raw CAF и не становится default без corpus gates.
```

Plainly: keep `local_fir` as the default, but make the first ASR-positive stronger mic candidate
usable as a repeatable, measurable and reviewable profile. The goal is not “perfect echo removal in
one step”. The goal is a safe promotion path for a candidate that now improves `5/6` evaluated
sessions without local-recall regression.

Current corpus snapshot after Review Burden Reduction follow-up on 2026-07-02:

- working sessions in scope: `24`;
- diagnostic sessions excluded from readiness: `26`;
- operational verdict: `pilot_ready_with_review`;
- `ready_for_notes`: `15`;
- `review_first`: `9`;
- `do_not_use_without_manual_review`: `0`;
- notes review burden: `1.32 min`;
- transcript/export review burden: `4.09 min`;
- mandatory review queue: `9` actions / `12` rows;
- low-materiality rows outside mandatory review: `28` rows / `70.95s`;
- corpus gate review limits: `15` actions / `25` rows;
- irreducible review gate: `irreducible_manual_review_queue_present`;
- pending safe suggestions: `0`.

Why this goal mattered:

- CLI, review, export and corpus gates are already usable enough for pilot work;
- Target-Me hardening closed only the rows that could be closed safely and left the truly ambiguous
  queue explicit;
- stronger-audio-judge closed one `check_transcript_order` overlap as `keep_me` only after
  mic-clean text strongly matched `Me` and remote text stayed isolated on the remote track;
- `manual_tail_explanation` now explains the remaining queue by reason, row count and seconds;
- single-word `так` tails without action/decision/risk markers are now counted as low-materiality
  instead of mandatory review;
- short exact partial remote duplicates with no unique `Me` content are also kept out of the
  mandatory queue;
- `coverage_v2_remote_gate_local_fir` is the first real audio candidate with ASR-positive evidence:
  it has now reached `5/6` safe improved evaluated sessions and `0/6` local-recall regressions;
- if stronger mic audio becomes safer, less work is pushed into transcript repair, Target-Me review
  and remote-duplicate cleanup.

Target-Me Evidence Hardening v1 completion notes:

- audio-review packs now include open readiness review-plan rows, so Target-Me sees the same rows as
  the mandatory review queue;
- Target-Me rows carry `source_audit_ids`, and review-lane suggestions match them by
  `source_audit_id` plus interval overlap;
- local-recall rows can now be answered as `drop_me` by a reviewer or by a future high-confidence
  safe suggestion; transcript-order rows remain keep/review/skip only;
- two safe `keep_me` suggestions were applied: one lost-Me/local-recall row and one order-risk row;
- no automatic transcript edit was made outside the existing review/apply flow.

Remaining review queue after the completed Target-Me pass:

- `sessions/2026-06-30_11-15-56`: one local-recall check, `1.08s`;
- `sessions/2026-07-01_14-01-09`: one local-recall check, `0.92s`;
- `sessions/2026-07-01_11-17-22`: two lost-Me checks, `2.72s`;
- `sessions/2026-06-30_17-17-20`: one uncertain audio check, `4.06s`.

This queue is intentionally not auto-closed. It is the current irreducible manual review list for
medium-risk use.

Important scope note: `ready_for_notes` is not the same as unattended full-transcript export. The
current corpus still has transcript/export review surface, and `finish` must block export when
export blockers remain. That is acceptable for the practical v2 state only if `status`, `review
progress`, `finish`, export and corpus reports point to the same residual review work.

Implementation result:

- `murmurmark audit asr-positive-echo-candidate SESSION` writes
  `derived/preprocess/echo/asr_positive_echo_candidate_report.{json,md}`;
- `murmurmark corpus echo-candidate SESSION...` writes
  `sessions/_reports/asr-positive-echo-candidate/asr_positive_echo_candidate_corpus_report.{json,md}`;
- current six-session corpus: `5/6` safe improved, `1/6` not applicable, `0/6` local-recall
  regressions;
- `murmurmark corpus gate` reads the report and enforces `shadow_only_do_not_promote`;
- candidate artifacts stay separate from `mic_for_asr.wav`, raw CAF and default `local_fir`.

Remaining work after this goal:

- widen the corpus before any promotion discussion;
- define rollback and inspection criteria for a non-default promoted bundle;
- keep comparing review burden, Target-Me evidence and remote-forbidden evidence;
- do not change default Echo Guard until a separate promotion-readiness goal passes.

# Recently Completed Goal: Review-Loop Stabilization v1

Status, 2026-07-01: completed and pushed in `bb8317b`.

The goal is to make the CLI path boring and reproducible after a session has been recorded:

```text
record -> process -> review suggested apply -> status/report
```

The important promise is not “zero review”. The promise is that every layer reports the same
remaining review queue, automatic decisions are cumulative, and unresolved seconds stay explicit.

Current implementation:

- `apply-review-workspace-decisions.py` preserves already reviewed rows even when the current
  generated template no longer contains them.
- `suggested_closure` now computes newly closed rows by stable review-row keys, not by list position.
- `report-session-quality.py` uses `review_decisions_progress.remaining_seconds` directly instead of
  reconstructing seconds from rounded minutes.
- `murmurmark review suggested` rebuilds lane packs, refreshes suggestions from cached
  stronger-audio-judge and Target-Me evidence, applies only safe generated answers, and then refreshes
  `reviewed_v1` readiness when anything was closed.
- `murmurmark process` now gives the stronger-audio-judge stage an `80` item budget by default. The
  older `12` item cap was too low for long meetings: on `sessions/2026-07-02_16-01-27`, a broader run
  reduced the manual tail from `78.52s` to `11.19s`, and a later timing-overlap evidence rule reduced
  it again to `8.92s`; on `sessions/2026-07-02_17-32-08`, it reduced
  the tail to `5.03s`.
- `audit-stronger-audio-judge.py` now treats a narrow `probable_timing_overlap` class as safe
  `keep_me` evidence when group-overlap already shows strong local support and weak remote/leak
  support. This closes benign timestamp overlaps without deleting text; conflicting double-talk
  remains manual.
- Targeted stronger-audio-judge is cached-first in the normal suggested path. It does not start a
  long new faster-whisper decode unless explicitly enabled:

```bash
MURMURMARK_TARGETED_JUDGE_COMPUTE=1 \
MURMURMARK_TARGETED_JUDGE_MAX_COMPUTED=4 \
murmurmark review suggested apply "$SESSION"
```

- Target-Me evidence is consumed by lane suggestions as high-confidence `keep_me` support when rows
  already exist. Refreshing Target-Me inside suggested review is explicit because it can be slower
  than the handoff itself:

```bash
MURMURMARK_REVIEW_TARGET_ME_REFRESH=1 murmurmark review suggested apply "$SESSION"
```

Verification snapshot:

- `sessions/2026-07-01_11-17-22`:
  - `review progress`: `2` remaining rows / `2.72s`;
  - workspace `suggested_closure`: `2` remaining rows / `2.72s`;
  - session-quality `review_scope_remaining_seconds`: `2.72s`.
- `sessions/2026-07-01_14-01-09`:
  - `review progress`: `7` remaining rows / `17.3s`;
  - workspace `suggested_closure`: `7` remaining rows / `17.3s`;
  - session-quality `review_scope_remaining_seconds`: `17.3s`.

# Recent Quality Goal: Target-Me Extraction Spike v1

Status, 2026-07-01: in progress as a shadow-only evidence spike. Not promoted.

The goal is to test whether MurmurMark can distinguish the user's real microphone speech from
remote leakage, double-talk and open-space background speech without changing capture, `local_fir`,
the main `whisper.cpp` ASR path or selected production profiles.

The implemented local baseline layer now has two MFCC variants:

- collect enrollment material from high-confidence local-only `Me` utterances;
- `mfcc_voiceprint_v0`: compute a simple local acoustic voiceprint with MFCC/spectral/pitch
  features;
- `mfcc_contrastive_v0`: add clean remote speech as a negative class and score risky clips against
  both `Me` and remote centroids;
- write `derived/audit/target-me/target_me_enrollment.json`;
- write `derived/audit/target-me/target_me_audit.jsonl`;
- write `derived/audit/target-me/target_me_summary.json`;
- write `derived/audit/target-me/target_me_report.md`;
- write corpus output under `sessions/_reports/target-me/`.

`--method auto` uses local WavLM if model files are present, otherwise `resemblyzer_dvector_v0` if
the package is installed, otherwise `mfcc_contrastive_v0`.

The first real local speaker-embedding backend has also been tested:

- `resemblyzer_dvector_v0` uses local `resemblyzer.VoiceEncoder`;
- install command: `.venv/bin/pip install resemblyzer`;
- explicit command: `murmurmark audit target-me "$SESSION" --method resemblyzer_dvector`;
- it writes the same shadow artifacts and does not edit transcripts or cleanup profiles.

The WavLM speaker-embedding backend is wired but not evaluated yet:

- `wavlm_xvector_v0` uses local `transformers` `AutoModelForAudioXVector`;
- default model path: `~/.local/share/murmurmark/models/target-me/wavlm-base-plus-sv`;
- explicit command: `murmurmark audit target-me "$SESSION" --method wavlm_xvector`;
- when model files are missing, the audit writes `status: missing_embedding_model`.

Current local backend probe:

- `torch`: available;
- `transformers`: available;
- `faster_whisper`: available, but it is an ASR judge, not speaker identity;
- `resemblyzer`: available after local `.venv` install;
- `speechbrain`, `pyannote`, `asteroid`, `wespeaker`: not installed;
- local WavLM model files: missing;
- ready source-separation candidate: none.

This keeps the spike local-only and reproducible. No audio is sent to an external service.

Current six-session smoke:

- ready enrollment sessions: `6/6`;
- audited clips: `102`;
- audited seconds: `479.4`;
- method: `mfcc_contrastive_v0`;
- `target_me_possible`: `89` clips / `441.89s`;
- `target_me_ambiguous`: `13` clips / `37.51s`;
- new review-burden reduction: `0` clips / `0.0s`;
- corroborating existing reliable evidence: `0` clips / `0.0s`;
- research decision: `no_clear_gain_yet_keep_as_evidence`;
- promotion decision: `shadow_only_do_not_promote`.

Current six-session d-vector smoke:

- method: `resemblyzer_dvector_v0`;
- ready enrollment sessions: `6/6`;
- audited clips: `102`;
- `target_me_confirmed`: `67` clips / `355.77s`;
- `target_me_possible`: `31` clips / `103.67s`;
- `target_me_ambiguous`: `4` clips / `19.96s`;
- new keep-evidence rows: `13` clips / `48.82s`;
- corroborating existing evidence: `54` clips / `306.95s`;
- readiness impact: `shadow_only_not_applied`; actual `ready_for_notes` / `review_first` / `risky`
  counts do not change until this evidence is integrated into review decisions;
- research decision: `promising_shadow_evidence_continue`;
- promotion decision: `shadow_only_do_not_promote`.

Interpretation: cheap acoustic voiceprints are useful for instrumentation, but they are not strong
enough to solve Target-Me extraction. Local d-vector embeddings are promising as a review/evidence
layer: they can protect real `Me` utterances that older audits marked as `remote_duplicate` or
`uncertain`. The current environment still has no ready source-separation package, and d-vector
evidence must stay behind corpus gates before it can drive automatic review decisions.

Working commands:

```bash
.venv/bin/pip install resemblyzer
murmurmark audit target-me "$SESSION" --profile auto --max-items 80
less "$SESSION/derived/audit/target-me/target_me_report.md"
```

```bash
.venv/bin/python scripts/audit-target-me.py \
  sessions/2026-06-23_14-04-37 \
  sessions/2026-06-25_11-14-27 \
  sessions/2026-06-26_11-15-50 \
  sessions/2026-06-26_12-04-04 \
  sessions/2026-06-29_16-31-02 \
  sessions/2026-06-30_17-17-20 \
  --profile auto \
  --max-items 20
```

# Latest Completed Goal: ASR-Positive Audio Candidate v2

Status, 2026-07-01: completed as a shadow-only Echo Guard candidate. Not promoted.

Remote-Forbidden Evidence Coverage v2 made the audit layer wide enough to judge real suspicious
windows instead of only a few early `remote_only` clips. ASR-positive audio candidate v2 uses that
judge and adds a real audio candidate: `coverage_v2_remote_gate_local_fir`.

The candidate remains shadow-only. `local_fir` is still the production default.

## Goal

Build a shadow Echo Guard candidate that reduces recognizable remote words in the `Me` path better
than current `local_fir`, without losing local-only words.

In plain words: use the Coverage v2 ASR windows as a judge, run audio-side cleanup candidates
through the same token-level remote-forbidden audit, and keep only candidates that improve remote
leakage without making the user's real speech worse.

## Why This Goal Now

Post-ASR cleanup, review lanes and remote-forbidden transcript guards are useful, but they are
compensation layers. The root problem remains that remote speech can enter the mic track and become
recognizable as `Me`.

Coverage v2 changed the situation:

- it selects suspicious ASR windows from speaker state, audio-review, stronger audio judge,
  group-overlap, transcript-overlap and local/order risk artifacts;
- the six-session smoke improved from `1/6` to `4/6` safe ASR-visible cases;
- local-only word recall did not regress;
- every selected window has an explainable `selection_reason`.

That measurement was enough to run a real candidate search without relying on loudness or ERLE
proxies.

## Scope

In scope:

- add one or more shadow audio candidates under `derived/preprocess/audio/`;
- evaluate every candidate with Coverage v2 ASR windows;
- compare candidates against `local_fir`, not against raw mic only;
- report remote-token leakage before/after, local-word recall, guarded seconds and review burden;
- keep candidate artifacts separate from `mic_for_asr.wav`;
- explain why each candidate wins, loses or remains inconclusive.

Candidate families worth trying first:

- smarter segment switching between `local_fir`, `remote_floor` and raw mic;
- stricter remote-only masking only where speaker state and ASR evidence agree;
- adaptive residual masking driven by `echo_hat`, remote energy and token guard outcome;
- per-window candidate selection: different cleanup strength for remote-only, boundary and
  double-talk windows.

Implemented v2 candidate:

- starts from the safer local-fir/segment-switch path;
- reads Coverage v2 ASR windows and their `selection_reason`;
- applies `remote_floor` cleanup only where the window is remote-risky and speaker-state does not
  show strong local speech;
- writes `offline_aec_v2_coverage_gate_plan.jsonl`;
- appears in ASR audit/report fields as `coverage_v2_remote_gate_local_fir`.

Out of scope:

- replacing `local_fir` as default;
- changing capture, raw CAF tracks, main `whisper.cpp` ASR or selected transcript profiles;
- adding cloud models;
- silently deleting uncertain `Me` text;
- claiming waveform-perfect echo removal;
- training a neural suppressor before the deterministic candidate search is exhausted.

## Acceptance

- Raw `audio/mic/*.caf` and `audio/remote/*.caf` are unchanged: passed.
- `local_fir` remains default: passed.
- At least one shadow candidate reduces ASR-visible remote-token leakage versus `local_fir` on the
  six-session smoke corpus: passed.
- Local-only word recall does not degrade by more than 2 percentage points on any evaluated
  session: passed.
- Corpus report explains every non-improved session as one of:
  `no_baseline_asr_visible_leak`, `candidate_not_better`, `local_recall_risk`,
  `asr_audit_inconclusive` or `not_enough_evidence`: passed.
- `murmurmark audit remote-forbidden` and corpus reports show candidate comparison clearly: passed.
- No audio or transcript candidate is promoted to default: passed.

## Working Commands

Baseline evidence audit:

```bash
murmurmark audit remote-forbidden "$SESSION" --profile auto
```

Current lab command for candidate evaluation:

```bash
.venv/bin/python scripts/echo-guard-offline-aec-v2-lab.py "$SESSION" \
  --asr-audit \
  --asr-window-profile coverage_v2 \
  --asr-max-clips 2 \
  --asr-max-risk-clips 2 \
  --asr-max-local-clips 1 \
  --asr-candidate-keys coverage_v2_remote_gate_local_fir
```

Corpus summary:

```bash
.venv/bin/python scripts/report-offline-aec-v2-corpus.py SESSION...
.venv/bin/python scripts/report-remote-forbidden-corpus.py SESSION...
```

Expected user-facing shape after this goal:

```bash
murmurmark audit remote-forbidden "$SESSION" --profile auto
murmurmark report "$SESSION"
less "$SESSION/derived/audit/remote-forbidden/remote_forbidden_review.md"
```

The result is shadow-only, but the report now says whether an audio candidate is actually better
than `local_fir`.

## Current Finding

Six-session smoke after ASR-positive audio candidate v2:

- `reports_found = 6/6`;
- `asr_audio_candidate_gate_passed = 4`;
- `asr_audio_candidate_safe_improved = 4`;
- `asr_local_word_recall_regressions = 0`;
- assessment classes:
  - `safe_improved = 4`;
  - `no_baseline_asr_visible_leak = 2`;
- `promotion_decision = do_not_promote_from_v0_corpus_report`.

Interpretation: the goal is achieved as a shadow candidate. The candidate is useful evidence and a
stronger research baseline, but it is not yet a production replacement for `local_fir`.

## Historical Larger Goals After This Layer

These were the next options when ASR-positive audio candidate v2 had just landed:

1. Target-Me Evidence Hardening v1: integrate `resemblyzer_dvector_v0` into review suggestions
   behind corpus gates, without automatic transcript edits.
2. Neural residual echo suppression spike: test a narrow local model only after deterministic
   candidate reports prove where it is needed.
3. Token-level remote-forbidden transcript reconciliation: keep a final safety net even when audio
   cleanup improves.

## Latest Completed Goal: Remote-Forbidden Evidence Coverage v2

Status, 2026-07-01: completed as a shadow/review evidence coverage layer. Not promoted.

Coverage v2 fixed the main v1 blocker. Instead of auditing only the first small set of
speaker-state clips, it selects risky ASR windows from speaker state, audio-review rows, stronger
audio judge rows, group overlaps, transcript overlaps and local/order risk artifacts.

Six-session smoke after Coverage v2:

- `reports_found = 6/6`;
- `safe_improved_sessions = 4`;
- `local_recall_regressions = 0`;
- `asr_windows_evaluable = 24`;
- `asr_windows_skipped = 578`;
- `suggest_drop_count = 1`;
- `quarantine_count = 16`;
- `needs_review_count = 1`;
- `target_status = target_met_two_sessions`.

Interpretation: Coverage v2 meets the evidence target. It does not promote audio or transcript
candidates. Its main value is that the next audio-candidate search now has a real ASR-visible judge.

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
