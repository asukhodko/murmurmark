#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_verify=1
verify_python="${MURMURMARK_PYTHON:-}"
live_checklist=0
report_path=""
live_session=""
sessions_root="sessions"
manual_gate_status="manual"
manual_gate_command="murmurmark acceptance --live-checklist"
report_session=""
report_readiness_status=""

usage() {
  cat <<'EOF'
usage: scripts/acceptance-cli-mvp.sh [--skip-release] [--python PATH] [--live-checklist] [--live-session SESSION|latest] [--sessions-root DIR] [--report PATH]

Checks the current CLI MVP acceptance gate without touching real sessions or
raw recordings. The automated gate covers install, doctor, self-test, local
config, open-source readiness and release bundle verification.

When running from a release bundle without Sources/, it verifies the bundle
with doctor --strict, self-test and local config initialization.

Options:
  --skip-release  Skip release bundle verification.
  --python PATH   Use PATH as MURMURMARK_PYTHON for release verification.
  --live-checklist Print the manual live recording gate and exit.
  --live-session SESSION|latest
                 Verify the manual live gate for an already recorded and processed session.
  --sessions-root DIR
                 Sessions directory for resolving latest. Default: sessions.
  --report PATH   Write a machine-readable acceptance report.
EOF
}

started_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
checks=()

