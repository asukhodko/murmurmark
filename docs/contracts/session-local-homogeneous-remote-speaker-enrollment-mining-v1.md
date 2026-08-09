# Session-Local Homogeneous Remote Speaker Enrollment Mining v1

## Purpose

This contract tests whether Coverage v3 enrollment can be replaced by several speaker-homogeneous
remote windows mined inside the same session. It is a shadow experiment. Coverage v3, selected
transcripts, raw CAF, Echo Guard and primary ASR remain authoritative and immutable.

## Stages

1. `prepare` reads only Coverage v3 attributed turn boundaries, anonymous profile IDs and remote
   audio. Text, names, cross-session voices and development outcomes are not inputs.
2. ECAPA and WavLM embed deterministic four-second windows from different turns.
3. A profile needs a joint 3-5 window clique, session-local impostor separation and both-model
   agreement.
4. `freeze` seals the candidate pack and every input hash before development truth is opened.
5. `evaluate` measures the frozen pack on the existing 33-item direct-truth development set.

## Schemas

- Policy: `murmurmark.session_local_homogeneous_remote_speaker_enrollment_policy/v1`.
- Private inventory: `murmurmark.session_local_homogeneous_remote_speaker_candidate/v1` JSONL.
- Frozen pack: `murmurmark.session_local_homogeneous_remote_speaker_enrollment_pack/v1`.
- Evaluation: `murmurmark.session_local_homogeneous_remote_speaker_enrollment_evaluation/v1`.
- Public report: `murmurmark.session_local_homogeneous_remote_speaker_enrollment_report/v1`.
- Replay: `murmurmark.session_local_homogeneous_remote_speaker_enrollment_replay/v1`.

Private artifacts may contain session IDs, intervals and embeddings. The public pack exposes only
session aliases, anonymous speaker IDs and counts.

## Terminal Outcomes

- `HOMOGENEOUS_ENROLLMENT_READY`: every frozen development gate passed; only a separate monotonic
  candidate may be opened.
- `KEEP_EXISTING_ENROLLMENT`: evidence is complete, but the candidate is not safer and useful enough.
- `EVIDENCE_BOUND`: frozen sources, local models or provenance cannot be verified.

No outcome from this contract promotes a transcript profile or permits disjoint truth collection.

## Safety

- Missing, silent, mixed, conflicting or model-disagreed evidence abstains.
- One-profile sessions mark impostor separation as not applicable; they never borrow voices from
  another session.
- Threshold search and post-hoc tuning are forbidden.
- Development clips are inaccessible to `prepare` and `freeze` by phase contract.
- Raw audio and every production guard are SHA-256 verified and never written.
