# Lexical Accuracy Reference Corpus v1 Contract

Status: complete with `REFERENCE_INSUFFICIENT`

Lexical Accuracy Reference Corpus measures word recognition without changing the primary ASR or any
selected transcript. Private reference text stays under ignored `sessions/`; tracked artifacts contain
only portable paths, hashes, source grades and aggregate metrics.

## Trust Grades

| Grade | May establish correctness | Meaning |
|---|---:|---|
| `exact_generated` | yes | Text generated the evaluated digital source exactly. |
| `human_reviewed` | yes | A person checked the words against the audio. |
| `scripted_expected` | no | The operator was asked to read the text, but delivery may differ. |
| `independent_machine` | no | Another recognizer provides disagreement evidence, not truth. |

Agreement between recognizers cannot promote lexical correctness. A weak source remains diagnostic
even when it matches the selected transcript exactly.

## Private Inputs

The private registry uses `murmurmark.lexical_accuracy_reference_registry/v1`. Imported source text,
speaker names, parsed intervals, ASR hypotheses and per-row alignments are stored under:

```text
sessions/_reports/lexical-accuracy-reference-corpus-v1/private/
```

External references are aligned to authoritative utterance intervals and roles. Only the interval
shared by the reference and selected dialogue contributes to disagreement metrics. Echo Lab prompts
are aligned by frozen `prompt_shown` events; their expected text remains diagnostic. The exact digital
source is evaluated with the current local `whisper-cli` and timestamped tokens.

## Public Outputs

```text
sessions/_reports/lexical-accuracy-reference-corpus-v1/
  lexical_accuracy_reference_report.json
  lexical_accuracy_reference_report.md
  reference_manifest.json

docs/testing/lexical-accuracy-reference-corpus-v1-manifest.json
```

Schemas:

- `murmurmark.lexical_accuracy_reference_corpus_report/v1`;
- `murmurmark.lexical_accuracy_reference_frozen_manifest/v1`;
- private `murmurmark.lexical_accuracy_private_evaluation/v1`.

The public report contains WER, CER, substitutions, deletions, insertions, term accuracy, role
metrics, interval coverage and provenance hashes. It contains no reference text, hypothesis text,
speaker names or machine-specific absolute paths.

## Decisions

- `LEXICAL_BASELINE_ESTABLISHED`: exact generated evidence and the required human-reviewed real
  1x1/group, role and acoustic coverage all exist.
- `REFERENCE_INSUFFICIENT`: the instrument is reproducible, but available evidence cannot establish
  real-meeting lexical correctness.

`REFERENCE_INSUFFICIENT` is a scientifically complete result, not a failed run. Transcript Perfection
may report the exact subset as bounded measurement, but must keep the real-meeting lexical blocker.

## Dependent Lexical Work

Human-Reviewed Lexical Seed v1 must add exact real-meeting truth before any ASR prompt, hotword or
correction candidate can claim improvement. The minimum coverage is two meetings spanning 1x1 and
group calls, `Me` and remote roles, and two acoustic modes.

Session-Scoped Lexical Context v1 may then compare the prompt-free control with a compact
meeting-specific context. Broad static glossaries are not candidates: the current bridge does not
consume `glossary.yaml`, and diagnostic prompt agreement is not truth. Promotion requires exact
frozen inputs, term-level gains, overall WER/CER non-regression, role and speaker conservation, and
byte-exact replay.

## Safety

- Capture, Echo Guard, primary ASR configuration and speaker profiles are read-only.
- Raw CAF, selected dialogue, selected Markdown and speaker selection must retain their hashes.
- Missing or changed private sources fail closed for measurement and never change a transcript.
- Replaying frozen private rows must reproduce every public byte.
- No aggregate quality score is derived from unlike evidence grades.
