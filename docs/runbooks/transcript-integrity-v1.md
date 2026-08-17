# Transcript Integrity v1 Runbook

The normal `murmurmark meeting` and `murmurmark process` paths run this stage once before the
authoritative handoff. Deterministic candidates are cheap; only the bounded unresolved set invokes
the local faster-whisper judge. Deferred enrichment never rewrites the selected integrity profile.

## Run Manually

```bash
SESSION="sessions/<session-id>"

.venv/bin/python scripts/apply-transcript-integrity.py "$SESSION" \
  --input-profile auto \
  --judge-mode auto

murmurmark synthesize "$SESSION" --transcript-profile auto
.venv/bin/python scripts/report-session-quality.py "$SESSION"
```

Use `--judge-mode cached` to reuse matching evidence without inference and `--judge-mode off` to
inspect only deterministic candidates. Missing local audio or model evidence leaves candidates
unresolved.

## Inspect The Result

```bash
REPORT="$SESSION/derived/transcript-simple/whisper-cpp/text-integrity/transcript_integrity_report.transcript_integrity_v1.json"

jq '{input_profile, output_profile, summary, gates, judge}' "$REPORT"
cat "$SESSION/derived/transcript-simple/whisper-cpp/text-integrity/transcript_integrity_review.transcript_integrity_v1.jsonl"
murmurmark status "$SESSION"
murmurmark transcript "$SESSION" --aggregate --cat
```

`applied_patch_count` is the number of proven repairs. `remaining_review_count` is intentionally
not an error: it means the evidence was too weak to change the transcript. Inspect the JSONL patch
and review rows when evaluating a new rule.

## Requalify The Corpus

Only requalify after changing `scripts/apply-transcript-integrity.py`:

```bash
.venv/bin/python scripts/report-transcript-integrity-corpus.py \
  sessions/<session-a> sessions/<session-b> sessions/<session-c> \
  --write-policy policies/transcript-integrity-v1.json
```

Commit a new policy only when the decision is `PROMOTE`, all frozen inputs and outputs are current,
raw fingerprints still match, and every safety gate passes. A changed algorithm hash without a new
qualification disables automatic selection.

## Recovery

No rollback is needed. Delete or ignore the `transcript_integrity_v1` artifacts and rerun
`murmurmark process "$SESSION"`; the selector falls back to the prior current profile whenever the
integrity report, policy or hashes are absent or stale.
