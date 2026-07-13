#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"

if [[ ! -x "$bin" ]]; then
  (cd "$repo_root" && swift build >/dev/null)
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-live-watch.XXXXXX")"
watch_pid=""
cleanup() {
  if [[ -n "$watch_pid" ]] && kill -0 "$watch_pid" >/dev/null 2>&1; then
    kill "$watch_pid" >/dev/null 2>&1 || true
    wait "$watch_pid" >/dev/null 2>&1 || true
  fi
  rm -rf "$workdir"
}
trap cleanup EXIT

session="$workdir/sessions/in-progress-live"
live_dir="$session/derived/live"
mkdir -p "$session/audio/mic" "$live_dir"
: >"$session/session.lock"

cat >"$live_dir/live_pipeline_state.json" <<'JSON'
{"schema":"murmurmark.live_pipeline_state/v1","status":"running","current_stage":"waiting_for_first_segment"}
JSON

cat >"$live_dir/transcript.preview.md" <<'MARKDOWN'
# Live Preview

Waiting for the first committed segment.
MARKDOWN

"$bin" live watch "$session" --poll-sec 0.2 >"$workdir/watch.out" 2>&1 &
watch_pid=$!

for _ in $(seq 1 25); do
  if grep -q '^watching:' "$workdir/watch.out"; then
    break
  fi
  sleep 0.1
done
grep -q '^watching:' "$workdir/watch.out"
grep -q 'status=running' "$workdir/watch.out"
kill -0 "$watch_pid" >/dev/null 2>&1

cat >"$session/session.json" <<'JSON'
{"schema":"murmurmark.session/v1","status":"completed"}
JSON
cat >"$live_dir/live_pipeline_state.json" <<'JSON'
{"schema":"murmurmark.live_pipeline_state/v1","status":"completed","current_stage":"completed"}
JSON

for _ in $(seq 1 25); do
  if ! kill -0 "$watch_pid" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
wait "$watch_pid"
watch_pid=""
grep -q 'status=completed' "$workdir/watch.out"

echo "live watch in-progress smoke ok"
