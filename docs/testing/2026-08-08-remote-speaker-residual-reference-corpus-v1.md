# Remote Speaker Residual Reference Corpus v1 Result

Date: 2026-08-08  
Decision: `REFERENCE_INSUFFICIENT`

## Result

The blind private review pack was materialized for the complete six-session Coverage v3 residual:

| Metric | Result |
|---|---:|
| Residual words | 851 |
| Coverage v3 residual seconds | 598.239509 |
| Word-bound referenceable seconds | 597.799509 |
| Explicit unaligned accounting gap | 0.440000 |
| Blind review items | 278 |
| Session-local speaker exemplars | 28 |
| WavLM proposal words | 53 |
| WavLM proposal seconds | 23.356997 |
| Human/exact reviewed items | 0 |
| Directly referenced proposal words | 0 |

All structural gates pass: six-session scope, unique word coverage, exact residual accounting,
proposal identity, blind prediction separation, source hashes, raw-audio preservation and selected
transcript preservation. Readiness fails only on the four truth gates: reviewed proposal coverage,
direct proposal coverage, minimum attributable words and candidate precision.

## Interpretation

The engineering path is now explicit. WavLM recovered a small candidate set, but there is no
independent evidence that its proposed speaker labels are correct. Lowering thresholds or tuning the
same meetings would optimize model agreement rather than correctness.

The pack remains useful private material for later blind review. Until then, Coverage v3 remains the
authoritative speaker-resolved source and all unsupported words stay aggregate `Colleagues`.

## Reproduction

```bash
murmurmark corpus remote-reference build
murmurmark corpus remote-reference replay
murmurmark corpus perfection all --verify-existing
```

No raw CAF, selected transcript, Coverage v3 output, primary ASR output or Echo Guard artifact was
modified.

