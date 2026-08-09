# Session-Local Homogeneous Remote Speaker Enrollment Mining v1 Runbook

## Full Replay

```bash
cd ~/dalamar81/murmurmark
source .venv/bin/activate

murmurmark corpus remote-homogeneous-enrollment-v1 preflight
murmurmark corpus remote-homogeneous-enrollment-v1 prepare
murmurmark corpus remote-homogeneous-enrollment-v1 freeze
murmurmark corpus remote-homogeneous-enrollment-v1 evaluate
murmurmark corpus remote-homogeneous-enrollment-v1 replay
murmurmark corpus remote-homogeneous-enrollment-v1 finalize
murmurmark corpus remote-homogeneous-enrollment-v1 status
```

`all` runs the same sequence. Do not delete a frozen pack and rerun it after reading development
outcomes; that would invalidate the one-shot experiment.

## Expected Result

The frozen 2026-08-09 run returns:

```text
decision: KEEP_EXISTING_ENROLLMENT
qualified_profiles: 9/14
preserved_confirmed_gains: 0/3
unsafe_accepts: 5
```

The report is:

```text
sessions/_reports/session-local-homogeneous-remote-speaker-enrollment-mining-v1/
  session_local_homogeneous_remote_speaker_enrollment_report.json
```

This is a valid negative result. Do not promote the candidate or relax thresholds. Coverage v3 and
explicit `unknown` remain the production behavior.

## Verification

```bash
.venv/bin/python scripts/check-session-local-homogeneous-remote-speaker-enrollment-mining-v1.py
murmurmark corpus perfection all --verify-existing
scripts/check.sh
```
