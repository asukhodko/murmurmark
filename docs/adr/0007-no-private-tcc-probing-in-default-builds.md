# ADR-0007: No Private TCC Probing in Default Builds

Status: accepted  
Date: 2026-06-22

## Context

macOS system-audio permission handling can tempt implementations to use private TCC probing APIs.

## Decision

Default builds must not use private TCC probing.

Permission readiness should be handled through `doctor`, controlled dry runs and clear instructions.

## Consequences

Benefits:

- cleaner open-source posture;
- lower notarization/security risk;
- less reliance on unstable private behavior.

Costs:

- permission UX may be less automatic;
- implementation must carefully explain dry-run results.

## Alternatives

- private TCC probing behind an experimental build flag;
- requiring manual setup before first run.

An experimental flag can be considered later, but not in default builds.

