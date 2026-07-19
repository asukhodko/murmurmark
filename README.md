# MurmurMark

Local-first meeting transcription for sensitive work.

MurmurMark records separate local microphone and remote system-audio tracks, processes them locally
and produces a transcript, quality verdict, evidence-backed notes, review plan, export bundle and
retention plan.

The product is CLI-first. Batch processing is authoritative. Live preview is an optional shadow
that cannot replace or weaken the durable recording.

## Mission

MurmurMark turns sensitive working conversations into reliable local transcripts and
evidence-backed meeting memory without sending raw meeting audio to a cloud recorder.

The user should be able to record a meeting, start processing and receive an honest result without
supervising internal stages. Uncertain regions remain explicit review items. Notes and exports point
back to transcript or audit evidence.

## Reliability Contract

For a supported macOS setup, MurmurMark produces one of these outcomes:

- `ready_for_notes`: transcript and notes are usable for ordinary follow-up;
- `review_first`: the result is useful, but explicit review is required before guarded export;
- `blocked`: capture or transcript evidence is insufficient for safe use.

Raw `audio/mic/*.caf` and `audio/remote/*.caf` files are immutable processing inputs. Derived
profiles are isolated and selected only after no-regression gates pass.

## Install

Prerequisites:

- macOS with Screen and System Audio Recording and microphone permissions;
- Swift toolchain;
- `ffmpeg`, `whisper-cpp` and `jq`;
- project Python virtual environment and local whisper.cpp model.

```bash
cd murmurmark
source .venv/bin/activate

scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"

murmurmark config init
murmurmark doctor --strict
murmurmark self-test
```

`scripts/install-local.sh` builds the release CLI and installs its wrapper into
`$HOME/.local/bin`. During development, `swift build` also provides `.build/debug/murmurmark`.

The optional stronger audio judge uses a local faster-whisper model. Its absence must not block the
normal pipeline; `murmurmark doctor` reports it as an optional warning.

## Stable Meeting Workflow

Use one explicit session path for every command. Avoid `latest` when another terminal may start a
new session.

```bash
cd murmurmark
source .venv/bin/activate
export PATH="$HOME/.local/bin:$PATH"

murmurmark doctor --strict

SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)"
echo "SESSION=\"$SESSION\""

murmurmark record --out "$SESSION" --target-bundle system
# Stop with Ctrl-C.

murmurmark inspect "$SESSION"
murmurmark process "$SESSION"
murmurmark next "$SESSION"
murmurmark status "$SESSION"
murmurmark outcome "$SESSION"
murmurmark transcript "$SESSION"
murmurmark notes "$SESSION"
murmurmark finish "$SESSION"
```

Recording without `--duration` continues until `Ctrl-C`. Run only one `murmurmark record` process
at a time. If capture is partial, sparse or silent, processing blocks instead of publishing an empty
successful transcript.

`process` returns after the authoritative transcript, verdict and next action are published.
Optional heavier diagnostics run separately:

```bash
murmurmark enrich "$SESSION"
```

An interrupted processing run is resumed with the same command and session path:

```bash
murmurmark process "$SESSION"
```

Use `--force-asr` only when intentionally invalidating the expensive ASR cache.

## Live Shadow Workflow

Live Evidence uses the same durable capture and a best-effort committed-PCM sidecar:

```text
capture -> durable raw writer -> stable session
                    |
                    +-> bounded committed PCM queue -> live draft
```

Recording terminal:

```bash
SESSION="sessions/$(date +%Y-%m-%d_%H-%M-%S)-live"
echo "SESSION=\"$SESSION\""

murmurmark record \
  --out "$SESSION" \
  --target-bundle system \
  --experiment live-shadow-v1

murmurmark process "$SESSION"
murmurmark experiment status "$SESSION"
murmurmark experiment report "$SESSION"
murmurmark experiment compare "$SESSION" --experiment live-shadow-v1
murmurmark status "$SESSION"
murmurmark transcript "$SESSION"
```

Second terminal while recording:

```bash
cd murmurmark
export PATH="$HOME/.local/bin:$PATH"

SESSION="sessions/<value-printed-by-the-recording-terminal>"
murmurmark live watch "$SESSION"
```

