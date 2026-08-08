# Remote Speaker Attribution Error Decomposition v1 Result

Date: 2026-08-08

Decision: `ADVANCE_STRONGER_SPEAKER_IDENTITY`.

## Frozen Scope

- input freeze: `710a0f6e9e2b7f0645d4974cb3557c61b80bd667a61ab6c4da8bd4ac8a3cebb8`;
- three exact corpora: Truth Lab v1 hard, once-opened hard-v2 and once-opened hard-v3;
- 393 words, including 338 known, 32 open-set and 23 mixed;
- 64 evaluated boundaries;
- five existing primary/control prediction tracks;
- hard-v2 and hard-v3 opening ledgers remained completed exactly once.

No threshold or candidate was selected in this goal. Coverage v3, selected transcripts, raw CAF,
Echo Guard and primary ASR were unchanged.

## Oracle Result

| Track | Known recall | Boundary recall | B-cubed F1 | Open-set false |
|---|---:|---:|---:|---:|
| Current primary evidence | 0.571006 | 0.421875 | 0.601933 | 2 |
| Oracle boundaries + current identity | 0.627219 | 0.500000 | 0.670265 | 2 |
| Current boundaries + oracle identity | 0.934911 | 0.750000 | 0.886021 | 2 |
| Overlap/open-set oracle | 0.571006 | 0.437500 | 0.601933 | 0 |
| Full oracle control | 1.000000 | 1.000000 | 1.000000 | 0 |

The fixed routing gains were:

- segmentation: `0.063882`;
- speaker identity: `0.351382`;
- overlap/open-set: `0.036364`.

Speaker identity exceeds the material-gain threshold and both alternatives by more than the fixed
dominance margin. The next experiment must therefore change the identity backend rather than tune
the rejected duration or segment-context thresholds again.

## Per-Corpus Evidence

| Corpus | Current known recall | Current boundary recall | Identity-oracle known recall | Identity-oracle boundary recall |
|---|---:|---:|---:|---:|
| Truth Lab v1 hard | 0.982759 | 1.000000 | 1.000000 | 1.000000 |
| hard-v2 | 0.551402 | 0.321429 | 0.925234 | 0.750000 |
| hard-v3 | 0.445087 | 0.100000 | 0.919075 | 0.550000 |

The direction is consistent: the established Coverage v3 control already works on the original
hard split, while both disjoint harder corpora lose most known words through identity abstention or
misidentification. Better boundaries alone recover little.

## Metric Normalization

Boundary truth now uses the word's normalized class: enrolled speaker ID, `unknown_speaker` for an
unseen voice and `mixed` for mixed speech. This removes an earlier inconsistency where hard-v2 counted
safe open-set transitions but hard-v3 compared them with an unreachable private open-set ID.

With the normalized rule, unchanged hard-v3 predictions recover two safe open-set transitions and
its current boundary recall is `0.10`, not the historical `0.00`. No prediction or upstream decision
was rewritten.

## Contract Corrections Before Freeze

The first attempted freeze was rejected before metrics because the hard-v3 Coverage v3 control does
not expose `segment_index`; its current partition is now correctly represented as contiguous label
runs. A later integration check removed the downstream Transcript Perfection manifest from production
guards to avoid an input-output hash cycle. Both corrections left oracle rules, decision thresholds,
metrics and the final decision unchanged. The final freeze above is the only accepted input freeze.

## Reproducibility

- every exact word, timestamp and evaluated boundary is counted once;
- full oracle reaches every reference gate;
- private word and boundary decompositions carry truth, prediction and frozen-manifest SHA-256;
- deterministic replay is byte-identical;
- public outputs contain no text, renderer voice or absolute private path;
- Transcript Perfection verifies 18/18 frozen sources.

## Next

`Stronger Remote Speaker Identity Backend Qualification v1` should compare a small predeclared set of
genuinely different local speaker-verification backends on existing development truth. One candidate
may then be frozen and opened once on a new disjoint hard-v4. Segment boundaries, open-set abstention
and production Coverage v3 remain fixed controls during that qualification.
