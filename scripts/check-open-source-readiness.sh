#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

failures=0
warnings=0

fail() {
  echo "[fail] $*" >&2
  failures=$((failures + 1))
}

warn() {
  echo "[warn] $*" >&2
  warnings=$((warnings + 1))
}

tracked="$(mktemp "${TMPDIR:-/tmp}/murmurmark-tracked.XXXXXX")"
trap 'rm -f "$tracked"' EXIT
git ls-files >"$tracked"

check_no_tracked_path() {
  local pattern="$1"
  local message="$2"
  if grep -E "$pattern" "$tracked" >/dev/null; then
    fail "$message"
    grep -E "$pattern" "$tracked" >&2
  fi
}

check_no_tracked_path '(^|/)(sessions|recordings|exports/private|\.venv|venv|\.build|DerivedData|build|dist|models|weights)(/|$)' \
  "private/runtime directory is tracked"
check_no_tracked_path '(^|/)murmurmark\.config\.json$' \
  "local config is tracked"
check_no_tracked_path '\.(caf|wav|flac|m4a|mp3|mp4|mkv)$' \
  "raw or rendered audio/video file is tracked"
non_example_domain_packs="$(grep '^examples/domain-packs/' "$tracked" | grep -v '^examples/domain-packs/example-domain/' || true)"
if [[ -n "$non_example_domain_packs" ]]; then
  fail "only examples/domain-packs/example-domain may be tracked"
  echo "$non_example_domain_packs" >&2
fi
check_no_tracked_path '^examples/domain-packs/.*/(glossary\.yaml|whisper-prompt.*\.txt)$' \
  "domain glossary/prompt files may contain private vocabulary and must stay ignored"

content_patterns=(
  '/Users/[^[:space:]]+'
  'chatgpt\.com/(c|share)/'
  'OPENAI_API_KEY'
  'HF_TOKEN'
  'BEGIN (RSA|OPENSSH) PRIVATE KEY'
)

extra_patterns_file="${MURMURMARK_READINESS_EXTRA_PATTERNS:-.murmurmark-readiness-local-patterns}"
if [[ -f "$extra_patterns_file" ]]; then
  while IFS= read -r pattern; do
    [[ -n "$pattern" ]] || continue
    [[ "$pattern" == \#* ]] && continue
    content_patterns+=("$pattern")
  done <"$extra_patterns_file"
fi

while IFS= read -r pattern; do
  [[ -n "$pattern" ]] || continue
  if git ls-files -z |
    grep -z -v '^scripts/check-open-source-readiness\.sh$' |
    xargs -0 rg -n "$pattern" >/tmp/murmurmark-readiness-rg.out 2>/dev/null; then
    fail "tracked content matches private pattern: $pattern"
    sed -n '1,40p' /tmp/murmurmark-readiness-rg.out >&2
  fi
done < <(printf '%s\n' "${content_patterns[@]}")
rm -f /tmp/murmurmark-readiness-rg.out

large_files="$(while IFS= read -r path; do
  [[ -f "$path" ]] || continue
  size="$(stat -f%z "$path")"
  if [[ "$size" -gt 5242880 ]]; then
    printf '%s %s\n' "$size" "$path"
  fi
done <"$tracked")"
if [[ -n "$large_files" ]]; then
  fail "tracked file larger than 5 MiB"
  echo "$large_files" >&2
fi

for required in README.md CONTRIBUTING.md SECURITY.md .gitignore scripts/check.sh scripts/check-open-source-readiness.sh; do
  if [[ ! -f "$required" ]]; then
    fail "missing required public project file: $required"
  elif ! git ls-files --error-unmatch "$required" >/dev/null 2>&1; then
    fail "required public project file is not tracked: $required"
  fi
done

if ! compgen -G "LICENSE*" >/dev/null; then
  warn "no LICENSE file yet; choose a license before public release"
fi

if [[ "$failures" -gt 0 ]]; then
  echo "open-source readiness: failed ($failures failures, $warnings warnings)" >&2
  exit 1
fi

echo "open-source readiness: ok ($warnings warnings)"
