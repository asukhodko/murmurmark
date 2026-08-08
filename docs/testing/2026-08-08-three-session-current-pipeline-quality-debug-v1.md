# Three-Session Current Pipeline Quality Debug v1

Date: 2026-08-08

Decision: `FIX_LEGACY_CACHE_PREFLIGHT / KEEP_QUALITY_FALLBACKS`.

## Scope

The current pipeline was replayed on three private real-session classes without reading or
publishing their speech text:

| Scenario | Shape | Acoustic condition | Duration |
|---|---|---|---:|
| A | group, several remote voices | speaker playback | 5897.418s |
| B | 1x1 | speaker playback | 1884.346s |
| C | group | headset and noisy office | 3293.409s |

The private freeze includes `session.json`, both raw CAF tracks and the selected transcript that
existed before replay. All six raw CAF hashes and all three `session.json` hashes were unchanged
after the runs. No speech text, names, private paths or audio hashes are stored in this public
report.

## Confirmed Defect And Fix

`--force-asr --reuse-asr-cache` used to append `--skip-transcribe` without checking the cache
contract. A legacy v1 cache therefore survived the expensive timeline-repair stage and failed only
at `check_asr_chunk_cache` with `authoritative_chunk_schema_required`.

The runner now executes the exact authoritative rebuild check before choosing cache reuse:

- a complete v2 cache with valid chunk identities, raw metadata, hashes and byte-identical replay
  is reused;
- a missing, legacy, incomplete or corrupt cache prints its per-track reason and is rebuilt;
- the effective decision is recorded in `inputs.asr_invocation` and
  `plan.asr_cache_reuse`;
- a reuse heartbeat says that the cache is validated and timeline repair is still running instead
  of presenting historical `chunks_transcribed` as current work.

`scripts/smoke-process-chunk-resume.sh` now covers the exact legacy
`--force-asr --reuse-asr-cache` combination. Interruption/resume and normal v2 replay remain covered.

## Before And After

| Metric | A before | A after | B before | B after | C before | C after |
|---|---:|---:|---:|---:|---:|---:|
| selected profile | `reviewed_v1` | `reviewed_v1` | `reviewed_v1` | `reviewed_v1` | `audit_cleanup_v2` | `audit_cleanup_v2` |
| verdict | usable with review | usable with review | usable with review | usable with review | usable with review | usable with review |
| needs-review rows | 40 | 40 | 4 | 4 | 37 | 35 |
| local-only island recall | 0.916667 | 0.916667 | 0.925000 | 0.925000 | 0.782609 | 0.928571 |
| order-review seconds | 0.000 | 0.000 | 0.000 | 0.000 | 141.910 | 123.750 |
| transcript-review seconds | 24.600 | 24.600 | 17.800 | 17.800 | 148.030 | 133.030 |

Scenario C improved after its legacy ASR cache was rebuilt by the current pipeline. These are
machine-evidence metrics, not human transcript truth; the remaining order risk stays explicit.
Scenarios A and B retained their original selected transcript SHA-256 exactly. Scenario C kept
the same selected profile and verdict while producing a new current aggregate.

## Layer Findings

### Capture

All sessions are complete, both tracks cover the meeting, capture continuity reports zero observed
gaps and zero restart-correlated loss. No capture change is justified by this sample.

### Pre-ASR Echo And Target-Me

Scenario A passed the promoted Speaker-Preserving Neural Echo v2.17 session gates. Scenario B
failed its per-session candidate gate and returned exactly to local FIR. Scenario C was correctly
classified as headset use, so speaker-playback echo processing was not applicable. The fallbacks
worked as designed; no threshold was tuned from these outcomes.

### ASR And Timeline

All six rebuilt/reused track caches pass authoritative v2 replay with `byte_identical: true` and no
integrity errors. The remaining performance bottleneck is downstream timeline/micro-ASR work:
validated raw-cache reuse still spent about 4.1 minutes on B and 10.5 minutes on A inside
`transcribe_current`. This is a measured future cache/checkpoint target, not a correctness reason to
reuse stale outputs blindly.

### Roles And Review

Scenario A still exposes remote-leak repair risk; scenario B has four transcript-only review rows
after its actionable review scope is exhausted; scenario C still has material chronology risk.
These states remain fail-open and prevent an unsupported `good` verdict. The empty review workspace
for B is an intentional documented non-actionable blocker, not a missing lane.

### Remote Speaker Attribution

The speaker-resolved selector published fingerprint-bound Coverage v3 evidence for all three
scenarios while preserving aggregate words, `Me`, timestamps and chronology:

| Scenario | Published remote speakers | Attributable remote speech |
|---|---:|---:|
| A | 3 | 0.946258 |
| B | 1 | 0.920996 |
| C | 4 | 0.922873 |

`unknown` remains explicit. Machine agreement is not treated as human identity truth.

## Runtime Finding

The full diagnostic path is intentionally not the ordinary user path. On A, authoritative handoff
took 800.652s and deferred enrichment another 1532.481s. Echo candidate evaluation alone took
1232.922s. On C, one-time legacy-cache migration made handoff 1125.402s and the stronger local audio
judge added 850.613s. The transcript existed before deferred enrichment completed.

Normal `meeting` must continue to publish the authoritative handoff first. `--full` remains an
explicit diagnostic command.

## Reproduction

Use private session paths supplied locally; do not add them to public manifests:

```bash
for SESSION in "$GROUP_SESSION" "$ONE_TO_ONE_SESSION" "$NOISY_HEADSET_SESSION"; do
  murmurmark process "$SESSION" \
    --force-asr \
    --reuse-asr-cache \
    --resource-profile opportunistic

  scripts/check-asr-chunk-cache.py "$SESSION" \
    --require-chunks \
    --require-authoritative
done

scripts/report-session-quality.py \
  "$GROUP_SESSION" "$ONE_TO_ONE_SESSION" "$NOISY_HEADSET_SESSION" \
  --out-dir sessions/_reports/three-session-current-pipeline-quality-debug-v1/after
```

The first command now rebuilds a legacy cache automatically. Subsequent runs reuse only an exact
authoritative cache.

## Remaining Boundary

- This sample validates lifecycle behavior and local evidence, not word-perfect human truth.
- None of the three sessions is `good`; all remain `usable_with_review` for explicit reasons.
- Micro-ASR/timeline cache reuse is the clearest measured performance follow-up.
- The active remote-speaker shadow error decomposition remains the quality roadmap's current goal;
  this debugging detour did not justify a new speaker backend or weaker abstention.
