#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
release_verify=1
verify_python="${MURMURMARK_PYTHON:-}"
live_checklist=0

if [[ ! -f "$repo_root/Sources/MurmurMarkCLI/MurmurMarkCLI.swift" ]]; then
  echo "error: CLI MVP acceptance requires a full developer checkout with Sources/." >&2
  echo "hint: release bundles should be verified with: murmurmark doctor --strict && murmurmark self-test" >&2
  exit 1
fi

usage() {
  cat <<'EOF'
usage: scripts/acceptance-cli-mvp.sh [--skip-release] [--python PATH] [--live-checklist]

Checks the current CLI MVP acceptance gate without touching real sessions or
raw recordings. The automated gate covers install, doctor, self-test, local
config, open-source readiness and release bundle verification.

Options:
  --skip-release  Skip release bundle verification.
  --python PATH   Use PATH as MURMURMARK_PYTHON for release verification.
  --live-checklist Print the manual live recording gate and exit.
EOF
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
status: manual
next: murmurmark doctor
EOF
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

export PATH="$prefix/bin:$PATH"

murmurmark doctor --strict >/dev/null
echo "  doctor: ok"

murmurmark self-test >/dev/null
echo "  self_test: ok"

murmurmark config init --config "$config_path" >/dev/null
murmurmark config print --config "$config_path" >/dev/null
echo "  local_config: ok"

"$repo_root/scripts/check-open-source-readiness.sh" >/dev/null
echo "  open_source_readiness: ok"

if [[ "$release_verify" == "1" ]]; then
  release_args=(--verify)
  if [[ -n "$verify_python" ]]; then
    release_args+=(--python "$verify_python")
  fi
  "$repo_root/scripts/build-release-bundle.sh" "${release_args[@]}" >/dev/null
  echo "  release_bundle: ok"
else
  echo "  release_bundle: skipped"
fi

echo "  live_recording: manual"
echo "status: ok"
echo "next: murmurmark acceptance --live-checklist"
