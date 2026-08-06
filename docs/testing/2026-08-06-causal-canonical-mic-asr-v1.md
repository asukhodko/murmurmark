# Causal Canonical Mic ASR v1

Date: 2026-08-06

Decision: `DO_NOT_PROMOTE`

## Question

Can MurmurMark prepare the exact selected microphone audio and finish authoritative whisper.cpp
work before recording stops, while preserving the current Echo Guard and Speaker-Preserving Neural
Echo quality gates?

The acceptance gate required byte-identical canonical `60s` hard windows with `5s` overlap and at
least `50%` lower post-stop ASR wall time on three fresh real sessions. Approximate waveform or text
matches were not eligible.

## Result

The frozen corpus contains three real sessions recorded on 2026-08-05:

| Session | Selected mic profile | Exact windows | Prefix probes 5/30/120s |
|---|---|---:|---|
| `2026-08-05_11-15-06` | `local_fir_role_masked` | `0/46` | `no/no/no` |
| `2026-08-05_14-16-08` | `speaker_preserving_neural_echo_v2` | `0/51` | `no/no/no` |
| `2026-08-05_17-00-29` | `speaker_preserving_neural_echo_v2` | `0/50` | `no/no/no` |

Corpus totals:

- exact windows: `0/147`;
- exact hard audio: `0/8743.1315s`;
- modeled post-stop ASR wall-time reduction: `0%`;
- recording-time eligible mic proofs: `0`;
- frozen input matches: `3/3`;
- raw capture integrity: passed.

The minimum exact future context is therefore `session_end` in the current production topology.
This is a causal boundary, not an implementation-performance problem.

## Why The Boundary Is Session-End

Committed raw microphone PCM, resampling and the speech-band filter can be streamed or finalized
with a bounded tail. The selected post-Echo microphone cannot:

- local FIR activity floors use the whole-session p20 distribution over `8s/2s` windows;
- canonical delay is the median over all reliable windows;
- a chunk may choose a remote-only fit window that occurs arbitrarily later;
- peak scaling, quality medians and candidate acceptance are global;
- acoustic-mode and Echo policy selection use the complete session;
- Speaker-Preserving Neural Echo selection requires complete candidate audio, ASR and shadow gates.

The bounded-prefix probe replayed the first local-FIR window with `5s`, `30s` and `120s` of future
context. Every candidate differed byte-for-byte from the final canonical local-FIR window on all
three sessions. Precomputing more whisper chunks over those candidates would only create cache
entries that strict batch validation must reject.

## Implementation

The isolated audit tool writes:

```text
derived/experiments/live-shadow-v1/authoritative-mic-asr/
  lineage.json
  windows.jsonl
  report.json
  report.md
  chunks/<index>/mic.*
```

Schemas:

- `murmurmark.causal_mic_lineage/v1`;
- `murmurmark.causal_canonical_mic_window/v1`;
- `murmurmark.causal_canonical_mic_asr_report/v1`;
- exact accepted proof: `murmurmark.authoritative_live_asr_chunk/v1`.

The tool runs with the background resource policy and may decode only byte-identical windows. It
supports deterministic rerun, proof reuse, corruption repair and interruption/resume. Missing raw
or canonical audio, missing model, mismatched geometry, stale proof or unsupported selected profile
remain fail-open batch fallbacks.

The corpus report freezes raw CAF, canonical audio, Echo reports, selected transcript, notes,
verdict, handoff and readiness fingerprints in
`docs/testing/causal-canonical-mic-asr-v1-manifest.json`.

## Commands

```bash
for SESSION in \
  sessions/2026-08-05_11-15-06 \
  sessions/2026-08-05_14-16-08 \
  sessions/2026-08-05_17-00-29
do
  scripts/causal-canonical-mic-asr.py "$SESSION" --prefix-probe
done

scripts/report-causal-canonical-mic-asr-corpus.py \
  sessions/2026-08-05_11-15-06 \
  sessions/2026-08-05_14-16-08 \
  sessions/2026-08-05_17-00-29
```

## Product Decision

Do not connect this producer to ordinary recording or batch cache materialization. Raw CAF,
Speaker-Preserving Neural Echo v2, the selected transcript, Evidence Handoff v2 and guarded export
remain unchanged.

A future latency experiment would first need a separately quality-gated causal Echo Guard whose
past state is immutable. Replacing the current whole-session estimator solely to gain latency is
not justified by this goal and is not on the immediate product path.

The next roadmap stage is Remote Speaker Evidence Map v1, which can operate on the already
authoritative remote track without weakening the proven transcription path.
