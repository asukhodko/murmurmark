# Speaker-Resolved Transcript Terminal Gate Instrument v1

Date: 2026-08-21

## Result

The fingerprint-bound instrument is `TERMINAL_GATE_INSTRUMENT_READY`; current product decision is
`NOT_READY`. Byte-exact replay passes and the public report contains no session IDs, speech, names
or absolute paths.

| Dimension | State | Current evidence |
|---|---:|---|
| durable capture | bounded | no-restart soak passes; controlled restart and current corpus retain measured source gaps |
| Target-Me preservation | bounded | production local preservation/fallback pass; 4 rows / 21.12s remain |
| lexical accuracy | blocked | Human-Reviewed Lexical Seed is `REVIEW_REQUIRED`, 0/28 |
| chronology and conservation | bounded | words/roles pass; two evidence layers closed 308.8 of 345.94s, leaving 37.14s |
| remote speaker attribution | bounded | direct truth v2 passes; fresh corpus lacks direct speaker-count truth |
| explicit unknown | bounded | 3.7874% words pass the 5% bound; 8.4747% seconds do not |
| review burden | pass | 83.27s / 14423.865s = 0.5773%, below 3% |
| publication and fallback | pass | 4 strict + 2 provisional; no aggregate-only session |

The result does not authorize transcript or audio changes. It turns the remaining work into separate,
non-compensating product blockers.

The instrument now reads 10 fingerprint-bound reports for eight independent dimensions.
Speaker-Bounded Chronology Evidence Arbitration v1 is a separate source: its promoted result closes
38/52 false-positive or expected overlap rows without changing the transcript. Word-level
localization then closed 9/14 residual rows; the 5-row / 37.14s
remainder stays bounded.

During the full regression run, the instrument exposed a Remote Unknown Recovery report whose file
was current but whose private manifest referenced the previous rebaseline SHA. The source was
requalified on the current corpus. Regression coverage now changes only `explicit_unknown` to
`not_measured` on this transitive drift; unrelated dimensions remain measurable.
