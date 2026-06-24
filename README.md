# MurmurMark

MurmurMark is a local-first macOS meeting capture and notes pipeline.

It records the user's microphone and the selected meeting application's audio into separate local tracks, without virtual audio devices, without changing the meeting app's audio routing, and without uploading raw audio by default. The recording package is then processed into a speaker-aware transcript, evidence-backed meeting notes, and documentation updates under an explicit privacy policy.

The repository now contains a first minimal Swift CLI plus local transcript and extractive notes scripts. Its current scope is capture, Echo Guard preprocessing, simple `Me`/`Colleagues` transcription, timeline repair for common mic/remote ordering failures, quality verdicts, and evidence-backed extractive notes. Full diarization and generative synthesis remain documented future work.

## Current CLI

### End-to-End From an Existing Recording

Use this when the session directory already exists and raw recording is complete.

```bash
cd murmurmark

SESSION=./sessions/<session>
MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
ASR_PROMPT="examples/domain-packs/backend-platform/whisper-prompt.ru.txt"
ASR_PROMPT_ARGS=()
[[ -f "$ASR_PROMPT" ]] && ASR_PROMPT_ARGS=(--prompt-file "$ASR_PROMPT")

git pull
swift build
.build/debug/murmurmark inspect "$SESSION"
.build/debug/murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
.build/debug/murmurmark inspect "$SESSION" --echo

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  "${ASR_PROMPT_ARGS[@]}" \
  --language ru \
  --force

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  "${ASR_PROMPT_ARGS[@]}" \
  --language ru \
  --skip-export \
  --skip-transcribe \
  --repair-profile shadow_v2

jq '{passed, no_regression_gates, control_texts}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/resolved/repair_comparison.json"

scripts/synthesize-simple-extractive.py "$SESSION" --transcript-profile auto

jq '{verdict, selected_transcript_profile, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.shadow_v2.md"
less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.md"
```

### End-to-End From a New Recording

This is the current practical path from a new local recording to the best available transcript candidate.
The `record` command keeps running until `Ctrl-C`; the following commands continue after the recording stops.

```bash
cd murmurmark

MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
ASR_PROMPT="examples/domain-packs/backend-platform/whisper-prompt.ru.txt"
ASR_PROMPT_ARGS=()
[[ -f "$ASR_PROMPT" ]] && ASR_PROMPT_ARGS=(--prompt-file "$ASR_PROMPT")

git pull
swift build
.build/debug/murmurmark doctor

.build/debug/murmurmark record --target-bundle system

SESSION="$(ls -td sessions/* | head -1)"
echo "SESSION=\"$SESSION\""

.build/debug/murmurmark inspect "$SESSION"
.build/debug/murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
.build/debug/murmurmark inspect "$SESSION" --echo

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  "${ASR_PROMPT_ARGS[@]}" \
  --language ru \
  --force

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  "${ASR_PROMPT_ARGS[@]}" \
  --language ru \
  --skip-export \
  --skip-transcribe \
  --repair-profile shadow_v2

jq '{passed, no_regression_gates, control_texts}' \
  "$SESSION/derived/transcript-simple/whisper-cpp/resolved/repair_comparison.json"

scripts/synthesize-simple-extractive.py "$SESSION" --transcript-profile auto

jq '{verdict, selected_transcript_profile, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

less "$SESSION/derived/transcript-simple/whisper-cpp/resolved/transcript.shadow_v2.md"
less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.md"
```

`transcript.md` is the stable baseline output. `transcript.shadow_v2.md` is the current best candidate when `repair_comparison.json` passes. The shadow profile does not replace the baseline transcript; it writes separate audit and comparison artifacts so changes can be checked before promotion.
`scripts/synthesize-simple-extractive.py` then selects the best allowed dialogue JSON, writes a quality verdict, and creates local extractive notes where every item cites utterance IDs.

### Command Reference

```bash
swift build
swift run murmurmark doctor
swift run murmurmark list-apps
.build/debug/murmurmark record --target-bundle system
.build/debug/murmurmark preprocess ./sessions/<session> --echo diagnostic
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine linear_baseline
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine local_fir
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine local_fir --echo-policy role_safe
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine speexdsp
.build/debug/murmurmark preprocess ./sessions/<session> --echo clean --echo-engine webrtc-apm
.build/debug/murmurmark reconcile-transcript ./sessions/<session>
.build/debug/murmurmark inspect ./sessions/<session>
.build/debug/murmurmark inspect ./sessions/<session> --echo
.build/debug/murmurmark export-audio ./sessions/<session>
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session>
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --prompt-file examples/domain-packs/backend-platform/whisper-prompt.ru.txt
.venv/bin/python scripts/transcribe-simple-whispercpp.py ./sessions/<session> --repair-profile shadow_v2 --skip-export --skip-transcribe
.venv/bin/python scripts/synthesize-simple-extractive.py ./sessions/<session> --transcript-profile auto
.venv/bin/python scripts/echo-guard-delay-lab.py ./sessions/<session>
.venv/bin/python scripts/echo-guard-fir-lab.py ./sessions/<session>
.venv/bin/python scripts/echo-guard-local-subtract-lab.py ./sessions/<session> --start-sec <seconds>
```

