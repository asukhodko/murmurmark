# Simple whisper.cpp Transcription Runbook

Use this temporary path while the full MurmurMark transcription pipeline is not implemented.

The goal is a useful local transcript and a first extractive notes package, not final diarization or polished generative notes.

## Preconditions

Install `whisper.cpp`:

```bash
brew install whisper-cpp
```

Download at least one multilingual GGML model:

```text
~/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin
```

Use the same `large-v3-q5_0` model for normal runs and debugging. This keeps model choice out of
the list of possible causes when comparing results.

## Recommended Flow

Start from a completed session. If the call was recorded through speakers, run Echo Guard first:

```bash
SESSION=./sessions/<session>

.build/debug/murmurmark preprocess "$SESSION" --echo clean --echo-engine local_fir
```

Run simple transcription:

```bash
MODEL="$HOME/.local/share/murmurmark/models/whisper.cpp/ggml-large-v3-q5_0.bin"
ASR_PROMPT="examples/domain-packs/backend-platform/whisper-prompt.ru.txt"

scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru
```

The script does three things:

1. Runs `murmurmark export-audio`.
2. Runs `whisper-cli` for `derived/asr/mic.wav` and `derived/asr/remote.wav`.
3. Builds raw segments, candidate utterances, role decisions, clean dialogue, and Markdown.

By default the bridge runs `whisper-cli` with `--max-context 0`, short ASR windows, and a
small overlap between windows. This avoids feeding a whole meeting into Whisper as one long file,
which has produced worse timestamps, missing short replies, and context drift in real MurmurMark
sessions.

Default ASR settings:

```text
--asr-mode windowed
--asr-window-sec 60
--asr-overlap-sec 5
--mic-audio-prep speech
--remote-audio-prep loudnorm
```

`remote` is normalized only for ASR input. Raw capture and `derived/asr/remote.wav` are not changed.
`mic` uses only speech-band filtering by default: high-pass, low-pass, and a safety limiter. Full-file
mic loudnorm is intentionally not enabled because it can amplify residual remote bleed in the cleaned
mic track.

For backend/platform calls, pass the compact domain prompt file:

```bash
ASR_PROMPT="examples/domain-packs/backend-platform/whisper-prompt.ru.txt"
```

The prompt is derived from the local domain pack and terms observed in current MurmurMark sessions:
Kubernetes, staging, throttling/logging vocabulary, internal names, people names, and recurring
work/health terms. It is intentionally short because `whisper.cpp` uses it as ASR context, not as a
post-processing dictionary.

To reproduce the old whole-track mode:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru \
  --asr-mode whole \
  --remote-audio-prep none
```

Raw Whisper results are cached in `raw/mic.json` and `raw/remote.json`. The cache is reused only
when the model, language, prompt, duration limit, `--max-context`, ASR window settings, and audio
preparation settings match the current run.
Use `--force` to regenerate raw Whisper output anyway. Changing `--prompt-file` also changes the
raw cache metadata, so the next non-skipped run will regenerate raw ASR output.

To test the next repair logic without changing the main transcript, run the shadow profile after the
normal transcript exists:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru \
  --skip-export \
  --skip-transcribe \
  --repair-profile shadow_v2
```

`current` remains the default profile. The shadow profile writes separate `*.shadow_v2.*` artifacts
and `repair_comparison.json`; `transcript.md` remains the baseline. The core raw/candidate/role
JSON files are also written with the same suffix so the shadow result is auditable without mixing it
with the baseline artifacts.

Build the local extractive synthesis package:

```bash
scripts/synthesize-simple-extractive.py "$SESSION" --transcript-profile auto

jq '{verdict, selected_transcript_profile, risk_items: (.risk_items | length)}' \
  "$SESSION/derived/synthesis-simple/extractive/quality_verdict.json"

less "$SESSION/derived/synthesis-simple/extractive/quality_verdict.md"
less "$SESSION/derived/synthesis-simple/extractive/notes.md"
```

## Outputs

