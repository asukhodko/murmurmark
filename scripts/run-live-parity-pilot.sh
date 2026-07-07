#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

usage() {
  cat <<'EOF'
usage: scripts/run-live-parity-pilot.sh [SESSION] [options]

Near-realtime pilot runner.

Without SESSION, records a short lab live-pipeline session, then runs the batch pipeline,
live-vs-batch comparison and live corpus report. With SESSION, skips recording and processes the
existing live-pipeline session. Use --controlled-real with SESSION to mark an existing date-named
Live Evidence run without starting another recording.

Options:
  --duration SEC       Recording duration for a new pilot. Default: 45.
  --segment-sec SEC    Live segment length. Default: 15.
  --overlap-sec SEC    Live overlap length. Default: 3.
  --out SESSION        Output session path for a new pilot.
  --controlled-real    Record a date-named controlled Live Evidence run until Ctrl-C.
                       Defaults to --segment-sec 60, --overlap-sec 5 and --live-no-finalize.
                       With SESSION, process existing controlled Live Evidence.
  --preflight-only     Check safety/corpus gates and exit before recording or processing.
  --skip-safety-gate   Reuse the existing full capture proof instead of running the probe first.
  --force-asr          Force batch ASR during murmurmark process.
  --help               Show this help.

This runner is a sidecar evidence path, not a source of final transcript truth. It keeps live
promotion blocked and batch transcript authoritative while collecting parity evidence. It also refreshes
derived/experiments/live-shadow-v1/{experiment_manifest.json,state.json,events.jsonl,report.json}.
EOF
}

fail() {
  echo "live parity pilot failed: $*" >&2
  exit 1
}

