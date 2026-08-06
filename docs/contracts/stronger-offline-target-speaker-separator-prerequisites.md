# Stronger Offline Target-Speaker Separator Prerequisites v1

This contract prepares one local separator for a later bounded qualification. It does not create a
production audio profile and cannot select `mic_for_asr`.

## Policy And Command

- policy: `policies/stronger-offline-target-speaker-separator-prerequisites-v1.json`;
- runner: `scripts/stronger-offline-target-speaker-separator-prerequisites-v1.py`;
- terminal decisions: `READY_FOR_STRONGER_SEPARATOR_QUALIFICATION` or
  `CURRENT_RESOURCE_LIMIT_REACHED`.

`run` freezes inputs, performs two offline resource probes, decides and verifies. Missing files,
changed hashes, an incompatible runtime, network access or a failed numeric check produce the
resource-limit decision. They never weaken the production fallback.

## Frozen Inputs

The policy pins SHA-256 for:

- Speaker-Preserving Neural Echo v2 policy, promotion decision and corpus report;
- Multi-Component Residual Separator v1 policy, train/dev report, decision and verification;
- Target-Me corpus fingerprint and split, item, query, enrollment and replay manifests;
- controlled Target-Me supervision manifests;
- every selected model file and every wheel used by its private runtime.

The selected backbone is SpeechBrain SepFormer Libri2Mix at model revision
`eb43c5bfbb2aa654630adbf849373bcec0a20ed4`, loaded by SpeechBrain `1.1.0`. Model weights and the
private runtime stay outside the repository.

## Outputs

The default output directory is
`sessions/_reports/stronger-offline-target-speaker-separator-prerequisites-v1/`:

- `frozen_inputs.json`;
- `gap_map.json`;
- `supervision_expansion.json`;
- `backbone_shortlist.json`;
- `license_evidence.json`;
- `resource_preflight.json`;
- `four_stem_adapter_plan.json`;
- `readiness_manifest.json`;
- `decision.json`, `decision.md` and `verification_report.json`.

Each JSON artifact has a deterministic content fingerprint. Runtime measurements may vary, while
`resource_preflight.evidence_fingerprint` and the final decision remain stable when model, runtime
and output tensors are unchanged.

## Resource Probe

The child process runs with the background resource policy, at most four Torch threads and blocked
network sockets. It must load locally, produce finite non-zero `[1, 8000, 2]` output, repeat the
exact output SHA-256, fit the memory and time budgets and demonstrate exact mixture reconstruction
after adapter scaling.

The probe proves local executability only. It does not prove preservation of Russian words,
Target-Me identity, production quality or suitability for direct ASR.

## Four-Stem Adapter

The next qualification must produce:

```text
target_me + remote_echo + other_local + unexplained_residual == input mixture
```

SepFormer separates two anonymous local speech estimates at 8 kHz. Frozen WavLM enrollment assigns
the Target-Me stem only with a paired similarity margin. Least-squares coefficients restore scale;
the unexplained residual is the exact remainder. Weak assignment, more than two local speakers,
missing evidence or unsupported audio returns the sample-exact production v2 fallback.

## Safety Boundary

- no training, hard/sealed evaluation or direct ASR occurs in this stage;
- raw CAF, authoritative remote, production v2, transcripts and Live Shadow are unchanged;
- ordinary meetings are evaluation evidence, not hidden supervision labels;
- post-ASR cleanup receives zero promotion credit;
- weights, enrollment and private audio remain local and ignored by Git.