```text
derived/
  asr/
    mic.wav
    remote.wav

  transcript-simple/
    whisper-cpp/
      raw/
        chunks/
        mic.txt
        mic.json
        mic.vtt
        remote.txt
        remote.json
        remote.vtt
      prepared-audio/
        mic_speech.wav
        remote_loudnorm.wav
      resolved/
        raw_segments.json
        candidate_utterances.json
        role_decisions.json
        clean_dialogue.json
        overlaps.json
        quality_report.json
        timeline_repair_report.json
        timeline_repair_examples.jsonl
        timeline_audit_examples.jsonl
        clean_dialogue.shadow_v2.json
        quality_report.shadow_v2.json
        timeline_repair_report.shadow_v2.json
        timeline_repair_examples.shadow_v2.jsonl
        timeline_audit_examples.shadow_v2.jsonl
        opening_repair_report.shadow_v2.json
        opening_candidates.shadow_v2.jsonl
        opening_micro_asr_runs.shadow_v2.jsonl
        opening_patch.shadow_v2.json
        corrections.shadow_v2.jsonl
        transcribe_simple_report.shadow_v2.json
        transcript.shadow_v2.md
        repair_comparison.json
        transcript.simple.json
        transcript.md
        corrections.jsonl
        transcribe_simple_report.json
      timeline-audit/
        current/
        shadow_v2/

  synthesis-simple/
    extractive/
      synthesis_manifest.json
      quality_verdict.json
      quality_verdict.md
      notes.md
      evidence_notes.json
      review_items.jsonl
```

Initial role assignment is deliberately simple:

```text
mic.wav    -> Me
remote.wav -> Colleagues
```

No remote diarization is attempted.

The bridge then runs a small reconciliation layer:

```text
raw_segments.json
  -> candidate_utterances.json
  -> timeline repair
  -> role_decisions.json
  -> clean_dialogue.json
  -> transcript.md
```

For `mic` candidates it combines Echo Guard speaker-state features, time overlap with `remote`
candidates, normalized text similarity, and a small domain-term normalizer. Remote candidates remain
authoritative for `Colleagues`.

Timeline repair runs before role decisions. If whisper.cpp glues a long `Me` segment across a
remote reply, the bridge treats the remote intervals as authoritative, cuts the mic candidate around
guarded remote spans, and keeps only local islands from `speaker_state.jsonl`. Short local islands
are re-recognized from `mic_clean_local_fir.wav` with micro ASR; longer islands may use token
timestamps as fallback. If no reliable local island remains, the original long mic candidate is
dropped instead of being published as one misleading `Me` block.

Repair actions are explicit in `role_decisions.json`:

```text
keep
drop
split
micro_reasr
keep_needs_review
```

`timeline_repair_report.json` contains aggregate counters. `timeline_repair_examples.jsonl` records
the parent candidate, matched remote intervals, local islands, and children for every repaired or
dropped crossing.

`shadow_v2` keeps the same baseline transcript intact, then runs a more expensive micro-ASR pass for
short local islands. It tries `mic_clean_local_fir.wav`, `mic_raw_for_asr.wav`, and
`mic_role_masked_for_asr.wav` when they exist, adds a short leading silence to temporary micro-ASR
clips, and tries normal/wide recognition windows. The already accepted `current` micro-ASR result is
used as the baseline candidate: a new decode can replace it only when it wins by score and does not
drop the beginning of the baseline phrase. This keeps the current transcript stable while testing
more aggressive recovery.

`shadow_v2` also has a deliberately narrow boundary-prefix repair for short local utterances. The
first supported case is `адно` -> `Ладно` near the start of an utterance, only when the local score is
high, remote similarity is low, and nearby words support the correction. Such edits are written to
`corrections.shadow_v2.jsonl` and to `timeline_audit_examples.shadow_v2.jsonl` as
`boundary_repair_candidate`; they are not applied to the default `transcript.md`.

