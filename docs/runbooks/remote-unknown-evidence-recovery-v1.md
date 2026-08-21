# Remote Unknown Evidence Recovery v1 Runbook

## Rebuild The Frozen Evaluation

The refresh is offline and CPU-intensive. It runs WavLM with `nice=20`, then builds the deterministic
consensus shadow and both direct-truth comparisons.

```bash
cd /path/to/murmurmark
source .venv/bin/activate

HF_HUB_OFFLINE=1 .venv/bin/python \
  scripts/report-remote-unknown-evidence-recovery-v1-corpus.py all \
  --refresh \
  --write-snapshot docs/testing/remote-unknown-evidence-recovery-v1-snapshot.json
```

## Inspect

```bash
jq '{decision, summary, truth: .truth_evaluation.combined, failed_gates, evidence_bound}' \
  sessions/_reports/remote-unknown-evidence-recovery-v1/remote_unknown_evidence_recovery_corpus_report.json

less sessions/_reports/remote-unknown-evidence-recovery-v1/remote_unknown_evidence_recovery_corpus_report.md
```

Expected decision: `EVIDENCE_BOUND`. Do not point the transcript selector at this profile.

## Deterministic Replay

```bash
.venv/bin/python scripts/report-remote-unknown-evidence-recovery-v1-corpus.py all \
  --verify-existing

.venv/bin/python scripts/check-remote-unknown-evidence-recovery-v1.py
```

`--verify-existing` performs no model inference. It verifies source and output fingerprints and
rebuilds the report in memory.

## Per-Word Diagnosis

Each strict session stores the shadow beside its evidence-fingerprint-bound Coverage v3 directory.
Inspect `recovery_decisions.jsonl`; do not infer correctness from the shadow Markdown alone.

```bash
jq -s '[.[] | select(.outcome == "attributed") | {
  word_id, speaker_id, reason,
  structural: .evidence.structural.supports
}]' \
  "$RECOVERY_DIR/recovery_decisions.jsonl"
```

If the WavLM model or artifacts are absent, the per-session auditor writes
`FALLBACK_COVERAGE_V3`. This is a successful fail-open outcome, not permission to relax evidence.
