# Meeting Cheat Sheet

Updated: 2026-07-23

## Update

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