The preview is advisory. Sidecar timeout, lag or backpressure may make it partial, but must not
damage raw capture. The old `--live-pipeline` transport is unsafe and lab-only.

## Review And Finish

Follow the exact command printed by `murmurmark next`. Common commands are:

```bash
murmurmark review next "$SESSION"
murmurmark review suggested "$SESSION"
murmurmark review suggested apply "$SESSION"
murmurmark status "$SESSION"
murmurmark finish "$SESSION"
```

Suggested review closes only rows supported by current local evidence. Unresolved rows remain
explicit. `finish` attempts guarded local export and writes retention recommendations; it never
deletes raw audio.

```bash
murmurmark finish "$SESSION" --format markdown
murmurmark finish "$SESSION" --format obsidian
murmurmark retention plan "$SESSION"
```

Raw deletion requires a compatible policy, successful export and an explicit confirmation command.

## Important Artifacts

```text
sessions/<session-id>/
  audio/mic/000001.caf
  audio/remote/000001.caf
  session.json
  events.jsonl
  derived/
    outcome/
    preprocess/
    transcript-simple/whisper-cpp/
    synthesis-simple/extractive/
    readiness/
    audit/
    retention/
```

Prefer CLI accessors over guessing profile-specific filenames:

```bash
murmurmark transcript "$SESSION"
murmurmark transcript "$SESSION" --path-only
murmurmark notes "$SESSION" --kind verdict
murmurmark notes "$SESSION"
murmurmark open "$SESSION" --kind transcript --command-only
```

## Current Development Direction

The stable batch CLI already supports durable capture, resumable processing, evidence-backed review,
guarded export and retention planning. The promoted authoritative profile is
`residual_local_recall_v1`. It classified all `13` local-recall rows / `48.073s` and safely closed
`9` rows / `26.953s` without inventing or inserting speech. Four ambiguous rows remain explicit.

The current goal is **Residual Chronology Closure v1** for `14` isolated order rows / `62.690s`.
The dependent critical path is:

```text
Residual Chronology Closure
-> Operational Rebaseline
-> Echo Suppression Promotion
-> Evidence Notes and Export
-> Release-quality CLI
```

Remote diarization, heavy local validators, LLM synthesis and UI are parallel or parked work. Live
promotion remains blocked; Live Shadow is maintained as advisory evidence only.

See the [current goal](docs/project/current-goal.md), [readable roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
and [OpsKarta v3 plan](docs/roadmap/murmurmark-cli-roadmap.plan.yaml).

## Scope And Limitations

- Current roles are `Me` and aggregate `Colleagues`; individual remote-speaker diarization is future
  work.
- Echo Guard reduces remote leakage but cannot yet guarantee that remote speech is absent from every
  `Me` candidate.
- Batch transcript is authoritative; live output is not used for export or retention decisions.
- No cloud ASR or cloud raw-audio upload is required by the normal workflow.
- A future UI must reuse CLI contracts and is not required for a useful product.

## Documentation

- [Documentation index](docs/00-index.md)
- [Mission and vision](docs/product/vision.md)
- [Product requirements](docs/product/prd-v1.md)
- [Current goal](docs/project/current-goal.md)
- [Reliable transcription route](docs/project/reliable-transcription-route.md)
- [Readable roadmap](docs/roadmap/murmurmark-cli-roadmap.md)
- [OpsKarta v3 roadmap](docs/roadmap/murmurmark-cli-roadmap.plan.yaml)
- [First recording runbook](docs/runbooks/first-recording.md)
- [Transcription and review runbook](docs/runbooks/transcribe-simple-whispercpp.md)
- [Transcript and evidence contracts](docs/contracts/transcript-and-evidence.md)
- [Historical planning and development snapshots](docs/history/README.md)

## Development Checks

```bash
swift build
.venv/bin/python -m py_compile scripts/*.py
scripts/check-planning-consistency.py
scripts/check-open-source-readiness.sh
scripts/check.sh
```

The active roadmap uses OpsKarta v3. Validate and render it with the adjacent OpsKarta repository:

```bash
PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  validate docs/roadmap/murmurmark-cli-roadmap.plan.yaml

PYTHONPATH=../opskarta .venv/bin/python -m specs.v3.tools.cli \
  render executive docs/roadmap/murmurmark-cli-roadmap.plan.yaml --view exec-top
```
