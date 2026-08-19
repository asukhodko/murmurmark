# Speaker-Resolved Transcript Default v1 Contract

Status: strict `PROMOTE`; provisional ordinary-read fallback enabled

Speaker-Resolved Transcript Default v1 selects the promoted Remote Speaker Coverage v3 view for a
session only when its complete evidence lineage is current. It never rewrites words, roles,
timestamps or ordering. Unsupported remote words remain aggregate `Colleagues`.

## Selection Inputs

The selector reads:

- `derived/readiness/session_readiness.json` and its selected dialogue/transcript profile;
- `policies/speaker-resolved-transcript-default-v1.json`;
- promoted Coverage v3 policy, implementation and frozen corpus manifest;
- a Coverage v3 report, rich transcript and artifact manifest over the same selected dialogue;
- every input and output SHA-256 verified by Coverage v3.
- optional `speaker-roster-v1.json`, whose identity is bound into selection and refresh keys.

The policy pins the strict selector, corpus runner and six-session default manifest. Missing
Python/model runtime, stale policy or implementation, a changed selected profile, missing artifacts
or a failed session gate still prevents a verified selection.

## Output

The selector writes:

```text
derived/transcript-rich/speaker-resolved-default-v1/
  selection.json
  selection.md
  evidence/<refresh-key>/...
  provisional/
    selection.json
    transcript.provisional.json
    transcript.provisional.md
    evidence/<refresh-key>/...
```

`selection.json` uses `murmurmark.speaker_resolved_transcript_selection/v1` and records:

- selected transcript profile and speaker profile;
- `selected` or `fallback` state;
- exact identities of aggregate transcript, selected dialogue, selected Markdown and v3 report;
- policy identity, fallback reason and deterministic semantic fingerprint;
- session-local identity scope and the prohibition on voice-derived names.

The provisional selection uses `murmurmark.provisional_speaker_transcript_selection/v1`; its rich
payload uses `murmurmark.provisional_speaker_transcript/v1`. It records `provisional` or
`unavailable`, strict failure reasons, attributed duration/count, stable anonymous clusters, exact
input/output identities and the materializer fingerprint.

## Default Read Rule

For `murmurmark transcript SESSION` with profile `auto`:

1. Validate or materialize the selector.
2. Return the v3 Markdown when state is `selected`.
3. If the strict selector falls back, rerun only the current fingerprint-bound v1 evidence with the
   global coverage floor removed. Per-cluster duration, span, cohesion and per-utterance
   similarity/margin gates remain unchanged.
4. Return a disclaimer-bearing provisional Markdown when at least one locally supported cluster is
   available. Unsupported utterances are labelled `remote_speaker_unknown`.
5. If no compatible current evidence exists, return an explicit `unavailable` Markdown in which
   every remote utterance is `remote_speaker_unknown`. It must never look like one real person.

For selected or provisional output, the CLI states that `remote_speaker_N` labels are anonymous
session-local acoustic clusters rather than verified people. Provisional Markdown additionally
warns that one person may be split or several people merged and prints attributed coverage. The
warning is also emitted for `--path-only` and `--cat`; `--aggregate` returns the exact role-only view
without speaker claims.

Evidence Handoff v2 and guarded export continue to consume only strict `selected` or exact aggregate
bytes; provisional evidence cannot silently become a verified external claim. The ordinary
`transcript`, `status`, `outcome` and meeting handoff use the provisional read view when strict
selection fails and expose its state, coverage and strict fallback reason.

The meeting lifecycle refreshes this selector after readiness changes and automatic review. The
refresh uses the final selected transcript profile; it runs before `outcome` is rebuilt. If the
profile changed from `audit_cleanup_v2` to `reviewed_v1`, stale evidence from the earlier profile is
never reused. Missing evidence produces explicit `remote_speaker_unknown`; the exact aggregate
remains available only by explicit request and to strict handoff/export consumers.

When a user provides a current roster count, v1 may repair exactly one acoustically split major
cluster through the separately documented two-backend consensus rule. The roster does not map
human names to voices. Changing or removing it invalidates the previous selection. See
[`Roster-Constrained Remote Speaker Evidence v1`](roster-constrained-remote-speaker-evidence-v1.md).

`--rich` remains a compatible diagnostic path. Human names remain opt-in and require a complete,
fingerprint-bound review decision for the current session. The default path never derives a human
identity from voice and never links speakers across sessions.

## Promotion Evidence

The frozen corpus contains two 1x1 and four group calls. All six selected v3, published 14 expected
session-local speakers and passed:

- exact dialogue, selected text, roles, `Me`, word timestamps and chronological order;
- word conservation, raw-audio preservation and anonymous session-local identity;
- 5/5 internal speaker-boundary controls;
- deterministic replay and frozen artifact identities.

Coverage remains `93.9312%` on the original frozen aggregate. The default qualification preserves
the honest residual: 851 words / `597.800s` remain aggregate `Colleagues` in the refreshed current
profiles.

## Safety Boundary

- Capture, Echo Guard, audio selection, primary ASR and selected dialogue are unchanged.
- No unsupported word receives a speaker ID; it is marked `remote_speaker_unknown`.
- Removing the provisional global coverage floor never removes local cluster or assignment gates.
- No human name is inferred from voice.
- No local mic multi-speaker or cross-session identity claim is introduced.
- Aggregate fallback is byte-identical through transcript, handoff and export.
- Batch remains authoritative; Live Shadow is not a speaker source.
