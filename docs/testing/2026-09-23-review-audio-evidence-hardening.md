# Review Audio Evidence Hardening

## Scope

Protect suggested review from ID reuse, stale audio and profile drift; keep quick evidence reusable
without falsely counting two sources as four. No changes to capture, Echo Guard, the primary ASR,
resource profile, thread limit or existing transcripts are part of this patch.

## Regressions

`scripts/check-review-audio-evidence.py` exercises real classifier/cache/apply orchestration with
temporary artifacts and a fake decoder. It checks changed text, role, time, audio, model and policy;
changed primary audio audit and speaker state, including previously absent inputs;
unchanged renumbering; split utterances; extra Me context; grouped confidence; stale receipts;
materialized versus missing local recall; voice versus lexical confirmation; padded boundaries;
cold/warm/adaptive/cache-only runs; missing sources/model; interruption and resume without decoding
completed clips again; read-only historical audit; remote-only error taxonomy.

Focused checks also cover the stronger judge, Target-Me evidence matching, review materialization,
meeting lifecycle, quality reconciliation and speaker-attributed rendering. Full verification uses
`scripts/check.sh`, including Swift build/tests, SwiftLint, Python compilation and smoke tests.

## Verification Results (2026-09-23)

- Focused evidence, stronger-judge, Target-Me, materialization, lifecycle and release-layout checks
  passed, including cold/warm cache reuse and interrupted checkpoint recovery with a fake decoder.
- The full `scripts/check.sh` run passed all stages before its final `smoke-fixture.sh`: Swift
  build and 13 tests, lint/compilation, corpus/provenance checks, pipeline/recovery/live smoke tests,
  planning consistency, release build and release acceptance (doctor, self-test, config, integrity).
- The final fixture initially failed because its import loader lacked the scripts directory and
  its old expectations allowed ID-only evidence and unsigned or manually edited suggested answers.
  Updated fixtures now reject those cases, preserve manual review and unrelated decisions, and
  leave positive sealed-suggestion coverage in the focused integration check.
- After those fixture-only corrections, the entire `scripts/smoke-fixture.sh` passed separately,
  including raw mic/remote SHA-256 invariance. The full wrapper was not rerun after these corrections.
- `git diff --check` passed. No real recording, full-session recognition rerun or throughput
  benchmark was performed; the existing session was audited read-only.

## Real-Session Audit

The inspected session has 22 applied decisions: 18 keeps and four whole-Me drops. Comparing the
input `transcript_integrity_v1` with `reviewed_v1` found exactly four removed Me utterances, zero
removed remote utterances and no added/changed utterances. All four drops have historical stronger
judge rows matching the original text, role and interval. Eight keeps lack a matching row in the
currently retained stronger-judge file; the historical workflow did not preserve sealed receipts.

This is a provenance check, not a listening verdict. It neither proves that the four removals were
correct nor justifies restoring them automatically. All 22 decisions predate the new sealed contract.
The private reproducible report lives under `sessions/_reports/review-evidence-hardening/`; no raw
audio, selected transcript or old decision was modified by this audit.

## Remaining Limits

- Full-session ASR latency is not changed by this patch. Existing pipeline step times remain the
  baseline; stronger-judge load/decode timing is now explicit.
- Legacy padded clips without a known origin, conflicting voice/text evidence and incomplete
  source coverage remain review work. Better accounting may expose more uncertainty initially.
- A model-free cache replay proves reuse behavior, not faster-whisper recognition quality or a
  measured end-to-end speedup on a fresh meeting.
- Primary-ASR instrumentation was deliberately excluded because that file is SHA-bound to the
  promoted echo-selector contract. Changing it requires separate provenance qualification.
- Attribution disclaimers are added by `transcript --cat`, without changing frozen Coverage v3
  Markdown or labels. `--path-only` still emits only the artifact path on stdout; directly reading
  that file retains its existing header. Duration-based CLI coverage explicitly names its unit.
