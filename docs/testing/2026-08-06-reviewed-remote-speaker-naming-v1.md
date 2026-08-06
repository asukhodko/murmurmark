# Reviewed Remote Speaker Naming v1

Date: 2026-08-06

Decision: `PROMOTE_OPTIONAL_REVIEWED_NAMING`

## Result

The optional reviewed layer accepts display labels only from
`review/remote-speaker-labels.v1.json`. The decision is bound to the current anonymous rich semantic
fingerprint, ordered speaker IDs and exact evidence counts/bounds. It cannot infer names from voice,
text, contacts, calendars or another session.

The frozen six-session corpus passed:

- sessions: `6/6`;
- anonymous speaker IDs available for explicit review: `14`;
- protected remote utterance references: `1235`;
- attributed remote utterances: `629`;
- aggregate `Colleagues` fallbacks: `606`;
- deterministic templates: `6/6`;
- missing-decision anonymous fail-open: `6/6`;
- ordinary output identities current before and after analysis: `6/6`;
- synthetic contract checker: passed;
- frozen replay: byte-identical.

The machine-readable decision is
`docs/testing/reviewed-remote-speaker-naming-v1-manifest.json`.

## Safety Evidence

The synthetic regression proves:

- unresolved, incomplete, stale, reserved, path-like and duplicate labels are rejected;
- `keep_anonymous` preserves the anonymous ID and aggregate evidence remains `Colleagues`;
- reviewed JSON retains the anonymous utterances and attribution rows exactly;
- private display labels are omitted from pointer manifests and reports;
- unchanged inputs replay byte-identically;
- an interrupted publication preserves the previous current pointer;
- missing decisions make `--rich --reviewed-speakers` fall back to verified anonymous rich output;
- plain transcript access remains unchanged.

## Promotion Boundary

Promotion covers only:

```bash
murmurmark speakers template "$SESSION"
murmurmark speakers apply "$SESSION"
murmurmark speakers status "$SESSION"
murmurmark transcript "$SESSION" --rich --reviewed-speakers
```

Plain transcript, notes, quality verdict, Evidence Handoff v2, auto-selection, guarded export and
retention do not consume reviewed labels. Speaker-aware notes/export requires a separate corpus
decision. Cross-session identity and voice-only naming remain forbidden.
