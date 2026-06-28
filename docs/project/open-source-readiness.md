# Open-Source Readiness

MurmurMark is moving toward a public CLI-first repository. The current readiness
work is focused on preventing private meeting material or workspace-specific
data from entering tracked files.

## Current Gate

Run:

```bash
scripts/check-open-source-readiness.sh
```

The gate fails when tracked files include:

- real session, recording or export directories;
- raw audio or video files;
- local runtime directories such as `.venv`, `.build`, `models` or `weights`;
- `murmurmark.config.json`;
- private domain prompts or glossaries;
- domain packs outside `examples/domain-packs/example-domain/`;
- personal absolute paths, private ChatGPT links, common secret names or private
  key headers;
- tracked files larger than 5 MiB.

The gate also checks that a repository license file exists. MurmurMark currently
uses the MIT license.

For repository-specific private words, create an ignored local file:

```text
.murmurmark-readiness-local-patterns
```

Each non-empty non-comment line is treated as an additional regular expression.

## Safe Examples

Tracked examples must be generic:

```text
examples/domain-packs/example-domain/
```

Workspace-specific terms, people, services and prompts belong in ignored local
files or outside the repository.

## Still Required Before Public Release

- Decide the public security contact.
- Re-run `scripts/check.sh` and `scripts/check-open-source-readiness.sh`.
- Build a release bundle with `scripts/build-release-bundle.sh --verify` and verify
  `bin/murmurmark doctor --strict`.
