# Speaker-Bounded Chronology Evidence Arbitration v1 Result

Date: 2026-08-21
Decision: `PROMOTE_CHRONOLOGY_EVIDENCE_ARBITRATION_V1`

## Result

The six-session rebaseline contained 52 blocking chronology rows / `345.94s`. All rows had exact
group-overlap matches; 44 also had local faster-whisper judge evidence.

| Outcome | Rows | Seconds | Terminal treatment |
|---|---:|---:|---|
| `benign_turn_boundary` | 34 | 229.52 | closed |
| `confirmed_double_talk` | 4 | 26.45 | closed |
| `insufficient_evidence` | 10 | 82.38 | remains |
| `remote_leak_or_asr_segmentation` | 2 | 4.17 | remains |
| `true_chronology_risk` | 2 | 3.42 | remains |

Closure is 38/52 rows (`73.0769%`) and `255.97/345.94s` (`73.9926%`), above both 50% promotion
gates. The terminal chronology residual is 14 rows / `89.97s`.

## Safety

The stage made no transcript patches. Selected text, roles, timestamps, profiles, raw CAF, Echo
Guard and primary ASR remain unchanged. Public report and tracked snapshot contain no session IDs,
speech or absolute paths. Every decision retains full private provenance and replay is byte-exact.

Synthetic regression covers one benign boundary, one genuine double-talk, one transferred
remote-leak case and one true order risk. It also verifies stale-input failure, privacy and the
absence of selected-dialogue mutation. Terminal-gate regression verifies that a zero-byte frozen
artifact remains a valid fingerprint and that only the arbitration remainder enters chronology.
