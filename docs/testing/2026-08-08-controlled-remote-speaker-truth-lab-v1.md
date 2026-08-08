# Controlled Remote Speaker Truth Lab v1 Result

Date: 2026-08-08

Decision: `DO_NOT_ADVANCE` for the WavLM candidate.

## Frozen Corpus

- 8 disjoint scripted sessions: 3 train, 2 dev and 3 hard;
- 6 local anonymous voices: 4 enrolled and 2 open-set;
- 240 exact words, including 71 hard words;
- hard split: 58 known single-speaker words, 8 mixed words and 5 unseen open-set words;
- short turns, internal changes, silence, rare speaker and overlap are present;
- maximum source-stem reconstruction error: 0 PCM samples;
- exact word/speaker/timestamp truth coverage: 100%;
- replay: deterministic.

Generated speech, renderer identities, scripts, word truth and predictions remain in ignored private
artifacts. The tracked manifest contains only aggregate metrics, portable paths and hashes.

## Held-Out Result

| Track | Decision | B-cubed F1 | Pairwise precision | Boundary recall | Open-set false attribution |
|---|---|---:|---:|---:|---:|
| Coverage v3 seeded-centroid control | `CONTROL_QUALIFIED` | `0.983505` | `1.000000` | `1.000000` | `0` |
| WavLM word-matched open-set candidate | `DO_NOT_ADVANCE` | `0.834325` | `0.950920` | `0.625000` | `2` |

The WavLM thresholds were selected only on dev: similarity `0.85`, margin `0.04`. Hard was not used
for tuning. Event-only, event-plus-word and word-only WavLM enrollment were evaluated during the
goal; word-only was best but still failed every candidate quality gate.

## Interpretation

The synthetic lab is valid and the current Coverage v3 decision shape is a strong control under
clean exact conditions. The independent WavLM candidate is not safe for progression: it loses short
word coverage and can force a previously unseen open-set voice into an enrolled identity.

This result does not validate real-session labels. Coverage v3 remains unchanged, and the blind
real-session residual still needs independent reference evidence.
