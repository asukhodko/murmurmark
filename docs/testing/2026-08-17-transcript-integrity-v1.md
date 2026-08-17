# Transcript Integrity v1 Qualification

Decision: `PROMOTE`.

The frozen three-session qualification found 19 candidates and safely applied 10 repairs:

| Repair class | Applied |
|---|---:|
| Adjacent boundary overlap | 4 |
| Adjacent contained duplicate | 1 |
| Adjacent fuzzy suffix duplicate | 1 |
| Internal exact repeat | 3 |
| Unsupported decoder loop | 1 |

Nine candidates remained explicit review items. All current input and output fingerprints, raw
capture fingerprints, roles, timestamps and retained utterance lineage passed. Judge-unavailable
fixtures remained review-only, intentional repeated speech was not removed, and repeated runs were
byte-stable.

The latest regression transcript removed a silent seven-copy decoder hallucination, three internal
decoder repeats and two adjacent segmentation duplicates. One adjacent exact repeat remained
unresolved because independent local ASR did not prove that it was mechanical.

The public corpus report contains only anonymous session slots and aggregate metrics. Previous
profiles and speaker evidence remain the fallback; downstream speaker-resolved selection is rebuilt
from the promoted aggregate profile.
