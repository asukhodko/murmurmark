# Meeting Cheat Sheet

Updated: 2026-08-06

## Update A Packaged Release

```bash
shasum -a 256 -c murmurmark-<version>-<commit>.tar.gz.sha256
tar -xzf murmurmark-<version>-<commit>.tar.gz
cd murmurmark-<version>-<commit>
./install.sh --python /absolute/path/to/python3
export PATH="$HOME/.local/bin:$PATH"

cd "$HOME/murmurmark-workspace"
export MURMURMARK_PYTHON=/absolute/path/to/python3
murmurmark doctor --strict
murmurmark self-test
```

## Update A Developer Checkout

```bash
cd murmurmark
git pull
source .venv/bin/activate
scripts/install-local.sh
export PATH="$HOME/.local/bin:$PATH"

murmurmark config init
murmurmark doctor --strict
murmurmark self-test
```

## Ordinary Meeting

```bash
murmurmark meeting --target-bundle system
```

Stop recording with `Ctrl-C`. Keep the terminal open while processing continues. The final block
prints the selected transcript and whether manual review remains.

The first authoritative result does not wait for optional Neural Echo enrichment. If its budget is
exhausted, the summary records `deferred_work` and still returns the baseline result. A terminal
`human_decision_required` line includes the exact bounded item count and seconds; inspect
`derived/meeting-lifecycle/report.md` rather than rerunning `status` in a loop.

Run `meeting` as the foreground command by itself. Do not paste the status/transcript command block
into that terminal while capture or processing is still active: the shell buffers that input and may
execute it later against an incomplete or different lifecycle. Wait until `meeting` returns, then run
read-only accessors if they are still useful.

The setup checks above are repeated after an update or environment change, not before every
meeting.

After capture is finalized, a new meeting may be started in another terminal even while this one is
still being processed. MurmurMark releases ScreenCaptureKit before post-processing. Processing
sessions may coexist; any second simultaneous active recording is forbidden.

If `meeting` exits with an error before it prints a final result, stop there. Do not run a pasted
tail of `status`, `outcome`, `notes` and `transcript` commands against that path: a failed startup
does not have `session.json`. MurmurMark now bounds ScreenCaptureKit startup and releases the
recording lock on this path.

## Meeting With Live Shadow

```bash
murmurmark meeting \
  --target-bundle system \
  --experiment live-shadow-v1
```

The live draft is advisory. Batch processing after `Ctrl-C` remains authoritative.

## Resume Processing

If processing was stopped with a second `Ctrl-C`, use the exact command printed by MurmurMark:

```bash
murmurmark meeting --resume sessions/<session-id>
```

Resume does not start another recording.

## Open The Result

The final lifecycle summary already prints the paths. The low-level accessors remain available:

```bash
SESSION="sessions/<session-id>"

murmurmark status "$SESSION"
murmurmark outcome "$SESSION"
murmurmark transcript "$SESSION"
murmurmark notes "$SESSION"
cat "$(murmurmark transcript "$SESSION" --path-only)"
```

## Low-Level Recovery

Use this only for sessions recorded before `meeting` or for stage diagnostics:

```bash
SESSION="sessions/<session-id>"

murmurmark inspect "$SESSION"
murmurmark process "$SESSION"
murmurmark enrich "$SESSION"
murmurmark next "$SESSION"
murmurmark finish "$SESSION"
```

Do not add `--force-asr`, `--allow-partial` or `--full` to the ordinary path.

For corpus-level lifecycle diagnostics:

```bash
murmurmark corpus lifecycle all --require-frozen-inputs --require-passing-gates
```
