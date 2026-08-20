# Capture Continuity Loss Closure v1

Date: 2026-08-20

## Result

`EVIDENCE_BOUND`. Restart hardening is safe to ship. The old fixed application delay is removed;
ScreenCaptureKit still has a short externally controlled outage when its stream is stopped and
recreated. MurmurMark now measures that loss and cannot publish the session as capture-complete.

## Frozen Failure

The frozen production case `2026-08-19_14-12-32` remains read-only:

- 3 `stream_stopped` restarts;
- 3 high-confidence mic+remote gaps;
- `2.268542s` total;
- `0.724646..0.785979s` per gap.

Its raw mic/remote CAF, `session.json`, capture events and original continuity report match the
SHA-256 manifest in `capture-continuity-loss-closure-v1-manifest.json`.
The closure reporter writes `pinned_sessions.json` for the frozen case, controlled restart and soak,
so normal derived compaction cannot silently remove the qualifying evidence.

## Controlled Restart

The final fault-injected capture used
`MURMURMARK_TEST_CAPTURE_RESTART_AFTER_SEC=8` for `25.357s`:

- one restart attempt, one terminal `started` event;
- strict monotonic provenance for request, old-stream disposition, start, callbacks and commits;
- MurmurMark software idle: `2.362ms`;
- ScreenCaptureKit start completion: `178.563ms` after start request;
- first source committed: `127.806ms` after restart request;
- both sources committed: `194.706ms` after restart request;
- one native uncaptured interval: `8.566313..9.035042`, `0.468729s`;
- `0.274687s` of the loss precedes the restart request; `0.194042s` follows it;
- subsequent batch processing completed normally;
- `outcome.capture_continuity` is blocking review and `transcript --cat` carries a disclaimer;
- no writer failure, duplicate terminal, deadlock, continuation leak or unavailable recording lock.

The previous unconditional `500ms` sleep and redundant `stopCapture` are gone. The measured gap is
about 38% shorter than the mean frozen gap, but it is not zero; calling it fully repaired would be
false.

## No-Restart Soak

The ordinary ScreenCaptureKit capture ran for `600.434s`:

- mic and remote each cover `600.434s`;
- restart count `0`;
- capture gap count `0`, gap seconds `0.000000`;
- `capture_complete=true`;
- remote silence was correctly retained as a health warning, not misclassified as continuity loss;
- final manifest and lock-release events occur exactly once.

## Rebaseline

The same six-session Post-Segmentation Transcript Rebaseline membership was refreshed only after its
last session's derived artifacts had stabilized. Production transcripts were not regenerated.
`REBASELINE_ESTABLISHED` remains `6/6`; unknown remote evidence remains `397.543570s / 547 words`,
capture loss remains `2.268542s`, and repeated replay is byte-exact.

## Boundary

This work cannot remove audio that macOS did not deliver. The safe production behavior is now:

1. restart immediately through one serialized attempt;
2. preserve every delivered mic/remote frame;
3. write exact missing intervals as `captured_audio=false`;
4. keep the batch transcript usable as partial evidence;
5. block terminal completeness until the user accepts the limitation or records again.

The next goal can return to Remote Unknown Evidence Recovery v1. Capture topology changes or a
second independent capture process require separate evidence.
