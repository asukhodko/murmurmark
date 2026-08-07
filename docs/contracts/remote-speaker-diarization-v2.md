# Remote Speaker Diarization v2 Contract

Status: `PROMOTE` for the optional session-local speaker-resolved read surface

Remote Speaker Diarization v2 is an isolated, local read profile over the selected transcript and
the authoritative remote track. It may add session-local anonymous speaker evidence, but it may not
rewrite recognized text, reorder words, change `Me`, mutate the plain transcript or infer identity.

## Inputs

Required inputs are fingerprinted before processing:

- the selected `clean_dialogue.<profile>.json`;
- `derived/asr/remote.wav` or the immutable raw remote CAF fallback;
- `derived/transcript-simple/whisper-cpp/raw/remote.json` with token timestamps;
- passing Remote Speaker Evidence Map v1 output from the same dialogue and remote audio;
- the pinned local Resemblyzer runtime and model.

The v1 map supplies only high-precision seed voices. V2 does not import display names or
cross-session identity. A missing, stale or incompatible input produces the exact aggregate
`Colleagues` fallback.

## Processing

The selected profile is immutable. V2:

1. rebuilds centroids from v1-attributed remote utterances;
2. analyzes authoritative remote audio in bounded overlapping voice windows;
3. aligns selected words to existing whisper.cpp token timestamps, with explicit bounded
   interpolation only where a selected correction has no exact raw token;
4. assigns each word to a session-local anonymous speaker or `unknown`;
5. splits a remote utterance into display turns only at supported word boundaries;
6. keeps overlap, weak evidence and conflicting judges explicit.

The first pinned candidate is `resemblyzer_seeded_frames_v1`: Resemblyzer `0.1.4`, 6-second
analysis windows, 3-second stride, cosine similarity `>= 0.72` and nearest-centroid margin
`>= 0.02`. Existing v1 assignments are retained as seeds. Minor v1 clusters may be recovered only
with the stricter similarity `>= 0.82` and margin `>= 0.08`; otherwise they remain `unknown`.

No model is downloaded implicitly. Heavy diarization backends such as pyannote or Sortformer stay
optional future candidates until a pinned local model, runtime and corpus comparison exist.

## Outputs

All artifacts live under:

```text
derived/audit/remote-speaker-diarization-v2/
  artifact_manifest.json
  frame_attribution.jsonl
  word_attribution.jsonl
  utterance_attribution.jsonl
  speaker_map.json
  transcript.rich.shadow.json
  transcript.rich.shadow.md
  report.json
  report.md
```

Schemas:

- `murmurmark.remote_speaker_diarization_report/v2`;
- `murmurmark.remote_speaker_frame/v2`;
- `murmurmark.remote_speaker_word/v2`;
- `murmurmark.remote_speaker_utterance/v2`;
- `murmurmark.remote_speaker_map/v2`;
- `murmurmark.remote_speaker_rich_transcript/v2`;
- `murmurmark.remote_speaker_diarization_artifact_manifest/v2`.

Every word row contains the source utterance ID, exact selected substring, character span,
monotonic start/end, anonymous speaker or null, decision reason, confidence and supporting frame
IDs. Concatenating the word and separator spans for an utterance must reproduce its selected text
byte for byte.

## Session Gates

A session may publish v2 evidence only when:

- dialogue, remote audio, raw token and v1 fingerprints are current;
- the local speaker backend is available and matches its recorded provenance;
- every selected utterance and every selected word is conserved exactly once and in order;
- all word timestamps are bounded by their source utterance and monotonic;
- every 1x1 control has one dominant anonymous remote speaker;
- conflicting remote overlap remains unknown rather than force-assigned;
- replay with identical inputs is deterministic.

Session publication remains an optional read surface. It never selects the ordinary transcript.

## Corpus Promotion

The frozen six-session corpus decides promotion. `PROMOTE` requires all of the following:

- attributable remote speech ratio `>= 0.85`;
- attributed-only B-cubed F1 `>= 0.90`;
- attributed-only pairwise precision `>= 0.90`;
- expected speaker-count ranges pass on every 1x1 and group control;
- frozen internal-change cases can represent at least two supported speaker runs without losing or
  duplicating words;
- raw remote audio, selected dialogue, `Me`, plain transcript, notes and export inputs are unchanged;
- exact fallback and deterministic replay tests pass.

If any gate fails, the decision is `DO_NOT_PROMOTE`. Audit artifacts remain useful for the next
candidate, while `murmurmark transcript` and the existing v1 rich handoff remain unchanged.

## Safety Boundary

- anonymous IDs exist only inside one session;
- voice never implies a human name;
- no cloud call, external write or implicit model download;
- no capture, Echo Guard, ASR text, local-role, retention or export change;
- no mic multi-speaker inference in this profile;
- uncertainty is preserved instead of hidden to improve coverage.
