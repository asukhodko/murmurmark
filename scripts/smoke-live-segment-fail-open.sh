#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fail() {
  echo "live segment fail-open smoke failed: $*" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || fail "missing jq"

bin="${MURMURMARK_BIN:-$repo_root/.build/debug/murmurmark}"
if [[ ! -x "$bin" ]]; then
  swift build >/dev/null
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-live-fail-open.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

session="$workdir/session"
record_log="$workdir/record.log"

set +e
MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 \
MURMURMARK_LIVE_SEGMENT_MAX_PENDING_SAMPLES=1 \
MURMURMARK_LIVE_SEGMENT_WRITE_DELAY_MS=250 \
  "$bin" record \
    --target-bundle system \
    --duration 4 \
    --live-pipeline \
    --live-no-worker \
    --live-no-finalize \
    --live-segment-sec 2 \
    --live-overlap-sec 0.5 \
    --out "$session" >"$record_log" 2>&1
status=$?
set -e

[[ "$status" -eq 0 ]] || {
  cat "$record_log" >&2
  fail "record command failed"
}

[[ -s "$session/session.json" ]] || {
  cat "$record_log" >&2
  fail "session.json was not created"
}

jq -e '
  (.health.partial // false) == false
  and (.health.tracks.mic.duration_sec // 0) >= 3.0
  and (.health.tracks.remote.duration_sec // 0) >= 3.0
  and (.health.tracks.mic.frames // 0) > 0
  and (.health.tracks.remote.frames // 0) > 0
' "$session/session.json" >/dev/null || {
  cat "$record_log" >&2
  jq '.health' "$session/session.json" >&2
  fail "raw capture did not survive live segment queue overload"
}

grep -q 'backlog exceeded 1 samples' "$session/session.json" || {
  cat "$record_log" >&2
  jq '.health.warnings' "$session/session.json" >&2
  fail "live segment queue overload warning was not recorded"
}

grep -q '"writer_mode":"async_bounded_queue"' "$session/events.jsonl" \
  || fail "live prepare event does not record async writer mode"

grep -q '"callback_policy":"raw_write_then_nonblocking_live_enqueue"' "$session/events.jsonl" \
  || fail "live prepare event does not record callback policy"

grep -q '"max_pending_samples":1' "$session/events.jsonl" \
  || fail "live prepare event does not record lab max pending setting"

grep -q '"artificial_write_delay_ms":250' "$session/events.jsonl" \
  || fail "live prepare event does not record lab delay setting"

echo "live segment fail-open smoke ok"
