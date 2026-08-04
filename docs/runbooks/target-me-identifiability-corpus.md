# Target-Me Identifiability Corpus v1 Runbook

This runbook reproduces the private corpus decision. It is research tooling, not part of an ordinary
`murmurmark meeting` run.

## Prerequisites

- the frozen Controlled Echo Supervision Lab publication and its protected source sessions;
- the local WavLM speaker encoder already checked by `murmurmark doctor --strict`;
- Python dependencies from the project virtual environment;
- approximately 3 GB of temporary and published local storage.

Download the official Mini LibriSpeech SLR31 archives:

```bash
cd murmurmark
source .venv/bin/activate

SOURCE_ROOT="sessions/_reports/target-me-identifiability-corpus-v1/sources/openslr31/archives"
mkdir -p "$SOURCE_ROOT"

curl -fL --retry 3 --continue-at - \
  https://www.openslr.org/resources/31/train-clean-5.tar.gz \
  -o "$SOURCE_ROOT/train-clean-5.tar.gz"

curl -fL --retry 3 --continue-at - \
  https://www.openslr.org/resources/31/dev-clean-2.tar.gz \
  -o "$SOURCE_ROOT/dev-clean-2.tar.gz"
```

The builder checks the official archive MD5 values and records local SHA-256 values before safe
extraction. It never downloads data itself and runs the remaining work offline.

## Build

```bash
.venv/bin/python scripts/target-me-identifiability-corpus-v1.py prepare
.venv/bin/python scripts/target-me-identifiability-corpus-v1.py build --refresh
.venv/bin/python scripts/target-me-identifiability-corpus-v1.py verify
```

The builder lowers its own priority with `nice=20`, limits native compute pools to four threads and
publishes transactionally. An interrupted `.staging-*` directory cannot replace `current.json`.

Inspect the decision:

```bash
ROOT="sessions/_reports/target-me-identifiability-corpus-v1"
PUB="$ROOT/$(jq -r .publication "$ROOT/current.json")"

jq '{decision, fingerprint, failed_gates}' "$PUB/corpus_decision.json"
jq '{coverage, isolation, query_controls, enrollment_similarity_margin_median}' \
  "$PUB/oracle_report.json"
jq '.' "$PUB/replay_report.json"
```

Expected immutable result:

```text
READY_FOR_TARGET_CONDITIONED_TRAINING
530cb0fd23503884d438bc24be10fff45610da1fb8fe710aad1b6b6cd992b2ce
```

Do not commit anything under `sessions/`. Do not remove the source archives or protected controlled
captures while the successor separator experiment depends on this publication.