write_report() {
  [[ -n "$report_path" ]] || return 0
  local mode="$1"
  local status="$2"
  local next_command="$3"
  local completed_at
  completed_at="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  mkdir -p "$(dirname "$report_path")"
  local report_python="${verify_python:-python3}"
  local checks_text=""
  if [[ "${#checks[@]}" -gt 0 ]]; then
    checks_text="$(printf '%s\n' "${checks[@]}")"
  fi
  CHECKS_TEXT="$checks_text" \
  REPORT_PATH="$report_path" \
  MODE="$mode" \
  STATUS_VALUE="$status" \
  NEXT_COMMAND="$next_command" \
  STARTED_AT="$started_at" \
  COMPLETED_AT="$completed_at" \
  RELEASE_VERIFY="$release_verify" \
  MANUAL_GATE_STATUS="$manual_gate_status" \
  MANUAL_GATE_COMMAND="$manual_gate_command" \
  REPORT_SESSION="$report_session" \
  REPORT_READINESS_STATUS="$report_readiness_status" \
  "$report_python" - <<'PY'
import json
import os
from pathlib import Path

checks = []
for line in os.environ.get("CHECKS_TEXT", "").splitlines():
    if not line:
        continue
    name, status = line.split(":", 1)
    checks.append({"name": name, "status": status})

payload = {
    "schema": "murmurmark.cli_mvp_acceptance_report/v1",
    "mode": os.environ["MODE"],
    "status": os.environ["STATUS_VALUE"],
    "started_at": os.environ["STARTED_AT"],
    "completed_at": os.environ["COMPLETED_AT"],
    "release_verify": os.environ["RELEASE_VERIFY"] == "1",
    "checks": checks,
    "manual_gates": [
        {
            "name": "live_recording",
            "status": os.environ["MANUAL_GATE_STATUS"],
            "command": os.environ["MANUAL_GATE_COMMAND"],
        }
    ],
    "next": os.environ["NEXT_COMMAND"],
}

if os.environ.get("REPORT_SESSION"):
    payload["session"] = os.environ["REPORT_SESSION"]
if os.environ.get("REPORT_READINESS_STATUS"):
    payload["readiness_status"] = os.environ["REPORT_READINESS_STATUS"]

path = Path(os.environ["REPORT_PATH"])
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
  echo "report: $report_path"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-release)
      release_verify=0
      shift
      ;;
    --python)
      [[ $# -ge 2 ]] || { echo "error: --python requires a path" >&2; exit 2; }
      verify_python="$2"
      shift 2
      ;;
    --live-checklist)
      live_checklist=1
      shift
      ;;
    --live-session)
      [[ $# -ge 2 ]] || { echo "error: --live-session requires a session path or latest" >&2; exit 2; }
      live_session="$2"
      shift 2
      ;;
    --sessions-root)
      [[ $# -ge 2 ]] || { echo "error: --sessions-root requires a path" >&2; exit 2; }
      sessions_root="$2"
      shift 2
      ;;
    --report)
      [[ $# -ge 2 ]] || { echo "error: --report requires a path" >&2; exit 2; }
      report_path="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$live_checklist" == "1" && -n "$live_session" ]]; then
  echo "error: --live-checklist and --live-session cannot be combined" >&2
  exit 2
fi

if [[ "$live_checklist" == "1" ]]; then
  cat <<'EOF'
live_recording_gate:
  scope: production batch-first recording, not near-realtime live-pipeline
  commands:
    - murmurmark doctor
    - murmurmark self-test
    - murmurmark config init
    - murmurmark record --target-bundle system
    - murmurmark inspect latest
    - murmurmark process latest
    - murmurmark status latest
    - murmurmark next latest
    - murmurmark acceptance --live-session latest --report /tmp/murmurmark-live-session.json
    - follow printed review command when readiness says review_first
    - murmurmark finish latest
  pass_when:
    - recording creates separate non-empty mic and remote tracks
    - process latest completes or prints a concrete next command
    - status latest reports a clear readiness state
    - acceptance --live-session reports status ok
    - risky transcript regions remain explicit review items
    - export is blocked while required review/export blockers exist
    - successful finish writes an export manifest and retention manifests
    - retention planning does not delete raw audio without explicit apply plus confirmation

near_realtime_shadow_gate:
  scope: lab proof plus controlled real parity evidence, not production live promotion
  commands:
    - MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh
    - murmurmark live pilot --duration 45
    - murmurmark corpus live all --refresh
    - murmurmark live pilot --controlled-real --skip-safety-gate --preflight-only
    - jq '.promotion_policy' sessions/_reports/live-pipeline/live_corpus_gates_report.json
  pass_when:
    - system-audio capture probe passes on the normal batch-first recording path
    - overloaded async live segment queue disables only live-derived artifacts
    - raw mic and remote tracks survive the live fail-open probe
    - pilot runner writes derived/live/live_parity_pilot_report.json
    - controlled real preflight refuses to record unless corpus gates allow evidence collection
    - live corpus report keeps promotion_policy.status blocked
    - live corpus report keeps batch_authoritative true
    - new_real_live_collection_allowed remains false until real parity coverage is explicitly approved
EOF
  write_report "live_checklist" "manual" "MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh"
  echo "status: manual"
  echo "next: MURMURMARK_RUN_LIVE_CAPTURE_TEST=1 scripts/check-capture-regressions.sh"
  exit 0
fi

if [[ -n "$live_session" ]]; then
  murmurmark_bin="${MURMURMARK_BIN:-murmurmark}"
  inspect_output="$("$murmurmark_bin" inspect "$live_session" --sessions-root "$sessions_root")"
  status_output="$("$murmurmark_bin" status "$live_session" --sessions-root "$sessions_root")"

  extract_field() {
    local line="$1"
    local field="$2"
    sed -n "s/.*$field=\\([0-9.]*\\).*/\\1/p" <<<"$line"
  }

  validate_track() {
    local source="$1"
    local line
    line="$(printf '%s\n' "$inspect_output" | grep "^$source:" || true)"
    [[ -n "$line" ]] || { echo "error: inspect did not report $source track" >&2; exit 1; }
    local files bytes frames duration
    files="$(extract_field "$line" "files")"
    bytes="$(extract_field "$line" "bytes")"
    frames="$(extract_field "$line" "frames")"
    duration="$(extract_field "$line" "duration")"
    [[ "${files:-0}" -ge 1 ]] || { echo "error: $source track has no files" >&2; exit 1; }
    [[ "${bytes:-0}" -gt 0 ]] || { echo "error: $source track is empty" >&2; exit 1; }
    [[ "${frames:-0}" -gt 0 ]] || { echo "error: $source track has no frames" >&2; exit 1; }
    awk -v d="${duration:-0}" 'BEGIN { exit !(d > 0) }' || {
      echo "error: $source track duration is not positive" >&2
      exit 1
    }
  }

  validate_track "mic"
  validate_track "remote"
  checks+=("mic_track:passed")
  checks+=("remote_track:passed")

  readiness_status="$(printf '%s\n' "$status_output" | sed -n 's/^  status: //p' | head -1)"
  next_command="$(printf '%s\n' "$status_output" | sed -n 's/^next: //p' | tail -1)"
  [[ -n "$readiness_status" ]] || { echo "error: status did not report readiness status" >&2; exit 1; }
  [[ -n "$next_command" ]] || { echo "error: status did not report final next command" >&2; exit 1; }
  case "$readiness_status" in
    review_required|exportable|exported)
      ;;
    *)
      echo "error: live session is not ready enough for acceptance: $readiness_status" >&2
      echo "hint: $next_command" >&2
      exit 1
      ;;
  esac

  checks+=("readiness:passed")
  checks+=("live_recording:passed")
  manual_gate_status="passed"
  manual_gate_command="murmurmark acceptance --live-session $live_session"
  report_session="$(printf '%s\n' "$status_output" | sed -n 's/^SESSION="\(.*\)"$/\1/p' | head -1)"
  report_readiness_status="$readiness_status"

  echo "acceptance_live_session:"
  echo "  session: ${report_session:-$live_session}"
  echo "  mic_track: ok"
  echo "  remote_track: ok"
  echo "  readiness_status: $readiness_status"
  echo "  live_recording: ok"
  write_report "live_session" "ok" "$next_command"
  echo "status: ok"
  echo "next: $next_command"
  exit 0
