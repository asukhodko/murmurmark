# Speaker-Resolved Transcript Default v1 Runbook

The normal meeting lifecycle builds speaker evidence after the authoritative transcript and selects
it automatically when all gates pass.

## Normal Use

```bash
SESSION="sessions/<session-id>"

murmurmark meeting --resume "$SESSION"
murmurmark transcript "$SESSION"
murmurmark status "$SESSION"
murmurmark outcome "$SESSION"
```

Look for:

```text
selected_speaker_profile: remote_speaker_coverage_v3
speaker_resolution_state: selected
```

The Markdown uses anonymous session-local labels such as `remote_speaker_01`. Intervals without
enough evidence remain `Colleagues`.

## Refresh Or Verify

For an older processed session or after review changed the selected transcript profile:

```bash
murmurmark audit speaker-default "$SESSION"
murmurmark audit speaker-default "$SESSION" --verify-only
murmurmark transcript "$SESSION"
```

The refresh may rebuild local v1/v2/v3 speaker evidence. It does not rerun the primary ASR and does
not modify raw CAF or the aggregate transcript.

Use the explicit diagnostic chain only when investigating attribution:

```bash
murmurmark transcript "$SESSION" --rich
murmurmark audit remote-speakers "$SESSION"
murmurmark audit remote-diarization "$SESSION"
murmurmark audit remote-coverage "$SESSION"
```

## Fallback

Typical status:

```text
selected_speaker_profile: aggregate_colleagues
speaker_resolution_state: fallback
speaker_fallback_reason: coverage_artifact_missing
```

Fallback is a valid, safe result. `murmurmark transcript`, Evidence Handoff and export use the exact
aggregate Markdown. Do not bypass a stale hash. Rebuild evidence with `audit speaker-default`; if it
still falls back, keep `Colleagues`.

## Human Names

Anonymous labels are the default. Human-readable names require a complete current-session review:

```bash
murmurmark speakers template "$SESSION"
# Complete review/remote-speaker-labels.v1.json.
murmurmark speakers apply "$SESSION"
murmurmark transcript "$SESSION" --rich --reviewed-speakers
```

Names are never inferred from voice and never reused across sessions automatically.

## Frozen Corpus

```bash
murmurmark corpus speaker-default all --verify-existing
```

To intentionally rebuild local speaker evidence before verification:

```bash
murmurmark corpus speaker-default all --refresh-evidence
```

The report is under `sessions/_reports/speaker-resolved-transcript-default-v1/`; the tracked frozen
lineage is `docs/testing/speaker-resolved-transcript-default-v1-manifest.json`.
