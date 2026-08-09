# Remote Speaker Enrollment Purity and Abstention Hardening v2 Result

Date: 2026-08-09
Decision: `KEEP_COVERAGE_V3`

## Frozen Result

- Scope: 278 residual items / 851 words; all 68 Coverage v3 accepts preserved.
- Provenance: 14/14 enrollment sources, 65/65 review artifacts and 355/355 production guards.
- Purity: 84 two-second requests, 82 embeddings and two fail-closed silent-window errors.
- Profiles: 7 qualified and 7 rejected; one profile also failed pairwise purity.
- Candidate additions: 0 items / 0 words / 0 seconds.
- Development truth: 0/3 confirmed v1 gains preserved; no lost control identity and no new false
  identity.
- Unsafe accepts: 8 for Coverage v3, 13 for v1 and 8 for v2.
- Conservation and replay: exact.

## Interpretation

The monotonic candidate solved the safety regression by abstaining, but produced no useful gain.
The existing enrollment material is the limiting factor: half of the profiles have no sufficiently
coherent core spanning both exemplars. Loosening gates on the same 33 direct-truth rows would be
post-hoc fitting and is prohibited.

Coverage v3 remains production. The next experiment is **Session-Local Homogeneous Remote Speaker
Enrollment Mining v1**: mine several longer, speaker-bounded, mutually consistent windows per
session-local profile, freeze them, and only then retry an additive identity candidate. No new human
review is needed until that candidate passes development gates.
