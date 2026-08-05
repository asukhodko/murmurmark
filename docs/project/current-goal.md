# Current Goal

Status: current

Updated: 2026-08-05

The stable product path remains `murmurmark meeting -> first Ctrl-C -> final result`. Raw CAF and
batch output are authoritative. Speaker-Preserving Neural Echo v2 remains the guarded production
audio profile. Evidence Notes And Export v2 is complete and now provides the stable product
handoff between processing, inspection, export and retention.

Roadmap status and dependencies live in
`docs/roadmap/murmurmark-cli-roadmap.plan.yaml`. `scripts/check-planning-consistency.py` keeps the
README, roadmap and OpsKarta wording aligned.

## Release-quality CLI

OpsKarta nearest goal: Release-quality CLI: превратить проверенный локальный pipeline и Evidence Handoff v2 в устанавливаемый, документированный и воспроизводимый CLI-релиз; зафиксировать поддерживаемое окружение, зависимости, модели и конфигурацию, добавить clean-install и upgrade acceptance, собрать release artifact и публичный operational contract, подтвердить end-to-end meeting без обязательного UI.

## Why This Is Next

The product path is already useful, local and evidence-backed. Evidence Handoff v2 now binds the
selected transcript, verdict, review burden and notes into one immutable fingerprint. On the
current 110-session corpus all manifests are valid, referential-integrity and deterministic-replay
failures are zero, and the strict corpus gate passes. Ten sessions are directly exportable; the
remaining sessions fail closed as `review_required` or `blocked` instead of publishing uncertain
material.

The largest remaining product risk is distribution rather than another processing profile. A new
user still needs repository knowledge, local build steps, model placement and several environment
assumptions. The supported platform and upgrade behavior are not yet expressed as one release
contract.

## Objective

Produce a versioned MurmurMark CLI release that can be installed, diagnosed, upgraded and used for
an end-to-end meeting through documented commands. The release must preserve current session data,
the fail-closed handoff contract and local-first privacy defaults.

## Intended Contract

```text
release artifact + dependency/model manifest + supported macOS contract
  -> install or upgrade
  -> doctor --strict + self-test + acceptance
  -> murmurmark meeting
  -> Evidence Handoff v2
  -> guarded export or one explicit blocker
```

## Required Work

1. Define the supported macOS, Swift, Python, whisper.cpp and optional local-model matrix.
2. Make installation idempotent and verify the installed executable, project home and script
   runtime without relying on the developer shell.
3. Add a versioned dependency/model manifest with paths, checksums or explicit compatibility
   checks and actionable `doctor` diagnostics.
4. Define configuration schema/versioning and prove upgrade compatibility for existing config and
   session packages.
5. Build a reproducible release artifact with version, checksum, license inventory and release
   notes.
6. Add isolated clean-install, upgrade, offline, no-optional-model and end-to-end acceptance tests.
7. Make failures leave durable diagnostics and one recovery command; never corrupt an existing
   session or successful Evidence Handoff v2.
8. Reconcile README, installation guide, runbooks, contracts, security/privacy text, changelog,
   roadmap and OpsKarta with the measured release behavior.
9. Exercise the release artifact, not only the repository build, in the final acceptance path.

## Safety Boundary

- no cloud dependency or automatic upload;
- no destructive migration of raw CAF, configs or existing session artifacts;
- no bypass of Evidence Handoff v2 review and integrity gates;
- optional heavy models may improve diagnostics but cannot be required for basic installation;
- installation and upgrade are transactional or fail without replacing the last working binary;
- UI, signing and notarization are not required to prove the CLI release contract.

## Acceptance Gates

- a clean isolated environment installs the documented release artifact and passes `doctor
  --strict`, `self-test` and non-live acceptance;
- an existing installation upgrades without changing config semantics or session fingerprints;
- the installed binary can process a fixture through Evidence Handoff v2 and guarded export fully
  offline;
- missing required dependencies produce actionable failures; missing optional models produce
  bounded warnings and preserve the core path;
- release artifact bytes, checksum, version and dependency manifest agree;
- repository and packaged acceptance exercise the same user-facing commands;
- privacy, absolute-path, secret, license, open-source and documentation checks pass;
- current capture, processing, handoff, review, export and retention regressions remain green.

## Definition Of Done

- the supported environment and compatibility policy are explicit;
- installation, upgrade, diagnostics and release assembly are implemented and tested;
- one versioned release artifact is reproducibly built and verified;
- a fresh end-to-end fixture run succeeds from the packaged CLI without repository-only shortcuts;
- README, contracts, runbooks, current goal, roadmap and OpsKarta describe measured behavior;
- full static, Swift, privacy, open-source, planning and acceptance checks pass;
- changes are committed, pushed to `origin/main`, and the worktree is clean.

## Outside This Goal

- another echo model or target-speaker separator;
- remote diarization inside `Colleagues`;
- cloud or local generative LLM synthesis;
- direct Jira/docs mutation;
- UI or menu-bar application;
- promotion of Live Shadow to authoritative output;
- mandatory signing, notarization or an installer application.

## Deferred Audio Research

A future Target-Me attempt requires a pretrained target-speaker extraction representation or a
larger multilingual speaker-query corpus. Reference-Conditioned v2 rules out repeating the same
small spectral mask on the current corpus.
