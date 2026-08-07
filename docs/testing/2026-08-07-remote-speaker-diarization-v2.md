# Remote Speaker Diarization v2

Status: `PROMOTE`

Remote Speaker Diarization v2 replaces the 50%-coverage utterance-only map with a local
word/frame-level optional read profile. The plain selected transcript, `Me`, recognized text, raw
audio, notes and export inputs remain unchanged.

## Chosen Profile

The promoted profile is `resemblyzer_seeded_frames_v1`:

- Remote Speaker Evidence Map v1 supplies high-precision session-local seed voices;
- authoritative remote audio is analyzed with 6-second windows and 3-second stride;
- frame assignment requires cosine similarity `>= 0.72` and nearest-centroid margin `>= 0.02`;
- uncertain minor-cluster recovery requires similarity `>= 0.82` and margin `>= 0.08`;
- selected words are aligned to existing whisper.cpp token timestamps;
- internal speaker changes split only the optional rich display turns;
- mixed, partial, overlapping and weak regions remain explicit `unknown`.

The existing Resemblyzer model was the shortest reproducible backend that met the target. A local
pyannote/Sortformer model was not installed and was not downloaded implicitly. Those backends remain
future validators rather than unmeasured dependencies.

## Frozen Corpus

The corpus contains two 1x1 controls and four group calls. The tracked inputs are:

- `docs/testing/remote-speaker-diarization-v2-manifest.json`;
- `docs/testing/remote-speaker-diarization-v2-boundaries.json`;
- `policies/remote-speaker-diarization-v2.json`.

| Session | Expected speakers | Published | Coverage | Internal changes |
|---|---:|---:|---:|---:|
| `2026-06-23_14-04-37` | 1 | 1 | 0.938504 | 0 |
| `2026-06-24_15-03-52` | 1 | 1 | 0.968133 | 0 |
| `2026-06-26_11-15-50` | 2..8 | 5 | 0.923255 | 27 |
| `2026-06-26_12-04-04` | 2..8 | 2 | 0.851247 | 5 |
| `2026-07-14_15-01-19-live` | 3..4 | 3 | 0.915044 | 18 |
| `2026-07-20_15-15-26-live` | 2..3 | 2 | 0.911825 | 3 |

Corpus totals:

- remote speech: `9857.660s`;
- attributable remote speech: `9059.887s`;
- attributable remote speech ratio: `0.919071`;
- selected remote words: `18212`;
- attributed words: `16993`;
- anonymous speakers: `14`;
- detected internal-change utterances: `53`;
- frozen internal-boundary cases: `5/5` passed;
- word loss or duplication: `0`;
- text, chronology, timestamp-order and raw-audio gates: passed on `6/6`.

## Private Reference

The same private server transcript used by v1 aligned `123` remote utterances. V2 published a safe
utterance-level compatibility label for `98`; mixed and partial rows deliberately abstained.

- attributed-only adjusted Rand index: `0.937976`;
- attributed-only B-cubed F1: `0.960690`;
- attributed-only pairwise precision: `0.959564`;
- attributed-only pairwise recall: `0.957106`.

The reference contains four remote people, while the profile safely publishes three major voices.
The rare fourth voice lacks enough independent enrollment and remains `unknown`. This is preferable
to merging it into a confident speaker solely to increase coverage.

## CLI

Build or refresh the isolated evidence:

```bash
murmurmark audit remote-diarization "$SESSION" --profile auto
murmurmark transcript "$SESSION" --rich
```

The rich selector checks the promoted policy, frozen corpus hash, implementation hash and current
session fingerprints. Missing or stale evidence falls back to the existing anonymous-v1 or plain
aggregate transcript. Reviewed human display names stay on their explicit v1 review path; v2 never
infers a name from voice.

## Decision

`PROMOTE` applies only to the optional session-local speaker-resolved read surface. It does not
change the plain transcript or make every word attributable. The remaining `8.1%` is useful explicit
uncertainty and becomes an input to Transcript Perfection Corpus v1 rather than a reason to force a
speaker label.
