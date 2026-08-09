# Stronger Local Remote Speaker Representation Qualification v1 Result

Date: 2026-08-09

## Decision

`KEEP_EXPLICIT_UNKNOWN`

WeSpeaker ResNet34-LM is materially different from the bounded ECAPA/WavLM pair and substantially
improved blind geometry on three of four multi-speaker sessions. It still failed the fixed safety
contract on the fourth session and on post-freeze open-set attribution.

## Frozen Evidence

- sessions: 6;
- session-local profiles: 14;
- blind remote windows: 347;
- direct-truth items: 33;
- frozen pack SHA-256: `00faf7ef22616f0fc9beb4849f8319e5aa7e6b6023c29d402e205221aa887653`;
- policy SHA-256: `8d293f7fe70cf8cc968494cdbeda7c6b1219c50102cb9f1452b08564da25b402`;
- model SHA-256: `7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068`.

## Geometry

- minimum silhouette: `0.263291` (pass);
- minimum stability ARI: `0.442394` (fail);
- minimum best-control ARI, informational: `0.189378`;
- minimum best-control NMI, informational: `0.355694`;
- maximum three-model fragmentation: `2.0`.

Three multi-speaker sessions had stability ARI `0.858856..0.965527`. The remaining session reached
only `0.442394`, so the improvement was not corpus-wide.

## Post-Freeze Safety

- minimum cluster purity: `0.25`;
- minimum mapping margin: `0.0`;
- ambiguous clusters: 6;
- confirmed gains preserved: `3/3`;
- correct control identities lost: 0;
- unsafe accepts: 17;
- new false identities: 12;
- embedding-unavailable items: 2.

The useful gains are real, but accepting them would also introduce substantially more false speaker
identity. Explicit unknown remains the safer production result.

## Consequence

The tested lightweight route is closed: SpeechBrain ECAPA, WavLM and WeSpeaker ResNet34-LM do not
provide a uniformly stable and open-set-safe fixed-window representation on this corpus. The next
large experiment should qualify a temporal/end-to-end remote diarization backend with the same
freeze-first discipline.
