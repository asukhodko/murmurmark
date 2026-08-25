# Human-Reviewed Lexical Seed v1 Runbook

This is a one-time development review, not part of an ordinary meeting lifecycle. The frozen queue
contains 24 primary intervals and four blind repeats from two real meetings.

## Install And Check

```bash
cd /path/to/murmurmark
git pull
source .venv/bin/activate
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"

murmurmark corpus lexical-seed-v1 preflight
murmurmark corpus lexical-seed-v1 progress
```

Do not rerun `freeze` after review begins. The command is guarded, but preserving the original
fingerprints is part of the evidence contract.

## Review

Use a quiet environment and headphones when practical:

```bash
murmurmark corpus lexical-seed-v1 review
```

For each clip, type the exact words you hear and press Enter. The production ASR answer is hidden.
Commands at the prompt:

```text
/r  play the clip again (`/к` also works with a Russian keyboard layout)
/i  inaudible speech
/m  mixed speakers
/x  unusable interval
/q  save progress and stop
```

Press Up Arrow and Enter to repeat the previous input. This is useful for replaying a difficult clip
several times: enter `/r` once, then use Up Arrow and Enter for each additional replay. Input history
exists only in the current review process and is not written to disk. Russian-layout aliases `/ш`,
`/ь`, `/ч` and `/й` are accepted for `/i`, `/m`, `/x` and `/q`. Any other input beginning with `/`
is rejected and cannot be saved as reference text. `Ctrl-C` stops review cleanly without losing
completed answers.

The queue resumes at the first unanswered slot. Do not normalize terminology to what the speaker
probably meant; enter the words actually spoken. Punctuation and letter case do not affect WER/CER.

## Evaluate And Replay

After all 28 answers:

```bash
murmurmark corpus lexical-seed-v1 evaluate \
  --write-snapshot docs/testing/human-reviewed-lexical-seed-v1-snapshot.json

murmurmark corpus lexical-seed-v1 replay \
  --write-snapshot docs/testing/human-reviewed-lexical-seed-v1-snapshot.json

jq '{decision,summary,metrics,repeat_review,failed_gates}' \
  sessions/_reports/human-reviewed-lexical-seed-v1/human_reviewed_lexical_seed_report.json
```

`REFERENCE_READY` unlocks the controlled Session-Scoped Lexical Context v1 experiment.
`EVIDENCE_BOUND` is also a complete result: inspect the failed gates before collecting more truth.

## Privacy

Never add files from `sessions/_reports/human-reviewed-lexical-seed-v1/private/` to git. The tracked
snapshot contains no phrases, speaker names or machine-specific paths.