fi

if [[ ! -f "$repo_root/Sources/MurmurMarkCLI/MurmurMarkCLI.swift" ]]; then
  workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-cli-release.XXXXXX")"
  trap 'rm -rf "$workdir"' EXIT
  config_path="$workdir/murmurmark.config.json"
  murmurmark_bin="${MURMURMARK_BIN:-murmurmark}"

  echo "acceptance_cli_mvp:"
  "$murmurmark_bin" doctor --strict >/dev/null
  echo "  doctor: ok"
  checks+=("doctor:passed")

  "$murmurmark_bin" self-test >/dev/null
  echo "  self_test: ok"
  checks+=("self_test:passed")

  "$murmurmark_bin" config init --config "$config_path" >/dev/null
  "$murmurmark_bin" config print --config "$config_path" >/dev/null
  echo "  local_config: ok"
  checks+=("local_config:passed")

  echo "  open_source_readiness: not_applicable"
  checks+=("open_source_readiness:not_applicable")
  echo "  release_bundle: current"
  checks+=("release_bundle:current")
  echo "  live_recording: manual"
  checks+=("live_recording:manual")
  write_report "release" "ok" "murmurmark acceptance --live-checklist"
  echo "status: ok"
  echo "next: murmurmark acceptance --live-checklist"
  exit 0
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-cli-mvp.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

prefix="$workdir/prefix"
config_path="$workdir/murmurmark.config.json"

echo "acceptance_cli_mvp:"

install_output="$("$repo_root/scripts/install-local.sh" --prefix "$prefix")"
echo "$install_output" | grep -q '^  murmurmark acceptance --skip-release$'
echo "$install_output" | grep -q '^  murmurmark acceptance --live-checklist$'
echo "$install_output" | grep -q '^  murmurmark acceptance --live-session latest --report /tmp/murmurmark-live-session.json$'
echo "  install_wrapper: ok"
checks+=("install_wrapper:passed")

export PATH="$prefix/bin:$PATH"

murmurmark doctor --strict >/dev/null
echo "  doctor: ok"
checks+=("doctor:passed")

murmurmark self-test >/dev/null
echo "  self_test: ok"
checks+=("self_test:passed")

murmurmark config init --config "$config_path" >/dev/null
murmurmark config print --config "$config_path" >/dev/null
echo "  local_config: ok"
checks+=("local_config:passed")

"$repo_root/scripts/check-open-source-readiness.sh" >/dev/null
echo "  open_source_readiness: ok"
checks+=("open_source_readiness:passed")

if [[ "$release_verify" == "1" ]]; then
  release_args=(--verify)
  if [[ -n "$verify_python" ]]; then
    release_args+=(--python "$verify_python")
  fi
  "$repo_root/scripts/build-release-bundle.sh" "${release_args[@]}" >/dev/null
  echo "  release_bundle: ok"
  checks+=("release_bundle:passed")
else
  echo "  release_bundle: skipped"
  checks+=("release_bundle:skipped")
fi

echo "  live_recording: manual"
checks+=("live_recording:manual")
write_report "automated" "ok" "murmurmark acceptance --live-checklist"
echo "status: ok"
echo "next: murmurmark acceptance --live-checklist"
