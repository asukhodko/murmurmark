# Speaker-Resolved Transcript Default v1 Contract

Status: `PROMOTE` for the ordinary local transcript, Evidence Handoff v2 and guarded export

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

The policy pins the selector, corpus runner and six-session default manifest. Missing Python/model
runtime, stale policy or implementation, a changed selected profile, missing artifacts or a failed
session gate produces a fallback rather than a partial selection.

## Output

The selector writes:

```text
derived/transcript-rich/speaker-resolved-default-v1/
  selection.json
  selection.md
  evidence/<refresh-key>/...
```

`selection.json` uses `murmurmark.speaker_resolved_transcript_selection/v1` and records:

- selected transcript profile and speaker profile;
- `selected` or `fallback` state;
- exact identities of aggregate transcript, selected dialogue, selected Markdown and v3 report;
- policy identity, fallback reason and deterministic semantic fingerprint;
- session-local identity scope and the prohibition on voice-derived names.

## Default Read Rule

For `murmurmark transcript SESSION` with profile `auto`:

1. Validate or materialize the selector.
2. Return the v3 Markdown when state is `selected`.
3. Return the exact selected aggregate Markdown when state is `fallback` or the selector runtime is
   unavailable.

Evidence Handoff v2 copies those same bytes into its bundle. Guarded export copies the handoff
bytes and records `session_local_anonymous` or `aggregate_colleagues` in `export_manifest.json`.
`status`, `outcome` and the meeting final report expose the selected speaker profile, state and
fallback reason.

The meeting lifecycle refreshes this selector after readiness changes and automatic review. The
refresh uses the final selected transcript profile; it runs before `outcome` is rebuilt. If the
profile changed from `audit_cleanup_v2` to `reviewed_v1`, stale evidence from the earlier profile is
never reused. Missing or insufficient evidence still fails open to exact aggregate `Colleagues`.

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
- No unsupported word receives a speaker ID.
- No human name is inferred from voice.
- No local mic multi-speaker or cross-session identity claim is introduced.
- Aggregate fallback is byte-identical through transcript, handoff and export.
- Batch remains authoritative; Live Shadow is not a speaker source.