Development check:

```bash
scripts/check.sh
```

This builds the CLI, runs SwiftLint and verifies `inspect`/`export-audio` on a synthetic two-track session package.

The normal minimal recorder uses ScreenCaptureKit to capture system/application audio and the microphone into separate local tracks. It does not require Loopback, BlackHole or any other virtual audio device. It writes:

```text
session/
  audio/mic/000001.caf
  audio/remote/000001.caf
  derived/asr/mic.wav
  derived/asr/remote.wav
  events.jsonl
  pipeline_job.json
  session.json
```

Echo Guard M0/M0.5/M1/M2/M2.5/M3 is implemented. The preprocess step materializes ASR/AEC working audio under `derived/preprocess/audio/`, writes `echo_diagnostics.json`, `echo_segments.jsonl` and, for `--echo clean`, a conservative `echo_suppression_report.json`. Cleanup engines currently available are `linear_baseline`, `local_fir`, `speexdsp` and `webrtc-apm`; the external engines build or invoke small local helpers when their toolchains are available.

`local_fir` is the current recommended experimental cleaner for real speaker-bleed sessions. It fits short local FIR echo models from the already separated `remote` and `mic` tracks, writes a listenable `mic_clean_local_fir.wav`, a selected `mic_role_masked_for_asr.wav`, a compact `mic_role_preview.wav` for auditioning retained mic regions, and promotes the role-masked file to `mic_for_asr.wav` only when its quality gate accepts the candidate. It also writes `derived/preprocess/mic_asr_segments/segments_manifest.json` for future chunk-based mic ASR. `reconcile-transcript` marks mic utterances that match delayed remote speech and updates `quality_report.json`. Raw capture stays untouched.

For `local_fir`, `--echo-policy preserve_local` is the default because it avoids deleting local speech in ambiguous remote-active regions. It mutes detected silence, passes local-only speech raw, and passes remote-active ambiguous chunks as mildly cleaned/flagged audio. `--echo-policy role_safe` hard-mutes only high-confidence `remote_only` regions. `--echo-policy strict_silence` mutes all remote-active regions, knowingly sacrificing overlap speech.

`export-audio` uses `derived/preprocess/audio/mic_for_asr.wav` when Echo Guard has created it; otherwise it exports the raw mic capture.

The temporary transcription bridge is `scripts/transcribe-simple-whispercpp.py`. It runs `export-audio`, prepares ASR-only speech-band `mic` audio and normalized `remote` audio, calls local `whisper-cli` on short overlapping windows, creates raw segment and candidate JSON, runs timeline repair plus role reconciliation, then writes `clean_dialogue.json`, `role_decisions.json`, `overlaps.json`, `quality_report.json`, `timeline_repair_report.json`, `transcript.md` and `transcript.simple.json` under `derived/transcript-simple/whisper-cpp/resolved/`. ASR preparation never changes raw capture. See [docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md).

The first synthesis bridge is `scripts/synthesize-simple-extractive.py`. It reads only transcript-derived JSON, chooses the best safe dialogue profile, writes `quality_verdict.json`/`.md`, and creates extractive `notes.md` plus `evidence_notes.json` and `review_items.jsonl` under `derived/synthesis-simple/extractive/`. It does not call an LLM and does not infer facts beyond quoted utterances.

Timeline repair treats `remote` as the authoritative `Colleagues` timeline. If whisper.cpp glues a long `Me` segment across a remote reply, the bridge cuts that mic candidate around guarded remote intervals, keeps only local islands from Echo Guard speaker state, and can run micro-ASR on those short islands. If no local island can be recovered, the misleading long `Me` block is dropped rather than published whole. `source_start`, `source_end`, `timeline_repair_examples.jsonl`, and `role_decisions.json` remain available for audit.

`--repair-profile current` is the default and keeps the current transcript path stable. `--repair-profile shadow_v2` writes a separate candidate transcript, quality report, timeline-repair report, comparison gates, and audit examples without replacing `transcript.md`. Shadow repair seeds every short local-island micro-ASR choice with the current result, then tests wider windows, alternate mic sources, leading silence, narrow boundary-prefix fixes such as `адно` -> `Ладно`, and a guarded start-of-call repair for short opening turns such as `Привет`, `Меня слышно?`, `Привет, да`. `repair_comparison.json` must pass no-regression gates before any shadow behavior is promoted.

