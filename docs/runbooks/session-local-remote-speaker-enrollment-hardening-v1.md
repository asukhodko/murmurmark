# Session-Local Remote Speaker Enrollment Hardening v1 Runbook

Run the frozen experiment:

```bash
murmurmark corpus remote-identity-enrollment-v1 preflight
murmurmark corpus remote-identity-enrollment-v1 all
murmurmark corpus remote-identity-enrollment-v1 status
murmurmark corpus remote-identity-enrollment-v1 replay
```

Inspect the public result:

```bash
jq '{decision, scope, candidate, comparison, gates, invariants, safety, next_action}' \
  sessions/_reports/session-local-remote-speaker-enrollment-hardening-v1/session_local_remote_speaker_enrollment_hardening_report.json
```

Expected frozen result:

```text
decision: DO_NOT_ADVANCE_ENROLLMENT_HARDENING
items: 278
words: 851
enrollment failures: 83 / 119.920926s
newly accepted: 11 / 44.694004s
removed control accepts: 5
new reference errors: 0
```

Do not change weights, floors or thresholds after seeing this result. Coverage v3 remains
authoritative. The next evidence step is a direct real-session speaker truth seed covering new
accepts, removed control accepts, abstentions and open-set negatives.
