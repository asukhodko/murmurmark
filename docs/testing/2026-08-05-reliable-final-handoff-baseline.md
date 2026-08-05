# Reliable Final Handoff v1 Baseline

Captured: 2026-08-05

This snapshot measures the existing `murmurmark.meeting_lifecycle_report/v1` files before Reliable
Final Handoff v1 changes runtime behavior. It is evidence for prioritization and a comparison point,
not a claim that all historical sessions have identical hardware, configuration or cache state.

## Corpus Snapshot

| Metric | Value |
|---|---:|
| lifecycle reports | 34 |
| `ready_with_review` | 29 |
| `ready` | 1 |
| `failed` | 3 |
| `interrupted` | 1 |
| usable reports with capture timing | 29 |
| median `total_after_stop` | 1746.216s |
| p90 `total_after_stop` | 2982.234s |
| maximum `total_after_stop` | 19467.679s |
| median `total_after_stop / capture` | 0.676 |
| p90 `total_after_stop / capture` | 1.502 |
| maximum `total_after_stop / capture` | 6.383 |

Percentiles use linear interpolation over the 29 reports whose result is `ready` or
`ready_with_review` and whose capture duration is positive.

## Current Outliers

| Session | Capture | After stop | Ratio | Result |
|---|---:|---:|---:|---|
| `2026-08-05_14-16-08` | 3050.140s | 19467.679s | 6.383 | `ready_with_review` |
| `2026-08-05_17-00-29` | 2940.897s | 13367.740s | 4.545 | `ready_with_review` |

For `2026-08-05_17-00-29`, the lifecycle attributed `13274.232s` to authoritative `process`.
The underlying stage log attributes about `10248s` to baseline ASR and `2595s` to the
Speaker-Preserving Neural Echo candidate. The candidate changed only bounded windows, so full
duplicate ASR is a concrete optimization target.

The same session later reached a better `reviewed_v1` transcript through safe suggested review, but
the original lifecycle report remained on `audit_cleanup_v2` and a blocking review state. This is the
actionability and stale-handoff regression case.

## Measurement Rule

The implementation must publish a versioned corpus report that reproduces these aggregates from
frozen report SHA-256 values. Missing or incompatible reports are counted separately, never silently
dropped. Comparisons must distinguish cold ASR, valid cache replay and changed candidate windows.
