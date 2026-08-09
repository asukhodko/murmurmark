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
sources: 27/27 verified
release_ready: false
largest_actionable_residual: unknown_remote_speaker (598.240s)
next_goal: Session-Local Remote Speaker Re-Clustering Feasibility v1
```

## Reading The Result

- `BASELINE_ESTABLISHED` means the scorecard and frozen lineage are valid.
- `release_ready: false` means transcript mission gaps remain.
- `bounded_exact_subset_only` means 67 generated words are measured; real meetings remain reference-insufficient.
- `lexical_prerequisite` names the external human-reviewed seed without overriding the autonomous next goal.
- `remote_speaker_turns.residual_reference_insufficient` keeps 0/53 direct proposal truth visible.
- `truth_lab_control_decision` and `truth_lab_candidate_decision` distinguish a passing control from
  a rejected candidate; synthetic evidence never resolves a real speaker label.
- segment-context hard-v3 metrics keep boundary, identity and open-set failures explicit;
- oracle decomposition keeps the dominant identity gain and smaller boundary/open-set gains separate.
- one-shot hard-v4 keeps the promoted ECAPA lab candidate separate from real-session production evidence.
- frozen real-session error decomposition routes only the dominant interval axis and keeps Coverage v3 authoritative.
- the one-shot interval candidate is closed without tuning after only two new words and one new reference error;
- enrollment weighting is closed after 11 gross gains also removed five control accepts and missed the 5% scope-item gate;
- the 33-item direct-truth seed and eight hidden repeats are complete; repeat consistency is `7/8`;
- 8 primary items have direct speaker attribution, while 11 are unknown, 4 mixed and 10 unusable;
- direct adjudication kept Coverage v3: candidate gained three correct identities, lost two correct
  controls and raised fail-closed unsafe accepts from 8 to 13;
- enrollment purity kept Coverage v3 and reduced unsafe accepts to control, but its seven qualified
  profiles produced zero additions and preserved none of the three confirmed gains;
- homogeneous mining found 39 windows for 9/14 profiles but preserved 0/3 gains, lost three
  correct controls and introduced four false identities;
- the next experiment clusters remote windows without Coverage speaker labels, then measures mapping;
- residual seconds belong to different frozen scopes and must not be summed.
- the first residual row selects the next bounded engineering goal.

The measured non-lexical residual ranking remains:

1. `unknown_remote_speaker`: `598.240s`, 851 words, 6 sessions;
2. `chronology_conflict`: `62.690s`, 14 rows, 10 sessions;
3. `ambiguous_me_audio_evidence`: `196.280s`, 65 rows, 13 sessions;
4. `missing_me_uncertainty`: `21.120s`, 4 rows, 4 sessions.

Chronology ranks above the longer ambiguous-audio queue because it has greater current
repairability. The score is a planning aid, not a product quality percentage.

The lexical prerequisite is not an actionable defect ranking. Inspect it separately with
`murmurmark corpus lexical status`; machine disagreement cannot identify the correct word.

## Stale Or Missing Inputs

Any required size, hash, schema or source-gate mismatch produces `INVALID_INPUTS` and exit code 2.
Do not update the tracked hash to silence this error. First determine whether the source corpus was
intentionally requalified. A legitimate rebaseline must update its own frozen decision, then this
manifest and the testing snapshot in the same reviewed change.

Private references and raw CAF remain under `sessions/`. Do not commit them.