duration=45
duration_set=0
segment_sec=15
segment_set=0
overlap_sec=3
overlap_set=0
session=""
record_new=0
skip_safety_gate=0
force_asr=0
controlled_real=0
preflight_only=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration)
      [[ $# -ge 2 ]] || fail "--duration requires a value"
      duration="$2"
      duration_set=1
      shift 2
      ;;
    --segment-sec)
      [[ $# -ge 2 ]] || fail "--segment-sec requires a value"
      segment_sec="$2"
      segment_set=1
      shift 2
      ;;
    --overlap-sec)
      [[ $# -ge 2 ]] || fail "--overlap-sec requires a value"
      overlap_sec="$2"
      overlap_set=1
      shift 2
      ;;
    --out)
      [[ $# -ge 2 ]] || fail "--out requires a path"
      session="$2"
      record_new=1
      shift 2
      ;;
    --controlled-real)
      controlled_real=1
      shift
      ;;
    --preflight-only)
      preflight_only=1
      shift
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
corpus_report="sessions/_reports/live-pipeline/live_corpus_gates_report.json"

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
  if [[ "$controlled_real" == "1" ]]; then
    session="sessions/$(date '+%Y-%m-%d_%H-%M-%S')"
  else
    session="sessions/live-pilot-$(date '+%Y-%m-%d_%H-%M-%S')"
  fi
  record_new=1
fi

if [[ "$controlled_real" == "1" ]]; then
  if [[ "$segment_set" != "1" ]]; then
    segment_sec=60
  fi
  if [[ "$overlap_set" != "1" ]]; then
    overlap_sec=5
  fi
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

if [[ "$record_new" == "1" && "$controlled_real" == "1" ]]; then
  echo "[pilot] preflight: refresh live corpus gates for controlled Live Evidence"
  "$bin" corpus live all --refresh --sessions-root sessions >/dev/null
  [[ -s "$corpus_report" ]] || fail "missing live corpus report: $corpus_report"
  jq -e '
    .promotion_policy.status == "blocked"
    and .promotion_policy.batch_authoritative == true
    and .promotion_policy.new_real_live_collection_allowed == false
    and .promotion_policy.controlled_real_live_pilot_allowed == true
    and ((.coverage_target.passing_compared_sessions_remaining // 0) > 0)
    and (((.capture_safe_candidate_scope.blocking_dimensions // []) | length) == 0)
  ' "$corpus_report" >/dev/null || {
    jq '{
      recommended_next,
      coverage_target,
      promotion_policy,
      capture_safe_candidate_scope
    }' "$corpus_report" >&2
    fail "controlled Live Evidence is not allowed by current live corpus gates"
  }
fi

if [[ "$preflight_only" == "1" ]]; then
  echo "preflight: ok"
  echo "session: $session"
  echo "record_new: $record_new"
  echo "controlled_real: $controlled_real"
  if [[ "$controlled_real" == "1" ]]; then
    echo "controlled_real_live_pilot_allowed: $(jq -r '.promotion_policy.controlled_real_live_pilot_allowed // false' "$corpus_report")"
    echo "new_real_live_collection_allowed: $(jq -r '.promotion_policy.new_real_live_collection_allowed // false' "$corpus_report")"
    echo "coverage_passing_remaining: $(jq -r '.coverage_target.passing_compared_sessions_remaining // 0' "$corpus_report")"
    echo "next: murmurmark live pilot --controlled-real --skip-safety-gate"
    echo "after_stop: murmurmark experiment status latest"
    echo "after_stop: murmurmark experiment compare latest --experiment live-shadow-v1"
    echo "final_source: batch transcript remains authoritative"
  else
    echo "next: murmurmark live pilot --duration $duration --skip-safety-gate"
  fi
  exit 0
fi

if [[ "$record_new" == "1" ]]; then
  echo "[pilot] record live shadow session -> $session"
  record_args=(
    record
    --target-bundle system
    --live-pipeline
    --live-segment-sec "$segment_sec"
    --live-overlap-sec "$overlap_sec"
    --out "$session"
  )
  if [[ "$controlled_real" == "1" ]]; then
    record_args+=(--live-no-finalize)
    if [[ "$duration_set" == "1" ]]; then
      record_args+=(--duration "$duration")
    fi
  else
    record_args+=(--duration "$duration")
  fi
  MURMURMARK_ENABLE_UNSAFE_LIVE_PIPELINE=1 "$bin" "${record_args[@]}"
else
  [[ -d "$session" ]] || fail "session not found: $session"
fi

python3 scripts/experiment-sidecar-contract.py refresh "$session" --experiment live-shadow-v1 >/dev/null

process_args=("$session" "--skip-build")
if [[ "$force_asr" == "1" ]]; then
  process_args+=("--force-asr")
fi

echo "[pilot] batch process and live compare"
"$bin" process "${process_args[@]}"

echo "[pilot] explicit live-vs-batch comparison"
python3 scripts/compare-live-batch.py "$session"
python3 scripts/experiment-sidecar-contract.py refresh "$session" --experiment live-shadow-v1 >/dev/null

echo "[pilot] live corpus report"
"$bin" corpus live all --refresh --sessions-root sessions

comparison="$session/derived/live/live_batch_comparison.json"
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
python3 - "$session" "$comparison" "$corpus_report" "$pilot_report" "$record_new" "$controlled_real" <<'PY'
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
controlled_real = sys.argv[6] == "1"

comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
session_keys = {str(session), session.name}
if not str(session).startswith("sessions/"):
    session_keys.add("sessions/" + str(session))

corpus_sessions = corpus.get("sessions") if isinstance(corpus.get("sessions"), list) else []
corpus_row = None
for row in corpus_sessions:
    if not isinstance(row, dict):
        continue
    row_session = str(row.get("session") or "")
    if row_session in session_keys or row_session == session.name:
        corpus_row = row
        break

parity_dimensions = corpus_row.get("parity_dimensions", {}) if isinstance(corpus_row, dict) else {}
non_passing_dimensions = [
    key
    for key, value in sorted(parity_dimensions.items())
    if isinstance(value, dict) and value.get("status") != "passed"
]
coverage_target = corpus.get("coverage_target") if isinstance(corpus.get("coverage_target"), dict) else {}
contributes_to_passing_coverage = bool(
    controlled_real
    and isinstance(corpus_row, dict)
    and corpus_row.get("evidence_scope") == "real_meeting"
    and corpus_row.get("meaningful_compared") is True
    and corpus_row.get("all_parity_gates_passed") is True
)
if contributes_to_passing_coverage:
    pilot_verdict = "passed_coverage"
elif controlled_real and isinstance(corpus_row, dict) and corpus_row.get("meaningful_compared") is True:
    pilot_verdict = "compared_but_not_passing"
elif controlled_real:
    pilot_verdict = "not_meaningfully_compared"
else:
    pilot_verdict = "diagnostic_only"

payload = {
    "schema": "murmurmark.live_parity_pilot_report/v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "session": str(session),
    "created_session": created_session,
    "controlled_real": controlled_real,
    "pilot_verdict": pilot_verdict,
    "contributes_to_passing_coverage": contributes_to_passing_coverage,
    "non_passing_dimensions": non_passing_dimensions,
    "non_passing_gates": corpus_row.get("non_passing_gates", []) if isinstance(corpus_row, dict) else [],
    "coverage_after": {
        "status": coverage_target.get("status"),
        "passing_compared_sessions_remaining": coverage_target.get("passing_compared_sessions_remaining"),
        "meaningful_compared_sessions_remaining": coverage_target.get("meaningful_compared_sessions_remaining"),
        "live_sessions_remaining": coverage_target.get("live_sessions_remaining"),
    },
    "comparison": {
        "path": str(comparison_path),
        "status": comparison.get("status"),
        "promotion_allowed": comparison.get("promotion_allowed"),
        "metrics": comparison.get("metrics", {}),
    },
    "corpus": {
        "path": str(corpus_path),
        "session_row": corpus_row,
        "summary": corpus.get("summary", {}),
        "promotion_policy": corpus.get("promotion_policy", {}),
    },
    "batch_authoritative": True,
    "promotion_must_remain_blocked": True,
}
out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

python3 scripts/experiment-sidecar-contract.py refresh "$session" --experiment live-shadow-v1 >/dev/null

echo "live_parity_pilot_report: $pilot_report"
echo "session: $session"
echo "pilot_verdict: $(jq -r '.pilot_verdict' "$pilot_report")"
echo "contributes_to_passing_coverage: $(jq -r '.contributes_to_passing_coverage' "$pilot_report")"
coverage_passing_remaining="$(jq -r '.coverage_after.passing_compared_sessions_remaining // "unknown"' "$pilot_report")"
echo "coverage_passing_remaining: $coverage_passing_remaining"
echo "promotion_policy: $(jq -r '.promotion_policy.status' "$corpus_report")"
echo "controlled_real_live_pilot_allowed: $(jq -r '.promotion_policy.controlled_real_live_pilot_allowed // false' "$corpus_report")"
echo "next: less $pilot_report"
echo "next: murmurmark experiment status $session"
echo "next: murmurmark experiment report $session"
echo "next: murmurmark experiment compare $session --experiment live-shadow-v1"
echo "next: murmurmark status $session"
echo "next: murmurmark transcript $session"
echo "final_source: batch transcript remains authoritative"