`shadow_v2` also runs a guarded start-of-call repair when the first remote segment starts too early
or the first local-only mic island is missing from the dialogue. It uses short micro-ASR windows over
remote and mic sources to recover only a small lexicon of opening turns such as `Привет`,
`Меня слышно?`, `Привет, да`, then retimes the first long remote content segment after those turns.
The patch is applied only if `opening_gate_passed` is true; otherwise the proposal remains in
`opening_*shadow_v2*` audit files and the shadow transcript stays unchanged.

`repair_comparison.json` compares current and shadow quality metrics and applies no-regression
gates. Session-specific golden checks should live in private fixtures or local configuration, not in
the open-source defaults. A failing gate means the shadow transcript is useful for review but should
not become the default yet.

The bridge also uses token-level confidence from whisper.cpp full JSON:

- short, low-confidence remote segments are dropped as probable ASR noise;
- remote display timestamps use the first confident speech token when Whisper's broad segment offset
  starts too early;
- long mic segments are split on large internal token gaps before role reconciliation, because
  whisper.cpp can glue separate local remarks into one segment across a pause or remote reply;
- timeline repair splits long mic candidates around authoritative remote intervals and can run
  micro-ASR for short local islands;
- a mic segment pinned to the beginning of the file can use Echo Guard's local-support start as the
  display timestamp;
- if a low-confidence remote candidate has matching mic candidates that were dropped as remote
  leakage, the remote utterance may use that mic duplicate text while keeping the `Colleagues` role.
  In JSON this is marked as `quality.text_source = matched_mic_echo_duplicate`.

To debug raw mic recognition without this reconciliation filter:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --skip-export \
  --skip-transcribe \
  --mic-policy keep_all
```

Dropped raw segments and dropped candidates are written to `resolved/corrections.jsonl`. Aggregate
counts are in `resolved/transcribe_simple_report.json` and `resolved/quality_report.json`.
Cross-role overlaps are written to `resolved/overlaps.json`.
Timeline repair details are in `resolved/timeline_repair_report.json` and
`resolved/timeline_repair_examples.jsonl`.

Risky remaining places are collected in `resolved/timeline_audit_examples.jsonl`: every
`needs_review` utterance and every cross-role overlap longer than two seconds. Each row contains the
utterance data, overlap metadata when present, nearby Echo Guard speaker-state rows, and `afplay`
commands for short mic/remote clips cut under `derived/transcript-simple/whisper-cpp/timeline-audit/`.
Raw `audio/*.caf` files are not touched.

## Extractive Synthesis

`scripts/synthesize-simple-extractive.py` reads only derived transcript JSON. It does not run ASR,
does not call an LLM, and does not read raw audio.

Profile selection:

```text
auto      -> shadow_v2 if repair_comparison.json passes, otherwise current
current   -> baseline clean_dialogue.json
shadow_v2 -> shadow clean_dialogue.shadow_v2.json, marked risky if comparison failed
```

The script writes a quality verdict and a conservative `notes.md`. The notes are extractive:
conversation outline entries cite utterance ranges, and potential decisions/actions/risks quote
utterances with matching lexical markers. The script does not invent owners, deadlines or decisions.

## Reusing Existing Raw Whisper Output

If `raw/mic.json` and `raw/remote.json` already exist, rebuild only the resolved artifacts:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" --skip-export --skip-transcribe
```

## Debugging Short Runs

Limit Whisper to the first 20 seconds:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --duration-ms 20000 \
  --force
```

Regenerate the full transcript with the selected model:

```bash
scripts/transcribe-simple-whispercpp.py "$SESSION" \
  --model "$MODEL" \
  --prompt-file "$ASR_PROMPT" \
  --language ru \
  --force
```

## Known Limitations

- The transcript is a draft.
- `whisper.cpp` may hallucinate repeated short phrases on long silence.
- The script filters obvious subtitle-credit hallucinations, repeated goodbye tails, weak isolated short phrases, and likely remote leakage in mic.
- Reconciliation is rule-based and still conservative; inspect `role_decisions.json` and `overlaps.json` for hard cases.
- Track roles are only first-level roles. `Colleagues` is not diarized into individual people.
- The bridge can pass a compact domain prompt to `whisper.cpp`, but it still does not perform
  glossary-based post-correction or semantic correction.
