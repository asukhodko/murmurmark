# Remote Speaker Residual Evidence v4 Runbook

Updated: 2026-08-07

## Session Audit

Build or reuse promoted v3, then run the isolated v4 evidence pass:

```bash
SESSION="sessions/<session-id>"

murmurmark audit remote-coverage "$SESSION"
murmurmark audit remote-residual "$SESSION"

jq '{decision, summary, gates, failure_reasons}' \
  "$SESSION/derived/audit/remote-speaker-residual-evidence-v4/report.json"
```

`PUBLISH_EVIDENCE` means the session artifact is internally valid. It does not mean the corpus
profile is promoted. Read `cause_ceiling.json` to see which v3 causes were resolved and which stayed
unknown.

## Frozen Corpus

```bash
murmurmark corpus remote-residual all

.venv/bin/python scripts/report-remote-speaker-residual-evidence-v4-corpus.py all \
  --frozen-manifest docs/testing/remote-speaker-residual-evidence-v4-manifest.json \
  --verify-existing
```

The reporter exits `2` for the expected frozen `DO_NOT_PROMOTE`; this is a measured product decision,
not a broken run. Reports live under:

```text
sessions/_reports/remote-speaker-residual-evidence-v4/
```

## Interpretation

- `recovered_words/seconds` are v3 unknown evidence assigned without lowering v3 thresholds.
- `remaining_unknown_words/seconds` remain aggregate `Colleagues` in supported read surfaces.
- `FALLBACK_V3` means the local candidate could not prove its inputs or enrollment.
- `--require-promoted` intentionally fails because the frozen v4 policy is not promoted.

Do not tune similarity or margin until the current report passes. A new model or enrollment method
must become a new isolated profile with its own immutable manifest and reference evaluation.
