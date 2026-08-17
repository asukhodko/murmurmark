# Changelog

All notable user-facing changes are recorded here. MurmurMark follows semantic versioning once a
public release tag exists; development builds also carry the source commit in their manifest.

## Unreleased

- Added a private remote-speaker cluster-purity audit with reproducible aggregate metrics, explicit
  session-local acoustic-cluster semantics and an exact `transcript --aggregate` fallback.
- Bound reviewed transcript profiles to their source dialogue and quality-report fingerprints so a
  regenerated base transcript cannot silently reuse stale review artifacts.
- Added private `retired_sessions/v1` overlays so superseded frozen experiments can release stale
  automatic pins while explicit current-development pins continue to protect active corpora.
- Completed the frozen disjoint remote-speaker truth v2 review: 72 primary and 12 repeat decisions,
  perfect repeat consistency and byte-exact replay now unlock one-shot qualification of a new local
  speaker model without changing production attribution.
- Added guarded `transcript_only` session compaction. Successful `meeting`/`finish` handoff now
  removes raw and rebuildable audio unless `--keep-debug-artifacts` is set; pinned corpus sessions
  remain protected.
- Added an optional session roster and fail-open two-backend consensus for repairing one
  acoustically split remote speaker without inferring human names.
- Added a fingerprint-bound v2.17 compatibility qualification for the personalized pre-ASR echo
  selector after authoritative incremental ASR changes. Incompatible policies now expose their
  exact FIR fallback in readiness, status and outcome instead of degrading silently.
- Fixed residual remote-in-`Me` reporting to preserve positive transcript/audio-review evidence
  when an overlap audit reports zero or the remote-forbidden audit is incomplete.
- Added a fingerprint-bound optional anonymous-speaker transcript with deterministic corpus gates
  and the explicit `murmurmark transcript SESSION --rich` read path.
- Added explicit session-local reviewed speaker labels, transactional immutable publication and
  fail-open `murmurmark transcript SESSION --rich --reviewed-speakers` access.

## 0.1.0 - 2026-08-05

- Added durable two-track local capture and the one-command meeting lifecycle.
- Added guarded Echo Guard, transcription repair, review, Evidence Handoff v2, export and retention.
- Added deterministic release archives, a versioned compatibility contract and license inventory.
- Added transactional clean install and upgrade with integrity verification and rollback.
- Added packaged offline acceptance for `doctor --strict`, self-test, Evidence Handoff v2 and
  guarded export.
- Added exact, corruption-safe ASR window resume with byte-identical replay; incompatible live
  chunks now fall back to ordinary batch recognition instead of entering the authoritative cache.
