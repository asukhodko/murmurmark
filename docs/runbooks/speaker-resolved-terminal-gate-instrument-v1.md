# Speaker-Resolved Terminal Gate Instrument v1 Runbook

This maintainer command measures convergence; it is not part of a normal meeting lifecycle.

## Inspect Current Freeze

```bash
murmurmark corpus terminal-gate-v1 status
murmurmark corpus terminal-gate-v1 replay --write-snapshot
```

`replay` must be byte-exact. `TERMINAL_GATE_INSTRUMENT_READY` says every dimension is measurable;
read `product_decision` separately.

## Intentional New Freeze

First refresh and verify every upstream canonical report. Then run:

```bash
murmurmark corpus terminal-gate-v1 preflight
murmurmark corpus terminal-gate-v1 all --refresh --write-snapshot
murmurmark corpus terminal-gate-v1 replay --write-snapshot
```

Never use `--refresh` to hide a stale input. Qualify the source change, regenerate its canonical
report, review the terminal diff, then create the new freeze.

## Interpret States

- `pass`: the dimension meets its own product threshold.
- `bounded`: evidence is current, but a measured residual exceeds the threshold.
- `blocked`: required direct truth or a product prerequisite is incomplete.
- `not_measured`: the frozen evidence is missing, stale or incompatible.

The instrument never compensates one row with another and never changes session artifacts. A
`source_stale:remote_unknown_recovery:upstream_rebaseline_manifest_stale` blocker is repaired by
requalifying that isolated report before creating a new terminal freeze:

```bash
HF_HUB_OFFLINE=1 .venv/bin/python \
  scripts/report-remote-unknown-evidence-recovery-v1-corpus.py all --refresh
```

Chronology uses the read-only arbitration remainder rather than the raw order-audit total. Refresh
that source first when its fingerprints drift:

```bash
murmurmark corpus chronology-arbitration-v1 all --refresh --write-snapshot
murmurmark corpus terminal-gate-v1 all --refresh --write-snapshot
```
