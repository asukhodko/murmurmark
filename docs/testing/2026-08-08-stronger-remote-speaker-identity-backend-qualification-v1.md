# Stronger Remote Speaker Identity Backend Qualification v1 Result

Date: 2026-08-08

Decision: `PROMOTE_LAB_IDENTITY_CANDIDATE`.

Selected candidate: `speechbrain_ecapa_voxceleb_candidate`.

## Frozen Design

- control: the existing WavLM XVector backend;
- candidate: SpeechBrain ECAPA-TDNN trained for VoxCeleb speaker verification;
- development: Truth Lab v1, once-opened hard-v2 and once-opened hard-v3;
- hard-v4 frozen before development selection;
- hard-v4 corpus SHA-256:
  `78560dc89172af3d7c5aa46568dd38396e09a6d24404e7cfdcc92b4837b330cc`;
- four new scenarios, four enrolled and two open-set voices;
- 154 exact words, 26 total boundaries, 23 evaluable boundaries and six mixed words;
- 72 enrollment words / `49.695688s`;
- one selected candidate and one hard-v4 opening.

Voices, scripts, vocabulary and enrollment are disjoint from all development corpora. The policy
freezes the exact ECAPA revision, Apache-2.0 license, runtime versions and model file hashes.

## Development Selection

| Backend | Similarity | Margin | B-cubed F1 | Pairwise precision | Known recall | Boundary recall | Open-set false |
|---|---:|---:|---:|---:|---:|---:|---:|
| WavLM control | 0.80 | 0.30 | 0.203651 | 1.000000 | 0.155914 | 0.054054 | 0 |
| ECAPA candidate | 0.50 | 0.30 | 0.971391 | 1.000000 | 0.970430 | 0.729730 | 0 |

The fixed selection order chose ECAPA. No hard-v4 metric participated in selection.

## One-Shot Hard-v4 Result

| Backend | B-cubed F1 | Pairwise precision | Known recall | Boundary recall | Open-set false | Mixed safe | Words |
|---|---:|---:|---:|---:|---:|---:|---:|
| WavLM control | 0.058394 | 1.000000 | 0.000000 | 0/23 | 0 | 6/6 | 154/154 |
| ECAPA candidate | 0.948042 | 1.000000 | 0.947368 | 13/23 | 0 | 6/6 | 154/154 |

Every frozen promotion gate passed. ECAPA recovered the identity axis without sacrificing exact
words, open-set safety, mixed abstention or pairwise precision. Boundary recall remains imperfect,
but it is a strict no-regression improvement over the control and no longer the dominant synthetic
failure.

## Reproducibility And Safety

- qualification report SHA-256:
  `002280f4e26fe4b253f74d26c1283a59e309fda09660825169d2ccbae47d54d3`;
- replay is byte-identical;
- hard-v4 opening ledger remains at `open_count: 1`;
- public artifacts contain no scripts, renderer voices, audio or absolute paths;
- Transcript Perfection verifies 19/19 frozen sources;
- selected transcripts, Coverage v3, raw CAF, primary ASR and Echo Guard are unchanged.

## Interpretation

This is strong synthetic evidence for the ECAPA model family, not proof of real-meeting quality.
Synthetic voices, clean enrollment and exact event boundaries do not reproduce every microphone,
codec, speaker and overlap condition. The candidate may proceed only to fail-open shadow evaluation
on frozen real sessions with reviewed session-local evidence. Production promotion requires a
separate decision.
