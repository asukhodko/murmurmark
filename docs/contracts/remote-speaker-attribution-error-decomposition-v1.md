# Remote Speaker Attribution Error Decomposition v1

Status: completed with `ADVANCE_STRONGER_SPEAKER_IDENTITY`.

## Purpose

This layer determines which subsystem limits anonymous remote-speaker attribution before another
model or topology is attempted. It reads frozen synthetic truth and existing predictions. It cannot
select or promote a production candidate.

## Inputs

The input freeze covers three exact corpora:

- Controlled Remote Speaker Truth Lab v1, hard split;
- Duration-Aware hard-v2, opened once;
- Segment-Context hard-v3, opened once.

For each corpus the freeze records SHA-256 for exact word and boundary truth, selected and control
predictions, frozen manifests, decisions, replay reports, candidate freezes and opening ledgers when
present. The hard-v2 and hard-v3 ledgers must remain `completed` with `decision_open_count: 1`.

## Oracle Matrix

`current` preserves every existing word label. The diagnostic tracks change one axis at a time:

| Track | Changed axis | Preserved evidence |
|---|---|---|
| `oracle_boundaries_current_identity` | exact event boundaries | current identity votes and special-class behavior |
| `current_boundaries_oracle_identity` | identity assigned to each current segment | current segment partition and special-class behavior |
| `overlap_open_set_oracle` | exact abstention for open-set and mixed words | every known-speaker prediction and current partition |
| `full_oracle_control` | all labels | validation only; never used to choose an implementation |

Open-set truth normalizes to `unknown_speaker`; mixed truth normalizes to `mixed`. Aggregate speaker
IDs are namespaced by corpus so equal synthetic IDs from independent corpora never imply shared
identity.

## Outputs

Default root:

```text
sessions/_reports/remote-speaker-attribution-error-decomposition-v1/
  input_manifest.public.json
  remote_speaker_attribution_error_decomposition_report.json
  remote_speaker_attribution_error_decomposition_report.md
  replay_report.json
  private/
    input_manifest.json
    word_error_decomposition.jsonl
    boundary_error_decomposition.jsonl
```

Schemas:

- `murmurmark.remote_speaker_attribution_error_decomposition_input/v1`;
- `murmurmark.remote_speaker_attribution_error_decomposition_public_input/v1`;
- `murmurmark.remote_speaker_attribution_word_error/v1`;
- `murmurmark.remote_speaker_attribution_boundary_error/v1`;
- `murmurmark.remote_speaker_attribution_error_decomposition_report/v1`;
- `murmurmark.remote_speaker_attribution_error_decomposition_replay/v1`.

The public report contains aggregate metrics and pseudonymous synthetic speaker IDs. Exact word text,
renderer voices and local absolute paths stay outside public outputs.

## Decision

The policy is fixed before analysis. It computes transparent gains in known-speaker recall and
boundary recall for the boundary and identity oracles. Special-class gain is measured separately.
Exactly one outcome is emitted:

- `ADVANCE_DEDICATED_SEGMENTATION`;
- `ADVANCE_STRONGER_SPEAKER_IDENTITY`;
- `ADVANCE_OVERLAP_OPEN_SET_MODEL`;
- `CURRENT_LOCAL_ATTRIBUTION_LIMIT`.

The decision is a routing result, not a promotion. Existing transcripts, Coverage v3, hard decisions,
raw audio, Echo Guard and ASR outputs remain byte-exact.

The completed run measured speaker-identity gain `0.351382`, segmentation gain `0.063882` and
overlap/open-set gain `0.036364` over 393 words and 64 evaluated boundaries. The tracked portable
lineage is `docs/testing/remote-speaker-attribution-error-decomposition-v1-manifest.json`.

## Determinism And Failure

`freeze` refuses to replace a different existing input manifest. `analyze` fails if any frozen input
hash changes. `replay` recomputes every public metric and private decomposition row and requires
byte-identical canonical output. Missing or inconsistent inputs fail closed without changing
production artifacts.
