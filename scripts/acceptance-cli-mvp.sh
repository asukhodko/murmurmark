#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_verify=1
verify_python="${MURMURMARK_PYTHON:-}"
live_checklist=0
report_path=""

usage() {
  cat <<'EOF'
usage: scripts/acceptance-cli-mvp.sh [--skip-release] [--python PATH] [--live-checklist] [--report PATH]

Checks the current CLI MVP acceptance gate without touching real sessions or
raw recordings. The automated gate covers install, doctor, self-test, local
config, open-source readiness and release bundle verification.

When running from a release bundle without Sources/, it verifies the bundle
with doctor --strict, self-test and local config initialization.

Options:
  --skip-release  Skip release bundle verification.
  --python PATH   Use PATH as MURMURMARK_PYTHON for release verification.
  --live-checklist Print the manual live recording gate and exit.
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
            "status": "manual",
            "command": "murmurmark acceptance --live-checklist",
        }
    ],
    "next": os.environ["NEXT_COMMAND"],
}

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

if [[ "$live_checklist" == "1" ]]; then
  cat <<'EOF'
live_recording_gate:
  commands:
    - murmurmark doctor
    - murmurmark self-test
    - murmurmark config init
    - murmurmark record --target-bundle system
    - murmurmark inspect latest
    - murmurmark process latest
    - murmurmark status latest
    - murmurmark next latest
    - follow printed review command when readiness says review_first
    - murmurmark export latest --format markdown --include-json
    - murmurmark retention plan latest
  pass_when:
    - recording creates separate non-empty mic and remote tracks
    - process latest completes or prints a concrete next command
    - status latest reports a clear readiness state
    - risky transcript regions remain explicit review items
    - export is blocked while required review/export blockers exist
    - successful export writes an export manifest
    - retention planning does not delete raw audio without apply plus confirmation
EOF
  write_report "live_checklist" "manual" "murmurmark doctor"
  echo "status: manual"
  echo "next: murmurmark doctor"
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
