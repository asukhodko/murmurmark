#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"

python3 - "$repo_root" <<'PY'
import importlib.util
from pathlib import Path
import sys

module_path = Path(sys.argv[1]) / "scripts/watch-live-draft.py"
spec = importlib.util.spec_from_file_location("watch_live_draft", module_path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
previous = "# Live Preview\n\n## 00:00 provisional\n\n**Colleagues**\n\nfirst-live-block\n"
current = (
    "# Live Preview\n\n## 00:00\n\n**Colleagues**\n\nfirst-live-block\n\n"
    "## 00:30 provisional\n\n**Me**\n\nsecond-live-block\n"
)
delta = module.draft_delta(previous, current)
assert "first-live-block" not in delta, delta
assert delta.count("second-live-block") == 1, delta
PY

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
{"schema":"murmurmark.live_pipeline_state/v1","status":"running","current_stage":"waiting_segments","heartbeat_at":"2026-07-15T09:00:00Z","progress":{"live_lag_sec":0.0}}
JSON

cat >"$live_dir/transcript.preview.md" <<'MARKDOWN'
# Live Preview

## 00:00 provisional

**Colleagues**

first-live-block
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
grep -q 'first-live-block' "$workdir/watch.out"
kill -0 "$watch_pid" >/dev/null 2>&1

cat >"$live_dir/live_pipeline_state.json" <<'JSON'
{"schema":"murmurmark.live_pipeline_state/v1","status":"running","current_stage":"waiting_segments","heartbeat_at":"2026-07-15T09:00:02Z","progress":{"live_lag_sec":0.0}}
JSON

cat >"$live_dir/transcript.preview.md" <<'MARKDOWN'
# Live Preview

## 00:00

**Colleagues**

first-live-block

## 00:30 provisional

**Me**

second-live-block
MARKDOWN

for _ in $(seq 1 25); do
  if grep -q 'second-live-block' "$workdir/watch.out"; then
    break
  fi
  sleep 0.1
done
grep -q 'second-live-block' "$workdir/watch.out"
[[ "$(grep -c 'first-live-block' "$workdir/watch.out")" -eq 1 ]]
[[ "$(grep -c 'second-live-block' "$workdir/watch.out")" -eq 1 ]]
[[ "$(grep -c 'status=running stage=waiting_segments' "$workdir/watch.out")" -eq 1 ]]
! grep -q 'draft refreshed' "$workdir/watch.out"

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
