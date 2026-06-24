# ADR-0001: Use Core Audio Process Taps for Remote Audio

Status: accepted  
Date: 2026-06-22

## Context

MurmurMark needs to record audio produced by a selected meeting application without becoming a virtual speaker and without changing the meeting app's audio routing.

## Decision

Use Core Audio Process Tap as the primary remote/app audio capture backend.

## Consequences

Benefits:

- audio-only capture;
- can target an application/process group;
- does not require BlackHole/Loopback;
- does not change the meeting app output device;
- fits the privacy story.

Costs:

- macOS-specific implementation;
- requires careful permission UX;
- browser/Electron process tracking is non-trivial;
- needs test matrix across macOS versions.

## Alternatives

- OBS macOS Audio Capture: useful fallback, not product foundation.
- ScreenCaptureKit: useful fallback, broader screen-capture framework.
- BlackHole/Loopback: manual fallback, not default.
- Audio Hijack: UX reference, not dependency.

