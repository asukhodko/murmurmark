# ADR-0002: Do Not Use Virtual Audio Devices by Default

Status: accepted  
Date: 2026-06-22

## Context

Virtual devices can route audio, but they make meeting setup fragile and can interfere with normal input/output behavior.

## Decision

MurmurMark v1 must not require a virtual microphone or virtual speaker by default.

BlackHole, Loopback and Aggregate Device routing may exist only as explicit manual fallback modes.

## Consequences

Benefits:

- fewer broken-call scenarios;
- lower setup burden;
- no meeting app audio settings change;
- cleaner user trust story.

Costs:

- native capture implementation is harder;
- older macOS versions may be unsupported or need fallback.

## Alternatives

- BlackHole + Audio MIDI Setup.
- Loopback as required dependency.
- OBS-only workflow.

These remain useful for experiments and emergency fallback.

