#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
default_bundle="$(cd "$script_dir/.." && pwd)"
bundle_root="$default_bundle"
prefix="${MURMURMARK_PREFIX:-$HOME/.local}"
python_bin="${MURMURMARK_PYTHON:-}"

usage() {
  cat <<'EOF'
usage: scripts/install-release.sh [--bundle DIR] [--prefix DIR] [--python PATH]

Verifies and transactionally installs an extracted MurmurMark release bundle.
Releases are immutable under PREFIX/share/murmurmark/releases. The `current`
symlink is replaced atomically only after bundle verification and self-test.

User config, sessions and exports stay in the caller's workspace and are never
copied into or rewritten by the installer.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bundle)
      [[ $# -ge 2 ]] || { echo "error: --bundle requires a path" >&2; exit 2; }
      bundle_root="$2"
      shift 2
      ;;
    --prefix)
      [[ $# -ge 2 ]] || { echo "error: --prefix requires a path" >&2; exit 2; }
      prefix="$2"
      shift 2
      ;;
    --python)
      [[ $# -ge 2 ]] || { echo "error: --python requires a path" >&2; exit 2; }
      python_bin="$2"
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

bundle_root="$(cd "$bundle_root" && pwd)"
prefix="$(mkdir -p "$prefix" && cd "$prefix" && pwd)"
if [[ -z "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" || ! -x "$python_bin" ]]; then
  echo "error: Python 3 is required to verify and install the release" >&2
  echo "hint: pass --python /path/to/python3 or set MURMURMARK_PYTHON" >&2
  exit 1
fi
if [[ ! -x "$bundle_root/scripts/release-bundle.py" ]]; then
  echo "error: release verifier is missing: $bundle_root/scripts/release-bundle.py" >&2
  exit 1
fi

"$python_bin" "$bundle_root/scripts/release-bundle.py" verify "$bundle_root" >/dev/null

metadata="$("$python_bin" - "$bundle_root/release-manifest.json" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["release_id"])
print(payload["version"])
print(payload["package_fingerprint"])
PY
)"
release_id="$(sed -n '1p' <<<"$metadata")"
version="$(sed -n '2p' <<<"$metadata")"
package_fingerprint="$(sed -n '3p' <<<"$metadata")"

install_root="$prefix/share/murmurmark"
releases_root="$install_root/releases"
destination="$releases_root/$release_id"
current_link="$install_root/current"
bin_dir="$prefix/bin"
wrapper="$bin_dir/murmurmark"
stage="$releases_root/.stage-$release_id-$$"
next_link="$install_root/.current-next-$$"
wrapper_next="$bin_dir/.murmurmark-next-$$"
wrapper_backup="$bin_dir/.murmurmark-previous-$$"
test_workspace="$(mktemp -d "${TMPDIR:-/tmp}/murmurmark-release-install.XXXXXX")"
old_target=""
if [[ -L "$current_link" ]]; then
  old_target="$(readlink "$current_link")"
fi

cleanup() {
  rm -rf "$stage" "$test_workspace"
  rm -f "$next_link" "$wrapper_next" "$wrapper_backup"
}
trap cleanup EXIT

mkdir -p "$releases_root" "$bin_dir"
if [[ -f "$wrapper" ]]; then
  cp -p "$wrapper" "$wrapper_backup"
fi
if [[ -d "$destination" ]]; then
  "$python_bin" "$destination/scripts/release-bundle.py" verify "$destination" >/dev/null
else
  mkdir -p "$stage"
  cp -R "$bundle_root/." "$stage/"
  "$python_bin" "$stage/scripts/release-bundle.py" verify "$stage" >/dev/null
  (
    cd "$test_workspace"
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    MURMURMARK_PYTHON="$python_bin" \
      "$stage/bin/murmurmark" self-test >/dev/null
  )
  if [[ "${MURMURMARK_INSTALL_TEST_FAIL_AFTER_STAGE:-0}" == "1" ]]; then
    echo "error: injected install failure after staging" >&2
    echo "recovery: the previous release remains current" >&2
    exit 1
  fi
  mv "$stage" "$destination"
fi

printf -v quoted_install_root '%q' "$install_root"
cat >"$wrapper_next" <<EOF
#!/usr/bin/env bash
set -euo pipefail
install_root=$quoted_install_root
current="\$install_root/current/bin/murmurmark"
if [[ ! -x "\$current" ]]; then
  echo "error: MurmurMark current release is unavailable: \$current" >&2
  echo "hint: reinstall the last verified release" >&2
  exit 1
fi
exec "\$current" "\$@"
EOF
chmod +x "$wrapper_next"

ln -s "releases/$release_id" "$next_link"
WRAPPER_NEXT="$wrapper_next" WRAPPER="$wrapper" "$python_bin" - <<'PY'
import os

os.replace(os.environ["WRAPPER_NEXT"], os.environ["WRAPPER"])
PY
set +e
NEXT_LINK="$next_link" CURRENT_LINK="$current_link" "$python_bin" - <<'PY'
import os

os.replace(os.environ["NEXT_LINK"], os.environ["CURRENT_LINK"])
PY
switch_status=$?
set -e
if [[ "$switch_status" -ne 0 ]]; then
  if [[ -f "$wrapper_backup" ]]; then
    mv "$wrapper_backup" "$wrapper"
  elif [[ -z "$old_target" ]]; then
    rm -f "$wrapper"
  fi
  echo "error: failed to activate the verified release" >&2
  echo "recovery: the previous release remains current" >&2
  exit 1
fi

set +e
installed_version="$(cd "$test_workspace" && MURMURMARK_PYTHON="$python_bin" "$wrapper" version 2>&1)"
post_switch_status=$?
set -e
if [[ "$post_switch_status" -ne 0 || "$installed_version" != "murmurmark $version" ]]; then
  if [[ -n "$old_target" ]]; then
    rollback_link="$install_root/.current-rollback-$$"
    ln -s "$old_target" "$rollback_link"
    ROLLBACK_LINK="$rollback_link" CURRENT_LINK="$current_link" "$python_bin" - <<'PY'
import os

os.replace(os.environ["ROLLBACK_LINK"], os.environ["CURRENT_LINK"])
PY
  else
    rm -f "$current_link"
  fi
  if [[ -f "$wrapper_backup" ]]; then
    mv "$wrapper_backup" "$wrapper"
  elif [[ -z "$old_target" ]]; then
    rm -f "$wrapper"
  fi
  echo "error: installed release failed its post-switch version check" >&2
  echo "recovery: the previous release was restored" >&2
  exit 1
fi

INSTALL_STATE="$install_root/install-state.json" \
INSTALL_HISTORY="$install_root/install-history.jsonl" \
RELEASE_ID="$release_id" \
VERSION="$version" \
PACKAGE_FINGERPRINT="$package_fingerprint" \
PREVIOUS_TARGET="$old_target" \
PREFIX_VALUE="$prefix" \
"$python_bin" - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

event = {
    "schema": "murmurmark.release_install_event/v1",
    "status": "installed",
    "release_id": os.environ["RELEASE_ID"],
    "version": os.environ["VERSION"],
    "package_fingerprint": os.environ["PACKAGE_FINGERPRINT"],
    "previous_target": os.environ.get("PREVIOUS_TARGET") or None,
    "prefix": os.environ["PREFIX_VALUE"],
    "installed_at": dt.datetime.now(tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
state = Path(os.environ["INSTALL_STATE"])
temporary = state.with_name(f".{state.name}.tmp-{os.getpid()}")
temporary.write_text(json.dumps(event, indent=2, sort_keys=True) + "\n", encoding="utf-8")
os.replace(temporary, state)
with Path(os.environ["INSTALL_HISTORY"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(event, sort_keys=True) + "\n")
PY

echo "installed: $wrapper"
echo "release: $release_id"
echo "version: $version"
echo "package_fingerprint: $package_fingerprint"
echo "runtime: $destination"
echo "workspace: external; current directory is preserved"
if [[ -n "$old_target" && "$old_target" != "releases/$release_id" ]]; then
  echo "previous: $old_target"
fi
if [[ ":$PATH:" != *":$bin_dir:"* ]]; then
  echo "path hint: export PATH=\"$bin_dir:\$PATH\""
fi
echo "next:"
echo "  cd /path/to/murmurmark-workspace"
echo "  export MURMURMARK_PYTHON=/path/to/python3"
echo "  murmurmark config init"
echo "  murmurmark doctor --strict"
echo "  murmurmark self-test"
