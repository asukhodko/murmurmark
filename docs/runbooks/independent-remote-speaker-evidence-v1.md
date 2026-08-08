# Independent Remote Speaker Evidence v1 Runbook

Updated: 2026-08-08

## Session Audit

Install the pinned WavLM model at the path recorded in the policy, then run:

```bash
SESSION="sessions/<session-id>"

murmurmark audit remote-coverage "$SESSION"
murmurmark audit remote-independent "$SESSION"

jq '{decision, summary, gates}' \
  "$SESSION/derived/audit/independent-remote-speaker-evidence-v1/report.json"
```

`PUBLISH_EVIDENCE` means the isolated session artifact is internally valid. It does not mean the
corpus profile is promoted. `FALLBACK_V3` is the correct fail-open result for missing model,
incomplete split enrollment, stale lineage or inference failure.

## Frozen Corpus

```bash
murmurmark corpus remote-independent all --verify-existing
```

The command reports the frozen `DO_NOT_PROMOTE`. The underlying reporter exits `2` for that product
decision; the CLI accepts it as a completed evaluation. Reports live under:

```text
sessions/_reports/independent-remote-speaker-evidence-v1/
```

To rebuild the frozen evidence deliberately:

```bash
for SESSION in \
  sessions/2026-06-23_14-04-37 \
  sessions/2026-06-24_15-03-52 \
  sessions/2026-06-26_11-15-50 \
  sessions/2026-06-26_12-04-04 \
  sessions/2026-07-14_15-01-19-live \
  sessions/2026-07-20_15-15-26-live
do
  murmurmark audit remote-independent "$SESSION"
done

.venv/bin/python scripts/report-independent-remote-speaker-evidence-v1-corpus.py all \
  --frozen-manifest docs/testing/independent-remote-speaker-evidence-v1-manifest.json
```

## Interpretation

- `recovered_words/seconds` are proposals supported by independent WavLM split enrollment.
- `candidate_reference_evaluation` states whether those proposals themselves have direct truth.
- `remaining_unknown_*` stays aggregate `Colleagues` in the supported transcript.
- do not use `--require-promoted`: this frozen profile intentionally remains audit-only.

The next evidence step is a private, blind reference corpus targeted at residual words and WavLM
proposals. Model agreement is diagnostic and cannot replace that reference.
