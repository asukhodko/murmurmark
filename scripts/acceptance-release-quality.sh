#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${MURMURMARK_PYTHON:-$repo_root/.venv/bin/python}"
report_path=""
keep_workdir=0
build=1

usage() {
  cat <<'EOF'
usage: scripts/acceptance-release-quality.sh [--python PATH] [--report PATH] [--no-build] [--keep-workdir]

Builds the release archive twice, proves deterministic packaging, installs the
verified artifact in an isolated prefix, exercises offline doctor/self-test,
tests idempotent install and upgrade, then proves a corrupt upgrade cannot
replace the current release or mutate workspace data.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --python)
      [[ $# -ge 2 ]] || { echo "error: --python requires a path" >&2; exit 2; }
      python_bin="$2"
      shift 2
      ;;
    --report)
      [[ $# -ge 2 ]] || { echo "error: --report requires a path" >&2; exit 2; }
      report_path="$2"
      shift 2
      ;;
    --no-build)
      build=0
      shift
      ;;
    --keep-workdir)
      keep_workdir=1
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

if [[ ! -x "$python_bin" ]]; then
  echo "error: release acceptance Python is not executable: $python_bin" >&2
  exit 1
fi
if [[ "$python_bin" != /* ]]; then
  python_bin="$(cd "$(dirname "$python_bin")" && pwd)/$(basename "$python_bin")"
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-release-quality.XXXXXX")"
on_error() {
  local status=$?
  keep_workdir=1
  echo "error: release-quality acceptance failed near line ${BASH_LINENO[0]}" >&2
  echo "diagnostics: $workdir" >&2
  return "$status"
}
cleanup() {
  if [[ "$keep_workdir" == "1" ]]; then
    echo "workdir: $workdir"
  else
    rm -rf "$workdir"
  fi
}
trap on_error ERR
trap cleanup EXIT

if [[ "$build" == "0" ]]; then
  "$repo_root/scripts/build-release-bundle.sh" \
    --out-dir "$workdir/build-a" \
    --python "$python_bin" \
    --no-build >/dev/null
else
  "$repo_root/scripts/build-release-bundle.sh" \
    --out-dir "$workdir/build-a" \
    --python "$python_bin" >/dev/null
fi
"$repo_root/scripts/build-release-bundle.sh" \
  --out-dir "$workdir/build-b" \
  --python "$python_bin" \
  --no-build >/dev/null

archive_a="$(find "$workdir/build-a" -maxdepth 1 -type f -name 'murmurmark-*.tar.gz' -print -quit)"
archive_b="$(find "$workdir/build-b" -maxdepth 1 -type f -name 'murmurmark-*.tar.gz' -print -quit)"
bundle_a="$(find "$workdir/build-a" -mindepth 1 -maxdepth 1 -type d -name 'murmurmark-*' -print -quit)"
[[ -n "$archive_a" && -n "$archive_b" && -n "$bundle_a" ]] || {
  echo "error: release builder did not create the expected artifacts" >&2
  exit 1
}
cmp "$archive_a" "$archive_b"
"$python_bin" "$bundle_a/scripts/release-bundle.py" verify "$archive_a" >/dev/null
archive_sha="$(shasum -a 256 "$archive_a" | awk '{print $1}')"

isolated_home="$workdir/home"
workspace="$workdir/workspace"
prefix="$workdir/prefix"
mkdir -p "$isolated_home" "$workspace"

model_path="$($python_bin - "$repo_root/murmurmark.config.example.json" <<'PY'
import json
import os
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(os.path.expanduser(payload["transcription"]["model"]))
PY
)"
[[ -f "$model_path" ]] || {
  echo "error: required whisper model is unavailable for release acceptance: $model_path" >&2
  exit 1
}
model_contract="$($python_bin - "$bundle_a/release/compatibility-v1.json" <<'PY'
import json
import sys

model = json.load(open(sys.argv[1], encoding="utf-8"))["models"]["whisper_cpp"]
print(model["tested_name"])
print(model["tested_size_bytes"])
print(model["tested_sha256"])
PY
)"
[[ "$(basename "$model_path")" == "$(sed -n '1p' <<<"$model_contract")" ]]
[[ "$(stat -f '%z' "$model_path")" == "$(sed -n '2p' <<<"$model_contract")" ]]
model_sha="$(shasum -a 256 "$model_path" | awk '{print $1}')"
[[ "$model_sha" == "$(sed -n '3p' <<<"$model_contract")" ]]

HOME="$isolated_home" \
MURMURMARK_PYTHON="$python_bin" \
  "$bundle_a/install.sh" --prefix "$prefix" --python "$python_bin" >/dev/null
installed="$prefix/bin/murmurmark"
[[ -x "$installed" ]]

(
  cd "$workspace"
  HOME="$isolated_home" MURMURMARK_PYTHON="$python_bin" \
    "$installed" config init >/dev/null
)
CONFIG="$workspace/murmurmark.config.json" MODEL="$model_path" "$python_bin" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["CONFIG"])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["transcription"]["model"] = os.environ["MODEL"]
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

doctor_output="$workdir/doctor.txt"
acceptance_output="$workdir/package-acceptance.txt"
if [[ -x /usr/bin/caffeinate ]]; then
  /usr/bin/caffeinate -u -t 5
fi
(
  cd "$workspace"
  HOME="$isolated_home" \
  HF_HUB_OFFLINE=1 \
  TRANSFORMERS_OFFLINE=1 \
  MURMURMARK_FASTER_WHISPER_MODEL="$isolated_home/missing/faster-whisper" \
  MURMURMARK_TARGET_ME_WAVLM_MODEL="$isolated_home/missing/wavlm" \
  MURMURMARK_PYTHON="$python_bin" \
    "$installed" doctor --strict >"$doctor_output"
  HOME="$isolated_home" \
  HF_HUB_OFFLINE=1 \
  TRANSFORMERS_OFFLINE=1 \
  MURMURMARK_PYTHON="$python_bin" \
  MURMURMARK_BIN="$installed" \
    "$installed" acceptance --skip-release >"$acceptance_output"
)
grep -q '^\[ok\] release compatibility:' "$doctor_output"
grep -q '^\[ok\] release manifest:' "$doctor_output"
grep -q '^\[warn\] faster-whisper model:' "$doctor_output"
grep -q '^status: doctor completed$' "$doctor_output"
grep -q '^  self_test: ok$' "$acceptance_output"
grep -q '^status: ok$' "$acceptance_output"

version_json="$(cd "$workspace" && HOME="$isolated_home" MURMURMARK_PYTHON="$python_bin" "$installed" version --json)"
VERSION_JSON="$version_json" "$python_bin" - <<'PY'
import json
import os

payload = json.loads(os.environ["VERSION_JSON"])
assert payload["runtime_mode"] == "release_bundle"
assert payload["manifest_schema"] == "murmurmark.release_bundle/v2"
assert len(payload["package_fingerprint"]) == 64
PY

session="$workspace/sessions/release-upgrade-fixture"
mkdir -p "$session/audio/mic" "$session/audio/remote" "$session/derived/evidence-handoff-v2"
printf 'immutable mic fixture\n' >"$session/audio/mic/000001.caf"
printf 'immutable remote fixture\n' >"$session/audio/remote/000001.caf"
cat >"$session/session.json" <<'JSON'
{"schema":"murmurmark.session/v1","session_id":"release-upgrade-fixture","status":"completed"}
JSON
cat >"$session/derived/evidence-handoff-v2/handoff_manifest.json" <<'JSON'
{"schema":"murmurmark.evidence_handoff/v2","state":"ready","handoff_fingerprint":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
JSON

workspace_fingerprint() {
  WORKSPACE="$workspace" "$python_bin" - <<'PY'
import hashlib
import json
import os
from pathlib import Path

root = Path(os.environ["WORKSPACE"])
paths = [
    root / "murmurmark.config.json",
    root / "sessions/release-upgrade-fixture/session.json",
    root / "sessions/release-upgrade-fixture/audio/mic/000001.caf",
    root / "sessions/release-upgrade-fixture/audio/remote/000001.caf",
    root / "sessions/release-upgrade-fixture/derived/evidence-handoff-v2/handoff_manifest.json",
]
payload = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
print(hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
PY
}

before_fingerprint="$(workspace_fingerprint)"
first_target="$(readlink "$prefix/share/murmurmark/current")"
HOME="$isolated_home" MURMURMARK_PYTHON="$python_bin" \
  "$bundle_a/install.sh" --prefix "$prefix" --python "$python_bin" >/dev/null
[[ "$(readlink "$prefix/share/murmurmark/current")" == "$first_target" ]]
[[ "$(workspace_fingerprint)" == "$before_fingerprint" ]]

upgrade_bundle="$workdir/upgrade/murmurmark-acceptance-upgrade"
mkdir -p "$(dirname "$upgrade_bundle")"
cp -R "$bundle_a" "$upgrade_bundle"
printf '\nAcceptance-only upgrade payload.\n' >>"$upgrade_bundle/CHANGELOG.md"
rm -f "$upgrade_bundle/release-manifest.json"
manifest_fields="$("$python_bin" - "$bundle_a/release-manifest.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["version"])
print(payload["git_commit"] + "u")
print(payload["source_date_epoch"])
print(payload["target_architecture"])
PY
)"
"$python_bin" "$upgrade_bundle/scripts/release-bundle.py" finalize "$upgrade_bundle" \
  --version "$(sed -n '1p' <<<"$manifest_fields")" \
  --git-commit "$(sed -n '2p' <<<"$manifest_fields")" \
  --source-date-epoch "$(sed -n '3p' <<<"$manifest_fields")" \
  --architecture "$(sed -n '4p' <<<"$manifest_fields")" \
  --dirty >/dev/null
HOME="$isolated_home" MURMURMARK_PYTHON="$python_bin" \
  "$upgrade_bundle/install.sh" --prefix "$prefix" --python "$python_bin" >/dev/null
second_target="$(readlink "$prefix/share/murmurmark/current")"
[[ "$second_target" != "$first_target" ]]
[[ "$(workspace_fingerprint)" == "$before_fingerprint" ]]

corrupt_bundle="$workdir/corrupt/murmurmark-corrupt-upgrade"
mkdir -p "$(dirname "$corrupt_bundle")"
cp -R "$upgrade_bundle" "$corrupt_bundle"
printf '\ncorrupt after manifest\n' >>"$corrupt_bundle/README.md"
corrupt_status=0
if HOME="$isolated_home" MURMURMARK_PYTHON="$python_bin" \
  "$corrupt_bundle/install.sh" --prefix "$prefix" --python "$python_bin" \
  >"$workdir/corrupt-install.txt" 2>&1; then
  corrupt_status=0
else
  corrupt_status=$?
fi
[[ "$corrupt_status" -ne 0 ]]
grep -q 'mismatch' "$workdir/corrupt-install.txt"
[[ "$(readlink "$prefix/share/murmurmark/current")" == "$second_target" ]]
[[ "$(workspace_fingerprint)" == "$before_fingerprint" ]]

missing_required_status=0
if (
  cd "$workspace"
  HOME="$isolated_home" \
  PATH="/usr/bin:/bin" \
  MURMURMARK_PYTHON="$python_bin" \
    "$installed" doctor --strict
) >"$workdir/missing-required.txt" 2>&1; then
  missing_required_status=0
else
  missing_required_status=$?
fi
[[ "$missing_required_status" -ne 0 ]]
grep -q '^\[fail\] ffmpeg: not found in PATH$' "$workdir/missing-required.txt"
grep -q 'hint: brew install ffmpeg' "$workdir/missing-required.txt"
grep -q '^\[fail\] whisper-cli: not found in PATH$' "$workdir/missing-required.txt"

if [[ -z "$report_path" ]]; then
  report_path="$repo_root/dist/release-quality-acceptance.json"
fi
mkdir -p "$(dirname "$report_path")"
REPORT="$report_path" \
ARCHIVE="$archive_a" \
ARCHIVE_SHA="$archive_sha" \
WORKSPACE_FINGERPRINT="$before_fingerprint" \
FIRST_TARGET="$first_target" \
SECOND_TARGET="$second_target" \
"$python_bin" - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

payload = {
    "schema": "murmurmark.release_quality_acceptance/v1",
    "status": "passed",
    "completed_at": dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "artifact": {
        "path": os.environ["ARCHIVE"],
        "sha256": os.environ["ARCHIVE_SHA"],
        "deterministic_rebuild": True,
    },
    "checks": {
        "manifest_and_full_checksums": "passed",
        "clean_install": "passed",
        "doctor_strict_offline": "passed",
        "optional_models_fail_open": "passed",
        "self_test_evidence_handoff_v2_and_guarded_export": "passed",
        "idempotent_reinstall": "passed",
        "upgrade_switch": "passed",
        "corrupt_upgrade_rollback": "passed",
        "required_dependency_diagnostics": "passed",
        "whisper_model_sha256": "passed",
        "workspace_immutability": "passed",
    },
    "workspace_fingerprint": os.environ["WORKSPACE_FINGERPRINT"],
    "first_target": os.environ["FIRST_TARGET"],
    "upgraded_target": os.environ["SECOND_TARGET"],
}
Path(os.environ["REPORT"]).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

echo "release_quality_acceptance:"
echo "  deterministic_archive: ok"
echo "  clean_install: ok"
echo "  doctor_strict_offline: ok"
echo "  self_test_handoff_export: ok"
echo "  idempotent_reinstall: ok"
echo "  upgrade_workspace_immutability: ok"
echo "  corrupt_upgrade_rollback: ok"
echo "  required_dependency_diagnostics: ok"
echo "  whisper_model_sha256: ok"
echo "  archive_sha256: $archive_sha"
echo "  report: $report_path"
echo "status: passed"