Before recording, macOS must allow the terminal or Codex app to use Screen & System Audio Recording and Microphone. `doctor` prints the current permission state and the basic toolchain checks.

For the first real run, use [docs/runbooks/first-recording.md](docs/runbooks/first-recording.md).

For real work, run `swift build` once and then use `.build/debug/murmurmark record --target-bundle system`. Without `--duration`, recording runs until `Ctrl-C`. Without `--out`, every recording gets a unique directory under `./sessions`, so previous sessions are not overwritten.

Before trying cleanup on a real speaker-bleed session, run the [Echo Guard delay lab](docs/runbooks/echo-guard-lab.md). If delay confidence is unstable, do not trust cleanup output yet; use transcript-level suppression and keep raw mic for ASR fallback.

## Product Shape

```text
MurmurMark Capture
  -> mic.caf + remote.caf + session.json + events.jsonl

MurmurMark Transcribe
  -> transcript.rich.json + speaker_map.json + quality_report.json

MurmurMark Evidence
  -> utterance citations + corrections + review flags

MurmurMark Synthesis
  -> extractive notes, potential decisions/actions/risks, docs patches later

MurmurMark Policy
  -> privacy modes, retention, redaction, provider approvals
```

## v1 Decisions

- Intended remote audio capture: Core Audio Process Tap for the meeting app.
- Current minimal remote audio capture: ScreenCaptureKit audio output.
- Microphone capture: AUHAL/Core Audio for a selected input device.
- Raw format: two independent CAF streams, not one stereo L/R file.
- No virtual audio devices by default.
- No cloud upload during capture.
- Capture and transcription are separate stages connected by a file manifest.
- Long meetings are handled as a global timeline with overlapping ASR windows.
- Notes must be evidence-backed: decisions, actions and risks need utterance IDs.

## Documentation Map

- [docs/00-index.md](docs/00-index.md): reading order.
- [docs/product/vision.md](docs/product/vision.md): product intent and non-goals.
- [docs/product/prd-v1.md](docs/product/prd-v1.md): v1 requirements.
- [docs/architecture/system-overview.md](docs/architecture/system-overview.md): whole-system design.
- [docs/architecture/capture.md](docs/architecture/capture.md): macOS recorder design.
- [docs/architecture/transcription.md](docs/architecture/transcription.md): ASR, diarization and correction.
- [docs/architecture/echo-suppression.md](docs/architecture/echo-suppression.md): Echo Guard diagnostics and derived echo suppression.
- [docs/architecture/evidence-synthesis.md](docs/architecture/evidence-synthesis.md): notes and docs integration.
- [docs/contracts/session-package.md](docs/contracts/session-package.md): local session schema.
- [docs/contracts/transcript-and-evidence.md](docs/contracts/transcript-and-evidence.md): transcript, quality and evidence schemas.
- [docs/security/privacy-and-threat-model.md](docs/security/privacy-and-threat-model.md): privacy defaults and threat model.
- [docs/runbooks/first-recording.md](docs/runbooks/first-recording.md): first local recording check.
- [docs/runbooks/echo-guard-lab.md](docs/runbooks/echo-guard-lab.md): delay and candidate-clip investigation before echo cleanup.
- [docs/runbooks/transcribe-simple-whispercpp.md](docs/runbooks/transcribe-simple-whispercpp.md): temporary local transcription with whisper.cpp.
- [docs/decisions/tradeoffs.md](docs/decisions/tradeoffs.md): accepted and rejected tradeoffs.
- [docs/rfc/0001-v1-scope.md](docs/rfc/0001-v1-scope.md): implementation RFC for v1.
- [docs/adr/](docs/adr/): architecture decision records.
- [docs/adr/0010-use-preserve-local-fir-for-current-echo-guard.md](docs/adr/0010-use-preserve-local-fir-for-current-echo-guard.md): current Echo Guard cleanup default.
- [docs/backlog/mic-remote-bleed-reduction.md](docs/backlog/mic-remote-bleed-reduction.md): active Echo Guard work for reducing remote audio bleed in the mic track.

## Implementation Posture

The intended repository shape is still a monorepo, but the first implemented piece is deliberately narrow:

```text
apps/macos/                 native menubar app, later
Sources/MurmurMarkCLI/      current minimal recorder CLI
Sources/MurmurMarkCaptureCore/
pipeline/                   Python-heavy ASR and synthesis code, later
docs/                       current focus
examples/                   domain packs and sample policies
```

The ScreenCaptureKit backend is a working bridge for the first local smoke tests. The documented Core Audio Process Tap backend remains the target design for more precise per-application capture.
