# Transcript Perfection Corpus v1 Runbook

This runbook verifies the unified local transcript scorecard. It is a development command, not part
of an ordinary `murmurmark meeting` run.

## Run

```bash
cd murmurmark
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

murmurmark corpus perfection all
```

Inspect the concise report:

```bash
REPORT="sessions/_reports/transcript-perfection-corpus-v1"

cat "$REPORT/transcript_perfection_corpus_report.md"
jq '{decision,summary,gates,release,next_goal}' \
  "$REPORT/transcript_perfection_corpus_report.json"
jq -s '.' "$REPORT/residual_ranking.jsonl"
```

Verify deterministic replay without rewriting outputs:

```bash
murmurmark corpus perfection all --verify-existing
```

Expected frozen baseline:

```text
decision: BASELINE_ESTABLISHED
sources: 12/12 verified
release_ready: false
largest_actionable_residual: unknown_remote_speaker (797.773s)
next_goal: Remote Speaker Coverage v3
```

## Reading The Result

- `BASELINE_ESTABLISHED` means the scorecard and frozen lineage are valid.
- `release_ready: false` means transcript mission gaps remain.
- `not_measured` is an evidence gap, never an implicit pass.
- residual seconds belong to different frozen scopes and must not be summed.
- the first residual row selects the next bounded engineering goal.

The current baseline ranks:

1. `unknown_remote_speaker`: `797.773s`, 1219 words, 6 sessions;
2. `chronology_conflict`: `62.690s`, 14 rows, 10 sessions;
3. `ambiguous_me_audio_evidence`: `196.280s`, 65 rows, 13 sessions;
4. `missing_me_uncertainty`: `21.120s`, 4 rows, 4 sessions.

Chronology ranks above the longer ambiguous-audio queue because it has greater current
repairability. The score is a planning aid, not a product quality percentage.

## Stale Or Missing Inputs

Any required size, hash, schema or source-gate mismatch produces `INVALID_INPUTS` and exit code 2.
Do not update the tracked hash to silence this error. First determine whether the source corpus was
intentionally requalified. A legitimate rebaseline must update its own frozen decision, then this
manifest and the testing snapshot in the same reviewed change.

Private references and raw CAF remain under `sessions/`. Do not commit them.
