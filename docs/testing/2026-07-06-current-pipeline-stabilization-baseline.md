# Current Pipeline Stabilization Baseline

Date: 2026-07-06

This baseline belongs to Current Pipeline Stabilization v1. It records the current state of the
existing pipeline without adding new product features or changing raw capture files.

## Checks Run

```bash
caffeinate -dimsu scripts/check.sh
MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
murmurmark report corpus
scripts/check-current-pipeline-stabilization.py
```

Result:

- `scripts/check.sh`: passed.
- capture regression check: passed in static and live modes.
- current-pipeline stabilization audit: passed.
- capture health matrix: `mic-only`, `remote-only` and `mic+remote` pass the early capture gate;
  `silence` and `interrupted` block before ASR.
- process resume smoke: passed; chunk resume, legacy raw-cache rebuild and interrupted process resume
  all complete.
- Ctrl-C recording smoke: passed in `/tmp`; `stop_reason=sigint`, `partial=false`, one mic file
  and one remote file, `inspect` reported `health: ok`.
- fresh short non-live recording with audible content: `sessions/2026-07-06_13-12-08`.
- fresh silent/failed capture blocker reference: `sessions/2026-07-06_11-16-22`.
- ScreenCaptureKit display-state check: when both displays were asleep, `doctor --strict` reported
  `shareable displays: 0` and blocked recording; after `caffeinate -u -t 5`, it saw shareable
  displays again and passed.

## Fresh Session Handoff

`sessions/2026-07-06_13-12-08` produced a non-empty transcript. It is still blocked by review
because the meeting is very short and the review burden ratio is high:

- `status`: `blocked`;
- `next`: `murmurmark review suggested sessions/2026-07-06_13-12-08`;
- `export`: blocked with `review_burden_too_high`;
- `retention plan`: `waiting_for_export`;
- `finish`: repeats the same review blocker.

`sessions/2026-07-06_11-16-22` is classified as failed capture:

- `status`: `blocked`;
- `gate`: `silent_capture`;
- `next`: `murmurmark inspect sessions/2026-07-06_11-16-22`;
- ASR is blocked before producing an empty successful transcript.

## Corpus Baseline

Generated report:

```text
sessions/_reports/session-quality/session_quality_report.json
sessions/_reports/operational-readiness/operational_readiness_report.json
sessions/_reports/current-pipeline-stabilization/current_pipeline_stabilization_check.json
```

All sessions under `sessions/`:

| Category | Count |
| --- | ---: |
| `usable` | 20 |
| `review_first` | 11 |
| `blocked` | 8 |
| `broken_capture_or_incomplete` | 19 |

Operational readiness scope:

| Metric | Value |
| --- | ---: |
| sessions in scope | 30 |
| excluded diagnostic sessions | 28 |
| complete pipeline sessions in scope | 30 |
| ready for notes | 15 |
| review first | 10 |
| do not use without manual review | 5 |
| notes review burden | 3.64 min |
| transcript review burden | 6.41 min |
| review actions | 21 |

Operational verdict: `not_ready`.

Main blocker: risky or failed session verdicts are still present. The current recommended review
focus is `sessions/2026-07-03_12-02-46`, lane `check_transcript_order`.

## Stabilization Meaning

This is not yet completion of Current Pipeline Stabilization v1. It proves that:

- the supported non-live path can produce a non-empty transcript;
- silent capture is blocked before ASR;
- `status` and `next` now agree for the tested blocked cases;
- incomplete sessions no longer show normal notes/transcript handoff just because old files exist;
- corpus classification is available and separates usable, review-first, blocked and incomplete
  sessions.
- `scripts/check-current-pipeline-stabilization.py` now checks these invariants from the corpus
  report and CLI handoff.

Remaining stabilization work:

- reduce or explicitly close the current corpus blockers;
- keep checking that no `pipeline passed + empty transcript` state exists in the real corpus;
- decide whether the 19 incomplete/diagnostic sessions should stay excluded or be renamed/archived;
- keep future changes behind the same checks.
