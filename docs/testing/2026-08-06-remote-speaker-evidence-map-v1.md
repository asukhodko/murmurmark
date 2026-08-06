# Remote Speaker Evidence Map v1

Status: `PROMOTE_AUDIT_ONLY`

Remote Speaker Evidence Map v1 adds local, session-scoped anonymous speaker evidence over the
authoritative remote track. It does not rename people, rewrite selected dialogue or change Evidence
Handoff v2, notes, verdict and guarded export.

## Implementation

- `scripts/audit-remote-speaker-evidence.py` takes the selected dialogue and canonical or raw remote
  audio, embeds bounded remote utterances with local Resemblyzer `0.1.4`, and clusters them with
  deterministic agglomerative cosine clustering.
- A cluster is publishable only with at least `10` evidence units, `60s` speech, `60s` temporal span
  and median cohesion `>=0.85`. Assignment additionally requires similarity `>=0.72` and nearest
  cluster margin `>=0.02`.
- Anonymous IDs are ordered by first confident interval and exist only inside one session.
- Short, long, low-margin, overlapping, minor-cluster and model-failure regions remain aggregate
  `Colleagues` with an explicit reason.
- Whole-session clusters are checked against reverse-order replay and `600s` chunk replay. Chunk
  clusters use a stricter `0.10` centroid merge distance so similar speakers are not joined merely
  because their broad utterance clusters are close.
- `scripts/report-remote-speaker-evidence-corpus.py` freezes inputs, verifies lossless rich output,
  checks 1x1/group speaker-count ranges and evaluates a private reference without publishing names
  or reference text.

## Frozen Corpus

The tracked manifest is `docs/testing/remote-speaker-evidence-map-v1-manifest.json`. It contains
only session IDs, expected anonymous-speaker ranges, model/runtime fingerprints, parameters and
SHA-256 values for inputs, outputs and both implementation scripts. The private reference text stays
under ignored session reports.

| Session kind | Sessions | Published speakers |
|---|---:|---:|
| 1x1 controls | 2 | 1 and 1 |
| Group controls | 4 | 5, 2, 3 and 2 |

Corpus totals:

- sessions: `6`;
- remote utterances: `1235`;
- attributed utterances: `629`;
- aggregate utterances: `606`;
- attributed speech: `4490.170s`;
- aggregate speech: `4420.800s`;
- attributed speech ratio: `0.503892`;
- reverse-order and publishable chunk-replay gates: passed on `6/6`;
- boundary shift: `0s` on `6/6`;
- raw remote and selected dialogue integrity: passed on `6/6`.

## Private Reference Evaluation

The group reference aligned `123` remote utterances. The mapper attributed `66` (`0.536585`) and
abstained on the rest.

- attributed-only adjusted Rand index: `0.865804`;
- attributed-only B-cubed F1: `0.913884`;
- attributed-only pairwise precision/recall/F1: `0.909222`;
- conservative B-cubed precision with every abstention retained: `0.953440`;
- conservative B-cubed recall: `0.293950`.

The conservative recall is intentionally low: unsupported rows are not forced into a speaker.
Coverage and attributed-only correctness are therefore separate gates.

## Decision

`PROMOTE_AUDIT_ONLY` means the anonymous map is suitable as optional, local evidence and as input
to a separately gated rich-transcript stage. It does not make per-person labels authoritative and
does not affect the ordinary Markdown transcript.

Remaining limits:

- roughly half of remote speech remains aggregate;
- boundaries come from selected remote utterances, not an independent frame-level diarizer;
- an utterance with an internal speaker switch may remain aggregate rather than be split;
- names, identity across meetings and automatic participant matching are forbidden;
- missing Resemblyzer/model/audio, stale inputs or failed consistency gates publish an explicit
  aggregate fallback.

The next stage is Anonymous Rich Transcript Handoff v1: bind current anonymous evidence to a
versioned optional rich artifact and CLI surface without weakening the authoritative plain path.
