# Roster-Constrained Remote Speaker Evidence v1

Status: production extension of Remote Speaker Evidence v1 with fail-open anonymous output

Some group calls contain one real participant whose voice forms two acoustically stable clusters,
for example after a device, codec or room condition changes. Other calls contain a participant who
speaks for less than the ordinary `10`-unit / `60s` publication floor. A fixed global threshold can
then over-count or under-count speakers even when the meeting roster gives the correct number.

This extension lets a user record that count without assigning names to voices:

```bash
murmurmark speakers roster "$SESSION" \
  --expected-remote-speakers 4 \
  --participant "Participant A" \
  --participant "Participant B" \
  --participant "Participant C" \
  --participant "Participant D"
```

The command writes `derived/transcript-rich/speaker-roster-v1.json` with schema
`murmurmark.remote_speaker_roster/v1`. Participant labels are roster metadata only.
`voice_identity_mapping` is always `not_asserted`; no name is assigned from voice, transcript,
contacts or calendar data.

## Decision Rule

With no roster, Remote Speaker Evidence v1 behaves exactly as before. With a current roster it:

1. Searches a bounded clustering-distance grid and keeps only partitions with reverse-order
   stability, chunk-replay ARI at least `0.90`, replay coverage at least `0.90` and matching major
   cluster counts.
2. Prefers the stable partition closest to the expected remote-speaker count.
3. When exactly one stable short cluster is needed to reach the roster count, accepts it only with
   at least `6` units, `24s` of speech, `60s` span and cohesion `>=0.90`. Reverse-order and chunk
   replay must remain stable, and Resemblyzer plus the independent local WeSpeaker ResNet34-LM
   backend must both keep all roster clusters distinct.
4. When exactly one extra major cluster remains, asks WeSpeaker to embed at most 12 high-cohesion
   representatives per major cluster.
5. Merges one pair only when Resemblyzer similarity is at least `0.86`, WeSpeaker similarity at
   least `0.78`, the winning pair leads alternatives by at least `0.12`, the clusters have a
   speaker-like temporal handoff within `5s`, and contradictory overlap is at most `0.5s`.
6. Publishes only if the final cluster count equals the asserted roster count and every
   ordinary v1 safety gate passes.

Missing models, malformed roster data, unsupported short clusters, model disagreement, an ambiguous
winning pair or temporal conflict produces `DO_NOT_PUBLISH`. The selected aggregate transcript
remains unchanged.

## Outputs And Provenance

The ordinary v1 report records:

- the roster SHA-256 and expected count;
- every tested clustering threshold;
- both embedding-model fingerprints;
- every candidate merge pair, similarities, temporal evidence, winner margin and gates;
- whether a merge was applied and why;
- `identity_mapping_applied: false`.

The speaker-resolved selector includes the roster, v1/v2/v3 implementations and local consensus
model in its refresh key. Adding or changing a roster invalidates an older selection and cached
evidence. Unsupported words remain `Colleagues`; `Me`, text, timestamps, order and raw CAF are not
modified.
