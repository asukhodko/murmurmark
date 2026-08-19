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
enough evidence are explicit `remote_speaker_unknown` in the ordinary read view.

## Known Participant Count

If a group call is `provisional` because one voice was split into multiple acoustic clusters or a
real participant spoke too briefly for the global publication floor, record the known remote roster
and read the transcript again:

```bash
murmurmark speakers roster "$SESSION" \
  --expected-remote-speakers 4 \
  --participant "Participant A" \
  --participant "Participant B" \
  --participant "Participant C" \
  --participant "Participant D"

murmurmark transcript "$SESSION"
```

The roster constrains count only. Output remains anonymous until names are explicitly reviewed.
The selector automatically invalidates stale speaker evidence and runs the bounded two-backend
check. A short participant still needs at least 6 usable utterances, 24 seconds of speech, broad
session span, high cohesion and stable independent separation. If the models or temporal evidence
disagree, the command keeps a provisional/unknown read view and does not claim verified people.

Inspect the configured roster without changing it:

```bash
murmurmark speakers roster "$SESSION" --status
```

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

## Provisional Or Unavailable Attribution

Typical status:

```text
selected_speaker_profile: remote_speaker_provisional_v1
speaker_resolution_state: provisional
speaker_attribution_coverage: 32.71%
speaker_fallback_reason: coverage_not_publishable:published_speech_ratio
```

The transcript header is mandatory: it reports strict failure reason, attributed coverage and warns
that anonymous labels can merge or split people. `remote_speaker_unknown` is genuinely unattributed
speech, not one participant. If no current compatible evidence exists, state becomes `unavailable`
and all remote speech receives that unknown label.

Do not bypass stale hashes. Rebuild strict evidence with `audit speaker-default`; ordinary
`murmurmark transcript` materializes the provisional view automatically. Use the exact role-only
fallback only when it is explicitly needed:

```bash
murmurmark transcript "$SESSION" --aggregate --cat
```

Evidence Handoff and guarded export remain strict and do not promote provisional labels.

## Human Names

Anonymous labels are the default. Human-readable names require a complete current-session review:

```bash
murmurmark speakers template "$SESSION"
# Complete review/remote-speaker-labels.v1.json.
murmurmark speakers apply "$SESSION"
murmurmark transcript "$SESSION" --rich --reviewed-speakers
```

Names are never inferred from voice and never reused across sessions automatically.

The roster command does not replace this naming review. It can establish that four remote voices
should exist, but cannot establish which voice belongs to which participant.

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
