#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

usage() {
  cat <<'EOF'
usage: scripts/run-live-parity-pilot.sh [SESSION] [options]

Lab-only near-realtime pilot runner.

Without SESSION, records a short unsafe live-pipeline session, then runs the batch pipeline,
live-vs-batch comparison and live corpus report. With SESSION, skips recording and processes the
existing live-pipeline session.

Options:
  --duration SEC       Recording duration for a new pilot. Default: 45.
  --segment-sec SEC    Live segment length. Default: 15.
  --overlap-sec SEC    Live overlap length. Default: 3.
  --out SESSION        Output session path for a new pilot.
  --skip-safety-gate   Reuse the existing full capture proof instead of running the probe first.
  --force-asr          Force batch ASR during murmurmark process.
  --help               Show this help.

This runner is not a production recording path. It keeps live promotion blocked and batch transcript
authoritative while collecting lab evidence.
EOF
}

fail() {
  echo "live parity pilot failed: $*" >&2
  exit 1
}

duration=45
segment_sec=15
overlap_sec=3
session=""
record_new=0
skip_safety_gate=0
force_asr=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration)
      [[ $# -ge 2 ]] || fail "--duration requires a value"
      duration="$2"
      shift 2
      ;;
    --segment-sec)
      [[ $# -ge 2 ]] || fail "--segment-sec requires a value"
      segment_sec="$2"
      shift 2
      ;;
    --overlap-sec)
      [[ $# -ge 2 ]] || fail "--overlap-sec requires a value"
      overlap_sec="$2"
      shift 2
      ;;
    --out)
      [[ $# -ge 2 ]] || fail "--out requires a path"
      session="$2"
      record_new=1
      shift 2
      ;;
    --skip-safety-gate)
      skip_safety_gate=1
      shift
      ;;
    --force-asr)
      force_asr=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    -*)
      fail "unknown option: $1"
      ;;
    *)
      if [[ -n "$session" ]]; then
        fail "multiple session paths provided"
      fi
      session="$1"
      record_new=0
      shift
      ;;
  esac
done

command -v jq >/dev/null 2>&1 || fail "missing jq"

capture_regression_report="${MURMURMARK_CAPTURE_REGRESSION_REPORT:-sessions/_reports/capture-regression/capture_regression_check.json}"

bin="${MURMURMARK_BIN:-}"
if [[ -z "$bin" ]]; then
  if command -v murmurmark >/dev/null 2>&1; then
    bin="$(command -v murmurmark)"
  else
    bin="$repo_root/.build/debug/murmurmark"
  fi
fi
if [[ ! -x "$bin" ]]; then
  swift build >/dev/null
fi
[[ -x "$bin" ]] || fail "murmurmark binary is not executable: $bin"

if [[ -z "$session" ]]; then
  session="sessions/live-pilot-$(date '+%Y-%m-%d_%H-%M-%S')"
  record_new=1
fi

if [[ "$record_new" == "1" && "$skip_safety_gate" != "1" ]]; then
  echo "[pilot] safety gate: system audio + live fail-open probe"
  MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
fi

if [[ "$record_new" == "1" ]]; then
  proof_status="$(
    jq -r '.capture_safe_proof.status // "missing"' "$capture_regression_report" 2>/dev/null || printf 'missing\n'
  )"
  if [[ "$proof_status" != "full_fail_open_proof_passed" ]]; then
    if [[ "${MURMURMARK_ALLOW_UNSAFE_LIVE_PILOT_WITHOUT_PROOF:-0}" != "1" ]]; then
      fail "capture-safe proof is not full_fail_open_proof_passed: ${proof_status}. Run MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh first."
    fi
    echo "[pilot] warning: bypassing missing capture-safe proof because MURMURMARK_ALLOW_UNSAFE_LIVE_PILOT_WITHOUT_PROOF=1" >&2
  fi
fi

if [[ "$record_new" == "1" ]]; then
  echo "[pilot] record live shadow session -> $session"
  MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 \
    "$bin" record \
      --target-bundle system \
      --duration "$duration" \
      --live-pipeline \
      --live-segment-sec "$segment_sec" \
      --live-overlap-sec "$overlap_sec" \
      --out "$session"
else
  [[ -d "$session" ]] || fail "session not found: $session"
fi

process_args=("$session" "--skip-build")
if [[ "$force_asr" == "1" ]]; then
  process_args+=("--force-asr")
fi

echo "[pilot] batch process and live compare"
"$bin" process "${process_args[@]}"

echo "[pilot] explicit live-vs-batch comparison"
python3 scripts/compare-live-batch.py "$session"

echo "[pilot] live corpus report"
"$bin" corpus live all --sessions-root sessions

comparison="$session/derived/live/live_batch_comparison.json"
corpus_report="sessions/_reports/live-pipeline/live_corpus_gates_report.json"
pilot_report="$session/derived/live/live_parity_pilot_report.json"

[[ -s "$comparison" ]] || fail "missing live comparison: $comparison"
[[ -s "$corpus_report" ]] || fail "missing live corpus report: $corpus_report"

jq -e '
  .promotion_policy.status == "blocked"
  and .promotion_policy.batch_authoritative == true
  and .promotion_policy.new_real_live_collection_allowed == false
' "$corpus_report" >/dev/null || {
  jq '.promotion_policy' "$corpus_report" >&2
  fail "live corpus promotion policy is not safely blocked"
}

mkdir -p "$(dirname "$pilot_report")"
python3 - "$session" "$comparison" "$corpus_report" "$pilot_report" "$record_new" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

session = Path(sys.argv[1])
comparison_path = Path(sys.argv[2])
corpus_path = Path(sys.argv[3])
out_path = Path(sys.argv[4])
created_session = sys.argv[5] == "1"

comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
payload = {
    "schema": "murmurmark.live_parity_pilot_report/v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "session": str(session),
    "created_session": created_session,
    "comparison": {
        "path": str(comparison_path),
        "parity_status": comparison.get("parity_status"),
        "promotion_allowed": comparison.get("promotion_allowed"),
        "metrics": comparison.get("metrics", {}),
    },
    "corpus": {
        "path": str(corpus_path),
        "summary": corpus.get("summary", {}),
        "promotion_policy": corpus.get("promotion_policy", {}),
    },
    "batch_authoritative": True,
    "promotion_must_remain_blocked": True,
}
out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "live_parity_pilot_report: $pilot_report"
echo "session: $session"
echo "promotion_policy: $(jq -r '.promotion_policy.status' "$corpus_report")"
echo "next: less $pilot_report"
