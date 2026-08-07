# Remote Speaker Coverage v3 Runbook

V3 is a promoted optional read profile. It improves anonymous remote-speaker coverage without
changing the ordinary transcript.

## One Session

```bash
SESSION="sessions/<session-id>"

murmurmark audit remote-coverage "$SESSION"
murmurmark transcript "$SESSION" --rich
```

`audit remote-coverage` builds v2 first when required, then writes v3 evidence. Verify an existing
result without recomputation:

```bash
murmurmark audit remote-coverage "$SESSION" --verify-only
```

If v3 is missing, stale or invalid, `transcript --rich` falls back to promoted v2 and then to the
aggregate `Colleagues` transcript according to the existing rich-transcript chain.

## Frozen Corpus

```bash
murmurmark corpus remote-coverage all
murmurmark corpus remote-coverage all --verify-existing
```

The report is written to:

```text
sessions/_reports/remote-speaker-coverage-v3/
  remote_speaker_coverage_corpus_report.json
  remote_speaker_coverage_corpus_report.md
```

The tracked lineage is `docs/testing/remote-speaker-coverage-v3-manifest.json`. Publication also
requires `policies/remote-speaker-coverage-v3.json` to match that manifest.

## Reading The Result

The expected promoted corpus summary is:

```text
decision: PROMOTE
recovered: 368 words / 199.533s
remaining unknown: 851 words / 598.240s
attributable remote speech: 0.939312
B-cubed F1: 0.962171
pairwise precision: 0.961675
```

Use `unknown_cause_map` to choose the next bounded experiment. Do not lower thresholds merely to
move rows out of `unknown`; `conflicting_frame_speakers` and `protected_remote_overlap` are expected
to remain uncertain without new independent evidence.

## Failure And Recovery

- `FALLBACK_V2` means policy, v2 lineage or source fingerprints are missing or stale.
- `DO_NOT_PROMOTE` means the candidate corpus missed a quality or conservation gate.
- Rebuild v2 only when its own inputs changed; otherwise keep the frozen evidence intact.
- Never use `--force` to hide a fingerprint mismatch. Find the changed input and qualify it again.
