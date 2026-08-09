# Session-Local Homogeneous Remote Speaker Enrollment Mining v1 Result

Date: 2026-08-09

Decision: `KEEP_EXISTING_ENROLLMENT`.

## Result

- Six real sessions and 14 anonymous session-local profiles were frozen.
- Every profile had at least 15 distinct attributed turns longer than four seconds.
- The miner evaluated up to 12 four-second windows per profile.
- ECAPA and independent WavLM selected 39 windows for 9/14 qualified profiles.
- The candidate pack was frozen as SHA-256
  `840fbb24fd001fc341208566dae525beaef5b711a383404a87d5958b910fc0ec` before development truth.
- On 33 direct-truth items the candidate preserved 0/3 confirmed v1 gains, produced 5 unsafe
  accepts, introduced 4 new false identities and lost 3 correct control identities.
- Deterministic replay passed; 68 Coverage v3 accepts and 355 production guards remained intact.

## Interpretation

Longer, internally homogeneous enrollment material exists, but label-conditioned mining does not
solve identity attribution. The same failure survives two embedding families and stricter local
impostor checks. The likely remaining ambiguity is between the Coverage-derived profile mapping and
the actual session-local voice clusters, rather than a lack of clean audio windows alone.

The next bounded experiment is label-independent session-local re-clustering. It must measure cluster
geometry before attempting to align clusters to existing profile IDs. No new identity candidate or
disjoint truth is justified by this result.
